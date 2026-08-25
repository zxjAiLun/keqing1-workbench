# -*- coding: utf-8 -*-
"""R12-A：Replay Stats Projection——full_replay artifact → libriichi detailed stats → ladder snapshot。

断链修复的核心契约：
1. ParticipantLedgerAdapter 对 full_replay + replay_id 的 Match 关联 replay artifact；
2. 投影阶段读取 artifact 事件流，按 seat 改写成正式 account_id，注入
   reach_accepted（libriichi Stat 依赖），防御性补齐 dora_marker/tehais；
3. 复用 build_stat_report（测试中以确定性 stub 替代原生 libriichi）聚合；
4. 详细指标 + 覆盖率字段（stats_games/stats_rounds/stats_total_games/
   stats_coverage）合并进 account_summary.json，行为指标分母只含实际
   算出的完整牌谱场数。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from participants import intake, ledger, registry
from participants.schemas import AccountCreate, MatchCreate, MatchSeat, ModelIdentityCreate
from replay import build_platform_account_report as account_report
from replay import ladder_ingest

SEASON = "official-ladder-v1"
ACCOUNTS = ("nick@01", "70k@01", "70k@02", "70k@03")


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    # 正式资格 gate（ensure_ladder_eligibility）需要有效赛季配置。
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    return root


def _accounts() -> None:
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="70k",
            label="70k",
            kind="local_model",
            artifact_path="artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth",
            stage="promoted",
        )
    )
    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("70k@03", "managed_bot"),
    ):
        registry.create_account(
            AccountCreate(
                account_id=account_id,
                display_name=account_id,
                account_type=account_type,
                model_identity_id="70k" if account_type != "human" else None,
            )
        )


def _create_match(
    *,
    occurred_at: str,
    final_scores: list[int],
    data_completeness: str = "result_only",
    replay_id: str | None = None,
) -> str:
    match = ledger.create_match(
        MatchCreate(
            occurred_at=occurred_at,
            game_length="hanchan",
            season_id=SEASON,
            rating_eligible=True,
            source="imported" if replay_id else "manual",
            data_completeness=data_completeness,
            replay_id=replay_id,
            provider="tenhou" if replay_id else None,
            external_match_id=replay_id,
            raw_player_names=["A", "B", "C", "D"] if replay_id else None,
            seats=[MatchSeat(seat=seat, account_id=account_id) for seat, account_id in enumerate(ACCOUNTS)],
            final_scores=final_scores,
        ),
        registry,
    )
    return match.match_id


def _hand(
    round_index: int,
    scores: list[int],
    *,
    winner: int | None = None,
    target: int | None = None,
    deltas: list[int] | None = None,
    reach: tuple[int, ...] = (),
    calls: dict[int, int] | None = None,
    declaration_ron: bool = False,
) -> list[dict]:
    events: list[dict] = [
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "dora_marker": None,  # 故意缺失：投影必须防御性补齐为 "?"
            "kyoku": round_index % 4 + 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": round_index % 4,
            "scores": list(scores),
            "tehais": [[], [], [], []],  # 空手牌：投影必须补齐到 13 张
        }
    ]
    for seat in reach:
        events.append({"type": "reach", "actor": seat})
        events.append({"type": "dahai", "actor": seat, "pai": "9p", "tsumogiri": False})
        if not declaration_ron:
            # 宣言牌未被荣和：牌局继续 → 下一次 take 触发立直成立
            # （真实 converter 流在宣言牌与和了/流局之间必有后续摸牌/副露）。
            events.append({"type": "tsumo", "actor": (seat + 1) % 4, "pai": "1m"})
    for seat, count in (calls or {}).items():
        for _ in range(count):
            events.append(
                {"type": "pon", "actor": seat, "target": (seat + 3) % 4, "pai": "9p", "consumed": ["9p", "9p"]}
            )
    if winner is not None:
        events.append(
            {"type": "hora", "actor": winner, "target": target, "deltas": list(deltas or [0, 0, 0, 0])}
        )
    else:
        events.append({"type": "ryukyoku", "reason": "ryukyoku", "deltas": list(deltas or [0, 0, 0, 0])})
    events.append({"type": "end_kyoku"})
    return events


def _write_replay_artifact(log_id: str, hands: list[list[dict]]) -> None:
    directory = intake.artifact_dir(log_id)
    directory.mkdir(parents=True, exist_ok=True)
    events: list[dict] = [
        {"type": "start_game", "names": ["A", "B", "C", "D"], "kyoku_first": 0, "aka_flag": True}
    ]
    for hand in hands:
        events.extend(hand)
    events.append({"type": "end_game"})
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n",
        encoding="utf-8",
    )
    (directory / "summary.json").write_text(
        json.dumps({"schema": "keqing.participant.replay_artifact.v1", "log_id": log_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def _zero_stats() -> dict[str, int]:
    # 覆盖 RAW_FIELDS 全量字段（format_markdown_report 会逐行读取）。
    raw = {field: 0 for field in account_report.RAW_FIELDS}
    raw.update(
        {
            "agari_point": 0,
            "fuuro_agari": 0,
            "fuuro_houjuu": 0,
            "riichi_agari": 0,
            "riichi_houjuu": 0,
        }
    )
    return raw


def _stat_stub(captured: list[dict]):
    """确定性 build_stat_report 替身：解析 account-normalized 日志，按
    libriichi Stat 口径计算 raw/derived，并捕获日志内容供测试核验。"""

    def _stub(**kwargs):
        log_dir = Path(kwargs["log_dir"])
        players: dict[str, dict] = {}
        for path in sorted(log_dir.glob("*.json.gz")):
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]
            names = [str(name) for name in events[0]["names"]]
            captured.append({"path": path.name, "names": names, "events": events})
            for seat, account_id in enumerate(names):
                stat = {**{key: 0 for key in _zero_stats()}, "game": 1}
                scores: list[int] | None = None
                fuuro_in_kyoku = 0
                declared = False
                for event in events:
                    event_type = event.get("type")
                    if event_type == "start_kyoku":
                        stat["round"] += 1
                        fuuro_in_kyoku = 0
                        declared = False
                        scores = [int(value) for value in event["scores"]]
                    elif event_type == "reach" and event.get("actor") == seat:
                        stat["riichi"] += 1
                        declared = True
                    elif event_type == "reach_accepted" and event.get("actor") == seat:
                        assert scores is not None
                        scores[seat] -= 1000
                    elif event_type in ("chi", "pon", "daiminkan") and event.get("actor") == seat:
                        fuuro_in_kyoku += 1
                    elif event_type == "hora":
                        actor = int(event.get("actor", -1))
                        target = int(event.get("target", -1))
                        deltas = [int(value) for value in event.get("deltas") or [0, 0, 0, 0]]
                        if scores is not None:
                            for index in range(4):
                                scores[index] += deltas[index]
                        if actor == seat:
                            stat["agari"] += 1
                            stat["agari_point"] += deltas[seat] - (1000 if declared else 0)
                            if declared:
                                stat["riichi_agari"] += 1
                            elif fuuro_in_kyoku > 0:
                                stat["fuuro_agari"] += 1
                        elif target == seat:
                            stat["houjuu"] += 1
                            if declared:
                                stat["riichi_houjuu"] += 1
                            elif fuuro_in_kyoku > 0:
                                stat["fuuro_houjuu"] += 1
                    elif event_type == "ryukyoku":
                        deltas = [int(value) for value in event.get("deltas") or [0, 0, 0, 0]]
                        if scores is not None:
                            for index in range(4):
                                scores[index] += deltas[index]
                    elif event_type == "end_kyoku" and fuuro_in_kyoku > 0:
                        stat["fuuro"] += 1
                stat["point"] = scores[seat] - 25000 if scores is not None else 0
                row = players.setdefault(account_id, {"raw": _zero_stats(), "derived": {}, "text": ""})
                for key, value in stat.items():
                    row["raw"][key] = int(row["raw"][key]) + int(value)
        for row in players.values():
            raw = row["raw"]
            rounds = int(raw["round"])
            wins = int(raw["agari"])
            # 覆盖 DERIVED_FIELDS 全量字段 + total/avg rank pt（format_markdown 逐行读取）。
            derived: dict = {field: None for field in account_report.DERIVED_FIELDS}
            derived.update({"total_rank_pt": None, "avg_rank_pt": None})
            derived.update({
                "agari_rate": round(wins / rounds, 4) if rounds else None,
                "houjuu_rate": round(int(raw["houjuu"]) / rounds, 4) if rounds else None,
                "fuuro_rate": round(int(raw["fuuro"]) / rounds, 4) if rounds else None,
                "riichi_rate": round(int(raw["riichi"]) / rounds, 4) if rounds else None,
                "agari_rate_after_fuuro": (
                    round(int(raw["fuuro_agari"]) / int(raw["fuuro"]), 4) if raw["fuuro"] else None
                ),
                "houjuu_rate_after_fuuro": (
                    round(int(raw["fuuro_houjuu"]) / int(raw["fuuro"]), 4) if raw["fuuro"] else None
                ),
                "agari_rate_after_riichi": (
                    round(int(raw["riichi_agari"]) / int(raw["riichi"]), 4) if raw["riichi"] else None
                ),
                "houjuu_rate_after_riichi": (
                    round(int(raw["riichi_houjuu"]) / int(raw["riichi"]), 4) if raw["riichi"] else None
                ),
                "avg_point_per_agari": (
                    round(int(raw["agari_point"]) / wins, 4) if wins else None
                ),
            })
            row["derived"] = derived
        return {
            "schema": "keqing.mortal.libriichi.stat.v1",
            "backend": "stub",
            "log_dir": str(log_dir),
            "rank_points_profile": "stub",
            "rank_pts": [0.0, 0.0, 0.0, 0.0],
            "players": players,
        }

    return _stub


def _season(tmp_path: Path) -> dict:
    return {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }


def _build_report(tmp_path: Path, monkeypatch, captured: list[dict]) -> dict:
    monkeypatch.setattr(account_report, "build_stat_report", _stat_stub(captured))
    return ladder_ingest.build_ingest_report(
        season=_season(tmp_path),
        sources_root=tmp_path / "sources",
        output_dir=tmp_path / "out",
    )


# ---------------------------------------------------------------------------
# Adapter：full_replay 关联 replay artifact
# ---------------------------------------------------------------------------

def test_participant_ledger_adapter_attaches_replay_artifact(participants_root):
    _accounts()
    full_id = _create_match(
        occurred_at="2026-08-04T10:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact("20260804gm-0009-0000-aaaaaaaa", [_hand(0, [25000] * 4)])
    _create_match(
        occurred_at="2026-08-04T11:00:00+08:00",
        final_scores=[30000, 25000, 25000, 20000],
    )

    adapter = ladder_ingest.ParticipantLedgerAdapter(season_id=SEASON)
    matches = {m.match_id: m for m in adapter.iter_matches(participants_root)}
    assert matches[full_id].replay_artifact_dir == intake.artifact_dir("20260804gm-0009-0000-aaaaaaaa")
    others = [m for mid, m in matches.items() if mid != full_id]
    assert len(others) == 1
    assert others[0].replay_artifact_dir is None


# ---------------------------------------------------------------------------
# 投影：详细统计合并 + 覆盖率
# ---------------------------------------------------------------------------

def _seed_full_replay_scenario() -> None:
    # 两场完整牌谱（3 局） + 一场 result_only
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact(
        "20260804gm-0009-0000-aaaaaaaa",
        [
            _hand(0, [25000, 25000, 25000, 25000], winner=0, target=0, deltas=[6000, -2000, -2000, -2000], reach=(0,)),
            _hand(1, [31000, 23000, 23000, 23000], winner=2, target=1, deltas=[0, -4000, 4000, 0], calls={2: 1}),
        ],
    )
    _create_match(
        occurred_at="2026-08-04T10:00:00+08:00",
        final_scores=[25000, 25000, 25000, 25000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0001-bbbbbbbb",
    )
    _write_replay_artifact(
        "20260804gm-0009-0001-bbbbbbbb",
        [_hand(0, [25000, 25000, 25000, 25000], deltas=[0, 0, 0, 0])],
    )
    _create_match(
        occurred_at="2026-08-04T11:00:00+08:00",
        final_scores=[30000, 25000, 25000, 20000],
    )


def test_replay_projects_detailed_stats_into_summary(tmp_path, monkeypatch):
    _accounts()
    _seed_full_replay_scenario()
    captured: list[dict] = []
    report = _build_report(tmp_path, monkeypatch, captured)

    assert len(captured) == 2  # 仅两场 full_replay 进入统计
    assert report["sources"]["stats_match_count"] == 2
    nick = next(row for row in report["accounts"] if row["account_id"] == "nick@01")
    # 三场正式对局：两场完整牌谱、一场 result_only
    assert nick["games"] == 3
    assert nick["stats_games"] == 2
    assert nick["stats_rounds"] == 3
    assert nick["stats_total_games"] == 3
    assert nick["stats_coverage"] == pytest.approx(0.6667)
    # 行为指标：1 和 / 3 局；立直 1 次且立直后和牌；总分数变化 +6000
    # （平均和牌打点扣自对立直棒：6000 - 1000 = 5000，与 libriichi 口径一致）
    assert nick["agari_rate"] == pytest.approx(0.3333)
    assert nick["houjuu_rate"] == 0.0
    assert nick["fuuro_rate"] == 0.0
    assert nick["riichi_rate"] == pytest.approx(0.3333)
    assert nick["agari_rate_after_riichi"] == 1.0
    assert nick["avg_point_per_agari"] == pytest.approx(5000.0)
    assert nick["total_delta_score"] == 6000
    # 放铳方 70k@01：1 放铳 / 3 局
    seat1 = next(row for row in report["accounts"] if row["account_id"] == "70k@01")
    assert seat1["houjuu_rate"] == pytest.approx(0.3333)
    assert seat1["agari_rate"] == 0.0
    # 派生产物：detailed_stats.json/.md 与 account_logs 派生副本就位
    out = tmp_path / "out"
    assert (out / "detailed_stats.json").is_file()
    assert (out / "detailed_stats.md").is_file()
    assert len(list((out / "account_logs").glob("*.json.gz"))) == 2


def test_stats_logs_account_normalized_with_reach_accepted(tmp_path, monkeypatch):
    _accounts()
    _seed_full_replay_scenario()
    captured: list[dict] = []
    _build_report(tmp_path, monkeypatch, captured)

    for entry in captured:
        assert entry["names"] == list(ACCOUNTS)  # start_game.names 已改写为正式账号
        events = entry["events"]
        for event in events:
            if event.get("type") == "start_kyoku":
                # dora_marker 缺失 → "?"；tehais 补齐到 13 张
                assert event.get("dora_marker") == "?"
                assert [len(hand) for hand in event["tehais"]] == [13, 13, 13, 13]
                assert all(tile == "?" for hand in event["tehais"] for tile in hand)
        # R12-A Repair：reach_accepted 按 convlog 状态机成立——宣言牌之后牌局
        # 继续才注入一次；注入点紧随其后的是下一次 take/call（而非紧跟 reach）；
        # 每个立直恰好一个 reach_accepted。
        reach_actors = [e["actor"] for e in events if e.get("type") == "reach"]
        accepted_actors = [e["actor"] for e in events if e.get("type") == "reach_accepted"]
        assert sorted(accepted_actors) == sorted(reach_actors)
        for index, event in enumerate(events):
            if event.get("type") == "reach_accepted":
                assert events[index + 1]["type"] in ("tsumo", "chi", "pon", "daiminkan")


def test_result_only_excluded_from_stats_denominator(tmp_path, monkeypatch):
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact("20260804gm-0009-0000-aaaaaaaa", [_hand(0, [25000] * 4)])
    _create_match(
        occurred_at="2026-08-04T10:00:00+08:00",
        final_scores=[30000, 25000, 25000, 20000],
    )
    captured: list[dict] = []
    report = _build_report(tmp_path, monkeypatch, captured)
    assert len(captured) == 1
    nick = next(row for row in report["accounts"] if row["account_id"] == "nick@01")
    assert nick["games"] == 2
    assert nick["stats_games"] == 1
    assert nick["stats_total_games"] == 2
    assert nick["stats_coverage"] == 0.5


def test_no_full_replay_keeps_stats_none(tmp_path, monkeypatch):
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[30000, 25000, 25000, 20000],
    )
    captured: list[dict] = []
    report = _build_report(tmp_path, monkeypatch, captured)
    assert captured == []
    nick = next(row for row in report["accounts"] if row["account_id"] == "nick@01")
    assert nick["agari_rate"] is None
    assert nick["total_delta_score"] is None
    assert nick["stats_games"] == 0
    assert nick["stats_rounds"] == 0
    assert nick["stats_coverage"] == 0.0
    out = tmp_path / "out"
    assert not (out / "detailed_stats.json").exists()
    assert not (out / "detailed_stats.md").exists()


def test_missing_artifact_skips_stats_gracefully(tmp_path, monkeypatch):
    """full_replay 声明的 artifact 缺失 → 跳过该局统计，覆盖率如实披露。"""
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-missing00",
    )
    captured: list[dict] = []
    report = _build_report(tmp_path, monkeypatch, captured)
    assert captured == []
    nick = next(row for row in report["accounts"] if row["account_id"] == "nick@01")
    assert nick["stats_games"] == 0
    assert nick["stats_coverage"] == 0.0
    assert nick["agari_rate"] is None


def test_stat_builder_failure_fails_closed(tmp_path, monkeypatch):
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact("20260804gm-0009-0000-aaaaaaaa", [_hand(0, [25000] * 4)])

    def _boom(**kwargs):
        raise RuntimeError("stat boom")

    monkeypatch.setattr(account_report, "build_stat_report", _boom)
    with pytest.raises(RuntimeError, match="stat boom"):
        ladder_ingest.build_ingest_report(
            season=_season(tmp_path),
            sources_root=tmp_path / "sources",
            output_dir=tmp_path / "out",
        )


def test_replay_without_stats_log_dir_skips_stat_builder(tmp_path, monkeypatch):
    """直接调用 replay_ladder_matches 不传 stats_log_dir → 不生成统计日志、
    不调用 build_stat_report（向后兼容既有调用方）。"""
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact("20260804gm-0009-0000-aaaaaaaa", [_hand(0, [25000] * 4)])
    called: list[bool] = []

    def _stub(**kwargs):
        called.append(True)
        return {"schema": "keqing.mortal.libriichi.stat.v1", "players": {}}

    monkeypatch.setattr(account_report, "build_stat_report", _stub)
    adapter = ladder_ingest.ParticipantLedgerAdapter(season_id=SEASON)
    matches = ladder_ingest.merge_ladder_matches(
        [(adapter, tmp_path / "participants")], allowed_accounts=set(ACCOUNTS)
    )
    result = ladder_ingest.replay_ladder_matches(
        matches, scoring_config={"system": "tenhou_rank_progression", "version": "v1"}
    )
    assert called == []
    assert result["stat_report"] is None
    nick = next(row for row in result["report"]["accounts"] if row["account_id"] == "nick@01")
    assert nick["agari_rate"] is None


def test_projection_fingerprint_includes_replay_artifact(tmp_path, monkeypatch):
    _accounts()
    _create_match(
        occurred_at="2026-08-04T09:00:00+08:00",
        final_scores=[31000, 19000, 27000, 23000],
        data_completeness="full_replay",
        replay_id="20260804gm-0009-0000-aaaaaaaa",
    )
    _write_replay_artifact("20260804gm-0009-0000-aaaaaaaa", [_hand(0, [25000] * 4)])
    before = ladder_ingest.participants_projection_fingerprint(SEASON)
    # 修改 artifact 文件内容（统计输入变化）→ 指纹必须变化
    events_path = intake.artifact_dir("20260804gm-0009-0000-aaaaaaaa") / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "end_game"}\n')
    after = ladder_ingest.participants_projection_fingerprint(SEASON)
    assert before != after


def test_normalize_stats_events_unit(participants_root):
    base = [
        {"type": "start_game", "names": ["A", "B", "C", "D"]},
        {
            "type": "start_kyoku",
            "dora_marker": None,
            "scores": [25000, 25000, 25000, 25000],
            "tehais": [["1m"], [], [], []],
        },
        {"type": "reach", "actor": 2},
        {"type": "dahai", "actor": 2, "pai": "9p", "tsumogiri": False},
    ]
    # 局在宣言牌后直接结束（hora 紧跟）：立直未成立 → 不注入 reach_accepted
    ron_events = base + [
        {"type": "hora", "actor": 1, "target": 2, "deltas": [0, 3000, -3000, 0]},
        {"type": "end_kyoku"},
    ]
    normalized = ladder_ingest.normalize_stats_events(ron_events, ACCOUNTS)
    assert normalized[0]["names"] == list(ACCOUNTS)
    start_kyoku = normalized[1]
    assert start_kyoku["dora_marker"] == "?"
    assert [len(hand) for hand in start_kyoku["tehais"]] == [13, 13, 13, 13]
    assert start_kyoku["tehais"][0][0] == "1m"
    assert start_kyoku["tehais"][0][1] == "?"
    assert [e["type"] for e in normalized if e["type"] == "reach_accepted"] == []
    # 牌局继续（下一次 take）→ 恰好一次 reach_accepted，且紧跟其后的是该 take
    continue_events = base + [
        {"type": "tsumo", "actor": 3, "pai": "1m"},
        {"type": "end_kyoku"},
    ]
    continued = ladder_ingest.normalize_stats_events(continue_events, ACCOUNTS)
    accepted = [e for e in continued if e.get("type") == "reach_accepted"]
    assert accepted == [{"type": "reach_accepted", "actor": 2}]
    accepted_index = continued.index(accepted[0])
    assert continued[accepted_index + 1] == {"type": "tsumo", "actor": 3, "pai": "1m"}
    # 输入流已自带 reach_accepted → 幂等，绝不重复注入
    already = ladder_ingest.normalize_stats_events(
        base + [{"type": "reach_accepted", "actor": 2}, {"type": "tsumo", "actor": 3, "pai": "1m"}],
        ACCOUNTS,
    )
    assert [e for e in already if e.get("type") == "reach_accepted"] == [
        {"type": "reach_accepted", "actor": 2}
    ]


# ---------------------------------------------------------------------------
# R12-A Repair：reach acceptance 语义（真实 libriichi Stat 后端回归）
# ---------------------------------------------------------------------------

def _stat_class():
    from replay.build_platform_account_report import import_stat_class

    mortal_root = Path(__file__).resolve().parents[1] / "third_party" / "Mortal"
    return import_stat_class(mortal_root)


def _stat_log(events: list[dict]) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


def _riichi_kyoku(*, accepted: bool) -> list[dict]:
    """构造最小立直局：seat 0 立直宣言，seat 1 荣和。

    ``accepted=True``：宣言牌未被荣和，牌局继续（下一次 take 发生）→ 立直成立；
    ``accepted=False``：宣言牌直接被 seat 1 荣和 → 立直不成立。
    """
    events: list[dict] = [
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "dora_marker": "1m",
            "kyoku": 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": 0,
            "scores": [25000, 25000, 25000, 25000],
            "tehais": [["1m"] * 13 for _ in range(4)],
        },
        {"type": "tsumo", "actor": 0, "pai": "1m"},
        {"type": "reach", "actor": 0},
        {"type": "dahai", "actor": 0, "pai": "9p", "tsumogiri": False},
    ]
    if accepted:
        # 牌局继续：下一次 take 发生 → 立直成立；立直者后续摸切被荣和
        events.extend([
            {"type": "tsumo", "actor": 1, "pai": "1m"},
            {"type": "tsumo", "actor": 0, "pai": "1m"},
            {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": True},
        ])
    events.extend([
        {"type": "hora", "actor": 1, "target": 0, "deltas": [-4000, 4000, 0, 0]},
        {"type": "end_kyoku"},
    ])
    return events


def test_real_stat_deducts_1000_on_accepted_riichi():
    """正常成立立直：reach → dahai → 牌局继续 ⇒ 恰好一次 reach_accepted，
    Stat 正确扣 1000（point = -5000 = -4000 放铳 - 1000 立直棒）。"""
    from convert.tenhou6_utils import insert_reach_accepted

    events = insert_reach_accepted(_riichi_kyoku(accepted=True))
    accepted_events = [e for e in events if e.get("type") == "reach_accepted"]
    assert accepted_events == [{"type": "reach_accepted", "actor": 0}]
    stat = _stat_class().from_log(_stat_log(events), 0)
    assert int(stat.point) == -5000
    assert int(stat.riichi) == 1
    assert int(stat.houjuu) == 1


def test_real_stat_no_deduction_on_declaration_discard_ron():
    """立直宣言牌被荣和：reach → dahai → hora(target=立直者) ⇒ 零 reach_accepted，
    立直未成立不多扣 1000（point = -4000 仅放铳）。"""
    from convert.tenhou6_utils import insert_reach_accepted

    events = insert_reach_accepted(_riichi_kyoku(accepted=False))
    assert [e for e in events if e.get("type") == "reach_accepted"] == []
    stat = _stat_class().from_log(_stat_log(events), 0)
    assert int(stat.point) == -4000
    assert int(stat.riichi) == 1
    assert int(stat.houjuu) == 1
