// src/replay_ui/src/components/ReviewWorkspace/ReplayWorkspaceNavigation.tsx
//
// Review Workspace 右栏导航（与 Q/P 分析共用竖栏）。
// 纯展示 + callback 转发组件，不实现任何回放跳转算法（算法在 GameBoardReplayPage）。
import { useState, type CSSProperties } from 'react';
import type { ReplayData } from '../../types/replay';
import type { ReplayBoardPhase } from '../../utils/replayAdapter';
import { CN_BAKAZE } from '../../utils/constants';

const PHASE_LABELS: Record<ReplayBoardPhase, string> = {
  pre: '动作前',
  reach: '立直',
  post: '动作后',
};

interface ReplayWorkspaceNavigationProps {
  onShowStats: () => void;
  kyokuOrder: ReplayData['kyoku_order'];
  currentKyoku: number;
  onGoToKyoku: (index: number) => void;
  kyokuLabel: string;
  currentStep: number;
  totalSteps: number;
  boardPhase: ReplayBoardPhase;
  onGoToStep: (step: number) => void;
  onPrevKyoku: () => void;
  onNextKyoku: () => void;
  prevKyokuDisabled: boolean;
  nextKyokuDisabled: boolean;
  onPrevStep: () => void;
  onNextStep: () => void;
  prevStepDisabled: boolean;
  nextStepDisabled: boolean;
  onPrevOwnDiscard: () => void;
  onNextOwnDiscard: () => void;
  onPrevDiff: () => void;
  onNextDiff: () => void;
}

export function ReplayWorkspaceNavigation({
  onShowStats,
  kyokuOrder,
  currentKyoku,
  onGoToKyoku,
  kyokuLabel,
  currentStep,
  totalSteps,
  boardPhase,
  onGoToStep,
  onPrevKyoku,
  onNextKyoku,
  prevKyokuDisabled,
  nextKyokuDisabled,
  onPrevStep,
  onNextStep,
  prevStepDisabled,
  nextStepDisabled,
  onPrevOwnDiscard,
  onNextOwnDiscard,
  onPrevDiff,
  onNextDiff,
}: ReplayWorkspaceNavigationProps) {
  const [kyokuListOpen, setKyokuListOpen] = useState(false);
  return (
    <div style={controlsStyle}>
      {/* 顶部工具区：返回位置替换为「对局列表」展开按钮 */}
      <div style={utilityGridStyle}>
        <button
          type="button"
          onClick={() => setKyokuListOpen(v => !v)}
          style={utilityButtonStyle}
          aria-expanded={kyokuListOpen}
          title="展开/收起全部小局"
        >
          {kyokuListOpen ? '收起' : '对局列表'}
        </button>
        <button type="button" onClick={onShowStats} style={utilityButtonStyle} title="统计">统计</button>
      </div>

      {/* 对局列表：默认收起，展开后完整 kyoku_order 点击跳局 */}
      {kyokuListOpen && (
        <div>
          <div style={sectionTitleStyle}>全部小局</div>
          <div style={{ display: 'grid', gap: 3 }}>
            {kyokuOrder.map((kyoku, idx) => {
              const active = idx === currentKyoku;
              return (
                <button
                  key={`${kyoku.bakaze}-${kyoku.kyoku}-${kyoku.honba}-${idx}`}
                  type="button"
                  onClick={() => { onGoToKyoku(idx); setKyokuListOpen(false); }}
                  style={kyokuItemStyle(active)}
                >
                  {CN_BAKAZE[kyoku.bakaze] ?? kyoku.bakaze}{kyoku.kyoku}局 · {kyoku.honba}本场
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* 当前回放位置 */}
      <div>
        <div style={sectionTitleStyle}>当前位置</div>
        <div style={metaStyle}>
          <span>{kyokuLabel || '回放'}</span>
          <span>{currentStep + 1}/{totalSteps} · {PHASE_LABELS[boardPhase]}</span>
        </div>
        <input
          type="range"
          min={0}
          max={totalSteps - 1}
          value={currentStep}
          onChange={e => onGoToStep(parseInt(e.target.value))}
          style={sliderStyle}
        />
      </div>

      {/* 步进控制：跳转算法全部在页面 controller 中 */}
      <div>
        <div style={sectionTitleStyle}>步进控制</div>
        <div style={buttonGridStyle}>
          <button type="button" onClick={onPrevKyoku} disabled={prevKyokuDisabled} style={sideButtonStyle(prevKyokuDisabled)} title="上一局">
            &lt; 上一局
          </button>
          <button type="button" onClick={onNextKyoku} disabled={nextKyokuDisabled} style={sideButtonStyle(nextKyokuDisabled)} title="下一局">
            下一局 &gt;
          </button>
          <button type="button" onClick={onPrevStep} disabled={prevStepDisabled} style={sideButtonStyle(prevStepDisabled)} title="上一步">
            &lt; 上一步
          </button>
          <button type="button" onClick={onNextStep} disabled={nextStepDisabled} style={sideButtonStyle(nextStepDisabled)} title="下一步">
            下一步 &gt;
          </button>
          <button type="button" onClick={onPrevOwnDiscard} style={sideButtonStyle(false)} title="上一自家打牌">
            &lt; 上一自家
          </button>
          <button type="button" onClick={onNextOwnDiscard} style={sideButtonStyle(false)} title="下一自家打牌">
            下一自家 &gt;
          </button>
          <button type="button" onClick={onPrevDiff} style={sideButtonStyle(false)} title="上一处与 Bot 不同的决策">
            &lt; 上一差异
          </button>
          <button type="button" onClick={onNextDiff} style={sideButtonStyle(false)} title="下一处与 Bot 不同的决策">
            下一差异 &gt;
          </button>
        </div>
      </div>
    </div>
  );
}

const controlsStyle: CSSProperties = {
  padding: 10,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
};

const utilityGridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: 6,
};

const sectionTitleStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  color: 'var(--text-muted)',
  marginBottom: 6,
};

const metaStyle: CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  gap: 8,
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 700,
  marginBottom: 6,
};

const sliderStyle: CSSProperties = {
  width: '100%',
  accentColor: 'var(--accent)',
};

const buttonGridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: '1fr 1fr',
  gap: 6,
};

const utilityButtonStyle: CSSProperties = {
  minHeight: 28,
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 3,
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
};

function sideButtonStyle(disabled: boolean): CSSProperties {
  return {
    minHeight: 34,
    border: '1px solid var(--border)',
    background: disabled ? 'var(--card-bg)' : 'var(--page-bg)',
    color: disabled ? 'var(--text-muted)' : 'var(--text-primary)',
    borderRadius: 3,
    fontSize: 13,
    fontWeight: 800,
    cursor: disabled ? 'default' : 'pointer',
  };
}

function kyokuItemStyle(active: boolean): CSSProperties {
  return {
    minHeight: 26,
    padding: '3px 8px',
    border: `1px solid ${active ? 'var(--accent)' : 'transparent'}`,
    borderRadius: 4,
    background: active ? 'rgba(52, 152, 219, 0.12)' : 'transparent',
    color: active ? 'var(--accent)' : 'var(--text-primary)',
    fontSize: 12,
    fontWeight: active ? 800 : 600,
    textAlign: 'left',
    cursor: 'pointer',
  };
}
