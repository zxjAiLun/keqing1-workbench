# -*- coding: utf-8 -*-
"""Season configuration and metadata runtime resolver."""
from __future__ import annotations

import re
from pathlib import Path

from .base import REPO_ROOT
from .ladder import ladder_registry_root, ladder_data_root

_DEFAULT_SEASONS_DIR = REPO_ROOT / "workbench" / "configs" / "ladder" / "seasons"


def resolve_season_config_dir(
    project_root: str | Path | None = None,
    *,
    allow_repo_seed: bool = False,
) -> Path:
    """Canonical runtime season-registry directory.

    All runtime operations strictly target ladder_registry_root().
    Repository configs under workbench/configs/ladder/seasons are seed/example
    templates only and will NOT be automatically fallen back to in production
    unless explicitly requested with allow_repo_seed=True.
    """
    if allow_repo_seed:
        runtime_root = ladder_registry_root()
        if runtime_root.is_dir():
            return runtime_root
        if project_root is not None:
            cand = Path(project_root) / "workbench" / "configs" / "ladder" / "seasons"
            if cand.is_dir():
                return cand
        return _DEFAULT_SEASONS_DIR
    del project_root
    return ladder_registry_root()


def resolve_season_config_path(
    season_id: str, configs_dir: Path | None = None
) -> Path:
    """Resolve the JSON configuration file path for a season_id."""
    clean_id = str(season_id).strip()
    if not clean_id or not re.match(r"^[0-9a-zA-Z_\-]+$", clean_id):
        raise ValueError(f"Invalid season_id: {season_id!r}")
    base = configs_dir or resolve_season_config_dir()
    return base / f"{clean_id}.json"


def list_season_config_paths(configs_dir: Path | None = None) -> list[Path]:
    """List all available season JSON configuration file paths."""
    base = configs_dir or resolve_season_config_dir()
    if not base.is_dir():
        return []
    return sorted(base.glob("*.json"))


def resolve_season_report_dir(season_id: str, report_dir: str | None = None) -> Path:
    """Resolve the report directory for a season."""
    clean_id = str(season_id).strip()
    if report_dir and str(report_dir).strip():
        path = Path(report_dir).expanduser()
        return path if path.is_absolute() else (ladder_data_root() / path).resolve()
    return ladder_data_root() / "reports" / clean_id
