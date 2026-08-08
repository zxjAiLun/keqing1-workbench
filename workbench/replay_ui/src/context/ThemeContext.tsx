import { useState, useCallback, useEffect } from 'react';
import {
  ThemeContext,
  type Theme,
  type TileBackColor,
  type TableClothColor,
  STORAGE_KEY_THEME,
  STORAGE_KEY_TILE_BACK,
  STORAGE_KEY_TABLE_CLOTH,
  DEFAULT_TILE_BACK,
  DEFAULT_TABLE_CLOTH,
} from './themeStore';

export function ThemeProvider({ children }: { children: import('react').ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const stored = localStorage.getItem(STORAGE_KEY_THEME);
    if (stored === 'white' || stored === 'modern') return stored;
    return 'white';
  });

  const [tileBack, setTileBackState] = useState<TileBackColor>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_TILE_BACK);
      if (raw) {
        const parsed = JSON.parse(raw) as TileBackColor;
        if (
          typeof parsed.r === 'number' && typeof parsed.g === 'number' && typeof parsed.b === 'number'
        ) {
          return {
            r: Math.max(0, Math.min(255, parsed.r)),
            g: Math.max(0, Math.min(255, parsed.g)),
            b: Math.max(0, Math.min(255, parsed.b)),
          };
        }
      }
    } catch {
      // ignore parse errors
    }
    return DEFAULT_TILE_BACK;
  });

  const [tableCloth, setTableClothState] = useState<TableClothColor>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_TABLE_CLOTH);
      if (raw) {
        const parsed = JSON.parse(raw) as TableClothColor;
        if (
          typeof parsed.r === 'number' && typeof parsed.g === 'number' && typeof parsed.b === 'number'
        ) {
          return {
            r: Math.max(0, Math.min(255, parsed.r)),
            g: Math.max(0, Math.min(255, parsed.g)),
            b: Math.max(0, Math.min(255, parsed.b)),
          };
        }
      }
    } catch {
      // ignore parse errors
    }
    return DEFAULT_TABLE_CLOTH;
  });

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    localStorage.setItem(STORAGE_KEY_THEME, t);
  }, []);

  const setTileBack = useCallback((c: TileBackColor) => {
    const clamped = {
      r: Math.max(0, Math.min(255, c.r)),
      g: Math.max(0, Math.min(255, c.g)),
      b: Math.max(0, Math.min(255, c.b)),
    };
    setTileBackState(clamped);
    localStorage.setItem(STORAGE_KEY_TILE_BACK, JSON.stringify(clamped));
  }, []);

  const setTableCloth = useCallback((c: TableClothColor) => {
    const clamped = {
      r: Math.max(0, Math.min(255, c.r)),
      g: Math.max(0, Math.min(255, c.g)),
      b: Math.max(0, Math.min(255, c.b)),
    };
    setTableClothState(clamped);
    localStorage.setItem(STORAGE_KEY_TABLE_CLOTH, JSON.stringify(clamped));
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === 'white' ? 'modern' : 'white');
  }, [theme, setTheme]);

  // Apply theme attribute
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Apply tile-back RGB to CSS variables
  useEffect(() => {
    document.documentElement.style.setProperty('--tile-back-r', String(tileBack.r));
    document.documentElement.style.setProperty('--tile-back-g', String(tileBack.g));
    document.documentElement.style.setProperty('--tile-back-b', String(tileBack.b));
  }, [tileBack]);

  // Apply table-cloth RGB to CSS variables
  useEffect(() => {
    document.documentElement.style.setProperty('--table-bg-r', String(tableCloth.r));
    document.documentElement.style.setProperty('--table-bg-g', String(tableCloth.g));
    document.documentElement.style.setProperty('--table-bg-b', String(tableCloth.b));
  }, [tableCloth]);

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme, tileBack, setTileBack, tableCloth, setTableCloth }}>
      {children}
    </ThemeContext.Provider>
  );
}