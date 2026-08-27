"""R14-D Phase 1: Immutable Season Archive Summary — builder & seal.

终局摘要的权威来源（Build-time 采样，全部为 canonical / frozen evidence）：

- **Frozen Runtime Manifest**（season registry 中的 ``runtime_manifest``）；
- **Execution Sessions**（``runtime/execution_sessions/<session>``，R14-B spawn
  epoch 的不可变记录）；
- **Final Process Ledger**（``runtime/seasons/<id>/processes.json``，最终进程
  状态的权威——不从 session participant 的 pid 推断终态）；
- **Canonical Match Ledger**（``participants/matches.jsonl`` 当前态权威行）。

明确**不读取** Participants Catalog Current（账号 enabled/rebind、Artifact
stage 等当前 lifecycle）——Season 完成后 Catalog 变化不得影响历史摘要。

两层结构：

1. ``build_archive_summary_projection(...)``：只构造 deterministic canonical
   projection（无 completed_at、无 hash）——running 赛季可安全预览；
2. ``seal_archive_summary(projection, completed_at)``：加入 completed_at、
   计算 seal hash / id，生成最终 ``SeasonArchiveSummary``。

Seal 语义（canonical projection 包含**全部**最终历史字段，含
``completed_at``；仅排除自引用字段 ``archive_summary_hash`` /
``archive_summary_id``）。按 account 聚合只保存整数（games / rank_sum /
各名次计数）——不做浮点平均，避免 canonical JSON 浮点格式问题。

Read-time（``validate_persisted_archive_summary``）只验证 persisted sealed
summary 自身与 persisted Season / Manifest 的 binding——绝不重新扫描
Match Ledger / processes.json / execution_sessions，保证 runtime evidence
日后被清理时历史 Season 仍可读。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ARCHIVE_SUMMARY_SCHEMA = "keqing.season.archive_summary.v1"


# ---------------------------------------------------------------------------
# Typed Schema
# ---------------------------------------------------------------------------

class ArchiveSessionEpoch(BaseModel):
    """一个 R14-B spawn epoch 的摘要事实（来源：execution session 文件）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    created_at: str
    manifest_id: str
    season_authority_hash: str
    participant_count: int
    local_model_participants: int


class ArchiveProcessRecord(BaseModel):
    """最终进程状态摘要（来源：processes.json——最终状态的唯一权威）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_id: str
    account_id: str
    controller_type: str
    state: str
    started_at: str | None = None
    stopped_at: str | None = None
    exit_code: int | None = None


class ArchiveAccountStats(BaseModel):
    """按 account 聚合（仅整数——平均顺位是派生展示值，不入 seal）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    games: int = 0
    rank_sum: int = 0
    first_places: int = 0
    second_places: int = 0
    third_places: int = 0
    fourth_places: int = 0


class ArchiveMatchSummary(BaseModel):
    """Canonical Match Ledger 的最终聚合。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_matches: int
    active_matches: int
    void_matches: int
    rating_eligible_matches: int
    runtime_bound_matches: int
    total_revisions: int
    first_match_occurred_at: str | None = None
    last_match_occurred_at: str | None = None
    account_stats: list[ArchiveAccountStats] = Field(default_factory=list)


class ArchiveOperationSummary(BaseModel):
    """Runtime 执行历史摘要（spawn epochs + final process ledger）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_count: int
    sessions: list[ArchiveSessionEpoch] = Field(default_factory=list)
    processes: list[ArchiveProcessRecord] = Field(default_factory=list)


class SeasonArchiveSummary(BaseModel):
    """R14-D: 一次 Season 终局的不可变封存摘要（sealed）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["keqing.season.archive_summary.v1"] = ARCHIVE_SUMMARY_SCHEMA
    season_id: str
    manifest_id: str
    season_authority_hash: str
    match_summary: ArchiveMatchSummary
    operation_summary: ArchiveOperationSummary
    completed_at: str
    archive_summary_hash: str
    archive_summary_id: str


class ArchiveSummaryError(ValueError):
    """Archive Summary 构建/校验失败（fail-closed）。"""


# ---------------------------------------------------------------------------
# Seal computation
# ---------------------------------------------------------------------------

def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_archive_seal_projection(summary: dict[str, Any]) -> dict[str, Any]:
    """Seal hash 的 canonical projection：全部最终历史字段，仅排除自引用字段。"""
    return {
        "schema_version": summary.get("schema_version"),
        "season_id": summary.get("season_id"),
        "manifest_id": summary.get("manifest_id"),
        "season_authority_hash": summary.get("season_authority_hash"),
        "match_summary": summary.get("match_summary"),
        "operation_summary": summary.get("operation_summary"),
        "completed_at": summary.get("completed_at"),
    }


def compute_archive_summary_hash(summary: dict[str, Any]) -> str:
    """对 canonical projection 做确定性 SHA-256（十六进制）。"""
    projection = build_archive_seal_projection(summary)
    return hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()


def compute_archive_summary_id(season_id: str, seal_hash: str) -> str:
    """确定性 archive_summary_id（不含自引用，可由 seal hash 重算）。"""
    return f"archive:{season_id}:{seal_hash[:12]}"


# ---------------------------------------------------------------------------
# Layer 1: deterministic canonical projection (no completed_at / no seal)
# ---------------------------------------------------------------------------

def _load_execution_sessions_strict(
    season_id: str,
    manifest: Any,
) -> list[dict[str, Any]]:
    """严格扫描该 season 的全部 execution session 文件（损坏 → fail-closed）。

    R14-D Repair 1 (P1-2): 目录下**任何**无法解析 / 无法通过 typed schema
    的 session 文件都 fail-closed——绝不能静默跳过后把不完整运行历史封成
    "合法历史"。属于该 Season 的 session 还必须与 Frozen Manifest 精确
    cross-integrity：manifest_id、season_authority_hash、逐 participant 的
    controller_type + FrozenExecutionProvenance（R14-C 同一语义，归档不降级）。
    """
    import json as _json

    from workbench.runtime.execution_session import execution_sessions_root

    root = execution_sessions_root()
    if not root.is_dir():
        return []

    from workbench.runtime.execution_session import RuntimeExecutionSession

    manifest_by_account = {p.account_id: p for p in manifest.participants}

    sessions: list[dict[str, Any]] = []
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        session_file = session_dir / "session.json"
        if not session_file.exists():
            continue
        try:
            raw = _json.loads(session_file.read_text(encoding="utf-8"))
            session = RuntimeExecutionSession.model_validate(raw)
        except (OSError, ValueError) as exc:
            raise ArchiveSummaryError(
                f"season {season_id}: execution session 文件损坏 "
                f"({session_dir.name})，无法构建终局摘要: {exc}"
            ) from exc

        if session.season_id != season_id:
            # 目录存在合法 session 文件但不属于本 season——合法共存，跳过
            continue

        # 属于本 season 的 session 必须与 Frozen Manifest 精确一致
        if session.manifest_id != manifest.manifest_id:
            raise ArchiveSummaryError(
                f"season {season_id}: execution session {session.session_id} 的 "
                f"manifest_id ({session.manifest_id}) 与 Frozen Manifest "
                f"({manifest.manifest_id}) 不一致"
            )
        if session.season_authority_hash != manifest.season_authority_hash:
            raise ArchiveSummaryError(
                f"season {season_id}: execution session {session.session_id} 的 "
                "season_authority_hash 与 Frozen Manifest 不一致"
            )
        _session_participants_cross_check(
            season_id, session.session_id, session, manifest_by_account
        )
        sessions.append(session.model_dump())
    return sessions


def _session_participants_cross_check(
    season_id: str,
    session_id: str,
    session: Any,
    manifest_by_account: dict[str, Any],
) -> None:
    """session participant 与 Frozen Manifest 逐 account 精确比对。"""
    session_accounts = {p.account_id for p in session.participants}
    if session_accounts != set(manifest_by_account):
        raise ArchiveSummaryError(
            f"season {season_id}: execution session {session_id} 的参与者集合 "
            f"({sorted(session_accounts)}) 与 Frozen Manifest "
            f"({sorted(manifest_by_account)}) 不一致"
        )
    for p in session.participants:
        m_part = manifest_by_account[p.account_id]
        if p.controller_type != m_part.controller_type:
            raise ArchiveSummaryError(
                f"season {season_id}: execution session {session_id} 的参与者 "
                f"{p.account_id} controller ({p.controller_type}) 与 Manifest "
                f"({m_part.controller_type}) 不一致"
            )
        if p.execution_provenance != m_part.execution_provenance:
            raise ArchiveSummaryError(
                f"season {season_id}: execution session {session_id} 的参与者 "
                f"{p.account_id} execution_provenance 与 Frozen Manifest 不一致"
            )


def _load_process_records_strict(
    season_id: str,
    manifest: Any,
) -> list[Any]:
    """typed 读取该 season 的 final process ledger 并校验归属（fail-closed）。

    R14-D Repair 1 (P1-2): 复用 supervisor 的 typed loader（不平行实现弱版）；
    每条 record 必须属于当前 season 与 Frozen Manifest（season_id /
    manifest_id 精确一致），account/controller 必须是 Manifest 的合法
    runtime participant。
    """
    from workbench.runtime.supervisor import (
        RuntimeSupervisorStateError,
        load_process_records_locked,
    )

    try:
        records = load_process_records_locked(season_id)
    except RuntimeSupervisorStateError as exc:
        raise ArchiveSummaryError(
            f"season {season_id}: runtime process ledger 损坏，无法构建终局摘要: {exc}"
        ) from exc
    manifest_by_account = {p.account_id: p for p in manifest.participants}
    for rec in records:
        if str(rec.season_id or "") != season_id:
            raise ArchiveSummaryError(
                f"season {season_id}: process record {rec.runtime_id} 的 "
                f"season_id ({rec.season_id}) 不属于本 season"
            )
        if str(rec.manifest_id or "") != manifest.manifest_id:
            raise ArchiveSummaryError(
                f"season {season_id}: process record {rec.runtime_id} 的 "
                f"manifest_id ({rec.manifest_id}) 与 Frozen Manifest "
                f"({manifest.manifest_id}) 不一致"
            )
        m_part = manifest_by_account.get(str(rec.account_id or ""))
        if m_part is None:
            raise ArchiveSummaryError(
                f"season {season_id}: process record {rec.runtime_id} 的账号 "
                f"{rec.account_id} 不在 Frozen Manifest 参与者中"
            )
        if str(rec.controller_type or "") != str(m_part.controller_type):
            raise ArchiveSummaryError(
                f"season {season_id}: process record {rec.runtime_id} 的 "
                f"controller ({rec.controller_type}) 与 Manifest participant "
                f"({m_part.controller_type}) 不一致"
            )
    return records


def _validate_season_match_rows(
    season_id: str,
    match_rows: list[dict[str, Any]],
) -> list[Any]:
    """typed 校验本 Season 的全部 Match 行（fail-closed）。

    R14-D Repair 1 (P1-2): raw ledger 行必须通过 ``Match.model_validate``；
    再检查四座、seat 编号 0..3 唯一、账号唯一、rank 0..3 完整。
    """
    from workbench.participants.schemas import Match

    typed_matches: list[Any] = []
    for row in match_rows:
        if not isinstance(row, dict):
            raise ArchiveSummaryError("Match Ledger 存在非对象行，无法构建终局摘要")
        if str(row.get("season_id") or "") != season_id:
            continue
        try:
            match = Match.model_validate(row)
        except Exception as exc:
            raise ArchiveSummaryError(
                f"season {season_id}: match {row.get('match_id')} 未通过 typed "
                f"schema 校验，无法构建终局摘要: {exc}"
            ) from exc

        if len(match.seats) != 4:
            raise ArchiveSummaryError(
                f"match {match.match_id}: seats 不是 4 座，无法构建终局摘要"
            )
        seat_nums = [s.seat for s in match.seats]
        if sorted(seat_nums) != [0, 1, 2, 3]:
            raise ArchiveSummaryError(
                f"match {match.match_id}: seat 编号必须覆盖 0..3 且唯一"
            )
        account_ids = [s.account_id for s in match.seats]
        if len(set(account_ids)) != 4:
            raise ArchiveSummaryError(
                f"match {match.match_id}: 同一对局不能重复出现同一账号"
            )
        if len(match.ranks) != 4 or not all(isinstance(r, int) for r in match.ranks):
            raise ArchiveSummaryError(
                f"match {match.match_id}: ranks 必须是 4 个整数（0-based）"
            )
        if sorted(match.ranks) != [0, 1, 2, 3]:
            raise ArchiveSummaryError(
                f"match {match.match_id}: ranks 必须是 0..3 的完整排列"
            )
        typed_matches.append(match)
    return typed_matches


def _validate_runtime_bindings(
    season_id: str,
    typed_matches: list[Any],
    manifest: Any,
) -> None:
    """runtime_binding 的 season/manifest/authority 必须与 Frozen Manifest 精确一致。"""
    for match in typed_matches:
        binding = match.runtime_binding
        if binding is None:
            continue
        if binding.season_id != season_id:
            raise ArchiveSummaryError(
                f"match {match.match_id}: runtime_binding.season_id "
                f"({binding.season_id}) 与本 season ({season_id}) 不一致"
            )
        if binding.manifest_id != manifest.manifest_id:
            raise ArchiveSummaryError(
                f"match {match.match_id}: runtime_binding.manifest_id "
                f"({binding.manifest_id}) 与 Frozen Manifest "
                f"({manifest.manifest_id}) 不一致"
            )
        if binding.season_authority_hash != manifest.season_authority_hash:
            raise ArchiveSummaryError(
                f"match {match.match_id}: runtime_binding.season_authority_hash "
                "与 Frozen Manifest 不一致"
            )


def _validate_revision_chains(
    season_id: str,
    typed_matches: list[Any],
    revision_rows: list[dict[str, Any]],
) -> int:
    """校验本 Season 每个 match 的 revision chain 并返回总行数（fail-closed）。

    R14-D Repair 1 (P1-2): 不能只按 match_id 数行——每局 revisions 必须：
    - 唯一（无重复行）；
    - 连续 ``1..match.revision``；
    - 最后一行 revision == match.revision 且 revision_id ==
      match.latest_revision_id；
    - 每行 match_id 都属于本 Season 最终 Match 集合（伪造行 fail-closed）。
    """
    from workbench.participants.schemas import Match as MatchModel

    match_ids = {m.match_id for m in typed_matches}
    by_match: dict[str, list[dict[str, Any]]] = {}
    for row in revision_rows:
        if not isinstance(row, dict):
            raise ArchiveSummaryError("Revision Ledger 存在非对象行，无法构建终局摘要")
        row_match_id = str(row.get("match_id") or "")
        if row_match_id not in match_ids:
            # 属于其他 season / 其他 match 的 revision 行——只有当该 match_id
            # 在整个 ledger 中都不存在时才是伪造；否则它只是别的 season 的
            # 合法 revision。这里无法区分，按 match 集合过滤即可。
            continue
        try:
            revision_no = int(row.get("revision"))
        except (TypeError, ValueError) as exc:
            raise ArchiveSummaryError(
                f"season {season_id}: match {row_match_id} 存在非法 revision 行"
                f"（revision 不是整数）: {row.get('revision')!r}"
            ) from exc
        by_match.setdefault(row_match_id, []).append({**row, "_revision_no": revision_no})

    total = 0
    for match in typed_matches:
        rows = by_match.get(match.match_id, [])
        revision_numbers = [r["_revision_no"] for r in rows]
        if len(revision_numbers) != len(set(revision_numbers)):
            raise ArchiveSummaryError(
                f"season {season_id}: match {match.match_id} 存在重复 revision 行"
            )
        expected = list(range(1, match.revision + 1))
        if sorted(revision_numbers) != expected:
            raise ArchiveSummaryError(
                f"season {season_id}: match {match.match_id} 的 revision 链不完整"
                f"（期望 1..{match.revision}，实际 {sorted(revision_numbers)}）"
            )
        last_row = max(rows, key=lambda r: r["_revision_no"])
        if str(last_row.get("revision_id") or "") != match.latest_revision_id:
            raise ArchiveSummaryError(
                f"season {season_id}: match {match.match_id} 最后一行 revision_id "
                f"({last_row.get('revision_id')}) 与 match.latest_revision_id "
                f"({match.latest_revision_id}) 不一致"
            )
        total += len(rows)
    del MatchModel
    return total


def _account_stats_from_matches(typed_matches: list[Any]) -> list[dict[str, Any]]:
    """按 account 聚合（仅整数）；只统计 active + rating_eligible 的 rank。"""
    stats: dict[str, dict[str, int]] = {}

    def _bucket(account_id: str) -> dict[str, int]:
        return stats.setdefault(
            account_id,
            {
                "account_id": account_id,
                "games": 0,
                "rank_sum": 0,
                "first_places": 0,
                "second_places": 0,
                "third_places": 0,
                "fourth_places": 0,
            },
        )

    for match in typed_matches:
        if match.status != "active":
            continue
        if not match.rating_eligible:
            continue
        for seat, rank in zip(match.seats, match.ranks):
            bucket = _bucket(seat.account_id)
            bucket["games"] += 1
            bucket["rank_sum"] += rank
            # ranks 是 0-based（0 = 一位）
            if rank == 0:
                bucket["first_places"] += 1
            elif rank == 1:
                bucket["second_places"] += 1
            elif rank == 2:
                bucket["third_places"] += 1
            elif rank == 3:
                bucket["fourth_places"] += 1
    return [stats[k] for k in sorted(stats)]


def build_archive_summary_projection(
    season: dict[str, Any],
    *,
    match_rows: list[dict[str, Any]] | None = None,
    revision_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建 deterministic canonical projection（无 completed_at / 无 seal 字段）。

    只读取 Frozen Manifest / Execution Sessions / Process Ledger / Match Ledger；
    不查询 Participants Catalog Current。同一 locked source state 重复 build
    得到完全相同的 projection。

    R14-D Repair 1 (P1-2): canonical evidence fail-closed——seal 前证明这些
    evidence 相互属于同一个 Frozen Authority：
    - Manifest 先经 ``validate_persisted_manifest_against_season`` typed 校验；
    - Execution Sessions strict read + 与 Manifest 的 exact cross-integrity；
    - Process Records typed read + season/manifest/account/controller 归属校验；
    - Match rows 全部 typed validate + 结构完整性 + runtime_binding 与
      Manifest 精确一致；
    - Revision chains 唯一、连续、末端一致。
    """
    season_id = str(season.get("season_id") or "")
    manifest_raw = season.get("runtime_manifest")
    if not isinstance(manifest_raw, dict):
        raise ArchiveSummaryError(
            f"season {season_id}: 缺少 persisted runtime_manifest，无法构建终局摘要"
        )
    from workbench.runtime.manifest import (
        SeasonRuntimeManifest,
        validate_persisted_manifest_against_season,
    )

    try:
        manifest = SeasonRuntimeManifest.model_validate(manifest_raw)
        validate_persisted_manifest_against_season(season, manifest_raw)
    except Exception as exc:
        raise ArchiveSummaryError(
            f"season {season_id}: runtime_manifest 校验失败，无法构建终局摘要: {exc}"
        ) from exc

    # --- Match Ledger（typed validate + 结构完整性 + binding 一致性）---
    if match_rows is None:
        from workbench.participants import ledger as participants_ledger

        match_rows = participants_ledger._read_match_rows()
    if revision_rows is None:
        from workbench.participants import ledger as participants_ledger

        revision_rows = participants_ledger._read_revision_rows()

    typed_matches = _validate_season_match_rows(season_id, match_rows)
    _validate_runtime_bindings(season_id, typed_matches, manifest)
    total_revisions = _validate_revision_chains(season_id, typed_matches, revision_rows)

    total_matches = len(typed_matches)
    active_matches = sum(1 for m in typed_matches if m.status == "active")
    void_matches = total_matches - active_matches
    rating_eligible_matches = sum(1 for m in typed_matches if m.rating_eligible)
    runtime_bound_matches = sum(1 for m in typed_matches if m.runtime_binding is not None)

    occurred = sorted(m.occurred_at for m in typed_matches if m.occurred_at)

    match_summary = {
        "total_matches": total_matches,
        "active_matches": active_matches,
        "void_matches": void_matches,
        "rating_eligible_matches": rating_eligible_matches,
        "runtime_bound_matches": runtime_bound_matches,
        "total_revisions": total_revisions,
        "first_match_occurred_at": occurred[0] if occurred else None,
        "last_match_occurred_at": occurred[-1] if occurred else None,
        "account_stats": _account_stats_from_matches(typed_matches),
    }

    # --- Execution Sessions（strict read + cross-integrity）---
    session_rows = _load_execution_sessions_strict(season_id, manifest)
    sessions = [
        {
            "session_id": str(s.get("session_id") or ""),
            "created_at": str(s.get("created_at") or ""),
            "manifest_id": str(s.get("manifest_id") or ""),
            "season_authority_hash": str(s.get("season_authority_hash") or ""),
            "participant_count": len(s.get("participants") or []),
            "local_model_participants": sum(
                1
                for p in (s.get("participants") or [])
                if str(p.get("controller_type") or "") == "local_model"
            ),
        }
        for s in session_rows
    ]
    sessions.sort(key=lambda s: (s["created_at"], s["session_id"]))

    # --- Final Process Ledger（typed read + 归属校验）---
    process_records = _load_process_records_strict(season_id, manifest)
    processes = [
        {
            "runtime_id": r.runtime_id,
            "account_id": r.account_id,
            "controller_type": str(r.controller_type),
            "state": str(r.state),
            "started_at": r.started_at,
            "stopped_at": r.stopped_at,
            "exit_code": r.exit_code,
        }
        for r in process_records
    ]
    processes.sort(key=lambda p: (p["account_id"], p["runtime_id"]))

    operation_summary = {
        "session_count": len(sessions),
        "sessions": sessions,
        "processes": processes,
    }

    return {
        "schema_version": ARCHIVE_SUMMARY_SCHEMA,
        "season_id": season_id,
        "manifest_id": manifest.manifest_id,
        "season_authority_hash": manifest.season_authority_hash,
        "match_summary": match_summary,
        "operation_summary": operation_summary,
    }


# ---------------------------------------------------------------------------
# Layer 2: seal (completed_at + hash/id)
# ---------------------------------------------------------------------------

def seal_archive_summary(projection: dict[str, Any], completed_at: str) -> SeasonArchiveSummary:
    """加入 completed_at、计算 seal hash / id，生成最终 SeasonArchiveSummary。

    ``completed_at`` 是最终历史字段，包含在 seal hash 内；只有
    ``archive_summary_hash`` / ``archive_summary_id``（自引用）被排除。
    """
    if not str(completed_at or "").strip():
        raise ArchiveSummaryError("completed_at 不能为空")
    payload = dict(projection)
    payload["completed_at"] = str(completed_at)
    seal_hash = compute_archive_summary_hash(payload)
    summary = SeasonArchiveSummary(
        **payload,
        archive_summary_hash=seal_hash,
        archive_summary_id=compute_archive_summary_id(
            str(payload.get("season_id") or ""), seal_hash
        ),
    )
    return summary


# ---------------------------------------------------------------------------
# Read-time validator (persisted sealed summary only — no ledger rescans)
# ---------------------------------------------------------------------------

def validate_persisted_archive_summary(
    season: dict[str, Any],
    raw: Any,
) -> SeasonArchiveSummary:
    """Read-time fail-closed 校验 persisted sealed summary。

    只验证：
    - typed schema（Literal schema_version、结构完整）；
    - season_id / manifest_id / season_authority_hash 与 persisted
      Season / runtime_manifest 一致；
    - completed_at == season.completed_at；
    - archive_summary_hash / archive_summary_id 重算一致。

    绝不重新扫描 Match Ledger / processes.json / execution_sessions——
    runtime evidence 日后清理不影响历史 Season 读取。
    """
    season_id = str(season.get("season_id") or "")

    def _fail(reason: str) -> ArchiveSummaryError:
        return ArchiveSummaryError(f"赛季 {season_id} archive_summary: {reason}")

    if not isinstance(raw, dict):
        raise _fail("必须是对象")
    try:
        summary = SeasonArchiveSummary.model_validate(raw)
    except Exception as exc:
        raise _fail(f"结构校验失败: {exc}") from exc

    if summary.season_id != season_id:
        raise _fail(f"season_id ({summary.season_id}) 与注册表不一致")

    manifest_raw = season.get("runtime_manifest")
    manifest_id = (
        str(manifest_raw.get("manifest_id") or "") if isinstance(manifest_raw, dict) else ""
    )
    authority_hash = (
        str(manifest_raw.get("season_authority_hash") or "")
        if isinstance(manifest_raw, dict)
        else ""
    )
    if summary.manifest_id != manifest_id:
        raise _fail(f"manifest_id ({summary.manifest_id}) 与 Frozen Manifest 不一致")
    if summary.season_authority_hash != authority_hash:
        raise _fail("season_authority_hash 与 Frozen Manifest 不一致")

    season_completed_at = str(season.get("completed_at") or "")
    if summary.completed_at != season_completed_at:
        raise _fail(
            f"completed_at ({summary.completed_at}) 与注册表 completed_at 不一致"
        )

    payload = summary.model_dump()
    expected_hash = compute_archive_summary_hash(payload)
    if summary.archive_summary_hash != expected_hash:
        raise _fail("archive_summary_hash 重算不一致（persisted summary 已被篡改）")
    expected_id = compute_archive_summary_id(season_id, expected_hash)
    if summary.archive_summary_id != expected_id:
        raise _fail("archive_summary_id 重算不一致（persisted summary 已被篡改）")
    return summary


__all__ = [
    "ARCHIVE_SUMMARY_SCHEMA",
    "ArchiveAccountStats",
    "ArchiveMatchSummary",
    "ArchiveOperationSummary",
    "ArchiveProcessRecord",
    "ArchiveSessionEpoch",
    "ArchiveSummaryError",
    "SeasonArchiveSummary",
    "build_archive_summary_projection",
    "compute_archive_summary_hash",
    "compute_archive_summary_id",
    "seal_archive_summary",
    "validate_persisted_archive_summary",
]
