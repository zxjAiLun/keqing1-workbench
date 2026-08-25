// src/replay_ui/src/types/participants.ts
//
// R10 参赛身份 / 四人阵容 / 统一对局账本 —— 与后端 src/participants/schemas.py 对应。

export type AccountType = 'human' | 'managed_bot' | 'external_bot';
export type ControllerType = 'human_ui' | 'local_model' | 'external_agent' | 'manual_only';
export type SourceType = 'native' | 'imported' | 'manual';
export type DataCompleteness = 'result_only' | 'hand_summary' | 'full_replay';
export type GameLength = 'tonpu' | 'hanchan';
export type SeatNo = 0 | 1 | 2 | 3;
export type ArtifactStage = 'promoted' | 'candidate' | 'deprecated' | 'retired';
export type ArtifactCapability = 'ladder_eligible' | 'playwithyou_allowed' | 'review_allowed';

export interface Account {
  account_id: string;
  display_name: string;
  account_type: AccountType;
  enabled: boolean;
  default_controller: ControllerType;
  // R11-D：该 AI 账号由哪个逻辑模型驱动（真人/无模型为 null）
  model_identity_id?: string | null;
  avatar?: string | null;
  note?: string | null;
  migrated_from_replay?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ModelArtifact {
  model_artifact_id: string;
  label: string;
  model_identity_id: string;
  artifact_path?: string | null;
  hash?: string | null;
  is_current: boolean;
  stage?: 'promoted' | 'candidate' | 'deprecated' | 'retired';
  is_ladder_eligible?: boolean;
  capabilities?: string[];
  created_at: string;
  retired_at?: string | null;
}

export interface ModelArtifactUpdate {
  stage?: 'promoted' | 'candidate' | 'deprecated' | 'retired';
  capabilities?: string[];
  is_current?: boolean;
}

export interface ExternalModelRevision {
  external_revision_id: string;
  model_identity_id: string;
  provider: string;
  version: string;
  external_ref?: string | null;
  is_current: boolean;
  stage: 'promoted' | 'candidate' | 'deprecated' | 'retired';
  is_ladder_eligible?: boolean;
  capabilities?: string[];
  created_at: string;
  retired_at?: string | null;
  note?: string | null;
}

export interface ExternalModelRevisionCreate {
  external_revision_id?: string | null;
  provider: string;
  version: string;
  external_ref?: string | null;
  stage?: 'promoted' | 'candidate' | 'deprecated' | 'retired';
  is_current?: boolean;
  capabilities?: string[];
  note?: string | null;
}

export interface ExternalModelRevisionUpdate {
  stage?: 'promoted' | 'candidate' | 'deprecated' | 'retired';
  capabilities?: string[];
  is_current?: boolean;
}

export interface FrozenExecutionProvenance {
  kind: 'human' | 'local_artifact' | 'external_revision';
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  external_revision_id?: string | null;
}

export interface ModelIdentity {
  model_identity_id: string;
  label: string;
  kind: 'local_model' | 'external_agent' | 'none';
  account_id?: string | null;
  is_current: boolean;
  created_at: string;
  retired_at?: string | null;
  note?: string | null;
  artifacts: ModelArtifact[];
  external_revisions?: ExternalModelRevision[];
}

export interface MatchSeat {
  seat: SeatNo;
  account_id: string;
  controller_type?: ControllerType | null;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  external_revision_id?: string | null;
  execution_provenance?: FrozenExecutionProvenance | null;
}

export interface Match {
  schema: string;
  match_id: string;
  occurred_at: string;
  game_length: GameLength;
  rule_set: string;
  starting_points: number;
  initial_oya: number;
  source: SourceType;
  source_ref?: string | null;
  note?: string | null;
  data_completeness: DataCompleteness;
  replay_id?: string | null;
  seats: MatchSeat[];
  final_scores: number[];
  ranks: number[];
  status: 'active' | 'void';
  void_reason?: string | null;
  season_id?: string | null;
  rating_eligible?: boolean;
  ladder_projection_state?: 'not_applicable' | 'pending' | 'ready' | 'error';
  revision: number;
  latest_revision_id: string;
  created_at: string;
  updated_at: string;
  created_by: 'manual' | 'migration' | 'system';
}

export interface ValidationIssue {
  code: string;
  message: string;
}

export interface RevisionSummary {
  revision_id: string;
  match_id: string;
  revision: number;
  action: 'create' | 'revise' | 'void';
  created_at: string;
  by: string;
  force: boolean;
  reason?: string | null;
  validation: { passed: boolean; issues: ValidationIssue[] };
}

export interface AccountCreate {
  account_id?: string | null;
  display_name: string;
  account_type: AccountType;
  default_controller?: ControllerType | null;
  model_identity_id?: string | null;
  avatar?: string | null;
  note?: string | null;
}

export interface AccountUpdate {
  display_name?: string | null;
  enabled?: boolean | null;
  default_controller?: ControllerType | null;
  model_identity_id?: string | null;
  avatar?: string | null;
  note?: string | null;
}

export interface ModelIdentityCreate {
  model_identity_id?: string | null;
  label: string;
  kind: 'local_model' | 'external_agent' | 'none';
  account_id?: string | null;
  artifact_path?: string | null;
  note?: string | null;
}

export interface ModelArtifactCreate {
  model_artifact_id?: string | null;
  label: string;
  artifact_path?: string | null;
}

export interface MatchCreate {
  occurred_at: string;
  game_length: GameLength;
  rule_set?: string;
  starting_points?: number;
  initial_oya?: number;
  source?: SourceType;
  source_ref?: string | null;
  note?: string | null;
  data_completeness?: DataCompleteness;
  replay_id?: string | null;
  seats: MatchSeat[];
  final_scores: number[];
  season_id?: string | null;
  rating_eligible?: boolean;
  force?: boolean;
  reason?: string | null;
}

export interface MatchRevise {
  occurred_at?: string | null;
  game_length?: GameLength | null;
  rule_set?: string | null;
  starting_points?: number | null;
  initial_oya?: number | null;
  note?: string | null;
  data_completeness?: DataCompleteness | null;
  seats?: MatchSeat[] | null;
  final_scores?: number[] | null;
  season_id?: string | null;
  rating_eligible?: boolean | null;
  force?: boolean;
  reason?: string | null;
}

export interface MatchResponse {
  match: Match;
  revisions: RevisionSummary[];
}

export interface MatchListResponse {
  schema: string;
  matches: Match[];
  total: number;
}

export interface AccountsResponse {
  schema: string;
  accounts: Account[];
}

export interface ModelsResponse {
  schema: string;
  identities: ModelIdentity[];
}

// ---- R10-D：外部别名 + 天凤统一摄入 ----

export type ExternalAliasScope = 'global' | 'session' | 'match';

export interface ExternalAlias {
  alias_id: string;
  provider: string;
  external_id: string;
  display_name?: string | null;
  account_id: string;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  scope: ExternalAliasScope;
  session_id?: string | null;
  confidence: 'confirmed' | 'unresolved';
  created_at: string;
  updated_at: string;
}

export interface ExternalAliasCreate {
  provider?: string;
  external_id: string;
  display_name?: string | null;
  account_id: string;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  scope?: ExternalAliasScope;
  session_id?: string | null;
  confidence?: 'confirmed' | 'unresolved';
}

export interface IntakePreviewRequest {
  url: string;
  session_id?: string | null;
}

export interface SeatResolution {
  seat: SeatNo;
  action: 'assign' | 'create';
  account_id?: string | null;
  display_name?: string | null;
  account_type?: AccountType | null;
  default_controller?: ControllerType | null;
  alias_id?: string | null;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  external_revision_id?: string | null;
  execution_provenance?: FrozenExecutionProvenance | null;
  alias_scope?: ExternalAliasScope | 'none';
  confidence?: 'confirmed' | 'unresolved';
}

export interface IntakeConfirmRequest {
  log_id: string;
  resolutions: SeatResolution[];
  session_id?: string | null;
  note?: string | null;
  // R10 UX Repair P1-6：正式天梯由 confirm 决定
  season_id?: string | null;
  rating_eligible?: boolean | null;
}

export interface LadderProjectionStatus {
  season_id: string;
  dirty: boolean;
  states: Record<string, number>;
  eligible_count: number;
}

export interface LadderProjectResult {
  season_id: string;
  state: 'ready' | 'error' | 'needs_rebuild' | 'already_running';
  snapshot_dir?: string | null;
  games?: number | null;
  reason?: string | null;
}

// ---- R10-G：账号详细统计 ----
export interface AccountStatsResponse {
  schema: string;
  account_id: string;
  coverage: {
    total_matches: number;
    matches_with_results: number;
    matches_with_hands: number;
    matches_with_full_replay: number;
    hands_used: number;
    hands_with_riichi_field: number;
    hands_with_call_field: number;
    ryukyoku_with_tenpai: number;
  };
  placement: {
    match_count: number;
    first_rate?: number | null;
    second_rate?: number | null;
    third_rate?: number | null;
    fourth_rate?: number | null;
    avg_rank?: number | null;
    avg_final_score?: number | null;
  };
  detailed: {
    hands_played: number;
    wins: number;
    dealins: number;
    win_rate?: number | null;
    dealin_rate?: number | null;
    tsumo_share?: number | null;
    riichi_rate?: number | null;
    call_rate?: number | null;
    tenpai_rate?: number | null;
    avg_win_points?: number | null;
    avg_dealin_points?: number | null;
    oya_win_rate?: number | null;
    koshu_win_rate?: number | null;
  };
}

export interface IntakePreviewSeat {
  seat: SeatNo;
  raw_name: string;
  candidates: ExternalAlias[];
  auto_account_id?: string | null;
  // R11-D：冻结模型身份下可绑定的候选账号（enabled）；无冻结模型时为 null
  eligible_account_ids?: string[] | null;
}

export interface IntakePreview {
  schema: string;
  provider: string;
  external_match_id: string;
  log_id: string;
  occurred_at: string;
  game_length: GameLength;
  starting_points: number;
  raw_player_names: string[];
  final_scores: number[];
  ranks: number[];
  data_completeness: DataCompleteness;
  hand_count: number;
  events_count: number;
  seats: IntakePreviewSeat[];
  duplicate_match_id?: string | null;
}

export interface MatchReplayArtifact {
  match_id: string;
  replay_id: string;
  schema: string;
  log_id: string;
  names: string[];
  final_scores: number[];
  ranks: number[];
  game_length: GameLength;
  started_at: string;
  hands: Array<{
    bakaze: string;
    kyoku: number;
    honba: number;
    oya: number;
    scores_before: number[];
    scores_after?: number[] | null;
    winners: Array<{ actor: number; win_type: string; target?: number | null; deltas: number[] }>;
    ryukyoku?: { reason?: string | null; deltas: number[] } | null;
  }>;
  has_events: boolean;
}

// ---- R13-C: 天梯准入评估 (Admission Assessment) ----

export interface AdmissionIssue {
  code: string;
  message: string;
  seat?: SeatNo | null;
}

export interface AdmissionSeatAssessment {
  seat: SeatNo;
  account_id?: string | null;
  display_name?: string | null;
  controller_type?: ControllerType | null;
  is_ladder_eligible: boolean;
  issues: AdmissionIssue[];
  frozen_provenance?: FrozenExecutionProvenance | null;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  external_revision_id?: string | null;
}

export interface IntakeAdmissionAssessmentRequest {
  log_id: string;
  resolutions: SeatResolution[];
  session_id?: string | null;
  season_id?: string | null;
  rating_eligible: boolean;
}

export interface IntakeAdmissionAssessment {
  state: 'ready' | 'blocked' | 'not_requested';
  season_id?: string | null;
  rating_eligible: boolean;
  issues: AdmissionIssue[];
  seats: AdmissionSeatAssessment[];
}
