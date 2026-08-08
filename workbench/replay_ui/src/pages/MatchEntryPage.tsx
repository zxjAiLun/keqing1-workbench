// src/replay_ui/src/pages/MatchEntryPage.tsx
// R10：手动录入对局 —— 4 座选择 + 最终分数 + force-save。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { SeatPicker } from '../components/Matches/SeatPicker';
import { EMPTY_SEATS } from '../components/Matches/labels';
import { ScoreEntryForm } from '../components/Matches/ScoreEntryForm';
import { ValidationNotice } from '../components/Matches/ValidationNotice';
import { participantsApi } from '../api/participantsApi';
import { ApiError } from '../api/replayApi';
import { routes } from '../routes';
import type { Account, GameLength, MatchSeat, ModelIdentity, ValidationIssue } from '../types/participants';

function localDateTimeValue(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function MatchEntryPage() {
  const navigate = useNavigate();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [identities, setIdentities] = useState<ModelIdentity[]>([]);
  const [seats, setSeats] = useState<MatchSeat[]>(EMPTY_SEATS);
  const [scores, setScores] = useState<string[]>(['', '', '', '']);
  const [startingPoints, setStartingPoints] = useState(25000);
  const [gameLength, setGameLength] = useState<GameLength>('hanchan');
  const [occurredAt, setOccurredAt] = useState(localDateTimeValue());
  const [note, setNote] = useState('');
  const [force, setForce] = useState(false);
  const [reason, setReason] = useState('');
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal: AbortSignal) => {
    try {
      const [accountsResp, modelsResp] = await Promise.all([
        participantsApi.listAccounts(signal),
        participantsApi.listModels(signal),
      ]);
      setAccounts(accountsResp.accounts);
      setIdentities(modelsResp.identities);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const submit = async () => {
    const finalScores = scores.map((s) => parseInt(s, 10));
    setSubmitting(true);
    setError(null);
    setIssues([]);
    try {
      const payload = {
        occurred_at: occurredAt ? new Date(occurredAt).toISOString() : new Date().toISOString(),
        game_length: gameLength,
        starting_points: startingPoints,
        source: 'manual' as const,
        note: note || null,
        seats,
        final_scores: finalScores,
        force,
        reason: force ? reason : null,
      };
      const resp = await participantsApi.createMatch(payload);
      navigate(routes.matchDetail(resp.match.match_id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.body && typeof e.body === 'object') {
        const detail = e.body as { issues?: ValidationIssue[] };
        setIssues(detail.issues ?? []);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageShell maxWidth={860}>
      <PageHeader
        title="录入对局"
        description="选择四家账号并录入最终点数，系统自动计算顺位"
        actions={(
          <button
            onClick={submit}
            disabled={submitting || seats.some((s) => !s.account_id) || scores.some((s) => !s.trim())}
            style={{
              border: '1px solid var(--accent)', background: 'var(--accent)', color: '#fff',
              borderRadius: 6, fontSize: 13, fontWeight: 700, padding: '8px 16px', cursor: 'pointer',
            }}
          >
            {submitting ? '保存中…' : '保存对局'}
          </button>
        )}
      />

      <div style={{ display: 'grid', gap: 16, paddingBottom: 24 }}>
        <section style={cardStyle}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>四家阵容</div>
          <SeatPicker seats={seats} accounts={accounts} identities={identities} onChange={setSeats} />
        </section>

        <section style={cardStyle}>
          <div style={{ fontWeight: 800, marginBottom: 10 }}>比赛信息与最终分数</div>
          <ScoreEntryForm
            scores={scores}
            startingPoints={startingPoints}
            gameLength={gameLength}
            occurredAt={occurredAt}
            note={note}
            onChange={(patch) => {
              if (patch.scores) setScores(patch.scores);
              if (patch.startingPoints) setStartingPoints(patch.startingPoints);
              if (patch.gameLength) setGameLength(patch.gameLength);
              if (patch.occurredAt !== undefined) setOccurredAt(patch.occurredAt);
              if (patch.note !== undefined) setNote(patch.note);
            }}
          />
        </section>

        {issues.length > 0 && (
          <ValidationNotice
            issues={issues}
            force={force}
            reason={reason}
            onForceChange={setForce}
            onReasonChange={setReason}
          />
        )}
        {error && <div style={{ color: '#e74c3c', fontSize: 13 }}>{error}</div>}
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
