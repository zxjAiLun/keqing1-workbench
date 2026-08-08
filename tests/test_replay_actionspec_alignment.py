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

    assert probs == pytest.approx([0.7310586, 0.2689414])


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
