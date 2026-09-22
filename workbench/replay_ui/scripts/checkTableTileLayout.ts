// Geometry regression: no browser or backend needed. Expected screen directions
// are explicit, not copied from the renderer's own orientation helper.
import assert from 'node:assert/strict';
import { layoutMeldTiles, layoutDiscardTiles } from '../src/components/BattleBoard/tableTileLayout.ts';
import { getSeatRowLayout, orderMeldsForDisplay, computeSouthMeldLaneWidth, type SeatPosition } from '../src/components/BattleBoard/seatLayout.ts';
import { CENTER_SIZE, POND_INSET, DISC_GAP, MELD_TILE_GAP, MELD_GROUP_GAP } from '../src/components/BattleBoard/tableLayout.ts';
import { TILE_SIZES } from '../src/components/BattleBoard/tileSizes.ts';
import type { MeldEntry } from '../src/types/battle.ts';

const seats: SeatPosition[] = ['south', 'west', 'north', 'east'];
const expected = {
  south: { normal: 0, called: 90, axis: 'x', sign: 1, direction: 'row', align: 'flex-end' },
  west: { normal: 270, called: 0, axis: 'y', sign: -1, direction: 'column-reverse', align: 'flex-end' },
  north: { normal: 180, called: 270, axis: 'x', sign: -1, direction: 'row-reverse', align: 'flex-start' },
  east: { normal: 90, called: 180, axis: 'y', sign: 1, direction: 'column', align: 'flex-start' },
} as const;
type Rect = { x: number; y: number; width: number; height: number };
const overlaps = (a: Rect, b: Rect) => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
function checkBounds(layout: { width: number; height: number; tiles: Rect[] }) {
  for (const [i, tile] of layout.tiles.entries()) {
    assert.ok(tile.x >= 0 && tile.y >= 0 && tile.x + tile.width <= layout.width && tile.y + tile.height <= layout.height, 'tile inside reserved box');
    for (const other of layout.tiles.slice(i + 1)) assert.ok(!overlaps(tile, other), 'tile faces must not overlap');
  }
}
function edge(tile: Rect, seat: SeatPosition) {
  switch (seat) {
    case 'south': return tile.y + tile.height;
    case 'north': return tile.y;
    case 'east': return tile.x;
    case 'west': return tile.x + tile.width;
  }
}
let cases = 0;
// All four absolute actors, all four viewing players, all three sources.
for (let actor = 0; actor < 4; actor++) for (let view = 0; view < 4; view++) {
  const seat = seats[(actor - view + 4) % 4];
  const want = expected[seat];
  assert.deepEqual(getSeatRowLayout(seat), { flexDirection: want.direction, alignItems: want.align });
  for (const size of ['small', 'normal', 'large'] as const) for (const delta of [3, 2, 1]) {
    for (const type of ['chi', 'pon', 'daiminkan', 'kakan', 'ankan'] as const) {
      if (type === 'chi' && delta !== 3) continue;
      const consumed = type === 'chi' ? ['4p', '6p'] : type === 'pon' ? ['5p', '5p'] : type === 'daiminkan' ? ['5p', '5p', '5p'] : ['5p', '5p', '5pr', '5p'];
      const meld: MeldEntry = { type, pai: '5pr', consumed, target: type === 'ankan' ? actor : (actor + delta) % 4 };
      const snapshot = JSON.stringify(meld);
      const layout = layoutMeldTiles(actor, meld, seat, size);
      const base = layout.tiles.filter(t => t.stackedOn === undefined);
      const calledIndex = delta === 3 ? 0 : delta === 2 ? 1 : base.length - 1;
      assert.equal(layout.tiles.length, type === 'pon' || type === 'chi' ? 3 : 4);
      checkBounds(layout);
      assert.equal(JSON.stringify(meld), snapshot, 'layout must not mutate replay state');
      assert.equal(layout.tiles.filter(t => t.tile === '5pr').length, 1, 'red five identity preserved');
      assert.equal(new Set(base.map(t => edge(t, seat))).size, 1, 'all base tiles share the table-side baseline');
      for (let i = 1; i < base.length; i++) assert.ok((base[i][want.axis] - base[i - 1][want.axis]) * want.sign > 0, 'source slots follow player-local left to right');
      if (type === 'ankan') {
        assert.deepEqual(base.map(t => Boolean(t.hidden)), [true, false, false, true]);
        assert.ok(base.every(t => t.orientation === want.normal));
      } else {
        assert.equal(base.findIndex(t => t.rotated), calledIndex, 'left/across/right source slot');
        assert.equal(base[calledIndex].orientation, want.called, `${seat} horizontal tile orientation`);
        assert.ok(base.filter(t => !t.rotated).every(t => t.orientation === want.normal));
      }
      if (type === 'kakan') {
        const stacked = layout.tiles.find(t => t.stackedOn !== undefined)!;
        const called = base[calledIndex];
        assert.equal(stacked.stackedOn, calledIndex);
        assert.equal(stacked.orientation, want.called);
        const dim = TILE_SIZES[size];
        const dx = stacked.x - called.x, dy = stacked.y - called.y;
        const lift = dim.w + MELD_TILE_GAP;
        assert.deepEqual([dx, dy], seat === 'south' ? [0, -lift] : seat === 'north' ? [0, lift] : seat === 'east' ? [lift, 0] : [-lift, 0]);
      }
      cases++;
    }
  }
}
// Owner's example: P1 (screen right) pons P2 (screen top). The called E belongs
// at the TOP of the right-side group, upright on screen, never upside down.
const owner = layoutMeldTiles(1, { type: 'pon', pai: 'E', consumed: ['E', 'E'], target: 2 }, 'west', 'normal');
assert.equal(owner.tiles[2].orientation, 0);
assert.equal(owner.tiles[2].y, 0);

const chronological: MeldEntry[] = [
  { type: 'pon', pai: 'E', consumed: ['E', 'E'], target: 1 },
  { type: 'kakan', pai: 'P', consumed: ['P', 'P', 'P', 'P'], target: 2 },
  { type: 'daiminkan', pai: 'C', consumed: ['C', 'C', 'C'], target: 3 },
  { type: 'ankan', pai: 'N', consumed: ['N', 'N', 'N', 'N'], target: 0 },
];
assert.deepEqual(orderMeldsForDisplay(chronological).map(m => m.pai), ['N', 'C', 'P', 'E']);
assert.equal(chronological[0].pai, 'E');
for (let count = 0; count <= 4; count++) {
  const groups = chronological.slice(0, count).map(m => layoutMeldTiles(0, m, 'south', 'large'));
  const width = groups.reduce((sum, g) => sum + g.width, 0) + Math.max(count - 1, 0) * MELD_GROUP_GAP;
  assert.ok(width <= computeSouthMeldLaneWidth(count), 'hand lane reserves enough real meld width');
}
// Fixed row stride, stable source order, complete fourth row, no river-corner
// collisions with a sideways declaration at ANY of the six columns.
for (const count of [0, 1, 6, 7, 12, 18, 24]) for (let reachIndex = 0; reachIndex < 6; reachIndex++) {
  const discards = Array.from({ length: count }, (_, i) => ({ pai: 'E', tsumogiri: i % 2 === 0, reach_declared: i === reachIndex }));
  const all: Rect[] = [];
  const inset = CENTER_SIZE / 2 + POND_INSET;
  for (const seat of seats) {
    const layout = layoutDiscardTiles(discards, seat);
    const want = expected[seat];
    checkBounds(layout);
    assert.equal(layout.tiles.length, count);
    for (const tile of layout.tiles) assert.equal(tile.orientation, tile.discardIndex === reachIndex ? want.called : want.normal);
    for (let row = 0; row < Math.ceil(count / 6); row++) {
      const tiles = layout.tiles.slice(row * 6, row * 6 + 6);
      assert.equal(new Set(tiles.map(t => edge(t, seat))).size, 1, 'river row outer baseline');
      if (row > 0) assert.equal(Math.abs(edge(tiles[0], seat) - edge(layout.tiles[(row - 1) * 6], seat)), TILE_SIZES.normal.h + DISC_GAP, 'fixed row stride');
    }
    const ox = seat === 'south' ? -inset : seat === 'north' ? inset - layout.width : seat === 'east' ? -inset - layout.width : inset;
    const oy = seat === 'south' ? inset : seat === 'north' ? -inset - layout.height : seat === 'east' ? -inset : inset - layout.height;
    for (const tile of layout.tiles) all.push({ ...tile, x: tile.x + ox, y: tile.y + oy });
    cases++;
  }
  for (let i = 0; i < all.length; i++) for (const other of all.slice(i + 1)) assert.ok(!overlaps(all[i], other), 'four rivers must not collide at corners');
}
console.log(`table tile layout OK (${cases} meld/river cases, all actors/viewpoints/sources/sizes, owner E-pon case, full kakan faces, baselines, corner clearance)`);
