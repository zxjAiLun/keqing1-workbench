"""R16 验收：Real Season Operational Acceptance / End-to-End Drill.

用生产代码路径完整跑一次 Season Runtime 主链，证明真实运行中 Manifest、
Execution Session、Match Binding、Process Ledger、Archive Summary 串起来
与测试夹具中的行为一致，并产出 evidence bundle。

链条：Create → Enroll → Preflight → Start → Spawn（真实子进程 + 真实
execution session）→ 3 局 intake（真实链路 + auto-discovery）→ Stop →
Finalize → Archive → cleanup runtime evidence → sealed history 再读。

真实边界：只在网络边界替换 download_tenhou6（tenhou6 结构经真实
tenhou6_events 转换器）；其余全部为生产函数调用。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from runtime.operational_drill import drill_tenhou6, run_operational_drill


@pytest.fixture
def drill_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    models_dir = tmp_path / "models"
    ladder_data_dir = tmp_path / "ladder_data"
    for d in (data_dir, configs_dir, models_dir, ladder_data_dir):
        d.mkdir(parents=True, exist_ok=True)
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


def test_r16_operational_drill_full_chain(drill_env, tmp_path: Path):
    """完整生产链 drill：每一步的权威事实互相咬合并产出 evidence bundle。"""
    # 网络边界 stub：按 log_id 返回真实结构的 tenhou6（分数逐局不同）
    scores_by_log = {
        "2026082710gm-00a9-0000-r16a": [35000, 25000, 20000, 20000],
        "2026082711gm-00a9-0000-r16b": [20000, 30000, 25000, 25000],
        "2026082712gm-00a9-0000-r16c": [25000, 25000, 35000, 15000],
    }

    def download_stub(log_id: str) -> dict:
        return drill_tenhou6(scores_by_log[log_id])

    evidence = run_operational_drill(drill_env["tmp_path"], download_stub=download_stub)

    # ---- evidence bundle 整体断言 ----------------------------------------
    assert evidence["completed"] is True
    assert evidence["season_id"] == "s_r16_drill"
    assert evidence["manifest_id"].startswith("manifest:s_r16_drill:")
    assert len(evidence["season_authority_hash"]) == 64

    # 步骤顺序 = 生产链顺序
    step_names = [s["step"] for s in evidence["steps"]]
    assert step_names == [
        "create", "enroll", "preflight", "start", "spawn",
        "intake", "stop", "finalize", "archive", "cleanup_reread",
    ]

    # ---- Start：Manifest 冻结且 spawn 的 session 与其一致 -----------------
    spawn_step = next(s for s in evidence["steps"] if s["step"] == "spawn")
    assert spawn_step["manifest_id_in_session"] == evidence["manifest_id"]
    assert len(evidence["execution_session_ids"]) == 1
    assert evidence["process_ids"]["account:bot_a"] > 0  # 真实 PID

    # ---- Intake：3 局全部 runtime-bound 且绑定同一 execution session -------
    matches = evidence["matches"]
    assert len(matches) == 3
    for m in matches:
        assert m["binding_session_id"] == evidence["execution_session_ids"][0]
        assert m["binding_manifest_id"] == evidence["manifest_id"]
        assert m["match_id"].startswith("m_")
    # 3 个不同 log_id
    assert len({m["log_id"] for m in matches}) == 3

    # ---- Stop：真实进程全部停止 -------------------------------------------
    for account, state in evidence["process_final_states"].items():
        assert state["state"] == "stopped", f"{account}: {state}"
        assert state["exit_code"] == 0
        assert state["stopped_at"]

    # ---- Finalize：sealed summary 的终局统计与真实运行一致 ------------------
    counts = evidence["final_counts"]
    assert counts["total_matches"] == 3
    assert counts["rating_eligible"] == 3
    assert counts["runtime_bound"] == 3
    assert counts["total_revisions"] == 3  # 每局 create revision ×3
    assert counts["session_count"] == 1
    assert counts["process_count"] >= 1  # bot 进程终态记录

    # ---- Archive & cleanup -----------------------------------------------
    assert evidence["cleanup_reread_ok"] is True
    assert evidence["cleanup_reread_summary_id"] == evidence["archive_summary_id"]
    assert evidence["archive_summary_id"].startswith("archive:s_r16_drill:")
    assert len(evidence["archive_seal_hash"]) == 64

    # ---- evidence bundle 序列化完整（可落盘审计）---------------------------
    bundle_json = json.dumps(evidence, ensure_ascii=False, indent=2)
    assert "manifest_id" in bundle_json
    assert "archive_seal_hash" in bundle_json
    bundle_path = drill_env["tmp_path"] / "evidence_bundle.json"
    bundle_path.write_text(bundle_json, encoding="utf-8")
    reloaded = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert reloaded == evidence


def test_r16_drill_blocked_after_finalize(drill_env, monkeypatch):
    """finalize 后：新 intake 被拒、reopen 被拒、bare complete 被拒。"""
    scores_by_log = {
        "2026082710gm-00a9-0000-r16a": [35000, 25000, 20000, 20000],
        "2026082711gm-00a9-0000-r16b": [20000, 30000, 25000, 25000],
        "2026082712gm-00a9-0000-r16c": [25000, 25000, 35000, 15000],
    }

    def download_stub(log_id: str) -> dict:
        return drill_tenhou6(scores_by_log.get(log_id, [25000, 25000, 25000, 25000]))

    run_operational_drill(drill_env["tmp_path"], download_stub=download_stub)

    # 新 intake 被 Season gate 拒绝（drill 结束后 season 已 archived）；
    # 网络边界同样 stub（drill 的替换已恢复）
    from participants import intake, ledger
    from participants.ledger import ValidationError as LedgerValidationError
    from replay import season_registry as sr
    from replay.ladder import SeasonRegistryError

    monkeypatch.setattr(intake, "download_tenhou6", download_stub)

    with pytest.raises((ValueError, LedgerValidationError), match="不是 running"):
        intake.resolve_and_create_match(
            log_id="2026082713gm-00a9-0000-r16d",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a"},
            ],
            season_id="s_r16_drill",
            rating_eligible=True,
        )

    # archived 永不 reopen；非法迁移全部拒绝
    configs_dir = drill_env["configs_dir"]
    with pytest.raises(SeasonRegistryError):
        sr.set_season_status(configs_dir, "s_r16_drill", "running")
    with pytest.raises(SeasonRegistryError):
        sr.set_season_status(configs_dir, "s_r16_drill", "completed")

    # Match ledger 保持 3 局（无新对局污染）
    assert ledger.list_matches().total == 3
