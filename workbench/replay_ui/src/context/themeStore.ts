import { createContext, useContext } from 'react';

export type Theme = 'white' | 'modern';

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

export const DEFAULT_TILE_BACK: TileBackColor = { r: 15, g: 30, b: 60 };
export const DEFAULT_TABLE_CLOTH: TableClothColor = { r: 232, g: 224, b: 208 }; // #e8e0d0

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
