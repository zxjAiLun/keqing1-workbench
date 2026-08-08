// src/replay_ui/src/components/BattleBoard/MahjongTable.tsx
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { Tile, TileBack } from "./Tile";
import { TILE_SIZES } from "./tileSizes";
import {
  BASE_TABLE_WIDTH,
  BASE_TABLE_HEIGHT,
  CENTER_SIZE,
  CENTER_INFO_SIZE,
  SOUTH_BOTTOM_OFFSET,
  NORTH_TOP_OFFSET,
  EAST_LEFT_OFFSET,
  WEST_RIGHT_OFFSET,
  POND_INSET,
  HAND_TILE_GAP,
  HAND_TILE_GAP_TIGHT,
  HAND_DRAW_GAP,
  MELD_GROUP_GAP,
  SIDE_ZONE_GAP,
  DISC_COLS,
  DISC_GAP,
} from "./tableLayout";
import { ActionBar } from "./ActionBar";
import type { BattleState, Action, DiscardEntry, MeldEntry } from "../../types/battle";
import type { LogitTileData } from "../../utils/replayAdapter";
import { BAKAZE_CN, JIKAZE_CN } from "../../utils/constants";
import { sortHand } from "../../utils/tileUtils";
import { buildMeldDisplayTiles, computeSelfHandContentOffset, computeSelfHandWidth, computeSouthMeldLaneWidth, getKakanStackOffset, getMeldTileOrientation, getSeatModel, orderMeldsForDisplay, SELF_HAND_LEFT_OFFSET, SELF_HAND_MELD_GAP, SELF_SEAT_SHELL_WIDTH_PX, SELF_SEAT_SIDE_MARGIN, type LayoutAxis, type SeatPosition } from "./seatLayout";
import { TABLECLOTH_OPTIONS } from "./tableclothOptions";
import type { TableclothId } from "./tableclothOptions";

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------
const SEAT_COLORS = ["var(--seat-0)", "var(--seat-1)", "var(--seat-2)", "var(--seat-3)"];

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function chunkDiscards(discards: DiscardEntry[], cols: number): DiscardEntry[][] {
  const rows: DiscardEntry[][] = [];
  for (let i = 0; i < discards.length; i += cols)
    rows.push(discards.slice(i, i + cols));
  return rows;
}



// ---------------------------------------------------------------------------
// 弃牌堆 tile 尺寸
// ---------------------------------------------------------------------------
const SELF_HAND_GAP = HAND_TILE_GAP;
const SELF_HAND_DRAW_GAP = HAND_DRAW_GAP;
const SELF_HAND_BAR_MAX_HEIGHT = Math.round(TILE_SIZES.large.h * 1.05);
const SELF_HAND_BAR_WIDTH = TILE_SIZES.large.w * 0.8;
const SELF_HAND_BAR_MIN_VISIBLE_PCT = 1;
const SELF_HAND_TEACHER_BAR_GAP = 2;

function normalizeOrientation(orientation: number): 0 | 90 | 180 | 270 {
  const normalized = ((orientation % 360) + 360) % 360;
  if (normalized === 90 || normalized === 180 || normalized === 270) return normalized;
  return 0;
}

function getTileBox(size: "small" | "normal" | "large", orientation: 0 | 90 | 180 | 270) {
  const dim = TILE_SIZES[size];
  const sideways = orientation === 90 || orientation === 270;
  return { width: sideways ? dim.h : dim.w, height: sideways ? dim.w : dim.h };
}

function decisionFrameStyle(color: string, inset: number): React.CSSProperties {
  return {
    position: "absolute",
    inset,
    border: `2px solid ${color}`,
    borderRadius: 4,
    pointerEvents: "none",
    zIndex: 4,
  };
}

function OrientedTile({
  tile,
  size,
  orientation,
  dimmed,
  outlined,
  outlineColor,
  outlineWidth,
}: {
  tile: string;
  size: "small" | "normal" | "large";
  orientation: 0 | 90 | 180 | 270;
  dimmed?: boolean;
  outlined?: boolean;
  outlineColor?: string;
  outlineWidth?: number;
}) {
  const { width, height } = getTileBox(size, orientation);
  return (
    <div style={{ width, height, position: "relative", flexShrink: 0 }}>
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: `translate(-50%, -50%) rotate(${orientation}deg)`,
          filter: dimmed ? "brightness(0.6)" : undefined,
          outline: outlined ? `${outlineWidth ?? 2}px solid ${outlineColor ?? "var(--gold)"}` : undefined,
          outlineOffset: "1px",
          borderRadius: outlined ? 3 : undefined,
          transition: "transform var(--table-transition-mid), filter var(--table-transition-fast), outline var(--table-transition-fast)",
        }}
      >
        <Tile tile={tile} size={size} />
      </div>
    </div>
  );
}

function normalizeClaimKey(pai: string): string {
  return pai.endsWith("r") ? pai.slice(0, -1) : pai;
}

function buildClaimedDiscardFlags(
  discards: DiscardEntry[][],
  melds: MeldEntry[][],
): boolean[][] {
  const flags = discards.map((row) => row.map(() => false));
  for (let actor = 0; actor < melds.length; actor += 1) {
    for (const meld of melds[actor] ?? []) {
      if (meld.type === "ankan") continue;
      const target = meld.target;
      if (target == null || target === actor) continue;
      const targetDiscards = discards[target] ?? [];
      const targetFlags = flags[target] ?? [];
      const want = normalizeClaimKey(meld.pai);
      for (let i = targetDiscards.length - 1; i >= 0; i -= 1) {
        if (targetFlags[i]) continue;
        if (normalizeClaimKey(targetDiscards[i].pai) !== want) continue;
        targetFlags[i] = true;
        break;
      }
    }
  }
  return flags;
}

function DiscardTile({ d, position, zIndex, claimed }: { d: DiscardEntry; position: SeatPosition; zIndex?: number; claimed?: boolean }) {
  const modelOrientation = getSeatModel(position).tileOrientation;
  const baseOrientation =
    position === "east" ? 90
    : position === "west" ? 270
    : modelOrientation;
  const orientation = normalizeOrientation(baseOrientation + (d.reach_declared ? 90 : 0));
  return (
    <div style={{ position: "relative", zIndex }}>
      <OrientedTile
        tile={d.pai}
        size="normal"
        orientation={orientation}
        dimmed={d.tsumogiri}
        outlined={Boolean(claimed || d.reach_declared)}
        outlineColor={claimed ? "#ef4444" : undefined}
      />
    </div>
  );
}

// 自家（底部）：左下角起，行左→右，新行向下
function DiscardPondSouth({ discards, claimedFlags = [] }: { discards: DiscardEntry[]; claimedFlags?: boolean[] }) {
  const rows = chunkDiscards(discards, DISC_COLS);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1, alignItems: "flex-start" }}>
      {rows.map((row, ri) => (
        <div key={ri} style={{ display: "flex", flexDirection: "row", gap: DISC_GAP }}>
          {row.map((d, di) => {
            const globalIndex = ri * DISC_COLS + di;
            return (
              <DiscardTile
                key={`${globalIndex}-${d.pai}-${d.tsumogiri ? "t" : "d"}-${d.reach_declared ? "r" : "n"}`}
                d={d}
                position="south"
                claimed={claimedFlags[globalIndex]}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

// 对家（顶部）：右上角起，行右→左，新行向上（column-reverse）
function DiscardPondNorth({ discards, claimedFlags = [] }: { discards: DiscardEntry[]; claimedFlags?: boolean[] }) {
  const rows = chunkDiscards(discards, DISC_COLS);
  return (
    <div style={{ display: "flex", flexDirection: "column-reverse", gap: 1, alignItems: "flex-end" }}>
      {rows.map((row, ri) => (
        <div key={ri} style={{ display: "flex", flexDirection: "row-reverse", gap: DISC_GAP }}>
          {row.map((d, di) => {
            const globalIndex = ri * DISC_COLS + di;
            return (
              <DiscardTile
                key={`${globalIndex}-${d.pai}-${d.tsumogiri ? "t" : "d"}-${d.reach_declared ? "r" : "n"}`}
                d={d}
                position="north"
                claimed={claimedFlags[globalIndex]}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

// 左家（上家）：牌旋转90°，列从上→下，新列向左
function DiscardPondLeft({ discards, claimedFlags = [] }: { discards: DiscardEntry[]; claimedFlags?: boolean[] }) {
  const cols = chunkDiscards(discards, DISC_COLS);
  return (
    <div style={{ display: "flex", flexDirection: "row-reverse", gap: 1, alignItems: "flex-start" }}>
      {cols.map((col, ci) => (
        <div key={ci} style={{ display: "flex", flexDirection: "column", gap: DISC_GAP, position: "relative", zIndex: cols.length - ci }}>
          {col.map((d, di) => {
            const globalIndex = ci * DISC_COLS + di;
            return (
              <DiscardTile
                key={`${globalIndex}-${d.pai}-${d.tsumogiri ? "t" : "d"}-${d.reach_declared ? "r" : "n"}`}
                d={d}
                position="east"
                zIndex={di + 1}
                claimed={claimedFlags[globalIndex]}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

// 右家（下家）：牌旋转90°+180°=270°，列从下→上，新列向右
function DiscardPondRight({ discards, claimedFlags = [] }: { discards: DiscardEntry[]; claimedFlags?: boolean[] }) {
  const cols = chunkDiscards(discards, DISC_COLS);
  return (
    <div style={{ display: "flex", flexDirection: "row", gap: 1, alignItems: "flex-end" }}>
      {cols.map((col, ci) => (
        <div key={ci} style={{ display: "flex", flexDirection: "column-reverse", gap: DISC_GAP, position: "relative", zIndex: cols.length - ci }}>
          {col.map((d, di) => {
            const globalIndex = ci * DISC_COLS + di;
            return (
              <DiscardTile
                key={`${globalIndex}-${d.pai}-${d.tsumogiri ? "t" : "d"}-${d.reach_declared ? "r" : "n"}`}
                d={d}
                position="west"
                zIndex={di + 1}
                claimed={claimedFlags[globalIndex]}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单家玩家区
// ---------------------------------------------------------------------------
function getFlexDirection(axis: "row" | "column", reverse: boolean) {
  if (axis === "row") return reverse ? "row-reverse" : "row";
  return reverse ? "column-reverse" : "column";
}

function getConcealedCountForSeat(melds: MeldEntry[]): number {
  return Math.max(0, 13 - melds.length * 3);
}

function hasPendingExtraTile(state: BattleState, pid: number): boolean {
  return state.replay_draw_actor === pid;
}

type DiscardHole = number | "draw" | null;

function deterministicHoleIndex(pid: number, handLength: number, discards: DiscardEntry[]): number {
  const last = discards[discards.length - 1];
  const seedText = `${pid}:${handLength}:${discards.length}:${last?.pai ?? ""}:${last?.tsumogiri ? 1 : 0}`;
  let hash = 0;
  for (let i = 0; i < seedText.length; i++) {
    hash = (hash * 31 + seedText.charCodeAt(i)) >>> 0;
  }
  return handLength > 0 ? hash % (handLength + 1) : 0;
}

function getDisplayedOpponentTiles(position: SeatPosition, hand: string[]): string[] {
  const tiles = sortHand([...hand], null);
  return position === "east" || position === "west" ? tiles.reverse() : tiles;
}

function getOpponentDrawGapStyle(
  position: SeatPosition,
  axis: LayoutAxis,
  reverse: boolean,
  gap: number,
) {
  if (axis === "row") {
    return { [reverse ? "marginRight" : "marginLeft"]: gap };
  }
  if (position === "east") return { marginBottom: gap };
  if (position === "west") return { marginTop: gap };
  return { [reverse ? "marginBottom" : "marginTop"]: gap };
}

function getOpponentDiscardHole(
  position: SeatPosition,
  hand: string[],
  discards: DiscardEntry[],
  lastDiscard: BattleState["last_discard"],
  pid: number,
  fallbackLength: number,
): DiscardHole {
  if (!lastDiscard || lastDiscard.actor !== pid) return null;
  const last = discards[discards.length - 1];
  if (!last || last.pai !== lastDiscard.pai) return null;
  if (last.tsumogiri) return "draw";
  if (hand.length === 0) return deterministicHoleIndex(pid, fallbackLength, discards);

  const displayed = getDisplayedOpponentTiles(position, hand);
  const withDiscard = getDisplayedOpponentTiles(position, [...hand, lastDiscard.pai]);
  const preferredIndex = withDiscard.indexOf(lastDiscard.pai);
  if (preferredIndex >= 0) return preferredIndex;
  return deterministicHoleIndex(pid, displayed.length, discards);
}

function MeldBlock({ pid, meld, position }: { pid: number; meld: MeldEntry; position: SeatPosition }) {
  const model = getSeatModel(position);
  const meldTileSize = position === "south" ? "large" : "normal";
  const displayTiles = buildMeldDisplayTiles(pid, meld);
  const flowDirection = getFlexDirection(model.meldAxis, false);
  const stackOffset = getKakanStackOffset(position, meldTileSize);
  return (
    <div style={{
      display: "flex",
      flexDirection: flowDirection,
      gap: position === "south" ? 4 : 3,
      alignItems: position === "south" ? "flex-end" : "center",
    }}>
      {displayTiles.map((entry, idx) => {
        const orientation = getMeldTileOrientation(position, entry.rotated);
        const stackedTile = displayTiles.find((candidate) => candidate.stackedOn === idx);
        const { width, height } = getTileBox(meldTileSize, orientation);
        return (
          <div key={`${entry.tile}-${idx}`} style={{ width, height, position: "relative", flexShrink: 0 }}>
            {entry.hidden ? (
              <TileBack size={meldTileSize} orientation={orientation} />
            ) : (
              <OrientedTile
                tile={entry.tile}
                size={meldTileSize}
                orientation={orientation}
              />
            )}
            {stackedTile && (
              // R2：kakan 第四张与被鸣牌同为横置，叠在基础 tile box 内，
              // 向牌桌中心偏移半张牌，保持约一半重叠。
              <div style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                transform: `translate(${stackOffset.x}px, ${stackOffset.y}px)`,
                pointerEvents: "none",
                zIndex: 2,
              }}>
                <OrientedTile
                  tile={stackedTile.tile}
                  size={meldTileSize}
                  orientation={getMeldTileOrientation(position, stackedTile.rotated)}
                />
              </div>
            )}
          </div>
        );
      }).filter((_, idx) => displayTiles[idx].stackedOn === undefined)}
    </div>
  );
}

function MeldArea({ pid, melds, position }: { pid: number; melds: MeldEntry[]; position: SeatPosition }) {
  if (melds.length === 0) return null;
  const model = getSeatModel(position);
  const flowDirection = getFlexDirection(model.meldAxis, model.meldPlacement === "before");
  // south 最早副露在最右（右吸附于边栏），后附露依次向左；其余保持时间顺序。
  const orderedMelds = orderMeldsForDisplay(melds, position);
  return (
    <div style={{ display: "flex", flexDirection: flowDirection, gap: MELD_GROUP_GAP, flexShrink: 0 }}>
      {orderedMelds.map((meld, idx) => (
        <MeldBlock key={`${meld.type}-${meld.pai}-${idx}`} pid={pid} meld={meld} position={position} />
      ))}
    </div>
  );
}

function ConcealedHand({
  position,
  count = 13,
  showDrawTile = false,
  discardHole = null,
  onClick,
}: {
  position: SeatPosition;
  count?: number;
  showDrawTile?: boolean;
  discardHole?: DiscardHole;
  onClick?: () => void;
}) {
  const model = getSeatModel(position);
  const concealedOrientation: 0 | 90 | 180 | 270 =
    position === "east" ? 90
    : position === "west" ? 270
    : model.tileOrientation;
  const concealedAxis = model.concealedAxis;
  const concealedReverse = model.concealedReverse;
  const flowDirection = getFlexDirection(concealedAxis, concealedReverse);
  const gap = position === "north" ? HAND_TILE_GAP_TIGHT : HAND_TILE_GAP;
  const drawGap = HAND_DRAW_GAP;
  const { width, height } = getTileBox("normal", concealedOrientation);
  const drawGapStyle = getOpponentDrawGapStyle(position, concealedAxis, concealedReverse, drawGap);
  const reservedMainSpan = count * (concealedAxis === "row" ? width : height) + Math.max(count - 1, 0) * gap;
  const reservedCrossSpan = concealedAxis === "row" ? height : width;
  const reservedTotalSpan = reservedMainSpan + drawGap + (concealedAxis === "row" ? width : height);
  const frameStyle =
    concealedAxis === "row"
      ? { width: reservedTotalSpan, height: reservedCrossSpan }
      : { width: reservedCrossSpan, height: reservedTotalSpan };
  const mainBackTiles = (
    <>
      {discardHole !== "draw" && Array.from({ length: count + (discardHole !== null ? 1 : 0) }, (_, idx) => {
        const isHole = discardHole !== null && idx === discardHole;
        const tileZIndex = concealedReverse ? idx + 1 : count - idx;
        return (
          <div key={idx} style={{ width, height, flexShrink: 0, position: "relative", zIndex: tileZIndex, opacity: isHole ? 0 : 1, transition: "opacity var(--table-transition-fast)" }}>
            {!isHole && <TileBack size="normal" orientation={concealedOrientation} />}
          </div>
        );
      })}
      {discardHole === "draw" && Array.from({ length: count }, (_, idx) => {
        const tileZIndex = concealedReverse ? idx + 1 : count - idx;
        return (
          <div key={idx} style={{ width, height, flexShrink: 0, position: "relative", zIndex: tileZIndex }}>
            <TileBack size="normal" orientation={concealedOrientation} />
          </div>
        );
      })}
    </>
  );
  const drawBackTile = (
    <>
      {showDrawTile && (
        <div style={{ ...drawGapStyle, position: "relative", flexShrink: 0 }}>
          <TileBack size="normal" orientation={concealedOrientation} />
        </div>
      )}
      {discardHole === "draw" && !showDrawTile && (
        <div style={{ ...drawGapStyle, width, height, flexShrink: 0 }} />
      )}
    </>
  );
  return (
    <div
      style={{
        display: "flex",
        justifyContent: concealedReverse ? "flex-end" : "flex-start",
        alignItems: concealedAxis === "row" ? "center" : concealedReverse ? "flex-end" : "flex-start",
        padding:
          position === "north" ? "0 2px 0"
          : position === "east" ? "0 2px 0"
          : position === "west" ? "0 2px 0"
          : "0 0 0",
        cursor: onClick ? "pointer" : undefined,
      }}
      onClick={onClick}
    >
      <div style={{ ...frameStyle, position: "relative", flexShrink: 0 }}>
        <div
          style={{
            display: "flex",
            flexDirection: flowDirection,
            gap,
            width: "100%",
            height: "100%",
            justifyContent: concealedReverse ? "flex-end" : "flex-start",
            alignItems: concealedAxis === "row" ? "center" : concealedReverse ? "flex-end" : "flex-start",
          }}
        >
          {concealedAxis === "column" ? drawBackTile : null}
          {mainBackTiles}
          {concealedAxis === "row" ? drawBackTile : null}
        </div>
      </div>
    </div>
  );
}

function RevealedOpponentHand({
  position,
  hand,
  showDrawTile = false,
  discardHole = null,
  onClick,
}: {
  position: SeatPosition;
  hand: string[];
  showDrawTile?: boolean;
  discardHole?: DiscardHole;
  onClick?: () => void;
}) {
  const model = getSeatModel(position);
  const revealedOrientation: 0 | 90 | 180 | 270 =
    position === "east" ? 90
    : position === "west" ? 270
    : model.tileOrientation;
  const sortedTiles = getDisplayedOpponentTiles(position, hand);
  const drawTile = showDrawTile && hand.length > 0 ? hand[hand.length - 1] : null;
  const mainTiles = showDrawTile ? getDisplayedOpponentTiles(position, hand.slice(0, -1)) : sortedTiles;
  const revealedAxis = model.concealedAxis;
  const revealedReverse = model.concealedReverse;
  const flowDirection = getFlexDirection(revealedAxis, revealedReverse);
  const gap = HAND_TILE_GAP;
  const drawGap = HAND_DRAW_GAP;
  const { width, height } = getTileBox("normal", revealedOrientation);
  const drawGapStyle = getOpponentDrawGapStyle(position, revealedAxis, revealedReverse, drawGap);
  const mainRevealedTiles = (
    <>
      {discardHole !== "draw" && Array.from({ length: mainTiles.length + (discardHole !== null ? 1 : 0) }, (_, idx) => {
        const isHole = discardHole !== null && idx === discardHole;
        const tile = isHole ? null : mainTiles[idx - (discardHole !== null && idx > discardHole ? 1 : 0)];
        return (
          <div key={`${tile ?? "hole"}-${idx}`} style={{ width, height, flexShrink: 0 }}>
            {tile && <OrientedTile tile={tile} size="normal" orientation={revealedOrientation} />}
          </div>
        );
      })}
      {discardHole === "draw" && mainTiles.map((tile, idx) => (
        <OrientedTile
          key={`${tile}-${idx}`}
          tile={tile}
          size="normal"
          orientation={revealedOrientation}
        />
      ))}
    </>
  );
  const revealedDrawTile = (
    <>
      {showDrawTile && drawTile && (
        <div style={{ ...drawGapStyle, flexShrink: 0 }}>
          <OrientedTile tile={drawTile} size="normal" orientation={revealedOrientation} />
        </div>
      )}
      {discardHole === "draw" && !showDrawTile && (
        <div style={{ ...drawGapStyle, width, height, flexShrink: 0 }} />
      )}
    </>
  );
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        flexDirection: flowDirection,
        gap,
        padding:
          position === "north" ? "0 2px 0"
          : position === "east" ? "0 2px 0"
          : position === "west" ? "0 2px 0"
          : "0 0 0",
        cursor: onClick ? "pointer" : undefined,
      }}
    >
      {revealedAxis === "column" ? revealedDrawTile : null}
      {mainRevealedTiles}
      {revealedAxis === "row" ? revealedDrawTile : null}
    </div>
  );
}

function PlayerZone({
  pid,
  position,
  hand,
  tsumoPai,
  // discards: 弃牌已移至中央 DiscardPond，这里不再使用
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  discards: _discards,
  melds,
  highlightIdx,
  onTileClick,
  logitData,
  activeTeacherModel,
  hideLogitHints,
  onSelfHandHintToggle,
  showReplayDrawTile,
  concealedCount,
  revealedHand,
  discardHole,
  onOpponentHandToggle,
}: {
  pid: number;
  position: "south" | "north" | "east" | "west";
  hand: string[];
  tsumoPai?: string | null;
  discards: DiscardEntry[];
  melds: MeldEntry[];
  isHuman?: boolean;
  highlightTile?: string | null;
  highlightIdx?: number | null;
  onTileClick?: (tile: string, idx: number) => void;
  logitData?: LogitTileData[];
  activeTeacherModel?: string | null;
  hideLogitHints?: boolean;
  onSelfHandHintToggle?: () => void;
  showReplayDrawTile?: boolean;
  concealedCount?: number;
  revealedHand?: string[] | null;
  discardHole?: DiscardHole;
  onOpponentHandToggle?: () => void;
}) {
  const model = getSeatModel(position);

  // ── 自家（south）──
  if (position === "south") {
    // 当前 BattleState 约定：self hand 为 13 张主手牌，摸牌单独走 tsumoPai。
    // 这里不能再从 hand 里删一次 tsumoPai，否则摸到同名牌时会把手里原有那张误删。
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const sortedHand = useMemo(() => {
      return sortHand([...hand], null);
    }, [hand, tsumoPai]);
    const hasLogitHints = Boolean(logitData?.length);
    const showLogitHints = hasLogitHints && !hideLogitHints;

    // 手牌宽度按实际数量计算：门清 13/14 张、1/2/3 副露时自然收缩到 10/11、7/8、4/5 张，
    // 四副露只剩 1/2 张——不再固定预留 14 张宽度（避免大吊车时手牌与副露之间出现巨大空白）。
    // 有摸牌时 flex 内含 n+1 个子元素（n 个普通 gap + drawGap margin），宽度必须与实际 flex 一致。
    const actualHandWidth = computeSelfHandWidth(
      sortedHand.length,
      Boolean(tsumoPai),
      TILE_SIZES.large.w,
      SELF_HAND_GAP,
      SELF_HAND_DRAW_GAP,
    );

    // P1 修复：144px 目标偏移必须受 lane 可用宽度约束。
    // hand lane 宽度 = shell − 副露 lane（保守按最宽 daiminkan 估算）− 固定 gap。
    // P2 稳定化：偏移按该副露数下的"最大摸牌态"宽度预留，而不是当前实际宽度，
    // 使 3~4 副露时 pre/post（摸/不摸）第一张牌起点保持一致、不跳动。
    const handLaneWidth = Math.max(
      0,
      SELF_SEAT_SHELL_WIDTH_PX - SELF_HAND_MELD_GAP - computeSouthMeldLaneWidth(melds.length),
    );
    const maxConcealedCount = Math.max(13 - 3 * melds.length, 0);
    const maxHandContentWidth = computeSelfHandWidth(
      maxConcealedCount,
      true,
      TILE_SIZES.large.w,
      SELF_HAND_GAP,
      SELF_HAND_DRAW_GAP,
    );
    const effectiveHandOffset = computeSelfHandContentOffset(SELF_HAND_LEFT_OFFSET, handLaneWidth, maxHandContentWidth);

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "flex-end",
          gap: SELF_HAND_MELD_GAP,
          width: SELF_SEAT_SHELL_WIDTH_PX,
          maxWidth: "100%",
        }}
      >
        {/* selfHandLane：flex:1 占据 shell 剩余宽度（shell − meldLane − 固定 gap），
            手牌内容左吸附（tile row 从 lane 左边缘开始，x = SELF_HAND_ORIGIN_X_PX）；
            柱状图在 tile row 容器内绝对定位、跟随牌面；
            手牌张数变化只在 lane 内伸缩，不推动副露锚点。 */}
        <div
          onClick={hasLogitHints ? onSelfHandHintToggle : undefined}
          style={{
            display: "flex",
            flexDirection: "row",
            justifyContent: "flex-start",
            alignItems: "flex-end",
            position: "relative",
            flex: "1 1 auto",
            minWidth: 0,
            cursor: hasLogitHints ? "pointer" : undefined,
          }}
        >
            {/* 手牌：左对齐 + 可收缩左偏移（3+ 副露自动收窄） */}
            <div style={{ display: "flex", gap: SELF_HAND_GAP, flexWrap: "nowrap", width: actualHandWidth, position: "relative", marginLeft: effectiveHandOffset }}>
            {/* 柱状图层（回放模式，绝对定位在手牌上方） */}
            {showLogitHints && (
              <div style={{
                position: "absolute", bottom: "100%", left: 0,
                display: "flex", gap: SELF_HAND_GAP, paddingBottom: 3,
                pointerEvents: "none", alignItems: "flex-end",
                width: "100%",
              }}>
                {(() => {
                  const visibleLogitData = logitData ?? [];
                  const maxPct = Math.max(...visibleLogitData.map((item) => item.pct), 1);
                  return [...sortedHand, ...(tsumoPai ? [tsumoPai] : [])].map((tile, i) => {
                    const d = visibleLogitData.find(x => x.pai === tile && x.isTsumo === (tsumoPai ? i === sortedHand.length : false))
                      ?? visibleLogitData.find(x => x.pai === tile);
                    const normalizedPct = d ? d.pct / maxPct : 0;
                    const localHeight = d && d.pct >= SELF_HAND_BAR_MIN_VISIBLE_PCT
                      ? Math.round(normalizedPct * SELF_HAND_BAR_MAX_HEIGHT)
                      : 0;
                    const localBarColor = d?.isChosen && d?.isGt ? "#8e44ad"
                      : d?.isChosen ? "#e74c3c"
                      : d?.isGt ? "#27ae60"
                      : d?.score !== undefined ? "var(--accent)"
                      : "transparent";
                    const teacherBars = d?.teacherBars ?? [];
                    const teacherBarWidth = teacherBars.length > 0
                      ? Math.max(2, (SELF_HAND_BAR_WIDTH - SELF_HAND_TEACHER_BAR_GAP * (teacherBars.length - 1)) / teacherBars.length)
                      : SELF_HAND_BAR_WIDTH;
                    return (
                      <div key={`bar-${tile}-${i}`} style={{
                        width: TILE_SIZES.large.w,
                        marginLeft: i === sortedHand.length && tsumoPai ? SELF_HAND_DRAW_GAP : 0,
                        display: "flex", alignItems: "flex-end", justifyContent: "center", flexShrink: 0,
                      }}>
                        {teacherBars.length > 0 ? (
                          <div style={{
                            width: SELF_HAND_BAR_WIDTH,
                            display: "flex",
                            alignItems: "flex-end",
                            justifyContent: "center",
                            gap: SELF_HAND_TEACHER_BAR_GAP,
                          }}>
                            {teacherBars.map((bar) => {
                              const height = bar.pct >= SELF_HAND_BAR_MIN_VISIBLE_PCT
                                ? Math.round((bar.pct / 100) * SELF_HAND_BAR_MAX_HEIGHT)
                                : 0;
                              const isActiveTeacher = !activeTeacherModel || bar.model === activeTeacherModel;
                              return (
                                <div
                                  key={bar.model}
                                  style={{
                                    width: teacherBarWidth,
                                    height,
                                    background: height > 0
                                      ? isActiveTeacher ? "#8e44ad" : "#9ca3af"
                                      : "transparent",
                                    borderRadius: "2px 2px 0 0",
                                    transition: "height 0.15s ease, background 0.15s ease",
                                  }}
                                />
                              );
                            })}
                          </div>
                        ) : localHeight > 0 ? (
                          <div style={{
                            width: SELF_HAND_BAR_WIDTH, height: localHeight,
                            background: localBarColor,
                            borderRadius: "2px 2px 0 0",
                            transition: "height 0.15s ease",
                          }} />
                        ) : null}
                      </div>
                    );
                  });
                })()}
              </div>
            )}

              {sortedHand.map((tile, i) => {
                const d = logitData?.find(x => x.pai === tile && !x.isTsumo) ?? logitData?.find(x => x.pai === tile);
                const showDecisionFrames = showLogitHints;
                return (
                  <div key={`${tile}-${i}`} style={{ width: TILE_SIZES.large.w, height: TILE_SIZES.large.h, flexShrink: 0, position: "relative" }}>
                    <Tile tile={tile} size="large" selected={!showDecisionFrames && i === highlightIdx} onClick={() => onTileClick?.(tile, i)} />
                    {showDecisionFrames && d?.isChosen && (
                      <div style={decisionFrameStyle("#8e44ad", 0)} />
                    )}
                    {showDecisionFrames && d?.isGt && (
                      <div style={decisionFrameStyle("#27ae60", d?.isChosen ? 4 : 0)} />
                    )}
                  </div>
                );
              })}
              {tsumoPai && (
                <div
                  style={{
                    width: TILE_SIZES.large.w,
                    height: TILE_SIZES.large.h,
                    flexShrink: 0,
                    cursor: onTileClick ? "pointer" : undefined,
                    marginLeft: SELF_HAND_DRAW_GAP,
                  }}
                  onClick={() => onTileClick?.(tsumoPai, sortedHand.length)}
                >
                  <Tile tile={tsumoPai} size="large" selected={!showLogitHints && sortedHand.length === highlightIdx} />
                </div>
              )}
            </div>
          </div>
          {/* selfMeldLane：flex:0 右吸附，锚点 = shell 右侧，不随手牌变化 */}
          <div style={{ flexShrink: 0, paddingBottom: 0 }}>
            <MeldArea pid={pid} melds={melds} position={position} />
          </div>
        </div>
    );
  }

  // ── 对面（north）── 整体旋转180deg
  if (position === "north") {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, paddingTop: 1 }}>
        <div style={{ display: "flex", flexDirection: "row", alignItems: "flex-end", gap: 5 }}>
          {model.meldPlacement === "before" && <MeldArea pid={pid} melds={melds} position={position} />}
          {revealedHand ? (
            <RevealedOpponentHand position={position} hand={revealedHand} showDrawTile={showReplayDrawTile} discardHole={discardHole} onClick={onOpponentHandToggle} />
          ) : (
            <ConcealedHand
              position={position}
              count={concealedCount}
              showDrawTile={showReplayDrawTile}
              discardHole={discardHole}
              onClick={onOpponentHandToggle}
            />
          )}
          {model.meldPlacement === "after" && <MeldArea pid={pid} melds={melds} position={position} />}
        </div>
      </div>
    );
  }

  // ── 左家（east）── 竖列暗牌，副露贴右手边
  if (position === "east") {
    return (
      <div style={{ display: "flex", flexDirection: "row", alignItems: "flex-start", gap: SIDE_ZONE_GAP }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center" }}>
          {revealedHand ? (
            <RevealedOpponentHand position={position} hand={revealedHand} showDrawTile={showReplayDrawTile} discardHole={discardHole} onClick={onOpponentHandToggle} />
          ) : (
            <ConcealedHand
              position={position}
              count={concealedCount}
              showDrawTile={showReplayDrawTile}
              discardHole={discardHole}
              onClick={onOpponentHandToggle}
            />
          )}
          <MeldArea pid={pid} melds={melds} position={position} />
        </div>
      </div>
    );
  }

  // ── 右家（west）── 竖列暗牌，副露贴右手边
  return (
    <div style={{ display: "flex", flexDirection: "row", alignItems: "flex-start", gap: SIDE_ZONE_GAP }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, alignItems: "center" }}>
        <MeldArea pid={pid} melds={melds} position={position} />
        {revealedHand ? (
          <RevealedOpponentHand position={position} hand={revealedHand} showDrawTile={showReplayDrawTile} discardHole={discardHole} onClick={onOpponentHandToggle} />
        ) : (
          <ConcealedHand
            position={position}
            count={concealedCount}
            showDrawTile={showReplayDrawTile}
            discardHole={discardHole}
            onClick={onOpponentHandToggle}
          />
        )}
      </div>
    </div>
  );
}

// 玩家标签子组件 - 2D天凤风扁平小标签（分数在中央区域显示）
function PlayerLabel({ color, playerName, jikaze, reached, isActive }: {
  color: string; playerName: string; jikaze: string; reached: boolean; isActive: boolean;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4, lineHeight: 1 }}>
      <div
        style={{
          width: 5,
          height: 5,
          borderRadius: "50%",
          background: color,
          opacity: isActive ? 1 : 0.65,
        }}
      />
      <span style={{ fontWeight: 600, fontSize: 10, color }}>{playerName}</span>
      <span style={{ fontSize: 8, color: "var(--table-text-muted)" }}>{jikaze}</span>
      {reached && <span style={{ fontSize: 8, fontWeight: 700, color: "var(--gold)" }}>立</span>}
    </div>
  );
}

function SeatNameMarker({
  position,
  color,
  playerName,
  jikaze,
  reached,
  isActive,
  style,
}: {
  position: SeatPosition;
  color: string;
  playerName: string;
  jikaze: string;
  reached: boolean;
  isActive: boolean;
  style: React.CSSProperties;
}) {
  const rotation =
    position === "south" ? 0
    : position === "north" ? 180
    : position === "east" ? 90
    : 270;
  return (
    <div
      style={{
        position: "absolute",
        zIndex: 18,
        pointerEvents: "none",
        ...style,
      }}
    >
      <div style={{ transform: `rotate(${rotation}deg)`, transformOrigin: "center center" }}>
        <PlayerLabel
          color={color}
          playerName={playerName}
          jikaze={jikaze}
          reached={reached}
          isActive={isActive}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 中心面板
// ---------------------------------------------------------------------------
function formatScoreDelta(delta: number): string {
  if (delta === 0) return "±0";
  return delta > 0 ? `+${delta.toLocaleString()}` : delta.toLocaleString();
}

function getJikazeLabel(pid: number, oya: number): string {
  return JIKAZE_CN[((pid - oya) % 4 + 4) % 4] ?? "";
}

function ScoreMarker({
  score,
  diffText,
  jikaze,
  color,
  style,
}: {
  score: number;
  diffText?: string;
  jikaze: string;
  color: string;
  style: React.CSSProperties;
}) {
  const sign = score < 0 ? "-" : "";
  const scoreText = Math.abs(score).toLocaleString().replace(/,/g, "");
  const scoreMain = sign + (scoreText.length > 2 ? scoreText.slice(0, -2) : scoreText);
  const scoreSuffix = scoreText.length > 2 ? scoreText.slice(-2) : "";
  return (
    <div
      style={{
        position: "absolute",
        display: "flex",
        alignItems: "center",
        gap: 3,
        pointerEvents: "none",
        ...style,
      }}
    >
      <span style={{ fontSize: 10, fontWeight: 800, color }}>
        {jikaze}
      </span>
      {diffText ? (
        <span style={{ fontSize: 10, fontWeight: 700, color, fontFamily: "Menlo, monospace" }}>
          {diffText}
        </span>
      ) : (
        <span style={{ display: "inline-flex", alignItems: "baseline", color, fontFamily: "Menlo, monospace" }}>
          <span style={{ fontSize: 13, fontWeight: 800 }}>{scoreMain}</span>
          {scoreSuffix && <span style={{ fontSize: 8, fontWeight: 700 }}>{scoreSuffix}</span>}
        </span>
      )}
    </div>
  );
}

function BoardInfoCorner({
  dora_markers,
  honba,
}: {
  dora_markers: string[];
  honba: number;
}) {
  return (
    <div
      style={{
        position: "absolute",
        top: 8,
        left: 10,
        zIndex: 24,
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "3px 0",
      }}
    >
      {/* 宝牌 */}
      <div style={{ display: "flex", gap: 1 }}>
        {Array.from({ length: 5 }, (_, i) => (
          <div
            key={i}
            style={{
              width: TILE_SIZES.small.w,
              height: TILE_SIZES.small.h,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: i < dora_markers.length ? "transparent" : "var(--table-text-faint)",
              border: i < dora_markers.length ? "none" : "1px solid var(--table-text-faint)",
            }}
          >
            {i < dora_markers.length && <Tile tile={dora_markers[i]} size="small" />}
          </div>
        ))}
      </div>
      {/* 分隔线 */}
      <div style={{ width: 1, height: 20, background: "var(--table-text-faint)" }} />
      {/* 本场 */}
      <span style={{
        fontSize: 10,
        fontWeight: 700,
        color: "var(--gold)",
        fontFamily: "Menlo, monospace",
      }}>
        {honba}本
      </span>
    </div>
  );
}

function CenterPanel({
  bakaze,
  kyoku,
  honba,
  scores,
  oya,
  humanId,
  topPid,
  leftPid,
  rightPid,
  showScoreDiff,
  onToggleScoreMode,
}: {
  bakaze: string;
  kyoku: number;
  honba: number;
  scores: number[];
  oya: number;
  humanId: number;
  topPid: number;
  leftPid: number;
  rightPid: number;
  showScoreDiff: boolean;
  onToggleScoreMode: () => void;
}) {
  const baseScore = scores[humanId] ?? 0;
  const diffText = (pid: number) =>
    showScoreDiff
      ? formatScoreDelta((scores[pid] ?? 0) - baseScore)
      : undefined;

  return (
    <div
      onClick={onToggleScoreMode}
      style={{
        position: "absolute",
        top: "50%", left: "50%",
        transform: "translate(-50%, -50%)",
        width: CENTER_INFO_SIZE, height: CENTER_INFO_SIZE,
        background: "var(--center-bg)",
        border: "1px solid var(--center-border)",
        borderRadius: 1,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        gap: 1, zIndex: 20,
        cursor: "pointer",
        userSelect: "none",
      }}
    >
      {/* 顶部：对面（北）分数 */}
      <ScoreMarker
        score={scores[topPid] ?? 0}
        diffText={diffText(topPid)}
        jikaze={getJikazeLabel(topPid, oya)}
        color={SEAT_COLORS[topPid]}
        style={{ top: 4, left: "50%", transform: "translateX(-50%)" }}
      />
      {/* 左侧：左家（东）分数 */}
      <ScoreMarker
        score={scores[leftPid] ?? 0}
        diffText={diffText(leftPid)}
        jikaze={getJikazeLabel(leftPid, oya)}
        color={SEAT_COLORS[leftPid]}
        style={{ left: 3, top: "50%", transform: "translateY(-50%) rotate(90deg)", transformOrigin: "left center" }}
      />
      {/* 右侧：右家（西）分数 */}
      <ScoreMarker
        score={scores[rightPid] ?? 0}
        diffText={diffText(rightPid)}
        jikaze={getJikazeLabel(rightPid, oya)}
        color={SEAT_COLORS[rightPid]}
        style={{ right: 3, top: "50%", transform: "translateY(-50%) rotate(270deg)", transformOrigin: "right center" }}
      />
      {/* 底部：自家（南）分数 */}
      <ScoreMarker
        score={scores[humanId] ?? 0}
        diffText={diffText(humanId)}
        jikaze={getJikazeLabel(humanId, oya)}
        color={SEAT_COLORS[humanId]}
        style={{ bottom: 4, left: "50%", transform: "translateX(-50%)" }}
      />

      {/* 中央：局数（大字） */}
      <span style={{
        fontSize: 23,
        fontWeight: 800,
        color: "var(--table-text)",
        fontFamily: "Menlo, monospace",
        letterSpacing: "0.02em",
        lineHeight: 1.1,
      }}>
        {BAKAZE_CN[bakaze] ?? bakaze}{kyoku}局
      </span>

      {/* 本場 */}
      <span style={{
        fontSize: 10,
        fontWeight: 600,
        color: "var(--table-text-muted)",
        fontFamily: "Menlo, monospace",
      }}>
        {honba}本场
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function MahjongTable({
  state, onAction, isMyTurn, selectedTile, selectedTileIdx, onTileSelect,
  autoHora, setAutoHora, noMeld, setNoMeld, autoTsumogiri, setAutoTsumogiri,
  suppressActionBar = false,
  actionPending,
  mode = "battle",
  logitData,
  activeTeacherModel,
  revealedOpponentHands,
  onToggleOpponentHands,
}: {
  state: BattleState;
  onAction: (action: Action) => void;
  isMyTurn: boolean;
  selectedTile: string | null;
  selectedTileIdx?: number | null;
  onTileSelect: (tile: string | null, idx?: number | null) => void;
  autoHora: boolean;
  setAutoHora: (v: boolean) => void;
  noMeld: boolean;
  setNoMeld: (v: boolean) => void;
  autoTsumogiri: boolean;
  setAutoTsumogiri: (v: boolean) => void;
  suppressActionBar?: boolean;
  actionPending?: boolean;
  mode?: "battle" | "replay";
  logitData?: LogitTileData[];
  activeTeacherModel?: string | null;
  revealedOpponentHands?: Array<string[] | null> | null;
  onToggleOpponentHands?: () => void;
}) {
  const [reachPending, setReachPending] = useState(false);
  const [tableScale, setTableScale] = useState(1);
  const [showScoreDiff, setShowScoreDiff] = useState(false);
  const [hideSelfHandLogitHints, setHideSelfHandLogitHints] = useState(false);
  const boardViewportRef = useRef<HTMLDivElement | null>(null);
  const {
    player_info, hand, tsumo_pai, discards, melds, reached,
    dora_markers, scores, actor_to_move, bakaze, kyoku, honba, oya,
  } = state;

  const humanId  = state.human_player_id;
  const oppRight = (humanId + 1) % 4;
  const oppTop   = (humanId + 2) % 4;
  const oppLeft  = (humanId + 3) % 4;
  const terminalWinner = state.round_result?.type === "hora" && state.round_result.actor !== undefined
    ? Number(state.round_result.actor)
    : -1;
  const terminalRevealedHands = useMemo(() => {
    if (terminalWinner < 0 || !state.revealed_hands) return null;
    const hands = [null, null, null, null] as Array<string[] | null>;
    hands[terminalWinner] = state.revealed_hands[terminalWinner] ?? null;
    return hands;
  }, [terminalWinner, state.revealed_hands]);
  const effectiveRevealedOpponentHands = revealedOpponentHands ?? terminalRevealedHands;

  const [tablecloth, setTablecloth]             = useState<TableclothId>("default");
  const showActionBar = mode === "battle" && isMyTurn && !reachPending && !state.pending_reach[humanId] && !suppressActionBar;
  const showReachBanner = mode === "battle" && isMyTurn && (reachPending || state.pending_reach[humanId]);
  const replayPendingReachActor = mode === "replay"
    ? state.pending_reach.findIndex(Boolean)
    : -1;
  const topConcealedCount = getConcealedCountForSeat(melds[oppTop] || []);
  const leftConcealedCount = getConcealedCountForSeat(melds[oppLeft] || []);
  const rightConcealedCount = getConcealedCountForSeat(melds[oppRight] || []);
  const topShowExtraTile = hasPendingExtraTile(state, oppTop);
  const leftShowExtraTile = hasPendingExtraTile(state, oppLeft);
  const rightShowExtraTile = hasPendingExtraTile(state, oppRight);
  const topDiscardHole = getOpponentDiscardHole("north", effectiveRevealedOpponentHands?.[oppTop] ?? [], discards[oppTop] || [], state.last_discard, oppTop, topConcealedCount);
  const leftDiscardHole = getOpponentDiscardHole("east", effectiveRevealedOpponentHands?.[oppLeft] ?? [], discards[oppLeft] || [], state.last_discard, oppLeft, leftConcealedCount);
  const rightDiscardHole = getOpponentDiscardHole("west", effectiveRevealedOpponentHands?.[oppRight] ?? [], discards[oppRight] || [], state.last_discard, oppRight, rightConcealedCount);
  const claimedDiscardFlags = useMemo(
    () => buildClaimedDiscardFlags(discards, melds),
    [discards, melds],
  );
  const canToggleOpponentHands = mode === "replay" && Boolean(onToggleOpponentHands);
  const selfHandLogitKey = useMemo(() => {
    if (!logitData?.length) return "";
    return logitData
      .map((item) => {
        const teacherKey = (item.teacherBars ?? [])
          .map((bar) => `${bar.model}:${bar.pct.toFixed(3)}`)
          .join(",");
        return `${item.pai}:${item.isTsumo ? 1 : 0}:${item.pct.toFixed(3)}:${teacherKey}`;
      })
      .join("|");
  }, [logitData]);

  // 赤宝牌归一化：5mr->5m, 5pr->5p, 5sr->5s
  const normTile = (t: string) => t === "5mr" ? "5m" : t === "5pr" ? "5p" : t === "5sr" ? "5s" : t;
  // 找与 tile 对应的 dahai legal action（赤宝牌兼容匹配）
  const findDahaiAction = useCallback((tile: string) => {
    const norm = normTile(tile);
    return state.legal_actions.find(
      x => x.type === "dahai" && (x.pai === tile || normTile(x.pai ?? "") === norm)
    );
  }, [state.legal_actions]);

  const handleTileClick = useCallback((tile: string, idx: number) => {
    if (!isMyTurn) return;
    if (reachPending) {
      // 立直选牌模式：只允许选合法 dahai 牌
      const a = findDahaiAction(tile);
      if (a) { onAction(a); setReachPending(false); onTileSelect(null, null); }
      return;
    }
    if (selectedTileIdx === idx && selectedTile === tile) {
      // 二次点击同一张牌（同tile同index）=> 立即打出
      const a = findDahaiAction(tile);
      if (a) { onAction(a); onTileSelect(null, null); }
    } else {
      if (findDahaiAction(tile)) {
        onTileSelect(tile, idx);
      }
    }
  }, [isMyTurn, reachPending, selectedTile, selectedTileIdx, findDahaiAction, onAction, onTileSelect]);

  const handleAction = useCallback((action: Action) => {
    if (action.type === "reach") {
      // 点立直：先发 reach 动作，然后进入立直选牌模式
      onAction(action);
      setReachPending(true);
      onTileSelect(null, null);
      return;
    }
    if (selectedTile && action.type === "dahai" && action.pai !== selectedTile) {
      const a = findDahaiAction(selectedTile);
      if (a) { onAction(a); onTileSelect(null, null); return; }
    }
    onAction(action);
    onTileSelect(null, null);
    setReachPending(false);
  }, [selectedTile, findDahaiAction, onAction, onTileSelect]);

  useEffect(() => {
    if (mode !== "battle") {
      setReachPending(false);
      return;
    }
    if (!isMyTurn) {
      setReachPending(false);
      return;
    }
    if (state.pending_reach[humanId]) {
      setReachPending(true);
      return;
    }
    if (!actionPending) {
      setReachPending(false);
    }
  }, [mode, isMyTurn, state.pending_reach, humanId, actionPending]);

  useEffect(() => {
    const node = boardViewportRef.current;
    if (!node) return;
    const updateScale = () => {
      const widthScale = node.clientWidth / BASE_TABLE_WIDTH;
      const heightScale = node.clientHeight / BASE_TABLE_HEIGHT;
      setTableScale(Math.min(widthScale, heightScale, 1));
    };
    updateScale();
    const observer = new ResizeObserver(updateScale);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const applyPreference = () => {
      const stored = window.localStorage.getItem("keqing1.tablecloth") ?? window.localStorage.getItem("keqing.tablecloth");
      if (stored && TABLECLOTH_OPTIONS.some((opt) => opt.id === stored)) {
        setTablecloth(stored as typeof tablecloth);
      } else {
        setTablecloth("default");
      }
    };
    applyPreference();
    const onStorage = (event: StorageEvent) => {
      if (event.key === "keqing1.tablecloth" || event.key === "keqing.tablecloth") applyPreference();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    setHideSelfHandLogitHints(false);
  }, [selfHandLogitKey]);

  const tableBg = TABLECLOTH_OPTIONS.find(t => t.id === tablecloth)?.color ?? "#1a2744";
  const viewportBg = "var(--page-bg)";

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ background: viewportBg }}>

      {/* 牌桌主体 */}
      <div ref={boardViewportRef} style={{
        flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center",
        padding: 0, position: "relative",
        background: viewportBg,
        transition: "background var(--transition)",
      }}>
        <div style={{
          width: BASE_TABLE_WIDTH, height: BASE_TABLE_HEIGHT,
          background: tableBg,
          borderRadius: 0,
          border: "none",
          boxShadow: "none",
          position: "relative", overflow: "hidden",
          transform: `scale(${tableScale})`,
          transformOrigin: "center center",
          flexShrink: 0,
          transition: "background 0.3s ease",
        }}>

          <BoardInfoCorner dora_markers={dora_markers} honba={honba} />

          {/* 中心面板 */}
          <CenterPanel
            bakaze={bakaze}
            kyoku={kyoku}
            honba={honba}
            scores={scores}
            oya={oya}
            humanId={humanId}
            topPid={oppTop}
            leftPid={oppLeft}
            rightPid={oppRight}
            showScoreDiff={showScoreDiff}
            onToggleScoreMode={() => setShowScoreDiff((v) => !v)}
          />

          {/* 中央弃牌堆 */}
          <div style={{ position: "absolute", left: `calc(50% - ${CENTER_SIZE/2 + POND_INSET}px)`, top: `calc(50% + ${CENTER_SIZE/2 + POND_INSET}px)` }}>
            <DiscardPondSouth discards={discards[humanId] || []} claimedFlags={claimedDiscardFlags[humanId] || []} />
          </div>
          <div style={{ position: "absolute", left: `calc(50% + ${CENTER_SIZE/2 + POND_INSET}px)`, top: `calc(50% - ${CENTER_SIZE/2 + POND_INSET}px)`, transform: "translate(-100%, -100%)" }}>
            <DiscardPondNorth discards={discards[oppTop] || []} claimedFlags={claimedDiscardFlags[oppTop] || []} />
          </div>
          <div style={{ position: "absolute", left: `calc(50% - ${CENTER_SIZE/2 + POND_INSET}px)`, top: `calc(50% - ${CENTER_SIZE/2 + POND_INSET}px)`, transform: "translateX(-100%)" }}>
            <DiscardPondLeft discards={discards[oppLeft] || []} claimedFlags={claimedDiscardFlags[oppLeft] || []} />
          </div>
          <div style={{ position: "absolute", left: `calc(50% + ${CENTER_SIZE/2 + POND_INSET}px)`, top: `calc(50% + ${CENTER_SIZE/2 + POND_INSET}px)`, transform: "translateY(-100%)" }}>
            <DiscardPondRight discards={discards[oppRight] || []} claimedFlags={claimedDiscardFlags[oppRight] || []} />
          </div>

          <SeatNameMarker
            position="north"
            color={SEAT_COLORS[oppTop]}
            playerName={player_info[oppTop]?.name || `P${oppTop}`}
            jikaze={getJikazeLabel(oppTop, oya)}
            reached={reached[oppTop]}
            isActive={actor_to_move === oppTop}
            style={{ left: `calc(50% - ${CENTER_SIZE / 2 + 132}px)`, top: `calc(50% - ${CENTER_SIZE / 2 + 20}px)` }}
          />
          <SeatNameMarker
            position="east"
            color={SEAT_COLORS[oppLeft]}
            playerName={player_info[oppLeft]?.name || `P${oppLeft}`}
            jikaze={getJikazeLabel(oppLeft, oya)}
            reached={reached[oppLeft]}
            isActive={actor_to_move === oppLeft}
            style={{ left: `calc(50% - ${CENTER_SIZE / 2 + 132}px)`, top: `calc(50% + ${CENTER_SIZE / 2 + 12}px)` }}
          />
          <SeatNameMarker
            position="west"
            color={SEAT_COLORS[oppRight]}
            playerName={player_info[oppRight]?.name || `P${oppRight}`}
            jikaze={getJikazeLabel(oppRight, oya)}
            reached={reached[oppRight]}
            isActive={actor_to_move === oppRight}
            style={{ left: `calc(50% + ${CENTER_SIZE / 2 + 22}px)`, top: `calc(50% - ${CENTER_SIZE / 2 + 20}px)` }}
          />
          <SeatNameMarker
            position="south"
            color={SEAT_COLORS[humanId]}
            playerName={player_info[humanId]?.name || `P${humanId}`}
            jikaze={getJikazeLabel(humanId, oya)}
            reached={reached[humanId]}
            isActive={actor_to_move === humanId}
            style={{ left: `calc(50% + ${CENTER_SIZE / 2 + 22}px)`, top: `calc(50% + ${CENTER_SIZE / 2 + 12}px)` }}
          />

          {/* 南（自家）：固定区域右吸附，手牌/副露 lane 锚点稳定 */}
          <div style={{ position: "absolute", right: SELF_SEAT_SIDE_MARGIN, bottom: SOUTH_BOTTOM_OFFSET }}>
            <PlayerZone
              pid={humanId} position="south"
              hand={hand} tsumoPai={tsumo_pai}
              discards={discards[humanId] || []} melds={melds[humanId] || []}
              isHuman={true} highlightTile={selectedTile} highlightIdx={selectedTileIdx}
              onTileClick={mode === "replay" ? undefined : handleTileClick}
              logitData={logitData}
              activeTeacherModel={activeTeacherModel}
              hideLogitHints={hideSelfHandLogitHints}
              onSelfHandHintToggle={() => setHideSelfHandLogitHints((v) => !v)}
            />
          </div>

          {(showActionBar || showReachBanner) && (
            <div
              style={{
                position: "absolute",
                left: "50%",
                bottom: 168,
                transform: "translateX(-50%)",
                width: "min(1080px, calc(100% - 72px))",
                zIndex: 34,
                pointerEvents: "auto",
                display: "flex",
                justifyContent: "center",
              }}
            >
              {showActionBar ? (
                <ActionBar
                  legalActions={state.legal_actions}
                  onAction={handleAction}
                  disabled={!isMyTurn}
                />
              ) : (
                <div
                  style={{
                    fontSize: 16,
                    color: "var(--gold)",
                    fontWeight: 800,
                    padding: "14px 22px",
                    borderRadius: 16,
                    background: "linear-gradient(180deg, rgba(24,18,7,0.88) 0%, rgba(24,18,7,0.72) 100%)",
                    border: "1px solid var(--gold-border)",
                    boxShadow: "0 14px 40px rgba(0,0,0,0.32)",
                    backdropFilter: "blur(16px)",
                  }}
                >
                  立直宣言中，请点击要打出的牌
                </div>
              )}
            </div>
          )}

          {/* 北（对面） */}
          <div style={{ position: "absolute", left: "50%", top: NORTH_TOP_OFFSET, transform: "translateX(-50%)" }}>
            <PlayerZone
              pid={oppTop} position="north"
              hand={[]} discards={discards[oppTop] || []} melds={melds[oppTop] || []}
              isHuman={false}
              concealedCount={topConcealedCount}
              showReplayDrawTile={topShowExtraTile}
              revealedHand={effectiveRevealedOpponentHands?.[oppTop] ?? null}
              discardHole={topDiscardHole}
              onOpponentHandToggle={canToggleOpponentHands ? onToggleOpponentHands : undefined}
            />
          </div>

          {/* 东（左） */}
          <div style={{ position: "absolute", left: EAST_LEFT_OFFSET, top: "50%", transform: "translateY(-50%)" }}>
            <PlayerZone
              pid={oppLeft} position="east"
              hand={[]} discards={discards[oppLeft] || []} melds={melds[oppLeft] || []}
              isHuman={false}
              concealedCount={leftConcealedCount}
              showReplayDrawTile={leftShowExtraTile}
              revealedHand={effectiveRevealedOpponentHands?.[oppLeft] ?? null}
              discardHole={leftDiscardHole}
              onOpponentHandToggle={canToggleOpponentHands ? onToggleOpponentHands : undefined}
            />
          </div>

          {/* 西（右） */}
          <div style={{ position: "absolute", right: WEST_RIGHT_OFFSET, top: "50%", transform: "translateY(-50%)" }}>
            <PlayerZone
              pid={oppRight} position="west"
              hand={[]} discards={discards[oppRight] || []} melds={melds[oppRight] || []}
              isHuman={false}
              concealedCount={rightConcealedCount}
              showReplayDrawTile={rightShowExtraTile}
              revealedHand={effectiveRevealedOpponentHands?.[oppRight] ?? null}
              discardHole={rightDiscardHole}
              onOpponentHandToggle={canToggleOpponentHands ? onToggleOpponentHands : undefined}
            />
          </div>

          {/* 行动中指示 */}
          {mode === "battle" && actor_to_move !== null && actor_to_move !== humanId &&
           (!state.last_discard || state.last_discard.actor === actor_to_move) && (
            <div style={{
              position: "absolute", top: "50%", left: "50%",
              transform: "translate(-50%, -50%)", zIndex: 30, pointerEvents: "none",
            }}>
              <div
                style={{
                  padding: "3px 10px", borderRadius: 3, fontSize: 10, fontWeight: 600,
                  background: "rgba(0,0,0,0.5)", color: SEAT_COLORS[actor_to_move],
                  border: `1px solid ${SEAT_COLORS[actor_to_move]}60`,
                  whiteSpace: "nowrap",
                }}
              >
                {player_info[actor_to_move]?.name || `P${actor_to_move}`}
              </div>
            </div>
          )}

          {mode === "replay" && replayPendingReachActor >= 0 && (
            <div style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              zIndex: 32,
              pointerEvents: "none",
            }}>
              <div style={{
                padding: "6px 14px",
                borderRadius: 2,
                background: "rgba(24, 18, 7, 0.9)",
                border: "1px solid var(--gold-border)",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}>
                <div style={{
                  width: 20,
                  height: 20,
                  borderRadius: 2,
                  background: "var(--gold)",
                  color: "#23180a",
                  fontWeight: 900,
                  fontSize: 11,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}>
                  立
                </div>
                <div style={{ fontSize: 11, fontWeight: 700, color: "var(--gold)" }}>
                  {player_info[replayPendingReachActor]?.name || `P${replayPendingReachActor}`} 立直
                </div>
              </div>
            </div>
          )}

        </div>
      </div>

      {mode === "battle" && <div style={{ minHeight: 8, flexShrink: 0 }} />}

      {/* 底部固定设置栏 — 天凤风格低存在感控制条 */}
      {mode === "replay" ? <div style={{ height: 8, flexShrink: 0 }} /> : null}
      {mode === "battle" && <div style={{
        minHeight: 40, flexShrink: 0,
        borderTop: "1px solid var(--ctrlbar-border)",
        background: "var(--ctrlbar-bg)",
        display: "flex", alignItems: "center", justifyContent: "center",
        gap: 8, padding: "5px 16px", flexWrap: "wrap",
      }}>
        {[
          { label: "自动胡牌", value: autoHora, set: setAutoHora },
          { label: "不响应附露", value: noMeld, set: setNoMeld },
          { label: "自动摸切", value: autoTsumogiri, set: setAutoTsumogiri },
        ].map(({ label, value, set }) => (
          <button
            key={label}
            onClick={() => set(!value)}
            style={{
              padding: "3px 12px",
              borderRadius: 5,
              border: `1px solid ${value ? "rgba(212,168,83,0.4)" : "rgba(255,255,255,0.08)"}`,
              background: value ? "var(--ctrlbar-active-bg)" : "rgba(255,255,255,0.04)",
              color: value ? "var(--ctrlbar-active-text)" : "var(--ctrlbar-text)",
              fontSize: 12, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s",
              display: "flex", alignItems: "center", gap: 5,
              letterSpacing: "0.02em",
            }}
          >
            <span style={{
              width: 7, height: 7, borderRadius: "50%",
              background: value ? "rgba(212,168,83,0.9)" : "rgba(255,255,255,0.2)",
              display: "inline-block", flexShrink: 0,
              transition: "background 0.15s",
            }} />
            {label}
          </button>
        ))}
      </div>}

      {/* 全局 CSS 动画 */}
      <style>{`
        @keyframes seatPulse {
          0%, 100% { opacity: 0.7; }
          50% { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
