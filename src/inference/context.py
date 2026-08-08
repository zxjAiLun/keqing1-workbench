from __future__ import annotations

from typing import Optional, Protocol

from inference.contracts import DecisionContext


class DecisionContextBuilder(Protocol):
    """Builds the runtime/model snapshots for a single decision point.

    The target split is:
    - runtime_snap: true post-apply state used for legal action reconstruction
    - model_snap: training-aligned decision snapshot used for feature encoding
    """

    def build(self, state, actor: int, event: dict) -> Optional[DecisionContext]:
        ...
