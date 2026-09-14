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
MORTAL_SCORE_SEMANTICS = br.MORTAL_SCORE_SEMANTICS
SCORE_SEMANTICS_CALIBRATED_Q = br.SCORE_SEMANTICS_CALIBRATED_Q
SCORE_SEMANTICS_ACTION_SCORE = br.SCORE_SEMANTICS_ACTION_SCORE


def score_semantics_for(spec: str) -> str:
    return br.score_semantics_for(spec)


def search_authoritative_checkpoint(relative: str | Path) -> Path | None:
    return br.search_authoritative_checkpoint(relative)


def resolve_model_checkpoint(path: str | Path, project_root: str | Path) -> Path:
    return br.resolve_model_checkpoint(path, project_root)


def resolve_bot_spec(
    spec: str, project_root: str | Path
) -> tuple[str, Path | None]:
    return br.resolve_bot_spec(spec, project_root)


def create_runtime_bot(*args, **kwargs) -> Any:
    return br.create_runtime_bot(*args, **kwargs)
