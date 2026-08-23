// src/replay_ui/src/pages/BattlePage.tsx
import { useState, useCallback, useEffect, useRef } from "react";
import type { CSSProperties } from "react";
import { MahjongTable } from "../components/BattleBoard/MahjongTable";
import { Tile } from "../components/BattleBoard/Tile";
import { startBattle, doAction, closeBattle, fetchWithTimeout, nextKyoku } from "../api/battleApi";
import { useAutoActions } from "../hooks/useAutoActions";
import { useBattlePolling } from "../hooks/useBattlePolling";
import { useConnectionManager } from "../hooks/useConnectionManager";
import type { BattleState, Action, StartBattleRequest } from "../types/battle";
import type { BotType } from "../types/bot";
import { DEFAULT_BOT_TYPE, GUI_BOT_CATALOG, getBotCatalogEntry } from "../utils/botCatalog";
import { sortHand } from "../utils/tileUtils";

export function BattlePage() {
  const [gameId, setGameId] = useState<string | null>(null);
  const [state, setState] = useState<BattleState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTile, setSelectedTile] = useState<string | null>(null);
  const [selectedTileIdx, setSelectedTileIdx] = useState<number | null>(null);
  const [playerName, setPlayerName] = useState("玩家");
  const [botModel, setBotModel] = useState<BotType>(DEFAULT_BOT_TYPE);
  const [autoHora, setAutoHora] = useState(true);       // 自动胡牌，默认开
  const [noMeld, setNoMeld] = useState(false);           // 不响应附露，默认关
  const [autoTsumogiri, setAutoTsumogiri] = useState(false); // 自动摸切，默认关
  const [gameLength, setGameLength] = useState<"tonpu" | "hanchan">("hanchan");
  const [nextCountdown, setNextCountdown] = useState(5);
  const pendingActionRef = useRef(false);
  const humanPlayerId = state?.human_player_id ?? 0;
  const forcedAutoTsumogiri =
    Boolean(state?.reached?.[humanPlayerId]) &&
    !Boolean(state?.pending_reach?.[humanPlayerId]);
  const effectiveAutoTsumogiri = autoTsumogiri || forcedAutoTsumogiri;

  const startNewGame = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const req: StartBattleRequest = { player_name: playerName, bot_count: 3, bot_model: botModel, game_length: gameLength };
      const res = await startBattle(req);
      setGameId(res.game_id);
      setState(res.state);
    } catch (e) {
      setError(e instanceof Error ? e.message : "启动失败");
    } finally {
      setLoading(false);
    }
  }, [playerName, botModel, gameLength]);

  const handleAction = useCallback(
    async (action: Action) => {
      if (!gameId) return;
      if (pendingActionRef.current) return;
      pendingActionRef.current = true;
      setLoading(true);
      try {
        const res = await doAction({ game_id: gameId, action });
        setState(res.state);
      } catch (e) {
        setError(e instanceof Error ? e.message : "操作失败");
      } finally {
        setLoading(false);
        pendingActionRef.current = false;
      }
    },
    [gameId]
  );

  const downloadExport = useCallback(async (format: "mjai" | "tenhou6") => {
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
  }, [gameId]);

  const handleNextKyoku = useCallback(async () => {
    if (!gameId || pendingActionRef.current) return;
    pendingActionRef.current = true;
    setLoading(true);
    try {
      const res = await nextKyoku(gameId);
      setState(res.state);
      setSelectedTile(null);
      setSelectedTileIdx(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "续局失败");
    } finally {
      setLoading(false);
      pendingActionRef.current = false;
    }
  }, [gameId]);

  const {
    isMyTurn,
    legalActions,
    willAutoPassOnlyNone,
    willAutoHora,
    willAutoDeclineMeld,
    willAutoTsumogiri,
    willAutoResolve,
    needsDecision,
  } = useAutoActions({
    state,
    autoHora,
    noMeld,
    autoTsumogiri: effectiveAutoTsumogiri,
  });

  useEffect(() => {
    if (isMyTurn) return;
    setSelectedTile(null);
    setSelectedTileIdx(null);
  }, [isMyTurn, state?.game_id, state?.actor_to_move, state?.last_discard]);

  // 自动化：自动胡牌 / 不响应附露 / 自动摸切
  useEffect(() => {
    if (!isMyTurn || !gameId || pendingActionRef.current) return;
    if (willAutoPassOnlyNone || willAutoDeclineMeld) {
      const noneAction = legalActions.find(a => a.type === "none");
      if (noneAction) { handleAction(noneAction); return; }
    }
    if (willAutoHora) {
      const horaAction = legalActions.find(a => a.type === "hora");
      if (horaAction) { handleAction(horaAction); return; }
    }
    if (willAutoTsumogiri) {
      const tsumogiriAction = legalActions.find(a => a.type === "dahai" && a.tsumogiri);
      if (tsumogiriAction) { handleAction(tsumogiriAction); return; }
    }
  }, [isMyTurn, gameId, state?.legal_actions, willAutoPassOnlyNone, willAutoHora, willAutoDeclineMeld, willAutoTsumogiri, legalActions, handleAction]);

  useBattlePolling({
    gameId,
    state,
    needsDecision,
    pendingActionRef,
    onStateUpdate: setState,
  });

  useEffect(() => {
    if (state?.phase !== "hand_result" || !state.can_continue) return;
    setNextCountdown(5);
    const startedAt = Date.now();
    const intervalId = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - startedAt) / 1000);
      setNextCountdown(Math.max(0, 5 - elapsed));
    }, 250);
    const timeoutId = window.setTimeout(() => {
      handleNextKyoku();
    }, 5000);
    return () => {
      window.clearInterval(intervalId);
      window.clearTimeout(timeoutId);
    };
  }, [state?.phase, state?.can_continue, state?.bakaze, state?.kyoku, state?.honba, handleNextKyoku]);

  // 退出对局
  const [showQuitConfirm, setShowQuitConfirm] = useState(false);
  const handleQuit = useCallback(async () => {
    if (!gameId) return;
    try {
      await fetch(`/api/battle/quit/${gameId}`, { method: 'POST' });
    } catch { /* ignore */ }
    setGameId(null);
    setState(null);
    setShowQuitConfirm(false);
  }, [gameId]);

  // 连接管理（心跳/断线/重连）
  const { status: connStatus, reconnectAttempts } = useConnectionManager({
    gameId: state?.phase === 'playing' ? gameId : null,
    playerId: state?.human_player_id ?? 0,
    onStateUpdate: setState,
    onGiveUp: () => { setGameId(null); setState(null); },
  });

  const selectedBot = getBotCatalogEntry(botModel);

  if (!state) {
    return (
      <div
        style={{
          background: 'var(--page-bg)',
          height: '100%',
          overflow: 'auto',
          padding: 14,
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <div>
            <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--text-primary)' }}>人机对战</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>紧凑设置栏，启动后牌桌占满工作区。</div>
          </div>
          <button
            onClick={startNewGame}
            disabled={loading}
            className="btn-primary"
            style={{ height: 34, padding: '0 16px', fontSize: 13 }}
          >
            {loading ? '启动中...' : '开始对战'}
          </button>
        </div>

        <div
          className="card"
          style={{
            padding: 12,
            display: 'grid',
            gridTemplateColumns: 'minmax(180px, 240px) 1fr',
            gap: 12,
            alignItems: 'start',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <input
              type="text"
              placeholder="你的名字"
              value={playerName}
              onChange={(e) => setPlayerName(e.target.value)}
              style={{
                width: '100%',
                padding: '9px 12px',
                border: '1px solid var(--border)',
                borderRadius: 7,
                fontSize: 13,
                background: 'var(--card-bg)',
                color: 'var(--text-primary)',
                outline: 'none',
              }}
            />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700 }}>规则长度</label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
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
                      border: `1px solid ${gameLength === value ? 'var(--accent)' : 'var(--border)'}`,
                      background: gameLength === value ? 'var(--accent-bg)' : 'var(--card-bg)',
                      color: gameLength === value ? 'var(--accent)' : 'var(--text-primary)',
                      fontSize: 13,
                      fontWeight: 700,
                      cursor: 'pointer',
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <label style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 700 }}>对手模型</label>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 6 }}>
                {GUI_BOT_CATALOG.map((bot) => (
                  <button
                    key={bot.value}
                    onClick={() => setBotModel(bot.value)}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 7,
                      fontSize: 13,
                      fontWeight: 500,
                      border: `1px solid ${botModel === bot.value ? 'var(--accent)' : 'var(--border)'}`,
                      background: botModel === bot.value ? 'var(--accent-bg)' : 'var(--card-bg)',
                      color: 'var(--text-primary)',
                      cursor: 'pointer',
                      textAlign: 'left',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
                      <span style={{ fontWeight: 700 }}>{bot.label}</span>
                      <span style={{ fontSize: 11, color: botModel === bot.value ? 'var(--accent)' : 'var(--text-muted)' }}>{bot.badge}</span>
                    </div>
                    <div style={{ marginTop: 3, fontSize: 11, color: 'var(--text-muted)' }}>{bot.description}</div>
                  </button>
                ))}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                当前选择：{selectedBot.label}，{selectedBot.description}
              </div>
            </div>
            {error && (
              <div style={{
                fontSize: 13, color: 'var(--error)',
                padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)'
              }}>
                {error}
              </div>
            )}
          </div>
        </div>

      </div>
    );
  }

  return (
    <div style={{ height: '100%', background: 'var(--page-bg)', position: 'relative', overflow: 'hidden' }}>
      <MahjongTable
        state={state}
        onAction={handleAction}
        isMyTurn={isMyTurn}
        selectedTile={selectedTile}
        selectedTileIdx={selectedTileIdx}
        onTileSelect={(tile, idx) => { setSelectedTile(tile); setSelectedTileIdx(idx ?? null); }}
        autoHora={autoHora} setAutoHora={setAutoHora}
        noMeld={noMeld} setNoMeld={setNoMeld}
        autoTsumogiri={autoTsumogiri} setAutoTsumogiri={setAutoTsumogiri}
        suppressActionBar={willAutoResolve}
        actionPending={loading}
      />

      <div
        style={{
          position: 'absolute',
          top: 10,
          right: 10,
          zIndex: 100,
          display: 'flex',
          gap: 6,
          flexWrap: 'wrap',
          justifyContent: 'flex-end',
        }}
      >
        <span style={{
          padding: '5px 8px',
          borderRadius: 6,
          border: '1px solid var(--overlay-border)',
          background: 'var(--overlay-bg)',
          color: 'var(--control-muted)',
          fontSize: 12,
        }}>
          {selectedBot.label} · {connStatus}
        </span>
        <button onClick={() => downloadExport("mjai")} style={battleToolButtonStyle}>Mjai</button>
        <button onClick={() => downloadExport("tenhou6")} style={battleToolButtonStyle}>Tenhou6</button>
        <button
          onClick={() => setShowQuitConfirm(true)}
          style={{ ...battleToolButtonStyle, borderColor: 'var(--negative)', color: 'var(--negative)' }}
        >
          退出
        </button>
      </div>

      {(state.phase === "hand_result" || state.phase === "ended") && (
        <BattleResultOverlay
          state={state}
          loading={loading}
          nextCountdown={nextCountdown}
          onNext={handleNextKyoku}
          onRestart={async () => {
            if (gameId) await closeBattle(gameId);
            setState(null);
            setGameId(null);
            setSelectedTile(null);
            setSelectedTileIdx(null);
          }}
          onExport={downloadExport}
        />
      )}

      {/* 退出确认对话框 */}
      {showQuitConfirm && (
        <div style={{
          position: 'fixed', inset: 0, background: 'var(--result-overlay-bg)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
        }}>
          <div style={{
            background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 12, padding: 28, minWidth: 280,
            boxShadow: 'var(--elev-3)', textAlign: 'center', color: 'var(--text-primary)',
          }}>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>确认退出对局？</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 20 }}>退出后该座位将由 Bot 接管，对局继续。</div>
            <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
              <button onClick={() => setShowQuitConfirm(false)}
                style={{ padding: '8px 20px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-3)', color: 'var(--text-primary)', cursor: 'pointer', fontSize: 13 }}>
                取消
              </button>
              <button onClick={handleQuit}
                style={{ padding: '8px 20px', borderRadius: 6, border: 'none', background: 'var(--negative)', color: 'var(--accent-text)', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
                确认退出
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 断线/重连提示覆盖层 */}
      {connStatus === 'disconnected' && (
        <div style={{
          position: 'fixed', inset: 0, background: 'var(--result-overlay-bg)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 300,
        }}>
          <div style={{
            background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 12, padding: 32, minWidth: 300,
            boxShadow: 'var(--elev-3)', textAlign: 'center', color: 'var(--text-primary)',
          }}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⚠️</div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>连接已断开</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>正在尝试重连... ({reconnectAttempts}/5)</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>若长时间无法重连，可以放弃本局。</div>
            <button onClick={() => { setGameId(null); setState(null); }}
              style={{ marginTop: 20, padding: '8px 20px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface-3)', color: 'var(--text-primary)', cursor: 'pointer', fontSize: 13 }}>
              放弃本局
            </button>
          </div>
        </div>
      )}

      {connStatus === 'reconnecting' && (
        <div style={{
          position: 'fixed', bottom: 24, left: '50%', transform: 'translateX(-50%)',
          background: 'var(--overlay-bg)', color: 'var(--text-primary)', border: '1px solid var(--overlay-border)', borderRadius: 20,
          padding: '8px 20px', fontSize: 13, zIndex: 300,
        }}>
          重连中... ({reconnectAttempts}/5)
        </div>
      )}
    </div>
  );
}

function BattleResultOverlay({
  state,
  loading,
  nextCountdown,
  onNext,
  onRestart,
  onExport,
}: {
  state: BattleState;
  loading: boolean;
  nextCountdown: number;
  onNext: () => void;
  onRestart: () => void | Promise<void>;
  onExport: (format: "mjai" | "tenhou6") => void;
}) {
  const round = state.round_result;
  const isFinal = state.phase === "ended";
  const resultScores = round?.scores ?? state.game_result?.final_scores ?? state.scores;
  const deltas = round?.deltas ?? [0, 0, 0, 0];
  const title = round?.type === "ryukyoku"
    ? "流局"
    : round?.type === "hora"
      ? `${round.fu ?? 0}符${round.han ?? 0}翻`
      : "对局结束";
  const subtitle = round?.type === "hora"
    ? `${state.player_info[round.actor ?? 0]?.name ?? `P${round.actor ?? 0}`} 和了`
    : round?.type === "ryukyoku"
      ? "本局流局"
      : "最终结算";
  const ratingByName = new Map(
    (state.game_result?.rating_updates ?? []).map((item) => [item.display_name, item])
  );
  const rankedSeats = [...resultScores.entries()].sort((left, right) => right[1] - left[1]);
  const winner = round?.type === "hora" && round.actor !== undefined ? Number(round.actor) : null;
  const winTile = round?.type === "hora" ? round.pai ?? null : null;
  const winnerTiles = winner != null ? [...(state.revealed_hands?.[winner] ?? [])] : [];
  const handWithoutWin = winTile ? removeOneTileForDisplay(winnerTiles, winTile) : winnerTiles;
  const yakuLines = round?.type === "hora"
    ? formatYakuDetails(round.yaku_details, round.yaku)
    : [];

  return (
    <div style={resultOverlayBackdropStyle}>
      <div style={resultOverlayPanelStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16 }}>
          <div style={{ color: "var(--result-title)", fontSize: 30, fontWeight: 500 }}>{isFinal ? "最终结算" : title}</div>
          <div style={{ color: "var(--result-subtitle)", fontSize: 14 }}>{subtitle}</div>
        </div>

        {winner != null && winnerTiles.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {sortHand(handWithoutWin, null).map((tile, idx) => (
              <Tile key={`${tile}-${idx}`} tile={tile} size="small" />
            ))}
            {winTile && (
              <div style={{ marginLeft: 8, outline: "2px solid var(--result-negative)", outlineOffset: 1, borderRadius: 3 }}>
                <Tile tile={winTile} size="small" />
              </div>
            )}
          </div>
        )}

        {yakuLines.length ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "4px 14px", color: "var(--result-title)", fontSize: 15, lineHeight: 1.5 }}>
            {yakuLines.map((line, idx) => (
              <span key={`${line}-${idx}`}>{line}</span>
            ))}
          </div>
        ) : null}

        <div style={{ display: "grid", gap: 6 }}>
          {rankedSeats.map(([pid, score], rank) => {
            const player = state.player_info[pid];
            const delta = deltas[pid] ?? 0;
            const rating = ratingByName.get(player?.name ?? "");
            return (
              <div key={pid} style={resultRowStyle}>
                <span style={{ color: "var(--result-muted)", width: 34 }}>{rank + 1}位</span>
                <span style={{ flex: 1 }}>{player?.name ?? `P${pid}`}</span>
                <span style={{ width: 90, textAlign: "right" }}>{score.toLocaleString()}</span>
                <span style={{ width: 86, textAlign: "right", color: delta >= 0 ? "var(--result-positive)" : "var(--result-negative)" }}>
                  {delta === 0 ? "" : `${delta > 0 ? "+" : ""}${delta.toLocaleString()}`}
                </span>
                {isFinal && (
                  <span style={{ width: 112, textAlign: "right", color: rating && rating.rating_delta >= 0 ? "var(--result-positive)" : "var(--result-negative)" }}>
                    {rating ? `${rating.rating_delta >= 0 ? "+" : ""}${rating.rating_delta.toFixed(1)} R` : ""}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
          {!isFinal && state.can_continue && (
            <button onClick={onNext} disabled={loading} style={resultPrimaryButtonStyle}>
              下一局 {nextCountdown}
            </button>
          )}
          {isFinal && (
            <button onClick={onRestart} style={resultPrimaryButtonStyle}>再来一场</button>
          )}
          <button onClick={() => onExport("mjai")} style={resultSecondaryButtonStyle}>Mjai</button>
          <button onClick={() => onExport("tenhou6")} style={resultSecondaryButtonStyle}>Tenhou6</button>
        </div>
      </div>
    </div>
  );
}

function removeOneTileForDisplay(tiles: string[], target: string): string[] {
  const normalizedTarget = normalizeAkaForDisplay(target);
  const copy = [...tiles];
  const index = copy.findIndex((tile) => normalizeAkaForDisplay(tile) === normalizedTarget);
  if (index >= 0) copy.splice(index, 1);
  return copy;
}

function normalizeAkaForDisplay(tile: string): string {
  if (tile === "5mr") return "5m";
  if (tile === "5pr") return "5p";
  if (tile === "5sr") return "5s";
  return tile;
}

function formatYakuDetails(
  details: NonNullable<BattleState["round_result"]>["yaku_details"] | undefined,
  fallback: string[] | undefined,
): string[] {
  if (Array.isArray(details) && details.length > 0) {
    return details.map((detail) => {
      const name = detail.name || detail.key || "役";
      const han = Number(detail.han ?? 0);
      return han > 0 ? `${name} ${han}番` : name;
    });
  }
  return (fallback ?? []).map((name) => String(name));
}

const battleToolButtonStyle: CSSProperties = {
  padding: '5px 9px',
  borderRadius: 6,
  border: '1px solid var(--overlay-border)',
  background: 'var(--overlay-bg)',
  color: 'var(--control-muted)',
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
};

const resultOverlayBackdropStyle: CSSProperties = {
  position: "absolute",
  inset: 0,
  zIndex: 150,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "var(--result-overlay-bg)",
};

const resultOverlayPanelStyle: CSSProperties = {
  width: 620,
  maxWidth: "92vw",
  padding: "28px 32px 24px",
  border: "1px solid var(--result-panel-border)",
  background: "var(--result-panel-bg)",
  boxShadow: "var(--result-panel-shadow)",
  display: "flex",
  flexDirection: "column",
  gap: 18,
};

const resultRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  minHeight: 31,
  color: "var(--result-title)",
  fontSize: 18,
  fontWeight: 500,
  fontVariantNumeric: "tabular-nums",
};

const resultPrimaryButtonStyle: CSSProperties = {
  height: 34,
  padding: "0 16px",
  border: "1px solid var(--gold-border)",
  background: "var(--gold-bg)",
  color: "var(--gold)",
  fontSize: 13,
  fontWeight: 700,
  cursor: "pointer",
};

const resultSecondaryButtonStyle: CSSProperties = {
  ...resultPrimaryButtonStyle,
  background: "transparent",
  color: "var(--result-subtitle)",
};
