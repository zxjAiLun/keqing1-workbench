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
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _artifact_path(registry, artifact_id: str) -> Path | None:
    """按 artifact_id 在 participants registry 查找产物路径。"""
    for identity in registry.list_models():
        for artifact in identity.artifacts:
            if artifact.model_artifact_id == artifact_id:
                return _resolve_checkpoint(artifact.artifact_path, PROJECT_ROOT)
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
            if seat.model_identity_id or seat.model_artifact_id:
                raise ValueError(
                    f"正式人类座位 {account_id} 不能携带冻结 bot 模型产物"
                )
            if seat.controller_type and seat.controller_type != "human_ui":
                raise ValueError(f"正式人类座位 {account_id} 的控制器必须为 human_ui")
        artifact_id = seat.model_artifact_id
        if artifact_id:
            # 模型受控座位：冻结 artifact 必须与该账号赛季模型的 checkpoint 精确一致
            # （赛季模型配置了 checkpoint 时才校验；未配置则无法比对，跳过）
            artifact_path = _artifact_path(registry, artifact_id)
            if artifact_path is None:
                raise ValueError(f"模型产物不存在: {artifact_id}")
            season_ckpt = _resolve_checkpoint(season_model["checkpoint"], project_root)
            if season_ckpt is not None and artifact_path != season_ckpt:
                raise ValueError(
                    f"账号 {account_id} 的模型产物与正式赛季 {season_model['model_id'] or '?'} "
                    f"checkpoint 不一致"
                )


__all__ = ["ensure_ladder_eligibility", "validate_ladder_eligibility"]
