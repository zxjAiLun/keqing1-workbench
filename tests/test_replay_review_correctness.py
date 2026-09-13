# -*- coding: utf-8 -*-
"""Review correctness 回归测试（统计虚高 / 差异漏报的根因）。

覆盖四类已确认缺陷：
1. ``_merge_terminal_event_details`` 曾用 ``{**action, **event}`` 把模型决策
   (``chosen``) 覆盖成"玩家实际打出的牌" → 一致率/类似度虚高、多个模型数字完全相同；
2. runtime teacher report 曾丢弃"玩家选择过、模型想鸣牌/荣和"的真实响应决策
   （``decision_kind == "pass"``）→ 直接少掉约 20% 的可比决策；
3. GUI 多模型 Review 曾允许 ``resolve_bot_spec("mortal")`` 在 V2 缺失时静默 alias 到 70k；
4. teacher report 未写 ``expected_prob`` / ``actual_prob``，前端只能拿 top1 概率冒充。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from replay.server import (
    _build_runtime_teacher_report,
    _load_teacher_report_entries,
    _merge_terminal_event_details,
    _review_checkpoint_for_bot_type,
)

PLAYER_ID = 1


def _dahai(pai: str, *, tsumogiri: bool = False) -> dict:
    return {"type": "dahai", "actor": PLAYER_ID, "pai": pai, "tsumogiri": tsumogiri}


def _own_entry(*, step: int, chosen: dict, gt_action: dict | None, candidates: list[dict] | None = None) -> dict:
    return {
        "step": step,
        "bakaze": "E",
        "kyoku": 1,
        "honba": 0,
        "is_obs": False,
        "chosen": chosen,
        "gt_action": gt_action,
        "candidates": candidates or [],
    }


# --------------------------------------------------------------------------
# 1. 普通动作事件不得改写任何一侧决策；只有终局事件补结算细节
# --------------------------------------------------------------------------


def test_normal_action_event_leaves_decisions_untouched():
    """模型打 E、玩家实际打 S（同为 dahai）→ 两者都不被事件改写（含不能凭事件补字段）。"""
    decisions = {"log": [_own_entry(step=9, chosen=_dahai("E"), gt_action=_dahai("S"))]}
    decisions["log"][0]["source_event_index"] = 3
    before = json.loads(json.dumps(decisions))
    events = [{"type": "start_game"}, {"type": "tsumo"}, {"type": "tsumo"}, _dahai("S", tsumogiri=True)]

    merged = _merge_terminal_event_details(decisions, events)

    assert merged["log"] == before["log"], "普通 dahai 事件不得改写 chosen/gt_action（统计失真根因回归）"
    assert merged["log"][0]["chosen"]["pai"] == "E"
    assert merged["log"][0]["gt_action"]["pai"] == "S"
    assert "deltas" not in merged["log"][0]["gt_action"], "普通事件不得带进结算字段"


def test_terminal_result_details_are_filled_without_overwriting_model_choice():
    """hora：han/fu/yaku/cost/deltas 等展示数据仍要补齐，但模型自己的牌不能被改。"""
    decisions = {"log": [_own_entry(
        step=12,
        chosen={"type": "hora", "actor": PLAYER_ID, "pai": "5m"},
        gt_action={"type": "hora", "actor": PLAYER_ID},
    )]}
    decisions["log"][0]["source_event_index"] = 5
    hora = {
        "type": "hora",
        "actor": PLAYER_ID,
        "target": 2,
        "pai": "5m",
        "hans": [{"han": 2, "yaku": "riichi"}],
        "fu": 40,
        "cost": 7700,
        "yaku": ["riichi"],
        "deltas": [7700, -3900, -3900, 0],
        "scores": [32700, 21100, 21100, 25000],
    }
    events = [None, None, None, None, None, hora]

    entry = _merge_terminal_event_details(decisions, events)["log"][0]

    for key in ("hans", "fu", "cost", "yaku", "deltas", "scores"):
        assert key in entry["chosen"], f"终局结算字段 {key} 应补进 chosen"
        assert key in entry["gt_action"], f"终局结算字段 {key} 应补进 gt_action"
    assert entry["chosen"]["pai"] == "5m" and entry["chosen"]["actor"] == PLAYER_ID, "模型决策字段不得被改写"
    assert entry["gt_action"]["target"] == 2

    # ryukyoku 同属终局事件，deltas/scores 同样要补齐（两份键都补）
    ryukyoku = {"type": "ryukyoku", "reason": "ryanmen", "deltas": [1000, -1000, -1000, -1000], "scores": [26000, 24000, 25000, 25000]}
    ryu_decisions = {"log": [_own_entry(
        step=20,
        chosen={"type": "ryukyoku", "actor": PLAYER_ID},
        gt_action={"type": "ryukyoku", "actor": PLAYER_ID},
    )]}
    ryu_decisions["log"][0]["source_event_index"] = 0
    ryu = _merge_terminal_event_details(ryu_decisions, [ryukyoku])["log"][0]
    assert ryu["gt_action"]["scores"] == [26000, 24000, 25000, 25000]
    assert ryu["chosen"]["deltas"] == [1000, -1000, -1000, -1000]


# --------------------------------------------------------------------------
# 2. 真实响应窗口的"过"必须进入 teacher report
# --------------------------------------------------------------------------


def _pass_opportunity_entry(*, step: int = 7, chosen_type: str = "pon") -> dict:
    pon_action = {"type": "pon", "actor": PLAYER_ID, "pai": "5m", "target": 2}
    hora_action = {"type": "hora", "actor": PLAYER_ID, "target": 2, "pai": "5m"}
    option = hora_action if chosen_type == "hora" else pon_action
    return _own_entry(
        step=step,
        chosen=option,
        gt_action={"type": "none", "actor": PLAYER_ID},
        candidates=[
            {"action": {"type": "none", "actor": PLAYER_ID}, "final_score": -0.1, "prob": 0.25},
            {"action": option, "final_score": 0.4, "prob": 0.75},
        ],
    )


def _report_entries(model: str, decisions: dict) -> list[dict]:
    report = _build_runtime_teacher_report(
        replay_id="replay_regression",
        model_type=model,
        player_id=PLAYER_ID,
        checkpoint=Path("/nonexistent/checkpoint.pth"),
        decisions=decisions,
    )
    return [entry for kyoku in report["review"]["kyokus"] for entry in kyoku["entries"]]


@pytest.mark.parametrize("chosen_type", ["pon", "hora"])
def test_response_pass_is_reported_as_real_decision(chosen_type):
    """玩家"过"/模型想碰**或想荣和**：必须挂载，且 expected=动作、actual=过 → 差异可见。"""
    entries = _report_entries("70k", {"player_id": PLAYER_ID, "log": [_pass_opportunity_entry(chosen_type=chosen_type)]})
    assert len(entries) == 1, "真实响应窗口的'过'被 drop（不对称漏报回归）"
    entry = entries[0]
    assert entry["decision_kind"] == "pass"
    assert entry["actor"] == PLAYER_ID
    assert entry["actual"] == {"type": "none", "actor": PLAYER_ID}
    assert entry["expected"]["type"] == chosen_type
    assert entry["is_equal"] is False
    assert len(entry["details"]) == 2


def test_player_hora_is_not_rewritten_into_a_pass():
    """玩家自己荣和：终局动作另有归属，不得被当成"过"（借none→pass路径改写 actual）。"""
    entry = _own_entry(
        step=8,
        chosen={"type": "hora", "actor": PLAYER_ID, "target": 2, "pai": "5m"},
        gt_action={"type": "hora", "actor": PLAYER_ID, "target": 2, "pai": "5m"},
        candidates=[
            {"action": {"type": "none", "actor": PLAYER_ID}, "final_score": -0.1, "prob": 0.25},
            {"action": {"type": "hora", "actor": PLAYER_ID, "target": 2, "pai": "5m"}, "final_score": 0.4, "prob": 0.75},
        ],
    )
    assert _report_entries("70k", {"player_id": PLAYER_ID, "log": [entry]}) == []


def test_auto_pass_without_call_option_is_not_reported():
    """没有鸣牌/荣和选项的自动过仍不算决策。"""
    decisions = {"player_id": PLAYER_ID, "log": [{
        "step": 4,
        "is_obs": False,
        "chosen": {"type": "none", "actor": PLAYER_ID},
        "gt_action": {"type": "none", "actor": PLAYER_ID},
        "candidates": [{"action": {"type": "none", "actor": PLAYER_ID}, "final_score": -0.1, "prob": 1.0}],
    }]}
    assert _report_entries("70k", decisions) == []


def test_terminal_actions_still_excluded():
    """hora/ryukyoku 等终局动作没有候选权重，仍不挂载。"""
    decisions = {"player_id": PLAYER_ID, "log": [{
        "step": 20,
        "is_obs": False,
        "chosen": {"type": "hora", "actor": PLAYER_ID, "target": 2},
        "gt_action": {"type": "hora", "actor": PLAYER_ID, "target": 2},
        "candidates": [],
    }]}
    assert _report_entries("70k", decisions) == []


# --------------------------------------------------------------------------
# 3. GUI Review checkpoint 严格解析（禁止静默 alias）
# --------------------------------------------------------------------------


def test_review_checkpoint_never_aliases_to_70k(monkeypatch):
    """V2 checkpoint 缺失时必须报错，而不是静默换成 70k 权重。"""
    import inference.bot_registry as bot_registry

    monkeypatch.setitem(bot_registry.MORTAL_CHECKPOINTS, "mortal", Path("V2_74000/definitely_absent.pth"))
    with pytest.raises(ValueError) as excinfo:
        _review_checkpoint_for_bot_type("mortal")
    message = str(excinfo.value)
    assert "unavailable" in message
    assert "V2 candidate" in message
    assert "70k" not in message, "报错信息不得暗示已回退到 70k"


def test_review_checkpoints_point_to_different_weights():
    """mortal(V2) 与 70k 必须是两个不同的权重，不能指向同一文件。"""
    from workbench.runtime.resolver import MORTAL_CHECKPOINTS

    mortal_path = Path(MORTAL_CHECKPOINTS["mortal"])
    anchor_path = Path(MORTAL_CHECKPOINTS["70k"])
    assert mortal_path != anchor_path
    assert mortal_path.name != anchor_path.name

    def available(bot_type: str) -> bool:
        try:
            _review_checkpoint_for_bot_type(bot_type)
            return True
        except ValueError:
            return False

    if not (available("mortal") and available("70k")):
        pytest.skip("authoritative checkpoints 不在此环境（CI）：已在上方校验路径不同")
    assert _review_checkpoint_for_bot_type("mortal") != _review_checkpoint_for_bot_type("70k")


# --------------------------------------------------------------------------
# 4. expected_prob / actual_prob 必须落到 overlay 数据里
# --------------------------------------------------------------------------


def test_teacher_overlay_carries_actual_and_expected_prob(tmp_path):
    report = _build_runtime_teacher_report(
        replay_id="replay_regression",
        model_type="70k",
        player_id=PLAYER_ID,
        checkpoint=Path("/nonexistent/checkpoint.pth"),
        decisions={"player_id": PLAYER_ID, "log": [_pass_opportunity_entry()]},
    )
    report_path = tmp_path / "70k.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    _tag, _player, entries = _load_teacher_report_entries(
        report_path, {"player_id": PLAYER_ID, "log": []},
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actual_prob"] == pytest.approx(0.25)
    assert entry["expected_prob"] == pytest.approx(0.75)
    assert entry["best_prob"] == pytest.approx(0.75)
