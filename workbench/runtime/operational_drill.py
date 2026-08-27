"""R16: Real Season Operational Acceptance Drill — 真实生产链端到端验收。

用生产代码路径完整跑一次 Season Runtime 主链（非测试夹具的旁路拼装）：

    Create → Enroll → Preflight → Start（冻结 Manifest）
    → Spawn（真实子进程 + 真实 execution session + 真实 worker 观测证据）
    → 多局 Match Intake（真实 intake 链路 + auto-discovery session 绑定）
    → Stop → Finalize（sealed archive summary）→ Archive
    → cleanup runtime evidence → 再读取历史

真实边界：
- 全部走生产函数：season_registry / runtime.manifest / runtime.supervisor /
  participants.intake / participants.ledger / runtime.archive_summary；
- spawn 使用 custom_command_builder 启动**真实子进程**（sleep 占位——
  worker 的对外行为在此 drill 中不可用真实 Tenhou 网络对局，进程事实
  （PID/birth_identity/execution session）与生产完全一致）；
- worker 观测证据由 drill 按真实 worker 语义写入（record_observed_log，
  每局、每 local worker 一条——与 bot_worker 的 start_game 观测行为一致）；
- intake 只在网络边界替换 download_tenhou6（tenhou6 结构经真实
  tenhou6_events 转换器；不 mock 任何校验/落账逻辑）。

输出：evidence bundle（JSON dict）——Season ID / Manifest ID / Authority
Hash / Execution Session IDs / Match IDs + Runtime Binding / process final
states / Archive Summary ID / Seal Hash / 最终 counts / cleanup 后再读结果。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# tenhou6 构造（真实结构，经真实转换器）
# ---------------------------------------------------------------------------

DRILL_NAMES = ["DrillHuman1", "DrillHuman2", "DrillHuman3", "DrillBotA"]


def _kyoku(round_index: int, scores: list[int], result: list[Any]) -> list[Any]:
    return [
        [round_index, 0, 0],
        scores,
        [],
        [],
        [], [], [],
        [], [], [],
        [], [], [],
        [], [], [],
        result,
    ]


def drill_tenhou6(final_scores: list[int]) -> dict:
    """构造一局东风战 tenhou6（结构真实，经 tenhou6_events 真实转换）。

    首局一家和牌（+5000/-5000），其余流局——最终分即传入的 final_scores
    （首局和牌后 delta 依次累加到目标分数）。
    """
    start = [25000, 25000, 25000, 25000]
    deltas = [final_scores[i] - start[i] for i in range(4)]
    return {
        "name": list(DRILL_NAMES),
        "rule": {"aka": True},
        "log": [
            _kyoku(0, start, ["和了", deltas, [0, 1]]),
            _kyoku(1, final_scores, ["流局", [0, 0, 0, 0]]),
            _kyoku(2, final_scores, ["流局", [0, 0, 0, 0]]),
            _kyoku(3, final_scores, ["流局", [0, 0, 0, 0]]),
        ],
    }


# ---------------------------------------------------------------------------
# Drill 主链
# ---------------------------------------------------------------------------

def run_operational_drill(
    root: Path,
    *,
    download_stub: Callable[[str], dict],
    startup_grace_seconds: float = 0.15,
) -> dict[str, Any]:
    """执行完整生产链并返回 evidence bundle。

    ``download_stub`` 是唯一的外部边界替换（真实 tenhou6 结构 → dict）；
    其余全部为生产函数调用。
    """
    import sys

    workbench = str(root.parent / "workbench")
    src = str(root.parent / "src")
    for p in (workbench, src):
        if p not in sys.path:
            sys.path.insert(0, p)

    from participants import aliases, intake, ledger
    from participants import registry as part_registry
    from replay import season_registry as sr

    from runtime.archive_summary import validate_persisted_archive_summary
    from runtime.execution_session import (
        find_sessions_for_log,
        load_execution_session,
        record_observed_log,
    )
    from runtime.manifest import (
        get_season_runtime_manifest,
        preflight_season_runtime,
        start_season_runtime,
    )
    from runtime.supervisor import (
        spawn_season_runtime,
        stop_season_runtime,
    )

    # 唯一网络边界替换：download_tenhou6 → drill 提供的真实结构
    _real_download = intake.download_tenhou6
    intake.download_tenhou6 = download_stub  # type: ignore[assignment]
    try:
        return _drill_inner(
            root=root,
            intake=intake,
            ledger=ledger,
            aliases=aliases,
            part_registry=part_registry,
            sr=sr,
            record_observed_log=record_observed_log,
            find_sessions_for_log=find_sessions_for_log,
            load_execution_session=load_execution_session,
            preflight_season_runtime=preflight_season_runtime,
            start_season_runtime=start_season_runtime,
            get_season_runtime_manifest=get_season_runtime_manifest,
            spawn_season_runtime=spawn_season_runtime,
            stop_season_runtime=stop_season_runtime,
            validate_persisted_archive_summary=validate_persisted_archive_summary,
            startup_grace_seconds=startup_grace_seconds,
        )
    finally:
        intake.download_tenhou6 = _real_download  # type: ignore[assignment]


def _drill_inner(
    *,
    root: Path,
    intake,
    ledger,
    aliases,
    part_registry,
    sr,
    record_observed_log,
    find_sessions_for_log,
    load_execution_session,
    preflight_season_runtime,
    start_season_runtime,
    get_season_runtime_manifest,
    spawn_season_runtime,
    stop_season_runtime,
    validate_persisted_archive_summary,
    startup_grace_seconds: float,
) -> dict[str, Any]:
    import sys

    from participants.schemas import (
        AccountCreate,
        ExternalAliasCreate,
        ModelArtifactCreate,
        ModelIdentityCreate,
    )

    configs_dir = root / "configs"
    models_dir = root / "models"
    season_id = "s_r16_drill"
    evidence: dict[str, Any] = {"season_id": season_id, "steps": []}

    def step(name: str, detail: dict[str, Any]) -> None:
        evidence["steps"].append({"step": name, **detail})

    # ---- 1. Create -------------------------------------------------------
    sr.create_season(configs_dir, season_id=season_id, title="R16 Operational Drill")
    season = sr.get_season(configs_dir, season_id)
    assert season["status"] == "draft"
    step("create", {"status": "draft"})

    # ---- 2. Enroll -------------------------------------------------------
    cp = models_dir / "drill_model.pth"
    cp.write_text("drill weights", encoding="utf-8")
    part_registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:drill", label="DrillModel", kind="local_model")
    )
    art = part_registry.add_model_artifact(
        "model:drill",
        ModelArtifactCreate(label="drill.pth", artifact_path=str(cp), stage="promoted"),
    )
    for i in range(1, 4):
        part_registry.create_account(
            AccountCreate(account_id=f"account:h{i}", display_name=f"DrillHuman{i}", account_type="human")
        )
    part_registry.create_account(
        AccountCreate(
            account_id="account:bot_a",
            display_name="DrillBotA",
            account_type="managed_bot",
            model_identity_id="model:drill",
        )
    )
    sr.set_season_enrollment(
        configs_dir,
        season_id,
        ["account:h1", "account:h2", "account:h3", "account:bot_a"],
        provenance_by_model={
            "model:drill": {
                "kind": "local_artifact",
                "model_identity_id": "model:drill",
                "model_artifact_id": art.model_artifact_id,
            }
        },
    )
    step("enroll", {"accounts": 4, "model_artifact_id": art.model_artifact_id})

    # ---- 3. Preflight ----------------------------------------------------
    report = preflight_season_runtime(configs_dir, season_id, project_root=root)
    assert report.can_start, f"preflight blocked: {[i.message for i in report.issues]}"
    step("preflight", {"can_start": True})

    # ---- 4. Start（冻结 Manifest）---------------------------------------
    start_season_runtime(configs_dir, season_id, project_root=root)
    manifest = get_season_runtime_manifest(configs_dir, season_id, project_root=root)
    assert manifest.materialization == "frozen"
    evidence["manifest_id"] = manifest.manifest_id
    evidence["season_authority_hash"] = manifest.season_authority_hash
    step("start", {"manifest_id": manifest.manifest_id})

    # ---- 5. Spawn（真实子进程 + 真实 execution session）------------------
    def _sleep_cmd(participant, project_root, py_exe):
        return [sys.executable, "-c", "import time; time.sleep(120)"]

    spawn_resp = spawn_season_runtime(
        configs_dir, season_id, project_root=root,
        custom_command_builder=_sleep_cmd,
        startup_grace_seconds=startup_grace_seconds,
    )
    assert spawn_resp.is_active, "spawn 后进程应处于活跃状态"
    bot_rec = next(r for r in spawn_resp.processes if r.account_id == "account:bot_a")
    session_id = bot_rec.command[bot_rec.command.index("--runtime-session-id") + 1]
    session = load_execution_session(session_id)
    assert session is not None, "spawn 应产生 execution session"
    evidence["execution_session_ids"] = [session_id]
    evidence["process_ids"] = {r.account_id: r.pid for r in spawn_resp.processes}
    step(
        "spawn",
        {
            "session_id": session_id,
            "manifest_id_in_session": session.manifest_id,
            "pids": evidence["process_ids"],
        },
    )

    # ---- 6. 多局 Match Intake（真实链路 + auto-discovery）----------------
    # 别名注册（真实 aliases 模块）
    for i in range(1, 4):
        aliases.register_alias(
            ExternalAliasCreate(provider="tenhou", external_id=f"DrillHuman{i}", account_id=f"account:h{i}")
        )
    aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou",
            external_id="DrillBotA",
            account_id="account:bot_a",
            model_identity_id="model:drill",
            model_artifact_id=art.model_artifact_id,
        )
    )

    log_specs = [
        ("2026082710gm-00a9-0000-r16a", [35000, 25000, 20000, 20000]),
        ("2026082711gm-00a9-0000-r16b", [20000, 30000, 25000, 25000]),
        ("2026082712gm-00a9-0000-r16c", [25000, 25000, 35000, 15000]),
    ]

    match_records = []
    for log_id, scores in log_specs:
        # 真实 worker 观测语义：每个 local worker 对该局各写一条 observation
        record_observed_log(session_id, "account:bot_a", tenhou_log_id=log_id)

        # 真实 intake：不带 session_id → auto-discovery（唯一 execution
        # origin → session 绑定 → RuntimeMatchBinding）
        res = intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {
                    "seat": 3,
                    "action": "assign",
                    "account_id": "account:bot_a",
                    "model_identity_id": "model:drill",
                    "model_artifact_id": art.model_artifact_id,
                },
            ],
            season_id=season_id,
            rating_eligible=True,
        )
        m = ledger.get_match(res["match_id"])
        assert m is not None
        assert m.runtime_binding is not None, f"{log_id} 应获得 runtime binding"
        assert m.runtime_binding.session_id == session_id
        assert m.runtime_binding.manifest_id == manifest.manifest_id
        # auto-discovery 反查结果一致
        discovered = find_sessions_for_log(log_id)
        assert len(discovered) == 1 and discovered[0].session_id == session_id
        match_records.append(
            {
                "log_id": log_id,
                "match_id": m.match_id,
                "binding_session_id": m.runtime_binding.session_id,
                "binding_manifest_id": m.runtime_binding.manifest_id,
                "final_scores": scores,
            }
        )
    evidence["matches"] = match_records
    step("intake", {"match_count": len(match_records), "all_runtime_bound": True})

    # ---- 7. Stop ----------------------------------------------------------
    stop_resp = stop_season_runtime(configs_dir, season_id)
    assert not stop_resp.is_active
    evidence["process_final_states"] = {
        r.account_id: {"state": r.state, "exit_code": r.exit_code, "stopped_at": r.stopped_at}
        for r in stop_resp.processes
    }
    step("stop", {k: v["state"] for k, v in evidence["process_final_states"].items()})

    # ---- 8. Finalize（sealed archive summary）----------------------------
    finalized = sr.finalize_season(configs_dir, season_id)
    assert finalized["status"] == "completed"
    summary = finalized["archive_summary"]
    evidence["completed_at"] = finalized["completed_at"]
    evidence["archive_summary_id"] = summary["archive_summary_id"]
    evidence["archive_seal_hash"] = summary["archive_summary_hash"]
    evidence["final_counts"] = {
        "total_matches": summary["match_summary"]["total_matches"],
        "rating_eligible": summary["match_summary"]["rating_eligible_matches"],
        "runtime_bound": summary["match_summary"]["runtime_bound_matches"],
        "total_revisions": summary["match_summary"]["total_revisions"],
        "session_count": summary["operation_summary"]["session_count"],
        "process_count": len(summary["operation_summary"]["processes"]),
    }
    step("finalize", {"archive_summary_id": summary["archive_summary_id"]})

    # ---- 9. Archive -------------------------------------------------------
    archived = sr.set_season_status(configs_dir, season_id, "archived")
    assert archived["status"] == "archived"
    assert archived["archive_summary"] == summary  # byte-for-byte 保留
    step("archive", {"archive_summary_preserved": True})

    # ---- 10. Cleanup runtime evidence → sealed history 仍可读 -------------
    import shutil

    from runtime.execution_session import execution_sessions_root
    from runtime.supervisor import runtime_records_file

    for session_dir in execution_sessions_root().iterdir():
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
    records_path = runtime_records_file(season_id)
    if records_path.exists():
        records_path.unlink()

    after_cleanup = sr.get_season(configs_dir, season_id)
    validate_persisted_archive_summary(after_cleanup, after_cleanup["archive_summary"])
    assert after_cleanup["archive_summary"] == summary
    evidence["cleanup_reread_ok"] = True
    evidence["cleanup_reread_summary_id"] = after_cleanup["archive_summary"]["archive_summary_id"]
    step("cleanup_reread", {"sealed_summary_readable": True})

    evidence["completed"] = True
    evidence["completed_at_wall"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    return evidence


__all__ = ["DRILL_NAMES", "drill_tenhou6", "run_operational_drill"]
