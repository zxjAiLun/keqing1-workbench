// src/replay_ui/src/components/Matches/MatchTable.tsx
import type { Account, Match } from '../../types/participants';
import { SEAT_WINDS } from './labels';
import { SOURCE_LABELS } from './labels';

export function MatchTable({
  matches,
  accounts,
  onOpen,
}: {
  matches: Match[];
  accounts: Account[];
  onOpen: (match: Match) => void;
}) {
  const nameOf = (accountId: string) =>
    accounts.find((a) => a.account_id === accountId)?.display_name ?? accountId;

  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {matches.map((match) => (
        <div
          key={match.match_id}
          onClick={() => onOpen(match)}
          style={{
            display: 'grid',
            gridTemplateColumns: '150px 60px 1fr 180px 90px 90px auto',
            gap: 8,
            alignItems: 'center',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '8px 12px',
            cursor: 'pointer',
            background: 'var(--card-bg)',
          }}
        >
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{match.occurred_at.slice(0, 16).replace('T', ' ')}</div>
          <div style={{ fontSize: 12, fontWeight: 700 }}>{match.game_length === 'hanchan' ? '半庄' : '东风'}</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, auto)', gap: 8, fontSize: 12 }}>
            {match.seats.map((seat) => (
              <span key={seat.seat}>
                {SEAT_WINDS[seat.seat]} {nameOf(seat.account_id)}
              </span>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, auto)', gap: 8, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
            {match.final_scores.map((score, i) => (
              <span key={i} title={`${SEAT_WINDS[i]}家 ${match.ranks[i] + 1}位`}>
                {score.toLocaleString()}
              </span>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, auto)', gap: 4, fontSize: 11, color: 'var(--text-muted)' }}>
            {match.ranks.map((rank, i) => (
              <span key={i}>{SEAT_WINDS[i]}{rank + 1}位</span>
            ))}
          </div>
          <div style={{ fontSize: 12, color: match.status === 'void' ? '#e74c3c' : 'var(--text-secondary)' }}>
            {SOURCE_LABELS[match.source]}{match.status === 'void' ? ' · 作废' : ''}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>rev {match.revision}</div>
        </div>
      ))}
      {matches.length === 0 && (
        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-muted)' }}>暂无对局</div>
      )}
    </div>
  );
}
