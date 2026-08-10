// src/replay_ui/src/routes.ts
//
// Workbench canonical route table.
//
// 主要页面跳转统一引用本文件的常量与构造函数，不要在页面组件里硬编码路径。
// legacyRoutes 指向仍然保留、但不再出现在主导航的兼容页面。

export const routes = {
  home: '/',
  reviewNew: '/reviews/new',
  reviewLibrary: '/reviews',
  reviewWorkspace: (replayId: string) => `/reviews/${encodeURIComponent(replayId)}`,
  ladder: '/ladder',
  ladderAccount: (accountId: string) => `/ladder/accounts/${encodeURIComponent(accountId)}`,
  ladderModel: (modelId: string) => `/ladder/models/${encodeURIComponent(modelId)}`,
  tenhou: '/tenhou',
  seasonManager: '/seasons',
  diagnosticsCasebook: '/diagnostics/casebook',
  participants: '/participants',
  matches: '/matches',
  matchEntry: '/matches/new',
  matchImport: '/matches/import',
  matchDetail: (matchId: string) => `/matches/${encodeURIComponent(matchId)}`,
} as const;

/** App.tsx 注册 Review Workspace 路由时使用的 pattern。 */
export const REVIEW_WORKSPACE_PATTERN = '/reviews/:replayId';

/** App.tsx 注册 Ladder 详情页路由时使用的 pattern。 */
export const LADDER_ACCOUNT_PATTERN = '/ladder/accounts/:accountId';
export const LADDER_MODEL_PATTERN = '/ladder/models/:modelId';

/** App.tsx 注册 Match 详情页路由时使用的 pattern。 */
export const MATCH_DETAIL_PATTERN = '/matches/:matchId';

/** 给 Ladder 相关路径追加 ?season= 查询参数。 */
export function withLadderSeason(path: string, seasonId: string | null | undefined): string {
  if (!seasonId) return path;
  const params = new URLSearchParams({ season: seasonId });
  return `${path}?${params.toString()}`;
}

/**
 * 兼容保留的 legacy 页面路径。
 * 这些路由继续可用（供旧链接与内部视图切换使用），但不属于 canonical 信息架构。
 */
export const legacyRoutes = {
  decisionList: '/replay',
  gameReplay: '/game-replay',
  mortalReview: '/mortal-review',
} as const;

export interface ReviewWorkspaceQuery {
  playerId?: number | string;
  teacherReports?: readonly string[];
}

/**
 * 构造带查询参数的 Review Workspace URL。
 * 使用 URLSearchParams 逐个 append，重复的 teacher_reports 会被完整保留。
 */
export function reviewWorkspaceUrl(
  replayId: string,
  query: ReviewWorkspaceQuery = {},
): string {
  const params = new URLSearchParams();
  if (query.playerId !== undefined) params.set('player_id', String(query.playerId));
  for (const report of query.teacherReports ?? []) {
    const trimmed = report.trim();
    if (trimmed) params.append('teacher_reports', trimmed);
  }
  const search = params.toString();
  return `${routes.reviewWorkspace(replayId)}${search ? `?${search}` : ''}`;
}
