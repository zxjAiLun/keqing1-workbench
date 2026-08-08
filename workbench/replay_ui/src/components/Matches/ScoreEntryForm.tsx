// src/replay_ui/src/components/Matches/ScoreEntryForm.tsx
import type { GameLength } from '../../types/participants';
import { SEAT_WINDS } from './labels';

export function ScoreEntryForm({
  scores,
  startingPoints,
  gameLength,
  occurredAt,
  note,
  onChange,
}: {
  scores: string[];
  startingPoints: number;
  gameLength: GameLength;
  occurredAt: string;
  note: string;
  onChange: (patch: Partial<{ scores: string[]; startingPoints: number; gameLength: GameLength; occurredAt: string; note: string }>) => void;
}) {
  const parsed = scores.map((s) => parseInt(s, 10));
  const sum = parsed.filter((n) => Number.isFinite(n)).reduce((a, b) => a + b, 0);
  const expected = 4 * startingPoints;
  const sumOk = parsed.length === 4 && parsed.every((n) => Number.isFinite(n)) && sum === expected;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <label style={labelStyle}>
          比赛时间
          <input
            type="datetime-local"
            value={occurredAt}
            onChange={(e) => onChange({ occurredAt: e.target.value })}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          东风 / 半庄
          <select value={gameLength} onChange={(e) => onChange({ gameLength: e.target.value as GameLength })} style={inputStyle}>
            <option value="hanchan">半庄</option>
            <option value="tonpu">东风</option>
          </select>
        </label>
        <label style={labelStyle}>
          起始点数
          <input
            type="number"
            value={startingPoints}
            onChange={(e) => onChange({ startingPoints: parseInt(e.target.value, 10) || 25000 })}
            style={inputStyle}
          />
        </label>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
        {SEAT_WINDS.map((wind, i) => (
          <label key={wind} style={labelStyle}>
            {wind}家
            <input
              type="number"
              value={scores[i] ?? ''}
              onChange={(e) => {
                const next = [...scores];
                next[i] = e.target.value;
                onChange({ scores: next });
              }}
              style={inputStyle}
              placeholder="最终点数"
            />
          </label>
        ))}
      </div>

      <div style={{ fontSize: 12, color: sumOk ? 'var(--text-secondary)' : (parsed.length > 0 && sum !== expected ? '#e74c3c' : 'var(--text-muted)') }}>
        总分 {Number.isFinite(sum) ? sum : '—'} / 期望 {expected} {sumOk ? '✓' : ''}
      </div>

      <label style={labelStyle}>
        备注
        <input value={note} onChange={(e) => onChange({ note: e.target.value })} style={inputStyle} placeholder="可选" />
      </label>
    </div>
  );
}

const labelStyle: React.CSSProperties = { display: 'grid', gap: 4, fontSize: 12, color: 'var(--text-secondary)', flex: 1 };
const inputStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  padding: '6px 8px',
  fontSize: 13,
  width: '100%',
  boxSizing: 'border-box',
};
