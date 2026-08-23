import { useTheme } from '../../context/themeStore';
import { Sun, Moon } from 'lucide-react';

export function ThemeToggle() {
  const { theme, toggle } = useTheme();

  return (
    <button
      onClick={toggle}
      title={theme === 'light' ? '切换到深色风格' : '切换到浅色风格（旧版）'}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 36,
        height: 36,
        borderRadius: 8,
        border: '1px solid var(--border)',
        background: 'var(--card-bg)',
        cursor: 'pointer',
        transition: 'all var(--transition)',
        flexShrink: 0,
      }}
      onMouseEnter={e => {
        e.currentTarget.style.background = 'var(--sidebar-hover-bg)';
        e.currentTarget.style.borderColor = 'var(--accent)';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = 'var(--card-bg)';
        e.currentTarget.style.borderColor = 'var(--border)';
      }}
    >
      {theme === 'light' ? (
        <Moon size={16} style={{ color: 'var(--text-secondary)', transition: 'color var(--transition)' }} />
      ) : (
        <Sun size={16} style={{ color: 'var(--accent)', transition: 'color var(--transition)' }} />
      )}
    </button>
  );
}
