// 桌布选项 — 支持明暗主题双态（暗黑沉浸 / 白天清爽）
export interface TableclothOption {
  id: string;
  label: string;
  color: string;
}

export const TABLECLOTH_OPTIONS_DARK: readonly TableclothOption[] = [
  { id: 'felt',     label: '绒绿', color: '#1e3527' },
  { id: 'ink',      label: '墨蓝', color: '#16222e' },
  { id: 'charcoal', label: '炭灰', color: '#1d1b18' },
] as const;

export const TABLECLOTH_OPTIONS_LIGHT: readonly TableclothOption[] = [
  { id: 'light-felt', label: '浅绿', color: '#dce8df' },
  { id: 'ivory',      label: '米白', color: '#f5f0e8' },
  { id: 'day-blue',   label: '浅蓝', color: '#e2ecf2' },
] as const;

export const ALL_TABLECLOTH_OPTIONS: readonly TableclothOption[] = [
  ...TABLECLOTH_OPTIONS_DARK,
  ...TABLECLOTH_OPTIONS_LIGHT,
];

// 兼容旧引用
export const TABLECLOTH_OPTIONS = ALL_TABLECLOTH_OPTIONS;

export type TableclothId = string;

export const DEFAULT_DARK_TABLECLOTH_ID: TableclothId = 'felt';
export const DEFAULT_LIGHT_TABLECLOTH_ID: TableclothId = 'light-felt';
export const DEFAULT_TABLECLOTH_ID: TableclothId = 'felt';

export function getTableclothOptions(theme: 'dark' | 'light'): readonly TableclothOption[] {
  return theme === 'light' ? TABLECLOTH_OPTIONS_LIGHT : TABLECLOTH_OPTIONS_DARK;
}

export function getDefaultTableclothId(theme: 'dark' | 'light'): TableclothId {
  return theme === 'light' ? DEFAULT_LIGHT_TABLECLOTH_ID : DEFAULT_DARK_TABLECLOTH_ID;
}

