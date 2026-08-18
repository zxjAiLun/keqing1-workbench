"""Ladder multi-source ingest: unified match events -> deterministic replay.

R9: a single official season (``official-ladder-v1``) consumes matches from any
source — native self-play league logs, play-with-you captures, manual tenhou
pastes — through source adapters that emit a uniform ``LadderMatch``.  The merge
pipeline validates, deduplicates and orders the stream; the replay re-runs the
Tenhou rank progression from 新人/R1500 over the whole stream and emits a v2
ladder report (the same snapshot contract the publisher consumes).

Source directories are engineering-level input isolation only; they all feed the
same season, the same accounts, the same scoring book.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence

from project_data import resolve_ladder_path
from replay.rank_systems import create_rank_system

_REPO_ROOT = Path(__file__).resolve().parents[2]

GAME_LENGTHS = ("hanchan", "tonpuu")


def resolve_ingest_sources_root(project_root: Path, season: Mapping[str, Any]) -> Path:
    """解析赛季 ingest.sources_root（API 与 publisher 共用同一套路径语义）。

    - 绝对路径原样使用；
    - 相对路径：设置 ``KEQING_LADDER_DATA_ROOT`` 时相对数据根，否则相对仓库根。
    """
    ingest = season.get("ingest") if isinstance(season.get("ingest"), dict) else {}
    raw = ingest.get("sources_root")
    if not raw:
        raise LadderIngestError("season 缺少 ingest.sources_root")
    del project_root
    return resolve_ladder_path(str(raw))


class LadderIngestError(ValueError):
    """Ingest validation / merge / replay failure (conflict, bad identity, ...)."""


def _resolve_mortal_root(mortal_root: str | Path) -> Path:
    """R12-A：相对 mortal_root 一律按仓库根解析（publisher 的 CWD 无关性）。"""
    path = Path(mortal_root)
    if path.is_absolute():
        return path
    return (_REPO_ROOT / path).resolve()


# ---------------------------------------------------------------------------
# Event contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LadderMatchPlayer:
    """One player in a finished ladder match, identified by official account_id."""

    account_id: str
    seat: int
    final_score: int


@dataclass(frozen=True)
class LadderMatch:
    """One finished 4-player ladder match.

    ``match_id`` is immutable and globally unique across all sources; identity
    mapping (raw name -> account_id) is resolved inside the source adapter, never
    by the replay.
    """

    match_id: str
    occurred_at: datetime
    game_length: str
    players: tuple[LadderMatchPlayer, ...]
    source_type: str
    source_ref: str | None = None
    # R12-A：可选的详细统计来源引用。只有 data_completeness == full_replay
    # 的来源才允许携带；手级数据保持在 replay artifact，投影阶段按需读取。
    replay_artifact_dir: Path | None = None


class SourceAdapter(Protocol):
    """Converts one source directory into a stream of ``LadderMatch``."""

    source_type: str

    def iter_matches(self, source_dir: Path) -> Iterator[LadderMatch]: ...


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LadderIngestError(f"occurred_at 无法解析: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_match(match: LadderMatch, allowed_accounts: set[str]) -> None:
    if match.game_length not in GAME_LENGTHS:
        raise LadderIngestError(f"match {match.match_id}: 未知 game_length {match.game_length!r}")
    players = match.players
    if len(players) != 4:
        raise LadderIngestError(f"match {match.match_id}: 需要恰好四名玩家，得到 {len(players)}")
    seats = [player.seat for player in players]
    if sorted(seats) != [0, 1, 2, 3]:
        raise LadderIngestError(f"match {match.match_id}: seat 必须恰为 0..3，得到 {seats}")
    account_ids = [player.account_id for player in players]
    if len(set(account_ids)) != 4:
        raise LadderIngestError(f"match {match.match_id}: account_id 重复")
    missing = sorted(set(account_ids) - allowed_accounts)
    if missing:
        raise LadderIngestError(
            f"match {match.match_id}: 账号未在正式赛季注册: {missing}"
        )


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

class NativeLogAdapter:
    """Reads offline native league mjai logs (``*.json.gz``).

    Raw player names are mapped to official account_ids via ``name_to_account``;
    names not present in the mapping are rejected loudly.

    ``occurred_at`` comes from a stable sidecar index, never from file mtime
    (copying / restoring / migrating a data disk changes mtime and would reorder
    the full replay).  The sidecar is ``source_dir/index.jsonl``, one record per
    line: ``{"match_id": "...", "occurred_at": "...", "path": "..."}``.  A log
    file absent from the index is rejected as a formal ingest source.
    """

    source_type = "native"
    INDEX_FILENAME = "index.jsonl"

    def __init__(
        self,
        name_to_account: Mapping[str, str],
        occurred_at_index: Mapping[str, str] | None = None,
    ):
        self.name_to_account = dict(name_to_account)
        self._occurred_at_index = dict(occurred_at_index) if occurred_at_index is not None else None

    def _load_index(self, source_dir: Path) -> dict[str, datetime]:
        if self._occurred_at_index is not None:
            return {
                match_id: _parse_iso(value)
                for match_id, value in self._occurred_at_index.items()
            }
        index_path = source_dir / self.INDEX_FILENAME
        if not index_path.is_file():
            raise LadderIngestError(
                f"native source {source_dir}: 缺少稳定时间索引 {self.INDEX_FILENAME}，"
                "拒绝作为正式 ingest source（不采用文件 mtime）"
            )
        result: dict[str, datetime] = {}
        with index_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LadderIngestError(
                        f"{index_path.name}:{line_number} JSON 无法解析: {exc}"
                    ) from exc
                match_id = raw.get("match_id")
                occurred = raw.get("occurred_at")
                if not isinstance(match_id, str) or not isinstance(occurred, str):
                    raise LadderIngestError(
                        f"{index_path.name}:{line_number} 需要 match_id + occurred_at"
                    )
                result[match_id] = _parse_iso(occurred)
        return result

    def iter_matches(self, source_dir: Path) -> Iterator[LadderMatch]:
        from replay.build_platform_account_report import (
            initial_and_final_scores_from_events,
            iter_log_files,
            read_events,
        )

        index = self._load_index(source_dir)
        for path in iter_log_files([source_dir]):
            match_id = path.name.removesuffix(".json.gz")
            try:
                occurred_at = index[match_id]
            except KeyError as exc:
                raise LadderIngestError(
                    f"native log {path.name}: 未在稳定时间索引中（match_id={match_id!r}），拒绝"
                ) from exc
            events = read_events(path)
            raw_names = [str(name) for name in events[0]["names"]]
            try:
                account_ids = [self.name_to_account[name] for name in raw_names]
            except KeyError as exc:
                raise LadderIngestError(
                    f"native log {path.name}: 玩家名 {exc.args[0]!r} 未在 name_to_account 映射中"
                ) from exc
            _initial, scores = initial_and_final_scores_from_events(events)
            players = tuple(
                LadderMatchPlayer(
                    account_id=account_ids[seat],
                    seat=seat,
                    final_score=int(scores[seat]),
                )
                for seat in range(4)
            )
            yield LadderMatch(
                match_id=match_id,
                occurred_at=occurred_at,
                game_length="hanchan",
                players=players,
                source_type=self.source_type,
                source_ref=str(path.resolve()),
            )


def parse_match_record(
    raw: Mapping[str, Any],
    *,
    source_type: str,
    source_ref: str | None = None,
) -> LadderMatch:
    """公共 JSON record parser：capture API 与 JSONL adapter 共用同一套校验。

    ``raw`` 形如：
    ``{"match_id": "...", "occurred_at": "...", "game_length": "hanchan",
        "players": [{"account_id": "...", "seat": 0, "final_score": 42100}, ...]}``
    """
    match_id = raw.get("match_id")
    if not isinstance(match_id, str) or not match_id.strip():
        raise LadderIngestError(f"{source_ref or source_type} match_id 必须是非空字符串")
    occurred_raw = raw.get("occurred_at")
    if not isinstance(occurred_raw, str):
        raise LadderIngestError(f"{source_ref or source_type} 缺少 occurred_at")
    game_length = str(raw.get("game_length") or "hanchan")
    raw_players = raw.get("players")
    if not isinstance(raw_players, list) or len(raw_players) != 4:
        raise LadderIngestError(f"{source_ref or source_type} players 必须恰好四项")
    players: list[LadderMatchPlayer] = []
    for index, entry in enumerate(raw_players):
        if not isinstance(entry, dict):
            raise LadderIngestError(f"{source_ref or source_type} players[{index}] 必须是对象")
        account_id = str(entry.get("account_id") or "").strip()
        seat = int(entry.get("seat") or 0)
        final_score = int(entry.get("final_score") or 0)
        players.append(
            LadderMatchPlayer(account_id=account_id, seat=seat, final_score=final_score)
        )
    return LadderMatch(
        match_id=match_id,
        occurred_at=_parse_iso(occurred_raw),
        game_length=game_length,
        players=tuple(players),
        source_type=source_type,
        source_ref=source_ref,
    )


class _JsonlMatchAdapter:
    """Shared JSONL parsing for capture/manual sources.

    Each line:
    ``{"match_id": "...", "occurred_at": "...", "game_length": "hanchan",
        "players": [{"account_id": "...", "seat": 0, "final_score": 42100}, ...]}``
    """

    source_type = "jsonl"

    def iter_matches(self, source_dir: Path) -> Iterator[LadderMatch]:
        for path in sorted(source_dir.glob("*.jsonl")):
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise LadderIngestError(
                            f"{path.name}:{line_number} JSON 无法解析: {exc}"
                        ) from exc
                    yield parse_match_record(
                        raw,
                        source_type=self.source_type,
                        source_ref=f"{path.name}:{line_number}",
                    )


class PlayWithYouResultAdapter(_JsonlMatchAdapter):
    """Reads confirmed play-with-you capture results (JSONL)."""

    source_type = "playwithyou"


class ManualTenhouAdapter(_JsonlMatchAdapter):
    """Reads manually pasted tenhou results (JSONL)."""

    source_type = "manual_tenhou"


class ParticipantLedgerAdapter:
    """R10-F：从 participants Match Ledger 直接读取正式计分比赛。

    只有满足以下条件才进入天梯：
    - ``status == active``（未作废）；
    - ``rating_eligible == True``；
    - ``season_id`` 与目标赛季一致；
    - 恰好 4 个座位（身份全部解决）。

    ``source_dir`` 为 participants 数据根目录（含 matches.jsonl）。
    """

    source_type = "participants"

    def __init__(self, season_id: str):
        self.season_id = season_id

    def iter_matches(self, source_dir: Path) -> Iterator[LadderMatch]:
        from participants import ledger as participants_ledger

        response = participants_ledger.list_matches(status="active")
        for match in response.matches:
            if match.season_id != self.season_id:
                continue
            if not match.rating_eligible:
                continue
            if len(match.seats) != 4:
                continue
            yield LadderMatch(
                match_id=match.match_id,
                occurred_at=_parse_iso(match.occurred_at),
                game_length=_ladder_game_length(match.game_length),
                players=tuple(
                    LadderMatchPlayer(
                        account_id=seat.account_id,
                        seat=seat.seat,
                        final_score=match.final_scores[seat.seat],
                    )
                    for seat in match.seats
                ),
                source_type=self.source_type,
                source_ref=match.external_match_id,
                # R12-A：full_replay + replay_id 的 Match 把 replay artifact
                # 作为详细统计来源关联进来（Ledger 本身仍不含手级数据）。
                replay_artifact_dir=replay_artifact_dir_for_match(match),
            )


def _ladder_game_length(game_length: str) -> str:
    """participants 的 tonpu → ladder 的 tonpuu（跨系统术语归一）。"""
    return "tonpuu" if game_length == "tonpu" else game_length


def replay_artifact_dir_for_match(match: Any) -> Path | None:
    """R12-A：解析 participants Match 的详细统计来源目录。

    仅 ``data_completeness == full_replay`` 且带 ``replay_id`` 的 Match 参与；
    artifact 目录按 participants intake 的统一路径解析（replays/<log_id>）。
    """
    if getattr(match, "data_completeness", None) != "full_replay":
        return None
    replay_id = getattr(match, "replay_id", None)
    if not replay_id:
        return None
    from participants import intake as participants_intake

    return participants_intake.artifact_dir(str(replay_id))


def participants_projection_fingerprint(season_id: str) -> str:
    """participants ledger 的语义投影指纹（P1-1）。

    只有影响天梯的字段参与：season_id + active + rating_eligible + match_id +
    occurred_at + game_length + 四座 account_id + final_scores +（R12-A）
    full_replay 比赛的 replay artifact 文件证据（路径/大小/mtime_ns）。
    按稳定顺序 canonical JSON 后 SHA-256。revision note / projection state
    等不影响。
    """
    from participants import ledger as participants_ledger

    rows: list[dict] = []
    for match in participants_ledger.list_matches(status="active").matches:
        if match.season_id != season_id or not match.rating_eligible:
            continue
        replay_evidence: dict[str, Any] | None = None
        artifact_dir = replay_artifact_dir_for_match(match)
        if artifact_dir is not None:
            # R12-A：详细统计来源于 replay artifact——artifact 被替换/修复/
            # 删除都会改变天梯快照内容，必须进入投影指纹。
            replay_evidence = {}
            for name in ("tenhou6.json", "events.jsonl", "hands.jsonl", "summary.json"):
                path = artifact_dir / name
                try:
                    st = path.stat()
                except OSError:
                    replay_evidence[name] = None
                else:
                    replay_evidence[name] = [st.st_size, st.st_mtime_ns]
        rows.append(
            {
                "match_id": match.match_id,
                "occurred_at": match.occurred_at,
                "game_length": match.game_length,
                "seats": [
                    (seat.seat, seat.account_id, seat.model_identity_id, seat.model_artifact_id)
                    for seat in sorted(match.seats, key=lambda s: s.seat)
                ],
                "final_scores": match.final_scores,
                "replay_artifact": replay_evidence,
            }
        )
    rows.sort(key=lambda row: row["match_id"])
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Deterministic merge
# ---------------------------------------------------------------------------

def canonical_players(match: LadderMatch) -> tuple[LadderMatchPlayer, ...]:
    """按 seat 排序的规范玩家序列（players 数组顺序不代表 seat 顺序）。

    所有计分、去重、replay 一律只消费 canonical 序列。
    """
    return tuple(sorted(match.players, key=lambda player: player.seat))


def _placements(canonical: Sequence[LadderMatchPlayer]) -> list[int]:
    """顺位：按 final_score 降序，同分按 seat 升序（与 report builder 一致）。

    ``canonical`` 已按 seat 排序，index == seat。
    """
    ordered = sorted(range(4), key=lambda seat: (-canonical[seat].final_score, seat))
    placements = [0, 0, 0, 0]
    for placement, seat in enumerate(ordered, 1):
        placements[seat] = placement
    return placements


def merge_ladder_matches(
    source_entries: Sequence[tuple[SourceAdapter, Path]],
    *,
    allowed_accounts: set[str],
) -> list[LadderMatch]:
    """读取所有 source -> 校验 -> 按 match_id 去重 -> 稳定排序。

    - 同 match_id、相同玩家/座位/分数/规则：no-op（保留最早 occurred_at，
      其他来源视为重复证据；occurred_at 差异不构成冲突）；
    - 同 match_id 但玩家/分数不同：冲突，整体拒绝发布（LadderIngestError）。
    """
    collected: list[LadderMatch] = []
    for adapter, source_dir in source_entries:
        if not source_dir.is_dir():
            continue
        for match in adapter.iter_matches(source_dir):
            validate_match(match, allowed_accounts)
            collected.append(match)

    by_id: dict[str, LadderMatch] = {}
    for match in sorted(collected, key=lambda item: (item.occurred_at, item.match_id)):
        previous = by_id.get(match.match_id)
        if previous is None:
            by_id[match.match_id] = match
        elif _content_key(previous) == _content_key(match):
            if match.occurred_at < previous.occurred_at:
                by_id[match.match_id] = match  # 保留最早记录时间
            elif (
                match.occurred_at == previous.occurred_at
                and previous.replay_artifact_dir is None
                and match.replay_artifact_dir is not None
            ):
                # R12-A：同 match_id 同内容同时间时，优先保留携带 replay artifact
                # 引用的证据（否则详细统计来源会在去重时被无统计来源的记录顶掉）。
                by_id[match.match_id] = match
        else:
            raise LadderIngestError(
                f"match_id 冲突: {match.match_id}（两个来源记录了不同内容，拒绝发布）"
            )
    return sorted(by_id.values(), key=lambda item: (item.occurred_at, item.match_id))


def _content_key(match: LadderMatch):
    """去重比较的实质内容：不含 occurred_at / source_type / source_ref。"""
    return (match.match_id, match.game_length, canonical_players(match))


# ---------------------------------------------------------------------------
# R12-A: Replay stats projection（详细统计投影）
# ---------------------------------------------------------------------------

STATS_LOG_SUBDIR = "account_logs"


def _safe_log_stem(value: str) -> str:
    """match_id → 文件名安全片段（确定性，不含路径分隔符）。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def read_replay_stats_events(artifact_dir: Path) -> list[dict[str, Any]] | None:
    """读取 replay artifact 的原始 mjai 事件流（events.jsonl 优先，
    tenhou6.json 重建回退）。artifact 缺失/不可读返回 None——调用方通过
    覆盖率字段如实披露，而不是把未知当作 0。

    artifact 目录由 participants intake 以 log_id 命名，因此目录名即 replay_id。
    """
    from participants import intake as participants_intake

    return participants_intake.read_replay_events(artifact_dir.name)


def normalize_stats_events(
    events: Sequence[Mapping[str, Any]], account_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """把 replay artifact 事件流改写成 account-normalized 统计日志事件。

    - ``start_game.names`` 按 seat 改写为正式 account_id（libriichi Stat 以
      player_name 匹配日志内名字，这里直接以 account_id 作为日志内名字）；
    - ``reach_accepted`` 由 canonical converter 状态机补齐（R12-A Repair）：
      旧 artifact 的事件流没有该事件 → 按 upstream convlog 状态机做 legacy
      synthesis（宣言牌之后牌局继续才成立；被荣和/局结束则不成立）；新
      artifact 已自带 → 幂等跳过，绝不重复注入；
    - ``start_kyoku`` 装饰字段防御性补齐：``dora_marker`` 缺失 → "?"，
      ``tehais`` 不足/超长 → 以 "?" 补齐到 13 张（统计不消费手牌内容，
      只为让事件流可解析）。
    """
    from convert.tenhou6_utils import insert_reach_accepted

    normalized: list[dict[str, Any]] = []
    for event in events:
        row = dict(event)
        event_type = row.get("type")
        if event_type == "start_game":
            row["names"] = [str(account_ids[seat]) for seat in range(4)]
        elif event_type == "start_kyoku":
            if row.get("dora_marker") is None:
                row["dora_marker"] = "?"
            tehais = row.get("tehais")
            if isinstance(tehais, list):
                padded: list[list[str]] = []
                for hand in tehais:
                    if isinstance(hand, list):
                        padded.append(([str(tile) for tile in hand] + ["?"] * 13)[:13])
                    else:
                        padded.append(["?"] * 13)
                row["tehais"] = padded
        normalized.append(row)
    return insert_reach_accepted(normalized)


def write_replay_stats_logs(
    matches: Sequence[LadderMatch],
    stats_log_dir: Path,
) -> list[str]:
    """为携带 replay artifact 引用的 Match 生成 account-normalized 统计日志。

    每局写出 ``<game_index:04d>_<match_id>.json.gz``（game_index 与全量重放的
    局序一致）。artifact 缺失/事件不可读时跳过该局，覆盖率字段如实披露。
    返回实际写出的 match_id 列表。
    """
    from replay.build_platform_account_report import write_account_log

    stats_log_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for game_index, match in enumerate(matches):
        artifact_dir = match.replay_artifact_dir
        if artifact_dir is None:
            continue
        events = read_replay_stats_events(artifact_dir)
        if not events:
            continue
        account_ids = [""] * 4
        for player in match.players:
            account_ids[player.seat] = player.account_id
        if any(not account_id for account_id in account_ids):
            continue
        normalized = normalize_stats_events(events, account_ids)
        write_account_log(
            normalized,
            account_ids,
            stats_log_dir / f"{game_index:04d}_{_safe_log_stem(match.match_id)}.json.gz",
        )
        written.append(match.match_id)
    return written


# ---------------------------------------------------------------------------
# Full replay (newcomer / R1500)
# ---------------------------------------------------------------------------

def replay_ladder_matches(
    matches: Sequence[LadderMatch],
    *,
    scoring_config: Mapping[str, Any] | None,
    stats_log_dir: Path | None = None,
    mortal_root: str | Path = Path("third_party/Mortal"),
) -> dict[str, Any]:
    """从新人/R1500 全量重放，产出 v2 报告核心产物（summary/ledger/curve）。

    与 report builder 的赛前快照规则一致：每局先复制四名账号的不可变赛前
    状态，构造一次共享 MatchContext，再逐 seat 独立 apply_result，全部算完
    后才写回账号状态。

    R12-A：``stats_log_dir`` 提供时，携带 replay artifact 引用的 Match 会
    生成 account-normalized 统计日志并复用现有 build_stat_report（libriichi
    Stat）把详细指标合并进 account summary；无 full replay 的账号保持 None
    并由 stats_games / stats_total_games / stats_coverage 披露覆盖率。
    """
    rank_system = create_rank_system(dict(scoring_config) if scoring_config else None)
    initial = rank_system.initial_state()

    # account_id -> mutable replay state
    states: dict[str, dict[str, Any]] = {}
    games_by_account: dict[str, int] = {}
    rank_counts: dict[str, list[int]] = {}
    promotions: dict[str, int] = {}
    demotions: dict[str, int] = {}
    highest: dict[str, str] = {}
    tenhou_reached: dict[str, bool] = {}
    total_pt_delta: dict[str, int] = {}

    def ensure(account_id: str) -> None:
        if account_id in states:
            return
        states[account_id] = {
            "rank_id": initial.rank_id,
            "pt": initial.pt,
            "rating": initial.rating,
            "games": 0,
        }
        games_by_account[account_id] = 0
        rank_counts[account_id] = [0, 0, 0, 0]
        promotions[account_id] = 0
        demotions[account_id] = 0
        highest[account_id] = initial.rank_id
        tenhou_reached[account_id] = initial.rank_id == "tenhou"
        total_pt_delta[account_id] = 0

    ledger_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []

    # R12-A：先为携带 replay artifact 引用的 Match 生成 account-normalized
    # 统计日志（与重放同一局序），随后统一走 build_stat_report 聚合。
    stats_eligible_match_ids: list[str] = []
    if stats_log_dir is not None:
        stats_eligible_match_ids = write_replay_stats_logs(matches, stats_log_dir)

    for game_index, match in enumerate(matches):
        for player in match.players:
            ensure(player.account_id)

        # 规范玩家序列（按 seat 排序，index == seat）；players 数组顺序不代表座位。
        canonical = canonical_players(match)

        # 赛前快照（不可变）
        pre_states = [
            _player_state(rank_system, states[player.account_id]) for player in canonical
        ]
        # R10 merge repair：每局 game_length 决定该局 PT 表（tonpuu 20/10/0 vs hanchan 30/15/0）
        ctx = rank_system.match_context(pre_states, game_length=match.game_length)

        placements = _placements(canonical)
        updates: list[dict[str, Any]] = []
        for seat, player in enumerate(canonical):
            update = rank_system.apply_result(
                pre_states[seat], placement=placements[seat], match=ctx
            )
            updates.append({"player": player, "placement": placements[seat], "update": update})

        for entry in updates:
            player = entry["player"]
            update = entry["update"]
            account_id = player.account_id
            placement = int(entry["placement"])
            state = states[account_id]
            state["rank_id"] = update.rank_after
            state["pt"] = update.pt_after
            state["rating"] = update.rating_after
            state["games"] += 1
            games_by_account[account_id] += 1
            rank_counts[account_id][placement - 1] += 1
            if update.transition in {"promotion", "tenhou"}:
                promotions[account_id] += 1
            if update.transition == "demotion":
                demotions[account_id] += 1
            if rank_system.rank_meta(update.rank_after).ordinal > rank_system.rank_meta(
                highest[account_id]
            ).ordinal:
                highest[account_id] = update.rank_after
            if update.rank_after == "tenhou":
                tenhou_reached[account_id] = True
            total_pt_delta[account_id] += int(update.pt_delta)

            after_meta = rank_system.rank_meta(update.rank_after)
            row = {
                "game_index": game_index,
                "source_log": match.source_ref,
                "seat": player.seat,
                "account_id": account_id,
                "model_label": None,
                "rank": placement,
                "final_score": int(player.final_score),
                "score_delta": None,
                "game_length": match.game_length,
                "table_avg_rating_before": float(ctx.avg_rating),
                "pt_tier": update.pt_tier,
                "positive_pt": list(update.positive_pt) if update.positive_pt else None,
                "rank_before": update.rank_before,
                "pt_before": int(update.pt_before),
                "pt_delta": int(update.pt_delta),
                "transition": update.transition,
                "rank_after": update.rank_after,
                "pt_after": int(update.pt_after),
                "rank_name": after_meta.rank_name,
                "pt_target": after_meta.target_pt,
                "rating_before": float(update.rating_before),
                "rating_delta": float(update.rating_after - update.rating_before),
                "rating_delta_raw": float(update.rating_delta_raw),
                "rating_after": float(update.rating_after),
                "games_before": int(state["games"]) - 1,
                "games_after": int(state["games"]),
            }
            ledger_rows.append(row)
            curve_rows.append(
                {
                    "game_index": game_index,
                    "account_id": account_id,
                    "model_label": None,
                    "rating": float(update.rating_after),
                    "pt": int(update.pt_after),
                    "rank_id": update.rank_after,
                    "rank_name": after_meta.rank_name,
                    "rank_before": update.rank_before,
                    "rank_after": update.rank_after,
                    "transition": update.transition,
                    "pt_target": after_meta.target_pt,
                    "games": int(state["games"]),
                }
            )

    # R12-A：有完整牌谱时复用 native report 的 build_stat_report（libriichi
    # Stat）——不手搓指标定义，保证与旧 native-log detailed stats 同一口径。
    stat_report: dict[str, Any] | None = None
    if stats_eligible_match_ids:
        from replay.build_platform_account_report import build_stat_report

        is_legacy_fixed = rank_system.system_id == "tenhou_houou_7dan_fixed"
        if is_legacy_fixed:
            # legacy fixed profile：stat 的"平均顺位列 PT"以 profile 的 rank_points 为权威。
            stat_rank_pts = [float(value) for value in rank_system.rank_points]
            stat_profile = "houou_7dan_hanchan"
        else:
            # 动态段位下平均顺位列 PT 失去意义；权威指标改为引擎 total_pt_delta /
            # avg_pt_delta。行为指标（和率/放铳率/副露率/立直率等）不受影响。
            stat_rank_pts = [0.0, 0.0, 0.0, 0.0]
            stat_profile = rank_system.system_id
        stat_report = build_stat_report(
            log_dir=stats_log_dir,
            players={account_id: account_id for account_id in sorted(states)},
            mortal_root=mortal_root,
            rank_pts=stat_rank_pts,
            rank_points_profile=stat_profile,
        )

    summary_rows: list[dict[str, Any]] = []
    for account_id in sorted(states):
        state = states[account_id]
        meta = rank_system.rank_meta(state["rank_id"])
        pt_target = meta.target_pt
        games = int(state["games"])
        stat_player = (stat_report or {}).get("players", {}).get(account_id, {})
        raw_stats = stat_player.get("raw", {})
        derived_stats = stat_player.get("derived", {})
        stats_games = int(raw_stats.get("game") or 0)
        stats_rounds = int(raw_stats.get("round") or 0)
        summary_rows.append(
            {
                "account_id": account_id,
                "model_label": None,
                "games": games,
                "rank_id": meta.rank_id,
                "rank_name": meta.rank_name,
                "rank_ordinal": meta.ordinal,
                "pt_initial": meta.initial_pt,
                "pt_current": int(state["pt"]),
                "pt_target": pt_target,
                "pt_progress": (
                    int(state["pt"]) / float(pt_target) if pt_target and float(pt_target) > 0 else None
                ),
                "rating": float(state["rating"]),
                "rank_1": rank_counts[account_id][0],
                "rank_2": rank_counts[account_id][1],
                "rank_3": rank_counts[account_id][2],
                "rank_4": rank_counts[account_id][3],
                "avg_rank": _avg_rank(rank_counts[account_id], games),
                "avg_rank_pt": None,
                "promotions": promotions[account_id],
                "demotions": demotions[account_id],
                "highest_rank_id": highest[account_id],
                "tenhou_reached": tenhou_reached[account_id],
                "total_pt_delta": total_pt_delta[account_id],
                "avg_pt_delta": (
                    total_pt_delta[account_id] / float(games) if games else None
                ),
                "agari_rate": derived_stats.get("agari_rate"),
                "houjuu_rate": derived_stats.get("houjuu_rate"),
                "fuuro_rate": derived_stats.get("fuuro_rate"),
                "riichi_rate": derived_stats.get("riichi_rate"),
                "agari_rate_after_fuuro": derived_stats.get("agari_rate_after_fuuro"),
                "houjuu_rate_after_fuuro": derived_stats.get("houjuu_rate_after_fuuro"),
                "agari_rate_after_riichi": derived_stats.get("agari_rate_after_riichi"),
                "houjuu_rate_after_riichi": derived_stats.get("houjuu_rate_after_riichi"),
                "avg_point_per_agari": derived_stats.get("avg_point_per_agari"),
                "total_delta_score": raw_stats.get("point"),
                # R12-A：详细统计覆盖率披露（分母只含实际算出的完整牌谱场数）。
                "stats_games": stats_games,
                "stats_rounds": stats_rounds,
                "stats_total_games": games,
                "stats_coverage": (
                    round(stats_games / float(games), 4) if games else None
                ),
            }
        )

    report: dict[str, Any] = {
        "schema": "keqing.mortal.platform_account_report.v2",
        "games": len(matches),
        "scoring": rank_system.scoring_block(),
        "accounts": summary_rows,
        "sources": {
            "ingest": True,
            "match_count": len(matches),
            # R12-A：实际进入详细统计的完整牌谱场数（覆盖率披露的事实依据）。
            "stats_match_count": len(stats_eligible_match_ids),
        },
    }
    return {
        "report": report,
        "ledger_rows": ledger_rows,
        "curve_rows": curve_rows,
        "stat_report": stat_report,
    }


def _player_state(rank_system: Any, state: Mapping[str, Any]):
    from replay.rank_systems import PlayerRankState

    return PlayerRankState(
        rank_id=str(state["rank_id"]),
        pt=int(state["pt"]),
        rating=state["rating"],
        games=int(state["games"]),
    )


def _avg_rank(rank_counts: Sequence[int], games: int) -> float | None:
    if not games:
        return None
    total = sum((index + 1) * count for index, count in enumerate(rank_counts))
    return total / float(games)


def write_ingest_outputs(output_dir: Path, result: dict[str, Any]) -> None:
    """写出快照必需三件套（与 SNAPSHOT_REQUIRED_FILES 契约一致）。

    R12-A：stat_report 存在时附带写出 detailed_stats.json/.md（native report
    同构产物，供人工核验）；不存在时清理残留旧文件。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report = result["report"]
    (output_dir / "account_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "account_ledger.jsonl").open("w", encoding="utf-8") as handle:
        for row in result["ledger_rows"]:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    curve_rows = result["curve_rows"]
    if curve_rows:
        fields = list(curve_rows[0].keys())
        with (output_dir / "rating_curve.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(curve_rows)
    else:
        (output_dir / "rating_curve.csv").write_text("", encoding="utf-8")
    stat_report = result.get("stat_report")
    if stat_report:
        from replay.build_platform_account_report import format_markdown_report

        (output_dir / "detailed_stats.json").write_text(
            json.dumps(stat_report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (output_dir / "detailed_stats.md").write_text(
            format_markdown_report(stat_report), encoding="utf-8"
        )
    else:
        for name in ("detailed_stats.json", "detailed_stats.md"):
            stale = output_dir / name
            if stale.exists():
                stale.unlink()


def build_ingest_report(
    *,
    season: Mapping[str, Any],
    sources_root: Path,
    output_dir: Path,
    mortal_root: str | Path = Path("third_party/Mortal"),
) -> dict[str, Any]:
    """赛季级 ingest 构建：读所有 source -> 合并 -> 全量重放 -> 写产物。

    ``season["ingest"]`` 结构：
    ``{"sources_root": str, "native": {"name_to_account": {...}} | null}``

    R12-A：``mortal_root`` 用于复用 build_stat_report（libriichi Stat）；
    相对路径按仓库根解析（与 publisher 运行目录无关）。
    """
    ingest = season.get("ingest")
    if not isinstance(ingest, dict):
        raise LadderIngestError("season 缺少 ingest 配置")

    resolved_mortal_root = _resolve_mortal_root(mortal_root)
    # R12-A：account-normalized 统计日志的派生目录（同 native report 的
    # account_logs 约定）。每次构建前清理，避免残留污染覆盖率与统计。
    stats_log_dir = output_dir / STATS_LOG_SUBDIR
    if stats_log_dir.exists():
        shutil.rmtree(stats_log_dir)
    stats_log_dir.mkdir(parents=True, exist_ok=True)

    allowed_accounts = {
        str(account.get("account_id"))
        for model in season.get("models", []) or []
        if isinstance(model, dict)
        for account in model.get("accounts", []) or []
        if isinstance(account, dict)
    }

    source_entries: list[tuple[SourceAdapter, Path]] = []
    native_dir = sources_root / "native"
    # 空 native 目录不是 source（无日志时不得因缺 index.jsonl 拒绝整个发布）。
    if native_dir.is_dir() and any(native_dir.glob("*.json.gz")):
        raw_mapping = (ingest.get("native") or {}).get("name_to_account") or {}
        name_to_account: dict[str, str] = {}
        # 显式映射优先；缺失时回退 display_name -> account_id
        for model in season.get("models", []) or []:
            if not isinstance(model, dict):
                continue
            for account in model.get("accounts", []) or []:
                if not isinstance(account, dict):
                    continue
                account_id = str(account.get("account_id") or "")
                display = str(account.get("display_name") or "")
                if display and account_id:
                    name_to_account.setdefault(display, account_id)
        name_to_account.update({str(k): str(v) for k, v in raw_mapping.items()})
        source_entries.append((NativeLogAdapter(name_to_account), native_dir))

    for adapter_cls, subdir in (
        (PlayWithYouResultAdapter, "playwithyou"),
        (ManualTenhouAdapter, "manual_tenhou"),
    ):
        source_dir = sources_root / subdir
        if source_dir.is_dir():
            source_entries.append((adapter_cls(), source_dir))

    # R10-F：participants Match Ledger 作为正式计分的事实来源。
    # 赛季 ingest 配置 ``{"participants": {"enabled": true}}`` 时，直接从
    # participants 账本读取 rating_eligible 的比赛（不再复制 manual_tenhou）。
    # ``exclusive: true`` 时不再读取 legacy playwithyou/manual_tenhou（P1-4 双计防护）。
    participants_cfg = ingest.get("participants")
    if isinstance(participants_cfg, dict) and participants_cfg.get("enabled"):
        from participants.paths import data_root as participants_data_root

        participants_dir = participants_data_root()
        if participants_dir.is_dir():
            season_id = str(season.get("season_id") or "")
            source_entries.append((ParticipantLedgerAdapter(season_id=season_id), participants_dir))
        if participants_cfg.get("exclusive"):
            # P1-4：exclusive 时只读 participants ledger，legacy source 仅保留审计/迁移
            exclusive_entries = [
                entry for entry in source_entries if entry[0].source_type == "participants"
            ]
            matches = merge_ladder_matches(exclusive_entries, allowed_accounts=allowed_accounts)
            result = replay_ladder_matches(
                matches,
                scoring_config=season.get("scoring"),
                stats_log_dir=stats_log_dir,
                mortal_root=resolved_mortal_root,
            )
            write_ingest_outputs(output_dir, result)
            return result["report"]

    matches = merge_ladder_matches(source_entries, allowed_accounts=allowed_accounts)
    result = replay_ladder_matches(
        matches,
        scoring_config=season.get("scoring"),
        stats_log_dir=stats_log_dir,
        mortal_root=resolved_mortal_root,
    )
    write_ingest_outputs(output_dir, result)
    return result["report"]
