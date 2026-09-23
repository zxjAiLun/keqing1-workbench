// src/replay_ui/scripts/checkReviewDiffSemantics.ts
//
// Review 前端语义的**最小**回归（只锁 Python 侧覆盖不到的行为）：
//   F1  activeTeacherModel 不得跨模型 fallback（该步没有它的 review → 不算差异）
//   F2  Stats 与「上一差异 / 下一差异」共用 comparable-decision 口径
//       （立直后强制摸切排除；真实响应窗口的"过"保留 —— 后者口径归 Python/报告侧）
//   F3  similarity = clamp01(actualProb / expectedProb) 的代表数值（不是 1-|Δp|）
//   F4  恶手率分母 = 有概率数据的决策数（不是 total）
//
// 已由 Python 覆盖、因此这里**不重复**：chosen 不被事件覆盖、pass-vs-call 进入
// teacher report、strict checkpoint（V2 不 alias 到 70k）、expected_prob/actual_prob
// 落库（见 tests/test_replay_review_correctness.py）。
import { isReplayReviewDiffForPlayer } from '../src/utils/tileUtils.ts';
import { isForcedActionEntry, isForcedRiichiTsumogiriEntry, isReplayReviewComparableEntry } from '../src/utils/reviewComparable.ts';
import { computeReviewModelStats } from '../src/utils/reviewStats.ts';
import type { DecisionLogEntry } from '../src/types/replay.ts';

const P0 = 0;

let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

function dahai(pai: string, tsumogiri = false) {
  return { type: 'dahai', actor: P0, pai, tsumogiri };
}

function pon(pai: string) {
  return { type: 'pon', actor: P0, pai, target: 2 };
}

function review(over: Record<string, unknown>) {
  return { model: 'A', is_equal: false, ...over };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function asEntry(raw: Record<string, unknown>): DecisionLogEntry {
  return raw as any;
}

// --- F1：active teacher model 严格语义 --------------------------------------
const bothModels = asEntry({
  step: 10,
  is_obs: false,
  actor_to_move: P0,
  chosen: dahai('1p'),
  gt_action: dahai('1p'),
  candidates: [],
  teacher_reviews: [
    review({ model: 'A', actual_action: dahai('1p'), expected_action: dahai('1p'), is_equal: true }),
    review({ model: 'B', actual_action: dahai('1p'), expected_action: dahai('9s'), is_equal: false }),
  ],
});
check(isReplayReviewDiffForPlayer(bothModels, P0, 'B') === true, 'F1: 选 B 时 B 的分歧算差异');
check(isReplayReviewDiffForPlayer(bothModels, P0, 'A') === false, 'F1: 选 A 时 A 的一致不算差异');

const onlyModelA = asEntry({
  step: 11,
  is_obs: false,
  actor_to_move: P0,
  chosen: dahai('1p'),
  gt_action: dahai('1p'),
  candidates: [],
  teacher_reviews: [
    review({ model: 'A', actual_action: dahai('1p'), expected_action: dahai('9s'), is_equal: false }),
  ],
});
check(
  isReplayReviewDiffForPlayer(onlyModelA, P0, 'B') === false,
  'F1: 该步没有 B 的 review 时必须返回 false（不得用 A 顶替）',
);
check(isReplayReviewDiffForPlayer(onlyModelA, P0, 'A') === true, 'F1: 有 A 的 review 时仍按 A 判定');

// --- F2：comparable 口径（统计与导航同源） ----------------------------------
const forcedTsumogiri = asEntry({
  step: 14,
  is_obs: false,
  actor_to_move: P0,
  reached: [true, false, false, false],
  chosen: dahai('3m', true),
  gt_action: dahai('3m', true),
  candidates: [],
  teacher_reviews: [
    review({ model: 'A', actual_action: dahai('3m', true), expected_action: dahai('3m', true), is_equal: true }),
  ],
});
check(isForcedRiichiTsumogiriEntry(forcedTsumogiri) === true, 'F2: 立直后摸切应被识别');
check(isForcedActionEntry(forcedTsumogiri) === true, 'F2: 强制摸切应属于 forced action');
check(isReplayReviewComparableEntry(forcedTsumogiri, P0) === false, 'F2: 强制摸切不得计入统计');
const forcedOnly = computeReviewModelStats([forcedTsumogiri], P0);
check(
  forcedOnly.every((row) => row.total === 0),
  'F2: 强制摸切不得进入统计分母',
);

const singleCandidateReachDiscard = asEntry({
  step: 141,
  is_obs: false,
  actor_to_move: P0,
  reached: [false, false, false, false],
  chosen: dahai('5m', true),
  gt_action: dahai('5m', true),
  candidates: [{ action: dahai('5m', true) }],
  teacher_reviews: [
    review({ model: 'A', actual_action: dahai('5m', true), expected_action: dahai('5m', true), is_equal: true }),
  ],
});
check(isForcedActionEntry(singleCandidateReachDiscard) === true, 'F2: 单候选 must-do 动作应被识别为 forced action');
check(isReplayReviewComparableEntry(singleCandidateReachDiscard, P0) === false, 'F2: 单候选 must-do 动作不得计入统计分母');
const singleCandOnly = computeReviewModelStats([singleCandidateReachDiscard], P0);
check(
  singleCandOnly.every((row) => row.total === 0),
  'F2: 单候选 must-do 动作不得进入统计分母',
);

const realPass = asEntry({
  step: 15,
  is_obs: false,
  actor_to_move: P0,
  chosen: pon('5m'),
  gt_action: { type: 'none', actor: P0 },
  candidates: [
    { action: { type: 'none', actor: P0 } },
    { action: pon('5m') },
  ],
  teacher_reviews: [
    review({ model: 'A', actual_action: { type: 'none', actor: P0 }, expected_action: pon('5m'), is_equal: false }),
  ],
});
check(isReplayReviewComparableEntry(realPass, P0) === true, 'F2: 真实响应窗口的"过"必须可比');
check(isReplayReviewDiffForPlayer(realPass, P0, 'A') === true, 'F2: 玩家过 / 模型想碰 → 差异可达');

// `none + hora`（玩家过 / 模型想荣和）属于同一类真实响应窗口，后端 join 后同步加入。
// 这里用 legacy 形状（gt_action 缺失）锁前端自己的丢掉路径：isImplicitPass 不得把它当"自动过"。
const horaPass = asEntry({
  step: 16,
  is_obs: false,
  actor_to_move: P0,
  chosen: null,
  gt_action: null,
  candidates: [
    { action: { type: 'none', actor: P0 } },
    { action: { type: 'hora', actor: P0, target: 2, pai: '5m' } },
  ],
  teacher_reviews: [
    review({
      model: 'A',
      actual_action: { type: 'none', actor: P0 },
      expected_action: { type: 'hora', actor: P0, target: 2, pai: '5m' },
      is_equal: false,
    }),
  ],
});
check(isReplayReviewComparableEntry(horaPass, P0) === true, 'F2: 玩家过 / 模型想荣和 必须可比');
check(isReplayReviewDiffForPlayer(horaPass, P0, 'A') === true, 'F2: 荣和漏报（none vs hora）必须可达');
const horaRows = computeReviewModelStats([horaPass], P0);
check(
  horaRows.length === 1 && horaRows[0].total === 1,
  `F2: 玩家过 / 模型想荣和 不得被当成"自动过"丢弃（得到 total=${horaRows[0]?.total}）`,
);

// --- F3 / F4：统计公式 ------------------------------------------------------
const probScoredBad = asEntry({
  step: 20,
  is_obs: false,
  actor_to_move: P0,
  chosen: pon('5m'),
  gt_action: { type: 'none', actor: P0 },
  candidates: [],
  teacher_reviews: [
    review({
      model: 'A',
      actual_action: { type: 'none', actor: P0 },
      expected_action: pon('5m'),
      actual_prob: 0.04,
      expected_prob: 0.2,
      is_equal: false,
    }),
    review({
      model: 'B',
      actual_action: { type: 'none', actor: P0 },
      expected_action: { type: 'none', actor: P0 },
      actual_prob: 0.04,
      expected_prob: 0.9,
      is_equal: true,
    }),
  ],
});
const noProb = asEntry({
  step: 21,
  is_obs: false,
  actor_to_move: P0,
  chosen: dahai('1p'),
  gt_action: dahai('1p'),
  candidates: [],
  teacher_reviews: [review({ model: 'A', actual_action: dahai('1p'), expected_action: dahai('9s'), is_equal: false })],
});

const rows = computeReviewModelStats([probScoredBad, noProb], P0);
const rowA = rows.find((row) => row.model === 'A');
const rowB = rows.find((row) => row.model === 'B');
check(rows.length === 2, `F3/F4: 两个模型必须各自成行（得到 ${rows.length}）`);
if (rowA && rowB) {
  check(
    rowA.similarity !== null && Math.abs(rowA.similarity - 20) < 1e-6,
    `F3: A 类似度应为 0.04/0.20 = 20.0%（得到 ${rowA.similarity}）—— 1-|Δp| 会给出 84%`,
  );
  check(
    rowB.similarity !== null && Math.abs(rowB.similarity - 100) < 1e-6,
    `F3: expected==actual 时类似度为 100%（得到 ${rowB.similarity}）`,
  );
  check(rowA.match === 0 && rowA.total === 2, `F3/F4: A 应 0/2（得到 ${rowA.match}/${rowA.total}）`);
  check(
    rowA.badMoveRate !== null && Math.abs(rowA.badMoveRate - 100) < 1e-6,
    `F4: A 恶手率分母只算有概率数据的 1 步 → 100%（得到 ${rowA.badMoveRate}）`,
  );
}

// --- F5：策略梯度端点（action_score）的 Q 差值量不适用 ----------------------
// 服务端对 action_score 模型把 q_loss 置空（见 tests/test_p4m11_u32_candidate.py）。
// 这里锁前端自己的底线：即使拿到一份陈旧的、仍带 q_loss 的报告，也不得把 Q 差值
// 当成"错误严重度"；Rating（实际动作在 Q 分布中的归一化位置）同样判为不适用。
// expected_action 置空是为了让流程真正走到 q_loss 那一个分支，而不是被"一致"早退。
const scoreSemanticsCase = (semantics: string) => asEntry({
  step: 20,
  is_obs: false,
  actor_to_move: P0,
  chosen: dahai('1p'),
  gt_action: dahai('1p'),
  candidates: [
    { action: dahai('1p'), final_score: -5, teachers: [{ model: 'A', q_value: -5, prob: 0.6 }] },
    { action: dahai('9s'), final_score: -1, teachers: [{ model: 'A', q_value: -1, prob: 0.4 }] },
  ],
  teacher_reviews: [
    review({
      model: 'A',
      actual_action: dahai('1p'),
      expected_action: null,
      top1: null,
      is_equal: true,
      score_semantics: semantics,
      q_loss: 4,
      actual_q: -5,
    }),
  ],
});

check(
  isReplayReviewDiffForPlayer(scoreSemanticsCase('calibrated_q'), P0, 'A') === true,
  'F5 对照: calibrated_q 的 q_loss 必须仍算差异（否则这条锁的是空行为）',
);
check(
  isReplayReviewDiffForPlayer(scoreSemanticsCase('action_score'), P0, 'A') === false,
  'F5: action_score 模型的 q_loss 不得产生"错误严重度"差异',
);

const calibratedRows = computeReviewModelStats([scoreSemanticsCase('calibrated_q')], P0);
const actionScoreRows = computeReviewModelStats([scoreSemanticsCase('action_score')], P0);
check(
  calibratedRows.every((row) => row.rating !== null && row.ratingNotApplicable === false),
  'F5 对照: calibrated_q 的 Rating 必须仍给出数值',
);
check(
  actionScoreRows.every((row) => row.rating === null && row.ratingNotApplicable === true),
  'F5: action_score 模型的 Rating 必须判为不适用，而不是换一个数字继续显示',
);

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exitCode = 1;
} else {
  console.log('review diff semantics OK (F1-F5)');
}
