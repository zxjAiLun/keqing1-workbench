# -*- coding: utf-8 -*-
"""Base runtime paths and environment overrides."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def data_root() -> Path:
    """Return the configured data root, relative paths being repo-relative."""
    raw = os.environ.get("KEQING_DATA_ROOT", "").strip()
    if not raw:
        shared = REPO_ROOT.parent.parent / "keqing-data"
        if shared.is_dir():
            return shared
        return REPO_ROOT / "data"
    root = Path(raw).expanduser()
    return root if root.is_absolute() else (REPO_ROOT / root).resolve()


def data_path(*parts: str) -> Path:
    """Build a path below data_root() without creating it."""
    return data_root().joinpath(*parts)


def _override_path(name: str, default: Path) -> Path:
    """Resolve an optional deployment override, otherwise return default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()
