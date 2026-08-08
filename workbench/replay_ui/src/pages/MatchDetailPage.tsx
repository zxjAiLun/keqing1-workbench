// src/replay_ui/src/pages/MatchDetailPage.tsx
// R10：对局详情 + 修订历史 + 修订/作废。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { MatchDetailView } from '../components/Matches/MatchDetailView';
import { RevisionTimeline } from '../components/Matches/RevisionTimeline';
import { SeatPicker } from '../components/Matches/SeatPicker';
import { ScoreEntryForm } from '../components/Matches/ScoreEntryForm';
import { ValidationNotice } from '../components/Matches/ValidationNotice';
import { participantsApi } from '../api/participantsApi';
import { ApiError } from '../api/replayApi';
import { routes } from '../routes';
import type {
  Account,
  GameLength,
  Match,
  MatchResponse,
  MatchSeat,
  ModelIdentity,
  ValidationIssue,
} from '../types/participants';

export function MatchDetailPage() {
  const { matchId = '' } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<MatchResponse | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [identities, setIdentities] = useState<ModelIdentity[]>([]);
  const [editing, setEditing] = useState(false);
  const [seats, setSeats] = useState<MatchSeat[]>([]);
  const [scores, setScores] = useState<string[]>([]);
  const [startingPoints, setStartingPoints] = useState(25000);
  const [gameLength, setGameLength] = useState<GameLength>('hanchan');
  const [note, setNote] = useState('');
  const [seasonId, setSeasonId] = useState('');
  const [ratingEligible, setRatingEligible] = useState(false);
  const [force, setForce] = useState(false);
  const [reason, setReason] = useState('');
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [projecting, setProjecting] = useState(false);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [matchResp, accountsResp, modelsResp] = await Promise.all([
        participantsApi.getMatch(matchId, signal),
        participantsApi.listAccounts(signal),
        participantsApi.listModels(signal),
      ]);
      setData(matchResp);
      setAccounts(accountsResp.accounts);
      setIdentities(modelsResp.identities);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [matchId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const startEdit = () => {
    if (!data) return;
    setSeats(data.match.seats);
    setScores(data.match.final_scores.map(String));
    setStartingPoints(data.match.starting_points);
    setGameLength(data.match.game_length);
    setNote(data.match.note ?? '');
    setSeasonId(data.match.season_id ?? '');
    setRatingEligible(data.match.rating_eligible ?? false);
    setForce(false);
    setReason('');
    setIssues([]);
    setEditing(true);
  };

  const submitRevise = async () => {
    if (!data) return;
    setError(null);
    setIssues([]);
    // P1-2（UX Repair 2）：rating_eligible=true 必须指定非空赛季
    if (ratingEligible && !seasonId.trim()) {
      setError('「纳入正式天梯计分」必须指定赛季（season_id 不能为空）');
      return;
    }
    try {
      await participantsApi.reviseMatch(data.match.match_id, {
        seats,
        final_scores: scores.map((s) => parseInt(s, 10)),
        starting_points: startingPoints,
        game_length: gameLength,
        note: note || null,
        season_id: seasonId || null,
        rating_eligible: ratingEligible,
        force,
        reason: force ? reason : null,
      });
      setEditing(false);
      await load(new AbortController().signal);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === 'object') {
        setIssues((e.body as { issues?: ValidationIssue[] }).issues ?? []);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    }
  };

  const submitVoid = async () => {
    if (!data) return;
    const voidReason = window.prompt('作废原因（罚符 / 中途结束 / 外部结算 / 人工修正…）');
    if (!voidReason) return;
    setError(null);
    try {
      await participantsApi.voidMatch(data.match.match_id, { reason: voidReason });
      await load(new AbortController().signal);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const match: Match | null = data?.match ?? null;

  // R10-F：触发天梯投影（ledger → 全量重放 → 快照发布）
  const projectSeason = async () => {
    if (!match?.season_id) return;
    setProjecting(true);
    setError(null);
    try {
      const result = await participantsApi.projectLadder(match.season_id);
      if (result.state === 'already_running') {
        setError('已有投影正在运行，稍后自动刷新');
      } else if (result.state === 'needs_rebuild') {
        setError('发布期间有新的账本写入，天梯将自动重算');
      } else if (result.state === 'error') {
        setError(result.reason ?? '天梯投影失败');
      }
      await load(new AbortController().signal);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProjecting(false);
    }
  };

  return (
    <PageShell maxWidth={860}>
      <PageHeader
        title={match ? `对局 ${match.match_id.slice(0, 12)}` : '对局'}
        actions={(
          <>
            <button style={ghostBtn} onClick={() => navigate(routes.matches)}>返回列表</button>
            {match && match.status !== 'void' && (
              <>
                <button style={primaryBtn} onClick={startEdit}>修订</button>
                <button style={{ ...ghostBtn, color: '#e74c3c' }} onClick={submitVoid}>作废</button>
              </>
            )}
          </>
        )}
      />

      {error && <div style={{ color: '#e74c3c', fontSize: 13, marginBottom: 10 }}>{error}</div>}

      {loading && !data && <div style={{ padding: 24, color: 'var(--text-muted)' }}>加载中…</div>}

      {match && !editing && <MatchDetailView match={match} accounts={accounts} />}

      {match && editing && (
        <div style={{ display: 'grid', gap: 14, marginBottom: 16 }}>
          <section style={cardStyle}>
            <div style={{ fontWeight: 800, marginBottom: 10 }}>修订阵容</div>
            <SeatPicker seats={seats} accounts={accounts} identities={identities} onChange={setSeats} />
          </section>
          <section style={cardStyle}>
            <div style={{ fontWeight: 800, marginBottom: 10 }}>修订分数</div>
            <ScoreEntryForm
              scores={scores}
              startingPoints={startingPoints}
              gameLength={gameLength}
              occurredAt={match.occurred_at}
              note={note}
              onChange={(patch) => {
                if (patch.scores) setScores(patch.scores);
                if (patch.startingPoints) setStartingPoints(patch.startingPoints);
                if (patch.gameLength) setGameLength(patch.gameLength);
                if (patch.note !== undefined) setNote(patch.note);
              }}
            />
          </section>
          {issues.length > 0 && (
            <ValidationNotice issues={issues} force={force} reason={reason} onForceChange={setForce} onReasonChange={setReason} />
          )}
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button style={ghostBtn} onClick={() => setEditing(false)}>取消</button>
            <button
              style={primaryBtn}
              onClick={submitRevise}
              disabled={seats.some((s) => !s.account_id) || scores.some((s) => !s.trim())}
            >
              提交修订
            </button>
          </div>
        </div>
      )}

      {data && (
        <section style={cardStyle}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>修订历史</div>
          <RevisionTimeline revisions={data.revisions} />
        </section>
      )}

      {match && match.status !== 'void' && (
        <section style={cardStyle}>
          <div style={{ fontWeight: 800, marginBottom: 8 }}>正式天梯计分</div>
          {editing ? (
            <div style={{ display: 'grid', gap: 10 }}>
              <div style={{ display: 'grid', gap: 4 }}>
                <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>赛季（season_id，空 = 仅账号统计）</label>
                <input
                  value={seasonId}
                  onChange={(e) => setSeasonId(e.target.value)}
                  placeholder="official-ladder-v1"
                  style={{
                    border: '1px solid var(--border)', background: 'var(--page-bg)',
                    color: 'var(--text-primary)', borderRadius: 4, padding: '6px 8px', fontSize: 13,
                  }}
                />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                <input type="checkbox" checked={ratingEligible} onChange={(e) => setRatingEligible(e.target.checked)} />
                纳入正式天梯计分（rating_eligible）
              </label>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                保存修订后：若已设赛季，账本会标记 dirty 并等待投影重算。
              </div>
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
              <div>
                赛季：<b>{match.season_id || '—'}</b>
                {match.season_id && match.rating_eligible ? ' · 纳入正式计分' : ''}
              </div>
              <div>
                投影状态：
                <b style={{ color: projectionColor(match.ladder_projection_state) }}>
                  {projectionLabel(match.ladder_projection_state)}
                </b>
              </div>
              {match.season_id && (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button
                    style={{ ...primaryBtn, background: '#16a085' }}
                    onClick={projectSeason}
                    disabled={projecting}
                  >
                    {projecting ? '天梯重算中…' : '触发天梯投影'}
                  </button>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    从 ledger 全量确定性重放并发布快照
                  </span>
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </PageShell>
  );
}

function projectionLabel(state?: string): string {
  switch (state) {
    case 'pending': return '天梯重算中…';
    case 'ready': return '已更新';
    case 'error': return '投影失败';
    default: return '未纳入';
  }
}

function projectionColor(state?: string): string {
  switch (state) {
    case 'pending': return '#e67e22';
    case 'ready': return '#27ae60';
    case 'error': return '#e74c3c';
    default: return 'var(--text-muted)';
  }
}

const cardStyle: React.CSSProperties = {
  background: 'var(--card-bg)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  padding: 14,
  marginBottom: 14,
};
const primaryBtn: React.CSSProperties = {
  border: '1px solid var(--accent)', background: 'var(--accent)', color: '#fff',
  borderRadius: 5, fontSize: 12, fontWeight: 700, padding: '7px 14px', cursor: 'pointer',
};
const ghostBtn: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
  borderRadius: 5, fontSize: 12, padding: '7px 14px', cursor: 'pointer',
};
