"""R14-D Phase 1 验收测试集: Immutable Season Archive Summary — builder & seal.

测试矩阵：
1. build 确定性：同一 locked source state 重复 build 得到完全相同的 projection/hash；
2. running preview 无副作用：不写任何 registry / runtime 文件；
3. 只读 canonical evidence：Match / Manifest / Execution Sessions / Process
   Ledger；不查询 Participants Catalog Current（disable/rebind/retire 不影响）；
4. 聚合语义与 Match schema 一致：total/active/void/rating-eligible/runtime-bound/
   revision 总数（按本 Season match_id 集合）/ rank_sum 与名次计数（仅整数）；
5. Execution sessions / process ledger 的损坏在 build 阶段 fail-closed；
6. 任一 sealed 字段被篡改 → validator fail-closed；
7. 生命周期规则：draft/running 禁 summary；completed/archived 有则强校验、
   无则 legacy 兼容可读；
8. read-time validator 不重扫外部 evidence（清掉 processes.json / session
   文件后 sealed season 仍可正常读取）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from participants import ledger
from participants import registry as part_registry
from participants.schemas import (
    AccountCreate,
    MatchCreate,
    MatchRevise,
    MatchSeat,
    ModelArtifactCreate,
    ModelIdentityCreate,
)
from replay import season_registry as sr
from replay.ladder import SeasonRegistryError, _validate_registry
from runtime.archive_summary import (
    ArchiveSummaryError,
    build_archive_summary_projection,
    seal_archive_summary,
    validate_persisted_archive_summary,
)
from runtime.execution_session import (
    ExecutionSessionParticipant,
    RuntimeExecutionSession,
    persist_execution_session_locked,
)
from runtime.manifest import (
    get_season_runtime_manifest,
    start_season_runtime,
)


@pytest.fixture
def r14d_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _create_checkpoint(models_dir: Path, name: str) -> Path:
    cp = models_dir / name
    cp.write_text("weights_data", encoding="utf-8")
    return cp


def _setup_running_season(env):
    """running 赛季 + 一场 eligible match + 一个 execution session。"""
    configs_dir = env["configs_dir"]
    tmp_path = env["tmp_path"]
    models_dir = env["models_dir"]

    cp_a = _create_checkpoint(models_dir, "model_a.pth")
    part_registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:ma", label="MA", kind="local_model")
    )
    art_a = part_registry.add_model_artifact(
        "model:ma",
        ModelArtifactCreate(label="ma.pth", artifact_path=str(cp_a), stage="promoted"),
    )
    for i in range(1, 4):
        part_registry.create_account(
            AccountCreate(account_id=f"account:h{i}", display_name=f"Human{i}", account_type="human")
        )
    part_registry.create_account(
        AccountCreate(
            account_id="account:bot_a",
            display_name="BotA",
            account_type="managed_bot",
            model_identity_id="model:ma",
        )
    )

    sr.create_season(configs_dir, season_id="s_r14d", title="R14-D Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_r14d",
        ["account:h1", "account:h2", "account:h3", "account:bot_a"],
        provenance_by_model={
            "model:ma": {
                "kind": "local_artifact",
                "model_identity_id": "model:ma",
                "model_artifact_id": art_a.model_artifact_id,
            }
        },
    )
    start_season_runtime(configs_dir, "s_r14d", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_r14d", project_root=tmp_path)

    # 一场 rating-eligible match（h1 一位、h2 二位、h3 三位、bot_a 四位）
    seats = [
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
    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id="s_r14d",
        rating_eligible=True,
        seats=seats,
        final_scores=[45000, 30000, 15000, 10000],
    )
    match1 = ledger.create_match(payload, registry=part_registry)

    # 一场 revision（测试 revision 总数）
    ledger.revise_match(match1.match_id, MatchRevise(note="note"), registry=part_registry)

    # 一个 execution session
    session = RuntimeExecutionSession(
        session_id="rtx_s_r14d_test_1",
        season_id="s_r14d",
        manifest_id=manifest.manifest_id,
        season_authority_hash=manifest.season_authority_hash,
        participants=[
            ExecutionSessionParticipant(
                account_id=aid,
                controller_type=p.controller_type,
                execution_provenance=p.execution_provenance,
            )
            for aid, p in (
                (p.account_id, p) for p in manifest.participants
            )
        ],
        created_at="2026-08-27T11:00:00+08:00",
    )
    persist_execution_session_locked(session)

    season = sr.get_season(configs_dir, "s_r14d")
    return {
        "season_id": "s_r14d",
        "season": season,
        "manifest": manifest,
        "art_a": art_a,
        "match1": match1,
        "session": session,
    }


# ---------------------------------------------------------------------------
# 1 & 2: deterministic build + running preview has no side effects
# ---------------------------------------------------------------------------

def test_r14d_build_is_deterministic(r14d_env):
    """1. 同一 locked source state 重复 build 得到完全相同的 projection。"""
    setup = _setup_running_season(r14d_env)

    p1 = build_archive_summary_projection(setup["season"])
    p2 = build_archive_summary_projection(setup["season"])
    assert p1 == p2

    # seal 同一 completed_at 两次 → 完全相同 hash/id
    s1 = seal_archive_summary(p1, "2026-08-27T18:00:00+08:00")
    s2 = seal_archive_summary(p2, "2026-08-27T18:00:00+08:00")
    assert s1 == s2
    assert s1.archive_summary_hash == s2.archive_summary_hash
    assert s1.archive_summary_id == s2.archive_summary_id

    # 不同 completed_at → 不同 hash（completed_at 在 seal 内）
    s3 = seal_archive_summary(p1, "2026-08-27T19:00:00+08:00")
    assert s3.archive_summary_hash != s1.archive_summary_hash


def test_r14d_running_preview_no_side_effects(r14d_env):
    """2. running preview 不写任何 registry / runtime 文件。"""
    setup = _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    season_path = configs_dir / "s_r14d.json"
    before_registry = season_path.read_text(encoding="utf-8")
    before_files = sorted(
        str(p.relative_to(r14d_env["ladder_data_dir"]))
        for p in r14d_env["ladder_data_dir"].rglob("*")
        if p.is_file()
    )
    before_data = sorted(
        str(p.relative_to(r14d_env["data_dir"]))
        for p in r14d_env["data_dir"].rglob("*")
        if p.is_file()
    )

    projection = build_archive_summary_projection(setup["season"])
    assert projection["season_id"] == "s_r14d"
    assert projection["manifest_id"] == setup["manifest"].manifest_id

    after_registry = season_path.read_text(encoding="utf-8")
    after_files = sorted(
        str(p.relative_to(r14d_env["ladder_data_dir"]))
        for p in r14d_env["ladder_data_dir"].rglob("*")
        if p.is_file()
    )
    after_data = sorted(
        str(p.relative_to(r14d_env["data_dir"]))
        for p in r14d_env["data_dir"].rglob("*")
        if p.is_file()
    )
    assert before_registry == after_registry
    assert before_files == after_files
    assert before_data == after_data


# ---------------------------------------------------------------------------
# 3 & 4: canonical evidence only + aggregation semantics
# ---------------------------------------------------------------------------

def test_r14d_aggregation_semantics(r14d_env):
    """3/4. 聚合语义与 Match schema 一致。"""
    setup = _setup_running_season(r14d_env)
    season = setup["season"]

    # 补一场 void + 一场 non-eligible + 一场其他赛季的 match
    seats = [
        MatchSeat(seat=i, account_id=f"account:h{i+1}", controller_type="human_ui")
        for i in range(3)
    ] + [
        MatchSeat(
            seat=3,
            account_id="account:bot_a",
            controller_type="local_model",
            model_identity_id="model:ma",
            model_artifact_id=setup["art_a"].model_artifact_id,
        )
    ]
    payload2 = MatchCreate(
        occurred_at="2026-08-27T13:00:00+08:00",
        game_length="hanchan",
        season_id="s_r14d",
        rating_eligible=True,
        seats=seats,
        final_scores=[25000, 25000, 25000, 25000],
    )
    match2 = ledger.create_match(payload2, registry=part_registry)
    ledger.void_match(match2.match_id, __import__("participants.schemas", fromlist=["MatchVoid"]).MatchVoid(reason="test"))

    payload3 = MatchCreate(
        occurred_at="2026-08-27T14:00:00+08:00",
        game_length="hanchan",
        season_id=None,
        rating_eligible=False,
        seats=[
            MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
            MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
            MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
            MatchSeat(seat=3, account_id="account:bot_a", controller_type="local_model", model_identity_id="model:ma", model_artifact_id=setup["art_a"].model_artifact_id),
        ],
        final_scores=[25000, 25000, 25000, 25000],
    )
    ledger.create_match(payload3, registry=part_registry)

    season = sr.get_season(r14d_env["configs_dir"], "s_r14d")
    projection = build_archive_summary_projection(season)
    ms = projection["match_summary"]

    # total = 2（本 season）；非本 season 的 match 不算
    assert ms["total_matches"] == 2
    assert ms["active_matches"] == 1
    assert ms["void_matches"] == 1
    assert ms["rating_eligible_matches"] == 2
    # match1（无 runtime session → runtime_binding=None）+ match2（void 但也曾 eligible）
    assert ms["runtime_bound_matches"] == 0

    # revision 总数：match1 有 create+revise 两行；match2 有 create+void 两行
    assert ms["total_revisions"] == 4

    assert ms["first_match_occurred_at"] == "2026-08-27T12:00:00+08:00"
    assert ms["last_match_occurred_at"] == "2026-08-27T13:00:00+08:00"

    # account 聚合：只有 match1（active+eligible）计入；match2 已 void
    # ranks 是 0-based（0 = 一位）
    stats = {s["account_id"]: s for s in ms["account_stats"]}
    assert set(stats) == {"account:h1", "account:h2", "account:h3", "account:bot_a"}
    h1 = stats["account:h1"]
    assert h1["games"] == 1
    assert h1["rank_sum"] == 0
    assert h1["first_places"] == 1
    assert h1["second_places"] == 0
    bot_a = stats["account:bot_a"]
    assert bot_a["games"] == 1
    assert bot_a["rank_sum"] == 3
    assert bot_a["fourth_places"] == 1

    # operation summary：1 个 session（4 participants、1 local_model）
    op = projection["operation_summary"]
    assert op["session_count"] == 1
    sess = op["sessions"][0]
    assert sess["session_id"] == "rtx_s_r14d_test_1"
    assert sess["participant_count"] == 4
    assert sess["local_model_participants"] == 1
    # processes.json 不存在 → processes 为空列表
    assert op["processes"] == []


def test_r14d_catalog_drift_does_not_affect_summary(r14d_env):
    """3. Start 后 Catalog Current 变化（retire/disable/rebind）不影响 build。"""
    setup = _setup_running_season(r14d_env)
    art_a = setup["art_a"]

    projection_before = build_archive_summary_projection(setup["season"])

    from participants.schemas import AccountUpdate, ModelArtifactUpdate

    part_registry.update_model_artifact(
        "model:ma", art_a.model_artifact_id,
        ModelArtifactUpdate(stage="retired", is_current=False),
    )
    part_registry.update_account("account:bot_a", AccountUpdate(enabled=False))

    season = sr.get_season(r14d_env["configs_dir"], "s_r14d")
    projection_after = build_archive_summary_projection(season)
    assert projection_before == projection_after


# ---------------------------------------------------------------------------
# 5: corrupted evidence fails closed at build time
# ---------------------------------------------------------------------------

def test_r14d_corrupted_process_ledger_fails_closed(r14d_env):
    """5a. processes.json 损坏 → build 阶段 fail-closed。"""
    setup = _setup_running_season(r14d_env)
    season = setup["season"]

    from runtime.supervisor import runtime_records_file

    records_path = runtime_records_file("s_r14d")
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text("{corrupted", encoding="utf-8")

    with pytest.raises(ArchiveSummaryError, match="损坏"):
        build_archive_summary_projection(season)


def test_r14d_corrupted_match_row_fails_closed(r14d_env):
    """5b. Match Ledger 损坏行 → build 阶段 fail-closed（无 silent skip）。"""
    setup = _setup_running_season(r14d_env)
    season = setup["season"]

    # 直接注入一条非法 revision 总数来源之外的损坏 match 行：
    # ranks 与 seats 数量不匹配（active+eligible 时聚合会 fail）
    matches_path = r14d_env["data_dir"] / "matches.jsonl"
    bad_row = {
        "schema": "keqing.participant.match.v1",
        "match_id": "m_bad",
        "occurred_at": "2026-08-27T15:00:00+08:00",
        "season_id": "s_r14d",
        "rating_eligible": True,
        "status": "active",
        "seats": [
            {"seat": i, "account_id": f"account:h{i+1}"}
            for i in range(4)
        ],
        "ranks": [1, 2, 3],  # 只有 3 个 rank
    }
    with open(matches_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad_row, ensure_ascii=False) + "\n")

    with pytest.raises(ArchiveSummaryError, match="不是 4 座"):
        build_archive_summary_projection(season)


# ---------------------------------------------------------------------------
# 6: tamper detection via validator
# ---------------------------------------------------------------------------

def _sealed_season_payload(env, setup, completed_at="2026-08-27T18:00:00+08:00"):
    """构造一个完整 sealed completed season payload（不落盘）。"""
    projection = build_archive_summary_projection(setup["season"])
    summary = seal_archive_summary(projection, completed_at)
    season = dict(setup["season"])
    season["status"] = "completed"
    season["completed_at"] = completed_at
    season["archive_summary"] = summary.model_dump()
    season["default"] = False
    return season, summary


def test_r14d_validator_accepts_sealed_summary(r14d_env):
    """6a. 合法 sealed summary 通过 validator 与 _validate_registry。"""
    setup = _setup_running_season(r14d_env)
    season, summary = _sealed_season_payload(r14d_env, setup)

    validated = validate_persisted_archive_summary(
        season, season["archive_summary"]
    )
    assert validated == summary
    # registry-level hook 也通过
    assert _validate_registry(season, "s_r14d.json") == season


@pytest.mark.parametrize(
    "tamper",
    [
        "archive_summary_hash",
        "archive_summary_id",
        "completed_at",
        "manifest_id",
        "season_id",
        "match_summary",
        "operation_summary",
    ],
)
def test_r14d_validator_rejects_tampering(r14d_env, tamper):
    """6b. 任一 sealed 字段被篡改 → validator fail-closed。"""
    setup = _setup_running_season(r14d_env)
    season, _summary = _sealed_season_payload(r14d_env, setup)
    raw = season["archive_summary"]

    if tamper in ("match_summary", "operation_summary"):
        raw[tamper]["total_matches" if tamper == "match_summary" else "session_count"] += 1
    else:
        raw[tamper] = "tampered" if tamper != "completed_at" else "2020-01-01T00:00:00+08:00"

    with pytest.raises((ArchiveSummaryError, SeasonRegistryError)):
        validate_persisted_archive_summary(season, raw)

    # registry-level hook 同样 fail-closed
    bad_season = dict(season)
    bad_season["archive_summary"] = raw
    with pytest.raises(SeasonRegistryError):
        _validate_registry(bad_season, "s_r14d.json")


def test_r14d_validator_rejects_completed_at_mismatch(r14d_env):
    """6c. summary.completed_at 与 season.completed_at 不一致 → fail-closed。"""
    setup = _setup_running_season(r14d_env)
    season, _ = _sealed_season_payload(r14d_env, setup)

    mismatched = dict(season)
    mismatched["completed_at"] = "2026-08-27T20:00:00+08:00"
    with pytest.raises(ArchiveSummaryError, match="completed_at"):
        validate_persisted_archive_summary(mismatched, season["archive_summary"])


# ---------------------------------------------------------------------------
# 7: lifecycle rules
# ---------------------------------------------------------------------------

def test_r14d_lifecycle_rules(r14d_env):
    """7. draft/running 禁 summary；completed/archived 有则强校验、无则 legacy。"""
    setup = _setup_running_season(r14d_env)

    # running 赛季带 summary → 拒绝
    bad_running = dict(setup["season"])
    bad_running["archive_summary"] = {"schema_version": "keqing.season.archive_summary.v1"}
    with pytest.raises(SeasonRegistryError, match="不允许包含 archive_summary"):
        _validate_registry(bad_running, "s_r14d.json")

    # legacy completed 无 summary → 允许读取
    legacy = dict(setup["season"])
    legacy["status"] = "completed"
    assert _validate_registry(legacy, "s_r14d.json") == legacy

    # legacy archived 无 summary → 允许读取
    legacy_archived = dict(legacy)
    legacy_archived["status"] = "archived"
    assert _validate_registry(legacy_archived, "s_r14d.json") == legacy_archived


# ---------------------------------------------------------------------------
# 8: read-time validator never rescans external evidence
# ---------------------------------------------------------------------------

def test_r14d_read_time_survives_evidence_cleanup(r14d_env):
    """8. 清掉 execution session / processes.json 后 sealed season 仍可读取。"""
    setup = _setup_running_season(r14d_env)
    season, _ = _sealed_season_payload(r14d_env, setup)
    raw = season["archive_summary"]

    # 篡改前先正常通过
    validate_persisted_archive_summary(season, raw)

    # 模拟日后 runtime evidence cleanup：删除 execution session 文件
    from runtime.execution_session import execution_sessions_root

    for p in execution_sessions_root().iterdir():
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    f.unlink()
            p.rmdir()

    # validator 仍然通过——它只验证 persisted sealed summary 与 season binding
    validate_persisted_archive_summary(season, raw)

    # registry-level hook 同样通过（completed + 有 summary 强校验但不重扫）
    assert _validate_registry(season, "s_r14d.json") == season


def test_r14d_seal_excludes_only_self_referencing_fields(r14d_env):
    """seal projection 覆盖全部最终历史字段（含 completed_at），仅排除自引用。"""
    from runtime.archive_summary import build_archive_seal_projection

    setup = _setup_running_season(r14d_env)
    projection = build_archive_summary_projection(setup["season"])
    summary = seal_archive_summary(projection, "2026-08-27T18:00:00+08:00")
    payload = summary.model_dump()

    seal_projection = build_archive_seal_projection(payload)
    assert "completed_at" in seal_projection
    assert "archive_summary_hash" not in seal_projection
    assert "archive_summary_id" not in seal_projection
    assert set(seal_projection) == {
        "schema_version",
        "season_id",
        "manifest_id",
        "season_authority_hash",
        "match_summary",
        "operation_summary",
        "completed_at",
    }


# ---------------------------------------------------------------------------
# R14-D Phase 2: finalize_season + sealed lifecycle rules
# ---------------------------------------------------------------------------

def test_r14d_finalize_season_seals_summary(r14d_env):
    """P2-1. finalize_season: running → completed，一次性原子写 status/completed_at/
    archive_summary/default，summary seal 自校验通过。"""
    setup = _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    finalized = sr.finalize_season(configs_dir, "s_r14d")
    assert finalized["status"] == "completed"
    assert finalized["completed_at"] == finalized["updated_at"]
    assert finalized["default"] is not True

    summary = finalized["archive_summary"]
    validated = validate_persisted_archive_summary(finalized, summary)
    assert validated.manifest_id == setup["manifest"].manifest_id
    assert validated.completed_at == finalized["completed_at"]
    assert validated.match_summary.total_matches == 1
    assert validated.match_summary.rating_eligible_matches == 1
    assert validated.operation_summary.session_count == 1

    # registry 读取（含 seal 校验 hook）正常
    reloaded = sr.get_season(configs_dir, "s_r14d")
    assert reloaded["archive_summary"] == summary


def test_r14d_finalize_requires_running(r14d_env):
    """P2-2. 非 running 赛季不能 finalize。"""
    _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    sr.finalize_season(configs_dir, "s_r14d")
    with pytest.raises(sr.SeasonRegistryError, match="只能 finalize running"):
        sr.finalize_season(configs_dir, "s_r14d")

    # draft 赛季也不能
    sr.create_season(configs_dir, season_id="s_draft_d", title="Draft")
    with pytest.raises(sr.SeasonRegistryError, match="只能 finalize running"):
        sr.finalize_season(configs_dir, "s_draft_d")


def test_r14d_finalize_rejects_legacy_without_manifest(r14d_env):
    """P2-3. 无 runtime_manifest 的 legacy 赛季不能 finalize。"""
    configs_dir = r14d_env["configs_dir"]
    sr.create_season(configs_dir, season_id="s_legacy_d", title="Legacy")
    with pytest.raises(sr.SeasonRegistryError, match="只能 finalize running"):
        sr.finalize_season(configs_dir, "s_legacy_d")


def test_r14d_sealed_reopen_forbidden(r14d_env):
    """P2-4. 带 archive_summary 的 completed 赛季 reopen → 拒绝；legacy 无
    summary 的 completed 保留旧 reopen 行为。"""
    _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    sr.finalize_season(configs_dir, "s_r14d")
    with pytest.raises(sr.SeasonRegistryError, match="不可 reopen"):
        sr.set_season_status(configs_dir, "s_r14d", "running")

    # legacy completed（无 summary）仍可 reopen
    sr.create_season(configs_dir, season_id="s_legacy_reopen", title="Legacy Reopen")
    legacy = sr.get_season(configs_dir, "s_legacy_reopen")
    legacy["status"] = "completed"
    legacy["updated_at"] = "2026-08-27T00:00:00+08:00"
    (configs_dir / "s_legacy_reopen.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    reopened = sr.set_season_status(configs_dir, "s_legacy_reopen", "running")
    assert reopened["status"] == "running"
    assert "reopened_at" in reopened


def test_r14d_running_to_archived_bypass_forbidden_for_frozen(r14d_env):
    """P2-5. frozen 赛季 running → archived 直接绕过 summary → 拒绝。"""
    _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    with pytest.raises(sr.SeasonRegistryError, match="不能从 running 直接归档"):
        sr.set_season_status(configs_dir, "s_r14d", "archived")

    # 赛季仍是 running（未受影响）
    assert sr.get_season(configs_dir, "s_r14d")["status"] == "running"


def test_r14d_completed_to_archived_preserves_summary_byte_for_byte(r14d_env):
    """P2-6. completed → archived：archive_summary 与 completed_at byte-for-byte 不变。"""
    _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    finalized = sr.finalize_season(configs_dir, "s_r14d")
    frozen_summary = json.dumps(finalized["archive_summary"], sort_keys=True)
    frozen_completed_at = finalized["completed_at"]

    archived = sr.set_season_status(configs_dir, "s_r14d", "archived")
    assert archived["status"] == "archived"
    assert json.dumps(archived["archive_summary"], sort_keys=True) == frozen_summary
    assert archived["completed_at"] == frozen_completed_at
    # summary seal 仍可校验（completed/archived 有 summary 强校验）
    validate_persisted_archive_summary(archived, archived["archive_summary"])

    # archived 永不 reopen
    with pytest.raises(sr.SeasonRegistryError):
        sr.set_season_status(configs_dir, "s_r14d", "running")


def test_r14d_finalize_after_complete_blocked_intake(r14d_env):
    """P2-7. finalize 后 intake 被 Season gate 拒绝（终局摘要不被新 match 污染）。"""
    setup = _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]
    art_a = setup["art_a"]

    finalized = sr.finalize_season(configs_dir, "s_r14d")
    frozen_summary = json.dumps(finalized["archive_summary"], sort_keys=True)

    seats = [
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
    payload = MatchCreate(
        occurred_at="2026-08-27T16:00:00+08:00",
        game_length="hanchan",
        season_id="s_r14d",
        rating_eligible=True,
        seats=seats,
        final_scores=[25000, 25000, 25000, 25000],
    )
    with pytest.raises(ValueError, match="不是 running"):
        ledger.create_match(payload, registry=part_registry)

    # summary 不变
    after = sr.get_season(configs_dir, "s_r14d")
    assert json.dumps(after["archive_summary"], sort_keys=True) == frozen_summary


def test_r14d_finalize_immutability_under_catalog_drift(r14d_env):
    """P2-8. finalize 后 Catalog retire/disable/rebind → summary 不变。"""
    setup = _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]
    art_a = setup["art_a"]

    finalized = sr.finalize_season(configs_dir, "s_r14d")
    frozen_summary = json.dumps(finalized["archive_summary"], sort_keys=True)

    from participants.schemas import AccountUpdate, ModelArtifactUpdate

    part_registry.update_model_artifact(
        "model:ma", art_a.model_artifact_id,
        ModelArtifactUpdate(stage="retired", is_current=False),
    )
    part_registry.update_account("account:bot_a", AccountUpdate(enabled=False))

    after = sr.get_season(configs_dir, "s_r14d")
    assert json.dumps(after["archive_summary"], sort_keys=True) == frozen_summary


def test_r14d_finalize_immutability_under_runtime_cleanup(r14d_env):
    """P2-9. finalize 后 runtime evidence 清理（删 session 文件）→ sealed summary 不变且可读。"""
    _setup_running_season(r14d_env)
    configs_dir = r14d_env["configs_dir"]

    finalized = sr.finalize_season(configs_dir, "s_r14d")
    frozen_summary = json.dumps(finalized["archive_summary"], sort_keys=True)

    # 模拟 runtime cleanup：删除 execution session evidence
    from runtime.execution_session import execution_sessions_root

    for p in execution_sessions_root().iterdir():
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    f.unlink()
            p.rmdir()

    after = sr.get_season(configs_dir, "s_r14d")
    assert json.dumps(after["archive_summary"], sort_keys=True) == frozen_summary
    validate_persisted_archive_summary(after, after["archive_summary"])


# ---------------------------------------------------------------------------
# R14-D Phase 3: read-only API (sealed vs preview)
# ---------------------------------------------------------------------------

def _r14d_api_setup(env):
    """running frozen season setup（复用）+ server module 指向测试 configs。"""
    _setup_running_season(env)
    from replay import server

    env["_server"] = server


def test_r14d_api_archive_summary_preview_for_running(r14d_env):
    """P3-1. running 赛季 → kind=preview 的实时 projection（无 completed_at/seal）。"""
    _r14d_api_setup(r14d_env)
    from replay import server

    # monkeypatch server 的 configs dir 指向测试环境
    original_dir = server._LADDER_SEASONS_DIR
    server._LADDER_SEASONS_DIR = r14d_env["configs_dir"]
    try:
        import asyncio

        resp = asyncio.run(server.get_ladder_season_archive_summary("s_r14d"))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["kind"] == "preview"
        assert body["season_status"] == "running"
        assert "archive_summary_projection" in body
        assert "completed_at" not in body
        assert "archive_summary_hash" not in body["archive_summary_projection"]
        assert body["archive_summary_projection"]["season_id"] == "s_r14d"
    finally:
        server._LADDER_SEASONS_DIR = original_dir


def test_r14d_api_archive_summary_sealed_after_finalize(r14d_env):
    """P3-2. finalize 后 → kind=sealed 返回 persisted summary（不实时重算）。"""
    _r14d_api_setup(r14d_env)
    from replay import server

    original_dir = server._LADDER_SEASONS_DIR
    server._LADDER_SEASONS_DIR = r14d_env["configs_dir"]
    try:
        import asyncio

        # finalize via API endpoint
        resp = asyncio.run(server.finalize_ladder_season("s_r14d"))
        assert resp.status_code == 200
        sealed_season = json.loads(resp.body)
        assert sealed_season["status"] == "completed"

        # sealed summary endpoint
        resp2 = asyncio.run(server.get_ladder_season_archive_summary("s_r14d"))
        assert resp2.status_code == 200
        body = json.loads(resp2.body)
        assert body["kind"] == "sealed"
        assert body["season_status"] == "completed"
        assert body["archive_summary"] == sealed_season["archive_summary"]
        assert body["completed_at"] == sealed_season["completed_at"]

        # runtime evidence 清理后 sealed summary 仍可读（persisted，不重算）
        from runtime.execution_session import execution_sessions_root

        for p in execution_sessions_root().iterdir():
            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        f.unlink()
                p.rmdir()
        resp3 = asyncio.run(server.get_ladder_season_archive_summary("s_r14d"))
        assert resp3.status_code == 200
        body3 = json.loads(resp3.body)
        assert body3["archive_summary"] == sealed_season["archive_summary"]
    finally:
        server._LADDER_SEASONS_DIR = original_dir


def test_r14d_api_archive_summary_legacy_unsealed(r14d_env):
    """P3-3. legacy completed（无 summary）→ 409 提示未封存。"""
    _r14d_api_setup(r14d_env)
    from replay import server

    original_dir = server._LADDER_SEASONS_DIR
    server._LADDER_SEASONS_DIR = r14d_env["configs_dir"]
    try:
        import asyncio

        sr.create_season(r14d_env["configs_dir"], season_id="s_legacy_api", title="L")
        legacy = sr.get_season(r14d_env["configs_dir"], "s_legacy_api")
        legacy["status"] = "completed"
        (r14d_env["configs_dir"] / "s_legacy_api.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
        )
        resp = asyncio.run(server.get_ladder_season_archive_summary("s_legacy_api"))
        assert resp.status_code == 409
        assert "未封存" in json.loads(resp.body)["error"]
    finally:
        server._LADDER_SEASONS_DIR = original_dir


def test_r14d_api_archive_summary_draft_rejected(r14d_env):
    """P3-4. draft 赛季 → 409。"""
    _r14d_api_setup(r14d_env)
    from replay import server

    original_dir = server._LADDER_SEASONS_DIR
    server._LADDER_SEASONS_DIR = r14d_env["configs_dir"]
    try:
        import asyncio

        sr.create_season(r14d_env["configs_dir"], season_id="s_draft_api", title="D")
        resp = asyncio.run(server.get_ladder_season_archive_summary("s_draft_api"))
        assert resp.status_code == 409
    finally:
        server._LADDER_SEASONS_DIR = original_dir


def test_r14d_api_finalize_error_mapping(r14d_env):
    """P3-5. finalize 非 running / 不存在的赛季 → 409 / 404。"""
    _r14d_api_setup(r14d_env)
    from replay import server

    original_dir = server._LADDER_SEASONS_DIR
    server._LADDER_SEASONS_DIR = r14d_env["configs_dir"]
    try:
        import asyncio

        # 不存在 → 404
        resp = asyncio.run(server.finalize_ladder_season("s_nope"))
        assert resp.status_code == 404

        # 已 finalize → 再 finalize 409
        resp = asyncio.run(server.finalize_ladder_season("s_r14d"))
        assert resp.status_code == 200
        resp = asyncio.run(server.finalize_ladder_season("s_r14d"))
        assert resp.status_code == 409
    finally:
        server._LADDER_SEASONS_DIR = original_dir
