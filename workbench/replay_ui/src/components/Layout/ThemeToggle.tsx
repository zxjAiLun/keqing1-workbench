import { useTheme } from '../../context/themeStore';
import { Sun, Moon } from 'lucide-react';

export function ThemeToggle() {
  const { theme, toggle } = useTheme();

  return (
    <button
      onClick={toggle}
      title={theme === 'white' ? '切换到现代风格' : '切换到白色风格'}
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
      {theme === 'white' ? (
        <Moon size={16} style={{ color: 'var(--text-secondary)', transition: 'color var(--transition)' }} />
      ) : (
        <Sun size={16} style={{ color: '#fbbf24', transition: 'color var(--transition)' }} />
      )}
    </button>
  );
}
