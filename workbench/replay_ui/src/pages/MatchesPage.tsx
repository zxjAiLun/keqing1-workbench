// src/replay_ui/src/pages/MatchesPage.tsx
// R10：对局记录 —— 统一账本列表 + 筛选 + 新建对局。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeader, PageShell } from '../components/Layout/PageScaffold';
import { MatchTable } from '../components/Matches/MatchTable';
import { participantsApi } from '../api/participantsApi';
import { routes } from '../routes';
import type { Account, Match, MatchListResponse } from '../types/participants';

export function MatchesPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<MatchListResponse | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const [matchesResp, accountsResp] = await Promise.all([
        participantsApi.listMatches(status ? { status } : {}, signal),
        participantsApi.listAccounts(signal),
      ]);
      setData(matchesResp);
      setAccounts(accountsResp.accounts);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const openMatch = (match: Match) => navigate(routes.matchDetail(match.match_id));

  return (
    <PageShell maxWidth={1120}>
      <PageHeader
        title="对局记录"
        description="统一比赛账本：手动 / 导入 / 本地，可筛选"
        actions={(
          <>
            <button
              onClick={() => navigate(routes.matchImport)}
              style={ghostBtn}
            >
              天凤导入
            </button>
            <button
              onClick={() => navigate(routes.matchEntry)}
              style={{
                border: '1px solid var(--accent)', background: 'var(--accent)', color: '#fff',
                borderRadius: 6, fontSize: 13, fontWeight: 700, padding: '8px 16px', cursor: 'pointer',
              }}
            >
              新建对局
            </button>
          </>
        )}
      />

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          style={{
            border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
            borderRadius: 4, padding: '5px 8px', fontSize: 13,
          }}
        >
          <option value="">全部状态</option>
          <option value="active">有效</option>
          <option value="void">已作废</option>
        </select>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>
          {loading ? '加载中…' : data ? `共 ${data.total} 场` : ''}
        </span>
      </div>

      {error ? (
        <div style={{ padding: 24, color: '#e74c3c' }}>{error}</div>
      ) : (
        <MatchTable matches={data?.matches ?? []} accounts={accounts} onOpen={openMatch} />
      )}
    </PageShell>
  );
}

const ghostBtn: React.CSSProperties = {
  border: '1px solid var(--border)', background: 'var(--page-bg)', color: 'var(--text-primary)',
  borderRadius: 6, fontSize: 13, padding: '8px 16px', cursor: 'pointer',
};
