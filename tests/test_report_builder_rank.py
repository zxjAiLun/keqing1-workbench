"""Integration tests for the versioned scoring engine inside the report builder.

These tests exercise ``build_platform_account_report.build_report`` with the
Tenhou ranked profile using synthetic mjai-style logs.  ``build_stat_report``
(Mortal libriichi parsing) is stubbed out so the tests only cover the
rank/rating engine pipeline, not Mortal's detailed stats.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from replay import build_platform_account_report as account_report

TENHOU_SCORING = {
    "system": "tenhou_rank_progression",
    "version": "v1",
    "game_length": "hanchan",
    "initial_rank": "newcomer",
    "initial_rating": 1500,
}

LEGACY_SCORING = {
    "system": "tenhou_houou_7dan_fixed",
    "version": "2026-07-01",
    "game_length": "hanchan",
    "room": "houou",
    "initial_rank": "7dan",
    "initial_pt": 1400,
    "initial_rating": 1500,
    "rank_points": [90, 45, 0, -135],
    "target_pt": 2800,
}


def _write_game(log_dir: Path, name: str, scores: list[int]) -> None:
    """Write a minimal playable log: start_game -> start_kyoku -> ryukyoku.

    The final scores are provided directly in start_kyoku and confirmed by a
    zero-delta ryukyoku, giving placements 1..4 by descending score (ties by
    seat order).
    """
    events = [
        {"type": "start_game", "names": ["a", "b", "c", "d"]},
        {"type": "start_kyoku", "scores": scores},
        {"type": "ryukyoku", "deltas": [0, 0, 0, 0]},
    ]
    path = log_dir / f"{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _build(
    tmp_path: Path,
    scoring_config: dict | None,
    game_scores: list[list[int]],
    monkeypatch: pytest.MonkeyPatch,
    *,
    rank_points: tuple[float, float, float, float] = (90.0, 45.0, 0.0, -135.0),
) -> dict:
    def _stub(**kwargs):
        return {
            "players": {},
            "backend": "stub",
            "log_dir": str(kwargs.get("log_dir") or ""),
            "rank_points_profile": str(kwargs.get("rank_points_profile") or "custom"),
            "rank_pts": [float(value) for value in kwargs.get("rank_pts") or [0, 0, 0, 0]],
        }

    monkeypatch.setattr(account_report, "build_stat_report", _stub)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    for index, scores in enumerate(game_scores):
        _write_game(log_dir, f"game_{index:03d}", scores)
    return account_report.build_report(
        log_dirs=[log_dir],
        output_dir=tmp_path / "out",
        mortal_root=Path("third_party/Mortal"),
        platform_model_label="testmodel",
        rank_points=rank_points,
        scoring_config=scoring_config,
    )


def _ledger_rows(tmp_path: Path) -> list[dict]:
    ledger_path = tmp_path / "out" / "account_ledger.jsonl"
    return [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _by_account(rows: list[dict], account_id: str) -> list[dict]:
    return [row for row in rows if row["account_id"] == account_id]


def test_tenhou_engine_progresses_newcomer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # a wins 1st (+90): newcomer(0) -> 9kyu(0), then 4th (-135): 9kyu floored at 0.
    report = _build(
        tmp_path,
        TENHOU_SCORING,
        [
            [40000, 25000, 20000, 15000],  # a=1st, b=2nd, c=3rd, d=4th
            [15000, 25000, 20000, 40000],  # d=1st, b=2nd, c=3rd, a=4th
        ],
        monkeypatch,
    )
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    assert a["rank_id"] == "9kyu"
    assert a["rank_ordinal"] == 2
    assert a["pt_current"] == 0
    assert a["pt_target"] == 20
    assert a["games"] == 2
    assert a["tenhou_reached"] is False


def test_ledger_records_transitions_and_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build(
        tmp_path,
        TENHOU_SCORING,
        [
            [40000, 25000, 20000, 15000],
            [40000, 25000, 20000, 15000],
        ],
        monkeypatch,
    )
    rows = _ledger_rows(tmp_path)
    a_rows = _by_account(rows, "testmodel@01")
    assert len(a_rows) == 2
    first = a_rows[0]
    # 四名新人 -> 个人档位 ippan（一般档 +30/+15/0），不记录卓别
    assert "table_room" not in first
    assert first["game_length"] == "hanchan"
    assert first["pt_tier"] == "ippan-equivalent"
    assert first["positive_pt"] == [30, 15, 0]
    assert first["rank_before"] == "newcomer"
    assert first["rank_after"] == "9kyu"
    assert first["transition"] == "promotion"
    assert first["pt_delta"] == 30
    second = a_rows[1]
    assert second["rank_before"] == "9kyu"
    assert second["rank_after"] == "8kyu"
    assert second["transition"] == "promotion"


def test_all_players_use_pre_match_rating_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two games: after game 1 every player's rating changed. Game 2's table
    # average must be computed from the four PRE-game ratings, not from a
    # partially-updated mix.
    _build(
        tmp_path,
        TENHOU_SCORING,
        [
            [40000, 25000, 20000, 15000],
            [40000, 25000, 20000, 15000],
        ],
        monkeypatch,
    )
    rows = _ledger_rows(tmp_path)
    game1 = [row for row in rows if row["game_index"] == 0]
    game2 = [row for row in rows if row["game_index"] == 1]
    avg1 = {row["account_id"]: row["table_avg_rating_before"] for row in game1}
    avg2 = {row["account_id"]: row["table_avg_rating_before"] for row in game2}
    # Table average is written per-row; all four rows of a game must agree.
    assert len(set(avg1.values())) == 1
    assert len(set(avg2.values())) == 1
    # Game 1: all four at 1500 -> avg exactly 1500.
    assert abs(avg1["testmodel@01"] - 1500.0) < 1e-9
    # No pollution: every player's game-2 rating_before must equal their
    # game-1 rating_after (the pre-match snapshot, not a mid-loop update).
    by_acct1 = {row["account_id"]: row for row in game1}
    by_acct2 = {row["account_id"]: row for row in game2}
    assert set(by_acct1) == set(by_acct2) == {"testmodel@01", "testmodel@02", "testmodel@03", "testmodel@04"}
    for account_id, row1 in by_acct1.items():
        assert by_acct2[account_id]["rating_before"] == row1["rating_after"]


def test_legacy_fixed_profile_report_keeps_7dan_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _build(
        tmp_path,
        LEGACY_SCORING,
        [
            [40000, 25000, 20000, 15000],
        ],
        monkeypatch,
    )
    assert report["schema"] == "keqing.mortal.platform_account_report.v2"
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    assert a["rank_id"] == "7dan"
    assert a["rank_name"] == "七段"
    assert a["rank_ordinal"] == 17
    assert a["pt_current"] == 1490
    assert a["pt_target"] == 2800
    assert a["promotions"] == 0
    assert a["demotions"] == 0
    assert a["total_pt_delta"] == 90
    assert a["avg_pt_delta"] == 90.0
    assert report["scoring"]["system"] == "tenhou_houou_7dan_fixed"


def test_report_scoring_block_describes_tenhou(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _build(
        tmp_path,
        TENHOU_SCORING,
        [[40000, 25000, 20000, 15000]],
        monkeypatch,
    )
    scoring = report["scoring"]
    assert scoring["system"] == "tenhou_rank_progression"
    assert scoring["version"] == "v1"
    assert scoring["tier_policy"] == "individual_highest"
    assert "room_policy" not in scoring
    assert "room" not in scoring
    assert scoring["initial_rank"] == "newcomer"
    assert scoring["initial_rating"] == 1500.0
    assert scoring["positive_pt_tables"]["ippan-equivalent"] == [30, 15, 0]
    assert scoring["positive_pt_tables"]["houou-equivalent"] == [90, 45, 0]


# --- 复审 H3/H4：--rank-points 与 scoring_config 权威性 --------------------

def test_custom_rank_points_drive_legacy_engine_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _build(
        tmp_path,
        None,
        [[40000, 25000, 20000, 15000]],
        monkeypatch,
        rank_points=(100.0, 50.0, 0.0, -150.0),
    )
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    # 1位按 100 计分（旧实现错误地仍用 90）
    assert a["pt_current"] == 1500
    assert a["total_pt_delta"] == 100
    assert report["scoring"]["pt_rank_deltas"] == [100.0, 50.0, 0.0, -150.0]


def test_explicit_scoring_config_rank_points_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {**LEGACY_SCORING, "rank_points": [100, 50, 0, -150]}
    report = _build(tmp_path, config, [[40000, 25000, 20000, 15000]], monkeypatch)
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    assert a["pt_current"] == 1500
    assert report["scoring"]["pt_rank_deltas"] == [100.0, 50.0, 0.0, -150.0]


def test_conflicting_cli_rank_points_with_scoring_config_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="--rank-points 与显式 scoring_config 冲突"):
        _build(
            tmp_path,
            LEGACY_SCORING,
            [[40000, 25000, 20000, 15000]],
            monkeypatch,
            rank_points=(100.0, 50.0, 0.0, -150.0),
        )


# --- 复审 H6：初始段位计入历史最高 ----------------------------------------

def test_initial_rank_is_historical_high_on_first_demotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scoring = {**TENHOU_SCORING, "initial_rank": "7dan"}
    # testmodel@01 一直四位：七段 1400 - 12×135 -> 第 11 场降为六段
    games = [[15000, 40000, 25000, 20000]] * 12
    report = _build(tmp_path, scoring, games, monkeypatch)
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    assert a["rank_id"] == "6dan"
    assert a["rank_ordinal"] == 16
    assert a["demotions"] >= 1
    assert a["highest_rank_id"] == "7dan"


# --- 复审 H7：rating_delta 数据契约 ---------------------------------------

def test_ledger_rating_before_plus_delta_equals_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build(
        tmp_path,
        TENHOU_SCORING,
        [[40000, 25000, 20000, 15000]] * 4,
        monkeypatch,
    )
    rows = _ledger_rows(tmp_path)
    assert rows
    for row in rows:
        assert "rating_delta_raw" in row
        assert abs((row["rating_before"] + row["rating_delta"]) - row["rating_after"]) < 1e-9


# --- 复审 H8：legacy 公式元数据与实际一致 ---------------------------------

def test_legacy_rating_formula_matches_actual_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _build(tmp_path, LEGACY_SCORING, [[40000, 25000, 20000, 15000]], monkeypatch)
    assert "max(table_avg_rating, 1500)" not in report["scoring"]["rating_formula"]
    assert "rounded up" not in report["scoring"]["rating_formula"]
    tenhou = _build(tmp_path, TENHOU_SCORING, [[40000, 25000, 20000, 15000]], monkeypatch)
    assert "max(table_avg_rating, 1500)" in tenhou["scoring"]["rating_formula"]


def test_tenhou_account_total_pt_delta_does_not_grow_at_tenhou(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 以天凤位开局（R2000 可进凤凰卓）：继续对局 PTΔ=0，累计不增长
    scoring = {**TENHOU_SCORING, "initial_rank": "tenhou", "initial_rating": 2000}
    report = _build(tmp_path, scoring, [[40000, 25000, 20000, 15000]] * 2, monkeypatch)
    a = next(row for row in report["accounts"] if row["account_id"] == "testmodel@01")
    assert a["rank_id"] == "tenhou"
    assert a["tenhou_reached"] is True
    assert a["total_pt_delta"] == 0
    assert a["pt_current"] == 0
    assert a["pt_target"] is None
