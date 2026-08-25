# -*- coding: utf-8 -*-
"""Tests for Execution-Provenance-First Resolver (R13-A).

Verifies the unified execution provenance control plane:
- Human participant provenance (human_ui, no model evidence)
- Managed local model provenance (local_model, ModelArtifact exact match, physical checkpoint)
- External agent provenance (external_agent, ExternalModelRevision exact match, NO checkpoint required)
- Strict type isolation and mismatch rejection
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from workbench.participants.execution_provenance import (
    CanonicalExecutionProvenance,
    resolve_execution_provenance,
)
from workbench.participants.schemas import (
    Account,
    ExternalModelRevision,
    ModelArtifact,
    ModelIdentity,
)


class MockRegistry:
    def __init__(self, accounts: list[Account], identities: list[ModelIdentity]):
        self._accounts = {a.account_id: a for a in accounts}
        self._identities = {m.model_identity_id: m for m in identities}

    def list_models(self) -> list[ModelIdentity]:
        return list(self._identities.values())

    def get_account(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    def get_model_identity(self, model_identity_id: str) -> ModelIdentity | None:
        return self._identities.get(model_identity_id)

    def identity_belongs_to_account(self, identity_id: str | None, account_id: str) -> bool:
        if identity_id is None:
            return True
        ident = self._identities.get(identity_id)
        if ident is None:
            return False
        if ident.account_id is not None:
            return ident.account_id == account_id
        acc = self._accounts.get(account_id)
        if acc is None:
            return False
        return acc.model_identity_id == ident.model_identity_id


# -----------------------------------------------------------------------------
# 1. Human Provenance Tests
# -----------------------------------------------------------------------------

def test_human_provenance_valid():
    acc = Account(
        account_id="account:human01",
        display_name="Human Player",
        account_type="human",
        default_controller="human_ui",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    prov = resolve_execution_provenance(account_id="account:human01", registry=reg)
    assert prov.kind == "human"
    assert prov.account_id == "account:human01"
    assert prov.model_identity_id is None
    assert prov.model_artifact_id is None
    assert prov.external_revision_id is None
    assert prov.resolved_checkpoint_path is None


@pytest.mark.parametrize(
    "forbidden_kwarg",
    [
        {"identity_id": "model:some_model"},
        {"artifact_id": "model:some_model@123"},
        {"external_revision_id": "external:mortal:4.1b"},
        {"checkpoint": "/some/path/model.pth"},
    ],
)
def test_human_provenance_rejects_model_evidence(forbidden_kwarg):
    acc = Account(
        account_id="account:human01",
        display_name="Human Player",
        account_type="human",
        default_controller="human_ui",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    with pytest.raises(ValueError, match="人类账号.*禁止携带"):
        resolve_execution_provenance(
            account_id="account:human01",
            registry=reg,
            **forbidden_kwarg,
        )


def test_human_provenance_rejects_mismatched_controller():
    acc = Account(
        account_id="account:human01",
        display_name="Human Player",
        account_type="human",
        default_controller="human_ui",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    with pytest.raises(ValueError, match="控制器类型不匹配"):
        resolve_execution_provenance(
            account_id="account:human01",
            controller_type="local_model",
            registry=reg,
        )


# -----------------------------------------------------------------------------
# 2. Local Managed Model Provenance Tests
# -----------------------------------------------------------------------------

def test_local_model_promoted_artifact_pass(tmp_path: Path):
    ckpt_file = tmp_path / "model.pth"
    ckpt_file.write_text("fake checkpoint bytes")

    art = ModelArtifact(
        model_artifact_id="model:m70k@art01",
        label="model.pth",
        model_identity_id="model:m70k",
        artifact_path=str(ckpt_file),
        stage="promoted",
        capabilities=["ladder_eligible", "playwithyou_allowed", "review_allowed"],
        is_current=True,
        created_at="2026-08-01T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:m70k",
        label="Mortal 70k",
        kind="local_model",
        artifacts=[art],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:bot01",
        display_name="Bot 01",
        account_type="managed_bot",
        default_controller="local_model",
        model_identity_id="model:m70k",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    prov = resolve_execution_provenance(
        account_id="account:bot01",
        project_root=tmp_path,
        registry=reg,
    )
    assert prov.kind == "local_artifact"
    assert prov.account_id == "account:bot01"
    assert prov.model_identity_id == "model:m70k"
    assert prov.model_artifact_id == "model:m70k@art01"
    assert prov.resolved_checkpoint_path == ckpt_file.resolve()
    assert prov.model_artifact is not None


def test_local_model_missing_artifact_reject(tmp_path: Path):
    ident = ModelIdentity(
        model_identity_id="model:m70k",
        label="Mortal 70k",
        kind="local_model",
        artifacts=[],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:bot01",
        display_name="Bot 01",
        account_type="managed_bot",
        default_controller="local_model",
        model_identity_id="model:m70k",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    with pytest.raises(ValueError, match="未找到匹配的模型产物"):
        resolve_execution_provenance(
            account_id="account:bot01",
            project_root=tmp_path,
            registry=reg,
        )


def test_local_model_nonexistent_physical_checkpoint_reject(tmp_path: Path):
    art = ModelArtifact(
        model_artifact_id="model:m70k@art01",
        label="missing.pth",
        model_identity_id="model:m70k",
        artifact_path=str(tmp_path / "nonexistent.pth"),
        stage="promoted",
        capabilities=["ladder_eligible"],
        is_current=True,
        created_at="2026-08-01T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:m70k",
        label="Mortal 70k",
        kind="local_model",
        artifacts=[art],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:bot01",
        display_name="Bot 01",
        account_type="managed_bot",
        default_controller="local_model",
        model_identity_id="model:m70k",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    # Unspecified artifact_id -> excluded from candidates due to missing file -> 0 match reject
    with pytest.raises(ValueError, match="未找到匹配的模型产物.*0 match"):
        resolve_execution_provenance(
            account_id="account:bot01",
            project_root=tmp_path,
            registry=reg,
        )

    # Explicit artifact_id -> explicit file nonexistent error
    with pytest.raises(ValueError, match="对应的实体文件不存在"):
        resolve_execution_provenance(
            account_id="account:bot01",
            artifact_id="model:m70k@art01",
            project_root=tmp_path,
            registry=reg,
        )


def test_local_model_rejects_external_revision_id():
    acc = Account(
        account_id="account:bot01",
        display_name="Bot 01",
        account_type="managed_bot",
        default_controller="local_model",
        model_identity_id="model:m70k",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    with pytest.raises(ValueError, match="本地托管模型账号.*禁止绑定外部版本证据"):
        resolve_execution_provenance(
            account_id="account:bot01",
            external_revision_id="external:rev1",
            registry=reg,
        )


# -----------------------------------------------------------------------------
# 3. External Agent Provenance Tests (Mortal 4.1b Core)
# -----------------------------------------------------------------------------

def test_external_revision_promoted_pass_without_any_checkpoint():
    rev = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b",
        external_ref="mortal-official-v4.1b",
        stage="promoted",
        capabilities=["ladder_eligible", "playwithyou_allowed", "review_allowed"],
        is_current=True,
        created_at="2026-08-01T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[rev],
        artifacts=[],  # Empty artifacts, no files!
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:mortal41b",
        display_name="mortal4.1b",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    prov = resolve_execution_provenance(
        account_id="account:mortal41b",
        purpose="ladder_eligible",
        registry=reg,
    )
    assert prov.kind == "external_revision"
    assert prov.account_id == "account:mortal41b"
    assert prov.model_identity_id == "model:mortal41b"
    assert prov.external_revision_id == "external:mortal41b:4.1b"
    assert prov.resolved_checkpoint_path is None
    assert prov.external_revision == rev


def test_external_zero_revision_reject():
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:mortal41b",
        display_name="mortal4.1b",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    with pytest.raises(ValueError, match="未找到匹配的外部版本.*0 match"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            purpose="ladder_eligible",
            registry=reg,
        )


def test_external_ambiguous_revisions_fail_closed():
    rev1 = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b-r1",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b-r1",
        stage="promoted",
        capabilities=["ladder_eligible"],
        is_current=False,
        created_at="2026-08-01T00:00:00+08:00",
    )
    rev2 = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b-r2",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b-r2",
        stage="promoted",
        capabilities=["ladder_eligible"],
        is_current=True,
        created_at="2026-08-02T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[rev1, rev2],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:mortal41b",
        display_name="mortal4.1b",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    # Multiple promoted revisions without explicit revision_id fails closed (strict exact match)
    with pytest.raises(ValueError, match="外部版本匹配存在歧义.*>1 matches"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            purpose="ladder_eligible",
            registry=reg,
        )

    # Specifying explicit external_revision_id resolves ambiguity
    prov = resolve_execution_provenance(
        account_id="account:mortal41b",
        external_revision_id="external:mortal41b:4.1b-r2",
        purpose="ladder_eligible",
        registry=reg,
    )
    assert prov.external_revision_id == "external:mortal41b:4.1b-r2"


@pytest.mark.parametrize("stage", ["deprecated", "retired", "candidate"])
def test_external_non_promoted_revision_rejects_ladder(stage):
    rev = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b",
        stage=stage,
        capabilities=["playwithyou_allowed", "review_allowed"] if stage != "retired" else ["review_allowed"],
        is_current=True,
        created_at="2026-08-01T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[rev],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:mortal41b",
        display_name="mortal4.1b",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    with pytest.raises(ValueError, match="未找到匹配的外部版本"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            purpose="ladder_eligible",
            registry=reg,
        )


def test_external_rejects_local_artifact_id_and_checkpoint():
    acc = Account(
        account_id="account:mortal41b",
        display_name="mortal4.1b",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    with pytest.raises(ValueError, match="外部模型账号.*禁止绑定本地 ModelArtifact"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            artifact_id="art:fake",
            registry=reg,
        )

    with pytest.raises(ValueError, match="外部模型账号.*禁止绑定本地 Checkpoint"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            checkpoint="/fake/path.pth",
            registry=reg,
        )


# -----------------------------------------------------------------------------
# 4. Mismatch & Cross-Type Rejections
# -----------------------------------------------------------------------------

def test_external_bot_with_local_identity_rejects():
    ident = ModelIdentity(
        model_identity_id="model:local_mortal",
        label="Local Mortal",
        kind="local_model",
        artifacts=[],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:ext01",
        display_name="External Bot",
        account_type="external_bot",
        default_controller="external_agent",
        model_identity_id="model:local_mortal",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    with pytest.raises(ValueError, match="类型错配"):
        resolve_execution_provenance(
            account_id="account:ext01",
            registry=reg,
        )


def test_managed_bot_with_external_identity_rejects():
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[],
        created_at="2026-08-01T00:00:00+08:00",
    )
    acc = Account(
        account_id="account:bot01",
        display_name="Local Bot",
        account_type="managed_bot",
        default_controller="local_model",
        model_identity_id="model:mortal41b",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [ident])

    with pytest.raises(ValueError, match="类型错配"):
        resolve_execution_provenance(
            account_id="account:bot01",
            registry=reg,
        )


# -----------------------------------------------------------------------------
# 5. Registry CRUD & Type Enforcement Tests
# -----------------------------------------------------------------------------

def test_registry_crud_external_revision(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    from workbench.participants import registry
    from workbench.participants.schemas import (
        AccountCreate,
        ExternalModelRevisionCreate,
        ExternalModelRevisionUpdate,
        ModelArtifactCreate,
        ModelIdentityCreate,
    )

    # 1. Create external agent identity
    ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal41b",
            label="Mortal 4.1b",
            kind="external_agent",
        )
    )
    assert ident.kind == "external_agent"

    # 2. Cannot add local ModelArtifact to external_agent identity
    with pytest.raises(ValueError, match="external_agent 模型身份.*禁止挂载本地 ModelArtifact"):
        registry.add_model_artifact(
            "model:mortal41b",
            ModelArtifactCreate(label="fake.pth", artifact_path="/fake/path.pth"),
        )

    # 3. Add ExternalModelRevision
    rev = registry.add_external_revision(
        "model:mortal41b",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal41b:4.1b",
            provider="mortal",
            version="4.1b",
            stage="promoted",
        ),
    )
    assert rev.external_revision_id == "external:mortal41b:4.1b"
    assert rev.is_ladder_eligible is True

    # 4. List external revisions
    revs = registry.list_external_revisions("model:mortal41b")
    assert len(revs) == 1
    assert revs[0].version == "4.1b"

    # 5. Update external revision
    updated = registry.update_external_revision(
        "model:mortal41b",
        "external:mortal41b:4.1b",
        ExternalModelRevisionUpdate(stage="deprecated"),
    )
    assert updated.stage == "deprecated"
    assert updated.is_ladder_eligible is False

    # 6. Local identity cannot add ExternalModelRevision
    local_ident = registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:local70k",
            label="Local 70k",
            kind="local_model",
        )
    )
    with pytest.raises(ValueError, match="仅 external_agent 模型身份可挂载 ExternalModelRevision"):
        registry.add_external_revision(
            "model:local70k",
            ExternalModelRevisionCreate(provider="local", version="v1"),
        )


# -----------------------------------------------------------------------------
# 6. Ladder Eligibility Integration with External Bot
# -----------------------------------------------------------------------------

def test_ladder_eligibility_with_external_bot_passes_without_checkpoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    configs_dir = tmp_path / "registries"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    from workbench.participants import registry
    from workbench.participants.ladder_eligibility import validate_ladder_eligibility
    from workbench.participants.schemas import (
        AccountCreate,
        ExternalModelRevisionCreate,
        MatchSeat,
        ModelIdentityCreate,
    )

    # Register external model and promoted revision
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal41b",
            label="Mortal 4.1b",
            kind="external_agent",
        )
    )
    registry.add_external_revision(
        "model:mortal41b",
        ExternalModelRevisionCreate(
            external_revision_id="external:mortal41b:4.1b",
            provider="mortal",
            version="4.1b",
            stage="promoted",
        ),
    )

    # Register external bot account
    registry.create_account(
        AccountCreate(
            account_id="account:mortal41b",
            display_name="mortal4.1b",
            account_type="external_bot",
            default_controller="external_agent",
            model_identity_id="model:mortal41b",
        )
    )

    # Register 3 human accounts
    for i in range(1, 4):
        registry.create_account(
            AccountCreate(
                account_id=f"account:human{i}",
                display_name=f"Human {i}",
                account_type="human",
                default_controller="human_ui",
            )
        )

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s1",
        "title": "Season 1",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {"model_id": "model:mortal41b", "accounts": [{"account_id": "account:mortal41b"}]},
        ],
    }
    (configs_dir / "s1.json").write_text(json.dumps(season_payload), encoding="utf-8")

    seats = [
        MatchSeat(seat=0, account_id="account:mortal41b", controller_type="external_agent", model_identity_id="model:mortal41b"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]

    # Must pass without requiring any physical checkpoint file on disk!
    validate_ladder_eligibility(
        season_id="s1",
        seats=seats,
        project_root=tmp_path,
        registry=registry,
    )


def test_ladder_eligibility_with_external_bot_zero_revision_rejects(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    configs_dir = tmp_path / "registries"
    configs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    from workbench.participants import registry
    from workbench.participants.ladder_eligibility import validate_ladder_eligibility
    from workbench.participants.schemas import (
        AccountCreate,
        MatchSeat,
        ModelIdentityCreate,
    )

    # Register external model without any revisions
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal41b_empty",
            label="Mortal 4.1b Empty",
            kind="external_agent",
        )
    )
    registry.create_account(
        AccountCreate(
            account_id="account:mortal41b_empty",
            display_name="mortal4.1b",
            account_type="external_bot",
            default_controller="external_agent",
            model_identity_id="model:mortal41b_empty",
        )
    )
    for i in range(1, 4):
        registry.create_account(
            AccountCreate(
                account_id=f"account:human{i}",
                display_name=f"Human {i}",
                account_type="human",
                default_controller="human_ui",
            )
        )

    season_payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "s1",
        "title": "Season 1",
        "status": "running",
        "report_dir": str(tmp_path / "reports"),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {
            "sources_root": "sources",
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": f"account:human{i}"} for i in range(1, 4)]},
            {"model_id": "model:mortal41b_empty", "accounts": [{"account_id": "account:mortal41b_empty"}]},
        ],
    }
    (configs_dir / "s1.json").write_text(json.dumps(season_payload), encoding="utf-8")

    seats = [
        MatchSeat(seat=0, account_id="account:mortal41b_empty", controller_type="external_agent", model_identity_id="model:mortal41b_empty"),
        MatchSeat(seat=1, account_id="account:human1", controller_type="human_ui"),
        MatchSeat(seat=2, account_id="account:human2", controller_type="human_ui"),
        MatchSeat(seat=3, account_id="account:human3", controller_type="human_ui"),
    ]

    with pytest.raises(ValueError, match="未找到匹配的外部版本.*0 match"):
        validate_ladder_eligibility(
            season_id="s1",
            seats=seats,
            project_root=tmp_path,
            registry=registry,
        )

