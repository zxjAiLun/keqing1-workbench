// src/replay_ui/src/pages/ParticipantsPage.tsx
// R10：参赛者管理 —— 账号 + 模型身份/产物（R10 UX Repair P1-4：artifact 管理）。
import { useCallback, useEffect, useState } from 'react';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { AccountTable } from '../components/Participants/AccountTable';
import { AccountFormModal } from '../components/Participants/AccountFormModal';
import { ModelFormModal } from '../components/Participants/ModelFormModal';
import { MODEL_PRESETS } from '../components/Participants/modelPresets';
import { participantsApi } from '../api/participantsApi';
import type { Account, AccountCreate, AccountStatsResponse, ModelArtifactCreate, ModelIdentity } from '../types/participants';

export function ParticipantsPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [identities, setIdentities] = useState<ModelIdentity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Account | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingModel, setCreatingModel] = useState(false);
  // R10-G：账号详细统计
  const [statsAccount, setStatsAccount] = useState('');
  const [stats, setStats] = useState<AccountStatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  // R10 UX Repair P1-4：artifact 管理（添加产物表单）
  const [addingArtifactFor, setAddingArtifactFor] = useState<string | null>(null);
  const [artifactPreset, setArtifactPreset] = useState('');
  const [artifactLabel, setArtifactLabel] = useState('');
  const [artifactPath, setArtifactPath] = useState('');
  const [artifactBusy, setArtifactBusy] = useState(false);

  const refreshModels = useCallback(async () => {
    try {
      const resp = await participantsApi.listModels();
      setIdentities(resp.identities);
    } catch {
      /* transient */
    }
  }, []);

  const openAddArtifact = (identityId: string) => {
    setAddingArtifactFor(identityId);
    setArtifactPreset('');
    setArtifactLabel('');
    setArtifactPath('');
  };

  const applyArtifactPreset = (presetId: string) => {
    setArtifactPreset(presetId);
    const preset = MODEL_PRESETS.find((p) => p.id === presetId);
    if (preset) {
      setArtifactLabel(preset.label);
      setArtifactPath(preset.artifact_path);
    }
  };

  const addArtifact = async () => {
    if (!addingArtifactFor || !artifactLabel.trim()) return;
    setArtifactBusy(true);
    try {
      const payload: ModelArtifactCreate = {
        label: artifactLabel.trim(),
        artifact_path: artifactPath.trim() || null,
      };
      await participantsApi.addModelArtifact(addingArtifactFor, payload);
      setAddingArtifactFor(null);
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setArtifactBusy(false);
    }
  };

  const setCurrentArtifact = async (identityId: string, artifactId: string) => {
    setArtifactBusy(true);
    try {
      await participantsApi.setCurrentArtifact(identityId, artifactId);
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setArtifactBusy(false);
    }
  };

  const loadStats = useCallback(async (accountId: string) => {
    if (!accountId) return;
    setStatsLoading(true);
    try {
      setStats(await participantsApi.getAccountStats(accountId));
    } catch {
      setStats(null);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [accountsResp, modelsResp] = await Promise.all([
        participantsApi.listAccounts(signal),
        participantsApi.listModels(signal),
      ]);
      setAccounts(accountsResp.accounts);
      setIdentities(modelsResp.identities);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const handleSaveAccount = async (payload: AccountCreate) => {
    if (editing) {
      const updated = await participantsApi.updateAccount(editing.account_id, {
        display_name: payload.display_name,
        default_controller: payload.default_controller ?? undefined,
        model_identity_id: payload.model_identity_id ?? null,
        note: payload.note ?? null,
      });
      setAccounts((prev) => prev.map((a) => (a.account_id === updated.account_id ? updated : a)));
    } else {
      const created = await participantsApi.createAccount(payload);
      setAccounts((prev) => [...prev, created]);
    }
  };

  const handleToggle = async (account: Account) => {
    const updated = await participantsApi.updateAccount(account.account_id, { enabled: !account.enabled });
    setAccounts((prev) => prev.map((a) => (a.account_id === updated.account_id ? updated : a)));
  };

  const handleDelete = async (account: Account) => {
    if (!window.confirm(`删除/停用 ${account.display_name}？被对局引用时将软停用。`)) return;
    await participantsApi.deleteAccount(account.account_id);
    await load(new AbortController().signal);
  };

  return (
    <PageShell>
      <PageHeader title="参赛者" description="真人 / 本地 AI / 外部 AI / 仅登记 —— 任意四账号可组成一桌" />
      <div style={{ display: 'grid', gap: 16 }}>
        <section style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={{ fontWeight: 800 }}>账号（{accounts.length}）</span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={primaryBtn} onClick={() => setCreating(true)}>新建参赛者</button>
              <button style={ghostBtn} onClick={() => setCreatingModel(true)}>新建模型身份</button>
            </div>
          </div>
          {loading ? (
            <div style={{ padding: 24, color: 'var(--text-muted)' }}>加载中…</div>
          ) : error ? (
            <div style={{ padding: 24, color: 'var(--negative)' }}>{error}</div>
          ) : (
            <AccountTable
              accounts={accounts}
              identities={identities}
              onEdit={setEditing}
              onToggleEnabled={handleToggle}
              onDelete={handleDelete}
            />
          )}
        </section>

        {/* R10-G：账号详细统计（completeness-aware） */}
        <section style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={{ fontWeight: 800 }}>账号统计</span>
            <select
              value={statsAccount}
              onChange={(e) => {
                setStatsAccount(e.target.value);
                void loadStats(e.target.value);
              }}
              style={{
                border: '1px solid var(--border)', background: 'var(--page-bg)',
                color: 'var(--text-primary)', borderRadius: 4, padding: '4px 8px', fontSize: 12,
              }}
            >
              <option value="">选择账号…</option>
              {accounts.map((a) => (
                <option key={a.account_id} value={a.account_id}>{a.display_name}（{a.account_id}）</option>
              ))}
            </select>
          </div>
          {statsLoading && <div style={{ padding: 12, fontSize: 12, color: 'var(--text-muted)' }}>统计中…</div>}
          {!statsLoading && stats && (
            <div style={{ display: 'grid', gap: 8, fontSize: 12 }}>
              <div style={{ color: 'var(--text-muted)' }}>
                覆盖：{stats.coverage.total_matches} 场有结果 · {stats.coverage.matches_with_hands} 场逐局 ·
                {stats.coverage.matches_with_full_replay} 场完整牌谱（{stats.coverage.hands_used} 局）
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 6 }}>
                <div>一位率 <b>{fmt(stats.placement.first_rate, true)}</b></div>
                <div>二位率 <b>{fmt(stats.placement.second_rate, true)}</b></div>
                <div>三位率 <b>{fmt(stats.placement.third_rate, true)}</b></div>
                <div>四位率 <b>{fmt(stats.placement.fourth_rate, true)}</b></div>
                <div>平均顺位 <b>{fmt(stats.placement.avg_rank)}</b></div>
                <div>平均最终点 <b>{fmt(stats.placement.avg_final_score)}</b></div>
                <div>和牌率 <b>{fmt(stats.detailed.win_rate, true)}</b></div>
                <div>放铳率 <b>{fmt(stats.detailed.dealin_rate, true)}</b></div>
                <div>自摸率 <b>{fmt(stats.detailed.tsumo_share, true)}</b></div>
                <div>立直率 <b>{fmt(stats.detailed.riichi_rate, true)}</b></div>
                <div>副露率 <b>{fmt(stats.detailed.call_rate, true)}</b></div>
                <div>流局听牌率 <b>{fmt(stats.detailed.tenpai_rate, true)}</b></div>
                <div>平均和牌点 <b>{fmt(stats.detailed.avg_win_points)}</b></div>
                <div>平均放铳点 <b>{fmt(stats.detailed.avg_dealin_points)}</b></div>
                <div>亲番胜率 <b>{fmt(stats.detailed.oya_win_rate, true)}</b></div>
                <div>子番胜率 <b>{fmt(stats.detailed.koshu_win_rate, true)}</b></div>
              </div>
            </div>
          )}
        </section>

        <section style={cardStyle}>
          <div style={cardHeaderStyle}>
            <span style={{ fontWeight: 800 }}>模型身份（{identities.length}）</span>
            <button style={primaryBtn} onClick={() => setCreatingModel(true)}>新建模型身份</button>
          </div>
          <div style={{ display: 'grid', gap: 8 }}>
            {identities.map((identity) => (
              <div key={identity.model_identity_id} style={modelRowStyle}>
                <div style={{ minWidth: 180 }}>
                  <div style={{ fontWeight: 700 }}>{identity.label}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {identity.model_identity_id} · {identity.kind}
                    {identity.account_id ? ` · ${identity.account_id}` : ' · 全局'}
                  </div>
                </div>
                {/* 产物列表（R10 UX Repair P1-4） */}
                <div style={{ flex: 1, display: 'grid', gap: 4, fontSize: 12 }}>
                  {identity.artifacts.length === 0 && (
                    <div style={{ color: 'var(--text-muted)' }}>无产物（外部代理或仅身份）</div>
                  )}
                  {identity.artifacts.map((art) => (
                    <div key={art.model_artifact_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 700, color: art.is_current ? 'var(--accent)' : 'var(--text-primary)' }}>
                        {art.label}{art.is_current ? '（当前）' : ''}
                      </span>
                      <code style={{ color: 'var(--text-muted)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {art.artifact_path ?? '—'}
                      </code>
                      {!art.is_current && (
                        <button
                          type="button"
                          onClick={() => void setCurrentArtifact(identity.model_identity_id, art.model_artifact_id)}
                          disabled={artifactBusy}
                          style={ghostSmallBtn}
                        >
                          设为当前
                        </button>
                      )}
                    </div>
                  ))}
                  {/* 添加产物 */}
                  {addingArtifactFor === identity.model_identity_id ? (
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
                      <select
                        value={artifactPreset}
                        onChange={(e) => applyArtifactPreset(e.target.value)}
                        style={smallInput}
                      >
                        <option value="">使用预置…</option>
                        {MODEL_PRESETS.map((p) => (
                          <option key={p.id} value={p.id}>{p.label}</option>
                        ))}
                      </select>
                      <input
                        value={artifactLabel}
                        onChange={(e) => setArtifactLabel(e.target.value)}
                        placeholder="产物名称"
                        style={{ ...smallInput, width: 120 }}
                      />
                      <input
                        value={artifactPath}
                        onChange={(e) => setArtifactPath(e.target.value)}
                        placeholder="checkpoint 路径"
                        style={{ ...smallInput, flex: 1, minWidth: 200 }}
                      />
                      <button
                        type="button"
                        onClick={() => void addArtifact()}
                        disabled={artifactBusy || !artifactLabel.trim()}
                        style={ghostSmallBtn}
                      >
                        添加
                      </button>
                      <button type="button" onClick={() => setAddingArtifactFor(null)} style={ghostSmallBtn}>取消</button>
                    </div>
                  ) : (
                    <button type="button" onClick={() => openAddArtifact(identity.model_identity_id)} style={ghostSmallBtn}>
                      + 添加产物
                    </button>
                  )}
                </div>
              </div>
            ))}
            {identities.length === 0 && (
              <div style={{ padding: 16, color: 'var(--text-muted)', textAlign: 'center' }}>暂无模型身份</div>
            )}
          </div>
        </section>
      </div>

      {(creating || editing) && (
        <AccountFormModal
          account={editing}
          identities={identities}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSave={handleSaveAccount}
        />
      )}
      {creatingModel && (
        <ModelFormModal
          onClose={() => setCreatingModel(false)}
          onSave={async (payload) => {
            const created = await participantsApi.createModel(payload);
            setIdentities((prev) => [...prev, created]);
          }}
        />
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
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10,
};
const primaryBtn: React.CSSProperties = {
  border: '1px solid var(--accent)', background: 'var(--accent)', color: 'var(--accent-text)',
  borderRadius: 5, fontSize: 12, fontWeight: 700, padding: '6px 12px', cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
  borderRadius: 5, fontSize: 12, padding: '6px 12px', cursor: 'pointer',
};
const modelRowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
  border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px',
};

const ghostSmallBtn: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)',
  borderRadius: 4, fontSize: 11, padding: '3px 8px', cursor: 'pointer', whiteSpace: 'nowrap',
};

const smallInput: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
  borderRadius: 4, padding: '4px 8px', fontSize: 12, boxSizing: 'border-box',
};

function fmt(value: number | null | undefined, percent = false): string {
  if (value === null || value === undefined) return '—';
  if (percent) return `${(value * 100).toFixed(1)}%`;
  return String(value);
}
