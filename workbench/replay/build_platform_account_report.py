#!/usr/bin/env python3
"""Build platform-style account pt/rating reports from Mortal native logs."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import shutil
import sys
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_WORKBENCH_DIR = _REPO_ROOT / "workbench"
if str(_WORKBENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_DIR))

from replay.rank_systems import PlayerRankState, create_rank_system  # noqa: E402

# ---------------------------------------------------------------------------
# libriichi.stat detail builder (workbench-owned copy).
#
# This used to live in training/mortal/stat_report.py; the report builder needs
# it, so it was copied here when ladder/report publication moved to Workbench.
# training/mortal/stat_report.py keeps its own copy for the arena scripts.
# Keep the public names (build_stat_report, format_markdown_report) stable:
# tests stub them via monkeypatch.
# ---------------------------------------------------------------------------

DEFAULT_RANK_PTS = (30.0, 10.0, -10.0, -30.0)

RAW_FIELDS = (
    "game",
    "round",
    "oya",
    "point",
    "rank_1",
    "rank_2",
    "rank_3",
    "rank_4",
    "tobi",
    "agari",
    "houjuu",
    "fuuro",
    "fuuro_num",
    "riichi",
    "ryukyoku",
    "yakuman",
    "nagashi_mangan",
)

DERIVED_FIELDS = (
    "rank_1_rate",
    "rank_2_rate",
    "rank_3_rate",
    "rank_4_rate",
    "tobi_rate",
    "avg_rank",
    "avg_point_per_game",
    "avg_point_per_round",
    "agari_rate",
    "houjuu_rate",
    "fuuro_rate",
    "riichi_rate",
    "ryukyoku_rate",
    "avg_point_per_agari",
    "avg_point_per_oya_agari",
    "avg_point_per_ko_agari",
    "avg_point_per_riichi_agari",
    "avg_point_per_fuuro_agari",
    "avg_point_per_dama_agari",
    "avg_point_per_ryukyoku",
    "avg_agari_jun",
    "avg_riichi_agari_jun",
    "avg_fuuro_agari_jun",
    "avg_dama_agari_jun",
    "avg_houjuu_jun",
    "avg_point_per_houjuu",
    "avg_point_per_houjuu_to_oya",
    "avg_point_per_houjuu_to_ko",
    "chasing_riichi_rate",
    "riichi_chased_rate",
    "agari_rate_after_riichi",
    "houjuu_rate_after_riichi",
    "avg_riichi_jun",
    "avg_riichi_point",
    "avg_fuuro_num",
    "agari_rate_after_fuuro",
    "houjuu_rate_after_fuuro",
    "avg_fuuro_point",
    "agari_rate_as_oya",
    "agari_as_oya_rate",
    "houjuu_to_oya_rate",
    "yakuman_rate",
    "nagashi_mangan_rate",
)

DISPLAY_ROWS = (
    ("Games", "game"),
    ("Rounds", "round"),
    ("Rounds as dealer", "oya"),
    ("1st (rate)", ("rank_1", "rank_1_rate")),
    ("2nd (rate)", ("rank_2", "rank_2_rate")),
    ("3rd (rate)", ("rank_3", "rank_3_rate")),
    ("4th (rate)", ("rank_4", "rank_4_rate")),
    ("Tobi(rate)", ("tobi", "tobi_rate")),
    ("Avg rank", "avg_rank"),
    ("Total rank pt", "total_rank_pt"),
    ("Avg rank pt", "avg_rank_pt"),
    ("Total delta score", "point"),
    ("Avg game delta score", "avg_point_per_game"),
    ("Avg round delta score", "avg_point_per_round"),
    ("Win rate", "agari_rate"),
    ("Deal-in rate", "houjuu_rate"),
    ("Fuuro round rate", "fuuro_rate"),
    ("Avg call events per game", "avg_fuuro_num"),
    ("Riichi rate", "riichi_rate"),
    ("Ryukyoku rate", "ryukyoku_rate"),
    ("Avg winning delta score", "avg_point_per_agari"),
    ("Avg winning delta score as dealer", "avg_point_per_oya_agari"),
    ("Avg winning delta score as non-dealer", "avg_point_per_ko_agari"),
    ("Avg riichi winning delta score", "avg_point_per_riichi_agari"),
    ("Avg open winning delta score", "avg_point_per_fuuro_agari"),
    ("Avg dama winning delta score", "avg_point_per_dama_agari"),
    ("Avg ryukyoku delta score", "avg_point_per_ryukyoku"),
    ("Avg winning turn", "avg_agari_jun"),
    ("Avg riichi winning turn", "avg_riichi_agari_jun"),
    ("Avg open winning turn", "avg_fuuro_agari_jun"),
    ("Avg dama winning turn", "avg_dama_agari_jun"),
    ("Avg deal-in turn", "avg_houjuu_jun"),
    ("Avg deal-in delta score", "avg_point_per_houjuu"),
    ("Avg deal-in delta score to dealer", "avg_point_per_houjuu_to_oya"),
    ("Avg deal-in delta score to non-dealer", "avg_point_per_houjuu_to_ko"),
    ("Chasing riichi rate", "chasing_riichi_rate"),
    ("Riichi chased rate", "riichi_chased_rate"),
    ("Winning rate after riichi", "agari_rate_after_riichi"),
    ("Deal-in rate after riichi", "houjuu_rate_after_riichi"),
    ("Avg riichi turn", "avg_riichi_jun"),
    ("Avg riichi delta score", "avg_riichi_point"),
    ("Winning rate after call", "agari_rate_after_fuuro"),
    ("Deal-in rate after call", "houjuu_rate_after_fuuro"),
    ("Avg call delta score", "avg_fuuro_point"),
    ("Dealer wins/all dealer rounds", "agari_rate_as_oya"),
    ("Dealer wins/all wins", "agari_as_oya_rate"),
    ("Deal-in to dealer/all deal-ins", "houjuu_to_oya_rate"),
    ("Yakuman (rate)", ("yakuman", "yakuman_rate")),
    ("Nagashi mangan (rate)", ("nagashi_mangan", "nagashi_mangan_rate")),
)


def import_stat_class(mortal_root: str | Path = Path("third_party/Mortal")) -> Any:
    mortal_python_dir = (Path(mortal_root) / "mortal").resolve()
    if str(mortal_python_dir) not in sys.path:
        sys.path.insert(0, str(mortal_python_dir))
    from libriichi.stat import Stat  # noqa: PLC0415

    return Stat


def stat_to_metrics(stat: Any, *, rank_pts: Sequence[int | float] = DEFAULT_RANK_PTS) -> dict[str, Any]:
    metrics: dict[str, Any] = {"raw": {}, "derived": {}, "text": str(stat)}
    for field in RAW_FIELDS:
        metrics["raw"][field] = _normalize_value(getattr(stat, field))
    for field in DERIVED_FIELDS:
        metrics["derived"][field] = _normalize_value(getattr(stat, field))
    pts = [float(value) for value in rank_pts]
    total_rank_pt = sum(float(getattr(stat, f"rank_{rank}")) * pts[rank - 1] for rank in range(1, 5))
    metrics["derived"]["total_rank_pt"] = _normalize_value(total_rank_pt)
    metrics["derived"]["avg_rank_pt"] = _normalize_value(total_rank_pt / float(stat.game) if stat.game else float("nan"))
    return metrics


def build_stat_report(
    *,
    log_dir: str | Path,
    players: Mapping[str, str],
    mortal_root: str | Path = Path("third_party/Mortal"),
    rank_pts: Sequence[int | float] = DEFAULT_RANK_PTS,
    rank_points_profile: str = "custom",
) -> dict[str, Any]:
    stat_cls = import_stat_class(mortal_root)
    player_stats: dict[str, dict[str, Any]] = {}
    for label, player_name in players.items():
        stat = stat_cls.from_dir(str(log_dir), str(player_name), True)
        player_stats[label] = {
            "player_name": str(player_name),
            **stat_to_metrics(stat, rank_pts=rank_pts),
        }
    return {
        "schema": "keqing.mortal.libriichi.stat.v1",
        "backend": "libriichi.stat.Stat.from_dir",
        "log_dir": str(log_dir),
        "rank_points_profile": str(rank_points_profile),
        "rank_points_values": [float(value) for value in rank_pts],
        "rank_pts": [float(value) for value in rank_pts],
        "players": player_stats,
    }


def format_markdown_report(report: Mapping[str, Any]) -> str:
    players = dict(report["players"])
    labels = list(players)
    lines = [
        "# Mortal Match Detailed Stats",
        "",
        f"- Backend: `{report['backend']}`",
        f"- Log dir: `{report['log_dir']}`",
        f"- Rank point profile: `{report.get('rank_points_profile', 'custom')}`",
        f"- Rank pts: `{report['rank_pts']}`",
        "",
        "| Metric | " + " | ".join(labels) + " |",
        "| --- | " + " | ".join("---" for _ in labels) + " |",
    ]
    for title, key in DISPLAY_ROWS:
        values = [_format_row_value(players[label], key) for label in labels]
        lines.append("| " + " | ".join([title, *values]) + " |")
    lines.append("")
    return "\n".join(lines)


def _format_row_value(player: Mapping[str, Any], key: str | tuple[str, str]) -> str:
    raw = player["raw"]
    derived = player["derived"]
    if isinstance(key, tuple):
        count = raw[key[0]]
        rate = derived[key[1]]
        return f"{_format_value(count)} ({_format_value(rate)})"
    if key in raw:
        return _format_value(raw[key])
    return _format_value(derived[key])


def _format_value(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return value if isfinite(value) else None
    return value

TENHOU_RANK_RESULTS = (30.0, 10.0, -10.0, -30.0)
HOUOU_7DAN_HANCHAN_PT = (90.0, 45.0, 0.0, -135.0)
DEFAULT_RANK_POINTS = HOUOU_7DAN_HANCHAN_PT
INITIAL_RATING = 1500.0
INITIAL_PT = 1400.0
PT_TARGET = 2800.0
RANK_NAME = "七段"

# 报告 schema：v2 引入版本化计分引擎（rank_id/transition/total_pt_delta 等字段）。
REPORT_SCHEMA = "keqing.mortal.platform_account_report.v2"

# 默认（无 scoring config）时保持历史 legacy fixed profile 行为。
_DEFAULT_SCORING_CONFIG = {
    "system": "tenhou_houou_7dan_fixed",
    "version": "2026-07-01",
    "game_length": "hanchan",
    "room": "houou",
    "initial_rank": "7dan",
    "initial_pt": INITIAL_PT,
    "initial_rating": INITIAL_RATING,
    "rank_points": list(HOUOU_7DAN_HANCHAN_PT),
    "target_pt": PT_TARGET,
}

KNOWN_OUTPUT_FILES = (
    "account_summary.json",
    "account_summary.csv",
    "account_summary.md",
    "account_ledger.jsonl",
    "rating_curve.csv",
    "per_game_results.csv",
    "detailed_stats.json",
    "detailed_stats.md",
)


@dataclass
class AccountState:
    account_id: str
    model_label: str
    rank_id: str
    pt: int
    rating: Decimal
    games: int = 0
    rank_counts: list[int] | None = None
    promotions: int = 0
    demotions: int = 0
    highest_rank_id: str | None = None
    tenhou_reached: bool = False
    total_pt_delta: int = 0

    def __post_init__(self) -> None:
        if self.rank_counts is None:
            self.rank_counts = [0, 0, 0, 0]

    def player_state(self) -> PlayerRankState:
        return PlayerRankState(
            rank_id=self.rank_id,
            pt=self.pt,
            rating=self.rating,
            games=self.games,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", action="append", type=Path, required=True, help="Input logs directory. Repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mortal-root", type=Path, default=Path("third_party/Mortal"))
    parser.add_argument("--platform-model-label", default=None, help="Force all seats to MODEL@01-04.")
    parser.add_argument("--rank-points", default="90,45,0,-135")
    parser.add_argument(
        "--scoring-config",
        type=Path,
        default=None,
        help="season scoring JSON（版本化计分 profile）；缺省时使用 legacy fixed profile",
    )
    parser.add_argument("--preserve-log-dir-order", action="store_true", help="process repeated --log-dir inputs in supplied order")
    parser.add_argument(
        "--interleave-log-dirs",
        action="store_true",
        help="interleave same-index games across log directories, rotating the directory order each round",
    )
    return parser.parse_args()


def parse_rank_points(value: str) -> tuple[float, float, float, float]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) != 4:
        raise ValueError(f"rank points must contain four numbers, got {value!r}")
    return tuple(float(part) for part in parts)  # type: ignore[return-value]


def iter_log_files(
    log_dirs: Sequence[Path],
    *,
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
) -> list[Path]:
    files_by_dir = [sorted(log_dir.glob("*.json.gz")) for log_dir in log_dirs]
    if interleave_log_dirs:
        files: list[Path] = []
        max_games = max((len(items) for items in files_by_dir), default=0)
        dir_count = len(files_by_dir)
        for game_index in range(max_games):
            # Rotate the per-round directory order so early Tenhou-R games are
            # not systematically assigned to the first league lineups.
            for offset in range(dir_count):
                dir_index = (game_index + offset) % dir_count
                if game_index < len(files_by_dir[dir_index]):
                    files.append(files_by_dir[dir_index][game_index])
        return files
    files = [path for items in files_by_dir for path in items]
    if preserve_log_dir_order:
        return files
    return sorted(files, key=lambda path: (str(path.parent), path.name))


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(json.loads(line))
    if not events or events[0].get("type") != "start_game":
        raise ValueError(f"missing start_game in {path}")
    return events


def stable_source_id(path: Path, seen: dict[str, int]) -> str:
    stem = path.name.removesuffix(".json.gz")
    safe_parent = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.parent.parent.name or path.parent.name)
    base = f"{safe_parent}_{stem}"
    count = seen.get(base, 0)
    seen[base] = count + 1
    return base if count == 0 else f"{base}_{count + 1}"


def normalize_model_label(raw_name: str) -> str:
    if raw_name in {"ext_mortal", "weak_mortal"}:
        return "ext_mortal"
    suffix_match = re.fullmatch(r"(.+)_([ab])", raw_name)
    if suffix_match and suffix_match.group(1) in {"ext_mortal", "70k", "V2_74000", "V3_74000"}:
        return suffix_match.group(1)
    return raw_name


def account_ids_for_names(names: Sequence[str], *, platform_model_label: str | None) -> list[dict[str, str]]:
    if len(names) != 4:
        raise ValueError(f"expected four names, got {names!r}")
    raw_names = [str(raw_name) for raw_name in names]
    if platform_model_label:
        return [
            {
                "account_id": f"{platform_model_label}@{seat + 1:02d}",
                "model_label": platform_model_label,
                "raw_player_name": str(raw_name),
            }
            for seat, raw_name in enumerate(raw_names)
        ]

    raw_counts: dict[str, int] = {}
    unique_raws_by_model: dict[str, list[str]] = {}
    for raw_name in raw_names:
        raw_counts[raw_name] = raw_counts.get(raw_name, 0) + 1
        model_label = normalize_model_label(raw_name)
        unique_raws_by_model.setdefault(model_label, [])
        if raw_name not in unique_raws_by_model[model_label]:
            unique_raws_by_model[model_label].append(raw_name)
    for raws in unique_raws_by_model.values():
        raws.sort()

    model_counts: dict[str, int] = {}
    result: list[dict[str, str]] = []
    for raw_name in raw_names:
        model_label = normalize_model_label(raw_name)
        if raw_counts[raw_name] == 1:
            account_number = unique_raws_by_model[model_label].index(raw_name) + 1
        else:
            model_counts[model_label] = model_counts.get(model_label, 0) + 1
            account_number = model_counts[model_label]
        result.append(
            {
                "account_id": f"{model_label}@{account_number:02d}",
                "model_label": model_label,
                "raw_player_name": raw_name,
            }
        )
    return result


def initial_and_final_scores_from_events(events: Sequence[Mapping[str, Any]]) -> tuple[list[int], list[int]]:
    initial_scores: list[int] | None = None
    scores: list[int] | None = None
    for event in events:
        event_type = event.get("type")
        if event_type == "start_kyoku":
            raw_scores = event.get("scores")
            if isinstance(raw_scores, list) and len(raw_scores) == 4:
                scores = [int(value) for value in raw_scores]
                if initial_scores is None:
                    initial_scores = list(scores)
        elif event_type == "reach_accepted" and scores is not None:
            actor = event.get("actor")
            if actor is not None:
                scores[int(actor)] -= 1000
        elif event_type in {"hora", "ryukyoku"} and scores is not None:
            deltas = event.get("deltas")
            if isinstance(deltas, list) and len(deltas) == 4:
                scores = [int(score + int(delta)) for score, delta in zip(scores, deltas, strict=True)]
    if initial_scores is None or scores is None:
        raise ValueError("could not reconstruct final scores")
    total = sum(scores)
    if total < 100_000:
        ranks = ranks_from_scores(scores)
        scores[ranks.index(1)] += 100_000 - total
    return initial_scores, scores


def ranks_from_scores(scores: Sequence[int]) -> list[int]:
    ordered = sorted(range(4), key=lambda seat: (-int(scores[seat]), seat))
    ranks = [0, 0, 0, 0]
    for rank, seat in enumerate(ordered, 1):
        ranks[seat] = rank
    return ranks


def write_account_log(events: Sequence[Mapping[str, Any]], account_ids: Sequence[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output_path, "wt", encoding="utf-8") as handle:
        for idx, event in enumerate(events):
            row = dict(event)
            if idx == 0 and row.get("type") == "start_game":
                row["names"] = list(account_ids)
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def prepare_output_dir(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in KNOWN_OUTPUT_FILES:
        path = output_dir / filename
        if path.exists():
            path.unlink()
    account_log_dir = output_dir / "account_logs"
    if account_log_dir.exists():
        shutil.rmtree(account_log_dir)
    account_log_dir.mkdir(parents=True, exist_ok=True)
    return account_log_dir


def resolve_effective_scoring_contract(
    scoring_config: dict[str, Any] | None,
    rank_points: str | tuple[float, float, float, float],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """解析出最终生效的 scoring 契约（publisher 与 builder 共用单一实现）。

    返回 ``(effective_config, resolved_scoring_block)``：

    - 无 scoring_config：legacy fixed profile，但必须采用 CLI --rank-points
      （否则 --rank-points 就变成只影响元数据的静默回归）。
    - 有 scoring_config：profile 为权威；若 CLI rank_points 非默认值则视为冲突拒绝。
    """
    if isinstance(rank_points, str):
        parsed_rank_points = parse_rank_points(rank_points)
    else:
        parsed_rank_points = tuple(float(value) for value in rank_points)
    if scoring_config is not None:
        if tuple(float(value) for value in parsed_rank_points) != tuple(
            float(value) for value in DEFAULT_RANK_POINTS
        ):
            raise ValueError(
                "--rank-points 与显式 scoring_config 冲突：自定义 rank_points "
                "必须写在 scoring_config.rank_points 中，不要同时传 CLI"
            )
        effective_config = dict(scoring_config)
    else:
        effective_config = {
            **_DEFAULT_SCORING_CONFIG,
            "rank_points": [float(value) for value in parsed_rank_points],
        }
    profile = create_rank_system(effective_config)
    return effective_config, profile.scoring_block()


def build_report(
    *,
    log_dirs: Sequence[Path],
    output_dir: Path,
    mortal_root: Path,
    platform_model_label: str | None,
    rank_points: tuple[float, float, float, float],
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
    scoring_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account_log_dir = prepare_output_dir(output_dir)

    # 版本化计分引擎：与 publisher 共用同一解析，确保 CLI/config 冲突在任何
    # skip 判断之前失败，且 effective config 与 resolved scoring block 一致。
    effective_scoring, _resolved = resolve_effective_scoring_contract(scoring_config, rank_points)
    rank_system = create_rank_system(effective_scoring)
    system_id = rank_system.system_id
    is_legacy_fixed = system_id == "tenhou_houou_7dan_fixed"

    files = iter_log_files(
        log_dirs,
        preserve_log_dir_order=preserve_log_dir_order,
        interleave_log_dirs=interleave_log_dirs,
    )
    accounts: dict[str, AccountState] = {}
    per_game_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []
    source_seen: dict[str, int] = {}

    for game_index, path in enumerate(files):
        events = read_events(path)
        raw_names = [str(name) for name in events[0]["names"]]
        seat_accounts = account_ids_for_names(raw_names, platform_model_label=platform_model_label)
        account_ids = [entry["account_id"] for entry in seat_accounts]
        source_id = stable_source_id(path, source_seen)
        write_account_log(events, account_ids, account_log_dir / f"{source_id}.json.gz")

        initial_scores, scores = initial_and_final_scores_from_events(events)
        placements = ranks_from_scores(scores)
        table_account_states: list[AccountState] = []
        for seat, entry in enumerate(seat_accounts):
            account_id = entry["account_id"]
            if account_id not in accounts:
                initial = rank_system.initial_state()
                accounts[account_id] = AccountState(
                    account_id=account_id,
                    model_label=entry["model_label"],
                    rank_id=initial.rank_id,
                    pt=initial.pt,
                    rating=initial.rating,
                    # 初始段位即历史最高起点：首场即降段时 highest 仍是初始段位。
                    highest_rank_id=initial.rank_id,
                    tenhou_reached=initial.rank_id == "tenhou",
                )
            table_account_states.append(accounts[account_id])

        # 四位共用同一份赛前状态快照：不允许逐个更新污染桌均 Rating / 段位。
        pre_states = [state.player_state() for state in table_account_states]
        match = rank_system.match_context(pre_states)
        table_avg_rating = match.avg_rating

        updates: list[dict[str, Any]] = []
        for seat, state in enumerate(table_account_states):
            placement = int(placements[seat])
            update = rank_system.apply_result(pre_states[seat], placement=placement, match=match)
            updates.append(
                {
                    "seat": seat,
                    "state": state,
                    "placement": placement,
                    "final_score": int(scores[seat]),
                    "score_delta": int(scores[seat] - initial_scores[seat]),
                    "update": update,
                }
            )

        for entry in updates:
            state = entry["state"]
            update = entry["update"]
            placement = int(entry["placement"])
            state.rank_id = update.rank_after
            state.pt = update.pt_after
            state.rating = update.rating_after
            state.games += 1
            assert state.rank_counts is not None
            state.rank_counts[placement - 1] += 1
            if update.transition in {"promotion", "tenhou"}:
                state.promotions += 1
            if update.transition == "demotion":
                state.demotions += 1
            if state.highest_rank_id is None or (
                rank_system.rank_meta(update.rank_after).ordinal
                > rank_system.rank_meta(state.highest_rank_id).ordinal
            ):
                state.highest_rank_id = update.rank_after
            if update.rank_after == "tenhou":
                state.tenhou_reached = True
            state.total_pt_delta += int(update.pt_delta)

            after_meta = rank_system.rank_meta(update.rank_after)
            seat = int(entry["seat"])
            row = {
                "game_index": game_index,
                "source_log": str(path),
                "seat": seat,
                "raw_player_name": seat_accounts[seat]["raw_player_name"],
                "account_id": state.account_id,
                "model_label": state.model_label,
                "rank": placement,
                "final_score": int(entry["final_score"]),
                "score_delta": int(entry["score_delta"]),
                "game_length": match.game_length,
                "table_avg_rating_before": float(table_avg_rating),
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
                # 原始（未取整）Δ 与有效 Δ 分离：保证 before + rating_delta == after
                "rating_delta_raw": float(update.rating_delta_raw),
                "rating_delta": float(update.rating_after - update.rating_before),
                "rating_after": float(update.rating_after),
                "games_before": int(state.games) - 1,
                "games_after": int(state.games),
            }
            per_game_rows.append(row)
            ledger_rows.append(row)
            curve_rows.append(
                {
                    "game_index": game_index,
                    "account_id": state.account_id,
                    "model_label": state.model_label,
                    "rating": float(update.rating_after),
                    "pt": int(update.pt_after),
                    "rank_id": update.rank_after,
                    "rank_name": after_meta.rank_name,
                    "rank_before": update.rank_before,
                    "rank_after": update.rank_after,
                    "transition": update.transition,
                    "pt_target": after_meta.target_pt,
                    "games": int(state.games),
                }
            )

    account_ids = sorted(accounts)
    if is_legacy_fixed:
        # stat_report 的"平均顺位列 PT"以 profile 的 rank_points 为权威。
        stat_rank_pts = [float(value) for value in rank_system.rank_points]
        stat_profile = "houou_7dan_hanchan"
    else:
        # 动态段位/卓别下 stat_report 的"平均顺位列 PT"失去意义；仍生成详细统计，
        # 但以 zero table 标注，权威指标改为引擎真实 total_pt_delta / avg_pt_delta。
        stat_rank_pts = [0.0, 0.0, 0.0, 0.0]
        stat_profile = system_id
    stat_report = build_stat_report(
        log_dir=account_log_dir,
        players={account_id: account_id for account_id in account_ids},
        mortal_root=mortal_root,
        rank_pts=stat_rank_pts,
        rank_points_profile=stat_profile,
    )

    summary_rows: list[dict[str, Any]] = []
    for account_id in account_ids:
        state = accounts[account_id]
        stat_player = stat_report["players"].get(account_id, {})
        raw = stat_player.get("raw", {})
        derived = stat_player.get("derived", {})
        ranks = state.rank_counts or [0, 0, 0, 0]
        meta = rank_system.rank_meta(state.rank_id)
        pt_target = meta.target_pt
        pt_progress = (
            float(state.pt) / float(pt_target)
            if pt_target is not None and float(pt_target) > 0
            else None
        )
        summary_rows.append(
            {
                "account_id": account_id,
                "model_label": state.model_label,
                "games": int(state.games),
                "rank_id": meta.rank_id,
                "rank_name": meta.rank_name,
                "rank_ordinal": meta.ordinal,
                "pt_initial": meta.initial_pt,
                "pt_current": int(state.pt),
                "pt_target": pt_target,
                "pt_progress": pt_progress,
                "rating": float(state.rating),
                "rank_1": ranks[0],
                "rank_2": ranks[1],
                "rank_3": ranks[2],
                "rank_4": ranks[3],
                "avg_rank": derived.get("avg_rank"),
                "avg_rank_pt": derived.get("avg_rank_pt") if is_legacy_fixed else None,
                "promotions": state.promotions,
                "demotions": state.demotions,
                "highest_rank_id": state.highest_rank_id,
                "tenhou_reached": state.tenhou_reached,
                "total_pt_delta": state.total_pt_delta,
                "avg_pt_delta": (
                    state.total_pt_delta / float(state.games) if state.games else None
                ),
                "agari_rate": derived.get("agari_rate"),
                "houjuu_rate": derived.get("houjuu_rate"),
                "fuuro_rate": derived.get("fuuro_rate"),
                "riichi_rate": derived.get("riichi_rate"),
                "agari_rate_after_fuuro": derived.get("agari_rate_after_fuuro"),
                "houjuu_rate_after_fuuro": derived.get("houjuu_rate_after_fuuro"),
                "agari_rate_after_riichi": derived.get("agari_rate_after_riichi"),
                "houjuu_rate_after_riichi": derived.get("houjuu_rate_after_riichi"),
                "avg_point_per_agari": derived.get("avg_point_per_agari"),
                "total_delta_score": raw.get("point"),
            }
        )

    # profile 自述的完整 scoring 描述（公式/PT 契约/房间策略）为单一事实来源。
    scoring_payload = rank_system.scoring_block()

    report = {
        "schema": REPORT_SCHEMA,
        "log_dirs": [str(path) for path in log_dirs],
        "output_dir": str(output_dir),
        "platform_model_label": platform_model_label,
        "preserve_log_dir_order": bool(preserve_log_dir_order),
        "interleave_log_dirs": bool(interleave_log_dirs),
        "scoring": scoring_payload,
        "games": len(files),
        "accounts": summary_rows,
        "stat_report": stat_report,
    }

    write_outputs(
        output_dir=output_dir,
        report=report,
        summary_rows=summary_rows,
        ledger_rows=ledger_rows,
        curve_rows=curve_rows,
        per_game_rows=per_game_rows,
    )
    return report


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fmt_float(value: Any, digits: int = 4) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def build_summary_markdown(report: Mapping[str, Any], summary_rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Platform Account Report",
        "",
        f"- Games: `{report['games']}`",
        f"- Platform model label override: `{report.get('platform_model_label')}`",
        f"- Pt profile: `{report['scoring']['pt_profile']}`",
        f"- Rating formula: `{report['scoring']['rating_formula']}`",
        "",
        "| Account | Model | Games | Pt | R | Rank counts | Avg rank | Agari | Houjuu | Fuuro | Riichi |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary_rows:
        ranks = f"[{row['rank_1']},{row['rank_2']},{row['rank_3']},{row['rank_4']}]"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["account_id"]),
                    str(row["model_label"]),
                    str(row["games"]),
                    fmt_float(row["pt_current"], 1),
                    fmt_float(row["rating"], 2),
                    ranks,
                    fmt_float(row["avg_rank"], 4),
                    fmt_float(row["agari_rate"], 4),
                    fmt_float(row["houjuu_rate"], 4),
                    fmt_float(row["fuuro_rate"], 4),
                    fmt_float(row["riichi_rate"], 4),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    *,
    output_dir: Path,
    report: Mapping[str, Any],
    summary_rows: Sequence[Mapping[str, Any]],
    ledger_rows: Sequence[Mapping[str, Any]],
    curve_rows: Sequence[Mapping[str, Any]],
    per_game_rows: Sequence[Mapping[str, Any]],
) -> None:
    (output_dir / "account_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "account_summary.csv", summary_rows)
    (output_dir / "account_summary.md").write_text(build_summary_markdown(report, summary_rows), encoding="utf-8")
    write_jsonl(output_dir / "account_ledger.jsonl", ledger_rows)
    write_csv(output_dir / "rating_curve.csv", curve_rows)
    write_csv(output_dir / "per_game_results.csv", per_game_rows)
    (output_dir / "detailed_stats.json").write_text(
        json.dumps(report["stat_report"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "detailed_stats.md").write_text(format_markdown_report(report["stat_report"]), encoding="utf-8")


def main() -> None:
    args = parse_args()
    scoring_config: dict[str, Any] | None = None
    if args.scoring_config is not None:
        scoring_config = json.loads(args.scoring_config.read_text(encoding="utf-8"))
    report = build_report(
        log_dirs=args.log_dir,
        output_dir=args.output_dir,
        mortal_root=args.mortal_root,
        platform_model_label=args.platform_model_label,
        rank_points=parse_rank_points(str(args.rank_points)),
        preserve_log_dir_order=bool(args.preserve_log_dir_order),
        interleave_log_dirs=bool(args.interleave_log_dirs),
        scoring_config=scoring_config,
    )
    print(json.dumps({"games": report["games"], "accounts": len(report["accounts"])}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
