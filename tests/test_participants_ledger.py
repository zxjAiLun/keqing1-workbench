# -*- coding: utf-8 -*-
"""participants ledger：对局创建/修订/作废、排名计算、revision 审计。"""
from __future__ import annotations

import json
import pytest

from mahjong_env.final_rank import final_ranks

from participants import ledger, registry
from participants.schemas import AccountCreate, MatchCreate, MatchRevise, MatchSeat, MatchVoid
from participants.ledger import ValidationError


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    return root


@pytest.fixture
def four_accounts():
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    registry.create_account(AccountCreate(account_id="friend@01", display_name="Friend", account_type="human", default_controller="manual_only"))
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    registry.create_account(AccountCreate(account_id="mortal41b", display_name="Mortal 4.1b", account_type="external_bot"))
    return ["nick@01", "friend@01", "70k@01", "mortal41b"]


def _create_payload(scores, account_ids):
    return MatchCreate(
        occurred_at="2026-08-06T12:00:00+08:00",
        game_length="hanchan",
        seats=[MatchSeat(seat=i, account_id=account_ids[i]) for i in range(4)],
        final_scores=scores,
    )


def test_create_match_computes_ranks(four_accounts):
    match = ledger.create_match(_create_payload([42300, 28100, 19400, 10200], four_accounts), registry)
    assert list(match.ranks) == list(final_ranks([42300, 28100, 19400, 10200], initial_oya=0))
    assert match.revision == 1
    assert match.status == "active"
    assert len(match.seats) == 4


def test_revision_increments_and_audit_appended(four_accounts):
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    revised = ledger.revise_match(
        match.match_id,
        MatchRevise(final_scores=[30000, 25000, 25000, 20000]),
        registry,
    )
    assert revised.revision == 2
    assert revised.final_scores == [30000, 25000, 25000, 20000]
    # matches.jsonl 当前态 = 修订后（JSONL 每行一局）
    with open(ledger._matches_path(), encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    assert rows[0]["revision"] == 2
    # 审计日志有 2 条（create + revise）
    revisions = ledger.list_revisions(match.match_id)
    assert len(revisions) == 2
    assert revisions[-1]["action"] == "revise"
    assert revisions[-1]["before"]["revision"] == 1
    assert revisions[-1]["after"]["revision"] == 2


def test_void_match(four_accounts):
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    voided = ledger.void_match(match.match_id, MatchVoid(reason="中途结束"))
    assert voided.status == "void"
    assert voided.void_reason == "中途结束"
    assert voided.revision == 2
    revisions = ledger.list_revisions(match.match_id)
    assert revisions[-1]["action"] == "void"
    # 已作废不能再修订
    with pytest.raises(ValueError, match="作废"):
        ledger.revise_match(match.match_id, MatchRevise(final_scores=[1, 2, 3, 4]), registry)


def test_list_matches_filters(four_accounts):
    ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    all_matches = ledger.list_matches()
    assert all_matches.total == 2
    by_account = ledger.list_matches(account_id="nick@01")
    assert by_account.total == 2
    # 作废一局后 status 过滤
    target = all_matches.matches[0]
    ledger.void_match(target.match_id, MatchVoid(reason="x"))
    active = ledger.list_matches(status="active")
    assert active.total == 1
    voided = ledger.list_matches(status="void")
    assert voided.total == 1


def test_round_trip_persists(four_accounts, participants_root):
    ledger.create_match(_create_payload([42300, 28100, 19400, 10200], four_accounts), registry)
    # 重新加载（新进程语义：直接再读盘）
    rows = ledger.list_matches()
    assert rows.matches[0].final_scores == [42300, 28100, 19400, 10200]
    assert (participants_root / "matches.jsonl").exists()
    assert (participants_root / "match_revisions.jsonl").exists()


def test_force_save_bypasses_total_mismatch(four_accounts):
    payload = _create_payload([42300, 28100, 19400, 10201], four_accounts)  # 总分 100001
    payload.force = True
    payload.reason = "外部结算"
    match = ledger.create_match(payload, registry)
    assert match.final_scores == [42300, 28100, 19400, 10201]
    revisions = ledger.list_revisions(match.match_id)
    assert revisions[-1]["force"] is True
    assert revisions[-1]["reason"] == "外部结算"
    # force+reason 放行 score_total_mismatch：passed=True，但 issue 仍在审计中
    assert revisions[-1]["validation"]["passed"] is True
    assert "score_total_mismatch" in {i["code"] for i in revisions[-1]["validation"]["issues"]}


# ---------------------------------------------------------------------------
# P1-3：pending transaction 恢复（revision append / matches replace / 清理三处故障注入）
# ---------------------------------------------------------------------------

def _build_rev2_tx(match):
    """构造 rev-2 修订状态 + revision 行。"""
    rev2 = match.model_copy(deep=True, update={
        "final_scores": [30000, 25000, 25000, 20000],
        "revision": 2,
        "latest_revision_id": f"rev_{match.match_id}_2",
    })
    revision_row = {
        "schema": "keqing.participant.match_revision.v1",
        "revision_id": rev2.latest_revision_id,
        "match_id": match.match_id,
        "revision": 2,
        "action": "revise",
        "created_at": rev2.updated_at,
        "by": "manual-entry",
        "force": False,
        "reason": None,
        "validation": {"passed": True, "issues": []},
        "before": match.model_dump(by_alias=True),
        "after": rev2.model_dump(by_alias=True),
    }
    return rev2, revision_row


def test_recovery_appends_missing_revision(four_accounts):
    """故障 1：pending 已写、revision 未落盘 → 下次写入恢复补 append + 更新 match。"""
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    rev2, revision_row = _build_rev2_tx(match)
    ledger._write_pending_transaction(rev2, revision_row)  # 模拟 append 前崩溃
    ledger.void_match(match.match_id, MatchVoid(reason="测试"))
    assert ledger.get_match(match.match_id).revision == 3  # 恢复 rev2 + void rev3
    assert not ledger._pending_tx_path().exists()
    revisions = ledger.list_revisions(match.match_id)
    assert any(r["revision"] == 2 for r in revisions)


def test_recovery_rewrites_match_when_revision_present(four_accounts):
    """故障 2：revision 已 append、matches 未替换 → 恢复补替换并删 pending。"""
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    rev2, revision_row = _build_rev2_tx(match)
    ledger._write_pending_transaction(rev2, revision_row)
    ledger._append_revision(revision_row)  # 模拟 matches replace 前崩溃
    ledger.void_match(match.match_id, MatchVoid(reason="测试"))
    assert ledger.get_match(match.match_id).revision == 3
    assert not ledger._pending_tx_path().exists()
    assert len(ledger.list_revisions(match.match_id)) == 3


def test_recovery_cleanup_only_when_complete(four_accounts):
    """故障 3：revision 与 match 都已落盘、仅删 pending 失败 → 恢复只清理。"""
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    rev2, revision_row = _build_rev2_tx(match)
    ledger._write_pending_transaction(rev2, revision_row)
    ledger._append_revision(revision_row)
    ledger._rewrite_match(rev2)
    ledger.void_match(match.match_id, MatchVoid(reason="测试"))
    assert ledger.get_match(match.match_id).revision == 3
    assert not ledger._pending_tx_path().exists()
    assert len(ledger.list_revisions(match.match_id)) == 3


def test_match_references_account_includes_void(four_accounts):
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    ledger.void_match(match.match_id, MatchVoid(reason="中途结束"))
    assert ledger.match_references_account("nick@01") is True, "void 局仍应阻止硬删除账号"


def test_revision_history_blocks_hard_delete(four_accounts):
    """P1-1：账号被修订移出当前座位后，历史 revision 仍引用 → 只能软删。"""
    from participants.schemas import AccountCreate as AC

    registry.create_account(AC(account_id="new@01", display_name="New", account_type="human"))
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    ledger.revise_match(
        match.match_id,
        MatchRevise(
            seats=[
                MatchSeat(seat=0, account_id="new@01"),
                MatchSeat(seat=1, account_id=four_accounts[1]),
                MatchSeat(seat=2, account_id=four_accounts[2]),
                MatchSeat(seat=3, account_id=four_accounts[3]),
            ]
        ),
        registry,
    )
    # nick@01 已不在当前座位，但 rev1 after / rev2 before 仍引用
    assert ledger.match_references_account("nick@01") is True
    # 统一删除（引用检查在锁内）：必须软删
    result = registry.delete_account_guarded(
        "nick@01", lambda: ledger.match_references_account("nick@01") or registry.identity_references_account("nick@01")
    )
    assert result["disabled"] is True
    assert registry.get_account("nick@01").enabled is False


def test_read_path_recovers_pending(four_accounts):
    """P2-1：只读请求（模拟重启后首次 GET）应自动恢复未完成事务。"""
    match = ledger.create_match(_create_payload([25000] * 4, four_accounts), registry)
    rev2, revision_row = _build_rev2_tx(match)
    ledger._write_pending_transaction(rev2, revision_row)
    assert ledger._pending_tx_path().exists()
    fetched = ledger.get_match(match.match_id)
    assert fetched.revision == 2
    assert not ledger._pending_tx_path().exists()


def test_guarded_delete_sees_only_pending_create(four_accounts):
    """P1-2：只有 pending（尚无 match/revision 落盘）时，guarded delete 也必须软删账号，
    恢复后不得出现悬空引用。"""
    from participants import registry as _reg
    from participants.schemas import AccountCreate as AC, MatchCreate as MC

    # 构造一个"写 pending 后、revision append 前崩溃"的 create 事务
    payload = MC(
        occurred_at="2026-08-06T12:00:00+08:00",
        game_length="hanchan",
        seats=[MatchSeat(seat=i, account_id=four_accounts[i]) for i in range(4)],
        final_scores=[25000] * 4,
    )
    _, pre_issues = ledger.validate_match(
        payload.seats, payload.final_scores, starting_points=25000, initial_oya=0,
        force=False, reason=None, registry=registry,
    )
    assert not ledger.blocking_issues(pre_issues, force=False, reason=None)
    now = ledger.now_iso()
    match_id = f"m_pending_{four_accounts[0].replace('@', '_')}"
    match = ledger.Match(
        match_id=match_id, occurred_at=payload.occurred_at, game_length="hanchan",
        rule_set="standard-4p", starting_points=25000, initial_oya=0,
        source="manual", data_completeness="result_only",
        seats=payload.seats, final_scores=payload.final_scores, ranks=[0, 1, 2, 3],
        revision=1, latest_revision_id=f"rev_{match_id}_1", created_at=now, updated_at=now,
    )
    revision_row = {
        "schema": "keqing.participant.match_revision.v1",
        "revision_id": match.latest_revision_id, "match_id": match_id, "revision": 1,
        "action": "create", "created_at": now, "by": "manual-entry", "force": False,
        "reason": None, "validation": {"passed": True, "issues": []},
        "before": None, "after": match.model_dump(by_alias=True),
    }
    ledger._write_pending_transaction(match, revision_row)
    assert not ledger._matches_path().exists()  # 尚无当前态
    assert ledger._read_revision_rows() == []  # 尚无 revision 落盘（用原始读取，避免触发读路径恢复）

    # guarded delete（与 API 相同的引用检查器：锁内先恢复 pending 再查引用）
    result = registry.delete_account_guarded(
        four_accounts[0],
        lambda: (
            ledger.recover_pending_transaction_locked(),
            ledger.match_references_account(four_accounts[0]) or registry.identity_references_account(four_accounts[0]),
        )[1],
    )
    assert result["disabled"] is True, "只有 pending 引用时必��软删"

    # checker 已把 pending 恢复成真实 match；引用的账号必须仍存在（未被硬删）
    assert not ledger._pending_tx_path().exists()
    recovered = ledger.get_match(match_id)
    assert recovered is not None
    assert registry.get_account(four_accounts[0]) is not None
    assert registry.get_account(four_accounts[0]).enabled is False


def test_create_match_controller_default_resolved_in_lock(four_accounts, monkeypatch):
    """P1：锁外预校验不得固化 controller 默认值——锁内必须以新默认值重新解析。"""
    from participants.schemas import AccountUpdate as AU

    original_validate = ledger.validate_match
    calls = {"n": 0}

    def racing_validate(*args, **kwargs):
        calls["n"] += 1
        result = original_validate(*args, **kwargs)
        if calls["n"] == 1:
            # 第一次（锁外）真实校验完成后、取得 data_lock 前，账号默认 controller 被并发修改
            registry.update_account(four_accounts[0], AU(default_controller="manual_only"))
        return result

    monkeypatch.setattr(ledger, "validate_match", racing_validate)

    payload = _create_payload([25000] * 4, four_accounts)  # controller_type 均未显式填写
    assert payload.seats[0].controller_type is None
    match = ledger.create_match(payload, registry)
    assert match.seats[0].controller_type == "manual_only", (
        f"锁内复检应使用新默认值 manual_only（得到 {match.seats[0].controller_type}）"
    )
    # 隔离合同：锁外/锁内解析都不得污染原始请求对象
    assert payload.seats[0].controller_type is None
    assert calls["n"] >= 2, "validate_match 应在锁外与锁内各执行一次"
