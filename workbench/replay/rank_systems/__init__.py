"""Versioned rank system profiles for the model ladder.

New competitive systems are added as ``RankSystem`` implementations and
selected by the season's ``scoring`` config — the report builder and the
ladder loaders never need to change.
"""

from __future__ import annotations

from .base import (
    MatchContext,
    PlayerRankState,
    RankMeta,
    RankSystem,
    RankUpdate,
)
from .legacy import LegacyFixedProfile
from .tenhou import TenhouRankProgression

__all__ = [
    "LegacyFixedProfile",
    "MatchContext",
    "PlayerRankState",
    "RankMeta",
    "RankSystem",
    "RankUpdate",
    "TenhouRankProgression",
    "create_rank_system",
]


def create_rank_system(config: dict | None) -> RankSystem:
    """Build a ``RankSystem`` from a season ``scoring`` config.

    ``config`` may be ``None`` (default legacy fixed profile, preserving the
    historical report behavior for standalone runs).  Unknown systems raise
    instead of silently reusing an arbitrary PT table.
    """
    if not config:
        return LegacyFixedProfile()

    system = str(config.get("system") or "")
    version = str(config.get("version") or "")
    if system == LegacyFixedProfile.system_id:
        return LegacyFixedProfile(
            version=version,
            game_length=str(config.get("game_length") or "hanchan"),
            room=str(config.get("room") or "houou"),
            initial_rank=str(config.get("initial_rank") or "7dan"),
            initial_pt=float(config.get("initial_pt") or 1400.0),
            initial_rating=float(config.get("initial_rating") or 1500.0),
            target_pt=float(config.get("target_pt") or 2800.0),
            rank_points=tuple(
                int(value)
                for value in (config.get("rank_points") or [90, 45, 0, -135])
            ),
        )
    if system == TenhouRankProgression.system_id:
        return TenhouRankProgression(
            version=version,
            game_length=str(config.get("game_length") or "hanchan"),
            initial_rank=str(config.get("initial_rank") or "newcomer"),
            initial_rating=float(config.get("initial_rating") or 1500.0),
        )
    raise ValueError(f"unknown rank system: {system!r}")
