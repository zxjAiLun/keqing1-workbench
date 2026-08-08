// src/replay_ui/src/components/Matches/ValidationNotice.tsx
import type { ValidationIssue } from '../../types/participants';
import { FORCE_REASON_OPTIONS } from './labels';

export function ValidationNotice({
  issues,
  force,
  reason,
  onForceChange,
  onReasonChange,
}: {
  issues: ValidationIssue[];
  force: boolean;
  reason: string;
  onForceChange: (force: boolean) => void;
  onReasonChange: (reason: string) => void;
}) {
  return (
    <div style={{
      border: '1px solid #e74c3c',
      background: 'rgba(231, 76, 60, 0.08)',
      borderRadius: 8,
      padding: 12,
      display: 'grid',
      gap: 8,
    }}>
      <div style={{ fontWeight: 800, color: '#e74c3c', fontSize: 13 }}>校验未通过</div>
      <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-secondary)', fontSize: 12 }}>
        {issues.map((issue) => (
          <li key={issue.code}>{issue.message}</li>
        ))}
      </ul>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
        <input type="checkbox" checked={force} onChange={(e) => onForceChange(e.target.checked)} />
        强制保存
      </label>
      {force && (
        <label style={{ display: 'grid', gap: 4, fontSize: 12, color: 'var(--text-secondary)' }}>
          原因
          <select value={reason} onChange={(e) => onReasonChange(e.target.value)} style={{
            border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
            borderRadius: 4, padding: '6px 8px', fontSize: 13,
          }}>
            {FORCE_REASON_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}
