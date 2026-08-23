import { createContext, useContext } from 'react';

/**
 * 设计系统 v2：深色为默认主题（dark = 雀馆分析台），
 * light 为旧白色主题的兼容层（deprecated，不再新增设计）。
 * 旧存储值自动迁移：modern → dark，white → light。
 */
export type Theme = 'dark' | 'light';

export interface TileBackColor {
  r: number;
  g: number;
  b: number;
}

export interface TableClothColor {
  r: number;
  g: number;
  b: number;
}

export interface ThemeContextValue {
  theme: Theme;
  toggle: () => void;
  setTheme: (t: Theme) => void;
  tileBack: TileBackColor;
  setTileBack: (c: TileBackColor) => void;
  tableCloth: TableClothColor;
  setTableCloth: (c: TableClothColor) => void;
}

export const STORAGE_KEY_THEME = 'keqing-theme';
export const STORAGE_KEY_TILE_BACK = 'keqing-tile-back';
export const STORAGE_KEY_TABLE_CLOTH = 'keqing-table-cloth';

export const DEFAULT_TILE_BACK: TileBackColor = { r: 16, g: 29, b: 48 }; // #101d30 墨蓝黑
export const DEFAULT_TABLE_CLOTH: TableClothColor = { r: 30, g: 53, b: 39 }; // #1e3527 深绒绿

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
