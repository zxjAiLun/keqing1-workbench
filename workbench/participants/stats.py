# -*- coding: utf-8 -*-
"""participants 账号详细统计（R10-G）。

- ``ledger`` 决定"哪些对局属于该账号 + 基础结果"（顺位/最终分数）；
- ``full replay artifact`` 决定牌谱屋式详细指标（和牌/放铳/立直/副露/听牌等）；
- 统计必须 **completeness-aware**：``result_only`` 比赛只进入顺位与最终分数指标；
  缺 replay 的指标不计入分母（绝不把未知当 0）；
- 结果带 ``stats_contract_version``，指标定义修正时可全量重算。
"""
from __future__ import annotations

from typing import Any

STATS_CONTRACT_VERSION = "keqing.participant.stats.v1"


def _seat_of(match, account_id: str) -> int | None:
    for seat in match.seats:
        if seat.account_id == account_id:
            return seat.seat
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def compute_account_stats(account_id: str, registry, ledger) -> dict[str, Any]:
    """账号详细统计（R10-G）。registry/ledger 由 API 注入保持签名兼容。"""
    from . import intake

    active = [
        m
        for m in ledger.list_matches(account_id=account_id).matches
        if m.status == "active"
    ]

    # ---- 基础指标（所有比赛，含 result_only） ----
    placements = [0, 0, 0, 0]
    total_final = 0
    matches_with_hands = 0
    matches_with_full_replay = 0
    for match in active:
        seat = _seat_of(match, account_id)
        if seat is None:
            continue
        placements[match.ranks[seat]] += 1
        total_final += match.final_scores[seat]
        if match.data_completeness in ("hand_summary", "full_replay"):
            matches_with_hands += 1
        if match.data_completeness == "full_replay":
            matches_with_full_replay += 1

    # ---- 详细指标（仅 full_replay artifact） ----
    wins = 0
    dealins = 0
    tsumo = 0
    win_points = 0
    dealin_points = 0
    riichi_declared = 0
    hands_with_call = 0
    tenpai_count = 0
    oya_hands = 0
    koshu_hands = 0
    oya_wins = 0
    koshu_wins = 0
    hands_with_detail = 0  # 有事件数据的局数（和牌/放铳分母）
    hands_with_riichi = 0  # 有 riichi 字段的局数（立直率分母）
    hands_with_call_field = 0  # 有 calls 字段的局数（副露率分母）
    ryukyoku_with_tenpai = 0  # 有听牌标记的流局数（听牌率分母）

    for match in active:
        if match.data_completeness != "full_replay" or not match.replay_id:
            continue
        seat = _seat_of(match, account_id)
        if seat is None:
            continue
        artifact = intake.read_replay_artifact(match.replay_id)
        if artifact is None:
            continue
        hands = intake.rich_hands_for_artifact(match.replay_id)
        for hand in hands:
            hands_with_detail += 1
            is_oya = hand.get("oya") == seat
            if is_oya:
                oya_hands += 1
            else:
                koshu_hands += 1
            for winner in hand.get("winners", []):
                if winner.get("actor") == seat:
                    wins += 1
                    if winner.get("win_type") == "tsumo":
                        tsumo += 1
                    win_points += int(winner["deltas"][seat])
                    if is_oya:
                        oya_wins += 1
                    else:
                        koshu_wins += 1
            # P1-2（R10-G Repair1）：放铳按局聚合——双响点炮两家只算一次放铳，
            # 平均放铳点取该局对放铳方的总损失。
            ron_targets = [
                winner
                for winner in hand.get("winners", [])
                if winner.get("target") == seat
            ]
            if ron_targets:
                dealins += 1
                dealin_points += sum(-int(w["deltas"][seat]) for w in ron_targets)
            if "riichi" in hand:
                hands_with_riichi += 1
                riichi_declared += hand["riichi"].count(seat)
            if "calls" in hand:
                hands_with_call_field += 1
                if hand["calls"][seat] > 0:
                    hands_with_call += 1
            if hand.get("ryukyoku"):
                tenpai = (hand["ryukyoku"] or {}).get("tenpai")
                if tenpai is not None:
                    ryukyoku_with_tenpai += 1
                    if tenpai[seat]:
                        tenpai_count += 1

    rank_sum = sum((index + 1) * count for index, count in enumerate(placements))
    return {
        "schema": STATS_CONTRACT_VERSION,
        "account_id": account_id,
        "coverage": {
            "total_matches": len(active),
            "matches_with_results": len(active),
            "matches_with_hands": matches_with_hands,
            "matches_with_full_replay": matches_with_full_replay,
            "hands_used": hands_with_detail,
            "hands_with_riichi_field": hands_with_riichi,
            "hands_with_call_field": hands_with_call_field,
            "ryukyoku_with_tenpai": ryukyoku_with_tenpai,
        },
        "placement": {
            "match_count": len(active),
            "first_rate": _rate(placements[0], len(active)),
            "second_rate": _rate(placements[1], len(active)),
            "third_rate": _rate(placements[2], len(active)),
            "fourth_rate": _rate(placements[3], len(active)),
            "avg_rank": round(rank_sum / len(active), 3) if active else None,
            "avg_final_score": round(total_final / len(active), 1) if active else None,
        },
        "detailed": {
            "hands_played": hands_with_detail,
            "wins": wins,
            "dealins": dealins,
            "win_rate": _rate(wins, hands_with_detail),
            "dealin_rate": _rate(dealins, hands_with_detail),
            "tsumo_share": _rate(tsumo, wins),
            "riichi_rate": _rate(riichi_declared, hands_with_riichi),
            "call_rate": _rate(hands_with_call, hands_with_call_field),
            "tenpai_rate": _rate(tenpai_count, ryukyoku_with_tenpai),
            "avg_win_points": round(win_points / wins, 1) if wins else None,
            "avg_dealin_points": round(dealin_points / dealins, 1) if dealins else None,
            "oya_win_rate": _rate(oya_wins, oya_hands),
            "koshu_win_rate": _rate(koshu_wins, koshu_hands),
        },
    }


__all__ = ["compute_account_stats", "STATS_CONTRACT_VERSION"]
