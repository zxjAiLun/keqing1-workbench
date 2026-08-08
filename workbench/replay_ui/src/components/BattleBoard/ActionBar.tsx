// src/replay_ui/src/components/BattleBoard/ActionBar.tsx
import type { Action } from "../../types/battle";

interface ActionBarProps {
  legalActions: Action[];
  onAction: (action: Action) => void;
  disabled?: boolean;
  pendingReach?: boolean; // 已点立直，等待选牌
}

export function ActionBar({ legalActions, onAction, disabled, pendingReach }: ActionBarProps) {
  if (pendingReach) {
    return (
      <div style={containerStyle}>
        <span style={{ color: 'var(--gold)', fontWeight: 700, fontSize: 13 }}>
          立直宣言 — 请选择打出的牌（仅高亮的牌可打）
        </span>
      </div>
    );
  }

  const reachAction = legalActions.find((a) => a.type === "reach");
  const horaActions = legalActions.filter((a) => a.type === "hora");
  const ponActions = legalActions.filter((a) => a.type === "pon");
  const chiActions = legalActions.filter((a) => a.type === "chi");
  const kanActions = legalActions.filter((a) =>
    a.type === "daiminkan" || a.type === "ankan" || a.type === "kakan"
  );
  const hasNone = legalActions.some((a) => a.type === "none");

  const hasSomething = reachAction || horaActions.length > 0 || ponActions.length > 0 ||
    chiActions.length > 0 || kanActions.length > 0;

  if (!hasSomething && !hasNone) return null;

  return (
    <div style={containerStyle}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>

        {/* 跳过/Pass：响应窗口里固定放最前，避免被挤到最后一排 */}
        {hasNone && (
          <button
            style={btnStyle("none")}
            disabled={disabled}
            onClick={() => {
              const noneAction = legalActions.find((a) => a.type === "none");
              if (noneAction) onAction(noneAction);
            }}
          >
            跳过
          </button>
        )}

        {/* 胡牌（自摸/荣和）优先显示 */}
        {horaActions.map((action, i) => (
          <button key={`hora-${i}`} style={btnStyle("hora")} disabled={disabled}
            onClick={() => onAction(action)}>
            {action.target === action.actor ? "自摸" : "荣和"}
          </button>
        ))}

        {/* 立直 */}
        {reachAction && (
          <button style={btnStyle("reach")} disabled={disabled}
            onClick={() => onAction(reachAction)}>
            立直
          </button>
        )}

        {/* 碰 */}
        {ponActions.map((action, i) => (
          <button key={`pon-${i}`} style={btnStyle("pon")} disabled={disabled}
            onClick={() => onAction(action)}>
            碰 {action.pai}
          </button>
        ))}

        {/* 吃（显示 consumed 组合区分多选） */}
        {chiActions.map((action, i) => (
          <button key={`chi-${i}`} style={btnStyle("chi")} disabled={disabled}
            onClick={() => onAction(action)}>
            吃 {chiLabel(action)}
          </button>
        ))}

        {/* 杠 */}
        {kanActions.map((action, i) => (
          <button key={`kan-${i}`} style={btnStyle(action.type)} disabled={disabled}
            onClick={() => onAction(action)}>
            {kanLabel(action)}
          </button>
        ))}
      </div>
    </div>
  );
}

function chiLabel(action: Action): string {
  // 显示 consumed 里的两张牌（不含被吃的那张）
  const consumed = action.consumed ?? [];
  if (consumed.length >= 2) {
    return consumed.join("+");
  }
  return action.pai ?? "";
}

function kanLabel(action: Action): string {
  switch (action.type) {
    case "daiminkan": return `大明杠 ${action.pai}`;
    case "ankan": return `暗杠 ${action.pai}`;
    case "kakan": return `加杠 ${action.pai}`;
    default: return action.type;
  }
}

const containerStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  width: '100%',
  maxWidth: 1080,
  padding: '14px 18px',
  borderRadius: 16,
  background: 'linear-gradient(180deg, rgba(14,20,30,0.78) 0%, rgba(14,20,30,0.62) 100%)',
  border: '1px solid rgba(255,255,255,0.14)',
  backdropFilter: 'blur(16px)',
  boxShadow: '0 14px 40px rgba(0,0,0,0.32)',
  transition: 'background var(--transition), border-color var(--transition), transform var(--transition)',
};

function btnStyle(type: string): React.CSSProperties {
  const base: React.CSSProperties = {
    padding: '12px 22px',
    borderRadius: 12,
    border: 'none',
    fontWeight: 700,
    fontSize: 15,
    lineHeight: 1.1,
    cursor: 'pointer',
    transition: 'opacity 0.15s, transform 0.15s',
    whiteSpace: 'nowrap',
    minHeight: 48,
    minWidth: 96,
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.12)',
  };
  switch (type) {
    case "hora":
      return { ...base, background: 'linear-gradient(135deg, #e74c3c 0%, #c0392b 100%)', color: '#fff' };
    case "reach":
      return { ...base, background: 'linear-gradient(135deg, var(--gold) 0%, #c49843 100%)', color: '#1a1a1a' };
    case "pon":
      return { ...base, background: 'linear-gradient(135deg, #e67e22 0%, #d35400 100%)', color: '#fff' };
    case "chi":
      return { ...base, background: 'linear-gradient(135deg, var(--success) 0%, #219a52 100%)', color: '#fff' };
    case "daiminkan":
    case "ankan":
    case "kakan":
      return { ...base, background: 'linear-gradient(135deg, #8e44ad 0%, #7d3c9e 100%)', color: '#fff' };
    case "none":
      return {
        ...base,
        background: 'linear-gradient(135deg, rgba(255,255,255,0.18) 0%, rgba(255,255,255,0.10) 100%)',
        color: '#ffffff',
        border: '1px solid rgba(255,255,255,0.22)',
        fontWeight: 700,
        minWidth: 112,
      };
    default:
      return {
        ...base,
        background: 'rgba(255,255,255,0.08)',
        color: '#fff',
        border: '1px solid rgba(255,255,255,0.14)',
      };
  }
}
