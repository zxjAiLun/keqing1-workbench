# -*- coding: utf-8 -*-
"""R12-C: Unit tests for workbench.runtime.resolver consolidation."""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from workbench.runtime.resolver import (
    # Model
    MORTAL_CHECKPOINTS,
    SUPPORTED_BOT_NAMES,
    resolve_model_checkpoint,
    resolve_bot_spec,
    search_authoritative_checkpoint,
    # Ladder
    ladder_data_root,
    ladder_registry_root,
    ladder_snapshots_root,
    ladder_relative_sources_root,
    resolve_ladder_path,
    encode_ladder_path,
    resolve_ladder_snapshot_path,
    resolve_ladder_manifest_path,
    resolve_ledger_path,
    # Capture
    ladder_capture_root,
    resolve_capture_file,
    resolve_session_state_file,
    resolve_raw_tenhou_file,
    # Season
    resolve_season_config_dir,
    resolve_season_config_path,
    list_season_config_paths,
    resolve_season_report_dir,
)


def _setup_authoritative_tree(root: Path) -> Path:
    models = root / "mortal" / "authoritative" / "season_auth_v1" / "models"
    (models / "ext_mortal").mkdir(parents=True, exist_ok=True)
    (models / "K0_70k").mkdir(parents=True, exist_ok=True)
    (models / "V2_74000").mkdir(parents=True, exist_ok=True)

    (models / "ext_mortal" / "external_mortal_20240308_best_min.pth").write_text("ext", encoding="utf-8")
    (models / "K0_70k" / "mortal_default_70k_promoted_candidate.pth").write_text("70k", encoding="utf-8")
    (models / "V2_74000" / "mortal_74000.pth").write_text("v2", encoding="utf-8")
    return root


def test_model_resolver_spec_and_checkpoint(tmp_path: Path, monkeypatch) -> None:
    data_dir = _setup_authoritative_tree(tmp_path / "data")
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("inference.bot_registry.data_root", lambda: data_dir)

    kind, cp = resolve_bot_spec("rulebase", project_dir)
    assert kind == "rulebase"
    assert cp is None

    kind, cp = resolve_bot_spec("ext_mortal", project_dir)
    assert kind == "mortal"
    assert cp == (data_dir / "mortal" / "authoritative" / "season_auth_v1" / "models" / "ext_mortal" / "external_mortal_20240308_best_min.pth").resolve()

    kind, cp = resolve_bot_spec("70k", project_dir)
    assert kind == "mortal"
    assert cp == (data_dir / "mortal" / "authoritative" / "season_auth_v1" / "models" / "K0_70k" / "mortal_default_70k_promoted_candidate.pth").resolve()

    kind, cp = resolve_bot_spec("mortal", project_dir)
    assert kind == "mortal"
    assert cp == (data_dir / "mortal" / "authoritative" / "season_auth_v1" / "models" / "V2_74000" / "mortal_74000.pth").resolve()


def test_ladder_resolver_paths(tmp_path: Path, monkeypatch) -> None:
    ladder_root = tmp_path / "ladder_data"
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(ladder_root))

    assert ladder_data_root() == ladder_root.resolve()
    assert ladder_registry_root() == (ladder_root / "registries").resolve()
    assert ladder_snapshots_root() == (ladder_root / "snapshots").resolve()
    assert ladder_relative_sources_root() == (ladder_root / "relative" / "sources").resolve()
    assert ladder_relative_sources_root("s1") == (ladder_root / "relative" / "sources" / "s1").resolve()

    # resolve / encode
    rel_path = "snapshots/s1/snapshot_latest.json"
    resolved = resolve_ladder_path(rel_path)
    assert resolved == (ladder_root / rel_path).resolve()
    assert encode_ladder_path(resolved) == rel_path

    # specific snapshot/manifest/ledger paths
    snap_path = resolve_ladder_snapshot_path("s1", "20260808_120000")
    assert snap_path == (ladder_root / "snapshots" / "s1" / "snapshot_s1_20260808_120000.json").resolve()

    manifest_path = resolve_ladder_manifest_path("s1")
    assert manifest_path == (ladder_root / "snapshots" / "s1" / "manifest.json").resolve()

    ledger_path = resolve_ledger_path("s1")
    assert ledger_path == (ladder_root / "ledgers" / "s1.jsonl").resolve()


def test_capture_resolver_paths(tmp_path: Path, monkeypatch) -> None:
    ladder_root = tmp_path / "ladder_data"
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(ladder_root))

    cap_root = ladder_capture_root()
    assert cap_root == (ladder_root / "captures" / "playwithyou").resolve()

    cap_file = resolve_capture_file("20260808gm-0001-0000-abcd1234")
    assert cap_file == (cap_root / "pending" / "20260808gm-0001-0000-abcd1234.json").resolve()

    session_file = resolve_session_state_file("sess-test-123")
    assert session_file == (cap_root / "sessions" / "sess-test-123.json").resolve()

    raw_tenhou = resolve_raw_tenhou_file("20260808gm-0001-0000-abcd1234")
    assert raw_tenhou == (cap_root / "raw_tenhou" / "20260808gm-0001-0000-abcd1234.json").resolve()


def test_season_resolver_paths(tmp_path: Path, monkeypatch) -> None:
    registries_dir = tmp_path / "registries"
    registries_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(registries_dir))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))

    (registries_dir / "official-v1.json").write_text("{}", encoding="utf-8")
    (registries_dir / "draft-v2.json").write_text("{}", encoding="utf-8")

    assert resolve_season_config_dir() == registries_dir.resolve()
    assert resolve_season_config_path("official-v1") == (registries_dir / "official-v1.json").resolve()

    paths = list_season_config_paths()
    assert len(paths) == 2
    assert [p.name for p in paths] == ["draft-v2.json", "official-v1.json"]

    report_dir = resolve_season_report_dir("official-v1")
    assert report_dir == (tmp_path / "ladder" / "reports" / "official-v1").resolve()


def test_season_resolver_strict_no_auto_fallback_to_repo_seed(tmp_path, monkeypatch) -> None:
    missing_dir = tmp_path / "non_existent_registries"
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(missing_dir))

    # Production default: strictly points to missing_dir (fail closed) without repo seed fallback
    assert resolve_season_config_dir() == missing_dir.resolve()

    # Explicit allow_repo_seed=True falls back to repo template seed
    repo_seed = resolve_season_config_dir(allow_repo_seed=True)
    assert repo_seed.is_dir()
    assert "workbench" in str(repo_seed)

