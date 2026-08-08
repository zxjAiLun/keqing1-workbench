// src/replay_ui/src/hooks/useLadderSeasonCatalog.ts
// 三页共享的赛季目录 Hook：加载 /api/ladder/seasons（含默认赛季与每赛季 readiness），
// 并统一规范化当前 URL 的 ?season= 选择语义。
//
// 选择语义：
// - URL 有 ?season=：严格使用 URL 赛季，即使不存在也不 fallback（让 endpoint 返回 404）；
// - URL 没有 ?season=：
//     有 default_season_id -> replace 导航到规范 URL（不增加浏览器历史）；
//     没有默认 -> activeSeasonId=null，要求用户选择。
// 目录本身通过 useVisibleLiveQuery 每 30 秒刷新，未就绪原因 / 外部默认项变更可被发现。
import { useEffect, useMemo } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ladderApi } from '../api/ladderApi';
import type { LadderSeason, LadderSeasonsResponse } from '../types/ladder';
import { useVisibleLiveQuery } from './useVisibleLiveQuery';

export interface LadderSeasonCatalogResult {
  seasons: LadderSeason[];
  /** 规范化后的当前生效赛季；无默认且未指定时为 null */
  activeSeasonId: string | null;
  defaultSeasonId: string | null;
  defaultSource: 'registry' | 'single_season' | null;
  /** URL 显式指定的赛季（不存在时保持，不 fallback） */
  urlSeasonId: string | null;
  loading: boolean;
  error: string | null;
  refreshing: boolean;
}

function mergeSearchWithSeason(search: string, seasonId: string): string {
  // 保留路径上的其他 query 参数，只更新 season
  const params = new URLSearchParams(search);
  params.set('season', seasonId);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function useLadderSeasonCatalog(): LadderSeasonCatalogResult {
  const location = useLocation();
  const navigate = useNavigate();

  const catalogQuery = useVisibleLiveQuery<LadderSeasonsResponse>({
    enabled: true,
    queryKey: 'ladder:season-catalog',
    load: useMemo(() => (signal: AbortSignal) => ladderApi.listSeasons(signal), []),
  });

  const seasons = catalogQuery.data?.seasons ?? [];
  const defaultSeasonId = catalogQuery.data?.default_season_id ?? null;
  const defaultSource = catalogQuery.data?.default_source ?? null;

  const urlSeasonId = useMemo(
    () => new URLSearchParams(location.search).get('season'),
    [location.search],
  );

  const activeSeasonId = useMemo(() => {
    if (urlSeasonId) return urlSeasonId;
    return defaultSeasonId;
  }, [urlSeasonId, defaultSeasonId]);

  // 无显式 ?season= 且存在默认赛季：replace 导航到规范 URL（不新增历史）。
  // URL 有显式 ?season= 时严格使用，即使不存在也不 fallback/重定向。
  useEffect(() => {
    if (catalogQuery.loading) return;
    if (urlSeasonId) return;
    if (!defaultSeasonId) return;
    const canonical = `${location.pathname}${mergeSearchWithSeason(location.search, defaultSeasonId)}`;
    navigate(canonical, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅当 catalog 确定默认后执行一次
  }, [catalogQuery.loading, urlSeasonId, defaultSeasonId]);


  return {
    seasons,
    activeSeasonId,
    defaultSeasonId,
    defaultSource,
    urlSeasonId,
    loading: catalogQuery.loading,
    error: catalogQuery.error,
    refreshing: catalogQuery.refreshing,
  };
}
