# -*- coding: utf-8 -*-
"""Tests for Execution-Provenance-First Resolver (R13-A Repair 1).

Verifies the unified execution provenance control plane:
- Human participant provenance (human_ui, no model evidence; ladder_eligible strictly requires human_ui)
- Managed local model provenance (local_model, ModelArtifact exact match, physical checkpoint)
- External agent provenance (external_agent, ExternalModelRevision exact match, NO checkpoint required)
- Strict type isolation, immutability, and zero lifecycle-based disambiguation
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


def test_human_ladder_requires_human_ui():
    acc = Account(
        account_id="account:human01",
        display_name="Human Player",
        account_type="human",
        default_controller="manual_only",
        created_at="2026-08-01T00:00:00+08:00",
        updated_at="2026-08-01T00:00:00+08:00",
    )
    reg = MockRegistry([acc], [])

    # Ladder eligible requires strictly human_ui
    with pytest.raises(ValueError, match="正式天梯人类账号.*控制器必须为 human_ui"):
        resolve_execution_provenance(
            account_id="account:human01",
            purpose="ladder_eligible",
            registry=reg,
        )

    # Review allowed accepts manual_only
    prov = resolve_execution_provenance(
        account_id="account:human01",
        purpose="review_allowed",
        registry=reg,
    )
    assert prov.kind == "human"


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
# 3. External Agent Provenance Tests (Mortal 4.1b Core & Strict Exact Match)
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


def test_external_resolver_spy_verifies_zero_checkpoint_lookups(monkeypatch):
    """Ensure external agent resolution never invokes checkpoint lookup machinery."""
    def bomb(*args, **kwargs):
        raise AssertionError("resolve_model_checkpoint must not be called for external agent")

    monkeypatch.setattr("workbench.runtime.resolver.resolve_model_checkpoint", bomb)
    monkeypatch.setattr("workbench.runtime.resolver.resolve_bot_spec", bomb)

    rev = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b",
        stage="promoted",
        capabilities=["ladder_eligible"],
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

    prov = resolve_execution_provenance(
        account_id="account:mortal41b",
        purpose="ladder_eligible",
        registry=reg,
    )
    assert prov.external_revision_id == "external:mortal41b:4.1b"


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


def test_external_ambiguity_no_lifecycle_disambiguation():
    """P1-3: Lifecycle (promoted vs deprecated) must NOT be used to disambiguate identity without explicit revision_id."""
    rev_promoted = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.1b",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.1b",
        stage="promoted",
        capabilities=["ladder_eligible"],
        is_current=True,
        created_at="2026-08-02T00:00:00+08:00",
    )
    rev_deprecated = ExternalModelRevision(
        external_revision_id="external:mortal41b:4.0",
        model_identity_id="model:mortal41b",
        provider="mortal",
        version="4.0",
        stage="deprecated",
        capabilities=["playwithyou_allowed", "review_allowed"],
        is_current=False,
        created_at="2026-08-01T00:00:00+08:00",
    )
    ident = ModelIdentity(
        model_identity_id="model:mortal41b",
        label="Mortal 4.1b",
        kind="external_agent",
        external_revisions=[rev_promoted, rev_deprecated],
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

    # 1. Without revision_id -> MUST fail closed with ambiguity (not silently pick promoted one!)
    with pytest.raises(ValueError, match="外部版本匹配存在歧义.*>1 matches"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            purpose="ladder_eligible",
            registry=reg,
        )

    # 2. With explicit promoted revision_id -> PASS
    prov = resolve_execution_provenance(
        account_id="account:mortal41b",
        external_revision_id="external:mortal41b:4.1b",
        purpose="ladder_eligible",
        registry=reg,
    )
    assert prov.external_revision_id == "external:mortal41b:4.1b"

    # 3. With explicit deprecated revision_id -> REJECT (fails capability, not identity)
    with pytest.raises(ValueError, match="不具备天梯资格"):
        resolve_execution_provenance(
            account_id="account:mortal41b",
            external_revision_id="external:mortal41b:4.0",
            purpose="ladder_eligible",
            registry=reg,
        )


@pytest.mark.parametrize("stage", ["deprecated", "retired", "candidate"])
def test_external_single_non_promoted_revision_rejects_ladder(stage):
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

    with pytest.raises(ValueError, match="不具备天梯资格"):
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
# 5. Registry CRUD, Immutability & Hard Type Enforcement Tests
# -----------------------------------------------------------------------------

def test_registry_create_model_identity_rejects_artifact_path_for_non_local_kinds(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    from workbench.participants import registry
    from workbench.participants.schemas import ModelIdentityCreate

    # external_agent with artifact_path -> reject
    with pytest.raises(ValueError, match="仅 local_model 模型身份可在创建时绑定本地 artifact_path"):
        registry.create_model_identity(
            ModelIdentityCreate(
                model_identity_id="model:ext_bad",
                label="Ext Bad",
                kind="external_agent",
                artifact_path="/some/path.pth",
            )
        )

    # none with artifact_path -> reject
    with pytest.raises(ValueError, match="仅 local_model 模型身份可在创建时绑定本地 artifact_path"):
        registry.create_model_identity(
            ModelIdentityCreate(
                model_identity_id="model:none_bad",
                label="None Bad",
                kind="none",
                artifact_path="/some/path.pth",
            )
        )


def test_registry_add_model_artifact_rejects_non_local_model_kinds(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    from workbench.participants import registry
    from workbench.participants.schemas import ModelArtifactCreate, ModelIdentityCreate

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:ext_only", label="Ext Only", kind="external_agent")
    )
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:none_only", label="None Only", kind="none")
    )

    with pytest.raises(ValueError, match="仅 local_model 模型身份可挂载 ModelArtifact"):
        registry.add_model_artifact(
            "model:ext_only",
            ModelArtifactCreate(label="test.pth", artifact_path="/test.pth"),
        )

    with pytest.raises(ValueError, match="仅 local_model 模型身份可挂载 ModelArtifact"):
        registry.add_model_artifact(
            "model:none_only",
            ModelArtifactCreate(label="test.pth", artifact_path="/test.pth"),
        )


def test_registry_model_identity_kind_immutability(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    from pydantic import ValidationError
    from workbench.participants import registry
    from workbench.participants.schemas import ModelIdentityCreate, ModelIdentityUpdate

    ident = registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:orig_local", label="Orig Local", kind="local_model")
    )
    assert ident.kind == "local_model"

    # Schema ModelIdentityUpdate forbids kind mutation (extra='forbid')
    with pytest.raises(ValidationError):
        ModelIdentityUpdate.model_validate({"label": "Renamed", "kind": "external_agent"})

    # Updating other fields preserves kind
    updated = registry.update_model_identity("model:orig_local", ModelIdentityUpdate(label="Renamed Local"))
    assert updated.label == "Renamed Local"
    assert updated.kind == "local_model"


def test_registry_crud_external_revision_and_retired_immutability(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KEQING_DATA_ROOT", str(tmp_path))
    from workbench.participants import registry
    from workbench.participants.schemas import (
        ExternalModelRevisionCreate,
        ExternalModelRevisionUpdate,
        ModelIdentityCreate,
    )

    # 1. Create external agent identity
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="model:mortal41b",
            label="Mortal 4.1b",
            kind="external_agent",
        )
    )

    # 2. Add ExternalModelRevision
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

    # 3. Retire revision
    retired = registry.retire_external_revision("model:mortal41b", "external:mortal41b:4.1b")
    assert retired.stage == "retired"
    assert retired.is_current is False

    # 4. Attempting any update on retired revision -> REJECT (immutable seal)
    with pytest.raises(ValueError, match="已退役.*已封存，不可变更任何字段"):
        registry.update_external_revision(
            "model:mortal41b",
            "external:mortal41b:4.1b",
            ExternalModelRevisionUpdate(stage="promoted"),
        )

    with pytest.raises(ValueError, match="已退役.*已封存，不可变更任何字段"):
        registry.update_external_revision(
            "model:mortal41b",
            "external:mortal41b:4.1b",
            ExternalModelRevisionUpdate(capabilities=["review_allowed"]),
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
