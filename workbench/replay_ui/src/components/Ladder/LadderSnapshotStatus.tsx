// src/replay_ui/src/components/Ladder/LadderSnapshotStatus.tsx
// 三页（天梯/账号/模型）统一展示的当前只读快照状态 footer。
//
// 展示：快照 <snapshot_id> · 更新 <本地时间> · 已计入 <games> 场 · 可见时每 30s 自动刷新
// 状态表现：
// - 首次加载：不显示（season 数据尚未就绪）；
// - 静默轮询中：显示"正在检查更新…"；
// - 成功：恢复普通文字；
// - 轮询失败：继续显示上一份有效 snapshot，不弹整页错误。
import type { CSSProperties } from 'react';
import type { LadderSeason } from '../../types/ladder';

interface LadderSnapshotStatusProps {
  season: LadderSeason | undefined;
  refreshing: boolean;
}

function fmtUpdatedAt(epochSecs: number | undefined): string {
  if (!epochSecs) return '—';
  return new Date(epochSecs * 1000).toLocaleString();
}

export function LadderSnapshotStatus({ season, refreshing }: LadderSnapshotStatusProps) {
  if (!season) return null;
  return (
    <div style={containerStyle} aria-live="polite">
      {refreshing ? '正在检查更新…' : (
        <>
          快照 {season.snapshot_id || '—'} · 更新 {fmtUpdatedAt(season.updated_at)} · 已计入 {season.games ?? '—'} 场
          <span style={{ marginLeft: 8 }}>（页面可见时每 30 秒自动刷新）</span>
        </>
      )}
    </div>
  );
}

const containerStyle: CSSProperties = {
  marginTop: 6,
  fontSize: 11,
  color: 'var(--text-muted)',
};
