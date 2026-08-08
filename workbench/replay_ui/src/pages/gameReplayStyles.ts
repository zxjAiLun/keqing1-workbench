import type { CSSProperties } from 'react';

export const centeredStatusStyle: CSSProperties = {
  background: 'var(--page-bg)',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 12,
};

export const mutedStatusTextStyle: CSSProperties = {
  color: 'var(--text-muted)',
  fontSize: 14,
};

export const errorStatusTextStyle: CSSProperties = {
  color: 'var(--error)',
  fontSize: 14,
};

export const backLinkButtonStyle: CSSProperties = {
  color: 'var(--accent)',
  fontSize: 14,
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  textDecoration: 'underline',
};
