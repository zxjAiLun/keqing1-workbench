# -*- coding: utf-8 -*-
"""R13 Final Seal: 跨层全生命周期矩阵、目录当前与赛季权威隔离性、及生产状态只读不变性审计。

涵盖核心契约：
1. 跨层生命周期矩阵 (Local & External): candidate -> promoted/current -> season enrollment -> frozen provenance -> retired
   - 封存后历史 Match / Season 中的 provenance 绝不漂移；
   - 退休版本/产物自动被新赛季 admission 拒绝；
2. 目录当前 (Catalog Current) != 赛季权威版本 (Season Authority):
   - 赛季冻结版本 A 后，目录 current 切到版本 B；
   - 旧赛季权威配置依然不可变锁定版本 A，且旧赛季 intake assessment 依然只放行版本 A；
3. P2 Contract Extra Forbid:
   - ModelArtifactCreate 与 ExternalModelRevisionCreate 收到非法字段时 fail-closed 抛出 422；
4. Production State Read-Only Invariant Audit:
   - 生产数据只读审计：所有 models / accounts / seasons / frozen provenance 均结构完好、引用闭包合规。
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from workbench.participants import aliases, execution_provenance, intake, ledger, registry
from workbench.participants.schemas import (
    AccountCreate,
    ExternalModelRevisionCreate,
    ExternalModelRevisionUpdate,
    MatchSeat,
    ModelArtifactCreate,
    ModelArtifactUpdate,
    ModelIdentityCreate,
)
from workbench.replay import season_registry as sr


@pytest.fixture
def vertical_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "registries"
    data_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    # Mock download_tenhou6
    monkeypatch.setattr(
        intake,
        "download_tenhou6",
        lambda log_id: {
            "name": ["P0", "P1", "P2", "P3"],
            "rule": {"aka": True},
            "log": [[[0, 0, 0], [25000, 25000, 25000, 25000], [], [], [], [], [], [], [], [], [], [], [], [], [], [], ["和了", [5000, -5000, 0, 0], [0, 1]]]],
        },
    )

    return {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "configs_dir": configs_dir,
    }


def test_cross_layer_lifecycle_matrix_external_revision(vertical_env):
    """External Revision: candidate -> promoted/current -> season enrollment -> frozen provenance -> retired."""
    tmp_path = vertical_env["tmp_path"]
    configs_dir = vertical_env["configs_dir"]

    # 1. Candidate revision
    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:mortal_v", label="Mortal V", kind="external_agent"))
    for i in range(1, 4):
        registry.create_account(AccountCreate(account_id=f"account:hum{i}", display_name=f"Human{i}", account_type="human"))
    registry.create_account(AccountCreate(account_id="account:mortal_bot_v", display_name="MBot", account_type="external_bot", model_identity_id="model:mortal_v"))

    rev_cand = registry.add_external_revision(
        "model:mortal_v",
        ExternalModelRevisionCreate(provider="mortal", version="4.0-cand", stage="candidate", is_current=False),
    )
    # Candidate revision is NOT ladder eligible
    with pytest.raises(ValueError, match="不具备天梯资格"):
        execution_provenance.resolve_execution_provenance(
            account_id="account:mortal_bot_v",
            purpose="ladder_eligible",
            external_revision_id=rev_cand.external_revision_id,
            registry=registry,
        )

    # 2. Promoted revision 4.1b & season enrollment
    rev_prom = registry.add_external_revision(
        "model:mortal_v",
        ExternalModelRevisionCreate(provider="mortal", version="4.1b", stage="promoted", is_current=True),
    )
    sr.create_season(configs_dir, season_id="s_v_season", title="Vertical Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_v_season",
        ["account:hum1", "account:hum2", "account:hum3", "account:mortal_bot_v"],
        provenance_by_model={
            "model:mortal_v": {
                "kind": "external_revision",
                "model_identity_id": "model:mortal_v",
                "external_revision_id": "external:mortal_v:4.1b",
            }
        },
    )
    sr.set_season_status(configs_dir, "s_v_season", "running")

    # 3. Import match with rating_eligible=True -> frozen provenance in match
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot_v", "model_identity_id": "model:mortal_v", "external_revision_id": "external:mortal_v:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:hum1"},
        {"seat": 2, "action": "assign", "account_id": "account:hum2"},
        {"seat": 3, "action": "assign", "account_id": "account:hum3"},
    ]
    created = intake.resolve_and_create_match(
        log_id="20260804gm-v-ext-01",
        resolutions=resolutions,
        season_id="s_v_season",
        rating_eligible=True,
    )
    match = ledger.get_match(created["match_id"])
    assert match.seats[0].execution_provenance.kind == "external_revision"
    assert match.seats[0].execution_provenance.external_revision_id == "external:mortal_v:4.1b"

    # 4. Retire revision 4.1b in catalog
    registry.retire_external_revision("model:mortal_v", rev_prom.external_revision_id)

    # 5. INVARIANT: Historical Match and Season remains completely immutable
    reloaded_match = ledger.get_match(match.match_id)
    assert reloaded_match.seats[0].execution_provenance.external_revision_id == "external:mortal_v:4.1b"
    season_cfg = sr.get_season(configs_dir, "s_v_season")
    m_grp = next(m for m in season_cfg["models"] if m.get("model_identity_id") == "model:mortal_v")
    assert m_grp["execution_provenance"]["external_revision_id"] == "external:mortal_v:4.1b"

    # 6. INVARIANT: New admission with retired revision is strictly rejected
    sr.create_season(configs_dir, season_id="s_v_new_season", title="New Season")
    with pytest.raises(ValueError, match="已退役|不具备天梯资格"):
        sr.set_season_enrollment(
            configs_dir,
            "s_v_new_season",
            ["account:hum1", "account:hum2", "account:hum3", "account:mortal_bot_v"],
            provenance_by_model={
                "model:mortal_v": {
                    "kind": "external_revision",
                    "model_identity_id": "model:mortal_v",
                    "external_revision_id": "external:mortal_v:4.1b",
                }
            },
        )


def test_cross_layer_lifecycle_matrix_local_artifact(vertical_env):
    """Local Artifact: candidate -> promoted/current -> season enrollment -> frozen provenance -> retired."""
    tmp_path = vertical_env["tmp_path"]
    configs_dir = vertical_env["configs_dir"]

    # 1. Candidate artifact
    cp_file = tmp_path / "models" / "loc70k.pth"
    cp_file.parent.mkdir(parents=True, exist_ok=True)
    cp_file.write_text("dummy", encoding="utf-8")

    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:loc70k", label="Loc 70k", kind="local_model"))
    for i in range(1, 4):
        registry.create_account(AccountCreate(account_id=f"account:lh{i}", display_name=f"LHuman{i}", account_type="human"))
    registry.create_account(AccountCreate(account_id="account:loc_bot", display_name="LBot", account_type="managed_bot", model_identity_id="model:loc70k"))

    art_cand = registry.add_model_artifact(
        "model:loc70k",
        ModelArtifactCreate(label="loc70k_cand.pth", artifact_path=str(cp_file), stage="candidate"),
    )
    with pytest.raises(ValueError, match="不具备天梯资格|无正式天梯"):
        execution_provenance.resolve_execution_provenance(
            account_id="account:loc_bot",
            purpose="ladder_eligible",
            artifact_id=art_cand.model_artifact_id,
            project_root=tmp_path,
            registry=registry,
        )

    # 2. Promote artifact & enroll season
    registry.promote_model_artifact("model:loc70k", art_cand.model_artifact_id)
    sr.create_season(configs_dir, season_id="s_loc_season", title="Loc Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_loc_season",
        ["account:lh1", "account:lh2", "account:lh3", "account:loc_bot"],
        provenance_by_model={
            "model:loc70k": {
                "kind": "local_artifact",
                "model_identity_id": "model:loc70k",
                "model_artifact_id": art_cand.model_artifact_id,
            }
        },
    )
    sr.set_season_status(configs_dir, "s_loc_season", "running")

    # 3. Confirm match
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:loc_bot", "model_identity_id": "model:loc70k", "model_artifact_id": art_cand.model_artifact_id},
        {"seat": 1, "action": "assign", "account_id": "account:lh1"},
        {"seat": 2, "action": "assign", "account_id": "account:lh2"},
        {"seat": 3, "action": "assign", "account_id": "account:lh3"},
    ]
    created = intake.resolve_and_create_match(
        log_id="20260804gm-v-loc-01",
        resolutions=resolutions,
        season_id="s_loc_season",
        rating_eligible=True,
    )
    match = ledger.get_match(created["match_id"])
    assert match.seats[0].execution_provenance.kind == "local_artifact"
    assert match.seats[0].execution_provenance.model_artifact_id == art_cand.model_artifact_id

    # 4. Retire artifact in catalog
    registry.retire_model_artifact("model:loc70k", art_cand.model_artifact_id)

    # 5. INVARIANT: Historical match & season configuration completely intact
    reloaded_match = ledger.get_match(match.match_id)
    assert reloaded_match.seats[0].execution_provenance.model_artifact_id == art_cand.model_artifact_id
    season_cfg = sr.get_season(configs_dir, "s_loc_season")
    m_grp = next(m for m in season_cfg["models"] if m.get("model_identity_id") == "model:loc70k")
    assert m_grp["execution_provenance"]["model_artifact_id"] == art_cand.model_artifact_id

    # 6. INVARIANT: New season enrollment with retired local artifact is strictly rejected
    sr.create_season(configs_dir, season_id="s_loc_new_season", title="New Loc Season")
    with pytest.raises(ValueError, match="已退役|不具备天梯资格|无正式天梯"):
        sr.set_season_enrollment(
            configs_dir,
            "s_loc_new_season",
            ["account:lh1", "account:lh2", "account:lh3", "account:loc_bot"],
            provenance_by_model={
                "model:loc70k": {
                    "kind": "local_artifact",
                    "model_identity_id": "model:loc70k",
                    "model_artifact_id": art_cand.model_artifact_id,
                }
            },
        )


def test_catalog_current_decoupled_from_season_authority(vertical_env):
    """Proves Catalog Current != Season Authority under adversarial mutations."""
    tmp_path = vertical_env["tmp_path"]
    configs_dir = vertical_env["configs_dir"]

    # Register External Model with Revision A (4.1b)
    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:mortal_dec", label="Mortal Decoupled", kind="external_agent"))
    for i in range(1, 4):
        registry.create_account(AccountCreate(account_id=f"account:dh{i}", display_name=f"DHuman{i}", account_type="human"))
    registry.create_account(AccountCreate(account_id="account:mortal_bot_dec", display_name="MortalBotDec", account_type="external_bot", model_identity_id="model:mortal_dec"))

    rev_a = registry.add_external_revision(
        "model:mortal_dec",
        ExternalModelRevisionCreate(provider="mortal", version="4.1b", stage="promoted", is_current=True),
    )

    # Freeze Season S1 with Revision A
    sr.create_season(configs_dir, season_id="s_season_1", title="Season 1")
    sr.set_season_enrollment(
        configs_dir,
        "s_season_1",
        ["account:dh1", "account:dh2", "account:dh3", "account:mortal_bot_dec"],
        provenance_by_model={
            "model:mortal_dec": {
                "kind": "external_revision",
                "model_identity_id": "model:mortal_dec",
                "external_revision_id": "external:mortal_dec:4.1b",
            }
        },
    )
    sr.set_season_status(configs_dir, "s_season_1", "running")

    # Add Revision B (4.2) and switch Catalog Current to Revision B
    rev_b = registry.add_external_revision(
        "model:mortal_dec",
        ExternalModelRevisionCreate(provider="mortal", version="4.2", stage="promoted", is_current=True),
    )
    assert rev_b.is_current is True

    # 1. S1 Authority remains frozen to Revision A (4.1b)
    s1_cfg = sr.get_season(configs_dir, "s_season_1")
    m_grp = next(m for m in s1_cfg["models"] if m.get("model_identity_id") == "model:mortal_dec")
    assert m_grp["execution_provenance"]["external_revision_id"] == "external:mortal_dec:4.1b"

    # 2. Intake assessment on S1 with Revision A -> READY
    res_a = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot_dec", "model_identity_id": "model:mortal_dec", "external_revision_id": "external:mortal_dec:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:dh1"},
        {"seat": 2, "action": "assign", "account_id": "account:dh2"},
        {"seat": 3, "action": "assign", "account_id": "account:dh3"},
    ]
    assess_a = intake.assess_intake_admission(
        log_id="20260804gm-dec-a",
        resolutions=res_a,
        season_id="s_season_1",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assess_a.state == "ready"

    # 3. Intake assessment on S1 with Revision B (which is catalog current!) -> BLOCKED!
    res_b = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot_dec", "model_identity_id": "model:mortal_dec", "external_revision_id": "external:mortal_dec:4.2"},
        {"seat": 1, "action": "assign", "account_id": "account:dh1"},
        {"seat": 2, "action": "assign", "account_id": "account:dh2"},
        {"seat": 3, "action": "assign", "account_id": "account:dh3"},
    ]
    assess_b = intake.assess_intake_admission(
        log_id="20260804gm-dec-b",
        resolutions=res_b,
        season_id="s_season_1",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assess_b.state == "blocked"
    assert any("不一致" in iss.message for iss in assess_b.issues)


def test_p2_create_contract_extra_forbid_fails_closed():
    """Verify ModelArtifactCreate & ExternalModelRevisionCreate forbid extra fields with ValidationError."""
    # 1. ModelArtifactCreate with extra forbidden fields (e.g. injected note/hash)
    with pytest.raises(ValidationError) as exc1:
        ModelArtifactCreate.model_validate({
            "label": "art1",
            "artifact_path": "a.pth",
            "illegal_field": "exploit",
        })
    assert "extra_forbidden" in str(exc1.value)

    # 2. ExternalModelRevisionCreate with extra forbidden fields (e.g. note)
    with pytest.raises(ValidationError) as exc2:
        ExternalModelRevisionCreate.model_validate({
            "provider": "mortal",
            "version": "4.2",
            "note": "legacy_note",
        })
    assert "extra_forbidden" in str(exc2.value)


def audit_production_control_plane(
    *,
    participant_data_root: Path,
    ladder_config_dir: Path,
) -> None:
    """Pure invariant audit function over participant models and ladder season configs."""
    from workbench.participants import registry as custom_registry
    from workbench.replay import ladder as ladder_data

    # Read accounts and models under specific participant root
    all_accounts = custom_registry.list_accounts()
    all_models = custom_registry.list_models()

    assert len(all_accounts) > 0, "Production accounts must not be empty"
    assert len(all_models) > 0, "Production models must not be empty"

    # 1. Verify identities shape
    for ident in all_models:
        if ident.kind == "external_agent":
            assert len(ident.external_revisions) > 0, f"External agent {ident.model_identity_id} must have registered external_revisions"
            curr_revs = [r for r in ident.external_revisions if r.is_current]
            assert len(curr_revs) <= 1, f"External agent {ident.model_identity_id} has multiple current revisions: {curr_revs}"
            assert len(ident.artifacts) == 0, f"External agent {ident.model_identity_id} must not have local artifacts"
        elif ident.kind == "local_model":
            curr_arts = [a for a in ident.artifacts if a.is_current]
            assert len(curr_arts) <= 1, f"Local model {ident.model_identity_id} has multiple current artifacts: {curr_arts}"
            assert len(ident.external_revisions) == 0, f"Local model {ident.model_identity_id} must not have external revisions"

    # 2. Verify official-ladder-v2 running season
    season_file = ladder_config_dir / "official-ladder-v2.json"
    assert season_file.exists(), f"Production season config missing: {season_file}"
    s_v2 = ladder_data.get_season_config(ladder_config_dir, "official-ladder-v2")
    assert s_v2.get("status") == "running"
    models = s_v2.get("models", [])
    for m in models:
        exec_prov = m.get("execution_provenance")
        if exec_prov:
            kind = exec_prov.get("kind")
            if kind == "external_revision":
                rev_id = exec_prov.get("external_revision_id")
                assert rev_id is not None
                ident_id = exec_prov.get("model_identity_id")
                ident = custom_registry.get_model_identity(ident_id)
                assert ident is not None
                assert any(r.external_revision_id == rev_id for r in ident.external_revisions)


def test_audit_production_control_plane_hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Hermetic unit test for audit_production_control_plane over a well-formed fixture."""
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    data_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    # Setup fixture
    registry.create_account(AccountCreate(account_id="account:aud_user", display_name="AudUser", account_type="human"))
    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:mortal_aud", label="Mortal Aud", kind="external_agent"))
    registry.add_external_revision("model:mortal_aud", ExternalModelRevisionCreate(provider="mortal", version="4.1b", is_current=True))

    registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:loc_aud", label="Loc Aud", kind="local_model"))
    registry.add_model_artifact("model:loc_aud", ModelArtifactCreate(label="loc.pth", artifact_path="loc.pth"))

    # Write official-ladder-v2 season config
    s_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "official-ladder-v2",
        "title": "Official V2",
        "report_dir": "artifacts/official-ladder-v2",
        "status": "running",
        "models": [
            {
                "model_id": "mortal_aud",
                "model_identity_id": "model:mortal_aud",
                "accounts": [{"account_id": "account:aud_user"}],
                "execution_provenance": {
                    "kind": "external_revision",
                    "model_identity_id": "model:mortal_aud",
                    "external_revision_id": "external:mortal_aud:4.1b",
                },
            }
        ],
    }
    (configs_dir / "official-ladder-v2.json").write_text(json.dumps(s_cfg, ensure_ascii=False), encoding="utf-8")

    # Run audit in hermetic test -> passes
    audit_production_control_plane(participant_data_root=data_dir, ladder_config_dir=configs_dir)


def test_production_state_read_only_invariant_audit(monkeypatch: pytest.MonkeyPatch):
    """Explicit production environment audit.

    Gated by KEQING_RUN_PRODUCTION_AUDIT=1 or skips cleanly if production dataset is absent.
    When KEQING_RUN_PRODUCTION_AUDIT=1 is set, fail-closed asserts on live dataset.
    """
    import os
    from workbench.runtime.resolver import data_path
    from workbench.replay import ladder as ladder_data

    run_audit = os.environ.get("KEQING_RUN_PRODUCTION_AUDIT") == "1"

    # Resolve production roots
    prod_data_root = Path.cwd().parent / "keqing-data" / "participants"
    if not prod_data_root.exists():
        prod_data_root = data_path("participants")

    prod_ladder_cfg = ladder_data.resolve_config_dir(Path.cwd())

    if not run_audit:
        if not prod_data_root.exists() or not (prod_data_root / "models.json").exists():
            pytest.skip("Production participant dataset not present in clean workspace (set KEQING_RUN_PRODUCTION_AUDIT=1 to enforce)")

    if not prod_data_root.exists() or not (prod_data_root / "models.json").exists():
        pytest.fail(f"KEQING_RUN_PRODUCTION_AUDIT=1 was specified but production data root does not exist: {prod_data_root}")

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(prod_data_root))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(prod_ladder_cfg))

    audit_production_control_plane(
        participant_data_root=prod_data_root,
        ladder_config_dir=prod_ladder_cfg,
    )
