// src/replay_ui/src/pages/TenhouImportPage.tsx
// R10-D：天凤链接 → preview → 逐座身份解析 → 确认落账。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { SEAT_WINDS } from '../components/Matches/labels';
import { participantsApi } from '../api/participantsApi';
import { ladderApi } from '../api/ladderApi';
import { ApiError } from '../api/replayApi';
import { routes } from '../routes';
import type {
  Account,
  ExternalAlias,
  IntakeAdmissionAssessment,
  IntakePreview,
  ModelIdentity,
  SeatResolution,
  SeatNo,
} from '../types/participants';

type DraftResolution = {
  seat: SeatNo;
  action: 'assign' | 'create';
  account_id: string;
  alias_id: string;
  display_name: string;
  account_type: 'human' | 'managed_bot' | 'external_bot';
  alias_scope: 'global' | 'session' | 'match' | 'none';
  confidence: 'confirmed' | 'unresolved';
};

const EMPTY_DRAFT: DraftResolution = {
  seat: 0,
  action: 'assign',
  account_id: '',
  alias_id: '',
  display_name: '',
  account_type: 'external_bot',
  alias_scope: 'match',
  confidence: 'confirmed',
};

export function TenhouImportPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const prefilledUrl = searchParams.get('url') ?? '';
  // R11-C Repair：session_id 只作为内部关联键——优先来自 SPA navigation state
  // （Play-with-you 赛后跳转），不放在地址栏；后端也能从 log_id 自动恢复。
  // 保留 URL 参数读取仅为兼容旧入口。
  const stateSession = (location.state as { session_id?: string } | null)?.session_id;
  const [url, setUrl] = useState(prefilledUrl);
  const [sessionId] = useState<string | undefined>(
    stateSession ?? searchParams.get('session_id') ?? undefined,
  );
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [identities, setIdentities] = useState<ModelIdentity[]>([]);
  const [preview, setPreview] = useState<IntakePreview | null>(null);
  const [drafts, setDrafts] = useState<DraftResolution[]>([]);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // R10 UX Repair P1-6：正式天梯由 intake/confirm 决定（不再是 Play-with-you 启动选项）
  const [ladderEligible, setLadderEligible] = useState(false);
  const [ladderSeason, setLadderSeason] = useState('');
  // R11-A1：该牌谱已存在（preview 检出或 confirm 返回 duplicate_match）时的已有对局 ID
  const [duplicateMatchId, setDuplicateMatchId] = useState<string | null>(null);
  // R13-C：天梯准入评估 (Admission Assessment) 状态与 Request Fingerprint
  const [assessment, setAssessment] = useState<IntakeAdmissionAssessment | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [completedAssessmentKey, setCompletedAssessmentKey] = useState<string | null>(null);

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      const [accountsResp, modelsResp, seasonsResp] = await Promise.all([
        participantsApi.listAccounts(signal),
        participantsApi.listModels(signal),
        // R11-A1.5：正式赛季不再硬编码，取 season catalog 的当前默认赛季
        ladderApi.listSeasons(signal),
      ]);
      setAccounts(accountsResp.accounts);
      setIdentities(modelsResp.identities);
      if (seasonsResp.default_season_id) {
        setLadderSeason(seasonsResp.default_season_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // R10-E：从 play-with-you 会话跳转时自动解析预览
  const autoRun = useRef(false);
  useEffect(() => {
    if (autoRun.current) return;
    if (!prefilledUrl) return;
    autoRun.current = true;
    void runPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runPreview = async () => {
    if (!url.trim()) return;
    setLoading(true);
    setError(null);
    setDuplicateMatchId(null);
    try {
      const result = await participantsApi.intakePreview({ url: url.trim(), session_id: sessionId });
      setPreview(result);
      // R11-A1：preview 已检出重复 → 直接进入"已有对局"状态，不再要求逐座确认
      setDuplicateMatchId(result.duplicate_match_id ?? null);
      setDrafts(
        result.seats.map((seat) => {
          const autoCandidate =
            seat.candidates.length === 1 && seat.candidates[0].confidence === 'confirmed'
              ? seat.candidates[0]
              : undefined;
          const autoHasAccount = Boolean(autoCandidate?.account_id);
          return {
            ...EMPTY_DRAFT,
            seat: seat.seat,
            // P2-D：account-less alias 的唯一可绑定账号自动建议（backend 已算好）
            account_id: autoCandidate?.account_id ?? seat.auto_account_id ?? '',
            alias_id: autoCandidate?.alias_id ?? '',
            // 消费已有候选别名（有账号）时不再创建新 alias；模型已冻结需人工选账号 → match
            alias_scope: autoHasAccount ? 'none' : 'match',
          };
        }),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const updateDraft = (seat: SeatNo, patch: Partial<DraftResolution>) => {
    setDrafts((prev) => prev.map((d) => (d.seat === seat ? { ...d, ...patch } : d)));
  };

  // R11-G Repair：frozen-model 座位的唯一 predicate——backend 已按 binding.json
  // 反推 identity 时给出 eligible_account_ids（候选模型证据可能不在 alias 上）。
  // render 与 accountOptionsForSeat 必须共用，避免"禁新建生效但下拉没收窄"。
  const isFrozenModelSeat = (seatInfo: {
    candidates: ExternalAlias[];
    eligible_account_ids?: string[] | null;
  }): boolean =>
    seatInfo.eligible_account_ids != null ||
    seatInfo.candidates.some((c) => c.model_identity_id && !c.account_id);

  // R11-D：候选账号规则——冻结模型座位只显示该模型下可绑定的账号；
  // 已被其他座位使用的账号禁用（后端仍保留四座唯一性校验，前端禁用只是 UX）。
  const accountOptionsForSeat = (
    seat: SeatNo,
    seatInfo: { candidates: ExternalAlias[]; eligible_account_ids?: string[] | null },
  ): Array<{ account_id: string; label: string; disabled: boolean }> => {
    const frozenModel = isFrozenModelSeat(seatInfo);
    const pool =
      frozenModel && seatInfo.eligible_account_ids
        ? accounts.filter((a) => seatInfo.eligible_account_ids!.includes(a.account_id))
        : accounts;
    const usedElsewhere = new Map<string, string>();
    drafts.forEach((d) => {
      if (d.seat !== seat && d.account_id) usedElsewhere.set(d.account_id, SEAT_WINDS[d.seat]);
    });
    return pool.map((a) => ({
      account_id: a.account_id,
      label: usedElsewhere.has(a.account_id)
        ? `${a.display_name}（已用于${usedElsewhere.get(a.account_id)}家）`
        : `${a.display_name}（${a.account_id}）`,
      disabled: usedElsewhere.has(a.account_id),
    }));
  };

  // R13-C: 响应式天梯准入评估与 Fingerprint 闭环
  const currentResolutions: SeatResolution[] = useMemo(() => {
    if (!preview) return [];
    return drafts.map((d) => {
      const base: SeatResolution = {
        seat: d.seat,
        action: d.action,
        alias_scope: d.alias_scope,
        confidence: d.confidence,
      };
      if (d.action === 'create') {
        return {
          ...base,
          display_name: d.display_name || preview.raw_player_names[d.seat],
          account_type: d.account_type,
        };
      }
      return {
        ...base,
        account_id: d.account_id,
        alias_id: d.alias_id || undefined,
      };
    });
  }, [preview, drafts]);

  const currentAssessmentKey = useMemo(() => {
    if (!preview || !ladderEligible || !ladderSeason.trim()) return null;
    return JSON.stringify({
      log_id: preview.log_id,
      session_id: sessionId ?? null,
      season_id: ladderSeason.trim(),
      rating_eligible: ladderEligible,
      resolutions: currentResolutions,
    });
  }, [preview, ladderEligible, ladderSeason, sessionId, currentResolutions]);

  useEffect(() => {
    if (!preview || !ladderEligible || !ladderSeason.trim() || !currentAssessmentKey) {
      setAssessment(null);
      setAssessing(false);
      setCompletedAssessmentKey(null);
      return;
    }

    // Effect 触发时立即重置当前评估，进入 assessing 状态（杜绝 200ms debounce 期间的 stale 状态）
    setAssessment(null);
    setAssessing(true);
    setCompletedAssessmentKey(null);

    const controller = new AbortController();
    const reqKey = currentAssessmentKey;
    const reqResolutions = currentResolutions;

    const timer = setTimeout(async () => {
      try {
        const res = await participantsApi.intakeAssessment(
          {
            log_id: preview.log_id,
            resolutions: reqResolutions,
            session_id: sessionId,
            season_id: ladderSeason.trim(),
            rating_eligible: true,
          },
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setAssessment(res);
          setCompletedAssessmentKey(reqKey);
        }
      } catch (err) {
        if (!controller.signal.aborted) {
          // 网络或解析异常
          setAssessment({
            state: 'blocked',
            season_id: ladderSeason.trim(),
            rating_eligible: true,
            issues: [{ code: 'assessment_error', message: err instanceof Error ? err.message : String(err) }],
            seats: [],
          });
          setCompletedAssessmentKey(reqKey);
        }
      } finally {
        if (!controller.signal.aborted) {
          setAssessing(false);
        }
      }
    }, 200);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [preview, currentResolutions, currentAssessmentKey, ladderEligible, ladderSeason, sessionId]);

  const assessmentIsCurrent =
    assessment !== null &&
    completedAssessmentKey !== null &&
    completedAssessmentKey === currentAssessmentKey;

  const canConfirmRated =
    assessmentIsCurrent &&
    assessment.state === 'ready' &&
    !assessing;

  const confirmImport = async () => {
    if (!preview) return;
    const resolutions = currentResolutions;
    if (resolutions.some((r) => r.action === 'assign' && !r.account_id)) {
      setError('仍有座位未指派账号');
      return;
    }
    // P1-2（UX Repair 2）：rating_eligible=true 必须指定非空赛季
    if (ladderEligible && !ladderSeason.trim()) {
      setError('计入正式天梯必须指定赛季（season_id 不能为空）');
      return;
    }
    if (ladderEligible && !canConfirmRated) {
      setError('当前天梯准入评估未通过或仍在评估中');
      return;
    }
    setConfirming(true);
    setError(null);
    try {
      const resp = await participantsApi.intakeConfirm({
        log_id: preview.log_id,
        resolutions,
        session_id: sessionId,
        season_id: ladderEligible ? ladderSeason : null,
        rating_eligible: ladderEligible ? true : null,
      });
      navigate(routes.matchDetail(resp.match.match_id));
    } catch (e) {
      // R11-A1：结构化错误 envelope {code, message, context}；仅 duplicate_match
      // 进入"已有对局"状态，其余一律显示后端真实 message，绝不谎报"重复导入"。
      const detail = e instanceof ApiError
        && e.body && typeof e.body === 'object'
        && 'detail' in e.body
        && e.body.detail && typeof e.body.detail === 'object'
        ? (e.body.detail as { code?: string; message?: string; context?: { existing_match_id?: string } })
        : null;
      if (detail?.code === 'duplicate_match') {
        setDuplicateMatchId(detail.context?.existing_match_id ?? preview?.duplicate_match_id ?? null);
      } else {
        setError(detail?.message ?? (e instanceof Error ? e.message : String(e)));
      }
    } finally {
      setConfirming(false);
    }
  };

  return (
    <PageShell maxWidth={860}>
      <PageHeader
        title="天凤链接导入"
        description="粘贴天凤牌谱链接 → 预览 → 逐座确认身份 → 写入统一账本（full_replay）"
      />

      <div style={{ display: 'grid', gap: 16, paddingBottom: 24 }}>
        <section style={cardStyle}>
          <div style={{ display: 'flex', gap: 8 }}>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void runPreview()}
              placeholder="https://tenhou.net/3/?log=20260804gm-xxxx-xxxx&tw=0"
              style={{
                flex: 1, border: '1px solid var(--border)', background: 'var(--page-bg)',
                color: 'var(--text-primary)', borderRadius: 6, padding: '8px 10px', fontSize: 13,
              }}
            />
            <button
              onClick={runPreview}
              disabled={loading || !url.trim()}
              style={{
                border: '1px solid var(--accent)', background: 'var(--accent)', color: 'var(--accent-text)',
                borderRadius: 6, fontSize: 13, fontWeight: 700, padding: '8px 16px', cursor: 'pointer',
              }}
            >
              {loading ? '解析中…' : '解析预览'}
            </button>
          </div>
          {error && <div style={{ color: 'var(--negative)', fontSize: 13, marginTop: 8 }}>{error}</div>}
        </section>

        {preview && (
          <>
            {duplicateMatchId && (
              <section style={cardStyle}>
                <div style={{ fontWeight: 800, fontSize: 14, marginBottom: 6 }}>该牌谱已存在</div>
                <div style={{ fontSize: 13, marginBottom: 10, color: 'var(--text-secondary)' }}>
                  本天凤牌谱已作为对局 <b style={{ color: 'var(--text-primary)' }}>{duplicateMatchId}</b> 导入（防重），
                  不再创建重复对局。可查看已有对局，或在其详情页修订身份 / 作废。
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    onClick={() => navigate(routes.matchDetail(duplicateMatchId))}
                    style={{
                      border: '1px solid var(--accent)', background: 'var(--accent)', color: 'var(--accent-text)',
                      borderRadius: 6, fontSize: 13, fontWeight: 700, padding: '8px 14px', cursor: 'pointer',
                    }}
                  >
                    查看已有对局
                  </button>
                  <button
                    onClick={() => navigate(routes.matchDetail(duplicateMatchId))}
                    style={{
                      border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-primary)',
                      borderRadius: 6, fontSize: 13, padding: '8px 14px', cursor: 'pointer',
                    }}
                  >
                    修订已有对局
                  </button>
                </div>
              </section>
            )}

            <section style={cardStyle}>
              <div style={{ fontWeight: 800, marginBottom: 8 }}>
                {preview.game_length === 'hanchan' ? '半庄' : '东风'} · {preview.occurred_at.slice(0, 10)} · {preview.hand_count} 局 · 完整牌谱
              </div>
              <div style={{ display: 'grid', gap: 6 }}>
                {preview.seats.map((seat) => (
                  <div
                    key={seat.seat}
                    style={{
                      display: 'grid', gridTemplateColumns: '40px 1fr 120px 120px', gap: 6,
                      border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', fontSize: 13,
                    }}
                  >
                    <span style={{ fontWeight: 800, color: 'var(--text-muted)' }}>{SEAT_WINDS[seat.seat]}</span>
                    <span style={{ fontWeight: 700 }}>{seat.raw_name}</span>
                    <span style={{ fontVariantNumeric: 'tabular-nums' }}>{preview.final_scores[seat.seat].toLocaleString()}</span>
                    <span>{preview.ranks[seat.seat] + 1}位</span>
                  </div>
                ))}
              </div>
            </section>

            {!duplicateMatchId && (
              <>
              <section style={cardStyle}>
              <div style={{ fontWeight: 800, marginBottom: 10 }}>身份解析</div>
              <div style={{ display: 'grid', gap: 8 }}>
                {drafts.map((draft) => {
                  const seatInfo = preview.seats.find((s) => s.seat === draft.seat)!;
                  // P1-B / R11-G Repair：frozen-model 座位 = eligible_account_ids
                  // 非空（backend 从 binding.json 反推的 provenance）或 legacy
                  // 候选模型证据；二者任一成立就按 eligible 收窄下拉、禁新建账号。
                  const frozenModel = isFrozenModelSeat(seatInfo);
                  return (
                    <div key={draft.seat} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '10px 12px', display: 'grid', gap: 8 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 800, color: 'var(--text-muted)' }}>{SEAT_WINDS[draft.seat]}</span>
                        <span style={{ fontWeight: 700 }}>{seatInfo.raw_name}</span>
                        {seatInfo.candidates.length > 0 && (
                          <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                            候选：{seatInfo.candidates.map((c) => c.account_id || '（模型已冻结，待选账号）').join(' / ')}
                          </span>
                        )}
                        {/* Play-with-you simplification：session 只冻结了模型 → 显示本次运行模型 */}
                        {seatInfo.candidates
                          .filter((c) => c.model_identity_id && !c.account_id)
                          .map((c) => {
                            const identity = identities.find((m) => m.model_identity_id === c.model_identity_id);
                            const artifact = identity?.artifacts.find((a) => a.model_artifact_id === c.model_artifact_id);
                            return (
                              <span key={c.alias_id} style={{ fontSize: 11, color: 'var(--seat-2)' }}>
                                本次运行模型：{identity?.label ?? c.model_identity_id}
                                {artifact ? ` / ${artifact.label}` : ''}
                              </span>
                            );
                          })}
                      </div>
                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <select
                          value={draft.action}
                          onChange={(e) => updateDraft(draft.seat, { action: e.target.value as 'assign' | 'create' })}
                          style={selectStyle}
                        >
                          <option value="assign">指派已有账号</option>
                          <option value="create" disabled={frozenModel}>新建账号</option>
                        </select>
                        {draft.action === 'assign' ? (
                          <>
                          <select
                            value={draft.account_id}
                            onChange={(e) => {
                              const accountId = e.target.value;
                              const candidate = seatInfo.candidates.find((c) => c.account_id === accountId);
                              // P1-B：选账号不能清掉 account-less source alias（NoName-N → 模型证据）；
                              // 无账号候选匹配时保留原 alias_id，确认时后端从 alias 读取冻结模型。
                              updateDraft(draft.seat, {
                                account_id: accountId,
                                alias_id: candidate?.alias_id ?? draft.alias_id,
                              });
                            }}
                            style={{ ...selectStyle, flex: 1 }}
                          >
                            <option value="">选择账号…</option>
                            {accountOptionsForSeat(draft.seat, seatInfo).map((opt) => (
                              <option key={opt.account_id} value={opt.account_id} disabled={opt.disabled}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                          {/* R11-D：候选唯一 → 预选但可改；无候选 → 需要先绑定 */}
                          {frozenModel && seatInfo.eligible_account_ids?.length === 1 && (
                            <span style={{ fontSize: 11, color: 'var(--success)' }}>
                              候选唯一，已预选（可更改）
                            </span>
                          )}
                          {frozenModel && seatInfo.eligible_account_ids?.length === 0 && (
                            <span style={{ fontSize: 11, color: 'var(--negative)' }}>
                              该模型下没有可绑定账号，需先在 Participants 绑定
                            </span>
                          )}
                          </>
                        ) : (
                          <>
                            <input
                              value={draft.display_name}
                              onChange={(e) => updateDraft(draft.seat, { display_name: e.target.value })}
                              placeholder={seatInfo.raw_name}
                              style={{ ...selectStyle, flex: 1 }}
                            />
                            <select
                              value={draft.account_type}
                              onChange={(e) => updateDraft(draft.seat, { account_type: e.target.value as DraftResolution['account_type'] })}
                              style={selectStyle}
                            >
                              <option value="human">真人</option>
                              <option value="managed_bot">本地 AI</option>
                              <option value="external_bot">外部 AI</option>
                            </select>
                          </>
                        )}
                        <select
                          value={draft.alias_scope}
                          onChange={(e) => {
                            const scope = e.target.value as DraftResolution['alias_scope'];
                            // P1：frozen-model 座位的 source alias 是不可变证据——
                            // 切 scope 绝不能清掉它（否则 confirm 丢失 model，且 NoName-N
                            // 可能被晋升成 global identity rule）。
                            updateDraft(draft.seat, {
                              alias_scope: scope,
                              alias_id: frozenModel ? draft.alias_id : scope !== 'none' ? '' : draft.alias_id,
                            });
                          }}
                          style={selectStyle}
                        >
                          {/* frozen NoName-N 只允许本局/不保存，禁止 global/session（session-generated name） */}
                          {frozenModel ? (
                            <>
                              <option value="match">保存本局账号对齐</option>
                              <option value="none">不保存账号对齐</option>
                            </>
                          ) : (
                            <>
                              <option value="global">保存为全局别名</option>
                              <option value="session">仅本次会话</option>
                              <option value="match">仅本局</option>
                              <option value="none">不保存别名</option>
                            </>
                          )}
                        </select>
                        <select
                          value={draft.confidence}
                          onChange={(e) => updateDraft(draft.seat, { confidence: e.target.value as 'confirmed' | 'unresolved' })}
                          style={selectStyle}
                        >
                          <option value="confirmed">确认</option>
                          <option value="unresolved">暂记 unresolved</option>
                        </select>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* R13-C: 正式天梯准入评估卡片 */}
            {ladderEligible && (
              <section style={{ ...cardStyle, borderLeft: assessment?.state === 'ready' ? '4px solid var(--positive, #10b981)' : assessment?.state === 'blocked' ? '4px solid var(--negative, #ef4444)' : '1px solid var(--border)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <div style={{ fontWeight: 800, fontSize: 14 }}>
                    正式天梯准入评估 ({ladderSeason || '未指定赛季'})
                  </div>
                  <div>
                    {assessing ? (
                      <span style={{ fontSize: 12, padding: '2px 8px', borderRadius: 4, background: 'var(--page-bg)', color: 'var(--text-muted)' }}>
                        评估中…
                      </span>
                    ) : assessment?.state === 'ready' ? (
                      <span style={{ fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(16, 185, 129, 0.15)', color: 'var(--positive, #10b981)' }}>
                        ✓ 可计入正式天梯
                      </span>
                    ) : (
                      <span style={{ fontSize: 12, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(239, 68, 68, 0.15)', color: 'var(--negative, #ef4444)' }}>
                        ✕ 当前不可计入
                      </span>
                    )}
                  </div>
                </div>

                {assessment && assessment.issues.length > 0 && (
                  <div style={{ background: 'rgba(239, 68, 68, 0.08)', border: '1px solid rgba(239, 68, 68, 0.2)', borderRadius: 6, padding: '8px 12px', marginBottom: 12 }}>
                    <div style={{ fontWeight: 700, fontSize: 12, color: 'var(--negative, #ef4444)', marginBottom: 4 }}>
                      拦截原因：
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text-primary)' }}>
                      {assessment.issues.map((iss, idx) => (
                        <li key={idx}>
                          {iss.seat != null ? `[${SEAT_WINDS[iss.seat as SeatNo]}] ` : ''}{iss.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {assessment && assessment.seats.length > 0 && (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {assessment.seats.map((st) => (
                      <div
                        key={st.seat}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '40px 140px 1fr 80px',
                          alignItems: 'center',
                          gap: 8,
                          padding: '6px 10px',
                          borderRadius: 6,
                          background: 'var(--page-bg)',
                          fontSize: 12,
                        }}
                      >
                        <span style={{ fontWeight: 800, color: 'var(--text-muted)' }}>{SEAT_WINDS[st.seat]}</span>
                        <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {st.display_name || st.account_id || '未指派'}
                        </span>
                        <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {st.frozen_provenance ? (
                            st.frozen_provenance.kind === 'human' ? (
                              <span style={{ color: 'var(--accent)' }}>真人 (human)</span>
                            ) : st.frozen_provenance.kind === 'local_artifact' ? (
                              <span>本地模型: <code>{st.frozen_provenance.model_artifact_id}</code></span>
                            ) : (
                              <span>外部模型: <code>{st.frozen_provenance.external_revision_id}</code></span>
                            )
                          ) : (
                            <span style={{ color: 'var(--text-muted)' }}>待评估</span>
                          )}
                        </span>
                        <span style={{ textAlign: 'right', fontWeight: 700, color: st.is_ladder_eligible ? 'var(--positive, #10b981)' : 'var(--negative, #ef4444)' }}>
                          {st.is_ladder_eligible ? '合格' : '不合格'}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 14 }}>
                {/* R10 UX Repair P1-6：确认时决定是否计入正式天梯 */}
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={ladderEligible}
                    onChange={(e) => setLadderEligible(e.target.checked)}
                  />
                  计入正式天梯
                  {ladderEligible && (
                    <input
                      value={ladderSeason}
                      onChange={(e) => setLadderSeason(e.target.value)}
                      style={{ width: 150, border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)', borderRadius: 4, padding: '4px 8px', fontSize: 12 }}
                    />
                  )}
                </label>
                <button
                  onClick={confirmImport}
                  disabled={
                    confirming ||
                    Boolean(preview.duplicate_match_id) ||
                    (ladderEligible && !canConfirmRated)
                  }
                  style={{
                    border: '1px solid var(--accent)',
                    background: ladderEligible && !canConfirmRated ? 'var(--border)' : 'var(--accent)',
                    color: ladderEligible && !canConfirmRated ? 'var(--text-muted)' : 'var(--accent-text)',
                    borderRadius: 6, fontSize: 13, fontWeight: 700, padding: '9px 18px',
                    cursor: ladderEligible && !canConfirmRated ? 'not-allowed' : 'pointer',
                  }}
                >
                  {confirming ? '导入中…' : '确认导入'}
                </button>
              </div>
              {ladderEligible && !canConfirmRated && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {assessing
                    ? '正在评估天梯准入资格…'
                    : assessment?.state === 'blocked'
                    ? '当前天梯准入未通过。可调整身份/版本，或取消勾选「计入正式天梯」作为普通对局导入。'
                    : '等待准入评估完成…'}
                </div>
              )}
            </div>
              </>
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}

const cardStyle: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  padding: 14,
};
const selectStyle: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
  borderRadius: 4, padding: '5px 8px', fontSize: 13,
};
