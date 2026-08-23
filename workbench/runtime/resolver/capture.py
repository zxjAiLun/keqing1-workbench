# -*- coding: utf-8 -*-
"""Play-with-you and battle capture runtime resolver."""
from __future__ import annotations

import re
from pathlib import Path

from .ladder import ladder_data_root


def ladder_capture_root() -> Path:
    """Canonical root directory for Play-with-you captures."""
    return ladder_data_root() / "captures" / "playwithyou"


def resolve_capture_file(log_id: str, subdir: str = "pending") -> Path:
    """Resolve the path to a capture JSON file under ladder_capture_root()."""
    safe_log_id = re.sub(r"[^0-9a-zA-Z_\-]", "_", str(log_id))
    return ladder_capture_root() / subdir / f"{safe_log_id}.json"


def resolve_session_state_file(session_id: str) -> Path:
    """Resolve the path to a session state JSON file."""
    safe_session_id = re.sub(r"[^0-9a-zA-Z_\-]", "_", str(session_id))
    return ladder_capture_root() / "sessions" / f"{safe_session_id}.json"


def resolve_raw_tenhou_file(log_id: str) -> Path:
    """Resolve the path to a raw Tenhou JSON download."""
    safe_log_id = re.sub(r"[^0-9a-zA-Z_\-]", "_", str(log_id))
    return ladder_capture_root() / "raw_tenhou" / f"{safe_log_id}.json"
