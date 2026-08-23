# -*- coding: utf-8 -*-
"""Model checkpoint and bot spec runtime resolver.

Unified entrypoint that delegates to the core bot registry with strict
fail-closed semantics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import inference.bot_registry as br

MORTAL_CHECKPOINTS = br.MORTAL_CHECKPOINTS
SUPPORTED_BOT_NAMES = br.SUPPORTED_BOT_NAMES


def search_authoritative_checkpoint(relative: str | Path) -> Path | None:
    return br.search_authoritative_checkpoint(relative)


def resolve_model_checkpoint(path: str | Path, project_root: str | Path) -> Path:
    return br.resolve_model_checkpoint(path, project_root)


def resolve_bot_spec(
    spec: str, project_root: str | Path
) -> tuple[str, Path | None]:
    return br.resolve_bot_spec(spec, project_root)
