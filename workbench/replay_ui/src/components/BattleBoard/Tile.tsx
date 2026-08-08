// src/replay_ui/src/components/BattleBoard/Tile.tsx
import { TILE_SVG_NAME } from "../../utils/tileUtils";
import { TILE_SIZES } from "./tileSizes";

interface TileProps {
  tile: string;
  size?: "small" | "normal" | "large";
  selected?: boolean;
  highlighted?: boolean;
  onClick?: () => void;
  className?: string;
}

export function Tile({ tile, size = "normal", selected, highlighted, onClick, className = "" }: TileProps) {
  const dim = TILE_SIZES[size];
  const tileSvgName = TILE_SVG_NAME[tile];

  const outline = selected
    ? "2px solid var(--gold)"
    : highlighted
    ? "2px solid #e85a5a"
    : "1px solid rgba(0,0,0,0.22)";

  return (
    <div
      className={`relative inline-flex items-center justify-center cursor-pointer ${className}`}
      style={{
        width: dim.w,
        height: dim.h,
        transition: "outline-color var(--table-transition-fast), opacity var(--table-transition-fast)",
        boxShadow: "none",
        borderRadius: 3,
        background: "#f5f2ea",
        outline,
        outlineOffset: -1,
        flexShrink: 0,
        overflow: "hidden",
      }}
      onClick={onClick}
    >
      {tileSvgName ? (
        <img
          src={`/tiles/${tileSvgName}.svg`}
          alt={tile}
          style={{ width: dim.w - 4, height: dim.h - 4, display: "block" }}
        />
      ) : (
        <div
          style={{
            width: dim.w - 4,
            height: dim.h - 4,
            background: "#eee9dd",
            borderRadius: 2,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#202020",
            fontSize: Math.max(10, Math.floor(dim.w * 0.28)),
            fontWeight: 700,
          }}
        >
          {tile}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

interface TileBackProps {
  size?: "small" | "normal" | "large";
  className?: string;
  orientation?: 0 | 90 | 180 | 270;
}

export function TileBack({ size = "normal", className = "", orientation = 0 }: TileBackProps) {
  const dim = TILE_SIZES[size];
  const rotated = orientation === 90 || orientation === 270;

  return (
    <div
      className={`relative ${className}`}
      style={{
        width: rotated ? dim.h : dim.w,
        height: rotated ? dim.w : dim.h,
        position: "relative",
        flexShrink: 0,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          width: dim.w,
          height: dim.h,
          transform: `translate(-50%, -50%) rotate(${orientation}deg)`,
          transformOrigin: "center center",
        borderRadius: 3,
        background: "#8b2222",
        border: "1px solid rgba(255,255,255,0.55)",
        boxShadow: "none",
        overflow: "hidden",
        transition: "transform var(--table-transition-mid), filter var(--table-transition-mid)",
        }}
      >
        <div style={{
          position: "absolute", inset: 3,
          background: `
            repeating-linear-gradient(45deg,
              rgba(255,255,255,0.10) 0px,
              rgba(255,255,255,0.10) 1px,
              transparent 1px,
              transparent 7px
            ),
            repeating-linear-gradient(-45deg,
              rgba(255,255,255,0.10) 0px,
              rgba(255,255,255,0.10) 1px,
              transparent 1px,
              transparent 7px
            )
          `,
          borderRadius: 2,
        }} />
      </div>
    </div>
  );
}
