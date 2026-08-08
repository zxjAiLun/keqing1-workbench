// src/replay_ui/src/pages/GameBoardPage.tsx
import { useState, useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { GameBoard } from '../components/GameBoard/GameBoard';
import { Loader2, ChevronLeft, ChevronRight, SkipBack, SkipForward } from 'lucide-react';
import { replayApi } from '../api/replayApi';
import type { ReplayData } from '../types/replay';
import type { DecisionLogEntry } from '../types/replay';
import { SEAT_NAMES_CN, BAKAZE_CN } from '../utils/constants';

export function GameBoardPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = location.state as { replayData?: ReplayData; replayId?: string } | null;
  const replayIdFromRoute = routeState?.replayId ?? null;
  const replayIdFromQuery = useMemo(() => new URLSearchParams(location.search).get('id'), [location.search]);
  const replayId = replayIdFromRoute ?? replayIdFromQuery;
  const initialData = routeState?.replayData && !replayIdFromQuery ? routeState.replayData : null;
  const initialError = initialData || replayId ? null : '未找到回放数据，请从首页上传牌谱';

  const [data, setData] = useState<ReplayData | null>(initialData);
  const [currentStep, setCurrentStep] = useState(0);
  const [playerId, setPlayerId] = useState(0);
  const [loading, setLoading] = useState<boolean>(initialData === null && initialError === null);
  const [error, setError] = useState<string | null>(initialError);

  useEffect(() => {
    if (data || !replayId) return;
    let cancelled = false;
    const loadReplay = async () => {
      try {
        const loaded = await replayApi.get(replayId);
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
  }, [data, replayId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (!data) return;
      if (e.key === 'ArrowLeft' || e.key === 'h') { e.preventDefault(); setCurrentStep(c => Math.max(0, c - 1)); }
      if (e.key === 'ArrowRight' || e.key === 'l') { e.preventDefault(); setCurrentStep(c => Math.min(data.log.length - 1, c + 1)); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [data]);

  if (loading) {
    return (
      <div
        className="h-full flex flex-col items-center justify-center gap-3"
        style={{ background: 'var(--page-bg)' }}
      >
        <Loader2 size={36} className="animate-spin" style={{ color: 'var(--accent)' }} />
        <p style={{ color: 'var(--text-secondary)', fontSize: 14 }}>加载回放数据中...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        className="h-full flex flex-col items-center justify-center gap-3"
        style={{ background: 'var(--page-bg)' }}
      >
        <p style={{ color: 'var(--error)', fontSize: 14 }}>{error || '未找到回放数据'}</p>
        <button
          onClick={() => navigate('/')}
          style={{ color: 'var(--accent)', fontSize: 14, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}
        >
          返回首页
        </button>
      </div>
    );
  }

  const entry: DecisionLogEntry | null = data.log[currentStep] ?? null;
  const total = data.log.length;
  const pid = data.player_id ?? 0;

  const highlightTile = entry?.chosen?.type === 'dahai' ? entry.chosen.pai ?? null : null;
  const gtTile = entry?.gt_action?.type === 'dahai' ? entry.gt_action.pai ?? null : null;
  const activeActor = entry?.actor_to_move ?? null;

  const kyoku = entry?.kyoku_key ?? { bakaze: 'E', kyoku: 1, honba: 0 };
  const kyokuLabel = `第${BAKAZE_CN[kyoku.bakaze] ?? kyoku.bakaze}${kyoku.kyoku}局 ${kyoku.honba}本场`;

  return (
    <div className="h-full flex flex-col" style={{ background: 'var(--page-bg)' }}>
      {/* 顶部信息栏 */}
      <div
        className="px-4 py-2 flex items-center gap-3 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)', background: 'var(--card-bg)' }}
      >
        <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
          {SEAT_NAMES_CN[pid]}视角
        </span>
        <span style={{ color: 'var(--border)' }}>|</span>
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{kyokuLabel}</span>

        <select
          value={playerId}
          onChange={e => setPlayerId(parseInt(e.target.value))}
          style={{
            marginLeft: 'auto',
            padding: '4px 8px',
            border: '1px solid var(--border)',
            borderRadius: 6,
            fontSize: 12,
            background: 'var(--card-bg)',
            color: 'var(--text-primary)',
          }}
        >
          {[0, 1, 2, 3].map(i => (
            <option key={i} value={i}>{SEAT_NAMES_CN[i]}视角</option>
          ))}
        </select>

        <button
          onClick={() => navigate('/')}
          style={{
            padding: '4px 10px',
            border: '1px solid var(--border)',
            borderRadius: 4,
            fontSize: 12,
            background: 'var(--sidebar-hover-bg)',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          🏠
        </button>
      </div>

      {/* 牌桌区域 */}
      <div className="flex-1 min-h-0 p-3">
        <div
          className="h-full rounded-2xl overflow-hidden"
          style={{
            border: '1px solid var(--table-border)',
            boxShadow: 'var(--table-shadow)',
          }}
        >
          <GameBoard
            entry={entry}
            playerId={playerId}
            activeActor={activeActor}
            highlightTile={highlightTile}
            gtTile={gtTile}
          />
        </div>
      </div>

      {/* 底部控制条 */}
      <div
        className="px-4 py-2 flex items-center gap-3 flex-shrink-0"
        style={{ borderTop: '1px solid var(--border)', background: 'var(--card-bg)' }}
      >
        {/* 步进控制 */}
        <button
          onClick={() => setCurrentStep(0)}
          disabled={currentStep === 0}
          style={{
            padding: 6,
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'var(--sidebar-hover-bg)',
            color: 'var(--text-secondary)',
            opacity: currentStep === 0 ? 0.4 : 1,
            cursor: currentStep === 0 ? 'not-allowed' : 'pointer',
          }}
          title="第一步"
        >
          <SkipBack size={14} />
        </button>
        <button
          onClick={() => setCurrentStep(c => Math.max(0, c - 1))}
          disabled={currentStep === 0}
          style={{
            padding: 6,
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'var(--sidebar-hover-bg)',
            color: 'var(--text-secondary)',
            opacity: currentStep === 0 ? 0.4 : 1,
            cursor: currentStep === 0 ? 'not-allowed' : 'pointer',
          }}
          title="上一步 (←)"
        >
          <ChevronLeft size={14} />
        </button>

        {/* 进度条 */}
        <div className="flex-1 flex flex-col gap-1">
          <input
            type="range"
            min={0}
            max={total - 1}
            value={currentStep}
            onChange={e => setCurrentStep(parseInt(e.target.value))}
            style={{ width: '100%', accentColor: 'var(--accent)' }}
          />
          <div className="flex justify-between text-xs" style={{ color: 'var(--text-muted)' }}>
            <span>Step {currentStep + 1} / {total}</span>
            <span>{Math.round((currentStep + 1) / total * 100)}%</span>
          </div>
        </div>

        <button
          onClick={() => setCurrentStep(c => Math.min(total - 1, c + 1))}
          disabled={currentStep === total - 1}
          style={{
            padding: 6,
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'var(--sidebar-hover-bg)',
            color: 'var(--text-secondary)',
            opacity: currentStep === total - 1 ? 0.4 : 1,
            cursor: currentStep === total - 1 ? 'not-allowed' : 'pointer',
          }}
          title="下一步 (→)"
        >
          <ChevronRight size={14} />
        </button>
        <button
          onClick={() => setCurrentStep(total - 1)}
          disabled={currentStep === total - 1}
          style={{
            padding: 6,
            borderRadius: 6,
            border: '1px solid var(--border)',
            background: 'var(--sidebar-hover-bg)',
            color: 'var(--text-secondary)',
            opacity: currentStep === total - 1 ? 0.4 : 1,
            cursor: currentStep === total - 1 ? 'not-allowed' : 'pointer',
          }}
          title="最后一步"
        >
          <SkipForward size={14} />
        </button>

        {/* 当前动作信息 */}
        <div className="ml-4 text-xs min-w-[120px]" style={{ color: 'var(--text-secondary)' }}>
          {entry?.chosen && (
            <span style={{ color: 'var(--accent)', fontWeight: 600 }}>
              Bot: {entry.chosen.type === 'dahai' ? `打 ${entry.chosen.pai}` : entry.chosen.type}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
