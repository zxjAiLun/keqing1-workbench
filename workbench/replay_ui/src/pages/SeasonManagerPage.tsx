// src/replay_ui/src/pages/SeasonManagerPage.tsx
// R11-E2：Season Manager —— 赛季列表 + lifecycle + 参赛阵容（enrollment）。
// 赛季成员只从 Participants Account Registry 勾选；backend 是硬 gate。
import { useCallback, useEffect, useState } from 'react';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { ladderApi } from '../api/ladderApi';
import { participantsApi } from '../api/participantsApi';
import type { Account, ModelIdentity } from '../types/participants';
import type { LadderSeason, LadderSeasonConfig, LadderSeasonsResponse } from '../types/ladder';

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  running: '进行中',
  completed: '已结束',
  archived: '已归档',
};

// 赛季状态色（功能色，引用语义令牌）
const STATUS_COLORS: Record<string, string> = {
  draft: 'var(--text-muted)',
  running: 'var(--success)',
  completed: 'var(--warning)',
  archived: 'var(--text-faint)',
};

export function SeasonManagerPage() {
  const [catalog, setCatalog] = useState<LadderSeasonsResponse | null>(null);
  const [config, setConfig] = useState<LadderSeasonConfig | null>(null);
  const [selectedId, setSelectedId] = useState<string>('');
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [identities, setIdentities] = useState<ModelIdentity[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newSeasonId, setNewSeasonId] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [draftSelection, setDraftSelection] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const [cat, accResp, modelsResp] = await Promise.all([
        ladderApi.listSeasons(),
        participantsApi.listAccounts(),
        participantsApi.listModels(),
      ]);
      setCatalog(cat);
      setAccounts(accResp.accounts);
      setIdentities(modelsResp.identities);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectSeason = async (seasonId: string) => {
    setSelectedId(seasonId);
    setError(null);
    try {
      const cfg = await ladderApi.getSeasonConfig(seasonId);
      setConfig(cfg);
      setDraftSelection(new Set(cfg.models.flatMap((m) => m.accounts.map((a) => a.account_id))));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const run = async (action: () => Promise<LadderSeasonConfig>) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      setConfig(updated);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const createSeason = async () => {
    if (!newSeasonId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const created = await ladderApi.createSeason({
        season_id: newSeasonId.trim(),
        title: newTitle.trim() || undefined,
      });
      setCreating(false);
      setNewSeasonId('');
      setNewTitle('');
      await refresh();
      await selectSeason(created.season_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveEnrollment = async () => {
    if (!config) return;
    await run(async () => {
      const updated = await ladderApi.setSeasonEnrollment(config.season_id, [...draftSelection]);
      return updated;
    });
  };

  // R11 post-release：删除赛季——running/current 由 backend 拒绝；此处强确认。
  const deleteSeason = async () => {
    if (!config) return;
    const isHistory = status === 'completed' || status === 'archived';
    if (isHistory) {
      const typed = window.prompt(
        `删除赛季 ${config.season_id} 会永久移除其注册表与天梯数据。\n请输入赛季 ID 确认删除：`,
        '',
      );
      if (typed !== config.season_id) {
        if (typed !== null) setError('确认输入与赛季 ID 不一致，已取消删除');
        return;
      }
    } else if (!window.confirm(`删除赛季 ${config.season_id}？`)) {
      return;
    }
    await run(async () => {
      await ladderApi.deleteSeason(config.season_id);
      setSelectedId('');
      setConfig(null);
      return config;
    });
  };

  const toggleAccount = (accountId: string) => {
    setDraftSelection((prev) => {
      const next = new Set(prev);
      if (next.has(accountId)) next.delete(accountId);
      else next.add(accountId);
      return next;
    });
  };

  const drivingModel = (account: Account): string => {
    if (!account.model_identity_id) return '—';
    const identity = identities.find((m) => m.model_identity_id === account.model_identity_id);
    return identity?.label ?? account.model_identity_id;
  };

  const status = config?.status ?? 'draft';
  const isCurrent = catalog?.default_season_id === selectedId;
  const enrolledIds = new Set(config?.models.flatMap((m) => m.accounts.map((a) => a.account_id)) ?? []);
  // 编辑权限：draft 可增删；running 只增；completed/archived 只读（backend 硬 gate）
  const canEdit = status === 'draft';
  const canAddOnly = status === 'running';

  return (
    <PageShell>
      <PageHeader title="赛季管理" description="canonical 赛季注册表：生命周期 + 参赛阵容（成员只来自 Participants Account）" />
      {error && <div style={{ fontSize: 13, color: 'var(--negative)', marginBottom: 10 }}>{error}</div>}

      {catalog && !catalog.default_season_id && (
        <div
          style={{
            fontSize: 13,
            padding: '10px 12px',
            borderRadius: 6,
            marginBottom: 12,
            border: '1px solid var(--gold-border)',
            background: 'var(--gold-bg)',
            color: 'var(--text-secondary)',
          }}
        >
          暂无当前正式赛季——请在赛季详情中「设为当前」。
        </div>
      )}

      <div style={{ display: 'grid', gap: 16 }}>
        {/* 赛季列表 */}
        <section style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={{ fontWeight: 800 }}>赛季列表（{catalog?.seasons.length ?? 0}）</span>
            <button style={primaryBtn} onClick={() => setCreating(true)}>新建赛季</button>
          </div>
          <div style={{ display: 'grid', gap: 6 }}>
            {(catalog?.seasons ?? []).map((season: LadderSeason) => (
              <div
                key={season.season_id}
                onClick={() => void selectSeason(season.season_id)}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 90px 70px 60px auto',
                  gap: 8,
                  alignItems: 'center',
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: `1px solid ${selectedId === season.season_id ? 'var(--accent)' : 'var(--border)'}`,
                  background: selectedId === season.season_id ? 'var(--accent-bg)' : 'var(--surface-subtle)',
                  cursor: 'pointer',
                  fontSize: 13,
                }}
              >
                <div>
                  <b>{season.title ?? season.season_id}</b>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {season.season_id}
                    {season.is_default ? ' · 当前正式赛季' : ''}
                  </div>
                </div>
                <span
                  style={{
                    justifySelf: 'center',
                    fontSize: 11,
                    fontWeight: 700,
                    padding: '3px 8px',
                    borderRadius: 6,
                    color: 'var(--accent-text)',
                    background: STATUS_COLORS[season.status ?? 'draft'] ?? 'var(--text-muted)',
                  }}
                >
                  {STATUS_LABELS[season.status ?? 'draft'] ?? season.status}
                </span>
                <span style={{ justifySelf: 'center', color: 'var(--text-secondary)' }}>{season.accounts} 账号</span>
                <span style={{ justifySelf: 'center', fontSize: 11, color: season.data_ready ? 'var(--success)' : 'var(--text-muted)' }}>
                  {season.data_ready ? '就绪' : '未就绪'}
                </span>
                <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>查看 →</span>
              </div>
            ))}
            {(catalog?.seasons.length ?? 0) === 0 && (
              <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>暂无赛季</div>
            )}
          </div>
        </section>

        {/* 赛季详情 + lifecycle + enrollment */}
        {config && (
          <section style={cardStyle}>
            <div style={cardHeaderStyle}>
              <span style={{ fontWeight: 800 }}>
                {config.title ?? config.season_id}
                {isCurrent && <span style={{ color: 'var(--success)', marginLeft: 8, fontSize: 12 }}>● 当前正式赛季</span>}
              </span>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: '3px 8px',
                  borderRadius: 6,
                  color: 'var(--accent-text)',
                  background: STATUS_COLORS[status] ?? 'var(--text-muted)',
                }}
              >
                {STATUS_LABELS[status] ?? status}
              </span>
            </div>

            {/* lifecycle 按钮（backend 是硬 gate；非法按钮直接 disabled） */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {status === 'draft' && (
                <button style={ghostBtn} disabled={busy} onClick={() => void run(() => ladderApi.startSeason(config.season_id))}>
                  开始赛季
                </button>
              )}
              {status === 'running' && (
                <>
                  <button
                    style={{ ...ghostBtn, borderColor: 'var(--success)', color: 'var(--success)' }}
                    disabled={busy || isCurrent}
                    onClick={() => void run(() => ladderApi.setDefaultSeason(config.season_id))}
                  >
                    {isCurrent ? '已是当前赛季' : '设为当前'}
                  </button>
                  <button
                    style={ghostBtn}
                    disabled={busy}
                    onClick={() => {
                      // R11-E Post-release Repair：结束是破坏性操作，需要确认
                      if (window.confirm('结束后将停止作为当前正式赛季（PT/Rating/对局数据保留）。确定结束？')) {
                        void run(() => ladderApi.completeSeason(config.season_id));
                      }
                    }}
                  >
                    结束赛季
                  </button>
                </>
              )}
              {status === 'completed' && (
                <>
                  <button
                    style={{ ...ghostBtn, borderColor: 'var(--success)', color: 'var(--success)' }}
                    disabled={busy}
                    onClick={() => void run(() => ladderApi.startSeason(config.season_id))}
                  >
                    重新开启
                  </button>
                  <button
                    style={ghostBtn}
                    disabled={busy}
                    onClick={() => {
                      if (window.confirm('归档后赛季永久锁定，不可重新开启。确定归档？')) {
                        void run(() => ladderApi.archiveSeason(config.season_id));
                      }
                    }}
                  >
                    归档赛季
                  </button>
                </>
              )}
              {status === 'archived' && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>已归档（永久只读）</span>}
              <span style={{ flex: 1 }} />
              <button
                style={{ ...ghostBtn, borderColor: 'var(--negative)', color: 'var(--negative)' }}
                disabled={busy || status === 'running' || isCurrent}
                title={status === 'running' || isCurrent ? '运行中的当前赛季不可删除' : '删除赛季'}
                onClick={() => void deleteSeason()}
              >
                删除赛季
              </button>
            </div>

            {/* 参赛阵容（只来自 Participants Accounts） */}
            <div style={{ fontWeight: 800, marginBottom: 8 }}>
              参赛阵容（{enrolledIds.size}）
              {canAddOnly && <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>进行中：只可增加</span>}
              {status !== 'draft' && status !== 'running' && (
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>只读</span>
              )}
            </div>
            <div style={{ display: 'grid', gap: 6, maxHeight: 320, overflow: 'auto', marginBottom: 10 }}>
              {accounts.map((account) => {
                const checked = draftSelection.has(account.account_id);
                const wasEnrolled = enrolledIds.has(account.account_id);
                const disabled = status === 'completed' || status === 'archived' || (canAddOnly && wasEnrolled);
                return (
                  <label
                    key={account.account_id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      fontSize: 13,
                      padding: '7px 10px',
                      borderRadius: 6,
                      border: '1px solid var(--border)',
                      background: 'var(--surface-subtle)',
                      cursor: disabled ? 'not-allowed' : 'pointer',
                      opacity: disabled ? 0.6 : 1,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => toggleAccount(account.account_id)}
                    />
                    <span style={{ fontWeight: 600 }}>{account.display_name}</span>
                    <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                      {account.account_type === 'human' ? '真人' : `驱动模型：${drivingModel(account)}`}
                    </span>
                    {canAddOnly && wasEnrolled && (
                      <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-muted)' }}>已参赛（不可移除）</span>
                    )}
                  </label>
                );
              })}
            </div>
            {(canEdit || canAddOnly) && (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <button style={{ ...primaryBtn, background: busy ? 'var(--text-muted)' : 'var(--accent)' }} disabled={busy} onClick={() => void saveEnrollment()}>
                  保存参赛阵容
                </button>
              </div>
            )}
          </section>
        )}
      </div>

      {/* 新建赛季 */}
      {creating && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 200,
            background: 'var(--result-overlay-bg)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div style={{ ...cardStyle, width: 360, display: 'grid', gap: 10 }}>
            <div style={{ fontWeight: 800 }}>新建赛季</div>
            <input
              value={newSeasonId}
              onChange={(e) => setNewSeasonId(e.target.value)}
              placeholder="season_id（字母/数字/_/-，最长 64）"
              style={inputStyle}
              autoFocus
            />
            <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="标题（可选）" style={inputStyle} />
            {error && <div style={{ fontSize: 12, color: 'var(--negative)' }}>{error}</div>}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button style={ghostBtn} onClick={() => setCreating(false)}>取消</button>
              <button style={primaryBtn} disabled={busy || !newSeasonId.trim()} onClick={() => void createSeason()}>
                创建
              </button>
            </div>
          </div>
        </div>
      )}
    </PageShell>
  );
}

const cardStyle: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  padding: 14,
};
const cardHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 10,
};
const primaryBtn: React.CSSProperties = {
  border: '1px solid var(--accent)',
  background: 'var(--accent)',
  color: 'var(--accent-text)',
  borderRadius: 5,
  fontSize: 12,
  fontWeight: 700,
  padding: '6px 12px',
  cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 5,
  fontSize: 12,
  padding: '6px 12px',
  cursor: 'pointer',
};
const inputStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  padding: '6px 8px',
  fontSize: 13,
};
