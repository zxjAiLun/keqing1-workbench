// src/replay_ui/src/utils/reviewComparable.ts
//
// Review 统计与「上一差异 / 下一差异」共用的「可比决策」口径。
//
// 历史问题：差异导航会跳过立直后的强制摸切，统计弹窗不会 —— 于是「统计说有 N 处
// 不一致」和「按钮只能跳到 M 处」永远对不上。所有需要判断"这一手算不算一次决策"
// 的地方都必须用这里的函数，不要再各自写一套。
import { getActualReplayAction } from './replayAdapter.ts';
import { isReplayPlayerDecision } from './tileUtils.ts';
import type { DecisionLogEntry } from '../types/replay';

// 响应窗口里"过"的另一侧选项：除了鸣牌，还有**荣和**（别人打出的牌可以选择不荣和）。
const RESPONSE_OPTION_TYPES = new Set(['chi', 'pon', 'daiminkan', 'ankan', 'kakan', 'hora']);

/** 立直后的强制摸切：不是真实选择，不计入 Review 比较。 */
export function isForcedRiichiTsumogiriEntry(
  entry: DecisionLogEntry | null | undefined,
): boolean {
  const action = getActualReplayAction(entry);
  return Boolean(
    entry
    && !entry.is_obs
    && action?.type === 'dahai'
    && action.tsumogiri
    && action.actor !== undefined
    && entry.reached?.[action.actor],
  );
}

/**
 * 真实响应窗口里的「过」：候选里同时存在 `none` 与至少一个响应选项（鸣牌**或荣和**）。
 *
 * 这种"过"是一次真实决策（模型可能想吃/碰/杠/荣和），必须参与一致率与差异统计；
 * 只有完全没有选择余地的自动过才应被排除。
 */
export function isReplayResponsePassOpportunity(
  entry: DecisionLogEntry | null | undefined,
): boolean {
  let hasNone = false;
  let hasOption = false;
  for (const candidate of entry?.candidates ?? []) {
    const type = candidate?.action?.type;
    if (type === 'none') hasNone = true;
    else if (type && RESPONSE_OPTION_TYPES.has(type)) hasOption = true;
    if (hasNone && hasOption) return true;
  }
  return false;
}

/**
 * 强制无选择步（must-do）：
 * 1. 立直后的强制摸切（isForcedRiichiTsumogiriEntry）；
 * 2. 候选动作不超过 1 个（例如立直宣言仅 1 牌可打以维持听牌、或无分支的强制动作）。
 * 这种步骤属于必须执行动作（must-do），无可选分支，不是真实决策，
 * UI 中不展示 Q 值对比/条形图，不计入 Review 统计（不进入 Total / Match）。
 */
export function isForcedActionEntry(
  entry: DecisionLogEntry | null | undefined,
): boolean {
  if (!entry || entry.is_obs) return false;
  if (isForcedRiichiTsumogiriEntry(entry)) return true;
  const cands = entry.candidates;
  if (cands && cands.length === 1) return true;
  return false;
}

/**
 * 这一手是否属于「有意义的 Review 比较决策」。
 *
 * 排除：他家观察步、被更高优先级动作截断的响应窗口（comparison_exempt）、
 * 强制无选择步（立直后强制摸切、单候选宣言打牌等）。真实响应窗口的「过」必须保留。
 */
export function isReplayReviewComparableEntry(
  entry: DecisionLogEntry | null | undefined,
  playerId: number,
): boolean {
  if (!entry) return false;
  if (!isReplayPlayerDecision(entry, playerId)) return false;
  if (entry.comparison_exempt) return false;
  return !isForcedActionEntry(entry);
}
