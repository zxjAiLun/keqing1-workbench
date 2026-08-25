"""Static import-boundary gate for the future repository split.

Enforces (on source, via AST; lazy imports included because the scan walks
every ``Import``/``ImportFrom`` node):

- ``src/**`` never imports ``training/**``
- ``training/**`` never imports Workbench packages (``workbench``,
  ``replay_ui``, ``replay``, ``gateway``, ``participants``, ``convert``,
  ``tools``, ``scripts``)
- ``workbench/**`` never imports ``training/**``

Violations fail before the repos can be split; data files are the interface
between Training and Workbench, not Python imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

TRAINING_TOPS = {"training"}
WORKBENCH_TOPS = {
    "workbench",
    "replay_ui",
    "replay",
    "gateway",
    "participants",
    "convert",
    "tools",
    "scripts",
}


def _first_party_tops(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops


def _scan(root: str) -> list[tuple[Path, set[str]]]:
    results: list[tuple[Path, set[str]]] = []
    base = _REPO_ROOT / root
    for py in sorted(base.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        results.append((py.relative_to(_REPO_ROOT), _first_party_tops(py)))
    return results


def test_src_never_imports_training() -> None:
    offenders = [
        str(path)
        for path, tops in _scan("src")
        if tops & TRAINING_TOPS
    ]
    assert not offenders, f"src/ must not import training/: {offenders}"


def test_training_never_imports_workbench() -> None:
    offenders = [
        str(path)
        for path, tops in _scan("training")
        if tops & WORKBENCH_TOPS
    ]
    assert not offenders, f"training/ must not import workbench/: {offenders}"


def test_workbench_never_imports_training() -> None:
    offenders = [
        str(path)
        for path, tops in _scan("workbench")
        if tops & TRAINING_TOPS
    ]
    assert not offenders, f"workbench/ must not import training/: {offenders}"


def test_workbench_never_directly_imports_project_data_or_bot_registry() -> None:
    """Workbench business code must use workbench.runtime.resolver instead of direct imports."""
    offenders: list[str] = []
    for py in sorted((_REPO_ROOT / "workbench").rglob("*.py")):
        parts = py.parts
        if "__pycache__" in parts or ("runtime" in parts and "resolver" in parts):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("project_data", "inference.bot_registry") or alias.name.startswith("project_data."):
                        offenders.append(f"{py.relative_to(_REPO_ROOT)}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if (
                    node.module in ("project_data", "inference.bot_registry")
                    or node.module.startswith("project_data.")
                    or (node.module == "inference" and any(alias.name == "bot_registry" for alias in node.names))
                ):
                    offenders.append(f"{py.relative_to(_REPO_ROOT)}: from {node.module} import ...")
    assert not offenders, f"workbench/ must use workbench.runtime.resolver instead of direct imports: {offenders}"

