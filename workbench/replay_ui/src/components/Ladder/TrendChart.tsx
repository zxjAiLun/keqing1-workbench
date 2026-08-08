// src/replay_ui/src/components/Ladder/TrendChart.tsx
// 轻量 SVG 趋势图：无第三方图表依赖，用于账号 PT / Rating 曲线。
import type { CSSProperties } from 'react';

interface TrendChartProps {
  title: string;
  points: number[];
  color?: string;
  formatValue?: (value: number) => string;
  height?: number;
}

const titleStyle: CSSProperties = {
  fontSize: 12,
  fontWeight: 700,
  color: 'var(--text-secondary)',
};

export function TrendChart({ title, points, color = 'var(--accent)', formatValue, height = 96 }: TrendChartProps) {
  const fmt = formatValue ?? ((value: number) => value.toFixed(1));

  if (points.length < 2) {
    return (
      <div>
        <div style={{ ...titleStyle, marginBottom: 4 }}>{title}</div>
        <div style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-muted)',
          fontSize: 12,
          border: '1px dashed var(--border)',
          borderRadius: 6,
        }}>
          数据不足
        </div>
      </div>
    );
  }

  const width = 320;
  const pad = 6;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = (width - pad * 2) / (points.length - 1);
  const coords = points.map((value, index) => {
    const x = pad + index * stepX;
    const y = pad + (1 - (value - min) / span) * (height - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = points[points.length - 1];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <span style={titleStyle}>{title}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>{fmt(last)}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: '100%', height, display: 'block', background: 'var(--page-bg)', border: '1px solid var(--border)', borderRadius: 6 }}
        role="img"
        aria-label={title}
      >
        <text x={pad} y={pad + 8} fontSize={8} fill="var(--text-muted)">{fmt(max)}</text>
        <text x={pad} y={height - 2} fontSize={8} fill="var(--text-muted)">{fmt(min)}</text>
        <polyline points={coords.join(' ')} fill="none" stroke={color} strokeWidth={1.6} />
      </svg>
    </div>
  );
}
