// src/replay_ui/scripts/checkReviewDaiminkan.ts
// 回归检查：副露动作（大明杠/kakan/chi/pon/ankan）的 pre/post 状态重建与副露牌面校验。
//
// 真实数据（2026-08-04 实战胜场 tenhou:2026080423gm-0009-2147-32af115e，南4局 1本场，
// 渲染自 workbench artifacts/replays/replay_54beeaed_1785854084 decisions step 475）：
//   自家 P0 已有 3 组副露 [pon P, chi 1m, pon N]，摸到第 4 张 N（tsumo）后加杠。
//   真实 gt_action = {type:kakan, actor:0, pai:N, consumed:[N,N,N]}（consumed 只有 3 张、无 target）。
//
// 大明杠 6m 场景在两局真实牌谱中并不存在（已核对两局 events 无 daiminkan），
// 该 fixture 为手工构造，仅用于锁定大明杠的展示与相位切换逻辑（验收 A/B/C）。
//
// 验收：
//   A. 大明杠动作前：4 张暗手（含 3 张 6m），3 组副露
//   B. 大明杠动作后：1 张暗手，4 组副露，新大明杠显示 4 张 6m
//   C. pre/post 来回切换无状态漂移
//   D. obs before→post 正确前向应用；obs after→pre 正确回退
//   E. kakan pre→post：pon 被替换为 kakan（不出现 pon+kakan 两组）
//   F. kakan post→pre：pon 回到原索引；last_discard=null；加杠牌回到 tsumo
//   G. 非法副露 → buildMeldDisplayTiles 返回空数组；ankan/kakan consumed 严格等于 4
//   H. computeSelfHandWidth 与 flex 实际宽度一致
import { entryToBattleState } from '../src/utils/replayAdapter.ts';
import {
  buildMeldDisplayTiles,
  computeSelfHandContentOffset,
  computeSelfHandWidth,
  computeSelfSeatGeometry,
  computeSouthMeldLaneWidth,
  getKakanStackOffset,
  getMeldTileOrientation,
  getSeatModel,
  orderMeldsForDisplay,
  SELF_HAND_LEFT_OFFSET,
  SELF_HAND_MELD_GAP,
  SELF_HAND_ORIGIN_X_PX,
  SELF_SEAT_SHELL_WIDTH_PX,
  SELF_SEAT_SIDE_MARGIN,
  validateMeldTiles,
} from '../src/components/BattleBoard/seatLayout.ts';
import { BASE_TABLE_WIDTH, HAND_TILE_GAP, HAND_DRAW_GAP } from '../src/components/BattleBoard/tableLayout.ts';
import { TILE_SIZES } from '../src/components/BattleBoard/tileSizes.ts';
import type { Action, DecisionLogEntry, MeldEntry } from '../src/types/replay.ts';

let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

const NAMES = ['P0', 'P1', 'P2', 'P3'];

function meld(type: MeldEntry['type'], pai: string, consumed: string[], target: number): MeldEntry {
  return { type, pai, consumed, target };
}

// --- 真实 kakan entry（南4局 1本场 step 475）---------------------------------

function realKakanEntry(): DecisionLogEntry {
  return {
    step: 475,
    bakaze: 'S',
    kyoku: 4,
    honba: 1,
    oya: 3,
    scores: [23100, 10000, 36900, 29000],
    reached: [false, false, false, false],
    dora_markers: [],
    hand: ['6m', '6m', '7p', 'S'],
    discards: {
      0: [
        { pai: '8s', tsumogiri: false }, { pai: '2s', tsumogiri: false },
        { pai: '4s', tsumogiri: false }, { pai: '9p', tsumogiri: false },
        { pai: '6p', tsumogiri: false }, { pai: '9p', tsumogiri: true },
      ],
      1: [{ pai: 'E', tsumogiri: false }, { pai: 'F', tsumogiri: false }],
      2: [{ pai: 'W', tsumogiri: false }],
      3: [
        { pai: '9m', tsumogiri: false }, { pai: 'P', tsumogiri: false },
        { pai: 'F', tsumogiri: false }, { pai: '1m', tsumogiri: false },
        { pai: '1s', tsumogiri: true }, { pai: '8m', tsumogiri: false },
      ],
    },
    melds: {
      0: [
        meld('pon', 'P', ['P', 'P'], 2),
        meld('chi', '1m', ['2m', '3m'], 3),
        meld('pon', 'N', ['N', 'N'], 3),
      ],
      1: [],
      2: [meld('chi', '6p', ['5pr', '7p'], 1)],
      3: [meld('pon', 'C', ['C', 'C'], 2)],
    },
    actor_to_move: 0,
    tsumo_pai: 'N',
    last_discard: { actor: 3, pai: '8m' },
    is_obs: false,
    // 真实 gt_action：consumed 只有 3 张、无 target
    chosen: { type: 'dahai', actor: 0, pai: '7p', tsumogiri: false },
    gt_action: { type: 'kakan', actor: 0, pai: 'N', consumed: ['N', 'N', 'N'] },
    candidates: [
      { action: { type: 'kakan', actor: 0, pai: 'N', consumed: ['N', 'N', 'N'] }, logit: 2 },
      { action: { type: 'dahai', actor: 0, pai: '7p', tsumogiri: false }, logit: 1 },
    ],
  };
}

// --- E/F：真实 kakan 的 pre/post ---------------------------------------------

const realKakan = realKakanEntry();
const realKakanPre = entryToBattleState(realKakan, NAMES, 0, 'pre');
const realKakanPost = entryToBattleState(realKakan, NAMES, 0, 'post');

check(realKakanPre.melds[0].length === 3, `kakan 动作前副露应仍为 3 组（真实快照即 pre，得到 ${realKakanPre.melds[0].length}）`);
check(realKakanPre.melds[0][2].type === 'pon' && realKakanPre.melds[0][2].pai === 'N',
  `kakan 动作前第 3 组应仍是 pon N`);
check(realKakanPre.hand.length === 4 && realKakanPre.hand.includes('6m'), `kakan 动作前暗手应保持 4 张`);
check(realKakanPre.tsumo_pai === 'N', `kakan 动作前 tsumo_pai 应为摸到的 N（得到 ${realKakanPre.tsumo_pai}）`);

check(realKakanPost.melds[0].length === 3, `kakan 动作后副露应为 3 组（替换而非追加，得到 ${realKakanPost.melds[0].length}）`);
const realKakanMeld = realKakanPost.melds[0][2];
check(realKakanMeld.type === 'kakan', `kakan 动作后第 3 组应为 kakan`);
check(realKakanMeld.consumed.length === 4, `kakan meld consumed 应为 4 张（3 张原 pon + 加杠），得到 ${realKakanMeld.consumed.length}`);
check(realKakanPost.hand.length === 4 && realKakanPost.hand.includes('6m'), `kakan 动作后暗手应保持 4 张（加杠牌从 tsumo 吃掉）`);
check(realKakanPost.tsumo_pai === null, `kakan 动作后 tsumo_pai 应为 null`);
check(realKakanPost.last_discard === null, `kakan 动作后 last_discard 应为 null`);

// kakan 动作后快照（修复后的后端形态：meld 已是 4 张 kakan、tsumo 已吃掉）
const realKakanPostEntry: DecisionLogEntry = {
  ...realKakanEntry(),
  tsumo_pai: null,
  last_discard: null,
  hand: ['6m', '6m', '7p', 'S'],
  melds: {
    0: [
      meld('pon', 'P', ['P', 'P'], 2),
      meld('chi', '1m', ['2m', '3m'], 3),
      meld('kakan', 'N', ['N', 'N', 'N', 'N'], 3),
    ],
    1: [],
    2: [meld('chi', '6p', ['5pr', '7p'], 1)],
    3: [meld('pon', 'C', ['C', 'C'], 2)],
  },
};
const realKakanBackPre = entryToBattleState(realKakanPostEntry, NAMES, 0, 'pre');
check(realKakanBackPre.melds[0].length === 3, `kakan post→pre 副露应恢复为 3 组`);
check(realKakanBackPre.melds[0][2].type === 'pon' && realKakanBackPre.melds[0][2].pai === 'N',
  `kakan post→pre 第 3 组应在原索引恢复为 pon N（H6）`);
check(realKakanBackPre.hand.includes('N'), `kakan post→pre 加杠牌应放回暗手（得到 ${realKakanBackPre.hand}）`);
check(realKakanBackPre.tsumo_pai === null, `kakan post→pre 不应猜测 tsumo（K2，得到 ${realKakanBackPre.tsumo_pai}）`);
check(realKakanBackPre.last_discard === null, `kakan 动作前 last_discard 应为 null（H7，加杠不是对弃牌的响应）`);
check(realKakanBackPre.hand.length === 5, `kakan post→pre 暗手应为 5 张（4 + 加杠）`);

// 往返：post→pre→post 无漂移
const realKakanBackPost = entryToBattleState(realKakanPostEntry, NAMES, 0, 'post');
check(realKakanBackPost.melds[0][2].type === 'kakan' && realKakanBackPost.melds[0].length === 3,
  `kakan 往返后回到 kakan（不漂移）`);

// --- A/B/C：大明杠 pre/post（合成 fixture，真实牌谱无 daiminkan）-------------

function daiminkanEntry(): DecisionLogEntry {
  return {
    step: 491,
    bakaze: 'S',
    kyoku: 4,
    honba: 1,
    oya: 1,
    scores: [20000, 20000, 20000, 20000],
    reached: [false, false, false, false],
    dora_markers: [],
    hand: ['1p'], // snapshot 为动作后：暗手只剩 1 张
    discards: { 0: [], 1: [{ pai: '6m', tsumogiri: false }], 2: [], 3: [] },
    melds: {
      0: [
        meld('chi', '3s', ['1s', '2s'], 1),
        meld('pon', '5p', ['5p', '5p'], 2),
        meld('pon', '9m', ['9m', '9m'], 3),
        meld('daiminkan', '6m', ['6m', '6m', '6m'], 1),
      ],
      1: [], 2: [], 3: [],
    },
    actor_to_move: 0,
    tsumo_pai: null,
    last_discard: { actor: 1, pai: '6m' },
    is_obs: false,
    chosen: { type: 'daiminkan', actor: 0, pai: '6m', consumed: ['6m', '6m', '6m'], target: 1 },
    gt_action: { type: 'daiminkan', actor: 0, pai: '6m', consumed: ['6m', '6m', '6m'], target: 1 },
    candidates: [
      { action: { type: 'daiminkan', actor: 0, pai: '6m', consumed: ['6m', '6m', '6m'], target: 1 }, logit: 1 },
      { action: { type: 'none', actor: 0 }, logit: 0 },
    ],
  };
}

const entry = daiminkanEntry();
const pre = entryToBattleState(entry, NAMES, 0, 'pre');
const post = entryToBattleState(entry, NAMES, 0, 'post');

check(pre.hand.length === 4, `动作前暗手应为 4 张，得到 ${pre.hand.length}`);
check(pre.hand.filter((t) => t === '6m').length === 3, `动作前暗手应含 3 张 6m，得到 ${pre.hand}`);
check(pre.melds[0].length === 3, `动作前副露应为 3 组，得到 ${pre.melds[0].length}`);
check(pre.actor_to_move === 0, `动作前 actor_to_move 应为 0`);
check(pre.tsumo_pai === null, `动作前 tsumo_pai 应为 null`);

check(post.hand.length === 1, `动作后暗手应为 1 张，得到 ${post.hand.length}`);
check(post.melds[0].length === 4, `动作后副露应为 4 组，得到 ${post.melds[0].length}`);

// 新大明杠显示 4 张 6m
const kanMeld = post.melds[0][3];
check(kanMeld.type === 'daiminkan', `动作后第 4 组应为 daiminkan`);
const kanTiles = buildMeldDisplayTiles(0, kanMeld);
check(kanTiles.length === 4, `daiminkan 应显示 4 张，得到 ${kanTiles.length}`);
check(kanTiles.every((t) => t.tile === '6m'), `daiminkan 四张都应是 6m`);

// --- C：pre/post 往返无漂移 ----------------------------------------------

const preAgain = entryToBattleState(entry, NAMES, 0, 'pre');
const postAgain = entryToBattleState(entry, NAMES, 0, 'post');
check(
  preAgain.hand.length === pre.hand.length && preAgain.melds[0].length === pre.melds[0].length,
  `pre 往返后状态漂移`,
);
check(
  postAgain.hand.length === post.hand.length && postAgain.melds[0].length === post.melds[0].length,
  `post 往返后状态漂移`,
);

// --- D：obs 矩阵（H3 before→post / H4 after→pre）----------------------------

// obs before_action：快照为动作前（暗手 4 张、无 daiminkan）
const obsBefore: DecisionLogEntry = {
  ...daiminkanEntry(),
  is_obs: true,
  board_phase: 'before_action',
  hand: ['1p', '6m', '6m', '6m'],
  melds: {
    0: [
      meld('chi', '3s', ['1s', '2s'], 1),
      meld('pon', '5p', ['5p', '5p'], 2),
      meld('pon', '9m', ['9m', '9m'], 3),
    ],
    1: [], 2: [], 3: [],
  },
};
const obsBeforePre = entryToBattleState(obsBefore, NAMES, 0, 'pre');
check(obsBeforePre.hand.length === 4 && obsBeforePre.melds[0].length === 3,
  `obs before_action + pre 应原样返回动作前状态`);
const obsBeforePost = entryToBattleState(obsBefore, NAMES, 0, 'post');
check(obsBeforePost.hand.length === 1, `obs before_action + post 应前向应用大明杠（H3），手 ${obsBeforePost.hand}`);
check(obsBeforePost.melds[0].length === 4 && obsBeforePost.melds[0][3].type === 'daiminkan',
  `obs before_action + post 应得到 4 组副露含 daiminkan（H3）`);

// obs after_action：快照为动作后（暗手 1 张、已含 daiminkan），真实 obs meld entry 即此形态
const obsAfter: DecisionLogEntry = {
  ...daiminkanEntry(),
  is_obs: true,
  board_phase: 'after_action',
};
const obsAfterPost = entryToBattleState(obsAfter, NAMES, 0, 'post');
check(obsAfterPost.hand.length === 1 && obsAfterPost.melds[0].length === 4,
  `obs after_action + post 应原样返回动作后状态`);
const obsAfterPre = entryToBattleState(obsAfter, NAMES, 0, 'pre');
check(obsAfterPre.hand.length === 4 && obsAfterPre.hand.filter((t) => t === '6m').length === 3,
  `obs after_action + pre 应回退大明杠（H4），手 ${obsAfterPre.hand}`);
check(obsAfterPre.melds[0].length === 3, `obs after_action + pre 副露应回到 3 组（H4）`);

// --- chi/pon/ankan 可逆 ------------------------------------------------------

function singleMeldEntry(action: Action, hand: string[], melds: MeldEntry[]): DecisionLogEntry {
  return {
    ...daiminkanEntry(),
    hand,
    melds: { 0: melds, 1: [], 2: [], 3: [] },
    chosen: action,
    gt_action: action,
    candidates: [],
  };
}

const chiEntry = singleMeldEntry(
  { type: 'chi', actor: 0, pai: '3s', consumed: ['1s', '2s'], target: 1 },
  ['9p'],
  [meld('chi', '3s', ['1s', '2s'], 1)],
);
const chiPre = entryToBattleState(chiEntry, NAMES, 0, 'pre');
check(chiPre.hand.length === 3 && chiPre.hand.includes('1s') && chiPre.hand.includes('2s'),
  `chi pre 应恢复 2 张 consumed（手 ${chiPre.hand}）`);
check(chiPre.melds[0].length === 0, `chi pre 应移除该组副露`);

const ponEntry = singleMeldEntry(
  { type: 'pon', actor: 0, pai: '5p', consumed: ['5p', '5p'], target: 2 },
  ['9p'],
  [meld('pon', '5p', ['5p', '5p'], 2)],
);
const ponPre = entryToBattleState(ponEntry, NAMES, 0, 'pre');
check(ponPre.hand.length === 3 && ponPre.hand.filter((t) => t === '5p').length === 2,
  `pon pre 应恢复 2 张 5p（手 ${ponPre.hand}）`);

const ankanEntry = singleMeldEntry(
  { type: 'ankan', actor: 0, consumed: ['7m', '7m', '7m', '7m'] },
  ['9p'],
  [meld('ankan', '', ['7m', '7m', '7m', '7m'], 0)],
);
const ankanPre = entryToBattleState(ankanEntry, NAMES, 0, 'pre');
check(ankanPre.hand.length === 5 && ankanPre.hand.filter((t) => t === '7m').length === 4,
  `ankan pre 应恢复 4 张 7m（手 ${ankanPre.hand}）`);
check(ankanPre.last_discard === null, `ankan pre last_discard 应为 null`);

// 真实编码的 kakan（action consumed 3 张 + target）：meld 为 4 张
const kakanEntry = singleMeldEntry(
  { type: 'kakan', actor: 0, pai: '6p', consumed: ['6p', '6p', '6p'], target: 2 },
  ['9p'],
  [meld('kakan', '6p', ['6p', '6p', '6p', '6p'], 2)],
);
const kakanPre = entryToBattleState(kakanEntry, NAMES, 0, 'pre');
check(kakanPre.melds[0].length === 1 && kakanPre.melds[0][0].type === 'pon',
  `kakan pre 应回退为 pon（得到 ${kakanPre.melds[0][0]?.type}）`);
check(kakanPre.melds[0][0].consumed.length === 2, `kakan pre 的 pon consumed 应为 2 张`);
check(kakanPre.hand.includes('6p'), `kakan pre 加杠牌应放回暗手（K2，得到 ${kakanPre.hand}）`);
check(kakanPre.tsumo_pai === null, `kakan pre 不应猜测 tsumo（K2，得到 ${kakanPre.tsumo_pai}）`);
const kakanPost = entryToBattleState(kakanEntry, NAMES, 0, 'post');
check(kakanPost.melds[0].length === 1 && kakanPost.melds[0][0].type === 'kakan',
  `kakan post 应保持 kakan（不追加 pon）`);
check(kakanPost.melds[0][0].consumed.length === 4, `kakan post meld consumed 应为严格 4 张（K1）`);

// K1：action.consumed 为 4 张（规范化输入）时，也按原 pon 构造严格 4 张
const kakan4Entry = singleMeldEntry(
  { type: 'kakan', actor: 0, pai: '6p', consumed: ['6p', '6p', '6p', '6p'], target: 2 },
  ['9p'],
  [meld('kakan', '6p', ['6p', '6p', '6p', '6p'], 2)],
);
const kakan4Pre = entryToBattleState(kakan4Entry, NAMES, 0, 'pre');
check(kakan4Pre.melds[0].length === 1 && kakan4Pre.melds[0][0].type === 'pon',
  `kakan（4 张 action consumed）pre 也应回退为 pon`);
const kakan4Post = entryToBattleState(kakan4Entry, NAMES, 0, 'post');
check(kakan4Post.melds[0][0].type === 'kakan' && kakan4Post.melds[0][0].consumed.length === 4,
  `kakan（4 张 action consumed）post meld 仍严格 4 张（K1）`);

// K1：原 pon 含赤牌时 canonical consumed 保留赤牌组成
const redPonEntry = singleMeldEntry(
  { type: 'kakan', actor: 0, pai: '5p', consumed: ['5pr', '5p', '5p'], target: 2 },
  ['9p'],
  [meld('pon', '5p', ['5pr', '5p'], 2)],
);
const redPonPost = entryToBattleState(redPonEntry, NAMES, 0, 'post');
check(
  redPonPost.melds[0][0].type === 'kakan'
    && JSON.stringify(redPonPost.melds[0][0].consumed) === JSON.stringify(['5pr', '5p', '5p', '5p']),
  `kakan canonical consumed 应保留原 pon 赤牌：['5pr','5p','5p','5p']（K1，得到 ${JSON.stringify(redPonPost.melds[0][0]?.consumed)}）`,
);

// K4：ankan 杠牌来自本巡摸牌（tsumo=4p，手 3 张）
const ankanTsumoEntry = singleMeldEntry(
  { type: 'ankan', actor: 0, consumed: ['4p', '4p', '4p', '4p'] },
  ['9p', '4p', '4p', '4p'],
  [],
);
const ankanTsumoEntryWithTsumo: DecisionLogEntry = { ...ankanTsumoEntry, tsumo_pai: '4p' };
const ankanTsumoPost = entryToBattleState(ankanTsumoEntryWithTsumo, NAMES, 0, 'post');
check(ankanTsumoPost.hand.filter((t) => t === '4p').length === 0, `ankan 摸牌来源：post 手应无 4p（K4）`);
check(ankanTsumoPost.tsumo_pai === null, `ankan 摸牌来源：post tsumo 应为 null（K4）`);
check(ankanTsumoPost.melds[0][0].type === 'ankan', `ankan 摸牌来源：post 应有 ankan 副露（K4）`);

// K4：ankan 全部来自手牌、另摸入不同牌（tsumo=3s 保留）
const ankanHandEntryWithTsumo: DecisionLogEntry = {
  ...singleMeldEntry(
    { type: 'ankan', actor: 0, consumed: ['7m', '7m', '7m', '7m'] },
    ['9p', '7m', '7m', '7m', '7m'],
    [],
  ),
  tsumo_pai: '3s',
};
const ankanHandPost = entryToBattleState(ankanHandEntryWithTsumo, NAMES, 0, 'post');
check(ankanHandPost.hand.filter((t) => t === '7m').length === 0, `ankan 手牌来源：post 手应无 7m（K4）`);
check(ankanHandPost.tsumo_pai === '3s', `ankan 手牌来源：post 应保留无关 tsumo 3s（K4，得到 ${ankanHandPost.tsumo_pai}）`);

// --- G：副露校验与拒绝渲染 --------------------------------------------------

check(validateMeldTiles(meld('daiminkan', '6m', ['6m', '6m', '6m'], 1)) === true, `合法 daiminkan 应通过校验`);
check(validateMeldTiles(meld('daiminkan', '6m', ['6m', '6m'], 1)) === false, `consumed 数量错误的 daiminkan 应失败`);
check(validateMeldTiles(meld('chi', '3s', ['1s', ''], 1)) === false, `含空牌的副露应失败`);
check(validateMeldTiles(meld('ankan', '', ['7m', '7m', '7m', '7m'], 0)) === true, `合法 ankan 应通过校验`);
check(validateMeldTiles(meld('ankan', '', ['7m', '7m', '7m'], 0)) === false, `ankan consumed 必须严格等于 4（H2）`);
check(validateMeldTiles(meld('kakan', '6p', ['6p', '6p', '6p', '6p'], 2)) === true, `合法 kakan 应通过校验`);
check(validateMeldTiles(meld('kakan', '6p', ['6p', '6p', '6p'], 2)) === false, `kakan consumed 必须严格等于 4（H2）`);

// 校验失败 → buildMeldDisplayTiles 拒绝渲染（H1）
check(buildMeldDisplayTiles(0, meld('daiminkan', '6m', ['6m', '6m'], 1)).length === 0,
  `consumed 数量错误的 daiminkan 不应渲染（H1）`);
check(buildMeldDisplayTiles(0, meld('chi', '3s', ['1s', ''], 1)).length === 0,
  `含空牌的 chi 不应渲染（H1）`);
check(buildMeldDisplayTiles(0, meld('kakan', '6p', ['6p', '6p', '6p'], 2)).length === 0,
  `3 张 kakan 不应渲染（H2）`);

const validKanTiles = buildMeldDisplayTiles(0, meld('daiminkan', '6m', ['6m', '6m', '6m'], 1));
check(validKanTiles.length === 4 && validKanTiles.every((t) => t.tile === '6m'), `合法 daiminkan 应渲染 4 张 6m`);

const validKakanTiles = buildMeldDisplayTiles(0, meld('kakan', 'N', ['N', 'N', 'N', 'N'], 3));
check(validKakanTiles.length === 4, `合法 kakan 应渲染 4 张（得到 ${validKakanTiles.length}）`);
check(validKakanTiles.some((t) => t.stackedOn !== undefined), `kakan 应有 1 张加杠叠牌`);

// --- H：computeSelfHandWidth 与实际 flex 宽度一致 ----------------------------

const TW = 100, TG = 10, DG = 20;
check(
  computeSelfHandWidth(13, false, TW, TG, DG) === 13 * TW + 12 * TG,
  `无摸牌 13 张宽度应为 13*w + 12*gap`,
);
check(
  computeSelfHandWidth(13, true, TW, TG, DG) === 14 * TW + 13 * TG + DG,
  `有摸牌 13 张宽度应为 14*w + 13*gap + drawGap（H8）`,
);
check(
  computeSelfHandWidth(4, true, TW, TG, DG) === 5 * TW + 4 * TG + DG,
  `有摸牌 4 张宽度应为 5*w + 4*gap + drawGap（H8）`,
);
check(
  computeSelfHandWidth(0, true, TW, TG, DG) === 1 * TW + DG,
  `仅摸牌 1 张宽度应为 w + drawGap`,
);
check(
  computeSelfHandWidth(1, true, TW, TG, DG) === 2 * TW + TG + DG,
  `1 张 + 摸牌应为 2*w + gap + drawGap`,
);

// --- I：主视角底部双 lane + kakan 组内锚点（Commit A/B/C） ------------------
// Commit A：south 手牌 lane 左吸附、副露 lane 右吸附、row 布局、副露在右侧。
const south = getSeatModel('south');
check(
  south.concealedAxis === 'row' && south.meldAxis === 'row' && south.meldPlacement === 'after',
  `south 手牌/副露应为 row 布局且副露在右侧（after）`,
);
check(south.concealedReverse === false, `south 手牌不应 reverse`);

// Commit B：kakan 加杠牌叠在原 pon 横置被鸣牌正上方（组内锚点，非整组偏移）。
function assertKakanStackedOn(target: number, expectedClaimedIndex: number) {
  const tiles = buildMeldDisplayTiles(0, meld('kakan', 'N', ['N', 'N', 'N', 'N'], target));
  check(tiles.length === 4, `kakan(target=${target}) 应渲染 4 张`);
  const stackedIndex = tiles.findIndex((t) => t.stackedOn !== undefined);
  check(stackedIndex >= 0, `kakan(target=${target}) 应有 1 张加杠叠牌`);
  if (stackedIndex < 0) return;
  const stacked = tiles[stackedIndex];
  check(
    stacked.stackedOn === expectedClaimedIndex,
    `kakan(target=${target}) 叠牌应锚定在被鸣牌索引 ${expectedClaimedIndex}（得到 ${stacked.stackedOn}）`,
  );
  check(
    tiles[expectedClaimedIndex].rotated === true,
    `kakan(target=${target}) 被锚定牌应为横置被鸣牌`,
  );
  check(
    stacked.rotated === true,
    `kakan(target=${target}) 第四张必须与被鸣牌同为横置牌`,
  );
  check(
    stacked.rotated === tiles[expectedClaimedIndex].rotated,
    `kakan(target=${target}) 第四张与被鸣牌必须使用相同旋转语义`,
  );
}
// left / across / right 三种被鸣来源：claimed index 分别为 0 / 1 / 2
assertKakanStackedOn(3, 0);
assertKakanStackedOn(2, 1);
assertKakanStackedOn(1, 2);

// --- J：自家底部固定区域几何（Commit A 收口）---------------------------------
// 手牌 lane 宽度 = shell − meldLane − 固定 gap（flex:1 可计算），
// 副露 lane 右吸附 → meldRight = tableWidth − rightMargin，对 0~4 副露与 13/14 摸打恒定。
const JW = TILE_SIZES.large.w, JG = HAND_TILE_GAP, JDG = HAND_DRAW_GAP;

const meldRights = new Set<number>();
const handLefts = new Set<number>();
const effectiveOffsets = new Set<number>();
for (let m = 0; m <= 4; m++) {
  const geometry = computeSelfSeatGeometry({
    shellWidth: SELF_SEAT_SHELL_WIDTH_PX,
    tableWidth: BASE_TABLE_WIDTH,
    rightMargin: SELF_SEAT_SIDE_MARGIN,
    handMeldGap: SELF_HAND_MELD_GAP,
    meldLaneWidth: computeSouthMeldLaneWidth(m),
  });
  meldRights.add(geometry.meldRight);
  handLefts.add(geometry.handLeft);
  check(
    geometry.meldLeft - geometry.handRight === SELF_HAND_MELD_GAP,
    `meld=${m}: 手牌/副露间隔应恒为 ${SELF_HAND_MELD_GAP}px（得到 ${geometry.meldLeft - geometry.handRight}）`,
  );
  // 暗手 = 13 − 3×m（BattleState 的 hand 不含摸牌；摸牌时最多 14 − 12 = 2 张可见）
  const handCount = Math.max(13 - 3 * m, 0);
  const handContent = computeSelfHandWidth(handCount, handCount > 0, JW, JG, JDG);
  check(
    handContent <= geometry.handLaneWidth,
    `meld=${m} hand=${handCount}: 手牌内容 ${handContent}px ≤ hand lane ${geometry.handLaneWidth}px（不裁切）`,
  );
  // P1：144px 目标偏移必须受 lane 约束（effectiveOffset + 内容 ≤ lane）
  // P2：偏移按最大摸牌态宽度预留，pre/post（摸/不摸）起点一致
  const drawContent = computeSelfHandWidth(handCount, true, JW, JG, JDG);
  const noDrawContent = computeSelfHandWidth(handCount, false, JW, JG, JDG);
  const stableOffset = computeSelfHandContentOffset(SELF_HAND_LEFT_OFFSET, geometry.handLaneWidth, drawContent);
  effectiveOffsets.add(stableOffset);
  check(
    stableOffset + drawContent <= geometry.handLaneWidth,
    `meld=${m}: 稳定偏移(${stableOffset}) + 摸牌态(${drawContent}) ≤ lane(${geometry.handLaneWidth})`,
  );
  check(
    stableOffset + noDrawContent <= geometry.handLaneWidth,
    `meld=${m}: 稳定偏移(${stableOffset}) + 无摸牌态(${noDrawContent}) ≤ lane(${geometry.handLaneWidth})`,
  );
  if (m <= 2) {
    check(stableOffset === SELF_HAND_LEFT_OFFSET, `meld=${m}: 应保持完整 ${SELF_HAND_LEFT_OFFSET}px 偏移（得到 ${stableOffset}）`);
  } else {
    check(stableOffset < SELF_HAND_LEFT_OFFSET, `meld=${m}: 偏移应自动收缩（得到 ${stableOffset}）`);
  }
  // 可见牌面起点 = lane 左边界 + 稳定偏移（摸/不摸一致）
  const visibleTileStart = geometry.handLeft + stableOffset;
  check(visibleTileStart >= geometry.handLeft, `meld=${m}: 可见牌面起点 ${visibleTileStart} 应 ≥ lane 左边界`);
}
check(meldRights.size === 1, `meldRight 对 0~4 副露应保持不变（${[...meldRights].join(',')}）`);
check(
  [...meldRights][0] === BASE_TABLE_WIDTH - SELF_SEAT_SIDE_MARGIN,
  `meldRight 应等于真实牌桌坐标 tableWidth − rightMargin`,
);
check(handLefts.size === 1, `handLeft（lane 左边界）对 0~4 副露必须保持不变（${[...handLefts].join(',')}）`);
check(
  [...handLefts][0] === SELF_HAND_ORIGIN_X_PX,
  `south hand lane 左边界必须为设计坐标 x=${SELF_HAND_ORIGIN_X_PX}（得到 ${[...handLefts][0]}）`,
);
check(
  SELF_SEAT_SHELL_WIDTH_PX === BASE_TABLE_WIDTH - SELF_SEAT_SIDE_MARGIN - SELF_HAND_ORIGIN_X_PX,
  `shell 宽度必须由真实牌桌坐标推导（得到 ${SELF_SEAT_SHELL_WIDTH_PX}）`,
);

// 13→14→13 摸打（0 副��）：handLeft / meldRight 不变，仅手牌内容在 lane 内伸缩
const g13 = computeSelfSeatGeometry({ shellWidth: SELF_SEAT_SHELL_WIDTH_PX, tableWidth: BASE_TABLE_WIDTH, rightMargin: SELF_SEAT_SIDE_MARGIN, handMeldGap: SELF_HAND_MELD_GAP, meldLaneWidth: 0 });
check(
  computeSelfHandWidth(13, false, JW, JG, JDG) <= g13.handLaneWidth
  && computeSelfHandWidth(13, true, JW, JG, JDG) <= g13.handLaneWidth,
  `13→14 张手牌内容都在 lane 内（不裁切）`,
);
check(
  g13.meldLeft - g13.handRight === SELF_HAND_MELD_GAP,
  `0 副露时手牌/副露间隔仍恒为 ${SELF_HAND_MELD_GAP}px`,
);

// --- K：meld 朝向矩阵与 kakan 叠放偏移（R2）---------------------------------
// 普通副露牌 vs 横置被鸣牌/kakan 叠牌的朝向矩阵：south 0/90、north 180/270、
// east 90/0、west 270/180。
check(getMeldTileOrientation('south', false) === 0 && getMeldTileOrientation('south', true) === 90, `south meld 朝向 0/90`);
check(getMeldTileOrientation('north', false) === 180 && getMeldTileOrientation('north', true) === 270, `north meld 朝向 180/270`);
check(getMeldTileOrientation('east', false) === 90 && getMeldTileOrientation('east', true) === 0, `east meld 朝向 90/0`);
check(getMeldTileOrientation('west', false) === 270 && getMeldTileOrientation('west', true) === 180, `west meld 朝向 270/180`);

// 叠放偏移由 TILE_SIZES 推导，不硬编码 24/14。
const liftLarge = Math.ceil(TILE_SIZES.large.w / 2);
const liftNormal = Math.ceil(TILE_SIZES.normal.w / 2);
const southStack = getKakanStackOffset('south', 'large');
check(
  southStack.x === 0 && southStack.y === -liftLarge,
  `south kakan 应垂直向上叠放半张牌，无横向漂移（${JSON.stringify(southStack)}）`,
);
check(
  getKakanStackOffset('north', 'normal').x === 0 && getKakanStackOffset('north', 'normal').y === liftNormal,
  `north kakan 应垂直向下叠放（朝中心）`,
);
check(
  getKakanStackOffset('east', 'normal').x === liftNormal && getKakanStackOffset('east', 'normal').y === 0,
  `east kakan 应向右叠放（朝中心）`,
);
check(
  getKakanStackOffset('west', 'normal').x === -liftNormal && getKakanStackOffset('west', 'normal').y === 0,
  `west kakan 应向左叠放（朝中心）`,
);

// --- L：south 副露显示顺序（最早副露在最右，右吸附）-------------------------
const chronoMelds = [
  meld('pon', 'P', ['P', 'P'], 2),
  meld('chi', '1m', ['2m', '3m'], 3),
  meld('pon', 'N', ['N', 'N'], 3),
];
const southOrder = orderMeldsForDisplay(chronoMelds, 'south');
check(
  southOrder[0].pai === 'N' && southOrder[1].pai === '1m' && southOrder[2].pai === 'P',
  `south 副露应按时间逆序显示：最早 P 在最右、最新 N 在最左（得到 ${southOrder.map(m => m.pai).join('/')}）`,
);
for (const pos of (['north', 'east', 'west'] as const)) {
  const kept = orderMeldsForDisplay(chronoMelds, pos);
  check(
    kept[0].pai === 'P' && kept[2].pai === 'N',
    `${pos} 副露应保持时间顺序（最早 P 在前）`,
  );
}

if (failures > 0) {
  console.error(`review daiminkan regression FAILED (${failures} issues)`);
  process.exit(1);
}
console.log('review daiminkan regression OK (real kakan encoding, obs matrix, rewind semantics, validation, width)');
