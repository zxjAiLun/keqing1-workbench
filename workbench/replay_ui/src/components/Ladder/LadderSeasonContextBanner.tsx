// src/replay_ui/src/components/Ladder/LadderSeasonContextBanner.tsx
// R11-F Repair：统一的"赛季当前性"上下文提示，Ladder / Account / Model 三页共用。
//
// 规则（status 与 current 是两个维度）：
// - defaultSeasonId == null
//     → "当前没有正式赛季"（正在查看的赛季明确标注"不是当前正式榜"）+ [前往赛季管理]
// - defaultSeasonId != null 且 activeSeasonId != defaultSeasonId
//     → completed/archived：历史赛季（仅供回顾，不是当前正式榜）
//     → running/draft：非当前赛季——当前正式赛季是 <default>
// - 正在查看 default 赛季：不显示 warning。
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';

interface LadderSeasonContextBannerProps {
  /** 当前正在查看的赛季（可能有 status/title） */
  activeSeasonId: string | null;
  activeSeason?: { season_id: string; title?: string; status?: string } | null;
  defaultSeasonId: string | null;
  defaultSeason?: { season_id: string; title?: string } | null;
}

export function LadderSeasonContextBanner({
  activeSeasonId,
  activeSeason,
  defaultSeasonId,
  defaultSeason,
}: LadderSeasonContextBannerProps) {
  const activeTitle = activeSeason?.title || activeSeasonId || '';
  const isHistorical =
    activeSeason?.status === 'completed' || activeSeason?.status === 'archived';

  // 1. 没有默认赛季：明确"没有当前正式赛季"
  if (!defaultSeasonId) {
    return (
      <div style={bannerStyle('#e67e22')}>
        <b>当前没有正式赛季</b>
        {activeSeasonId ? `——正在查看 ${activeTitle}，它不是当前正式榜。` : '。'}
        {' '}
        <Link to="/seasons" style={linkStyle}>前往赛季管理 →</Link>
      </div>
    );
  }

  // 2. 正在查看的赛季不是 default
  if (activeSeasonId && activeSeasonId !== defaultSeasonId) {
    if (isHistorical) {
      return (
        <div style={bannerStyle('#95a5a6')}>
          ⏳ <b>历史赛季</b>（{activeSeason?.status === 'completed' ? '已结束' : '已归档'}）——仅供回顾，不是当前正式榜。
        </div>
      );
    }
    const defaultTitle = defaultSeason?.title || defaultSeasonId;
    return (
      <div style={bannerStyle('#95a5a6')}>
        <b>非当前赛季</b>——正在查看 {activeTitle}；当前正式赛季是 <b>{defaultTitle}</b>。
      </div>
    );
  }

  // 3. 正在查看 default 赛季：当前正式榜，不显示 warning
  return null;
}

const bannerStyle = (color: string): CSSProperties => ({
  fontSize: 12,
  padding: '6px 12px',
  borderRadius: 6,
  marginBottom: 10,
  border: `1px solid ${color}`,
  background: 'rgba(149,165,166,0.08)',
  color: 'var(--text-secondary)',
});

const linkStyle: CSSProperties = {
  color: 'var(--accent)',
  fontWeight: 700,
  fontSize: 12,
};
