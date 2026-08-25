// src/replay_ui/src/pages/ParticipantsPage.tsx
// R10：参赛者管理 —— 账号 + 模型身份/产物（R10 UX Repair P1-4：artifact 管理）。
import { useCallback, useEffect, useState } from 'react';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { AccountTable } from '../components/Participants/AccountTable';
import { AccountFormModal } from '../components/Participants/AccountFormModal';
import { ModelFormModal } from '../components/Participants/ModelFormModal';
import { MODEL_PRESETS } from '../components/Participants/modelPresets';
import { participantsApi } from '../api/participantsApi';
import type {
  Account,
  AccountCreate,
  AccountStatsResponse,
  ArtifactStage,
  ExternalModelRevisionCreate,
  ModelArtifactCreate,
  ModelIdentity,
} from '../types/participants';

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

  // R13-D：External Revision 管理
  const [addingRevisionFor, setAddingRevisionFor] = useState<string | null>(null);
  const [revProvider, setRevProvider] = useState('mortal');
  const [revVersion, setRevVersion] = useState('');
  const [revExternalRef, setRevExternalRef] = useState('');
  const [revStage, setRevStage] = useState<ArtifactStage>('promoted');
  const [revIsCurrent, setRevIsCurrent] = useState(true);
  const [revLadderEligible, setRevLadderEligible] = useState(true);
  const [revPlaywithyouAllowed, setRevPlaywithyouAllowed] = useState(true);
  const [revReviewAllowed, setRevReviewAllowed] = useState(true);
  const [revBusy, setRevBusy] = useState(false);

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

  const openAddRevision = (identityId: string, label: string) => {
    setAddingRevisionFor(identityId);
    setRevProvider(identityId.includes('mortal') ? 'mortal' : 'external');
    setRevVersion(label.includes('4.2') ? '4.2' : label.includes('4.1') ? '4.1b' : '');
    setRevExternalRef('');
    setRevStage('promoted');
    setRevIsCurrent(true);
    setRevLadderEligible(true);
    setRevPlaywithyouAllowed(true);
    setRevReviewAllowed(true);
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

  const updateArtifactStage = async (identityId: string, artifactId: string, stage: ArtifactStage) => {
    setArtifactBusy(true);
    try {
      await participantsApi.updateModelArtifact(identityId, artifactId, { stage });
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setArtifactBusy(false);
    }
  };

  const addRevision = async () => {
    if (!addingRevisionFor || !revVersion.trim() || !revProvider.trim()) return;
    setRevBusy(true);
    try {
      const capabilities: string[] = [];
      if (revLadderEligible) capabilities.push('ladder_eligible');
      if (revPlaywithyouAllowed) capabilities.push('playwithyou_allowed');
      if (revReviewAllowed) capabilities.push('review_allowed');

      const payload: ExternalModelRevisionCreate = {
        provider: revProvider.trim(),
        version: revVersion.trim(),
        external_ref: revExternalRef.trim() || null,
        stage: revStage,
        is_current: revIsCurrent,
        capabilities,
      };
      await participantsApi.addExternalRevision(addingRevisionFor, payload);
      setAddingRevisionFor(null);
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevBusy(false);
    }
  };

  const setCurrentRevision = async (identityId: string, revisionId: string) => {
    setRevBusy(true);
    try {
      await participantsApi.setCurrentExternalRevision(identityId, revisionId);
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevBusy(false);
    }
  };

  const updateRevisionStage = async (identityId: string, revisionId: string, stage: ArtifactStage) => {
    setRevBusy(true);
    try {
      await participantsApi.updateExternalRevision(identityId, revisionId, { stage });
      await refreshModels();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevBusy(false);
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
                <div style={{ minWidth: 200 }}>
                  <div style={{ fontWeight: 700 }}>{identity.label}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    {identity.model_identity_id} · <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{identity.kind}</span>
                    {identity.account_id ? ` · ${identity.account_id}` : ' · 全局'}
                  </div>
                </div>

                {/* 1. local_model 专属：本地 Artifacts 管理 */}
                {identity.kind === 'local_model' && (
                  <div style={{ flex: 1, display: 'grid', gap: 6, fontSize: 12 }}>
                    {identity.artifacts.length === 0 && (
                      <div style={{ color: 'var(--text-muted)' }}>暂无本地产物</div>
                    )}
                    {identity.artifacts.map((art) => {
                      const isRetired = art.stage === 'retired';
                      return (
                        <div key={art.model_artifact_id} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 700, color: art.is_current ? 'var(--accent)' : 'var(--text-primary)' }}>
                            {art.label}{art.is_current ? '（当前）' : ''}
                          </span>
                          <span style={{
                            fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 700,
                            background: art.stage === 'promoted' ? 'rgba(16, 185, 129, 0.15)' : art.stage === 'deprecated' ? 'rgba(245, 158, 11, 0.15)' : 'rgba(156, 163, 175, 0.15)',
                            color: art.stage === 'promoted' ? 'var(--positive, #10b981)' : art.stage === 'deprecated' ? '#f59e0b' : 'var(--text-muted)',
                          }}>
                            {art.stage || 'promoted'}
                          </span>
                          <code style={{ color: 'var(--text-muted)', fontSize: 11, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {art.artifact_path ?? '—'}
                          </code>
                          {!art.is_current && !isRetired && (
                            <button
                              type="button"
                              onClick={() => void setCurrentArtifact(identity.model_identity_id, art.model_artifact_id)}
                              disabled={artifactBusy}
                              style={ghostSmallBtn}
                            >
                              设为当前
                            </button>
                          )}
                          {!isRetired && art.stage !== 'promoted' && (
                            <button
                              type="button"
                              onClick={() => void updateArtifactStage(identity.model_identity_id, art.model_artifact_id, 'promoted')}
                              disabled={artifactBusy}
                              style={ghostSmallBtn}
                            >
                              晋升
                            </button>
                          )}
                          {!isRetired && art.stage !== 'deprecated' && (
                            <button
                              type="button"
                              onClick={() => void updateArtifactStage(identity.model_identity_id, art.model_artifact_id, 'deprecated')}
                              disabled={artifactBusy}
                              style={ghostSmallBtn}
                            >
                              废弃 (deprecate)
                            </button>
                          )}
                          {!isRetired && (
                            <button
                              type="button"
                              onClick={() => void updateArtifactStage(identity.model_identity_id, art.model_artifact_id, 'retired')}
                              disabled={artifactBusy}
                              style={{ ...ghostSmallBtn, color: 'var(--negative, #ef4444)' }}
                            >
                              退役 (retire)
                            </button>
                          )}
                          {isRetired && (
                            <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>已封存</span>
                          )}
                        </div>
                      );
                    })}

                    {/* 添加本地产物 */}
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
                      <div style={{ marginTop: 2 }}>
                        <button type="button" onClick={() => openAddArtifact(identity.model_identity_id)} style={ghostSmallBtn}>
                          + 添加本地产物
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* 2. external_agent 专属：外部版本 External Revisions 管理 */}
                {identity.kind === 'external_agent' && (
                  <div style={{ flex: 1, display: 'grid', gap: 6, fontSize: 12 }}>
                    {(!identity.external_revisions || identity.external_revisions.length === 0) && (
                      <div style={{ color: 'var(--text-muted)' }}>暂无外部版本记录</div>
                    )}
                    {identity.external_revisions?.map((rev) => {
                      const isRetired = rev.stage === 'retired';
                      return (
                        <div key={rev.external_revision_id} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontWeight: 700, color: rev.is_current ? 'var(--accent)' : 'var(--text-primary)' }}>
                            v{rev.version}{rev.is_current ? '（当前）' : ''}
                          </span>
                          <span style={{
                            fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 700,
                            background: rev.stage === 'promoted' ? 'rgba(16, 185, 129, 0.15)' : rev.stage === 'deprecated' ? 'rgba(245, 158, 11, 0.15)' : 'rgba(156, 163, 175, 0.15)',
                            color: rev.stage === 'promoted' ? 'var(--positive, #10b981)' : rev.stage === 'deprecated' ? '#f59e0b' : 'var(--text-muted)',
                          }}>
                            {rev.stage}
                          </span>
                          <code style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                            {rev.external_revision_id}
                          </code>
                          <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                            [{rev.provider}]
                          </span>
                          {!rev.is_current && !isRetired && (
                            <button
                              type="button"
                              onClick={() => void setCurrentRevision(identity.model_identity_id, rev.external_revision_id)}
                              disabled={revBusy}
                              style={ghostSmallBtn}
                            >
                              设为当前
                            </button>
                          )}
                          {!isRetired && rev.stage !== 'promoted' && (
                            <button
                              type="button"
                              onClick={() => void updateRevisionStage(identity.model_identity_id, rev.external_revision_id, 'promoted')}
                              disabled={revBusy}
                              style={ghostSmallBtn}
                            >
                              晋升 (promoted)
                            </button>
                          )}
                          {!isRetired && rev.stage !== 'deprecated' && (
                            <button
                              type="button"
                              onClick={() => void updateRevisionStage(identity.model_identity_id, rev.external_revision_id, 'deprecated')}
                              disabled={revBusy}
                              style={ghostSmallBtn}
                            >
                              废弃 (deprecate)
                            </button>
                          )}
                          {!isRetired && (
                            <button
                              type="button"
                              onClick={() => void updateRevisionStage(identity.model_identity_id, rev.external_revision_id, 'retired')}
                              disabled={revBusy}
                              style={{ ...ghostSmallBtn, color: 'var(--negative, #ef4444)' }}
                            >
                              退役 (retire)
                            </button>
                          )}
                          {isRetired && (
                            <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>已封存</span>
                          )}
                        </div>
                      );
                    })}

                    {/* 添加外部版本 */}
                    {addingRevisionFor === identity.model_identity_id ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 10px', background: 'var(--page-bg)', borderRadius: 6, border: '1px solid var(--border)', marginTop: 4 }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                          <input
                            value={revProvider}
                            onChange={(e) => setRevProvider(e.target.value)}
                            placeholder="Provider (如 mortal)"
                            style={{ ...smallInput, width: 110 }}
                          />
                          <input
                            value={revVersion}
                            onChange={(e) => setRevVersion(e.target.value)}
                            placeholder="版本号 (如 4.2 / 4.1b)"
                            style={{ ...smallInput, width: 140 }}
                          />
                          <input
                            value={revExternalRef}
                            onChange={(e) => setRevExternalRef(e.target.value)}
                            placeholder="外部引用 ref（可选）"
                            style={{ ...smallInput, flex: 1, minWidth: 150 }}
                          />
                          <select
                            value={revStage}
                            onChange={(e) => setRevStage(e.target.value as ArtifactStage)}
                            style={smallInput}
                          >
                            <option value="promoted">promoted（正式）</option>
                            <option value="candidate">candidate（候选）</option>
                            <option value="deprecated">deprecated（已废弃）</option>
                          </select>
                        </div>
                        <div style={{ display: 'flex', gap: 12, alignItems: 'center', fontSize: 11, color: 'var(--text-secondary)' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                            <input type="checkbox" checked={revIsCurrent} onChange={(e) => setRevIsCurrent(e.target.checked)} />
                            设为当前版本
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                            <input type="checkbox" checked={revLadderEligible} onChange={(e) => setRevLadderEligible(e.target.checked)} />
                            具备天梯资格 (ladder_eligible)
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                            <input type="checkbox" checked={revPlaywithyouAllowed} onChange={(e) => setRevPlaywithyouAllowed(e.target.checked)} />
                            允许陪打 (playwithyou)
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                            <input type="checkbox" checked={revReviewAllowed} onChange={(e) => setRevReviewAllowed(e.target.checked)} />
                            允许复盘 (review)
                          </label>
                        </div>
                        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', marginTop: 2 }}>
                          <button
                            type="button"
                            onClick={() => void addRevision()}
                            disabled={revBusy || !revVersion.trim() || !revProvider.trim()}
                            style={primaryBtn}
                          >
                            注册外部版本
                          </button>
                          <button type="button" onClick={() => setAddingRevisionFor(null)} style={ghostSmallBtn}>取消</button>
                        </div>
                      </div>
                    ) : (
                      <div style={{ marginTop: 2 }}>
                        <button type="button" onClick={() => openAddRevision(identity.model_identity_id, identity.label)} style={ghostSmallBtn}>
                          + 注册外部版本 (External Revision)
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* 3. none 类型 */}
                {identity.kind === 'none' && (
                  <div style={{ flex: 1, color: 'var(--text-muted)', fontSize: 12 }}>
                    纯占位模型身份（无产物与版本管理）
                  </div>
                )}
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
