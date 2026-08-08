"""Versioned rank system profile contracts.

A ``RankSystem`` encapsulates the full semantics of a competitive ranking /
rating system (rank ladder, PT tables, rating formula).  Report builders and
ladder loaders depend only on this interface, so new systems (Tenhou ranked,
Majsoul, custom league pts, ...) can be added as new implementations without
rewriting the report pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol, Sequence


@dataclass(frozen=True)
class PlayerRankState:
    """Immutable per-player rank state snapshot (used for pre-match inputs)."""

    rank_id: str
    pt: int
    rating: Decimal
    games: int


@dataclass(frozen=True)
class RankMeta:
    """Display / progression metadata for a single rank."""

    rank_id: str
    rank_name: str
    ordinal: int
    initial_pt: int | None
    target_pt: int | None
    is_tenhou: bool = False


@dataclass(frozen=True)
class MatchContext:
    """Shared per-game context.

    Only carries what is genuinely shared by the table:

    - ``game_length`` (tonpuu / hanchan)
    - ``avg_rating``: the raw pre-match table-average rating

    This profile family does NOT simulate Tenhou room isolation / matchmaking /
    eligibility: any four accounts may share a table and each player resolves
    their own PT tier independently from their pre-match rank and rating.
    """

    game_length: str
    avg_rating: Decimal


Transition = Literal["none", "promotion", "demotion", "tenhou"]


@dataclass(frozen=True)
class RankUpdate:
    """One player's rank/rating transition for a single game."""

    rank_before: str
    pt_before: int
    pt_delta: int

    transition: Transition

    rank_after: str
    pt_after: int

    rating_before: Decimal
    rating_delta_raw: Decimal
    rating_after: Decimal

    # 本局该玩家的个人计分档位（无档位概念或天凤位时为 None）。
    pt_tier: str | None = None
    positive_pt: tuple[int, int, int] | None = None


class RankSystem(Protocol):
    """Common interface for a ranked scoring profile."""

    system_id: str
    version: str

    def initial_state(self) -> PlayerRankState: ...
    def match_context(
        self,
        players: Sequence[PlayerRankState],
        *,
        game_length: str | None = None,
    ) -> MatchContext: ...
    def apply_result(
        self,
        state: PlayerRankState,
        *,
        placement: int,
        match: MatchContext,
    ) -> RankUpdate: ...
    def rank_meta(self, rank_id: str) -> RankMeta: ...
    def scoring_block(self) -> dict[str, Any]: ...
