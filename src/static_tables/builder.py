from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from .schema import classify_table, slugify_table_name


def _to_scalar(text: str) -> Optional[float | str]:
    value = text.strip()
    if value == "":
        return None
    normalized = value.replace(",", "")
    try:
        return float(normalized)
    except ValueError:
        return value


def _load_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def build_bundle(source_dir: Path) -> dict:
    tables = []
    for csv_path in sorted(source_dir.glob("*.csv")):
        rows = _load_csv_rows(csv_path)
        if len(rows) < 2:
            continue
        meta = rows[0]
        header = rows[1]
        title = (meta[0].strip() if meta else csv_path.stem) or csv_path.stem
        table_ref = meta[1].strip() if len(meta) > 1 and meta[1].strip() else None
        unit = meta[2].strip() if len(meta) > 2 and meta[2].strip() else None
        headers = [cell.strip() for cell in header[1:] if cell.strip()]

        data_rows = []
        for row in rows[2:]:
            if not row:
                continue
            row_key = row[0].strip()
            if not row_key:
                continue
            values = {}
            for idx, col in enumerate(headers, start=1):
                cell = row[idx] if idx < len(row) else ""
                values[col] = _to_scalar(cell)
            data_rows.append({"key": row_key, "values": values})

        tables.append(
            {
                "slug": slugify_table_name(csv_path.stem),
                "title": title,
                "source_file": csv_path.name,
                "category": classify_table(csv_path.name),
                "table_ref": table_ref,
                "unit": unit,
                "headers": headers,
                "rows": data_rows,
            }
        )

    return {
        "version": 1,
        "source_root": str(source_dir),
        "generated_from": "dataset/mahjong_heratu_data/csv",
        "tables": tables,
    }
