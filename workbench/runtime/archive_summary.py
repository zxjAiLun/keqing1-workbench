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

def _load_execution_sessions(season_id: str) -> list[dict[str, Any]]:
    """扫描该 season 的全部 execution session 文件；损坏 → fail-closed。"""
    from workbench.runtime.execution_session import (
        execution_sessions_root,
        load_execution_session,
    )

    root = execution_sessions_root()
    if not root.is_dir():
        return []
    sessions: list[dict[str, Any]] = []
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        session = load_execution_session(session_dir.name)
        if session is None:
            continue  # 无效 session 文件（非本 season / 损坏 schema）不属于本 season 的证据
        if session.season_id != season_id:
            continue
        sessions.append(session.model_dump())
    return sessions


def _load_process_records(season_id: str) -> list[dict[str, Any]]:
    """读取该 season 的 final process ledger；损坏 → fail-closed。"""
    from workbench.runtime.supervisor import runtime_records_file

    path = runtime_records_file(season_id)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveSummaryError(
            f"season {season_id}: runtime process ledger 损坏，无法构建终局摘要: {exc}"
        ) from exc
    if not isinstance(raw, list):
        raise ArchiveSummaryError(
            f"season {season_id}: runtime process ledger 结构损坏（应为列表）"
        )
    return raw


def _account_stats_from_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    for match in matches:
        if str(match.get("status") or "active") != "active":
            continue
        if not match.get("rating_eligible"):
            continue
        ranks = match.get("ranks") or []
        seats = match.get("seats") or []
        if len(ranks) != 4 or len(seats) != 4:
            raise ArchiveSummaryError(
                f"match {match.get('match_id')}: ranks/seats 不是 4 座，无法聚合"
            )
        for seat, rank in zip(seats, ranks):
            account_id = str(seat.get("account_id") or "")
            if not account_id:
                raise ArchiveSummaryError(
                    f"match {match.get('match_id')}: 存在无 account 的座位，无法聚合"
                )
            if not isinstance(rank, int):
                raise ArchiveSummaryError(
                    f"match {match.get('match_id')}: rank 不是整数: {rank!r}"
                )
            bucket = _bucket(account_id)
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


def _revision_total_for_matches(
    match_ids: set[str], revision_rows: list[dict[str, Any]]
) -> int:
    """当前 Season 最终 Match 集合中，各 match_id 对应的 Revision 行数总和。"""
    return sum(1 for row in revision_rows if str(row.get("match_id") or "") in match_ids)


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
    """
    season_id = str(season.get("season_id") or "")
    manifest_raw = season.get("runtime_manifest")
    if not isinstance(manifest_raw, dict):
        raise ArchiveSummaryError(
            f"season {season_id}: 缺少 persisted runtime_manifest，无法构建终局摘要"
        )
    manifest_id = str(manifest_raw.get("manifest_id") or "")
    authority_hash = str(manifest_raw.get("season_authority_hash") or "")
    if not manifest_id or not authority_hash:
        raise ArchiveSummaryError(
            f"season {season_id}: runtime_manifest 缺少 manifest_id / season_authority_hash"
        )

    # --- Match Ledger（canonical 当前态行；损坏行 fail-closed）---
    if match_rows is None:
        from workbench.participants import ledger as participants_ledger

        match_rows = participants_ledger._read_match_rows()
    if revision_rows is None:
        from workbench.participants import ledger as participants_ledger

        revision_rows = participants_ledger._read_revision_rows()

    season_matches: list[dict[str, Any]] = []
    for row in match_rows:
        if not isinstance(row, dict):
            raise ArchiveSummaryError("Match Ledger 存在非对象行，无法构建终局摘要")
        if str(row.get("season_id") or "") == season_id:
            season_matches.append(row)

    total_matches = len(season_matches)
    active_matches = sum(
        1 for m in season_matches if str(m.get("status") or "active") == "active"
    )
    void_matches = total_matches - active_matches
    rating_eligible_matches = sum(1 for m in season_matches if m.get("rating_eligible"))
    runtime_bound_matches = sum(
        1 for m in season_matches if m.get("runtime_binding") is not None
    )
    match_ids = {str(m.get("match_id") or "") for m in season_matches}
    total_revisions = _revision_total_for_matches(match_ids, revision_rows)

    occurred = sorted(
        str(m.get("occurred_at") or "") for m in season_matches if m.get("occurred_at")
    )

    match_summary = {
        "total_matches": total_matches,
        "active_matches": active_matches,
        "void_matches": void_matches,
        "rating_eligible_matches": rating_eligible_matches,
        "runtime_bound_matches": runtime_bound_matches,
        "total_revisions": total_revisions,
        "first_match_occurred_at": occurred[0] if occurred else None,
        "last_match_occurred_at": occurred[-1] if occurred else None,
        "account_stats": _account_stats_from_matches(season_matches),
    }

    # --- Execution Sessions（spawn epochs）---
    session_rows = _load_execution_sessions(season_id)
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

    # --- Final Process Ledger（最终进程状态的唯一权威）---
    process_rows = _load_process_records(season_id)
    processes = [
        {
            "runtime_id": str(r.get("runtime_id") or ""),
            "account_id": str(r.get("account_id") or ""),
            "controller_type": str(r.get("controller_type") or ""),
            "state": str(r.get("state") or ""),
            "started_at": r.get("started_at"),
            "stopped_at": r.get("stopped_at"),
            "exit_code": r.get("exit_code"),
        }
        for r in process_rows
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
        "manifest_id": manifest_id,
        "season_authority_hash": authority_hash,
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
