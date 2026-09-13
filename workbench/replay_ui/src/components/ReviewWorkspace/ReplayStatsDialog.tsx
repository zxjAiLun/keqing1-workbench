// src/replay_ui/src/components/ReviewWorkspace/ReplayStatsDialog.tsx
//
// 决策统计 dialog。从 ReplayViewPage 提取的共享组件，
// 供 GameBoardReplayPage（Review Workspace）与 ReplayViewPage（legacy 决策列表）共用。
//
// 口径（与「上一差异 / 下一差异」完全一致）：
//   * 可比决策 = isReplayReviewComparableEntry（剔除 obs / comparison_exempt / 立直后强制摸切）；
//   * 每个模型单独统计，类似度/一致率/恶手率/Rating 均按 review.model 分组；
//   * 聚合算法在 utils/reviewStats.ts（纯函数，行为可被 check 脚本锁定）。
import { useEffect } from 'react';
import type { ReplayData } from '../../types/replay';
import { sameReplayAction } from '../../utils/tileUtils';
import { isReplayReviewComparableEntry } from '../../utils/reviewComparable';
import { computeReviewModelStats } from '../../utils/reviewStats';

export function ReplayStatsDialog({ data, onClose }: { data: ReplayData; onClose: () => void }) {
  // Escape 关闭（页面的快捷键处理在 dialog 打开期间已被禁用）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const log = data.log.filter(e => isReplayReviewComparableEntry(e, data.player_id));
  const fallbackTotal = log.length;
  const fallbackMatch = log.filter((e) => sameReplayAction(e.chosen, e.gt_action)).length;
  const teacherStats = computeReviewModelStats(log, data.player_id);
  const hasTeacherStats = teacherStats.length > 0;
  const fallbackPct = fallbackTotal ? fallbackMatch / fallbackTotal * 100 : 0;

  return (
    <>
      <div className="stats-overlay open" onClick={onClose} aria-hidden="true" />
      <div
        className="stats-panel open"
        role="dialog"
        aria-modal="true"
        aria-labelledby="replay-stats-dialog-title"
      >
        <div className="stats-header">
          <span id="replay-stats-dialog-title">决策统计</span>
          <button className="stats-close" onClick={onClose} aria-label="关闭统计">×</button>
        </div>
        <div className="stats-body">
          <div className="stats-summary">
            {[
              { val: hasTeacherStats ? teacherStats.length : 1, lbl: 'Review 模型数' },
              { val: fallbackTotal, lbl: '总决策数' },
              { val: data.kyoku_order?.length || 0, lbl: '总局数' },
            ].map(item => (
              <div key={item.lbl} className="stats-card">
                <div className="val">{item.val}</div>
                <div className="lbl">{item.lbl}</div>
              </div>
            ))}
          </div>

          <div className="stats-section-title">模型 Review 统计</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 13,
            }}>
              <thead>
                <tr style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>
                  <th style={statsThStyle}>模型</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>类似度</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>一致率</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>恶手率</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>Rating</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>Match</th>
                  <th style={{ ...statsThStyle, textAlign: 'right' }}>Total</th>
                </tr>
              </thead>
              <tbody>
                {(hasTeacherStats ? teacherStats : [{
                  model: data.model_label || data.bot_type || 'Bot',
                  total: fallbackTotal,
                  match: fallbackMatch,
                  badMove: 0,
                  probScored: 0,
                  pct: fallbackPct,
                  similarity: null,
                  rating: data.rating,
                  badMoveRate: null,
                }]).map((item) => {
                  const pct = item.total ? item.match / item.total * 100 : 0;
                  return (
                    <tr key={item.model} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={statsTdStyle} title={item.model}>{item.model}</td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>
                        {item.similarity === null || item.similarity === undefined ? '—' : `${item.similarity.toFixed(1)}%`}
                      </td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>{pct.toFixed(1)}%</td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>
                        {item.badMoveRate === null || item.badMoveRate === undefined ? '—' : `${item.badMoveRate.toFixed(1)}%`}
                      </td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>
                        {item.rating === null || item.rating === undefined ? '—' : item.rating.toFixed(1)}
                      </td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>{item.match}</td>
                      <td style={{ ...statsTdStyle, textAlign: 'right', fontFamily: 'Menlo, Consolas, monospace' }}>{item.total}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

const statsThStyle: React.CSSProperties = {
  padding: '7px 8px',
  fontSize: 11,
  fontWeight: 800,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};

const statsTdStyle: React.CSSProperties = {
  padding: '8px',
  color: 'var(--text-primary)',
  fontWeight: 700,
};
