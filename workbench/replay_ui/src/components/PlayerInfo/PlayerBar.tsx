// src/replay_ui/src/components/PlayerInfo/PlayerBar.tsx
import type { StepEntry } from '../../types/replay';

interface PlayerBarProps {
  entry: StepEntry | null;
  playerId: number;
}

const SEAT_CN = ['自家', '下家', '对面', '上家'];
const JIKAZE_CN = ['東', '南', '西', '北'];
const PLAYER_COLORS = ['#e74c3c', '#3498db', '#9b59b6', '#27ae60'];

export function PlayerBar({ entry, playerId }: PlayerBarProps) {
  if (!entry) {
    return <div className="h-16" />;
  }

  const { scores, reached } = entry;
  const maxScore = Math.max(...scores);
  const minScore = Math.min(...scores);
  const range = maxScore - minScore || 1;

  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      {scores.map((score, i) => {
        const relativeScore = (score - minScore) / range; // 0~1
        const color = PLAYER_COLORS[i];
        const name = SEAT_CN[i];
        const jikaze = JIKAZE_CN[(i - playerId + 4) % 4];
        const isReached = reached[i];
        const isSelf = i === playerId;

        return (
          <div
            key={i}
            className={`flex-shrink-0 rounded-lg px-3 py-2 min-w-[110px] ${
              isSelf ? 'ring-2 ring-emerald-400' : ''
            }`}
            style={{ backgroundColor: `${color}18`, borderLeft: `3px solid ${color}` }}
          >
            {/* 玩家名称 + 自风 */}
            <div className="flex items-center justify-between mb-1">
              <span className={`text-xs font-bold`} style={{ color }}>
                {name}
              </span>
              <span className="text-xs text-gray-400">[{jikaze}]</span>
            </div>

            {/* 分数 */}
            <div className="text-sm font-bold text-gray-800 mb-1" style={{ color: isSelf ? color : undefined }}>
              {score.toLocaleString()}
            </div>

            {/* 分数条 */}
            <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden mb-1">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{ width: `${relativeScore * 100}%`, backgroundColor: color }}
              />
            </div>

            {/* 状态标签 */}
            <div className="flex gap-1 flex-wrap">
              {isReached && (
                <span className="text-xs px-1 py-0.5 bg-black text-white rounded">立直</span>
              )}
              {isSelf && (
                <span className="text-xs px-1 py-0.5 bg-emerald-500 text-white rounded">视角</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
