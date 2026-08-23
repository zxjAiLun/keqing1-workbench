// src/replay_ui/src/components/DecisionPanel/LogitBar.tsx
import { actionLabel } from '../../utils/tileUtils';
import type { Action } from '../../types/replay';
import { decisionColors, decisionBg, chartColors } from '../BattleBoard/tableStyles';

interface Candidate {
  action: Action;
  logit: number;
}

interface LogitBarProps {
  candidates: Candidate[];
  chosen: Action | null;
  gtAction: Action | null;
  maxDisplay?: number;
}

export function LogitBar({ candidates, chosen, gtAction, maxDisplay = 8 }: LogitBarProps) {
  if (!candidates || candidates.length === 0) {
    return (
      <div className="text-xs py-2" style={{ color: 'var(--text-muted)' }}>无可用候选动作</div>
    );
  }

  const maxLogit = candidates[0]?.logit ?? 0;
  const minLogit = candidates[candidates.length - 1]?.logit ?? 0;
  const range = Math.max(maxLogit - minLogit, 0.001);

  const displayCandidates = candidates.slice(0, maxDisplay);

  return (
    <div className="flex flex-col gap-1">
      {displayCandidates.map((c, idx) => {
        const { action, logit } = c;
        const isChosen = chosen && chosen.type === action.type &&
          chosen.pai === action.pai &&
          chosen.actor === action.actor;
        const isGt = gtAction && gtAction.type === action.type &&
          gtAction.pai === action.pai &&
          gtAction.actor === action.actor;

        const pct = Math.max(((logit - minLogit) / range) * 100, 2);
        const isCorrect = isChosen && isGt;
        const isBotWrong = isChosen && !isGt && gtAction;
        const isPlayerWrong = isGt && !isChosen;

        let barColor: string = chartColors.neutral;
        let badge = '';
        let rowBg = '';
        let labelColor = 'var(--text-primary)';

        if (isCorrect) {
          barColor = decisionColors.both;
          badge = '✓★';
          rowBg = decisionBg.both;
          labelColor = decisionColors.both;
        } else if (isBotWrong) {
          barColor = decisionColors.bot;
          badge = '✓Bot';
          rowBg = decisionBg.bot;
          labelColor = decisionColors.bot;
        } else if (isPlayerWrong) {
          barColor = decisionColors.gt;
          badge = '★玩家';
          rowBg = decisionBg.gt;
          labelColor = decisionColors.gt;
        } else if (isChosen) {
          barColor = decisionColors.bot;
          badge = '✓Bot';
          rowBg = decisionBg.bot;
          labelColor = decisionColors.bot;
        } else if (isGt) {
          barColor = decisionColors.gt;
          badge = '★玩家';
          rowBg = decisionBg.gt;
          labelColor = decisionColors.gt;
        }

        return (
          <div
            key={`${action.type}-${action.pai}-${action.actor}-${idx}`}
            className="flex items-center gap-2 px-2 py-1 rounded text-xs"
            style={{ background: rowBg || undefined }}
          >
            {/* 排名 */}
            <span className="w-4 text-right flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{idx + 1}</span>

            {/* 动作标签 */}
            <span className="flex-shrink-0 min-w-[90px] font-medium" style={{ color: labelColor }}>
              {actionLabel(action)}
            </span>

            {/* Logit 柱状条 */}
            <div className="flex-1 relative h-4 rounded overflow-hidden" style={{ background: chartColors.track }}>
              <div
                className="absolute left-0 top-0 h-full rounded transition-all duration-200"
                style={{ width: `${pct}%`, backgroundColor: barColor }}
              />
            </div>

            {/* Logit 数值 */}
            <span className="w-16 text-right font-mono flex-shrink-0" style={{ color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>
              {logit >= 0 ? '+' : ''}{logit.toFixed(2)}
            </span>

            {/* 标记 */}
            {badge && (
              <span className="text-xs font-bold w-10 flex-shrink-0" style={{ color: labelColor }}>
                {badge}
              </span>
            )}
          </div>
        );
      })}

      {candidates.length > maxDisplay && (
        <div className="text-xs text-center py-1" style={{ color: 'var(--text-muted)' }}>
          还有 {candidates.length - maxDisplay} 个候选动作...
        </div>
      )}
    </div>
  );
}
