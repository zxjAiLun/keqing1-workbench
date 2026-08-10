"""Minimal shared convention for the repository-local runtime data directory.

``KEQING_DATA_ROOT`` is intentionally the only new cross-feature setting.  It
defaults to the shared ``keqing-data`` directory beside the project folder
when present (the exchange point with keqing-mortal), otherwise to
``<repo>/data`` so a checkout works without machine-specific setup.
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


def ladder_capture_root() -> Path:
    """Play-with-you ladder capture root。

    Writer（launcher collector 写 capture JSON）与 reader（participants intake 按
    log_id 反查 session provenance）必须共用同一个路径合同，防止两端数据根漂移
    （capture 一直落在 ``KEQING_DATA_ROOT/ladder/captures/playwithyou``）。
    ``KEQING_LADDER_DATA_ROOT`` 为部署 override。
    """
    raw = os.environ.get("KEQING_LADDER_DATA_ROOT", "").strip()
    root = Path(raw) if raw else data_path("ladder")
    return root / "captures" / "playwithyou"
