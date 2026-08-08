"""Static statistical tables for offline priors and runtime lookup."""

from .loader import StaticTableBundle, StaticTableRecord, load_static_table_bundle
from .lookup import StaticTableLookup, load_default_lookup
from .mapper import (
    ReplayPointEvContext,
    ReplayTileDangerContext,
    map_replay_honor_danger_query,
    map_replay_point_ev_query,
    map_replay_suited_danger_query,
    replay_point_ev_context_from_entry,
    replay_point_ev_context_from_snapshot,
    replay_tile_danger_context_from_entry,
    replay_tile_danger_context_from_snapshot,
    resolve_replay_point_ev,
    resolve_replay_tile_danger,
)
from .query import PointEvQuery, StaticExactQuery, StaticTableHit, TileDangerQuery

__all__ = [
    "StaticTableBundle",
    "StaticTableRecord",
    "StaticTableLookup",
    "StaticExactQuery",
    "TileDangerQuery",
    "PointEvQuery",
    "StaticTableHit",
    "ReplayTileDangerContext",
    "ReplayPointEvContext",
    "replay_tile_danger_context_from_entry",
    "replay_tile_danger_context_from_snapshot",
    "replay_point_ev_context_from_entry",
    "replay_point_ev_context_from_snapshot",
    "map_replay_honor_danger_query",
    "map_replay_point_ev_query",
    "map_replay_suited_danger_query",
    "resolve_replay_point_ev",
    "resolve_replay_tile_danger",
    "load_static_table_bundle",
    "load_default_lookup",
]
