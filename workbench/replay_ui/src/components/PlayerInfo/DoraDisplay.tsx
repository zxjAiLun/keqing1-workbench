// src/replay_ui/src/components/PlayerInfo/DoraDisplay.tsx
import { useState, useEffect } from 'react';

interface DoraDisplayProps {
  doraMarkers: string[];
}

const TILE_W = 28;
const TILE_H = 38;

export function DoraDisplay({ doraMarkers }: DoraDisplayProps) {
  const [images, setImages] = useState<Record<string, HTMLImageElement>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const TILE_SVG_NAME: Record<string, string> = {
      '1m': 'Man1', '2m': 'Man2', '3m': 'Man3', '4m': 'Man4', '5m': 'Man5',
      '6m': 'Man6', '7m': 'Man7', '8m': 'Man8', '9m': 'Man9',
      '1p': 'Pin1', '2p': 'Pin2', '3p': 'Pin3', '4p': 'Pin4', '5p': 'Pin5',
      '6p': 'Pin6', '7p': 'Pin7', '8p': 'Pin8', '9p': 'Pin9',
      '1s': 'Sou1', '2s': 'Sou2', '3s': 'Sou3', '4s': 'Sou4', '5s': 'Sou5',
      '6s': 'Sou6', '7s': 'Sou7', '8s': 'Sou8', '9s': 'Sou9',
      '5mr': 'Man5-Dora', '5pr': 'Pin5-Dora', '5sr': 'Sou5-Dora',
      'E': 'Ton', 'S': 'Nan', 'W': 'Shaa', 'N': 'Pei',
      'P': 'Haku', 'F': 'Hatsu', 'C': 'Chun',
    };

    let loadedCount = 0;
    const total = doraMarkers.length;
    const newImages: Record<string, HTMLImageElement> = {};

    if (total === 0) {
      setLoaded(true);
      return;
    }

    doraMarkers.forEach(tile => {
      const name = TILE_SVG_NAME[tile];
      if (!name) { loadedCount++; return; }
      const img = new Image();
      img.src = `/tiles/${name}.svg`;
      img.onload = () => {
        newImages[tile] = img;
        loadedCount++;
        if (loadedCount === total) {
          setImages(newImages);
          setLoaded(true);
        }
      };
      img.onerror = () => {
        loadedCount++;
        if (loadedCount === total) {
          setImages(newImages);
          setLoaded(true);
        }
      };
    });
  }, [doraMarkers]);

  if (!loaded || doraMarkers.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-500">宝牌</span>
        <span className="text-xs text-gray-400">无</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-amber-500 font-semibold">宝牌</span>
      <div className="flex gap-1">
        {doraMarkers.slice(0, 5).map((tile, i) => {
          const img = images[tile];
          return (
            <div key={`${tile}-${i}`} className="relative">
              {img ? (
                <img
                  src={img.src}
                  width={TILE_W}
                  height={TILE_H}
                  alt={tile}
                  className="rounded"
                />
              ) : (
                <div
                  className="bg-gray-200 rounded"
                  style={{ width: TILE_W, height: TILE_H }}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
