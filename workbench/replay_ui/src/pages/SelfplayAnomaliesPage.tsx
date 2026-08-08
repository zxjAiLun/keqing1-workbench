import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Loader2, MonitorPlay, ListTree } from 'lucide-react';
import { replayApi } from '../api/replayApi';
import type { SelfplayAnomalyReplayGroup } from '../types/replay';
import { PageHeader, PageShell, SectionTitle } from '../components/Layout/PageScaffold';
import { legacyRoutes, routes } from '../routes';

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export function SelfplayAnomaliesPage() {
  const navigate = useNavigate();
  const [groups, setGroups] = useState<SelfplayAnomalyReplayGroup[]>([]);
  const [selectedRunPath, setSelectedRunPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const runs = useMemo(() => {
    const byRun = new Map<string, {
      output_dir: string;
      output_dir_path: string;
      updated_at: number;
      stats: SelfplayAnomalyReplayGroup['stats'];
      collections: SelfplayAnomalyReplayGroup[];
    }>();

    for (const group of groups) {
      const existing = byRun.get(group.output_dir_path);
      if (existing) {
        existing.updated_at = Math.max(existing.updated_at, group.updated_at);
        existing.collections.push(group);
      } else {
        byRun.set(group.output_dir_path, {
          output_dir: group.output_dir,
          output_dir_path: group.output_dir_path,
          updated_at: group.updated_at,
          stats: group.stats,
          collections: [group],
        });
      }
    }

    return Array.from(byRun.values()).sort((a, b) => b.updated_at - a.updated_at);
  }, [groups]);

  const activeRun = runs.find((run) => run.output_dir_path === selectedRunPath) ?? runs[0] ?? null;

  useEffect(() => {
    replayApi.listSelfplayReplayCollections()
      .then((data) => {
        setGroups(data.groups);
        if (data.groups.length > 0) {
          setSelectedRunPath(data.groups[0].output_dir_path);
        }
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, []);

  if (loading) {
    return (
        <div style={{ minHeight: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
        <Loader2 size={28} className="animate-spin" style={{ color: 'var(--accent)' }} />
        <span style={{ color: 'var(--text-muted)' }}>加载对局回放清单中...</span>
      </div>
    );
  }

  if (error) {
    return (
      <PageShell width={1100}>
        <div className="card">
          <div className="card-title">对局回放</div>
          <div style={{ color: 'var(--error)', fontSize: 14 }}>{error}</div>
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell width={1160}>
      <PageHeader
        eyebrow="Diagnostics"
        title="Casebook"
        description="自动聚合 selfplay 导出的 `replays/manifest.json` 与 `anomaly_replays/manifest.json`，统一跳转决策列表或牌桌回放。"
      />

        {runs.length === 0 && (
          <div className="card">
            <SectionTitle title="暂无对局回放" description="先运行 selfplay，默认会保存 10 局；异常抽样回放也会一起出现在这里。" />
            <div style={{ color: 'var(--text-muted)', fontSize: 14 }}>
              当前还没有可展示的回放导出数据。
            </div>
          </div>
        )}

        {runs.length > 0 && (
          <div className="card" style={{ marginBottom: 16 }}>
            <SectionTitle title="选择测试批次" description="按 benchmark 输出目录切换，不同时间戳的牌谱集不再混在同一个列表里。" />
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {runs.map((run) => {
                const active = activeRun?.output_dir_path === run.output_dir_path;
                return (
                  <button
                    key={run.output_dir_path}
                    onClick={() => setSelectedRunPath(run.output_dir_path)}
                    style={{
                      ...runButtonStyle,
                      borderColor: active ? 'var(--accent)' : 'var(--border)',
                      background: active ? 'rgba(52, 152, 219, 0.10)' : 'var(--card-bg)',
                      color: active ? 'var(--accent)' : 'var(--text-primary)',
                    }}
                  >
                    <div style={{ fontWeight: 700 }}>{run.output_dir}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      更新时间 {fmtTime(run.updated_at)}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {activeRun?.collections.map((group) => (
          <div key={`${group.output_dir_path}-${group.collection_type}`} className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
              <div>
                <div className="card-title" style={{ marginBottom: 4 }}>
                  {group.collection_type === 'anomaly_replays' ? '异常抽样回放' : '普通对局回放'}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  更新时间 {fmtTime(group.updated_at)}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                  manifest: {group.manifest_path}
                </div>
              </div>
              {group.stats && (
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <div style={pillStyle}>games {group.stats.games ?? '-'}</div>
                  <div style={pillStyle}>ok {group.stats.completed_games ?? '-'}</div>
                  <div style={pillStyle}>err {group.stats.error_games ?? '-'}</div>
                  <div style={pillStyle}>
                    sec/game {typeof group.stats.seconds_per_game === 'number' ? group.stats.seconds_per_game.toFixed(2) : '-'}
                  </div>
                </div>
              )}
            </div>

            <div style={{ display: 'grid', gap: 10 }}>
              {group.items.map((item) => (
                <div
                  key={`${group.output_dir}-${item.game_id}`}
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: 12,
                    padding: 14,
                    background: 'var(--card-bg)',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
                    <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                      game_{String(item.game_id).padStart(5, '0')}
                      {typeof item.anomaly_score === 'number' ? ` · score ${item.anomaly_score.toFixed(1)}` : ''}
                      {typeof item.interest === 'number' ? ` · interest ${item.interest.toFixed(1)}` : ''}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                      {item.score_components
                        ? Object.entries(item.score_components).map(([k, v]) => `${k}=${v}`).join(', ')
                        : [
                            Array.isArray(item.scores) ? `scores=${item.scores.join('/')}` : null,
                            Array.isArray(item.ranks) ? `ranks=${item.ranks.join('/')}` : null,
                            typeof item.rounds === 'number' ? `rounds=${item.rounds}` : null,
                            typeof item.turns === 'number' ? `turns=${item.turns}` : null,
                          ].filter(Boolean).join(', ')}
                    </div>
                  </div>

                  {item.replay_ui_error && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--error)', fontSize: 13, marginBottom: 10 }}>
                      <AlertTriangle size={14} />
                      <span>{item.replay_ui_error}</span>
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                    <button
                      onClick={() => item.replay_id && navigate(`${legacyRoutes.decisionList}?id=${item.replay_id}`)}
                      className="btn-primary"
                      disabled={!item.replay_id}
                      style={btnStyle}
                    >
                      <ListTree size={15} />
                      决策列表
                    </button>
                    <button
                      onClick={() => item.replay_id && navigate(routes.reviewWorkspace(item.replay_id))}
                      className="btn-primary"
                      disabled={!item.replay_id}
                      style={{ ...btnStyle, background: 'var(--success)' }}
                    >
                      <MonitorPlay size={15} />
                      牌桌回放
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
    </PageShell>
  );
}

const pillStyle: CSSProperties = {
  padding: '6px 10px',
  borderRadius: 999,
  border: '1px solid var(--border)',
  color: 'var(--text-secondary)',
  fontSize: 12,
  background: 'var(--page-bg)',
};

const runButtonStyle: CSSProperties = {
  minWidth: 240,
  padding: '12px 14px',
  borderRadius: 12,
  border: '1px solid var(--border)',
  textAlign: 'left',
  cursor: 'pointer',
  transition: 'all 120ms ease',
};

const btnStyle: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 36,
  padding: '0 16px',
  fontSize: 13,
};
