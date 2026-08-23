// 桌布选项 — 全局唯一配置源（设计系统 v2 §3.1：收敛为深色系三选）
export const TABLECLOTH_OPTIONS = [
  { id: 'felt',     label: '绒绿', color: '#1e3527' },
  { id: 'ink',      label: '墨蓝', color: '#16222e' },
  { id: 'charcoal', label: '炭灰', color: '#1d1b18' },
] as const;

export type TableclothId = (typeof TABLECLOTH_OPTIONS)[number]['id'];

export const DEFAULT_TABLECLOTH_ID: TableclothId = 'felt';
