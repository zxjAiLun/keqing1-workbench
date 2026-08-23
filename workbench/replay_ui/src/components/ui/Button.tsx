import type { ReactNode, CSSProperties } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps {
  children: ReactNode;
  variant?: Variant;
  size?: Size;
  disabled?: boolean;
  loading?: boolean;
  icon?: ReactNode;
  onClick?: () => void;
  type?: 'button' | 'submit' | 'reset';
  style?: CSSProperties;
  className?: string;
  title?: string;
}

const variantStyles: Record<Variant, CSSProperties> = {
  primary: {
    background: 'var(--btn-primary-bg)',
    color: 'var(--btn-primary-text)',
    border: 'none',
  },
  secondary: {
    background: 'transparent',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
  },
  ghost: {
    background: 'transparent',
    color: 'var(--text-secondary)',
    border: '1px solid transparent',
  },
  danger: {
    background: 'transparent',
    color: 'var(--negative)',
    border: '1px solid var(--negative)',
  },
};

// 设计系统 v2 §6.1：高度 26/32/38
const sizeStyles: Record<Size, CSSProperties> = {
  sm: { height: 26, padding: '0 10px', fontSize: 12, gap: 5 },
  md: { height: 32, padding: '0 16px', fontSize: 13, gap: 6 },
  lg: { height: 38, padding: '0 22px', fontSize: 14, gap: 8 },
};

export function Button({
  children,
  variant = 'primary',
  size = 'md',
  disabled = false,
  loading = false,
  icon,
  onClick,
  type = 'button',
  style,
  className,
  title,
}: ButtonProps) {
  const base: CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 'var(--radius-sm)',
    fontWeight: 600,
    cursor: disabled || loading ? 'not-allowed' : 'pointer',
    transition: `background var(--transition), transform 0.1s, box-shadow var(--transition)`,
    whiteSpace: 'nowrap',
    flexShrink: 0,
    opacity: disabled ? 0.5 : 1,
    ...variantStyles[variant],
    ...sizeStyles[size],
    ...(variant === 'primary' && !disabled && !loading
      ? { boxShadow: '0 2px 8px var(--accent-shadow)' }
      : {}),
    ...style,
  };

  return (
    <button
      type={type}
      onClick={disabled || loading ? undefined : onClick}
      style={base}
      className={className}
      title={title}
    >
      {loading ? (
        <span
          style={{
            display: 'inline-block',
            width: 14,
            height: 14,
            border: '2px solid rgba(255,255,255,0.4)',
            borderTopColor: 'currentColor',
            borderRadius: '50%',
            animation: 'spin 0.7s linear infinite',
          }}
        />
      ) : icon ? (
        <span style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>{icon}</span>
      ) : null}
      {children}
    </button>
  );
}
