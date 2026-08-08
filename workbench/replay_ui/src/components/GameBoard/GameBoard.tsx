// src/replay_ui/src/components/GameBoard/GameBoard.tsx
import { useRef, useEffect, useCallback, useState } from 'react';
import type { DecisionLogEntry } from '../../types/replay';
import { sortHand } from '../../utils/tileUtils';
import { SEAT_NAMES_CN } from '../../utils/constants';

interface GameBoardProps {
  entry: DecisionLogEntry | null;
  playerId: number;
  activeActor: number | null;
  highlightTile: string | null;
  gtTile: string | null;
}

const TILE_W = 32;
const TILE_H = 44;
const TILE_GAP = 2;
const DISCARD_COLS = 6;

// ---------------------------------------------------------------------------
// CSS 变量读取工具（支持 :root 和 [data-theme]）
// ---------------------------------------------------------------------------
function readCssVar(name: string, fallback: string): string {
  const val = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return val || fallback;
}

// ---------------------------------------------------------------------------
// 绘制工具函数（接收颜色参数，主题无关）
// ---------------------------------------------------------------------------

/**
 * 绘制3D麻将牌效果
 * 白色背景 + 顶部/右边高光白边（模拟现实麻将牌）
 */
function drawTileImage(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement | undefined,
  x: number,
  y: number,
  w: number,
  h: number,
  borderColor?: string,
  borderWidth: number = 2,
  faceColor?: string,
  strokeColor?: string
) {
  const r = 3; // 圆角
  const face = faceColor ?? '#f5f0e8';
  const stroke = strokeColor ?? '#c8cdd6';

  // 牌主体
  ctx.fillStyle = face;
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fill();

  // 顶部3D白边
  const topGrad = ctx.createLinearGradient(x, y, x + w, y);
  topGrad.addColorStop(0, 'rgba(255,255,255,0.0)');
  topGrad.addColorStop(0.3, 'rgba(255,255,255,0.9)');
  topGrad.addColorStop(1.0, 'rgba(255,255,255,1)');
  ctx.fillStyle = topGrad;
  ctx.beginPath();
  ctx.roundRect(x, y, w, 3, [r, r, 0, 0]);
  ctx.fill();

  // 右边3D白边
  const rightGrad = ctx.createLinearGradient(x + w, y, x + w, y + h);
  rightGrad.addColorStop(0, 'rgba(255,255,255,0.9)');
  rightGrad.addColorStop(0.3, 'rgba(255,255,255,0.7)');
  rightGrad.addColorStop(1.0, 'rgba(255,255,255,0.3)');
  ctx.fillStyle = rightGrad;
  ctx.beginPath();
  ctx.roundRect(x + w - 2, y, 2, h, [r, 0, 0, r]);
  ctx.fill();

  // 牌面底纹（细微描边）
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x + 0.5, y + 0.5, w - 1, h - 1, r);
  ctx.stroke();

  // 绘制牌图
  if (img && img.complete && img.naturalWidth > 0) {
    const px = 1.5;
    ctx.drawImage(img, x + px, y + px, w - px * 2, h - px * 2);
  }

  // 高亮边框
  if (borderColor) {
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = borderWidth;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
    ctx.stroke();
  }
}

function drawTileBack(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  backStart?: string,
  backEnd?: string
) {
  const r = 3;
  const start = backStart ?? '#1e4a7a';
  const end = backEnd ?? '#0f2d4a';

  // 麻将蓝底
  const grad = ctx.createLinearGradient(x, y, x + w, y + h);
  grad.addColorStop(0, start);
  grad.addColorStop(1, end);
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fill();

  // 顶部白边
  const topGrad = ctx.createLinearGradient(x, y, x + w, y);
  topGrad.addColorStop(0, 'rgba(255,255,255,0.0)');
  topGrad.addColorStop(0.3, 'rgba(255,255,255,0.6)');
  topGrad.addColorStop(1.0, 'rgba(255,255,255,0.8)');
  ctx.fillStyle = topGrad;
  ctx.beginPath();
  ctx.roundRect(x, y, w, 2, [r, r, 0, 0]);
  ctx.fill();

  // 内部斜纹
  ctx.fillStyle = 'rgba(255,255,255,0.06)';
  ctx.save();
  ctx.beginPath();
  ctx.roundRect(x + 2, y + 2, w - 4, h - 4, r - 1);
  ctx.clip();
  for (let i = -h; i < w + h; i += 8) {
    ctx.beginPath();
    ctx.moveTo(x + i, y);
    ctx.lineTo(x + i + h, y + h);
    ctx.lineTo(x + i + h + 2, y + h);
    ctx.lineTo(x + i + 2, y);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

function drawText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  font: string,
  color: string,
  align: CanvasTextAlign = 'left'
) {
  ctx.font = font;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = 'top';
  ctx.fillText(text, x, y);
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function GameBoard({
  entry,
  playerId,
  activeActor,
  highlightTile,
  gtTile,
}: GameBoardProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imagesRef = useRef<Record<string, HTMLImageElement>>({});
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const loadedRef = useRef(false);

  // 加载牌图 SVG
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;

    const TILE_SVG_NAME: Record<string, string> = {
      '1m': 'Man1', '2m': 'Man2', '3m': 'Man3', '4m': 'Man4', '5m': 'Man5',
      '6m': 'Man6', '7m': 'Man7', '8m': 'Man8', '9m': 'Man9',
      '1p': 'Pin1', '2p': 'Pin2', '3p': 'Pin3', '4p': 'Pin4', '5p': 'Pin5',
      '6p': 'Pin6', '7p': 'Pin7', '8p': 'Pin8', '9p': 'Pin9',
      '1s': 'Sou1', '2s': 'Sou2', '3s': 'Sou3', '4s': 'Sou4', '5s': 'Sou5',
      '6s': 'Sou6', '7s': 'Sou7', '8s': 'Sou8', '9s': 'Sou9',
      '5mr': 'Man5-Dora', '5pr': 'Pin5-Dora', '5sr': 'Sou5-Dora',
      'E': 'Ton', 'S': 'Nan', 'W': 'Shaa', 'N': 'Pei',
      'P': 'Haku', 'F': 'Hatsu', 'C': 'Chun',
    };

    let loaded = 0;
    const total = Object.keys(TILE_SVG_NAME).length;

    Object.entries(TILE_SVG_NAME).forEach(([tile, name]) => {
      const img = new Image();
      img.src = `/tiles/${name}.svg`;
      img.onload = () => {
        imagesRef.current[tile] = img;
        loaded++;
        if (loaded === total) setImagesLoaded(true);
      };
      img.onerror = () => {
        loaded++;
        if (loaded === total) setImagesLoaded(true);
      };
    });
  }, []);

  // 读取当前主题的 Canvas 颜色
  const getThemeColors = useCallback(() => {
    return {
      tileFace: readCssVar('--canvas-tile-face', '#f5f0e8'),
      tileStroke: readCssVar('--canvas-tile-stroke', '#c8cdd6'),
      tileBackStart: readCssVar('--canvas-tile-back-start', '#1e4a7a'),
      tileBackEnd: readCssVar('--canvas-tile-back-end', '#0f2d4a'),
      highlightRed: readCssVar('--canvas-highlight-red', '#e74c3c'),
      highlightGreen: readCssVar('--canvas-highlight-green', '#27ae60'),
      highlightPurple: readCssVar('--canvas-highlight-purple', '#9b6dff'),
      boardBg: readCssVar('--canvas-board-bg', '#e8ecf0'),
      textLabel: readCssVar('--canvas-text-label', '#6b7280'),
      textDark: readCssVar('--canvas-text-dark', '#1f2937'),
      activeActor: readCssVar('--canvas-active-actor', '#3498db'),
      seat0: readCssVar('--seat-0', '#e74c3c'),
      seat1: readCssVar('--seat-1', '#3498db'),
      seat2: readCssVar('--seat-2', '#8e44ad'),
      seat3: readCssVar('--seat-3', '#27ae60'),
    };
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const W = canvas.width;
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);

    const colors = getThemeColors();

    // 牌桌背景
    ctx.fillStyle = colors.boardBg;
    ctx.fillRect(0, 0, W, H);

    if (!entry) {
      ctx.fillStyle = colors.textLabel;
      ctx.font = '18px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('请上传牌谱开始回放', W / 2, H / 2);
      return;
    }

    const { hand, discards, melds, dora_markers, scores, reached } = entry;

    const CX = W / 2;
    const H_H = H;

    const SELF_H_Y = H_H - 80;
    const SELF_H_X = CX - (hand.length * (TILE_W + TILE_GAP)) / 2;

    const SELF_DISC_Y = H_H - 180;
    const SELF_DISC_X = 40;

    const OPP_DISC_Y = 30;
    const OPP_DISC_X = CX - (DISCARD_COLS * (TILE_W * 0.8 + TILE_GAP)) / 2;

    const LEFT_DISC_X = 30;
    const LEFT_DISC_Y = H_H / 2 - 120;

    const RIGHT_DISC_X = W - 30 - 6 * (TILE_W * 0.65 + 1);
    const RIGHT_DISC_Y = H_H / 2 - 120;

    const playerColors = [colors.seat0, colors.seat1, colors.seat2, colors.seat3];

    // Dora
    const doraX = CX - (dora_markers.length * (TILE_W * 0.7 + 2)) / 2;
    drawText(ctx, '宝牌', doraX - 30, H_H / 2 - 40, 'bold 12px sans-serif', colors.textLabel, 'left');
    dora_markers.slice(0, 5).forEach((tile, i) => {
      const x = doraX + i * (TILE_W * 0.7 + 2);
      drawTileImage(ctx, imagesRef.current[tile], x, H_H / 2 - 30, TILE_W * 0.7, TILE_H * 0.7,
        undefined, 2, colors.tileFace, colors.tileStroke);
    });

    // 自家（底部）
    const selfName = SEAT_NAMES_CN[playerId];
    drawText(ctx, `自家 [${selfName}]`, SELF_H_X, SELF_H_Y - 20, 'bold 13px sans-serif', colors.textDark, 'left');
    const sortedHand = sortHand(hand, entry.tsumo_pai);
    sortedHand.forEach((tile, i) => {
      const x = SELF_H_X + i * (TILE_W + TILE_GAP);
      const isHighlight = tile === highlightTile;
      const isGt = tile === gtTile;
      let borderColor: string | undefined;
      if (isHighlight && isGt) borderColor = colors.highlightPurple;
      else if (isHighlight) borderColor = colors.highlightRed;
      else if (isGt) borderColor = colors.highlightGreen;
      drawTileImage(ctx, imagesRef.current[tile], x, SELF_H_Y, TILE_W, TILE_H,
        borderColor, 2, colors.tileFace, colors.tileStroke);
    });
    drawText(ctx, `${hand.length}枚`, SELF_H_X + sortedHand.length * (TILE_W + TILE_GAP) + 4, SELF_H_Y + 10, '11px sans-serif', colors.textLabel, 'left');

    // 自家舍牌
    const selfDiscards = (discards && typeof discards === 'object' && !Array.isArray(discards)) ? (discards[playerId] ?? []) : [];
    drawText(ctx, '舍牌', SELF_DISC_X, SELF_DISC_Y - 18, 'bold 11px sans-serif', colors.textLabel, 'left');
    selfDiscards.forEach((d, i) => {
      const col = i % DISCARD_COLS;
      const row = Math.floor(i / DISCARD_COLS);
      const x = SELF_DISC_X + col * (TILE_W * 0.75 + TILE_GAP);
      const y = SELF_DISC_Y + row * (TILE_H * 0.75 + 2);
      drawTileImage(ctx, imagesRef.current[d.pai], x, y, TILE_W * 0.75, TILE_H * 0.75,
        undefined, 1, colors.tileFace, colors.tileStroke);
      if (d.tsumogiri) {
        ctx.fillStyle = colors.highlightRed;
        ctx.beginPath();
        ctx.moveTo(x + TILE_W * 0.75 - 4, y + 2);
        ctx.lineTo(x + TILE_W * 0.75 - 10, y + 2);
        ctx.lineTo(x + TILE_W * 0.75 - 4, y + 8);
        ctx.closePath();
        ctx.fill();
      }
    });

    // 点数
    scores.forEach((score, i) => {
      const color = playerColors[i];
      const x = W - 100;
      const y = H_H - 20 - i * 18;
      const rMark = reached[i] ? ' R' : '';
      drawText(ctx, `P${i}: ${score}${rMark}`, x, y, '11px monospace', color, 'left');
    });

    // 其他三家
    const otherPids = [1, 2, 3].map(o => (playerId + o) % 4);
    const otherPositions = [
      { x: OPP_DISC_X, y: OPP_DISC_Y, maxCols: DISCARD_COLS, tileScale: 0.7 },
      { x: RIGHT_DISC_X, y: RIGHT_DISC_Y, maxCols: 6, tileScale: 0.65 },
      { x: LEFT_DISC_X, y: LEFT_DISC_Y, maxCols: 6, tileScale: 0.65 },
    ];

    otherPids.forEach((pid, idx) => {
      const pos = otherPositions[idx];
      const name = SEAT_NAMES_CN[pid];
      const rMark = reached[pid] ? 'R' : '';
      const color = playerColors[pid];

      drawText(ctx, `${name} ${rMark}`, pos.x, pos.y - 18, 'bold 11px sans-serif', color, 'left');

      const pidMelds = (melds && typeof melds === 'object' && !Array.isArray(melds)) ? (melds[pid] ?? []) : [];
      if (pidMelds.length > 0) {
        const meldX = pos.x;
        const meldY = pos.y + 16;
        pidMelds.slice(0, 4).forEach((meld, _mi) => {
          const mx = meldX + _mi * (TILE_W * 0.65 * 3 + 4);
          const consumed = meld.consumed ?? [];
          consumed.forEach((_, ci) => {
            drawTileBack(ctx, mx + ci * (TILE_W * 0.65), meldY, TILE_W * 0.65, TILE_H * 0.65,
              colors.tileBackStart, colors.tileBackEnd);
          });
        });
      }

      const pidDiscardsRaw = (discards && typeof discards === 'object' && !Array.isArray(discards)) ? (discards[pid] ?? []) : [];
      const pidDiscards = pidDiscardsRaw.slice(-12);
      pidDiscards.forEach((d, i) => {
        const col = i % pos.maxCols;
        const row = Math.floor(i / pos.maxCols);
        const x = pos.x + col * (TILE_W * pos.tileScale + 1);
        const y = pos.y + 16 + (pidMelds.length > 0 ? TILE_H * 0.65 + 4 : 0) + row * (TILE_H * pos.tileScale + 1);
        drawTileImage(ctx, imagesRef.current[d.pai], x, y, TILE_W * pos.tileScale, TILE_H * pos.tileScale,
          undefined, 1, colors.tileFace, colors.tileStroke);
        if (d.tsumogiri) {
          ctx.fillStyle = colors.highlightRed;
          ctx.beginPath();
          const sx = x + TILE_W * pos.tileScale - 4;
          const sy = y + 2;
          ctx.moveTo(sx, sy);
          ctx.lineTo(sx - 6, sy);
          ctx.lineTo(sx, sy + 6);
          ctx.closePath();
          ctx.fill();
        }
      });
    });

    // 行动者高亮
    if (activeActor !== null) {
      const labels = ['北', '东', '南', '西'];
      ctx.strokeStyle = colors.activeActor;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 3]);

      if (activeActor === playerId) {
        ctx.strokeRect(SELF_H_X - 4, SELF_H_Y - 4, sortedHand.length * (TILE_W + TILE_GAP) + 4, TILE_H + 8);
      }

      ctx.setLineDash([]);
      drawText(ctx, `▶ ${labels[activeActor]}`, CX - 10, H_H / 2 + 60, 'bold 14px sans-serif', colors.activeActor, 'center');
    }

  }, [entry, playerId, activeActor, highlightTile, gtTile, getThemeColors]);

  useEffect(() => {
    if (imagesLoaded) {
      draw();
    }
  }, [draw, imagesLoaded]);

  useEffect(() => {
    const handleResize = () => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
      draw();
    };

    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [draw]);

  return (
    <canvas
      ref={canvasRef}
      className="w-full h-full rounded-2xl"
      style={{
        minHeight: '400px',
        backgroundColor: 'var(--canvas-board-bg, #e8ecf0)',
        border: '1px solid var(--table-border, #e5e7eb)',
      }}
    />
  );
}
