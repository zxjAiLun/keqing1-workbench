from __future__ import annotations

from math import isfinite
from typing import Sequence

from mahjong_env.final_rank import (
    FINAL_RANK_DIM,
    final_rank_for_seat,
    final_ranks,
)


def validate_pt_map(pt_map: Sequence[int | float]) -> tuple[float, float, float, float]:
    if len(pt_map) != FINAL_RANK_DIM:
        raise ValueError(f"pt_map must have length {FINAL_RANK_DIM}, got {len(pt_map)}")
    normalized = tuple(float(value) for value in pt_map)
    if not all(isfinite(value) for value in normalized):
        raise ValueError(f"pt_map must contain finite values, got {pt_map}")
    return normalized


def pt_for_rank(
    rank: int,
    pt_map: Sequence[int | float],
) -> float:
    normalized_pt_map = validate_pt_map(pt_map)
    if rank < 0 or rank >= FINAL_RANK_DIM:
        raise ValueError(f"rank must be in [0, {FINAL_RANK_DIM}), got {rank}")
    return normalized_pt_map[rank]


def expected_pt_from_rank_probs(
    rank_probs: Sequence[int | float],
    pt_map: Sequence[int | float],
) -> float:
    if len(rank_probs) != FINAL_RANK_DIM:
        raise ValueError(
            f"rank_probs must have length {FINAL_RANK_DIM}, got {len(rank_probs)}"
        )
    distribution = tuple(float(value) for value in rank_probs)
    if not all(isfinite(value) for value in distribution):
        raise ValueError(f"rank_probs must contain finite values, got {rank_probs}")
    total = sum(distribution)
    if abs(total - 1.0) > 1e-4:
        raise ValueError(f"rank_probs must sum to 1.0, got {total}")
    normalized_pt_map = validate_pt_map(pt_map)
    return sum(prob * pt for prob, pt in zip(distribution, normalized_pt_map))


def placement_utility_from_outputs(
    rank_probs: Sequence[int | float],
    *,
    final_score_delta: float = 0.0,
    rank_bonus: Sequence[int | float] = (90.0, 45.0, 0.0, -135.0),
    rank_bonus_norm: float = 90.0,
    rank_score_scale: float = 0.0,
) -> float:
    normalized_bonus = tuple(
        value / max(float(rank_bonus_norm), 1e-6)
        for value in validate_pt_map(rank_bonus)
    )
    expected_bonus = expected_pt_from_rank_probs(rank_probs, normalized_bonus)
    return expected_bonus + float(rank_score_scale) * float(final_score_delta)


def expected_pt_for_scores(
    scores: Sequence[int | float],
    seat: int,
    pt_map: Sequence[int | float],
    *,
    initial_oya: int = 0,
) -> float:
    return pt_for_rank(final_rank_for_seat(scores, seat, initial_oya=initial_oya), pt_map)


def expected_pt_for_all_seats(
    scores: Sequence[int | float],
    pt_map: Sequence[int | float],
    *,
    initial_oya: int = 0,
) -> tuple[float, float, float, float]:
    rank_targets = final_ranks(scores, initial_oya=initial_oya)
    return tuple(
        pt_for_rank(rank, pt_map)
        for rank in rank_targets
    )  # type: ignore[return-value]


__all__ = [
    "expected_pt_for_all_seats",
    "expected_pt_for_scores",
    "expected_pt_from_rank_probs",
    "placement_utility_from_outputs",
    "pt_for_rank",
    "validate_pt_map",
]
