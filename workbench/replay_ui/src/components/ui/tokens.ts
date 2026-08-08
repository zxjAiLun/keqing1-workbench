/**
 * KEQING MJ — Design Tokens
 *
 * All spacing/sizing/z-index values exposed as constants.
 * CSS variables are defined in globals.css; these JS constants
 * are for TypeScript consumption (e.g. inline styles, JSX).
 *
 * Token naming follows semantic intent, not raw value.
 */

export const t = {
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
