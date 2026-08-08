from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class ModelAuxOutputs:
    score_delta: float = 0.0
    win_prob: float = 0.0
    dealin_prob: float = 0.0
    rank_probs: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    final_score_delta: float = 0.0
    rank_pt_value: float = 0.0


@dataclass(frozen=True)
class Xmodel1RuntimeOutputs:
    discard_logits: np.ndarray
    candidate_tile_id: np.ndarray
    candidate_mask: np.ndarray
    response_logits: np.ndarray
    response_action_idx: np.ndarray
    response_action_mask: np.ndarray
    response_post_candidate_feat: np.ndarray
    response_post_candidate_mask: np.ndarray
    response_teacher_discard_idx: np.ndarray
    win_prob: float
    dealin_prob: float
    pts_given_win: float
    pts_given_dealin: float
    opp_tenpai_probs: np.ndarray


@dataclass(frozen=True)
class ModelForwardResult:
    policy_logits: np.ndarray
    value: float
    aux: ModelAuxOutputs = field(default_factory=ModelAuxOutputs)
    xmodel1: Xmodel1RuntimeOutputs | None = None


@dataclass(frozen=True)
class DecisionContext:
    actor: int
    event: dict[str, Any]
    runtime_snap: dict[str, Any]
    model_snap: dict[str, Any]
    legal_actions: list[dict[str, Any]]


@dataclass(frozen=True)
class ScoredCandidate:
    action: dict[str, Any]
    logit: float
    final_score: float
    beam_score: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionResult:
    chosen: dict[str, Any]
    candidates: list[ScoredCandidate]
    model_value: float
    model_aux: ModelAuxOutputs = field(default_factory=ModelAuxOutputs)
