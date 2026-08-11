// src/replay_ui/src/pages/PlayWithYouPage.tsx
// "Play with you" — 呼出模型（Play-with-you simplification）。
// 只负责「呼出哪些模型、几个 bot、进哪个房间」。账号是谁、实际東南西北是谁、
// 记到谁名下、是否进正式天梯——全部由赛后 Tenhou Import 决定。
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { PageShell, SectionTitle } from "../components/Layout/PageScaffold";
import {
  startPlayWithYou,
  stopPlayWithYou,
  getPlayWithYouStatus,
  listPlayWithYouModels,
  type SpeedId,
  type DeviceId,
  type BotInfo,
  type PlayWithYouStatus,
  type RuntimeModelInfo,
} from "../api/playwithyouApi";
import { useNavigate } from "react-router-dom";
import { routes } from "../routes";

const ACCENT = "#8e44ad";

const SPEED_OPTIONS: Array<{ value: SpeedId; label: string }> = [
  { value: "slow", label: "Slow" },
  { value: "normal", label: "Normal" },
  { value: "fast", label: "Fast" },
  { value: "turbo", label: "Turbo" },
];

const DEVICE_OPTIONS: Array<{ value: DeviceId; label: string }> = [
  { value: "cuda", label: "CUDA" },
  { value: "cpu", label: "CPU" },
];

function shortSpec(spec: string): string {
  if (!spec) return spec;
  if (spec.includes("/") || spec.includes("\\")) {
    return spec.split(/[\\/]/).pop() || spec;
  }
  return spec;
}

function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${options.length}, 1fr)`, gap: 6 }}>
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            style={{
              height: 32,
              borderRadius: 6,
              fontSize: 13,
              fontWeight: 700,
              border: `1px solid ${active ? ACCENT : "var(--border)"}`,
              background: active ? "rgba(142,68,173,0.08)" : "var(--surface-subtle)",
              color: active ? ACCENT : "var(--text-primary)",
              cursor: disabled ? "not-allowed" : "pointer",
              opacity: disabled ? 0.5 : 1,
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// PWY UX Repair：Mortal-style 固定三槽（AI #1/#2/#3），不呼出 = ""
const SLOT_COUNT = 3;
const NONE_LABEL = '不呼出';

export function PlayWithYouPage() {
  const navigate = useNavigate();
  const [lobbyId, setLobbyId] = useState<string>("2147");
  const [speed, setSpeed] = useState<SpeedId>("normal");
  const [device, setDevice] = useState<DeviceId>("cuda");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [status, setStatus] = useState<PlayWithYouStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // PWY UX Repair：固定三槽，默认 AI #1 = 70k（catalog 加载后校正），其余不呼出。
  const [slots, setSlots] = useState<string[]>(["70k", "", ""]);
  const [models, setModels] = useState<RuntimeModelInfo[]>([]);

  const logRef = useRef<HTMLDivElement | null>(null);
  const pollingRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const s = await getPlayWithYouStatus();
      setStatus(s);
      if (s.running) {
        if (!pollingRef.current) {
          pollingRef.current = window.setInterval(refresh, 2000);
        }
      } else {
        stopPolling();
      }
    } catch {
      /* ignore transient */
    }
  }, [stopPolling]);

  const setSlot = (index: number, modelId: string) => {
    setSlots((prev) => prev.map((value, i) => (i === index ? modelId : value)));
  };

  const filledSlots = slots.filter((modelId) => modelId);

  const start = async () => {
    setLoading(true);
    setError(null);
    try {
      if (filledSlots.length === 0) {
        throw new Error("请至少选择一个要呼出的模型");
      }
      const s = await startPlayWithYou({
        lobby_id: lobbyId,
        speed,
        device,
        launchers: filledSlots.map((model_id) => ({ model_id })),
      });
      setStatus(s);
      stopPolling();
      void refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动失败");
    } finally {
      setLoading(false);
    }
  };

  const stop = async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await stopPlayWithYou();
      setStatus(s);
      stopPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : "停止失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    listPlayWithYouModels()
      .then((resp) => {
        setModels(resp.models);
        // 运行时模型目录驱动的安全默认：AI #1 若不在目录中 → 70k（存在时），
        // 否则 catalog 第一个；AI #2/#3 维持"不呼出"。
        setSlots((prev) => {
          const first = resp.models.some((m) => m.model_id === prev[0])
            ? prev[0]
            : resp.models.find((m) => m.model_id === "70k")?.model_id
              ?? resp.models[0]?.model_id
              ?? "";
          return [
            first,
            resp.models.some((m) => m.model_id === prev[1]) ? prev[1] : "",
            resp.models.some((m) => m.model_id === prev[2]) ? prev[2] : "",
          ];
        });
      })
      .catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  // mount 时恢复运行状态（F5 刷新后仍能看到 running / frozen roster）
  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [status?.log_tail]);

  const isRunning = status?.running ?? false;
  const joinUrl = `https://tenhou.net/0/?${status?.lobby_id ?? lobbyId}`;

  return (
    <PageShell width={960}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 12,
        }}
      >
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "var(--text-primary)" }}>
            Play with you · 天凤在线呼出
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            只负责呼出模型进入天凤个室；账号/坐席/正式计分全部在赛后导入时决定。
          </div>
        </div>
        {isRunning ? (
          <button
            onClick={stop}
            disabled={loading}
            className="btn-primary"
            style={{ height: 34, padding: "0 16px", fontSize: 13, background: loading ? "var(--text-muted)" : "#c0392b" }}
          >
            {loading ? "处理中..." : "停止呼出"}
          </button>
        ) : (
          <button
            onClick={start}
            disabled={loading}
            className="btn-primary"
            style={{ height: 34, padding: "0 16px", fontSize: 13, background: loading ? "var(--text-muted)" : ACCENT }}
          >
            {loading ? "呼出中..." : "呼出"}
          </button>
        )}
      </div>

      {error && (
        <div style={{ fontSize: 13, color: "var(--error)", marginBottom: 10 }}>{error}</div>
      )}

      <div className="card" style={{ padding: 14, marginBottom: 12 }}>
        <SectionTitle title="呼出设置" description="你 + 最多 3 个 AI 进入天凤个室（半庄四人麻）。" />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 12 }}>
          <div>
            <label style={labelStyle}>天凤个室 ID</label>
            <input
              value={lobbyId}
              onChange={(e) => setLobbyId(e.target.value)}
              placeholder="2147"
              disabled={isRunning}
              style={inputStyle}
            />
            <div style={hintStyle}>将进入 L{lobbyId || "2147"} 个室（半庄，4 人）。</div>
          </div>
          <div>
            <label style={labelStyle}>速度</label>
            <Segmented options={SPEED_OPTIONS} value={speed} onChange={setSpeed} disabled={isRunning} />
            <div style={hintStyle}>Speed 在自己回合前的思考停顿，便于观战。</div>
          </div>
        </div>

        {/* PWY UX Repair：Mortal 模型——固定 AI #1/#2/#3 三槽 */}
        <label style={labelStyle}>Mortal 模型</label>
        <div style={{ display: "grid", gap: 8, marginBottom: 8 }}>
          {Array.from({ length: SLOT_COUNT }, (_, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                gap: 8,
                alignItems: "center",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "8px 10px",
                background: "var(--surface-subtle)",
              }}
            >
              <span style={{ width: 64, fontWeight: 800, color: "var(--text-muted)" }}>AI #{index + 1}</span>
              <select
                value={slots[index]}
                disabled={isRunning}
                onChange={(e) => setSlot(index, e.target.value)}
                style={{ ...inputStyle, flex: 1, minWidth: 200 }}
              >
                <option value="">{NONE_LABEL}</option>
                {models.map((m) => (
                  <option key={m.model_id} value={m.model_id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            呼出数量：<b style={{ color: "var(--text-primary)" }}>{filledSlots.length}</b>
          </span>
          {/* 高级设置：Device 是本地 runtime 实现细节 */}
          <button
            type="button"
            onClick={() => setShowAdvanced((value) => !value)}
            disabled={isRunning}
            style={ghostSmallBtn}
          >
            {showAdvanced ? "收起高级设置" : "高级设置"}
          </button>
        </div>
        {showAdvanced && (
          <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 8 }}>
            <label style={{ ...labelStyle, marginBottom: 0 }}>运行设备</label>
            <Segmented options={DEVICE_OPTIONS} value={device} onChange={setDevice} disabled={isRunning} />
            <span style={hintStyle}>默认 CUDA；CPU 仅在无 GPU 时选择。</span>
          </div>
        )}
      </div>

      {/* Status / live log */}
      {status && (
        <div className="card" style={{ padding: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
            <span
              style={{
                padding: "4px 10px",
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 700,
                color: "#fff",
                background: isRunning ? "#27ae60" : "#7f8c8d",
              }}
            >
              {isRunning ? "运行中" : "已结束"}
            </span>
            <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
              个室 L{status.lobby_id} · Speed {status.speed} · {status.device}
            </span>
            {status.lobby_id && (
              <a
                href={joinUrl}
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: 13, color: ACCENT, fontWeight: 700 }}
              >
                打开天凤加入 L{status.lobby_id} ↗
              </a>
            )}
          </div>

          {status.bots.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
              {status.bots.map((b: BotInfo) => (
                <span
                  key={b.name}
                  style={{
                    fontSize: 12,
                    padding: "3px 8px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: "var(--surface-subtle)",
                    color: "var(--text-primary)",
                  }}
                >
                  {b.name} = {shortSpec(b.spec)}
                </span>
              ))}
            </div>
          )}

          {/* R11-C：自动赛后导入引导（session_id 只作内部关联键）。
              规则：
              - awaiting_import + URL        → 本局已完成 / 自动导入
              - natural_exit + 无 URL        → 本局已结束 / 粘贴链接 fallback
              - manual_stop                  → 已停止呼出，无任何赛后 CTA */}
          {status.session_id && status.tenhou_log_url && (
            <div
              style={{
                marginBottom: 10,
                padding: "10px 12px",
                borderRadius: 6,
                border: "1px solid rgba(39,174,96,0.4)",
                background: "rgba(39,174,96,0.06)",
                color: "var(--text-secondary)",
              }}
            >
              <div style={{ fontWeight: 800, color: "var(--text-primary)", fontSize: 13, marginBottom: 4 }}>
                本局已完成
              </div>
              <div style={{ fontSize: 12, marginBottom: 8 }}>
                已自动获取本局牌谱链接，确认结果并导入账号/正式计分。
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  onClick={() => {
                    const params = new URLSearchParams();
                    if (status.tenhou_log_url) params.set("url", status.tenhou_log_url);
                    navigate(`${routes.matchImport}?${params.toString()}`, {
                      state: { session_id: status.session_id },
                    });
                  }}
                  style={{ ...ghostSmallBtn, borderColor: "#27ae60", color: "#27ae60", fontWeight: 700 }}
                >
                  确认结果 → 导入牌谱
                </button>
              </div>
            </div>
          )}
          {status.session_id &&
            !status.tenhou_log_url &&
            status.termination_reason === "natural_exit" && (
            <div
              style={{
                marginBottom: 10,
                padding: "10px 12px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--surface-subtle)",
                color: "var(--text-secondary)",
              }}
            >
              <div style={{ fontWeight: 800, color: "var(--text-primary)", fontSize: 13, marginBottom: 4 }}>
                本局已结束
              </div>
              <div style={{ fontSize: 12, marginBottom: 8 }}>
                未自动拿到牌谱链接（可能天凤未输出 log），去导入页粘贴链接即可；本局会话信息已自动带上。
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  onClick={() =>
                    navigate(routes.matchImport, { state: { session_id: status.session_id } })
                  }
                  style={ghostSmallBtn}
                >
                  打开导入页（粘贴链接）
                </button>
              </div>
            </div>
          )}

          {/* 本次启动的模型（不是账号事实；账号在赛后导入决定） */}
          {status.frozen_roster && status.frozen_roster.length > 0 && (
            <div
              style={{
                marginTop: 4,
                marginBottom: 10,
                fontSize: 12,
                padding: "8px 10px",
                borderRadius: 6,
                border: "1px solid rgba(142,68,173,0.3)",
                background: "rgba(142,68,173,0.05)",
                color: "var(--text-secondary)",
              }}
            >
              <div style={{ fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>
                本次启动的模型（session 冻结；账号/坐席由赛后导入决定）
              </div>
              {(status.frozen_roster ?? []).length > 0
                ? status.frozen_roster!.map((entry, index) => {
                    if (entry.launcher_slot === null || entry.launcher_slot === undefined) return null;
                    const model = models.find((m) => m.model_id === entry.model_id);
                    return (
                      <div key={index} style={{ padding: "2px 0" }}>
                        <b>{entry.expected_raw_name}</b>
                        {entry.model_id && (
                          <span style={{ color: "var(--text-muted)" }}>
                            {" "}→ {model?.label ?? entry.model_id}
                          </span>
                        )}
                      </div>
                    );
                  })
                : slots.map((modelId, index) => {
                    if (!modelId) return null;
                    const model = models.find((m) => m.model_id === modelId);
                    return (
                      <div key={index} style={{ padding: "2px 0" }}>
                        {index === 0 && filledSlots.length === 1
                          ? "NoName"
                          : `NoName-${index + 1}`}
                        <span style={{ color: "var(--text-muted)" }}> → {model?.label ?? modelId}</span>
                      </div>
                    );
                  })}
            </div>
          )}

          <div
            ref={logRef}
            style={{
              height: 320,
              overflow: "auto",
              background: "#0f1115",
              color: "#d6dde6",
              borderRadius: 8,
              padding: 10,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: 12,
              lineHeight: 1.5,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {status.log_tail.length > 0
              ? status.log_tail.join("\n")
              : "（暂无日志，呼出后这里会实时滚动显示 bot / gateway 输出）"}
          </div>
        </div>
      )}
    </PageShell>
  );
}

const labelStyle: CSSProperties = {
  fontSize: 12,
  color: "var(--text-muted)",
  fontWeight: 600,
  display: "block",
  marginBottom: 5,
};

const hintStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  marginTop: 4,
};

const inputStyle: CSSProperties = {
  height: 34,
  borderRadius: 6,
  border: "1px solid var(--border)",
  background: "var(--surface-subtle)",
  color: "var(--text-primary)",
  padding: "0 10px",
  fontSize: 13,
  boxSizing: "border-box",
};

const ghostSmallBtn: CSSProperties = {
  border: "1px solid var(--border)",
  background: "transparent",
  color: "var(--text-secondary)",
  borderRadius: 4,
  fontSize: 12,
  padding: "4px 10px",
  cursor: "pointer",
  whiteSpace: "nowrap",
};
