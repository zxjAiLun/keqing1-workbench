import type { ReactNode, CSSProperties } from 'react';

/**
 * Card — 通用卡片容器
 *
 * 用法:
 *   <Card>内容</Card>
 *   <Card padding="sm">紧凑卡片</Card>
 */
interface CardProps {
  children: ReactNode;
  /** 'none' | 'sm' | 'md' | 'lg' */
  padding?: 'none' | 'sm' | 'md' | 'lg';
  /** @deprecated 设计系统 v2 已移除玻璃拟态，此参数不再生效 */
  glass?: boolean;
  style?: CSSProperties;
  className?: string;
  onClick?: () => void;
}

const paddingMap = {
  none: 0,
  sm: '12px 14px',
  md: '18px 20px',
  lg: '24px 28px',
};

export function Card({
  children,
  padding = 'md',
  style,
  className,
  onClick,
}: CardProps) {
  const base: CSSProperties = {
    background: 'var(--card-bg)',
    border: '1px solid var(--card-border)',
    borderRadius: 'var(--radius-md)',
    boxShadow: 'var(--card-shadow)',
    padding: typeof padding === 'string' ? paddingMap[padding] : padding,
    transition: 'background var(--transition), border-color var(--transition), box-shadow var(--transition)',
    cursor: onClick ? 'pointer' : undefined,
    ...style,
  };

  return (
    <div
      style={base}
      className={className}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => e.key === 'Enter' && onClick() : undefined}
    >
      {children}
    </div>
  );
}

/**
 * Panel — 带标题的卡片
 *
 * 用法:
 *   <Panel title="设置">
 *     ...
 *   </Panel>
 */
interface PanelProps {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
  padding?: 'none' | 'sm' | 'md' | 'lg';
  /** @deprecated 设计系统 v2 已移除玻璃拟态，此参数不再生效 */
  glass?: boolean;
  style?: CSSProperties;
  className?: string;
}

export function Panel({
  title,
  children,
  actions,
  padding = 'md',
  style,
  className,
}: PanelProps) {
  return (
    <Card padding="none" style={style} className={className}>
      {title && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 18px',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <span
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: 'var(--card-title-color)',
            }}
          >
            {title}
          </span>
          {actions && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>{actions}</div>
          )}
        </div>
      )}
      <div style={{ padding: paddingMap[padding] ?? padding }}>{children}</div>
    </Card>
  );
}

/**
 * StatBlock — 统计数据块（用于指标卡片）
 */
interface StatBlockProps {
  label: string;
  value: string | number;
  tone?: 'accent' | 'success' | 'warning' | 'error' | 'gold';
  icon?: ReactNode;
  style?: CSSProperties;
}

const toneColors: Record<NonNullable<StatBlockProps['tone']>, string> = {
  accent: 'var(--accent)',
  success: 'var(--success)',
  warning: 'var(--warning)',
  error: 'var(--negative)',
  gold: 'var(--gold)',
};

export function StatBlock({ label, value, tone = 'accent', icon, style }: StatBlockProps) {
  const color = toneColors[tone];
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        padding: '14px 16px',
        background: 'var(--card-bg)',
        border: '1px solid var(--card-border)',
        borderRadius: 'var(--radius-md)',
        boxShadow: 'var(--card-shadow)',
        ...style,
      }}
    >
      {icon && (
        <span style={{ fontSize: 20, flexShrink: 0, display: 'flex' }}>{icon}</span>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 22,
            fontWeight: 800,
            color,
            fontFamily: 'Menlo, monospace',
            fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.2,
          }}
        >
          {value}
        </div>
        <div
          style={{
            fontSize: 12,
            color: 'var(--text-muted)',
            marginTop: 2,
          }}
        >
          {label}
        </div>
      </div>
    </div>
  );
}
