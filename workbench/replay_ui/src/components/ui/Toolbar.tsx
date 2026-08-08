import type { ReactNode, CSSProperties } from 'react';

/**
 * Toolbar — 水平工具栏容器
 *
 * 用法:
 *   <Toolbar>
 *     <ToolbarGroup>按钮组</ToolbarGroup>
 *     <ToolbarSpacer />
 *     <ToolbarGroup>右侧操作</ToolbarGroup>
 *   </Toolbar>
 */

interface ToolbarProps {
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
}

export function Toolbar({ children, style, className }: ToolbarProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
        ...style,
      }}
      className={className}
    >
      {children}
    </div>
  );
}

/**
 * ToolbarGroup — 工具栏内的按钮/控件组
 */
interface ToolbarGroupProps {
  children: ReactNode;
  gap?: number;
  style?: CSSProperties;
}

export function ToolbarGroup({ children, gap = 8, style }: ToolbarGroupProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap,
        flexWrap: 'wrap',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/**
 * ToolbarSpacer — 工具栏分隔，将后续元素推至右侧
 */
export function ToolbarSpacer() {
  return <div style={{ flex: 1, minWidth: 8 }} />;
}

/**
 * Divider — 工具栏分隔线
 */
export function Divider({ vertical = false }: { vertical?: boolean }) {
  return (
    <div
      style={{
        width: vertical ? '1px' : '100%',
        height: vertical ? 20 : 1,
        background: 'var(--border)',
        flexShrink: 0,
        alignSelf: 'center',
      }}
    />
  );
}
