// src/replay_ui/src/pages/ReplayViewPage.tsx
import { useState, useEffect, useCallback, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import type { ReplayData } from '../types/replay';
import type { DecisionLogEntry } from '../types/replay';
import { replayApi } from '../api/replayApi';
import { legacyRoutes, routes } from '../routes';
import { actionLabel, isReplayReviewDiffForPlayer, sameReplayAction } from '../utils/tileUtils';
import { ReplayStatsDialog } from '../components/ReviewWorkspace/ReplayStatsDialog';
import { CN_BAKAZE, SEAT_NAMES_CN } from '../utils/constants';
import { decisionColors, decisionBg } from '../components/BattleBoard/tableStyles';
import { normalizeReplayPlayerNames, replayPlayerDisplayName } from '../utils/replayNames';

const TILE_BASE = '/tiles';
const TILE_SVG: Record<string, string> = {
  '1m':'Man1','2m':'Man2','3m':'Man3','4m':'Man4','5m':'Man5','6m':'Man6','7m':'Man7','8m':'Man8','9m':'Man9',
  '1p':'Pin1','2p':'Pin2','3p':'Pin3','4p':'Pin4','5p':'Pin5','6p':'Pin6','7p':'Pin7','8p':'Pin8','9p':'Pin9',
  '1s':'Sou1','2s':'Sou2','3s':'Sou3','4s':'Sou4','5s':'Sou5','6s':'Sou6','7s':'Sou7','8s':'Sou8','9s':'Sou9',
  '5mr':'Man5-Dora','5pr':'Pin5-Dora','5sr':'Sou5-Dora',
  'E':'Ton','S':'Nan','W':'Shaa','N':'Pei','P':'Haku','F':'Hatsu','C':'Chun',
};
const _TILE_ORDER: Record<string, number> = {
  '1m':0,'2m':1,'3m':2,'4m':3,'5m':4,'6m':5,'7m':6,'8m':7,'9m':8,'5mr':4,
  '1p':9,'2p':10,'3p':11,'4p':12,'5p':13,'6p':14,'7p':15,'8p':16,'9p':17,'5pr':13,
  '1s':18,'2s':19,'3s':20,'4s':21,'5s':22,'6s':23,'7s':24,'8s':25,'9s':26,'5sr':22,
  'E':27,'S':28,'W':29,'N':30,'P':31,'F':32,'C':33,
};
const MAX_BAR_H = 57;

function tileUrl(name: string) {
  return `${TILE_BASE}/${TILE_SVG[name] || 'Blank'}.svg`;
}

interface TileWithMeta {
  name: string;
  logit?: number;
  prob?: number;
  minLogit: number;
  logitRange: number;
  isTsumo: boolean;
}

function softmaxProbabilities(scores: number[]): number[] {
  if (scores.length === 0) return [];
  const maxScore = Math.max(...scores);
  const exps = scores.map((score) => Math.exp(score - maxScore));
  const total = exps.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) return scores.map(() => 0);
  return exps.map((value) => value / total);
}

function candidateScore(c: { logit: number; beam_score?: number; final_score?: number }): number {
  return c.final_score ?? c.beam_score ?? c.logit;
}

function candidateProbabilities(candidates: Array<{ logit: number; beam_score?: number; final_score?: number; prob?: number }>): number[] {
  const fallback = softmaxProbabilities(candidates.map(candidateScore));
  return candidates.map((candidate, idx) => (
    typeof candidate.prob === 'number' && Number.isFinite(candidate.prob)
      ? candidate.prob
      : fallback[idx] ?? 0
  ));
}

function buildSortedTiles(entry: DecisionLogEntry): TileWithMeta[] {
  const hand = entry.hand || [];
  const tsumo_pai = entry.tsumo_pai || null;
  const tileLogit: Record<string, number> = {};
  const tileProb: Record<string, number> = {};
  let minLogit = 0, maxLogit = 1;
  if (entry.candidates && entry.candidates.length) {
    const probs = candidateProbabilities(entry.candidates);
    entry.candidates.forEach((c, idx) => {
      if (c.action && c.action.type === 'dahai' && c.action.pai) {
        tileLogit[c.action.pai] = c.logit;
        tileProb[c.action.pai] = probs[idx] ?? 0;
      }
    });
    const vals = Object.values(tileLogit);
    if (vals.length > 1) { minLogit = Math.min(...vals); maxLogit = Math.max(...vals); }
    else if (vals.length === 1) { minLogit = maxLogit = vals[0]; }
  }
  const logitRange = Math.max(maxLogit - minLogit, 0.001);
  const others = hand.filter(t => t !== tsumo_pai);
  others.sort((a, b) => (_TILE_ORDER[a] ?? 99) - (_TILE_ORDER[b] ?? 99));
  const sorted = tsumo_pai ? [...others, tsumo_pai] : others;
  return sorted.map(name => ({
    name,
    logit: tileLogit[name],
    prob: tileProb[name],
    minLogit,
    logitRange,
    isTsumo: name === tsumo_pai,
  }));
}

function barHeight(tile: TileWithMeta): number {
  if (tile.logit === undefined) return 0;
  const pct = tile.prob !== undefined
    ? Math.max(1, tile.prob * 100)
    : Math.max(1, (tile.logit - tile.minLogit) / tile.logitRange * 100);
  return Math.max(3, Math.round(pct / 100 * MAX_BAR_H));
}

function barColor(tile: TileWithMeta, entry: DecisionLogEntry) {
  const c = entry.chosen, g = entry.gt_action;
  if (tile.name === c?.pai && tile.name === g?.pai) return decisionColors.both;
  if (tile.name === c?.pai) return decisionColors.bot;
  if (tile.name === g?.pai) return decisionColors.gt;
  return 'var(--accent)';
}

function tileCssClass(tile: TileWithMeta, entry: DecisionLogEntry): string {
  const c = entry.chosen, g = entry.gt_action;
  const b = tile.name === c?.pai, gg = tile.name === g?.pai;
  if (b && gg) return 'is-both';
  if (b) return 'is-bot';
  if (gg) return 'is-gt';
  return '';
}

// ---------------------------------------------------------------------------
// 候选评分表
// ---------------------------------------------------------------------------
function CandidateTable({
  candidates,
  chosen,
  gtAction,
}: {
  candidates: Array<{ action: import('../types/replay').Action; logit: number; beam_score?: number; final_score?: number; prob?: number }>;
  chosen: import('../types/replay').Action | null;
  gtAction: import('../types/replay').Action | null;
}) {
  const hasFinal = candidates.some(c => c.final_score !== undefined);
  const hasBeam = candidates.some(c => c.beam_score !== undefined);
  const probs = candidateProbabilities(candidates);
  return (
    <div style={{ marginTop: 6, fontSize: 11, overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th style={{ padding: '2px 8px', textAlign: 'left', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>动作</th>
            <th style={{ padding: '2px 8px', textAlign: 'right', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontFamily: 'monospace' }}>Logit</th>
            {hasFinal && <th style={{ padding: '2px 8px', textAlign: 'right', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontFamily: 'monospace' }}>Final</th>}
            {hasBeam && <th style={{ padding: '2px 8px', textAlign: 'right', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontFamily: 'monospace' }}>Beam</th>}
            <th style={{ padding: '2px 8px', textAlign: 'right', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', fontFamily: 'monospace' }}>P</th>
            <th style={{ padding: '2px 8px', borderBottom: '1px solid var(--border)' }}></th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c, i) => {
            const isBot = sameReplayAction(c.action, chosen);
            const isGt  = sameReplayAction(c.action, gtAction);
            const isBoth = isBot && isGt;
            const bg = isBoth ? decisionBg.both : isBot ? decisionBg.bot : isGt ? decisionBg.gt : (i % 2 === 0 ? 'transparent' : decisionBg.zebra);
            const color = isBoth ? decisionColors.both : isBot ? decisionColors.bot : isGt ? decisionColors.gt : 'var(--text-primary)';
            const marks = (isBot ? '✓Bot ' : '') + (isGt ? '★玩家' : '');
            return (
              <tr key={i} style={{ background: bg, color, fontWeight: isBot || isBoth ? 600 : 400 }}>
                <td style={{ padding: '2px 8px', borderBottom: '1px solid var(--border)' }}>{actionLabel(c.action)}</td>
                <td style={{ padding: '2px 8px', textAlign: 'right', fontFamily: 'monospace', borderBottom: '1px solid var(--border)', fontVariantNumeric: 'tabular-nums' }}>{c.logit >= 0 ? '+' : ''}{c.logit.toFixed(3)}</td>
                {hasFinal && <td style={{ padding: '2px 8px', textAlign: 'right', fontFamily: 'monospace', borderBottom: '1px solid var(--border)', fontVariantNumeric: 'tabular-nums' }}>{c.final_score !== undefined ? (c.final_score >= 0 ? '+' : '') + c.final_score.toFixed(3) : '—'}</td>}
                {hasBeam && <td style={{ padding: '2px 8px', textAlign: 'right', fontFamily: 'monospace', borderBottom: '1px solid var(--border)', fontVariantNumeric: 'tabular-nums' }}>{c.beam_score !== undefined ? (c.beam_score >= 0 ? '+' : '') + c.beam_score.toFixed(3) : '—'}</td>}
                <td style={{ padding: '2px 8px', textAlign: 'right', fontFamily: 'monospace', borderBottom: '1px solid var(--border)', fontVariantNumeric: 'tabular-nums' }}>{((probs[i] ?? 0) * 100).toFixed(1)}%</td>
                <td style={{ padding: '2px 8px', fontSize: 10, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }}>{marks}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


// ---------------------------------------------------------------------------
// 单步卡片
// ---------------------------------------------------------------------------
function StepCard({ entry, playerNames }: { entry: DecisionLogEntry; playerNames: string[] }) {
  // obs 步：简化渲染
  if (entry.is_obs) {
    const k = entry.kyoku_key || entry;
    const kyokuLabel = `${CN_BAKAZE[k.bakaze] || k.bakaze}${k.kyoku}局 ${k.honba}本场`;
    const actor = entry.actor_to_move ?? entry.chosen?.actor ?? '?';
    return (
      <div className="step-card" style={{ opacity: 0.6, borderLeft: '3px solid var(--info)' }}>
        <div className="step-top">
          <div className="step-header">{kyokuLabel} · Step {entry.step}</div>
          <div className="step-meta-right">
            <span className="action-chip none">
              {replayPlayerDisplayName(playerNames, Number(actor))}: {actionLabel(entry.chosen)}
            </span>
          </div>
        </div>
      </div>
    );
  }

  const tiles = buildSortedTiles(entry);
  const isDahai = entry.chosen?.type === 'dahai' || entry.chosen?.type === 'none';

  const chosenClass = (() => {
    const c = entry.chosen;
    if (!c) return 'none';
    const g = entry.gt_action;
    if (sameReplayAction(c, g)) return 'both';
    if (c.type === 'dahai' || c.type === 'none') return 'bot';
    return 'gt';
  })();

  const gtClass = (() => {
    const c = entry.chosen, g = entry.gt_action;
    if (!g) return 'none';
    if (sameReplayAction(c, g)) return 'both';
    return 'gt';
  })();

  const isMatch = sameReplayAction(entry.chosen, entry.gt_action);

  const k = entry.kyoku_key || entry;
  const kyokuLabel = `${CN_BAKAZE[k.bakaze] || k.bakaze}${k.kyoku}局 ${k.honba}本场`;

  const chipClass: Record<string, string> = {
    bot: 'action-chip bot',
    gt: 'action-chip gt',
    both: 'action-chip both',
    none: 'action-chip none',
  };

  return (
    <div className="step-card">
      {/* 顶栏 */}
      <div className="step-top">
        <div className="step-header">{kyokuLabel} · Step {entry.step}</div>
        <div className="step-meta-right">
          {entry.scores.map((s, i) => (
            <span
              key={i}
              className={`score-chip${entry.reached[i] ? ' reached' : ''}`}
            >
              P{i}{entry.reached[i] ? 'R' : ''}:{s}
            </span>
          ))}
          {entry.dora_markers?.length > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 4 }}>
              宝:{entry.dora_markers.map(d => (
                <img key={d} src={tileUrl(d)} width={20} className="dora" style={{ marginRight: 3 }} />
              ))}
            </span>
          )}
          <span className={chipClass[chosenClass]}>
            Bot: {actionLabel(entry.chosen)}
          </span>
          <span className={chipClass[gtClass]}>
            实际: {entry.gt_action ? actionLabel(entry.gt_action) : '—'}
          </span>
          {isMatch && (
            <span style={{ fontSize: 11, color: 'var(--success)', fontWeight: 600 }}>✓ Match</span>
          )}
        </div>
      </div>

      {/* 手牌 + 指示条 */}
      {isDahai && (
        <div className="hand-row">
          {tiles.map(tile => {
            const h = barHeight(tile);
            const cls = tileCssClass(tile, entry);
            const rClass = h >= 5 ? 'bar r5' : 'bar r3';
            return (
              <div key={tile.name} className={`tile-slot ${cls}${tile.isTsumo ? ' is-tsumo' : ''}`}>
                {tile.isTsumo && (
                  <span
                    style={{
                      position: 'absolute',
                      top: -14,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      fontSize: 9,
                      fontWeight: 700,
                      color: 'var(--warning)',
                      background: 'var(--gold-bg)',
                      border: '1px solid var(--gold-border)',
                      borderRadius: 3,
                      padding: '0 3px',
                      lineHeight: 1.4,
                    }}
                  >
                    摸
                  </span>
                )}
                {h > 0 && (
                  <div className={`bar-wrap ${cls}`}>
                    <div className="bar-label">{tile.logit?.toFixed(2)}</div>
                    <div className={rClass} style={{ height: h, background: barColor(tile, entry) }} />
                  </div>
                )}
                <img
                  src={tileUrl(tile.name)}
                  width={38}
                  className="tile-img"
                  style={{
                    display: 'block',
                    borderRadius: 3,
                    border: cls ? `2px solid ${cls === 'is-bot' ? decisionColors.bot : cls === 'is-gt' ? decisionColors.gt : decisionColors.both}` : '1px solid var(--text-muted)',
                  }}
                />
              </div>
            );
          })}
        </div>
      )}

      {/* dahai 候选评分表 */}
      {isDahai && entry.candidates && entry.candidates.length > 0 && (
        <CandidateTable candidates={entry.candidates} chosen={entry.chosen} gtAction={entry.gt_action} />
      )}

      {/* 非 dahai */}
      {!isDahai && (
        <div className="nondahai-box">
          <div>Bot: <b>{entry.chosen?.type === 'none' ? '过' : actionLabel(entry.chosen)}</b></div>
          {entry.gt_action && (
            <div style={{ color: decisionColors.gt }}>实际: <b>{entry.gt_action.type === 'none' ? '过' : actionLabel(entry.gt_action)}</b></div>
          )}
        </div>
      )}

      {/* 非 dahai 候选评分表 */}
      {!isDahai && entry.candidates && entry.candidates.length > 0 && (
        <CandidateTable candidates={entry.candidates} chosen={entry.chosen} gtAction={entry.gt_action} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主页面
// ---------------------------------------------------------------------------
export function ReplayViewPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const routeState = location.state as { replayData?: ReplayData; replayId?: string } | null;
  const replayIdFromQuery = params.get('id');
  const replayIdFromRoute = routeState?.replayId ?? null;
  const replayId = replayIdFromRoute ?? replayIdFromQuery;
  const playerIdFromQuery = Number(params.get('player_id') ?? '0');
  const requestedPlayerId = Number.isFinite(playerIdFromQuery) ? playerIdFromQuery : 0;
  const teacherReportFromQuery = params.get('teacher_report') || params.get('teacher_report_path');
  const teacherReportsFromQuery = params
    .getAll('teacher_reports')
    .flatMap((value) => value.split(','))
    .map((value) => value.trim())
    .filter(Boolean);
  const teacherReportsKey = teacherReportsFromQuery.join('\n');
  const initialData = routeState?.replayData && !replayIdFromQuery ? routeState.replayData : null;
  const initialError = initialData || replayId ? null : '未找到回放数据，请从首页上传牌谱';

  const [data, setData] = useState<ReplayData | null>(initialData);
  const [curKyoku, setCurKyoku] = useState(0);
  const [showStats, setShowStats] = useState(false);
  const [loading, setLoading] = useState(initialData === null && initialError === null);
  const [error, setError] = useState<string | null>(initialError);
  const stepRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const [scrollToStep, setScrollToStep] = useState<number | null>(null);
  const focusedStep = useRef<number>(-1);

  useEffect(() => {
    if (data || !replayId) return;
    let cancelled = false;
    const loadReplay = async () => {
      try {
        const loaded = await replayApi.get(replayId, requestedPlayerId, teacherReportFromQuery, teacherReportsFromQuery);
        if (!cancelled) {
          setData(loaded);
          setError(null);
          setLoading(false);
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      }
    };
    loadReplay();
    return () => {
      cancelled = true;
    };
  }, [data, replayId, requestedPlayerId, teacherReportFromQuery, teacherReportsKey]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (showStats) return;
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === 'ArrowLeft' || e.key === 'h') { e.preventDefault(); setCurKyoku(c => Math.max(0, c - 1)); }
      if (e.key === 'ArrowRight' || e.key === 'l') { e.preventDefault(); if (data) setCurKyoku(c => Math.min(data.kyoku_order.length - 1, c + 1)); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [data, showStats]);

  const visibleSteps = data ? data.log.filter(e => {
    const k = e.kyoku_key || e;
    const ko = data.kyoku_order[curKyoku];
    return k.bakaze === ko?.bakaze && k.kyoku === ko?.kyoku && k.honba === ko?.honba;
  }) : [];

  const isDiff = useCallback((e: DecisionLogEntry) =>
    data !== null && isReplayReviewDiffForPlayer(e, data.player_id)
  , [data]);

  const jumpToPrevDiff = useCallback(() => {
    if (!data) return;
    const from = focusedStep.current >= 0 ? focusedStep.current - 1 : (data.log.length - 1);
    for (let i = from; i >= 0; i--) {
      const e = data.log[i];
      if (!isDiff(e)) continue;
      const ki = data.kyoku_order.findIndex(
        k => k.bakaze === e.kyoku_key.bakaze && k.kyoku === e.kyoku_key.kyoku && k.honba === e.kyoku_key.honba
      );
      if (ki < 0) continue;
      focusedStep.current = e.step;
      setCurKyoku(ki);
      setScrollToStep(e.step);
      return;
    }
  }, [data, isDiff]);

  const jumpToNextDiff = useCallback(() => {
    if (!data) return;
    const from = focusedStep.current >= 0 ? focusedStep.current + 1 : 0;
    for (let i = from; i < data.log.length; i++) {
      const e = data.log[i];
      if (!isDiff(e)) continue;
      const ki = data.kyoku_order.findIndex(
        k => k.bakaze === e.kyoku_key.bakaze && k.kyoku === e.kyoku_key.kyoku && k.honba === e.kyoku_key.honba
      );
      if (ki < 0) continue;
      focusedStep.current = e.step;
      setCurKyoku(ki);
      setScrollToStep(e.step);
      return;
    }
  }, [data, isDiff]);

  // scroll 到目标 step（在 curKyoku 切换并重新渲染后执行）
  useEffect(() => {
    if (scrollToStep === null) return;
    // defer until after DOM paint so new kyoku's cards are mounted
    const id = setTimeout(() => {
      const el = stepRefs.current.get(scrollToStep);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      setScrollToStep(null);
    }, 50);
    return () => clearTimeout(id);
  }, [scrollToStep, curKyoku]);



  const handleExportHtml = useCallback(() => {
    if (!data) return;
    const form = new FormData();
    form.append('data', JSON.stringify(data));
    fetch('/api/export-html', { method: 'POST', body: form })
      .then(r => r.blob())
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'replay.html';
        a.click();
        URL.revokeObjectURL(url);
      });
  }, [data]);

  if (loading) {
    return (
      <div style={{ background: 'var(--page-bg)', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <Loader2 size={32} className="animate-spin" style={{ color: 'var(--accent)' }} />
        <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>加载回放数据中...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div style={{ background: 'var(--page-bg)', height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <p style={{ color: 'var(--error)', fontSize: 14 }}>{error || '未找到回放数据'}</p>
        <button onClick={() => navigate(routes.home)} style={{ color: 'var(--accent)', fontSize: 14, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>
          返回首页
        </button>
      </div>
    );
  }

  const numKyoku = data.kyoku_order.length;
  const ko = data.kyoku_order[curKyoku];
  const kyokuLabel = ko ? `第${CN_BAKAZE[ko.bakaze] || ko.bakaze}${ko.kyoku}局 ${ko.honba}本场 (${curKyoku + 1}/${numKyoku})` : '';
  const pid = data.player_id || 0;
  const playerNames = normalizeReplayPlayerNames(data);
  const switchPerspective = (nextPid: number) => {
    const replayId = replayIdFromQuery ?? (location.state as { replayId?: string } | null)?.replayId;
    if (!replayId) return;
    navigate(`${legacyRoutes.decisionList}?id=${encodeURIComponent(replayId)}&player_id=${nextPid}`);
  };

  return (
    <div style={{ background: 'var(--page-bg)', height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 导航栏 */}
      <div className="nav-bar">
        <span className="nav-title">{SEAT_NAMES_CN[pid]}视角 · Replay Review</span>

        <button
          className="btn"
          onClick={() => setCurKyoku(c => Math.max(0, c - 1))}
          disabled={curKyoku === 0}
        >
          ◀ 上一局
        </button>
        <span style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 600, minWidth: 180, textAlign: 'center' }}>
          {kyokuLabel}
        </span>
        <button
          className="btn"
          onClick={() => setCurKyoku(c => Math.min(numKyoku - 1, c + 1))}
          disabled={curKyoku === numKyoku - 1}
        >
          下一局 ▶
        </button>

        <span style={{ marginLeft: 8, display: 'flex', gap: 8 }}>
          <button className="btn" onClick={jumpToPrevDiff} title="上一个与Bot不同的决策">⏮差异</button>
          <button className="btn" onClick={jumpToNextDiff} title="下一个与Bot不同的决策">差异⏭</button>
          <button className="btn" onClick={() => setShowStats(true)}>📊统计</button>
          <button className="btn" onClick={handleExportHtml}>💾导出</button>
          <button
            className="btn"
            onClick={() => replayIdFromQuery
              ? navigate(`${routes.reviewWorkspace(replayIdFromQuery)}?player_id=${pid}`)
              : navigate(legacyRoutes.gameReplay, { state: { replayData: data } })}
            title="切换到牌桌视图"
          >
            🀄牌桌
          </button>
        </span>

        <button className="btn" onClick={() => navigate(routes.home)}>🏠</button>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8, display: 'none' }}>←/→ 换局</span>
      </div>

      {/* 统计面板 */}
      {showStats && <ReplayStatsDialog data={data} onClose={() => setShowStats(false)} />}

      {/* 步列表 */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
          <div style={{ maxWidth: 860, margin: '0 auto' }}>
            {visibleSteps.map(entry => (
              <div key={entry.step} ref={el => { if (el) stepRefs.current.set(entry.step, el); else stepRefs.current.delete(entry.step); }}>
                <StepCard entry={entry} playerNames={playerNames} />
              </div>
            ))}
            {visibleSteps.length === 0 && (
              <p style={{ textAlign: 'center', color: 'var(--text-muted)', fontSize: 14, padding: '48px 0' }}>
                该局暂无数据
              </p>
            )}
          </div>
        </div>
        <div
          style={{
            width: 220,
            borderLeft: '1px solid var(--border)',
            background: 'var(--sidebar-bg)',
            padding: 12,
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            flexShrink: 0,
          }}
        >
          <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>切换主视角</div>
          {playerNames.map((name, idx) => {
            const active = idx === pid;
            return (
              <button
                key={idx}
                onClick={() => switchPerspective(idx)}
                disabled={active}
                className="btn"
                style={{
                  justifyContent: 'flex-start',
                  opacity: active ? 1 : 0.92,
                  fontWeight: active ? 700 : 500,
                  outline: active ? '2px solid var(--accent-border)' : 'none',
                }}
                title={`切换到 ${name}`}
              >
                {SEAT_NAMES_CN[idx]} · {replayPlayerDisplayName(playerNames, idx)}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
