# -*- coding: utf-8 -*-
"""正式天梯资格 gate（R10 Production UX Repair 1 / P1-2）。

任何把 ``rating_eligible`` 置 true 的入口（Tenhou intake confirm / manual Match
create / Match revise）都必须先通过本校验，防止「成绩记错模型」：

- 赛季存在 + running + 启用 participants 正式投影（enabled + exclusive）；
- 四个账号均为正式赛季成员（season.models[*].accounts）；
- 人类账号必须属于 ``model_id=human``（C23 契约）；
- 带冻结模型（seat.model_artifact_id）的座位：artifact checkpoint 必须与该
  账号所属赛季模型的 checkpoint 精确一致（如 70k@02 + V3 artifact → REJECT）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _season_account_model(season: dict[str, Any], account_id: str) -> dict | None:
    """账号所属赛季模型（model_id + checkpoint）。"""
    for model in season.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        for account in model.get("accounts", []) or []:
            if isinstance(account, dict) and str(account.get("account_id") or "") == account_id:
                return {
                    "model_id": str(model.get("model_id") or ""),
                    "checkpoint": model.get("checkpoint"),
                }
    return None


def _resolve_checkpoint(checkpoint: Any, project_root: Path) -> Path | None:
    if not checkpoint:
        return None
    path = Path(str(checkpoint))
    # Eligibility must compare the same physical checkpoint used by the
    # launcher/intake path.  In particular, old season registries may retain
    # ``artifacts/...`` or Windows paths while Participants stores the portable
    # path under the shared ``keqing-data/mortal/authoritative`` tree.
    from workbench.runtime.resolver import resolve_model_checkpoint

    try:
        return resolve_model_checkpoint(path, project_root)
    except FileNotFoundError:
        # Keep the old virtual-path behavior for test fixtures and for a
        # missing checkpoint: the caller still reports the precise eligibility
        # mismatch instead of turning a registry comparison into an import
        # crash.  Existing files always take the canonical resolver path above.
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
) -> str:
    """统一硬不变量（UX Repair 2/3 / P1-2）：

    ``rating_eligible == true`` ⇒ ``season_id`` 必须是非空有效字符串
    ⇒ 必须通过 ``validate_ladder_eligibility``。

    返回 **normalized season_id**（trim 后），caller **必须用返回值回写**
    Match——否则 `" official-ladder-v1 "` 验证通过却落账成未规范化的值，
    与 dirty marker / adapter 过滤不匹配，永远无法进入正式投影。
    """
    normalized = str(season_id or "").strip()
    if not normalized:
        raise ValueError("rating_eligible=true 必须指定正式赛季（season_id 不能为空）")
    validate_ladder_eligibility(normalized, seats, registry=registry, project_root=project_root)
    return normalized


def validate_ladder_eligibility(
    season_id: str,
    seats: list[Any],
    *,
    registry,
    project_root: Path = PROJECT_ROOT,
) -> None:
    """正式天梯资格校验；不通过抛 ValueError（带中文原因）。"""
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

    for seat in seats:
        account_id = str(seat.account_id or "")
        if not account_id:
            raise ValueError("正式计分比赛不能有未指派账号的座位")
        account = registry.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        season_model = _season_account_model(season, account_id)
        if season_model is None:
            raise ValueError(f"账号 {account_id} 未在正式赛季 {season_id} 注册")
        if account.account_type == "human":
            if season_model["model_id"] != "human":
                raise ValueError(
                    f"人类账号 {account_id} 必须属于 model_id=human（当前属于 {season_model['model_id'] or '未知'}）"
                )
            # P1（UX Repair 2）：正式人类座位不能携带冻结 bot 模型产物——
            # 否则 '实际跑 70k artifact + 赛后记 nick@01' 会被 human checkpoint=none 跳过比较。
            if getattr(seat, "model_identity_id", None) or getattr(seat, "model_artifact_id", None):
                raise ValueError(
                    f"正式人类座位 {account_id} 不能携带冻结 bot 模型产物"
                )
            controller_type = getattr(seat, "controller_type", None)
            if controller_type and controller_type != "human_ui":
                raise ValueError(f"正式人类座位 {account_id} 的控制器必须为 human_ui")
        else:
            # 非人类座位：必须通过单一 artifact policy resolver 唯一解析并满足 ladder_eligible
            from .artifact_policy import resolve_artifact_binding

            identity_id = getattr(seat, "model_identity_id", None) or account.model_identity_id or season_model.get("model_id")
            artifact_id = getattr(seat, "model_artifact_id", None)
            season_ckpt = season_model.get("checkpoint")

            # 校验 account 是否存在且未停用
            if identity_id and not registry.get_model_identity(identity_id):
                # 如果 identity 尚未存在于 registry，但在 season 中配置了 checkpoint/model_id，尝试绑定
                identity_id = season_model.get("model_id")

            resolve_artifact_binding(
                account_id=account_id,
                identity_id=identity_id,
                artifact_id=artifact_id,
                checkpoint=season_ckpt,
                purpose="ladder_eligible",
                project_root=project_root,
                registry=registry,
            )


__all__ = ["ensure_ladder_eligibility", "validate_ladder_eligibility"]
