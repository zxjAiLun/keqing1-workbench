import copy
import re

from replay.normalize import normalize_replay_decisions


def test_normalize_replay_decisions_fills_explicit_none_gt_action():
    decisions = {
        "player_id": 2,
        "log": [
            {
                "is_obs": False,
                "chosen": {"type": "none"},
                "gt_action": None,
                "candidates": [
                    {"action": {"type": "none"}},
                    {"action": {"type": "pon", "actor": 2, "target": 1, "pai": "P", "consumed": ["P", "P"]}},
                ],
            },
            {
                "is_obs": True,
                "chosen": {"type": "dahai", "actor": 1, "pai": "P"},
                "gt_action": {"type": "dahai", "actor": 1, "pai": "P"},
            },
        ],
    }

    normalized = normalize_replay_decisions(decisions)

    assert normalized["log"][0]["gt_action"] == {"type": "none", "actor": 2}
    assert normalized["total_ops"] == 1
    assert normalized["match_count"] == 1


def test_normalize_replay_decisions_fills_response_gt_action_from_confirmed_action():
    decisions = {
        "player_id": 1,
        "log": [
            {
                "is_obs": False,
                "chosen": {
                    "type": "chi",
                    "actor": 1,
                    "target": 0,
                    "pai": "4p",
                    "consumed": ["5p", "6p"],
                },
                "gt_action": None,
                "candidates": [
                    {"action": {"type": "none"}},
                    {"action": {"type": "chi", "actor": 1, "target": 0, "pai": "4p", "consumed": ["5pr", "6p"]}},
                ],
            },
            {
                "is_obs": True,
                "chosen": {"type": "chi", "actor": 1, "target": 0, "pai": "4p", "consumed": ["5pr", "6p"]},
                "gt_action": {"type": "chi", "actor": 1, "target": 0, "pai": "4p", "consumed": ["5pr", "6p"]},
            },
        ],
    }

    normalized = normalize_replay_decisions(decisions, meta={"bot_type": "mortal"})

    assert normalized["log"][0]["gt_action"]["type"] == "chi"
    assert normalized["bot_type"] == "mortal"
    assert normalized["match_count"] == 1


def test_normalize_replay_decisions_marks_unconfirmed_response_as_none():
    decisions = {
        "player_id": 2,
        "log": [
            {
                "is_obs": False,
                "chosen": {
                    "type": "pon",
                    "actor": 2,
                    "target": 1,
                    "pai": "9s",
                    "consumed": ["9s", "9s"],
                },
                "gt_action": None,
                "last_discard": {"actor": 1, "pai": "9s"},
                "candidates": [
                    {"action": {"type": "pon", "actor": 2, "target": 1, "pai": "9s", "consumed": ["9s", "9s"]}},
                    {"action": {"type": "none"}},
                ],
            },
            {
                "is_obs": False,
                "chosen": {"type": "dahai", "actor": 2, "pai": "9s", "tsumogiri": False},
                "gt_action": {"type": "dahai", "actor": 2, "pai": "2m", "tsumogiri": False},
                "tsumo_pai": "7s",
            },
        ],
    }

    normalized = normalize_replay_decisions(decisions)

    assert normalized["log"][0]["gt_action"] == {"type": "none", "actor": 2}
    assert normalized["match_count"] == 0


def test_normalize_replay_decisions_exempts_chi_preempted_by_pon():
    decisions = {
        "player_id": 0,
        "log": [
            {
                "is_obs": False,
                "chosen": {
                    "type": "chi",
                    "actor": 0,
                    "target": 3,
                    "pai": "5s",
                    "consumed": ["6s", "7s"],
                },
                "gt_action": None,
                "candidates": [
                    {"action": {"type": "chi", "actor": 0, "target": 3, "pai": "5s"}},
                    {"action": {"type": "none"}},
                ],
            },
            {
                "is_obs": True,
                "chosen": {
                    "type": "pon",
                    "actor": 1,
                    "target": 3,
                    "pai": "5s",
                    "consumed": ["5s", "5sr"],
                },
                "gt_action": {
                    "type": "pon",
                    "actor": 1,
                    "target": 3,
                    "pai": "5s",
                    "consumed": ["5s", "5sr"],
                },
            },
        ],
    }

    normalized = normalize_replay_decisions(decisions)

    pending = normalized["log"][0]
    assert pending["gt_action"] == {"type": "none", "actor": 0}
    assert pending["comparison_exempt"] == "response_preempted"
    assert normalized["total_ops"] == 0
    assert normalized["match_count"] == 0


# ---------------------------------------------------------------------------
# _repair_kakan_snapshots：native 缓存快照的 kakan 修复（N1-N8）
# ---------------------------------------------------------------------------

def _kakan_events(accepted_pai="N"):
    """小局 S4 内带 kakan_accepted 的事件流（normalize 后 mjai 形态）。"""
    return [
        {"type": "start_game", "names": ["p0", "p1", "p2", "p3"]},
        {"type": "start_kyoku", "bakaze": "S", "kyoku": 4, "honba": 1},
        {"type": "tsumo", "actor": 0, "pai": accepted_pai},
        {"type": "kakan", "actor": 0, "pai": accepted_pai, "consumed": ["N", "N", "N"]},
        {"type": "kakan_accepted", "actor": 0, "pai": accepted_pai},
        {"type": "tsumo", "actor": 0, "pai": "3p"},
        {"type": "dahai", "actor": 0, "pai": "3p"},
    ]


def _kakan_log_entries(pon_melds, added_in_hand, candidates=None, pon_pai="N", accepted_pai="N"):
    """返回 [kakan 决策 entry（动作前）, 后续 stale entry]。"""
    kakan_gt = {"type": "kakan", "actor": 0, "pai": accepted_pai, "consumed": ["N", "N", "N"]}
    stale_hand = ["6m", "6m", "7p", "S"]
    if added_in_hand:
        stale_hand.append(accepted_pai)
    stale_candidates = copy.deepcopy(candidates) if candidates is not None else []
    return [
        {
            "is_obs": False,
            "hand": ["6m", "6m", "7p", "S"],
            "tsumo_pai": accepted_pai,
            "actor_to_move": 0,
            "melds": [copy.deepcopy(pon_melds), [], [], []],
            "gt_action": kakan_gt,
            "chosen": {"type": "dahai", "actor": 0, "pai": "7p"},
            "candidates": [],
        },
        {
            "is_obs": False,
            "hand": stale_hand,
            "tsumo_pai": "3p",
            "actor_to_move": 0,
            "melds": [copy.deepcopy(pon_melds), [], [], []],
            "gt_action": {"type": "dahai", "actor": 0, "pai": "3p"},
            "chosen": {"type": "dahai", "actor": 0, "pai": "3p"},
            "candidates": stale_candidates,
        },
    ]


def _make_decisions(entries, kyoku="S4h1", player_id=0):
    bakaze = kyoku[0]
    m = re.match(r"([EWSN])(\d+)(?:h(\d+))?", kyoku)
    kyoku_num = int(m.group(2))
    honba = int(m.group(3) or 0)
    return {
        "player_id": player_id,
        "log": [
            {
                "bakaze": bakaze,
                "kyoku": kyoku_num,
                "honba": honba,
                "kyoku_key": {"bakaze": bakaze, "kyoku": kyoku_num, "honba": honba},
                **entry,
            }
            for entry in entries
        ],
    }


def _standard_pon_n():
    return {"type": "pon", "pai": "N", "pai_raw": "N", "consumed": ["N", "N"], "target": 3}


def test_n1_standard_pon_repairs_to_four_tile_kakan():
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True)
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    assert "N" not in stale["hand"], "加杠牌应从暗手移除"
    kakan = stale["melds"][0][0]
    assert kakan["type"] == "kakan"
    assert kakan["consumed"] == ["N", "N", "N", "N"], "canonical kakan 必须严格四张"


def test_n2_red_five_composition_preserved():
    pon = {"type": "pon", "pai": "5p", "pai_raw": "5p", "consumed": ["5pr", "5p"], "target": 1}
    entries = _kakan_log_entries([pon], added_in_hand=True, pon_pai="5p", accepted_pai="5pr")
    # 事件与 entry 用赤五
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events(accepted_pai="5pr"))

    stale = normalized["log"][1]
    kakan = stale["melds"][0][0]
    assert kakan["type"] == "kakan"
    assert kakan["consumed"] == ["5pr", "5p", "5p", "5pr"], (
        "赤五 pon + 赤五加杠应保留四张赤牌组成"
    )
    assert kakan["pai"] == "5pr" and kakan["pai_raw"] == "5pr"
    assert "5pr" not in stale["hand"]


def test_n3_kakan_decision_entry_keeps_pre_snapshot():
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True)
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    decision = normalized["log"][0]
    assert decision["hand"] == ["6m", "6m", "7p", "S"], "加杠决策 entry 保持动作前快照"
    assert decision["tsumo_pai"] == "N"
    assert decision["melds"][0][0]["type"] == "pon", "决策 entry 的 pon 不被升级"


def test_n4_already_correct_kakan_snapshot_is_noop():
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True)
    # 构造已正确的后续 entry：kakan 已四张、手已移除
    entries[1]["melds"] = [
        [{"type": "kakan", "pai": "N", "pai_raw": "N", "consumed": ["N", "N", "N", "N"], "target": 3}],
        [], [], [],
    ]
    entries[1]["hand"] = ["6m", "6m", "7p", "S"]
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    assert stale["melds"][0][0]["consumed"] == ["N", "N", "N", "N"]
    assert stale["hand"] == ["6m", "6m", "7p", "S"]


def test_n5_double_normalization_is_idempotent():
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True)
    decisions = _make_decisions(entries)
    once = normalize_replay_decisions(decisions, events=_kakan_events())
    twice = normalize_replay_decisions(once, events=_kakan_events())
    assert once == twice


def test_n6_no_unique_pon_does_not_mutate():
    # 两个同名 pon → 多匹配，不做任何修改
    entries = _kakan_log_entries([_standard_pon_n(), _standard_pon_n()], added_in_hand=True)
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    assert "N" in stale["hand"], "多匹配时不猜测，不删除加杠牌"
    assert stale["melds"][0][0]["type"] == "pon", "多匹配时不升级副露"

    # 完全没有 pon N → 不修改
    entries2 = _kakan_log_entries([], added_in_hand=True)
    decisions2 = _make_decisions(entries2)
    normalized2 = normalize_replay_decisions(decisions2, events=_kakan_events())
    stale2 = normalized2["log"][1]
    assert "N" in stale2["hand"]


def test_n7_stale_candidates_filtered_valid_kept():
    candidates = [
        {"action": {"type": "dahai", "actor": 0, "pai": "N"}},
        {"action": {"type": "kakan", "actor": 0, "pai": "N"}},
        {"action": {"type": "dahai", "actor": 0, "pai": "7p"}},
    ]
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True, candidates=candidates)
    decisions = _make_decisions(entries)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    assert stale["candidates"] == [{"action": {"type": "dahai", "actor": 0, "pai": "7p"}}], (
        "引用已移除加杠牌的 stale 候选应被过滤，合法候选保留"
    )


def test_n8_active_repair_not_inherited_across_kyoku():
    entries = _kakan_log_entries([_standard_pon_n()], added_in_hand=True)
    # 下一个小局 S5 里有一个"看起来 stale"的 entry（手含 N + pon N），
    # 但不应继承 S4 的 active 修复
    next_kyoku_entry = {
        "is_obs": False,
        "hand": ["2m", "2m", "2m", "N"],
        "tsumo_pai": None,
        "actor_to_move": 0,
        "melds": [[_standard_pon_n()], [], [], []],
        "gt_action": {"type": "dahai", "actor": 0, "pai": "N"},
        "chosen": {"type": "dahai", "actor": 0, "pai": "N"},
        "candidates": [],
    }
    decisions = _make_decisions(entries + [next_kyoku_entry], kyoku="S5h0", player_id=0)
    decisions["log"][2]["kyoku_key"] = {"bakaze": "S", "kyoku": 5, "honba": 0}
    decisions["log"][2]["bakaze"] = "S"
    decisions["log"][2]["kyoku"] = 5
    decisions["log"][2]["honba"] = 0
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    nk = normalized["log"][2]
    assert "N" in nk["hand"], "下一个小局不继承 active 修复"
    assert nk["melds"][0][0]["type"] == "pon"


def test_n9_malformed_consumed_length_is_complete_noop():
    """唯一 pon 但 consumed 长度非标准 → hand/melds/candidates 全部不变。"""
    malformed_pon = {"type": "pon", "pai": "N", "pai_raw": "N", "consumed": ["N"], "target": 3}
    candidates = [
        {"action": {"type": "dahai", "actor": 0, "pai": "N"}},
        {"action": {"type": "dahai", "actor": 0, "pai": "7p"}},
    ]
    entries = _kakan_log_entries([malformed_pon], added_in_hand=True, candidates=candidates)
    decisions = _make_decisions(entries)
    before = copy.deepcopy(decisions)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    stale_before = before["log"][1]
    assert stale["hand"] == stale_before["hand"], "hand 不得改变（残留加杠牌仍在）"
    assert stale["melds"] == stale_before["melds"], "melds 不得改变"
    assert stale["candidates"] == stale_before["candidates"], "candidates 不得被过滤"
    assert stale["melds"][0][0]["type"] == "pon"


def test_n10_wrong_tile_family_is_complete_noop():
    """consumed 两张但其中一张牌族错误 → 完整 no-op + diagnostic。"""
    wrong_family_pon = {"type": "pon", "pai": "N", "pai_raw": "N", "consumed": ["N", "S"], "target": 3}
    entries = _kakan_log_entries([wrong_family_pon], added_in_hand=True)
    decisions = _make_decisions(entries)
    before = copy.deepcopy(decisions)
    normalized = normalize_replay_decisions(decisions, events=_kakan_events())

    stale = normalized["log"][1]
    stale_before = before["log"][1]
    assert stale["hand"] == stale_before["hand"]
    assert stale["melds"] == stale_before["melds"]
    assert stale["candidates"] == stale_before["candidates"]
    assert stale["melds"][0][0]["type"] == "pon"
