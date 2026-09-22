"""tenhou6 里宝牌指示牌与原生结算的回归。

锚点是真实牌谱 tenhou:2026092213gm-0009-2147-1461ff1a 南1局（kyoku index 5）：
庄家 P0 立直后荣和 P1，天凤原生结算为
``"三倍満36000点" / 立直(1飜) 混全帯幺九(2飜) ドラ(4飜) 裏ドラ(5飜)``。

修复前该局的里宝牌指示牌（tenhou6 的 ``kyoku[3]``）从未被读取，
本地只算出 7 番 12000 点，且 ``yaku_level`` 恒为 ``mangan``。
"""

from __future__ import annotations

from typing import List

from convert.tenhou6_utils import _parse_tenhou_result_fields, _result_events, _start_kyoku_event
from mahjong_env.scoring import canonical_yaku_level

# --- 天凤牌谱的字段形状（取自上述真实牌谱） --------------------------------

# kyoku[0]=[局番, 本场, 供托]；kyoku[2]=[表宝牌指示牌, 杠宝牌指示牌...]；
# kyoku[3]=[里宝牌指示牌, 杠里宝牌指示牌...]。18=8m 16=6m 28=8p。
KYOKU_DORA_CODES = [18, 16]
KYOKU_URA_CODES = [28, 18]

REAL_RESULT = [
    "和了",
    [37000, -36000, 0, 0],
    [0, 1, 0, "三倍満36000点", "立直(1飜)", "混全帯幺九(2飜)", "ドラ(4飜)", "裏ドラ(5飜)"],
]


def _kyoku_with_ura() -> List[object]:
    """最小 tenhou6 局骨架：meta / scores / dora / ura / 四家配牌。"""
    return [
        [5, 0, 0],
        [19400, 37600, 20100, 22900],
        list(KYOKU_DORA_CODES),
        list(KYOKU_URA_CODES),
        [19, 19, 19, 22, 27, 28, 32, 35, 37, 42, 45, 45, 47],
        [24, 39, 23, 21, 19, 38, 53, 15],
        [32, 42, 47, 35, "191919a19", "r24", 60, 60],
        [12, 14, 16, 16, 18, 24, 26, 27, 28, 31, 32, 33, 41],
        [11, 17, 13, 51, 29, 13, 34],
        [41, 11, 24, 12, 32, 31, 29],
        [11, 14, 15, 22, 24, 27, 31, 36, 44, 44, 44, 46, 47],
        [39, 38, 43, 34, 15, 12],
        [60, 11, 31, 47, 24, 44],
        [13, 25, 52, 32, 32, 35, 38, 41, 42, 43, 44, 46, 47],
        [33, 33, 25, 41, 18, 17],
        [41, 43, 42, 60, 47, 44],
        list(REAL_RESULT),
    ]


def test_start_kyoku_carries_ura_dora_indicators() -> None:
    """kyoku[3] 必须被读出，缺失时是空列表而不是省略字段。"""
    event = _start_kyoku_event(_kyoku_with_ura(), [], {})

    assert event["dora_marker"] == "8m"
    assert event["ura_dora_markers"] == ["8p", "8m"]

    no_ura = _kyoku_with_ura()
    no_ura[3] = []
    assert _start_kyoku_event(no_ura, [], {})["ura_dora_markers"] == []


def test_ura_indicators_only_apply_to_riichi_win() -> None:
    """里宝牌指示牌整局透传；是否计入由打分层的立直守卫决定。"""
    events = _result_events(REAL_RESULT, ura_dora_markers=["8p", "8m"])

    assert len(events) == 1
    assert events[0]["ura_dora_markers"] == ["8p", "8m"]
    # 未传时仍是显式空列表，避免下游把缺失当未知。
    assert _result_events(REAL_RESULT)[0]["ura_dora_markers"] == []


def test_result_event_keeps_tenhou_native_settlement() -> None:
    """天凤原生等级/点数/役种必须原样保留（本地重算可能少算里宝牌）。"""
    event = _result_events(REAL_RESULT, ura_dora_markers=["8p", "8m"])[0]

    assert event["result_label"] == "三倍満36000点"
    assert event["is_limit"] is True
    assert event["han"] == 12  # 1 + 2 + 4 + 5
    assert event["yaku"] == ["立直", "混全帯幺九", "ドラ", "裏ドラ"]
    assert event["yaku_details"] == [
        {"key": "Riichi", "name": "立直", "han": 1},
        {"key": "Chantai", "name": "混全帯幺九", "han": 2},
        {"key": "Dora", "name": "ドラ", "han": 4},
        {"key": "Ura Dora", "name": "裏ドラ", "han": 5},
    ]


def test_non_limit_result_has_no_limit_flag() -> None:
    """普通手不标限制；等级仍由番数决定。"""
    parsed = _parse_tenhou_result_fields([3, 2, 3, "40符1飜1300点", "断幺九(1飜)"])

    assert parsed["result_label"] == "40符1飜1300点"
    assert "is_limit" not in parsed
    assert parsed["han"] == 1


def test_tsumo_mangan_label_is_recognized_as_limit() -> None:
    """自摸结算串带 '∀' 后缀，仍须识别为限制手。"""
    parsed = _parse_tenhou_result_fields(
        [1, 1, 1, "満貫4000点∀", "立直(1飜)", "一発(1飜)", "門前清自摸和(1飜)", "ドラ(1飜)", "赤ドラ(1飜)"]
    )

    assert parsed["is_limit"] is True
    assert parsed["han"] == 5


def test_canonical_yaku_level_from_han() -> None:
    """原生 adapter 对所有限制手恒返回 mangan，必须按番数重算等级。"""
    # 真实 bug：12 番三倍满被标成 mangan。
    assert canonical_yaku_level(12, {"yaku_level": "mangan"}) == "sanbaiman"
    assert canonical_yaku_level(7, {"yaku_level": "mangan"}) == "haneman"
    assert canonical_yaku_level(5, {"yaku_level": "mangan"}) == "mangan"
    assert canonical_yaku_level(8, {"yaku_level": "mangan"}) == "baiman"
    assert canonical_yaku_level(11, {"yaku_level": "mangan"}) == "sanbaiman"
    assert canonical_yaku_level(13, {"yaku_level": "mangan"}) == "yakuman"
    assert canonical_yaku_level(26, {"yaku_level": "mangan"}) == "2x yakuman"
    # 未达满贯时保留打分器给的等级（通常是空串）。
    assert canonical_yaku_level(3, {"yaku_level": ""}) == ""
    assert canonical_yaku_level(1, {}) == ""
