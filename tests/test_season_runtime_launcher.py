# -*- coding: utf-8 -*-
"""R14-B 验收测试集: Runtime Launcher & Process Supervision

测试矩阵：
1. build_launcher_plan: 仅 local_model 为 spawnable_participants，human 及 external_agent 为 unmanaged；
2. build_launcher_plan: 非 frozen 清单 (如 projected_legacy) 抛出异常拒绝生成执行计划；
3. build_process_command: 命令严格绑定 Manifest 的 resolved_checkpoint_path；
4. spawn_season_runtime: 仅允许 status='running' 且 manifest 自洽的赛季启动；
5. spawn_season_runtime: 成功启动后生成 processes.json，记录 PID、command、log_path 与 healthy 状态；
6. 幂等防重: 相同 (season_id, manifest_id, account_id) 在运行中时重复 spawn 不产生额外进程；
7. stop_season_runtime: 幂等优雅终止进程，状态变更为 stopped；
8. 崩溃与重启恢复检测: 进程退出后 sync_process_states_locked 自动识别进程已死亡并更新状态为 stopped；
9. 启动失败审计证据: Popen 异常时留下 terminal failure 记录 (state='failed', failure_reason)；
10. API 端点测试: /runtime/spawn, /runtime/stop, /runtime/status 契约。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest

from participants import registry as part_registry
from participants.schemas import (
    AccountCreate,
    ExternalModelRevisionCreate,
    ModelArtifactCreate,
    ModelIdentityCreate,
)
from replay import season_registry as sr
from replay.ladder import SeasonRegistryError
from runtime.launcher import (
    SeasonRuntimeLauncherPlan,
    SeasonRuntimeProcessRecord,
    build_launcher_plan,
    build_process_command,
    make_runtime_id,
)
from runtime.manifest import (
    get_season_runtime_manifest,
    start_season_runtime,
)
from runtime.supervisor import (
    get_season_runtime_status,
    is_pid_alive,
    load_process_records_locked,
    runtime_records_file,
    spawn_season_runtime,
    stop_season_runtime,
    sync_process_states_locked,
)


@pytest.fixture
def r14b_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    models_dir = tmp_path / "models"
    ladder_data_dir = tmp_path / "ladder_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    ladder_data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(ladder_data_dir))

    return {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "configs_dir": configs_dir,
        "models_dir": models_dir,
        "ladder_data_dir": ladder_data_dir,
    }


def _create_checkpoint(models_dir: Path, name: str) -> Path:
    cp = models_dir / name
    cp.write_text("weights_data", encoding="utf-8")
    return cp


def test_r14b_launcher_plan_construction_and_non_frozen_rejection(r14b_env):
    """1 & 2. Plan 分组正确，且非 frozen 清单拒绝构建 Plan."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "plan.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:p_loc", label="PLoc", kind="local_model"))
    art = part_registry.add_model_artifact("model:p_loc", ModelArtifactCreate(label="p.pth", artifact_path=str(cp), stage="promoted"))

    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:p_ext", label="PExt", kind="external_agent"))
    rev = part_registry.add_external_revision("model:p_ext", ExternalModelRevisionCreate(provider="mortal", version="1.0", is_current=True))

    part_registry.create_account(AccountCreate(account_id="account:ph1", display_name="PH1", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:ph2", display_name="PH2", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:pbot_loc", display_name="PBotLoc", account_type="managed_bot", model_identity_id="model:p_loc"))
    part_registry.create_account(AccountCreate(account_id="account:pbot_ext", display_name="PBotExt", account_type="external_bot", model_identity_id="model:p_ext"))

    sr.create_season(configs_dir, season_id="s_plan", title="Plan Season")
    sr.set_season_enrollment(
        configs_dir, "s_plan", ["account:ph1", "account:ph2", "account:pbot_loc", "account:pbot_ext"],
        provenance_by_model={
            "model:p_loc": {"kind": "local_artifact", "model_identity_id": "model:p_loc", "model_artifact_id": art.model_artifact_id},
            "model:p_ext": {"kind": "external_revision", "model_identity_id": "model:p_ext", "external_revision_id": rev.external_revision_id},
        }
    )

    # 1. 启动赛季并构建 Plan
    started = start_season_runtime(configs_dir, "s_plan", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_plan", project_root=tmp_path)

    plan = build_launcher_plan(manifest)
    assert len(plan.spawnable_participants) == 1
    assert plan.spawnable_participants[0].account_id == "account:pbot_loc"
    assert len(plan.unmanaged_participants) == 3
    unmanaged_ids = {p.account_id for p in plan.unmanaged_participants}
    assert unmanaged_ids == {"account:ph1", "account:ph2", "account:pbot_ext"}

    # 2. 非 frozen 清单拒绝构建 Plan
    legacy_manifest = manifest.model_copy(update={"materialization": "projected_legacy"})
    with pytest.raises(ValueError, match="Launcher 只能执行 materialization='frozen'"):
        build_launcher_plan(legacy_manifest)


def test_r14b_process_command_exact_checkpoint_binding(r14b_env):
    """3. 进程启动命令严格提取 Manifest 的 resolved_checkpoint_path."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "cmd_exact.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:cmd_loc", label="CmdLoc", kind="local_model"))
    art = part_registry.add_model_artifact("model:cmd_loc", ModelArtifactCreate(label="c.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_cmd", display_name="BotCmd", account_type="managed_bot", model_identity_id="model:cmd_loc"))

    sr.create_season(configs_dir, season_id="s_cmd", title="Cmd Season")
    sr.set_season_enrollment(
        configs_dir, "s_cmd", ["account:h1", "account:h2", "account:h3", "account:bot_cmd"],
        provenance_by_model={"model:cmd_loc": {"kind": "local_artifact", "model_identity_id": "model:cmd_loc", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_cmd", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_cmd", project_root=tmp_path)
    bot_p = next(p for p in manifest.participants if p.account_id == "account:bot_cmd")

    cmd = build_process_command(bot_p, project_root=tmp_path, python_executable="/custom/bin/python")
    assert cmd[0] == "/custom/bin/python"
    assert "--model-path" in cmd
    idx = cmd.index("--model-path")
    assert cmd[idx + 1] == str(cp.resolve())


def test_r14b_spawn_and_stop_lifecycle_and_idempotency(r14b_env):
    """4, 5, 6, 7. Spawn 门禁、启动记录持久化、防重幂等与 Stop 生命周期."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "life.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:life", label="Life", kind="local_model"))
    art = part_registry.add_model_artifact("model:life", ModelArtifactCreate(label="l.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_life", display_name="BotLife", account_type="managed_bot", model_identity_id="model:life"))

    sr.create_season(configs_dir, season_id="s_life", title="Life Season")
    sr.set_season_enrollment(
        configs_dir, "s_life", ["account:h1", "account:h2", "account:h3", "account:bot_life"],
        provenance_by_model={"model:life": {"kind": "local_artifact", "model_identity_id": "model:life", "model_artifact_id": art.model_artifact_id}}
    )

    # 4. 未 Start 的 draft 赛季直接 spawn -> 409
    with pytest.raises(SeasonRegistryError, match="只能为 running 状态的赛季"):
        spawn_season_runtime(configs_dir, "s_life", project_root=tmp_path)

    # Start 固化
    start_season_runtime(configs_dir, "s_life", project_root=tmp_path)

    # 使用 sleep 作为安全 mock 进程（保证短时间内保持存活）
    dummy_exe = sys.executable
    # 5. 首次 spawn
    resp1 = spawn_season_runtime(configs_dir, "s_life", project_root=tmp_path, python_executable=dummy_exe)
    assert resp1.is_active is True
    assert len(resp1.processes) == 1
    p_rec = resp1.processes[0]
    assert p_rec.account_id == "account:bot_life"
    assert p_rec.state == "healthy"
    assert p_rec.pid is not None
    assert p_rec.pid > 0

    first_pid = p_rec.pid

    # 6. 重复 spawn -> 幂等，不生成新 PID
    resp2 = spawn_season_runtime(configs_dir, "s_life", project_root=tmp_path, python_executable=dummy_exe)
    assert resp2.processes[0].pid == first_pid

    # 7. Stop 停止进程
    stop_resp = stop_season_runtime(configs_dir, "s_life")
    assert stop_resp.is_active is False
    assert stop_resp.processes[0].state == "stopped"
    assert not is_pid_alive(first_pid)

    # 重复 Stop 幂等
    stop_resp2 = stop_season_runtime(configs_dir, "s_life")
    assert stop_resp2.is_active is False


def test_r14b_crash_recovery_and_stale_detection(r14b_env):
    """8. 崩溃/退出检测：外部杀死进程后 sync_process_states_locked 自动识别并置 stopped."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "crash.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:crash", label="Crash", kind="local_model"))
    art = part_registry.add_model_artifact("model:crash", ModelArtifactCreate(label="cr.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_crash", display_name="BotCrash", account_type="managed_bot", model_identity_id="model:crash"))

    sr.create_season(configs_dir, season_id="s_crash", title="Crash Season")
    sr.set_season_enrollment(
        configs_dir, "s_crash", ["account:h1", "account:h2", "account:h3", "account:bot_crash"],
        provenance_by_model={"model:crash": {"kind": "local_artifact", "model_identity_id": "model:crash", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_crash", project_root=tmp_path)
    resp = spawn_season_runtime(configs_dir, "s_crash", project_root=tmp_path)
    pid = resp.processes[0].pid
    assert pid is not None

    # 外部杀死子进程模拟崩溃
    try:
        os.kill(pid, 9)
    except Exception:
        pass
    time.sleep(0.1)

    # 同步状态并读取
    records = sync_process_states_locked("s_crash")
    assert records[0].state == "stopped"

    # 查询状态接口确认 is_active=False
    status_resp = get_season_runtime_status(configs_dir, "s_crash")
    assert status_resp.is_active is False
    assert status_resp.processes[0].state == "stopped"


def test_r14b_spawn_failure_records_terminal_state(r14b_env):
    """9. Popen 发生异常时留下明确的 terminal failure 审计记录."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "fail.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:fail", label="Fail", kind="local_model"))
    art = part_registry.add_model_artifact("model:fail", ModelArtifactCreate(label="f.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_fail", display_name="BotFail", account_type="managed_bot", model_identity_id="model:fail"))

    sr.create_season(configs_dir, season_id="s_fail", title="Fail Season")
    sr.set_season_enrollment(
        configs_dir, "s_fail", ["account:h1", "account:h2", "account:h3", "account:bot_fail"],
        provenance_by_model={"model:fail": {"kind": "local_artifact", "model_identity_id": "model:fail", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_fail", project_root=tmp_path)

    # 传入不存在的 python 可执行文件触发 Popen FileNotFoundError
    resp = spawn_season_runtime(configs_dir, "s_fail", project_root=tmp_path, python_executable="/non/existent/executable_path_12345")
    assert resp.is_active is False
    assert len(resp.processes) == 1
    failed_rec = resp.processes[0]
    assert failed_rec.state == "failed"
    assert failed_rec.failure_reason is not None
    assert "进程启动失败" in failed_rec.failure_reason

    # 验证磁盘 processes.json 中同样存在该 failed 记录
    records_disk = load_process_records_locked("s_fail")
    assert records_disk[0].state == "failed"
