# -*- coding: utf-8 -*-
"""R10 Production UX Repair 1 回归：intake dirty 原子顺序 / 正式赛季资格 gate / ext_mortal 可呼出。"""
from __future__ import annotations

import json

import pytest

from participants import aliases, intake, ledger, registry
from participants.schemas import (
    AccountCreate,
    AccountUpdate,
    ExternalAliasCreate,
    MatchCreate,
    MatchSeat,
    ModelArtifactCreate,
    ModelIdentityCreate,
)
from participants.paths import data_lock

SEASON = "official-ladder-v1"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    # 有效赛季配置：human（nick）+ 70k（bots，带 checkpoint）
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)

    # 实体文件 checkpoint
    cp70k = tmp_path / "checkpoints" / "70k.pth"
    cpv3 = tmp_path / "checkpoints" / "v3.pth"
    cp70k.parent.mkdir(parents=True, exist_ok=True)
    cp70k.write_text("70k_data", encoding="utf-8")
    cpv3.write_text("v3_data", encoding="utf-8")

    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {"sources_root": str(tmp_path / "sources"), "participants": {"enabled": True, "exclusive": True}},
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "checkpoint": str(cp70k),
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    return tmp_path


def _accounts(tmp_path: Path | None = None):
    _models(tmp_path)
    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("70k@03", "managed_bot"),
    ):
        registry.create_account(
            AccountCreate(
                account_id=account_id,
                display_name=account_id,
                account_type=account_type,
                model_identity_id="70k" if account_type != "human" else None,
            )
        )


def _models(tmp_path: Path | None = None):
    """70k / V3 两个身份 + 产物（checkpoint 路径不同）。"""
    ident_70k = registry.get_model_identity("70k")
    if ident_70k is None:
        cp70k = "checkpoints/70k.pth" if tmp_path is None else str(tmp_path / "checkpoints" / "70k.pth")
        ident_70k = registry.create_model_identity(
            ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path=cp70k, stage="promoted")
        )
    ident_v3 = registry.get_model_identity("V3")
    if ident_v3 is None:
        cpv3 = "checkpoints/v3.pth" if tmp_path is None else str(tmp_path / "checkpoints" / "v3.pth")
        ident_v3 = registry.create_model_identity(
            ModelIdentityCreate(model_identity_id="V3", label="V3", kind="local_model", artifact_path=cpv3, stage="promoted")
        )
    return ident_70k, ident_v3


def _seats(seat1_identity_id=None, seat1_artifact_id=None, extra_account="70k@03"):
    return [
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model", model_identity_id=seat1_identity_id or None, model_artifact_id=seat1_artifact_id or None),
        MatchSeat(seat=3, account_id=extra_account, controller_type="local_model"),
    ]


def _match_create(seats, *, season_id=SEASON):
    return MatchCreate(
        occurred_at="2026-08-08T10:00:00+08:00",
        game_length="hanchan",
        season_id=season_id,
        rating_eligible=True,
        seats=seats,
        final_scores=[30000, 20000, 25000, 25000],
        source="manual",
    )


# ---------------------------------------------------------------------------
# P1-1：intake 恢复补标 dirty（crash window）
# ---------------------------------------------------------------------------

def test_recover_intake_transaction_restores_dirty(env, tmp_path):
    _accounts(tmp_path)
    match = ledger.create_match(_match_create(_seats()), registry)
    # 模拟「dirty 丢失」：正常提交后再清掉（等价于旧顺序下死在 rewrite 与 dirty 之间）
    ledger.clear_ladder_dirty(SEASON)
    assert not ledger.ladder_dirty_path(SEASON).exists()

    tx = {
        "intent": "intake",
        "accounts": [],
        "aliases": [],
        "match": match.model_dump(by_alias=True),
        "revision": {
            "schema": "keqing.participant.match_revision.v1",
            "revision_id": match.latest_revision_id,
            "match_id": match.match_id,
            "revision": match.revision,
            "action": "create",
            "created_at": match.created_at,
            "by": "t",
            "force": False,
            "reason": None,
            "validation": {"passed": True, "issues": []},
            "before": None,
            "after": match.model_dump(by_alias=True),
        },
    }
    import json as _json

    from participants import paths as participant_paths

    from participants.ledger import pending_transaction_path

    participant_paths.atomic_write_text(pending_transaction_path(), _json.dumps(tx, ensure_ascii=False))
    with data_lock():
        ledger._recover_pending_transaction()
    # 恢复后 dirty 必须存在（eligible Match 恢复即产生 generation）
    assert ledger.ladder_dirty_path(SEASON).exists()


# ---------------------------------------------------------------------------
# P1-2：正式赛季资格 gate
# ---------------------------------------------------------------------------

def test_gate_rejects_70k_account_with_v3_artifact(env, tmp_path):
    _accounts(tmp_path)
    _identity_70k, identity_v3 = _models(tmp_path)
    art_v3 = identity_v3.artifacts[0]
    with pytest.raises((ValueError, ledger.ValidationError)):
        ledger.create_match(_match_create(_seats("V3", art_v3.model_artifact_id)), registry)


def test_gate_accepts_70k_account_with_70k_artifact(env, tmp_path):
    _accounts(tmp_path)
    identity_70k, _v3 = _models(tmp_path)
    art_70k = identity_70k.artifacts[0]
    match = ledger.create_match(_match_create(_seats(identity_70k.model_identity_id, art_70k.model_artifact_id)), registry)
    assert match.rating_eligible is True
    assert ledger.ladder_dirty_path(SEASON).exists()


def test_gate_rejects_unknown_season(env, tmp_path):
    _accounts(tmp_path)
    with pytest.raises(ValueError, match="正式赛季不存在"):
        ledger.create_match(_match_create(_seats(), season_id="ghost-season"), registry)


def test_gate_rejects_unregistered_account(env, tmp_path):
    _accounts(tmp_path)
    # 账号存在但不在赛季成员 → 正式资格 gate 拒绝
    registry.create_account(AccountCreate(account_id="ghost@01", display_name="ghost", account_type="external_bot"))
    with pytest.raises(ValueError, match="未在正式赛季"):
        ledger.create_match(_match_create(_seats(extra_account="ghost@01")), registry)


def test_gate_rejects_human_not_under_human_model(env, tmp_path):
    """人类账号必须属于 model_id=human（C23 契约）。"""
    _accounts(tmp_path)
    # nick@01 是 human，赛季配置在 human 模型下 → 正常；这里构造一个 human 账号
    # 在 bot 模型的 season 场景：用 70k@01（bot）改造成 human 需要重建 fixture，
    # 直接验证现有配置下 human nick 通过即可 + 一个反例。
    registry.create_account(AccountCreate(account_id="human-bot@01", display_name="hb", account_type="human"))
    seats = [
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="human-bot@01", controller_type="human_ui"),
    ]
    # human-bot@01 不在赛季 models 的 human 模型（human 模型只有 nick@01）
    with pytest.raises(ValueError, match="未在正式赛季"):
        ledger.create_match(_match_create(seats), registry)


# ---------------------------------------------------------------------------
# P1-3：artifact-backed external_agent 可被本系统呼出
# ---------------------------------------------------------------------------

def test_external_agent_launched_allowed_with_artifact():
    """ext_mortal：local_model + launched + artifact → 启动校验通过。"""
    from gateway.api import playwithyou as pw

    identity = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ext_mortal",
            label="External Mortal",
            kind="local_model",
            artifact_path="artifacts/external_mortal_20240308_best_min.pth",
        )
    )
    for aid in ("ext_mortal@01", "ext_mortal@02", "ext_mortal@03", "ext_mortal@04"):
        registry.create_account(
            AccountCreate(account_id=aid, display_name=aid, account_type="managed_bot", model_identity_id="ext_mortal")
        )
    art = identity.artifacts[0]
    bindings = [
        {"account_id": "ext_mortal@01", "controller_type": "local_model", "model_identity_id": identity.model_identity_id, "model_artifact_id": art.model_artifact_id, "launcher_slot": 0, "expected_raw_name": "NoName-1"},
        {"account_id": "ext_mortal@02", "controller_type": "local_model", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
        {"account_id": "ext_mortal@03", "controller_type": "local_model", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
        {"account_id": "ext_mortal@04", "controller_type": "local_model", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
    ]
    # 不抛错：local_model + artifact 合法（specs 数 = launched 数）
    pw._validate_roster_bindings(bindings, ["artifacts/external_mortal_20240308_best_min.pth"])

    # 无 artifact 的真实远程 agent 拒绝本地 launcher
    bad = [
        {"account_id": "ext_mortal@01", "controller_type": "external_agent", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": 0, "expected_raw_name": "NoName-1"},
        {"account_id": "ext_mortal@02", "controller_type": "external_agent", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
        {"account_id": "ext_mortal@03", "controller_type": "external_agent", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
        {"account_id": "ext_mortal@04", "controller_type": "external_agent", "model_identity_id": None, "model_artifact_id": None, "launcher_slot": None},
    ]
    with pytest.raises(ValueError, match="可启动的模型产物"):
        pw._validate_roster_bindings(bad, ["artifacts/external_mortal_20240308_best_min.pth"])


def test_gate_rejects_rating_eligible_without_season(env):
    """P1-2 不变量：rating_eligible=true 但 season 为空 → REJECT（不能绕过 gate）。"""
    _accounts()
    seats = [
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="70k@03", controller_type="local_model"),
    ]
    with pytest.raises(ValueError, match="season_id 不能为空"):
        ledger.create_match(
            MatchCreate(
                occurred_at="2026-08-08T12:00:00+08:00",
                game_length="hanchan",
                season_id="",
                rating_eligible=True,
                seats=seats,
                final_scores=[30000, 20000, 25000, 25000],
                source="manual",
            ),
            registry,
        )


def test_gate_normalizes_season_id(env, tmp_path):
    """UX Repair 3 / P1：season_id 带空白 → 验证通过但必须落账为 trim 后的规范值。"""
    _accounts(tmp_path)
    seats = [
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="70k@03", controller_type="local_model"),
    ]
    match = ledger.create_match(
        MatchCreate(
            occurred_at="2026-08-08T13:00:00+08:00",
            game_length="hanchan",
            season_id=" official-ladder-v1 ",
            rating_eligible=True,
            seats=seats,
            final_scores=[30000, 20000, 25000, 25000],
            source="manual",
        ),
        registry,
    )
    # 落账必须用 trim 后的规范 season_id
    assert match.season_id == SEASON
    assert match.ladder_projection_state == "pending"
    # dirty marker 路径也必须用规范名
    assert ledger.ladder_dirty_path(SEASON).exists()
    assert not ledger.ladder_dirty_path(" official-ladder-v1 ").exists()


def test_gate_rejects_human_seat_with_bot_artifact(env, tmp_path):
    """P1：正式人类座位不能携带冻结 bot 模型产物——防止 bot 战绩记到 Nick 名下。

    用全局 identity 复现真实漏洞场景：账号显式绑定模型后，
    identity_belongs_to_account 通过，必须由 eligibility gate 拒绝。
    """
    _accounts(tmp_path)
    cp70k = str(tmp_path / "checkpoints" / "70k.pth")
    global_70k = registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k-global", label="70k", kind="local_model", artifact_path=cp70k, stage="promoted")
    )
    for aid in ("70k@01", "70k@02", "70k@03"):
        registry.update_account(aid, AccountUpdate(model_identity_id="70k-global"))
    art_70k = global_70k.artifacts[0]
    seats = [
        # nick@01 是 human，却携带 70k artifact → REJECT
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui", model_identity_id=global_70k.model_identity_id, model_artifact_id=art_70k.model_artifact_id),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="70k@03", controller_type="local_model"),
    ]
    # R11-D 严格语义下，未绑定该模型的账号（含人类）在落账前即被拒绝
    with pytest.raises(ValueError, match="未绑定模型身份"):
        ledger.create_match(_match_create(seats), registry)


def test_gate_accepts_bot_artifact_on_bot_account(env, tmp_path):
    """P1：bot artifact + 对应 bot 账号 + official ladder → ACCEPT。"""
    _accounts(tmp_path)
    cp70k = str(tmp_path / "checkpoints" / "70k.pth")
    global_70k = registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k-global", label="70k", kind="local_model", artifact_path=cp70k, stage="promoted")
    )
    for aid in ("70k@01", "70k@02", "70k@03"):
        registry.update_account(aid, AccountUpdate(model_identity_id="70k-global"))
    art_70k = global_70k.artifacts[0]
    seats = [
        MatchSeat(seat=0, account_id="nick@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="70k@01", controller_type="local_model", model_identity_id=global_70k.model_identity_id, model_artifact_id=art_70k.model_artifact_id),
        MatchSeat(seat=2, account_id="70k@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="70k@03", controller_type="local_model"),
    ]
    match = ledger.create_match(_match_create(seats), registry)
    assert match.rating_eligible is True
    assert ledger.ladder_dirty_path(SEASON).exists()
