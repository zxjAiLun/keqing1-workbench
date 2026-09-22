import type { DiscardEntry, MeldEntry } from "../../types/battle";
import {
  buildMeldDisplayTiles,
  getKakanStackOffset,
  getMeldTileOrientation,
  getSeatModel,
  type MeldDisplayTile,
  type SeatPosition,
} from "./seatLayout.ts";
import { DISC_COLS, DISC_GAP, MELD_TILE_GAP } from "./tableLayout.ts";
import { TILE_SIZES } from "./tileSizes.ts";

type Orientation = 0 | 90 | 180 | 270;
interface TileRect {
  x: number;
  y: number;
  width: number;
  height: number;
  orientation: Orientation;
}
interface TileLayout<T> {
  width: number;
  height: number;
  tiles: (T & TileRect)[];
}

/**
 * 在玩家自己的视角排好整组，再将位置、尺寸和牌面一起旋转。
 * 只转牌面不转 tile box / 组内顺序，会让对家和下家的鸣牌来源左右颠倒。
 */
function rotateLayout<T extends TileRect>(
  width: number,
  height: number,
  tiles: T[],
  position: SeatPosition,
): TileLayout<T> {
  const rotation = getSeatModel(position).tileOrientation;
  const sideways = rotation === 90 || rotation === 270;
  return {
    width: sideways ? height : width,
    height: sideways ? width : height,
    tiles: tiles.map((tile) => {
      let x = tile.x;
      let y = tile.y;
      if (rotation === 90) {
        x = height - tile.y - tile.height;
        y = tile.x;
      } else if (rotation === 180) {
        x = width - tile.x - tile.width;
        y = height - tile.y - tile.height;
      } else if (rotation === 270) {
        x = tile.y;
        y = width - tile.x - tile.width;
      }
      return {
        ...tile,
        x,
        y,
        width: sideways ? tile.height : tile.width,
        height: sideways ? tile.width : tile.height,
        orientation: ((tile.orientation + rotation) % 360) as Orientation,
      };
    }),
  };
}

export function layoutMeldTiles(
  actor: number,
  meld: MeldEntry,
  position: SeatPosition,
  size: keyof typeof TILE_SIZES,
): TileLayout<MeldDisplayTile> {
  const display = buildMeldDisplayTiles(actor, meld);
  if (!display.length) return { width: 0, height: 0, tiles: [] };
  const dim = TILE_SIZES[size];
  const hasStack = display.some((tile) => tile.stackedOn !== undefined);
  const height = hasStack ? Math.max(dim.h, 2 * dim.w + MELD_TILE_GAP) : dim.h;
  const tiles: (MeldDisplayTile & TileRect)[] = [];
  let cursor = 0;
  for (const tile of display) {
    const width = tile.rotated ? dim.h : dim.w;
    const tileHeight = tile.rotated ? dim.w : dim.h;
    const base = tile.stackedOn === undefined ? null : tiles[tile.stackedOn];
    const offset = getKakanStackOffset("south", size);
    tiles.push({
      ...tile,
      x: base ? base.x + offset.x : cursor,
      y: base ? base.y + offset.y : height - tileHeight,
      width,
      height: tileHeight,
      orientation: getMeldTileOrientation("south", tile.rotated),
    });
    if (!base) cursor += width + MELD_TILE_GAP;
  }
  return rotateLayout(cursor - MELD_TILE_GAP, height, tiles, position);
}

/** 六张一行，行高固定，横置立直牌与普通牌统一对齐到玩家侧的外沿。 */
export function layoutDiscardTiles(
  discards: DiscardEntry[],
  position: SeatPosition,
): TileLayout<{ discard: DiscardEntry; discardIndex: number }> {
  const dim = TILE_SIZES.normal;
  let cursor = 0;
  let width = 0;
  const tiles = discards.map((discard, discardIndex) => {
    if (discardIndex % DISC_COLS === 0) cursor = 0;
    const rotated = Boolean(discard.reach_declared);
    const tileWidth = rotated ? dim.h : dim.w;
    const tileHeight = rotated ? dim.w : dim.h;
    const tile = {
      discard,
      discardIndex,
      x: cursor,
      y: Math.floor(discardIndex / DISC_COLS) * (dim.h + DISC_GAP) + dim.h - tileHeight,
      width: tileWidth,
      height: tileHeight,
      orientation: getMeldTileOrientation("south", rotated),
    };
    width = Math.max(width, cursor + tileWidth);
    cursor += tileWidth + DISC_GAP;
    return tile;
  });
  const rows = Math.ceil(discards.length / DISC_COLS);
  const height = rows ? rows * dim.h + (rows - 1) * DISC_GAP : 0;
  return rotateLayout(width, height, tiles, position);
}
