import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Bot, History } from 'lucide-react';
import { replayApi } from '../api/replayApi';
import { PageHeader, PageShell, SectionTitle } from '../components/Layout/PageScaffold';
import { reviewWorkspaceUrl, routes } from '../routes';
import type { ReviewHistoryItem } from '../types/replay';

const toolEntries = [
  { label: 'Review Library', path: routes.reviewLibrary, icon: History },
  { label: '天凤呼出', path: routes.tenhou, icon: Bot },
  { label: 'Casebook', path: routes.diagnosticsCasebook, icon: AlertTriangle },
];

function WorkbenchPanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="card" style={{ padding: 12 }}>
      <SectionTitle title={title} />
      {children}
    </section>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const [history, setHistory] = useState<ReviewHistoryItem[]>([]);

  useEffect(() => {
    replayApi.listReviewHistory().then((items) => setHistory(items.slice(0, 6))).catch(() => setHistory([]));
  }, []);

  return (
    <PageShell width={1120}>
      <PageHeader
        title="工作台总览"
        actions={
          <button
            onClick={() => navigate(routes.reviewNew)}
            className="btn-primary"
            style={{ height: 34, padding: '0 16px', fontSize: 13 }}
          >
            新建 Review
          </button>
        }
      />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 12,
          alignItems: 'start',
        }}
      >
        <WorkbenchPanel title="工具入口">
          <div style={{ display: 'grid', gap: 6 }}>
            {toolEntries.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.path}
                  onClick={() => navigate(item.path)}
                  style={{
                    height: 44,
                    border: '1px solid var(--border)',
                    borderRadius: 7,
                    background: 'var(--card-bg)',
                    color: 'var(--text-primary)',
                    display: 'grid',
                    gridTemplateColumns: '24px 1fr auto',
                    alignItems: 'center',
                    gap: 8,
                    padding: '0 10px',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                >
                  <Icon size={16} style={{ color: 'var(--accent)' }} />
                  <span style={{ minWidth: 0, fontSize: 13, fontWeight: 700 }}>{item.label}</span>
                  <span style={{ color: 'var(--text-muted)', fontSize: 16 }}>›</span>
                </button>
              );
            })}
          </div>
        </WorkbenchPanel>

        <WorkbenchPanel title="最近 Review">
            <div style={{ display: 'grid', gap: 5 }}>
              {history.map((item) => (
                <button
                  key={`${item.replay_id}-${item.player_id}`}
                  type="button"
                  onClick={() => navigate(reviewWorkspaceUrl(item.replay_id, {
                    playerId: item.player_id,
                    teacherReports: item.teacher_report_paths,
                  }))}
                  style={{
                    minHeight: 34,
                    border: '1px solid var(--border)',
                    borderRadius: 5,
                    background: 'var(--card-bg)',
                    color: 'var(--text-primary)',
                    display: 'grid',
                    gridTemplateColumns: '132px 1fr auto',
                    gap: 8,
                    alignItems: 'center',
                    padding: '4px 7px',
                    textAlign: 'left',
                    cursor: 'pointer',
                    fontSize: 11,
                  }}
                >
                  <span style={{ fontFamily: 'Menlo, Consolas, monospace' }}>{item.created_at}</span>
                  <span style={{ fontWeight: 700 }}>{item.player_name || `P${item.player_id}`}</span>
                  <span>{item.models.length} 模型</span>
                </button>
              ))}
              {history.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>暂无记录</div>}
            </div>
        </WorkbenchPanel>
      </div>
    </PageShell>
  );
}
