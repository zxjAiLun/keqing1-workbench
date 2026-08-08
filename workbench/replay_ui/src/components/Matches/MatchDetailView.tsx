// src/replay_ui/src/components/Matches/MatchDetailView.tsx
import type { Account, Match } from '../../types/participants';
import { SEAT_WINDS } from './labels';
import { SOURCE_LABELS } from './labels';

export function MatchDetailView({ match, accounts }: { match: Match; accounts: Account[] }) {
  const nameOf = (accountId: string) =>
    accounts.find((a) => a.account_id === accountId)?.display_name ?? accountId;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
        <span>{match.match_id}</span>
        <span>·</span>
        <span>{match.game_length === 'hanchan' ? '半庄' : '东风'}</span>
        <span>·</span>
        <span>{SOURCE_LABELS[match.source]}</span>
        {match.source_ref && <span>· {match.source_ref}</span>}
        {match.status === 'void' && <span style={{ color: '#e74c3c' }}>· 已作废：{match.void_reason}</span>}
      </div>

      <div style={{ display: 'grid', gap: 6 }}>
        {match.seats.map((seat) => (
          <div
            key={seat.seat}
            style={{
              display: 'grid',
              gridTemplateColumns: '40px 1fr 120px 120px 100px',
              gap: 8,
              alignItems: 'center',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: '8px 12px',
              fontSize: 13,
            }}
          >
            <span style={{ fontWeight: 800, color: 'var(--text-muted)' }}>{SEAT_WINDS[seat.seat]}</span>
            <span style={{ fontWeight: 700 }}>{nameOf(seat.account_id)}</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{seat.controller_type}</span>
            <span style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
              {match.final_scores[seat.seat]?.toLocaleString()}
            </span>
            <span style={{ fontSize: 12, fontWeight: 700 }}>{match.ranks[seat.seat] + 1}位</span>
          </div>
        ))}
      </div>

      {match.note && <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>备注：{match.note}</div>}
    </div>
  );
}
