// src/replay_ui/src/components/Participants/AccountTable.tsx
import type { Account, ModelIdentity } from '../../types/participants';
import { ACCOUNT_TYPE_LABELS, CONTROLLER_LABELS } from './labels';

export function AccountTable({
  accounts,
  identities,
  onEdit,
  onToggleEnabled,
  onDelete,
}: {
  accounts: Account[];
  identities: ModelIdentity[];
  onEdit: (account: Account) => void;
  onToggleEnabled: (account: Account) => void;
  onDelete: (account: Account) => void;
}) {
  const identityCount = (accountId: string) =>
    identities.filter((m) => m.account_id === accountId || m.account_id == null).length;

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>
          <th style={thStyle}>名称</th>
          <th style={thStyle}>ID</th>
          <th style={thStyle}>类型</th>
          <th style={thStyle}>控制器</th>
          <th style={thStyle}>状态</th>
          <th style={thStyle}>模型</th>
          <th style={thStyle}>操作</th>
        </tr>
      </thead>
      <tbody>
        {accounts.map((account) => (
          <tr key={account.account_id} style={{ borderBottom: '1px solid var(--border)' }}>
            <td style={tdStyle}>{account.display_name}</td>
            <td style={{ ...tdStyle, color: 'var(--text-muted)' }}>{account.account_id}</td>
            <td style={tdStyle}>{ACCOUNT_TYPE_LABELS[account.account_type]}</td>
            <td style={tdStyle}>{CONTROLLER_LABELS[account.default_controller]}</td>
            <td style={tdStyle}>{account.enabled ? '启用' : '停用'}</td>
            <td style={tdStyle}>{identityCount(account.account_id)}</td>
            <td style={{ ...tdStyle, display: 'flex', gap: 6 }}>
              <button style={linkBtn} onClick={() => onEdit(account)}>编辑</button>
              <button style={linkBtn} onClick={() => onToggleEnabled(account)}>
                {account.enabled ? '停用' : '启用'}
              </button>
              <button style={{ ...linkBtn, color: '#e74c3c' }} onClick={() => onDelete(account)}>删除</button>
            </td>
          </tr>
        ))}
        {accounts.length === 0 && (
          <tr>
            <td colSpan={7} style={{ ...tdStyle, color: 'var(--text-muted)', textAlign: 'center', padding: 24 }}>
              暂无参赛者
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: 'left',
  padding: '6px 8px',
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: 'var(--text-muted)',
  borderBottom: '1px solid var(--border)',
};
const tdStyle: React.CSSProperties = { padding: '7px 8px', verticalAlign: 'middle' };
const linkBtn: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  fontSize: 12,
  padding: '2px 8px',
  cursor: 'pointer',
};
