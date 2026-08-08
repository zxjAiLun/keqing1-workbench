// src/replay_ui/src/api/replayApi.ts
import type {
  ReplayData,
  ReplayMeta,
  ReviewHistoryItem,
  SelfplayAnomalyReplayGroup,
} from '../types/replay';
import type { BotType } from '../types/bot';
import { DEFAULT_BOT_TYPE } from '../utils/botCatalog';

const API_BASE = '/api';

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    const msg = body && typeof body === 'object' && 'error' in body
      ? String((body as { error: string }).error)
      : `API error ${status}`;
    super(msg);
    this.status = status;
    this.body = body;
  }
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body);
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

export const replayApi = {
  /** 提交新回放（使用已有端点，FormData 方式） */
  submit: async (content: string, inputType: string, playerId = 0, botType: BotType = DEFAULT_BOT_TYPE): Promise<ReplayData> => {
    const formData = new FormData();
    formData.append('json_text', content);
    formData.append('input_type', inputType);
    formData.append('player_id', String(playerId));
    formData.append('bot_type', botType);

    const res = await fetch(`${API_BASE}/replay`, {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new ApiError(res.status, err);
    }
    return res.json();
  },

  /** 列出所有已保存的回放 */
  list: (): Promise<ReplayMeta[]> =>
    api<ReplayMeta[]>('/replay/list'),

  listReviewHistory: (): Promise<ReviewHistoryItem[]> =>
    api<ReviewHistoryItem[]>('/replay/review-history'),

  /** 获取回放完整数据 */
  get: (
    replayId: string,
    playerId?: number,
    teacherReport?: string | null,
    teacherReports: string[] = [],
  ): Promise<ReplayData> => {
    const params = new URLSearchParams();
    if (playerId !== undefined) params.set('player_id', String(playerId));
    if (teacherReport) params.set('teacher_report', teacherReport);
    for (const report of teacherReports) {
      if (report.trim()) params.append('teacher_reports', report.trim());
    }
    const query = params.toString();
    return api<ReplayData>(
      `/replay/${encodeURIComponent(replayId)}${query ? `?${query}` : ''}`
    );
  },

  /** 获取回放元信息 */
  getMeta: (replayId: string): Promise<ReplayMeta> =>
    api<ReplayMeta>(`/replay/${encodeURIComponent(replayId)}/meta`),

  /** 分页获取事件流 */
  getEvents: (replayId: string, fromStep = 0, limit = 50) =>
    api<{ events: unknown[]; total: number; next_step: number | null }>(
      `/replay/${encodeURIComponent(replayId)}/events?from_step=${fromStep}&limit=${limit}`
    ),

  /** 删除回放 */
  delete: (replayId: string): Promise<void> =>
    api<void>(`/replay/${encodeURIComponent(replayId)}`, { method: 'DELETE' }),

  /** 列出 selfplay 对局回放导出 */
  listSelfplayReplayCollections: (): Promise<{ groups: SelfplayAnomalyReplayGroup[] }> =>
    api<{ groups: SelfplayAnomalyReplayGroup[] }>('/selfplay/replay-collections'),

  /** 导出 HTML */
  exportHtml: (data: ReplayData): Promise<Blob> =>
    fetch(`${API_BASE}/export-html`, {
      method: 'POST',
      body: JSON.stringify(data),
      headers: { 'Content-Type': 'application/json' },
    }).then(r => r.blob()),
};
