// src/replay_ui/src/utils/candidateProbability.ts
//
// 候选动作概率的唯一口径。
//
// 后端会在每个 candidate 上写 `prob`（用 tau=0.1 对 final_score 做 softmax）。
// 但有些产物（旧 decisions.json、教师报告、外部报告）没有该字段，前端于是各自
// 写了一个 fallback softmax —— 历史上这些 fallback 用的是隐式 tau=1，比后端和
// Mortal 官方站点扁平得多，导致同一手牌「有 prob 的候选」和「走 fallback 的候选」
// 概率尺度不一致。
//
// 现在统一到这里：温度与后端 `src/inference/review.py`
// 的 DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE 保持一致（0.1）。
//
// 为什么是 0.1：Mortal 官方 review 站点（mjai.ekyu.moe）报告的 details[].prob
// 就是用 tau=0.1 对 q_value 做 softmax；从该站点报告反解温度，72/72 个决策条目
// 的估计值均为 0.10000。改这里之前请先确认后端常量。
export const CANDIDATE_SOFTMAX_TEMPERATURE = 0.1;

/** 候选打分优先级：final_score → beam_score → logit（与后端 candidate_score 一致）。 */
export function candidateScore(c: {
  logit?: number;
  beam_score?: number;
  final_score?: number;
}): number {
  return c.final_score ?? c.beam_score ?? c.logit ?? Number.NEGATIVE_INFINITY;
}

/**
 * 对候选打分做 softmax，得到与后端 / Mortal 站点同尺度的概率。
 *
 * 数值稳定：先减去最大值再取指数。
 */
export function softmaxProbabilities(
  scores: number[],
  temperature: number = CANDIDATE_SOFTMAX_TEMPERATURE,
): number[] {
  if (scores.length === 0) return [];
  const tau = Number.isFinite(temperature) && temperature > 0
    ? temperature
    : CANDIDATE_SOFTMAX_TEMPERATURE;
  const finite = scores.filter((s) => Number.isFinite(s));
  if (finite.length === 0) return scores.map(() => 0);
  const maxScore = Math.max(...finite);
  const exps = scores.map((score) => (
    Number.isFinite(score) ? Math.exp((score - maxScore) / tau) : 0
  ));
  const total = exps.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) return scores.map(() => 0);
  return exps.map((value) => value / total);
}

/**
 * 候选概率：优先用后端写好的 `prob`，缺失时按同一温度回退计算。
 *
 * 注意 fallback 必须用**完整候选集**做 softmax（后端也是在整个动作空间上算的）；
 * 只取展示用子集会让分母偏小、概率偏大。
 */
export function candidateProbabilities<
  T extends { logit?: number; beam_score?: number; final_score?: number; prob?: number },
>(candidates: T[]): number[] {
  const fallback = softmaxProbabilities(candidates.map(candidateScore));
  return candidates.map((candidate, idx) => (
    typeof candidate.prob === 'number' && Number.isFinite(candidate.prob)
      ? candidate.prob
      : fallback[idx] ?? 0
  ));
}
