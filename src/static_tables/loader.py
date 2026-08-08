from __future__ import annotations

import json
from pathlib import Path

from .schema import StaticTableBundle, StaticTableRecord, StaticTableRow


def load_static_table_bundle(path: str | Path) -> StaticTableBundle:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    tables = []
    for table in raw["tables"]:
        rows = [
            StaticTableRow(key=row["key"], values=row["values"])
            for row in table["rows"]
        ]
        tables.append(
            StaticTableRecord(
                slug=table["slug"],
                title=table["title"],
                source_file=table["source_file"],
                category=table["category"],
                table_ref=table.get("table_ref"),
                unit=table.get("unit"),
                headers=list(table["headers"]),
                rows=rows,
            )
        )
    return StaticTableBundle(
        version=int(raw["version"]),
        source_root=str(raw["source_root"]),
        generated_from=str(raw["generated_from"]),
        tables=tables,
    )
