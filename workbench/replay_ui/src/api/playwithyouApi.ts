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
  // R10 legacy roster/account-bound：参与者预绑定（R11-B 的 model-only 呼出不走这里）
  account_id?: string | null;
  controller_type?: string | null;
  model_identity_id?: string | null;
  model_artifact_id?: string | null;
  launcher_slot?: number | null; // 本系统实际呼出的 slot；null = 不启动
  expected_raw_name?: string | null; // NoName-1 等
  resolution_required?: boolean;
}

// R11-B：Play-with-you 只呼出模型（model_id 来自运行时模型目录）
export interface PlayWithYouLauncherRequest {
  model_id: string;
}

export interface RuntimeModelInfo {
  model_id: string;
  label: string;
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
  launchers?: PlayWithYouLauncherRequest[]; // R11-B：只呼出模型（1-4 个），不预绑账号
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
  // R11-B：model-only 冻结阵容（model_id + resolved_checkpoint_path；无账号绑定）
  frozen_roster?: Array<{
    account_id?: string | null;
    model_id?: string | null;
    resolved_checkpoint_path?: string | null;
    controller_type?: string | null;
    model_identity_id?: string | null;
    model_artifact_id?: string | null;
    launcher_slot?: number | null;
    expected_raw_name?: string | null;
  }> | null;
  // R11-C：对局结束后的 awaiting_import 天凤链接（无则需手工粘贴）
  tenhou_log_url?: string | null;
  // R11-C Repair：会话结束方式（running / natural_exit / manual_stop）。
  // manual_stop 不显示任何赛后导入 CTA。
  termination_reason?: "running" | "natural_exit" | "manual_stop" | null;
}

// R11-B：运行时模型目录
export interface PlayWithYouModelsResponse {
  schema: string;
  models: RuntimeModelInfo[];
}

export async function listPlayWithYouModels(): Promise<PlayWithYouModelsResponse> {
  const res = await fetchWithTimeout(`${BASE}/models`);
  if (!res.ok) throw new Error(`models ${res.statusText}`);
  return res.json();
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
