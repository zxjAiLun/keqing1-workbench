// src/replay_ui/src/hooks/useBattlePolling.ts
import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { BattleState } from "../types/battle";

interface UseBattlePollingOptions {
  gameId: string | null;
  state: BattleState | null;
  needsDecision: boolean;
  /** 必须是稳定引用（useState setter 或 useCallback 包裹） */
  onStateUpdate: (state: BattleState) => void;
  /** 传入 pendingActionRef，防止与 doAction 并发 */
  pendingActionRef: MutableRefObject<boolean>;
}

export function useBattlePolling({
  gameId,
  state,
  needsDecision,
  onStateUpdate,
  pendingActionRef,
}: UseBattlePollingOptions) {
  const mountedRef = useRef(true);

  // 用 ref 缓存最新值，避免 interval 依赖频繁重建
  const stateRef = useRef(state);
  const needsDecisionRef = useRef(needsDecision);
  // onStateUpdate 应为稳定引用，但也用 ref 做防御
  const onStateUpdateRef = useRef(onStateUpdate);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    needsDecisionRef.current = needsDecision;
  }, [needsDecision]);

  useEffect(() => {
    onStateUpdateRef.current = onStateUpdate;
  }, [onStateUpdate]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!gameId) return;

    const interval = window.setInterval(async () => {
      try {
        const s = stateRef.current;
        if (s?.phase !== "playing") return;
        if (pendingActionRef.current) return;  // doAction 进行中，跳过

        const playerId = s.human_player_id ?? 0;
        const actorToMove = s.actor_to_move;

        if (actorToMove !== null && actorToMove !== undefined && actorToMove !== playerId) {
          // bot 回合：推进一步
          const res = await fetch(`/api/battle/advance/${gameId}`, { method: "POST" });
          if (!res.ok) return;
          const data = await res.json();
          if (mountedRef.current) onStateUpdateRef.current(data.state);
        } else {
          const onlyNone =
            s.needs_input &&
            s.legal_actions?.length === 1 &&
            s.legal_actions[0]?.type === "none";
          if (onlyNone) {
            pendingActionRef.current = true;
            try {
              const res = await fetch(`/api/battle/action`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ game_id: gameId, action: s.legal_actions[0] }),
              });
              if (!res.ok) return;
              const data = await res.json();
              if (mountedRef.current) onStateUpdateRef.current(data.state);
            } finally {
              pendingActionRef.current = false;
            }
            return;
          }
          if (needsDecisionRef.current) return;  // 只在玩家本人回合等待玩家决策
          // 人类回合：刷新状态
          const res = await fetch(`/api/battle/state/${gameId}?player_id=${playerId}`);
          if (!res.ok) return;
          const data = await res.json();
          if (mountedRef.current) onStateUpdateRef.current(data.state);
        }
      } catch { /* ignore network errors */ }
    }, 500);

    return () => clearInterval(interval);
  }, [gameId, pendingActionRef]);  // interval 只在 gameId 变化时重建
}
