// src/replay_ui/src/pages/TeacherReportsPage.tsx
//
// 牌谱积累：已归档的外部教师报告（mjai.ekyu.moe / naga）。
//
// 页面只做「看得见」：列表 + 登记字段 + 展开原始报告。口径写死在页面上，
// 避免把展示概率/单视角标签误当成训练目标。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Database, ExternalLink, Plus, RefreshCw } from 'lucide-react';
import { replayApi } from '../api/replayApi';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import type { TeacherReportEntry, TeacherReportImportResult } from '../types/replay';

export function TeacherReportsPage() {
  const [entries, setEntries] = useState<TeacherReportEntry[]>([]);
  const [root, setRoot] = useState('');
  const [modelTags, setModelTags] = useState<string[]>([]);
  const [filterTag, setFilterTag] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<unknown>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [urls, setUrls] = useState('');
  const [replayId, setReplayId] = useState('');
  const [playerOverride, setPlayerOverride] = useState('');
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<TeacherReportImportResult | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await replayApi.listTeacherReports();
      setEntries(data.reports);
      setRoot(data.root);
      setModelTags(data.model_tags);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const submitImport = useCallback(async () => {
    const links = urls.split(/[\s,;]+/).filter(Boolean);
    if (links.length === 0) {
      setImportError('请先粘贴至少一个跑谱报告链接');
      return;
    }
    setImporting(true);
    setImportError(null);
    setImportResult(null);
    try {
      const override = playerOverride.trim();
      const result = await replayApi.importTeacherReports(
        links.join('\n'),
        replayId.trim(),
        override === '' ? undefined : Number(override),
      );
      setImportResult(result);
      if (result.imported > 0) {
        setUrls('');
        await load();
      }
    } catch (reason) {
      setImportError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setImporting(false);
    }
  }, [urls, replayId, playerOverride, load]);

  const visible = useMemo(
    () => (filterTag ? entries.filter((item) => item.model_tag === filterTag) : entries),
    [entries, filterTag],
  );

  const toggle = useCallback(async (entry: TeacherReportEntry) => {
    if (expanded === entry.report_id) {
      setExpanded(null);
      setDetail(null);
      return;
    }
    setExpanded(entry.report_id);
    setDetail(null);
    setDetailError(null);
    try {
      const loaded = await replayApi.getTeacherReport(entry.report_id);
      setDetail(loaded.report);
    } catch (reason) {
      setDetailError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [expanded]);

  const totalDecisions = visible.reduce((sum, item) => sum + (item.decision_count || 0), 0);

  return (
    <PageShell width={1180}>
      <PageHeader
        title="Teacher Reports"
        actions={(
          <>
            <select
              value={filterTag}
              onChange={(event) => setFilterTag(event.target.value)}
              style={selectStyle}
              aria-label="按教师标签过滤"
            >
              <option value="">全部教师</option>
              {modelTags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
            </select>
            <button type="button" onClick={() => void load()} className="btn-secondary" style={actionButtonStyle}>
              <RefreshCw size={14} />
              刷新
            </button>
          </>
        )}
      />

      <p style={caveatStyle}>
        归档来自在线跑谱（站点只保留约 15 天）的<strong>原始报告</strong>，按内容指纹去重。
        一份报告只提供<strong>所选玩家</strong>的教师标签（不是四家标签）；<code>prob</code> 是站点展示温度
        （如 0.1）下的产物，训练应使用原始分数。<code>报告决策数</code>是报告内条目数，不等于该局全部决策。
      </p>

      <section style={importPanelStyle}>
        <div style={importTitleStyle}><Plus size={13} /> 导入跑谱报告</div>
        <textarea
          value={urls}
          onChange={(event) => setUrls(event.target.value)}
          placeholder={'粘贴跑谱报告链接，一行一个（也支持逗号/空格分隔）：\nhttps://mjai.ekyu.moe/killerducky/?data=/report/xxxxxxxx.json'}
          rows={4}
          style={textareaStyle}
        />
        <div style={importRowStyle}>
          <label style={labelStyle}>
            关联牌谱 ID（可选）
            <input
              value={replayId}
              onChange={(event) => setReplayId(event.target.value)}
              placeholder="replay_xxxxxxxx_0000000000；不填则只归档"
              style={inputStyle}
            />
          </label>
          <label style={labelStyle}>
            视角覆盖（可选）
            <input
              value={playerOverride}
              onChange={(event) => setPlayerOverride(event.target.value)}
              placeholder="0-3；默认用报告自带视角"
              inputMode="numeric"
              style={{ ...inputStyle, width: 160 }}
            />
          </label>
          <button
            type="button"
            onClick={() => void submitImport()}
            disabled={importing}
            className="btn-primary"
            style={importButtonStyle}
          >
            <Database size={14} />
            {importing ? '导入中...' : '导入并归档'}
          </button>
        </div>
        {importError && <div style={{ ...statusInlineStyle, color: 'var(--error)' }}>{importError}</div>}
        {importResult && (
          <div style={importResultStyle}>
            <div>
              已归档 <strong>{importResult.imported}</strong> 份
              {importResult.failed > 0 && <>, 失败 <strong style={{ color: 'var(--error)' }}>{importResult.failed}</strong> 份</>}
              {importResult.replay_id && (
                importResult.replay_exists
                  ? <> · 已关联到 <code>{importResult.replay_id}</code></>
                  : <> · <span style={{ color: 'var(--warn, var(--text-muted))' }}>未找到牌谱 <code>{importResult.replay_id}</code>，已作为独立归档保存</span></>
              )}
            </div>
            <ul style={importListStyle}>
              {importResult.results.map((item) => (
                <li key={item.url} style={{ color: item.status === 'archived' ? 'var(--text-secondary)' : 'var(--error)' }}>
                  {item.status === 'archived'
                    ? <>✓ {item.model_tag} · p{item.player_id ?? '?'} · {item.kyoku_count}局/{item.decision_count}条 · <code>{item.report_id}</code></>
                    : <>✗ {item.url} — {item.error}</>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <div style={summaryStyle}>
        <span><Database size={13} /> 归档 {visible.length} 份 · 报告内决策 {totalDecisions} 条</span>
        <span style={rootStyle} title={root}>{root}</span>
      </div>

      <div style={tableStyle}>
        <div style={headerStyle}>
          <span>首次归档</span>
          <span>教师</span>
          <span>视角</span>
          <span>规模</span>
          <span>来源 / 引用</span>
          <span>报告</span>
        </div>
        {visible.map((item) => (
          <div key={item.report_id}>
            <div style={rowStyle}>
              <span style={monoStyle}>{item.first_seen_at}</span>
              <span style={{ fontWeight: 700 }}>{item.model_tag || item.source}</span>
              <span>P{item.player_id ?? '?'}</span>
              <span>{item.kyoku_count}局 / {item.decision_count}条</span>
              <span style={sourceCellStyle}>
                {item.source_url
                  ? (
                    <a href={item.source_url} target="_blank" rel="noreferrer" style={linkStyle}>
                      {item.source} <ExternalLink size={11} />
                    </a>
                  )
                  : <span>{item.source}</span>}
                <span style={metaStyle}>
                  引用 {item.replay_ids.length} 次 · 抓取 {item.fetch_count} 次 · {item.report_id}
                </span>
              </span>
              <span>
                <button type="button" onClick={() => void toggle(item)} style={openButtonStyle}>
                  {expanded === item.report_id ? '收起' : '展开'}
                </button>
              </span>
            </div>
            {expanded === item.report_id && (
              <div style={detailStyle}>
                <div style={detailMetaStyle}>
                  sha256 {item.content_sha256}
                  {item.replay_ids.length > 0 && <> · 引用牌谱 {item.replay_ids.join(', ')}</>}
                </div>
                {detailError && <div style={{ color: 'var(--error)' }}>{detailError}</div>}
                {!detailError && detail === null && <div style={statusStyle}>加载中...</div>}
                {detail !== null && (
                  <pre style={preStyle}>{JSON.stringify(detail, null, 2)}</pre>
                )}
              </div>
            )}
          </div>
        ))}
        {!loading && visible.length === 0 && !error && (
          <div style={statusStyle}>
            暂无归档。在「新建 Review」里贴一次跑谱报告链接（mortal 报告框），保存后即会自动归档。
          </div>
        )}
        {loading && <div style={statusStyle}>加载中...</div>}
        {error && <div style={{ ...statusStyle, color: 'var(--error)' }}>{error}</div>}
      </div>
    </PageShell>
  );
}

const tableStyle: React.CSSProperties = {
  border: '1px solid var(--border)',
  borderRadius: 7,
  overflowX: 'auto',
  background: 'var(--card-bg)',
  marginTop: 10,
};
const gridColumns = '150px minmax(110px, 1fr) 60px 120px minmax(220px, 1.4fr) 90px';
const headerStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: gridColumns,
  gap: 10,
  padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-muted)',
  fontSize: 11,
  fontWeight: 700,
  minWidth: 860,
};
const rowStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: gridColumns,
  gap: 10,
  alignItems: 'center',
  minHeight: 44,
  padding: '6px 10px',
  color: 'var(--text-primary)',
  fontSize: 12,
  minWidth: 860,
};
const monoStyle: React.CSSProperties = { fontFamily: 'Menlo, Consolas, monospace', fontSize: 11 };
const sourceCellStyle: React.CSSProperties = { display: 'grid', gap: 2, minWidth: 0 };
const metaStyle: React.CSSProperties = { color: 'var(--text-muted)', fontSize: 10 };
const linkStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 3,
  color: 'var(--accent)',
  fontSize: 11,
  textDecoration: 'none',
};
const detailStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--border)',
  background: 'var(--surface-muted, rgba(0,0,0,0.02))',
  padding: '8px 10px 10px',
};
const detailMetaStyle: React.CSSProperties = {
  fontFamily: 'Menlo, Consolas, monospace',
  fontSize: 10,
  color: 'var(--text-muted)',
  marginBottom: 6,
  wordBreak: 'break-all',
};
const preStyle: React.CSSProperties = {
  margin: 0,
  maxHeight: 420,
  overflow: 'auto',
  fontFamily: 'Menlo, Consolas, monospace',
  fontSize: 11,
  lineHeight: 1.45,
  color: 'var(--text-secondary)',
};
const caveatStyle: React.CSSProperties = {
  margin: '4px 0 0',
  fontSize: 11,
  lineHeight: 1.6,
  color: 'var(--text-muted)',
};
const summaryStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 12,
  marginTop: 10,
  fontSize: 11,
  color: 'var(--text-secondary)',
};
const rootStyle: React.CSSProperties = {
  fontFamily: 'Menlo, Consolas, monospace',
  fontSize: 10,
  color: 'var(--text-muted)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  maxWidth: 520,
};
const openButtonStyle: React.CSSProperties = {
  height: 26,
  padding: '0 10px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  border: '1px solid var(--accent-border)',
  borderRadius: 5,
  background: 'var(--accent-bg)',
  color: 'var(--accent)',
  fontWeight: 800,
  cursor: 'pointer',
};
const actionButtonStyle: React.CSSProperties = { height: 32, display: 'inline-flex', alignItems: 'center', gap: 6 };
const selectStyle: React.CSSProperties = { height: 32, fontSize: 12 };
const statusStyle: React.CSSProperties = { padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 12 };
const importPanelStyle: React.CSSProperties = {
  marginTop: 12,
  padding: '10px 12px',
  border: '1px solid var(--border)',
  borderRadius: 7,
  background: 'var(--card-bg)',
  display: 'grid',
  gap: 8,
};
const importTitleStyle: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 700 };
const textareaStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  fontFamily: 'Menlo, Consolas, monospace',
  fontSize: 11,
  lineHeight: 1.5,
  padding: 8,
  resize: 'vertical',
};
const importRowStyle: React.CSSProperties = { display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap' };
const labelStyle: React.CSSProperties = { display: 'grid', gap: 3, fontSize: 10, color: 'var(--text-muted)', flex: '1 1 240px' };
const inputStyle: React.CSSProperties = { height: 30, fontSize: 11, fontFamily: 'Menlo, Consolas, monospace', padding: '0 8px' };
const importButtonStyle: React.CSSProperties = { height: 30, display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' };
const statusInlineStyle: React.CSSProperties = { fontSize: 11 };
const importResultStyle: React.CSSProperties = { fontSize: 11, color: 'var(--text-secondary)', display: 'grid', gap: 4 };
const importListStyle: React.CSSProperties = { margin: 0, paddingLeft: 16, display: 'grid', gap: 2, fontSize: 10.5 };
