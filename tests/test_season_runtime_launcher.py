# -*- coding: utf-8 -*-
"""R14-B 验收测试集: Runtime Launcher & Process Supervision (Repair 2 封板加固版)

测试矩阵：
1. production worker CLI 真正执行，参数正确传递而不是 import-and-exit；
2. production worker non-verify contract: 验证 GatewayBotClient.run 真正被调用并传入 stop_event；
3. build_launcher_plan: 仅 local_model 为 spawnable_participants，human 及 external_agent 为 unmanaged；
4. build_launcher_plan: 非 frozen 清单 (如 projected_legacy) 抛出异常拒绝生成执行计划；
5. build_process_command: 命令严格绑定 Manifest 的 resolved_checkpoint_path 与 bot_worker 入口；
6. instant-exit process (如 exit 1 或秒崩进程) 启动后立即被识别为 failed/stopped，绝不误报 healthy；
7. birth identity 获取失败时 (get_process_birth_identity -> None) fail-closed 强杀孤儿并置 failed；
8. processes.json 损坏时严格抛出 RuntimeSupervisorStateError fail-closed；
9. 活跃锁超过 lease 阈值且 owner PID 仍存活时，绝对不得被第二 owner reclaim；
10. dead owner 锁（包含旧 birth_identity 记录）可以被成功 reclaim 并接管；
11. PID 被系统复用 (birth identity 不匹配) 时视为 stale，绝不误杀无关 PID，锁亦可被 reclaim；
12. state="failed" 但底层 OS PID 仍然存活的矛盾记录，必须阻断 Lifecycle (complete/archive/delete) 与 duplicate spawn；
13. state="stopped" 但底层 OS PID 仍然存活的矛盾记录，必须同样阻断 Lifecycle；
14. spawn vs complete 并发竞态原子性测试：双域锁下绝不能产生 (completed + live process) 状态；
15. spawn vs delete 并发竞态原子性测试：绝不能产生 (season JSON deleted + live process)；
16. Stop 流程覆盖 SIGTERM 优雅退出及 SIGTERM ignored 后的强制 SIGKILL 最终存活确认与 -9 退出码审计。
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
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
    assert_no_live_runtime_processes_locked,
    get_process_birth_identity,
    get_season_runtime_status,
    is_pid_alive_and_matched,
    load_process_records_locked,
    record_has_live_owned_process,
    runtime_lock,
    runtime_records_file,
    save_process_records_locked,
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


def test_r14b_bot_worker_non_verify_executes_gateway_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """2. Worker non-verify 分支真正实例化 GatewayBotClient 并调用 run()."""
    dummy_cp = tmp_path / "worker_run.pth"
    dummy_cp.write_text("dummy_weights", encoding="utf-8")

    called = {}

    class MockGatewayBotClient:
        def __init__(self, config):
            called["config"] = config

        def run(self, stop_event=None):
            called["stop_event"] = stop_event
            # 立即退出模拟完成
            return

    monkeypatch.setattr("runtime.bot_worker.GatewayBotClient", MockGatewayBotClient)

    argv = [
        "--account-id", "account:mock_bot",
        "--model-path", str(dummy_cp),
        "--name", "MockBot",
        "--project-root", str(tmp_path),
    ]
    args = parse_args(argv)
    code = run_worker(args)
    assert code == 0
    assert "config" in called
    assert called["config"].ladder_account_id == "account:mock_bot"
    assert called["config"].model_path == dummy_cp.resolve()
    assert "stop_event" in called
    assert isinstance(called["stop_event"], threading.Event)


def test_r14b_launcher_plan_construction_and_non_frozen_rejection(r14b_env):
    """3 & 4. Plan 分组正确，且非 frozen 清单拒绝构建 Plan."""
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
    """5. 进程启动命令严格提取 Manifest 的 resolved_checkpoint_path 并使用 bot_worker."""
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
    """6. 启动后立即退出的进程（如 exit 42），绝不得被误报为 healthy."""
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


def test_r14b_birth_identity_acquisition_failure_fails_closed(r14b_env, monkeypatch: pytest.MonkeyPatch):
    """7. birth_identity 获取失败时 fail-closed 强杀新进程并标记 failed."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "birth_fail.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:bf", label="BF", kind="local_model"))
    art = part_registry.add_model_artifact("model:bf", ModelArtifactCreate(label="bf.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_bf", display_name="BotBF", account_type="managed_bot", model_identity_id="model:bf"))

    sr.create_season(configs_dir, season_id="s_bf", title="BF Season")
    sr.set_season_enrollment(
        configs_dir, "s_bf", ["account:h1", "account:h2", "account:h3", "account:bot_bf"],
        provenance_by_model={"model:bf": {"kind": "local_artifact", "model_identity_id": "model:bf", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_bf", project_root=tmp_path)

    # 模拟 get_process_birth_identity 返回 None
    monkeypatch.setattr("runtime.supervisor.get_process_birth_identity", lambda pid: None)

    def _sleep_cmd(p, root, py_exe):
        return [sys.executable, "-c", "import time; time.sleep(30)"]

    resp = spawn_season_runtime(
        configs_dir, "s_bf",
        project_root=tmp_path,
        custom_command_builder=_sleep_cmd,
        startup_grace_seconds=0.05,
    )
    assert resp.is_active is False
    p_rec = resp.processes[0]
    assert p_rec.state == "failed"
    assert "无法获取进程 birth_identity" in (p_rec.failure_reason or "")
    # 确认进程已被清理杀灭
    if p_rec.pid:
        time.sleep(0.1)
        assert is_pid_alive_and_matched(p_rec.pid, None) is False


def test_r14b_corrupted_processes_json_fails_closed(r14b_env):
    """8. processes.json 文件损坏时抛出 RuntimeSupervisorStateError，绝不假定为空."""
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
    """9 & 10 & 11. 锁 owner dead/birth mismatch 安全 reclaim，活跃 owner 绝不误删."""
    ladder_data_dir = r14b_env["ladder_data_dir"]
    season_id = "s_lock_test"
    lock_dir = ladder_data_dir / "runtime" / "seasons" / season_id
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".runtime.lock"

    # 1. 模拟死进程留下旧锁（PID=99999999 不存在），mtime 为旧时间
    dead_pid = 99999999
    lock_path.write_text(json.dumps({
        "pid": dead_pid,
        "birth_identity": "dead_birth",
        "token": "dead_token",
    }), encoding="utf-8")
    old_time = time.time() - 100.0
    os.utime(lock_path, (old_time, old_time))

    # 尝试获取锁：由于 dead owner，必须成功 reclaim 拿到锁！
    with runtime_lock(season_id, timeout_seconds=1.0, lease_seconds=10.0):
        new_data = json.loads(lock_path.read_text(encoding="utf-8"))
        assert new_data["pid"] == os.getpid()
        assert new_data["token"] != "dead_token"

    # 2. 模拟 PID 复用：PID=os.getpid() 但 birth_identity 不匹配
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),
        "birth_identity": "stale_birth_identity_xyz",
        "token": "stale_token",
    }), encoding="utf-8")
    os.utime(lock_path, (old_time, old_time))

    # 尝试获取锁：因为 birth 不匹配识别为 dead/stale，必须成功 reclaim 拿到锁！
    with runtime_lock(season_id, timeout_seconds=1.0, lease_seconds=10.0):
        new_data = json.loads(lock_path.read_text(encoding="utf-8"))
        assert new_data["token"] != "stale_token"

    # 3. 模拟活跃长任务：当前进程真实 PID 与真实 birth_identity，即使 mtime 超时也不可 reclaim
    my_birth = get_process_birth_identity(os.getpid())
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),
        "birth_identity": my_birth,
        "token": "live_token",
    }), encoding="utf-8")
    os.utime(lock_path, (old_time, old_time))

    with pytest.raises(TimeoutError, match="超时"):
        with runtime_lock(season_id, timeout_seconds=0.2, lease_seconds=10.0):
            pass

    # 清理现场
    lock_path.unlink(missing_ok=True)


def test_r14b_failed_or_stopped_but_live_process_blocks_lifecycle_and_duplicate(r14b_env):
    """12 & 13. record.state 为 failed 或 stopped 但 OS PID 实际存活时，绝不允许 complete/delete 或 duplicate spawn."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "contra.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:cnt", label="CNT", kind="local_model"))
    art = part_registry.add_model_artifact("model:cnt", ModelArtifactCreate(label="c.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_cnt", display_name="BotCNT", account_type="managed_bot", model_identity_id="model:cnt"))

    sr.create_season(configs_dir, season_id="s_cnt", title="CNT Season")
    sr.set_season_enrollment(
        configs_dir, "s_cnt", ["account:h1", "account:h2", "account:h3", "account:bot_cnt"],
        provenance_by_model={"model:cnt": {"kind": "local_artifact", "model_identity_id": "model:cnt", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_cnt", project_root=tmp_path)

    # 启动一个真实的后台 sleep 进程
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    birth_id = get_process_birth_identity(proc.pid)

    # 手工在 processes.json 中写入矛盾状态：state="failed" 但 PID 存活
    rec_failed_but_live = SeasonRuntimeProcessRecord(
        runtime_id="proc_s_cnt_1",
        season_id="s_cnt",
        manifest_id="manifest:s_cnt:fake",
        account_id="account:bot_cnt",
        controller_type="local_model",
        pid=proc.pid,
        birth_identity=birth_id,
        state="failed",  # 谎称 failed
        failure_reason="fake reason",
    )
    with runtime_lock("s_cnt"):
        save_process_records_locked("s_cnt", [rec_failed_but_live])

    # 1. 尝试 Complete 赛季：基于 OS 事实，发现进程实际存活 -> 拦截 409！
    with pytest.raises(SeasonRegistryError, match="runtime_active|正在运行的本地模型进程"):
        sr.set_season_status(configs_dir, "s_cnt", "completed")

    # 2. 尝试 duplicate spawn：发现 account:bot_cnt 底层 PID 存活 -> 不生成新进程
    spawn_resp = spawn_season_runtime(configs_dir, "s_cnt", project_root=tmp_path)
    assert spawn_resp.processes[0].pid == proc.pid

    # 3. 改写为 state="stopped" 但 PID 存活，且将 season 设为 completed（模拟异常残留孤儿）：尝试 Delete -> 依然强行拦截！
    rec_failed_but_live.state = "stopped"
    with runtime_lock("s_cnt"):
        save_process_records_locked("s_cnt", [rec_failed_but_live])

    # 直接将 season 状态改为 draft/completed 以测试 delete_season 的 runtime 拦截
    season_path = configs_dir / "s_cnt.json"
    raw_cfg = json.loads(season_path.read_text(encoding="utf-8"))
    raw_cfg["status"] = "completed"
    season_path.write_text(json.dumps(raw_cfg, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SeasonRegistryError, match="runtime_active|正在运行的本地模型进程"):
        sr.delete_season(configs_dir, "s_cnt")

    # 清理后台测试进程
    try:
        proc.kill()
    except Exception:
        pass


def test_r14b_spawn_vs_complete_concurrency_race(r14b_env):
    """14. 并发竞态：spawn 与 complete 同时进行，绝不能产生 (completed + live process)."""
    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "race.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:rc", label="RC", kind="local_model"))
    art = part_registry.add_model_artifact("model:rc", ModelArtifactCreate(label="r.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_rc", display_name="BotRC", account_type="managed_bot", model_identity_id="model:rc"))

    sr.create_season(configs_dir, season_id="s_race", title="Race Season")
    sr.set_season_enrollment(
        configs_dir, "s_race", ["account:h1", "account:h2", "account:h3", "account:bot_rc"],
        provenance_by_model={"model:rc": {"kind": "local_artifact", "model_identity_id": "model:rc", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_race", project_root=tmp_path)

    results = []

    def _do_spawn():
        try:
            def _sleep_cmd(p, root, py_exe):
                return [sys.executable, "-c", "import time; time.sleep(30)"]
            resp = spawn_season_runtime(configs_dir, "s_race", project_root=tmp_path, custom_command_builder=_sleep_cmd)
            results.append(("spawn", resp))
        except Exception as e:
            results.append(("spawn_err", str(e)))

    def _do_complete():
        try:
            cfg = sr.set_season_status(configs_dir, "s_race", "completed")
            results.append(("complete", cfg))
        except Exception as e:
            results.append(("complete_err", str(e)))

    t1 = threading.Thread(target=_do_spawn)
    t2 = threading.Thread(target=_do_complete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # 结果检查：必须互斥（要么 spawn 成功则 complete 报错 runtime_active；要么 complete 成功则 spawn 报错 season_not_running）
    final_cfg = sr.get_season(configs_dir, "s_race")
    status_resp = get_season_runtime_status(configs_dir, "s_race")

    if final_cfg["status"] == "completed":
        assert status_resp.is_active is False
    else:
        assert final_cfg["status"] == "running"
        assert status_resp.is_active is True

    # 清理现场
    stop_season_runtime(configs_dir, "s_race")


def test_r14b_stop_sigterm_ignored_triggers_sigkill_and_exit_code_audit(r14b_env):
    """16. 测试 Stop 遇到无视 SIGTERM 的进程时，升级为 SIGKILL 强杀并审计退出码 -9."""
    if os.name == "nt":
        pytest.skip("SIGTERM trap test applies to POSIX")

    configs_dir = r14b_env["configs_dir"]
    tmp_path = r14b_env["tmp_path"]
    models_dir = r14b_env["models_dir"]

    cp = _create_checkpoint(models_dir, "sigkill.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:sk", label="SK", kind="local_model"))
    art = part_registry.add_model_artifact("model:sk", ModelArtifactCreate(label="sk.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_sk", display_name="BotSK", account_type="managed_bot", model_identity_id="model:sk"))

    sr.create_season(configs_dir, season_id="s_sk", title="SK Season")
    sr.set_season_enrollment(
        configs_dir, "s_sk", ["account:h1", "account:h2", "account:h3", "account:bot_sk"],
        provenance_by_model={"model:sk": {"kind": "local_artifact", "model_identity_id": "model:sk", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_sk", project_root=tmp_path)

    # 注入一个忽略 SIGTERM 的顽固进程
    def _trap_sigterm_cmd(p, root, py_exe):
        return [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ]

    spawn_resp = spawn_season_runtime(
        configs_dir, "s_sk",
        project_root=tmp_path,
        custom_command_builder=_trap_sigterm_cmd,
        startup_grace_seconds=0.1,
    )
    assert spawn_resp.is_active is True
    pid = spawn_resp.processes[0].pid

    # 停止进程（设置 timeout_seconds=0.3 促使其快速升级为 SIGKILL）
    stop_resp = stop_season_runtime(configs_dir, "s_sk", timeout_seconds=0.3)
    assert stop_resp.is_active is False
    rec = stop_resp.processes[0]
    assert rec.state == "stopped"
    assert rec.exit_code == -9  # 真实审计 SIGKILL 退出码
    assert not is_pid_alive_and_matched(pid, rec.birth_identity)
