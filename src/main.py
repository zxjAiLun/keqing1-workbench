"""Compatibility launcher for the Workbench application entry point.

New commands should use ``python workbench/main.py``.  Retaining this tiny
shim keeps existing local launch commands working during the folder split.
"""
from __future__ import annotations

from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (_REPO_ROOT, _REPO_ROOT / "workbench", _REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from workbench.main import main


if __name__ == "__main__":
    main()
