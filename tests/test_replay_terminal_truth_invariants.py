# -*- coding: utf-8 -*-
"""终局结算三层不变量回归：
1. 外部牌谱的 authoritative terminal truth 不被本地 scorer 覆盖（只补缺口）。
2. terminal obs 条目的 ``chosen == gt_action == event`` 不变量不被破坏。
3. 普通宝牌（脱离里宝牌）能被正确计入番数。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _path in (PROJECT_ROOT, PROJECT_ROOT / "src", PROJECT_ROOT / "workbench"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from workbench.replay.server import (  # noqa: E402
    _merge_terminal_event_details,
    _normalize_replay_events,
)


# --------------------------------------------------------------------------
# 1. authoritative terminal truth 不被本地 scorer 覆盖
# --------------------------------------------------------------------------


def _patch_score_hora(monkeypatch, *, han: int, main: int, deltas: list[int]) -> None:
    """把本地 scorer 换成故意算错/算低的版本，验证它不能污染权威事件。"""
    from mahjong_env.scoring import HoraResult

    def fake_score_hora(state, *, actor, target, pai, is_tsumo, ura_dora_markers=None, **kwargs):
        return HoraResult(
            han=han,
            fu=30,
            yaku=["Riichi"],
            yaku_details=[{"key": "Riichi", "name": "Riichi", "han": 1}],
            is_open_hand=False,
            cost={
                "main": main,
                "main_bonus": 0,
                "additional": 0,
                "additional_bonus": 0,
                "kyotaku_bonus": 0,
                "total": main,
                "yaku_level": "",
            },
            deltas=list(deltas),
        )

    import mahjong_env.scoring as scoring

    monkeypatch.setattr(scoring, "score_hora", fake_score_hora)


def test_authoritative_terminal_deltas_survive_local_rescore(monkeypatch) -> None:
    """外部牌谱的 deltas 是真值：即使本地 scorer 算出 3000，也必须保留 8000。"""
    _patch_score_hora(monkeypatch, han=1, main=3000, deltas=[0, -3000, 0, 3000])

    events = [
        {"type": "start_game", "names": ["P0", "P1", "P2", "P3"]},
        {
            "type": "hora",
            "actor": 1,
            "target": 0,
            "pai": "2m",
            "is_tsumo": False,
            # 天凤原生结算串 = 外部权威标记
            "result_label": "満貫8000点",
            "han": 3,
            "fu": 30,
            "yaku": ["Riichi", "Dora"],
            "yaku_details": [
                {"key": "Riichi", "name": "Riichi", "han": 1},
                {"key": "Dora", "name": "Dora", "han": 2},
            ],
            "deltas": [0, -8000, 0, 8000],
            "cost": {"main": 8000, "total": 8000, "yaku_level": "mangan"},
        },
    ]

    hora = _normalize_replay_events(events)[-1]

    # 权威字段一律保留
    assert hora["deltas"] == [0, -8000, 0, 8000]
    assert hora["han"] == 3
    assert hora["yaku"] == ["Riichi", "Dora"]


def test_authoritative_scores_derived_from_authoritative_deltas(monkeypatch) -> None:
    """scores 缺失时，必须由权威 deltas 推得，不能混入本地重算的 deltas。"""
    _patch_score_hora(monkeypatch, han=1, main=3000, deltas=[0, -3000, 0, 3000])

    events = [
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": 0,
            "scores": [25000, 25000, 25000, 25000],
            "dora_marker": "1m",
            "tehais": [["1m"] * 13, ["2m"] * 13, ["3m"] * 13, ["4m"] * 13],
        },
        {
            "type": "hora",
            "actor": 1,
            "target": 0,
            "pai": "2m",
            "is_tsumo": False,
            "result_label": "満貫8000点",
            "han": 3,
            "yaku": ["Riichi"],
            "deltas": [0, -8000, 0, 8000],
        },
    ]

    hora = _normalize_replay_events(events)[-1]

    assert hora["deltas"] == [0, -8000, 0, 8000]
    # scores 必须由采用后的权威 deltas 推得（而非本地重算的 3000）
    assert hora["scores"] == [25000, 17000, 25000, 33000]


def test_local_rescore_still_fills_non_authoritative_hora(monkeypatch) -> None:
    """无原生结算串的牌谱，仍由本地重算填充（保留 Ippatsu 等本地修正）。"""
    _patch_score_hora(monkeypatch, han=2, main=2000, deltas=[-2000, 2000, 0, 0])

    events = [
        {"type": "start_game", "names": ["P0", "P1", "P2", "P3"]},
        {
            "type": "hora",
            "actor": 1,
            "target": 0,
            "pai": "2m",
            "is_tsumo": False,
            "han": 1,
            "yaku": ["Riichi"],
            "deltas": [-1000, 1000, 0, 0],
        },
    ]

    hora = _normalize_replay_events(events)[-1]

    # 无 result_label => 非外部权威 => 本地重算覆盖
    assert hora["han"] == 2
    assert hora["deltas"] == [-2000, 2000, 0, 0]


def test_authoritative_ura_markers_preserved_under_mjai_alias(monkeypatch) -> None:
    """事件用 mjai 习惯名 ura_markers 携带里宝牌时，不能被丢弃。"""
    _patch_score_hora(monkeypatch, han=1, main=3000, deltas=[0, -3000, 0, 3000])

    events = [
        {"type": "start_game", "names": ["P0", "P1", "P2", "P3"]},
        {
            "type": "hora",
            "actor": 3,
            "target": 1,
            "pai": "1m",
            "is_tsumo": False,
            "result_label": "三倍満36000点",
            "han": 12,
            "deltas": [0, -18600, 0, 20600],
            "ura_markers": ["7m", "7p"],
        },
    ]

    hora = _normalize_replay_events(events)[-1]

    assert hora["ura_dora_markers"] == ["7m", "7p"]
    assert hora["deltas"] == [0, -18600, 0, 20600]


# --------------------------------------------------------------------------
# 2. terminal obs 的 chosen == gt_action 不变量
# --------------------------------------------------------------------------


@pytest.mark.parametrize("terminal", ["hora", "ryukyoku"])
def test_terminal_obs_chosen_equals_gt_action(terminal: str) -> None:
    """obs 观察步的 chosen/gt_action 必须同为事件副本（bot.py 创建时不变量）。"""
    if terminal == "hora":
        event = {
            "type": "hora",
            "actor": 3,
            "target": 1,
            "pai": "1m",
            "is_tsumo": False,
            "han": 12,
            "fu": 70,
            "deltas": [0, -18600, 0, 20600],
            "scores": [33400, 7400, 27400, 31800],
            "ura_dora_markers": ["7m", "7p"],
        }
    else:
        event = {
            "type": "ryukyoku",
            "deltas": [0, -1000, 3000, -2000],
            "scores": [25000, 24000, 28000, 23000],
            "reason": "exhaustive_draw",
            "tenpai": [False, False, True, False],
        }

    # obs entry：chosen 被污染成与事件不一致（模拟既有 bug）
    stale_chosen = {**event, "deltas": [0, -3000, 0, 3000]}
    decisions = {
        "log": [
            {
                "step": 302,
                "is_obs": True,
                "obs_kind": "terminal",
                "source_event_index": 0,
                "chosen": stale_chosen,
                "gt_action": {**event, "deltas": [0, -3000, 0, 3000]},
            }
        ]
    }

    merged = _merge_terminal_event_details(decisions, [event])
    entry = merged["log"][0]

    assert entry["chosen"] == event
    assert entry["gt_action"] == event
    # 必须是两份独立副本，避免别名互相污染
    assert entry["chosen"] is not entry["gt_action"]
    entry["chosen"]["deltas"] = [9, 9, 9, 9]
    assert entry["gt_action"]["deltas"] == event["deltas"]


def test_non_obs_decision_keeps_chosen_gt_action_distinction() -> None:
    """自家真实决策步：gt_action 是事件真值，chosen 是模型选择，不得被覆盖。"""
    event = {
        "type": "hora",
        "actor": 1,
        "target": 0,
        "pai": "2m",
        "is_tsumo": False,
        "han": 3,
        "deltas": [0, -8000, 0, 8000],
        "scores": [25000, 17000, 25000, 33000],
    }
    model_choice = {
        "type": "hora",
        "actor": 1,
        "target": 0,
        "pai": "2m",
        "is_tsumo": False,
        "deltas": [0, -1000, 0, 1000],
    }
    decisions = {
        "log": [
            {
                "step": 40,
                "is_obs": False,
                "source_event_index": 0,
                "chosen": model_choice,
                "gt_action": {**event, "deltas": [0, -1000, 0, 1000]},
            }
        ]
    }

    merged = _merge_terminal_event_details(decisions, [event])
    entry = merged["log"][0]

    # gt_action 无条件等于事件真值
    assert entry["gt_action"] == event
    # chosen 保留模型自己的动作字段（deltas 非 None，不被事件覆盖）
    assert entry["chosen"]["deltas"] == [0, -1000, 0, 1000]
    # 但结算展示字段（han 等）允许补上
    assert entry["chosen"]["han"] == 3


# --------------------------------------------------------------------------
# 3. 普通宝牌（脱离里宝牌）必须进入番数
# --------------------------------------------------------------------------


def test_ordinary_dora_counted_without_riichi_or_ura() -> None:
    """最小普通宝牌和牌：无立直、里宝牌为空，普通宝牌必须 +N 番。

    锁死 result.han = base_yaku_han + ordinary_dora，防止未来把普通宝牌
    与里宝牌耦合成同一路径而回归（历史上曾用「立直+暗杠+里宝牌」的复合用例
    作唯一回归，修一项会被另一项顶绿）。
    """
    from mahjong_env.scoring import score_hora
    from mahjong_env.state import GameState

    def build_state() -> GameState:
        state = GameState()
        state.oya = 0
        state.bakaze = "E"
        state.kyoku = 1
        state.honba = 0
        state.kyotaku = 0
        state.scores = [25000, 25000, 25000, 25000]
        # P0 手牌：123m 456m 789m 123p + 荣和 5p 成 55p 雀头（一气通贯，无立直）
        hand = ["1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p", "3p", "5p"]
        for tile in hand:
            state.players[0].hand[tile] += 1
        # dora 指示牌 4p => 宝牌 5p；P0 手里有 5p 一张（荣和的那张也是 5p）
        state.dora_markers = ["4p"]
        return state

    state = build_state()
    result = score_hora(
        state,
        actor=0,
        target=1,
        pai="5p",
        is_tsumo=False,
        ura_dora_markers=[],
    )
    assert result.han > 0, "该手应有合法役"
    dora_details = [d for d in result.yaku_details if d.get("key") == "Dora"]
    assert dora_details, f"普通宝牌（指示牌 4p → 宝牌 5p）必须计入，实际 yaku={result.yaku}"
    dora_han = sum(int(d.get("han") or 0) for d in dora_details)
    assert dora_han >= 1

    # 对照：去掉 dora 指示牌，番数必须严格下降
    state_no_dora = build_state()
    state_no_dora.dora_markers = []
    baseline = score_hora(
        state_no_dora,
        actor=0,
        target=1,
        pai="5p",
        is_tsumo=False,
        ura_dora_markers=[],
    )
    assert result.han == baseline.han + dora_han, (
        f"番数必须满足 base + ordinary_dora："
        f"有宝牌 {result.han} vs 无宝牌 {baseline.han} + dora {dora_han}"
    )
    # 该用例完全不涉及立直与里宝牌，锁死普通宝牌独立可用
    assert result.yaku.count("Riichi") == 0
    assert not [d for d in (result.yaku_details or []) if d.get("key") == "Ura Dora"]
