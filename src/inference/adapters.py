from __future__ import annotations

from typing import Protocol

import numpy as np

from inference.contracts import ModelForwardResult


class InferenceAdapter(Protocol):
    model_version: str

    def encode(self, snap: dict, actor: int) -> tuple[np.ndarray, np.ndarray]: ...

    def forward(self, snap: dict, actor: int) -> ModelForwardResult: ...


class RuntimeReviewExporter(Protocol):
    def build_decision_entry(
        self,
        *,
        step: int,
        ctx,
        decision,
        gt_action: dict | None,
        actor: int,
    ) -> dict: ...

    def candidate_sort_key(self, candidate: dict) -> float: ...
