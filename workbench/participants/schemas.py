# -*- coding: utf-8 -*-
"""Pydantic 模型 — participants（账号/模型/对局账本）API 请求与响应类型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import TZ_OFFSET

AccountType = Literal["human", "managed_bot", "external_bot"]
ControllerType = Literal["human_ui", "local_model", "external_agent", "manual_only"]
SourceType = Literal["native", "imported", "manual"]
DataCompleteness = Literal["result_only", "hand_summary", "full_replay"]
GameLength = Literal["tonpu", "hanchan"]
SeatNo = Literal[0, 1, 2, 3]
ExternalAliasScope = Literal["global", "session", "match"]
LadderProjectionState = Literal["not_applicable", "pending", "ready", "error"]

ACCOUNTS_SCHEMA = "keqing.participant.accounts.v1"
MODELS_SCHEMA = "keqing.participant.models.v1"
MATCH_SCHEMA = "keqing.participant.match.v1"
MATCH_REVISION_SCHEMA = "keqing.participant.match_revision.v1"
MIGRATION_STATE_SCHEMA = "keqing.participant.migration_state.v1"
ALIASES_SCHEMA = "keqing.participant.aliases.v1"
REPLAY_ARTIFACT_SCHEMA = "keqing.participant.replay_artifact.v1"


def normalize_occurred_at(v: str) -> str:
    """解析 ISO-8601 并统一到 +08:00 输出；naive 视为 +08:00。非法格式抛 ValueError。"""
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_OFFSET)
    return dt.astimezone(TZ_OFFSET).isoformat(timespec="seconds")


class Account(BaseModel):
    account_id: str
    display_name: str
    account_type: AccountType
    enabled: bool = True
    default_controller: ControllerType
    avatar: str | None = None
    note: str | None = None
    migrated_from_replay: bool = False
    created_at: str
    updated_at: str


class AccountCreate(BaseModel):
    account_id: str | None = None  # 缺省自动生成 slug
    display_name: str = Field(min_length=1)
    account_type: AccountType
    default_controller: ControllerType | None = None  # 缺省按 account_type 推导
    avatar: str | None = None
    note: str | None = None
    migrated_from_replay: bool = False


class AccountUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    default_controller: ControllerType | None = None
    avatar: str | None = None
    note: str | None = None


class ModelArtifact(BaseModel):
    model_artifact_id: str
    label: str
    model_identity_id: str
    artifact_path: str | None = None
    hash: str | None = None
    is_current: bool = True
    created_at: str
    retired_at: str | None = None


class ModelIdentity(BaseModel):
    model_identity_id: str
    label: str
    kind: Literal["local_model", "external_agent", "none"]
    account_id: str | None = None
    is_current: bool = True
    created_at: str
    retired_at: str | None = None
    note: str | None = None
    artifacts: list[ModelArtifact] = Field(default_factory=list)


class ModelIdentityCreate(BaseModel):
    model_identity_id: str | None = None
    label: str = Field(min_length=1)
    kind: Literal["local_model", "external_agent", "none"]
    account_id: str | None = None
    artifact_path: str | None = None
    note: str | None = None


class ModelIdentityUpdate(BaseModel):
    label: str | None = None
    kind: Literal["local_model", "external_agent", "none"] | None = None
    account_id: str | None = None
    is_current: bool | None = None
    note: str | None = None


class ModelArtifactCreate(BaseModel):
    model_artifact_id: str | None = None
    label: str = Field(min_length=1)
    artifact_path: str | None = None


# ---------------------------------------------------------------------------
# 外部账号别名（身份解析）
# ---------------------------------------------------------------------------

class ExternalAlias(BaseModel):
    alias_id: str
    provider: str = "tenhou"
    external_id: str
    display_name: str | None = None
    # Play-with-you simplification：session alias 可只携带模型事实，不绑定账号
    account_id: str | None = None
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    scope: ExternalAliasScope = "global"
    session_id: str | None = None
    external_match_id: str | None = None  # scope=match 必填；scope=session/global 应为空
    confidence: Literal["confirmed", "unresolved"] = "confirmed"
    created_at: str
    updated_at: str


class ExternalAliasCreate(BaseModel):
    provider: str = "tenhou"
    external_id: str = Field(min_length=1)
    display_name: str | None = None
    # 允许账号无绑定：session 阶段只记录 NoName-N → 模型身份/产物
    account_id: str | None = None
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    scope: ExternalAliasScope = "global"
    session_id: str | None = None
    external_match_id: str | None = None
    confidence: Literal["confirmed", "unresolved"] = "confirmed"

    @model_validator(mode="after")
    def _scope_consistency(self) -> "ExternalAliasCreate":
        if self.scope == "global":
            if self.session_id is not None or self.external_match_id is not None:
                raise ValueError("scope=global 不允许携带 session_id / external_match_id")
        elif self.scope == "session":
            if not self.session_id:
                raise ValueError("scope=session 必须提供 session_id")
            if self.external_match_id is not None:
                raise ValueError("scope=session 不允许携带 external_match_id")
        elif self.scope == "match":
            if not self.external_match_id:
                raise ValueError("scope=match 必须提供 external_match_id")
            if self.session_id is not None:
                raise ValueError("scope=match 不允许携带 session_id")
        return self


# ---------------------------------------------------------------------------
# 天凤 intake（preview / confirm）
# ---------------------------------------------------------------------------

class IntakePreviewRequest(BaseModel):
    url: str = Field(min_length=1)
    session_id: str | None = None


class SeatResolution(BaseModel):
    seat: SeatNo
    action: Literal["assign", "create"] = "assign"
    account_id: str | None = None
    display_name: str | None = None
    account_type: AccountType | None = None
    default_controller: ControllerType | None = None
    alias_id: str | None = None  # 候选别名 ID：服务端读取 account + model 信息
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    alias_scope: ExternalAliasScope | Literal["none"] = "match"
    confidence: Literal["confirmed", "unresolved"] = "confirmed"


class IntakeConfirmRequest(BaseModel):
    log_id: str = Field(min_length=1)
    resolutions: list[SeatResolution]
    session_id: str | None = None
    note: str | None = None
    # R10 UX Repair P1-6：正式天梯由 intake/confirm/revise 决定，不再是启动模式
    season_id: str | None = None
    rating_eligible: bool | None = None


class MatchSeat(BaseModel):
    seat: SeatNo
    account_id: str
    controller_type: ControllerType | None = None  # 缺省取 account.default_controller
    model_identity_id: str | None = None
    model_artifact_id: str | None = None


class MatchCreate(BaseModel):
    occurred_at: str
    game_length: GameLength
    rule_set: str = "standard-4p"
    starting_points: int = Field(25000, ge=1)
    initial_oya: int = 0
    source: SourceType = "manual"
    source_ref: str | None = None
    note: str | None = None
    data_completeness: DataCompleteness = "result_only"
    replay_id: str | None = None
    provider: str | None = None  # 外部来源，如 "tenhou"
    external_match_id: str | None = None  # 外部比赛唯一键，如 tenhou log_id
    raw_player_names: list[str] | None = None  # 四个原始外部名称
    resolution: dict | None = None  # 逐座身份解析审计
    season_id: str | None = None  # 正式计分赛季（null = 仅账号统计）
    rating_eligible: bool = False  # 是否纳入正式天梯计分
    seats: list[MatchSeat]
    final_scores: list[int]
    force: bool = False
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _norm_occurred_at(cls, v: str) -> str:
        return normalize_occurred_at(v)


class MatchRevise(BaseModel):
    occurred_at: str | None = None
    game_length: GameLength | None = None
    rule_set: str | None = None
    starting_points: int | None = Field(default=None, ge=1)
    initial_oya: int | None = None
    note: str | None = None
    data_completeness: DataCompleteness | None = None
    seats: list[MatchSeat] | None = None
    final_scores: list[int] | None = None
    season_id: str | None = None
    rating_eligible: bool | None = None
    force: bool = False
    reason: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _norm_occurred_at(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return normalize_occurred_at(v)


class MatchVoid(BaseModel):
    reason: str = Field(min_length=1)


class Match(BaseModel):  # matches.jsonl 行（当前态）
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(default=MATCH_SCHEMA, alias="schema")
    match_id: str
    occurred_at: str
    game_length: GameLength
    rule_set: str
    starting_points: int
    initial_oya: int
    source: SourceType
    source_ref: str | None = None
    note: str | None = None
    data_completeness: DataCompleteness
    replay_id: str | None = None
    provider: str | None = None
    external_match_id: str | None = None
    raw_player_names: list[str] | None = None
    resolution: dict | None = None
    season_id: str | None = None
    rating_eligible: bool = False
    ladder_projection_state: LadderProjectionState = "not_applicable"
    seats: list[MatchSeat]
    final_scores: list[int]
    ranks: list[int]
    status: Literal["active", "void"] = "active"
    void_reason: str | None = None
    revision: int
    latest_revision_id: str
    created_at: str
    updated_at: str
    created_by: Literal["manual", "migration", "system"] = "manual"


class ValidationIssue(BaseModel):
    code: str
    message: str


class RevisionSummary(BaseModel):
    revision_id: str
    match_id: str
    revision: int
    action: Literal["create", "revise", "void"]
    created_at: str
    by: str
    force: bool
    reason: str | None = None
    validation: dict


class MatchResponse(BaseModel):
    match: Match
    revisions: list[RevisionSummary]


class MatchListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(default=MATCH_SCHEMA, alias="schema")
    matches: list[Match]
    total: int
