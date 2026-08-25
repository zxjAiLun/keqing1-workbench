# -*- coding: utf-8 -*-
"""Unified Model Artifact Policy and Admission Resolver.

Single source of truth for resolving and validating ModelArtifact provenance,
lifecycle stages, and execution capabilities across Ladder, Play-with-you,
and Replay/Review.

Invariant:
  Every execution / match participant requiring model provenance must resolve
  to EXACTLY ONE canonical ModelIdentity + ModelArtifact + Checkpoint.
  0 matches, >1 matches (ambiguity), account/identity mismatch, artifact/identity
  mismatch, checkpoint mismatch, or capability violation FAIL CLOSED.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workbench.runtime.resolver import (
    REPO_ROOT,
    resolve_bot_spec,
    resolve_model_checkpoint,
    search_authoritative_checkpoint,
)
from .schemas import ArtifactCapability, ModelArtifact, ModelIdentity


@dataclass(frozen=True)
class CanonicalArtifactBinding:
    account_id: str | None
    model_identity_id: str
    model_artifact_id: str
    resolved_checkpoint_path: Path
    artifact: ModelArtifact
    identity: ModelIdentity


def _resolve_physical_artifact_path(
    artifact: ModelArtifact, project_root: Path
) -> Path | None:
    """Resolve physical checkpoint path for an artifact record."""
    raw = artifact.artifact_path
    if not raw:
        return None
    try:
        return resolve_model_checkpoint(raw, project_root).resolve()
    except (FileNotFoundError, RuntimeError, ValueError):
        return None


def resolve_artifact_binding(
    *,
    account_id: str | None = None,
    identity_id: str | None = None,
    artifact_id: str | None = None,
    checkpoint: str | Path | None = None,
    purpose: ArtifactCapability,
    project_root: Path = REPO_ROOT,
    registry: Any = None,
) -> CanonicalArtifactBinding:
    """Resolve and enforce policy admission for a model artifact.

    Parameters
    ----------
    account_id:
        Optional participant account ID. If provided, identity must belong to account.
    identity_id:
        Optional requested ModelIdentity ID.
    artifact_id:
        Optional requested ModelArtifact ID.
    checkpoint:
        Optional checkpoint string, alias (e.g. "70k"), or physical path.
    purpose:
        Required capability: "ladder_eligible" | "playwithyou_allowed" | "review_allowed".
    project_root:
        Project root directory for relative path resolution.
    registry:
        Participants registry module (defaults to workbench.participants.registry).
    """
    if registry is None:
        from . import registry as default_registry
        registry = default_registry

    account = None
    if account_id:
        account = registry.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        if not account.enabled:
            raise ValueError(f"账号已停用: {account_id}")

    # Determine resolved physical checkpoint if requested
    resolved_checkpoint_path: Path | None = None
    if checkpoint is not None:
        raw_cp_str = str(checkpoint).strip()
        if raw_cp_str:
            try:
                _kind, resolved = resolve_bot_spec(raw_cp_str, project_root)
                if resolved is not None:
                    resolved_checkpoint_path = Path(str(resolved)).resolve()
                else:
                    resolved_checkpoint_path = resolve_model_checkpoint(raw_cp_str, project_root).resolve()
            except Exception as exc:
                raise ValueError(f"无法解析 checkpoint 路径 {checkpoint!r}: {exc}") from exc

    # If explicit identity and artifact provided
    if identity_id and artifact_id:
        identity = registry.get_model_identity(identity_id)
        if identity is None:
            raise ValueError(f"模型身份不存在: {identity_id}")
        if account_id and not registry.identity_belongs_to_account(identity_id, account_id):
            raise ValueError(f"模型身份 {identity_id} 不属于账号 {account_id}")
        artifact = next((a for a in identity.artifacts if a.model_artifact_id == artifact_id), None)
        if artifact is None:
            raise ValueError(f"模型产物 {artifact_id} 不属于身份 {identity_id}")

        phys_path = _resolve_physical_artifact_path(artifact, project_root)
        if phys_path is None:
            raise ValueError(f"模型产物 {artifact_id} 对应的实体文件不存在")

        if resolved_checkpoint_path is not None and phys_path != resolved_checkpoint_path:
            raise ValueError(
                f"模型产物 {artifact_id} (路径 {phys_path}) 与目标 checkpoint ({resolved_checkpoint_path}) 不一致"
            )

        _enforce_artifact_purpose(artifact, purpose, account_id=account_id)
        return CanonicalArtifactBinding(
            account_id=account_id,
            model_identity_id=identity.model_identity_id,
            model_artifact_id=artifact.model_artifact_id,
            resolved_checkpoint_path=phys_path,
            artifact=artifact,
            identity=identity,
        )

    # Search candidates matching given constraints
    candidates: list[tuple[ModelIdentity, ModelArtifact, Path]] = []
    all_identities = registry.list_models()

    for ident in all_identities:
        if identity_id and ident.model_identity_id != identity_id:
            continue
        if account_id and not registry.identity_belongs_to_account(ident.model_identity_id, account_id):
            continue

        for art in ident.artifacts:
            if artifact_id and art.model_artifact_id != artifact_id:
                continue

            phys = _resolve_physical_artifact_path(art, project_root)
            if phys is None:
                continue

            if resolved_checkpoint_path is not None and phys != resolved_checkpoint_path:
                continue

            candidates.append((ident, art, phys))

    # If no exact match found but account has a bound model_identity
    if not candidates and account_id and account and account.model_identity_id and not identity_id and not artifact_id:
        ident = registry.get_model_identity(account.model_identity_id)
        if ident is not None:
            for art in ident.artifacts:
                phys = _resolve_physical_artifact_path(art, project_root)
                if phys is None:
                    continue
                if resolved_checkpoint_path is not None and phys != resolved_checkpoint_path:
                    continue
                candidates.append((ident, art, phys))

    if len(candidates) == 0:
        desc = []
        if account_id:
            desc.append(f"account={account_id}")
        if identity_id:
            desc.append(f"identity={identity_id}")
        if artifact_id:
            desc.append(f"artifact={artifact_id}")
        if checkpoint:
            desc.append(f"checkpoint={checkpoint}")
        spec_info = ", ".join(desc) if desc else "unspecified"
        raise ValueError(f"未找到匹配的模型产物 (0 match, criteria: {spec_info})，拒绝准入")

    if len(candidates) > 1:
        cand_names = [f"{i.model_identity_id}:{a.model_artifact_id}" for i, a, _ in candidates]
        raise ValueError(
            f"模型产物存在歧义，匹配到 {len(candidates)} 个候选 ({', '.join(cand_names)})，拒绝准入"
        )

    ident, art, phys = candidates[0]
    _enforce_artifact_purpose(art, purpose, account_id=account_id)
    return CanonicalArtifactBinding(
        account_id=account_id,
        model_identity_id=ident.model_identity_id,
        model_artifact_id=art.model_artifact_id,
        resolved_checkpoint_path=phys,
        artifact=art,
        identity=ident,
    )


def _enforce_artifact_purpose(
    artifact: ModelArtifact, purpose: ArtifactCapability, *, account_id: str | None = None
) -> None:
    """Enforce capability and stage permissions for the given purpose."""
    acc_prefix = f"账号 {account_id} 所用" if account_id else ""
    stage = getattr(artifact, "stage", "candidate")

    if purpose == "ladder_eligible":
        if stage != "promoted" or not artifact.is_ladder_eligible:
            raise ValueError(
                f"{acc_prefix}模型产物 {artifact.model_artifact_id} 处于 {stage} 状态或缺少 ladder_eligible 权限，无正式天梯参赛资格"
            )
    elif purpose == "playwithyou_allowed":
        if stage == "retired" or not artifact.is_playwithyou_allowed:
            raise ValueError(
                f"{acc_prefix}模型产物 {artifact.model_artifact_id} 处于 {stage} 状态或缺少 playwithyou_allowed 权限，无法用于 Play-with-you 对局"
            )
    elif purpose == "review_allowed":
        if not artifact.is_review_allowed:
            raise ValueError(
                f"{acc_prefix}模型产物 {artifact.model_artifact_id} 缺少 review_allowed 权限"
            )
    else:
        raise ValueError(f"未知的模型产物准入目标: {purpose}")


__all__ = [
    "CanonicalArtifactBinding",
    "resolve_artifact_binding",
]
