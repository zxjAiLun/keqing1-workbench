# -*- coding: utf-8 -*-
"""R12-D: Model Artifact Lifecycle and Purpose Classification tests."""
from __future__ import annotations

import json
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

    # 1. Cannot set retired artifact as current
    with pytest.raises(ValueError, match="无法将已退役"):
        registry.set_current_model_artifact("m_arch", art_id)

    # 2. Cannot transition out of retired stage
    with pytest.raises(ValueError, match="已封存"):
        registry.promote_model_artifact("m_arch", art_id)

    # 3. Cannot mutate capabilities, label, or stage on sealed retired artifact
    from workbench.participants.schemas import ModelArtifactUpdate

    for update_payload in [
        ModelArtifactUpdate(capabilities=[]),
        ModelArtifactUpdate(capabilities=["review_allowed", "playwithyou_allowed"]),
        ModelArtifactUpdate(label="Modified Label"),
        ModelArtifactUpdate(is_current=True),
        ModelArtifactUpdate(stage="deprecated"),
    ]:
        with pytest.raises(ValueError, match="已封存，不可变更任何字段"):
            registry.update_model_artifact("m_arch", art_id, update_payload)

    # Invariant check: capabilities and stage remain completely unmodified
    ident_refreshed = registry.get_model_identity("m_arch")
    art_refreshed = next(a for a in ident_refreshed.artifacts if a.model_artifact_id == art_id)
    assert art_refreshed.stage == "retired"
    assert art_refreshed.capabilities == ["review_allowed"]
    assert art_refreshed.is_current is False


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
    with pytest.raises(ValueError, match="已退役|无法用于 Play-with-you"):
        pw._freeze_launcher_models([roster_entry], [str(fake_cp)])


def test_artifact_policy_resolver_adversarial_matrix(participants_env, tmp_path, monkeypatch) -> None:
    from participants.artifact_policy import resolve_artifact_binding

    # Setup files
    cp1 = tmp_path / "models" / "cp1.pth"
    cp2 = tmp_path / "models" / "cp2.pth"
    cp1.parent.mkdir(parents=True, exist_ok=True)
    cp1.write_text("model1", encoding="utf-8")
    cp2.write_text("model2", encoding="utf-8")

    # 1. Nonexistent identity -> rejects
    with pytest.raises(ValueError, match="0 match|未找到匹配"):
        resolve_artifact_binding(
            identity_id="nonexistent_id",
            purpose="review_allowed",
            project_root=tmp_path,
            registry=registry,
        )

    # 2. Identity with zero artifacts -> rejects
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ident_empty",
            label="Empty Identity",
            kind="local_model",
        )
    )
    # create_model_identity without artifact_path does not create artifacts
    with pytest.raises(ValueError, match="0 match|未找到匹配"):
        resolve_artifact_binding(
            identity_id="ident_empty",
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )

    # 3. Create two identities with distinct artifacts
    i1 = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ident_1",
            label="Ident 1",
            kind="local_model",
            artifact_path=str(cp1),
            stage="promoted",
        )
    )
    art1_id = i1.artifacts[0].model_artifact_id

    i2 = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ident_2",
            label="Ident 2",
            kind="local_model",
            artifact_path=str(cp2),
            stage="candidate",
        )
    )
    art2_id = i2.artifacts[0].model_artifact_id

    # 4. Artifact belonging to another identity -> rejects
    with pytest.raises(ValueError, match="不属于身份"):
        resolve_artifact_binding(
            identity_id="ident_1",
            artifact_id=art2_id,
            purpose="review_allowed",
            project_root=tmp_path,
            registry=registry,
        )

    # 5. Checkpoint mismatch -> rejects
    with pytest.raises(ValueError, match="不一致"):
        resolve_artifact_binding(
            identity_id="ident_1",
            artifact_id=art1_id,
            checkpoint=str(cp2),
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )

    # 6. Candidate stage for ladder_eligible -> rejects
    with pytest.raises(ValueError, match="无正式天梯参赛资格"):
        resolve_artifact_binding(
            identity_id="ident_2",
            artifact_id=art2_id,
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )

    # 7. Candidate stage for playwithyou_allowed -> accepts
    b_pwy = resolve_artifact_binding(
        identity_id="ident_2",
        artifact_id=art2_id,
        purpose="playwithyou_allowed",
        project_root=tmp_path,
        registry=registry,
    )
    assert b_pwy.model_identity_id == "ident_2"
    assert b_pwy.model_artifact_id == art2_id
    assert b_pwy.resolved_checkpoint_path == cp2.resolve()

    # 8. Promoted stage for ladder_eligible -> accepts exactly one
    b_ladder = resolve_artifact_binding(
        identity_id="ident_1",
        artifact_id=art1_id,
        purpose="ladder_eligible",
        project_root=tmp_path,
        registry=registry,
    )
    assert b_ladder.model_identity_id == "ident_1"
    assert b_ladder.model_artifact_id == art1_id
    assert b_ladder.resolved_checkpoint_path == cp1.resolve()

    # 9. Retired stage -> blocks playwithyou but allows review
    registry.retire_model_artifact("ident_1", art1_id)
    with pytest.raises(ValueError, match="无法用于 Play-with-you"):
        resolve_artifact_binding(
            identity_id="ident_1",
            artifact_id=art1_id,
            purpose="playwithyou_allowed",
            project_root=tmp_path,
            registry=registry,
        )
    b_rev = resolve_artifact_binding(
        identity_id="ident_1",
        artifact_id=art1_id,
        purpose="review_allowed",
        project_root=tmp_path,
        registry=registry,
    )
    assert b_rev.model_artifact_id == art1_id

    # 10. Ambiguous match (>1 candidates with same checkpoint, even if exactly one is current) -> must reject
    cp_shared = tmp_path / "models" / "shared.pth"
    cp_shared.write_text("shared", encoding="utf-8")
    i_amb1 = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ident_amb1",
            label="Amb 1",
            kind="local_model",
            artifact_path=str(cp_shared),
            stage="promoted",
        )
    )
    # Add second artifact under same identity pointing to same checkpoint
    art_amb2 = registry.add_model_artifact(
        "ident_amb1",
        ModelArtifactCreate(
            label="Amb 1 second",
            artifact_path=str(cp_shared),
            stage="promoted",
        ),
    )
    # Now ident_amb1 has 2 artifacts for cp_shared; exactly 1 is current.
    # Policy must strictly reject without identity_id + artifact_id narrow down
    with pytest.raises(ValueError, match="模型产物存在歧义"):
        resolve_artifact_binding(
            identity_id="ident_amb1",
            checkpoint=str(cp_shared),
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )

    # 11. Two records pointing to nonexistent checkpoint -> both fail canonical resolution -> 0 match / parse reject
    nonexistent_path = "models/ghost_nonexistent_12345.pth"
    i_ghost1 = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="ident_ghost1",
            label="Ghost 1",
            kind="local_model",
            artifact_path=nonexistent_path,
            stage="promoted",
        )
    )
    with pytest.raises(ValueError, match="无法解析 checkpoint 路径|未找到匹配"):
        resolve_artifact_binding(
            identity_id="ident_ghost1",
            checkpoint=nonexistent_path,
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )

    # 12. Invalid checkpoint alias -> fails closed
    with pytest.raises(ValueError, match="无法解析 checkpoint 路径|未找到匹配"):
        resolve_artifact_binding(
            checkpoint="completely_invalid_alias_and_not_file",
            purpose="ladder_eligible",
            project_root=tmp_path,
            registry=registry,
        )


def test_ladder_rejects_explicit_nonexistent_seat_identity(participants_env, tmp_path, monkeypatch) -> None:
    from participants.ladder_eligibility import validate_ladder_eligibility

    season_id = "test-season-e2e"
    # Create running season config
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": season_id,
        "status": "running",
        "report_dir": str(tmp_path / "reports" / season_id),
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "human@01"}]},
            {
                "model_id": "mortal_70k",
                "checkpoint": "checkpoints/70k.pth",
                "accounts": [
                    {"account_id": "ai@01"},
                    {"account_id": "ai@02"},
                    {"account_id": "ai@03"},
                ],
            },
        ],
    }
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / f"{season_id}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(cfg_dir))

    cp_file = tmp_path / "checkpoints" / "70k.pth"
    cp_file.parent.mkdir(parents=True, exist_ok=True)
    cp_file.write_text("model_data", encoding="utf-8")

    # Create legal account & legal promoted identity
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="mortal_70k",
            label="70k",
            kind="local_model",
            artifact_path=str(cp_file),
            stage="promoted",
        )
    )
    for acc, acc_type in [
        ("human@01", "human"),
        ("ai@01", "managed_bot"),
        ("ai@02", "managed_bot"),
        ("ai@03", "managed_bot"),
    ]:
        registry.create_account(
            AccountCreate(
                account_id=acc,
                display_name=acc,
                account_type=acc_type,
                model_identity_id="mortal_70k" if acc_type != "human" else None,
            )
        )

    # Valid seats pass
    valid_seats = [
        MatchSeat(seat=0, account_id="human@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="ai@01", controller_type="local_model"),
        MatchSeat(seat=2, account_id="ai@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="ai@03", controller_type="local_model"),
    ]
    validate_ladder_eligibility(season_id, valid_seats, registry=registry, project_root=tmp_path)

    # Explicit nonexistent model_identity_id provided on seat -> MUST NOT fallback to season model -> REJECT
    bad_seat_explicit_identity = [
        MatchSeat(seat=0, account_id="human@01", controller_type="human_ui"),
        MatchSeat(seat=1, account_id="ai@01", controller_type="local_model", model_identity_id="does-not-exist"),
        MatchSeat(seat=2, account_id="ai@02", controller_type="local_model"),
        MatchSeat(seat=3, account_id="ai@03", controller_type="local_model"),
    ]
    with pytest.raises(ValueError, match="未找到匹配的模型产物|不存在"):
        validate_ladder_eligibility(season_id, bad_seat_explicit_identity, registry=registry, project_root=tmp_path)


def test_external_revision_lifecycle_e2e_isolation(participants_env, tmp_path, monkeypatch) -> None:
    """R13-D E2E: External Revision lifecycle transitions, season immutability, and intake isolation."""
    from workbench.replay import season_registry as sr
    from workbench.participants import intake
    from workbench.participants.schemas import (
        AccountCreate,
        ExternalModelRevisionCreate,
        ExternalModelRevisionUpdate,
        ModelIdentityCreate,
    )

    configs_dir = tmp_path / "registries"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    # 1. Register accounts & external model with 4.1b
    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:mortal", label="Mortal Agent", kind="external_agent"))
    for i in range(1, 4):
        registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    registry.create_account(AccountCreate(account_id="account:mortal_bot", display_name="MortalBot", account_type="external_bot", model_identity_id="model:mortal"))

    rev41 = registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(provider="mortal", version="4.1b", is_current=True, stage="promoted"),
    )
    assert rev41.external_revision_id == "external:mortal:4.1b"

    # 2. Lock Season to 4.1b
    sr.create_season(configs_dir, season_id="s_mortal_season", title="Mortal Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_mortal_season",
        ["account:h1", "account:h2", "account:h3", "account:mortal_bot"],
        provenance_by_model={
            "model:mortal": {
                "kind": "external_revision",
                "model_identity_id": "model:mortal",
                "external_revision_id": "external:mortal:4.1b",
            }
        },
    )
    sr.set_season_status(configs_dir, "s_mortal_season", "running")

    # Snapshot season config
    season_before = sr.get_season(configs_dir, "s_mortal_season")
    mortal_group_before = next(m for m in season_before["models"] if m.get("model_identity_id") == "model:mortal")
    assert mortal_group_before["execution_provenance"]["external_revision_id"] == "external:mortal:4.1b"

    # 3. Add new revision 4.2 and set current to 4.2 in catalog
    rev42 = registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(provider="mortal", version="4.2", is_current=True, stage="promoted"),
    )
    assert rev42.external_revision_id == "external:mortal:4.2"
    ident = registry.get_model_identity("model:mortal")
    curr = next(r for r in ident.external_revisions if r.is_current)
    assert curr.version == "4.2"

    # 4. Verified: Season config remains completely untouched & immutable (still locked to 4.1b)
    season_after = sr.get_season(configs_dir, "s_mortal_season")
    mortal_group_after = next(m for m in season_after["models"] if m.get("model_identity_id") == "model:mortal")
    assert mortal_group_after["execution_provenance"]["external_revision_id"] == "external:mortal:4.1b"

    # Mock tenhou download
    monkeypatch.setattr(
        intake,
        "download_tenhou6",
        lambda log_id: {
            "name": ["Bot", "H1", "H2", "H3"],
            "rule": {"aka": True},
            "log": [[[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]]],
        },
    )

    # 5. Intake assessment on s_mortal_season:
    # 5a. Seat with 4.1b -> PASS (ready)
    res_41 = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:h1"},
        {"seat": 2, "action": "assign", "account_id": "account:h2"},
        {"seat": 3, "action": "assign", "account_id": "account:h3"},
    ]
    assess_41 = intake.assess_intake_admission(
        log_id="20260804gm-test-r13d-41",
        resolutions=res_41,
        season_id="s_mortal_season",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assess_41.state == "ready"
    assert assess_41.seats[0].frozen_provenance.external_revision_id == "external:mortal:4.1b"

    # 5b. Seat with 4.2 -> BLOCKED (season mismatch)
    res_42 = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.2"},
        {"seat": 1, "action": "assign", "account_id": "account:h1"},
        {"seat": 2, "action": "assign", "account_id": "account:h2"},
        {"seat": 3, "action": "assign", "account_id": "account:h3"},
    ]
    assess_42 = intake.assess_intake_admission(
        log_id="20260804gm-test-r13d-42",
        resolutions=res_42,
        season_id="s_mortal_season",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assess_42.state == "blocked"
    assert any("external:mortal:4.2" in iss.message and "不一致" in iss.message for iss in assess_42.issues)



