# -*- coding: utf-8 -*-
"""participants ledger：统一对局账本 + 手动录入 + revision 审计。

- ``matches.jsonl``：每行一局（当前态），修改时整行原子重写。
- ``match_revisions.jsonl``：追加式审计日志，每次 create/revise/void 记全量 before/after。
- 排名恒由 ``mahjong_env.final_rank.final_ranks`` 服务端计算并落盘。
- 校验失败默认拒绝（422）；``force + reason`` 允许强制保存，原因与校验结果入 revision。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

from mahjong_env.final_rank import final_ranks

from .paths import data_root, now_iso, atomic_write_text, data_lock
from .schemas import (
    MATCH_REVISION_SCHEMA,
    Match,
    MatchCreate,
    MatchListResponse,
    MatchRevise,
    MatchSeat,
    MatchVoid,
    RevisionSummary,
    ValidationIssue,
)

_write_lock = threading.RLock()


def _matches_path() -> Path:
    return data_root() / "matches.jsonl"


def _revisions_path() -> Path:
    return data_root() / "match_revisions.jsonl"


def _generate_match_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d")
    return f"m_{stamp}_{uuid.uuid4().hex[:6]}"


def _generate_revision_id(match_id: str, revision: int) -> str:
    return f"rev_{match_id}_{revision}"


def generate_match_id() -> str:
    """公开：intake 事务在锁内预生成 match_id（恢复幂等依赖确定性 revision id）。"""
    return _generate_match_id()


def generate_revision_id(match_id: str, revision: int) -> str:
    return _generate_revision_id(match_id, revision)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _read_match_rows() -> list[dict]:
    path = _matches_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_revision_rows() -> list[dict]:
    path = _revisions_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def list_matches(
    *,
    source: str | None = None,
    source_ref: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
    from_at: str | None = None,
    to_at: str | None = None,
    provider: str | None = None,
    external_match_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> MatchListResponse:
    _maybe_recover_pending()
    rows = _read_match_rows()
    matches: list[Match] = []
    for raw in rows:
        match = Match.model_validate(raw)
        if source and match.source != source:
            continue
        if source_ref and match.source_ref != source_ref:
            continue
        if provider and match.provider != provider:
            continue
        if external_match_id and match.external_match_id != external_match_id:
            continue
        if status and match.status != status:
            continue
        if account_id and not any(seat.account_id == account_id for seat in match.seats):
            continue
        if from_at and match.occurred_at < from_at:
            continue
        if to_at and match.occurred_at > to_at:
            continue
        matches.append(match)
    matches.sort(key=lambda m: (m.occurred_at, m.match_id), reverse=True)
    total = len(matches)
    if offset:
        matches = matches[offset:]
    if limit is not None:
        matches = matches[:limit]
    return MatchListResponse(matches=matches, total=total)


def count_matches_for_season(season_id: str) -> int:
    """统计引用该赛季的历史对局数（删除赛季前的引用检查）。

    任何 status（含 void）都算引用——历史事实仍指向该赛季。
    """
    _maybe_recover_pending()
    return count_matches_for_season_locked(season_id)


def count_matches_for_season_locked(season_id: str) -> int:
    """锁内版本：调用方已持有 data_lock 时使用。

    不做 pending 恢复（恢复本身要获取 data_lock，锁内调用会自锁）；
    data_lock 已保证没有进行中的 intake 事务，因此跳过恢复是安全的。
    """
    return sum(1 for raw in _read_match_rows() if Match.model_validate(raw).season_id == season_id)


def get_match(match_id: str) -> Match | None:
    _maybe_recover_pending()
    for raw in _read_match_rows():
        if raw.get("match_id") == match_id:
            return Match.model_validate(raw)
    return None


def find_match_by_external(provider: str, external_match_id: str) -> Match | None:
    """按外部唯一键查找（幂等防重）：provider + external_match_id。"""
    _maybe_recover_pending()
    for raw in _read_match_rows():
        match = Match.model_validate(raw)
        if match.provider == provider and match.external_match_id == external_match_id:
            return match
    return None


def _read_match(match_id: str) -> Match | None:
    """锁内使用的原始读取（不触发 pending 恢复，避免文件锁重入）。"""
    for raw in _read_match_rows():
        if raw.get("match_id") == match_id:
            return Match.model_validate(raw)
    return None


def list_revisions(match_id: str) -> list[dict]:
    _maybe_recover_pending()
    return [row for row in _read_revision_rows() if row.get("match_id") == match_id]


def list_revision_summaries(match_id: str) -> list[RevisionSummary]:
    return [RevisionSummary.model_validate(row) for row in list_revisions(match_id)]


def match_references_account(account_id: str) -> bool:
    """账号是否被引用：当前 match seats（active/void）+ 任意 revision before/after seats
    + 未完成 pending transaction 的 match/revision seats（P1-2）。"""
    for raw in _read_match_rows():
        match = Match.model_validate(raw)
        if any(seat.account_id == account_id for seat in match.seats):
            return True
    for row in _read_revision_rows():
        for key in ("before", "after"):
            snapshot = row.get(key) or {}
            for seat in snapshot.get("seats", []):
                if seat.get("account_id") == account_id:
                    return True
    pending = _pending_tx_path()
    if pending.exists():
        try:
            tx = json.loads(pending.read_text(encoding="utf-8"))
        except ValueError:
            tx = {}
        match_snapshot = tx.get("match") or {}
        for seat in match_snapshot.get("seats", []):
            if seat.get("account_id") == account_id:
                return True
        revision_row = tx.get("revision") or {}
        for key in ("before", "after"):
            for seat in (revision_row.get(key) or {}).get("seats", []):
                if seat.get("account_id") == account_id:
                    return True
    return False


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

# force 允许覆盖的 issue：其余一律不可绕过。
FORCEABLE_ISSUE_CODES = {"score_total_mismatch", "account_disabled"}
FATAL_ISSUE_CODES = {
    "seat_count",
    "seat_duplicate",
    "account_duplicate",
    "account_unknown",
    "model_identity_mismatch",
    "model_artifact_mismatch",
    "score_count",
    "score_type",
    "initial_oya",
    "force_reason_required",
}


def blocking_issues(issues: list[ValidationIssue], *, force: bool, reason: str | None) -> list[ValidationIssue]:
    """返回实际阻塞的 issue：真 allowlist——只有明确可 force 的 code 能在 force+reason 下放行，
    其余所有已知或未来新增的 code 一律默认阻塞。"""
    has_force_reason = force and bool((reason or "").strip())
    return [
        issue
        for issue in issues
        if issue.code not in FORCEABLE_ISSUE_CODES or not has_force_reason
    ]


def _assert_seat_accounts_exist(seats, registry) -> None:
    """锁内复检座位账号存在（防 TOCTOU：校验与写入必须同一临界区）。"""
    missing = [
        seat.account_id for seat in seats
        if registry.get_account(seat.account_id) is None
    ]
    if missing:
        issues = [
            ValidationIssue(code="account_unknown", message=f"账号不存在: {aid}")
            for aid in missing
        ]
        raise ValidationError(issues)


def validate_match(
    seats: Sequence[MatchSeat],
    final_scores: Sequence[int],
    *,
    starting_points: int,
    initial_oya: int,
    force: bool,
    reason: str | None,
    registry,
) -> tuple[list[int], list[ValidationIssue]]:
    """返回 (ranks, issues)。排名恒计算；校验不通过时由调用方决定是否强制保存。"""
    issues: list[ValidationIssue] = []

    if len(seats) != 4:
        issues.append(ValidationIssue(code="seat_count", message=f"必须恰好 4 个座位，得到 {len(seats)}"))
    seat_nums = [seat.seat for seat in seats]
    if len(set(seat_nums)) != len(seat_nums):
        issues.append(ValidationIssue(code="seat_duplicate", message="座位编号重复"))
    account_ids = [seat.account_id for seat in seats]
    if len(set(account_ids)) != len(account_ids):
        issues.append(ValidationIssue(code="account_duplicate", message="同一对局不能重复出现同一账号"))

    for seat in seats:
        account = registry.get_account(seat.account_id)
        if account is None:
            issues.append(ValidationIssue(code="account_unknown", message=f"账号不存在: {seat.account_id}"))
            continue
        if not account.enabled:
            issues.append(
                ValidationIssue(code="account_disabled", message=f"账号已停用: {seat.account_id}（可强制保存）")
            )
        if seat.controller_type is None:
            seat.controller_type = account.default_controller
        if seat.model_identity_id and not registry.identity_belongs_to_account(seat.model_identity_id, seat.account_id):
            issues.append(
                ValidationIssue(
                    code="model_identity_mismatch",
                    message=f"账号 {seat.account_id} 未绑定模型身份 {seat.model_identity_id}",
                )
            )
        if not registry.artifact_belongs_to_identity(seat.model_identity_id, seat.model_artifact_id):
            issues.append(
                ValidationIssue(
                    code="model_artifact_mismatch",
                    message=f"模型产物 {seat.model_artifact_id} 不属于身份 {seat.model_identity_id}",
                )
            )
        if getattr(seat, "external_revision_id", None) and not registry.external_revision_belongs_to_identity(
            seat.model_identity_id, seat.external_revision_id
        ):
            issues.append(
                ValidationIssue(
                    code="external_revision_mismatch",
                    message=f"外部版本 {seat.external_revision_id} 不属于身份 {seat.model_identity_id}",
                )
            )

    if len(final_scores) != 4:
        issues.append(ValidationIssue(code="score_count", message=f"必须恰好 4 个最终分数，得到 {len(final_scores)}"))
    if not all(isinstance(s, int) for s in final_scores):
        issues.append(ValidationIssue(code="score_type", message="最终分数必须为整数"))

    if len(final_scores) == 4 and all(isinstance(s, int) for s in final_scores):
        expected = 4 * starting_points
        if sum(final_scores) != expected:
            issues.append(
                ValidationIssue(
                    code="score_total_mismatch",
                    message=f"总分 {sum(final_scores)} ≠ 期望 {expected}",
                )
            )

    if initial_oya < 0 or initial_oya > 3:
        issues.append(ValidationIssue(code="initial_oya", message=f"initial_oya 必须在 [0,3]，得到 {initial_oya}"))

    if force and not (reason or "").strip():
        issues.append(ValidationIssue(code="force_reason_required", message="强制保存必须填写原因"))

    try:
        ranks = list(final_ranks(list(final_scores), initial_oya=initial_oya))
    except ValueError:
        ranks = [-1, -1, -1, -1]
    return ranks, issues


# ---------------------------------------------------------------------------
# 写入（可恢复事务）
# ---------------------------------------------------------------------------

def _pending_tx_path() -> Path:
    return data_root() / "pending_transaction.json"


def pending_transaction_path() -> Path:
    return _pending_tx_path()


def ladder_dirty_path(season_id: str) -> Path:
    """赛季天梯 dirty marker（发布器幂等重建的依据）。"""
    return data_root() / f"ladder_dirty_{season_id}.json"


def mark_ladder_dirty(season_id: str | None) -> None:
    """标记赛季天梯需重算。每次写入新的 generation（CAS 协议，P1-2）。

    调用方须已持有 data_lock（不重取文件锁）。
    幂等语义：多写一次只是多一次重建（安全 false-positive），少写会永久漏更新。
    """
    if not season_id:
        return
    path = ladder_dirty_path(season_id)
    atomic_write_text(
        path,
        json.dumps(
            {"season_id": season_id, "generation": uuid.uuid4().hex, "marked_at": now_iso()},
            ensure_ascii=False,
        ),
    )


def read_ladder_generation(season_id: str) -> str | None:
    """读取当前 dirty marker 的 generation（无锁读；投影 CAS 用）。"""
    path = ladder_dirty_path(season_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload.get("generation")


def begin_ladder_projection(season_id: str) -> str | None:
    """投影起点 barrier（P1-1）：锁内恢复 pending 后返回当前 dirty generation。

    防止投影在「dirty generation 已可见、但对应 Match 事务尚未提交」时读到旧
    ledger 并清掉该 generation。
    """
    with _write_lock, data_lock():
        _recover_pending_transaction()
        return read_ladder_generation(season_id)


def clear_ladder_dirty(season_id: str) -> bool:
    with _write_lock, data_lock():
        try:
            ladder_dirty_path(season_id).unlink()
            return True
        except FileNotFoundError:
            return False


def complete_ladder_projection(season_id: str, expected_generation: str | None) -> bool:
    """发布完成后的 CAS：若 dirty generation 仍为 expected，则清 dirty + 批量置 ready。

    返回 False 表示发布期间有新的 ledger 写入（generation 已变），dirty 与 pending
    必须保留（P1-2 lost-update 防护）。检查 + 清除 + 状态更新在同一 data_lock 临界区。
    """
    with _write_lock, data_lock():
        _recover_pending_transaction()
        current_gen = read_ladder_generation(season_id)
        if current_gen != expected_generation:
            return False
        try:
            ladder_dirty_path(season_id).unlink()
        except FileNotFoundError:
            pass
        _set_season_projection_state_locked(season_id, "ready")
        return True


def mark_season_projection_error(season_id: str, expected_generation: str | None) -> bool:
    """发布失败后的 CAS 回写：仅当 dirty generation 仍为 expected 才置 error。

    防止过期失败覆盖更新后的 ready/pending（P1-2 single-flight 补充防护）。
    """
    with _write_lock, data_lock():
        _recover_pending_transaction()
        if read_ladder_generation(season_id) != expected_generation:
            return False
        _set_season_projection_state_locked(season_id, "error")
        return True


def _set_season_projection_state_locked(season_id: str, state: str) -> int:
    """锁内批量更新赛季内 active + rating_eligible match 的投影状态。"""
    rows = _read_match_rows()
    changed = 0
    for raw in rows:
        match = Match.model_validate(raw)
        if match.season_id != season_id or match.status != "active":
            continue
        if not match.rating_eligible:
            # P2-2：不参与正式计分的比赛恒为 not_applicable
            if match.ladder_projection_state != "not_applicable":
                match.ladder_projection_state = "not_applicable"
                match.updated_at = now_iso()
                _rewrite_match(match)
                changed += 1
            continue
        if match.ladder_projection_state == state:
            continue
        match.ladder_projection_state = state
        match.updated_at = now_iso()
        _rewrite_match(match)
        changed += 1
    return changed


def set_season_projection_state(season_id: str, state: str) -> int:
    """批量更新赛季内 match 的投影状态（ready/error）。

    投影状态是派生视图（非对局修订），直接重写 matches.jsonl，不产生 revision。
    返回更新数。
    """
    with _write_lock, data_lock():
        _recover_pending_transaction()
        return _set_season_projection_state_locked(season_id, state)


def _append_revision(row: dict, *, fsync: bool = False) -> None:
    with open(_revisions_path(), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if fsync:
            fh.flush()
            os.fsync(fh.fileno())


def _rewrite_match(match: Match) -> None:
    rows = _read_match_rows()
    out: list[dict] = []
    replaced = False
    for raw in rows:
        if raw.get("match_id") == match.match_id:
            out.append(match.model_dump(by_alias=True))
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(match.model_dump(by_alias=True))
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in out)
    atomic_write_text(_matches_path(), text)


def _write_pending_transaction(match: Match, revision_row: dict) -> None:
    atomic_write_text(
        _pending_tx_path(),
        json.dumps({"match": match.model_dump(by_alias=True), "revision": revision_row}, ensure_ascii=False),
    )


def _recover_pending_transaction() -> None:
    """启动或下次写入时恢复未完成事务（幂等）。

    - ledger 事务（默认）：pending 记录 after-match 与 revision 行；
    - intake 事务（``intent=="intake"``）：额外包含账号/别名/artifact 暂存，
      恢复逻辑委托给 participants.intake。
    """
    path = _pending_tx_path()
    if not path.exists():
        return
    tx = json.loads(path.read_text(encoding="utf-8"))
    if tx.get("intent") == "intake":
        from . import intake as _intake

        _intake.recover_intake_transaction_locked(tx)
        return
    after = Match.model_validate(tx["match"])
    revision_row = tx["revision"]
    if not any(row.get("revision_id") == revision_row["revision_id"] for row in _read_revision_rows()):
        _append_revision(revision_row, fsync=True)
    _rewrite_match(after)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _maybe_recover_pending() -> None:
    """只读入口：存在未完成事务时加锁恢复，避免读侧持续看到旧当前态+新 revision。"""
    if not _pending_tx_path().exists():
        return
    with _write_lock, data_lock():
        _recover_pending_transaction()


def recover_pending_transaction() -> bool:
    """公开恢复入口（服务启动 / 运维）。返回是否实际发生了恢复。"""
    if not _pending_tx_path().exists():
        return False
    with _write_lock, data_lock():
        _recover_pending_transaction()
    return True


def recover_pending_transaction_locked() -> None:
    """锁内恢复入口：调用方已持有 data_lock 时使用（供 guarded delete 等路径）。"""
    _recover_pending_transaction()


def _transactional_match_update(match: Match, revision_row: dict) -> None:
    """write-ahead：pending → revision(fsync) → matches 原子替换 → 删 pending。"""
    _write_pending_transaction(match, revision_row)
    _append_revision(revision_row, fsync=True)
    _rewrite_match(match)
    try:
        _pending_tx_path().unlink()
    except FileNotFoundError:
        pass



def create_match(payload: MatchCreate, registry) -> Match:
    if not payload.occurred_at:
        raise ValueError("occurred_at 不能为空")
    # 锁外预校验：快速失败。deep-copy 隔离副作用——validate_match 会原地填充
    # controller_type，不能污染最终输入（P1：默认值必须以锁内结果为准）。
    pre_seats = [seat.model_copy(deep=True) for seat in payload.seats]
    _pre_ranks, pre_issues = validate_match(
        pre_seats,
        payload.final_scores,
        starting_points=payload.starting_points,
        initial_oya=payload.initial_oya,
        force=payload.force,
        reason=payload.reason,
        registry=registry,
    )
    if blocking_issues(pre_issues, force=payload.force, reason=payload.reason):
        raise ValidationError(pre_issues, "score_total_mismatch" in {i.code for i in pre_issues})

    with _write_lock, data_lock():
        _recover_pending_transaction()
        now = now_iso()
        match_id = _generate_match_id()
        match, issues, blocking = build_match(payload, registry, match_id, now)
        # P1-2：正式计分 → 正式赛季资格 gate（rating_eligible ⇒ 必填 season + 校验）
        # 返回值是 trim 后的规范 season_id 与逐座 CanonicalExecutionProvenance，必须回写 Match
        if match.rating_eligible:
            from .ladder_eligibility import ensure_ladder_eligibility
            from .execution_provenance import freeze_execution_provenance

            admission = ensure_ladder_eligibility(match.season_id, match.seats, registry=registry)
            match.season_id = admission.season_id
            match.runtime_binding = admission.runtime_binding
            for s in match.seats:
                if s.seat in admission.provenance_by_seat:
                    frozen_prov = freeze_execution_provenance(admission.provenance_by_seat[s.seat])
                    s.execution_provenance = frozen_prov
                    s.model_identity_id = frozen_prov.model_identity_id
                    s.model_artifact_id = frozen_prov.model_artifact_id
                    s.external_revision_id = frozen_prov.external_revision_id
        # P1-5：dirty 先于 Match 提交（即使后续崩溃也只会多重建一次，不会永久漏更新）
        mark_ladder_dirty(match.season_id)
        _transactional_match_update(
            match,
            {
                "schema": MATCH_REVISION_SCHEMA,
                "revision_id": match.latest_revision_id,
                "match_id": match.match_id,
                "revision": 1,
                "action": "create",
                "created_at": now,
                "by": "migration" if payload.source == "imported" else "manual-entry",
                "force": payload.force,
                "reason": payload.reason,
                "validation": {"passed": not blocking, "issues": [i.model_dump() for i in issues]},
                "before": None,
                "after": match.model_dump(by_alias=True),
            },
        )
    return match


def build_match(
    payload: MatchCreate,
    registry,
    match_id: str,
    now: str,
) -> tuple[Match, list[ValidationIssue], list[ValidationIssue]]:
    """锁内/锁外通用：完整校验 + 构造 Match（调用方持有 data_lock）。

    从原始请求 deep-copy 座位，controller 默认值/账号 enabled/identity/artifact
    归属全部以本次校验的 registry 状态为准。返回 (match, issues, blocking)。
    """
    resolved_seats = [seat.model_copy(deep=True) for seat in payload.seats]
    ranks, issues = validate_match(
        resolved_seats,
        payload.final_scores,
        starting_points=payload.starting_points,
        initial_oya=payload.initial_oya,
        force=payload.force,
        reason=payload.reason,
        registry=registry,
    )
    blocking = blocking_issues(issues, force=payload.force, reason=payload.reason)
    if blocking:
        raise ValidationError(issues, "score_total_mismatch" in {i.code for i in issues})
    match = Match(
        match_id=match_id,
        occurred_at=payload.occurred_at,
        game_length=payload.game_length,
        rule_set=payload.rule_set,
        starting_points=payload.starting_points,
        initial_oya=payload.initial_oya,
        source=payload.source,
        source_ref=payload.source_ref,
        note=payload.note,
        data_completeness=payload.data_completeness,
        replay_id=payload.replay_id,
        provider=payload.provider,
        external_match_id=payload.external_match_id,
        raw_player_names=payload.raw_player_names,
        resolution=payload.resolution,
        season_id=payload.season_id,
        rating_eligible=payload.rating_eligible,
        ladder_projection_state="pending" if (payload.season_id and payload.rating_eligible) else "not_applicable",
        seats=resolved_seats,
        final_scores=payload.final_scores,
        ranks=ranks,
        revision=1,
        latest_revision_id=_generate_revision_id(match_id, 1),
        created_at=now,
        updated_at=now,
        created_by="migration" if payload.source == "imported" else "manual",
    )
    return match, issues, blocking


def revise_match(match_id: str, payload: MatchRevise, registry) -> Match:
    with _write_lock, data_lock():
        _recover_pending_transaction()
        current = _read_match(match_id)
        if current is None:
            raise KeyError(f"match not found: {match_id}")
        if current.status == "void":
            raise ValueError("已作废的对局不能再修订")

        next_match = current.model_copy(deep=True)
        # 直接应用 typed 字段（保留 MatchSeat 对象，避免 dict 污染）
        if payload.occurred_at is not None:
            next_match.occurred_at = payload.occurred_at
        if payload.game_length is not None:
            next_match.game_length = payload.game_length
        if payload.rule_set is not None:
            next_match.rule_set = payload.rule_set
        if payload.starting_points is not None:
            next_match.starting_points = payload.starting_points
        if payload.initial_oya is not None:
            next_match.initial_oya = payload.initial_oya
        if payload.note is not None:
            next_match.note = payload.note
        if payload.data_completeness is not None:
            next_match.data_completeness = payload.data_completeness
        if payload.seats is not None:
            next_match.seats = payload.seats
        if payload.final_scores is not None:
            next_match.final_scores = payload.final_scores
        # P1-3：区分「字段省略」与「显式 null」——显式 null 才能清空 season
        if "season_id" in payload.model_fields_set:
            next_match.season_id = payload.season_id
        if "rating_eligible" in payload.model_fields_set:
            next_match.rating_eligible = bool(payload.rating_eligible)
        # P2-2：season 缺失或 not eligible → not_applicable；否则 pending（待重算）
        if next_match.season_id and next_match.rating_eligible:
            next_match.ladder_projection_state = "pending"
        else:
            next_match.ladder_projection_state = "not_applicable"
        old_season = current.season_id
        new_season = next_match.season_id

        ranks, issues = validate_match(
            next_match.seats,
            next_match.final_scores,
            starting_points=next_match.starting_points,
            initial_oya=next_match.initial_oya,
            force=payload.force,
            reason=payload.reason,
            registry=registry,
        )
        next_match.ranks = ranks
        blocking = blocking_issues(issues, force=payload.force, reason=payload.reason)
        if blocking:
            raise ValidationError(issues, "score_total_mismatch" in {i.code for i in issues})

        next_match.revision = current.revision + 1
        next_match.latest_revision_id = _generate_revision_id(match_id, next_match.revision)
        next_match.updated_at = now_iso()
        _assert_seat_accounts_exist(next_match.seats, registry)
        # P1-2：修订后为正式计分 → 正式赛季资格 gate（rating_eligible ⇒ 必填 season + 校验）
        # 返回值是 trim 后的规范 season_id 与逐座 CanonicalExecutionProvenance，必须回写 Match
        if next_match.rating_eligible:
            from .ladder_eligibility import ensure_ladder_eligibility
            from .execution_provenance import freeze_execution_provenance

            admission = ensure_ladder_eligibility(
                next_match.season_id, next_match.seats, registry=registry
            )
            next_match.season_id = admission.season_id
            next_match.runtime_binding = admission.runtime_binding
            for s in next_match.seats:
                if s.seat in admission.provenance_by_seat:
                    frozen_prov = freeze_execution_provenance(admission.provenance_by_seat[s.seat])
                    s.execution_provenance = frozen_prov
                    s.model_identity_id = frozen_prov.model_identity_id
                    s.model_artifact_id = frozen_prov.model_artifact_id
                    s.external_revision_id = frozen_prov.external_revision_id
            new_season = next_match.season_id
        # P1-3/P1-5：旧赛季与新赛季都标 dirty（去重），且先于 Match 提交
        for season in {old_season, new_season}:
            mark_ladder_dirty(season)
        _transactional_match_update(
            next_match,
            {
                "schema": MATCH_REVISION_SCHEMA,
                "revision_id": next_match.latest_revision_id,
                "match_id": match_id,
                "revision": next_match.revision,
                "action": "revise",
                "created_at": next_match.updated_at,
                "by": "manual-entry",
                "force": payload.force,
                "reason": payload.reason,
                "validation": {"passed": not blocking, "issues": [i.model_dump() for i in issues]},
                "before": current.model_dump(by_alias=True),
                "after": next_match.model_dump(by_alias=True),
            },
        )
        return next_match


def void_match(match_id: str, payload: MatchVoid) -> Match:
    with _write_lock, data_lock():
        _recover_pending_transaction()
        current = _read_match(match_id)
        if current is None:
            raise KeyError(f"match not found: {match_id}")
        if current.status == "void":
            raise ValueError("对局已作废")
        now = now_iso()
        updated = current.model_copy(
            update={
                "status": "void",
                "void_reason": payload.reason,
                "revision": current.revision + 1,
                "latest_revision_id": _generate_revision_id(match_id, current.revision + 1),
                "updated_at": now,
            }
        )
        # P1-5：作废比赛不再计分 → 提交前标 dirty（作废前所属赛季）
        mark_ladder_dirty(current.season_id)
        _transactional_match_update(
            updated,
            {
                "schema": MATCH_REVISION_SCHEMA,
                "revision_id": updated.latest_revision_id,
                "match_id": match_id,
                "revision": updated.revision,
                "action": "void",
                "created_at": now,
                "by": "manual-entry",
                "force": False,
                "reason": payload.reason,
                "validation": {"passed": True, "issues": []},
                "before": current.model_dump(by_alias=True),
                "after": updated.model_dump(by_alias=True),
            },
        )
        return updated


class ValidationError(ValueError):
    """校验失败（对应 HTTP 422），携带 issues 与是否总分不匹配标记。"""

    def __init__(self, issues: list[ValidationIssue], score_mismatch: bool = False):
        super().__init__("; ".join(issue.message for issue in issues))
        self.issues = issues
        self.score_mismatch = score_mismatch
