import pytest

from inference.review import (
    DefaultRuntimeReviewExporter,
    candidate_probabilities,
    same_action as _same_action,
)
from replay.legacy_render import render_candidates_logit


def test_replay_same_action_ignores_dahai_tsumogiri_difference():
    chosen = {"type": "dahai", "actor": 1, "pai": "3p", "tsumogiri": False}
    gt = {"type": "dahai", "actor": 1, "pai": "3p", "tsumogiri": True}

    assert _same_action(chosen, gt) is True


def test_replay_same_action_matches_aka_equivalent_chi():
    chosen = {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "4p",
        "consumed": ["5p", "6p"],
    }
    gt = {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "4p",
        "consumed": ["5pr", "6p"],
    }

    assert _same_action(chosen, gt) is True


def test_render_candidates_logit_marks_equivalent_gt_action():
    candidate_action = {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "4p",
        "consumed": ["5pr", "6p"],
    }
    chosen = {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "4p",
        "consumed": ["5p", "6p"],
    }
    gt = {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "4p",
        "consumed": ["5p", "6p"],
    }
    html = render_candidates_logit(
        [{"action": candidate_action, "logit": 1.25}],
        chosen,
        gt,
    )

    assert "✓Bot" in html
    assert "★玩家" in html


def test_candidate_probabilities_use_softmax_over_final_scores():
    candidates = [
        {"action": {"type": "dahai", "actor": 0, "pai": "1m"}, "logit": 0.0, "final_score": 2.0},
        {"action": {"type": "dahai", "actor": 0, "pai": "2m"}, "logit": 0.0, "final_score": 1.0},
    ]

    probs = candidate_probabilities(candidates)

    # 展示层默认 tau=0.1，与 Mortal 官方 review 站点一致
    assert probs == pytest.approx([0.9999546021312976, 4.5397868702434395e-05])


def test_candidate_probabilities_default_temperature_matches_mortal_review():
    """展示层默认温度必须与 Mortal 站点一致（tau=0.1）。

    站点报告的 ``details[].prob`` 是用 tau=0.1 对 ``q_value`` 做 softmax 得到的；
    实测从该站点报告的 (q_value, prob) 反解温度，72/72 个条目均为 0.10000。
    若这里改回 1.0，本仓展示的概率会比 Mortal 扁平得多，同一局两边看着对不上。
    """
    from inference.review import DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE

    assert DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE == pytest.approx(0.1)

    # 用外部报告的真实 (q_value, prob) 做锚点：tau=0.1 必须能复现站点概率。
    # 注意必须用**完整候选集**：站点是在整个动作空间上做 softmax，只取前几个
    # 会让分母偏小、概率偏大（实测差 2.7e-05）。
    details = [
        (0.42897758, 0.84512913),
        (0.25926575, 0.15483673),
        (-0.6087061, 0.000026322063),
        (-0.803426, 0.0000037554519),
        (-0.92385817, 0.0000011262434),
        (-0.9646679, 7.488546e-7),
        (-0.98482275, 6.121621e-7),
        (-0.99718666, 5.4096637e-7),
        (-1.0741291, 2.5061868e-7),
        (-1.087764, 2.1867442e-7),
        (-1.0907362, 2.1227098e-7),
        (-1.1005564, 1.9241605e-7),
        (-1.1064153, 1.8146666e-7),
        (-1.1445965, 1.2387325e-7),
    ]
    candidates = [
        {"action": {"type": "dahai", "actor": 0, "pai": "1m"}, "final_score": q_value}
        for q_value, _ in details
    ]
    probs = candidate_probabilities(candidates)
    assert probs == pytest.approx([prob for _, prob in details], abs=1e-6)


def test_rating_matches_mortal_faq_minmax_squared_formula():
    log = [
        {
            "gt_action": {"type": "dahai", "actor": 0, "pai": "2m"},
            "candidates": [
                {"action": {"type": "dahai", "actor": 0, "pai": "1m"}, "logit": 2.0, "final_score": 2.0},
                {"action": {"type": "dahai", "actor": 0, "pai": "2m"}, "logit": 1.0, "final_score": 1.0},
                {"action": {"type": "dahai", "actor": 0, "pai": "3m"}, "logit": 0.0, "final_score": 0.0},
            ],
        },
        {
            "gt_action": {"type": "dahai", "actor": 0, "pai": "1p"},
            "candidates": [
                {"action": {"type": "dahai", "actor": 0, "pai": "1p"}, "logit": 4.0, "final_score": 4.0},
                {"action": {"type": "dahai", "actor": 0, "pai": "2p"}, "logit": 0.0, "final_score": 0.0},
            ],
        },
    ]

    assert DefaultRuntimeReviewExporter().compute_rating(log) == 56.2
