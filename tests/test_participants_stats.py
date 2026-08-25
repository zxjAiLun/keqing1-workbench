# -*- coding: utf-8 -*-
"""R10-G：账号详细统计——ledger 基础指标 + full replay 牌谱屋式指标（completeness-aware）。"""
from __future__ import annotations

import pytest

from participants import intake, ledger, registry, stats
from participants.schemas import AccountCreate, MatchCreate, MatchSeat, ModelIdentityCreate


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    return root


def _accounts():
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


def _seats():
    return [
        MatchSeat(seat=0, account_id="nick@01"),
        MatchSeat(seat=1, account_id="70k@01"),
        MatchSeat(seat=2, account_id="70k@02"),
        MatchSeat(seat=3, account_id="70k@03"),
    ]


def _full_replay_match(log_id="log1", occurred_at="2026-08-04T10:00:00+08:00"):
    return ledger.create_match(
        MatchCreate(
            occurred_at=occurred_at,
            game_length="hanchan",
            source="imported",
            source_ref=log_id,
            provider="tenhou",
            external_match_id=log_id,
            replay_id=log_id,
            data_completeness="full_replay",
            seats=_seats(),
            final_scores=[30000, 20000, 25000, 25000],
        ),
        registry,
    )


def _result_only_match(occurred_at="2026-08-05T10:00:00+08:00"):
    return ledger.create_match(
        MatchCreate(
            occurred_at=occurred_at,
            game_length="hanchan",
            source="manual",
            data_completeness="result_only",
            seats=_seats(),
            final_scores=[42000, 18000, 25000, 15000],
        ),
        registry,
    )


def _rich_hands():
    """三个事件局：nick@01(seat0) 荣和、他人自摸、流局听牌。"""
    return [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000, 25000, 25000, 25000],
            "winners": [{"actor": 0, "win_type": "ron", "target": 1, "deltas": [5000, -5000, 0, 0]}],
            "ryukyoku": None, "riichi": [0], "calls": [1, 0, 0, 0],
        },
        {
            "bakaze": "E", "kyoku": 2, "honba": 0, "oya": 1,
            "scores_before": [30000, 20000, 25000, 25000],
            "winners": [{"actor": 1, "win_type": "tsumo", "target": 1, "deltas": [1000, 3000, -2000, -2000]}],
            "ryukyoku": None, "riichi": [1], "calls": [0, 0, 0, 0],
        },
        {
            "bakaze": "E", "kyoku": 3, "honba": 0, "oya": 2,
            "scores_before": [31000, 23000, 23000, 23000],
            "winners": [],
            "ryukyoku": {"reason": "ryukyoku", "deltas": [0, 0, 0, 0], "tenpai": [True, False, False, False]},
            "riichi": [], "calls": [0, 0, 0, 0],
        },
    ]


def _persist(log_id, hands):
    intake._write_artifact_files(
        intake.artifact_dir(log_id),
        tenhou6={},
        events=[],
        hands=hands,
        summary={"log_id": log_id, "names": ["Nick", "A", "B", "C"]},
    )


def test_stats_placement_from_all_matches(participants_root):
    _accounts()
    _full_replay_match()
    _result_only_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["schema"] == stats.STATS_CONTRACT_VERSION
    assert result["coverage"]["total_matches"] == 2
    assert result["coverage"]["matches_with_results"] == 2
    assert result["coverage"]["matches_with_hands"] == 1
    assert result["coverage"]["matches_with_full_replay"] == 1
    assert result["placement"]["match_count"] == 2
    # 第一场 nick 顺位 1（30000 最高），第二场 nick 顺位 1（42000 最高）
    assert result["placement"]["first_rate"] == 1.0
    assert result["placement"]["second_rate"] == 0.0
    assert result["placement"]["avg_rank"] == 1.0
    assert result["placement"]["avg_final_score"] == (30000 + 42000) / 2


def test_stats_detailed_from_full_replay(participants_root):
    _accounts()
    _persist("log1", _rich_hands())
    _full_replay_match()
    _result_only_match()

    result = stats.compute_account_stats("nick@01", registry, ledger)
    detailed = result["detailed"]
    # nick@01 (seat0)：3 局，1 次荣和（5000），0 次放铳，1 次立直，1 局副露，流局听牌 1/1
    assert detailed["hands_played"] == 3
    assert detailed["wins"] == 1
    assert detailed["dealins"] == 0
    assert detailed["win_rate"] == round(1 / 3, 4)
    assert detailed["dealin_rate"] == 0.0
    assert detailed["tsumo_share"] == 0.0  # 1 次和牌是荣和
    assert detailed["riichi_rate"] == round(1 / 3, 4)
    assert detailed["call_rate"] == round(1 / 3, 4)
    assert detailed["tenpai_rate"] == 1.0
    assert detailed["avg_win_points"] == 5000.0
    assert detailed["oya_win_rate"] == 1.0  # 第 1 局 oya=0（nick）
    assert detailed["koshu_win_rate"] == 0.0  # 第 2/3 局非庄，未和牌


def test_stats_detailed_handles_dealin(participants_root):
    _accounts()
    # nick@01 放铳给 70k@01
    hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000, 25000, 25000, 25000],
            "winners": [{"actor": 1, "win_type": "ron", "target": 0, "deltas": [-8000, 8000, 0, 0]}],
            "ryukyoku": None, "riichi": [], "calls": [0, 0, 0, 0],
        }
    ]
    _persist("log1", hands)
    _full_replay_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["detailed"]["dealins"] == 1
    assert result["detailed"]["avg_dealin_points"] == 8000.0
    assert result["detailed"]["win_rate"] == 0.0


def test_stats_result_only_no_detailed(participants_root):
    """result_only 比赛不得进入详细指标分母。"""
    _accounts()
    _result_only_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["coverage"]["matches_with_full_replay"] == 0
    assert result["detailed"]["hands_played"] == 0
    assert result["detailed"]["win_rate"] is None
    assert result["detailed"]["riichi_rate"] is None


def test_hand_summaries_capture_riichi_calls_tenpai():
    """扩展后的逐局摘要含 riichi/calls/tenpai（R10-G 输入）。"""
    events = [
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0, "scores": [25000] * 4},
        {"type": "reach", "actor": 0},
        {"type": "pon", "actor": 1, "target": 0},
        {"type": "hora", "actor": 0, "target": 1, "deltas": [5000, -5000, 0, 0]},
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 2, "honba": 0, "oya": 1, "scores": [30000, 20000, 25000, 25000]},
        {"type": "ryukyoku", "reason": "ryukyoku", "deltas": [0, 0, 0, 0], "tenpai": [True, False, False, False]},
    ]
    hands = intake.hand_summaries(events)
    assert hands[0]["riichi"] == [0]
    assert hands[0]["calls"] == [0, 1, 0, 0]
    assert hands[1]["ryukyoku"]["tenpai"] == [True, False, False, False]


# ---------------------------------------------------------------------------
# R10-G Repair 1：全不听分母 / 双响放铳 / 暗杠副露 / legacy 重建
# ---------------------------------------------------------------------------

def test_tenhou6_all_false_tenpai_preserved():
    """P1-1：四家全不听的流局 → tenpai=[False]*4 保留（已知不听，非未知）。"""
    kyoku = [[0, 0, 0], [25000] * 4, [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["流局", [0, 0, 0, 0], [0, 0, 0, 0]]]
    tenhou6 = {"name": ["A", "B", "C", "D"], "rule": {"aka": True}, "log": [kyoku]}
    events = intake.tenhou6_events(tenhou6)
    hands = intake.hand_summaries(events)
    assert hands[0]["ryukyoku"]["tenpai"] == [False, False, False, False]


def test_stats_all_false_tenpai_counts_denominator(participants_root):
    """P1-1：全不听流局进入听牌率分母（1 听牌 + 1 全不听 = 50%，非 100%）。"""
    _accounts()
    hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4, "winners": [],
            "ryukyoku": {"reason": "ryukyoku", "deltas": [0, 0, 0, 0], "tenpai": [True, False, False, False]},
            "riichi": [], "calls": [0, 0, 0, 0],
        },
        {
            "bakaze": "E", "kyoku": 2, "honba": 0, "oya": 1,
            "scores_before": [25000] * 4, "winners": [],
            "ryukyoku": {"reason": "ryukyoku", "deltas": [0, 0, 0, 0], "tenpai": [False, False, False, False]},
            "riichi": [], "calls": [0, 0, 0, 0],
        },
    ]
    _persist("log1", hands)
    _full_replay_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["coverage"]["ryukyoku_with_tenpai"] == 2
    assert result["detailed"]["tenpai_rate"] == 0.5


def test_stats_double_ron_single_dealin(participants_root):
    """P1-2：双响点炮两家 → 只算一次放铳，平均放铳点为总损失。"""
    _accounts()
    hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4,
            "winners": [
                {"actor": 1, "win_type": "ron", "target": 0, "deltas": [-3900, 3900, 0, 0]},
                {"actor": 2, "win_type": "ron", "target": 0, "deltas": [-8000, 0, 8000, 0]},
            ],
            "ryukyoku": None, "riichi": [], "calls": [0, 0, 0, 0],
        }
    ]
    _persist("log1", hands)
    _full_replay_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["detailed"]["dealins"] == 1
    assert result["detailed"]["dealin_rate"] == 1.0
    assert result["detailed"]["avg_dealin_points"] == 11900.0


def test_hand_summaries_ankan_not_call():
    """P1-3：暗杠（ankan）不计入副露（门清不被破坏）。"""
    events = [
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0, "scores": [25000] * 4},
        {"type": "ankan", "actor": 0, "consumed": ["1m", "1m", "1m", "1m"]},
    ]
    hands = intake.hand_summaries(events)
    assert hands[0]["calls"] == [0, 0, 0, 0]
    events2 = [
        {"type": "start_kyoku", "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0, "scores": [25000] * 4},
        {"type": "pon", "actor": 0, "target": 1, "pai": "3p", "consumed": ["3p", "3p"]},
    ]
    hands2 = intake.hand_summaries(events2)
    assert hands2[0]["calls"] == [1, 0, 0, 0]


def test_stats_ankan_only_zero_call_rate(participants_root):
    """P1-3：整局门清只有暗杠 → 副露率 = 0。"""
    _accounts()
    hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4, "winners": [],
            "ryukyoku": None, "riichi": [], "calls": [0, 0, 0, 0],  # 仅暗杠不计
        }
    ]
    _persist("log1", hands)
    _full_replay_match()
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["detailed"]["call_rate"] == 0.0


def test_legacy_artifact_lazy_reconstruction(participants_root):
    """P2-1：旧 full replay（hands 缺 G 字段）→ 从 tenhou6 内存重建。"""
    import json as _json

    _accounts()
    log_id = "log1"
    directory = intake.artifact_dir(log_id)
    directory.mkdir(parents=True, exist_ok=True)
    # 旧格式 hands：只有 winners，无 riichi/calls/tenpai
    old_hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4,
            "winners": [{"actor": 0, "win_type": "ron", "target": 1, "deltas": [5000, -5000, 0, 0]}],
            "ryukyoku": None,
        }
    ]
    (directory / "hands.jsonl").write_text(
        "\n".join(_json.dumps(h, ensure_ascii=False) for h in old_hands), encoding="utf-8"
    )
    kyoku = [[0, 0, 0], [25000] * 4, [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]]
    (directory / "tenhou6.json").write_text(
        _json.dumps({"name": ["Nick", "A", "B", "C"], "rule": {"aka": True}, "log": [kyoku]}), encoding="utf-8"
    )
    (directory / "summary.json").write_text(_json.dumps({"log_id": log_id}), encoding="utf-8")
    _full_replay_match(log_id)

    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["coverage"]["hands_used"] == 1
    assert result["detailed"]["wins"] == 1
    # 重建后 riichi/calls 字段存在 → 立直率/副露率可算（分母 1，均为 0）
    assert result["detailed"]["riichi_rate"] == 0.0
    assert result["detailed"]["call_rate"] == 0.0


# ---------------------------------------------------------------------------
# R10-G Repair 2：legacy 重建优先原始 Tenhou6（恢复历史 tenpai）
# ---------------------------------------------------------------------------

def _legacy_kyoku(result):
    return [[0, 0, 0], [25000] * 4, [], [], [], [], [], [], [], [], [], [], [], [], [], [], result]


def test_legacy_reconstruction_prefers_raw_tenhou6(participants_root):
    """P2：真实旧目录（old hands + 旧 events 无 tenpai + tenhou6 有 tenpai）→
    重建优先 tenhou6，恢复流局 tenpai，统计分母正确。"""
    import json as _json

    _accounts()
    log_id = "log1"
    directory = intake.artifact_dir(log_id)
    directory.mkdir(parents=True, exist_ok=True)
    # 旧 hands（无 riichi/calls/tenpai）
    old_hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4, "winners": [],
            "ryukyoku": {"reason": "ryukyoku", "deltas": [0, 0, 0, 0]},  # 旧格式：无 tenpai
        }
    ]
    (directory / "hands.jsonl").write_text(_json.dumps(old_hands[0], ensure_ascii=False) + "\n", encoding="utf-8")
    # 旧 events：ryukyoku 无 tenpai（若被优先使用则无法恢复）
    (directory / "events.jsonl").write_text(
        _json.dumps({"type": "ryukyoku", "reason": "ryukyoku", "deltas": [0, 0, 0, 0]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # 原始 tenhou6：流局听牌 [1,0,0,0]
    tenhou6 = {"name": ["Nick", "A", "B", "C"], "rule": {"aka": True}, "log": [_legacy_kyoku(["流局", [0, 0, 0, 0], [1, 0, 0, 0]])]}
    (directory / "tenhou6.json").write_text(_json.dumps(tenhou6, ensure_ascii=False), encoding="utf-8")
    (directory / "summary.json").write_text(_json.dumps({"log_id": log_id}), encoding="utf-8")
    _full_replay_match(log_id)

    hands = intake.rich_hands_for_artifact(log_id)
    assert hands[0]["ryukyoku"]["tenpai"] == [True, False, False, False]
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["coverage"]["ryukyoku_with_tenpai"] == 1
    assert result["detailed"]["tenpai_rate"] == 1.0


def test_g_era_artifact_missing_all_false_tenpai_still_upgraded(participants_root):
    """P2：初版-G artifact（已有 riichi/calls 但流局缺 tenpai）→ 仍触发升级恢复。"""
    import json as _json

    _accounts()
    log_id = "log1"
    directory = intake.artifact_dir(log_id)
    directory.mkdir(parents=True, exist_ok=True)
    # 已有 riichi/calls，但 ryukyoku 无 tenpai（全不听被旧 converter 省略）
    hands = [
        {
            "bakaze": "E", "kyoku": 1, "honba": 0, "oya": 0,
            "scores_before": [25000] * 4, "winners": [],
            "ryukyoku": {"reason": "ryukyoku", "deltas": [0, 0, 0, 0]},
            "riichi": [], "calls": [0, 0, 0, 0],
        }
    ]
    (directory / "hands.jsonl").write_text(_json.dumps(hands[0], ensure_ascii=False) + "\n", encoding="utf-8")
    # 原始 tenhou6：全不听 [0,0,0,0]
    tenhou6 = {"name": ["Nick", "A", "B", "C"], "rule": {"aka": True}, "log": [_legacy_kyoku(["流局", [0, 0, 0, 0], [0, 0, 0, 0]])]}
    (directory / "tenhou6.json").write_text(_json.dumps(tenhou6, ensure_ascii=False), encoding="utf-8")
    (directory / "summary.json").write_text(_json.dumps({"log_id": log_id}), encoding="utf-8")
    _full_replay_match(log_id)

    assert intake._needs_hand_upgrade(hands) is True
    rebuilt = intake.rich_hands_for_artifact(log_id)
    assert rebuilt[0]["ryukyoku"]["tenpai"] == [False, False, False, False]
    result = stats.compute_account_stats("nick@01", registry, ledger)
    assert result["coverage"]["ryukyoku_with_tenpai"] == 1
    assert result["detailed"]["tenpai_rate"] == 0.0
