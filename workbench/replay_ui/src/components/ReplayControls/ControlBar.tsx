// src/replay_ui/src/components/ReplayControls/ControlBar.tsx
import { Play, Pause, SkipBack, SkipForward, ChevronsLeft, ChevronsRight } from 'lucide-react';
import type { PlaybackSpeed } from '../../hooks/useReplayPlayer';

interface ControlBarProps {
  currentStep: number;
  totalSteps: number;
  currentKyoku: number;
  totalKyoku: number;
  isPlaying: boolean;
  speed: PlaybackSpeed;
  onTogglePlay: () => void;
  onStepForward: () => void;
  onStepBackward: () => void;
  onGoToStart: () => void;
  onGoToEnd: () => void;
  onKyokuForward: () => void;
  onKyokuBackward: () => void;
  onSpeedChange: (speed: PlaybackSpeed) => void;
  onStepClick: (step: number) => void;
  kyokuLabel: string;
}

const SPEED_OPTIONS: PlaybackSpeed[] = [0.5, 1, 2, 4];

export function ControlBar({
  currentStep,
  totalSteps,
  currentKyoku,
  totalKyoku,
  isPlaying,
  speed,
  onTogglePlay,
  onStepForward,
  onStepBackward,
  onGoToStart,
  onGoToEnd,
  onKyokuForward,
  onKyokuBackward,
  onSpeedChange,
  onStepClick,
  kyokuLabel,
}: ControlBarProps) {
  const progress = totalSteps > 0 ? (currentStep / (totalSteps - 1)) * 100 : 0;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const step = parseInt(e.target.value, 10);
    onStepClick(step);
  };

  return (
    <div className="control-bar bg-white/90 backdrop-blur-sm border-b border-gray-200 px-4 py-2 flex flex-wrap items-center gap-2">
      {/* 播放控制 */}
      <div className="flex items-center gap-1">
        <button
          onClick={onGoToStart}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition-colors"
          title="回到开始 (Home)"
        >
          <ChevronsLeft size={18} />
        </button>
        <button
          onClick={onStepBackward}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition-colors"
          title="上一步 (↑/k)"
        >
          <SkipBack size={18} />
        </button>
        <button
          onClick={onTogglePlay}
          className={`p-2 rounded-full transition-colors ${
            isPlaying
              ? 'bg-amber-500 hover:bg-amber-600 text-white'
              : 'bg-emerald-500 hover:bg-emerald-600 text-white'
          }`}
          title="播放/暂停 (Space)"
        >
          {isPlaying ? <Pause size={20} /> : <Play size={20} />}
        </button>
        <button
          onClick={onStepForward}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition-colors"
          title="下一步 (↓/j)"
        >
          <SkipForward size={18} />
        </button>
        <button
          onClick={onGoToEnd}
          className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition-colors"
          title="跳到结尾 (End)"
        >
          <ChevronsRight size={18} />
        </button>
      </div>

      {/* 小局导航 */}
      <div className="flex items-center gap-1 border-l border-gray-200 pl-2">
        <button
          onClick={onKyokuBackward}
          disabled={currentKyoku === 0}
          className="px-2 py-1 text-xs rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          ◀ 上一局
        </button>
        <span className="text-xs font-medium text-gray-700 min-w-[80px] text-center">
          {kyokuLabel} ({currentKyoku + 1}/{totalKyoku})
        </span>
        <button
          onClick={onKyokuForward}
          disabled={currentKyoku >= totalKyoku - 1}
          className="px-2 py-1 text-xs rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          下一局 ▶
        </button>
      </div>

      {/* 进度条 */}
      <div className="flex-1 min-w-[120px] flex items-center gap-2">
        <span className="text-xs text-gray-500 font-mono whitespace-nowrap">
          {currentStep + 1}/{totalSteps}
        </span>
        <div className="relative flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="absolute left-0 top-0 h-full bg-emerald-500 rounded-full transition-all duration-75"
            style={{ width: `${progress}%` }}
          />
          <input
            type="range"
            min={0}
            max={totalSteps - 1}
            value={currentStep}
            onChange={handleSliderChange}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
          />
        </div>
      </div>

      {/* 速度选择 */}
      <div className="flex items-center gap-1 border-l border-gray-200 pl-2">
        <span className="text-xs text-gray-500">速度</span>
        <div className="flex rounded overflow-hidden border border-gray-200">
          {SPEED_OPTIONS.map(s => (
            <button
              key={s}
              onClick={() => onSpeedChange(s)}
              className={`px-2 py-0.5 text-xs transition-colors ${
                speed === s
                  ? 'bg-emerald-500 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
