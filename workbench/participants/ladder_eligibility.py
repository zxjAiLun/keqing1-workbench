# -*- coding: utf-8 -*-
"""正式天梯资格 gate（R10 Production UX Repair 1 / P1-2 / R13-B Frozen Provenance Admission）。

任何把 ``rating_eligible`` 置 true 的入口（Tenhou intake confirm / manual Match
create / Match revise）都必须先通过本校验，防止「成绩记错模型」：

- 赛季存在 + running + 启用 participants 正式投影（enabled + exclusive）；
- 四个账号均为正式赛季成员（season.models[*].accounts）；
- 人类账号必须属于 ``model_id=human``（C23 契约）；
- 带冻结模型（seat.model_artifact_id / seat.external_revision_id）的座位：
  artifact / revision 必须与该账号所属赛季模型的冻结配置精确一致（如 70k@02 + V3 artifact → REJECT，Mortal 4.1b + 4.2 revision → REJECT）；
- 校验通过后返回每个座位的规范执行凭据（CanonicalExecutionProvenance），供写入层冻结为不可变历史事实。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .execution_provenance import (
    CanonicalExecutionProvenance,
    resolve_execution_provenance,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LadderAdmissionResult:
    season_id: str
    provenance_by_seat: dict[int, CanonicalExecutionProvenance]


def _season_account_model(season: dict[str, Any], account_id: str) -> dict | None:
    """账号所属赛季模型（model_id + checkpoint + execution_provenance + model_artifact_id + external_revision_id）。"""
    for model in season.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        for account in model.get("accounts", []) or []:
            if isinstance(account, dict) and str(account.get("account_id") or "") == account_id:
                return {
                    "model_id": str(model.get("model_id") or ""),
                    "checkpoint": model.get("checkpoint"),
                    "model_artifact_id": model.get("model_artifact_id"),
                    "external_revision_id": model.get("external_revision_id"),
                    "execution_provenance": model.get("execution_provenance"),
                }
    return None


def _resolve_checkpoint(checkpoint: Any, project_root: Path) -> Path | None:
    if not checkpoint:
        return None
    path = Path(str(checkpoint))
    from workbench.runtime.resolver import resolve_model_checkpoint

    try:
        return resolve_model_checkpoint(path, project_root)
    except FileNotFoundError:
        if not path.is_absolute():
            path = project_root / path
        return path.resolve()


def _artifact_record(registry, artifact_id: str) -> Any | None:
    """按 artifact_id 在 participants registry 查找产物对象。"""
    for identity in registry.list_models():
        for artifact in identity.artifacts:
            if artifact.model_artifact_id == artifact_id:
                return artifact
    return None


def _artifact_path(registry, artifact_id: str, project_root: Path) -> Path | None:
    """按 artifact_id 在 participants registry 查找产物路径。"""
    artifact = _artifact_record(registry, artifact_id)
    if artifact is not None:
        return _resolve_checkpoint(artifact.artifact_path, project_root)
    return None


def ensure_ladder_eligibility(
    season_id: str | None,
    seats: list[Any],
    *,
    registry,
    project_root: Path = PROJECT_ROOT,
) -> LadderAdmissionResult:
    """统一硬不变量（UX Repair 2/3 / P1-2 / R13-B）：

    ``rating_eligible == true`` ⇒ ``season_id`` 必须是非空有效字符串
    ⇒ 必须通过 ``validate_ladder_eligibility``。

    返回 **LadderAdmissionResult**（含 normalized season_id 与逐座 CanonicalExecutionProvenance），
    调用方必须用返回值回写 Match 的 season_id 与 execution_provenance。
    """
    normalized = str(season_id or "").strip()
    if not normalized:
        raise ValueError("rating_eligible=true 必须指定正式赛季（season_id 不能为空）")
    provenance_map = validate_ladder_eligibility(
        normalized, seats, registry=registry, project_root=project_root
    )
    return LadderAdmissionResult(season_id=normalized, provenance_by_seat=provenance_map)


def validate_ladder_eligibility(
    season_id: str,
    seats: list[Any],
    *,
    registry,
    project_root: Path = PROJECT_ROOT,
) -> dict[int, CanonicalExecutionProvenance]:
    """正式天梯资格校验；不通过抛 ValueError（带中文原因）。返回逐座规范执行凭据。"""
    from replay import ladder as ladder_data

    configs_dir = ladder_data.resolve_config_dir(project_root)
    try:
        season = ladder_data.get_season_config(configs_dir, season_id)
    except ladder_data.SeasonNotFoundError as exc:
        raise ValueError(f"正式赛季不存在: {season_id}") from exc

    if str(season.get("status") or "") != "running":
        raise ValueError(f"赛季 {season_id} 不是 running 状态，不能计入正式天梯")
    ingest = season.get("ingest") if isinstance(season.get("ingest"), dict) else {}
    participants_cfg = ingest.get("participants")
    if not (
        isinstance(participants_cfg, dict)
        and participants_cfg.get("enabled")
        and participants_cfg.get("exclusive")
    ):
        raise ValueError(f"赛季 {season_id} 未启用 participants 正式投影（需 ingest.participants.enabled+exclusive）")

    provenance_by_seat: dict[int, CanonicalExecutionProvenance] = {}

    for seat in seats:
        seat_no = getattr(seat, "seat", None)
        if seat_no is None:
            raise ValueError("座位缺少 seat 编号")
        account_id = str(getattr(seat, "account_id", None) or "")
        if not account_id:
            raise ValueError(f"座位 {seat_no} 未指派账号")
        account = registry.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        season_model = _season_account_model(season, account_id)
        if season_model is None:
            raise ValueError(f"账号 {account_id} 未在正式赛季 {season_id} 注册")

        # 提取赛季级别配置
        season_model_id = season_model.get("model_id")
        season_ckpt = season_model.get("checkpoint")
        season_art_id = season_model.get("model_artifact_id")
        season_rev_id = season_model.get("external_revision_id")
        season_exec_prov = season_model.get("execution_provenance")

        if isinstance(season_exec_prov, dict):
            if not season_art_id:
                season_art_id = season_exec_prov.get("model_artifact_id")
            if not season_rev_id:
                season_rev_id = season_exec_prov.get("external_revision_id")
            if not season_model_id and season_exec_prov.get("model_identity_id"):
                season_model_id = season_exec_prov.get("model_identity_id")

        if account.account_type == "human":
            if season_model_id != "human" and (
                not isinstance(season_exec_prov, dict) or season_exec_prov.get("kind") != "human"
            ):
                raise ValueError(
                    f"人类账号 {account_id} 必须属于 model_id=human（当前属于 {season_model_id or '未知'}）"
                )
            if (
                getattr(seat, "model_identity_id", None)
                or getattr(seat, "model_artifact_id", None)
                or getattr(seat, "external_revision_id", None)
            ):
                raise ValueError(f"正式人类座位 {account_id} 不能携带冻结 bot 模型产物")
            controller_type = getattr(seat, "controller_type", None)
            if controller_type and controller_type != "human_ui":
                raise ValueError(f"正式人类座位 {account_id} 的控制器必须为 human_ui")

        # 严格 identity 判定：
        seat_ident = getattr(seat, "model_identity_id", None)
        if seat_ident is not None:
            identity_id = seat_ident
        elif account.model_identity_id is not None:
            identity_id = account.model_identity_id
        else:
            identity_id = season_model_id if account.account_type != "human" else None

        controller_type = getattr(seat, "controller_type", None)
        artifact_id = getattr(seat, "model_artifact_id", None) or season_art_id
        external_revision_id = getattr(seat, "external_revision_id", None) or season_rev_id
        ckpt_for_resolution = season_ckpt if account.account_type == "managed_bot" else None

        resolved_prov = resolve_execution_provenance(
            account_id=account_id,
            controller_type=controller_type,
            identity_id=identity_id,
            artifact_id=artifact_id,
            external_revision_id=external_revision_id,
            checkpoint=ckpt_for_resolution,
            purpose="ladder_eligible",
            project_root=project_root,
            registry=registry,
        )

        # -----------------------------------------------------------------
        # 严格校验：Seat 实际 provenance 是否等于 Season freeze 的 provenance (R13-B Invariant 5)
        # -----------------------------------------------------------------
        if isinstance(season_exec_prov, dict):
            season_kind = season_exec_prov.get("kind")
            if season_kind and resolved_prov.kind != season_kind:
                raise ValueError(
                    f"座位 {seat_no} 执行类型 {resolved_prov.kind} 与赛季冻结类型 {season_kind} 不一致"
                )
            season_ident = season_exec_prov.get("model_identity_id")
            if season_ident and resolved_prov.model_identity_id != season_ident:
                raise ValueError(
                    f"座位 {seat_no} 模型身份 {resolved_prov.model_identity_id} 与赛季冻结身份 {season_ident} 不一致"
                )
            season_frozen_art = season_exec_prov.get("model_artifact_id")
            if season_frozen_art and resolved_prov.model_artifact_id != season_frozen_art:
                raise ValueError(
                    f"座位 {seat_no} 模型产物 {resolved_prov.model_artifact_id} 与赛季冻结产物 {season_frozen_art} 不一致"
                )
            season_frozen_rev = season_exec_prov.get("external_revision_id")
            if season_frozen_rev and resolved_prov.external_revision_id != season_frozen_rev:
                raise ValueError(
                    f"座位 {seat_no} 外部版本 {resolved_prov.external_revision_id} 与赛季冻结版本 {season_frozen_rev} 不一致"
                )

        if season_rev_id and resolved_prov.external_revision_id != season_rev_id:
            raise ValueError(
                f"座位 {seat_no} 外部版本 {resolved_prov.external_revision_id} 与赛季指定版本 {season_rev_id} 不一致"
            )
        if season_art_id and resolved_prov.model_artifact_id != season_art_id:
            raise ValueError(
                f"座位 {seat_no} 模型产物 {resolved_prov.model_artifact_id} 与赛季指定产物 {season_art_id} 不一致"
            )

        provenance_by_seat[seat_no] = resolved_prov

    return provenance_by_seat


__all__ = ["LadderAdmissionResult", "ensure_ladder_eligibility", "validate_ladder_eligibility"]
