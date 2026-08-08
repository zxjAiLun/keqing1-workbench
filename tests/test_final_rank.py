from __future__ import annotations

import pytest

from inference.pt_map import (
    expected_pt_for_all_seats,
    expected_pt_for_scores,
    expected_pt_from_rank_probs,
    pt_for_rank,
    validate_pt_map,
)
from mahjong_env.final_rank import (
    expected_rank_from_probs,
    final_rank_for_seat,
    final_ranks,
    tie_break_order,
)


def test_final_rank_uses_initial_oya_tie_break():
    scores = [30000, 20000, 20000, 30000]

    assert tie_break_order(3) == (3, 0, 1, 2)
    assert final_rank_for_seat(scores, 3, initial_oya=3) == 0
    assert final_rank_for_seat(scores, 0, initial_oya=3) == 1
    assert final_ranks(scores, initial_oya=3) == (1, 2, 3, 0)


def test_pt_map_utility_uses_rank_probs_and_deterministic_ranks():
    pt_map = validate_pt_map((90, 45, 0, -135))
    rank_probs = (0.0, 0.5, 0.5, 0.0)

    assert expected_rank_from_probs(rank_probs) == pytest.approx(2.5)
    assert expected_pt_from_rank_probs(rank_probs, pt_map) == pytest.approx(22.5)
    assert pt_for_rank(0, pt_map) == pytest.approx(90.0)
    assert expected_pt_for_scores([37000, 21000, 21000, 21000], 1, pt_map, initial_oya=1) == pytest.approx(45.0)
    assert expected_pt_for_all_seats([30000, 30000, 22000, 18000], pt_map, initial_oya=1) == pytest.approx(
        (45.0, 90.0, 0.0, -135.0)
    )
