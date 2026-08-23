import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { replayApi } from '../api/replayApi';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { reviewWorkspaceUrl, routes } from '../routes';
import type { ReviewHistoryItem } from '../types/replay';

export function ReviewHistoryPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<ReviewHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await replayApi.listReviewHistory());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <PageShell width={1180}>
      <PageHeader
        title="Review Library"
        actions={(
          <>
            <button type="button" onClick={() => navigate(routes.reviewNew)} className="btn-secondary" style={actionButtonStyle}>
              新建 Review
            </button>
            <button type="button" onClick={() => void load()} className="btn-secondary" style={actionButtonStyle}>
              <RefreshCw size={14} />
              刷新
            </button>
          </>
        )}
      />

      <div style={tableStyle}>
        <div style={headerStyle}>
          <span>时间</span>
          <span>视角</span>
          <span>模型</span>
          <span>牌局</span>
          <span>打开</span>
        </div>
        {items.map((item) => (
          <div key={`${item.replay_id}-${item.player_id}`} style={rowStyle}>
            <span style={monoStyle}>{item.created_at}</span>
            <span style={{ fontWeight: 700 }}>{item.player_name || `P${item.player_id}`}</span>
            <span style={modelListStyle}>
              {item.models.map((model) => <span key={model} style={modelTagStyle}>{model}</span>)}
            </span>
            <span>{item.kyoku_count}局 / {item.total_steps}步</span>
            <span style={actionGroupStyle}>
              <button
                type="button"
                onClick={() => navigate(reviewWorkspaceUrl(item.replay_id, {
                  playerId: item.player_id,
                  teacherReports: item.teacher_report_paths,
                }))}
                style={openButtonStyle}
              >
                打开
              </button>
            </span>
          </div>
        ))}
        {!loading && items.length === 0 && !error && <div style={statusStyle}>暂无历史 Review</div>}
        {loading && <div style={statusStyle}>加载中...</div>}
        {error && <div style={{ ...statusStyle, color: 'var(--error)' }}>{error}</div>}
      </div>
    </PageShell>
  );
}

const tableStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 7,
  overflowX: 'auto',
  background: 'var(--card-bg)',
};

const gridColumns = '150px minmax(100px, 1fr) minmax(210px, 1.5fr) 110px minmax(190px, auto)';
const headerStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: gridColumns,
  gap: 10,
  padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-muted)',
  fontSize: 11,
  fontWeight: 700,
  minWidth: 720,
};
const rowStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: gridColumns,
  gap: 10,
  alignItems: 'center',
  minHeight: 44,
  padding: '6px 10px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-primary)',
  fontSize: 12,
  minWidth: 720,
};
const monoStyle: React.CSSProperties = { fontFamily: 'Menlo, Consolas, monospace', fontSize: 11 };
const modelListStyle: React.CSSProperties = { display: 'flex', gap: 4, flexWrap: 'wrap' };
const modelTagStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 4,
  padding: '2px 5px',
  color: 'var(--text-secondary)',
  fontSize: 10,
  fontWeight: 700,
};
const openButtonStyle: React.CSSProperties = {
  height: 28,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 5,
  border: '1px solid var(--accent-border)',
  borderRadius: 5,
  background: 'var(--accent-bg)',
  color: 'var(--accent)',
  fontWeight: 800,
  cursor: 'pointer',
};
const actionGroupStyle: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' };
const actionButtonStyle: React.CSSProperties = { height: 32, display: 'inline-flex', alignItems: 'center', gap: 6 };
const statusStyle: React.CSSProperties = { padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 };
