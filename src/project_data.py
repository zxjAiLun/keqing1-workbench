"""Minimal shared convention for the repository-local runtime data directory.

``KEQING_DATA_ROOT`` is intentionally the only new cross-feature setting.  It
defaults to the sibling ``keqing-data`` directory when present (the shared
exchange point with keqing-mortal), otherwise to ``<repo>/data`` so a checkout
works without machine-specific setup.
"""
from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


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
    """Build a path below :func:`data_root` without creating it."""
    return data_root().joinpath(*parts)
