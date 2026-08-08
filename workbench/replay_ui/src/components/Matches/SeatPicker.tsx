// src/replay_ui/src/components/Matches/SeatPicker.tsx
import type { Account, ControllerType, MatchSeat, ModelIdentity } from '../../types/participants';
import { CONTROLLER_LABELS } from '../Participants/labels';
import { SEAT_WINDS } from './labels';

export function SeatPicker({
  seats,
  accounts,
  identities,
  onChange,
}: {
  seats: MatchSeat[];
  accounts: Account[];
  identities: ModelIdentity[];
  onChange: (seats: MatchSeat[]) => void;
}) {
  const update = (seat: number, patch: Partial<MatchSeat>) => {
    onChange(seats.map((s, i) => (i === seat ? { ...s, ...patch } : s)));
  };

  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {SEAT_WINDS.map((wind, seat) => {
        const current = seats[seat];
        const relevantIdentities = identities.filter(
          (m) => m.account_id === current.account_id || m.account_id == null,
        );
        const chosenIdentity = relevantIdentities.find((m) => m.model_identity_id === current.model_identity_id);
        const artifacts = chosenIdentity?.artifacts ?? [];
        return (
          <div key={seat} style={rowStyle}>
            <div style={{ width: 26, fontWeight: 800, color: 'var(--text-muted)' }}>{wind}</div>
            <select
              value={current.account_id}
              onChange={(e) => {
                const accountId = e.target.value;
                const acc = accounts.find((a) => a.account_id === accountId);
                update(seat, {
                  account_id: accountId,
                  controller_type: acc?.default_controller ?? null,
                  model_identity_id: null,
                  model_artifact_id: null,
                });
              }}
              style={selectStyle}
            >
              <option value="">选择账号…</option>
              {accounts.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.display_name}（{a.account_id}）{a.enabled ? '' : ' · 停用'}
                </option>
              ))}
            </select>
            <select
              value={current.controller_type ?? ''}
              onChange={(e) => update(seat, { controller_type: e.target.value as ControllerType | null })}
              style={selectStyle}
            >
              {Object.entries(CONTROLLER_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            {relevantIdentities.length > 0 && (
              <select
                value={current.model_identity_id ?? ''}
                onChange={(e) => update(seat, { model_identity_id: e.target.value || null, model_artifact_id: null })}
                style={selectStyle}
              >
                <option value="">无模型身份</option>
                {relevantIdentities.map((m) => <option key={m.model_identity_id} value={m.model_identity_id}>{m.label}</option>)}
              </select>
            )}
            {artifacts.length > 0 && (
              <select
                value={current.model_artifact_id ?? ''}
                onChange={(e) => update(seat, { model_artifact_id: e.target.value || null })}
                style={selectStyle}
              >
                <option value="">选择产物…</option>
                {artifacts.map((art) => (
                  <option key={art.model_artifact_id} value={art.model_artifact_id}>
                    {art.label}{art.is_current ? '（当前）' : ''}
                  </option>
                ))}
              </select>
            )}
          </div>
        );
      })}
    </div>
  );
}

const rowStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8,
  border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px',
};
const selectStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  padding: '5px 8px',
  fontSize: 13,
  minWidth: 0,
  flex: 1,
};
