# -*- coding: utf-8 -*-
"""R13-C: 天梯准入评估测试 (Admission Assessment UX Preflight & TOCTOU-Resistance)."""
import json
from pathlib import Path
import pytest

from workbench.participants import aliases, intake, ledger, registry
from workbench.participants.schemas import (
    AccountCreate,
    ExternalModelRevisionCreate,
    ModelArtifactCreate,
    ModelIdentityCreate,
    SeatResolution,
)
from workbench.replay import season_registry as sr


@pytest.fixture
def setup_assessment_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    configs_dir = tmp_path / "registries"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("workbench.replay.ladder.resolve_config_dir", lambda root=None: configs_dir)
    try:
        import replay.ladder
        monkeypatch.setattr("replay.ladder.resolve_config_dir", lambda root=None: configs_dir)
    except ImportError:
        pass

    # 1. Accounts
    for i in range(1, 4):
        registry.create_account(
            AccountCreate(
                account_id=f"account:human{i}",
                display_name=f"Human {i}",
                account_type="human",
                default_controller="human_ui",
            )
        )

    # Local model m70k with promoted artifact
    ckpt_path = tmp_path / "m70k.pth"
    ckpt_path.write_text("dummy")
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:m70k",
            label="Mortal 70k",
            kind="local_model",
        )
    )
    registry.add_model_artifact(
        "model:m70k",
        ModelArtifactCreate(
            model_artifact_id="model:m70k@01",
            label="m70k.pth",
            artifact_path=str(ckpt_path),
            stage="promoted",
        ),
    )
    # Add a second promoted artifact for local model
    ckpt_path2 = tmp_path / "m70k_v2.pth"
    ckpt_path2.write_text("dummy2")
    registry.add_model_artifact(
        "model:m70k",
        ModelArtifactCreate(
            model_artifact_id="model:m70k@02",
            label="m70k_v2.pth",
            artifact_path=str(ckpt_path2),
            stage="promoted",
        ),
    )
    registry.create_account(
        AccountCreate(
            account_id="account:m70k_bot",
            display_name="Mortal 70k Bot",
            account_type="managed_bot",
            default_controller="local_model",
            model_identity_id="model:m70k",
        )
    )

    # External model mortal with two revisions
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal",
            label="Mortal AI",
            kind="external_agent",
        )
    )
    registry.add_external_revision(
        "model:mortal",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal:4.1b",
            provider="mortal",
            version="4.1b",
            stage="promoted",
        ),
    )
    registry.add_external_revision(
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

    # 2. Season with frozen 4.1b
    sr.create_season(configs_dir, season_id="s_running", title="Running Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_running",
        ["account:human1", "account:human2", "account:human3", "account:mortal_bot"],
        provenance_by_model={
            "model:mortal": {
                "kind": "external_revision",
                "model_identity_id": "model:mortal",
                "external_revision_id": "external:mortal:4.1b",
            }
        },
    )
    sr.set_season_status(configs_dir, "s_running", "running")

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

    return {
        "data_dir": data_dir,
        "configs_dir": configs_dir,
        "tmp_path": tmp_path,
    }


def test_assessment_ready_external_mortal(setup_assessment_env):
    """External Mortal 4.1b + 3 Humans against matching running season returns state=ready and frozen provenance."""
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-ready",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "ready"
    assert assessment.season_id == "s_running"
    assert assessment.rating_eligible is True
    assert len(assessment.issues) == 0
    assert len(assessment.seats) == 4
    assert all(s.is_ladder_eligible for s in assessment.seats)

    seat0 = assessment.seats[0]
    assert seat0.frozen_provenance is not None
    assert seat0.frozen_provenance.kind == "external_revision"
    assert seat0.frozen_provenance.external_revision_id == "external:mortal:4.1b"
    assert seat0.frozen_provenance.model_identity_id == "model:mortal"


def test_assessment_blocked_season_mismatch_revision(setup_assessment_env):
    """Season locked to Mortal 4.1b, but seat resolves to 4.2 -> state=blocked with descriptive issue."""
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.2"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-mismatch",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "blocked"
    assert not assessment.seats[0].is_ladder_eligible
    assert any("外部版本" in iss.message and "不一致" in iss.message for iss in assessment.issues)


def test_assessment_blocked_human_not_in_season(setup_assessment_env):
    """Human account not enrolled in the season is detected and blocked."""
    registry.create_account(
        AccountCreate(
            account_id="account:unregistered_human",
            display_name="Unregistered Human",
            account_type="human",
            default_controller="human_ui",
        )
    )
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:unregistered_human"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-not-enrolled",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "blocked"
    assert not assessment.seats[1].is_ladder_eligible
    assert any("未在正式赛季" in iss.message for iss in assessment.issues)


def test_assessment_blocked_nonexistent_or_draft_season(setup_assessment_env):
    """Nonexistent or non-running season reports state=blocked."""
    configs_dir = setup_assessment_env["configs_dir"]
    sr.create_season(configs_dir, season_id="s_draft_only", title="Draft Season")

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]

    # Nonexistent
    a1 = intake.assess_intake_admission(
        log_id="20260804gm-test-nonexist",
        resolutions=resolutions,
        season_id="s_does_not_exist",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert a1.state == "blocked"
    assert any("不存在" in iss.message for iss in a1.issues)

    # Draft only
    a2 = intake.assess_intake_admission(
        log_id="20260804gm-test-draft",
        resolutions=resolutions,
        season_id="s_draft_only",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert a2.state == "blocked"
    assert any("非 running" in iss.message for iss in a2.issues)


def test_assessment_not_requested_when_unranked(setup_assessment_env):
    """When rating_eligible is False, returns state=not_requested with 0 blocking issues."""
    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-unranked",
        resolutions=resolutions,
        season_id=None,
        rating_eligible=False,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "not_requested"
    assert assessment.rating_eligible is False
    assert len(assessment.issues) == 0


def test_assessment_is_pure_preflight_no_side_effects(setup_assessment_env):
    """Assessment executes without writing accounts, aliases, matches, revisions, staging or dirty markers."""
    data_dir = setup_assessment_env["data_dir"]

    # Snapshot existing files bytes before assessment
    def _read_bytes_if_exists(p):
        return p.read_bytes() if p.exists() else None

    accounts_before = _read_bytes_if_exists(data_dir / "accounts.json")
    aliases_before = _read_bytes_if_exists(data_dir / "aliases.json")
    matches_before = _read_bytes_if_exists(data_dir / "matches.jsonl")
    revisions_before = _read_bytes_if_exists(data_dir / "revisions.jsonl")

    resolutions = [
        {"seat": 0, "action": "create", "display_name": "NewPlayer0"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-pure",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "blocked"

    # Confirm zero state mutations (exact binary & file nonexistence verification)
    assert _read_bytes_if_exists(data_dir / "accounts.json") == accounts_before
    assert _read_bytes_if_exists(data_dir / "aliases.json") == aliases_before
    assert _read_bytes_if_exists(data_dir / "matches.jsonl") == matches_before
    assert _read_bytes_if_exists(data_dir / "revisions.jsonl") == revisions_before
    assert not (data_dir / "replays_staging" / "20260804gm-test-pure").exists()
    assert not (data_dir / "staging").exists()
    assert not (data_dir / "pending_transaction.json").exists()
    assert not (data_dir / "ladder_dirty_s_running.json").exists()


def test_assessment_rejects_invalid_resolution_shape(setup_assessment_env):
    """Assessment enforces exact 4-seat shape (0..3), returning state=blocked on duplicate/missing seats."""
    # 5 seats with duplicate seat 3
    resolutions_dup = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-shape",
        resolutions=resolutions_dup,
        season_id="s_running",
        rating_eligible=True,
        project_root=setup_assessment_env["tmp_path"],
    )
    assert assessment.state == "blocked"
    assert any("4 个座位" in iss.message for iss in assessment.issues)


def test_toctou_assessment_ready_then_confirm_revalidates_and_fails(setup_assessment_env):
    """TOCTOU proof: assessment returns ready -> season/registry mutates -> confirm independently re-evaluates and fails closed."""
    configs_dir = setup_assessment_env["configs_dir"]
    tmp_path = setup_assessment_env["tmp_path"]
    data_dir = setup_assessment_env["data_dir"]

    resolutions = [
        {"seat": 0, "action": "assign", "account_id": "account:mortal_bot", "model_identity_id": "model:mortal", "external_revision_id": "external:mortal:4.1b"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]

    # 1. Assessment says READY
    assessment = intake.assess_intake_admission(
        log_id="20260804gm-test-toctou",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assessment.state == "ready"

    # Snapshot data before confirm failure
    matches_count_before = len(ledger.list_matches().matches)

    # 2. Behind the scenes: Season status transitions to completed
    sr.set_season_status(configs_dir, "s_running", "completed")

    # 3. Confirm attempts to import match with rating_eligible=True without new assessment -> must fail!
    with pytest.raises(ValueError, match="不是 running 状态"):
        intake.resolve_and_create_match(
            log_id="20260804gm-test-toctou",
            resolutions=resolutions,
            season_id="s_running",
            rating_eligible=True,
        )

    # 4. Confirm zero side effects on failure (no staging, no pending, no committed match)
    assert len(ledger.list_matches().matches) == matches_count_before
    assert not (data_dir / "replays_staging" / "20260804gm-test-toctou").exists()
    assert not (data_dir / "pending_transaction.json").exists()


def test_assessment_blocked_local_artifact_mismatch(setup_assessment_env):
    """Local model artifact mismatch (seat has 70k@02 vs season locked to 70k@01) -> state=blocked."""
    configs_dir = setup_assessment_env["configs_dir"]
    tmp_path = setup_assessment_env["tmp_path"]

    sr.create_season(configs_dir, season_id="s_local_season", title="Local Season")
    sr.set_season_enrollment(
        configs_dir,
        "s_local_season",
        ["account:human1", "account:human2", "account:human3", "account:m70k_bot"],
        provenance_by_model={
            "model:m70k": {
                "kind": "local_artifact",
                "model_identity_id": "model:m70k",
                "model_artifact_id": "model:m70k@01",
            }
        },
    )
    sr.set_season_status(configs_dir, "s_local_season", "running")

    # Mismatch: Seat specifies 70k@02
    resolutions_mismatch = [
        {"seat": 0, "action": "assign", "account_id": "account:m70k_bot", "model_identity_id": "model:m70k", "model_artifact_id": "model:m70k@02"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment_mismatch = intake.assess_intake_admission(
        log_id="20260804gm-test-loc-mismatch",
        resolutions=resolutions_mismatch,
        season_id="s_local_season",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assessment_mismatch.state == "blocked"
    assert any("模型产物" in iss.message and "不一致" in iss.message for iss in assessment_mismatch.issues)

    # Match: Seat specifies 70k@01
    resolutions_match = [
        {"seat": 0, "action": "assign", "account_id": "account:m70k_bot", "model_identity_id": "model:m70k", "model_artifact_id": "model:m70k@01"},
        {"seat": 1, "action": "assign", "account_id": "account:human1"},
        {"seat": 2, "action": "assign", "account_id": "account:human2"},
        {"seat": 3, "action": "assign", "account_id": "account:human3"},
    ]
    assessment_match = intake.assess_intake_admission(
        log_id="20260804gm-test-loc-match",
        resolutions=resolutions_match,
        season_id="s_local_season",
        rating_eligible=True,
        project_root=tmp_path,
    )
    assert assessment_match.state == "ready"
    assert assessment_match.seats[0].frozen_provenance.model_artifact_id == "model:m70k@01"


def test_api_intake_assessment_endpoint(setup_assessment_env):
    """FastAPI endpoint POST /api/participants/intake/assessment returns 200 with structured assessment."""
    from workbench.participants import api as participants_api
    from workbench.participants.schemas import IntakeAdmissionAssessmentRequest, SeatResolution

    resolutions = [
        SeatResolution(seat=0, action="assign", account_id="account:mortal_bot", model_identity_id="model:mortal", external_revision_id="external:mortal:4.1b"),
        SeatResolution(seat=1, action="assign", account_id="account:human1"),
        SeatResolution(seat=2, action="assign", account_id="account:human2"),
        SeatResolution(seat=3, action="assign", account_id="account:human3"),
    ]
    req = IntakeAdmissionAssessmentRequest(
        log_id="20260804gm-test-endpoint",
        resolutions=resolutions,
        season_id="s_running",
        rating_eligible=True,
    )
    res = participants_api.api_intake_assessment(req)
    assert res.state == "ready"
    assert res.season_id == "s_running"
    assert len(res.seats) == 4
    assert res.seats[0].frozen_provenance.external_revision_id == "external:mortal:4.1b"

