/**
 * KEQING MJ — Design Tokens（设计系统 v2，见 docs/DESIGN_SYSTEM.md）
 *
 * All spacing/sizing/z-index values exposed as constants.
 * CSS variables are defined in globals.css; these JS constants
 * are for TypeScript consumption (e.g. inline styles, JSX).
 *
 * 颜色一律通过 CSS 变量引用（var(--xxx)），保证主题切换即时生效；
 * 禁止在此文件写裸 hex。
 * Token naming follows semantic intent, not raw value.
 */

export const t = {
  // ── Color（CSS 变量镜像，勿写裸 hex） ─────────────────────
  color: {
    pageBg: 'var(--page-bg)',
    surface1: 'var(--surface-1)',
    surface2: 'var(--surface-2)',
    surface3: 'var(--surface-3)',
    border: 'var(--border)',
    borderStrong: 'var(--border-strong)',
    textPrimary: 'var(--text-primary)',
    textSecondary: 'var(--text-secondary)',
    textMuted: 'var(--text-muted)',
    textFaint: 'var(--text-faint)',
    accent: 'var(--accent)',
    accentHover: 'var(--accent-hover)',
    accentText: 'var(--accent-text)',
    accentBg: 'var(--accent-bg)',
    accentBorder: 'var(--accent-border)',
    positive: 'var(--positive)',
    negative: 'var(--negative)',
    success: 'var(--success)',
    warning: 'var(--warning)',
    info: 'var(--info)',
    gold: 'var(--gold)',
    goldBg: 'var(--gold-bg)',
    goldBorder: 'var(--gold-border)',
    seat: ['var(--seat-0)', 'var(--seat-1)', 'var(--seat-2)', 'var(--seat-3)'] as const,
  },

  // ── Elevation ──────────────────────────────────────────────
  elev: {
    1: 'var(--elev-1)',
    2: 'var(--elev-2)',
    3: 'var(--elev-3)',
  } as const,

  // ── Spacing ────────────────────────────────────────────────
  space: {
    1: '4px',
    2: '8px',
    3: '12px',
    4: '16px',
    5: '20px',
    6: '24px',
    8: '32px',
    10: '40px',
    12: '48px',
    16: '64px',
  } as const,

  // ── Border Radius ───────────────────────────────────────────
  radius: {
    sm: '6px',
    md: '10px',
    lg: '16px',
    xl: '20px',
    full: '9999px',
  } as const,

  // ── Z-Index ────────────────────────────────────────────────
  z: {
    base: 0,
    dropdown: 50,
    sticky: 100,
    overlay: 150,
    modal: 200,
    toast: 300,
    tooltip: 400,
  } as const,

  // ── Sidebar ────────────────────────────────────────────────
  sidebar: {
    width: 224,
    widthCollapsed: 72,
  } as const,

  // ── Panel / Drawer ─────────────────────────────────────────
  rightPanel: {
    width: 320,
  } as const,

  // ── Transition ─────────────────────────────────────────────
  transition: {
    fast: '0.15s',
    default: '0.2s',
    slow: '0.3s',
  } as const,
} as const;
