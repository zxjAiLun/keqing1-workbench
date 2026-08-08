// src/replay_ui/src/utils/replayAdapter.ts
// DecisionLogEntry → BattleState 适配，供 MahjongTable 消费

import type { Action, DecisionLogEntry } from '../types/replay';
import type { BattleState, DiscardEntry, MeldEntry } from '../types/battle';
import { TILE_ORDER } from './tileUtils.ts';
import { sameReplayAction } from './tileUtils.ts';

// ---------------------------------------------------------------------------
// 柱状图数据结构
// ---------------------------------------------------------------------------
export interface LogitTileData {
  pai: string;
  /** final_score 优先，兼容 beam_score/logit；undefined 表示该牌无候选权重 */
  score: number | undefined;
  /** Q value 按 tau=1 softmax 后的概率 */
  prob?: number;
  /** 概率百分比 0-100，用于柱高 */
  pct: number;
  isChosen: boolean;
  isGt: boolean;
  isTsumo: boolean;
  teacherBars?: Array<{
    model: string;
    qValue?: number | null;
    prob?: number | null;
    pct: number;
    rank?: number | null;
  }>;
}

function normalizedPercentages(scores: number[]): number[] {
  if (scores.length === 0) return [];
  const maxScore = Math.max(...scores);
  const exps = scores.map((score) => Math.exp(score - maxScore));
  const total = exps.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) {
    return scores.map(() => 0);
  }
  return exps.map((value) => (value / total) * 100);
}

/** 从 DecisionLogEntry 提取手牌柱状图数据（dahai 类决策才有意义） */
export function buildLogitData(entry: DecisionLogEntry): LogitTileData[] {
  const hand = entry.hand ?? [];
  const tsumo = entry.tsumo_pai ?? null;
  const chosenPai = entry.chosen?.type === 'dahai' ? (entry.chosen.pai ?? null) : null;
  const gtPai = entry.gt_action?.type === 'dahai' ? (entry.gt_action.pai ?? null) : null;

  // 构建 pai → score 映射（final_score 优先，兼容 beam/logit）
  const scoreMap: Record<string, number> = {};
  const probMap: Record<string, number> = {};
  const teacherMap: Record<string, LogitTileData['teacherBars']> = {};
  for (const c of entry.candidates ?? []) {
    if (c.action?.type === 'dahai' && c.action.pai) {
      scoreMap[c.action.pai] = c.final_score ?? c.beam_score ?? c.logit;
      if (typeof c.prob === 'number' && Number.isFinite(c.prob)) {
        probMap[c.action.pai] = c.prob;
      }
      if (c.teachers?.length) {
        teacherMap[c.action.pai] = c.teachers.map((teacher) => ({
          model: teacher.model,
          qValue: teacher.q_value,
          prob: teacher.prob,
          pct: typeof teacher.prob === 'number' && Number.isFinite(teacher.prob)
            ? teacher.prob * 100
            : 0,
          rank: teacher.rank,
        }));
      }
    }
  }

  const normalizedPcts = normalizedPercentages(Object.values(scoreMap));
  const scorePctMap = Object.fromEntries(
    Object.keys(scoreMap).map((pai, idx) => [pai, normalizedPcts[idx] ?? 0]),
  );

  // 排序：摸切排最后，其余按 TILE_ORDER
  const others = hand.filter(t => t !== tsumo);
  others.sort((a, b) => (TILE_ORDER[a] ?? 99) - (TILE_ORDER[b] ?? 99));
  const sorted = tsumo ? [...others, tsumo] : others;

  return sorted.map(pai => {
    const score = scoreMap[pai];
    const prob = probMap[pai];
    const pct = score !== undefined
      ? prob !== undefined ? prob * 100 : scorePctMap[pai] ?? 0
      : 0;
    return {
      pai,
      score,
      prob,
      pct,
      isChosen: pai === chosenPai,
      isGt: pai === gtPai,
      isTsumo: pai === tsumo,
      teacherBars: teacherMap[pai],
    };
  });
}

/** DecisionLogEntry 中非 dahai 候选的权重列表（final_score 优先，降序） */
export interface CandidateScore {
  action: DecisionLogEntry['candidates'][number]['action'];
  score: number;
  prob?: number;
  isChosen: boolean;
  isGt: boolean;
}

export function buildCandidateScores(entry: DecisionLogEntry): CandidateScore[] {
  const candidates = entry.candidates ?? [];
  const chosen = entry.chosen;
  const gt = entry.gt_action;

  const list: CandidateScore[] = candidates.map(c => ({
    action: c.action,
    score: c.final_score ?? c.beam_score ?? c.logit,
    prob: c.prob,
    isChosen: sameReplayAction(chosen, c.action),
    isGt: sameReplayAction(gt, c.action),
  }));

  list.sort((a, b) => b.score - a.score);
  return list;
}

export type ReplayBoardPhase = 'pre' | 'reach' | 'post';

/**
 * 牌谱实际动作的唯一来源（P2-3）。
 * - gt_action 存在 → gt_action
 * - obs 步 → chosen（观察步的 chosen 就是他家实际动作）
 * - 明确 pass/none → none
 * - 其他 gt_action=null 且 chosen 非 none → null + diagnostic（模型建议不是牌谱事实）
 */
export function getActualReplayAction(
  entry: DecisionLogEntry | null | undefined,
): Action | null {
  if (!entry) return null;
  const gt = entry.gt_action;
  if (gt) return gt;
  const chosen = entry.chosen;
  if (entry.is_obs && chosen) {
    return chosen.type === 'none' ? { type: 'none', actor: chosen.actor } : chosen;
  }
  if (chosen?.type === 'none') {
    return { type: 'none', actor: chosen.actor };
  }
  if (typeof console !== 'undefined') {
    console.error(
      `[replayAdapter] entry ${entry.step ?? '?'} gt_action 缺失且 chosen 非 none（${chosen?.type ?? '?'}）`
      + ' — 不将模型建议作为牌谱实际动作',
    );
  }
  return null;
}

/**
 * 跨 entry 合并白名单：只允许长生命周期棋盘字段从上一 entry 继承。
 * 决策级字段（hand/tsumo_pai/chosen/gt_action/candidates/actor_to_move/
 * last_discard/is_obs/board_phase/source_event_index/teacher_reviews）缺失时
 * 必须显式报诊断，严禁静默继承上一巡，避免两套状态机串味。
 */
const MERGE_FALLBACK_KEYS = ['discards', 'melds', 'dora_markers', 'reached', 'scores'] as const;

function mergeReplayEntryWithPrevious(
  entry: DecisionLogEntry,
  prevEntry?: DecisionLogEntry | null,
): DecisionLogEntry {
  if (!prevEntry) return entry;
  const merged: DecisionLogEntry = { ...entry };
  const mergedRecord = merged as unknown as Record<string, unknown>;
  for (const key of MERGE_FALLBACK_KEYS) {
    if (mergedRecord[key] == null && prevEntry[key] != null) {
      mergedRecord[key] = prevEntry[key];
    }
  }
  // 决策级字段缺失时显式诊断（不继承上一巡）。
  // 注意区分：字段不存在（键缺失）vs 字段存在但合法为 null（如 tsumo_pai / last_discard）。
  for (const key of ['hand', 'tsumo_pai', 'chosen', 'gt_action', 'actor_to_move', 'last_discard', 'is_obs', 'board_phase'] as const) {
    if (!(key in mergedRecord)) {
      if (typeof console !== 'undefined') {
        console.error(`[replayAdapter] entry ${entry.step ?? '?'} missing decision field: ${key}（不继承上一巡）`);
      }
    }
  }
  return merged;
}

function cloneDiscards(discards: DiscardEntry[][]): DiscardEntry[][] {
  return discards.map(row => row.map(item => ({ ...item })));
}

function cloneMelds(melds: MeldEntry[][]): MeldEntry[][] {
  return melds.map(row => row.map(item => ({ ...item, consumed: [...item.consumed] })));
}

function removeTileOnce(hand: string[], pai?: string): string[] {
  if (!pai) return [...hand];
  const idx = hand.findIndex(tile => tile === pai);
  if (idx < 0) return [...hand];
  const next = [...hand];
  next.splice(idx, 1);
  return next;
}

function popLastDiscardIfMatches(
  discards: DiscardEntry[][],
  actor: number,
  pai?: string,
): { discards: DiscardEntry[][]; removed?: DiscardEntry } {
  const next = cloneDiscards(discards);
  const actorDiscards = [...(next[actor] ?? [])];
  const last = actorDiscards[actorDiscards.length - 1];
  if (last && (!pai || last.pai === pai)) {
    actorDiscards.pop();
    next[actor] = actorDiscards;
    return { discards: next, removed: last };
  }
  return { discards: next };
}

const MELD_ACTION_TYPES = ['chi', 'pon', 'daiminkan', 'ankan', 'kakan'] as const;

function normalizedTileList(tiles: string[]): string {
  return [...tiles].sort().join('|');
}

/** kakan 的 consumed 对比：真实 gt_action 只有 3 张（不含加杠那张），修复后的 meld 为 4 张。 */
function kakanConsumedMatches(a: string[], b: string[]): boolean {
  if (normalizedTileList(a) === normalizedTileList(b)) return true;
  const [shortList, longList] = a.length <= b.length ? [a, b] : [b, a];
  return longList.length === shortList.length + 1
    && shortList.every((tile) => longList.includes(tile));
}

function sameMeldShape(a: MeldEntry, b: MeldEntry): boolean {
  if (a.type !== b.type || a.pai !== b.pai) return false;
  if (a.type === 'kakan') {
    // kakan 的 target 在真实 gt_action 中可能缺失（只是原 pon 的来源座位），
    // 且 consumed 可能比 meld 少一张加杠牌——统一放宽为 pai + consumed 子集匹配。
    return kakanConsumedMatches(a.consumed, b.consumed);
  }
  if (a.target !== b.target) return false;
  return normalizedTileList(a.consumed) === normalizedTileList(b.consumed);
}

function meldShapeForAction(action: Action): MeldEntry {
  return {
    type: action.type as MeldEntry['type'],
    pai: action.pai ?? '',
    consumed: [...(action.consumed ?? [])],
    target: action.target ?? action.actor,
  };
}

function needsMeldRewind(state: BattleState, action: Action): boolean {
  if (!(MELD_ACTION_TYPES as readonly string[]).includes(action?.type ?? '')) return false;
  const actorMelds = state.melds[action.actor] ?? [];
  return actorMelds.some((meld) => sameMeldShape(meld, meldShapeForAction(action)));
}

function addTileOnce(hand: string[], tile?: string): string[] {
  if (!tile) return [...hand];
  return [...hand, tile];
}

/** 把副露动作回退为动作前状态（pre phase 专用）。 */
function rewindMeldAction(
  state: BattleState,
  action: Action,
  viewPlayerId: number,
): BattleState {
  const melds = cloneMelds(state.melds);
  const actorMelds = [...(melds[action.actor] ?? [])];
  const shape = meldShapeForAction(action);

  // 匹配此次动作新增的 meld（type/pai/target/consumed），不是简单 popLast
  let removeIndex = -1;
  for (let i = actorMelds.length - 1; i >= 0; i--) {
    if (sameMeldShape(actorMelds[i], shape)) { removeIndex = i; break; }
  }
  const targetIndex = removeIndex >= 0 ? removeIndex : actorMelds.length - 1;
  const removed = actorMelds[targetIndex];
  const remaining = actorMelds.filter((_, i) => i !== targetIndex);

  let hand = [...state.hand];

  if (action.type === 'kakan' && removed) {
    // kakan 前态是原 pon：consumed[0..1] 为 pon 手牌、consumed[2] 为被鸣牌、
    // consumed[3] 为加杠牌。
    const restoredPon: MeldEntry = {
      type: 'pon',
      pai: removed.consumed[2] ?? removed.pai,
      consumed: removed.consumed.slice(0, 2),
      target: removed.target,
    };
    // 在原索引插回，保持副露时间顺序不漂移
    remaining.splice(targetIndex, 0, restoredPon);
    if (action.actor === viewPlayerId) {
      // K2：加杠牌来源无法从动作后 meld 推断（可能是本巡摸牌，也可能是手牌原有）。
      // 不猜测 tsumo，统一放回暗手；tsumo_pai 保持动作后快照值。
      const addedTile = removed.consumed[3] ?? removed.pai;
      if (addedTile) hand = addTileOnce(hand, addedTile);
    }
  } else if (action.actor === viewPlayerId) {
    for (const tile of action.consumed ?? []) hand = addTileOnce(hand, tile);
  }

  const lastDiscard =
    action.type === 'ankan' || action.type === 'kakan'
      ? null
      : action.pai
        ? { actor: action.target ?? action.actor, pai: action.pai, pai_raw: action.pai }
        : null;

  return {
    ...state,
    hand,
    melds: { ...melds, [action.actor]: remaining },
    last_discard: lastDiscard,
    actor_to_move: action.actor,
    tsumo_pai: state.tsumo_pai,
  };
}

/**
 * 把副露动作正向应用到动作后状态（post phase 专用）。
 * kakan 走 applyKakanAction：替换原 pon，而不是追加一组。
 */
function applyMeldAction(
  state: BattleState,
  action: Action,
  viewPlayerId: number,
): BattleState {
  if (action.type === 'kakan') {
    return applyKakanAction(state, action, viewPlayerId);
  }
  const melds = cloneMelds(state.melds);
  const actorMelds = [...(melds[action.actor] ?? [])];
  let hand = [...state.hand];
  let tsumoPai = state.tsumo_pai;
  if (action.actor === viewPlayerId) {
    const consumed = [...(action.consumed ?? [])];
    // 摸牌进副露（ankan 等自摸动作）：先吃掉 tsumo_pai，再吃暗手
    if (tsumoPai && consumed.includes(tsumoPai)) {
      consumed.splice(consumed.indexOf(tsumoPai), 1);
      tsumoPai = null;
    }
    for (const tile of consumed) hand = removeTileOnce(hand, tile);
  }
  actorMelds.push({
    type: action.type as MeldEntry['type'],
    pai: action.pai ?? '',
    consumed: [...(action.consumed ?? [])],
    target: action.target ?? action.actor,
  });
  return {
    ...state,
    hand,
    melds: { ...melds, [action.actor]: actorMelds },
    tsumo_pai: tsumoPai,
    last_discard: null,
    actor_to_move: action.actor,
  };
}

/**
 * kakan 正向应用：找到同牌、同 target 的原 pon 并原地替换为 kakan。
 * 加杠牌（= action.pai）从自家暗手/摸牌中移除；找不到唯一原 pon 时报错并保持原状态。
 */
function applyKakanAction(
  state: BattleState,
  action: Action,
  viewPlayerId: number,
): BattleState {
  const melds = cloneMelds(state.melds);
  const actorMelds = [...(melds[action.actor] ?? [])];

  let ponIndex = -1;
  for (let i = actorMelds.length - 1; i >= 0; i--) {
    const meld = actorMelds[i];
    if (
      meld.type === 'pon'
      && meld.pai === action.pai
      && (action.target == null || meld.target === action.target)
    ) {
      if (ponIndex >= 0) { ponIndex = -1; break; }
      ponIndex = i;
    }
  }
  if (ponIndex < 0) {
    if (typeof console !== 'undefined') {
      console.error('kakan forward: no unique original pon found', action, actorMelds);
    }
    return state;
  }
  const pon = actorMelds[ponIndex];
  const addedTile = action.pai ?? '';
  // K1：canonical consumed 严格四张，从原 pon 构造，兼容 action.consumed 为 3 张或 4 张：
  //   [...pon.consumed, pon.pai, addedTile]
  // 保留原 pon 的赤牌组成与被鸣牌身份，不再"action.consumed + pai"补一张。
  const kakan: MeldEntry = {
    type: 'kakan',
    pai: action.pai ?? '',
    consumed: [...pon.consumed, pon.pai, ...(addedTile ? [addedTile] : [])],
    target: action.target ?? pon.target,
  };
  actorMelds[ponIndex] = kakan;

  let hand = [...state.hand];
  let tsumoPai = state.tsumo_pai;
  if (action.actor === viewPlayerId && addedTile) {
    if (tsumoPai === addedTile) {
      tsumoPai = null;
    } else {
      hand = removeTileOnce(hand, addedTile);
    }
  }

  return {
    ...state,
    hand,
    melds: { ...melds, [action.actor]: actorMelds },
    tsumo_pai: tsumoPai,
    last_discard: null,
    actor_to_move: action.actor,
  };
}

function supportsPostActionPhase(entry: DecisionLogEntry): boolean {
  const action = getActualReplayAction(entry);
  return action != null && ['dahai', 'chi', 'pon', 'daiminkan', 'ankan', 'kakan', 'hora', 'ryukyoku'].includes(action.type);
}

function supportsReachPhase(entry: DecisionLogEntry): boolean {
  const action = getActualReplayAction(entry);
  return action?.type === 'reach';
}

function isResponseNoneAction(entry: DecisionLogEntry | null | undefined): boolean {
  const action = getActualReplayAction(entry);
  return action?.type === 'none';
}

function hasNonNoneCandidates(entry: DecisionLogEntry | null | undefined): boolean {
  return Boolean(entry?.candidates?.some((candidate) => candidate.action?.type !== 'none'));
}

export function isCollapsibleResponsePassStep(
  entry: DecisionLogEntry | null | undefined,
  prevEntry?: DecisionLogEntry | null,
): boolean {
  const prevAction = getActualReplayAction(prevEntry);
  return Boolean(
    entry
    && prevEntry
    && isResponseNoneAction(entry)
    && prevEntry.is_obs
    && prevAction?.type === 'dahai'
    && !hasNonNoneCandidates(entry)
  );
}

export function hasReplayPostAction(entry: DecisionLogEntry | null | undefined): boolean {
  return Boolean(entry && supportsPostActionPhase(entry));
}

export function hasReplayReachPhase(entry: DecisionLogEntry | null | undefined): boolean {
  return Boolean(entry && supportsReachPhase(entry));
}

// ---------------------------------------------------------------------------
// DecisionLogEntry → BattleState
// ---------------------------------------------------------------------------
export function entryToBattleState(
  entry: DecisionLogEntry,
  playerNames: string[] = ['P0', 'P1', 'P2', 'P3'],
  viewPlayerId: number = 0,
  phase: ReplayBoardPhase = 'pre',
  prevEntry?: DecisionLogEntry | null,
): BattleState {
  if (isCollapsibleResponsePassStep(entry, prevEntry)) {
    return entryToBattleState(prevEntry!, playerNames, viewPlayerId, 'post');
  }
  const mergedEntry = mergeReplayEntryWithPrevious(entry, prevEntry);
  const previousAction = prevEntry ? (prevEntry.gt_action ?? prevEntry.chosen) : null;
  // Record<number, X[]> → X[][]（4家，空补齐）
  function toArray<T>(rec: Record<number, T[]> | undefined): T[][] {
    return [0, 1, 2, 3].map(i => rec?.[i] ?? []);
  }

  const discards = Array.isArray(mergedEntry.discards)
    ? mergedEntry.discards as DiscardEntry[][]
    : toArray(mergedEntry.discards) as DiscardEntry[][];
  const melds = Array.isArray(mergedEntry.melds)
    ? mergedEntry.melds as MeldEntry[][]
    : toArray(mergedEntry.melds) as MeldEntry[][];
  const action = getActualReplayAction(mergedEntry);
  const isMeldAction = action != null && (MELD_ACTION_TYPES as readonly string[]).includes(action.type);
  const isPreDiscardPhase = phase === 'pre' && action?.type === 'dahai';
  const suppressSnapshotDrawActor = action?.type === 'none';
  const pendingDiscardActorFromSnapshot =
    mergedEntry.actor_to_move !== null && mergedEntry.actor_to_move !== undefined && mergedEntry.last_discard === null
      ? mergedEntry.actor_to_move
      : null;
  const showsReplayDraw = isPreDiscardPhase
    ? action.actor
    : suppressSnapshotDrawActor
      ? null
      : pendingDiscardActorFromSnapshot;
  const normalizedTsumoPai =
    isPreDiscardPhase && action.actor === viewPlayerId
      ? (mergedEntry.tsumo_pai ?? (action.tsumogiri ? action.pai ?? null : null))
      : mergedEntry.tsumo_pai;
  const normalizedActorToMove =
    isPreDiscardPhase
      ? action.actor
      : mergedEntry.actor_to_move;
  const normalizedLastDiscard =
    isPreDiscardPhase && mergedEntry.last_discard?.actor === action.actor
      ? null
      : mergedEntry.last_discard;

  const baseState: BattleState = {
    game_id: 'replay',
    phase: 'playing',
    winner: null,
    bakaze: mergedEntry.bakaze,
    kyoku: mergedEntry.kyoku,
    honba: mergedEntry.honba,
    kyotaku: 0,
    oya: mergedEntry.oya,
    scores: mergedEntry.scores,
    dora_markers: mergedEntry.dora_markers ?? [],
    actor_to_move: normalizedActorToMove,
    last_discard: normalizedLastDiscard,
    hand: mergedEntry.hand ?? [],
    tsumo_pai: normalizedTsumoPai,
    discards,
    melds,
    reached: mergedEntry.reached ?? [false, false, false, false],
    pending_reach: [false, false, false, false],
    legal_actions: [],
    remaining_wall: 0,
    human_player_id: viewPlayerId,
    player_info: playerNames.map((name, id) => ({ player_id: id, name, type: id === 0 ? 'human' : 'bot' })),
    replay_draw_actor: showsReplayDraw,
  };

  if (mergedEntry.is_obs) {
    // snapshotPhase：entry 数据本身处于什么时点；targetPhase：用户请求的 pre/post。
    // 打牌动作按目标 phase 推导快照时点（obs 弃牌 entry 的 board_phase 不可靠），
    // 其余动作读 entry.board_phase。
    const snapshotPhase =
      action?.type === 'dahai'
        ? (phase === 'post' ? 'after_action' : 'before_action')
        : (mergedEntry.board_phase ?? 'after_action');

    if (action?.type === 'dahai') {
      if (snapshotPhase === 'after_action') {
        return { ...baseState, replay_draw_actor: null };
      }
      const nextState: BattleState = {
        ...baseState,
        hand: [...baseState.hand],
        discards: cloneDiscards(baseState.discards),
        melds: cloneMelds(baseState.melds),
        reached: [...baseState.reached],
        pending_reach: [...baseState.pending_reach],
        last_discard: baseState.last_discard ? { ...baseState.last_discard } : null,
        actor_to_move: baseState.actor_to_move,
        replay_draw_actor: null,
      };
      const { discards: prevDiscards, removed } = popLastDiscardIfMatches(
        baseState.discards,
        action.actor,
        action.pai,
      );
      nextState.discards = prevDiscards;
      nextState.last_discard = null;
      nextState.actor_to_move = action.actor;
      nextState.replay_draw_actor = action.actor;
      if (removed?.reach_declared) {
        nextState.reached[action.actor] = false;
        nextState.pending_reach[action.actor] = true;
      }
      return nextState;
    }

    if (isMeldAction && action) {
      // 完整矩阵：
      //   before & pre  → 原样
      //   after  & post → 原样
      //   before & post → 正向应用
      //   after  & pre  → 逆向回退
      if (snapshotPhase === 'before_action' && phase === 'post') {
        if (needsMeldRewind(baseState, action)) {
          // 快照已是动作后：不再前向应用
          return { ...baseState, replay_draw_actor: null };
        }
        return applyMeldAction(baseState, action, viewPlayerId);
      }
      if (snapshotPhase === 'after_action' && phase === 'pre') {
        if (needsMeldRewind(baseState, action)) {
          return rewindMeldAction(baseState, action, viewPlayerId);
        }
        return baseState;
      }
      return { ...baseState, replay_draw_actor: null };
    }

    // 非副露、非打牌动作（reach/none/hora 等）：after_action 时取消摸牌指示
    if (snapshotPhase === 'after_action') {
      return { ...baseState, replay_draw_actor: null };
    }
    return baseState;
  }

  if (phase === 'pre') {
    if (action && needsMeldRewind(baseState, action)) {
      // 动作前：把 snapshot 中的副露动作回退（恢复 consumed、正确 meld、last_discard）
      return rewindMeldAction(baseState, action, viewPlayerId);
    }
    return baseState;
  }

  const nextState: BattleState = {
    ...baseState,
    hand: [...baseState.hand],
    discards: cloneDiscards(baseState.discards),
    melds: cloneMelds(baseState.melds),
    reached: [...baseState.reached],
    pending_reach: [...baseState.pending_reach],
    last_discard: baseState.last_discard ? { ...baseState.last_discard } : null,
    actor_to_move: baseState.actor_to_move,
    replay_draw_actor: null,
  };

  if (phase === 'reach' && action?.type === 'reach') {
    nextState.pending_reach[action.actor] = true;
    nextState.actor_to_move = action.actor;
    return nextState;
  }

  if (!supportsPostActionPhase(mergedEntry)) {
    return baseState;
  }

  if (!action) {
    // actual action 缺失（gt_action=null 且 chosen 非 none）：不得把模型建议当牌谱动作
    return baseState;
  }

  if (action.type === 'dahai') {
    const declaresReach =
      (mergedEntry.gt_action?.type === 'reach' && mergedEntry.gt_action.actor === action.actor)
      || (previousAction?.type === 'reach' && previousAction.actor === action.actor);
    if (action.actor === viewPlayerId) {
      const handWithDraw = baseState.tsumo_pai ? [...nextState.hand, baseState.tsumo_pai] : [...nextState.hand];
      nextState.hand = removeTileOnce(handWithDraw, action.pai);
      nextState.tsumo_pai = null;
    }
    if (declaresReach) {
      nextState.reached[action.actor] = true;
      nextState.pending_reach[action.actor] = false;
    }
    nextState.discards[action.actor] = [
      ...(nextState.discards[action.actor] ?? []),
      {
        pai: action.pai ?? '?',
        tsumogiri: Boolean(action.tsumogiri),
        reach_declared: declaresReach,
      },
    ];
    nextState.last_discard = action.pai
      ? { actor: action.actor, pai: action.pai, pai_raw: action.pai }
      : null;
    nextState.actor_to_move = null;
    return nextState;
  }

  if (isMeldAction && action) {
    if (needsMeldRewind(baseState, action)) {
      // snapshot 已经是动作后状态（meld 已在、consumed 已移除）：不再前向应用
      return baseState;
    }
    return applyMeldAction(nextState, action, viewPlayerId);
  }

  return baseState;
}
