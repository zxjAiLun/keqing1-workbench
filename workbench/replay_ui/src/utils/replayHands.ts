// src/replay_ui/src/utils/replayHands.ts
// 从原始 mjai events 重建四家手牌，用于对手 Oracle reveal / 终局牌姿 / 调试对照。
//
// 对齐契约：
//   - 每个决策 entry 的 gt_action 对应原始 events 的一个事件，用 source_event_index 定位。
//   - pre：应用小局开始后、gt_action 事件之前的全部事件。
//   - post：在 pre 基础上应用 gt_action 事件（kakan 还会继续吞掉后续 kakan_accepted，
//     与 adapter 的"kakan 立即完成"使用同一时间点）。
//   - 每步都从稳定边界重建，不依赖上一 decision 是否成功推进 cursor。
//   - source_event_index 缺失时走 legacy 窗口匹配（前后 obs 边界内找动作事件），
//     失败返回 null + 对齐诊断，绝不悄悄沿用旧 cursor。

import type { Action } from '../types/replay';
import { getActualReplayAction, type ReplayBoardPhase } from './replayAdapter.ts';
import type { DecisionLogEntry, ReplayData } from '../types/replay';

export type ReplayEvent = Record<string, unknown>;

export function removeTileOnce(hand: string[], tile?: string) {
  if (!tile) return hand;
  const idx = hand.indexOf(tile);
  if (idx >= 0) {
    hand.splice(idx, 1);
    return hand;
  }
  const norm = tile.endsWith('r') ? tile.slice(0, 2) : tile;
  const normIdx = hand.findIndex((h) => h === norm || (h.endsWith('r') ? h.slice(0, 2) : h) === norm);
  if (normIdx >= 0) hand.splice(normIdx, 1);
  return hand;
}

export function sameConsumed(a: string[] = [], b: string[] = []): boolean {
  if (a.length !== b.length) return false;
  return [...a].sort().join(',') === [...b].sort().join(',');
}

export function eventMatchesAction(ev: ReplayEvent, action: Action): boolean {
  const type = String(ev.type ?? '');
  if (type !== action.type) return false;
  if (Number(ev.actor ?? -1) !== Number(action.actor ?? -1)) return false;
  if (action.pai !== undefined && String(ev.pai ?? '') !== String(action.pai ?? '')) return false;
  if (action.target !== undefined && Number(ev.target ?? -1) !== Number(action.target ?? -1)) return false;
  if (action.consumed && !sameConsumed(((ev.consumed as string[] | undefined) ?? []).map(String), action.consumed)) return false;
  return true;
}

export function applyReplayEventToHands(hands: string[][], ev: ReplayEvent) {
  const type = String(ev.type ?? '');
  const actor = Number(ev.actor ?? -1);
  if (actor < 0) return;
  if (type === 'tsumo') {
    hands[actor].push(String(ev.pai ?? ''));
    return;
  }
  if (type === 'dahai') {
    removeTileOnce(hands[actor], String(ev.pai ?? ''));
    return;
  }
  if (['chi', 'pon', 'daiminkan', 'ankan'].includes(type)) {
    const consumed = ((ev.consumed as string[] | undefined) ?? []).map(String);
    for (const tile of consumed) removeTileOnce(hands[actor], tile);
    return;
  }
  // kakan 的加杠牌只在 kakan_accepted 时离开暗手/摸牌位
  if (type === 'kakan_accepted') {
    removeTileOnce(hands[actor], String(ev.pai ?? ''));
  }
}

export function findKyokuEventRange(events: ReplayEvent[], entry: DecisionLogEntry) {
  const kyokuKey = entry.kyoku_key;
  let startIdx = -1;
  let endIdx = events.length;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (
      ev.type === 'start_kyoku' &&
      ev.bakaze === kyokuKey.bakaze &&
      ev.kyoku === kyokuKey.kyoku &&
      ev.honba === kyokuKey.honba
    ) {
      startIdx = i;
      continue;
    }
    if (startIdx >= 0 && i > startIdx && ev.type === 'start_kyoku') {
      endIdx = i;
      break;
    }
  }
  return startIdx >= 0 ? { startIdx, endIdx } : null;
}

function sameKyoku(a: DecisionLogEntry, b: DecisionLogEntry): boolean {
  return (
    a.kyoku_key?.bakaze === b.kyoku_key?.bakaze
    && a.kyoku_key?.kyoku === b.kyoku_key?.kyoku
    && a.kyoku_key?.honba === b.kyoku_key?.honba
  );
}

function actionStr(action: Action): string {
  const pai = action.pai ? ` ${action.pai}` : '';
  const consumed = action.consumed?.length ? ` [${action.consumed.join('')}]` : '';
  return `${action.type}${pai}${consumed} by ${action.actor}`;
}

export interface ReplayHandsBoundary {
  /** kyokuEvents 内局部索引：当前 entry 的 gt_action 实际事件（含） */
  actionIndex: number;
  aligned: boolean;
  actionEvent?: ReplayEvent;
}

/**
 * 确定当前 entry 在原始 events 中的动作事件边界。
 * 优先 source_event_index，但必须验证事件与动作匹配（P2-2）；
 * 缺失或失配时走前后 obs 边界内的 legacy 窗口匹配。
 * 无法对齐时返回 null 并输出诊断。
 */
export function resolveEntryEventBoundary(
  kyokuEvents: ReplayEvent[],
  rangeStartIdx: number,
  data: ReplayData,
  entry: DecisionLogEntry,
): ReplayHandsBoundary | null {
  const action = getActualReplayAction(entry);
  if (!action) return { actionIndex: -1, aligned: true };

  // 无事件动作（pass/none）：停在最近的 obs 边界（该步不产生新事件）
  if (action.type === 'none') {
    const log = data.log;
    const entryIdx = log.indexOf(entry);
    for (let i = entryIdx - 1; i >= 0; i--) {
      const prev = log[i];
      if (sameKyoku(prev, entry) && typeof prev.source_event_index === 'number') {
        return { actionIndex: prev.source_event_index - rangeStartIdx, aligned: true };
      }
    }
    return { actionIndex: 0, aligned: true };
  }

  const sei = entry.source_event_index;
  if (typeof sei === 'number') {
    const local = sei - rangeStartIdx;
    if (local >= 1 && local < kyokuEvents.length && eventMatchesAction(kyokuEvents[local], action)) {
      return { actionIndex: local, aligned: true, actionEvent: kyokuEvents[local] };
    }
    if (typeof console !== 'undefined') {
      console.error(
        `[replayHands] event boundary diagnostic: entry step=${entry.step} sei=${sei} 指向的事件与动作不匹配`
        + `（${action.type} ${action.pai ?? ''} by ${action.actor}），尝试 legacy 窗口匹配`,
      );
    }
  }

  // legacy：前后 obs 边界（带 sei 的相邻 entry）之间匹配动作事件
  const log = data.log;
  const entryIdx = log.indexOf(entry);
  let prevSei: number | null = null;
  for (let i = entryIdx - 1; i >= 0; i--) {
    const prev = log[i];
    if (sameKyoku(prev, entry) && typeof prev.source_event_index === 'number') { prevSei = prev.source_event_index; break; }
  }
  let nextSei: number | null = null;
  for (let i = entryIdx + 1; i < log.length; i++) {
    const next = log[i];
    if (sameKyoku(next, entry) && typeof next.source_event_index === 'number') { nextSei = next.source_event_index; break; }
  }
  const fromLocal = (prevSei ?? rangeStartIdx) - rangeStartIdx + 1;
  const toLocal = (nextSei ?? rangeStartIdx + kyokuEvents.length - 1) - rangeStartIdx;
  for (let i = fromLocal; i <= toLocal; i++) {
    if (eventMatchesAction(kyokuEvents[i], action)) {
      return { actionIndex: i, aligned: true, actionEvent: kyokuEvents[i] };
    }
  }
  if (typeof console !== 'undefined') {
    console.error(
      `[replayHands] alignment diagnostic: entry step=${entry.step} action=${actionStr(action)} `
      + `source_event_index=${sei ?? 'MISSING'} — 未能在事件窗口 [${fromLocal + rangeStartIdx}, ${toLocal + rangeStartIdx}] 定位动作事件`,
    );
  }
  return null;
}

function isTurnAdvancingEvent(ev: ReplayEvent): boolean {
  const type = String(ev.type ?? '');
  return type === 'tsumo' || type === 'dahai' || type === 'chi' || type === 'pon'
    || type === 'daiminkan' || type === 'ankan';
}

/**
 * 重建四家手牌。每步从稳定事件边界重建，不再累积 cursor。
 * 仅用于对手 reveal / 终局牌姿 / 调试；自家棋盘以 decision snapshot 为权威。
 */
export function buildReplayHandsForBoard(
  events: ReplayEvent[] | null,
  data: ReplayData | null,
  currentEntry: ReplayData['log'][number] | null,
  boardPhase: ReplayBoardPhase,
): string[][] | null {
  if (!events || !data || !currentEntry) return null;
  const range = findKyokuEventRange(events, currentEntry);
  if (!range) return null;

  const kyokuEvents = events.slice(range.startIdx, range.endIdx);
  const startEv = kyokuEvents[0];
  const hands = ((startEv.tehais as string[][] | undefined) ?? [[], [], [], []]).map((tiles) => [...tiles]);

  const boundary = resolveEntryEventBoundary(kyokuEvents, range.startIdx, data, currentEntry);
  if (!boundary) return null; // 对齐失败：输出诊断后不返回陈旧手牌

  const action = getActualReplayAction(currentEntry);
  if (!action) {
    // 无实际动作（gt_action 缺失且 chosen 非 none）：无可靠事件边界可重建，
    // 未知不能伪装成开局配牌 → 返回 null（P2-1）。
    return null;
  }

  if (action.type === 'none') {
    // none/pass：该步不产生新事件，但手牌应反映"当前时点"——
    // 应用 start_kyoku 到最近事件边界的全部既有事件，而不是返回开局配牌。
    const endIdx = Math.min(Math.max(boundary.actionIndex, 0), kyokuEvents.length - 1);
    for (let i = 1; i <= endIdx; i++) applyReplayEventToHands(hands, kyokuEvents[i]);
    return hands.map((tiles) => [...tiles]);
  }

  if (boundary.actionIndex < 1) {
    return hands.map((tiles) => [...tiles]);
  }

  const endIdx = boardPhase === 'post' ? boundary.actionIndex : boundary.actionIndex - 1;
  for (let i = 1; i <= endIdx; i++) applyReplayEventToHands(hands, kyokuEvents[i]);

  // K5：post 时继续吞掉 kakan_accepted，让加杠牌离开暗手，与 adapter 的"kakan 立即完成"一致。
  if (boardPhase === 'post' && boundary.actionEvent?.type === 'kakan') {
    for (let i = boundary.actionIndex + 1; i < kyokuEvents.length; i++) {
      const ev = kyokuEvents[i];
      if (ev.type === 'kakan_accepted' && Number(ev.actor ?? -1) === Number(action.actor ?? -1)) {
        applyReplayEventToHands(hands, ev);
        break;
      }
      if (isTurnAdvancingEvent(ev)) break;
    }
  }

  return hands.map((tiles) => [...tiles]);
}

// ---------------------------------------------------------------------------
// first-divergence checker
// ---------------------------------------------------------------------------

function normalizedMultisetKey(tiles: string[]): string {
  return [...tiles].sort().join('|');
}

/** 把动作应用到 (hand + tsumo) 的多重集，得到动作后手牌（用于与 raw-event 重建对比）。 */
function applyActionToMultiset(tiles: string[], action: Action): string[] {
  const hand = [...tiles];
  if (action.type === 'dahai') {
    removeTileOnce(hand, action.pai);
    return hand;
  }
  if (action.type === 'reach') {
    return hand; // reach 事件本身不改变手牌（随之的 dahai 才移除）
  }
  if (action.type === 'kakan') {
    removeTileOnce(hand, action.pai); // 加杠牌（恒等于 pai）进入 meld
    return hand;
  }
  if (['chi', 'pon', 'daiminkan', 'ankan'].includes(action.type ?? '')) {
    for (const tile of action.consumed ?? []) removeTileOnce(hand, tile);
    return hand;
  }
  return hand;
}

export interface HandDivergence {
  step: number;
  sourceEventIndex: number | null;
  phase: ReplayBoardPhase;
  expectedEvent: string;
  actualEvent: string;
  decisionHand: string[];
  rebuiltHand: string[];
  viewPlayerId: number;
}

/**
 * 逐 entry 比较 decision snapshot hand/tsumo、raw-event rebuilt hand 与
 * source_event_index 所指 action，返回第一处不一致。
 */
export function findFirstHandDivergence(
  events: ReplayEvent[] | null,
  data: ReplayData | null,
  viewPlayerId: number,
): HandDivergence | null {
  if (!events || !data) return null;
  for (let step = 0; step < data.log.length; step++) {
    const entry = data.log[step];
    const action = getActualReplayAction(entry);
    if (!action || action.type === 'none') continue;

    const sei = entry.source_event_index;
    if (sei != null) {
      const ev = events[sei];
      if (!ev || !eventMatchesAction(ev, action)) {
        return {
          step,
          sourceEventIndex: sei,
          phase: 'pre',
          expectedEvent: actionStr(action),
          actualEvent: ev ? `${ev.type ?? '?'} ${String(ev.pai ?? '')}` : '(no event)',
          decisionHand: entry.hand ?? [],
          rebuiltHand: [],
          viewPlayerId,
        };
      }
    }

    for (const phase of (['pre', 'post'] as const)) {
      const rebuilt = buildReplayHandsForBoard(events, data, entry, phase);
      if (!rebuilt) continue;
      const decisionHand = [...(entry.hand ?? [])];
      // 决策快照恒为动作前：hand(13) + tsumo_pai。pre 直接比较；post 在 hand+tsumo 上应用动作。
      const expected = [...decisionHand, ...(entry.tsumo_pai ? [entry.tsumo_pai] : [])];
      const target = phase === 'post'
        ? (action.actor === viewPlayerId ? applyActionToMultiset(expected, action) : expected)
        : expected;
      const rebuiltView = rebuilt[viewPlayerId] ?? [];
      if (normalizedMultisetKey(target) !== normalizedMultisetKey(rebuiltView)) {
        return {
          step,
          sourceEventIndex: sei ?? null,
          phase,
          expectedEvent: actionStr(action),
          actualEvent: 'hand-mismatch',
          decisionHand,
          rebuiltHand: rebuiltView,
          viewPlayerId,
        };
      }
    }
  }
  return null;
}
