// src/replay_ui/src/components/DecisionPanel/BotDecisionCard.tsx
import { motion } from 'framer-motion';
import { LogitBar } from './LogitBar';
import { actionLabel } from '../../utils/tileUtils';
import type { StepEntry, BotDecision, Action } from '../../types/replay';

interface BotDecisionCardProps {
  entry: StepEntry | null;
  playerId: number;
}

export function BotDecisionCard({ entry, playerId }: BotDecisionCardProps) {
  if (!entry) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-4 text-center text-gray-400">
        选择一个回放文件开始分析
      </div>
    );
  }

  const { bakaze, kyoku, honba, gt_action } = entry;
  const botDecision: BotDecision | undefined = entry.bot_decisions?.[playerId];
  const chosen = botDecision?.action ?? null;
  const candidates: { action: Action; logit: number }[] = [];
  const isCorrect = botDecision?.is_correct ?? null;

  return (
    <motion.div
      key={entry.step}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
      className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden"
    >
      {/* 头部信息 */}
      <div className="px-4 py-2 bg-gray-50 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="font-bold text-sm text-gray-800">
            Step {entry.step + 1} · {bakaze}{kyoku}局 {honba}本场
          </span>
          <span className="text-xs text-gray-500">
            行动者: P{entry.actor_to_move}
          </span>
        </div>
        {isCorrect !== null && (
          <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
            isCorrect
              ? 'bg-purple-100 text-purple-700'
              : 'bg-red-100 text-red-700'
          }`}>
            {isCorrect ? 'Bot 选对 ✓' : 'Bot 选错 ✗'}
          </span>
        )}
      </div>

      {/* Bot 决策 vs 玩家实际 */}
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="grid grid-cols-2 gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-red-600 bg-red-50 px-2 py-0.5 rounded">Bot</span>
            <span className="text-sm font-medium text-gray-800">
              {chosen ? actionLabel(chosen) : '—'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-green-600 bg-green-50 px-2 py-0.5 rounded">玩家</span>
            <span className={`text-sm font-medium ${
              isCorrect ? 'text-gray-800' : 'text-red-700'
            }`}>
              {gt_action ? actionLabel(gt_action) : '—'}
            </span>
          </div>
        </div>
      </div>

      {/* Logit 排名 */}
      <div className="px-4 py-3">
        <div className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">
          Bot Logit 排名
        </div>
        <LogitBar
          candidates={candidates}
          chosen={chosen}
          gtAction={gt_action}
          maxDisplay={8}
        />
      </div>

      {/* 鸣牌动作标注 */}
      {(chosen?.type === 'chi' || chosen?.type === 'pon' || chosen?.type === 'daiminkan') && chosen.consumed && (
        <div className="px-4 py-2 bg-blue-50 text-xs text-blue-700 border-t border-blue-100">
          鸣牌消耗: {chosen.consumed.join(', ')}
        </div>
      )}

      {/* 立直宣言 */}
      {chosen?.type === 'reach' && (
        <div className="px-4 py-2 bg-amber-50 text-xs text-amber-700 border-t border-amber-100">
          宣言立直，等待打出一张宣言牌
        </div>
      )}
    </motion.div>
  );
}
