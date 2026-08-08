// src/replay_ui/src/hooks/useVisibleLiveQuery.ts
// 三页（天梯/账号/模型）共享的"页面可见时实时刷新"查询生命周期。
//
// 语义：
// - 首次加载：loading=true，失败显示 error，data=null；
// - 静默轮询：页面 visible 时每 intervalMs 执行一次，hidden 时跳过，恢复 visible 立即执行；
// - 轮询失败：保留当前 data，不替换成整页错误，下一轮继续重试；
// - 轮询成功：原子替换 data，清除旧 error，从失败状态恢复；
// - queryKey 改变：旧请求失去所有权并 abort、状态同步切换到新 key、开始新实体首次加载；
// - 卸载：解除所有权并 abort、移除 interval 与 visibilitychange listener。
//
// 同一实例保证最多一个在途请求（activeRequestRef 持有请求对象身份），
// 不使用 setInterval(async () => ...) 直接堆请求。
//
// 所有权规则（防止旧请求的 finally 释放新请求状态）：
// - 启动时若已有 active request 则直接返回（in-flight 去重）；
// - 只有 activeRef.current === request 的调用才能更新 data/error/loading/refreshing 与 finally；
// - queryKey 变化 / disabled / unmount 时先让旧请求失去所有权，再 abort，
//   因此旧 Promise 后续的 catch/finally 无法触碰新请求状态。
import { useCallback, useEffect, useRef, useState } from 'react';

export interface VisibleLiveQueryOptions<T> {
  /** false 时不加载、不轮询（如缺少 activeSeasonId）。 */
  enabled: boolean;
  /** 变化即清空旧数据并重新首次加载；旧请求被 abort。 */
  queryKey: string;
  load: (signal: AbortSignal) => Promise<T>;
  intervalMs?: number;
}

export interface VisibleLiveQueryResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** 静默轮询进行中（非首次加载）。 */
  refreshing: boolean;
}

interface ActiveRequest {
  controller: AbortController;
  key: string;
  mode: 'initial' | 'poll';
}

interface QueryState<T> {
  key: string;
  data: T | null;
  loading: boolean;
  error: string | null;
  refreshing: boolean;
}

export function useVisibleLiveQuery<T>(options: VisibleLiveQueryOptions<T>): VisibleLiveQueryResult<T> {
  const { enabled, queryKey, load, intervalMs = 30_000 } = options;

  const [state, setState] = useState<QueryState<T>>(() => ({
    key: queryKey,
    data: null,
    loading: true,
    error: null,
    refreshing: false,
  }));

  const loadRef = useRef(load);
  const enabledRef = useRef(enabled);
  const queryKeyRef = useRef(queryKey);
  const activeRef = useRef<ActiveRequest | null>(null);

  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  const run = useCallback(async (mode: 'initial' | 'poll') => {
    if (!enabledRef.current) return;
    if (activeRef.current) return; // 同一实例最多一个请求在途
    // initial 始终执行（后台打开详情页也会加载）；只有周期 poll 在 hidden 时跳过。
    if (mode === 'poll' && document.visibilityState !== 'visible') return;

    const request: ActiveRequest = {
      controller: new AbortController(),
      key: queryKeyRef.current,
      mode,
    };
    activeRef.current = request;

    if (mode === 'poll') {
      setState((prev) => (prev.key === request.key ? { ...prev, refreshing: true } : prev));
    }

    try {
      const payload = await loadRef.current(request.controller.signal);
      if (activeRef.current !== request || request.controller.signal.aborted) return;
      setState({
        key: request.key,
        data: payload,
        loading: false,
        error: null,
        refreshing: false,
      });
    } catch (reason) {
      if (activeRef.current !== request || request.controller.signal.aborted) return;
      // AbortError 属于 queryKey 切换/卸载的正常取消，不作为用户错误
      if (reason instanceof DOMException && reason.name === 'AbortError') return;
      if (mode === 'initial') {
        setState({
          key: request.key,
          data: null,
          loading: false,
          error: reason instanceof Error ? reason.message : String(reason),
          refreshing: false,
        });
      } else {
        // 轮询失败：保留现有 data，等待下一轮重试；只清 refreshing。
        setState((prev) => (prev.key === request.key ? { ...prev, refreshing: false } : prev));
      }
    } finally {
      // 仅 owner 能释放；旧请求的 finally 不得触碰新请求状态。
      if (activeRef.current === request) {
        activeRef.current = null;
      }
    }
  }, []);

  // queryKey 变化 / enabled 建立：同步切换所有权后启动 initial。
  useEffect(() => {
    if (!enabled) return;
    if (queryKey !== queryKeyRef.current) {
      const previous = activeRef.current;
      activeRef.current = null; // 旧请求先失去所有权
      previous?.controller.abort();
      queryKeyRef.current = queryKey;
      setState({
        key: queryKey,
        data: null,
        loading: true,
        error: null,
        refreshing: false,
      });
    }
    void run('initial');
  }, [enabled, queryKey, run]);

  // disabled：解除所有权并 abort 在途请求。
  useEffect(() => {
    if (enabled) return;
    const previous = activeRef.current;
    activeRef.current = null;
    previous?.controller.abort();
  }, [enabled]);

  // 可见性轮询：visible 每 intervalMs；hidden 跳过；恢复 visible 立即刷新。
  // intervalMs 变化时重建 timer（旧 timer 清除，按新周期继续）。
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void run('poll');
    }, intervalMs);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') void run('poll');
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [enabled, intervalMs, run]);

  // 卸载（含 StrictMode 重放 cleanup）：解除所有权并 abort。
  useEffect(() => {
    return () => {
      const previous = activeRef.current;
      activeRef.current = null;
      previous?.controller.abort();
    };
  }, []);

  // render 阶段同步屏蔽旧 key 的实体，避免 queryKey 变化后的一帧旧内容。
  const keyMatches = state.key === queryKey;
  return {
    data: keyMatches ? state.data : null,
    loading: enabled && (!keyMatches || state.loading),
    error: keyMatches ? state.error : null,
    refreshing: keyMatches && state.refreshing,
  };
}
