# -*- coding: utf-8 -*-
"""Ladder runtime path and storage resolver."""
from __future__ import annotations

import os
from pathlib import Path

from .base import REPO_ROOT, data_path, _override_path


def ladder_data_root() -> Path:
    """Canonical root for all portable Ladder runtime data."""
    return _override_path("KEQING_LADDER_DATA_ROOT", data_path("ladder"))


def ladder_registry_root() -> Path:
    """Canonical runtime season-registry directory."""
    return _override_path("KEQING_LADDER_CONFIG_DIR", ladder_data_root() / "registries")


def ladder_snapshots_root() -> Path:
    """Canonical root for ladder snapshots."""
    return _override_path("KEQING_LADDER_SNAPSHOTS_ROOT", ladder_data_root() / "snapshots")


def ladder_relative_sources_root(season_id: str | None = None) -> Path:
    """Canonical root for relative sources ingested by the ladder."""
    base = ladder_data_root() / "relative" / "sources"
    if season_id:
        return base / season_id
    return base


def resolve_ladder_path(raw: str | Path) -> Path:
    """Resolve a persisted Ladder path relative to ladder_data_root()."""
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (ladder_data_root() / path).resolve()


def encode_ladder_path(path: str | Path) -> str:
    """Encode an in-root physical path for portable registry persistence."""
    root = ladder_data_root().resolve()
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"Ladder path is outside data root: {resolved}") from exc


def resolve_ladder_snapshot_path(season_id: str, timestamp: str | None = None) -> Path:
    """Resolve the path to a snapshot JSON file for a season."""
    base = ladder_snapshots_root() / season_id
    if timestamp:
        return base / f"snapshot_{season_id}_{timestamp}.json"
    return base / "snapshot_latest.json"


def resolve_ladder_manifest_path(season_id: str) -> Path:
    """Resolve the manifest.json path for a season's snapshots."""
    return ladder_snapshots_root() / season_id / "manifest.json"


def resolve_ledger_path(season_id: str) -> Path:
    """Resolve the match ledger jsonl path for a season."""
    return ladder_data_root() / "ledgers" / f"{season_id}.jsonl"
