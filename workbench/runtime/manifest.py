# -*- coding: utf-8 -*-
"""R14-A: Season Runtime Manifest & Start Preflight

核心职责：
1. 规范化提取 Season 权威投影（Season Authority Projection）并计算确定性 SHA-256（season_authority_hash）；
2. 构造严格类型化的运行时规范（HumanRuntimeSpec, LocalArtifactRuntimeSpec, ExternalRevisionRuntimeSpec）；
3. 按照 Season 冻结的 exact frozen provenance 构造确定性 SeasonRuntimeManifest（绝不查询 Catalog Current）；
4. 提供纯只读 Preflight 校验（preflight_season_runtime）；
5. 提供单文件原子写 Start 迁移逻辑（start_season_runtime，双锁保护：participants_data_lock -> _registry_lock）；
6. 对历史运行/归档 Season 支持投影式只读 Manifest（projected_legacy，fail-closed 完整性保障）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workbench.participants import paths as participant_paths
from workbench.participants import registry as participants_registry
from workbench.participants.schemas import (
    AccountType,
    ControllerType,
    FrozenExecutionProvenance,
)
from workbench.runtime.resolver.base import REPO_ROOT
from workbench.runtime.resolver.model import resolve_model_checkpoint


# ---------------------------------------------------------------------------
# 1. 运行时规范模型 (Discriminated Runtime Specs)
# ---------------------------------------------------------------------------

class HumanRuntimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["human"] = "human"
    controller_type: Literal["human_ui"] = "human_ui"


class LocalArtifactRuntimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["local_artifact"] = "local_artifact"
    controller_type: Literal["local_model"] = "local_model"
    model_identity_id: str
    model_artifact_id: str
    artifact_path: str
    resolved_checkpoint_path: str
    artifact_hash: str | None = None


class ExternalRevisionRuntimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["external_revision"] = "external_revision"
    controller_type: Literal["external_agent"] = "external_agent"
    model_identity_id: str
    external_revision_id: str
    provider: str
    version: str
    external_ref: str | None = None


RuntimeSpecUnion = HumanRuntimeSpec | LocalArtifactRuntimeSpec | ExternalRevisionRuntimeSpec


class SeasonRuntimeParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: str
    display_name: str
    account_type: AccountType
    controller_type: ControllerType
    model_identity_id: str | None = None
    execution_provenance: FrozenExecutionProvenance
    runtime_spec: RuntimeSpecUnion


class SeasonRuntimeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["keqing.season.runtime_manifest.v1"] = "keqing.season.runtime_manifest.v1"
    manifest_id: str
    season_id: str
    season_authority_hash: str
    materialization: Literal["frozen", "projected_legacy"]
    participants: list[SeasonRuntimeParticipant]
    scoring_summary: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None


class PreflightIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    account_id: str | None = None
    model_identity_id: str | None = None
    detail: str | None = None


class SeasonPreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    season_id: str
    season_status: str
    can_start: bool
    state: Literal["ready", "blocked"]
    issues: list[PreflightIssue] = Field(default_factory=list)
    manifest: SeasonRuntimeManifest | None = None


# ---------------------------------------------------------------------------
# 2. Season Authority Projection & Canonical Hashing
# ---------------------------------------------------------------------------

def build_season_authority_projection(season: dict[str, Any]) -> dict[str, Any]:
    """提取 Season 中真正影响 Runtime Authority 的 canonical projection。

    明确排除生命周期与展示元数据：
    - status, default, title, created_at, updated_at, reopened_at, report_dir, runtime_manifest 等。
    """
    season_id = str(season.get("season_id") or "")
    scoring = season.get("scoring") or {}

    raw_models = season.get("models") or []
    canonical_models = []
    for m in raw_models:
        if not isinstance(m, dict):
            continue
        accounts = []
        for a in (m.get("accounts") or []):
            if isinstance(a, dict) and a.get("account_id"):
                accounts.append({
                    "account_id": a["account_id"],
                    "display_name": a.get("display_name"),
                })
        accounts.sort(key=lambda x: x["account_id"])

        canonical_models.append({
            "model_id": str(m.get("model_id") or ""),
            "model_identity_id": m.get("model_identity_id"),
            "accounts": accounts,
            "execution_provenance": m.get("execution_provenance"),
            "model_artifact_id": m.get("model_artifact_id"),
            "external_revision_id": m.get("external_revision_id"),
        })

    canonical_models.sort(key=lambda x: x["model_id"])

    return {
        "season_id": season_id,
        "scoring": scoring,
        "models": canonical_models,
    }


def compute_season_authority_hash(season: dict[str, Any]) -> str:
    """计算 Season 权威投影的确定性 SHA-256（十六进制字符串）。"""
    projection = build_season_authority_projection(season)
    raw_json = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


def compute_manifest_id(season_id: str, authority_hash: str, participants: list[SeasonRuntimeParticipant]) -> str:
    """计算不含 generated_at 的确定性 manifest_id。"""
    payload = {
        "season_id": season_id,
        "season_authority_hash": authority_hash,
        "participants": [p.model_dump() for p in participants],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sub_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"manifest:{season_id}:{sub_hash}"


# ---------------------------------------------------------------------------
# 3. Runtime Resolution & Manifest Construction (Exact Frozen Match)
# ---------------------------------------------------------------------------

def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_runtime_manifest(
    season: dict[str, Any],
    *,
    materialization: Literal["frozen", "projected_legacy"] = "frozen",
    generated_at: str | None = None,
    project_root: Path = REPO_ROOT,
    registry=participants_registry,
) -> tuple[SeasonRuntimeManifest, list[PreflightIssue]]:
    """按照 Season 冻结的 exact frozen provenance 构造确定性 Manifest。

    严禁查询 Catalog Current 替换。
    返回 (Manifest, list[PreflightIssue])。
    """
    season_id = str(season.get("season_id") or "")
    authority_hash = compute_season_authority_hash(season)
    issues: list[PreflightIssue] = []

    participants: list[SeasonRuntimeParticipant] = []
    models = season.get("models") or []
    if not isinstance(models, list):
        models = []

    # 汇总所有 accounts
    accounts_by_id = {a.account_id: a for a in registry.list_accounts()}

    seen_account_ids: set[str] = set()

    for m in models:
        if not isinstance(m, dict):
            continue
        model_id = str(m.get("model_id") or "")
        model_identity_id = m.get("model_identity_id")
        raw_exec_prov = m.get("execution_provenance")
        m_accounts = m.get("accounts") or []

        for acc_entry in m_accounts:
            if not isinstance(acc_entry, dict):
                continue
            account_id = acc_entry.get("account_id")
            if not account_id:
                continue

            if account_id in seen_account_ids:
                issues.append(PreflightIssue(
                    code="duplicate_account_in_season",
                    message=f"账号 {account_id} 在赛季中重复出现",
                    account_id=account_id,
                ))
                continue
            seen_account_ids.add(account_id)

            account = accounts_by_id.get(account_id)
            if account is None:
                issues.append(PreflightIssue(
                    code="account_not_found",
                    message=f"赛季成员账号不存在: {account_id}",
                    account_id=account_id,
                ))
                continue

            # 使用 Season enrollment 中已冻结的 display_name，绝不被当前 account 篡改
            frozen_display_name = acc_entry.get("display_name") or account.display_name

            # 检查 account 是否启用 (frozen 启动时必须可用)
            if materialization == "frozen" and not getattr(account, "enabled", True):
                issues.append(PreflightIssue(
                    code="account_disabled",
                    message=f"账号 {account_id} 已被禁用，无法用于赛季运行",
                    account_id=account_id,
                ))

            # 推导 ControllerType
            account_type = account.account_type
            controller_type: ControllerType
            if account.default_controller:
                controller_type = account.default_controller
            elif account_type == "human":
                controller_type = "human_ui"
            elif account_type == "external_bot":
                controller_type = "external_agent"
            else:
                controller_type = "local_model"

            # 解析 FrozenExecutionProvenance
            frozen_prov: FrozenExecutionProvenance
            if raw_exec_prov:
                try:
                    frozen_prov = FrozenExecutionProvenance.model_validate(raw_exec_prov)
                except Exception as exc:
                    issues.append(PreflightIssue(
                        code="invalid_frozen_provenance",
                        message=f"组 {model_id} 的 execution_provenance 结构无效: {exc}",
                        account_id=account_id,
                    ))
                    continue
            else:
                # 若 Season 未记录 execution_provenance（例如 legacy 未冻结的 human 组）
                if model_id == "human" or account_type == "human":
                    frozen_prov = FrozenExecutionProvenance(kind="human")
                else:
                    issues.append(PreflightIssue(
                        code="missing_execution_provenance",
                        message=f"AI 组 {model_id} 缺少 execution_provenance 凭据",
                        account_id=account_id,
                        model_identity_id=model_identity_id,
                    ))
                    continue

            # ---------------------------------------------------------------
            # 严格 Account / Provenance 一致性与控制器校验
            # ---------------------------------------------------------------
            if controller_type == "manual_only":
                issues.append(PreflightIssue(
                    code="controller_manual_only_not_startable",
                    message=f"账号 {account_id} 的控制器为 manual_only，无法用于正式自动化天梯运行",
                    account_id=account_id,
                ))

            # 构建精确 RuntimeSpec
            runtime_spec: RuntimeSpecUnion
            if frozen_prov.kind == "human":
                if materialization == "frozen":
                    if account_type != "human":
                        issues.append(PreflightIssue(
                            code="account_type_mismatch",
                            message=f"真人组账号 {account_id} 的 account_type 必须为 human (当前为 {account_type})",
                            account_id=account_id,
                        ))
                    if account.model_identity_id is not None:
                        issues.append(PreflightIssue(
                            code="human_account_has_model_binding",
                            message=f"真人账号 {account_id} 不能绑定模型身份 (当前绑定 {account.model_identity_id})",
                            account_id=account_id,
                        ))
                    if controller_type != "human_ui":
                        issues.append(PreflightIssue(
                            code="controller_type_mismatch",
                            message=f"真人账号 {account_id} 控制器必须为 human_ui (当前为 {controller_type})",
                            account_id=account_id,
                        ))
                runtime_spec = HumanRuntimeSpec(kind="human", controller_type="human_ui")

            elif frozen_prov.kind == "local_artifact":
                target_ident_id = frozen_prov.model_identity_id or model_identity_id
                target_art_id = frozen_prov.model_artifact_id
                if not target_ident_id or not target_art_id:
                    issues.append(PreflightIssue(
                        code="incomplete_local_artifact_provenance",
                        message=f"本地模型凭据不完整: ident={target_ident_id}, art={target_art_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                if materialization == "frozen":
                    if account_type != "managed_bot":
                        issues.append(PreflightIssue(
                            code="account_type_mismatch",
                            message=f"本地模型账号 {account_id} 的 account_type 必须为 managed_bot (当前为 {account_type})",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if account.model_identity_id != target_ident_id:
                        issues.append(PreflightIssue(
                            code="account_binding_drift",
                            message=f"账号 {account_id} 当前模型绑定 ({account.model_identity_id}) 与赛季冻结身份 ({target_ident_id}) 不一致",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if model_identity_id and model_identity_id != target_ident_id:
                        issues.append(PreflightIssue(
                            code="group_identity_mismatch",
                            message=f"组身份 {model_identity_id} 与凭据身份 {target_ident_id} 不一致",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if controller_type != "local_model":
                        issues.append(PreflightIssue(
                            code="controller_type_mismatch",
                            message=f"本地模型账号 {account_id} 控制器必须为 local_model (当前为 {controller_type})",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))

                ident = registry.get_model_identity(target_ident_id)
                if ident is None:
                    issues.append(PreflightIssue(
                        code="model_identity_not_found",
                        message=f"模型身份不存在: {target_ident_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                if materialization == "frozen" and ident.kind != "local_model":
                    issues.append(PreflightIssue(
                        code="model_identity_kind_mismatch",
                        message=f"模型身份 {target_ident_id} 的 kind 必须为 local_model (当前为 {ident.kind})",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))

                # 必须 EXACT MATCH 指定 artifact_id，严禁 fallback 到 current!
                exact_art = next((a for a in ident.artifacts if a.model_artifact_id == target_art_id), None)
                if exact_art is None:
                    issues.append(PreflightIssue(
                        code="artifact_not_found",
                        message=f"模型身份 {target_ident_id} 未找到指定产物: {target_art_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                # 只有在全新启动 (materialization == "frozen" / draft preflight) 时才严格检查 stage / eligible
                if materialization == "frozen":
                    if exact_art.stage == "retired":
                        issues.append(PreflightIssue(
                            code="artifact_retired",
                            message=f"模型产物 {target_art_id} 已退役，无法用于新赛季运行",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    elif exact_art.stage != "promoted" or not exact_art.is_ladder_eligible:
                        issues.append(PreflightIssue(
                            code="artifact_not_ladder_eligible",
                            message=f"模型产物 {target_art_id} 阶段为 {exact_art.stage}，无正式天梯资格",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))

                # 检查本地文件路径
                if not exact_art.artifact_path:
                    issues.append(PreflightIssue(
                        code="artifact_path_empty",
                        message=f"模型产物 {target_art_id} 未配置本地路径",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                try:
                    resolved_cp = resolve_model_checkpoint(exact_art.artifact_path, project_root)
                except (FileNotFoundError, ValueError, Exception) as exc:
                    issues.append(PreflightIssue(
                        code="checkpoint_file_missing" if isinstance(exc, FileNotFoundError) else "checkpoint_resolve_failed",
                        message=f"解析/定位产物路径失败 ({exact_art.artifact_path}): {exc}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                        detail=str(exc),
                    ))
                    continue

                if not resolved_cp.exists() or not resolved_cp.is_file():
                    issues.append(PreflightIssue(
                        code="checkpoint_file_missing",
                        message=f"模型产物文件不存在: {resolved_cp}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                        detail=str(resolved_cp),
                    ))
                    continue

                # 校验 hash（若已登记）
                if exact_art.hash:
                    actual_hash = _file_sha256(resolved_cp)
                    if actual_hash.lower() != exact_art.hash.lower():
                        issues.append(PreflightIssue(
                            code="artifact_hash_mismatch",
                            message=f"模型产物文件哈希不匹配: 期望 {exact_art.hash}, 实际 {actual_hash}",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                        continue

                runtime_spec = LocalArtifactRuntimeSpec(
                    kind="local_artifact",
                    controller_type="local_model",
                    model_identity_id=target_ident_id,
                    model_artifact_id=target_art_id,
                    artifact_path=exact_art.artifact_path,
                    resolved_checkpoint_path=str(resolved_cp),
                    artifact_hash=exact_art.hash,
                )

            elif frozen_prov.kind == "external_revision":
                target_ident_id = frozen_prov.model_identity_id or model_identity_id
                target_rev_id = frozen_prov.external_revision_id
                if not target_ident_id or not target_rev_id:
                    issues.append(PreflightIssue(
                        code="incomplete_external_revision_provenance",
                        message=f"外部模型凭据不完整: ident={target_ident_id}, rev={target_rev_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                if materialization == "frozen":
                    if account_type != "external_bot":
                        issues.append(PreflightIssue(
                            code="account_type_mismatch",
                            message=f"外部模型账号 {account_id} 的 account_type 必须为 external_bot (当前为 {account_type})",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if account.model_identity_id != target_ident_id:
                        issues.append(PreflightIssue(
                            code="account_binding_drift",
                            message=f"账号 {account_id} 当前模型绑定 ({account.model_identity_id}) 与赛季冻结身份 ({target_ident_id}) 不一致",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if model_identity_id and model_identity_id != target_ident_id:
                        issues.append(PreflightIssue(
                            code="group_identity_mismatch",
                            message=f"组身份 {model_identity_id} 与凭据身份 {target_ident_id} 不一致",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    if controller_type != "external_agent":
                        issues.append(PreflightIssue(
                            code="controller_type_mismatch",
                            message=f"外部模型账号 {account_id} 控制器必须为 external_agent (当前为 {controller_type})",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))

                ident = registry.get_model_identity(target_ident_id)
                if ident is None:
                    issues.append(PreflightIssue(
                        code="model_identity_not_found",
                        message=f"模型身份不存在: {target_ident_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                if materialization == "frozen" and ident.kind != "external_agent":
                    issues.append(PreflightIssue(
                        code="model_identity_kind_mismatch",
                        message=f"模型身份 {target_ident_id} 的 kind 必须为 external_agent (当前为 {ident.kind})",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))

                # 必须 EXACT MATCH 指定 revision_id，严禁 fallback 到 current!
                exact_rev = next((r for r in ident.external_revisions if r.external_revision_id == target_rev_id), None)
                if exact_rev is None:
                    issues.append(PreflightIssue(
                        code="external_revision_not_found",
                        message=f"模型身份 {target_ident_id} 未找到指定版本: {target_rev_id}",
                        account_id=account_id,
                        model_identity_id=target_ident_id,
                    ))
                    continue

                # 只有在全新启动 (materialization == "frozen" / draft preflight) 时才严格检查 stage / eligible
                if materialization == "frozen":
                    if exact_rev.stage == "retired":
                        issues.append(PreflightIssue(
                            code="external_revision_retired",
                            message=f"外部版本 {target_rev_id} 已退役，无法用于新赛季运行",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))
                    elif exact_rev.stage != "promoted" or not exact_rev.is_ladder_eligible:
                        issues.append(PreflightIssue(
                            code="external_revision_not_ladder_eligible",
                            message=f"外部版本 {target_rev_id} 阶段为 {exact_rev.stage}，无正式天梯资格",
                            account_id=account_id,
                            model_identity_id=target_ident_id,
                        ))

                runtime_spec = ExternalRevisionRuntimeSpec(
                    kind="external_revision",
                    controller_type="external_agent",
                    model_identity_id=target_ident_id,
                    external_revision_id=target_rev_id,
                    provider=exact_rev.provider,
                    version=exact_rev.version,
                    external_ref=exact_rev.external_ref,
                )
            else:
                issues.append(PreflightIssue(
                    code="unknown_provenance_kind",
                    message=f"未知的凭据类型: {frozen_prov.kind}",
                    account_id=account_id,
                ))
                continue

            participants.append(SeasonRuntimeParticipant(
                account_id=account.account_id,
                display_name=frozen_display_name,
                account_type=account.account_type,
                controller_type=controller_type,
                model_identity_id=target_ident_id if frozen_prov.kind != "human" else None,
                execution_provenance=frozen_prov,
                runtime_spec=runtime_spec,
            ))

    # 排序 participants 确保确定性
    participants.sort(key=lambda p: p.account_id)

    manifest_id = compute_manifest_id(season_id, authority_hash, participants)
    manifest = SeasonRuntimeManifest(
        schema_version="keqing.season.runtime_manifest.v1",
        manifest_id=manifest_id,
        season_id=season_id,
        season_authority_hash=authority_hash,
        materialization=materialization,
        participants=participants,
        scoring_summary=season.get("scoring") or {},
        generated_at=generated_at,
    )

    return manifest, issues


# ---------------------------------------------------------------------------
# 4. Pure Read-Only Preflight
# ---------------------------------------------------------------------------

def preflight_season_runtime(
    configs_dir: Path,
    season_id: str,
    *,
    project_root: Path = REPO_ROOT,
    registry=participants_registry,
) -> SeasonPreflightReport:
    """纯只读预检：检查 Season 能否合法启动为 running 赛季。

    严禁产生任何文件写入或状态修改副作用。
    """
    from workbench.replay.ladder import _load_registry_file

    season_path = configs_dir / f"{season_id}.json"
    if not season_path.exists():
        return SeasonPreflightReport(
            season_id=season_id,
            season_status="unknown",
            can_start=False,
            state="blocked",
            issues=[PreflightIssue(code="season_not_found", message=f"赛季配置不存在: {season_id}")],
            manifest=None,
        )

    try:
        season = _load_registry_file(season_path)
    except Exception as exc:
        return SeasonPreflightReport(
            season_id=season_id,
            season_status="invalid",
            can_start=False,
            state="blocked",
            issues=[PreflightIssue(code="invalid_season_config", message=f"赛季配置解析失败: {exc}")],
            manifest=None,
        )

    status = season.get("status", "draft")
    issues: list[PreflightIssue] = []

    # 1. 状态合法性检查：只有 draft 允许全新启动
    if status == "running":
        issues.append(PreflightIssue(code="already_running", message="赛季已在运行中"))
    elif status in ("completed", "archived"):
        issues.append(PreflightIssue(code="season_completed_or_archived", message=f"赛季状态为 {status}，无法重新启动"))

    # 2. 构造 Manifest 并收集凭据与文件检查 issue
    manifest, manifest_issues = build_runtime_manifest(
        season,
        materialization="frozen",
        generated_at=None,
        project_root=project_root,
        registry=registry,
    )
    issues.extend(manifest_issues)

    # 3. 参赛人数与席位完整性检查（至少 4 名合法成员，且所有成员均需解析成功）
    expected_accounts_count = sum(len(m.get("accounts") or []) for m in (season.get("models") or []) if isinstance(m, dict))
    if len(manifest.participants) < 4:
        issues.append(PreflightIssue(
            code="insufficient_participants",
            message=f"赛季成员数量不足（当前有效 {len(manifest.participants)} 人，至少需要 4 人）",
        ))
    if len(manifest.participants) != expected_accounts_count:
        issues.append(PreflightIssue(
            code="participant_count_mismatch",
            message=f"有效成员数 ({len(manifest.participants)}) 与配置成员数 ({expected_accounts_count}) 不一致",
        ))

    can_start = len(issues) == 0 and status == "draft"
    state = "ready" if can_start else "blocked"

    return SeasonPreflightReport(
        season_id=season_id,
        season_status=status,
        can_start=can_start,
        state=state,
        issues=issues,
        manifest=manifest,
    )


# ---------------------------------------------------------------------------
# 5. Atomic Season Runtime Start (Dual Domain Locks + Single JSON Commit)
# ---------------------------------------------------------------------------

def start_season_runtime(
    configs_dir: Path,
    season_id: str,
    *,
    project_root: Path = REPO_ROOT,
    registry=participants_registry,
) -> dict[str, Any]:
    """激活赛季：draft → running 并固化 runtime_manifest（服务级双锁原子提交）。

    顺序取得固定锁（participants_data_lock → season _registry_lock），在双锁临界区内：
    1. 重新读取 canonical season；
    2. 校验 status == "draft"；
    3. 执行 preflight 检查（若存在任何 issue 则直接抛错 fail-closed）；
    4. 构造带 generated_at 的 runtime_manifest (materialization="frozen")；
    5. 一次 _atomic_write_json 写入运行状态与清单；
    6. 返回更新后的完整 Season dict。
    """
    from workbench.participants.paths import now_iso, data_lock as participants_data_lock
    from replay.ladder import SeasonNotFoundError, SeasonRegistryError, _load_registry_file
    from workbench.replay.season_registry import (
        _registry_lock,
        _season_path,
        _atomic_write_json,
        validate_registry_payload,
    )

    with participants_data_lock(), _registry_lock(configs_dir):
        path = _season_path(configs_dir, season_id)
        if not path.exists():
            raise SeasonNotFoundError(f"赛季不存在: {season_id}")

        season = _load_registry_file(path)
        status = season.get("status", "draft")

        if status == "running":
            raise SeasonRegistryError(f"赛季 {season_id} 已在运行中 (already_started)")
        if status in ("completed", "archived"):
            raise SeasonRegistryError(f"赛季 {season_id} 状态为 {status}，无法启动 (season_not_startable)")
        if status != "draft":
            raise SeasonRegistryError(f"赛季 {season_id} 状态异常 ({status})，无法启动")

        # 构造 Manifest 并执行全量 Preflight 检查
        current_time = now_iso()
        manifest, issues = build_runtime_manifest(
            season,
            materialization="frozen",
            generated_at=current_time,
            project_root=project_root,
            registry=registry,
        )

        expected_accounts_count = sum(len(m.get("accounts") or []) for m in (season.get("models") or []) if isinstance(m, dict))
        if len(manifest.participants) < 4:
            issues.append(PreflightIssue(
                code="insufficient_participants",
                message=f"赛季成员数量不足（当前有效 {len(manifest.participants)} 人，至少需要 4 人）",
            ))
        if len(manifest.participants) != expected_accounts_count:
            issues.append(PreflightIssue(
                code="participant_count_mismatch",
                message=f"有效成员数 ({len(manifest.participants)}) 与配置成员数 ({expected_accounts_count}) 不一致",
            ))

        if issues:
            detail_msgs = "; ".join(f"[{i.code}] {i.message}" for i in issues)
            raise SeasonRegistryError(f"赛季启动预检未通过: {detail_msgs}")

        # 原子写入 running 与 runtime_manifest
        season["status"] = "running"
        season["runtime_manifest"] = manifest.model_dump()
        season["updated_at"] = current_time

        validated = validate_registry_payload(season)
        _atomic_write_json(path, validated)
        return validated


# ---------------------------------------------------------------------------
# 6. Read-Only Manifest Retrieval (With Legacy Projection Support)
# ---------------------------------------------------------------------------

def get_season_runtime_manifest(
    configs_dir: Path,
    season_id: str,
    *,
    project_root: Path = REPO_ROOT,
    registry=participants_registry,
) -> SeasonRuntimeManifest:
    """读取指定赛季的运行时清单。

    - 若已固化 runtime_manifest，直接返回；
    - 若为 legacy running/completed/archived 且无 runtime_manifest，则生成只读 projected_legacy Manifest；
    - 若为 draft 且无 runtime_manifest，则生成预览版 Manifest (materialization="frozen", generated_at=None)。

    注意：对于 legacy projection，如果存在无法完整解析的成员凭据，禁止返回部分 Manifest，必须 fail-closed 抛错！
    """
    from replay.ladder import SeasonNotFoundError, SeasonRegistryError, _load_registry_file

    season_path = configs_dir / f"{season_id}.json"
    if not season_path.exists():
        raise SeasonNotFoundError(f"赛季配置不存在: {season_id}")

    season = _load_registry_file(season_path)
    if season.get("runtime_manifest"):
        return SeasonRuntimeManifest.model_validate(season["runtime_manifest"])

    status = season.get("status", "draft")
    mat_type: Literal["frozen", "projected_legacy"] = "projected_legacy" if status != "draft" else "frozen"

    manifest, issues = build_runtime_manifest(
        season,
        materialization=mat_type,
        generated_at=None,
        project_root=project_root,
        registry=registry,
    )

    expected_accounts_count = sum(len(m.get("accounts") or []) for m in (season.get("models") or []) if isinstance(m, dict))
    if issues or len(manifest.participants) != expected_accounts_count:
        detail_msgs = "; ".join(f"[{i.code}] {i.message}" for i in issues) if issues else "成员数量与配置不匹配"
        raise SeasonRegistryError(f"无法生成历史运行时清单: {detail_msgs}")

    return manifest


# ---------------------------------------------------------------------------
# 7. Persisted Manifest Self-Integrity Validator
# ---------------------------------------------------------------------------

def validate_persisted_manifest_against_season(
    season: dict[str, Any],
    manifest_raw: Any,
) -> SeasonRuntimeManifest:
    """严格校验 canonical Season JSON 中持久化的 runtime_manifest 的自洽性与权威绑定。

    防范任何静默篡改（包括仅篡改 runtime_spec、伪造 manifest_id、或改换 provenance）。
    """
    from replay.ladder import SeasonRegistryError

    if not isinstance(manifest_raw, dict):
        raise SeasonRegistryError("runtime_manifest 必须是对象")

    try:
        manifest = SeasonRuntimeManifest.model_validate(manifest_raw)
    except Exception as exc:
        raise SeasonRegistryError(f"runtime_manifest 结构校验失败: {exc}") from exc

    season_id = str(season.get("season_id") or "")
    status = str(season.get("status") or "")

    # 1. 生命周期门禁：draft 不允许持久化 frozen manifest
    if status == "draft":
        raise SeasonRegistryError("draft 赛季不允许包含持久化的 runtime_manifest")

    if manifest.materialization != "frozen":
        raise SeasonRegistryError(
            f"持久化的 runtime_manifest 其 materialization 必须为 'frozen' (当前为 '{manifest.materialization}')"
        )

    if not manifest.generated_at:
        raise SeasonRegistryError("持久化 frozen runtime_manifest 必须包含 generated_at 时间戳")

    if manifest.season_id != season_id:
        raise SeasonRegistryError(
            f"runtime_manifest.season_id '{manifest.season_id}' 与注册表 season_id '{season_id}' 不一致"
        )

    # 2. Season Authority Hash 强校验
    expected_authority_hash = compute_season_authority_hash(season)
    if manifest.season_authority_hash != expected_authority_hash:
        raise SeasonRegistryError(
            f"runtime_manifest 的 season_authority_hash '{manifest.season_authority_hash}' "
            f"与当前配置权威哈希 '{expected_authority_hash}' 不一致 (authority_hash_drift)"
        )

    # 3. Manifest ID 重新计算并比对（保证 participants / runtime_spec 未被单边篡改）
    expected_manifest_id = compute_manifest_id(
        manifest.season_id,
        manifest.season_authority_hash,
        manifest.participants,
    )
    if manifest.manifest_id != expected_manifest_id:
        raise SeasonRegistryError(
            f"runtime_manifest 的 manifest_id '{manifest.manifest_id}' "
            f"与 participants 内容推导值 '{expected_manifest_id}' 不一致 (manifest_id_drift)"
        )

    # 4. scoring_summary 与 Season scoring 比对
    if manifest.scoring_summary != (season.get("scoring") or {}):
        raise SeasonRegistryError("runtime_manifest 的 scoring_summary 与 season scoring 不一致 (scoring_drift)")

    # 5. Manifest Participants 与 Season Enrollment 精确交叉校验
    season_account_info: dict[str, dict[str, Any]] = {}
    for m in season.get("models") or []:
        if not isinstance(m, dict):
            continue
        model_id = str(m.get("model_id") or "")
        group_ident = m.get("model_identity_id")
        group_prov = m.get("execution_provenance")
        for acc in m.get("accounts") or []:
            if not isinstance(acc, dict) or not acc.get("account_id"):
                continue
            aid = acc["account_id"]
            season_account_info[aid] = {
                "model_id": model_id,
                "group_ident": group_ident,
                "group_prov": group_prov,
                "display_name": acc.get("display_name"),
            }

    manifest_accounts = {p.account_id: p for p in manifest.participants}
    if set(manifest_accounts.keys()) != set(season_account_info.keys()):
        raise SeasonRegistryError("runtime_manifest 的参与者集合与 Season enrollment 不一致")

    for p in manifest.participants:
        info = season_account_info[p.account_id]
        if info["display_name"] is not None and p.display_name != info["display_name"]:
            raise SeasonRegistryError(
                f"participant {p.account_id} display_name '{p.display_name}' 与 season enrollment '{info['display_name']}' 不一致"
            )

        # 检查 execution_provenance 与 group provenance 是否一致
        if info["group_prov"]:
            expected_prov = FrozenExecutionProvenance.model_validate(info["group_prov"])
            if p.execution_provenance != expected_prov:
                raise SeasonRegistryError(
                    f"participant {p.account_id} 的 execution_provenance 与 season group provenance 不一致"
                )
        else:
            if info["model_id"] == "human":
                if p.execution_provenance.kind != "human":
                    raise SeasonRegistryError(f"真人 participant {p.account_id} 的 execution_provenance.kind 必须为 human")
            else:
                raise SeasonRegistryError(f"AI participant {p.account_id} 所属 group 缺少 execution_provenance")

        # 检查 model_identity_id
        if p.execution_provenance.kind == "human":
            if p.model_identity_id is not None:
                raise SeasonRegistryError(f"真人 participant {p.account_id} model_identity_id 必须为 None")
        else:
            expected_ident = p.execution_provenance.model_identity_id or info["group_ident"]
            if p.model_identity_id != expected_ident:
                raise SeasonRegistryError(
                    f"participant {p.account_id} model_identity_id '{p.model_identity_id}' 与期望 '{expected_ident}' 不一致"
                )

        # 检查 runtime_spec 与 provenance 交叉一致性
        if p.execution_provenance.kind == "human":
            if p.runtime_spec.kind != "human" or p.runtime_spec.controller_type != "human_ui":
                raise SeasonRegistryError(f"真人 participant {p.account_id} runtime_spec 类型异常")
        elif p.execution_provenance.kind == "local_artifact":
            if p.runtime_spec.kind != "local_artifact" or p.runtime_spec.controller_type != "local_model":
                raise SeasonRegistryError(f"本地模型 participant {p.account_id} runtime_spec 类型异常")
            if p.runtime_spec.model_identity_id != p.execution_provenance.model_identity_id:
                raise SeasonRegistryError(f"participant {p.account_id} runtime_spec model_identity_id 与凭据不一致")
            if p.runtime_spec.model_artifact_id != p.execution_provenance.model_artifact_id:
                raise SeasonRegistryError(f"participant {p.account_id} runtime_spec model_artifact_id 与凭据不一致")
        elif p.execution_provenance.kind == "external_revision":
            if p.runtime_spec.kind != "external_revision" or p.runtime_spec.controller_type != "external_agent":
                raise SeasonRegistryError(f"外部模型 participant {p.account_id} runtime_spec 类型异常")
            if p.runtime_spec.model_identity_id != p.execution_provenance.model_identity_id:
                raise SeasonRegistryError(f"participant {p.account_id} runtime_spec model_identity_id 与凭据不一致")
            if p.runtime_spec.external_revision_id != p.execution_provenance.external_revision_id:
                raise SeasonRegistryError(f"participant {p.account_id} runtime_spec external_revision_id 与凭据不一致")

    return manifest
