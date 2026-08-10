# -*- coding: utf-8 -*-
"""participants API：账号/模型/对局账本 HTTP 端点。

前缀 ``/api/participants``。错误映射：
- ``ledger.ValidationError`` → 422（携带 issues 与 score_mismatch）
- ``KeyError`` → 404
- ``ValueError``（ID 冲突等）→ 409
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import ledger, registry, stats, aliases, intake, projection
from .schemas import (
    Account,
    AccountCreate,
    AccountUpdate,
    ExternalAliasCreate,
    IntakeConfirmRequest,
    IntakePreviewRequest,
    Match,
    MatchCreate,
    MatchListResponse,
    MatchResponse,
    MatchRevise,
    MatchVoid,
    ModelArtifactCreate,
    ModelIdentity,
    ModelIdentityCreate,
    ModelIdentityUpdate,
)
from .ledger import ValidationError

router = APIRouter(prefix="/api/participants")


def _error(status: int, message: str):
    return HTTPException(status_code=status, detail={"error": message})


def _intake_error(status: int, code: str, message: str, context: dict | None = None):
    """R11-A1：结构化 intake 错误 envelope ``{detail: {code, message, context}}``。

    前端按 ``code`` 精确分类业务错误（只有 ``duplicate_match`` 才展示"重复导入"），
    其余一律原样显示 ``message``，不再靠 HTTP status 或猜测文案。
    """
    return HTTPException(
        status_code=status,
        detail={
            "code": code,
            "message": message,
            "context": context or {},
        },
    )


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------

@router.get("/accounts", response_model=dict)
def api_list_accounts(
    enabled: bool | None = None,
    q: str | None = None,
) -> dict:
    return {"schema": "keqing.participant.accounts.v1", "accounts": [a.model_dump() for a in registry.list_accounts(enabled=enabled, q=q)]}


@router.post("/accounts", response_model=Account)
def api_create_account(payload: AccountCreate) -> Account:
    try:
        return registry.create_account(payload)
    except ValueError as exc:
        raise _error(409, str(exc)) from exc


@router.get("/accounts/{account_id}", response_model=dict)
def api_get_account(account_id: str) -> dict:
    account = registry.get_account(account_id)
    if account is None:
        raise _error(404, f"account not found: {account_id}")
    # R11-D：模型投影按新关系——账号专属身份（legacy）或账号反向引用的 global 身份
    identities = [
        m.model_dump()
        for m in registry.list_models()
        if m.account_id == account_id
        or (account.model_identity_id and m.model_identity_id == account.model_identity_id)
    ]
    return {"account": account.model_dump(), "identities": identities}


@router.patch("/accounts/{account_id}", response_model=Account)
def api_update_account(account_id: str, payload: AccountUpdate) -> Account:
    try:
        return registry.update_account(account_id, payload)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc


@router.delete("/accounts/{account_id}", response_model=dict)
def api_delete_account(account_id: str) -> dict:
    try:
        # 引用检查与删除在同一 data_lock 临界区内（P1-2 防 TOCTOU）；
        # 先做锁内 pending 恢复，再查引用，避免"只有 pending"的写失败后硬删账号。
        def _reference_checker() -> bool:
            ledger.recover_pending_transaction_locked()
            return (
                ledger.match_references_account(account_id)
                or registry.identity_references_account(account_id)
                or aliases.alias_references_account(account_id)
            )

        return registry.delete_account_guarded(account_id, _reference_checker)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc


@router.get("/accounts/{account_id}/stats", response_model=dict)
def api_account_stats(account_id: str) -> dict:
    if registry.get_account(account_id) is None:
        raise _error(404, f"account not found: {account_id}")
    return stats.compute_account_stats(account_id, registry, ledger)


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

@router.get("/models", response_model=dict)
def api_list_models() -> dict:
    return {"schema": "keqing.participant.models.v1", "identities": [m.model_dump() for m in registry.list_models()]}


@router.post("/models", response_model=ModelIdentity)
def api_create_model(payload: ModelIdentityCreate) -> ModelIdentity:
    try:
        return registry.create_model_identity(payload)
    except ValueError as exc:
        raise _error(409, str(exc)) from exc


@router.post("/models/{model_identity_id}/artifacts", response_model=dict)
def api_add_artifact(model_identity_id: str, payload: ModelArtifactCreate) -> dict:
    try:
        artifact = registry.add_model_artifact(model_identity_id, payload)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc
    except ValueError as exc:
        raise _error(409, str(exc)) from exc
    return artifact.model_dump()


@router.post("/models/{model_identity_id}/artifacts/{artifact_id}/current", response_model=dict)
def api_set_current_artifact(model_identity_id: str, artifact_id: str) -> dict:
    """把指定 artifact 设为 current（R10 UX Repair P1-4：current artifact 管理）。"""
    try:
        artifact = registry.set_current_model_artifact(model_identity_id, artifact_id)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc
    return artifact.model_dump()


@router.patch("/models/{model_identity_id}", response_model=ModelIdentity)
def api_update_model(model_identity_id: str, payload: ModelIdentityUpdate) -> ModelIdentity:
    try:
        return registry.update_model_identity(model_identity_id, payload)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc


# ---------------------------------------------------------------------------
# 对局账本
# ---------------------------------------------------------------------------

@router.get("/matches", response_model=MatchListResponse)
def api_list_matches(
    source: str | None = None,
    status: str | None = None,
    account_id: str | None = None,
    from_at: str | None = None,
    to_at: str | None = None,
    provider: str | None = None,
    external_match_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> MatchListResponse:
    if limit is not None and not (1 <= limit <= 500):
        raise _error(422, f"limit 必须在 [1, 500]，得到 {limit}")
    if offset < 0:
        raise _error(422, f"offset 必须非负，得到 {offset}")
    return ledger.list_matches(
        source=source,
        status=status,
        account_id=account_id,
        from_at=from_at,
        to_at=to_at,
        provider=provider,
        external_match_id=external_match_id,
        limit=limit,
        offset=offset,
    )


@router.post("/matches", response_model=MatchResponse)
def api_create_match(payload: MatchCreate) -> MatchResponse:
    try:
        match = ledger.create_match(payload, registry)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "校验失败", "issues": [i.model_dump() for i in exc.issues], "score_mismatch": exc.score_mismatch},
        ) from exc
    except ValueError as exc:
        raise _error(409, str(exc)) from exc
    projection.request_projection(match.season_id)
    return MatchResponse(match=match, revisions=ledger.list_revision_summaries(match.match_id))


@router.get("/matches/{match_id}", response_model=MatchResponse)
def api_get_match(match_id: str) -> MatchResponse:
    match = ledger.get_match(match_id)
    if match is None:
        raise _error(404, f"match not found: {match_id}")
    return MatchResponse(match=match, revisions=ledger.list_revision_summaries(match_id))


@router.get("/matches/{match_id}/revisions", response_model=dict)
def api_get_revisions(match_id: str) -> dict:
    if ledger.get_match(match_id) is None:
        raise _error(404, f"match not found: {match_id}")
    return {"match_id": match_id, "revisions": ledger.list_revisions(match_id)}


@router.post("/matches/{match_id}/revise", response_model=MatchResponse)
def api_revise_match(match_id: str, payload: MatchRevise) -> MatchResponse:
    try:
        match = ledger.revise_match(match_id, payload, registry)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "校验失败", "issues": [i.model_dump() for i in exc.issues], "score_mismatch": exc.score_mismatch},
        ) from exc
    except ValueError as exc:
        raise _error(409, str(exc)) from exc
    projection.request_projection(match.season_id)
    return MatchResponse(match=match, revisions=ledger.list_revision_summaries(match_id))


@router.post("/matches/{match_id}/void", response_model=MatchResponse)
def api_void_match(match_id: str, payload: MatchVoid) -> MatchResponse:
    try:
        match = ledger.void_match(match_id, payload)
    except KeyError as exc:
        raise _error(404, str(exc)) from exc
    except ValueError as exc:
        raise _error(409, str(exc)) from exc
    projection.request_projection(match.season_id)
    return MatchResponse(match=match, revisions=ledger.list_revision_summaries(match_id))


# ---------------------------------------------------------------------------
# 外部别名（身份解析）
# ---------------------------------------------------------------------------

@router.get("/aliases", response_model=dict)
def api_list_aliases(
    provider: str | None = None,
    account_id: str | None = None,
    scope: str | None = None,
) -> dict:
    return {
        "schema": "keqing.participant.aliases.v1",
        "aliases": [a.model_dump() for a in aliases.list_aliases(provider=provider, account_id=account_id, scope=scope)],
    }


@router.post("/aliases", response_model=dict)
def api_create_alias(payload: ExternalAliasCreate) -> dict:
    if registry.get_account(payload.account_id) is None:
        raise _error(422, f"账号不存在: {payload.account_id}")
    alias = aliases.register_alias(payload)
    return alias.model_dump()


# ---------------------------------------------------------------------------
# 天凤统一摄入（R10-D）
# ---------------------------------------------------------------------------

@router.post("/intake/preview", response_model=dict)
def api_intake_preview(payload: IntakePreviewRequest) -> dict:
    try:
        return intake.build_preview(payload.url, session_id=payload.session_id)
    except ValueError as exc:
        raise _error(422, str(exc)) from exc
    except Exception as exc:  # 网络/解析错误统一 502
        raise HTTPException(status_code=502, detail={"error": f"牌谱获取失败: {exc}"}) from exc


@router.post("/intake/confirm", response_model=MatchResponse)
def api_intake_confirm(payload: IntakeConfirmRequest) -> MatchResponse:
    try:
        result = intake.resolve_and_create_match(
            log_id=payload.log_id,
            resolutions=[r.model_dump() for r in payload.resolutions],
            session_id=payload.session_id,
            note=payload.note,
            season_id=payload.season_id,
            rating_eligible=payload.rating_eligible,
        )
    except ValidationError as exc:
        raise _intake_error(
            422,
            "validation_failed",
            "校验失败",
            {"issues": [i.model_dump() for i in exc.issues], "score_mismatch": exc.score_mismatch},
        ) from exc
    except intake.DuplicateMatchError as exc:
        raise _intake_error(
            409,
            "duplicate_match",
            "该天凤牌谱已经导入",
            {"existing_match_id": exc.existing_match_id},
        ) from exc
    except ValueError as exc:
        raise _intake_error(409, "intake_conflict", str(exc)) from exc
    except Exception as exc:
        raise _intake_error(502, "intake_ledger_failed", f"落账失败: {exc}") from exc
    match = ledger.get_match(result["match_id"])
    projection.request_projection(match.season_id if match else None)
    return MatchResponse(
        match=match,
        revisions=ledger.list_revision_summaries(result["match_id"]),
    )


@router.get("/matches/{match_id}/replay", response_model=dict)
def api_match_replay_artifact(match_id: str) -> dict:
    match = ledger.get_match(match_id)
    if match is None:
        raise _error(404, f"match not found: {match_id}")
    if not match.replay_id:
        raise _error(404, f"对局 {match_id} 无 replay artifact")
    artifact = intake.read_replay_artifact(match.replay_id)
    if artifact is None:
        raise _error(404, f"对局 {match_id} 的 replay artifact 不存在")
    return {**artifact, "match_id": match.match_id, "replay_id": match.replay_id}


# ---------------------------------------------------------------------------
# R10-F：Ledger-driven Ladder Projection
# ---------------------------------------------------------------------------

_PARTICIPANTS_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@router.get("/ladder/{season_id}/status", response_model=dict)
def api_ladder_projection_status(season_id: str) -> dict:
    """天梯投影状态：dirty 标记 + 赛季内 match 的投影状态汇总。"""
    dirty = ledger.ladder_dirty_path(season_id).exists()
    season_matches = [
        m for m in ledger.list_matches(status="active").matches if m.season_id == season_id
    ]
    states: dict[str, int] = {}
    for match in season_matches:
        states[match.ladder_projection_state] = states.get(match.ladder_projection_state, 0) + 1
    return {
        "season_id": season_id,
        "dirty": dirty,
        "states": states,
        "eligible_count": sum(1 for m in season_matches if m.rating_eligible),
    }


@router.post("/ladder/{season_id}/project", response_model=dict)
def api_project_ladder_season(season_id: str) -> dict:
    """从 participants ledger 确定性重建并发布赛季天梯快照（R10-F）。

    按钮/手动重试路径：generation CAS 保护（发布期间新写入 → needs_rebuild），
    失败置 error（dirty 保留）。正常流程由后台 worker 自动消费。
    """
    result = projection.project_season(season_id)
    if result["state"] == "error":
        raise HTTPException(
            status_code=502,
            detail={"error": "天梯投影失败", "reason": result.get("reason")},
        )
    return result
