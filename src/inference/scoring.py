"""Archived Keqing/xmodel action scorer stub."""

from __future__ import annotations

from typing import Protocol


class ActionScorer(Protocol):
    def score(self, ctx):
        ...


class DefaultActionScorer:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "DefaultActionScorer is archived with the Keqing/xmodel runtime. "
            "Use MortalReviewBot for active Mortal review."
        )
