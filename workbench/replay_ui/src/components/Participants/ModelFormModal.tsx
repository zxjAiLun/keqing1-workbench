// src/replay_ui/src/components/Participants/ModelFormModal.tsx
import { useState } from 'react';
import type { ModelIdentityCreate } from '../../types/participants';
import { MODEL_PRESETS } from './modelPresets';
import { Modal } from './Modal';

export function ModelFormModal({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (payload: ModelIdentityCreate) => Promise<void>;
}) {
  const [label, setLabel] = useState('');
  const [kind, setKind] = useState<'local_model' | 'external_agent' | 'none'>('local_model');
  const [accountId, setAccountId] = useState('');
  const [artifactPath, setArtifactPath] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const applyPreset = (presetId: string) => {
    if (!presetId) return;
    const preset = MODEL_PRESETS.find((p) => p.id === presetId);
    if (!preset) return;
    setLabel(preset.label);
    setKind(preset.kind);
    setArtifactPath(preset.artifact_path);
  };

  const submit = async () => {
    if (!label.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({
        label: label.trim(),
        kind,
        account_id: accountId.trim() || null,
        artifact_path: artifactPath.trim() || null,
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title="新建模型身份" onClose={onClose}>
      <div style={{ display: 'grid', gap: 10 }}>
        <label style={labelStyle}>
          使用预置（可选）
          <select value="" onChange={(e) => applyPreset(e.target.value)} style={inputStyle}>
            <option value="">— 选择预置 —</option>
            {MODEL_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}{p.kind === 'external_agent' ? '（外部代理）' : ''}</option>
            ))}
          </select>
        </label>
        <label style={labelStyle}>
          名称
          <input value={label} onChange={(e) => setLabel(e.target.value)} style={inputStyle} autoFocus placeholder="如 Mortal 70k / Mortal 4.1b" />
        </label>
        <label style={labelStyle}>
          类型
          <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} style={inputStyle}>
            <option value="local_model">本地模型</option>
            <option value="external_agent">外部代理（External Mortal 用这个）</option>
            <option value="none">仅身份</option>
          </select>
        </label>
        <label style={labelStyle}>
          绑定账号（可留空 = 全局身份）
          <input value={accountId} onChange={(e) => setAccountId(e.target.value)} style={inputStyle} placeholder="account_id，如 70k@01" />
        </label>
        <label style={labelStyle}>
          checkpoint 路径（可选，自动建产物）
          <input value={artifactPath} onChange={(e) => setArtifactPath(e.target.value)} style={inputStyle} placeholder="artifacts/.../xxx.pth" />
        </label>
        {error && <div style={{ color: '#e74c3c', fontSize: 12 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
          <button style={btnStyle} onClick={onClose}>取消</button>
          <button style={{ ...btnStyle, background: 'var(--accent)', color: '#fff' }} onClick={submit} disabled={saving || !label.trim()}>
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
