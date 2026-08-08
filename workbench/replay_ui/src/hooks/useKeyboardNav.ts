// src/replay_ui/src/hooks/useKeyboardNav.ts
import { useEffect } from 'react';

interface KeyboardNavHandlers {
  onStepForward?: () => void;
  onStepBackward?: () => void;
  onKyokuForward?: () => void;
  onKyokuBackward?: () => void;
  onTogglePlay?: () => void;
  onGoToStart?: () => void;
  onGoToEnd?: () => void;
}

export function useKeyboardNav(handlers: KeyboardNavHandlers) {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      // 忽略输入框中的按键
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

      switch (e.key) {
        case 'ArrowRight':
          handlers.onKyokuForward?.();
          break;
        case 'ArrowLeft':
          handlers.onKyokuBackward?.();
          break;
        case 'ArrowDown':
        case 'j':
          e.preventDefault();
          handlers.onStepForward?.();
          break;
        case 'ArrowUp':
        case 'k':
          e.preventDefault();
          handlers.onStepBackward?.();
          break;
        case ' ':
          e.preventDefault();
          handlers.onTogglePlay?.();
          break;
        case 'Home':
          handlers.onGoToStart?.();
          break;
        case 'End':
          handlers.onGoToEnd?.();
          break;
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handlers]);
}
