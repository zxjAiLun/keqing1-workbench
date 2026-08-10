import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import {
  AlertTriangle,
  BarChart2,
  Bot,
  History,
  LayoutDashboard,
  ListTree,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Table2,
  Users,
  X,
} from 'lucide-react';
import { ThemeToggle } from './ThemeToggle';
import { TABLECLOTH_OPTIONS } from '../BattleBoard/tableclothOptions';
import type { TableclothId } from '../BattleBoard/tableclothOptions';
import { routes } from '../../routes';

type NavItem = {
  path: string;
  icon: typeof LayoutDashboard;
  label: string;
  exact?: boolean;
};

const NAV_SECTIONS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: '工作台',
    items: [{ path: routes.home, icon: LayoutDashboard, label: '总览', exact: true }],
  },
  {
    label: 'Review',
    items: [
      { path: routes.reviewNew, icon: BarChart2, label: '新建 Review' },
      { path: routes.reviewLibrary, icon: History, label: 'Review Library', exact: true },
    ],
  },
  {
    label: '竞技',
    items: [
      { path: routes.ladder, icon: ListTree, label: '天梯榜' },
      { path: routes.seasonManager, icon: Settings, label: '赛季管理' },
    ],
  },
  {
    label: '在线',
    items: [{ path: routes.tenhou, icon: Bot, label: '天凤呼出' }],
  },
  {
    label: '对战记录',
    items: [
      { path: routes.participants, icon: Users, label: '参赛者' },
      { path: routes.matches, icon: Table2, label: '对局记录', exact: true },
    ],
  },
  {
    label: '诊断',
    items: [{ path: routes.diagnosticsCasebook, icon: AlertTriangle, label: 'Casebook' }],
  },
];

const SIDEBAR_WIDTH = 176;
const SIDEBAR_COLLAPSED = 56;

export function Sidebar() {
  const [isMobile, setIsMobile] = useState(false);
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    const stored =
      window.localStorage.getItem('keqing1.sidebar.collapsed')
      ?? window.localStorage.getItem('keqing.sidebar.collapsed');
    return stored === 'true';
  });
  const [tablecloth, setTablecloth] = useState<TableclothId>(() => {
    const stored =
      window.localStorage.getItem('keqing1.tablecloth')
      ?? window.localStorage.getItem('keqing.tablecloth');
    if (stored && TABLECLOTH_OPTIONS.some((item) => item.id === stored)) {
      return stored as TableclothId;
    }
    return 'default';
  });

  useEffect(() => {
    window.localStorage.setItem('keqing1.sidebar.collapsed', String(collapsed));
    window.localStorage.setItem('keqing.sidebar.collapsed', String(collapsed));
  }, [collapsed]);

  useEffect(() => {
    const update = () => setIsMobile(window.innerWidth < 900);
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  useEffect(() => {
    document.documentElement.style.setProperty('--mobile-shell-offset', isMobile ? '44px' : '0px');
    return () => document.documentElement.style.setProperty('--mobile-shell-offset', '0px');
  }, [isMobile]);

  const updateTablecloth = (next: TableclothId) => {
    setTablecloth(next);
    window.localStorage.setItem('keqing1.tablecloth', next);
    window.localStorage.setItem('keqing.tablecloth', next);
    window.dispatchEvent(new StorageEvent('storage', { key: 'keqing1.tablecloth', newValue: next }));
    window.dispatchEvent(new StorageEvent('storage', { key: 'keqing.tablecloth', newValue: next }));
  };

  const navLink = ({ path, icon: Icon, label, exact }: NavItem) => (
    <NavLink
      key={path}
      to={path}
      end={exact}
      onClick={() => setOpen(false)}
      className={({ isActive }) => `nav-link-item${isActive ? ' nav-link-active' : ''}`}
      title={label}
      style={{
        height: 34,
        padding: collapsed && !isMobile ? '0 10px' : '0 10px',
        justifyContent: collapsed && !isMobile ? 'center' : 'flex-start',
      }}
    >
      <Icon size={17} />
      {(!collapsed || isMobile) && <span>{label}</span>}
    </NavLink>
  );

  const settings = (
    <div style={{ display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--sidebar-text-muted)', fontSize: 11 }}>
        <Settings size={13} />
        {(!collapsed || isMobile) && <span>设置</span>}
      </div>
      {(!collapsed || isMobile) && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <span style={{ color: 'var(--sidebar-text-muted)', fontSize: 11 }}>主题</span>
            <ThemeToggle />
          </div>
          <div style={{ display: 'grid', gap: 5 }}>
            <span style={{ color: 'var(--sidebar-text-muted)', fontSize: 11 }}>桌布</span>
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              {TABLECLOTH_OPTIONS.map((item) => (
                <button
                  key={item.id}
                  onClick={() => updateTablecloth(item.id)}
                  title={item.label}
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 5,
                    border: tablecloth === item.id
                      ? '2px solid var(--accent)'
                      : '1px solid rgba(255,255,255,0.18)',
                    background: item.color,
                    cursor: 'pointer',
                  }}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );

  const body = (
    <aside
      style={{
        width: isMobile ? 236 : collapsed ? SIDEBAR_COLLAPSED : SIDEBAR_WIDTH,
        height: '100%',
        background: 'var(--sidebar-bg)',
        borderRight: '1px solid var(--sidebar-border)',
        color: 'var(--sidebar-text)',
        display: 'flex',
        flexDirection: 'column',
        transition: 'width var(--transition), transform var(--transition)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          height: 48,
          display: 'flex',
          alignItems: 'center',
          justifyContent: collapsed && !isMobile ? 'center' : 'space-between',
          padding: collapsed && !isMobile ? '0' : '0 10px',
          borderBottom: '1px solid var(--sidebar-border)',
          gap: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 7,
              background: 'var(--accent)',
              color: 'var(--btn-primary-text)',
              display: 'grid',
              placeItems: 'center',
              fontWeight: 800,
              flexShrink: 0,
            }}
          >
            K
          </div>
          {(!collapsed || isMobile) && (
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 800, whiteSpace: 'nowrap' }}>Keqing1</div>
              <div style={{ fontSize: 10, color: 'var(--sidebar-text-muted)' }}>review workbench</div>
            </div>
          )}
        </div>
        {!isMobile && (
          <button
            onClick={() => setCollapsed((value) => !value)}
            title={collapsed ? '展开' : '收起'}
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(255,255,255,0.04)',
              color: 'var(--sidebar-text-muted)',
              display: 'grid',
              placeItems: 'center',
              cursor: 'pointer',
            }}
          >
            {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          </button>
        )}
      </div>

      <nav style={{ display: 'grid', gap: 4, padding: '10px 8px', flex: 1, overflow: 'auto', alignContent: 'start' }}>
        {NAV_SECTIONS.map((section, sectionIndex) => (
          <div key={section.label} style={{ display: 'grid', gap: 4 }}>
            {sectionIndex > 0 && collapsed && !isMobile && (
              <div style={{ height: 1, background: 'var(--sidebar-border)', margin: '6px 4px' }} />
            )}
            {(!collapsed || isMobile) && (
              <div
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  color: 'var(--sidebar-text-muted)',
                  padding: `${sectionIndex === 0 ? '0' : '8px'} 10px 2px`,
                }}
              >
                {section.label}
              </div>
            )}
            {section.items.map(navLink)}
          </div>
        ))}
      </nav>

      <div style={{ padding: 10, borderTop: '1px solid var(--sidebar-border)' }}>{settings}</div>
    </aside>
  );

  if (isMobile) {
    return (
      <>
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            height: 44,
            zIndex: 120,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 10px',
            background: 'var(--sidebar-bg)',
            borderBottom: '1px solid var(--sidebar-border)',
          }}
        >
          <button
            onClick={() => setOpen(true)}
            aria-label="打开导航"
            style={{
              width: 32,
              height: 32,
              border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(255,255,255,0.04)',
              color: 'var(--sidebar-text)',
              borderRadius: 6,
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <Menu size={18} />
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--sidebar-text)' }}>
            <Table2 size={16} />
            <span style={{ fontSize: 13, fontWeight: 800 }}>Keqing1</span>
          </div>
          <ThemeToggle />
        </div>
        {open && (
          <div
            onClick={() => setOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 130,
              background: 'rgba(0,0,0,0.42)',
              display: 'flex',
            }}
          >
            <div onClick={(event) => event.stopPropagation()} style={{ height: '100%' }}>
              <div style={{ position: 'absolute', top: 8, left: 242, zIndex: 140 }}>
                <button
                  onClick={() => setOpen(false)}
                  aria-label="关闭导航"
                  style={{
                    width: 32,
                    height: 32,
                    border: 'none',
                    borderRadius: 6,
                    background: 'rgba(255,255,255,0.9)',
                    color: '#111827',
                    display: 'grid',
                    placeItems: 'center',
                  }}
                >
                  <X size={18} />
                </button>
              </div>
              {body}
            </div>
          </div>
        )}
      </>
    );
  }

  return body;
}
