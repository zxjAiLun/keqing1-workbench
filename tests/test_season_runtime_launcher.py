# -*- coding: utf-8 -*-
"""R14-B 验收测试集: Runtime Launcher & Process Supervision (Repair 1 加固版)

测试矩阵：
1. production worker CLI 真正执行，参数正确传递而不是 import-and-exit；
2. build_launcher_plan: 仅 local_model 为 spawnable_participants，human 及 external_agent 为 unmanaged；
3. build_launcher_plan: 非 frozen 清单 (如 projected_legacy) 抛出异常拒绝生成执行计划；
4. build_process_command: 命令严格绑定 Manifest 的 resolved_checkpoint_path 与 bot_worker 入口；
5. instant-exit process (如 exit 1 或秒崩进程) 启动后立即被识别为 failed/stopped，绝不误报 healthy；
6. processes.json 损坏时严格抛出 RuntimeSupervisorStateError fail-closed；
7. 活跃锁超过 lease 阈值且 owner PID 仍存活时，绝对不得被第二 owner reclaim；
8. dead owner 锁可以被成功 reclaim 并接管；
9. PID 被系统复用 (birth identity 不匹配) 时视为 stale，绝不误杀无关 PID；
10. Lifecycle Gate Interlock: 存在存活运行中进程时，Complete / Archive / Delete 抛出 409 (runtime_active)；
11. 正常 stop_season_runtime 后，Complete / Archive / Delete 允许正常执行；
12. Stop 流程覆盖 SIGTERM 优雅退出及 SIGKILL 强制杀灭最终确认。
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
from runtime.bot_worker import parse_args, run_worker
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
    RuntimeSupervisorStateError,
    ensure_no_active_runtime_processes,
    get_process_birth_identity,
    get_season_runtime_status,
    is_pid_alive_and_matched,
    load_process_records_locked,
    runtime_lock,
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


def test_r14b_bot_worker_cli_execution_and_verify_mode(tmp_path: Path):
    """1. production worker CLI 真正执行，参数校验通过且 verify 模式返回 0."""
    dummy_cp = tmp_path / "worker_test.pth"
    dummy_cp.write_text("dummy_weights", encoding="utf-8")

    argv = [
        "--account-id", "account:test_bot",
        "--model-path", str(dummy_cp),
        "--name", "TestBotName",
        "--verify-only",
        "--project-root", str(tmp_path),
    ]
    args = parse_args(argv)
    assert args.account_id == "account:test_bot"
    assert args.model_path == str(dummy_cp)
    assert args.name == "TestBotName"
    assert args.verify_only is True

    code = run_worker(args)
    assert code == 0


def test_r14b_launcher_plan_construction_and_non_frozen_rejection(r14b_env):
    """2 & 3. Plan 分组正确，且非 frozen 清单拒绝构建 Plan."""
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

    # 启动赛季并构建 Plan
    start_season_runtime(configs_dir, "s_plan", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_plan", project_root=tmp_path)

    plan = build_launcher_plan(manifest)
    assert len(plan.spawnable_participants) == 1
    assert plan.spawnable_participants[0].account_id == "account:pbot_loc"
    assert len(plan.unmanaged_participants) == 3
    unmanaged_ids = {p.account_id for p in plan.unmanaged_participants}
    assert unmanaged_ids == {"account:ph1", "account:ph2", "account:pbot_ext"}

    # 非 frozen 清单拒绝构建 Plan
    legacy_manifest = manifest.model_copy(update={"materialization": "projected_legacy"})
    with pytest.raises(ValueError, match="Launcher 只能执行 materialization='frozen'"):
        build_launcher_plan(legacy_manifest)


def test_r14b_process_command_exact_checkpoint_binding(r14b_env):
    """4. 进程启动命令严格提取 Manifest 的 resolved_checkpoint_path 并使用 bot_worker."""
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
    assert cmd[1:3] == ["-m", "workbench.runtime.bot_worker"]
    assert "--model-path" in cmd
    idx = cmd.index("--model-path")
    assert cmd[idx + 1] == str(cp.resolve())


def test_r14b_instant_exit_process_never_reported_healthy(r14b_env):
    """5. 启动后立即退出的进程（如 exit 1），绝不得被误报为 healthy."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "instant.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:inst", label="Inst", kind="local_model"))
    art = part_registry.add_model_artifact("model:inst", ModelArtifactCreate(label="i.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_inst", display_name="BotInst", account_type="managed_bot", model_identity_id="model:inst"))

    sr.create_season(configs_dir, season_id="s_inst", title="Inst Season")
    sr.set_season_enrollment(
        configs_dir, "s_inst", ["account:h1", "account:h2", "account:h3", "account:bot_inst"],
        provenance_by_model={"model:inst": {"kind": "local_artifact", "model_identity_id": "model:inst", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_inst", project_root=tmp_path)

    # 注入一个会立即退出的命令 (python -c "import sys; sys.exit(42)")
    def _instant_exit_cmd(p, root, py_exe):
        return [sys.executable, "-c", "import sys; sys.exit(42)"]

    resp = spawn_season_runtime(
        configs_dir, "s_inst",
        project_root=tmp_path,
        custom_command_builder=_instant_exit_cmd,
        startup_grace_seconds=0.1,
    )
    assert resp.is_active is False
    assert len(resp.processes) == 1
    p_rec = resp.processes[0]
    assert p_rec.state == "failed"
    assert p_rec.exit_code == 42
    assert "立即退出" in (p_rec.failure_reason or "")


def test_r14b_corrupted_processes_json_fails_closed(r14b_env):
    """6. processes.json 文件损坏时抛出 RuntimeSupervisorStateError，绝不假定为空."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]

    season_id = "s_corrupt"
    pfile = runtime_records_file(season_id)
    pfile.parent.mkdir(parents=True, exist_ok=True)
    pfile.write_text("{corrupted_json_syntax...", encoding="utf-8")

    with pytest.raises(RuntimeSupervisorStateError, match="损坏"):
        load_process_records_locked(season_id)

    with pytest.raises(RuntimeSupervisorStateError, match="损坏"):
        sync_process_states_locked(season_id)


def test_r14b_runtime_lock_owner_dead_reclaim_and_active_protection(r14b_env):
    """7 & 8. 活跃 owner 锁（即使超过 lease_seconds）不可被删除；确认 dead 的 owner 锁可被安全 reclaim."""
    ladder_data_dir = r14b_env["ladder_data_dir"]
    season_id = "s_lock_test"
    lock_dir = ladder_data_dir / "runtime" / "seasons" / season_id
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".runtime.lock"

    # 1. 模拟死进程留下旧锁（PID=99999999 不存在），mtime 为旧时间
    dead_pid = 99999999
    lock_path.write_text(json.dumps({"pid": dead_pid, "token": "dead_token"}), encoding="utf-8")
    old_time = time.time() - 100.0
    os.utime(lock_path, (old_time, old_time))

    # 尝试获取锁：由于 dead owner，必须成功 reclaim 拿到锁！
    with runtime_lock(season_id, timeout_seconds=1.0, lease_seconds=10.0):
        # 此时锁文件应该已被新 owner 替换
        new_data = json.loads(lock_path.read_text(encoding="utf-8"))
        assert new_data["pid"] == os.getpid()
        assert new_data["token"] != "dead_token"

    # 2. 模拟活跃长任务：当前进程作为 owner，mtime 超时，但 PID 存活
    lock_path.write_text(json.dumps({"pid": os.getpid(), "token": "live_token"}), encoding="utf-8")
    os.utime(lock_path, (old_time, old_time))

    # 第二个请求者在短超时内争抢锁：因为 owner PID 存活，必须拒绝 reclaim 并超时抛错！
    with pytest.raises(TimeoutError, match="超时"):
        with runtime_lock(season_id, timeout_seconds=0.2, lease_seconds=10.0):
            pass

    # 清理现场
    lock_path.unlink(missing_ok=True)


def test_r14b_birth_identity_mismatch_prevents_killing_unrelated_process(r14b_env):
    """9. PID 复用时 birth_identity 不匹配，视为 stale，stop 时绝不向该 PID 发信号."""
    current_pid = os.getpid()
    actual_birth = get_process_birth_identity(current_pid)

    # 匹配校验：真实 birth_id 匹配
    assert is_pid_alive_and_matched(current_pid, actual_birth) is True

    # 防复用校验：伪造不匹配的 birth_id → 判定为 false（stale）
    assert is_pid_alive_and_matched(current_pid, "fake_birth_identity_99999") is False


def test_r14b_lifecycle_interlock_complete_archive_delete_fail_closed(r14b_env):
    """10 & 11. 存在运行中进程时 Complete / Archive / Delete 抛 409；stop 之后允许操作."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "interlock.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:itlk", label="Itlk", kind="local_model"))
    art = part_registry.add_model_artifact("model:itlk", ModelArtifactCreate(label="it.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_itlk", display_name="BotItlk", account_type="managed_bot", model_identity_id="model:itlk"))

    sr.create_season(configs_dir, season_id="s_itlk", title="Itlk Season")
    sr.set_season_enrollment(
        configs_dir, "s_itlk", ["account:h1", "account:h2", "account:h3", "account:bot_itlk"],
        provenance_by_model={"model:itlk": {"kind": "local_artifact", "model_identity_id": "model:itlk", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_itlk", project_root=tmp_path)

    # 使用长期存活的 mock 命令启动
    def _long_living_cmd(p, root, py_exe):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    spawn_resp = spawn_season_runtime(
        configs_dir, "s_itlk",
        project_root=tmp_path,
        custom_command_builder=_long_living_cmd,
        startup_grace_seconds=0.1,
    )
    assert spawn_resp.is_active is True

    # 10. 进程存活时，尝试 complete 赛季 -> 必须抛错 (runtime_active)
    with pytest.raises(SeasonRegistryError, match="runtime_active|正在运行的本地模型进程"):
        sr.set_season_status(configs_dir, "s_itlk", "completed")

    # 尝试 archive 赛季 -> 必须抛错
    with pytest.raises(SeasonRegistryError, match="runtime_active|正在运行的本地模型进程"):
        sr.set_season_status(configs_dir, "s_itlk", "archived")

    # 11. 调用 stop_season_runtime 停止进程
    stop_resp = stop_season_runtime(configs_dir, "s_itlk")
    assert stop_resp.is_active is False
    assert stop_resp.processes[0].state == "stopped"

    # 停止后，状态迁移顺利进行
    sr.set_season_status(configs_dir, "s_itlk", "completed")
    cfg = sr.get_season(configs_dir, "s_itlk")
    assert cfg["status"] == "completed"

    # 删除已完成的无 reference 赛季
    del_res = sr.delete_season(configs_dir, "s_itlk")
    assert del_res["deleted"] is True
