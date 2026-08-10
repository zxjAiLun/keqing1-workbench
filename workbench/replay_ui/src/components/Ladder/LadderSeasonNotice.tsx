// src/replay_ui/src/components/Ladder/LadderSeasonNotice.tsx
// 赛季可用状态展示：无默认多赛季、默认未发布、数据损坏、身份不匹配。
//
// UI 原则：
// - readiness.message 作为主文案；detail 放小号运维文本（可折叠）；
// - 未就绪不显示红色"应用崩溃"式 alert（那是 404/500 等真正请求错误才用）；
// - 一旦实体 payload 成功，主体优先显示，本 notice 不再遮挡。
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import type { LadderSeason } from '../../types/ladder';

interface LadderSeasonNoticeProps {
  seasons: LadderSeason[];
  /** 当前生效赛季（未就绪时可能无 data） */
  activeSeason: LadderSeason | undefined;
  defaultSeasonId: string | null;
}

export function LadderSeasonNotice({ seasons, activeSeason, defaultSeasonId }: LadderSeasonNoticeProps) {
  const readiness = activeSeason?.readiness;

  // R11-F：没有显式 default 就是"暂无当前正式赛季"——绝不自动猜第一个 running，
  // 明确引导去赛季管理。
  if (!defaultSeasonId) {
    return (
      <div className="card" style={cardStyle}>
        <div style={titleStyle}>暂无当前正式赛季</div>
        <div style={bodyStyle}>
          {seasons.length > 0
            ? '请在赛季管理中对 running 赛季执行「设为当前」。'
            : '请先在赛季管理中创建并开始一个正式赛季。'}
          {' '}
          <Link to="/seasons" style={linkStyle}>前往赛季管理 →</Link>
        </div>
      </div>
    );
  }

  // 没有任何赛季：保持"暂无赛季"语义
  if (seasons.length === 0) {
    return (
      <div className="card" style={cardStyle}>
        <div style={titleStyle}>暂无已注册赛季</div>
        <div style={bodyStyle}>
          请在赛季管理中创建赛季并设置参赛阵容。
          {' '}
          <Link to="/seasons" style={linkStyle}>前往赛季管理 →</Link>
        </div>
      </div>
    );
  }

  // 当前赛季未就绪：结构化原因展示
  if (readiness && readiness.state !== 'ready') {
    return (
      <div className="card" style={cardStyle}>
        <div style={titleStyle}>{noticeTitle(readiness.state, activeSeason)}</div>
        <div style={bodyStyle}>{readiness.message || readiness.code}</div>
        {readiness.detail && <div style={detailStyle}>{readiness.detail}</div>}
        {readiness.retryable && <div style={retryStyle}>页面会自动重试。</div>}
      </div>
    );
  }

  return null;
}

function noticeTitle(state: string, season: LadderSeason | undefined): string {
  const seasonId = season?.season_id || '';
  switch (state) {
    case 'not_published':
      return season ? `赛季数据正在等待首次发布（${seasonId}）` : '赛季数据尚未发布';
    case 'registry_mismatch':
      return '赛季身份契约不一致';
    case 'invalid':
      return '赛季快照不可用';
    default:
      return '赛季数据异常';
  }
}

const cardStyle: CSSProperties = {
  padding: 16,
  color: 'var(--text-secondary)',
  fontSize: 13,
  marginBottom: 12,
};

const linkStyle: CSSProperties = {
  color: 'var(--accent)',
  fontWeight: 700,
  fontSize: 12,
};

const titleStyle: CSSProperties = {
  fontSize: 14,
  fontWeight: 800,
  color: 'var(--text-primary)',
  marginBottom: 6,
};

const bodyStyle: CSSProperties = {
  color: 'var(--text-secondary)',
};

const detailStyle: CSSProperties = {
  marginTop: 6,
  fontSize: 11,
  color: 'var(--text-muted)',
  fontFamily: 'Menlo, Consolas, monospace',
  wordBreak: 'break-all',
};

const retryStyle: CSSProperties = {
  marginTop: 6,
  fontSize: 11,
  color: 'var(--text-muted)',
};
