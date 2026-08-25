# -*- coding: utf-8 -*-
"""Tests for Frozen Execution Provenance in Season & Match (R13-B).

Verifies the frozen execution provenance architecture:
- Schema validation locking exact provenance shapes (human, local_artifact, external_revision)
- Conversion from CanonicalExecutionProvenance to FrozenExecutionProvenance (zero runtime paths)
- Season-level frozen provenance admission (comparing Seat vs Season provenance)
- Rejection of mismatched versions even when alternate versions are promoted and ladder-eligible
- Immutable ledger persistence (registry updates do not alter historical matches or revision records)
- Revision history captures frozen provenance snapshots across revisions
- Zero runtime paths in Match JSON persistence
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from workbench.participants import ledger, registry
from workbench.participants.execution_provenance import (
    CanonicalExecutionProvenance,
    freeze_execution_provenance,
)
from workbench.participants.ladder_eligibility import (
    LadderAdmissionResult,
    ensure_ladder_eligibility,
    validate_ladder_eligibility,
)
from workbench.participants.schemas import (
    AccountCreate,
    ExternalModelRevisionCreate,
    ExternalModelRevisionUpdate,
    FrozenExecutionProvenance,
    MatchCreate,
    MatchRevise,
    MatchSeat,
    ModelArtifactCreate,
    ModelIdentityCreate,
)


# -----------------------------------------------------------------------------
# 1. Schema Validation Tests (FrozenExecutionProvenance)
# -----------------------------------------------------------------------------

def test_frozen_provenance_human_shape():
    # Valid human
    prov = FrozenExecutionProvenance(kind="human")
    assert prov.kind == "human"
    assert prov.model_identity_id is None
    assert prov.model_artifact_id is None
    assert prov.external_revision_id is None

    # Human with identity -> reject
    with pytest.raises(ValidationError, match="human execution_provenance 不允许携带 model_identity_id"):
        FrozenExecutionProvenance(kind="human", model_identity_id="model:human01")

    # Human with artifact -> reject
    with pytest.raises(ValidationError, match="human execution_provenance 不允许携带 model_artifact_id"):
        FrozenExecutionProvenance(kind="human", model_artifact_id="model:m@art01")

    # Human with revision -> reject
    with pytest.raises(ValidationError, match="human execution_provenance 不允许携带 external_revision_id"):
        FrozenExecutionProvenance(kind="human", external_revision_id="external:mortal:4.1b")


def test_frozen_provenance_local_artifact_shape():
    # Valid local artifact
    prov = FrozenExecutionProvenance(
        kind="local_artifact",
        model_identity_id="model:m70k",
        model_artifact_id="model:m70k@01",
    )
    assert prov.kind == "local_artifact"
    assert prov.model_identity_id == "model:m70k"
    assert prov.model_artifact_id == "model:m70k@01"
    assert prov.external_revision_id is None

    # Missing artifact_id -> reject
    with pytest.raises(ValidationError, match="local_artifact execution_provenance 必须提供 model_artifact_id"):
        FrozenExecutionProvenance(kind="local_artifact", model_identity_id="model:m70k")

    # Missing model_identity_id -> reject
    with pytest.raises(ValidationError, match="local_artifact execution_provenance 必须提供 model_identity_id"):
        FrozenExecutionProvenance(kind="local_artifact", model_artifact_id="model:m70k@01")

    # Local artifact with revision_id -> reject
    with pytest.raises(ValidationError, match="local_artifact execution_provenance 不允许携带 external_revision_id"):
        FrozenExecutionProvenance(
            kind="local_artifact",
            model_identity_id="model:m70k",
            model_artifact_id="model:m70k@01",
            external_revision_id="external:rev1",
        )


def test_frozen_provenance_external_revision_shape():
    # Valid external revision
    prov = FrozenExecutionProvenance(
        kind="external_revision",
        model_identity_id="model:mortal41b",
        external_revision_id="external:mortal41b:4.1b",
    )
    assert prov.kind == "external_revision"
    assert prov.model_identity_id == "model:mortal41b"
    assert prov.external_revision_id == "external:mortal41b:4.1b"
    assert prov.model_artifact_id is None

    # Missing revision_id -> reject
    with pytest.raises(ValidationError, match="external_revision execution_provenance 必须提供 external_revision_id"):
        FrozenExecutionProvenance(kind="external_revision", model_identity_id="model:mortal41b")

    # Missing model_identity_id -> reject
    with pytest.raises(ValidationError, match="external_revision execution_provenance 必须提供 model_identity_id"):
        FrozenExecutionProvenance(kind="external_revision", external_revision_id="external:mortal41b:4.1b")

    # External revision with artifact_id -> reject
    with pytest.raises(ValidationError, match="external_revision execution_provenance 不允许携带 model_artifact_id"):
        FrozenExecutionProvenance(
            kind="external_revision",
            model_identity_id="model:mortal41b",
            external_revision_id="external:mortal41b:4.1b",
            model_artifact_id="model:m70k@01",
        )


def test_freeze_execution_provenance_excludes_runtime_path(tmp_path: Path):
    canonical = CanonicalExecutionProvenance(
        kind="local_artifact",
        account_id="account:bot01",
        model_identity_id="model:m70k",
        model_artifact_id="model:m70k@01",
        resolved_checkpoint_path=tmp_path / "model.pth",
    )
    frozen = freeze_execution_provenance(canonical)
    assert frozen.kind == "local_artifact"
    assert frozen.model_identity_id == "model:m70k"
    assert frozen.model_artifact_id == "model:m70k@01"
    assert not hasattr(frozen, "resolved_checkpoint_path")
    # Verify model_dump does not contain any runtime path
    dump = frozen.model_dump()
    assert "resolved_checkpoint_path" not in dump
    assert "checkpoint" not in dump


# -----------------------------------------------------------------------------
# 2. Season Admission Tests (Seat vs Season Provenance Matching)
# -----------------------------------------------------------------------------

@pytest.fixture
def setup_provenance_env(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_DATA_ROOT", str(data_dir))
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    # 1. Human accounts
    for i in range(1, 4):
        registry.create_account(
            AccountCreate(
                account_id=f"account:human{i}",
                display_name=f"Human {i}",
                account_type="human",
                default_controller="human_ui",
            )
        )

    # 2. Local model with two artifacts
    ckpt1 = tmp_path / "m70k_v1.pth"
    ckpt1.write_text("v1")
    ckpt2 = tmp_path / "m70k_v2.pth"
    ckpt2.write_text("v2")

    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:m70k",
            label="Mortal 70k",
            kind="local_model",
        )
    )
    art1 = registry.add_model_artifact(
        "model:m70k",
        ModelArtifactCreate(
            model_artifact_id="model:m70k@01",
            label="m70k_v1.pth",
            artifact_path=str(ckpt1),
            stage="promoted",
        ),
    )
    art2 = registry.add_model_artifact(
        "model:m70k",
        ModelArtifactCreate(
            model_artifact_id="model:m70k@02",
            label="m70k_v2.pth",
            artifact_path=str(ckpt2),
            stage="promoted",
        ),
    )

    registry.create_account(
        AccountCreate(
            account_id="account:m70k_bot",
            display_name="70k Bot",
            account_type="managed_bot",
            default_controller="local_model",
            model_identity_id="model:m70k",
        )
    )

    # 3. External agent with two promoted revisions
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal",
            label="Mortal AI",
            kind="external_agent",
        )
    )
    rev1 = registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal:4.1b",
            provider="mortal",
            version="4.1b",
            stage="promoted",
        ),
    )
    rev2 = registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal:4.2",
            provider="mortal",
            version="4.2",
            stage="promoted",
        ),
    )

    registry.create_account(
        AccountCreate(
            account_id="account:mortal_bot",
            display_name="Mortal Bot",
            account_type="external_bot",
            default_controller="external_agent",
            model_identity_id="model:mortal",
        )
    )

    return {
        "configs_dir": configs_dir,
        "tmp_path": tmp_path,
        "art1": art1,
        "art2": art2,
        "rev1": rev1,
        "rev2": rev2,
    }


def test_season_external_revision_admission_exact_match(setup_provenance_env):
    """Season freezes Mortal 4.1b; seat matching 4.1b passes; seat requesting 4.2 fails even if 4.2 is promoted."""
    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_external",
        "title": "External Season",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {
                "model_id": "model:mortal",
                "execution_provenance": {
                    "kind": "external_revision",
                    "model_identity_id": "model:mortal",
                    "external_revision_id": "external:mortal:4.1b",
                },
                "accounts": [{"account_id": "account:mortal_bot"}],
            },
        ],
    }
    (configs_dir / "s_external.json").write_text(json.dumps(season_payload), encoding="utf-8")

    # 1. Seat without explicit revision -> automatically resolves to season's frozen 4.1b -> PASS
    seats_implicit = [
        MatchSeat(seat=0, account_id="account:mortal_bot", controller_type="external_agent", model_identity_id="model:mortal"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    admission = ensure_ladder_eligibility("s_external", seats_implicit, registry=registry, project_root=tmp_path)
    assert admission.season_id == "s_external"
    assert admission.provenance_by_seat[0].external_revision_id == "external:mortal:4.1b"

    # 2. Seat explicitly requesting 4.1b -> matches season -> PASS
    seats_explicit_match = [
        MatchSeat(
            seat=0,
            account_id="account:mortal_bot",
            controller_type="external_agent",
            model_identity_id="model:mortal",
            external_revision_id="external:mortal:4.1b",
        ),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    admission2 = ensure_ladder_eligibility("s_external", seats_explicit_match, registry=registry, project_root=tmp_path)
    assert admission2.provenance_by_seat[0].external_revision_id == "external:mortal:4.1b"

    # 3. Seat explicitly requesting 4.2 -> mismatch with season's frozen 4.1b -> REJECT!
    seats_mismatch = [
        MatchSeat(
            seat=0,
            account_id="account:mortal_bot",
            controller_type="external_agent",
            model_identity_id="model:mortal",
            external_revision_id="external:mortal:4.2",
        ),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    with pytest.raises(ValueError, match="外部版本.*与赛季.*不一致"):
        ensure_ladder_eligibility("s_external", seats_mismatch, registry=registry, project_root=tmp_path)


def test_season_local_artifact_admission_exact_match(setup_provenance_env):
    """Season freezes local artifact m70k@01; seat requesting m70k@02 fails even if @02 is promoted."""
    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_local",
        "title": "Local Season",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {
                "model_id": "model:m70k",
                "execution_provenance": {
                    "kind": "local_artifact",
                    "model_identity_id": "model:m70k",
                    "model_artifact_id": "model:m70k@01",
                },
                "accounts": [{"account_id": "account:m70k_bot"}],
            },
        ],
    }
    (configs_dir / "s_local.json").write_text(json.dumps(season_payload), encoding="utf-8")

    # 1. Matching seat with @01 -> PASS
    seats_match = [
        MatchSeat(
            seat=0,
            account_id="account:m70k_bot",
            controller_type="local_model",
            model_identity_id="model:m70k",
            model_artifact_id="model:m70k@01",
        ),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    admission = ensure_ladder_eligibility("s_local", seats_match, registry=registry, project_root=tmp_path)
    assert admission.provenance_by_seat[0].model_artifact_id == "model:m70k@01"

    # 2. Seat with @02 -> Mismatch with season -> REJECT
    seats_mismatch = [
        MatchSeat(
            seat=0,
            account_id="account:m70k_bot",
            controller_type="local_model",
            model_identity_id="model:m70k",
            model_artifact_id="model:m70k@02",
        ),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    with pytest.raises(ValueError, match="模型产物.*与赛季.*不一致"):
        ensure_ladder_eligibility("s_local", seats_mismatch, registry=registry, project_root=tmp_path)


# -----------------------------------------------------------------------------
# 3. Persistence & Ledger Frozen Invariant Tests
# -----------------------------------------------------------------------------

def test_ledger_persists_frozen_execution_provenance_and_immutable_to_registry_changes(setup_provenance_env):
    """Writing a formal match freezes exact FrozenExecutionProvenance into MatchSeat.

    Subsequent updates to registry (new versions, current flags changing, retirement)
    MUST NOT alter the persisted execution provenance of already recorded matches.
    """
    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_formal",
        "title": "Formal Season",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {
                "model_id": "model:mortal",
                "external_revision_id": "external:mortal:4.1b",
                "accounts": [{"account_id": "account:mortal_bot"}],
            },
        ],
    }
    (configs_dir / "s_formal.json").write_text(json.dumps(season_payload), encoding="utf-8")

    # 1. Create match
    seats = [
        MatchSeat(seat=0, account_id="account:mortal_bot", controller_type="external_agent", model_identity_id="model:mortal", external_revision_id="external:mortal:4.1b"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    payload = MatchCreate(
        occurred_at="2026-08-10T12:00:00+08:00",
        game_length="hanchan",
        season_id="s_formal",
        rating_eligible=True,
        seats=seats,
        final_scores=[35000, 25000, 25000, 15000],
    )
    match = ledger.create_match(payload, registry)
    match_id = match.match_id

    # Verify memory object has frozen execution provenance
    seat0 = match.seats[0]
    assert seat0.execution_provenance is not None
    assert seat0.execution_provenance.kind == "external_revision"
    assert seat0.execution_provenance.external_revision_id == "external:mortal:4.1b"
    assert seat0.execution_provenance.model_identity_id == "model:mortal"

    # Verify human seats have human frozen provenance
    assert match.seats[1].execution_provenance == FrozenExecutionProvenance(kind="human")

    # 2. Inspect raw matches.jsonl on disk
    raw_match = ledger.get_match(match_id)
    assert raw_match is not None
    assert raw_match.seats[0].execution_provenance.external_revision_id == "external:mortal:4.1b"

    # Verify raw disk JSON does NOT contain any physical/runtime paths
    matches_file = ledger._matches_path()
    raw_json = matches_file.read_text(encoding="utf-8")
    assert "resolved_checkpoint_path" not in raw_json
    assert "artifact_path" not in raw_json

    # 3. Now mutate registry:
    # Deprecate 4.1b, add 4.3, set 4.3 as current
    registry.deprecate_external_revision("model:mortal", "external:mortal:4.1b")
    registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal:4.3",
            provider="mortal",
            version="4.3",
            stage="promoted",
            is_current=True,
        ),
    )

    # 4. Re-read match from disk / ledger: provenance MUST remain strictly 4.1b!
    reloaded_match = ledger.get_match(match_id)
    assert reloaded_match.seats[0].execution_provenance.external_revision_id == "external:mortal:4.1b"


# -----------------------------------------------------------------------------
# 4. Revision History Snapshots Capturing Frozen Provenance
# -----------------------------------------------------------------------------

def test_revision_history_records_frozen_provenance_snapshots(setup_provenance_env):
    """Match revisions preserve historical frozen provenance in revision snapshots."""
    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    # Season with both mortal 4.1b and 4.2 allowed
    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_multi",
        "title": "Multi Season",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {
                "model_id": "model:mortal",
                "accounts": [{"account_id": "account:mortal_bot"}],
            },
        ],
    }
    (configs_dir / "s_multi.json").write_text(json.dumps(season_payload), encoding="utf-8")

    # 1. Create with Mortal 4.1b
    seats_v1 = [
        MatchSeat(seat=0, account_id="account:mortal_bot", controller_type="external_agent", model_identity_id="model:mortal", external_revision_id="external:mortal:4.1b"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    match = ledger.create_match(
        MatchCreate(
            occurred_at="2026-08-10T12:00:00+08:00",
            game_length="hanchan",
            season_id="s_multi",
            rating_eligible=True,
            seats=seats_v1,
            final_scores=[30000, 25000, 25000, 20000],
        ),
        registry,
    )
    match_id = match.match_id
    assert match.revision == 1

    # 2. Revise to Mortal 4.2
    seats_v2 = [
        MatchSeat(seat=0, account_id="account:mortal_bot", controller_type="external_agent", model_identity_id="model:mortal", external_revision_id="external:mortal:4.2"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    revised_match = ledger.revise_match(
        match_id,
        MatchRevise(
            seats=seats_v2,
            reason="Corrected bot model revision to 4.2",
        ),
        registry,
    )
    assert revised_match.revision == 2
    assert revised_match.seats[0].execution_provenance.external_revision_id == "external:mortal:4.2"

    # 3. Read revision history rows
    revisions_file = ledger._revisions_path()
    lines = [json.loads(line) for line in revisions_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    match_revs = [r for r in lines if r.get("match_id") == match_id]
    assert len(match_revs) == 2

    # Rev 1 snapshot
    rev1_seat0 = match_revs[0]["after"]["seats"][0]
    assert rev1_seat0["execution_provenance"]["external_revision_id"] == "external:mortal:4.1b"

    # Rev 2 snapshot
    rev2_seat0 = match_revs[1]["after"]["seats"][0]
    assert rev2_seat0["execution_provenance"]["external_revision_id"] == "external:mortal:4.2"


# -----------------------------------------------------------------------------
# 5. Intake Confirm Flow with External Revision Frozen Provenance
# -----------------------------------------------------------------------------

def test_intake_confirm_freezes_external_revision_provenance(setup_provenance_env, monkeypatch):
    """Tenhou intake flow: preview resolves candidate identity; confirm with rating_eligible=True freezes exact ExternalModelRevision."""
    from workbench.participants import intake

    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_intake",
        "title": "Intake Season",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {
                "model_id": "model:mortal",
                "external_revision_id": "external:mortal:4.1b",
                "accounts": [{"account_id": "account:mortal_bot"}],
            },
        ],
    }
    (configs_dir / "s_intake.json").write_text(json.dumps(season_payload), encoding="utf-8")

    # Mock download_tenhou6
    monkeypatch.setattr(
        intake,
        "download_tenhou6",
        lambda log_id: {
            "name": ["mortal4.1b", "Human1", "Human2", "Human3"],
            "rule": {"aka": True},
            "log": [
                [[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]],
            ],
        },
    )

    log_id = "2026082520gm-0009-2147-9b52f82a"

    # Preview does NOT freeze authority provenance prematurely
    preview = intake.build_preview(f"https://tenhou.net/3/?log={log_id}")
    assert preview["log_id"] == log_id
    assert len(preview["seats"]) == 4

    # Confirm with formal season
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b", "alias_scope": "none"},
        {"seat": 1, "action": "assign", "account_id": "account:human1", "alias_scope": "none"},
        {"seat": 2, "action": "assign", "account_id": "account:human2", "alias_scope": "none"},
        {"seat": 3, "action": "assign", "account_id": "account:human3", "alias_scope": "none"},
    ]

    result = intake.resolve_and_create_match(
        log_id=log_id,
        resolutions=resolutions,
        season_id="s_intake",
        rating_eligible=True,
    )
    match_id = result["match_id"]
    persisted_match = ledger.get_match(match_id)
    assert persisted_match is not None
    seat0 = persisted_match.seats[0]
    assert seat0.execution_provenance is not None
    assert seat0.execution_provenance.kind == "external_revision"
    assert seat0.execution_provenance.external_revision_id == "external:mortal:4.1b"
    assert seat0.execution_provenance.model_identity_id == "model:mortal"


# -----------------------------------------------------------------------------
# 6. Production-Path Season Registry Lifecycle & Freeze Control Plane Tests
# -----------------------------------------------------------------------------

def test_production_path_season_lifecycle_and_freeze_invariance(setup_provenance_env):
    """Production path: create_season -> set_season_enrollment -> set_season_status('running') -> ensure_ladder_eligibility.

    Verifies:
    1. Single promoted revision automatically freezes into season JSON execution_provenance.
    2. Subsequent changes to registry (new versions, current flag changes) do NOT mutate the season.
    3. Adding accounts to running season preserves the original frozen execution_provenance without drifting.
    """
    from workbench.replay import season_registry as sr

    configs_dir = setup_provenance_env["configs_dir"]
    tmp_path = setup_provenance_env["tmp_path"]

    # 1. Setup isolated model with exactly 1 promoted revision
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:single_mortal",
            label="Single Mortal",
            kind="external_agent",
        )
    )
    registry.add_external_revision(
        "model:single_mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:single_mortal:4.1b",
            provider="mortal",
            version="4.1b",
            stage="promoted",
        ),
    )
    registry.create_account(
        AccountCreate(
            account_id="account:single_mortal_bot",
            display_name="Single Mortal Bot",
            account_type="external_bot",
            default_controller="external_agent",
            model_identity_id="model:single_mortal",
        )
    )

    # 2. Production control plane: create season & enroll
    sr.create_season(configs_dir, season_id="s_prod_life", title="Prod Season")
    season = sr.set_season_enrollment(
        configs_dir,
        "s_prod_life",
        ["account:human1", "account:human2", "account:human3", "account:single_mortal_bot"],
    )

    # Verify season JSON generated FrozenExecutionProvenance
    mortal_group = next(m for m in season["models"] if m["model_id"] == "model:single_mortal")
    assert mortal_group["execution_provenance"]["kind"] == "external_revision"
    assert mortal_group["execution_provenance"]["external_revision_id"] == "external:single_mortal:4.1b"
    assert mortal_group["execution_provenance"]["model_identity_id"] == "model:single_mortal"

    # Transition to running
    running_season = sr.set_season_status(configs_dir, "s_prod_life", "running")
    assert running_season["status"] == "running"

    # 3. Mutate registry: add 4.2 promoted and current
    registry.add_external_revision(
        "model:single_mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:single_mortal:4.2",
            provider="mortal",
            version="4.2",
            stage="promoted",
            is_current=True,
        ),
    )

    # 4. Enroll another human account into running season
    registry.create_account(
        AccountCreate(
            account_id="account:human4",
            display_name="Human 4",
            account_type="human",
            default_controller="human_ui",
        )
    )
    updated_running = sr.set_season_enrollment(
        configs_dir,
        "s_prod_life",
        ["account:human1", "account:human2", "account:human3", "account:human4", "account:single_mortal_bot"],
    )
    # Frozen execution_provenance MUST NOT drift to 4.2!
    mortal_group_after = next(m for m in updated_running["models"] if m["model_id"] == "model:single_mortal")
    assert mortal_group_after["execution_provenance"]["external_revision_id"] == "external:single_mortal:4.1b"

    # 5. Ladder admission: 4.1b passes, 4.2 rejects
    seats_41b = [
        MatchSeat(seat=0, account_id="account:single_mortal_bot", controller_type="external_agent", model_identity_id="model:single_mortal", external_revision_id="external:single_mortal:4.1b"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    admission = ensure_ladder_eligibility("s_prod_life", seats_41b, registry=registry, project_root=tmp_path)
    assert admission.provenance_by_seat[0].external_revision_id == "external:single_mortal:4.1b"

    seats_42 = [
        MatchSeat(seat=0, account_id="account:single_mortal_bot", controller_type="external_agent", model_identity_id="model:single_mortal", external_revision_id="external:single_mortal:4.2"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]
    with pytest.raises(ValueError, match="外部版本.*与赛季.*不一致"):
        ensure_ladder_eligibility("s_prod_life", seats_42, registry=registry, project_root=tmp_path)


def test_season_enrollment_ambiguity_and_explicit_provenance(setup_provenance_env):
    """When a model identity has >1 promoted revisions, enrollment without explicit provenance fails closed; explicit provenance succeeds."""
    from workbench.replay import season_registry as sr

    configs_dir = setup_provenance_env["configs_dir"]

    # model:mortal has both 4.1b and 4.2 promoted
    sr.create_season(configs_dir, season_id="s_ambig", title="Ambig Season")

    # 1. Unspecified provenance -> fails closed with ambiguity error
    with pytest.raises(ValueError, match="存在多个晋升版本.*存在歧义.*必须显式指定 external_revision_id"):
        sr.set_season_enrollment(
            configs_dir,
            "s_ambig",
            ["account:human1", "account:human2", "account:human3", "account:mortal_bot"],
        )

    # 2. Explicitly specify 4.1b -> succeeds
    season = sr.set_season_enrollment(
        configs_dir,
        "s_ambig",
        ["account:human1", "account:human2", "account:human3", "account:mortal_bot"],
        provenance_by_model={
            "model:mortal": {
                "kind": "external_revision",
                "model_identity_id": "model:mortal",
                "external_revision_id": "external:mortal:4.1b",
            }
        },
    )
    m_group = next(m for m in season["models"] if m["model_id"] == "model:mortal")
    assert m_group["execution_provenance"]["external_revision_id"] == "external:mortal:4.1b"


def test_season_registry_loader_validates_frozen_execution_provenance_structure(setup_provenance_env):
    """Malformed or invalid execution_provenance in season JSON is rejected by registry loader."""
    from workbench.replay.ladder import SeasonRegistryError, get_season_config, read_registry

    configs_dir = setup_provenance_env["configs_dir"]

    # 1. Invalid shape: external_revision missing external_revision_id
    bad_payload1 = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_bad1",
        "report_dir": "reports/s_bad1",
        "models": [
            {
                "model_id": "model:mortal",
                "execution_provenance": {
                    "kind": "external_revision",
                    "model_identity_id": "model:mortal",
                    # missing external_revision_id!
                },
                "accounts": [{"account_id": "account:mortal_bot"}],
            }
        ],
    }
    path1 = configs_dir / "s_bad1.json"
    path1.write_text(json.dumps(bad_payload1), encoding="utf-8")
    with pytest.raises(SeasonRegistryError, match="execution_provenance 结构无效"):
        read_registry(path1)

    # 2. Mismatched model_identity_id in local_artifact provenance
    bad_payload2 = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s_bad2",
        "report_dir": "reports/s_bad2",
        "models": [
            {
                "model_id": "model:m70k",
                "execution_provenance": {
                    "kind": "local_artifact",
                    "model_identity_id": "model:other_model",
                    "model_artifact_id": "model:other_model@01",
                },
                "accounts": [{"account_id": "account:m70k_bot"}],
            }
        ],
    }
    path2 = configs_dir / "s_bad2.json"
    path2.write_text(json.dumps(bad_payload2), encoding="utf-8")
    with pytest.raises(SeasonRegistryError, match="local_artifact model_identity_id.*与 group.*不一致"):
        read_registry(path2)


def test_running_status_transition_rejects_missing_frozen_provenance(setup_provenance_env):
    """Transitioning to running rejects any season containing model groups without valid execution_provenance."""
    from workbench.replay import season_registry as sr
    from workbench.replay.ladder import SeasonRegistryError

    configs_dir = setup_provenance_env["configs_dir"]

    sr.create_season(configs_dir, season_id="s_missing_prov", title="Missing Prov Season")
    path = configs_dir / "s_missing_prov.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Manually inject an un-frozen model group
    raw["models"] = [
        {"model_id": "model:mortal", "accounts": [{"account_id": "account:mortal_bot"}]}
    ]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SeasonRegistryError, match="缺少完整不可变的 execution_provenance，禁止开赛"):
        sr.set_season_status(configs_dir, "s_missing_prov", "running")


