# -*- coding: utf-8 -*-
"""Execution-Provenance-First Control Plane Resolver (R13-A).

Unifies execution provenance validation across all participant controller types:
- Human: Account + enabled + controller=human_ui (no model evidence)
- Local managed model: Account + Identity(kind=local_model) + exact ModelArtifact + physical checkpoint
- External model: Account + Identity(kind=external_agent) + exact ExternalModelRevision (NO filesystem/checkpoint required)

Invariant:
  Every execution / match participant requiring provenance must resolve
  to EXACTLY ONE canonical execution provenance.
  0 matches, >1 matches (ambiguity), account/identity/controller kind mismatch,
  or capability violation FAIL CLOSED.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from workbench.runtime.resolver import REPO_ROOT

from .artifact_policy import resolve_artifact_binding
from .schemas import (
    ArtifactCapability,
    ControllerType,
    ExternalModelRevision,
    ModelArtifact,
    ModelIdentity,
)


@dataclass(frozen=True)
class CanonicalExecutionProvenance:
    kind: Literal["human", "local_artifact", "external_revision"]
    account_id: str
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    external_revision_id: str | None = None
    resolved_checkpoint_path: Path | None = None
    model_artifact: ModelArtifact | None = None
    external_revision: ExternalModelRevision | None = None
    model_identity: ModelIdentity | None = None


def resolve_execution_provenance(
    *,
    account_id: str,
    controller_type: ControllerType | str | None = None,
    identity_id: str | None = None,
    artifact_id: str | None = None,
    external_revision_id: str | None = None,
    checkpoint: str | Path | None = None,
    purpose: ArtifactCapability = "ladder_eligible",
    project_root: Path = REPO_ROOT,
    registry: Any = None,
) -> CanonicalExecutionProvenance:
    """Resolve and enforce policy admission for a participant's execution provenance.

    Parameters
    ----------
    account_id:
        Participant account ID.
    controller_type:
        Optional requested or actual controller type ("human_ui" | "local_model" | "external_agent").
        Defaults to account.default_controller.
    identity_id:
        Optional requested ModelIdentity ID.
    artifact_id:
        Optional requested ModelArtifact ID (valid ONLY for local_model).
    external_revision_id:
        Optional requested ExternalModelRevision ID (valid ONLY for external_agent).
    checkpoint:
        Optional checkpoint string or path (valid ONLY for local_model).
    purpose:
        Required capability: "ladder_eligible" | "playwithyou_allowed" | "review_allowed".
    project_root:
        Project root directory for local artifact checkpoint resolution.
    registry:
        Participants registry module (defaults to workbench.participants.registry).
    """
    if registry is None:
        from . import registry as default_registry

        registry = default_registry

    account = registry.get_account(account_id)
    if account is None:
        raise ValueError(f"账号不存在: {account_id}")
    if not account.enabled:
        raise ValueError(f"账号已停用: {account_id}")

    effective_controller = controller_type or account.default_controller

    # -------------------------------------------------------------------------
    # 1. Human branch
    # -------------------------------------------------------------------------
    if account.account_type == "human":
        if purpose == "ladder_eligible" and effective_controller != "human_ui":
            raise ValueError(
                f"正式天梯人类账号 {account_id} 控制器必须为 human_ui (当前为 {effective_controller})"
            )
        if effective_controller not in ("human_ui", "manual_only"):
            raise ValueError(
                f"人类账号 {account_id} 控制器类型不匹配: {effective_controller} (期望 human_ui 或 manual_only)"
            )
        if identity_id is not None:
            raise ValueError(f"人类账号 {account_id} 禁止携带模型身份证据 (identity_id={identity_id})")
        if artifact_id is not None:
            raise ValueError(f"人类账号 {account_id} 禁止携带模型产物证据 (artifact_id={artifact_id})")
        if external_revision_id is not None:
            raise ValueError(
                f"人类账号 {account_id} 禁止携带外部版本证据 (external_revision_id={external_revision_id})"
            )
        if checkpoint is not None:
            raise ValueError(f"人类账号 {account_id} 禁止携带 checkpoint 证据")

        return CanonicalExecutionProvenance(
            kind="human",
            account_id=account.account_id,
        )

    # -------------------------------------------------------------------------
    # 2. Local managed model branch
    # -------------------------------------------------------------------------
    if account.account_type == "managed_bot":
        if effective_controller != "local_model":
            raise ValueError(
                f"本地托管机器人账号 {account_id} 控制器类型不匹配: {effective_controller} (期望 local_model)"
            )
        if external_revision_id is not None:
            raise ValueError(
                f"本地托管模型账号 {account_id} 禁止绑定外部版本证据 (external_revision_id={external_revision_id})"
            )

        target_identity_id = identity_id or account.model_identity_id
        if not target_identity_id:
            raise ValueError(f"本地托管机器人账号 {account_id} 未配置或提供 model_identity_id")

        ident = registry.get_model_identity(target_identity_id)
        if ident is None:
            raise ValueError(f"模型身份不存在: {target_identity_id}")
        if ident.kind != "local_model":
            raise ValueError(
                f"账号 {account_id} (managed_bot) 与模型身份 {target_identity_id} (kind={ident.kind}) 类型错配"
            )

        # Delegate local physical artifact exact-match to R12 artifact_policy
        binding = resolve_artifact_binding(
            account_id=account_id,
            identity_id=target_identity_id,
            artifact_id=artifact_id,
            checkpoint=checkpoint,
            purpose=purpose,
            project_root=project_root,
            registry=registry,
        )
        return CanonicalExecutionProvenance(
            kind="local_artifact",
            account_id=account.account_id,
            model_identity_id=binding.model_identity_id,
            model_artifact_id=binding.model_artifact_id,
            resolved_checkpoint_path=binding.resolved_checkpoint_path,
            model_artifact=binding.artifact,
            model_identity=binding.identity,
        )

    # -------------------------------------------------------------------------
    # 3. External agent branch
    # -------------------------------------------------------------------------
    if account.account_type == "external_bot":
        if effective_controller != "external_agent":
            raise ValueError(
                f"外部机器人账号 {account_id} 控制器类型不匹配: {effective_controller} (期望 external_agent)"
            )
        if artifact_id is not None:
            raise ValueError(f"外部模型账号 {account_id} 禁止绑定本地 ModelArtifact (artifact_id={artifact_id})")
        if checkpoint is not None:
            raise ValueError(f"外部模型账号 {account_id} 禁止绑定本地 Checkpoint")

        target_identity_id = identity_id or account.model_identity_id
        if not target_identity_id:
            raise ValueError(f"外部机器人账号 {account_id} 未配置或提供 model_identity_id")

        ident = registry.get_model_identity(target_identity_id)
        if ident is None:
            raise ValueError(f"模型身份不存在: {target_identity_id}")
        if ident.kind != "external_agent":
            raise ValueError(
                f"账号 {account_id} (external_bot) 与模型身份 {target_identity_id} (kind={ident.kind}) 类型错配"
            )

        # Check account <-> identity binding compatibility
        if not registry.identity_belongs_to_account(target_identity_id, account_id):
            raise ValueError(f"模型身份 {target_identity_id} 与账号 {account_id} 绑定不兼容")

        # Step 1: Identify the exact ExternalModelRevision (NO lifecycle disambiguation!)
        if external_revision_id is not None:
            selected_rev = next(
                (r for r in ident.external_revisions if r.external_revision_id == external_revision_id),
                None,
            )
            if selected_rev is None:
                raise ValueError(
                    f"外部版本不存在或不属于模型 {target_identity_id}: {external_revision_id}"
                )
        else:
            total_revisions = ident.external_revisions
            if len(total_revisions) == 0:
                raise ValueError(
                    f"未找到匹配的外部版本 (0 match, criteria: account={account_id}, identity={target_identity_id})"
                )
            if len(total_revisions) > 1:
                rev_ids = [r.external_revision_id for r in total_revisions]
                raise ValueError(
                    f"外部版本匹配存在歧义 (>1 matches: {rev_ids}, criteria: account={account_id}, "
                    f"identity={target_identity_id})，必须显式指定 external_revision_id"
                )
            selected_rev = total_revisions[0]

        # Step 2: Validate capability and stage for purpose on the identified revision
        if purpose == "ladder_eligible" and not selected_rev.is_ladder_eligible:
            raise ValueError(
                f"外部版本 {selected_rev.external_revision_id} 不具备天梯资格 "
                f"(stage={selected_rev.stage}, capabilities={selected_rev.capabilities})"
            )
        if purpose == "playwithyou_allowed" and not selected_rev.is_playwithyou_allowed:
            raise ValueError(
                f"外部版本 {selected_rev.external_revision_id} 不具备 Play-with-you 权限 (stage={selected_rev.stage})"
            )
        if purpose == "review_allowed" and not selected_rev.is_review_allowed:
            raise ValueError(
                f"外部版本 {selected_rev.external_revision_id} 不具备 Review 权限"
            )

        return CanonicalExecutionProvenance(
            kind="external_revision",
            account_id=account.account_id,
            model_identity_id=ident.model_identity_id,
            external_revision_id=selected_rev.external_revision_id,
            external_revision=selected_rev,
            model_identity=ident,
        )

    raise ValueError(f"未知的账号类型: {account.account_type}")
