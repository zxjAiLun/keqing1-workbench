/**
 * BattleBoard — 牌桌样式令牌（设计系统 v2 §3）
 *
 * MahjongTable 内联样式的语义化单一事实源。
 * 颜色一律引用 CSS 变量，禁止裸 hex / rgba 字面量。
 */
import type { CSSProperties } from "react";

// ── 决策分析配色（Q/P、logit 柱、决策框） ─────────────────────
// bot = AI 实际选择，gt = 正确答案，both = 二者一致
export const decisionColors = {
  bot: "var(--negative)",
  gt: "var(--success)",
  both: "var(--seat-2)",
  /** logit 分数柱（无决策标注时） */
  score: "var(--accent)",
  /** teacher 模型柱 */
  teacher: "var(--seat-2)",
  /** 非激活 teacher 模型柱 */
  teacherInactive: "var(--table-text-faint)",
} as const;

/** 决策行的柔和底色（深色底上可读的色晕） */
export const decisionBg = {
  bot: "rgba(240,112,112,0.10)",
  gt: "rgba(79,195,138,0.10)",
  both: "rgba(180,154,232,0.12)",
  /** 斑马纹（深色主题下用暖白微光） */
  zebra: "rgba(240,234,217,0.03)",
} as const;

/** 决策标记边框（chip/小标记用） */
export const decisionBorder = {
  bot: "rgba(240,112,112,0.40)",
  gt: "rgba(79,195,138,0.40)",
  both: "rgba(180,154,232,0.45)",
} as const;

/** 中性柱/轨道色（logit bar 等） */
export const chartColors = {
  track: "rgba(240,234,217,0.08)",
  neutral: "var(--text-muted)",
} as const;

// ── 桌面质感层（§3.1：呢绒纹理 + 四边暗角） ────────────────────
/** 纹理层：黑白细线交织，整体不透明度由 --table-texture-opacity 控制 */
export const tableTextureLayer: CSSProperties = {
  position: "absolute",
  inset: 0,
  pointerEvents: "none",
  opacity: "var(--table-texture-opacity)" as unknown as number,
  background:
    "repeating-linear-gradient(0deg, rgba(255,255,255,0.5) 0 1px, transparent 1px 3px)," +
    "repeating-linear-gradient(90deg, rgba(0,0,0,0.6) 0 1px, transparent 1px 3px)",
};

/** 暗角层：四边压暗，聚焦中央 */
export const tableVignetteLayer: CSSProperties = {
  position: "absolute",
  inset: 0,
  pointerEvents: "none",
  background:
    "radial-gradient(ellipse 95% 95% at 50% 46%, transparent 58%, rgba(0,0,0,0.32) 100%)",
};

// ── 立直宣言横幅（§3.4：深色玻璃 + 金描边） ────────────────────
export const reachBanner: CSSProperties = {
  fontSize: 16,
  color: "var(--gold)",
  fontWeight: 800,
  padding: "14px 22px",
  borderRadius: 16,
  background: "var(--overlay-bg)",
  border: "1px solid var(--gold-border)",
  boxShadow: "var(--elev-2)",
  backdropFilter: "blur(16px)",
};

// ── 回放立直提示 chip ─────────────────────────────────────────
export const reachChip: CSSProperties = {
  padding: "6px 14px",
  borderRadius: 2,
  background: "var(--overlay-bg)",
  border: "1px solid var(--gold-border)",
  display: "flex",
  alignItems: "center",
  gap: 8,
};

export const reachChipIcon: CSSProperties = {
  width: 20,
  height: 20,
  borderRadius: 2,
  background: "var(--gold)",
  color: "var(--accent-text)",
  fontWeight: 900,
  fontSize: 11,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

// ── 行动中指示 ────────────────────────────────────────────────
export const actorIndicatorBase: CSSProperties = {
  padding: "3px 10px",
  borderRadius: 3,
  fontSize: 10,
  fontWeight: 600,
  background: "rgba(12,10,7,0.6)",
  whiteSpace: "nowrap",
};

// ── 左下控制条开关（§3.4） ────────────────────────────────────
export function ctrlToggleStyle(active: boolean): CSSProperties {
  return {
    padding: "3px 12px",
    borderRadius: 5,
    border: `1px solid ${active ? "var(--gold-border)" : "var(--ctrlbar-border)"}`,
    background: active ? "var(--ctrlbar-active-bg)" : "transparent",
    color: active ? "var(--ctrlbar-active-text)" : "var(--ctrlbar-text)",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
    transition: "all 0.15s",
    display: "flex",
    alignItems: "center",
    gap: 5,
    letterSpacing: "0.02em",
  };
}

export function ctrlToggleDotStyle(active: boolean): CSSProperties {
  return {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: active ? "var(--gold)" : "var(--control-muted)",
    display: "inline-block",
    flexShrink: 0,
    transition: "background 0.15s",
  };
}
