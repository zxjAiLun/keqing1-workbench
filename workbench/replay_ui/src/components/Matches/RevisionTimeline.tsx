// src/replay_ui/src/components/Matches/RevisionTimeline.tsx
import type { RevisionSummary } from '../../types/participants';

const ACTION_LABELS: Record<RevisionSummary['action'], string> = {
  create: '创建',
  revise: '修订',
  void: '作废',
};

export function RevisionTimeline({ revisions }: { revisions: RevisionSummary[] }) {
  return (
    <div style={{ display: 'grid', gap: 6 }}>
      {[...revisions].reverse().map((rev) => (
        <div
          key={rev.revision_id}
          style={{
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '8px 12px',
            fontSize: 12,
            display: 'grid',
            gap: 4,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontWeight: 800 }}>rev {rev.revision} · {ACTION_LABELS[rev.action]}</span>
            {rev.force && <span style={{ color: '#e74c3c', fontWeight: 700 }}>强制</span>}
            <span style={{ color: 'var(--text-muted)', marginLeft: 'auto' }}>{rev.created_at}</span>
          </div>
          {rev.reason && <div style={{ color: 'var(--text-secondary)' }}>原因：{rev.reason}</div>}
          {!rev.validation.passed && rev.validation.issues.length > 0 && (
            <div style={{ color: '#e74c3c' }}>
              {rev.validation.issues.map((issue) => issue.message).join('；')}
            </div>
          )}
        </div>
      ))}
      {revisions.length === 0 && <div style={{ color: 'var(--text-muted)', padding: 8 }}>无修订记录</div>}
    </div>
  );
}
