// src/replay_ui/src/pages/LadderModelPage.tsx
// 模型详情：账号横向对比 + 可选联赛聚合。
import { useMemo, type CSSProperties } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ladderApi } from '../api/ladderApi';
import { LadderSeasonNotice } from '../components/Ladder/LadderSeasonNotice';
import { LadderSeasonContextBanner } from '../components/Ladder/LadderSeasonContextBanner';
import { LadderSnapshotStatus } from '../components/Ladder/LadderSnapshotStatus';
import { PageHeader, PageShell, SectionTitle } from '../components/Layout/PageScaffold';
import { useLadderSeasonCatalog } from '../hooks/useLadderSeasonCatalog';
import { useVisibleLiveQuery } from '../hooks/useVisibleLiveQuery';
import { routes, withLadderSeason } from '../routes';
import type { LadderAccountRow, LadderModelDetail } from '../types/ladder';
import { fmtPt, fmtRank, fmtRate, fmtRating } from '../utils/ladderFormat';

function leagueNum(entry: Record<string, unknown>, key: string): number | null {
  const value = entry[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function leagueRankCounts(entry: Record<string, unknown>): number[] | null {
  const value = entry['rank_counts'];
  return Array.isArray(value) && value.length === 4 ? value.map((item) => Number(item)) : null;
}

export function LadderModelPage() {
  const { modelId } = useParams();
  const navigate = useNavigate();

  const catalog = useLadderSeasonCatalog();
  const { seasons, activeSeasonId, loading: catalogLoading } = catalog;

  // 完整 LadderModelDetail 为单一原子状态：snapshot 更新时 model 汇总/账号列表/
  // league_summary/season 一起替换，避免跨 snapshot 混合。
  const detailQuery = useVisibleLiveQuery<LadderModelDetail>({
    enabled: Boolean(activeSeasonId && modelId),
    queryKey: `model:${activeSeasonId ?? ''}:${modelId ?? ''}`,
    load: useMemo(
      () => (signal: AbortSignal) => {
        if (!activeSeasonId || !modelId) return Promise.reject(new Error('missing season or model'));
        return ladderApi.getModel(activeSeasonId, modelId, signal);
      },
      [activeSeasonId, modelId],
    ),
  });
  const detail = detailQuery.data;
  const loading = detailQuery.loading;

  const model = detail?.model;
  const activeSeason = seasons.find((s) => s.season_id === activeSeasonId);
  const seasonProblem = activeSeason?.readiness?.state !== 'ready' ? activeSeason?.readiness : null;
  // 未就绪抑制裸 409（结构化 Notice 展示原因）；catalog 500 / 实体 404 仍显示 alert
  const visibleError = catalog.error ?? (seasonProblem ? null : detailQuery.error);
  const backToLadder = () => navigate(withLadderSeason(routes.ladder, activeSeasonId));
  const openAccount = (row: LadderAccountRow) => {
    navigate(withLadderSeason(routes.ladderAccount(row.account_id), activeSeasonId));
  };

  return (
    <PageShell width={1180}>
      {/* R11-F Repair：season-context banner（历史/非当前/无正式赛季）三页统一 */}
      <LadderSeasonContextBanner
        activeSeasonId={activeSeasonId}
        activeSeason={activeSeason}
        defaultSeasonId={catalog.defaultSeasonId}
        defaultSeason={seasons.find((s) => s.season_id === catalog.defaultSeasonId)}
      />
      <PageHeader
        eyebrow="Model Profile"
        title={model?.model_id ?? modelId ?? '模型详情'}
        description={detail ? `${detail.season.title || detail.season.season_id} · ${model?.checkpoint || '未登记 checkpoint'}` : undefined}
        actions={(
          <button type="button" onClick={backToLadder} className="btn-secondary" style={actionButtonStyle}>
            返回天梯
          </button>
        )}
      />

      {/* 赛季列表错误与模型查询错误统一展示；轮询失败不进入 query.error */}
      {/* 赛季目录错误与模型查询错误统一展示；未就绪抑制裸 409（结构化 Notice 展示） */}
      {visibleError && (
        <div role="alert" style={{ color: 'var(--error)', fontSize: 13, marginBottom: 10 }}>{visibleError}</div>
      )}
      {(!visibleError && catalogLoading && !detailQuery.error) || (loading && activeSeasonId && modelId) ? (
        <div style={{ color: 'var(--text-muted)', fontSize: 13, padding: 20, textAlign: 'center' }}>加载中...</div>
      ) : null}

      {/* 无默认 / 未就绪：结构化状态展示（实体 payload 成功时主体优先，不遮挡） */}
      {!catalogLoading && !visibleError && !detail && !(loading && activeSeasonId && modelId) && (
        <LadderSeasonNotice
          seasons={seasons}
          activeSeason={seasons.find((s) => s.season_id === activeSeasonId)}
          defaultSeasonId={catalog.defaultSeasonId}
        />
      )}

      {!loading && detail && model && (
        <>
          {/* R11-F2：模型聚合是分析视图；正式排名以账号为主体 */}
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
            模型聚合为分析视图——正式排名始终以 Account 为主体；同一模型下的每个账号独立计分。
          </div>
          {/* 模型汇总（展示性聚合，不另算 Rating；跨段位不平均 PT） */}
          {model.summary && (
            <section className="card" style={{ padding: 14, marginBottom: 12 }}>
              <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', fontSize: 12 }}>
                {[
                  ['账号数', String(model.summary.accounts)],
                  ['总场数', String(model.summary.games)],
                  ['最高段位', model.summary.highest_rank_name || model.summary.highest_rank_id || '—'],
                  ['中位段位', model.summary.median_rank_name || '—'],
                  ['平均 Rating', fmtRating(model.summary.avg_rating)],
                  ['平均顺位', fmtRank(model.summary.avg_rank)],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{label}</div>
                    <div style={{ color: 'var(--text-primary)', fontWeight: 800, fontSize: 16, fontFamily: 'Menlo, Consolas, monospace' }}>{value}</div>
                  </div>
                ))}
              </div>
              {model.summary.rank_distribution && Object.keys(model.summary.rank_distribution).length > 0 && (
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  {Object.entries(model.summary.rank_distribution).map(([name, count]) => (
                    <span key={name} style={{ fontSize: 11, color: 'var(--text-muted)', border: '1px solid var(--border)', borderRadius: 4, padding: '2px 7px' }}>
                      {name} <b style={{ color: 'var(--text-primary)' }}>×{count}</b>
                    </span>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* 账号横向对比 */}
          <section className="card" style={{ padding: 12, marginBottom: 12 }}>
            <SectionTitle title="账号对比" description="点击账号查看详情。" />
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                    <th style={thStyle}>账号</th>
                    <th style={thStyle}>段位</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>PT</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Rating</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>场数</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>平均顺位</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>一位率</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>四位率</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>和率</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>放铳率</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>副露率</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>立直率</th>
                  </tr>
                </thead>
                <tbody>
                  {model.accounts.map((row) => (
                    <tr
                      key={row.account_id}
                      onClick={() => openAccount(row)}
                      style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                      title={`查看 ${row.display_name} 账号详情`}
                    >
                      <td style={{ ...tdStyle, fontWeight: 800, color: 'var(--accent)' }}>{row.display_name}</td>
                      <td style={{ ...tdStyle, fontWeight: 800 }}>
                        {row.rank_name || '七段'}
                        {row.tenhou_reached ? ' 👑' : ''}
                      </td>
                      <td style={{ ...tdStyle, ...numStyle, fontWeight: 800 }}>
                        {row.pt_target !== null ? fmtPt(row.pt_current) : '—'}
                      </td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRating(row.rating)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{row.games}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRank(row.avg_rank)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.rank_1_rate)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.rank_4_rate)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.agari_rate)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.houjuu_rate)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.fuuro_rate)}</td>
                      <td style={{ ...tdStyle, ...numStyle }}>{fmtRate(row.riichi_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* 联赛聚合（可选） */}
          {detail.league_summary && (
            <section className="card" style={{ padding: 12 }}>
              <SectionTitle
                title="联赛聚合"
                description={`${detail.league_summary.schema || ''} · 共 ${detail.league_summary.games_total ?? '—'} 场 · lineups: ${(detail.league_summary.lineups ?? []).join(', ') || '—'}`}
              />
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 12 }}>
                {leagueRankCounts(detail.league_summary.model) && (
                  <div>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>顺位分布</div>
                    <div style={{ color: 'var(--text-primary)', fontWeight: 700, fontFamily: 'Menlo, Consolas, monospace' }}>
                      {leagueRankCounts(detail.league_summary.model)?.join(' / ')}
                    </div>
                  </div>
                )}
                {[
                  ['场数', leagueNum(detail.league_summary.model, 'games')],
                  ['局数', leagueNum(detail.league_summary.model, 'rounds')],
                  ['平均顺位', leagueNum(detail.league_summary.model, 'avg_rank')],
                  ['平均顺位列PT', leagueNum(detail.league_summary.model, 'avg_rank_pt')],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{label}</div>
                    <div style={{ color: 'var(--text-primary)', fontWeight: 700, fontFamily: 'Menlo, Consolas, monospace' }}>
                      {value === null ? '—' : typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(3) : value}
                    </div>
                  </div>
                ))}
                {[
                  ['和率', fmtRate(leagueNum(detail.league_summary.model, 'agari_rate'))],
                  ['放铳率', fmtRate(leagueNum(detail.league_summary.model, 'houjuu_rate'))],
                  ['副露率', fmtRate(leagueNum(detail.league_summary.model, 'fuuro_rate'))],
                  ['立直率', fmtRate(leagueNum(detail.league_summary.model, 'riichi_rate'))],
                  ['流局率', fmtRate(leagueNum(detail.league_summary.model, 'ryukyoku_rate'))],
                  ['被飞率', fmtRate(leagueNum(detail.league_summary.model, 'tobi_rate'))],
                ].map(([label, value]) => (
                  <div key={label}>
                    <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>{label}</div>
                    <div style={{ color: 'var(--text-primary)', fontWeight: 700, fontFamily: 'Menlo, Consolas, monospace' }}>{value}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 快照状态：数据更新时间 / snapshot ID / 已计入场数 */}
          <LadderSnapshotStatus season={detail.season} refreshing={detailQuery.refreshing} />
        </>
      )}
    </PageShell>
  );
}

const actionButtonStyle: CSSProperties = {
  height: 32,
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '0 12px',
};

const thStyle: CSSProperties = {
  padding: '6px 8px',
  fontSize: 11,
  fontWeight: 700,
  whiteSpace: 'nowrap',
};

const tdStyle: CSSProperties = {
  padding: '6px 8px',
  color: 'var(--text-primary)',
  whiteSpace: 'nowrap',
};

const numStyle: CSSProperties = {
  textAlign: 'right',
  fontFamily: 'Menlo, Consolas, monospace',
};
