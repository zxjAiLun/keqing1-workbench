#!/usr/bin/env python3
"""Migrate persisted Ladder ``report_dir`` values to portable relative paths.

The script never chooses a different snapshot: it rewrites only a path that
can be proven to live below ``<data-root>/ladder`` and verifies the exact
target snapshot before atomically replacing its registry file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from project_data import ladder_data_root  # noqa: E402

REQUIRED_FILES = ("account_summary.json", "account_ledger.jsonl", "rating_curve.csv")
WINDOWS_LADDER_MARKER = re.compile(r"(?:^|[\\/])keqing-data[\\/]ladder[\\/](.+)$", re.IGNORECASE)


def portable_report_dir(raw: str, root: Path) -> str:
    """Convert an in-root Linux/Windows absolute report path; reject all else."""
    raw = raw.strip()
    path = Path(raw)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"absolute report_dir is outside ladder root: {raw}") from exc
    if PureWindowsPath(raw).is_absolute():
        match = WINDOWS_LADDER_MARKER.search(raw)
        if not match:
            raise ValueError(f"Windows report_dir is outside keqing-data/ladder: {raw}")
        return Path(match.group(1).replace("\\", "/")).as_posix()
    return Path(raw).as_posix()


def verify_snapshot(root: Path, relative: str) -> None:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"report_dir escapes ladder root: {relative}") from exc
    missing = [name for name in REQUIRED_FILES if not (target / name).is_file()]
    if missing:
        raise ValueError(f"snapshot is incomplete: {target} (missing {', '.join(missing)})")


def migrate(registry_path: Path, root: Path, *, dry_run: bool) -> bool:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    raw = str(payload.get("report_dir") or "")
    if not raw:
        raise ValueError(f"{registry_path}: missing report_dir")
    relative = portable_report_dir(raw, root)
    verify_snapshot(root, relative)
    if raw == relative:
        return False
    if dry_run:
        print(f"would migrate {registry_path.name}: {raw} -> {relative}")
        return True
    backup = registry_path.with_name(f"{registry_path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(registry_path, backup)
    payload["report_dir"] = relative
    temp = registry_path.with_name(f".{registry_path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, registry_path)
    print(f"migrated {registry_path.name}: {relative} (backup: {backup.name})")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ladder-root", type=Path, default=ladder_data_root())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = args.ladder_root.resolve()
    registries = root / "registries"
    if not registries.is_dir():
        raise SystemExit(f"registry directory not found: {registries}")
    changed = 0
    for path in sorted(registries.glob("*.json")):
        changed += int(migrate(path, root, dry_run=args.dry_run))
    print(f"processed {changed} registry file(s)")


if __name__ == "__main__":
    main()
