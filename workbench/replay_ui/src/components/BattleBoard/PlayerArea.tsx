// src/replay_ui/src/components/BattleBoard/PlayerArea.tsx
import { Tile } from "./Tile";
import { TileBack } from "./Tile";
import { sortHand } from "../../utils/tileUtils";
import type { DiscardEntry, MeldEntry } from "../../types/battle";
import { JIKAZE_CN } from "../../utils/constants";

interface PlayerAreaProps {
  playerId: number;
  playerInfo: { player_id: number; name: string; type: string };
  hand: string[];
  tsumoPai?: string | null;
  discards: DiscardEntry[];
  melds: MeldEntry[];
  reached: boolean;
  isActive: boolean;
  isHuman: boolean;
  highlightTile?: string | null;
  onTileClick?: (tile: string) => void;
}

export function PlayerArea({
  playerId,
  playerInfo,
  hand,
  tsumoPai,
  discards,
  melds,
  reached,
  isActive,
  isHuman,
  highlightTile,
  onTileClick,
}: PlayerAreaProps) {
  const sortedHand = sortHand(hand, tsumoPai || null);
  const jikaze = JIKAZE_CN[playerId];
  const playerColor = ["#e74c3c", "#3498db", "#8e44ad", "#27ae60"][playerId];

  const containerStyle: React.CSSProperties = {
    background: isActive ? `${playerColor}15` : 'rgba(0,0,0,0.04)',
    border: isActive ? `2px solid ${playerColor}` : '2px solid transparent',
    borderRadius: 10,
    padding: "10px 12px",
    transition: "all 0.2s ease",
  };

  if (!isHuman) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', ...containerStyle }}>
        {/* 玩家信息 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <div className="seat-indicator" style={{ width: 8, height: 8, borderRadius: '50%', background: isActive ? playerColor : 'transparent' }} />
          <span style={{ fontWeight: 700, fontSize: 13, color: playerColor }}>{playerInfo.name}</span>
          <span style={{ fontSize: 11, color: '#9ca3af' }}>{jikaze}</span>
          {reached && (
            <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#fef3c7', color: '#92400e', border: '1px solid #fcd34d', fontWeight: 600 }}>
              立直
            </span>
          )}
        </div>

        {/* 副露 */}
        <div style={{ display: 'flex', gap: 4, marginBottom: 8 }}>
          {melds.slice(0, 4).map((meld, mi) => (
            <div key={mi} style={{ display: 'flex', gap: 2 }}>
              {meld.consumed.map((_, ci) => (
                <TileBack key={ci} size="small" />
              ))}
            </div>
          ))}
        </div>

        {/* 舍牌 */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 2 }}>
          {discards.slice(0, 12).map((d, i) => (
            <Tile key={i} tile={d.pai} size="small" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', ...containerStyle }}>
      {/* 玩家信息 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <div className="seat-indicator" style={{ width: 10, height: 10, borderRadius: '50%', background: isActive ? playerColor : 'transparent' }} />
        <span style={{ fontWeight: 700, fontSize: 15, color: playerColor }}>{playerInfo.name}</span>
        <span style={{ fontSize: 12, color: '#9ca3af' }}>{jikaze}</span>
        {reached && (
          <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#fef3c7', color: '#92400e', border: '1px solid #fcd34d', fontWeight: 600 }}>
            立直
          </span>
        )}
      </div>

      {/* 手牌 */}
      <div style={{ display: 'flex', gap: 4, marginBottom: 12, flexWrap: 'wrap', justifyContent: 'center' }}>
        {sortedHand.map((tile, i) => (
          <Tile
            key={`${tile}-${i}`}
            tile={tile}
            size="normal"
            selected={tile === highlightTile}
            onClick={() => onTileClick?.(tile)}
          />
        ))}
        {tsumoPai && (
          <div style={{ marginLeft: 4, border: '2px solid #d4a853', borderRadius: 4, boxShadow: '0 0 8px rgba(212,168,83,0.4)' }}>
            <Tile tile={tsumoPai} size="normal" />
          </div>
        )}
      </div>

      {/* 舍牌 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 2 }}>
        {discards.map((d, i) => (
          <div key={i} style={{ position: 'relative' }}>
            <Tile tile={d.pai} size="small" />
            {d.tsumogiri && (
              <div style={{ position: 'absolute', top: -2, right: -2, width: 8, height: 8, borderRadius: '50%', background: '#e74c3c', border: '1px solid rgba(0,0,0,0.3)' }} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
