// src/replay_ui/src/components/Participants/AccountFormModal.tsx
import { useState } from 'react';
import type { Account, AccountCreate, AccountType, ControllerType } from '../../types/participants';
import { Modal } from './Modal';
import { ACCOUNT_TYPE_LABELS, CONTROLLER_LABELS } from './labels';

const ACCOUNT_TYPES: AccountType[] = ['human', 'managed_bot', 'external_bot'];
const CONTROLLERS: ControllerType[] = ['human_ui', 'local_model', 'external_agent', 'manual_only'];

export function AccountFormModal({
  account,
  onClose,
  onSave,
}: {
  account?: Account | null;
  onClose: () => void;
  onSave: (payload: AccountCreate) => Promise<void>;
}) {
  const [displayName, setDisplayName] = useState(account?.display_name ?? '');
  const [accountType, setAccountType] = useState<AccountType>(account?.account_type ?? 'human');
  const [controller, setController] = useState<ControllerType>(account?.default_controller ?? 'human_ui');
  const [note, setNote] = useState(account?.note ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!displayName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({ display_name: displayName.trim(), account_type: accountType, default_controller: controller, note: note || null });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={account ? `编辑 ${account.display_name}` : '新建参赛者'} onClose={onClose}>
      <div style={{ display: 'grid', gap: 10 }}>
        <label style={labelStyle}>
          显示名称
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} style={inputStyle} autoFocus />
        </label>
        <label style={labelStyle}>
          账号类型
          <select value={accountType} onChange={(e) => setAccountType(e.target.value as AccountType)} style={inputStyle}>
            {ACCOUNT_TYPES.map((t) => <option key={t} value={t}>{ACCOUNT_TYPE_LABELS[t]}</option>)}
          </select>
        </label>
        <label style={labelStyle}>
          默认控制器
          <select value={controller} onChange={(e) => setController(e.target.value as ControllerType)} style={inputStyle}>
            {CONTROLLERS.map((c) => <option key={c} value={c}>{CONTROLLER_LABELS[c]}</option>)}
          </select>
        </label>
        <label style={labelStyle}>
          备注
          <input value={note} onChange={(e) => setNote(e.target.value)} style={inputStyle} placeholder="可选" />
        </label>
        {error && <div style={{ color: '#e74c3c', fontSize: 12 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
          <button style={btnStyle} onClick={onClose}>取消</button>
          <button style={{ ...btnStyle, background: 'var(--accent)', color: '#fff' }} onClick={submit} disabled={saving || !displayName.trim()}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

const labelStyle: React.CSSProperties = { display: 'grid', gap: 4, fontSize: 12, color: 'var(--text-secondary)' };
const inputStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--page-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  padding: '6px 8px',
  fontSize: 13,
};
const btnStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  background: 'var(--card-bg)',
  color: 'var(--text-primary)',
  borderRadius: 4,
  fontSize: 13,
  padding: '6px 14px',
  cursor: 'pointer',
};
