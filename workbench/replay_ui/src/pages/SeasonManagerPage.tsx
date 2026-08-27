// src/replay_ui/src/pages/SeasonManagerPage.tsx
// R11-E2：Season Manager —— 赛季列表 + lifecycle + 参赛阵容（enrollment）。
// 赛季成员只从 Participants Account Registry 勾选；backend 是硬 gate。
// R15：终局摘要（archive summary）只读展示——running 显示 preview，
// completed/archived 显示 persisted sealed summary；UI 不重算任何权威事实。
import { useCallback, useEffect, useState } from 'react';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { ladderApi } from '../api/ladderApi';
import { participantsApi } from '../api/participantsApi';
import type { Account, ModelIdentity } from '../types/participants';
import type {
  ArchiveMatchSummary,
  ArchiveOperationSummary,
  ArchiveSummaryResponse,
  LadderSeason,
  LadderSeasonConfig,
  LadderSeasonsResponse,
  SeasonPreflightReport,
  SeasonRuntimeStatusResponse,
} from '../types/ladder';

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
  const [preflight, setPreflight] = useState<SeasonPreflightReport | null>(null);
  const [runtimeStatus, setRuntimeStatus] = useState<SeasonRuntimeStatusResponse | null>(null);
  // R15：终局摘要（只读消费 /archive-summary；不重算权威事实）
  const [archiveSummary, setArchiveSummary] = useState<ArchiveSummaryResponse | null>(null);
  const [archiveSummaryNotice, setArchiveSummaryNotice] = useState<string | null>(null);
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
    setArchiveSummary(null);
    setArchiveSummaryNotice(null);
    try {
      const [cfg, pre, rt, arch] = await Promise.all([
        ladderApi.getSeasonConfig(seasonId),
        ladderApi.getSeasonPreflight(seasonId).catch(() => null),
        ladderApi.getSeasonRuntimeStatus(seasonId).catch(() => null),
        // draft → 409；legacy completed 未封存 → 409（提示性 notice）
        ladderApi
          .getArchiveSummary(seasonId)
          .then((resp) => ({ ok: true as const, resp }))
          .catch((e: unknown) => ({ ok: false as const, e })),
      ]);
      setConfig(cfg);
      setPreflight(pre);
      setRuntimeStatus(rt);
      if (arch.ok) {
        setArchiveSummary(arch.resp);
      } else {
        setArchiveSummary(null);
        const message = arch.e instanceof Error ? arch.e.message : '';
        // 409 是预期内的（draft / legacy 未封存）；其他错误也降级为提示
        setArchiveSummaryNotice(
          message.includes('未封存')
            ? '该赛季为 legacy 未封存赛季（无终局摘要）'
            : null,
        );
      }
      setDraftSelection(new Set(cfg.models.flatMap((m) => m.accounts.map((a) => a.account_id))));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleSpawnRuntime = async () => {
    if (!config) return;
    setBusy(true);
    setError(null);
    try {
      const rt = await ladderApi.spawnSeasonRuntime(config.season_id);
      setRuntimeStatus(rt);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleStopRuntime = async () => {
    if (!config) return;
    setBusy(true);
    setError(null);
    try {
      const rt = await ladderApi.stopSeasonRuntime(config.season_id);
      setRuntimeStatus(rt);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const run = async (action: () => Promise<LadderSeasonConfig>) => {
    setBusy(true);
    setError(null);
    try {
      const updated = await action();
      setConfig(updated);
      await refresh();
      // R15：lifecycle 变化后刷新终局摘要（finalize → sealed；draft → 清空）
      try {
        const arch = await ladderApi.getArchiveSummary(updated.season_id);
        setArchiveSummary(arch);
        setArchiveSummaryNotice(null);
      } catch {
        setArchiveSummary(null);
      }
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
      if (updated.status === 'draft') {
        try {
          const pre = await ladderApi.getSeasonPreflight(config.season_id);
          setPreflight(pre);
        } catch {
          setPreflight(null);
        }
      }
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
  // 编辑权限：draft 可增删；legacy running（无 manifest）只增；frozen running 及 completed/archived 只读（backend 硬 gate）
  const canEdit = status === 'draft';
  const canAddOnly = status === 'running' && !config?.runtime_manifest;
  const rosterFrozen = (status === 'running' && !!config?.runtime_manifest) || status === 'completed' || status === 'archived';

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

            {/* Preflight 就绪状态与 Manifest 卡片 */}
            {status === 'draft' && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 6,
                  marginBottom: 14,
                  border: `1px solid ${preflight?.can_start ? 'var(--success)' : 'var(--negative)'}`,
                  background: 'var(--surface-subtle)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>
                    运行时预检 (Preflight): {preflight ? (preflight.can_start ? '✅ 准备就绪' : '❌ 无法启动') : '⚠️ 预检中或不可用'}
                  </span>
                  {preflight?.manifest && (
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto', fontFamily: 'monospace' }}>
                      Manifest ID: {preflight.manifest.manifest_id}
                    </span>
                  )}
                </div>
                {preflight && preflight.issues.length > 0 && (
                  <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 12, color: 'var(--negative)' }}>
                    {preflight.issues.map((iss, idx) => (
                      <li key={idx}>
                        [{iss.code}] {iss.message}
                      </li>
                    ))}
                  </ul>
                )}
                {!preflight && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>尚未取得预检结果，请检查网络或重新选择赛季。</div>
                )}
              </div>
            )}

            {status === 'running' && config.runtime_manifest && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 6,
                  marginBottom: 14,
                  border: '1px solid var(--border)',
                  background: 'var(--surface-subtle)',
                  fontSize: 12,
                }}
              >
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontWeight: 700 }}>🔒 已冻结 Runtime Manifest:</span>
                  <span style={{ fontFamily: 'monospace', color: 'var(--accent)' }}>{config.runtime_manifest.manifest_id}</span>
                  <span style={{ color: 'var(--text-muted)' }}>Authority Hash: {config.runtime_manifest.season_authority_hash.slice(0, 12)}…</span>
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, marginBottom: 8 }}>
                  <span style={{ fontWeight: 700 }}>进程监管 (Process Supervision):</span>
                  <span style={{ color: runtimeStatus?.is_active ? 'var(--success)' : 'var(--text-muted)', fontWeight: 600 }}>
                    {runtimeStatus?.is_active ? '● 运行中' : '○ 未运行 / 已停止'}
                  </span>
                  <span style={{ flex: 1 }} />
                  <button
                    style={{ ...ghostBtn, borderColor: 'var(--success)', color: 'var(--success)', padding: '2px 8px', fontSize: 12 }}
                    disabled={busy || runtimeStatus?.is_active}
                    onClick={() => void handleSpawnRuntime()}
                  >
                    启动本地模型进程
                  </button>
                  <button
                    style={{ ...ghostBtn, borderColor: 'var(--negative)', color: 'var(--negative)', padding: '2px 8px', fontSize: 12 }}
                    disabled={busy || !runtimeStatus?.is_active}
                    onClick={() => void handleStopRuntime()}
                  >
                    停止进程
                  </button>
                </div>

                {runtimeStatus && runtimeStatus.processes.length > 0 && (
                  <div style={{ display: 'grid', gap: 4, marginTop: 6 }}>
                    {runtimeStatus.processes.map((p) => (
                      <div
                        key={p.runtime_id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          padding: '4px 8px',
                          borderRadius: 4,
                          background: 'var(--surface)',
                          border: '1px solid var(--border)',
                          fontFamily: 'monospace',
                          fontSize: 11,
                        }}
                      >
                        <span style={{ fontWeight: 600 }}>{p.account_id}</span>
                        <span style={{ color: 'var(--text-muted)' }}>PID: {p.pid ?? '—'}</span>
                        <span
                          style={{
                            padding: '1px 5px',
                            borderRadius: 3,
                            color: p.state === 'healthy' ? 'var(--success)' : p.state === 'failed' ? 'var(--negative)' : 'var(--text-muted)',
                            background: 'var(--surface-subtle)',
                            fontWeight: 700,
                          }}
                        >
                          {p.state}
                        </span>
                        {p.failure_reason && <span style={{ color: 'var(--negative)' }}>({p.failure_reason})</span>}
                        {p.started_at && <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>启动于: {p.started_at}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* R15：终局摘要——running 显示 preview；completed/archived 显示 persisted sealed summary。
                UI 只读消费 /archive-summary，绝不重算权威事实。 */}
            {archiveSummary && archiveSummary.kind === 'preview' && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 6,
                  marginBottom: 14,
                  border: '1px dashed var(--border)',
                  background: 'var(--surface-subtle)',
                  fontSize: 12,
                }}
              >
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700 }}>📊 终局预览 (Archive Preview):</span>
                  <span style={{ color: 'var(--text-muted)' }}>
                    实时预览，非权威历史版本——finalize 时才封存并加盖 seal
                  </span>
                </div>
                <ArchiveSummaryBody
                  matchSummary={archiveSummary.archive_summary_projection.match_summary}
                  operationSummary={archiveSummary.archive_summary_projection.operation_summary}
                />
              </div>
            )}

            {archiveSummary && archiveSummary.kind === 'sealed' && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 6,
                  marginBottom: 14,
                  border: '1px solid var(--accent)',
                  background: 'var(--surface-subtle)',
                  fontSize: 12,
                }}
              >
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700 }}>🔒 已封存终局摘要 (Sealed Archive Summary):</span>
                  <span style={{ color: 'var(--text-muted)' }}>完成于 {archiveSummary.archive_summary.completed_at}</span>
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8, color: 'var(--text-muted)' }}>
                  <span style={{ fontFamily: 'monospace' }}>Summary ID: {archiveSummary.archive_summary.archive_summary_id}</span>
                  <span style={{ fontFamily: 'monospace' }}>
                    Seal Hash: {archiveSummary.archive_summary.archive_summary_hash.slice(0, 16)}…
                  </span>
                  <span style={{ fontFamily: 'monospace' }}>Manifest: {archiveSummary.archive_summary.manifest_id}</span>
                  <span style={{ fontFamily: 'monospace' }}>
                    Authority: {archiveSummary.archive_summary.season_authority_hash.slice(0, 12)}…
                  </span>
                </div>
                <ArchiveSummaryBody
                  matchSummary={archiveSummary.archive_summary.match_summary}
                  operationSummary={archiveSummary.archive_summary.operation_summary}
                />
              </div>
            )}

            {!archiveSummary && archiveSummaryNotice && (status === 'completed' || status === 'archived') && (
              <div
                style={{
                  padding: '10px 12px',
                  borderRadius: 6,
                  marginBottom: 14,
                  border: '1px dashed var(--border)',
                  background: 'var(--surface-subtle)',
                  fontSize: 12,
                  color: 'var(--text-muted)',
                }}
              >
                {archiveSummaryNotice}
              </div>
            )}

            {/* lifecycle 按钮（backend 是硬 gate；非法按钮直接 disabled） */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {status === 'draft' && (
                <button
                  style={{
                    ...ghostBtn,
                    borderColor: preflight?.can_start ? 'var(--success)' : 'var(--border)',
                    color: preflight?.can_start ? 'var(--success)' : 'var(--text-muted)',
                  }}
                  disabled={busy || !preflight || !preflight.can_start}
                  onClick={() => void run(() => ladderApi.startSeason(config.season_id))}
                >
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
                      // R15：frozen 赛季结束 = finalize（封存终局摘要，sealed 后不可 reopen）
                      const confirmText = config.runtime_manifest
                        ? '结束将封存终局摘要（sealed，不可重新开启；PT/Rating/对局数据保留）。确定结束？'
                        : '结束后将停止作为当前正式赛季（PT/Rating/对局数据保留）。确定结束？';
                      if (window.confirm(confirmText)) {
                        void run(() => ladderApi.completeSeason(config.season_id));
                      }
                    }}
                  >
                    {config.runtime_manifest ? '结束并封存摘要' : '结束赛季'}
                  </button>
                </>
              )}
              {status === 'completed' && (
                <>
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
              {status === 'running' && config.runtime_manifest && (
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>🔒 Runtime Manifest 已冻结，阵容只读</span>
              )}
              {status !== 'draft' && status !== 'running' && (
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 8 }}>只读</span>
              )}
            </div>
            <div style={{ display: 'grid', gap: 6, maxHeight: 320, overflow: 'auto', marginBottom: 10 }}>
              {accounts.map((account) => {
                const checked = draftSelection.has(account.account_id);
                const wasEnrolled = enrolledIds.has(account.account_id);
                const disabled = rosterFrozen || (canAddOnly && wasEnrolled);
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

/** R15：终局摘要正文（Match/Revision/Session/Process 统计）——纯展示，不重算。 */
function ArchiveSummaryBody({
  matchSummary,
  operationSummary,
}: {
  matchSummary: ArchiveMatchSummary;
  operationSummary: ArchiveOperationSummary;
}) {
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', color: 'var(--text-secondary)' }}>
        <span>对局：<b>{matchSummary.total_matches}</b></span>
        <span>有效：<b>{matchSummary.active_matches}</b></span>
        <span>作废：<b>{matchSummary.void_matches}</b></span>
        <span>正式计分：<b>{matchSummary.rating_eligible_matches}</b></span>
        <span>Runtime 绑定：<b>{matchSummary.runtime_bound_matches}</b></span>
        <span>Revision 总数：<b>{matchSummary.total_revisions}</b></span>
      </div>
      {(matchSummary.first_match_occurred_at || matchSummary.last_match_occurred_at) && (
        <div style={{ color: 'var(--text-muted)' }}>
          首局 {matchSummary.first_match_occurred_at ?? '—'} · 末局 {matchSummary.last_match_occurred_at ?? '—'}
        </div>
      )}
      {matchSummary.account_stats.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          {matchSummary.account_stats.map((s) => (
            <div
              key={s.account_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '3px 8px',
                borderRadius: 4,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                fontFamily: 'monospace',
                fontSize: 11,
              }}
            >
              <span style={{ fontWeight: 600, minWidth: 140 }}>{s.account_id}</span>
              <span>局数 {s.games}</span>
              <span>顺位和 {s.rank_sum}</span>
              <span style={{ color: 'var(--gold, var(--accent))' }}>
                🥇{s.first_places} 🥈{s.second_places} 🥉{s.third_places} 4位:{s.fourth_places}
              </span>
            </div>
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', color: 'var(--text-secondary)' }}>
        <span>执行会话 (Execution Sessions)：<b>{operationSummary.session_count}</b></span>
        {operationSummary.sessions.map((s) => (
          <span
            key={s.session_id}
            style={{
              fontFamily: 'monospace',
              fontSize: 11,
              padding: '2px 6px',
              borderRadius: 3,
              background: 'var(--surface)',
              border: '1px solid var(--border)',
            }}
            title={`创建于 ${s.created_at} · 参与者 ${s.participant_count}（local_model ${s.local_model_participants}）`}
          >
            {s.session_id}
          </span>
        ))}
      </div>
      {operationSummary.processes.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }}>
          {operationSummary.processes.map((p) => (
            <div
              key={p.runtime_id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '3px 8px',
                borderRadius: 4,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                fontFamily: 'monospace',
                fontSize: 11,
              }}
            >
              <span style={{ fontWeight: 600 }}>{p.account_id}</span>
              <span
                style={{
                  padding: '1px 5px',
                  borderRadius: 3,
                  color: p.state === 'healthy' ? 'var(--success)' : p.state === 'failed' ? 'var(--negative)' : 'var(--text-muted)',
                  background: 'var(--surface-subtle)',
                  fontWeight: 700,
                }}
              >
                {p.state}
              </span>
              {typeof p.exit_code === 'number' && <span style={{ color: 'var(--text-muted)' }}>exit {p.exit_code}</span>}
              {p.stopped_at && <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>停止于 {p.stopped_at}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
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
