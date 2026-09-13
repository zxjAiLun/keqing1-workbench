// src/replay_ui/src/utils/reviewStats.ts
//
// 每个 Review 模型自己的统计（类似度 / 一致率 / 恶手率 / Rating）。
// 从 ReplayStatsDialog 内联算法提取为纯函数，便于锁行为；算法与原实现一致，
// 只修三处口径问题：
//   1. 输入口径与差异导航统一：内部用 isReplayReviewComparableEntry 过滤
//   2. 类似度 = actualProb / expectedProb（clamp 0..1）。原来的 1 - |Δp| 在
//      "模型首选 20% / 实际 15%" 这种明显分歧下也给出 95%，天然贴 100%；
//      比值语义更直接："实际动作在模型看来相对模型首选有多强"。
//   3. 恶手率分母 = 真正带概率数据的决策数（原实现用 total，系统性偏低）。
import { isReplayResponsePassOpportunity, isReplayReviewComparableEntry } from './reviewComparable.ts';
import { sameReplayAction } from './tileUtils.ts';
import type { DecisionLogEntry, TeacherReviewEntry } from '../types/replay';

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export type ReviewModelStatsRow = {
  model: string;
  total: number;
  match: number;
  badMove: number;
  /** 有完整概率数据、真正进入恶手率分母的决策数。 */
  probScored: number;
  pct: number;
  similarity: number | null;
  rating: number | null;
  badMoveRate: number | null;
};

/** 模型展示顺序：main bot 家族优先，其余按名称排在后面。 */
const MODEL_ORDER: Record<string, number> = {
  ext_mortal: 0,
  '70k': 1,
  'V2 candidate': 2,
};

/**
 * 按 `review.model` 分组统计。
 *
 * 内部先用 `isReplayReviewComparableEntry` 过滤 —— 与「上一差异 / 下一差异」共用
 * 同一口径（剔除 obs / comparison_exempt / 立直后强制摸切），避免"统计的
 * mismatch 数"和"按钮能到达的差异数"对不上。真实响应窗口的"过"必须保留。
 */
export function computeReviewModelStats(
  log: DecisionLogEntry[],
  playerId: number,
): ReviewModelStatsRow[] {
  const entries = log.filter((entry) => isReplayReviewComparableEntry(entry, playerId));
  const stats = new Map<string, {
    model: string;
    total: number;
    match: number;
    badMove: number;
    probScored: number;
    ratingScores: number[];
    similarityScores: number[];
  }>();

  const ensure = (model: string) => {
    const key = model || 'model';
    let item = stats.get(key);
    if (!item) {
      item = { model: key, total: 0, match: 0, badMove: 0, probScored: 0, ratingScores: [], similarityScores: [] };
      stats.set(key, item);
    }
    return item;
  };

  for (const entry of entries) {
    const reviews: TeacherReviewEntry[] = entry.teacher_reviews && entry.teacher_reviews.length > 0
      ? entry.teacher_reviews
      : entry.teacher_review ? [entry.teacher_review] : [];
    for (const review of reviews) {
      const item = ensure(review.model || 'model');
      const actual = review.actual_action ?? entry.gt_action;
      const expected = review.expected_action ?? review.top1?.action ?? null;
      // 只有"完全没有选择余地的自动过"才剔除；真实响应窗口的"过"是决策。
      const isImplicitPass = entry.gt_action == null
        && actual?.type === 'none'
        && !isReplayResponsePassOpportunity(entry);
      if (actual && expected && !isImplicitPass) {
        item.total += 1;
        if (sameReplayAction(expected, actual)) item.match += 1;
      }

      const qValues: number[] = [];
      let actualQ = finiteNumber(review.actual_q);
      let actualProb = finiteNumber(review.actual_prob);
      let expectedProb = finiteNumber(review.expected_prob ?? review.top1?.prob ?? review.best_prob);
      for (const candidate of entry.candidates ?? []) {
        const teachers = candidate.teachers ?? (candidate.teacher ? [candidate.teacher] : []);
        const teacher = teachers.find((value) => value.model === review.model);
        const q = finiteNumber(teacher?.q_value);
        const prob = finiteNumber(teacher?.prob);
        if (q !== null) {
          qValues.push(q);
          if (actualQ === null && sameReplayAction(candidate.action, actual)) {
            actualQ = q;
          }
        }
        if (prob !== null) {
          if (actualProb === null && sameReplayAction(candidate.action, actual)) {
            actualProb = prob;
          }
          if (expected && expectedProb === null && sameReplayAction(candidate.action, expected)) {
            expectedProb = prob;
          }
        }
      }

      const expectedMatchesActual = Boolean(expected && actual && sameReplayAction(expected, actual));
      if (actual && expected && actualProb !== null && expectedProb !== null) {
        item.probScored += 1;
        if (expectedProb > 0) {
          item.similarityScores.push(clamp01(expectedMatchesActual ? 1 : actualProb / expectedProb));
        }
        if (!expectedMatchesActual && actualProb < 0.05) {
          item.badMove += 1;
        }
      }

      if (actualQ === null || qValues.length < 2) continue;
      if (!qValues.some((value) => value === actualQ)) qValues.push(actualQ);
      const minQ = Math.min(...qValues);
      const maxQ = Math.max(...qValues);
      const range = maxQ - minQ;
      if (range <= 0) continue;
      item.ratingScores.push((actualQ - minQ) / range);
    }
  }

  return Array.from(stats.values()).map((item) => {
    const pct = item.total ? item.match / item.total * 100 : 0;
    const rating = item.ratingScores.length
      ? Math.round(1000 * 100 * Math.pow(item.ratingScores.reduce((sum, value) => sum + value, 0) / item.ratingScores.length, 2)) / 1000
      : null;
    const similarity = item.similarityScores.length
      ? item.similarityScores.reduce((sum, value) => sum + value, 0) / item.similarityScores.length * 100
      : null;
    return {
      model: item.model,
      total: item.total,
      match: item.match,
      badMove: item.badMove,
      probScored: item.probScored,
      pct,
      rating: rating === null ? null : Math.round(rating * 10) / 10,
      similarity: similarity === null ? null : Math.round(similarity * 10) / 10,
      badMoveRate: item.probScored ? item.badMove / item.probScored * 100 : null,
    };
  }).sort((left, right) => (MODEL_ORDER[left.model] ?? 100) - (MODEL_ORDER[right.model] ?? 100));
}
