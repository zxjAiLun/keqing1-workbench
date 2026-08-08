from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from mahjong_env.tiles import normalize_tile

from .lookup import StaticTableLookup, load_default_lookup
from .query import PointEvQuery, StaticTableHit, TileDangerQuery

_DANGER_DEMO_TABLE = "one_chance的危险度"
_DANGER_DEMO_ROW = "全体"
_TURN_DANGER_TABLE = "各巡目的数牌危险度"
_HONOR_DANGER_TABLE = "各巡目的字牌危险度"
_WIND_ORDER = ["E", "S", "W", "N"]


@dataclass(frozen=True)
class ReplayTileDangerContext:
    pai: str
    actor: int
    bakaze: str
    oya: int
    hand: tuple[str, ...]
    discards: tuple[tuple[str, ...], ...]
    meld_tiles: tuple[str, ...]


@dataclass(frozen=True)
class ReplayPointEvContext:
    actor: int
    hand_value_band: str
    turn: int
    comparison: str


def replay_tile_danger_context_from_entry(entry: dict, pai: str, actor: Optional[int] = None) -> ReplayTileDangerContext:
    chosen = entry.get("chosen") or {}
    resolved_actor = int(actor if actor is not None else chosen.get("actor", entry.get("actor_to_move", 0)))
    hand = tuple(t for t in entry.get("hand") or [] if isinstance(t, str))
    discards = tuple(
        tuple(
            t for t in _iter_tiles(player_discards)
        )
        for player_discards in (entry.get("discards") or [[], [], [], []])
        if isinstance(player_discards, list)
    )
    meld_tiles: list[str] = []
    for meld in entry.get("melds") or []:
        if not isinstance(meld, dict):
            continue
        meld_tiles.extend(t for t in _iter_tiles(meld.get("consumed") or []))
        meld_pai = meld.get("pai")
        if isinstance(meld_pai, str):
            meld_tiles.append(meld_pai)
    return ReplayTileDangerContext(
        pai=pai,
        actor=resolved_actor,
        bakaze=str(entry.get("bakaze", "E")),
        oya=int(entry.get("oya", 0)),
        hand=hand,
        discards=discards,
        meld_tiles=tuple(meld_tiles),
    )


def replay_point_ev_context_from_entry(
    entry: dict,
    *,
    hand_value_band: str,
    comparison: str = "riichi_vs_dama",
    actor: Optional[int] = None,
) -> ReplayPointEvContext:
    chosen = entry.get("chosen") or {}
    resolved_actor = int(actor if actor is not None else chosen.get("actor", entry.get("actor_to_move", 0)))
    discards = entry.get("discards") or [[], [], [], []]
    turn = 1
    if 0 <= resolved_actor < len(discards) and isinstance(discards[resolved_actor], list):
        turn = len(discards[resolved_actor]) + 1
    return ReplayPointEvContext(
        actor=resolved_actor,
        hand_value_band=hand_value_band,
        turn=turn,
        comparison=comparison,
    )


def replay_point_ev_context_from_snapshot(
    snapshot: dict,
    *,
    hand_value_band: str,
    comparison: str = "riichi_vs_dama",
    actor: Optional[int] = None,
) -> ReplayPointEvContext:
    resolved_actor = int(actor if actor is not None else snapshot.get("actor", snapshot.get("actor_to_move", 0)))
    discards = snapshot.get("discards") or [[], [], [], []]
    turn = 1
    if 0 <= resolved_actor < len(discards) and isinstance(discards[resolved_actor], list):
        turn = len(discards[resolved_actor]) + 1
    return ReplayPointEvContext(
        actor=resolved_actor,
        hand_value_band=hand_value_band,
        turn=turn,
        comparison=comparison,
    )


def replay_tile_danger_context_from_snapshot(
    snapshot: dict,
    pai: str,
    actor: Optional[int] = None,
) -> ReplayTileDangerContext:
    resolved_actor = int(actor if actor is not None else snapshot.get("actor", snapshot.get("actor_to_move", 0)))
    hand = tuple(t for t in snapshot.get("hand") or [] if isinstance(t, str))
    discards = tuple(
        tuple(t for t in _iter_tiles(player_discards))
        for player_discards in (snapshot.get("discards") or [[], [], [], []])
        if isinstance(player_discards, list)
    )
    meld_tiles: list[str] = []
    snapshot_melds = snapshot.get("melds") or []
    if snapshot_melds and all(isinstance(m, dict) for m in snapshot_melds):
        meld_groups = [snapshot_melds]
    else:
        meld_groups = [group for group in snapshot_melds if isinstance(group, list)]
    for meld_group in meld_groups:
        for meld in meld_group:
            if not isinstance(meld, dict):
                continue
            meld_tiles.extend(t for t in _iter_tiles(meld.get("consumed") or []))
            meld_pai = meld.get("pai")
            if isinstance(meld_pai, str):
                meld_tiles.append(meld_pai)
    return ReplayTileDangerContext(
        pai=pai,
        actor=resolved_actor,
        bakaze=str(snapshot.get("bakaze", "E")),
        oya=int(snapshot.get("oya", 0)),
        hand=hand,
        discards=discards,
        meld_tiles=tuple(meld_tiles),
    )


def resolve_replay_tile_danger(
    context: ReplayTileDangerContext,
    *,
    lookup: Optional[StaticTableLookup] = None,
) -> Optional[StaticTableHit]:
    runtime_lookup = lookup or load_default_lookup()
    normalized = normalize_tile(context.pai)
    if len(normalized) == 1:
        query = map_replay_honor_danger_query(context)
        return runtime_lookup.resolve_tile_danger(query) if query is not None else None

    query = map_replay_suited_danger_query(context)
    if query is not None:
        hit = runtime_lookup.resolve_tile_danger(query)
        if hit is not None:
            return hit

    tile_class = _danger_demo_tile_class(normalized)
    if tile_class is None:
        return None
    return runtime_lookup.resolve_tile_danger(
        TileDangerQuery(
            table_slug=_DANGER_DEMO_TABLE,
            situation=_DANGER_DEMO_ROW,
            tile_class=tile_class,
        )
    )


def map_replay_point_ev_query(context: ReplayPointEvContext) -> Optional[PointEvQuery]:
    if context.comparison != "riichi_vs_dama":
        return None
    turn_bucket = _point_ev_turn_bucket(context.turn)
    if turn_bucket is None:
        return None
    return PointEvQuery(
        table_slug="先制两面立直_默听的局收支",
        hand_value_band=context.hand_value_band,
        scene=f"{turn_bucket}巡立直",
    )


def resolve_replay_point_ev(
    context: ReplayPointEvContext,
    *,
    lookup: Optional[StaticTableLookup] = None,
) -> Optional[StaticTableHit]:
    runtime_lookup = lookup or load_default_lookup()
    query = map_replay_point_ev_query(context)
    if query is None:
        return None
    return runtime_lookup.resolve_point_ev(query)


def map_replay_honor_danger_query(context: ReplayTileDangerContext) -> Optional[TileDangerQuery]:
    normalized = normalize_tile(context.pai)
    if len(normalized) != 1:
        return None
    turn = _turn_number_from_context(context)
    if turn is None:
        return None
    return TileDangerQuery(
        table_slug=_HONOR_DANGER_TABLE,
        situation=str(turn),
        tile_class=_honor_danger_column(normalized, context),
    )


def map_replay_suited_danger_query(context: ReplayTileDangerContext) -> Optional[TileDangerQuery]:
    normalized = normalize_tile(context.pai)
    if len(normalized) != 2 or normalized[1] not in ("m", "p", "s"):
        return None
    tile_class = _danger_demo_tile_class(normalized)
    if tile_class is None:
        return None
    one_chance_row = _one_chance_row(normalized, context)
    if one_chance_row is not None:
        return TileDangerQuery(
            table_slug=_DANGER_DEMO_TABLE,
            situation=one_chance_row,
            tile_class=tile_class,
        )
    turn = _turn_number_from_context(context)
    if turn is None:
        return None
    turn_column = _suited_turn_danger_column(normalized, context)
    if turn_column is None:
        return None
    return TileDangerQuery(
        table_slug=_TURN_DANGER_TABLE,
        situation=str(turn),
        tile_class=turn_column,
    )


def _danger_demo_tile_class(tile: str) -> Optional[str]:
    normalized = normalize_tile(tile)
    if len(normalized) != 2 or normalized[1] not in ("m", "p", "s"):
        return None
    number = int(normalized[0])
    mapping = {
        5: "無筋5",
        4: "無筋4(6)",
        6: "無筋4(6)",
        3: "無筋3(7)",
        7: "無筋3(7)",
        2: "無筋2(8)",
        8: "無筋2(8)",
        1: "無筋1(9)",
        9: "無筋1(9)",
    }
    return mapping.get(number)


def _iter_tiles(values: Iterable) -> Iterable[str]:
    for value in values:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            pai = value.get("pai")
            if isinstance(pai, str):
                yield pai


def _iter_visible_tiles(context: ReplayTileDangerContext) -> Iterable[str]:
    yield from context.hand
    for discards in context.discards:
        yield from discards
    yield from context.meld_tiles


def _iter_discard_tiles(context: ReplayTileDangerContext) -> Iterable[str]:
    for discards in context.discards:
        yield from discards


def _visible_tile_count(context: ReplayTileDangerContext, tile: str) -> int:
    normalized = normalize_tile(tile)
    return sum(1 for seen in _iter_visible_tiles(context) if normalize_tile(seen) == normalized)


def _discarded_tile_count(context: ReplayTileDangerContext, tile: str) -> int:
    normalized = normalize_tile(tile)
    return sum(1 for seen in _iter_discard_tiles(context) if normalize_tile(seen) == normalized)


def _discarded_any(context: ReplayTileDangerContext, tiles: Iterable[str]) -> bool:
    return any(_discarded_tile_count(context, tile) > 0 for tile in tiles)


def _one_chance_partner_tiles(tile: str) -> list[str]:
    normalized = normalize_tile(tile)
    if len(normalized) != 2 or normalized[1] not in ("m", "p", "s"):
        return []
    number = int(normalized[0])
    suit = normalized[1]
    mapping = {
        1: [4],
        2: [5],
        3: [6],
        4: [1, 7],
        5: [2, 8],
        6: [3, 9],
        7: [4],
        8: [5],
        9: [6],
    }
    return [f"{n}{suit}" for n in mapping.get(number, [])]


def _one_chance_row(tile: str, context: ReplayTileDangerContext) -> Optional[str]:
    partners = _one_chance_partner_tiles(tile)
    if not partners:
        return None
    counts = [_visible_tile_count(context, partner) for partner in partners]
    if not counts:
        return None
    if max(counts) >= 4:
        return "ノーチャンス"
    if sum(1 for cnt in counts if cnt >= 3) >= 2:
        return "ダブルワンチャンス"
    if sum(1 for cnt in counts if cnt >= 2) >= 2:
        return "ダブルツーチャンス"
    if max(counts) >= 3:
        return "非外側ワンチャンス"
    return None


def _suited_turn_danger_column(tile: str, context: ReplayTileDangerContext) -> Optional[str]:
    normalized = normalize_tile(tile)
    if len(normalized) != 2 or normalized[1] not in ("m", "p", "s"):
        return None
    if _discarded_tile_count(context, normalized) > 0:
        return "通った筋"

    number = int(normalized[0])
    suit = normalized[1]

    if number == 5:
        low_seen = _discarded_any(context, [f"2{suit}"])
        high_seen = _discarded_any(context, [f"8{suit}"])
        if low_seen and high_seen:
            return "両筋5"
        if low_seen or high_seen:
            return "片筋5"
        return "無筋5"

    if number in {4, 6}:
        low_partner = f"{number - 3}{suit}"
        high_partner = f"{number + 3}{suit}"
        low_seen = _discarded_any(context, [low_partner]) if 1 <= number - 3 <= 9 else False
        high_seen = _discarded_any(context, [high_partner]) if 1 <= number + 3 <= 9 else False
        if low_seen and high_seen:
            return "両筋46"
        if low_seen:
            return "片筋46A"
        if high_seen:
            return "片筋46B"
        return "無筋46"

    if number in {3, 7}:
        partner = f"{number + 3}{suit}" if number == 3 else f"{number - 3}{suit}"
        return "筋37" if _discarded_any(context, [partner]) else "無筋37"

    if number in {2, 8}:
        partner = f"{number + 3}{suit}" if number == 2 else f"{number - 3}{suit}"
        return "筋28" if _discarded_any(context, [partner]) else "無筋28"

    if number in {1, 9}:
        partner = f"{number + 3}{suit}" if number == 1 else f"{number - 3}{suit}"
        return "筋19" if _discarded_any(context, [partner]) else "無筋19"

    tile_class = _danger_demo_tile_class(normalized)
    if tile_class is None:
        return None
    return _normalize_turn_table_column(tile_class)


def _honor_danger_column(tile: str, context: ReplayTileDangerContext) -> str:
    visible_count = _visible_tile_count(context, tile)
    if tile in {"P", "F", "C"}:
        if visible_count <= 1:
            return "役牌ションパイ"
        if visible_count == 2:
            return "役牌1枚切れ"
        return "役牌2枚切れ"
    jikaze = _WIND_ORDER[(context.actor - context.oya) % 4]
    if tile == context.bakaze or tile == jikaze:
        if visible_count <= 1:
            return "役牌ションパイ"
        if visible_count == 2:
            return "役牌1枚切れ"
        return "役牌2枚切れ"
    if visible_count <= 1:
        return "オタカゼションパイ"
    if visible_count == 2:
        return "オタカゼ1枚切れ"
    return "オタカゼ2枚切れ"


def _normalize_turn_table_column(tile_class: str) -> str:
    mapping = {
        "無筋5": "無筋5",
        "無筋4(6)": "無筋46",
        "無筋3(7)": "無筋37",
        "無筋2(8)": "無筋28",
        "無筋1(9)": "無筋19",
    }
    return mapping.get(tile_class, tile_class)


def _turn_number_from_context(context: ReplayTileDangerContext) -> Optional[int]:
    if not (0 <= context.actor < len(context.discards)):
        return None
    return min(max(len(context.discards[context.actor]) + 1, 1), 12)


def _point_ev_turn_bucket(turn: int) -> Optional[int]:
    if turn <= 5:
        return 5
    if turn <= 8:
        return 8
    if turn <= 12:
        return 12
    return None
