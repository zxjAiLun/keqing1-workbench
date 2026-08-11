# -*- coding: utf-8 -*-
"""R10-D：外部别名 + 天凤统一摄入（preview → 身份解析 → 落账 + artifact + 防重）。"""
from __future__ import annotations

import pytest

from participants import aliases, intake, ledger, registry
from participants.schemas import AccountCreate, AccountUpdate, ExternalAliasCreate


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    return root


FAKE_LOG_ID = "20260804gm-0009-2147-32af115e"


def _kyoku(round_index, scores, result):
    return [
        [round_index, 0, 0],  # 0 meta
        scores,               # 1 scores
        [],                   # 2 dora（空 = 无宝牌指示）
        [],                   # 3
        [], [], [],           # 4,5,6 seat0
        [], [], [],           # 7,8,9 seat1
        [], [], [],           # 10,11,12 seat2
        [], [], [],           # 13,14,15 seat3
        result,               # 16 result
    ]


FAKE_TENHOU6 = {
    "name": ["Nick", "NoName-1", "NoName-2", "FriendID"],
    "rule": {"aka": True},
    "log": [
        _kyoku(0, [25000, 25000, 25000, 25000], ["和了", [5000, -5000, 0, 0], [0, 1]]),
        _kyoku(1, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0]]),
        _kyoku(2, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0]]),
        _kyoku(3, [30000, 20000, 25000, 25000], ["流局", [0, 0, 0, 0]]),
    ],
}


@pytest.fixture
def fake_download(monkeypatch):
    monkeypatch.setattr(intake, "download_tenhou6", lambda log_id: FAKE_TENHOU6)


def test_parse_tenhou_url():
    parsed = intake.parse_tenhou_url(f"https://tenhou.net/3/?log={FAKE_LOG_ID}&tw=2")
    assert parsed["provider"] == "tenhou"
    assert parsed["log_id"] == FAKE_LOG_ID
    assert parsed["tw"] == 2
    with pytest.raises(ValueError):
        intake.parse_tenhou_url("https://example.com/foo")


def test_build_preview_does_not_persist(fake_download, participants_root):
    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}")
    assert preview["raw_player_names"] == ["Nick", "NoName-1", "NoName-2", "FriendID"]
    assert preview["game_length"] == "tonpu"
    assert preview["final_scores"] == [30000, 20000, 25000, 25000]
    assert preview["hand_count"] == 4
    assert preview["data_completeness"] == "full_replay"
    assert preview["duplicate_match_id"] is None
    # 未落账：无 match，无 artifact
    assert ledger.list_matches().total == 0
    assert intake.read_replay_artifact(FAKE_LOG_ID) is None


def test_hand_summaries_extract_winners():
    events = intake.tenhou6_events(FAKE_TENHOU6)
    hands = intake.hand_summaries(events)
    assert len(hands) == 4
    assert hands[0]["winners"][0]["actor"] == 0
    assert hands[0]["winners"][0]["win_type"] == "ron"
    assert hands[1]["ryukyoku"] is not None


def test_resolve_and_create_match(fake_download, participants_root):
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "global"},
        {"seat": 1, "action": "create", "display_name": "Bot 70k", "account_type": "managed_bot", "alias_scope": "session"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "session"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "global"},
    ]
    result = intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=resolutions, session_id="s1")
    match = ledger.get_match(result["match_id"])
    assert match is not None
    assert match.provider == "tenhou"
    assert match.external_match_id == FAKE_LOG_ID
    assert match.data_completeness == "full_replay"
    assert match.raw_player_names == ["Nick", "NoName-1", "NoName-2", "FriendID"]
    assert match.resolution["0"]["account_id"] == "nick@01"
    # artifact 持久化
    artifact = intake.read_replay_artifact(FAKE_LOG_ID)
    assert artifact is not None
    assert artifact["has_events"] is True
    assert len(artifact["hands"]) == 4
    # 别名注册：global + session（session 别名只在同一会话内解析）
    nick_alias = aliases.resolve_candidates("tenhou", "Nick")
    assert any(a.account_id == "nick@01" and a.scope == "global" for a in nick_alias)
    session_alias = aliases.resolve_candidates("tenhou", "NoName-1", session_id="s1")
    assert any(a.scope == "session" for a in session_alias)
    assert aliases.resolve_candidates("tenhou", "NoName-1") == []
    # 重复导入被拒绝（R11-A1：结构化 DuplicateMatchError，带已有对局 ID）
    with pytest.raises(intake.DuplicateMatchError) as exc_info:
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=resolutions)
    assert exc_info.value.existing_match_id
    assert ledger.list_matches().total == 1


def test_session_id_recovered_from_awaiting_import_capture(tmp_path, monkeypatch):
    """R11-C Repair：未显式带 session_id 时，intake 从 awaiting_import capture
    按 log_id 反查 session provenance（Import URL 不再需要 session_id）。

    用真实 canonical capture root（KEQING_LADDER_DATA_ROOT → ladder_capture_root()），
    与生产 writer 的路径合同一致。
    """
    import json

    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    from project_data import ladder_capture_root

    session_dir = ladder_capture_root() / "sess-from-capture" / "pending"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "c1.json").write_text(
        json.dumps(
            {
                "capture_id": "c1",
                "session_id": "sess-from-capture",
                "state": "awaiting_import",
                "tenhou_log_url": f"https://tenhou.net/3/?log={FAKE_LOG_ID}",
            }
        ),
        encoding="utf-8",
    )
    # 非 awaiting_import 不参与恢复
    (session_dir / "c2.json").write_text(
        json.dumps(
            {
                "capture_id": "c2",
                "session_id": "sess-other",
                "state": "log_captured",
                "tenhou_log_url": f"https://tenhou.net/3/?log={FAKE_LOG_ID}",
            }
        ),
        encoding="utf-8",
    )

    assert intake._session_id_for_log_id(FAKE_LOG_ID) == "sess-from-capture"
    assert intake._session_id_for_log_id("20260999gm-0000-2147-00000000") is None


def test_vertical_session_recovery_from_capture(tmp_path, monkeypatch, fake_download, participants_root):
    """R11-C Repair 2 vertical：只带 ?url=（无 session_id）时，Preview 与 Confirm
    都从 awaiting_import capture 恢复同一 provenance。

    一次覆盖两个 P1：capture 路径合同正确（ladder_capture_root）+ recovered
    session 贯穿到 confirm（_resolve_seat 能重新校验 session alias）。
    """
    import json

    from participants.schemas import ModelIdentityCreate

    # 1. writer 合同：capture 落在 ladder_capture_root()（KEQING_LADDER_DATA_ROOT）
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    from project_data import ladder_capture_root

    capture_dir = ladder_capture_root() / "sess-v" / "pending"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "c1.json").write_text(
        json.dumps(
            {
                "capture_id": "c1",
                "session_id": "sess-v",
                "state": "awaiting_import",
                "tenhou_log_url": f"https://tenhou.net/3/?log={FAKE_LOG_ID}",
            }
        ),
        encoding="utf-8",
    )

    # 2. 模型身份 + 绑定账号（R11-D：身份先建，账号再绑定）
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:70k",
            label="70k",
            kind="local_model",
            artifact_path="artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth",
        )
    )
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    registry.create_account(
        AccountCreate(account_id="70k-bot", display_name="70k bot", account_type="managed_bot", model_identity_id="model:70k")
    )

    # 3. session alias：NoName-1 → frozen 70k 模型（account-less）
    identity = registry.get_model_identity("model:70k")
    frozen_artifact_id = identity.artifacts[0].model_artifact_id
    session_alias = aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou",
            external_id="NoName-1",
            account_id=None,
            model_identity_id="model:70k",
            model_artifact_id=frozen_artifact_id,
            scope="session",
            session_id="sess-v",
        )
    )

    # 4. Preview 无 session_id → 自动恢复 provenance，出现 frozen model candidate
    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}")
    assert any(
        c.get("model_identity_id") == "model:70k" and not c.get("account_id")
        for c in preview["seats"][1]["candidates"]
    )

    # 5. Confirm 无 session_id → 复用恢复出的 session：落账成功且座位保留模型证据
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {
            "seat": 1,
            "action": "assign",
            "account_id": "70k-bot",
            "alias_id": session_alias.alias_id,
            "alias_scope": "match",
        },
        {"seat": 2, "action": "create", "display_name": "Bot B", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    result = intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=resolutions)
    match = ledger.get_match(result["match_id"])
    assert match is not None
    assert match.resolution["1"]["model_identity_id"] == "model:70k"
    assert match.resolution["1"]["model_artifact_id"] == frozen_artifact_id
    assert match.resolution["1"]["account_id"] == "70k-bot"


def test_preview_narrows_eligible_accounts_by_frozen_model(fake_download, participants_root):
    """R11-D：session 冻结模型 → preview seat 的 eligible_account_ids 只含该模型
    下已绑定的账号（enabled），未绑定账号不在候选；模型不能替用户猜账号。"""
    from participants.schemas import ModelIdentityCreate

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot", model_identity_id="70k")
    )
    registry.create_account(
        AccountCreate(account_id="70k@02", display_name="70k-2", account_type="managed_bot", model_identity_id="70k")
    )
    registry.create_account(AccountCreate(account_id="other@01", display_name="Other", account_type="managed_bot"))
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    identity = registry.get_model_identity("70k")
    aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1",
            account_id=None, model_identity_id="70k", model_artifact_id=identity.artifacts[0].model_artifact_id,
            scope="session", session_id="s1",
        )
    )

    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}", session_id="s1")
    seat1 = preview["seats"][1]
    assert set(seat1["eligible_account_ids"] or []) == {"70k@01", "70k@02"}
    assert "other@01" not in (seat1["eligible_account_ids"] or [])
    # 无冻结模型的座位不受限
    assert preview["seats"][0]["eligible_account_ids"] is None


def test_auto_account_id_ignores_disabled_accounts(fake_download, participants_root):
    """R11-D Repair：_auto_account_id 与 eligible_account_ids 共用 enabled 候选——
    唯一 enabled 绑定账号 → 自动预选；disabled 绑定账号不参与。"""
    from participants.schemas import ModelIdentityCreate

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot", model_identity_id="70k")
    )
    registry.create_account(
        AccountCreate(account_id="70k-old", display_name="70k-old", account_type="managed_bot", model_identity_id="70k")
    )
    registry.update_account("70k-old", AccountUpdate(enabled=False))
    identity = registry.get_model_identity("70k")
    aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1",
            account_id=None, model_identity_id="70k", model_artifact_id=identity.artifacts[0].model_artifact_id,
            scope="session", session_id="s1",
        )
    )

    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}", session_id="s1")
    seat1 = preview["seats"][1]
    # 唯一 enabled 候选 → 自动预选，且与 eligible 一致（disabled 不参与）
    assert seat1["eligible_account_ids"] == ["70k@01"]
    assert seat1["auto_account_id"] == "70k@01"


def test_model_only_provenance_recovers_identity_and_artifact(
    tmp_path, monkeypatch, fake_download, participants_root
):
    """R11-G Repair：R11-B model-only 的 session alias 无模型字段时，从 binding.json
    （冻结 checkpoint）唯一恢复 (identity, artifact)——Preview 收窄候选 + 自动预选，
    Confirm 把 identity 和 artifact 都写进 MatchSeat。"""
    import json as _json

    from participants.schemas import ModelIdentityCreate

    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    from project_data import ladder_capture_root

    checkpoint = tmp_path / "ckpt" / "70k.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("", encoding="utf-8")

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path=str(checkpoint))
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot", model_identity_id="70k")
    )
    identity = registry.get_model_identity("70k")
    artifact_id = identity.artifacts[0].model_artifact_id

    # session alias：R11-B model-only 风格——无模型字段
    alias = aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1", account_id=None, scope="session", session_id="sess-p"
        )
    )
    # binding.json（frozen roster：checkpoint 绝对路径）
    capture_dir = ladder_capture_root() / "sess-p"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "binding.json").write_text(
        _json.dumps(
            {
                "session_id": "sess-p",
                "roster": [
                    {"expected_raw_name": "NoName-1", "resolved_checkpoint_path": str(checkpoint)}
                ],
            }
        ),
        encoding="utf-8",
    )

    # Preview：唯一 enabled 候选 → 收窄 + 自动预选
    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}", session_id="sess-p")
    seat1 = preview["seats"][1]
    assert seat1["eligible_account_ids"] == ["70k@01"]
    assert seat1["auto_account_id"] == "70k@01"

    # Confirm：identity + artifact 一起写入 MatchSeat
    resolutions = [
        {"seat": 0, "action": "create", "display_name": "Nick", "account_type": "human", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "account_id": "70k@01", "alias_id": alias.alias_id, "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot B", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    result = intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=resolutions, session_id="sess-p")
    match = ledger.get_match(result["match_id"])
    seat1_match = next(s for s in match.seats if s.seat == 1)
    assert seat1_match.model_identity_id == "70k"
    assert seat1_match.model_artifact_id == artifact_id


def test_ambiguous_checkpoint_provenance_fails_closed(tmp_path, monkeypatch, fake_download, participants_root):
    """R11-G Repair：同一 checkpoint 属于多个 ModelIdentity → 冲突，绝不 first-match。"""
    import json as _json

    from participants.schemas import ModelIdentityCreate

    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    from project_data import ladder_capture_root

    checkpoint = tmp_path / "ckpt" / "shared.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("", encoding="utf-8")

    for mid in ("model-a", "model-b"):
        registry.create_model_identity(
            ModelIdentityCreate(model_identity_id=mid, label=mid, kind="local_model", artifact_path=str(checkpoint))
        )
    aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="NoName-1", account_id=None, scope="session", session_id="sess-x")
    )
    capture_dir = ladder_capture_root() / "sess-x"
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "binding.json").write_text(
        _json.dumps(
            {
                "session_id": "sess-x",
                "roster": [{"expected_raw_name": "NoName-1", "resolved_checkpoint_path": str(checkpoint)}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="对应多个 ModelArtifact"):
        intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}", session_id="sess-x")


def test_preview_auto_resolves_confirmed_global_alias(fake_download, participants_root):
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="Nick", account_id="nick@01", scope="global")
    )
    preview = intake.build_preview(f"https://tenhou.net/3/?log={FAKE_LOG_ID}")
    assert preview["seats"][0]["auto_account_id"] == "nick@01"
    # 未知 NoName：绝不自动猜
    assert preview["seats"][1]["auto_account_id"] is None
    assert preview["seats"][1]["candidates"] == []


def test_alias_register_and_resolve(participants_root):
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="keqing1", account_id="nick@01", scope="global")
    )
    aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="NoName-1", account_id="70k@01", scope="session", session_id="s1")
    )
    # session-scoped 绑定优先
    cands = aliases.resolve_candidates("tenhou", "NoName-1", session_id="s1")
    assert cands and cands[0].account_id == "70k@01"
    # 无 session 时不命中 session 别名
    cands_none = aliases.resolve_candidates("tenhou", "NoName-1")
    assert all(a.scope != "session" for a in cands_none)
    # global 稳定别名
    cands2 = aliases.resolve_candidates("tenhou", "keqing1")
    assert cands2 and cands2[0].account_id == "nick@01"


# ---------------------------------------------------------------------------
# R10-D Repair：match alias 隔离 / 原子事务 / session 模型信息 / 时间规则
# ---------------------------------------------------------------------------

LOG_B = "20260807gm-0009-2147-32af115e"


def _nick_resolutions(bot_a_name="Bot 70k"):
    return [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "global"},
        {"seat": 1, "action": "create", "display_name": bot_a_name, "account_type": "managed_bot", "alias_scope": "match"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "match"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "match"},
    ]


def test_match_alias_isolated_between_matches(fake_download, participants_root):
    """P1-1：match scope 别名只作用于同一 external_match_id，不串到其他牌谱。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_nick_resolutions())
    # 同一 NoName 的 match 别名只属于 FAKE_LOG_ID
    preview_b = intake.build_preview(f"https://tenhou.net/3/?log={LOG_B}")
    no_name_candidates = preview_b["seats"][1]["candidates"]
    assert all(c.get("external_match_id") != FAKE_LOG_ID or c["scope"] != "match" for c in no_name_candidates)
    assert preview_b["seats"][1]["auto_account_id"] is None
    # 导入 B：同名 NoName 指向另一账号，不覆盖 A 的别名 → 两条独立记录
    # （座位 2/3 的确定性 ID 已在 A 中创建 → 用 assign 复用，座位 1 新建 Bot V4）
    bot_v3 = next(a for a in registry.list_accounts() if a.display_name == "Bot V3")
    friend = next(a for a in registry.list_accounts() if a.display_name == "Friend")
    res_b = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "create", "display_name": "Bot V4", "account_type": "managed_bot", "alias_scope": "match"},
        {"seat": 2, "action": "assign", "account_id": bot_v3.account_id, "alias_scope": "none"},
        {"seat": 3, "action": "assign", "account_id": friend.account_id, "alias_scope": "none"},
    ]
    intake.resolve_and_create_match(log_id=LOG_B, resolutions=res_b)
    match_aliases = [a for a in aliases.list_aliases() if a.external_id == "NoName-1" and a.scope == "match"]
    assert {a.external_match_id for a in match_aliases} == {FAKE_LOG_ID, LOG_B}
    assert len(match_aliases) == 2


def test_confirm_failure_mid_commit_is_recoverable(fake_download, participants_root, monkeypatch):
    """P1-2：revision append 故障 → 无半成品；pending 恢复后恰好一场。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))

    original_append = ledger._append_revision

    def failing_append(row, *, fsync=False):
        raise RuntimeError("injected revision append failure")

    monkeypatch.setattr(ledger, "_append_revision", failing_append)
    with pytest.raises(RuntimeError):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_nick_resolutions(), session_id="s1")
    # 仅恢复 _append_revision（不能 undo()，否则 fixture 的 env 也被回滚）
    monkeypatch.setattr(ledger, "_append_revision", original_append)

    # 故障后：pending 已写、match/revision 未提交 → 恢复（public 入口模拟重启）
    assert ledger.pending_transaction_path().exists()
    assert ledger.recover_pending_transaction() is True
    assert not ledger.pending_transaction_path().exists()
    match = ledger.find_match_by_external("tenhou", FAKE_LOG_ID)
    assert match is not None
    assert ledger.list_matches().total == 1
    assert len(ledger.list_revisions(match.match_id)) == 1
    artifact = intake.read_replay_artifact(FAKE_LOG_ID)
    assert artifact is not None
    assert artifact["match_id"] == match.match_id
    # 账号/别名都已被事务引用（无孤立残骸：全部账号都被 match 引用）
    for seat in match.seats:
        assert registry.get_account(seat.account_id) is not None


def test_concurrent_confirm_creates_single_match(fake_download, participants_root):
    """P1-2：并发 confirm → 恰好一场 match / 一个 revision / 一个 artifact。"""
    import threading

    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    results: list[str] = []
    errors: list[str] = []

    def run():
        try:
            r = intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_nick_resolutions(), session_id="s1")
            results.append(r["match_id"])
        except ValueError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert ledger.list_matches().total == 1
    match = ledger.find_match_by_external("tenhou", FAKE_LOG_ID)
    assert match is not None
    assert len(ledger.list_revisions(match.match_id)) == 1


def test_session_alias_model_info_enters_match(fake_download, participants_root):
    """P1-3：session 别名的 model_identity/artifact 进入 MatchSeat / resolution / revision / summary。"""
    from participants.schemas import ModelIdentityCreate

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot", model_identity_id="70k")
    )
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    identity = registry.get_model_identity("70k")
    artifact = identity.artifacts[0]
    alias = aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1", account_id="70k@01",
            model_identity_id="70k", model_artifact_id=artifact.model_artifact_id,
            scope="session", session_id="s1",
        )
    )
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "alias_id": alias.alias_id, "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    result = intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=resolutions, session_id="s1")
    match = ledger.get_match(result["match_id"])
    seat1 = next(s for s in match.seats if s.seat == 1)
    assert seat1.account_id == "70k@01"
    assert seat1.model_identity_id == "70k"
    assert seat1.model_artifact_id == artifact.model_artifact_id
    assert match.resolution["1"]["model_identity_id"] == "70k"
    # revision after 快照含模型信息
    rev = ledger.list_revisions(match.match_id)[0]
    assert rev["after"]["seats"][1]["model_identity_id"] == "70k"
    # replay summary 含 resolution
    replay = intake.read_replay_artifact(FAKE_LOG_ID)
    assert replay["resolution"]["1"]["model_identity_id"] == "70k"
    # 身份/产物归属校验：错误产物被拒（不落账）——使用独立 session 避免与好 alias 键冲突
    alias_bad = aliases.register_alias(
        ExternalAliasCreate(
            provider="tenhou", external_id="NoName-1", account_id="70k@01",
            model_identity_id="70k", model_artifact_id="art_does_not_exist",
            scope="session", session_id="s_bad",
        )
    )
    bad_resolutions = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "alias_id": alias_bad.alias_id, "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    with pytest.raises(ValueError, match="不属于"):
        intake.resolve_and_create_match(log_id=LOG_B, resolutions=bad_resolutions, session_id="s_bad")


def test_occurred_at_keeps_hour():
    assert intake._occurred_at_from_log_id("2026080700gm-0009-2147-32af115e") == "2026-08-07T00:00:00+08:00"
    assert intake._occurred_at_from_log_id("2026080418gm-0009-2147-32af115e") == "2026-08-04T18:00:00+08:00"
    assert intake._occurred_at_from_log_id("20260804gm-0009-2147-32af115e") == "2026-08-04T00:00:00+08:00"


def test_game_length_prefers_rule_metadata(fake_download):
    assert intake._game_length_from_rule({"tonnan": 1}) == "hanchan"
    assert intake._game_length_from_rule({"tonnan": 0}) == "tonpu"
    assert intake._game_length_from_rule({}) is None
    # 无规则元数据时按局数/风向回退
    events = intake.tenhou6_events(FAKE_TENHOU6)
    hands = intake.hand_summaries(events)
    assert intake._game_length_from_hands(hands) == "tonpu"


# ---------------------------------------------------------------------------
# R10-D Repair 2：alias_id 上下文锁 / stale alias / scope schema / staging 残留
# ---------------------------------------------------------------------------

def _resolutions_with_alias(alias_id, seat=1):
    return [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "alias_id": alias_id, "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]


def test_alias_id_scope_isolated_on_confirm(fake_download, participants_root):
    """P1-1：session s1 的 alias 用于 s2 / 名字不匹配 / 覆盖冻结 artifact / match A 用于 match B → 拒绝。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    s1_alias = aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="NoName-1", account_id="70k@01", scope="session", session_id="s1")
    )
    # s1 alias 用于 s2 → 拒绝
    with pytest.raises(ValueError, match="不适用于"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_resolutions_with_alias(s1_alias.alias_id), session_id="s2")
    # 名字不匹配：NoName-1 的 alias 用在 FriendID 座位 → 拒绝
    res_friend = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V4", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "assign", "alias_id": s1_alias.alias_id, "alias_scope": "none"},
    ]
    with pytest.raises(ValueError, match="不适用于"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=res_friend, session_id="s1")
    # 覆盖 alias 冻结的 artifact → 拒绝（请求字段与 alias 冲突）
    res_override = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "alias_id": s1_alias.alias_id, "model_artifact_id": "hack", "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    with pytest.raises(ValueError, match="冲突"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=res_override, session_id="s1")
    # match A 的别名用于 match B → 拒绝
    match_a_alias = aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="NoName-1", account_id="70k@01", scope="match", external_match_id=FAKE_LOG_ID)
    )
    with pytest.raises(ValueError, match="不适用于"):
        intake.resolve_and_create_match(log_id=LOG_B, resolutions=_resolutions_with_alias(match_a_alias.alias_id))
    # 全程不产生 Match
    assert ledger.list_matches().total == 0


def test_stale_alias_account_cannot_be_hard_deleted(fake_download, participants_root):
    """P1-3：session alias 阻止硬删；软删后 confirm 拒绝且不产生 Match。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    alias = aliases.register_alias(
        ExternalAliasCreate(provider="tenhou", external_id="NoName-1", account_id="70k@01", scope="session", session_id="s1")
    )
    # 所有 scope 的 alias 都参与删除保护 → 只能软删
    result = registry.delete_account_guarded("70k@01", lambda: aliases.alias_references_account("70k@01"))
    assert result["disabled"] is True
    assert registry.get_account("70k@01").enabled is False
    # confirm 使用该 alias → 拒绝（停用），不产生 Match
    with pytest.raises(ValueError, match="停用"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_resolutions_with_alias(alias.alias_id), session_id="s1")
    assert ledger.find_match_by_external("tenhou", FAKE_LOG_ID) is None
    assert ledger.list_matches().total == 0


def test_alias_scope_schema_validation():
    """P2-1：scope 一致性一次性校验（model_validator 覆盖默认值场景）。"""
    from participants.schemas import ExternalAliasCreate as EAC

    with pytest.raises(Exception, match="session_id"):
        EAC(provider="tenhou", external_id="x", account_id="a", scope="session")
    with pytest.raises(Exception, match="external_match_id"):
        EAC(provider="tenhou", external_id="x", account_id="a", scope="match")
    with pytest.raises(Exception, match="global"):
        EAC(provider="tenhou", external_id="x", account_id="a", scope="global", session_id="s1")
    with pytest.raises(Exception):
        EAC(provider="tenhou", external_id="x", account_id="a", scope="session", session_id="s1", external_match_id="m")
    # 合法
    EAC(provider="tenhou", external_id="x", account_id="a", scope="global")
    EAC(provider="tenhou", external_id="x", account_id="a", scope="session", session_id="s1")
    EAC(provider="tenhou", external_id="x", account_id="a", scope="match", external_match_id="m")


def test_validation_failure_leaves_no_staging(fake_download, participants_root):
    """P2-2：四座重复校验失败 → 不残留 staging 目录。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    res = [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": "human", "alias_scope": "none"},
    ]
    with pytest.raises(ValueError, match="同一账号"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=res)
    assert not intake._staging_dir(FAKE_LOG_ID).exists()


# ---------------------------------------------------------------------------
# R10-D Repair 3：action=create 不得静默复用同 ID 账号
# ---------------------------------------------------------------------------

def _imp_id_for(raw_name, display_name):
    import hashlib

    slug = "".join(ch.lower() if (ch.isalnum() or ch in "-_") else ("-" if ch.isspace() else "") for ch in display_name.strip())
    return f"imp_{slug.strip('-') or 'unknown'}-{hashlib.sha256(raw_name.encode('utf-8')).hexdigest()[:6]}"


def _create_friend_resolutions(account_type="human"):
    return [
        {"seat": 0, "action": "assign", "account_id": "nick@01", "alias_scope": "none"},
        {"seat": 1, "action": "create", "display_name": "Bot V3", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 2, "action": "create", "display_name": "Bot V4", "account_type": "managed_bot", "alias_scope": "none"},
        {"seat": 3, "action": "create", "display_name": "Friend", "account_type": account_type, "alias_scope": "none"},
    ]


def test_create_rejects_existing_deterministic_id(fake_download, participants_root):
    """P1：预先存在同 ID 不同类账号 → action=create 拒绝，且无任何残留。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    existing_id = _imp_id_for("FriendID", "Friend")
    registry.create_account(
        AccountCreate(account_id=existing_id, display_name="Friend", account_type="external_bot")
    )
    with pytest.raises(ValueError, match="已存在"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_create_friend_resolutions(account_type="human"))
    # 无 pending / staging / Match / revision / 新 alias
    assert not ledger.pending_transaction_path().exists()
    assert not intake._staging_dir(FAKE_LOG_ID).exists()
    assert ledger.list_matches().total == 0
    assert aliases.list_aliases() == []
    assert registry.get_account(existing_id).account_type == "external_bot"


def test_create_rejects_existing_disabled_id(fake_download, participants_root):
    """P1：既有同 ID 账号已停用 → action=create 拒绝，不产生引用停用账号的 Match。"""
    from participants.schemas import AccountUpdate

    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    existing_id = _imp_id_for("FriendID", "Friend")
    registry.create_account(
        AccountCreate(account_id=existing_id, display_name="Friend", account_type="external_bot")
    )
    registry.update_account(existing_id, AccountUpdate(enabled=False))
    with pytest.raises(ValueError, match="已存在"):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_create_friend_resolutions(account_type="human"))
    assert ledger.find_match_by_external("tenhou", FAKE_LOG_ID) is None


def test_recovery_still_works_with_strict_create(fake_download, participants_root, monkeypatch):
    """P1：严格初始创建不破坏 pending 恢复（既有故障恢复路径仍成立）。"""
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    original_append = ledger._append_revision

    def failing_append(row, *, fsync=False):
        raise RuntimeError("injected")

    monkeypatch.setattr(ledger, "_append_revision", failing_append)
    with pytest.raises(RuntimeError):
        intake.resolve_and_create_match(log_id=FAKE_LOG_ID, resolutions=_nick_resolutions(), session_id="s1")
    monkeypatch.setattr(ledger, "_append_revision", original_append)
    assert ledger.recover_pending_transaction() is True
    assert ledger.list_matches().total == 1
    assert not ledger.pending_transaction_path().exists()
