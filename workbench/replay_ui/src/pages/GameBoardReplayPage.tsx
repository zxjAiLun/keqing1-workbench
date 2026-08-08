// src/replay_ui/src/pages/GameBoardReplayPage.tsx
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { MahjongTable } from '../components/BattleBoard/MahjongTable';
import { Tile } from '../components/BattleBoard/Tile';
import { ReplayDecisionPanel } from '../components/DecisionPanel/ReplayDecisionPanel';
import { ReplayStatsDialog } from '../components/ReviewWorkspace/ReplayStatsDialog';
import { ReplayWorkspaceNavigation } from '../components/ReviewWorkspace/ReplayWorkspaceNavigation';
import { entryToBattleState, buildLogitData, getActualReplayAction, hasReplayPostAction, hasReplayReachPhase, isCollapsibleResponsePassStep, type ReplayBoardPhase } from '../utils/replayAdapter';
import { buildReplayHandsForBoard, removeTileOnce, type ReplayEvent } from '../utils/replayHands';
import { useReplayPlayer } from '../hooks/useReplayPlayer';
import { replayApi } from '../api/replayApi';
import { routes } from '../routes';
import { CN_BAKAZE } from '../utils/constants';
import { isReplayReviewDiffForPlayer, TILE_ORDER } from '../utils/tileUtils';
import { normalizeReplayPlayerNames, replayPlayerDisplayName } from '../utils/replayNames';
import type { Action, ReplayData } from '../types/replay';
import {
  backLinkButtonStyle,
  centeredStatusStyle,
  errorStatusTextStyle,
  mutedStatusTextStyle,
} from './gameReplayStyles';
import '../components/ReviewWorkspace/reviewWorkspace.css';

const REPLAY_BOARD_PHASE_LABELS: Record<ReplayBoardPhase, string> = {
  pre: '动作前',
  reach: '立直',
  post: '动作后',
};

function isForcedRiichiTsumogiriEntry(entry: ReplayData['log'][number] | null | undefined): boolean {
  const action = getActualReplayAction(entry);
  return Boolean(
    entry
    && !entry.is_obs
    && action?.type === 'dahai'
    && action.tsumogiri
    && action.actor !== undefined
    && entry.reached?.[action.actor],
  );
}

export function GameBoardReplayPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const routeState = location.state as { replayData?: ReplayData; replayId?: string } | null;
  const params = new URLSearchParams(location.search);
  const { replayId: replayIdFromPath } = useParams();
  const replayIdFromRoute = replayIdFromPath ?? routeState?.replayId ?? params.get('id');
  const playerIdFromQuery = Number(params.get('player_id') ?? '0');
  const requestedPlayerId = Number.isFinite(playerIdFromQuery) ? playerIdFromQuery : 0;
  const teacherReportFromQuery = params.get('teacher_report') || params.get('teacher_report_path');
  const teacherReportsFromQuery = useMemo(
    () => new URLSearchParams(location.search)
      .getAll('teacher_reports')
      .flatMap((value) => value.split(','))
      .map((value) => value.trim())
      .filter(Boolean),
    [location.search],
  );
  const teacherReportsKey = teacherReportsFromQuery.join('\n');
  const focusEventIndexFromQuery = Number(params.get('focus_event_index') ?? '');
  const requestedFocusEventIndex = Number.isFinite(focusEventIndexFromQuery) ? focusEventIndexFromQuery : null;
  const focusStepFromQuery = Number(params.get('focus_step') ?? '');
  const requestedFocusStep = Number.isFinite(focusStepFromQuery) ? focusStepFromQuery : null;
  const stepFromQuery = Number(params.get('step') ?? '');
  const requestedStep = Number.isFinite(stepFromQuery) ? stepFromQuery : null;
  const phaseFromQuery = params.get('phase');
  const requestedPhase: ReplayBoardPhase | null =
    phaseFromQuery === 'post' || phaseFromQuery === 'reach' || phaseFromQuery === 'pre'
      ? phaseFromQuery
      : null;
  const focusResolution = params.get('focus_resolution');
  const routeReplayData = routeState?.replayData;
  const routeReplayId = routeState?.replayId;
  const [data, setData] = useState<ReplayData | null>(null);
  const [events, setEvents] = useState<Record<string, unknown>[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoHora, setAutoHora] = useState(true);
  const [noMeld, setNoMeld] = useState(false);
  const [autoTsumogiri, setAutoTsumogiri] = useState(false);
  const [showStats, setShowStats] = useState(false);
  const [activeDrawer, setActiveDrawer] = useState<'right' | null>(null);
  const [boardPhase, setBoardPhase] = useState<ReplayBoardPhase>('pre');
  const boardViewportRef = useRef<HTMLDivElement>(null);
  const workspaceShellRef = useRef<HTMLDivElement>(null);
  const [showOpponentHands, setShowOpponentHands] = useState(false);
  const [activeTeacherModel, setActiveTeacherModel] = useState<string | null>(null);
  const lastAppliedReplaySearchRef = useRef<string | null>(null);

  // 加载数据
  useEffect(() => {
    setLoading(true);
    setError(null);
    if (routeReplayData && !replayIdFromRoute) {
      setData(routeReplayData);
      setLoading(false);
    } else if (routeReplayId) {
      replayApi.get(routeReplayId, requestedPlayerId, teacherReportFromQuery, teacherReportsFromQuery)
        .then(d => { setData(d); setLoading(false); })
        .catch(e => { setError(String(e)); setLoading(false); });
    } else {
      const replayId = replayIdFromRoute;
      if (replayId) {
        replayApi.get(replayId, requestedPlayerId, teacherReportFromQuery, teacherReportsFromQuery)
          .then(d => { setData(d); setLoading(false); })
          .catch(e => { setError(String(e)); setLoading(false); });
      } else {
        setError('未找到回放数据，请从首页上传牌谱');
        setLoading(false);
      }
    }
  }, [replayIdFromRoute, requestedPlayerId, routeReplayData, routeReplayId, teacherReportFromQuery, teacherReportsKey]);

  useEffect(() => {
    if (!replayIdFromRoute) return;
    replayApi.getEvents(replayIdFromRoute, 0, 10000)
      .then((payload) => {
        setEvents((payload.events ?? []) as Record<string, unknown>[]);
      })
      .catch(() => {
        setEvents(null);
      });
  }, [replayIdFromRoute]);

  const {
    currentStep, totalSteps,
    currentEntry, currentKyoku, totalKyoku,
    stepForward, stepBackward,
    goToStep, goToKyoku,
  } = useReplayPlayer(data);

  const currentHasPostPhase = hasReplayPostAction(currentEntry);
  const currentHasReachPhase = hasReplayReachPhase(currentEntry);
  const viewPlayerId = data?.player_id ?? 0;

  const isRedundantResponseStep = useCallback((step: number) => {
    if (!data || step <= 0 || step >= data.log.length) return false;
    const entry = data.log[step];
    const prevEntry = data.log[step - 1];
    if (!entry || !prevEntry) return false;
    return isCollapsibleResponsePassStep(entry, prevEntry);
  }, [data]);

  const resetBoardPhase = useCallback(() => {
    setBoardPhase('pre');
  }, []);

  useEffect(() => {
    if (!data) return;
    let targetStep = requestedStep ?? requestedFocusStep;
    if (targetStep === null && requestedFocusEventIndex !== null) {
      targetStep = data.log.findIndex((entry) => entry.source_event_index === requestedFocusEventIndex);
    }
    if (typeof targetStep === 'number' && targetStep >= 0) {
      const targetPhase = requestedPhase ?? 'pre';
      if (targetStep === currentStep && targetPhase === boardPhase) {
        lastAppliedReplaySearchRef.current = location.search;
        return;
      }
      if (requestedPhase) setBoardPhase(requestedPhase);
      else resetBoardPhase();
      setShowOpponentHands(false);
      goToStep(targetStep);
    }
  }, [data, location.search, goToStep, resetBoardPhase]);

  useEffect(() => {
    if (!data) return;
    let targetStep = requestedStep ?? requestedFocusStep;
    if (targetStep === null && requestedFocusEventIndex !== null) {
      targetStep = data.log.findIndex((entry) => entry.source_event_index === requestedFocusEventIndex);
    }
    const targetPhase = requestedPhase ?? 'pre';
    if (typeof targetStep === 'number' && targetStep >= 0 && targetStep === currentStep && targetPhase === boardPhase) {
      lastAppliedReplaySearchRef.current = location.search;
    }
  }, [
    boardPhase,
    currentStep,
    data,
    location.search,
    requestedFocusEventIndex,
    requestedFocusStep,
    requestedStep,
    requestedPhase,
  ]);

  useEffect(() => {
    if (!data || !replayIdFromRoute) return;
    if (data.player_id !== requestedPlayerId) return;
    let targetStep = requestedStep ?? requestedFocusStep;
    if (targetStep === null && requestedFocusEventIndex !== null) {
      targetStep = data.log.findIndex((entry) => entry.source_event_index === requestedFocusEventIndex);
    }
    const targetPhase = requestedPhase ?? 'pre';
    const hasUnappliedUrlTarget =
      typeof targetStep === 'number'
      && targetStep >= 0
      && location.search !== lastAppliedReplaySearchRef.current
      && (targetStep !== currentStep || targetPhase !== boardPhase);
    if (hasUnappliedUrlTarget) return;

    const nextParams = new URLSearchParams(location.search);
    // canonical 路由下 replayId 已在 path 中，清掉冗余的 ?id=；legacy 路由继续保留 query id
    if (replayIdFromPath) {
      nextParams.delete('id');
    } else {
      nextParams.set('id', replayIdFromRoute);
    }
    nextParams.set('player_id', String(viewPlayerId));
    nextParams.set('step', String(currentStep));
    nextParams.set('phase', boardPhase);
    const nextSearch = `?${nextParams.toString()}`;
    if (nextSearch !== location.search) {
      navigate({ pathname: location.pathname, search: nextSearch }, { replace: true });
    }
  }, [boardPhase, currentStep, data, location.pathname, location.search, navigate, replayIdFromPath, replayIdFromRoute, requestedPlayerId, viewPlayerId]);

  const moveBoardStep = useCallback((direction: 1 | -1) => {
    if (!data || !currentEntry) return;

    if (direction > 0) {
      if (boardPhase === 'pre' && currentHasReachPhase) {
        setBoardPhase('reach');
        return;
      }
      if ((boardPhase === 'pre' || boardPhase === 'reach') && currentHasPostPhase) {
        setBoardPhase('post');
        return;
      }
      for (let i = currentStep + 1; i < data.log.length; i++) {
        if (isRedundantResponseStep(i)) continue;
        if (data.log[i]?.chosen) {
          goToStep(i);
          setBoardPhase('pre');
          return;
        }
      }
      return;
    }

    if (boardPhase === 'post') {
      if (currentHasReachPhase) {
        setBoardPhase('reach');
        return;
      }
      setBoardPhase('pre');
      return;
    }
    if (boardPhase === 'reach') {
      setBoardPhase('pre');
      return;
    }
    for (let i = currentStep - 1; i >= 0; i--) {
      if (isRedundantResponseStep(i)) continue;
      if (!data.log[i]?.chosen) continue;
      goToStep(i);
      if (hasReplayPostAction(data.log[i])) {
        setBoardPhase('post');
      } else if (hasReplayReachPhase(data.log[i])) {
        setBoardPhase('reach');
      } else {
        setBoardPhase('pre');
      }
      return;
    }
  }, [data, currentEntry, boardPhase, currentHasPostPhase, currentHasReachPhase, currentStep, goToStep, isRedundantResponseStep]);

  const handleStepForward = useCallback(() => {
    resetBoardPhase();
    setShowOpponentHands(false);
    stepForward();
  }, [resetBoardPhase, stepForward]);

  const handleStepBackward = useCallback(() => {
    resetBoardPhase();
    setShowOpponentHands(false);
    stepBackward();
  }, [resetBoardPhase, stepBackward]);

  const handleGoToStep = useCallback((step: number) => {
    resetBoardPhase();
    setShowOpponentHands(false);
    goToStep(step);
  }, [goToStep, resetBoardPhase]);

  const handleGoToKyoku = useCallback((kyokuIdx: number) => {
    resetBoardPhase();
    setShowOpponentHands(false);
    goToKyoku(kyokuIdx);
  }, [goToKyoku, resetBoardPhase]);

  const jumpToPrevDiff = useCallback(() => {
    if (!data) return;
    const pid = data.player_id;
    for (let i = currentStep - 1; i >= 0; i--) {
      if (isForcedRiichiTsumogiriEntry(data.log[i])) continue;
      if (isReplayReviewDiffForPlayer(data.log[i], pid, activeTeacherModel)) {
        resetBoardPhase();
        setShowOpponentHands(false);
        goToStep(i);
        return;
      }
    }
  }, [activeTeacherModel, data, currentStep, goToStep, resetBoardPhase]);

  const jumpToNextDiff = useCallback(() => {
    if (!data) return;
    const pid = data.player_id;
    for (let i = currentStep + 1; i < data.log.length; i++) {
      if (isForcedRiichiTsumogiriEntry(data.log[i])) continue;
      if (isReplayReviewDiffForPlayer(data.log[i], pid, activeTeacherModel)) {
        resetBoardPhase();
        setShowOpponentHands(false);
        goToStep(i);
        return;
      }
    }
  }, [activeTeacherModel, data, currentStep, goToStep, resetBoardPhase]);

  const isOwnDiscardStep = useCallback((step: number) => {
    if (!data || step < 0 || step >= data.log.length) return false;
    const action = getActualReplayAction(data.log[step]);
    return action?.type === 'dahai' && action.actor === viewPlayerId;
  }, [data, viewPlayerId]);

  const jumpToPrevOwnDiscard = useCallback(() => {
    if (!data) return;
    for (let i = currentStep - 1; i >= 0; i--) {
      if (!isOwnDiscardStep(i)) continue;
      resetBoardPhase();
      setShowOpponentHands(false);
      goToStep(i);
      return;
    }
  }, [data, currentStep, goToStep, isOwnDiscardStep, resetBoardPhase]);

  const jumpToNextOwnDiscard = useCallback(() => {
    if (!data) return;
    for (let i = currentStep + 1; i < data.log.length; i++) {
      if (!isOwnDiscardStep(i)) continue;
      resetBoardPhase();
      setShowOpponentHands(false);
      goToStep(i);
      return;
    }
  }, [data, currentStep, goToStep, isOwnDiscardStep, resetBoardPhase]);

  // 键盘快捷键（Stats dialog / 抽屉打开时不响应回放导航；Escape 关闭覆盖层）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (showStats || activeDrawer) {
          e.preventDefault();
          setShowStats(false);
          setActiveDrawer(null);
        }
        return;
      }
      if (showStats || activeDrawer) return;
      const target = e.target as HTMLElement;
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return;
      // 提前拦截方向键和空格，防止页面滚动
      if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown',' ','h','j','k','l'].includes(e.key)) {
        e.preventDefault();
      }
      switch (e.key) {
        case 'ArrowLeft':  case 'h': handleStepBackward(); break;
        case 'ArrowRight': case 'l': handleStepForward(); break;
        case 'ArrowDown':  case 'j': moveBoardStep(1); break;
        case 'ArrowUp':    case 'k': moveBoardStep(-1); break;
      }
    };
    // useCapture=true 确保在其他元素之前捕获
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [handleStepBackward, handleStepForward, moveBoardStep, showStats, activeDrawer]);

  // 滚轮步进只在牌桌区域生效：左右面板、Stats dialog 与 ctrl+滚轮（浏览器缩放）不被拦截
  useEffect(() => {
    const el = boardViewportRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      if (e.ctrlKey) return;
      e.preventDefault();
      if (e.deltaY > 0) moveBoardStep(1);
      else moveBoardStep(-1);
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, [moveBoardStep]);

  // 宽布局下左右栏转为静态三栏：清空抽屉 modal state，避免其泄漏后持续屏蔽回放快捷键。
  // 布局本身仍由 CSS container query 驱动，这里只做 React 状态收口。
  useEffect(() => {
    const shell = workspaceShellRef.current;
    if (!shell) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width >= 1160) {
        setActiveDrawer(null);
      }
    });
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);

  // 优先使用后端返回的真实玩家名，fallback 到 P0/P1/P2/P3
  const playerNames = normalizeReplayPlayerNames(data);

  const isForcedRiichiTsumogiri = isForcedRiichiTsumogiriEntry(currentEntry);

  // 适配数据
  const battleState = useMemo(
    () => {
      if (!currentEntry || !data) return null;
      const prevEntry = currentStep > 0 ? data.log[currentStep - 1] : null;
      return entryToBattleState(currentEntry, playerNames, viewPlayerId, boardPhase, prevEntry);
    },
    [currentEntry, data, currentStep, playerNames, viewPlayerId, boardPhase],
  );
  // 只在打出前显示权重条；打出后/立直展示阶段不再显示。
  const baseLogitData = currentEntry && !currentEntry.is_obs && boardPhase === 'pre' && !isForcedRiichiTsumogiri
    ? buildLogitData(currentEntry)
    : undefined;
  const replayTeacherModels = useMemo(() => {
    if (!data) return [];
    const models: string[] = [];
    const addModel = (model: string | null | undefined) => {
      const normalized = model?.trim();
      if (normalized && !models.includes(normalized)) models.push(normalized);
    };

    data.selected_teacher_models?.forEach((model) => addModel(model.label));
    data.teacher_review_overlays?.forEach((overlay) => addModel(overlay.model));
    addModel(data.teacher_review_overlay?.model);
    data.log.forEach((logEntry) => {
      logEntry.teacher_reviews?.forEach((review) => addModel(review.model));
      addModel(logEntry.teacher_review?.model);
    });
    // 保持插入顺序：selected_teacher_models 在前，与牌局里紫色 bar 的顺序一致。
    return models;
  }, [data]);
  const replayTeacherModelsKey = replayTeacherModels.join('\n');

  useEffect(() => {
    if (replayTeacherModels.length === 0) return;
    if (!activeTeacherModel || !replayTeacherModels.includes(activeTeacherModel)) {
      setActiveTeacherModel(replayTeacherModels[0]);
    }
  }, [replayTeacherModelsKey, activeTeacherModel, replayTeacherModels]);

  const entry = currentEntry;
  const k = entry?.kyoku_key;
  const kyokuLabel = k ? `${CN_BAKAZE[k.bakaze] ?? k.bakaze}${k.kyoku}局 ${k.honba}本场` : '';
  const localReviewerLabel = displayReviewerModelLabel(
    data?.selected_teacher_models?.[0]?.label
    ?? data?.model_label
    ?? data?.bot_type
    ?? replayPlayerDisplayName(playerNames, viewPlayerId),
  );

  // 当前步 bot 选择和实际打出
  const chosenPai = entry?.chosen?.type === 'dahai' ? entry.chosen.pai ?? null : null;
  const resultSummary = useMemo(
    () => {
      if (!currentEntry) return null;
      return buildReplayResultSummary(events, currentEntry, playerNames);
    },
    [events, currentEntry, playerNames],
  );
  const replayHands = useMemo(
    () => buildReplayHandsForBoard(events as ReplayEvent[] | null, data, currentEntry ?? null, boardPhase),
    [events, data, currentEntry, boardPhase],
  );
  const replayHandsForTable = useMemo(() => {
    if (!resultSummary || resultSummary.type !== 'hora' || resultSummary.winner == null) {
      return showOpponentHands ? replayHands : null;
    }
    const baseHands = (replayHands ?? [[], [], [], []]).map((tiles) => [...tiles]) as Array<string[] | null>;
    baseHands[resultSummary.winner] = [...resultSummary.winnerHand];
    if (showOpponentHands) return baseHands;
    return baseHands.map((tiles, pid) => (pid === resultSummary.winner ? tiles : null));
  }, [replayHands, resultSummary, showOpponentHands]);

  // R2：决策 entry 的动作前快照是唯一权威状态源。
  // 自家手牌/摸牌/候选一律来自 currentEntry（经 replayAdapter 处理），
  // 不再用 raw-event 重放的 replayHands 覆盖，也不再据此过滤候选。
  // replayHands 仅用于对手 Oracle reveal / 终局牌姿 / 调试对照。
  const effectiveReplayEntry = currentEntry;
  const effectiveBattleState = battleState;
  const logitData = currentEntry && !currentEntry.is_obs && boardPhase === 'pre' && !isForcedRiichiTsumogiri
    ? buildLogitData(currentEntry)
    : baseLogitData;

  if (loading) {
    return (
      <div style={centeredStatusStyle}>
        <Loader2 size={32} className="animate-spin" style={{ color: 'var(--accent)' }} />
        <p style={mutedStatusTextStyle}>加载回放数据中...</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div style={centeredStatusStyle}>
        <p style={errorStatusTextStyle}>{error || '未找到回放数据'}</p>
        <button onClick={() => navigate(routes.reviewNew)} style={backLinkButtonStyle}>
          返回上传
        </button>
      </div>
    );
  }

  const phaseLabel = REPLAY_BOARD_PHASE_LABELS[boardPhase];
  const rightPanelId = 'review-workspace-right-panel';

  return (
    <div className="review-workspace-shell" ref={workspaceShellRef}>
      {showStats && data && <ReplayStatsDialog data={data} onClose={() => setShowStats(false)} />}

      <div className="review-workspace-grid">


        <div className="review-workspace-center">
          <div className="review-workspace-compact-toolbar">
            <button
              type="button"
              className="review-workspace-compact-toolbar__button"
              aria-expanded={activeDrawer === 'right'}
              aria-controls={rightPanelId}
              onClick={() => setActiveDrawer((current) => (current === 'right' ? null : 'right'))}
            >回放导航 · 决策分析</button>
            <span>{kyokuLabel || '回放'} · {currentStep + 1}/{totalSteps}</span>
          </div>
          <div className="review-workspace-board" ref={boardViewportRef}>
            {effectiveBattleState ? (
              <>
                <MahjongTable
              state={effectiveBattleState}
              onAction={() => {}}
              isMyTurn={false}
              selectedTile={chosenPai}
              selectedTileIdx={null}
              onTileSelect={() => {}}
              autoHora={autoHora} setAutoHora={setAutoHora}
              noMeld={noMeld} setNoMeld={setNoMeld}
              autoTsumogiri={autoTsumogiri} setAutoTsumogiri={setAutoTsumogiri}
              mode="replay"
              logitData={logitData}
              activeTeacherModel={activeTeacherModel}
              revealedOpponentHands={replayHandsForTable}
              onToggleOpponentHands={() => setShowOpponentHands((v) => !v)}
            />
            {resultSummary && <ReplayResultOverlay summary={resultSummary} playerNames={playerNames} />}
            {focusResolution && focusResolution !== 'exact' && (
              <div
                style={{
                  position: 'absolute',
                  left: 16,
                  bottom: 16,
                  zIndex: 35,
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid rgba(255,255,255,0.16)',
                  background: 'rgba(17,24,39,0.82)',
                  color: '#f3f4f6',
                  fontSize: 12,
                  boxShadow: '0 10px 28px rgba(0,0,0,0.25)',
                }}
              >
                {focusResolution === 'nearest' ? '已跳到最近的回放决策步' : '未找到可定位的焦点步'}
              </div>
            )}

              </>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                <p style={{ color: 'var(--text-muted)', fontSize: 14 }}>选择一个步骤</p>
              </div>
            )}
          </div>
        </div>

        <aside
          className="review-workspace-right"
          id={rightPanelId}
          data-open={activeDrawer === 'right'}
          aria-label="决策分析"
        >
          <div className="review-workspace-panel-header">
            <span>回放导航 · 决策分析</span>
            <button
              type="button"
              className="review-workspace-panel-header__close review-workspace-drawer-only"
              onClick={() => setActiveDrawer(null)}
              aria-label="关闭决策分析"
            >×</button>
          </div>
          <div className="review-workspace-nav-section">
                        <ReplayWorkspaceNavigation
              onShowStats={() => setShowStats(true)}
              kyokuOrder={data.kyoku_order}
              currentKyoku={currentKyoku}
              onGoToKyoku={handleGoToKyoku}
              kyokuLabel={kyokuLabel}
              currentStep={currentStep}
              totalSteps={totalSteps}
              boardPhase={boardPhase}
              onGoToStep={handleGoToStep}
              onPrevKyoku={() => handleGoToKyoku(Math.max(0, currentKyoku - 1))}
              onNextKyoku={() => handleGoToKyoku(Math.min(totalKyoku - 1, currentKyoku + 1))}
              prevKyokuDisabled={currentKyoku === 0}
              nextKyokuDisabled={currentKyoku === totalKyoku - 1}
              onPrevStep={handleStepBackward}
              onNextStep={handleStepForward}
              prevStepDisabled={currentStep === 0 && boardPhase === 'pre'}
              nextStepDisabled={currentStep === totalSteps - 1 && !currentHasPostPhase && !currentHasReachPhase}
              onPrevOwnDiscard={jumpToPrevOwnDiscard}
              onNextOwnDiscard={jumpToNextOwnDiscard}
              onPrevDiff={jumpToPrevDiff}
              onNextDiff={jumpToNextDiff}
            />
          </div>
          <div className="review-workspace-right-meta">
            当前视角：P{viewPlayerId} {replayPlayerDisplayName(playerNames, viewPlayerId)} · {phaseLabel}
          </div>
          <div className="review-workspace-right-body">
            <ReplayDecisionPanel
              entry={isForcedRiichiTsumogiri ? null : effectiveReplayEntry}
              step={currentStep}
              totalSteps={totalSteps}
              compact={false}
              playerNames={playerNames}
              currentPlayerId={viewPlayerId}
              localReviewerLabel={localReviewerLabel}
              availableTeacherModels={replayTeacherModels}
              activeTeacherModel={activeTeacherModel}
              onActiveTeacherModelChange={setActiveTeacherModel}
              hideWeights={boardPhase === 'post'}
            />
          </div>
        </aside>
      </div>

      {activeDrawer && (
        <button
          type="button"
          className="review-workspace-scrim"
          aria-label="关闭面板"
          onClick={() => setActiveDrawer(null)}
        />
      )}
    </div>
  );
}




function displayReviewerModelLabel(raw: string | undefined): string {
  const label = (raw || '主视角模型').trim();
  if (label === '70k.pth') return '70k';
  if (label === 'V2 candidate') return 'candidate';
  return label.endsWith('.pth') ? label.slice(0, -4) : label;
}

type ResultSummary = {
  type: 'hora' | 'ryukyoku';
  title: string;
  subtitle: string;
  honba: number;
  kyotaku: number;
  doraMarkers: string[];
  uraMarkers: string[];
  yakuLines: string[];
  scoreLines: Array<{ pid: number; label: string; name: string; before: number; delta: number; after: number }>;
  winnerHand: string[];
  winnerMelds: Array<{ type: string; pai: string; consumed: string[] }>;
  winner: number | null;
  winTile: string | null;
};

const YAKU_LABELS: Record<string, string> = {
  Riichi: '立直',
  Ippatsu: '一发',
  'Menzen Tsumo': '门前清自摸和',
  Tsumo: '门前清自摸和',
  Pinfu: '平和',
  Tanyao: '断幺九',
  Dora: '宝牌',
  'Aka Dora': '赤宝牌',
  'Ura Dora': '里宝牌',
  Iipeiko: '一杯口',
  Ryanpeikou: '二杯口',
  Ittsu: '一气通贯',
  Chanta: '混全带幺九',
  Junchan: '纯全带幺九',
  Toitoi: '对对和',
  Sanankou: '三暗刻',
  Sankantsu: '三杠子',
  Shousangen: '小三元',
  Honroutou: '混老头',
  Honitsu: '混一色',
  Chinitsu: '清一色',
  Chiitoitsu: '七对子',
  'Sanshoku Doukou': '三色同刻',
  'Sanshoku Doujun': '三色同顺',
  'Yakuhai (chun)': '役牌 中',
  'Yakuhai (haku)': '役牌 白',
  'Yakuhai (hatsu)': '役牌 发',
  'Yakuhai (east)': '役牌 东',
  'Yakuhai (south)': '役牌 南',
  'Yakuhai (west)': '役牌 西',
  'Yakuhai (north)': '役牌 北',
};

const RESULT_LEVEL_LABELS: Record<string, string> = {
  mangan: '满贯',
  haneman: '跳满',
  baiman: '倍满',
  sanbaiman: '三倍满',
  yakuman: '役满',
};

function getSeatLabel(oya: number, pid: number): string {
  const order = ['东', '南', '西', '北'];
  return order[(pid - oya + 4) % 4];
}

function normalizeTileForDora(tile: string): string {
  return tile.endsWith('r') ? tile.slice(0, 2) : tile;
}

function indicatorToDoraTile(tile: string): string | null {
  const normalized = normalizeTileForDora(tile);
  const num = normalized[0];
  const suit = normalized[1];
  const honorCycle: Record<string, string> = {
    E: 'S',
    S: 'W',
    W: 'N',
    N: 'E',
    P: 'F',
    F: 'C',
    C: 'P',
  };
  if (normalized in honorCycle) {
    return honorCycle[normalized];
  }
  if (suit === 'm' || suit === 'p' || suit === 's') {
    const next = num === '9' ? '1' : String(Number(num) + 1);
    return `${next}${suit}`;
  }
  return null;
}

function countMarkerDora(allTiles: string[], markers: string[]): number {
  let total = 0;
  for (const marker of markers) {
    const doraTile = indicatorToDoraTile(marker);
    if (!doraTile) continue;
    const normalizedDora = normalizeTileForDora(doraTile);
    total += allTiles.filter((tile) => normalizeTileForDora(tile) === normalizedDora).length;
  }
  return total;
}

function buildFallbackYakuDetails(
  yakuList: string[],
  winnerTiles: string[],
  winnerMelds: Array<{ type: string; pai: string; consumed: string[] }>,
  doraMarkers: string[],
  uraMarkers: string[],
): Array<{ key: string; name: string; han: number }> {
  const doraLike = new Set(['Dora', 'Ura Dora', 'Aka Dora']);
  const grouped = new Map<string, number>();
  for (const name of yakuList) {
    if (doraLike.has(name)) continue;
    grouped.set(name, (grouped.get(name) ?? 0) + 1);
  }

  const allTiles = [
    ...winnerTiles,
    ...winnerMelds.flatMap((meld) => [...meld.consumed, meld.pai].filter(Boolean)),
  ];
  const details = Array.from(grouped.entries()).map(([name, han]) => ({ key: name, name, han }));
  const doraCount = countMarkerDora(allTiles, doraMarkers);
  const uraCount = countMarkerDora(allTiles, uraMarkers);
  const akaCount = allTiles.filter((tile) => tile.endsWith('r')).length;

  if (doraCount > 0) details.push({ key: 'Dora', name: 'Dora', han: doraCount });
  if (uraCount > 0) details.push({ key: 'Ura Dora', name: 'Ura Dora', han: uraCount });
  if (akaCount > 0) details.push({ key: 'Aka Dora', name: 'Aka Dora', han: akaCount });

  return details;
}

function sortTilesForResult(tiles: string[], winTile?: string | null): string[] {
  const sorted = [...tiles].sort((a, b) => (TILE_ORDER[a] ?? 99) - (TILE_ORDER[b] ?? 99));
  if (!winTile) return sorted;
  const exactIdx = sorted.findIndex((tile) => tile === winTile);
  if (exactIdx >= 0) {
    const [tile] = sorted.splice(exactIdx, 1);
    sorted.push(tile);
    return sorted;
  }
  const norm = winTile.endsWith('r') ? winTile.slice(0, 2) : winTile;
  const normIdx = sorted.findIndex((tile) => (tile.endsWith('r') ? tile.slice(0, 2) : tile) === norm);
  if (normIdx >= 0) {
    const [tile] = sorted.splice(normIdx, 1);
    sorted.push(tile);
  }
  return sorted;
}

function buildReplayResultSummary(
  events: ReplayEvent[] | null,
  entry: ReplayData['log'][number] | undefined,
  playerNames: string[],
): ResultSummary | null {
  if (!events || !entry) return null;
  const action = getActualReplayAction(entry) as Action | null;
  if (!action || (action.type !== 'hora' && action.type !== 'ryukyoku')) return null;
  const kyokuKey = entry.kyoku_key;
  if (!kyokuKey) return null;

  let startIdx = -1;
  let endIdx = events.length;
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (
      ev.type === 'start_kyoku' &&
      ev.bakaze === kyokuKey.bakaze &&
      ev.kyoku === kyokuKey.kyoku &&
      ev.honba === kyokuKey.honba
    ) {
      startIdx = i;
      continue;
    }
    if (startIdx >= 0 && i > startIdx && ev.type === 'start_kyoku') {
      endIdx = i;
      break;
    }
  }
  if (startIdx < 0) return null;
  const kyokuEvents = events.slice(startIdx, endIdx);
  const resultEvent = kyokuEvents.find((ev) => {
    if (ev.type !== action.type) return false;
    if (action.type === 'hora') {
      return ev.actor === action.actor && ev.pai === action.pai && ev.target === action.target;
    }
    return true;
  }) as ReplayEvent | undefined;
  if (!resultEvent) return null;

  const startEv = kyokuEvents[0];
  const hands = ((startEv.tehais as string[][] | undefined) ?? [[], [], [], []]).map((tiles) => [...tiles]);
  const melds = [[], [], [], []] as Array<Array<{ type: string; pai: string; consumed: string[] }>>;
  const doraMarkers = [String(startEv.dora_marker ?? '')].filter(Boolean);
  let inferredKyotaku = Number(startEv.kyotaku ?? 0);
  const inferredHonba = Number(startEv.honba ?? kyokuKey.honba ?? 0);
  const resultIndex = kyokuEvents.indexOf(resultEvent);
  for (let i = 1; i < resultIndex; i++) {
    const ev = kyokuEvents[i];
    const type = String(ev.type ?? '');
    const actor = Number(ev.actor ?? -1);
    if (type === 'tsumo' && actor >= 0) {
      hands[actor].push(String(ev.pai ?? ''));
    } else if (type === 'dahai' && actor >= 0) {
      removeTileOnce(hands[actor], String(ev.pai ?? ''));
    } else if (['chi', 'pon', 'daiminkan', 'ankan'].includes(type) && actor >= 0) {
      const consumed = ((ev.consumed as string[] | undefined) ?? []).map(String);
      for (const tile of consumed) removeTileOnce(hands[actor], tile);
      melds[actor].push({ type, pai: String(ev.pai ?? ''), consumed });
    } else if (type === 'kakan_accepted' && actor >= 0) {
      removeTileOnce(hands[actor], String(ev.pai ?? ''));
      const meld = melds[actor].find((m) => m.type === 'pon' && m.pai === String(ev.pai ?? ''));
      if (meld) meld.type = 'kakan';
    } else if (type === 'dora') {
      doraMarkers.push(String(ev.dora_marker ?? ''));
    } else if (type === 'reach_accepted') {
      if (typeof ev.kyotaku === 'number') inferredKyotaku = Number(ev.kyotaku);
      else inferredKyotaku += 1;
    }
  }

  if (action.type === 'hora' && action.actor !== undefined && action.pai && !action.is_tsumo) {
    hands[action.actor].push(action.pai);
  }

  const afterScores = ((resultEvent.scores as number[] | undefined) ?? entry.scores ?? [0, 0, 0, 0]).map(Number);
  const deltas = ((resultEvent.deltas as number[] | undefined) ?? [0, 0, 0, 0]).map(Number);
  const beforeScores = afterScores.map((score, idx) => score - (deltas[idx] ?? 0));
  const honba = Number(resultEvent.honba ?? inferredHonba);
  const kyotaku = Number(resultEvent.kyotaku ?? inferredKyotaku);

  if (action.type === 'ryukyoku') {
    return {
      type: 'ryukyoku',
      title: '流局',
      subtitle: '本局无和牌',
      honba,
      kyotaku,
      doraMarkers,
      uraMarkers: ((resultEvent.ura_dora_markers as string[] | undefined) ?? []).map(String),
      yakuLines: [],
      scoreLines: afterScores.map((score, pid) => ({
        pid,
        label: getSeatLabel(entry.oya, pid),
        name: replayPlayerDisplayName(playerNames, pid),
        before: beforeScores[pid],
        delta: deltas[pid] ?? 0,
        after: score,
      })),
      winnerHand: [],
      winnerMelds: [],
      winner: null,
      winTile: null,
    };
  }

  const winner = Number(action.actor);
  const winnerHand = winner >= 0 ? sortTilesForResult(hands[winner], String(action.pai ?? '')) : [];
  const winnerMelds = winner >= 0 ? melds[winner] : [];
  const cost = (resultEvent.cost as Action['cost']) ?? action.cost ?? {};
  const yakuList = ((resultEvent.yaku as string[] | undefined) ?? action.yaku ?? []).map(String);
  const structuredYaku =
    ((resultEvent.yaku_details as Action['yaku_details'] | undefined) ?? action.yaku_details ?? []).map((detail) => ({
      key: String(detail.key),
      name: String(detail.name),
      han: Number(detail.han ?? 0),
    }));
  const yakuDetails =
    structuredYaku.length > 0
      ? structuredYaku
      : buildFallbackYakuDetails(
          yakuList,
          winnerHand,
          winnerMelds,
          doraMarkers,
          ((resultEvent.ura_dora_markers as string[] | undefined) ?? (action.ura_dora_markers ?? [])).map(String),
        );
  const yakuLines = yakuDetails.map((detail) => {
    const label = YAKU_LABELS[detail.name] ?? detail.name;
    return `${label} · ${detail.han}翻`;
  });
  const levelKey = String(cost?.yaku_level ?? '').toLowerCase();
  const baseRonPoints = Number(cost?.main ?? 0);
  const baseTsumoMain = Number(cost?.main ?? 0);
  const baseTsumoAdditional = Number(cost?.additional ?? 0);
  const title = levelKey
    ? action.is_tsumo
      ? baseTsumoAdditional > 0 && baseTsumoAdditional !== baseTsumoMain
        ? `${RESULT_LEVEL_LABELS[levelKey] ?? cost?.yaku_level} ${baseTsumoAdditional}-${baseTsumoMain}点`
        : `${RESULT_LEVEL_LABELS[levelKey] ?? cost?.yaku_level} ${baseTsumoMain}点`
      : `${RESULT_LEVEL_LABELS[levelKey] ?? cost?.yaku_level} ${baseRonPoints || Number(cost?.total ?? 0)}点`
    : action.is_tsumo
      ? `${Number(resultEvent.han ?? action.han ?? 0)}翻 ${Number(resultEvent.fu ?? action.fu ?? 0)}符 ${baseTsumoAdditional}-${baseTsumoMain}点`
      : `${Number(resultEvent.han ?? action.han ?? 0)}翻 ${Number(resultEvent.fu ?? action.fu ?? 0)}符 ${baseRonPoints}点`;
  const subtitle = action.is_tsumo ? '自摸和了' : '荣和';

  return {
    type: 'hora',
    title,
    subtitle,
    honba,
    kyotaku,
    doraMarkers,
    uraMarkers: ((resultEvent.ura_dora_markers as string[] | undefined) ?? (action.ura_dora_markers ?? [])).map(String),
    yakuLines,
    scoreLines: afterScores.map((score, pid) => ({
      pid,
      label: getSeatLabel(entry.oya, pid),
      name: replayPlayerDisplayName(playerNames, pid),
      before: beforeScores[pid],
      delta: deltas[pid] ?? 0,
      after: score,
    })),
    winnerHand,
    winnerMelds,
    winner,
    winTile: String(action.pai ?? ''),
  };
}

function ReplayResultOverlay({ summary, playerNames }: { summary: ResultSummary; playerNames: string[] }) {
  const winnerName = summary.winner !== null ? replayPlayerDisplayName(playerNames, summary.winner) : null;
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
        zIndex: 40,
        background: 'rgba(0, 0, 0, 0.34)',
      }}
    >
      <div
        style={{
          width: 640,
          maxWidth: '78%',
          minHeight: 360,
          background: 'rgba(2, 8, 14, 0.86)',
          border: '2px solid rgba(230, 237, 245, 0.65)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.45)',
          padding: '30px 42px 28px',
          color: '#f8fafc',
          display: 'grid',
          gridTemplateRows: 'auto auto auto 1fr',
          gap: 18,
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', alignItems: 'start', gap: 18 }}>
          <div style={{ fontSize: 40, fontWeight: 500, lineHeight: 1.08, letterSpacing: 0 }}>
            {summary.title}
          </div>
          <div style={{ textAlign: 'right', color: 'rgba(226,232,240,0.82)', fontSize: 15, lineHeight: 1.5, paddingTop: 5 }}>
            <div>{summary.honba}本場</div>
            <div>供託:{summary.kyotaku}</div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center', minWidth: 0 }}>
            <span style={{ fontSize: 18, color: '#f8fafc', marginRight: 2 }}>ドラ</span>
            {summary.doraMarkers.length > 0
              ? summary.doraMarkers.map((tile, idx) => <Tile key={`d-${tile}-${idx}`} tile={tile} size="small" />)
              : <span style={{ fontSize: 16, color: 'rgba(226,232,240,0.72)' }}>-</span>
            }
          </div>
          <div style={{ display: 'flex', gap: 4, alignItems: 'center', minWidth: 0 }}>
            <span style={{ fontSize: 18, color: '#f8fafc', marginRight: 2 }}>裏</span>
            {summary.uraMarkers.length > 0
              ? summary.uraMarkers.map((tile, idx) => <Tile key={`u-${tile}-${idx}`} tile={tile} size="small" />)
              : <span style={{ fontSize: 16, color: 'rgba(226,232,240,0.72)' }}>-</span>
            }
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1fr', gap: 20, alignItems: 'start' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 22, color: '#f8fafc', marginBottom: 8 }}>
              {winnerName ? `${winnerName}` : summary.subtitle}
            </div>
            {summary.winner !== null ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
                {summary.winnerHand.map((tile, idx) => (
                  <Tile
                    key={`h-${tile}-${idx}`}
                    tile={tile}
                    size="small"
                    highlighted={tile === summary.winTile && idx === summary.winnerHand.lastIndexOf(tile)}
                  />
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 24, color: '#f8fafc' }}>流局</div>
            )}
          </div>
          <div>
            {summary.yakuLines.length > 0 ? (
              <div style={{ display: 'grid', gap: 4 }}>
                {summary.yakuLines.map((line, idx) => (
                  <div key={`y-${idx}`} style={{ fontSize: 20, color: '#f8fafc', lineHeight: 1.15 }}>{line}</div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 20, color: 'rgba(226,232,240,0.72)' }}>-</div>
            )}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 6, alignSelf: 'end' }}>
          {summary.scoreLines.map((row) => (
            <div key={row.pid} style={{
              display: 'grid',
              gridTemplateColumns: '44px minmax(120px, 1fr) 110px 110px',
              alignItems: 'center',
              gap: 10,
              fontSize: 24,
              lineHeight: 1.1,
            }}>
              <span style={{ color: 'rgba(226,232,240,0.82)', textAlign: 'right' }}>{row.label}</span>
              <span style={{ color: '#f8fafc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {row.name}
              </span>
              <span style={{ fontFamily: 'Menlo, Consolas, monospace', color: '#f8fafc', textAlign: 'right' }}>
                {row.after.toLocaleString()}
              </span>
              <span style={{
                fontFamily: 'Menlo, Consolas, monospace',
                color: row.delta > 0 ? '#22d3ee' : row.delta < 0 ? '#ff2b2b' : 'rgba(226,232,240,0.64)',
                textAlign: 'right',
              }}>
                {row.delta >= 0 ? '+' : ''}{row.delta.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
