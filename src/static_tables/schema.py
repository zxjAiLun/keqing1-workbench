from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, List, Optional


def slugify_table_name(name: str) -> str:
    stem = Path(name).stem
    slug = stem.strip().lower()
    slug = slug.replace("·", "_").replace("・", "_")
    slug = re.sub(r"[\s/]+", "_", slug)
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "table"


def classify_table(filename: str) -> str:
    name = Path(filename).stem
    if "危险度" in name:
        return "danger"
    if "和率" in name:
        return "win_rate"
    if "局收支" in name:
        return "point_ev"
    if "各项数值" in name:
        return "various"
    return "other"


@dataclass(frozen=True)
class StaticTableRow:
    key: str
    values: dict[str, Optional[float | str]]


@dataclass(frozen=True)
class StaticTableRecord:
    slug: str
    title: str
    source_file: str
    category: str
    table_ref: Optional[str]
    unit: Optional[str]
    headers: List[str]
    rows: List[StaticTableRow]

    def header_set(self) -> set[str]:
        return set(self.headers)

    def row_keys(self) -> List[str]:
        return [row.key for row in self.rows]


@dataclass(frozen=True)
class StaticTableBundle:
    version: int
    source_root: str
    generated_from: str
    tables: List[StaticTableRecord]

    def iter_tables(self) -> Iterable[StaticTableRecord]:
        return iter(self.tables)
