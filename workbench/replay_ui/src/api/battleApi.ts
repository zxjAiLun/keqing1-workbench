// src/replay_ui/src/api/battleApi.ts
import type {
  BattleState,
  StartBattleRequest,
  StartBattleResponse,
  ActionRequest,
  ActionResponse,
} from "../types/battle";

const API_BASE = "/api/battle";
const FETCH_TIMEOUT = 10000;

export async function fetchWithTimeout(url: string, options?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timeout);
    return res;
  } catch (e) {
    clearTimeout(timeout);
    throw e;
  }
}

export async function startBattle(req: StartBattleRequest): Promise<StartBattleResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    throw new Error(`Failed to start battle: ${response.statusText}`);
  }
  return response.json();
}

export async function getBattleState(gameId: string, playerId: number = 0): Promise<{ state: BattleState }> {
  const response = await fetchWithTimeout(`${API_BASE}/state/${gameId}?player_id=${playerId}`);
  if (!response.ok) {
    throw new Error(`Failed to get state: ${response.statusText}`);
  }
  return response.json();
}

export async function closeBattle(gameId: string): Promise<void> {
  await fetchWithTimeout(`${API_BASE}/close/${gameId}`, { method: "POST" });
}

export async function doAction(req: ActionRequest): Promise<ActionResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    throw new Error(`Failed to do action: ${response.statusText}`);
  }
  return response.json();
}

export async function nextKyoku(gameId: string): Promise<ActionResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/next_kyoku/${gameId}`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error(`Failed to advance kyoku: ${response.statusText}`);
  }
  return response.json();
}
