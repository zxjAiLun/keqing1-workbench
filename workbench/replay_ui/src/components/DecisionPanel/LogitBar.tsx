// src/replay_ui/src/components/DecisionPanel/LogitBar.tsx
import { actionLabel } from '../../utils/tileUtils';
import type { Action } from '../../types/replay';

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
      <div className="text-xs text-gray-400 py-2">无可用候选动作</div>
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

        let barColor = '#94a3b8';
        let badge = '';
        let rowBg = '';

        if (isCorrect) {
          barColor = '#8e44ad';
          badge = '✓★';
          rowBg = 'bg-purple-50';
        } else if (isBotWrong) {
          barColor = '#e74c3c';
          badge = '✓Bot';
          rowBg = 'bg-red-50';
        } else if (isPlayerWrong) {
          barColor = '#27ae60';
          badge = '★玩家';
          rowBg = 'bg-green-50';
        } else if (isChosen) {
          barColor = '#e74c3c';
          badge = '✓Bot';
          rowBg = 'bg-red-50';
        } else if (isGt) {
          barColor = '#27ae60';
          badge = '★玩家';
          rowBg = 'bg-green-50';
        }

        return (
          <div
            key={`${action.type}-${action.pai}-${action.actor}-${idx}`}
            className={`flex items-center gap-2 px-2 py-1 rounded text-xs ${rowBg}`}
          >
            {/* 排名 */}
            <span className="w-4 text-gray-400 text-right flex-shrink-0">{idx + 1}</span>

            {/* 动作标签 */}
            <span className={`flex-shrink-0 min-w-[90px] font-medium ${
              isCorrect ? 'text-purple-700' :
              isBotWrong ? 'text-red-700' :
              isPlayerWrong ? 'text-green-700' : 'text-gray-700'
            }`}>
              {actionLabel(action)}
            </span>

            {/* Logit 柱状条 */}
            <div className="flex-1 relative h-4 bg-gray-100 rounded overflow-hidden">
              <div
                className="absolute left-0 top-0 h-full rounded transition-all duration-200"
                style={{ width: `${pct}%`, backgroundColor: barColor }}
              />
            </div>

            {/* Logit 数值 */}
            <span className="w-16 text-right font-mono text-gray-600 flex-shrink-0">
              {logit >= 0 ? '+' : ''}{logit.toFixed(2)}
            </span>

            {/* 标记 */}
            {badge && (
              <span className={`text-xs font-bold w-10 flex-shrink-0 ${
                isCorrect ? 'text-purple-600' :
                isBotWrong ? 'text-red-600' : 'text-green-600'
              }`}>
                {badge}
              </span>
            )}
          </div>
        );
      })}

      {candidates.length > maxDisplay && (
        <div className="text-xs text-gray-400 text-center py-1">
          还有 {candidates.length - maxDisplay} 个候选动作...
        </div>
      )}
    </div>
  );
}
