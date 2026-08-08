from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StaticExactQuery:
    table_slug: str
    row_key: str
    column: str


@dataclass(frozen=True)
class TileDangerQuery:
    table_slug: str
    situation: str
    tile_class: str


@dataclass(frozen=True)
class PointEvQuery:
    table_slug: str
    hand_value_band: str
    scene: str


@dataclass(frozen=True)
class StaticTableHit:
    table_slug: str
    table_title: str
    category: str
    row_key: str
    column: str
    value: float | str
    unit: Optional[str] = None
