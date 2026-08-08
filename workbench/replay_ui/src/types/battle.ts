import type { BotType } from './bot';

// src/replay_ui/src/types/battle.ts

export interface PlayerInfo {
  player_id: number;
  name: string;
  type: "human" | "bot";
}

export interface DiscardEntry {
  pai: string;
  tsumogiri: boolean;
  /** 后端实际不返回此字段，需兼容处理 */
  reach_declared?: boolean;
}

export interface MeldEntry {
  type: "pon" | "chi" | "daiminkan" | "ankan" | "kakan";
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
}

export type ActionType =
  | "dahai"
  | "reach"
  | "pon"
  | "chi"
  | "daiminkan"
  | "ankan"
  | "kakan"
  | "hora"
  | "ryukyoku"
  | "none";

export interface BattleState {
  game_id: string;
  phase: "waiting" | "playing" | "hand_result" | "ended";
  game_length?: "tonpu" | "hanchan";
  winner: number | null;
  bakaze: string;
  kyoku: number;
  honba: number;
  kyotaku: number;
  oya: number;
  scores: number[];
  dora_markers: string[];
  actor_to_move: number | null;
  /** 后端不返回 pai_raw，忽略此字段 */
  last_discard: { actor: number; pai: string; pai_raw?: string } | null;
  last_kakan?: { actor: number; pai?: string } | null;
  hand: string[];
  tsumo_pai: string | null;
  discards: DiscardEntry[][];
  melds: MeldEntry[][];
  reached: boolean[];
  pending_reach: boolean[];
  needs_input?: boolean;
  input_context?: "self_turn" | "discard_response" | "kakan_response" | null;
  legal_actions: Action[];
  remaining_wall: number;
  human_player_id: number;
  round_result?: {
    type: "hora" | "ryukyoku";
    actor?: number;
    target?: number;
    pai?: string;
    is_tsumo?: boolean;
    deltas?: number[];
    scores?: number[];
    han?: number;
    fu?: number;
    yaku?: string[];
    yaku_details?: Array<{ key?: string; name: string; han: number }>;
    cost?: Action["cost"];
    honba?: number;
    kyotaku?: number;
    tenpai_players?: number[];
  } | null;
  game_result?: {
    final_scores: number[];
    ranks: number[];
    rating_updates?: Array<{
      player_id: string;
      display_name: string;
      rating: number;
      rating_delta: number;
      games: number;
      average_rank: number;
      rank: number;
      score: number;
    }>;
  } | null;
  can_continue?: boolean;
  revealed_hands?: string[][] | null;
  player_info: PlayerInfo[];
  /** 仅回放使用：当前处于摸牌前态的玩家，用于展示他家第 14 张手牌 */
  replay_draw_actor?: number | null;
}

export interface StartBattleRequest {
  player_name: string;
  bot_count?: number;
  seed?: number;
  bot_model?: BotType;
  game_length?: "tonpu" | "hanchan";
}

export interface StartBattleResponse {
  game_id: string;
  state: BattleState;
}

export interface ActionRequest {
  game_id: string;
  action: Action;
}

export interface ActionResponse {
  success: boolean;
  state: BattleState;
  bot_action?: Action;
  rating_updates?: Array<{
    player_id: string;
    display_name: string;
    rating: number;
    rating_delta: number;
    games: number;
    average_rank: number;
    rank: number;
    score: number;
  }>;
}
