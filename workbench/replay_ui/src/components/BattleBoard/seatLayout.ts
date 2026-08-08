import type { MeldEntry } from "../../types/battle";
import { TILE_SIZES } from "./tileSizes.ts";
import { BASE_TABLE_WIDTH, MELD_GROUP_GAP } from "./tableLayout.ts";

// ── 自家底部固定区域常量（Commit A 收口 + R2 手牌起点）───────────────────
// shell 右边缘保持 1240（right margin 40）不动，宽度由真实牌桌坐标推导：
// shell 左边缘 = meldRight − shellWidth = SELF_HAND_ORIGIN_X_PX（hand lane 左边界）。
// 手牌 tile row 左吸附，并在 lane 内额外应用可收缩的 SELF_HAND_LEFT_OFFSET：
// 可见第一张牌起点 = handLeft + effectiveOffset（见 computeSelfHandContentOffset）。
// 副露 lane 右吸附（meldRight = 1240 恒定）。
// 最坏组合：4 组最宽副露（daiminkan，south large：66 + 3×48 + 3×4 = 222）≈ 897px，
// 加暗手 1 张 + 摸牌 = 2 张可见（2×48 + 1 + 4 = 101px），再计固定 24px gap；
// shell 1080 → 四副露 hand lane = 1080 − 897 − 24 = 159 ≥ 101，不裁切、不重叠。
export const SELF_HAND_ORIGIN_X_PX = 160;
/** 手牌内容（tile row + 柱状图）相对 lane 左边缘的目标左偏移：3 个牌的宽度。 */
export const SELF_HAND_LEFT_OFFSET = 3 * TILE_SIZES.large.w;

/**
 * 手牌内容在 lane 内的有效左偏移（P1 修复：偏移必须受 lane 可用宽度约束）。
 * 大屏常用 0~2 副露保持完整 desiredOffset；3+ 副露 lane 变窄时自动收缩，
 * 保证 effectiveOffset + handContentWidth ≤ handLaneWidth（不压 24px 间隔、不重叠副露）。
 */
export function computeSelfHandContentOffset(
  desiredOffset: number,
  handLaneWidth: number,
  handContentWidth: number,
): number {
  return Math.max(0, Math.min(desiredOffset, handLaneWidth - handContentWidth));
}
export const SELF_SEAT_SIDE_MARGIN = 40;
export const SELF_SEAT_SHELL_WIDTH_PX =
  BASE_TABLE_WIDTH - SELF_SEAT_SIDE_MARGIN - SELF_HAND_ORIGIN_X_PX;
export const SELF_HAND_MELD_GAP = 24;

export type SeatPosition = "south" | "north" | "east" | "west";
export type LayoutAxis = "row" | "column";
export type RelativeCallSide = "left" | "across" | "right" | "self";

export interface SeatModel {
  position: SeatPosition;
  tileOrientation: 0 | 90 | 180 | 270;
  concealedAxis: LayoutAxis;
  concealedReverse: boolean;
  meldAxis: LayoutAxis;
  meldPlacement: "before" | "after";
  labelPlacement: "top" | "bottom" | "left" | "right";
}

export interface MeldDisplayTile {
  tile: string;
  rotated: boolean;
  stackedOn?: number;
  hidden?: boolean;
}

const SEAT_MODELS: Record<SeatPosition, SeatModel> = {
  south: {
    position: "south",
    tileOrientation: 0,
    concealedAxis: "row",
    concealedReverse: false,
    meldAxis: "row",
    meldPlacement: "after",
    labelPlacement: "bottom",
  },
  north: {
    position: "north",
    tileOrientation: 180,
    concealedAxis: "row",
    concealedReverse: true,
    meldAxis: "row",
    meldPlacement: "before",
    labelPlacement: "top",
  },
  east: {
    position: "east",
    tileOrientation: 270,
    concealedAxis: "column",
    concealedReverse: true,
    meldAxis: "column",
    meldPlacement: "after",
    labelPlacement: "right",
  },
  west: {
    position: "west",
    tileOrientation: 90,
    concealedAxis: "column",
    concealedReverse: false,
    meldAxis: "column",
    meldPlacement: "before",
    labelPlacement: "left",
  },
};

export function getSeatModel(position: SeatPosition): SeatModel {
  return SEAT_MODELS[position];
}

export function getRelativeCallSide(actor: number, target: number | null | undefined): RelativeCallSide {
  if (target == null || target === actor) return "self";
  const delta = (target - actor + 4) % 4;
  if (delta === 1) return "right";
  if (delta === 2) return "across";
  if (delta === 3) return "left";
  return "self";
}

function getClaimedTileIndex(tileCount: number, side: RelativeCallSide): number {
  if (tileCount <= 1) return 0;
  if (side === "left") return 0;
  if (side === "right") return tileCount - 1;
  if (side === "across") return Math.floor((tileCount - 1) / 2);
  return tileCount - 1;
}

function buildCalledMeldTiles(
  actor: number,
  target: number | null | undefined,
  handTiles: string[],
  calledTile: string,
): MeldDisplayTile[] {
  const claimedIndex = getClaimedTileIndex(handTiles.length + 1, getRelativeCallSide(actor, target));
  const displayTiles: MeldDisplayTile[] = [];
  let handIndex = 0;
  for (let idx = 0; idx < handTiles.length + 1; idx++) {
    if (idx === claimedIndex) {
      displayTiles.push({ tile: calledTile, rotated: true });
    } else {
      displayTiles.push({ tile: handTiles[handIndex], rotated: false });
      handIndex += 1;
    }
  }
  return displayTiles;
}

export function validateMeldTiles(meld: MeldEntry): boolean {
  // 空牌/空字符串禁止渲染成空白牌框
  if (meld.consumed.some((tile) => !tile)) {
    if (typeof console !== "undefined") console.error("Malformed meld (empty consumed tile)", meld);
    return false;
  }
  let ok = true;
  switch (meld.type) {
    case "chi":
    case "pon":
      ok = meld.consumed.length === 2 && Boolean(meld.pai);
      break;
    case "daiminkan":
      ok = meld.consumed.length === 3 && Boolean(meld.pai);
      break;
    case "ankan":
      ok = meld.consumed.length === 4;
      break;
    case "kakan":
      ok = meld.consumed.length === 4;
      break;
    default:
      ok = false;
  }
  if (!ok && typeof console !== "undefined") console.error("Malformed meld (tile count)", meld);
  return ok;
}

export function buildMeldDisplayTiles(actor: number, meld: MeldEntry): MeldDisplayTile[] {
  // 校验失败即拒绝渲染：不产生空白牌框或 undefined 牌
  if (!validateMeldTiles(meld)) return [];
  if (meld.type === "kakan") {
    // kakan consumed = [手牌1, 手牌2, 被鸣, 加杠]：前 3 张为原 pon，第 4 张叠在被鸣牌上
    const baseHandTiles = meld.consumed.slice(0, 2);
    const calledTile = meld.consumed[2] ?? meld.pai;
    const addedTile = meld.consumed[3];
    const baseDisplayTiles = buildCalledMeldTiles(actor, meld.target, baseHandTiles, calledTile);
    const claimedIndex = baseDisplayTiles.findIndex(tile => tile.rotated);
    // R2：第四张加杠牌与被鸣牌同为横置（rotated:true），叠在被鸣牌正上方。
    return [...baseDisplayTiles, { tile: addedTile, rotated: true, stackedOn: claimedIndex }];
  }

  if (meld.type === "ankan") {
    const tiles = meld.consumed.slice(0, 4);
    return tiles.map((tile, idx) => ({
      tile,
      rotated: false,
      hidden: idx === 0 || idx === tiles.length - 1,
    }));
  }

  return buildCalledMeldTiles(actor, meld.target, [...meld.consumed], meld.pai);
}

/**
 * 副露组内单张牌的渲染朝向（R2 统一 helper，供 MeldBlock 与 checker 共用）。
 * 普通副露牌使用该座的 tileOrientation；横置被鸣牌与 kakan 第四张按四家矩阵：
 *   south 90 / north 270 / east 0 / west 180。
 */
export function getMeldTileOrientation(
  position: SeatPosition,
  rotated: boolean,
): 0 | 90 | 180 | 270 {
  if (!rotated) {
    if (position === "east") return 90;
    if (position === "west") return 270;
    return getSeatModel(position).tileOrientation;
  }
  if (position === "south") return 90;
  if (position === "north") return 270;
  if (position === "east") return 0;
  return 180;
}

/**
 * 副露显示顺序（south 右吸附规则）。
 * melds 数组按时间顺序（最早在前）；south 副露 lane 右吸附于右侧边栏，
 * 因此最早副露应位于最右、后附露依次向左排 → 反转。
 * 其余座位保持时间顺序。
 */
export function orderMeldsForDisplay(
  melds: MeldEntry[],
  position: SeatPosition,
): MeldEntry[] {
  if (position === "south") return [...melds].reverse();
  return melds;
}

/**
 * kakan 叠牌相对基础被鸣牌 tile box 的确定性偏移（R2）。
 * 方向延续四家"朝牌桌中心叠放"语义，偏移量为半张牌（w/2）：
 *   south 向上、north 向下、east 向右（朝中心）、west 向左（朝中心）。
 */
export function getKakanStackOffset(
  position: SeatPosition,
  size: "small" | "normal" | "large",
): { x: number; y: number } {
  const lift = Math.ceil(TILE_SIZES[size].w / 2);
  switch (position) {
    case "south":
      return { x: 0, y: -lift };
    case "north":
      return { x: 0, y: lift };
    case "east":
      return { x: lift, y: 0 };
    case "west":
      return { x: -lift, y: 0 };
  }
}

/** 自家手牌区实际渲染宽度（含摸牌）。与 MahjongTable 的 flex 布局保持一致。 */
export function computeSelfHandWidth(
  handCount: number,
  hasTsumoPai: boolean,
  tileWidth: number,
  tileGap: number,
  drawGap: number,
): number {
  const tileCount = handCount + (hasTsumoPai ? 1 : 0);
  // flex 子元素数 = tileCount，普通 gap 数 = tileCount - 1；��牌另加 drawGap margin
  const normalGapCount =
    Math.max(0, handCount - 1)
    + (hasTsumoPai && handCount > 0 ? 1 : 0);
  return tileCount * tileWidth + normalGapCount * tileGap + (hasTsumoPai ? drawGap : 0);
}

/**
 * 自家底部固定区域的确定性几何（Commit A 收口）。
 *
 * 布局契约：
 *   - shell 拥有确定宽度（SELF_SEAT_SHELL_WIDTH_PX），右吸附于牌桌右侧
 *     （CSS `right: SELF_SEAT_SIDE_MARGIN`）；
 *   - SelfHandLane 宽度 = shell − meldLaneWidth − handMeldGap（可计算，非随暗手伸缩）；
 *   - 手牌与副露之间 gap 恒定 = handMeldGap；
 *   - SelfMeldLane 右吸附 → meldRight = tableWidth − rightMargin（真实牌桌坐标），
 *     对任意 0~4 副露不变。
 *
 * 手牌内容（left-aligned）恒小于 lane 宽度：手牌张数与副露数反比
 * （13 − 3×meldCount 暗手 + 摸牌），因此 0~4 副露、13/14 张摸打都不裁切。
 */
export interface SelfSeatGeometry {
  shellWidth: number;
  tableWidth: number;
  rightMargin: number;
  handMeldGap: number;
  handLaneWidth: number;
  meldLaneWidth: number;
  handLeft: number;
  handRight: number;
  meldLeft: number;
  meldRight: number;
}

export function computeSelfSeatGeometry(params: {
  shellWidth: number;
  tableWidth: number;
  rightMargin: number;
  handMeldGap: number;
  meldLaneWidth: number;
}): SelfSeatGeometry {
  const { shellWidth, tableWidth, rightMargin, handMeldGap, meldLaneWidth } = params;
  const handLaneWidth = Math.max(shellWidth - meldLaneWidth - handMeldGap, 0);
  // 真实牌桌坐标：shell 右边缘 = tableWidth − rightMargin，shell 左边缘 = meldRight − shellWidth
  const meldRight = tableWidth - rightMargin;
  const handLeft = meldRight - shellWidth;
  const handRight = handLeft + handLaneWidth;
  const meldLeft = handRight + handMeldGap;
  return {
    shellWidth,
    tableWidth,
    rightMargin,
    handMeldGap,
    handLaneWidth,
    meldLaneWidth,
    handLeft,
    handRight,
    meldLeft,
    meldRight,
  };
}

/**
 * south 副露 lane 的保守宽度（用于几何校验）。
 * south 使用 large tile；组内最宽为 daiminkan：1 张横置（box 宽 = tile 高）+ 3 张直立
 * + 3 个组内 gap（MeldBlock south 为 4px）。0 组返回 0。
 */
export function computeSouthMeldLaneWidth(meldCount: number): number {
  if (meldCount <= 0) return 0;
  const tileWidth = TILE_SIZES.large.w;
  const rotatedBoxWidth = TILE_SIZES.large.h; // 横置 90/270 后 box 宽 = 原高
  const innerTileGap = 4; // MeldBlock 对 south 使用的组内 gap
  const maxGroupWidth = rotatedBoxWidth + 3 * tileWidth + 3 * innerTileGap;
  return meldCount * maxGroupWidth + (meldCount - 1) * MELD_GROUP_GAP;
}
