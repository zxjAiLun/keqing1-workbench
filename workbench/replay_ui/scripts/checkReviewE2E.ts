// src/replay_ui/scripts/checkReviewE2E.ts
// 端到端状态一致性回归：真实 API decisions.json + events.json + teacher overlay。
//
// 覆盖复审 R1/R2/R3/R6 与 Gate G1-G6、G10：
//   S1-S6：真实多巡自家决策 pre/post 无串味（replay_97508ebc，player 3）
//   G3：前 10 个本人打牌决策，decision snapshot 与 raw-event 重建无分歧
//   G4：pre/post/next/prev 往返无累计漂移
//   G5/R6：teacher overlay 不改变棋盘事实（A/B/C/D 字段一致）
//   G6：候选不再由 replayHands 静默过滤
//   G10：真实 kakan step 475（replay_54beeaed，player 0）pre/post 正确
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { entryToBattleState, buildLogitData, getActualReplayAction } from '../src/utils/replayAdapter.ts';
import { buildReplayHandsForBoard, findFirstHandDivergence, findKyokuEventRange, resolveEntryEventBoundary } from '../src/utils/replayHands.ts';
import type { DecisionLogEntry, ReplayData } from '../src/types/replay.ts';

const REPO = resolve(import.meta.dirname, '../../..');
const FIXTURE = resolve(REPO, 'fixtures/review_state');

let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function loadFixture(...parts: string[]): any {
  return JSON.parse(readFileSync(resolve(FIXTURE, ...parts), 'utf8'));
}

function multiset(tiles: string[]): string {
  return [...tiles].sort().join('|');
}

function sameMultiset(a: string[], b: string[]): boolean {
  return multiset(a) === multiset(b);
}

function findKyokuRangeStart(events: Record<string, unknown>[], entry: DecisionLogEntry): number {
  return findKyokuEventRange(events, entry)?.startIdx ?? 0;
}

function applyDahaiToHand(tiles: string[], pai?: string): string[] {
  const hand = [...tiles];
  if (!pai) return hand;
  const idx = hand.indexOf(pai);
  if (idx >= 0) {
    hand.splice(idx, 1);
    return hand;
  }
  const norm = pai.endsWith('r') ? pai.slice(0, 2) : pai;
  const normIdx = hand.findIndex((t) => (t.endsWith('r') ? t.slice(0, 2) : t) === norm);
  if (normIdx >= 0) hand.splice(normIdx, 1);
  return hand;
}

const BOARD_FACT_KEYS = [
  'hand', 'tsumo_pai', 'melds', 'discards', 'actor_to_move', 'last_discard',
  'gt_action', 'scores', 'reached', 'dora_markers',
];

// ---------------------------------------------------------------------------
// Fixture 1：replay_97508ebc_p3 多巡自家决策
// ---------------------------------------------------------------------------

const data = loadFixture('replay_97508ebc_p3/decisions.json') as ReplayData;
const events = loadFixture('replay_97508ebc_p3/events.json') as Record<string, unknown>[];
const names = data.player_names ?? ['P0', 'P1', 'P2', 'P3'];
const VIEW = 3;
const byStep = new Map<number, DecisionLogEntry>(data.log.map((e) => [e.step, e]));

check(data.log.length >= 20, `fixture 应包含完整第一小局决策（得到 ${data.log.length}）`);
check(data.player_id === VIEW, `fixture player_id 应为 ${VIEW}`);

// 每个有实际动作的 entry 都应带 source_event_index（backend bot.py 契约）
const missingSei = data.log.filter((e) => !e.is_obs && (e.gt_action ?? e.chosen)?.type !== 'none' && e.source_event_index == null);
check(missingSei.length === 0, `所有有动作的 entry 都应带 source_event_index（缺失 ${missingSei.length}）`);

// 预期状态来自 expected_states.json（由真实数据生成）
const expectedStates = loadFixture('replay_97508ebc_p3/expected_states.json');

function checkPre(stepKey: string): void {
  const spec = expectedStates.steps[stepKey];
  const entry = byStep.get(spec.step);
  check(Boolean(entry), `${stepKey}: 存在 step ${spec.step} entry`);
  if (!entry) return;
  const state = entryToBattleState(entry, names, VIEW, 'pre');
  const actualAction = entry.gt_action ?? entry.chosen;
  check(
    sameMultiset(state.hand, spec.hand) && (state.tsumo_pai ?? null) === (spec.tsumo_pai ?? null),
    `${stepKey} pre: hand/tsumo 应等于真实快照（hand=${state.hand.join('')} tsumo=${state.tsumo_pai}）`,
  );
  check(
    (actualAction?.type === spec.actual?.type && actualAction?.pai === spec.actual?.pai),
    `${stepKey} pre: actual 应为 ${spec.actual?.type} ${spec.actual?.pai}`,
  );
}

function checkPostCrossStep(step: number, nextStep: number, label: string): void {
  const entry = byStep.get(step);
  const next = byStep.get(nextStep);
  check(Boolean(entry && next), `${label}: 存在 step ${step}/${nextStep} entry`);
  if (!entry || !next) return;
  const state = entryToBattleState(entry, names, VIEW, 'post');
  const nextHand = next.hand ?? [];
  // post 应等于下一 obs entry 的 view 手牌（真实牌谱交叉验证）
  check(
    sameMultiset(state.hand, nextHand),
    `${label} post: 手牌应等于下一 entry 快照（post=${state.hand.join('')} next=${nextHand.join('')}）`,
  );
  check(state.tsumo_pai === null, `${label} post: tsumo_pai 应为 null`);
}

// S1/S2：第一巡（step 3）摸 5s 打 W
checkPre('S1_step3_pre');
checkPostCrossStep(3, 4, 'S2 第一巡');

// S3/S4：第二巡（step 8）摸 F 打 P
checkPre('S3_step8_pre');
checkPostCrossStep(8, 9, 'S4 第二巡');

// S5/S6：复审复现 step 30 摸 6s 打 E
checkPre('S5_step30_pre');
checkPostCrossStep(30, 31, 'S6 step30');

// G4：pre/post 往返无漂移（任意切换不累积）
for (const step of [3, 8, 30]) {
  const entry = byStep.get(step);
  if (!entry) continue;
  const a = entryToBattleState(entry, names, VIEW, 'pre');
  const b = entryToBattleState(entry, names, VIEW, 'post');
  const c = entryToBattleState(entry, names, VIEW, 'pre');
  const d = entryToBattleState(entry, names, VIEW, 'post');
  check(
    sameMultiset(a.hand, c.hand) && a.tsumo_pai === c.tsumo_pai,
    `G4 step ${step}: pre 往返无漂移`,
  );
  check(
    sameMultiset(b.hand, d.hand) && b.tsumo_pai === d.tsumo_pai,
    `G4 step ${step}: post 往返无漂移`,
  );
}

// G3：前 10 个本人打牌决策，decision snapshot 与 raw-event 重建无分歧
const firstOwnSteps = expectedStates.first_10_own_steps ?? [];
check(firstOwnSteps.length >= 10, `应至少锁定 10 个本人打牌决策（得到 ${firstOwnSteps.length}）`);
const divergence = findFirstHandDivergence(events, data, VIEW);
check(divergence === null, `G3: 不应有 hand/action 分歧${divergence ? `（step ${divergence.step} ${divergence.actualEvent}）` : ''}`);

// R3 legacy fallback：obs 锚点保留、自家 entry 去掉 sei 时仍能对齐
const legacyData = JSON.parse(JSON.stringify(data)) as ReplayData;
for (const en of legacyData.log) if (!en.is_obs) delete en.source_event_index;
const legacyDivergence = findFirstHandDivergence(events, legacyData, VIEW);
check(legacyDivergence === null, `R3 legacy fallback（obs 锚定）应无分歧${legacyDivergence ? `（step ${legacyDivergence.step}）` : ''}`);

// G6：候选原样来自当前 entry，不被 replayHands 静默过滤
const step8 = byStep.get(8);
if (step8) {
  const candidates = step8.candidates ?? [];
  const allHand = new Set([...(step8.hand ?? []), ...(step8.tsumo_pai ? [step8.tsumo_pai] : [])]);
  const filteredOut = candidates.filter((c) =>
    (c.action.type === 'dahai' || c.action.type === 'kakan') && c.action.pai && !allHand.has(c.action.pai),
  );
  check(
    filteredOut.length === 0,
    `G6: 候选不应被静默过滤（step 8 候选 ${candidates.length}，含不在手牌的 ${filteredOut.length} 个）`,
  );
  // buildLogitData 也不应丢弃候选
  const logit = buildLogitData(step8);
  check(logit.length > 0, `G6: step 8 logit 数据应存在`);
}

// G5/R6：teacher overlay 不得影响棋盘事实
const overlayFiles = ['external_mortal.json', '70k.json'];
const overlayEntries: Record<string, unknown>[] = [];
for (const file of overlayFiles) {
  const overlay = loadFixture('replay_97508ebc_p3', file);
  const review = overlay.review ?? {};
  for (const kyoku of review.kyokus ?? []) {
    for (const entry of kyoku.entries ?? []) {
      overlayEntries.push(entry);
    }
  }
}
check(overlayEntries.length > 0, `teacher overlay 应包含条目`);
for (const entry of overlayEntries) {
  for (const key of BOARD_FACT_KEYS) {
    check(
      !(key in entry),
      `R6: teacher overlay 条目不应包含棋盘事实字段 ${key}`,
    );
  }
}
// 模拟 server 将 teacher_reviews 挂到 entry 上，棋盘事实应保持不变
const withTeacher = JSON.parse(JSON.stringify(data)) as ReplayData;
withTeacher.log = withTeacher.log.map((e, idx) => ({
  ...e,
  teacher_reviews: idx < 20 ? [{ model: '70k', entry_index: idx }] : undefined,
  teacher_review: idx < 20 ? { model: '70k', entry_index: idx } : undefined,
}));
for (const step of [3, 8, 30]) {
  const base = byStep.get(step);
  const overlaid = withTeacher.log.find((e) => e.step === step);
  check(Boolean(base && overlaid), `R6: step ${step} 存在`);
  if (!base || !overlaid) continue;
  const a = entryToBattleState(base, names, VIEW, 'pre');
  const b = entryToBattleState(overlaid, names, VIEW, 'pre');
  check(
    sameMultiset(a.hand, b.hand) && a.tsumo_pai === b.tsumo_pai
      && JSON.stringify(a.melds) === JSON.stringify(b.melds)
      && JSON.stringify(a.discards) === JSON.stringify(b.discards),
    `R6: teacher overlay 不改变 step ${step} 棋盘事实`,
  );
}

// --- Gate D：none/pass step 的 Oracle 不应是开局配牌 -------------------------

const passEntry = data.log.find((e) => getActualReplayAction(e)?.type === 'none');
check(Boolean(passEntry), `Gate D: fixture 应含 none/pass entry`);
if (passEntry) {
  const rebuiltPass = buildReplayHandsForBoard(events, data, passEntry, 'pre');
  check(rebuiltPass != null, `Gate D: none/pass step 应能重建手牌`);
  if (rebuiltPass) {
    const startHand = events[findKyokuRangeStart(events, passEntry)].tehais[VIEW] as string[] ?? [];
    check(
      !sameMultiset(rebuiltPass[VIEW], startHand),
      `Gate D: none/pass Oracle 不应返回开局配牌（rebuilt=${rebuiltPass[VIEW].join('')}）`,
    );
  }
}

// --- Gate E：错误 source_event_index 不静默显示错误手牌 ----------------------

const gateEStep8 = byStep.get(8);
if (gateEStep8) {
  const corrupted = JSON.parse(JSON.stringify(gateEStep8)) as DecisionLogEntry;
  corrupted.source_event_index = 3; // 指向事件 3（dahai 2s by 0），与 step 8 动作不匹配
  const range = findKyokuEventRange(events, corrupted);
  const boundary = range
    ? resolveEntryEventBoundary(events.slice(range.startIdx, range.endIdx), range.startIdx, data, corrupted)
    : null;
  check(boundary == null || boundary.actionEvent?.pai === 'P', `Gate E: sei 失配应被校验拦截或经 legacy 对齐到正确事件`);
  const rebuilt = buildReplayHandsForBoard(events, data, corrupted, 'post');
  if (rebuilt) {
    const expectedPost = applyDahaiToHand([...(gateEStep8.hand ?? []), ...(gateEStep8.tsumo_pai ? [gateEStep8.tsumo_pai] : [])], (gateEStep8.gt_action ?? gateEStep8.chosen)?.pai);
    check(sameMultiset(rebuilt[VIEW], expectedPost), `Gate E: sei 失配不应产生错误手牌（rebuilt=${rebuilt[VIEW].join('')}）`);
  }
}

// --- Gate F：gt_action=null 且 chosen 非 none 时不修改棋盘 -------------------

const step3 = byStep.get(3);
if (step3) {
  const incomplete = JSON.parse(JSON.stringify(step3)) as DecisionLogEntry;
  delete incomplete.gt_action;
  incomplete.chosen = { type: 'pon', actor: VIEW, pai: '5p', consumed: ['5p', '5p'], target: 2 };
  const state = entryToBattleState(incomplete, names, VIEW, 'post');
  check(
    sameMultiset(state.hand, step3.hand ?? []) && state.tsumo_pai === step3.tsumo_pai,
    `Gate F: gt_action=null 且 chosen 非 none 时 post 不得应用模型建议（hand=${state.hand.join('')}）`,
  );
  check(
    JSON.stringify(state.melds) === JSON.stringify(entryToBattleState(step3, names, VIEW, 'pre').melds),
    `Gate F: gt_action=null 时不得新增模型建议的副露`,
  );
  // P2-1：无可靠事件边界时 Oracle 重建应返回 null，不得伪装成开局配牌
  const rebuilt = buildReplayHandsForBoard(events, data, incomplete, 'pre');
  check(rebuilt === null, `Gate F/P2-1: gt_action=null 时 buildReplayHandsForBoard 应返回 null（不得返回开局配牌）`);
}

// ---------------------------------------------------------------------------
// Fixture 2：replay_54beeaed_p0 真实 kakan step 475（G10）
// ---------------------------------------------------------------------------

const kData = loadFixture('replay_54beeaed_p0/decisions.json') as ReplayData;
const kEvents = loadFixture('replay_54beeaed_p0/events.json') as Record<string, unknown>[];
const kNames = kData.player_names ?? ['P0', 'P1', 'P2', 'P3'];
const kView = 0;

const kakanEntry = kData.log.find((e) => (e.gt_action ?? e.chosen)?.type === 'kakan');
check(Boolean(kakanEntry), `G10: fixture 应含 kakan entry`);
if (kakanEntry) {
  const pre = entryToBattleState(kakanEntry, kNames, kView, 'pre');
  const post = entryToBattleState(kakanEntry, kNames, kView, 'post');
  check(pre.melds[kView].length === 3, `G10: kakan 动作前应为 3 组副露（得到 ${pre.melds[kView].length}）`);
  const preKanPon = pre.melds[kView].find((m) => m.type === 'pon' && m.pai === 'N');
  check(Boolean(preKanPon), `G10: kakan 动作前应存在被加杠的 pon N`);
  check(pre.tsumo_pai === 'N', `G10: kakan 动作前 tsumo_pai 应为摸到的 N（得到 ${pre.tsumo_pai}）`);
  check(pre.hand.length === 4, `G10: kakan 动作前暗手应为 4 张（得到 ${pre.hand.length}）`);
  const kanMeld = post.melds[kView].find((m) => m.type === 'kakan');
  check(Boolean(kanMeld), `G10: kakan 动作后应存在 kakan 副露`);
  if (kanMeld) {
    check(kanMeld.consumed.length === 4, `G10: kakan consumed 应严格 4 张（得到 ${kanMeld.consumed.length}）`);
  }
  check(post.tsumo_pai === null, `G10: kakan 动作后 tsumo_pai 应为 null`);
  check(post.melds[kView].length === 3, `G10: kakan 动作后副露仍 3 组（原地替换）`);
  const kDiv = findFirstHandDivergence(kEvents, kData, kView);
  check(kDiv === null, `G10: kakan 牌局 raw-event 重建无分歧${kDiv ? `（step ${kDiv.step} ${kDiv.actualEvent}）` : ''}`);
  // buildReplayHandsForBoard 在 kakan post 应完成加杠（K5：meld 与 hand 同一时间点）
  const rebuilt = buildReplayHandsForBoard(kEvents, kData, kakanEntry, 'post');
  check(rebuilt != null, `G10: kakan post 重建手牌存在`);
  if (rebuilt) {
    check(
      !rebuilt[kView].includes('N'),
      `G10/K5: kakan post 暗手不应再含加杠 N（得到 ${rebuilt[kView].join('')}）`,
    );
  }
}

if (failures > 0) {
  console.error(`review E2E regression FAILED (${failures} issues)`);
  process.exit(1);
}
console.log('review E2E regression OK (real multi-turn snapshots, divergence, teacher independence, real kakan)');
