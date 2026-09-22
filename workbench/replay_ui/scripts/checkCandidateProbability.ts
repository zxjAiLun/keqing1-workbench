// 展示层候选概率温度回归。
//
// 契约：前端 softmax fallback 的温度必须与后端
// src/inference/review.py 的 DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE 一致，
// 且该值必须等于 Mortal 官方 review 站点使用的 tau=0.1。
//
// 历史问题：fallback 曾用隐式 tau=1，比后端和站点扁平得多
// （top1 实际 0.9998 被显示成 ~0.21），同一手牌概率尺度不一致。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import {
  CANDIDATE_SOFTMAX_TEMPERATURE,
  candidateScore,
  candidateProbabilities,
  softmaxProbabilities,
} from '../src/utils/candidateProbability.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..', '..');

// ---------------------------------------------------------------------------
// 1. 温度与后端常量一致
// ---------------------------------------------------------------------------

assert.equal(CANDIDATE_SOFTMAX_TEMPERATURE, 0.1, '展示层温度应为 0.1（Mortal 站点口径）');

const backendSource = readFileSync(
  resolve(REPO_ROOT, 'src', 'inference', 'review.py'),
  'utf-8',
);
const backendMatch = backendSource.match(
  /DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE\s*=\s*([0-9.]+)/,
);
assert.ok(backendMatch, '后端必须定义 DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE');
assert.equal(
  Number(backendMatch[1]),
  CANDIDATE_SOFTMAX_TEMPERATURE,
  '前端温度必须与后端 DEFAULT_CANDIDATE_SOFTMAX_TEMPERATURE 完全一致',
);

// ---------------------------------------------------------------------------
// 2. 用 Mortal 站点真实 (q_value, prob) 做锚点
// ---------------------------------------------------------------------------

// 取自 https://mjai.ekyu.moe/report/5d768382baf9fb3d.json 的一条 14 候选决策
// （完整候选集：站点在整动作空间上做 softmax，子集会让分母偏小）。
const MORTAL_DECISION: Array<[number, number]> = [
  [0.42897758, 0.84512913],
  [0.25926575, 0.15483673],
  [-0.6087061, 0.000026322063],
  [-0.803426, 0.0000037554519],
  [-0.92385817, 0.0000011262434],
  [-0.9646679, 7.488546e-7],
  [-0.98482275, 6.121621e-7],
  [-0.99718666, 5.4096637e-7],
  [-1.0741291, 2.5061868e-7],
  [-1.087764, 2.1867442e-7],
  [-1.0907362, 2.1227098e-7],
  [-1.1005564, 1.9241605e-7],
  [-1.1064153, 1.8146666e-7],
  [-1.1445965, 1.2387325e-7],
];

const mortalProbs = softmaxProbabilities(MORTAL_DECISION.map(([q]) => q));
MORTAL_DECISION.forEach(([, expected], index) => {
  const got = mortalProbs[index];
  assert.ok(
    Math.abs(got - expected) < 1e-6,
    `候选 ${index}: 复现概率 ${got} 与 Mortal 站点 ${expected} 不符（温度不是 0.1？）`,
  );
});

// 反向护栏：若温度退回 1.0，top1 概率会明显偏低，这里必须抓到。
const flatProbs = softmaxProbabilities(MORTAL_DECISION.map(([q]) => q), 1.0);
assert.ok(
  Math.abs(flatProbs[0] - MORTAL_DECISION[0][1]) > 0.1,
  '对照组失效：tau=1.0 本应给出明显不同的概率',
);

// ---------------------------------------------------------------------------
// 3. 后端已给 prob 时优先用后端值；缺失才回退
// ---------------------------------------------------------------------------

const withBackendProb = candidateProbabilities([
  { final_score: 2.0, prob: 0.9 },
  { final_score: 1.0, prob: 0.1 },
]);
assert.deepEqual(withBackendProb, [0.9, 0.1], '后端 prob 必须优先于本地 fallback');

const mixed = candidateProbabilities([
  { final_score: 0.42897758, prob: 0.84512913 },
  { final_score: 0.25926575 },
]);
assert.equal(mixed[0], 0.84512913, '有 prob 的候选用后端值');
// fallback 是在本组候选内做 softmax（与后端一致：后端也是在候选集内算）；
// 这里只验它与 tau=0.1 的自算结果一致，不断言等于 14 候选集的站点值。
const expectedFallback = softmaxProbabilities([0.42897758, 0.25926575]);
assert.ok(
  Math.abs(mixed[1] - expectedFallback[1]) < 1e-12,
  `缺 prob 的候选应按 tau=0.1 回退，实际 ${mixed[1]} 期望 ${expectedFallback[1]}`,
);
// 反向护栏：tau=1.0 会给出明显不同的值
const fallbackTau1 = softmaxProbabilities([0.42897758, 0.25926575], 1.0);
assert.ok(
  Math.abs(mixed[1] - fallbackTau1[1]) > 0.01,
  '对照组失效：tau=1.0 本应给出明显不同的回退概率',
);

// ---------------------------------------------------------------------------
// 4. 打分优先级与后端 candidate_score 一致：final_score → beam_score → logit
// ---------------------------------------------------------------------------

assert.equal(candidateScore({ final_score: 3, beam_score: 2, logit: 1 }), 3);
assert.equal(candidateScore({ beam_score: 2, logit: 1 }), 2);
assert.equal(candidateScore({ logit: 1 }), 1);

// ---------------------------------------------------------------------------
// 5. 边界：空/非法温度/非有限分数不应产生 NaN
// ---------------------------------------------------------------------------

assert.deepEqual(softmaxProbabilities([]), []);
assert.deepEqual(softmaxProbabilities([1, 2], 0), softmaxProbabilities([1, 2]), '非法温度回退默认');
assert.deepEqual(softmaxProbabilities([1, 2], Number.NaN), softmaxProbabilities([1, 2]));
const withInf = softmaxProbabilities([1, Number.NEGATIVE_INFINITY, 2]);
assert.equal(withInf[1], 0, '-inf 候选概率应为 0');
assert.ok(withInf.every((p) => Number.isFinite(p)), '不应出现 NaN');
assert.equal(
  softmaxProbabilities([1, 2]).reduce((a, b) => a + b, 0),
  1,
  '概率和必须为 1',
);

console.log('candidate probability OK (tau=0.1 aligned with backend + Mortal review site)');
