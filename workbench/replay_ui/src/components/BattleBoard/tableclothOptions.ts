// 桌布选项 — 全局唯一配置源
export const TABLECLOTH_OPTIONS = [
  { id: 'default', label: '默认', color: '#f5f0e8' },
  { id: 'light',   label: '浅色', color: '#ffffff' },
  { id: 'deep',    label: '藏青', color: '#0d1b2e' },
  { id: 'navy',    label: '深海', color: '#0a1628' },
  { id: 'slate',   label: '石墨', color: '#1c2333' },
] as const;

export type TableclothId = (typeof TABLECLOTH_OPTIONS)[number]['id'];
