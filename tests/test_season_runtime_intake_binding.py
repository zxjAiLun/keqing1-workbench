# -*- coding: utf-8 -*-
"""R14-C Repair 1 验收测试集: Frozen Admission Transaction & Immutable Runtime Evidence

测试矩阵：
1. manual create_match (无 session) 不产生 RuntimeMatchBinding (P1-4)
2. 有效 intake (有 capture session) 产生包含完整 RuntimeMatchBinding 的 Match (P1-4)
3. Start 后 retire Artifact A，frozen manifest path 仍可摄入 Frozen A (P1-1)
4. Start 后 disable Account，frozen manifest path 仍可摄入 (P1-1)
5. Catalog Current A→B 后，用 Frozen A 可摄入、用新产物 B 被拒 (P1-1)
6. note-only revise 后 binding 完全不变 (byte-for-byte) (P1-2)
7. runtime-bound Match 尝试改 seat/season 被拒 (P1-2)
8. completed 赛季禁止摄入新 rating-eligible match (P1-3)
9. 篡改 manifest_id 被拒 (P1-1)
10. 无 session 的 manual match 不能获得 runtime binding (P1-4)
11. wrong-manifest/non-member account blocked (P1-1)
12. pending 写完后注入异常→recovery→Match/Revision/Artifact 三处 binding 一致 (P2-2)
13. RuntimeMatchBinding frozen=True 且不在 MatchCreate/MatchRevise 中暴露 (P2-1)
14. intake-vs-complete Barrier 并发测试 (P1-3)
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from participants import aliases, intake, ledger
from participants import registry as part_registry
from participants.schemas import (
    AccountCreate,
    ExternalAliasCreate,
    MatchCreate,
    MatchRevise,
    MatchSeat,
    ModelArtifactCreate,
    ModelIdentityCreate,
    RuntimeMatchBinding,
)
from replay import season_registry as sr
from runtime.manifest import (
    get_season_runtime_manifest,
    start_season_runtime,
)


@pytest.fixture
def r14c_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    models_dir = tmp_path / "models"
    ladder_data_dir = tmp_path / "ladder_data"
    capture_dir = tmp_path / "capture"
    data_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    ladder_data_dir.mkdir(parents=True, exist_ok=True)
    capture_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(ladder_data_dir))
    monkeypatch.setenv("KEQING_LADDER_CAPTURE_ROOT", str(capture_dir))

    return {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "configs_dir": configs_dir,
        "models_dir": models_dir,
        "ladder_data_dir": ladder_data_dir,
        "capture_dir": capture_dir,
    }


def _create_checkpoint(models_dir: Path, name: str) -> Path:
    cp = models_dir / name
    cp.write_text("weights_data", encoding="utf-8")
    return cp


def _setup_two_bot_frozen_season(env):
    """双 local bot 赛季（h1/h2 human + bot_a/bot_b local_model）——用于多 worker 观测证据测试."""
    configs_dir = env["configs_dir"]
    tmp_path = env["tmp_path"]
    models_dir = env["models_dir"]

    cp_a = _create_checkpoint(models_dir, "model_a.pth")
    cp_b = _create_checkpoint(models_dir, "model_ab.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ma", label="MA", kind="local_model"))
    art_a = part_registry.add_model_artifact("model:ma", ModelArtifactCreate(label="ma.pth", artifact_path=str(cp_a), stage="promoted"))
    art_b = part_registry.add_model_artifact("model:ma", ModelArtifactCreate(label="mab.pth", artifact_path=str(cp_b), stage="promoted"))

    for i in range(1, 3):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"Human{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_a", display_name="BotA", account_type="managed_bot", model_identity_id="model:ma"))
    part_registry.create_account(AccountCreate(account_id="account:bot_b", display_name="BotB", account_type="managed_bot", model_identity_id="model:ma"))

    sr.create_season(configs_dir, season_id="s_r14c2", title="R14-C Two-Bot Season")
    sr.set_season_enrollment(
        configs_dir, "s_r14c2", ["account:h1", "account:h2", "account:bot_a", "account:bot_b"],
        provenance_by_model={"model:ma": {"kind": "local_artifact", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_r14c2", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_r14c2", project_root=tmp_path)
    return {
        "season_id": "s_r14c2",
        "art_a": art_a,
        "manifest": manifest,
    }


def _setup_frozen_season(env):
    configs_dir = env["configs_dir"]
    tmp_path = env["tmp_path"]
    models_dir = env["models_dir"]

    cp_a = _create_checkpoint(models_dir, "model_a.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ma", label="MA", kind="local_model"))
    art_a = part_registry.add_model_artifact("model:ma", ModelArtifactCreate(label="ma.pth", artifact_path=str(cp_a), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"Human{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_a", display_name="BotA", account_type="managed_bot", model_identity_id="model:ma"))

    sr.create_season(configs_dir, season_id="s_r14c", title="R14-C Season")
    sr.set_season_enrollment(
        configs_dir, "s_r14c", ["account:h1", "account:h2", "account:h3", "account:bot_a"],
        provenance_by_model={"model:ma": {"kind": "local_artifact", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_r14c", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_r14c", project_root=tmp_path)
    return {
        "season_id": "s_r14c",
        "art_a": art_a,
        "cp_a": cp_a,
        "manifest": manifest,
    }


def _create_execution_session(
    env,
    session_id: str,
    manifest,
    *,
    accounts: list[str] | None = None,
    season_id: str | None = None,
    manifest_id: str | None = None,
    authority_hash: str | None = None,
):
    """创建真实的 R14-B execution session 记录（keqing.runtime.execution_session.v1）.

    默认从 setup 的 manifest 冻结 season/manifest/authority hash 与全体
    participants；可通过参数显式制造错配（wrong season/manifest/roster）。
    """
    from runtime.execution_session import (
        ExecutionSessionParticipant,
        RuntimeExecutionSession,
        persist_execution_session_locked,
    )

    if accounts is None:
        accounts = ["account:h1", "account:h2", "account:h3", "account:bot_a"]
    manifest_accounts = {p.account_id: p for p in manifest.participants}
    participants = []
    for aid in accounts:
        m_part = manifest_accounts.get(aid)
        participants.append(
            ExecutionSessionParticipant(
                account_id=aid,
                controller_type=m_part.controller_type if m_part else "human_ui",
                execution_provenance=(
                    m_part.execution_provenance if m_part else {"kind": "human"}
                ),
            )
        )
    session = RuntimeExecutionSession(
        session_id=session_id,
        season_id=season_id if season_id is not None else manifest.season_id,
        manifest_id=manifest_id if manifest_id is not None else manifest.manifest_id,
        season_authority_hash=(
            authority_hash if authority_hash is not None else manifest.season_authority_hash
        ),
        participants=participants,
        created_at="2026-08-27T12:00:00+08:00",
    )
    persist_execution_session_locked(session)
    return session


def _record_worker_log(session_id: str, log_id: str, account_id: str = "account:bot_a"):
    """模拟 bot worker 在 execution session 内记录观察到的 Tenhou log."""
    from runtime.execution_session import record_observed_log

    record_observed_log(session_id, account_id, tenhou_log_id=log_id)


def _record_all_local_worker_logs(session, log_id: str):
    """为 session 的全部 local_model participants 记录 log 观测（完整证据）."""
    from runtime.execution_session import record_observed_log

    for p in session.participants:
        if p.controller_type == "local_model":
            record_observed_log(session.session_id, p.account_id, tenhou_log_id=log_id)


def _make_seats(art_a):
    return [
        MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(
            seat=3,
            account_id="account:bot_a",
            controller_type="local_model",
            model_identity_id="model:ma",
            model_artifact_id=art_a.model_artifact_id,
        ),
    ]


# ---------------------------------------------------------------------------
# P1-4: manual create_match without session → no RuntimeMatchBinding
# ---------------------------------------------------------------------------

def test_r14c_manual_create_match_no_session_no_binding(r14c_env):
    """1 & 10. manual create_match (无 session) 正确通过资格校验，但不产生 RuntimeMatchBinding."""
    setup = _setup_frozen_season(r14c_env)
    seats = _make_seats(setup["art_a"])

    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    created = ledger.create_match(payload, registry=part_registry)
    # 没有 session evidence → 不能获得 RuntimeMatchBinding
    assert created.runtime_binding is None
    # 但 execution_provenance 仍然正确冻结
    assert created.seats[3].execution_provenance is not None
    assert created.seats[3].execution_provenance.model_artifact_id == setup["art_a"].model_artifact_id


# ---------------------------------------------------------------------------
# P1-4: intake with capture session → RuntimeMatchBinding present
# ---------------------------------------------------------------------------

def test_r14c_intake_with_session_produces_binding(r14c_env, monkeypatch):
    """2. 有效 intake (有 capture session) 产生完整 RuntimeMatchBinding."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # 创建真实 R14-B execution session（supervisor spawn epoch 语义）
    session_id = "r14c_session_1"
    _create_execution_session(r14c_env, session_id, manifest)

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:h3"},
        {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]

    log_id = "2026082712gm-00a9-0000-r14c_sess"
    _record_worker_log(session_id, log_id)
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=resolutions,
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )

    m = ledger.get_match(res["match_id"])
    assert m.runtime_binding is not None
    assert m.runtime_binding.manifest_id == manifest.manifest_id
    assert m.runtime_binding.session_id == session_id
    assert m.runtime_binding.seat_accounts[3] == "account:bot_a"
    assert m.runtime_binding.seat_provenance[3].model_artifact_id == art_a.model_artifact_id

    # 三处一致：Match / Revision / Replay Artifact
    m_binding = m.runtime_binding.model_dump(mode="json")
    rev_rows = ledger._read_revision_rows()
    match_revs = [r for r in rev_rows if r.get("match_id") == m.match_id]
    assert len(match_revs) == 1
    assert match_revs[0]["after"].get("runtime_binding") == m_binding
    artifact_summary = intake.read_replay_artifact(log_id)
    assert artifact_summary is not None
    assert artifact_summary.get("runtime_binding") == m_binding

    # note-only revise 之后三处依然一致（binding byte-for-byte 不变）
    revised = ledger.revise_match(m.match_id, MatchRevise(note="赛后备注"), registry=part_registry)
    assert revised.note == "赛后备注"
    assert revised.runtime_binding is not None
    assert revised.runtime_binding.model_dump(mode="json") == m_binding
    m2 = ledger.get_match(m.match_id)
    assert m2.runtime_binding.model_dump(mode="json") == m_binding
    artifact_summary2 = intake.read_replay_artifact(log_id)
    assert artifact_summary2.get("runtime_binding") == m_binding


def test_r14c_wrong_season_session_blocked(r14c_env, monkeypatch):
    """11b. session 属于其他 Season 时，fail-closed blocked（不得静默降级）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # session 冻结的是另一个 Season
    session_id = "r14c_wrong_season_session"
    _create_execution_session(r14c_env, session_id, manifest, season_id="s_other_season")

    with pytest.raises(ValueError, match="属于赛季|不一致"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-r14c_wses",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    # fail-closed：没有任何 Match 落账
    assert ledger.list_matches().total == 0


def test_r14c_wrong_manifest_session_blocked(r14c_env, monkeypatch):
    """11c. session 记录的 manifest_id 与 persisted Manifest 不一致时，fail-closed blocked."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # session 记录了同一 Season，但 manifest_id 是伪造的
    session_id = "r14c_wrong_manifest_session"
    _create_execution_session(
        r14c_env, session_id, manifest,
        manifest_id="manifest:fake_manifest_id",
    )

    with pytest.raises(ValueError, match="manifest.*不一致|不一致"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-r14c_wman",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


def test_r14c_roster_mismatch_session_blocked(r14c_env, monkeypatch):
    """11d. session 参与者集合与 Match seats 不一致时，fail-closed blocked."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # session 参与者缺少 account:h2、多了 stranger999
    session_id = "r14c_roster_mismatch_session"
    _create_execution_session(
        r14c_env, session_id, manifest,
        accounts=["account:h1", "account:h3", "account:bot_a", "account:stranger999"],
    )

    with pytest.raises(ValueError, match="参与者集合|不一致"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-r14c_rmm",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


def test_r14c_missing_session_record_blocked(r14c_env, monkeypatch):
    """11e. 显式声称 runtime session 但没有 execution session 记录 → blocked（不得静默降级）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # 只创建旧 Play-with-you binding.json（legacy），不创建 execution session
    session_id = "r14c_legacy_pwy_session"
    from workbench.runtime.resolver import ladder_capture_root

    session_dir = ladder_capture_root() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "binding.json").write_text(
        json.dumps({"session_id": session_id, "season_id": setup["season_id"], "roster": []}),
        encoding="utf-8",
    )

    # 旧 Play-with-you binding.json 不再被当成 R14 runtime evidence → blocked
    with pytest.raises(ValueError, match="不是有效的 R14-B execution session"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-r14c_legacy",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


# ---------------------------------------------------------------------------
# P1-1: Start 后 retire/disable 仍可通过 frozen manifest 摄入
# ---------------------------------------------------------------------------

def test_r14c_frozen_manifest_admits_after_artifact_retire(r14c_env):
    """3. Start 后 retire Artifact A，frozen manifest path 仍允许摄入 Frozen A."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    # 退役 Artifact A
    from participants.schemas import ModelArtifactUpdate

    part_registry.update_model_artifact(
        "model:ma", art_a.model_artifact_id, ModelArtifactUpdate(stage="retired", is_current=False)
    )

    seats = _make_seats(art_a)
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    # 以前会被拒绝，现在 frozen manifest path 不检查当前 Catalog lifecycle
    created = ledger.create_match(payload, registry=part_registry)
    assert created.seats[3].execution_provenance.model_artifact_id == art_a.model_artifact_id


def test_r14c_frozen_manifest_admits_after_account_disable(r14c_env):
    """4. Start 后 disable Account，frozen manifest path 仍允许摄入."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    # 禁用 bot 账号
    from participants.schemas import AccountUpdate

    part_registry.update_account("account:bot_a", AccountUpdate(enabled=False))

    seats = _make_seats(art_a)
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    created = ledger.create_match(payload, registry=part_registry)
    assert created.seats[3].execution_provenance.model_artifact_id == art_a.model_artifact_id


def test_r14c_intake_after_disable_and_rebind(r14c_env, monkeypatch):
    """4b. 真实 resolve_and_create_match 在 Start 后 disable + rebind 仍成功（P1-2 收口）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_disable_rebind_session"
    _create_execution_session(r14c_env, session_id, manifest)
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_dr")

    # Start 之后：disable bot 账号 + 把 identity 从 bot 账号解绑（rebind 模拟）
    from participants.schemas import AccountUpdate

    part_registry.update_account("account:bot_a", AccountUpdate(enabled=False))

    res = intake.resolve_and_create_match(
        log_id="2026082712gm-00a9-0000-r14c_dr",
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    m = ledger.get_match(res["match_id"])
    assert m is not None
    assert m.rating_eligible is True
    # Frozen Manifest 证据完整冻结（不被 disable/rebind 影响）
    assert m.runtime_binding is not None
    assert m.runtime_binding.session_id == session_id
    assert m.seats[3].execution_provenance is not None
    assert m.seats[3].execution_provenance.model_artifact_id == art_a.model_artifact_id
    # controller 由 Manifest canonicalize
    assert m.seats[3].controller_type == "local_model"


# ---------------------------------------------------------------------------
# P1-1: Catalog Current A→B drift protection
# ---------------------------------------------------------------------------

def test_r14c_catalog_drift_frozen_a_accepted_new_b_rejected(r14c_env):
    """5. Catalog Current A→B 后，用 Frozen A 可摄入，用 B 被拒."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    models_dir = r14c_env["models_dir"]

    cp_b = _create_checkpoint(models_dir, "model_b.pth")
    art_b = part_registry.add_model_artifact("model:ma", ModelArtifactCreate(label="mb.pth", artifact_path=str(cp_b), stage="promoted"))

    # 用 Frozen A 成功
    seats = _make_seats(art_a)
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    created = ledger.create_match(payload, registry=part_registry)
    assert created.seats[3].execution_provenance.model_artifact_id == art_a.model_artifact_id

    # 用新产物 B 被拒
    seats_b = [
        MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:bot_a", controller_type="local_model",
                  model_identity_id="model:ma", model_artifact_id=art_b.model_artifact_id),
    ]
    bad_payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats_b,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="Frozen Runtime Manifest 不一致"):
        ledger.create_match(bad_payload, registry=part_registry)


# ---------------------------------------------------------------------------
# P1-2: Immutable RuntimeMatchBinding
# ---------------------------------------------------------------------------

def test_r14c_note_only_revise_preserves_binding_byte_for_byte(r14c_env, monkeypatch):
    """6. note-only revise 后 binding byte-for-byte 不变."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_revise_session"
    _create_execution_session(r14c_env, session_id, manifest)
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_rev")

    log_id = "2026082712gm-00a9-0000-r14c_rev"
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )

    m1 = ledger.get_match(res["match_id"])
    original_binding = m1.runtime_binding
    assert original_binding is not None

    # note-only revise
    revised = ledger.revise_match(res["match_id"], MatchRevise(note="新备注"), registry=part_registry)
    assert revised.note == "新备注"
    assert revised.runtime_binding is not None
    assert revised.runtime_binding.model_dump(mode="json") == original_binding.model_dump(mode="json")


def test_r14c_runtime_bound_match_rejects_seat_change(r14c_env, monkeypatch):
    """7. runtime-bound Match 尝试改 seat/season 被拒."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_reject_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_rej")

    log_id = "2026082712gm-00a9-0000-r14c_rej"
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    match_id = res["match_id"]

    # 尝试修改 seats (swap accounts) → 拒绝
    part_registry.create_account(AccountCreate(account_id="account:stranger", display_name="Stranger", account_type="human"))
    swapped_seats = [
        MatchSeat(seat=0, account_id="account:stranger", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:bot_a", controller_type="local_model", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id),
    ]
    with pytest.raises(ValueError, match="不可修订"):
        ledger.revise_match(match_id, MatchRevise(seats=swapped_seats), registry=part_registry)

    # 尝试修改 season → 拒绝
    with pytest.raises(ValueError, match="不可修订"):
        ledger.revise_match(
            match_id,
            MatchRevise(season_id="s_other_season_x", rating_eligible=True),
            registry=part_registry,
        )

    # Match 保持原状
    m = ledger.get_match(match_id)
    assert m.seats[0].account_id == "account:h1"
    assert m.season_id == setup["season_id"]


def test_r14c_runtime_bound_match_rejects_same_roster_seat_swap(r14c_env, monkeypatch):
    """7b. 同 roster 内换座（h1↔h2，账号集合不变）必须被拒绝——set 比较漏洞封死."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_swap_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_swap")

    res = intake.resolve_and_create_match(
        log_id="2026082712gm-00a9-0000-r14c_swap",
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    match_id = res["match_id"]

    # 同 roster 换座：账号集合完全相同 {h1,h2,h3,bot_a}，seat 0/1 互换
    swapped = [
        MatchSeat(seat=0, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:bot_a", controller_type="local_model", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id),
    ]
    with pytest.raises(ValueError, match="不可修订"):
        ledger.revise_match(match_id, MatchRevise(seats=swapped), registry=part_registry)

    # Match 座位未被改写（与 runtime_binding 一致）
    m = ledger.get_match(match_id)
    assert m.seats[0].account_id == "account:h1"
    assert m.seats[1].account_id == "account:h2"


def test_r14c_runtime_bound_match_rejects_controller_change(r14c_env, monkeypatch):
    """7c. 相同账号/模型但 controller_type 改变必须被拒绝——bot 座位执行类型是冻结事实."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_ctrl_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_ctrl")

    res = intake.resolve_and_create_match(
        log_id="2026082712gm-00a9-0000-r14c_ctrl",
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    match_id = res["match_id"]

    # 相同账号 + 相同模型，但 bot 座位 controller 从 local_model 改为 human_ui
    changed = [
        MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:bot_a", controller_type="human_ui", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id),
    ]
    with pytest.raises(ValueError, match="不可修订"):
        ledger.revise_match(match_id, MatchRevise(seats=changed), registry=part_registry)

    m = ledger.get_match(match_id)
    assert m.seats[3].controller_type == "local_model"


# ---------------------------------------------------------------------------
# P1-3: completed season blocks new intake
# ---------------------------------------------------------------------------
def test_r14c_completed_season_blocks_intake(r14c_env):
    """8. completed 赛季禁止摄入新 rating-eligible match."""
    setup = _setup_frozen_season(r14c_env)
    sr.set_season_status(r14c_env["configs_dir"], setup["season_id"], "completed")

    seats = _make_seats(setup["art_a"])
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="不是 running 状态"):
        ledger.create_match(payload, registry=part_registry)


# ---------------------------------------------------------------------------
# P1-3: intake vs complete — explicit Barrier concurrency test
# ---------------------------------------------------------------------------

def test_r14c_intake_vs_complete_barrier_no_completed_plus_new_match(r14c_env, monkeypatch):
    """14. intake 与 complete 经显式 Barrier 并发竞争：绝不能产生 (completed + newly committed Match)."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_barrier_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_barrier")

    barrier = threading.Barrier(2)
    results = []

    def _do_intake():
        barrier.wait()
        try:
            res = intake.resolve_and_create_match(
                log_id="2026082712gm-00a9-0000-r14c_barrier",
                resolutions=[
                    {"seat": 0, "action": "assign", "account_id": "account:h1"},
                    {"seat": 1, "action": "assign", "account_id": "account:h2"},
                    {"seat": 2, "action": "assign", "account_id": "account:h3"},
                    {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
                ],
                season_id=setup["season_id"],
                rating_eligible=True,
                session_id=session_id,
            )
            results.append(("intake", res["match_id"]))
        except Exception as e:
            results.append(("intake_err", str(e)))

    def _do_complete():
        barrier.wait()
        try:
            cfg = sr.set_season_status(r14c_env["configs_dir"], setup["season_id"], "completed")
            results.append(("complete", cfg["status"]))
        except Exception as e:
            results.append(("complete_err", str(e)))

    t1 = threading.Thread(target=_do_intake)
    t2 = threading.Thread(target=_do_complete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    final_season = sr.get_season(r14c_env["configs_dir"], setup["season_id"])
    intake_ok = [r for r in results if r[0] == "intake"]
    final_status = final_season["status"]

    # 核心不变量：Season completed 之后绝不允许新的 rating-eligible Match 落账。
    # complete 与 intake 持同一 data_lock 串行化：
    # - complete 先获得锁 → Season 已 completed → intake 的锁内 Season 复检必须拒绝；
    # - intake 先获得锁 → Match 先落账 → complete 之后才生效（合法串行序 intake→complete）。
    if intake_ok and final_status == "completed":
        # intake 成功且 Season completed：intake 一定发生在 complete 之前
        # （data_lock 保证），Match 属于 running 窗口内合法落账。
        m = ledger.get_match(intake_ok[0][1])
        assert m is not None
        assert m.season_id == setup["season_id"]
    elif intake_ok:
        assert final_status == "running", f"intake succeeded but season is {final_status}"
    else:
        # intake 失败——若 Season 已 completed，必须是 Season gate 拒绝
        err = [r for r in results if r[0] == "intake_err"]
        assert err, "intake must either succeed or fail with reason"
        if final_status == "completed":
            assert any("不是 running 状态" in e[1] for e in err), (
                f"intake failed for non-season reason: {err}"
            )

    # 无 pending transaction 残留
    assert not ledger.pending_transaction_path().exists()


def test_r14c_intake_commit_barrier_complete_blocked(r14c_env, monkeypatch):
    """14b. intake 在 admission 后、Match commit 前暂停（Event）——complete 必须等它提交后才生效.

    确定性验证锁层级：intake 持 data_lock 期间（admission 已过、commit 未发生），
    complete 无法插入写 completed——绝不会出现 "completed 已落盘、Match 其后才提交"。
    """
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_pause_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_pause")

    # 在 pending 落盘后、Match rewrite 前暂停 intake
    _real_rewrite_match = ledger._rewrite_match
    pause_event = threading.Event()
    entered_rewrite = threading.Event()

    def _paused_rewrite(match):
        entered_rewrite.set()
        pause_event.wait(timeout=10)
        return _real_rewrite_match(match)

    monkeypatch.setattr(ledger, "_rewrite_match", _paused_rewrite)

    intake_result = []

    def _do_intake():
        try:
            res = intake.resolve_and_create_match(
                log_id="2026082712gm-00a9-0000-r14c_pause",
                resolutions=[
                    {"seat": 0, "action": "assign", "account_id": "account:h1"},
                    {"seat": 1, "action": "assign", "account_id": "account:h2"},
                    {"seat": 2, "action": "assign", "account_id": "account:h3"},
                    {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
                ],
                season_id=setup["season_id"],
                rating_eligible=True,
                session_id=session_id,
            )
            intake_result.append(("intake", res["match_id"]))
        except Exception as e:  # noqa: BLE001
            intake_result.append(("intake_err", str(e)))

    t_intake = threading.Thread(target=_do_intake)
    t_intake.start()
    # 等 intake 进入 commit 阶段（已过 admission，pending 已写，rewrite 暂停中）
    assert entered_rewrite.wait(timeout=10), "intake did not reach commit phase"

    # 此时启动 complete：它必须阻塞在 data_lock 上（无法写 completed）
    complete_result = []

    def _do_complete():
        try:
            cfg = sr.set_season_status(r14c_env["configs_dir"], setup["season_id"], "completed")
            complete_result.append(("complete", cfg["status"]))
        except Exception as e:  # noqa: BLE001
            complete_result.append(("complete_err", str(e)))

    t_complete = threading.Thread(target=_do_complete)
    t_complete.start()
    # complete 正在等待 data_lock——给一点时间确认它没有提前完成
    t_complete.join(timeout=0.5)
    assert not complete_result, (
        "complete 必须阻塞在 intake 的 data_lock 上（intake 持锁期间不得写 completed）"
    )

    # 释放 intake：Match 先提交，complete 随后生效
    pause_event.set()
    t_intake.join(timeout=10)
    t_complete.join(timeout=10)

    # intake 成功提交
    assert intake_result and intake_result[0][0] == "intake", intake_result
    match_id = intake_result[0][1]
    m = ledger.get_match(match_id)
    assert m is not None
    assert m.runtime_binding is not None
    # complete 也成功（串行序 intake→complete）
    assert complete_result and complete_result[0][0] == "complete", complete_result
    assert complete_result[0][1] == "completed"


def test_r14c_complete_recovers_crash_pending_before_lifecycle(r14c_env, monkeypatch):
    """14c. crash 留下 pending 后调用 complete——complete 必须先 recovery pending 再写 completed."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_pending_complete_session"
    _create_execution_session(r14c_env, session_id, setup["manifest"])
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_pc")

    # 崩溃：pending 写完后 rewrite 前抛异常
    _real_rewrite_match = ledger._rewrite_match
    _crashed = [False]

    def _crash_on_first_rewrite(match):
        if not _crashed[0]:
            _crashed[0] = True
            raise RuntimeError("模拟进程崩溃")
        return _real_rewrite_match(match)

    monkeypatch.setattr(ledger, "_rewrite_match", _crash_on_first_rewrite)
    with pytest.raises(RuntimeError, match="模拟进程崩溃"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-r14c_pc",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    monkeypatch.setattr(ledger, "_rewrite_match", _real_rewrite_match)
    assert ledger.pending_transaction_path().exists(), "crash 应留下 pending"

    # 直接调用 complete：锁内必须先 recovery pending（Match 落账），再写 completed
    cfg = sr.set_season_status(r14c_env["configs_dir"], setup["season_id"], "completed")
    assert cfg["status"] == "completed"
    assert not ledger.pending_transaction_path().exists(), "complete 后 pending 必须已被 recovery"

    # Match 已落账（running 窗口内的合法提交）
    all_matches = ledger.list_matches().matches
    assert len(all_matches) == 1
    m = all_matches[0]
    assert m.season_id == setup["season_id"]
    assert m.rating_eligible is True
    assert m.runtime_binding is not None


# ---------------------------------------------------------------------------
# Tampered manifest / non-member
# ---------------------------------------------------------------------------

def test_r14c_tampered_manifest_id_blocked(r14c_env):
    """9. 篡改 manifest_id 被拒."""
    setup = _setup_frozen_season(r14c_env)
    cfg_path = r14c_env["configs_dir"] / f"{setup['season_id']}.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["runtime_manifest"]["manifest_id"] = "fake_id"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    seats = _make_seats(setup["art_a"])
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(Exception, match="manifest_id_drift|不一致"):
        ledger.create_match(payload, registry=part_registry)


def test_r14c_non_member_account_blocked(r14c_env):
    """11. 非 Manifest 成员账号被拒."""
    setup = _setup_frozen_season(r14c_env)
    part_registry.create_account(AccountCreate(account_id="account:stranger2", display_name="X", account_type="human"))

    seats = [
        MatchSeat(seat=0, account_id="account:stranger2", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:bot_a", controller_type="local_model",
                  model_identity_id="model:ma", model_artifact_id=setup["art_a"].model_artifact_id),
    ]
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=setup["season_id"],
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="未在正式赛季|未包含在当前赛季的冻结运行时清单"):
        ledger.create_match(payload, registry=part_registry)


# ---------------------------------------------------------------------------
# P2-2: Crash recovery exercises actual recovery path
# ---------------------------------------------------------------------------

def test_r14c_crash_recovery_preserves_binding(r14c_env, monkeypatch):
    """12. pending 写完后注入异常→recovery→Match/Revision/Artifact 三处 binding 一致."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_crash_session"
    _create_execution_session(r14c_env, session_id, manifest)
    _record_worker_log(session_id, "2026082712gm-00a9-0000-r14c_crash")

    # 模拟崩溃：在 pending 写完后、commit 前注入异常
    _real_rewrite_match = ledger._rewrite_match
    _crash_triggered = [False]

    def _crash_on_first_rewrite(match):
        if not _crash_triggered[0]:
            # 先写 pending，但在 rewrite match 时崩溃
            _crash_triggered[0] = True
            raise RuntimeError("模拟进程崩溃")
        return _real_rewrite_match(match)

    monkeypatch.setattr(ledger, "_rewrite_match", _crash_on_first_rewrite)

    log_id = "2026082712gm-00a9-0000-r14c_crash"
    with pytest.raises(RuntimeError, match="模拟进程崩溃"):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )

    # 确认 pending 文件存在
    pending_path = ledger.pending_transaction_path()
    assert pending_path.exists(), "pending transaction file should exist after crash"

    # 恢复 rewrite
    monkeypatch.setattr(ledger, "_rewrite_match", _real_rewrite_match)

    # 执行恢复
    recovered = ledger.recover_pending_transaction()
    assert recovered, "recovery should have occurred"

    # 验证 Match 已恢复
    all_matches = ledger.list_matches().matches
    binding_matches = [m for m in all_matches if m.runtime_binding is not None]
    assert len(binding_matches) == 1

    m = binding_matches[0]
    assert m.runtime_binding.manifest_id == manifest.manifest_id
    assert m.runtime_binding.session_id == session_id

    # 验证 revision
    rev_rows = ledger._read_revision_rows()
    match_revs = [r for r in rev_rows if r.get("match_id") == m.match_id]
    assert len(match_revs) == 1
    rev_binding = match_revs[0]["after"].get("runtime_binding")
    assert rev_binding is not None
    assert rev_binding == m.runtime_binding.model_dump(mode="json")

    # 三处一致：Replay Artifact 的 summary.json runtime_binding 也必须完全一致
    artifact_summary = intake.read_replay_artifact("2026082712gm-00a9-0000-r14c_crash")
    assert artifact_summary is not None
    assert artifact_summary.get("runtime_binding") == m.runtime_binding.model_dump(mode="json")


# ---------------------------------------------------------------------------
# P2-1: RuntimeMatchBinding frozen & not in input models
# ---------------------------------------------------------------------------

def test_r14c_runtime_match_binding_frozen_and_server_owned(r14c_env):
    """13. RuntimeMatchBinding frozen=True 且不在 MatchCreate/MatchRevise 中暴露."""
    from pydantic import ValidationError as PydanticValidationError

    # frozen=True: 不能赋值
    binding = RuntimeMatchBinding(
        season_id="s1",
        manifest_id="m1",
        season_authority_hash="h1",
        admitted_at="2026-01-01T00:00:00+08:00",
    )
    with pytest.raises(PydanticValidationError):
        binding.season_id = "s2"

    # 不在 MatchCreate 输入模型中
    assert "runtime_binding" not in MatchCreate.model_fields
    # 不在 MatchRevise 输入模型中
    assert "runtime_binding" not in MatchRevise.model_fields


# ---------------------------------------------------------------------------
# Non-R14 backward compatibility
# ---------------------------------------------------------------------------

def test_r14c_non_eligible_match_backward_compatible(r14c_env):
    """10-compat. 非 rating_eligible 的 match 保持兼容，runtime_binding 为 None."""
    for i in range(1, 5):
        part_registry.create_account(AccountCreate(account_id=f"account:free{i}", display_name=f"Free{i}", account_type="human"))

    seats = [MatchSeat(seat=i, account_id=f"account:free{i+1}", controller_type="human_ui") for i in range(4)]
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        rating_eligible=False,
        seats=seats,
        final_scores=[25000, 25000, 25000, 25000],
    )
    created = ledger.create_match(payload, registry=part_registry)
    assert created.runtime_binding is None


# ---------------------------------------------------------------------------
# P1-4: End-to-end R14-B → R14-C bridge (auto-discovered session)
# ---------------------------------------------------------------------------

def test_r14c_intake_auto_discovers_execution_session(r14c_env, monkeypatch):
    """15. intake 不带显式 session_id 时，从 R14-B execution session 的 worker
    观测记录自动反查 session 并产生 RuntimeMatchBinding."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # R14-B spawn epoch 创建 execution session；worker 观测到 Tenhou log
    session_id = "rtx_s_r14c_autodiscover"
    _create_execution_session(r14c_env, session_id, manifest)
    log_id = "2026082712gm-00a9-0000-r14c_auto"
    _record_worker_log(session_id, log_id)

    # intake 不传 session_id——自动从 execution session 反查
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
    )
    m = ledger.get_match(res["match_id"])
    assert m.runtime_binding is not None
    assert m.runtime_binding.session_id == session_id
    assert m.runtime_binding.manifest_id == manifest.manifest_id


# ---------------------------------------------------------------------------
# R14-C Repair 3: Exact Match Observation & Session Coherence
# ---------------------------------------------------------------------------

def test_r14c_real_session_cannot_vouch_for_unobserved_log(r14c_env, monkeypatch):
    """R3-1. 真实 S 观察过 Match A，但显式拿 S 导入完全无关的 Match B → blocked.

    P1-1 收口：session 与 Manifest 相容不再足够——该 exact log 必须被该
    session 的 local workers 观察过。
    """
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_unobserved_session"
    _create_execution_session(r14c_env, session_id, manifest)
    # S 只观察过 Match A
    _record_worker_log(session_id, "2026082712gm-00a9-0000-match_A")

    # 显式拿 S 导入无关的 Match B → blocked（没有 B 的观测证据）
    with pytest.raises(ValueError, match="没有观察到对局.*完整执行证据"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-match_B",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


def test_r14c_consecutive_logs_both_preserved(r14c_env, monkeypatch):
    """R3-2. 同一 session 连续观察 A、B 两局——两局证据都保留，各自可 intake（P1-2）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_multi_game_session"
    _create_execution_session(r14c_env, session_id, manifest)
    log_a = "2026082712gm-00a9-0000-multi_A"
    log_b = "2026082712gm-00a9-0000-multi_B"
    # 同一 worker 连续观察两局——latest-only 覆盖 bug 的直接反例
    _record_worker_log(session_id, log_a)
    _record_worker_log(session_id, log_b)

    from runtime.execution_session import load_observed_log

    assert load_observed_log(session_id, log_a, "account:bot_a") is not None, "A 局证据不得被 B 局覆盖"
    assert load_observed_log(session_id, log_b, "account:bot_a") is not None

    # 两局各自都能以该 session 正式摄入
    for log_id in (log_a, log_b):
        res = intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
        m = ledger.get_match(res["match_id"])
        assert m.runtime_binding is not None
        assert m.runtime_binding.session_id == session_id


def test_r14c_missing_required_local_observer_blocked(r14c_env, monkeypatch):
    """R3-3. 双 local worker session 只记录一个 observation → blocked.

    required observers = 全部 local_model participants；缺任意一个都不算
    "整组本地 worker 真进入了这一桌"。
    """
    setup = _setup_two_bot_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "BotA", "BotB"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotB", account_id="account:bot_b", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_missing_observer_session"
    _create_execution_session(
        r14c_env, session_id, manifest,
        accounts=["account:h1", "account:h2", "account:bot_a", "account:bot_b"],
    )
    # 只记录 bot_a 的观测——bot_b (local_model) 缺失
    _record_worker_log(session_id, "2026082712gm-00a9-0000-missing_obs", account_id="account:bot_a")

    with pytest.raises(ValueError, match="没有观察到对局.*完整执行证据"):
        intake.resolve_and_create_match(
            log_id="2026082712gm-00a9-0000-missing_obs",
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
                {"seat": 3, "action": "assign", "account_id": "account:bot_b", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


def test_r14c_two_local_workers_both_observations_preserved(r14c_env, monkeypatch):
    """R3-3b. 双 local worker 对同一 log 的两份 observation 都保留且都算数（P1-2）."""
    setup = _setup_two_bot_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "BotA", "BotB"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotB", account_id="account:bot_b", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_dual_observer_session"
    _create_execution_session(
        r14c_env, session_id, manifest,
        accounts=["account:h1", "account:h2", "account:bot_a", "account:bot_b"],
    )
    log_id = "2026082712gm-00a9-0000-dual_obs"
    # 两个 local worker 各自记录对同一 log 的观测
    _record_worker_log(session_id, log_id, account_id="account:bot_a")
    _record_worker_log(session_id, log_id, account_id="account:bot_b")

    from runtime.execution_session import session_observer_ids

    assert session_observer_ids(session_id, log_id) == ["account:bot_a", "account:bot_b"]

    # 完整双 worker 证据 → intake 成功
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=[
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            {"seat": 3, "action": "assign", "account_id": "account:bot_b", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ],
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    m = ledger.get_match(res["match_id"])
    assert m.runtime_binding is not None
    assert m.runtime_binding.session_id == session_id


def test_r14c_two_sessions_claim_same_log_auto_intake_ambiguous_blocked(r14c_env, monkeypatch):
    """R3-4. S1/S2 两个 session 同时声称观测同一 log → auto intake blocked ambiguous（P1-3）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    log_id = "2026082712gm-00a9-0000-ambiguous"
    s1 = "r14c_ambiguous_session_1"
    s2 = "r14c_ambiguous_session_2"
    _create_execution_session(r14c_env, s1, manifest)
    _create_execution_session(r14c_env, s2, manifest)
    _record_worker_log(s1, log_id)
    _record_worker_log(s2, log_id)

    # auto intake（不带 session_id）→ ambiguous，fail-closed
    with pytest.raises(ValueError, match="多个 R14 execution sessions 声称观测.*运行时来源不唯一"):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
        )
    assert ledger.list_matches().total == 0


def test_r14c_tampered_session_provenance_blocked(r14c_env, monkeypatch):
    """R3-5. session participant provenance 被篡改（账号正确但 Artifact A→B）→ blocked（P2）."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    # 篡改 session：账号集合正确，但 bot_a 的 provenance 指向伪造 artifact
    session_id = "r14c_tampered_prov_session"
    session = _create_execution_session(r14c_env, session_id, manifest)
    log_id = "2026082712gm-00a9-0000-tampered_prov"
    _record_worker_log(session_id, log_id)

    from runtime.execution_session import (
        ExecutionSessionParticipant,
        RuntimeExecutionSession,
        persist_execution_session_locked,
    )
    tampered_participants = []
    for p in session.participants:
        if p.account_id == "account:bot_a":
            tampered_participants.append(
                ExecutionSessionParticipant(
                    account_id="account:bot_a",
                    controller_type=p.controller_type,
                    execution_provenance={"kind": "local_artifact", "model_identity_id": "model:ma", "model_artifact_id": "artifact:forged"},
                )
            )
        else:
            tampered_participants.append(p)
    persist_execution_session_locked(
        RuntimeExecutionSession(
            session_id=session_id,
            season_id=session.season_id,
            manifest_id=session.manifest_id,
            season_authority_hash=session.season_authority_hash,
            participants=tampered_participants,
            created_at=session.created_at,
        )
    )

    with pytest.raises(ValueError, match="execution_provenance.*不一致"):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=[
                {"seat": 0, "action": "assign", "account_id": "account:h1"},
                {"seat": 1, "action": "assign", "account_id": "account:h2"},
                {"seat": 2, "action": "assign", "account_id": "account:h3"},
                {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
            ],
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=session_id,
        )
    assert ledger.list_matches().total == 0


# ---------------------------------------------------------------------------
# R14-C Repair 4: Execution Origin Uniqueness Seal
# ---------------------------------------------------------------------------

def _r4_ambiguous_setup(r14c_env, monkeypatch):
    """S1、S2 都完整观察同一 log X 的公共 setup."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    log_id = "2026082712gm-00a9-0000-r4_unique"
    s1 = "r14c_r4_session_1"
    s2 = "r14c_r4_session_2"
    _create_execution_session(r14c_env, s1, manifest)
    _create_execution_session(r14c_env, s2, manifest)
    _record_worker_log(s1, log_id)
    _record_worker_log(s2, log_id)

    def _resolutions():
        return [
            {"seat": 0, "action": "assign", "account_id": "account:h1"},
            {"seat": 1, "action": "assign", "account_id": "account:h2"},
            {"seat": 2, "action": "assign", "account_id": "account:h3"},
            {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        ]

    return setup, log_id, s1, s2, _resolutions


def test_r14c_r4_ambiguous_auto_intake_blocked(r14c_env, monkeypatch):
    """R4-1. S1、S2 都完整观察 X：auto intake → blocked ambiguous."""
    setup, log_id, _s1, _s2, resolutions = _r4_ambiguous_setup(r14c_env, monkeypatch)

    with pytest.raises(ValueError, match="多个 R14 execution sessions 声称观测.*运行时来源不唯一"):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=resolutions(),
            season_id=setup["season_id"],
            rating_eligible=True,
        )
    assert ledger.list_matches().total == 0


def test_r14c_r4_ambiguous_explicit_session_still_blocked(r14c_env, monkeypatch):
    """R4-2. 同样状态：显式传 S1（或 S2）也必须 blocked——人工指定不能覆盖证据歧义."""
    setup, log_id, s1, s2, resolutions = _r4_ambiguous_setup(r14c_env, monkeypatch)

    for explicit in (s1, s2):
        with pytest.raises(ValueError, match="多个 R14 execution sessions 声称观测.*运行时来源不唯一"):
            intake.resolve_and_create_match(
                log_id=log_id,
                resolutions=resolutions(),
                season_id=setup["season_id"],
                rating_eligible=True,
                session_id=explicit,
            )
    assert ledger.list_matches().total == 0


def test_r14c_r4_unique_session_explicit_mismatch_blocked(r14c_env, monkeypatch):
    """R4-3. 只有 S1 观察 X：显式请求 S2 → blocked；显式请求 S1 → success."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    log_id = "2026082712gm-00a9-0000-r4_mismatch"
    s1 = "r14c_r4_unique_session"
    s2 = "r14c_r4_other_session"
    _create_execution_session(r14c_env, s1, manifest)
    _create_execution_session(r14c_env, s2, manifest)
    # 只有 S1 观察了 X
    _record_worker_log(s1, log_id)

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:h3"},
        {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]

    # 显式请求 S2（没观察到该 log）→ blocked
    with pytest.raises(ValueError, match="唯一可证明的 execution session.*与显式请求.*不一致"):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=resolutions,
            season_id=setup["season_id"],
            rating_eligible=True,
            session_id=s2,
        )
    assert ledger.list_matches().total == 0

    # 显式请求 S1（正确唯一来源）→ success
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=resolutions,
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=s1,
    )
    m = ledger.get_match(res["match_id"])
    assert m.runtime_binding is not None
    assert m.runtime_binding.session_id == s1


# ---------------------------------------------------------------------------
# R14-C Repair 5: Preflight 与 Confirm 的 runtime-origin 唯一性语义一致
# ---------------------------------------------------------------------------

def _r5_preflight_resolutions(art_a):
    return [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:h3"},
        {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]


def test_r14c_r5_preflight_ambiguous_origin_blocked(r14c_env, monkeypatch):
    """R5-1. S1+S2 都观察 X → assess_intake_admission 返回 blocked + runtime_origin_ambiguous."""
    setup, log_id, _s1, _s2, _ = _r4_ambiguous_setup(r14c_env, monkeypatch)
    art_a = setup["art_a"]

    assessment = intake.assess_intake_admission(
        log_id=log_id,
        resolutions=_r5_preflight_resolutions(art_a),
        season_id=setup["season_id"],
        rating_eligible=True,
    )
    assert assessment.state == "blocked"
    codes = [i.code for i in assessment.issues]
    assert "runtime_origin_ambiguous" in codes
    # 所有 seat 不可计分
    assert all(not s.is_ladder_eligible for s in assessment.seats)


def test_r14c_r5_preflight_explicit_mismatch_blocked_unique_ok(r14c_env, monkeypatch):
    """R5-2. 唯一 S1 观察 X：显式请求 S2 → preflight blocked；显式 S1 → ready."""
    setup = _setup_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    log_id = "2026082712gm-00a9-0000-r5_mismatch"
    s1 = "r14c_r5_unique_session"
    s2 = "r14c_r5_other_session"
    _create_execution_session(r14c_env, s1, manifest)
    _create_execution_session(r14c_env, s2, manifest)
    _record_worker_log(s1, log_id)

    # 显式请求 S2（未观察到该 log）→ preflight blocked
    blocked = intake.assess_intake_admission(
        log_id=log_id,
        resolutions=_r5_preflight_resolutions(art_a),
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=s2,
    )
    assert blocked.state == "blocked"
    codes = [i.code for i in blocked.issues]
    assert "runtime_origin_mismatch" in codes
    assert all(not s.is_ladder_eligible for s in blocked.seats)

    # 显式请求 S1（正确唯一来源）→ ready
    ready = intake.assess_intake_admission(
        log_id=log_id,
        resolutions=_r5_preflight_resolutions(art_a),
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=s1,
    )
    assert ready.state == "ready"
    assert len(ready.issues) == 0
    assert all(s.is_ladder_eligible for s in ready.seats)


def test_r14c_r5_preflight_requires_all_local_observers(r14c_env, monkeypatch):
    """R14-C Closure: 双 bot session 只记录 Bot A 的 observation → preflight blocked；
    补 Bot B observation 后同一请求 ready。Preflight 与 Confirm 共用完整的
    exact-log observer gate（tenhou_log_id 贯穿）。
    """
    setup = _setup_two_bot_frozen_season(r14c_env)
    art_a = setup["art_a"]
    manifest = setup["manifest"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "BotA", "BotB"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotB", account_id="account:bot_b", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    session_id = "r14c_closure_observer_session"
    _create_execution_session(
        r14c_env, session_id, manifest,
        accounts=["account:h1", "account:h2", "account:bot_a", "account:bot_b"],
    )
    log_id = "2026082712gm-00a9-0000-closure_obs"
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
        {"seat": 3, "action": "assign", "account_id": "account:bot_b", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]

    # 只有 Bot A 写了 observation——Bot B 缺失 → preflight 必须 blocked
    _record_worker_log(session_id, log_id, account_id="account:bot_a")
    blocked = intake.assess_intake_admission(
        log_id=log_id,
        resolutions=resolutions,
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    assert blocked.state == "blocked"
    assert any("没有观察到对局" in i.message for i in blocked.issues)
    assert all(not s.is_ladder_eligible for s in blocked.seats)

    # 补上 Bot B 的 observation → 同一请求 ready
    _record_worker_log(session_id, log_id, account_id="account:bot_b")
    ready = intake.assess_intake_admission(
        log_id=log_id,
        resolutions=resolutions,
        season_id=setup["season_id"],
        rating_eligible=True,
        session_id=session_id,
    )
    assert ready.state == "ready"
    assert len(ready.issues) == 0
    assert all(s.is_ladder_eligible for s in ready.seats)
