import type { ReactNode, CSSProperties } from 'react';
import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

type DrawerSide = 'right' | 'left' | 'bottom';

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  side?: DrawerSide;
  width?: number;
  /** 默认 true */
  showClose?: boolean;
  /** 背景遮罩点击关闭，默认 true */
  backdropClose?: boolean;
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  side = 'right',
  width = 320,
  showClose = true,
  backdropClose = true,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Focus trap & ESC close
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // Prevent body scroll
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  const isVertical = side === 'right' || side === 'left';

  const panelStyle: CSSProperties = {
    position: 'fixed',
    top: 0,
    ...(isVertical
      ? {
          [side]: 0,
          bottom: 0,
          width,
          maxWidth: '90vw',
        }
      : {
          left: 0,
          right: 0,
          bottom: 0,
          height: '70vh',
          maxHeight: '90vh',
        }),
    background: 'var(--card-bg)',
    border: '1px solid var(--card-border)',
    boxShadow: side === 'right'
      ? '-4px 0 24px rgba(0,0,0,0.15)'
      : side === 'left'
      ? '4px 0 24px rgba(0,0,0,0.15)'
      : '0 -4px 24px rgba(0,0,0,0.15)',
    zIndex: 200, // modal
    display: 'flex',
    flexDirection: 'column',
    transform: open
      ? 'translateX(0)'
      : side === 'right'
      ? 'translateX(100%)'
      : side === 'left'
      ? 'translateX(-100%)'
      : 'translateY(100%)',
    transition: 'transform 0.25s ease',
    overflow: 'hidden',
  };

  const backdropStyle: CSSProperties = {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.35)',
    zIndex: 150, // overlay
    opacity: open ? 1 : 0,
    pointerEvents: open ? 'auto' : 'none',
    transition: 'opacity 0.25s ease',
  };

  return (
    <>
      {/* Backdrop */}
      <div
        style={backdropStyle}
        onClick={backdropClose ? onClose : undefined}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        ref={panelRef}
        style={panelStyle}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 16px',
            borderBottom: '1px solid var(--border)',
            flexShrink: 0,
            background: 'var(--nav-bg)',
          }}
        >
          <span
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: '#fff',
            }}
          >
            {title}
          </span>
          {showClose && (
            <button
              onClick={onClose}
              style={{
                width: 28,
                height: 28,
                borderRadius: '50%',
                border: 'none',
                background: 'rgba(255,255,255,0.2)',
                color: '#fff',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'background var(--transition)',
                flexShrink: 0,
              }}
              aria-label="关闭"
            >
              <X size={16} />
            </button>
          )}
        </div>

        {/* Body */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px',
          }}
        >
          {children}
        </div>
      </div>
    </>
  );
}
