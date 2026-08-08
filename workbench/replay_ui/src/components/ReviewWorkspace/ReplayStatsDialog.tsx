// src/replay_ui/src/components/ReviewWorkspace/ReplayStatsDialog.tsx
//
// 决策统计 dialog。从 ReplayViewPage 提取的共享组件，
// 供 GameBoardReplayPage（Review Workspace）与 ReplayViewPage（legacy 决策列表）共用。
// 统计计算公式、teacher 聚合、一致率/类似度/恶手率/Rating 算法与原实现保持一致。
import { useEffect } from 'react';
import type { ReplayData } from '../../types/replay';
import { isReplayPlayerDecision, sameReplayAction } from '../../utils/tileUtils';

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function ReplayStatsDialog({ data, onClose }: { data: ReplayData; onClose: () => void }) {
  // Escape 关闭（页面的快捷键处理在 dialog 打开期间已被禁用）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const log = data.log.filter(
    e => isReplayPlayerDecision(e, data.player_id) && !e.comparison_exempt,
  );
  const fallbackTotal = log.length;
  const fallbackMatch = log.filter((e) => sameReplayAction(e.chosen, e.gt_action)).length;
  const teacherStats = (() => {
    const stats = new Map<string, {
      model: string;
      total: number;
      match: number;
      badMove: number;
      ratingScores: number[];
      similarityScores: number[];
    }>();

    const ensure = (model: string) => {
      const key = model || 'model';
      let item = stats.get(key);
      if (!item) {
        item = { model: key, total: 0, match: 0, badMove: 0, ratingScores: [], similarityScores: [] };
        stats.set(key, item);
      }
      return item;
    };

    for (const entry of log) {
      const reviews = entry.teacher_reviews && entry.teacher_reviews.length > 0
        ? entry.teacher_reviews
        : entry.teacher_review ? [entry.teacher_review] : [];
      for (const review of reviews) {
        const model = review.model || 'model';
        const item = ensure(model);
        const actual = review.actual_action ?? entry.gt_action;
        const expected = review.expected_action ?? review.top1?.action ?? null;
        const isImplicitPass = entry.gt_action == null && actual?.type === 'none';
        if (actual && expected && !isImplicitPass) {
          item.total += 1;
          if (sameReplayAction(expected, actual)) item.match += 1;
        }

        if (!actual || isImplicitPass) continue;
        const qValues: number[] = [];
        let actualQ = finiteNumber(review.actual_q);
        let actualProb = finiteNumber(review.actual_prob);
        let expectedProb = finiteNumber(review.expected_prob ?? review.top1?.prob ?? review.best_prob);
        for (const candidate of entry.candidates ?? []) {
          const teachers = candidate.teachers ?? (candidate.teacher ? [candidate.teacher] : []);
          const teacher = teachers.find((value) => value.model === model);
          const q = finiteNumber(teacher?.q_value);
          const prob = finiteNumber(teacher?.prob);
          if (q !== null) {
            qValues.push(q);
            if (actualQ === null && sameReplayAction(candidate.action, actual)) {
              actualQ = q;
            }
          }
          if (prob !== null) {
            if (actualProb === null && sameReplayAction(candidate.action, actual)) {
              actualProb = prob;
            }
            if (expected && expectedProb === null && sameReplayAction(candidate.action, expected)) {
              expectedProb = prob;
            }
          }
        }
        if (expected && actualProb !== null && expectedProb !== null) {
          const penalty = sameReplayAction(expected, actual) ? 0 : Math.abs(actualProb - expectedProb);
          item.similarityScores.push(Math.max(0, Math.min(1, 1 - penalty)));
          if (!sameReplayAction(expected, actual) && actualProb < 0.05) {
            item.badMove += 1;
          }
        }
        if (actualQ === null || qValues.length < 2) continue;
        if (!qValues.some((value) => value === actualQ)) qValues.push(actualQ);
        const minQ = Math.min(...qValues);
        const maxQ = Math.max(...qValues);
        const range = maxQ - minQ;
        if (range <= 0) continue;
        item.ratingScores.push((actualQ - minQ) / range);
      }
    }

    const modelOrder: Record<string, number> = {
      ext_mortal: 0,
      '70k': 1,
      'V2 candidate': 2,
    };
    return Array.from(stats.values()).map((item) => {
      const pct = item.total ? item.match / item.total * 100 : 0;
      const rating = item.ratingScores.length
        ? Math.round(1000 * 100 * Math.pow(item.ratingScores.reduce((sum, value) => sum + value, 0) / item.ratingScores.length, 2)) / 1000
        : null;
      const similarity = item.similarityScores.length
        ? item.similarityScores.reduce((sum, value) => sum + value, 0) / item.similarityScores.length * 100
        : null;
      return {
        ...item,
        pct,
        rating: rating === null ? null : Math.round(rating * 10) / 10,
        similarity: similarity === null ? null : Math.round(similarity * 10) / 10,
        badMoveRate: item.total ? item.badMove / item.total * 100 : 0,
      };
    }).sort((left, right) => (modelOrder[left.model] ?? 100) - (modelOrder[right.model] ?? 100));
  })();

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
                  pct: fallbackPct,
                  similarity: null,
                  rating: data.rating,
                  badMove: 0,
                  badMoveRate: null,
                  ratingScores: [],
                  similarityScores: [],
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
