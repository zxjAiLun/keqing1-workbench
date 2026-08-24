# -*- coding: utf-8 -*-
"""R12-D: Model Artifact Lifecycle and Purpose Classification tests."""
from __future__ import annotations

from pathlib import Path
import pytest

from participants import registry
from participants.schemas import (
    AccountCreate,
    ModelArtifact,
    ModelArtifactCreate,
    ModelArtifactUpdate,
    ModelIdentityCreate,
    MatchSeat,
)
from participants.ladder_eligibility import validate_ladder_eligibility


@pytest.fixture
def participants_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "participants"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    return data_dir


def test_model_artifact_stage_capabilities_schema() -> None:
    # 1. candidate
    cand = ModelArtifact(
        model_artifact_id="art-cand",
        label="cand",
        model_identity_id="m1",
        stage="candidate",
        created_at="2026-08-08T00:00:00+08:00",
    )
    assert not cand.is_ladder_eligible
    assert cand.is_playwithyou_allowed
    assert cand.is_review_allowed

    # 2. promoted
    prom = ModelArtifact(
        model_artifact_id="art-prom",
        label="prom",
        model_identity_id="m1",
        stage="promoted",
        created_at="2026-08-08T00:00:00+08:00",
    )
    assert prom.is_ladder_eligible
    assert prom.is_playwithyou_allowed
    assert prom.is_review_allowed

    # 3. deprecated
    depr = ModelArtifact(
        model_artifact_id="art-depr",
        label="depr",
        model_identity_id="m1",
        stage="deprecated",
        created_at="2026-08-08T00:00:00+08:00",
    )
    assert not depr.is_ladder_eligible
    assert depr.is_playwithyou_allowed
    assert depr.is_review_allowed

    # 4. retired
    ret = ModelArtifact(
        model_artifact_id="art-ret",
        label="ret",
        model_identity_id="m1",
        stage="retired",
        created_at="2026-08-08T00:00:00+08:00",
    )
    assert not ret.is_ladder_eligible
    assert not ret.is_playwithyou_allowed
    assert ret.is_review_allowed


def test_model_artifact_lifecycle_transitions(participants_env) -> None:
    ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="mortal_v4",
            label="Mortal V4",
            kind="local_model",
            artifact_path="artifacts/mortal_v4_exp1.pth",
            stage="candidate",
        )
    )
    assert len(ident.artifacts) == 1
    art_id = ident.artifacts[0].model_artifact_id
    assert ident.artifacts[0].stage == "candidate"

    # Promote to production
    promoted = registry.promote_model_artifact("mortal_v4", art_id)
    assert promoted.stage == "promoted"
    assert promoted.is_current is True
    assert promoted.is_ladder_eligible

    # Deprecate
    deprecated = registry.deprecate_model_artifact("mortal_v4", art_id)
    assert deprecated.stage == "deprecated"
    assert not deprecated.is_ladder_eligible
    assert deprecated.is_playwithyou_allowed

    # Retire
    retired = registry.retire_model_artifact("mortal_v4", art_id)
    assert retired.stage == "retired"
    assert retired.retired_at is not None
    assert retired.is_current is False
    assert not retired.is_playwithyou_allowed


def test_ladder_eligibility_blocks_non_promoted_and_retired_artifacts(
    participants_env, tmp_path, monkeypatch
) -> None:
    # Setup test season
    configs_dir = tmp_path / "registries"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    season_id = "test-season-v1"
    season_file = configs_dir / f"{season_id}.json"
    ckpt_path = tmp_path / "models" / "mortal.pth"
    ckpt_path.parent.mkdir(parents=True, exist_ok=True)
    ckpt_path.write_text("model_bytes", encoding="utf-8")

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": season_id,
        "title": "Test Season",
        "status": "running",
        "default": True,
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {"sources_root": "sources", "participants": {"enabled": True, "exclusive": True}},
        "models": [
            {
                "model_id": "human",
                "accounts": [{"account_id": "human@01", "display_name": "Human"}],
            },
            {
                "model_id": "mortal_ai",
                "checkpoint": str(ckpt_path),
                "accounts": [
                    {"account_id": "ai@01", "display_name": "AI 1"},
                    {"account_id": "ai@02", "display_name": "AI 2"},
                    {"account_id": "ai@03", "display_name": "AI 3"},
                ],
            },
        ],
    }
    import json
    season_file.write_text(json.dumps(season_payload), encoding="utf-8")

    # 1. Create Model Identity & Artifact with candidate stage FIRST (referential integrity)
    ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="mortal_identity",
            label="Mortal Identity",
            kind="local_model",
            artifact_path=str(ckpt_path),
            stage="candidate",
        )
    )
    art_id = ident.artifacts[0].model_artifact_id

    # 2. Create Accounts
    registry.create_account(AccountCreate(account_id="human@01", display_name="Human", account_type="human"))
    for aid in ("ai@01", "ai@02", "ai@03"):
        registry.create_account(AccountCreate(account_id=aid, display_name=aid, account_type="managed_bot", model_identity_id="mortal_identity"))

    seats = [
        MatchSeat(seat=0, account_id="human@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="ai@01", controller_type="local_model", model_identity_id="mortal_identity", model_artifact_id=art_id),
        MatchSeat(seat=2, account_id="ai@02", controller_type="local_model", model_identity_id="mortal_identity", model_artifact_id=art_id),
        MatchSeat(seat=3, account_id="ai@03", controller_type="local_model", model_identity_id="mortal_identity", model_artifact_id=art_id),
    ]

    # Candidate artifact -> rejected
    with pytest.raises(ValueError, match="无正式天梯参赛资格"):
        validate_ladder_eligibility(season_id, seats, registry=registry, project_root=tmp_path)

    # Missing artifact on candidate identity -> resolved to candidate artifact -> rejected (fail closed: cannot bypass lifecycle by omitting artifact)
    seats_no_artifact = [
        MatchSeat(seat=0, account_id="human@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="ai@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="ai@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="ai@03", controller_type="local_model"),
    ]
    with pytest.raises(ValueError, match="无正式天梯参赛资格"):
        validate_ladder_eligibility(season_id, seats_no_artifact, registry=registry, project_root=tmp_path)

    # Promote artifact -> accepted
    registry.promote_model_artifact("mortal_identity", art_id)
    validate_ladder_eligibility(season_id, seats, registry=registry, project_root=tmp_path)

    # Retire artifact -> rejected
    registry.retire_model_artifact("mortal_identity", art_id)
    with pytest.raises(ValueError, match="无正式天梯参赛资格"):
        validate_ladder_eligibility(season_id, seats, registry=registry, project_root=tmp_path)


def test_retired_artifact_blocks_current_and_transitions(participants_env) -> None:
    ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="m_arch",
            label="Arch",
            kind="local_model",
            artifact_path="artifacts/arch.pth",
            stage="promoted",
        )
    )
    art_id = ident.artifacts[0].model_artifact_id
    registry.retire_model_artifact("m_arch", art_id)

    # Cannot set retired artifact as current
    with pytest.raises(ValueError, match="无法将已退役"):
        registry.set_current_model_artifact("m_arch", art_id)

    # Cannot transition out of retired stage
    with pytest.raises(ValueError, match="已封存"):
        registry.promote_model_artifact("m_arch", art_id)


def test_explicit_empty_capabilities_respected() -> None:
    art = ModelArtifact(
        model_artifact_id="art-custom",
        label="custom",
        model_identity_id="m1",
        stage="promoted",
        capabilities=[],
        created_at="2026-08-08T00:00:00+08:00",
    )
    assert art.capabilities == []
    assert not art.is_ladder_eligible
    assert not art.is_playwithyou_allowed
    assert not art.is_review_allowed


def test_playwithyou_model_only_launchers_blocks_retired_artifact(tmp_path, monkeypatch) -> None:
    from gateway.api import playwithyou as pw

    data_dir = tmp_path / "participants"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)

    # Create model artifact for 70k and retire it
    ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="70k",
            label="70k",
            kind="local_model",
            artifact_path="K0_70k/mortal_default_70k_promoted_candidate.pth",
            stage="promoted",
        )
    )
    art_id = ident.artifacts[0].model_artifact_id
    registry.retire_model_artifact("70k", art_id)

    # Mock resolve_bot_spec to return the same path
    fake_cp = tmp_path / "K0_70k" / "mortal_default_70k_promoted_candidate.pth"
    fake_cp.parent.mkdir(parents=True, exist_ok=True)
    fake_cp.write_text("model_data", encoding="utf-8")
    monkeypatch.setattr("workbench.runtime.resolver.resolve_bot_spec", lambda spec, root: ("mortal", fake_cp))
    monkeypatch.setattr(pw, "_resolve_spec", lambda network, custom_paths, slot: "70k")

    # Start request with launchers=[{model_id: "70k"}] must be rejected
    roster_entry = {"launcher_slot": 0, "model_id": "70k", "controller_type": "local_model"}
    with pytest.raises(ValueError, match="已退役"):
        pw._freeze_launcher_models([roster_entry], [str(fake_cp)])

