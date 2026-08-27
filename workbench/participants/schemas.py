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
    # R11-D：该 AI Account 由哪个逻辑模型驱动（真人/无模型为 null）。
    # 多个 Account 可引用同一个 ModelIdentity；global identity（account_id=None）
    # 不再视为 wildcard——兼容性由 model_identity_id 反向绑定决定。
    model_identity_id: str | None = None
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
    model_identity_id: str | None = None
    avatar: str | None = None
    note: str | None = None
    migrated_from_replay: bool = False


class AccountUpdate(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    default_controller: ControllerType | None = None
    model_identity_id: str | None = None
    avatar: str | None = None
    note: str | None = None


ArtifactStage = Literal["candidate", "promoted", "deprecated", "retired"]
ArtifactCapability = Literal["ladder_eligible", "playwithyou_allowed", "review_allowed"]

_DEFAULT_CAPABILITIES_BY_STAGE: dict[ArtifactStage, list[ArtifactCapability]] = {
    "candidate": ["playwithyou_allowed", "review_allowed"],
    "promoted": ["ladder_eligible", "playwithyou_allowed", "review_allowed"],
    "deprecated": ["playwithyou_allowed", "review_allowed"],
    "retired": ["review_allowed"],
}


class ModelArtifact(BaseModel):
    model_artifact_id: str
    label: str
    model_identity_id: str
    artifact_path: str | None = None
    hash: str | None = None
    stage: ArtifactStage = "promoted"
    capabilities: list[ArtifactCapability] | None = None
    is_current: bool = True
    created_at: str
    retired_at: str | None = None

    @model_validator(mode="after")
    def populate_stage_and_capabilities(self) -> ModelArtifact:
        if self.capabilities is None:
            self.capabilities = _DEFAULT_CAPABILITIES_BY_STAGE.get(self.stage, ["review_allowed"])[:]
        return self

    @property
    def is_ladder_eligible(self) -> bool:
        return self.stage == "promoted" and "ladder_eligible" in (self.capabilities or [])

    @property
    def is_playwithyou_allowed(self) -> bool:
        return self.stage != "retired" and "playwithyou_allowed" in (self.capabilities or [])

    @property
    def is_review_allowed(self) -> bool:
        return "review_allowed" in (self.capabilities or [])


class ExternalModelRevision(BaseModel):
    external_revision_id: str
    model_identity_id: str
    provider: str
    version: str
    external_ref: str | None = None
    stage: ArtifactStage = "promoted"
    capabilities: list[ArtifactCapability] | None = None
    is_current: bool = True
    created_at: str
    retired_at: str | None = None

    @model_validator(mode="after")
    def populate_stage_and_capabilities(self) -> ExternalModelRevision:
        if self.capabilities is None:
            self.capabilities = _DEFAULT_CAPABILITIES_BY_STAGE.get(self.stage, ["review_allowed"])[:]
        return self

    @property
    def is_ladder_eligible(self) -> bool:
        return self.stage == "promoted" and "ladder_eligible" in (self.capabilities or [])

    @property
    def is_playwithyou_allowed(self) -> bool:
        return self.stage != "retired" and "playwithyou_allowed" in (self.capabilities or [])

    @property
    def is_review_allowed(self) -> bool:
        return "review_allowed" in (self.capabilities or [])


class ExternalModelRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_revision_id: str | None = None
    provider: str = Field(min_length=1)
    version: str = Field(min_length=1)
    external_ref: str | None = None
    stage: ArtifactStage = "promoted"
    capabilities: list[ArtifactCapability] | None = None
    is_current: bool = True


class ExternalModelRevisionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: ArtifactStage | None = None
    capabilities: list[ArtifactCapability] | None = None
    is_current: bool | None = None


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
    external_revisions: list[ExternalModelRevision] = Field(default_factory=list)


class ModelIdentityCreate(BaseModel):
    model_identity_id: str | None = None
    label: str = Field(min_length=1)
    kind: Literal["local_model", "external_agent", "none"]
    account_id: str | None = None
    artifact_path: str | None = None
    stage: ArtifactStage = "promoted"
    note: str | None = None


class ModelIdentityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    account_id: str | None = None
    is_current: bool | None = None
    note: str | None = None


class ModelArtifactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_artifact_id: str | None = None
    label: str = Field(min_length=1)
    artifact_path: str | None = None
    stage: ArtifactStage = "promoted"
    capabilities: list[ArtifactCapability] | None = None


class ModelArtifactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    stage: ArtifactStage | None = None
    capabilities: list[ArtifactCapability] | None = None
    is_current: bool | None = None


# ---------------------------------------------------------------------------
# 冻结执行凭据（不可变历史落账证据）
# ---------------------------------------------------------------------------

class FrozenExecutionProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["human", "local_artifact", "external_revision"]
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    external_revision_id: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "FrozenExecutionProvenance":
        if self.kind == "human":
            if self.model_identity_id is not None:
                raise ValueError("human execution_provenance 不允许携带 model_identity_id")
            if self.model_artifact_id is not None:
                raise ValueError("human execution_provenance 不允许携带 model_artifact_id")
            if self.external_revision_id is not None:
                raise ValueError("human execution_provenance 不允许携带 external_revision_id")
        elif self.kind == "local_artifact":
            if not self.model_identity_id:
                raise ValueError("local_artifact execution_provenance 必须提供 model_identity_id")
            if not self.model_artifact_id:
                raise ValueError("local_artifact execution_provenance 必须提供 model_artifact_id")
            if self.external_revision_id is not None:
                raise ValueError("local_artifact execution_provenance 不允许携带 external_revision_id")
        elif self.kind == "external_revision":
            if not self.model_identity_id:
                raise ValueError("external_revision execution_provenance 必须提供 model_identity_id")
            if not self.external_revision_id:
                raise ValueError("external_revision execution_provenance 必须提供 external_revision_id")
            if self.model_artifact_id is not None:
                raise ValueError("external_revision execution_provenance 不允许携带 model_artifact_id")
        return self


# ---------------------------------------------------------------------------
# R14-C: 运行时对局凭据绑定 (Runtime Match Provenance Binding)
# ---------------------------------------------------------------------------

class RuntimeMatchBinding(BaseModel):
    """R14-C: 描述正式天梯对局在运行时所绑定的 Frozen Runtime Manifest 与执行事实。

    - 冻结进入 Match / revision / replay artifact；
    - 绝不在 intake 时重新从 Catalog Current 推导；
    - 历史 / 手工导入 / 非 R14 对局保持可选兼容 (None)。
    """
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["keqing.runtime.match_binding.v1"] = "keqing.runtime.match_binding.v1"
    season_id: str
    manifest_id: str
    season_authority_hash: str
    session_id: str | None = None
    seat_provenance: dict[int, FrozenExecutionProvenance] = Field(default_factory=dict)
    seat_accounts: dict[int, str] = Field(default_factory=dict)
    admitted_at: str


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
    external_revision_id: str | None = None
    execution_provenance: FrozenExecutionProvenance | None = None
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


# ---------------------------------------------------------------------------
# R13-C: 摄入准入评估 (Admission Assessment)
# ---------------------------------------------------------------------------

class AdmissionIssue(BaseModel):
    code: str
    message: str
    seat: SeatNo | None = None


class AdmissionSeatAssessment(BaseModel):
    seat: SeatNo
    account_id: str | None = None
    display_name: str | None = None
    controller_type: ControllerType | None = None
    is_ladder_eligible: bool = False
    issues: list[AdmissionIssue] = Field(default_factory=list)
    frozen_provenance: FrozenExecutionProvenance | None = None
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    external_revision_id: str | None = None


class IntakeAdmissionAssessmentRequest(BaseModel):
    log_id: str = Field(min_length=1)
    resolutions: list[SeatResolution]
    session_id: str | None = None
    season_id: str | None = None
    rating_eligible: bool = False


class IntakeAdmissionAssessment(BaseModel):
    state: Literal["ready", "blocked", "not_requested"]
    season_id: str | None = None
    rating_eligible: bool = False
    issues: list[AdmissionIssue] = Field(default_factory=list)
    seats: list[AdmissionSeatAssessment] = Field(default_factory=list)


class MatchSeat(BaseModel):
    seat: SeatNo
    account_id: str
    controller_type: ControllerType | None = None  # 缺省取 account.default_controller
    # 兼容投影字段（旧数据 / UI 读取兼容，权威字段为 execution_provenance）
    model_identity_id: str | None = None
    model_artifact_id: str | None = None
    external_revision_id: str | None = None
    # 权威不可变执行证据对象 (R13-B)
    execution_provenance: FrozenExecutionProvenance | None = None


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
    runtime_binding: RuntimeMatchBinding | None = None  # R14-C 权威运行时清单绑定
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
    runtime_binding: RuntimeMatchBinding | None = None
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
    runtime_binding: RuntimeMatchBinding | None = None  # R14-C
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
