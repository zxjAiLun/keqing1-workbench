import type { BotType } from './bot';

// src/replay_ui/src/types/replay.ts

export interface DiscardEntry {
  pai: string;
  tsumogiri: boolean;
  /** 后端实际不返回此字段，需兼容处理 */
  reach_declared?: boolean;
}

export interface MeldEntry {
  type: string;
  pai: string;
  consumed: string[];
  target: number;
}

export interface Action {
  type: ActionType;
  actor: number;
  pai?: string;
  target?: number;
  consumed?: string[];
  tsumogiri?: boolean;
  is_tsumo?: boolean;
  deltas?: number[];
  scores?: number[];
  han?: number;
  fu?: number;
  yaku?: string[];
  yaku_details?: Array<{
    key: string;
    name: string;
    han: number;
  }>;
  ura_dora_markers?: string[];
  cost?: {
    main?: number;
    main_bonus?: number;
    additional?: number;
    additional_bonus?: number;
    kyoutaku_bonus?: number;
    total?: number;
    yaku_level?: string;
  };
  honba?: number;
  kyotaku?: number;
  tenpai_players?: number[];
}

export type ActionType =
  | 'dahai'
  | 'reach'
  | 'reach_accepted'
  | 'chi'
  | 'pon'
  | 'daiminkan'
  | 'ankan'
  | 'kakan'
  | 'hora'
  | 'ryukyoku'
  | 'none';

export interface KyokuInfo {
  bakaze: string;
  kyoku: number;
  honba: number;
}

export interface TeacherCandidateValue {
  model: string;
  q_value?: number | null;
  prob?: number | null;
  rank?: number | null;
}

export interface TeacherReviewOverlay {
  model?: string;
  report_path?: string;
  report_player_id?: number | null;
  teacher_decision_count?: number;
  attached_decision_count?: number;
  alignment?: string;
  error?: string;
}

export interface TeacherReviewEntry {
  model: string;
  report_path: string;
  report_player_id?: number | null;
  kyoku_index: number;
  entry_index: number;
  junme?: number | null;
  tiles_left?: number | null;
  shanten?: number | null;
  actual_action?: Action | null;
  expected_action?: Action | null;
  is_equal?: boolean | null;
  actual_q?: number | null;
  expected_q?: number | null;
  actual_prob?: number | null;
  expected_prob?: number | null;
  best_q?: number | null;
  best_prob?: number | null;
  q_loss?: number | null;
  top1?: { action: Action; q_value?: number | null; prob?: number | null; rank?: number | null } | null;
  top2?: { action: Action; q_value?: number | null; prob?: number | null; rank?: number | null } | null;
  candidate_count?: number;
  display_mode?: 'joint_reach_dahai' | null;
  candidates?: Array<{
    action: Action;
    q_value?: number | null;
    prob?: number | null;
    rank?: number | null;
  }>;
}

/** /api/replay 返回的 decision_log 条目结构 */
export interface DecisionLogEntry {
  step: number;
  bakaze: string;
  kyoku: number;
  honba: number;
  oya: number;
  scores: number[];
  reached: boolean[];
  dora_markers: string[];
  hand: string[];
  discards: Record<number, DiscardEntry[]> | DiscardEntry[][];
  melds?: Record<number, MeldEntry[]> | MeldEntry[][];
  actor_to_move: number;
  tsumo_pai: string | null;
  last_discard: { actor: number; pai: string; pai_raw?: string } | null;
  /** 是否为其他家的观察步（无 bot 推理数据） */
  is_obs: boolean;
  /** 当前视角 Bot 的决策（obs 步为他家实际动作） */
  chosen: Action;
  /** 所有合法动作候选；prob 为 Q value 按 tau=1 softmax 后的概率，旧版本回放前端会补算 */
  candidates: Array<{
    action: Action;
    logit: number;
    beam_score?: number;
    final_score?: number;
    prob?: number;
    teachers?: TeacherCandidateValue[];
    teacher?: TeacherCandidateValue;
  }>;
  /** 当前视角 Bot 的 value loss 预测 */
  value?: number;
  /** ground truth：玩家实际动作 */
  gt_action: Action | null;
  /** 响应窗口被其他玩家更高优先级动作截断，不参与错误/一致率统计 */
  comparison_exempt?: 'response_preempted' | string;
  comparison_exempt_by?: Action | null;
  /** 观察步类型，仅 is_obs=true 时有意义 */
  obs_kind?: 'discard' | 'meld' | 'reach' | 'terminal';
  /** 当前棋盘快照语义，默认 after_action */
  board_phase?: 'before_action' | 'after_action';
  /** 对应 events.jsonl 的事件序号 */
  source_event_index?: number;
  /** 供前端按小局过滤 */
  kyoku_key: KyokuInfo;
  /** 多个 reviewer teacher overlay，例如 Mortal 3.0 / 4.1b 的 q/prob。 */
  teacher_reviews?: TeacherReviewEntry[];
  /** 可选本地模型 overlay，包含候选动作的 q/prob。 */
  teacher_review?: TeacherReviewEntry;
}

export interface ReplayData {
  replay_id?: string;
  log: DecisionLogEntry[];
  kyoku_order: KyokuInfo[];
  total_ops: number;
  match_count: number;
  rating: number | null;
  player_id: number;
  player_names?: string[];
  bot_type?: BotType;
  model_label?: string;
  teacher_report_paths?: string[];
  selected_teacher_models?: Array<{
    type: BotType;
    label: string;
    checkpoint: string;
  }>;
  teacher_review_overlays?: TeacherReviewOverlay[];
  teacher_review_overlay?: TeacherReviewOverlay;
  external_review_links?: ExternalReviewLinks;
}

export interface ExternalReviewLinks {
  naga?: string;
  mortal?: string;
}

export interface ReplayMeta {
  replay_id: string;
  created_at: string;
  bot_type: BotType;
  kyoku_count: number;
  total_steps: number;
  player_names: string[];
  final_scores: number[];
  external_review_links?: ExternalReviewLinks;
}

export interface ReviewHistoryItem {
  replay_id: string;
  created_at: string;
  player_id: number;
  player_name: string;
  player_names: string[];
  kyoku_count: number;
  total_steps: number;
  models: string[];
  teacher_report_paths: string[];
  external_review_links?: ExternalReviewLinks;
}

export interface ReplaySubmitRequest {
  input_type: 'tenhou_url' | 'tenhou6_json' | 'mjson_file' | 'mjson_text';
  content: string;
  bot_type: BotType;
  player_ids: number[];
  checkpoint?: string;
}

export interface BotDecision {
  action: Action;
  logit: number;
  is_correct: boolean | null;
  value: number | null;
}

export interface StepEntry {
  step: number;
  bakaze: string;
  kyoku: number;
  honba: number;
  oya: number;
  scores: number[];
  reached: boolean[];
  dora_markers: string[];
  hand: string[];
  discards: Record<number, DiscardEntry[]>;
  melds: Record<number, MeldEntry[]>;
  actor_to_move: number;
  tsumo_pai: string | null;
  last_discard: { actor: number; pai: string; pai_raw?: string } | null;
  bot_decisions: Record<number, BotDecision>;
  gt_action: Action | null;
  kyoku_key: KyokuInfo;
}

export interface ReplaySubmitResponse {
  replay_id: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  progress: number;
  error?: string;
}

export interface SelfplayAnomalyReplayItem {
  game_id: number;
  anomaly_score: number;
  score_components: Record<string, number>;
  interest?: number;
  scores?: number[];
  ranks?: number[];
  turns?: number;
  rounds?: number;
  mjson: string;
  meta: string;
  replay_id?: string;
  replay_player_id?: number;
  replay_bot_type?: string;
  replay_view_url?: string;
  game_board_url?: string;
  replay_ui_error?: string | null;
}

export interface SelfplayAnomalyReplayGroup {
  output_dir: string;
  output_dir_path: string;
  manifest_path: string;
  collection_type?: string;
  updated_at: number;
  stats: {
    games?: number;
    completed_games?: number;
    error_games?: number;
    seconds_per_game?: number;
    avg_turns?: number;
  } | null;
  items: SelfplayAnomalyReplayItem[];
}

export interface BehaviorReplayCase {
  case_id: string;
  case_kind: string;
  source_log: string;
  mjson_path: string;
  review_payload_path: string;
  focus_event_index: number;
  focus_step: number;
  model_label: string;
  checkpoint_path?: string | null;
  slice_tags: string[];
  decision_kind: string;
  action_type: string;
  actor: number;
  turn: number;
  margin: number;
  chosen_q: number;
  alternative_action: string;
  alternative_q: number;
  outcome: string;
  agari: boolean;
  houjuu: boolean;
  ryukyoku: boolean;
  why_selected: string;
  selection_score: number;
  shanten: number | null;
}

export interface PairedBehaviorReplayCase {
  case_id: string;
  case_kind: string;
  left_model: string;
  right_model: string;
  left_checkpoint_path?: string | null;
  right_checkpoint_path?: string | null;
  left_source_log: string;
  right_source_log: string;
  left_mjson_path: string;
  right_mjson_path: string;
  left_focus_event_index: number;
  right_focus_event_index: number;
  prefix_match_event_count: number;
  divergence_kind: string;
  actor: number;
  oya: number | null;
  bakaze: string | null;
  kyoku: number | null;
  turn: number | null;
  start_rank: number | null;
  score_bucket: string | null;
  left_action: Action;
  right_action: Action;
  downstream_summary: {
    left_kyoku_outcome?: string;
    right_kyoku_outcome?: string;
    left_final_scores?: number[];
    right_final_scores?: number[];
    left_actor_final_rank?: number;
    right_actor_final_rank?: number;
  };
  slice_tags: string[];
  why_selected: string;
  selection_score: number;
}

export interface BehaviorCasebookResponse {
  casebook_dir: string;
  manifest_path: string;
  updated_at: number | null;
  case_counts: Record<string, number>;
  cases: BehaviorReplayCase[];
  paired_casebook_dir?: string;
  paired_manifest_path?: string;
  paired_updated_at?: number | null;
  paired_case_counts?: Record<string, number>;
  paired_cases?: PairedBehaviorReplayCase[];
}

export interface BehaviorCaseImportResponse {
  replay_id: string;
  case: BehaviorReplayCase | PairedBehaviorReplayCase;
  side?: 'left' | 'right';
  player_id: number;
  focus_event_index: number;
  focus_step: number;
  focus_replay_step: number | null;
  focus_resolution: 'exact' | 'nearest' | 'missing';
  game_board_url: string;
}

export type PlayerMode =
  | { type: 'replay'; replay_id: string }
  | { type: 'realtime'; game_id: string };
