// src/replay_ui/src/utils/ladderFormat.ts
// Ladder 页面共用的数值格式化工具。

export function fmtRate(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;
}

export function fmtPt(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : Math.round(value).toLocaleString();
}

export function fmtRating(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(1);
}

export function fmtRank(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toFixed(2);
}

export function fmtSignedInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value > 0 ? '+' : ''}${Math.round(value).toLocaleString()}`;
}
