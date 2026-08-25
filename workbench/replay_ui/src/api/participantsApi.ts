// src/replay_ui/src/api/participantsApi.ts
import { ApiError } from './replayApi';
import type {
  Account,
  AccountCreate,
  AccountUpdate,
  AccountsResponse,
  ExternalAlias,
  ExternalAliasCreate,
  IntakeConfirmRequest,
  IntakeAdmissionAssessmentRequest,
  IntakeAdmissionAssessment,
  IntakePreview,
  IntakePreviewRequest,
  MatchCreate,
  MatchListResponse,
  MatchReplayArtifact,
  MatchResponse,
  MatchRevise,
  ModelsResponse,
  ModelArtifact,
  ModelArtifactCreate,
  ModelIdentity,
  ModelIdentityCreate,
  RevisionSummary,
  LadderProjectionStatus,
  LadderProjectResult,
  AccountStatsResponse,
} from '../types/participants';

const API_BASE = '/api';

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: 'no-store',
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(res.status, body);
  }
  return res.json();
}

export const participantsApi = {
  // ---- 账号 ----
  listAccounts: (signal?: AbortSignal): Promise<AccountsResponse> =>
    api('/participants/accounts', { signal }),
  createAccount: (payload: AccountCreate): Promise<Account> =>
    api('/participants/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  getAccount: (accountId: string, signal?: AbortSignal): Promise<{ account: Account; identities: ModelIdentity[] }> =>
    api(`/participants/accounts/${encodeURIComponent(accountId)}`, { signal }),
  updateAccount: (accountId: string, payload: AccountUpdate): Promise<Account> =>
    api(`/participants/accounts/${encodeURIComponent(accountId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAccount: (accountId: string): Promise<{ deleted: boolean; disabled: boolean; account_id: string }> =>
    api(`/participants/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),

  // ---- 模型 ----
  listModels: (signal?: AbortSignal): Promise<ModelsResponse> =>
    api('/participants/models', { signal }),
  createModel: (payload: ModelIdentityCreate): Promise<ModelIdentity> =>
    api('/participants/models', { method: 'POST', body: JSON.stringify(payload) }),
  addModelArtifact: (modelIdentityId: string, payload: ModelArtifactCreate): Promise<ModelArtifact> =>
    api(`/participants/models/${encodeURIComponent(modelIdentityId)}/artifacts`, { method: 'POST', body: JSON.stringify(payload) }),
  setCurrentArtifact: (modelIdentityId: string, artifactId: string): Promise<ModelArtifact> =>
    api(`/participants/models/${encodeURIComponent(modelIdentityId)}/artifacts/${encodeURIComponent(artifactId)}/current`, { method: 'POST' }),

  // ---- 对局账本 ----
  listMatches: (params: {
    source?: string; status?: string; account_id?: string; from_at?: string; to_at?: string; limit?: number; offset?: number;
  } = {}, signal?: AbortSignal): Promise<MatchListResponse> => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') qs.set(key, String(value));
    }
    const search = qs.toString();
    return api(`/participants/matches${search ? `?${search}` : ''}`, { signal });
  },
  createMatch: (payload: MatchCreate): Promise<MatchResponse> =>
    api('/participants/matches', { method: 'POST', body: JSON.stringify(payload) }),
  getMatch: (matchId: string, signal?: AbortSignal): Promise<MatchResponse> =>
    api(`/participants/matches/${encodeURIComponent(matchId)}`, { signal }),
  getRevisions: (matchId: string, signal?: AbortSignal): Promise<{ match_id: string; revisions: RevisionSummary[] }> =>
    api(`/participants/matches/${encodeURIComponent(matchId)}/revisions`, { signal }),
  reviseMatch: (matchId: string, payload: MatchRevise): Promise<MatchResponse> =>
    api(`/participants/matches/${encodeURIComponent(matchId)}/revise`, { method: 'POST', body: JSON.stringify(payload) }),
  voidMatch: (matchId: string, payload: { reason: string }): Promise<MatchResponse> =>
    api(`/participants/matches/${encodeURIComponent(matchId)}/void`, { method: 'POST', body: JSON.stringify(payload) }),
  getMatchReplay: (matchId: string, signal?: AbortSignal): Promise<MatchReplayArtifact> =>
    api(`/participants/matches/${encodeURIComponent(matchId)}/replay`, { signal }),

  // ---- R10-D：外部别名 + 天凤摄入 ----
  listAliases: (params: { provider?: string; account_id?: string; scope?: string } = {}, signal?: AbortSignal): Promise<{ schema: string; aliases: ExternalAlias[] }> => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') qs.set(key, String(value));
    }
    const search = qs.toString();
    return api(`/participants/aliases${search ? `?${search}` : ''}`, { signal });
  },
  createAlias: (payload: ExternalAliasCreate): Promise<ExternalAlias> =>
    api('/participants/aliases', { method: 'POST', body: JSON.stringify(payload) }),
  intakePreview: (payload: IntakePreviewRequest): Promise<IntakePreview> =>
    api('/participants/intake/preview', { method: 'POST', body: JSON.stringify(payload) }),
  intakeAssessment: (payload: IntakeAdmissionAssessmentRequest): Promise<IntakeAdmissionAssessment> =>
    api('/participants/intake/assessment', { method: 'POST', body: JSON.stringify(payload) }),
  intakeConfirm: (payload: IntakeConfirmRequest): Promise<MatchResponse> =>
    api('/participants/intake/confirm', { method: 'POST', body: JSON.stringify(payload) }),

  // ---- R10-F：天梯投影 ----
  getLadderProjectionStatus: (seasonId: string, signal?: AbortSignal): Promise<LadderProjectionStatus> =>
    api(`/participants/ladder/${encodeURIComponent(seasonId)}/status`, { signal }),
  projectLadder: (seasonId: string): Promise<LadderProjectResult> =>
    api(`/participants/ladder/${encodeURIComponent(seasonId)}/project`, { method: 'POST' }),
  getAccountStats: (accountId: string, signal?: AbortSignal): Promise<AccountStatsResponse> =>
    api(`/participants/accounts/${encodeURIComponent(accountId)}/stats`, { signal }),
};

export { ApiError };
