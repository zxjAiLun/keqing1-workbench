"""Lock mixed-rank progression semantics end-to-end (J1-J9).

Round 8 follow-up: the Tenhou rank progression profile must never reject a
table regardless of the four players' pre-match ranks/ratings, resolve each
player's PT tier independently, and keep the shared pre-match rating snapshot.

The engine-level golden cases live in test_rank_systems.py; these tests lock
the report-builder / ladder-loader data contract for a mixed-rank season.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from replay import ladder
from replay import build_platform_account_report as account_report

TENHOU_SCORING = {
    "system": "tenhou_rank_progression",
    "version": "v1",
    "game_length": "hanchan",
    "initial_rank": "newcomer",
    "initial_rating": 1500,
}


def _write_game(log_dir: Path, name: str, scores: list[int]) -> None:
    events = [
        {"type": "start_game", "names": ["a", "b", "c", "d"]},
        {"type": "start_kyoku", "scores": scores},
        {"type": "ryukyoku", "deltas": [0, 0, 0, 0]},
    ]
    path = log_dir / f"{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _build_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    game_scores: list[list[int]],
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
    report = account_report.build_report(
        log_dirs=[log_dir],
        output_dir=tmp_path / "out",
        mortal_root=Path("third_party/Mortal"),
        platform_model_label="rt",
        rank_points=(90.0, 45.0, 0.0, -135.0),
        scoring_config=TENHOU_SCORING,
    )
    return report


def _season(tmp_path: Path, report_dir: Path) -> dict:
    return {
        "schema": ladder.SEASON_SCHEMA,
        "season_id": "rt-progression",
        "status": "running",
        "report_dir": str(report_dir.resolve()),
        "games_expected": 4,
        "default": True,
        "scoring": TENHOU_SCORING,
        "models": [
            {
                "model_id": "rt",
                "accounts": [
                    {"account_id": f"rt@{i:02d}", "display_name": f"RT-{i:02d}"}
                    for i in range(1, 5)
                ],
            }
        ],
    }


# J1: 混合段位同桌永不拒绝（builder 端到端）
def test_mixed_rank_season_builds_without_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 一场后四账号段位即分化（新人/新人/新人/9kyu...），后续场次继续分化
    report = _build_report(tmp_path, monkeypatch, [[40000, 25000, 20000, 15000]] * 4)
    assert report["games"] == 4
    assert len(report["accounts"]) == 4
    ranks = {row["rank_id"] for row in report["accounts"]}
    # 端到端成功构建即证明：无论段位如何分化，都正常结算、不抛"无共同卓"
    assert len(ranks) >= 1


# J2: 同一局四人可使用不同正分档位（builder ledger 锁定）
def test_ledger_records_per_player_tiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_report(tmp_path, monkeypatch, [[40000, 25000, 20000, 15000]] * 4)
    ledger_path = tmp_path / "out" / "account_ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        assert "table_room" not in row
        assert row.get("pt_tier") is not None
        # 一/二/三位有正分档位；四位 positive_pt 为 null
        if row["rank"] < 4:
            assert row.get("positive_pt") is not None
        else:
            assert row.get("positive_pt") is None


# J6: 赛前快照（四人同一份桌均 R，且无逐个更新污染）
def test_pre_match_rating_snapshot_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_report(tmp_path, monkeypatch, [[40000, 25000, 20000, 15000]] * 2)
    ledger_path = tmp_path / "out" / "account_ledger.jsonl"
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    game1 = [row for row in rows if row["game_index"] == 0]
    game2 = [row for row in rows if row["game_index"] == 1]
    by1 = {row["account_id"]: row for row in game1}
    by2 = {row["account_id"]: row for row in game2}
    # 桌均 R 每局四人一致
    assert len({row["table_avg_rating_before"] for row in game1}) == 1
    assert len({row["table_avg_rating_before"] for row in game2}) == 1
    # 第二局每位赛前 Rating == 第一局赛后 Rating（快照而非中途更新）
    for account_id, row1 in by1.items():
        assert by2[account_id]["rating_before"] == row1["rating_after"]


# J8: 数据契约（report.scoring 无房间字段；loader 经赛季正常读取）
def test_ladder_loader_reads_mixed_rank_season(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _build_report(tmp_path, monkeypatch, [[40000, 25000, 20000, 15000]] * 4)
    scoring = report["scoring"]
    assert "room_policy" not in scoring
    assert "room" not in scoring
    assert scoring["tier_policy"] == "individual_highest"
    assert scoring["positive_pt_tables"]["tokujou-equivalent"] == [75, 30, 0]

    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    season = _season(tmp_path, tmp_path / "out")
    (configs / "rt-progression.json").write_text(json.dumps(season, ensure_ascii=False), encoding="utf-8")

    payload = ladder.load_ladder(tmp_path, configs, "rt-progression", sort="rank")
    assert payload["sort"] == "rank"
    assert len(payload["accounts"]) == 4
    detail = ladder.load_account(tmp_path, configs, "rt-progression", "rt@01")
    recent = detail["recent_games"][0]
    assert "pt_tier" in recent
    assert "table_room" not in recent


# J9: 历史兼容（legacy fixed profile 经 builder + loader 正常，无重复历史最高）
def test_legacy_fixed_profile_loader_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_scoring = {
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
    configs = tmp_path / "configs"
    configs.mkdir(parents=True)
    report_dir = tmp_path / "out"
    def _stub(**kwargs):
        return {
            "players": {},
            "backend": "stub",
            "log_dir": str(kwargs.get("log_dir") or ""),
            "rank_points_profile": "stub",
            "rank_pts": [0.0, 0.0, 0.0, 0.0],
        }
    monkeypatch.setattr(account_report, "build_stat_report", _stub)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _write_game(log_dir, "game_000", [40000, 25000, 20000, 15000])
    account_report.build_report(
        log_dirs=[log_dir],
        output_dir=report_dir,
        mortal_root=Path("third_party/Mortal"),
        platform_model_label="rt",
        rank_points=(90.0, 45.0, 0.0, -135.0),
        scoring_config=legacy_scoring,
    )
    season = _season(tmp_path, report_dir)
    season["scoring"] = legacy_scoring
    (configs / "rt-progression.json").write_text(json.dumps(season, ensure_ascii=False), encoding="utf-8")

    payload = ladder.load_ladder(tmp_path, configs, "rt-progression", sort="rank")
    row = payload["accounts"][0]
    # legacy fixed 仍为七段固定 PT
    assert row["rank_name"] == "七段"
    assert row["rank_ordinal"] == 17
    assert row["pt_target"] == 2800
    # 不伪造"历史最高"重复：highest == 当前段位时 UI 不会额外展示历史最高
    assert row["highest_rank_id"] == row["rank_id"] == "7dan"
    detail = ladder.load_account(tmp_path, configs, "rt-progression", "rt@01")
    assert detail["account"]["rank_name"] == "七段"
    assert detail["account"]["highest_rank_id"] == "7dan"
