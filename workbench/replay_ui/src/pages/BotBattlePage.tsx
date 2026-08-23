// src/replay_ui/src/pages/BotBattlePage.tsx
import { useState, useEffect, useRef } from "react";
import type { CSSProperties } from "react";
import { MahjongTable } from "../components/BattleBoard/MahjongTable";
import { fetchWithTimeout } from "../api/battleApi";
import { PageShell, SectionTitle } from "../components/Layout/PageScaffold";
import type { BattleState } from "../types/battle";
import type { BotType } from "../types/bot";
import { DEFAULT_BOT_TYPE, GUI_BOT_CATALOG, getBotCatalogEntry } from "../utils/botCatalog";

export function BotBattlePage() {
  const [gameId, setGameId] = useState<string | null>(null);
  const [state, setState] = useState<BattleState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [botModel, setBotModel] = useState<BotType>(DEFAULT_BOT_TYPE);
  const [gameLength, setGameLength] = useState<"tonpu" | "hanchan">("hanchan");
  const pollingRef = useRef<number | null>(null);
  const mountedRef = useRef(true);

  const startBotBattle = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchWithTimeout("/api/battle/start_4bot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot_model: botModel, game_length: gameLength }),
      });
      if (!res.ok) throw new Error("启动失败");
      const data = await res.json();
      setGameId(data.game_id);
      setState(data.state);
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (!gameId) return;
    if (pollingRef.current) clearInterval(pollingRef.current);
    pollingRef.current = window.setInterval(async () => {
      try {
        const res = await fetchWithTimeout(`/api/battle/state/${gameId}?player_id=0`);
        if (!res.ok) return;
        const data = await res.json();
        if (mountedRef.current) {
          setState(data.state);
        }
      } catch { /* ignore */ }
    }, 500);
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, [gameId]);

  const downloadExport = async (format: "mjai" | "tenhou6") => {
    if (!gameId) return;
    try {
      const endpoint = format === "mjai" ? "export_mjai" : "export_tenhou6";
      const res = await fetchWithTimeout(`/api/battle/${endpoint}/${gameId}`);
      if (!res.ok) throw new Error("导出失败");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${gameId}_${format}.${format === "mjai" ? "jsonl" : "json"}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert("导出失败");
    }
  };

  const selectedBot = getBotCatalogEntry(botModel);

  if (!state) {
    return (
      <PageShell width={1120}>
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
            <div style={{ fontSize: 20, fontWeight: 800, color: "var(--text-primary)" }}>4 Bot 对战</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>启动后牌桌占满主区域，导出放在顶部工具组。</div>
          </div>
          <button
            onClick={startBotBattle}
            disabled={loading}
            className="btn-primary"
            style={{ height: 34, padding: "0 16px", fontSize: 13, background: loading ? "var(--text-muted)" : "var(--accent)" }}
          >
            {loading ? "启动中..." : "开始对战"}
          </button>
        </div>

        <div className="card" style={{ padding: 12 }}>
          <SectionTitle title="设置" description="选择同一组模型运行四家自动对战。" />
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
            <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>
              规则长度
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
              {([
                ["tonpu", "东风"],
                ["hanchan", "半庄"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setGameLength(value)}
                  style={{
                    height: 32,
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 700,
                    border: `1px solid ${gameLength === value ? "var(--accent)" : "var(--border)"}`,
                    background: gameLength === value ? "var(--accent-bg)" : "var(--surface-subtle)",
                    color: gameLength === value ? "var(--accent)" : "var(--text-primary)",
                    cursor: "pointer",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <label style={{ fontSize: 12, color: "var(--text-muted)", fontWeight: 500 }}>
              Bot 类型
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 6 }}>
              {GUI_BOT_CATALOG.map((bot) => (
                <button
                  key={bot.value}
                  onClick={() => setBotModel(bot.value)}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 7,
                    fontSize: 13,
                    fontWeight: 500,
                    border: `1px solid ${botModel === bot.value ? "var(--accent)" : "var(--border)"}`,
                    background: botModel === bot.value ? "var(--accent-bg)" : "var(--surface-subtle)",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                    transition: "all var(--transition)",
                    textAlign: "left",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
                    <span style={{ fontWeight: 700 }}>{bot.label}</span>
                    <span style={{ fontSize: 11, color: botModel === bot.value ? "var(--accent)" : "var(--text-muted)" }}>{bot.badge}</span>
                  </div>
                  <div style={{ marginTop: 3, fontSize: 12, color: "var(--text-muted)" }}>{bot.description}</div>
                </button>
              ))}
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
              当前选择：{selectedBot.label}，{selectedBot.description}
            </div>
          </div>
          {error && (
            <div style={{ fontSize: 13, textAlign: "center", color: "var(--error)", marginTop: 8 }}>
              {error}
            </div>
          )}
        </div>

      </PageShell>
    );
  }

  return (
    <div style={{ height: "100%", background: "var(--page-bg)", position: "relative", overflow: "hidden" }}>
      <div
        style={{
          position: "absolute",
          top: 10,
          right: 10,
          zIndex: 100,
          display: "flex",
          gap: 6,
          flexWrap: "wrap",
          justifyContent: "flex-end",
        }}
      >
        <span style={botBattleStatusStyle}>{selectedBot.label} · {state.phase}</span>
        <button onClick={() => downloadExport("mjai")} style={botBattleToolButtonStyle}>Mjai</button>
        <button onClick={() => downloadExport("tenhou6")} style={botBattleToolButtonStyle}>Tenhou6</button>
        {state.phase === "ended" && (
          <button onClick={() => { setState(null); setGameId(null); }} style={botBattleToolButtonStyle}>再来一局</button>
        )}
      </div>
      {state.phase === "ended" && (
        <div
          style={{
            position: "absolute",
            left: 10,
            top: 10,
            zIndex: 100,
            ...botBattleStatusStyle,
          }}
        >
          对局结束，可导出牌谱
        </div>
      )}
      <MahjongTable
        state={state}
        onAction={() => {}}
        isMyTurn={false}
        selectedTile={null}
        onTileSelect={() => {}}
        autoHora={false} setAutoHora={() => {}}
        noMeld={false} setNoMeld={() => {}}
        autoTsumogiri={false} setAutoTsumogiri={() => {}}
      />
    </div>
  );
}

const botBattleToolButtonStyle: CSSProperties = {
  padding: "5px 9px",
  borderRadius: 6,
  border: "1px solid var(--overlay-border)",
  background: "var(--overlay-bg)",
  color: "var(--control-muted)",
  fontSize: 12,
  fontWeight: 700,
  cursor: "pointer",
};

const botBattleStatusStyle: CSSProperties = {
  padding: "5px 8px",
  borderRadius: 6,
  border: "1px solid var(--overlay-border)",
  background: "var(--overlay-bg)",
  color: "var(--control-muted)",
  fontSize: 12,
};
