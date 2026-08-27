# -*- coding: utf-8 -*-
"""R14-C 验收测试集: Live Match Intake & Runtime Provenance Binding

测试矩阵：
1. running Season + persisted frozen Manifest 产生包含完整 RuntimeMatchBinding 的 Match、Revision 与 Replay Artifact；
2. match 四座账号与 seat provenance 必须精确等于 Manifest participant provenance；
3. Catalog Current 随后切换（A -> B），对局摄入与已落账 Match 依然严格记录 Frozen A 的 binding 与 provenance；
4. 篡改或不匹配的 Manifest ID 直接被 blocked / 拒绝摄入；
5. completed 或 archived 赛季禁止摄入新的 rating_eligible runtime match；
6. 摄入幂等性：同一 external_match_id (log_id) 重复 intake 抛出 DuplicateMatchError；
7. Intake Pending 崩溃恢复测试：崩溃在 pending 落盘后，恢复流程正确补全 Match、Revision、Artifact 且 runtime_binding 完整无损；
8. 非 R14 / 历史 / rating_eligible=False 的 match 保持兼容，runtime_binding 为 None。
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from participants import aliases, intake, ledger
from participants import registry as part_registry
from participants.schemas import (
    AccountCreate,
    ExternalAliasCreate,
    MatchCreate,
    MatchSeat,
    ModelArtifactCreate,
    ModelIdentityCreate,
    SeatResolution,
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

    started = start_season_runtime(configs_dir, "s_r14c", project_root=tmp_path)
    manifest = get_season_runtime_manifest(configs_dir, "s_r14c", project_root=tmp_path)
    return {
        "season_id": "s_r14c",
        "art_a": art_a,
        "cp_a": cp_a,
        "manifest": manifest,
    }


def test_r14c_match_create_freezes_runtime_match_binding(r14c_env):
    """1 & 2. 正常摄入正式对局，产生完整 RuntimeMatchBinding，且与 Manifest 完全一致."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    manifest = setup["manifest"]
    art_a = setup["art_a"]

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

    create_payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )

    created = ledger.create_match(create_payload, registry=part_registry)
    assert created.runtime_binding is not None
    binding = created.runtime_binding
    assert binding.season_id == season_id
    assert binding.manifest_id == manifest.manifest_id
    assert binding.season_authority_hash == manifest.season_authority_hash
    assert binding.seat_accounts[0] == "account:h1"
    assert binding.seat_accounts[3] == "account:bot_a"
    assert binding.seat_provenance[3].model_artifact_id == art_a.model_artifact_id


def test_r14c_catalog_current_switch_does_not_drift_match_binding(r14c_env):
    """3. 摄入前/后 Catalog Current 升级（A -> B），摄入与已落账 Match 依然绑定 Frozen A."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    models_dir = r14c_env["models_dir"]
    art_a = setup["art_a"]

    # Catalog 引入新模型产物 B 并 promote 为 promoted/current
    cp_b = _create_checkpoint(models_dir, "model_b.pth")
    art_b = part_registry.add_model_artifact("model:ma", ModelArtifactCreate(label="mb.pth", artifact_path=str(cp_b), stage="promoted"))

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

    create_payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )

    created = ledger.create_match(create_payload, registry=part_registry)
    assert created.runtime_binding.seat_provenance[3].model_artifact_id == art_a.model_artifact_id

    # 尝试用 Catalog 新产物 B 摄入该赛季对局 -> 必须被拒绝（与 Frozen Manifest 不一致）
    seats_with_b = [
        MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(
            seat=3,
            account_id="account:bot_a",
            controller_type="local_model",
            model_identity_id="model:ma",
            model_artifact_id=art_b.model_artifact_id,
        ),
    ]
    bad_payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=season_id,
        rating_eligible=True,
        seats=seats_with_b,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="与目标 checkpoint.*不一致|与赛季冻结产物|与 Frozen Runtime Manifest 不一致"):
        ledger.create_match(bad_payload, registry=part_registry)


def test_r14c_completed_or_archived_season_blocks_intake(r14c_env):
    """5. 赛季结束 (completed) 或归档 (archived) 后，禁止摄入新的 rating_eligible match."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    configs_dir = r14c_env["configs_dir"]
    art_a = setup["art_a"]

    # 停止赛季并置 completed
    sr.set_season_status(configs_dir, season_id, "completed")

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
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="不是 running 状态"):
        ledger.create_match(payload, registry=part_registry)


def test_r14c_intake_pending_crash_recovery_preserves_binding(r14c_env, monkeypatch: pytest.MonkeyPatch):
    """7. Intake pending 崩溃恢复流程：恢复后 Match、Revision、Replay Artifact 的 runtime_binding 完整无损."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    manifest = setup["manifest"]
    art_a = setup["art_a"]

    # 构造假 Tenhou 下载数据
    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    # 为各座位注册别名
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:h3"},
        {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]

    log_id = "2026082712gm-00a9-0000-r14ctest"
    # 执行 intake confirm
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=resolutions,
        season_id=season_id,
        rating_eligible=True,
    )
    match_id = res["match_id"]

    # 校验落账 Match
    m = ledger.get_match(match_id)
    assert m is not None
    assert m.runtime_binding is not None
    assert m.runtime_binding.manifest_id == manifest.manifest_id
    assert m.runtime_binding.seat_provenance[3].model_artifact_id == art_a.model_artifact_id


def test_r14c_intake_match_revision_artifact_triple_consistency(r14c_env, monkeypatch: pytest.MonkeyPatch):
    """9. Match / Revision / Replay Artifact 三处 RuntimeMatchBinding 精确一致."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    manifest = setup["manifest"]
    art_a = setup["art_a"]

    fake_tenhou6 = {"title": ["", ""], "name": ["H1", "H2", "H3", "BotA"], "dan": [0, 0, 0, 0], "rate": [1500, 1500, 1500, 1500], "sx": ["C", "C", "C", "C"]}
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: fake_tenhou6)
    monkeypatch.setattr(intake, "tenhou6_events", lambda t6: [])
    monkeypatch.setattr(intake, "hand_summaries", lambda ev: [])

    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H1", account_id="account:h1"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H2", account_id="account:h2"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="H3", account_id="account:h3"))
    aliases.register_alias(ExternalAliasCreate(provider="tenhou", external_id="BotA", account_id="account:bot_a", model_identity_id="model:ma", model_artifact_id=art_a.model_artifact_id))

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:h1"},
        {"seat": 1, "action": "assign", "account_id": "account:h2"},
        {"seat": 2, "action": "assign", "account_id": "account:h3"},
        {"seat": 3, "action": "assign", "account_id": "account:bot_a", "model_identity_id": "model:ma", "model_artifact_id": art_a.model_artifact_id},
    ]

    log_id = "2026082712gm-00a9-0000-r14c_triple"
    res = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=resolutions,
        season_id=season_id,
        rating_eligible=True,
    )
    match_id = res["match_id"]

    # 1. Check Match
    m = ledger.get_match(match_id)
    assert m.runtime_binding is not None
    m_binding = m.runtime_binding.model_dump(mode="json")

    # 2. Check Revision
    rev_rows = ledger._read_revision_rows()
    match_revs = [r for r in rev_rows if r.get("match_id") == match_id]
    assert len(match_revs) == 1
    rev_after = match_revs[0]["after"]
    assert rev_after.get("runtime_binding") == m_binding

    # 3. Check Replay Artifact summary
    artifact_summary = intake.read_replay_artifact(log_id)
    assert artifact_summary is not None
    assert artifact_summary.get("runtime_binding") == m_binding

    # 4. Duplicate intake test (Criterion 7)
    with pytest.raises(intake.DuplicateMatchError):
        intake.resolve_and_create_match(
            log_id=log_id,
            resolutions=resolutions,
            season_id=season_id,
            rating_eligible=True,
        )


def test_r14c_tampered_manifest_id_fails_closed(r14c_env):
    """4 & 5. 赛季中的 runtime_manifest 被外部篡改 (manifest_id / hash 不符) 时，摄入立即 blocked / 报错."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    configs_dir = r14c_env["configs_dir"]
    art_a = setup["art_a"]

    # 篡改 season config 中的 runtime_manifest
    season_cfg_path = configs_dir / f"{season_id}.json"
    cfg = json.loads(season_cfg_path.read_text(encoding="utf-8"))
    cfg["runtime_manifest"]["manifest_id"] = "fake_tampered_manifest_id"
    season_cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

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
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(Exception, match="manifest_id_drift|与 participants 内容推导值.*不一致"):
        ledger.create_match(payload, registry=part_registry)


def test_r14c_seat_not_in_manifest_fails_closed(r14c_env):
    """2. 对局四座账号与 Manifest 不对应时，摄入被拒绝."""
    setup = _setup_frozen_season(r14c_env)
    season_id = setup["season_id"]
    art_a = setup["art_a"]

    part_registry.create_account(AccountCreate(account_id="account:stranger", display_name="Stranger", account_type="human"))

    seats = [
        MatchSeat(seat=0, account_id="account:stranger", controller_type="human_ui"),
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
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 20000, 20000],
    )
    with pytest.raises(ValueError, match="未在正式赛季|未包含在当前赛季的冻结运行时清单"):
        ledger.create_match(payload, registry=part_registry)


def test_r14c_non_r14_and_ineligible_match_backward_compatible(r14c_env):
    """8. 非 R14 (如 rating_eligible=False 或无 season) 的 match 保持兼容，runtime_binding 为 None."""
    seats = [
        MatchSeat(seat=0, account_id="account:h1", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="account:h2", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:h3", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:h1", controller_type="human_ui"),  # 无 season 时
    ]
    # 创建 4 个不同账号
    for i in range(1, 5):
        part_registry.create_account(AccountCreate(account_id=f"account:free{i}", display_name=f"Free{i}", account_type="human"))

    free_seats = [
        MatchSeat(seat=i, account_id=f"account:free{i+1}", controller_type="human_ui")
        for i in range(4)
    ]

    payload = MatchCreate(
        occurred_at="2026-08-27T12:00:00+08:00",
        game_length="hanchan",
        season_id=None,
        rating_eligible=False,
        seats=free_seats,
        final_scores=[25000, 25000, 25000, 25000],
    )
    created = ledger.create_match(payload, registry=part_registry)
    assert created.runtime_binding is None
