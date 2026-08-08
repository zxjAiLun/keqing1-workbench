// src/replay_ui/src/api/playwithyouApi.ts
// Client for the "Play with you" backend: summon Mortal-weight bot accounts
// into a Tenhou private room (e.g. L2147) as NoName guests.
import { fetchWithTimeout } from "./battleApi";

const BASE = "/api/battle/playwithyou";

export type NetworkId = "none" | "mortal" | "70k" | "ext_mortal" | "custom";
export type SpeedId = "slow" | "normal" | "fast" | "turbo";
export type DeviceId = "cuda" | "cpu";

export interface LadderCaptureRequest {
  enabled: boolean;
  season_id: string;
  human_account_id: string;
  bot_account_ids: string[];
  mode: "confirm";
}

export interface ParticipantBindingRequest {
  // Play-with-you simplification：account/controller 可选（launchers 只带模型）
  account_id?: string | null;
  controller_type?: string | null;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  launcher_slot?: number | null; // 本系统实际呼出的 slot；null = 不启动
  expected_raw_name?: string | null; // NoName-1 等
  resolution_required?: boolean;
}

export interface StartPlayWithYouRequest {
  lobby_id: string;
  speed: SpeedId;
  quantity?: number;
  device: DeviceId;
  name_prefix?: string;
  tenhou_cookie?: string;
  // R10 UX Repair：生产 UI 不再发送旧 networks/custom_paths/ladder_capture；
  // 保留为 backend compatibility（旧 launcher spec 只做兼容映射，不做 UI truth）。
  networks?: string[]; // length 4, slot 0..3
  custom_paths?: Record<number, string>; // slot -> absolute .pth path
  ladder_capture?: LadderCaptureRequest;
  roster?: ParticipantBindingRequest[]; // R10-E 通用四人阵容（唯一启动模式）
  launchers?: ParticipantBindingRequest[]; // Play-with-you simplification：只呼出模型（1-4 个），不预绑账号
}

export interface BotInfo {
  name: string;
  spec: string;
}

export interface LadderCaptureView {
  enabled: boolean;
  season_id: string;
  human_account_id: string;
  bot_account_ids: string[];
  mode: string;
}

export interface PlayWithYouStatus {
  session_id: string | null;
  running: boolean;
  lobby_id: string | null;
  speed: string | null;
  device: string | null;
  bots: BotInfo[];
  log_tail: string[];
  started_at: number | null;
  ladder_capture?: LadderCaptureView | null;
  // R10 UX Repair 2：roster 模式冻结阵容（后端为名字真相源）
  frozen_roster?: Array<{
    account_id: string;
    controller_type?: string | null;
    model_identity_id?: string | null;
    model_artifact_id?: string | null;
    launcher_slot?: number | null;
    expected_raw_name?: string | null;
  }> | null;
}

export interface LadderCaptureEntry {
  capture_id: string;
  session_id: string;
  state: string;
  season_id?: string | null;
  match?: {
    match_id?: string;
    occurred_at?: string;
    game_length?: string;
    players?: Array<{ account_id: string; seat: number; final_score: number }>;
  } | null;
  tenhou_log_url?: string | null;
  observer_accounts?: string[];
  score_observers?: string[];
  roster?: Array<{
    account_id: string;
    controller_type: string;
    launcher_slot?: number | null;
    expected_raw_name?: string | null;
    model_identity_id?: string | null;
    model_artifact_id?: string | null;
  }>;
  evidence_warning?: string | null;
}

export async function startPlayWithYou(req: StartPlayWithYouRequest): Promise<PlayWithYouStatus> {
  const res = await fetchWithTimeout(`${BASE}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getPlayWithYouStatus(): Promise<PlayWithYouStatus> {
  const res = await fetchWithTimeout(`${BASE}/status`);
  if (!res.ok) throw new Error(`status ${res.statusText}`);
  return res.json();
}

export async function stopPlayWithYou(): Promise<PlayWithYouStatus> {
  const res = await fetchWithTimeout(`${BASE}/stop`, { method: "POST" });
  if (!res.ok) throw new Error(`stop ${res.statusText}`);
  return res.json();
}

export async function listLadderCaptures(): Promise<{ captures: LadderCaptureEntry[] }> {
  const res = await fetchWithTimeout(`${BASE}/captures`);
  if (!res.ok) throw new Error(`captures ${res.statusText}`);
  return res.json();
}

export async function confirmLadderCapture(captureId: string): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout(`${BASE}/captures/${encodeURIComponent(captureId)}/confirm`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function ignoreLadderCapture(captureId: string): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout(`${BASE}/captures/${encodeURIComponent(captureId)}/ignore`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`ignore ${res.statusText}`);
  return res.json();
}

export async function retryPublishLadderCapture(captureId: string): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout(
    `${BASE}/captures/${encodeURIComponent(captureId)}/retry-publish`,
    { method: "POST" },
  );
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}
