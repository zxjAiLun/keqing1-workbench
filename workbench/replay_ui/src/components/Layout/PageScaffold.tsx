import type { ReactNode } from 'react';
import { Panel, StatBlock } from '../ui';

/**
 * PageShell — 页面内容容器
 *
 * @param width  Alias for maxWidth (backward compat)
 */
export function PageShell({
  children,
  width,
  maxWidth = 1120,
  padding = 14,
}: {
  children: ReactNode;
  /** @deprecated Use maxWidth */
  width?: number;
  maxWidth?: number;
  padding?: number;
}) {
  return (
    <div style={{ height: '100%', overflow: 'auto', padding }}>
      <div style={{ maxWidth: width ?? maxWidth, margin: '0 auto' }}>{children}</div>
    </div>
  );
}

/**
 * PageHeader — 页面标题区
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-end',
        gap: 12,
        flexWrap: 'wrap',
        marginBottom: 12,
      }}
    >
      <div style={{ minWidth: 0 }}>
        {eyebrow && (
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              textTransform: 'uppercase',
              color: 'var(--text-muted)',
              marginBottom: 4,
            }}
          >
            {eyebrow}
          </div>
        )}
        <h1 style={{ fontSize: 20, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 4 }}>
          {title}
        </h1>
        {description && (
          <div style={{ maxWidth: 720, fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
            {description}
          </div>
        )}
      </div>
      {actions && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>{actions}</div>
      )}
    </div>
  );
}

/**
 * SectionTitle — 区块标题
 */
export function SectionTitle({
  title,
  description,
}: {
  title: string;
  description?: ReactNode;
}) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: 'var(--text-primary)',
          marginBottom: description ? 2 : 0,
        }}
      >
        {title}
      </div>
      {description && <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{description}</div>}
    </div>
  );
}

/**
 * MetricCard — 指标卡片（使用 StatBlock 封装 Panel）
 */
export function MetricCard({
  label,
  value,
  tone = 'accent',
}: {
  label: string;
  value: ReactNode;
  tone?: 'accent' | 'success' | 'warning' | 'error' | 'gold';
}) {
  return (
    <StatBlock
      label={label}
      value={value as string | number}
      tone={tone}
      style={{ minWidth: 96 }}
    />
  );
}

/**
 * SettingsPanel — 设置面板（带标题的 Panel）
 */
export function SettingsPanel({
  title,
  children,
  actions,
}: {
  title: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Panel title={title} actions={actions} padding="md">
      {children}
    </Panel>
  );
}
