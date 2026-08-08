import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';

export function MainLayout() {
  return (
    <div
      className="flex"
      style={{
        height: '100dvh',
        background: 'var(--page-bg)',
        transition: 'background var(--transition)',
        overflow: 'hidden',
      }}
    >
      <Sidebar />
      <main
        className="flex-1"
        style={{
          background: 'var(--page-bg)',
          transition: 'background var(--transition)',
          minWidth: 0,
          paddingTop: 'var(--mobile-shell-offset, 0px)',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: 38,
            minHeight: 38,
            borderBottom: '1px solid var(--border)',
            background: 'var(--card-bg)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 14px',
            color: 'var(--text-secondary)',
            fontSize: 12,
          }}
        >
          <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>Keqing1 工作台</span>
        </div>
        <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
