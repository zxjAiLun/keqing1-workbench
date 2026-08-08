// src/replay_ui/src/hooks/useTileImages.ts
import { useState, useEffect, useRef } from 'react';
import { TILE_SVG_NAME } from '../utils/tileUtils';

const TILES_DIR = '/tiles';
const ALL_TILES = Object.keys(TILE_SVG_NAME);

export function useTileImages() {
  const [loaded, setLoaded] = useState(false);
  const imagesRef = useRef<Record<string, HTMLImageElement>>({});

  useEffect(() => {
    let cancelled = false;
    let loadedCount = 0;
    const total = ALL_TILES.length;

    ALL_TILES.forEach(tile => {
      const name = TILE_SVG_NAME[tile] ?? 'Blank';
      const img = new Image();
      img.src = `${TILES_DIR}/${name}.svg`;
      img.onload = () => {
        if (!cancelled) {
          imagesRef.current[tile] = img;
          loadedCount++;
          if (loadedCount === total) {
            setLoaded(true);
          }
        }
      };
      img.onerror = () => {
        if (!cancelled) {
          // 用空白图片代替
          const blank = new Image();
          blank.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="40" height="52"/>';
          imagesRef.current[tile] = blank;
          loadedCount++;
          if (loadedCount === total) {
            setLoaded(true);
          }
        }
      };
    });

    return () => {
      cancelled = true;
    };
  }, []);

  return { loaded, images: imagesRef.current };
}
