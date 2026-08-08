// src/replay_ui/src/api/ladderApi.ts
import { ApiError } from './replayApi';
import type {
  LadderAccountDetail,
  LadderModelDetail,
  LadderResponse,
  LadderSeasonsResponse,
} from '../types/ladder';

const API_BASE = '/api';

async function api<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    signal,
    headers: {
      Accept: 'application/json',
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body);
  }
  return res.json();
}

export const ladderApi = {
  /** 已注册的评测赛季（含默认赛季与每赛季 readiness） */
  listSeasons: (signal?: AbortSignal): Promise<LadderSeasonsResponse> =>
    api('/ladder/seasons', signal),

  /** 赛季天梯榜：账号排名 + 模型展示性聚合 */
  getLadder: (seasonId: string, sort = 'pt', signal?: AbortSignal): Promise<LadderResponse> =>
    api(`/ladder/seasons/${encodeURIComponent(seasonId)}?sort=${encodeURIComponent(sort)}`, signal),

  /** 账号详情：聚合指标 + 曲线 + 最近对局 */
  getAccount: (seasonId: string, accountId: string, recentGames = 50, signal?: AbortSignal): Promise<LadderAccountDetail> =>
    api(`/ladder/seasons/${encodeURIComponent(seasonId)}/accounts/${encodeURIComponent(accountId)}?recent_games=${recentGames}`, signal),

  /** 模型详情：账号横向对比 + 可选联赛聚合 */
  getModel: (seasonId: string, modelId: string, signal?: AbortSignal): Promise<LadderModelDetail> =>
    api(`/ladder/seasons/${encodeURIComponent(seasonId)}/models/${encodeURIComponent(modelId)}`, signal),
};
