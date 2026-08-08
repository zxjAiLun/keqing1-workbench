// src/replay_ui/src/hooks/useConnectionManager.ts
// 负责心跳发送、断线检测、自动重连
import { useEffect, useRef, useState } from 'react';
import type { BattleState } from '../types/battle';

type ConnStatus = 'connected' | 'reconnecting' | 'disconnected';

interface UseConnectionManagerOptions {
  /** gameId 为 null 时停止心跳（游戏未开始/已结束） */
  gameId: string | null;
  playerId: number;
  onStateUpdate: (state: BattleState) => void;
  /** 重连彻底失败后回调（放弃本局） */
  onGiveUp: () => void;
}

const HEARTBEAT_INTERVAL = 5000;   // 每 5s 发一次心跳
const RECONNECT_INTERVAL = 3000;   // 重连间隔 3s
const MAX_RECONNECT_ATTEMPTS = 5;  // 最多重连 5 次

export function useConnectionManager({
  gameId,
  playerId,
  onStateUpdate,
  onGiveUp,
}: UseConnectionManagerOptions) {
  const [status, setStatus] = useState<ConnStatus>('connected');
  const [reconnectAttempts, setReconnectAttempts] = useState(0);

  const statusRef = useRef<ConnStatus>('connected');
  const attemptsRef = useRef(0);
  const gameIdRef = useRef(gameId);
  const onStateUpdateRef = useRef(onStateUpdate);
  const onGiveUpRef = useRef(onGiveUp);

  useEffect(() => {
    gameIdRef.current = gameId;
  }, [gameId]);

  useEffect(() => {
    onStateUpdateRef.current = onStateUpdate;
  }, [onStateUpdate]);

  useEffect(() => {
    onGiveUpRef.current = onGiveUp;
  }, [onGiveUp]);

  const setStatusBoth = (s: ConnStatus) => {
    statusRef.current = s;
    setStatus(s);
  };

  // 心跳循环
  useEffect(() => {
    if (!gameId) {
      const resetTimer = window.setTimeout(() => {
        setStatusBoth('connected');
        attemptsRef.current = 0;
        setReconnectAttempts(0);
      }, 0);
      return () => clearTimeout(resetTimer);
    }

    let cancelled = false;
    let reconnectTimer: number | null = null;

    const sendHeartbeat = async (): Promise<boolean> => {
      try {
        const res = await fetch(`/api/battle/heartbeat/${gameIdRef.current}`, {
          method: 'POST',
          signal: AbortSignal.timeout(3000),
        });
        return res.ok;
      } catch {
        return false;
      }
    };

    const tryReconnect = async () => {
      if (cancelled) return;
      if (attemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setStatusBoth('disconnected');
        setReconnectAttempts(attemptsRef.current);
        return;
      }

      attemptsRef.current += 1;
      setReconnectAttempts(attemptsRef.current);
      setStatusBoth('reconnecting');

      try {
        const res = await fetch(
          `/api/battle/reconnect/${gameIdRef.current}?player_id=${playerId}`,
          { signal: AbortSignal.timeout(5000) }
        );
        if (res.ok) {
          const data = await res.json();
          if (!cancelled) {
            onStateUpdateRef.current(data.state);
            setStatusBoth('connected');
            attemptsRef.current = 0;
            setReconnectAttempts(0);
          }
          return;
        }
      } catch { /* ignore */ }

      // 重连失败，等待后再试
      if (!cancelled) {
        reconnectTimer = window.setTimeout(tryReconnect, RECONNECT_INTERVAL);
      }
    };

    const interval = window.setInterval(async () => {
      if (statusRef.current === 'reconnecting' || statusRef.current === 'disconnected') return;
      const ok = await sendHeartbeat();
      if (!ok && !cancelled) {
        // 心跳失败，开始重连
        attemptsRef.current = 0;
        tryReconnect();
      }
    }, HEARTBEAT_INTERVAL);

    return () => {
      cancelled = true;
      clearInterval(interval);
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
    };
  }, [gameId, playerId]);

  // 重连彻底失败后触发 onGiveUp
  useEffect(() => {
    if (status === 'disconnected' && reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      // 延迟 3s 后自动放弃，让用户看到提示
      const t = window.setTimeout(() => onGiveUpRef.current(), 3000);
      return () => clearTimeout(t);
    }
  }, [status, reconnectAttempts]);

  return { status, reconnectAttempts };
}
