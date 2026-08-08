from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .loader import load_static_table_bundle
from .schema import StaticTableBundle, StaticTableRecord
from .query import PointEvQuery, StaticExactQuery, StaticTableHit, TileDangerQuery

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_BUNDLE_PATH = _PROJECT_ROOT / "dataset" / "data" / "static" / "tables" / "mahjong_book_stats.json"


@dataclass
class StaticTableLookup:
    bundle: StaticTableBundle

    def __post_init__(self):
        self._tables_by_slug = {table.slug: table for table in self.bundle.tables}
        self._tables_by_category: dict[str, list[StaticTableRecord]] = {}
        for table in self.bundle.tables:
            self._tables_by_category.setdefault(table.category, []).append(table)

    def get_table(self, slug: str) -> Optional[StaticTableRecord]:
        return self._tables_by_slug.get(slug)

    def list_tables(self, *, category: Optional[str] = None) -> list[StaticTableRecord]:
        if category is None:
            return list(self.bundle.tables)
        return list(self._tables_by_category.get(category, ()))

    def find_table(
        self,
        *,
        slug_contains: Optional[str] = None,
        title_contains: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Optional[StaticTableRecord]:
        slug_contains = slug_contains.lower() if slug_contains else None
        title_contains = title_contains.lower() if title_contains else None
        candidates = self.list_tables(category=category)
        for table in candidates:
            if slug_contains and slug_contains not in table.slug.lower():
                continue
            if title_contains and title_contains not in table.title.lower():
                continue
            return table
        return None

    def lookup_exact(self, slug: str, row_key: str, column: str) -> Optional[float | str]:
        table = self.get_table(slug)
        if table is None:
            return None
        if column not in table.header_set():
            return None
        for row in table.rows:
            if row.key == row_key:
                return row.values.get(column)
        return None

    def lookup_tile_danger(self, slug: str, row_key: str, column: str) -> Optional[float | str]:
        table = self.get_table(slug)
        if table is None or table.category != "danger":
            return None
        return self.lookup_exact(slug, row_key, column)

    def lookup_point_ev(self, slug: str, row_key: str, column: str) -> Optional[float | str]:
        table = self.get_table(slug)
        if table is None or table.category != "point_ev":
            return None
        return self.lookup_exact(slug, row_key, column)

    def resolve_exact(self, query: StaticExactQuery) -> Optional[StaticTableHit]:
        table = self.get_table(query.table_slug)
        if table is None:
            return None
        value = self.lookup_exact(query.table_slug, query.row_key, query.column)
        if value is None:
            return None
        return StaticTableHit(
            table_slug=table.slug,
            table_title=table.title,
            category=table.category,
            row_key=query.row_key,
            column=query.column,
            value=value,
            unit=table.unit,
        )

    def resolve_tile_danger(self, query: TileDangerQuery) -> Optional[StaticTableHit]:
        table = self.get_table(query.table_slug)
        if table is None or table.category != "danger":
            return None
        value = self.lookup_tile_danger(
            query.table_slug,
            query.situation,
            query.tile_class,
        )
        if value is None:
            return None
        return StaticTableHit(
            table_slug=table.slug,
            table_title=table.title,
            category=table.category,
            row_key=query.situation,
            column=query.tile_class,
            value=value,
            unit=table.unit,
        )

    def resolve_point_ev(self, query: PointEvQuery) -> Optional[StaticTableHit]:
        table = self.get_table(query.table_slug)
        if table is None or table.category != "point_ev":
            return None
        value = self.lookup_point_ev(
            query.table_slug,
            query.hand_value_band,
            query.scene,
        )
        if value is None:
            return None
        return StaticTableHit(
            table_slug=table.slug,
            table_title=table.title,
            category=table.category,
            row_key=query.hand_value_band,
            column=query.scene,
            value=value,
            unit=table.unit,
        )


@lru_cache(maxsize=1)
def load_default_lookup(bundle_path: str | Path = _DEFAULT_BUNDLE_PATH) -> StaticTableLookup:
    bundle = load_static_table_bundle(bundle_path)
    return StaticTableLookup(bundle=bundle)
