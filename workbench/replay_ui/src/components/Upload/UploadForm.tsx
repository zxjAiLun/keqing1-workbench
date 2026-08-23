// src/replay_ui/src/components/Upload/UploadForm.tsx
import { useState, useRef } from 'react';
import type { BotType } from '../../types/bot';
import { GUI_BOT_CATALOG } from '../../utils/botCatalog';

interface UploadFormProps {
  onDataLoaded: (data: unknown) => void;
  onUploadStart?: () => void;
}

type InputType = 'tenhou_url' | 'mjai_json' | 'tenhou6_json';

const TENHOU6_JSON_MARKER = '#json=';

const INPUT_TYPE_TABS: Array<{ value: InputType; label: string }> = [
  { value: 'tenhou_url', label: '天凤链接' },
  { value: 'tenhou6_json', label: 'tenhou6 JSON' },
  { value: 'mjai_json', label: 'mjai JSONL' },
];

/** 各 Tab 的 textarea placeholder。 */
const TAB_PLACEHOLDERS: Record<InputType, string> = {
  tenhou_url: 'https://tenhou.net/0/?log=...&tw=0\n\n或每行一个：\nhttps://tenhou.net/6/#json=...',
  tenhou6_json: '{"name":[...],"rule":{...},"log":[...]}',
  mjai_json: '{"type":"start_game",...}\n{"type":"start_kyoku",...}\n{"type":"tsumo",...}',
};

/** 各 Tab 输入为空时的错误提示。 */
const TAB_EMPTY_ERRORS: Record<InputType, string> = {
  tenhou_url: '请输入天凤牌谱链接或 tenhou6 #json= 链接',
  tenhou6_json: '请上传 tenhou6 JSON 文件或粘贴 JSON',
  mjai_json: '请上传 mjai 文件或粘贴 JSONL',
};

/** 各 Tab 输入区下方的辅助说明。 */
const TAB_INPUT_HINTS: Record<InputType, string> = {
  tenhou_url:
    '支持普通天凤牌谱链接（从 tw= 读取默认视角）与 tenhou6 #json= 链接（每行一个，自动按 tenhou6 数据提交）。仅支持 tenhou.net。',
  tenhou6_json:
    '上传单个 .json 文件，或粘贴包含 log 数组的 tenhou6 JSON 对象。多条 #json= 链接请改用「天凤链接」Tab。',
  mjai_json:
    '上传单个 .json/.jsonl/.txt 文件，或粘贴每行一个事件的 mjai JSONL（兼容单个 JSON 数组）。',
};

/** JSON 类 Tab 的文件类型限制（与后端单文件语义一致）。 */
const JSON_TAB_FILE_ACCEPT: Record<'tenhou6_json' | 'mjai_json', string> = {
  tenhou6_json: '.json',
  mjai_json: '.json,.jsonl,.txt',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseTenhou6JsonPayload(payload: string): Record<string, unknown> {
  const trimmed = payload.trim();
  const candidates = [trimmed];
  try {
    const decoded = decodeURIComponent(trimmed);
    if (decoded !== trimmed) candidates.push(decoded);
  } catch {
    // Keep the raw fragment; malformed percent escapes will be reported as JSON errors below.
  }

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (isRecord(parsed) && Array.isArray(parsed.log)) return parsed;
    } catch {
      // Try the next representation.
    }
  }
  throw new Error('tenhou6 链接中的 json 不是有效牌谱对象');
}

function parseTenhou6JsonLinks(text: string): Record<string, unknown> | null {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const payloads = lines
    .map((line) => {
      const idx = line.indexOf(TENHOU6_JSON_MARKER);
      return idx >= 0 ? line.slice(idx + TENHOU6_JSON_MARKER.length) : null;
    })
    .filter((payload): payload is string => Boolean(payload?.trim()));

  if (payloads.length === 0) return null;
  if (payloads.length !== lines.length) {
    throw new Error('检测到 tenhou6 json 链接时，请每行只放一个 tenhou.net/6/#json=... 链接');
  }

  const parsed = payloads.map(parseTenhou6JsonPayload);
  if (parsed.length === 1) return parsed[0];

  const [first, ...rest] = parsed;
  return {
    ...first,
    log: [first, ...rest].flatMap((item) => Array.isArray(item.log) ? item.log : []),
  };
}

function parseTenhou6TextInput(text: string): Record<string, unknown> {
  const fromLinks = parseTenhou6JsonLinks(text);
  if (fromLinks) return fromLinks;
  const parsed = JSON.parse(text);
  if (!isRecord(parsed) || !Array.isArray(parsed.log)) {
    throw new Error('tenhou6 JSON 需要是包含 log 数组的对象');
  }
  return parsed;
}

function isTenhou6JsonLinkText(text: string): boolean {
  return text.includes(TENHOU6_JSON_MARKER);
}

// ---------------------------------------------------------------------------
// 外部 Review 链接校验（与后端 external_reports.resolve_external_report_url 对齐）
// ---------------------------------------------------------------------------
type ExternalReviewKind = 'naga' | 'mortal';

const EXTERNAL_REVIEW_RULES: Record<ExternalReviewKind, { host: string; label: string }> = {
  naga: { host: 'naga.dmv.nico', label: 'NAGA Review' },
  mortal: { host: 'mjai.ekyu.moe', label: 'Mortal Review' },
};

/** 返回 null 表示通过（空值 = 不导入外部报告），否则为字段级错误文案。 */
function validateExternalReviewUrl(kind: ExternalReviewKind, rawValue: string): string | null {
  const value = rawValue.trim();
  if (!value) return null;
  const { host, label } = EXTERNAL_REVIEW_RULES[kind];
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return `${label} 链接格式错误`;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return `${label} 链接必须是 http/https URL`;
  }
  if (parsed.hostname.toLowerCase() !== host) {
    return `${label} 链接必须来自 ${host}`;
  }
  // 结构校验与后端 external_reports.resolve_external_report_url 对齐
  if (kind === 'naga') {
    const hasReportId = Boolean(parsed.searchParams.get('report_id')?.trim());
    const hasReportPath = parsed.pathname.startsWith('/reports/') && parsed.pathname.endsWith('.json');
    if (!hasReportId && !hasReportPath) {
      return 'NAGA Review 链接中未找到 report_id 或有效报告路径';
    }
  } else {
    const hasDataPath = Boolean(parsed.searchParams.get('data')?.trim());
    const hasReportPath = parsed.pathname.startsWith('/report/') && parsed.pathname.endsWith('.json');
    if (!hasDataPath && !hasReportPath) {
      return 'Mortal Review 链接中未找到 data 报告路径';
    }
  }
  return null;
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** 表单错误：标注来源，便于区分输入校验、外部链接与服务器错误。 */
type FormError = { source: '输入' | '外部链接' | '服务器'; text: string };

const textareaStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 13,
  fontFamily: '"Menlo", "Consolas", monospace',
  resize: 'vertical',
  color: 'var(--text-primary)',
  background: 'var(--card-bg)',
  outline: 'none',
  boxSizing: 'border-box',
};

const inputHintStyle: React.CSSProperties = {
  marginTop: 6,
  fontSize: 11,
  lineHeight: 1.5,
  color: 'var(--text-muted)',
};

const inlineErrorStyle: React.CSSProperties = {
  marginTop: 6,
  fontSize: 12,
  lineHeight: 1.5,
  color: 'var(--error)',
};

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: 'var(--text-secondary)',
  display: 'block',
};

function FieldHint({ children }: { children: React.ReactNode }) {
  return <div style={inputHintStyle}>{children}</div>;
}

// ---------------------------------------------------------------------------
// 子组件：天凤链接输入
// ---------------------------------------------------------------------------
function TenhouUrlInput({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={TAB_PLACEHOLDERS.tenhou_url}
        disabled={disabled}
        style={{ ...textareaStyle, height: 88 }}
      />
      <FieldHint>{TAB_INPUT_HINTS.tenhou_url}</FieldHint>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子组件：JSON 输入（上传文件 / 粘贴文本，二选一）
// ---------------------------------------------------------------------------
function JsonReplayInput({
  text,
  onTextChange,
  files,
  onFilesChange,
  placeholder,
  hint,
  fileAccept,
  conflict,
  disabled,
}: {
  text: string;
  onTextChange: (v: string) => void;
  files: File[];
  onFilesChange: (files: File[]) => void;
  placeholder: string;
  hint: string;
  fileAccept: string;
  conflict: boolean;
  disabled?: boolean;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: File[]) => {
    const next = incoming.find(Boolean);
    onFilesChange(next ? [next] : []);
  };

  const removeFile = (name: string) => {
    if (disabled) return;
    onFilesChange(files.filter(p => p.name !== name));
    // 同时清空原生 file input 的值，确保移除后可以重新选择同一个文件
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  return (
    <div>
      {/* 拖拽上传区（单文件） */}
      <div
        onClick={() => { if (!disabled) fileInputRef.current?.click(); }}
        onDragOver={e => e.preventDefault()}
        onDrop={e => { e.preventDefault(); if (!disabled) addFiles(Array.from(e.dataTransfer.files)); }}
        style={{
          border: '2px dashed var(--border)',
          borderRadius: 8,
          padding: 16,
          textAlign: 'center',
          cursor: disabled ? 'not-allowed' : 'pointer',
          color: 'var(--text-muted)',
          fontSize: 13,
          marginBottom: 8,
          background: 'var(--page-bg)',
          transition: 'border-color 0.2s, background 0.2s',
          opacity: disabled ? 0.6 : 1,
        }}
      >
        选择文件（单文件）
        <input
          ref={fileInputRef}
          type="file"
          accept={fileAccept}
          disabled={disabled}
          style={{ display: 'none' }}
          onChange={e => addFiles(Array.from(e.target.files || []))}
        />
      </div>

      {/* 已选文件卡片：文件名 + 大小 + 移除按钮 */}
      {files.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {files.map(f => (
            <div key={f.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'var(--page-bg)', border: '1px solid var(--border)', borderRadius: 999, padding: '4px 10px', fontSize: 12, color: 'var(--text-secondary)' }}>
              📄 {f.name}（{formatFileSize(f.size)}）
              <button
                type="button"
                title="移除文件"
                aria-label={`移除文件 ${f.name}`}
                onClick={() => removeFile(f.name)}
                disabled={disabled}
                style={{
                  border: 'none',
                  background: 'none',
                  padding: 0,
                  fontSize: 13,
                  lineHeight: 1,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  color: 'var(--text-muted)',
                  fontWeight: 'bold',
                }}
              >×</button>
            </div>
          ))}
        </div>
      )}

      {/* 文本粘贴区 */}
      <textarea
        value={text}
        onChange={e => onTextChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        style={{ ...textareaStyle, height: 110 }}
      />
      <FieldHint>{hint}</FieldHint>
      {conflict && (
        <div role="alert" style={inlineErrorStyle}>
          已同时选择文件与粘贴文本：请只保留上传文件或粘贴文本中的一种输入。
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 子组件：外部 Review（可选高级功能）
// ---------------------------------------------------------------------------
function ExternalReviewFields({
  nagaUrl,
  mortalUrl,
  onNagaChange,
  onMortalChange,
  disabled,
}: {
  nagaUrl: string;
  mortalUrl: string;
  onNagaChange: (v: string) => void;
  onMortalChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <details
      style={{
        border: '1px solid var(--border)',
        borderRadius: 8,
        background: 'var(--page-bg)',
        padding: '10px 12px',
      }}
    >
      <summary style={{ cursor: 'pointer', display: 'grid', gap: 3, listStyle: 'none' }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>外部 Review（可选）</span>
        <span style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)' }}>
          导入与当前牌谱和视角对应的外部 Review，作为额外 teacher overlay 与本地模型结果对比。
        </span>
      </summary>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10, marginTop: 10 }}>
        <label style={externalLinkLabelStyle}>
          <span>NAGA Review</span>
          <input
            type="url"
            value={nagaUrl}
            onChange={(event) => onNagaChange(event.target.value)}
            placeholder="https://naga.dmv.nico/...?report_id=..."
            disabled={disabled}
            style={externalLinkInputStyle}
          />
          <FieldHint>
            支持 naga.dmv.nico 的 Review 页面链接；需对应当前牌谱与玩家视角；后端解析 report_id 并下载报告（主要提供动作概率，不一定包含 Q 值）。
          </FieldHint>
        </label>
        <label style={externalLinkLabelStyle}>
          <span>Mortal 4.1c Review</span>
          <input
            type="url"
            value={mortalUrl}
            onChange={(event) => onMortalChange(event.target.value)}
            placeholder="https://mjai.ekyu.moe/.../?data=/report/xxxx.json"
            disabled={disabled}
            style={externalLinkInputStyle}
          />
          <FieldHint>
            支持 mjai.ekyu.moe 的 Review 页面或报告链接；需对应当前牌谱与玩家视角；后端解析 data=/report/...json（评分字段以报告内容为准）。
          </FieldHint>
        </label>
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// 子组件：本地 Mortal 模型选择
// ---------------------------------------------------------------------------
function ModelSelector({
  selectedModels,
  onToggle,
  onSelectAll,
  onClear,
  disabled,
}: {
  selectedModels: BotType[];
  onToggle: (model: BotType) => void;
  onSelectAll: () => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <label style={sectionLabelStyle}>本地 Mortal 模型</label>
        <span style={{ display: 'inline-flex', gap: 10 }}>
          <button type="button" onClick={onSelectAll} disabled={disabled} style={miniActionStyle}>
            全选
          </button>
          <button type="button" onClick={onClear} disabled={disabled} style={miniActionStyle}>
            清空
          </button>
        </span>
      </div>
      <FieldHint>选择一个或多个 checkpoint 生成并排 teacher overlay。</FieldHint>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 6, marginTop: 6 }}>
        {GUI_BOT_CATALOG.map((bot) => {
          const active = selectedModels.includes(bot.value);
          return (
            <button
              key={bot.value}
              type="button"
              onClick={() => onToggle(bot.value)}
              disabled={disabled}
              style={{
                minHeight: 36,
                padding: '7px 9px',
                borderRadius: 7,
                border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
                background: active ? 'var(--accent-bg)' : 'var(--card-bg)',
                color: active ? 'var(--accent)' : 'var(--text-primary)',
                cursor: disabled ? 'not-allowed' : 'pointer',
                textAlign: 'left',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: 12, fontWeight: 800 }}>{bot.label}</span>
                {active && <span style={{ fontSize: 10 }}>已选</span>}
              </div>
              <div style={{ marginTop: 4 }}>
                <span
                  style={{
                    display: 'inline-block',
                    border: `1px solid ${active ? 'var(--accent-border)' : 'var(--border)'}`,
                    borderRadius: 4,
                    padding: '1px 5px',
                    fontSize: 10,
                    fontWeight: 700,
                    color: active ? 'var(--accent)' : 'var(--text-muted)',
                  }}
                >
                  {bot.badge}
                </span>
              </div>
              <div style={{ marginTop: 4, fontSize: 11, lineHeight: 1.45, color: active ? 'var(--accent)' : 'var(--text-muted)' }}>
                {bot.description}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------
export function UploadForm({ onDataLoaded, onUploadStart }: UploadFormProps) {
  const [inputType, setInputType] = useState<InputType>('tenhou_url');
  const [tenhouUrl, setTenhouUrl] = useState('');
  const [mjaiText, setMjaiText]   = useState('');
  const [tenhou6Text, setTenhou6Text] = useState('');
  // 文件状态按 Tab 隔离：tenhou6 文件只属于 tenhou6 Tab，mjai 文件只属于 mjai Tab
  const [tenhou6Files, setTenhou6Files] = useState<File[]>([]);
  const [mjaiFiles, setMjaiFiles] = useState<File[]>([]);
  const [playerId, setPlayerId]   = useState<string>('auto');
  const [selectedModels, setSelectedModels] = useState<BotType[]>(['ext_mortal', '70k', 'mortal']);
  const [nagaUrl, setNagaUrl] = useState('');
  const [mortalUrl, setMortalUrl] = useState('');
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<FormError | null>(null);
  const [success, setSuccess]     = useState<string | null>(null);

  const activeText = inputType === 'tenhou_url' ? tenhouUrl : inputType === 'tenhou6_json' ? tenhou6Text : mjaiText;
  // 当前 Tab 的文件（天凤链接 Tab 不携带任何文件）
  const activeFiles = inputType === 'tenhou6_json' ? tenhou6Files : inputType === 'mjai_json' ? mjaiFiles : [];
  const tenhouUrlIsTenhou6Json = inputType === 'tenhou_url' && isTenhou6JsonLinkText(tenhouUrl);
  const playerIdValue = tenhouUrlIsTenhou6Json && playerId === 'auto' ? '0' : playerId;
  // 后端同时收到文件和文本时只读取文件，前端按 Tab 将二者限制为互斥（只检查当前 Tab）
  const sourceConflict = inputType !== 'tenhou_url' && activeFiles.length > 0 && activeText.trim().length > 0;

  // 切换输入类型时重置视角默认值与错误，但保留各 Tab 已输入的文本与已选文件
  const switchInputType = (t: InputType) => {
    setInputType(t);
    setPlayerId(t === 'tenhou_url' ? 'auto' : '1');
    setError(null);
    setSuccess(null);
  };

  const toggleModel = (model: BotType) => {
    setSelectedModels((current) =>
      current.includes(model)
        ? current.filter((item) => item !== model)
        : [...current, model],
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    const text = activeText.trim();
    const hasSource = inputType === 'tenhou_url'
      ? text.length > 0
      : text.length > 0 || activeFiles.length > 0;
    if (!hasSource) {
      setError({ source: '输入', text: TAB_EMPTY_ERRORS[inputType] });
      return;
    }
    if (sourceConflict) {
      setError({ source: '输入', text: '请只保留上传文件或粘贴文本中的一种输入。' });
      return;
    }
    if (selectedModels.length === 0) {
      setError({ source: '输入', text: '请至少选择一个本地 Mortal 模型' });
      return;
    }
    const nagaError = validateExternalReviewUrl('naga', nagaUrl);
    if (nagaError) {
      setError({ source: '外部链接', text: nagaError });
      return;
    }
    const mortalError = validateExternalReviewUrl('mortal', mortalUrl);
    if (mortalError) {
      setError({ source: '外部链接', text: mortalError });
      return;
    }

    setLoading(true);
    setError(null);
    setSuccess(null);
    onUploadStart?.();

    let formData: FormData;
    try {
      formData = new FormData();

      if (inputType === 'tenhou_url') {
        const linkedTenhou6 = parseTenhou6JsonLinks(text);
        if (linkedTenhou6) {
          formData.append('json_text', JSON.stringify(linkedTenhou6));
          formData.append('input_type', 'tenhou6');
        } else {
          formData.append('json_text', text);
          formData.append('input_type', 'url');
        }
      } else if (inputType === 'tenhou6_json') {
        if (tenhou6Files.length === 0) {
          let data: Record<string, unknown>;
          try {
            data = parseTenhou6TextInput(text);
          } catch {
            throw new Error('tenhou6 JSON 格式错误，请检查输入');
          }
          formData.append('json_text', JSON.stringify(data));
        }
        formData.append('input_type', 'tenhou6');
        for (const f of tenhou6Files) formData.append('files', f);
      } else {
        if (mjaiFiles.length === 0) {
          const lines = text.split('\n').filter(l => l.trim());
          let events;
          try {
            if (lines.length > 1 || !text.includes('"type"')) {
              events = lines.map(l => JSON.parse(l));
            } else {
              events = JSON.parse(text);
            }
          } catch {
            throw new Error('JSON 格式错误，请检查输入');
          }
          formData.append('json_text', JSON.stringify(events));
        }
        formData.append('input_type', 'mjai');
        for (const f of mjaiFiles) formData.append('files', f);
      }

      if (playerIdValue !== 'auto') formData.append('player_id', playerIdValue);
      for (const model of selectedModels) {
        formData.append('model_types', model);
      }
      if (nagaUrl.trim()) formData.append('naga_url', nagaUrl.trim());
      if (mortalUrl.trim()) formData.append('mortal_url', mortalUrl.trim());
    } catch (err) {
      // 本地解析失败属于输入校验错误
      setError({ source: '输入', text: err instanceof Error ? err.message : String(err) });
      setLoading(false);
      return;
    }

    try {
      const res = await fetch('/api/replay/multi-teacher', { method: 'POST', body: formData });
      if (!res.ok) {
        const errBody: unknown = await res.json().catch(() => null);
        const message = isRecord(errBody) && typeof errBody.error === 'string'
          ? errBody.error
          : `请求失败: ${res.status}`;
        throw new Error(message);
      }

      const data = await res.json();
      onDataLoaded(data);
      const stepCount = isRecord(data) && Array.isArray(data.log) ? data.log.length : 0;
      setSuccess(`回放加载成功！共 ${stepCount} 步`);
    } catch (err) {
      setError({ source: '服务器', text: err instanceof Error ? err.message : String(err) });
    } finally {
      setLoading(false);
    }
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: '7px 20px',
    fontSize: 13,
    fontWeight: active ? 600 : 400,
    color: active ? 'var(--accent-text)' : 'var(--text-secondary)',
    background: active ? 'var(--accent)' : 'transparent',
    border: '1px solid',
    borderColor: active ? 'var(--accent)' : 'var(--border)',
    borderRadius: 8,
    cursor: loading ? 'not-allowed' : 'pointer',
    transition: 'all 0.15s',
  });

  const selectStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 12px',
    border: '1px solid var(--border)',
    borderRadius: 8,
    fontSize: 14,
    background: 'var(--card-bg)',
    color: 'var(--text-primary)',
  };

  const submitDisabled = loading || sourceConflict;

  return (
    <form onSubmit={handleSubmit}>
      {/* 输入类型 Tab */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {INPUT_TYPE_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            disabled={loading}
            style={tabStyle(inputType === tab.value)}
            onClick={() => switchInputType(tab.value)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 输入区域 */}
      <div style={{ marginBottom: 14 }}>
        {inputType === 'tenhou_url' && <TenhouUrlInput value={tenhouUrl} onChange={setTenhouUrl} disabled={loading} />}
        {inputType === 'tenhou6_json' && (
          <JsonReplayInput
            text={tenhou6Text}
            onTextChange={setTenhou6Text}
            files={tenhou6Files}
            onFilesChange={setTenhou6Files}
            placeholder={TAB_PLACEHOLDERS.tenhou6_json}
            hint={TAB_INPUT_HINTS.tenhou6_json}
            fileAccept={JSON_TAB_FILE_ACCEPT.tenhou6_json}
            conflict={sourceConflict}
            disabled={loading}
          />
        )}
        {inputType === 'mjai_json' && (
          <JsonReplayInput
            text={mjaiText}
            onTextChange={setMjaiText}
            files={mjaiFiles}
            onFilesChange={setMjaiFiles}
            placeholder={TAB_PLACEHOLDERS.mjai_json}
            hint={TAB_INPUT_HINTS.mjai_json}
            fileAccept={JSON_TAB_FILE_ACCEPT.mjai_json}
            conflict={sourceConflict}
            disabled={loading}
          />
        )}
      </div>

      {/* 本地 Mortal 模型 */}
      <div style={{ marginBottom: 14 }}>
        <ModelSelector
          selectedModels={selectedModels}
          onToggle={toggleModel}
          onSelectAll={() => setSelectedModels(GUI_BOT_CATALOG.map((bot) => bot.value))}
          onClear={() => setSelectedModels([])}
          disabled={loading}
        />
      </div>

      {/* 底部参数行：视角座位 + 提交 */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 14 }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <label style={{ ...sectionLabelStyle, marginBottom: 6 }}>视角座位</label>
          <select value={playerIdValue} onChange={e => setPlayerId(e.target.value)} disabled={loading} style={selectStyle}>
            {inputType === 'tenhou_url' && !tenhouUrlIsTenhou6Json && <option value="auto">自动（读取链接中的 tw=）</option>}
            <option value="0">东家（0）</option>
            <option value="1">南家（1）</option>
            <option value="2">西家（2）</option>
            <option value="3">北家（3）</option>
          </select>
          {inputType === 'tenhou_url' && !tenhouUrlIsTenhou6Json && (
            <FieldHint>链接未提供 tw= 时由后端使用默认视角。</FieldHint>
          )}
          {tenhouUrlIsTenhou6Json && (
            <FieldHint>检测到 #json= 链接：自动按 tenhou6 数据提交；未手动选择时以东家（0）视角提交。</FieldHint>
          )}
        </div>

        <button
          type="submit"
          disabled={submitDisabled}
          style={{
            height: 40,
            padding: '0 28px',
            background: 'var(--btn-primary-bg)',
            color: 'var(--btn-primary-text)',
            border: 'none',
            borderRadius: 8,
            fontSize: 15,
            fontWeight: 600,
            cursor: submitDisabled ? 'not-allowed' : 'pointer',
            opacity: submitDisabled ? 0.6 : 1,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            whiteSpace: 'nowrap',
          }}
        >
          {loading ? (
            <><span style={{ display: 'inline-block', width: 16, height: 16, border: '2px solid rgba(255,255,255,0.4)', borderTopColor: 'currentColor', borderRadius: '50%', animation: 'spin 0.7s linear infinite' }} />正在生成 Review…</>
          ) : '创建 Review'}
        </button>
      </div>

      {/* 外部 Review（可选高级功能，次级视觉层级） */}
      <ExternalReviewFields
        nagaUrl={nagaUrl}
        mortalUrl={mortalUrl}
        onNagaChange={setNagaUrl}
        onMortalChange={setMortalUrl}
        disabled={loading}
      />

      {error && (
        <div role="alert" style={{ marginTop: 10, fontSize: 13, color: 'var(--error)' }}>
          <span style={{ fontWeight: 700 }}>{error.source}错误：</span>
          {error.text}
        </div>
      )}
      {success && <div style={{ marginTop: 10, fontSize: 13, color: 'var(--success)' }}>{success}</div>}
    </form>
  );
}

const miniActionStyle: React.CSSProperties = {
  border: 'none',
  background: 'none',
  padding: 0,
  fontSize: 11,
  fontWeight: 700,
  color: 'var(--accent)',
  cursor: 'pointer',
};

const externalLinkLabelStyle: React.CSSProperties = {
  display: 'grid',
  gap: 5,
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 700,
};

const externalLinkInputStyle: React.CSSProperties = {
  width: '100%',
  height: 34,
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '0 9px',
  background: 'var(--card-bg)',
  color: 'var(--text-primary)',
  fontFamily: 'Menlo, Consolas, monospace',
  fontSize: 11,
  boxSizing: 'border-box',
};
