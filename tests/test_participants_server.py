# -*- coding: utf-8 -*-
"""participants API 端点直接调用测试（仿 test_ladder_server.py）。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from participants import api
from participants.schemas import (
    AccountCreate,
    MatchCreate,
    MatchRevise,
    MatchSeat,
    MatchVoid,
)


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    return root


@pytest.fixture
def four_account_ids():
    ids = ["nick@01", "friend@01", "70k@01", "mortal41b"]
    for aid in ids:
        api.api_create_account(AccountCreate(account_id=aid, display_name=aid, account_type="human"))
    return ids


def _match_create(account_ids, scores):
    return MatchCreate(
        occurred_at="2026-08-06T12:00:00+08:00",
        game_length="hanchan",
        seats=[MatchSeat(seat=i, account_id=account_ids[i]) for i in range(4)],
        final_scores=scores,
    )


def test_account_crud_endpoints():
    created = api.api_create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    assert created.account_id == "nick@01"
    listed = api.api_list_accounts()
    assert [a["account_id"] for a in listed["accounts"]] == ["nick@01"]
    got = api.api_get_account("nick@01")
    assert got["account"]["display_name"] == "Nick"
    from participants.schemas import AccountUpdate

    updated = api.api_update_account("nick@01", AccountUpdate(display_name="Nick v2"))
    assert updated.display_name == "Nick v2"
    # 重名冲突 → 409
    with pytest.raises(HTTPException) as exc:
        api.api_create_account(AccountCreate(account_id="nick@01", display_name="Nick2", account_type="human"))
    assert exc.value.status_code == 409
    # 不存在 → 404
    with pytest.raises(HTTPException) as exc2:
        api.api_get_account("ghost@01")
    assert exc2.value.status_code == 404
    api.api_delete_account("nick@01")
    with pytest.raises(HTTPException) as exc3:
        api.api_get_account("nick@01")
    assert exc3.value.status_code == 404


def test_match_flow_endpoints(four_account_ids):
    resp = api.api_create_match(_match_create(four_account_ids, [25000] * 4))
    match = resp.match
    assert match.revision == 1
    # 详情 + revisions
    detail = api.api_get_match(match.match_id)
    assert detail.match.match_id == match.match_id
    revs = api.api_get_revisions(match.match_id)
    assert len(revs["revisions"]) == 1
    # 修订 → revision 2
    revised = api.api_revise_match(match.match_id, MatchRevise(final_scores=[30000, 25000, 25000, 20000]))
    assert revised.match.revision == 2
    # 作废
    voided = api.api_void_match(match.match_id, MatchVoid(reason="中途结束"))
    assert voided.match.status == "void"
    # 列表 status 过滤
    listed = api.api_list_matches(status="void")
    assert listed.total == 1
    active = api.api_list_matches(status="active")
    assert active.total == 0


def test_create_match_validation_422(four_account_ids):
    with pytest.raises(HTTPException) as exc:
        api.api_create_match(_match_create(four_account_ids, [42300, 28100, 19400, 10201]))
    assert exc.value.status_code == 422
    assert exc.value.detail.get("score_mismatch") is True
    # force + reason 通过
    payload = _match_create(four_account_ids, [42300, 28100, 19400, 10201])
    payload.force = True
    payload.reason = "罚符"
    resp = api.api_create_match(payload)
    assert resp.match.final_scores == [42300, 28100, 19400, 10201]


def test_missing_match_404(four_account_ids):
    with pytest.raises(HTTPException) as exc:
        api.api_get_match("m_missing_000000")
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc2:
        api.api_revise_match("m_missing_000000", MatchRevise())
    assert exc2.value.status_code == 404


def test_account_stats_stub(four_account_ids):
    resp = api.api_account_stats("nick@01")
    assert resp["schema"] == "keqing.participant.stats.v1"
    assert resp["account_id"] == "nick@01"
    assert resp["placement"]["match_count"] == 0
    with pytest.raises(HTTPException) as exc:
        api.api_account_stats("ghost@01")
    assert exc.value.status_code == 404


def test_model_endpoints(four_account_ids):
    from participants.schemas import ModelArtifactCreate, ModelIdentityCreate

    identity = api.api_create_model(ModelIdentityCreate(model_identity_id="model-70k", label="70k", kind="local_model", account_id="70k@01", artifact_path="ckpt.pth"))
    assert identity.model_identity_id == "model-70k"
    listed = api.api_list_models()
    assert len(listed["identities"]) == 1
    artifact = api.api_add_artifact("model-70k", ModelArtifactCreate(label="70k-fixed.pth", artifact_path="ckpt2.pth"))
    assert artifact["is_current"] is True
    with pytest.raises(HTTPException) as exc:
        api.api_add_artifact("model-ghost", ModelArtifactCreate(label="x", artifact_path="y"))
    assert exc.value.status_code == 404


def test_list_matches_pagination_validation(four_account_ids):
    from fastapi import HTTPException as HE

    with pytest.raises(HE) as exc:
        api.api_list_matches(limit=0)
    assert exc.value.status_code == 422
    with pytest.raises(HE) as exc2:
        api.api_list_matches(offset=-1)
    assert exc2.value.status_code == 422
    # 合法分页
    resp = api.api_list_matches(limit=10, offset=0)
    assert resp.total == 0


def test_api_patch_account_binding_conflict_is_409(four_account_ids):
    """R11-D Repair 2：PATCH account 的绑定引用完整性错误 → 409（不是 500）。"""
    from participants.schemas import AccountUpdate

    with pytest.raises(HTTPException) as exc:
        api.api_update_account("nick@01", AccountUpdate(model_identity_id="model:does-not-exist"))
    assert exc.value.status_code == 409
    # 真人绑定模型 → 409
    with pytest.raises(HTTPException) as exc2:
        api.api_update_account("nick@01", AccountUpdate(model_identity_id="model-70k"))
    assert exc2.value.status_code == 409
    assert "真人账号不能绑定驱动模型" in str(exc2.value.detail)


def test_api_create_human_with_model_binding_is_409():
    """R11-D Repair 2：human 账号带 model_identity_id → 409（类型不变量）。"""
    with pytest.raises(HTTPException) as exc:
        api.api_create_account(
            AccountCreate(account_id="human-bot", display_name="HB", account_type="human", model_identity_id="model-70k")
        )
    assert exc.value.status_code == 409
    assert "真人账号不能绑定驱动模型" in str(exc.value.detail)


def test_api_patch_model_destructive_conversion_is_409(four_account_ids):
    """R11-D Repair 2：被引用 global identity 转 account-scoped → 409（不是 500）。"""
    from participants.schemas import ModelIdentityCreate, ModelIdentityUpdate

    api.api_create_model(
        ModelIdentityCreate(model_identity_id="70k-global", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    api.api_create_account(
        AccountCreate(account_id="bot1", display_name="bot1", account_type="managed_bot", model_identity_id="70k-global")
    )
    with pytest.raises(HTTPException) as exc:
        api.api_update_model("70k-global", ModelIdentityUpdate(account_id="bot1"))
    assert exc.value.status_code == 409
    assert "不能转成账号专属身份" in str(exc.value.detail)


def test_alias_api_endpoints(four_account_ids):
    from participants.schemas import ExternalAliasCreate

    created = api.api_create_alias(
        ExternalAliasCreate(provider="tenhou", external_id="keqing1", account_id="nick@01", scope="global")
    )
    assert created["account_id"] == "nick@01"
    listed = api.api_list_aliases(provider="tenhou")
    assert len(listed["aliases"]) == 1
    from fastapi import HTTPException as HE

    with pytest.raises(HE) as exc:
        api.api_create_alias(
            ExternalAliasCreate(provider="tenhou", external_id="x", account_id="ghost@01", scope="global")
        )
    assert exc.value.status_code == 422


def test_intake_preview_endpoint(four_account_ids, monkeypatch):
    from participants import intake

    monkeypatch.setattr(
        intake, "download_tenhou6",
        lambda log_id: {
            "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
            "rule": {"aka": True},
            "log": [
                [[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]],
            ],
        },
    )
    from participants.schemas import IntakePreviewRequest

    preview = api.api_intake_preview(IntakePreviewRequest(url="https://tenhou.net/3/?log=20260804gm-0009-2147-32af115e"))
    assert preview["raw_player_names"][0] == "Nick"
    assert preview["duplicate_match_id"] is None
    assert preview["game_length"] == "tonpu"


def test_intake_confirm_endpoint(four_account_ids, monkeypatch):
    from participants import intake
    from participants.schemas import IntakeConfirmRequest, SeatResolution

    monkeypatch.setattr(
        intake, "download_tenhou6",
        lambda log_id: {
            "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
            "rule": {"aka": True},
            "log": [
                [[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]],
            ],
        },
    )
    resolutions = [
        SeatResolution(seat=0, action="assign", account_id="nick@01", alias_scope="global"),
        SeatResolution(seat=1, action="create", display_name="Bot A", account_type="managed_bot", alias_scope="session"),
        SeatResolution(seat=2, action="create", display_name="Bot B", account_type="managed_bot", alias_scope="session"),
        SeatResolution(seat=3, action="create", display_name="Friend", account_type="human", alias_scope="global"),
    ]
    resp = api.api_intake_confirm(
        IntakeConfirmRequest(log_id="20260804gm-0009-2147-32af115e", resolutions=resolutions, session_id="s9")
    )
    assert resp.match.provider == "tenhou"
    assert resp.match.data_completeness == "full_replay"
    # 重复确认 → 409 + R11-A1 结构化 envelope（code=duplicate_match + existing_match_id）
    from fastapi import HTTPException as HE

    with pytest.raises(HE) as exc:
        api.api_intake_confirm(
            IntakeConfirmRequest(log_id="20260804gm-0009-2147-32af115e", resolutions=resolutions, session_id="s9")
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "duplicate_match"
    assert exc.value.detail["context"]["existing_match_id"] == resp.match.match_id
    # replay artifact 可查，match_id 一致（P2-2）
    replay = api.api_match_replay_artifact(resp.match.match_id)
    assert replay["replay_id"] == "20260804gm-0009-2147-32af115e"
    assert replay["has_events"] is True
    assert replay["match_id"] == resp.match.match_id


def test_intake_confirm_with_ladder_eligibility(four_account_ids, monkeypatch, tmp_path):
    """R10 UX Repair P1-6：confirm 时 season_id+rating_eligible → Match 带赛季 + 标 dirty。"""
    import json as _json

    from participants import intake, ledger
    from participants.schemas import IntakeConfirmRequest, SeatResolution

    # 正式资格 gate 需要有效赛季配置（账号全部为成员）
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "official-ladder-v1",
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression"},
        "ingest": {"sources_root": str(tmp_path / "sources"), "participants": {"enabled": True, "exclusive": True}},
        "models": [
            {
                "model_id": "human",
                "accounts": [
                    {"account_id": "nick@01"},
                    {"account_id": "friend@01"},
                    {"account_id": "70k@01"},
                    {"account_id": "mortal41b"},
                ],
            }
        ],
    }
    (configs / "official-ladder-v1.json").write_text(_json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))

    monkeypatch.setattr(
        intake, "download_tenhou6",
        lambda log_id: {
            "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
            "rule": {"aka": True},
            "log": [
                [[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]],
            ],
        },
    )
    resolutions = [
        SeatResolution(seat=0, action="assign", account_id="nick@01", alias_scope="global"),
        SeatResolution(seat=1, action="assign", account_id="70k@01", alias_scope="global"),
        SeatResolution(seat=2, action="assign", account_id="friend@01", alias_scope="global"),
        SeatResolution(seat=3, action="assign", account_id="mortal41b", alias_scope="global"),
    ]
    resp = api.api_intake_confirm(
        IntakeConfirmRequest(
            log_id="20260807gm-0009-2147-32af115e",
            resolutions=resolutions,
            season_id="official-ladder-v1",
            rating_eligible=True,
        )
    )
    assert resp.match.season_id == "official-ladder-v1"
    assert resp.match.rating_eligible is True
    assert resp.match.ladder_projection_state == "pending"
    # 锁内标 dirty（durable outbox）：赛季天梯待重算
    assert ledger.ladder_dirty_path("official-ladder-v1").exists()


def test_intake_accountless_session_alias_manual_assignment(four_account_ids, monkeypatch, tmp_path):
    """Play-with-you simplification：session alias 只冻结模型 → confirm 需人工选账号。

    所选账号必须能使用该模型身份；匹配后记录 match-scoped 对齐别名。
    """
    import json as _json

    from participants import aliases, intake, ledger, registry
    from participants.schemas import ExternalAliasCreate, IntakeConfirmRequest, ModelIdentityCreate, SeatResolution

    # 一个账号限定的模型身份（70k@01 可用）——用于校验 account-less alias 人工选账号
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model-70k", label="70k", kind="local_model", account_id="70k@01", artifact_path="checkpoints/70k.pth")
    )
    identity = registry.get_model_identity("model-70k")
    # 注册 account-less session alias（模拟 Play-with-you 呼出后）
    aliases.register_alias_locked(
        ExternalAliasCreate(
            provider="tenhou",
            external_id="NoName-1",
            account_id=None,
            model_identity_id=identity.model_identity_id,
            model_artifact_id=identity.artifacts[0].model_artifact_id,
            scope="session",
            session_id="s-acc-less",
        )
    )

    monkeypatch.setattr(
        intake, "download_tenhou6",
        lambda log_id: {
            "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
            "rule": {"aka": True},
            "log": [
                [[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]],
            ],
        },
    )
    alias = next(
        a for a in aliases.resolve_candidates("tenhou", "NoName-1", session_id="s-acc-less")
    )
    assert alias.account_id is None  # session alias 不绑账号

    resolutions = [
        SeatResolution(seat=0, action="assign", account_id="nick@01", alias_scope="global"),
        SeatResolution(seat=1, action="assign", account_id="70k@01", alias_id=alias.alias_id, alias_scope="match"),
        SeatResolution(seat=2, action="assign", account_id="friend@01", alias_scope="global"),
        SeatResolution(seat=3, action="create", display_name="Friend2", account_type="human", alias_scope="global"),
    ]
    resp = api.api_intake_confirm(
        IntakeConfirmRequest(log_id="20260808gm-0009-2147-32af115e", resolutions=resolutions, session_id="s-acc-less")
    )
    seat1 = next(s for s in resp.match.seats if s.seat == 1)
    assert seat1.account_id == "70k@01"
    assert seat1.model_identity_id == identity.model_identity_id
    assert seat1.model_artifact_id == identity.artifacts[0].model_artifact_id
    # 人工对齐记录成 match-scoped 别名
    match_aliases = [
        a for a in aliases.list_aliases()
        if a.external_id == "NoName-1" and a.scope == "match" and a.external_match_id == "20260808gm-0009-2147-32af115e"
    ]
    assert len(match_aliases) == 1 and match_aliases[0].account_id == "70k@01"


def test_intake_accountless_alias_rejects_incompatible_account(four_account_ids, monkeypatch, tmp_path):
    """account-less alias 人工选账号必须能使用该模型身份（70k@01 专属身份 → 选 friend@01 拒绝）。"""
    from participants import aliases, intake, registry
    from participants.schemas import ExternalAliasCreate, IntakeConfirmRequest, ModelIdentityCreate, SeatResolution

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model-70k", label="70k", kind="local_model", account_id="70k@01", artifact_path="checkpoints/70k.pth")
    )
    identity = registry.get_model_identity("model-70k")
    aliases.register_alias_locked(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1", account_id=None,
            model_identity_id=identity.model_identity_id,
            model_artifact_id=identity.artifacts[0].model_artifact_id,
            scope="session", session_id="s-bad",
        )
    )
    monkeypatch.setattr(
        intake, "download_tenhou6",
        lambda log_id: {
            "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
            "rule": {"aka": True},
            "log": [[[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]]],
        },
    )
    alias = next(a for a in aliases.resolve_candidates("tenhou", "NoName-1", session_id="s-bad"))
    resolutions = [
        SeatResolution(seat=0, action="assign", account_id="nick@01", alias_scope="global"),
        SeatResolution(seat=1, action="assign", account_id="friend@01", alias_id=alias.alias_id, alias_scope="match"),
        SeatResolution(seat=2, action="assign", account_id="70k@01", alias_scope="global"),
        SeatResolution(seat=3, action="create", display_name="Friend2", account_type="human", alias_scope="global"),
    ]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        api.api_intake_confirm(
            IntakeConfirmRequest(log_id="20260808gm-0009-2147-32af115e", resolutions=resolutions, session_id="s-bad")
        )
    assert exc.value.status_code == 409
