// src/replay_ui/src/components/DecisionPanel/ReplayDecisionPanel.tsx
import { useEffect, useMemo, useState } from 'react';
import type { DecisionLogEntry } from '../../types/replay';
import { sameReplayAction } from '../../utils/tileUtils';

interface ReplayDecisionPanelProps {
  entry: DecisionLogEntry | null;
  step: number;
  totalSteps: number;
  compact?: boolean;
  playerNames?: string[];
  currentPlayerId?: number;
  onSwitchPlayer?: (playerId: number) => void;
  localReviewerLabel?: string;
  availableTeacherModels?: string[];
  activeTeacherModel?: string | null;
  onActiveTeacherModelChange?: (model: string | null) => void;
  /** 为 true 时隐藏候选动作权重表格（如 post 阶段） */
  hideWeights?: boolean;
}

/** 候选动作的显示值：final_score（统一口径） */
function displayScore(c: { logit: number; beam_score?: number; final_score?: number }): number {
  return c.final_score ?? c.beam_score ?? c.logit;
}

function displayScoreLabel(c: { logit: number; beam_score?: number; final_score?: number }): string {
  return displayScore(c).toFixed(2);
}

function softmaxProbabilities(scores: number[]): number[] {
  if (scores.length === 0) return [];
  const maxScore = Math.max(...scores);
  const exps = scores.map((score) => Math.exp(score - maxScore));
  const total = exps.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) {
    return scores.map(() => 0);
  }
  return exps.map((value) => value / total);
}

function displayProbLabel(probability: number): string {
  if (!Number.isFinite(probability)) return '—';
  return `${(probability * 100).toFixed(1)}%`;
}

function displayOptionalScore(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—';
}

function displayOptionalProb(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? displayProbLabel(value) : '—';
}

function teacherAction(review: NonNullable<DecisionLogEntry['teacher_reviews']>[number] | undefined) {
  return review?.expected_action ?? review?.top1?.action ?? null;
}

function candidateTeachers(candidate: DecisionLogEntry['candidates'][number]) {
  return candidate.teachers ?? (candidate.teacher ? [candidate.teacher] : []);
}

function teacherValueFor(candidate: DecisionLogEntry['candidates'][number], model: string | null | undefined) {
  if (!model) return null;
  return candidateTeachers(candidate).find((teacher) => teacher.model === model) ?? null;
}

function sortCandidatesForReviewer(
  candidates: DecisionLogEntry['candidates'],
  activeModel: string | null | undefined,
) {
  const list = [...candidates];
  if (!activeModel) {
    return list.sort((a, b) => displayScore(b) - displayScore(a));
  }
  return list.sort((a, b) => {
    const at = teacherValueFor(a, activeModel);
    const bt = teacherValueFor(b, activeModel);
    const av = at?.q_value;
    const bv = bt?.q_value;
    const aq = typeof av === 'number' && Number.isFinite(av) ? av : -Infinity;
    const bq = typeof bv === 'number' && Number.isFinite(bv) ? bv : -Infinity;
    if (bq !== aq) return bq - aq;
    const ap = typeof at?.prob === 'number' && Number.isFinite(at.prob) ? at.prob : -Infinity;
    const bp = typeof bt?.prob === 'number' && Number.isFinite(bt.prob) ? bt.prob : -Infinity;
    if (bp !== ap) return bp - ap;
    return displayScore(b) - displayScore(a);
  });
}

function ensureVisibleCandidates(
  candidates: DecisionLogEntry['candidates'],
  chosen: DecisionLogEntry['chosen'],
  gtAction: DecisionLogEntry['gt_action'],
  teacherReviews: NonNullable<DecisionLogEntry['teacher_reviews']> = [],
  limit = 12,
): DecisionLogEntry['candidates'] {
  const sorted = [...candidates].sort((a, b) => displayScore(b) - displayScore(a));
  const visible = sorted.slice(0, limit);

  const ensureAction = (action: DecisionLogEntry['chosen'] | DecisionLogEntry['gt_action']) => {
    if (!action) return;
    const alreadyVisible = visible.some((candidate) => sameReplayAction(candidate.action, action));
    if (alreadyVisible) return;
    const matched = sorted.find((candidate) => sameReplayAction(candidate.action, action));
    if (matched) {
      visible.push(matched);
    }
  };

  ensureAction(chosen);
  ensureAction(gtAction);
  for (const teacherReview of teacherReviews) {
    ensureAction(teacherReview?.expected_action ?? teacherReview?.top1?.action ?? null);
    ensureAction(teacherReview?.top2?.action ?? null);
  }

  return visible.sort((a, b) => displayScore(b) - displayScore(a));
}

/** 动作的短名（用于表格第一列） */
function shortLabel(action: { type: string; pai?: string; consumed?: string[] }): string {
  switch (action.type) {
    case 'dahai':   return action.pai ?? '?';
    case 'reach':   return action.pai ? `${action.pai}立直` : '立直';
    case 'none':    return '过';
    case 'hora':    return '和牌';
    case 'chi':     return `吃${action.pai ?? ''}`;
    case 'pon':     return `碰${action.pai ?? ''}`;
    case 'daiminkan': return `杠${action.pai ?? ''}`;
    case 'ankan':   return `暗杠`;
    case 'kakan':   return `加杠`;
    case 'ryukyoku': return '流局';
    default:        return action.type;
  }
}

export function ReplayDecisionPanel({
  entry,
  compact = false,
  playerNames = [],
  currentPlayerId,
  onSwitchPlayer,
  availableTeacherModels = [],
  activeTeacherModel: controlledActiveTeacherModel,
  onActiveTeacherModelChange,
  hideWeights = false,
}: ReplayDecisionPanelProps) {
  const teacherReviews = useMemo(
    () => entry
      ? (entry.teacher_reviews && entry.teacher_reviews.length > 0
        ? entry.teacher_reviews
        : entry.teacher_review ? [entry.teacher_review] : [])
      : [],
    [entry],
  );
  const teacherModels = useMemo(
    () => Array.from(new Set([
      ...availableTeacherModels,
      ...teacherReviews.map((review) => review.model),
    ].filter(Boolean))),
    [availableTeacherModels, teacherReviews],
  );
  const teacherModelsKey = teacherModels.join('\n');
  const [activeTeacherModel, setActiveTeacherModel] = useState<string | null>(null);
  const selectedTeacherModel = controlledActiveTeacherModel !== undefined
    ? controlledActiveTeacherModel
    : activeTeacherModel;
  const updateTeacherModel = (model: string | null) => {
    if (onActiveTeacherModelChange) onActiveTeacherModelChange(model);
    else setActiveTeacherModel(model);
  };

  useEffect(() => {
    if (controlledActiveTeacherModel !== undefined) return;
    if (teacherModels.length === 0) {
      if (activeTeacherModel !== null) setActiveTeacherModel(null);
      return;
    }
    if (!activeTeacherModel || !teacherModels.includes(activeTeacherModel)) {
      setActiveTeacherModel(teacherModels[0]);
    }
  }, [teacherModelsKey, controlledActiveTeacherModel, activeTeacherModel, teacherModels]);

  const teacherSelector = teacherModels.length > 0 ? (
    <div style={sectionStyle}>
      <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
        {teacherModels.map((model) => (
          <button
            key={model}
            type="button"
            onClick={() => updateTeacherModel(model)}
            style={teacherChipStyle(model === selectedTeacherModel)}
          >
            {model}
          </button>
        ))}
      </div>
    </div>
  ) : null;

  if (!entry) {
    return (
      <div style={panelStyle(compact)}>
        {teacherSelector}
        <div style={{ color: 'var(--text-muted)', fontSize: 12, padding: 16 }}>无数据</div>
      </div>
    );
  }

  // obs 步：其他家操作，无 bot 推理数据
  if (entry.is_obs) {
    return (
      <div style={panelStyle(compact)}>
        {teacherSelector}
      </div>
    );
  }

  const { chosen, gt_action, candidates } = entry;
  const comparisonExempt = Boolean(entry.comparison_exempt);

  const activeTeacherReview = teacherReviews.find((review) => review.model === selectedTeacherModel);
  const teacherExpected = teacherAction(activeTeacherReview);
  const usesJointReachCandidates = activeTeacherReview?.display_mode === 'joint_reach_dahai'
    && Boolean(activeTeacherReview.candidates?.length);
  const jointReachCandidates: DecisionLogEntry['candidates'] = usesJointReachCandidates
    ? (activeTeacherReview?.candidates ?? []).map((candidate) => ({
        action: candidate.action,
        logit: candidate.q_value ?? 0,
        final_score: candidate.q_value ?? 0,
        prob: candidate.prob ?? undefined,
        teachers: [{
          model: activeTeacherReview?.model ?? '',
          q_value: candidate.q_value,
          prob: candidate.prob,
          rank: candidate.rank,
        }],
      }))
    : [];

  // 按当前 reviewer 的 q 值降序排序；仍强制展示 Bot、实际动作和 teacher 前排动作。
  const visibleCandidates = usesJointReachCandidates
    ? jointReachCandidates
    : ensureVisibleCandidates(candidates, chosen, gt_action, teacherReviews, 12);
  const sorted = sortCandidatesForReviewer(visibleCandidates, selectedTeacherModel);
  const actualActionForRows = comparisonExempt
    ? null
    : usesJointReachCandidates
    ? activeTeacherReview?.actual_action ?? null
    : gt_action;

  const fallbackProbs = softmaxProbabilities(candidates.map((candidate) => displayScore(candidate)));
  const fallbackProbByCandidate = new Map<DecisionLogEntry['candidates'][number], number>(
    candidates.map((candidate, idx) => [candidate, fallbackProbs[idx] ?? 0]),
  );
  const probabilityOf = (candidate: DecisionLogEntry['candidates'][number]): number => {
    const backendProb = candidate.prob;
    return typeof backendProb === 'number' && Number.isFinite(backendProb)
      ? backendProb
      : fallbackProbByCandidate.get(candidate) ?? 0;
  };

  return (
    <div style={panelStyle(compact)}>
      {teacherSelector}

      {comparisonExempt && (
        <div style={{
          marginBottom: 8,
          padding: '6px 8px',
          border: '1px solid rgba(180, 83, 9, 0.24)',
          borderRadius: 4,
          background: 'rgba(245, 158, 11, 0.08)',
          color: '#92400e',
          fontSize: 11,
        }}>
          响应被其他玩家的更高优先级动作截断，本步不计入错误统计
        </div>
      )}

      {/* 权重表格（post 阶段隐藏） */}
      {!hideWeights && (
      <div style={{ ...sectionStyle, flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 12, fontWeight: 800, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            候选动作
          </span>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-muted)', opacity: 0.8 }}>
            {selectedTeacherModel ?? '本地'} Q / P
          </span>
        </div>

        {/* 表头 */}
        <div style={tableHeaderStyle}>
          <div style={{ width: COL1_W }}>动作</div>
          <div style={{ flex: 1, textAlign: 'right' }}>Q</div>
          <div style={{ width: 66, textAlign: 'right' }}>P</div>
        </div>

        {/* 行 */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {sorted.map((c, idx) => {
            const isChosen = sameReplayAction(c.action, chosen);
            const isGt = sameReplayAction(c.action, actualActionForRows);
            const isTeacher = teacherExpected ? sameReplayAction(c.action, teacherExpected) : false;
            const probability = probabilityOf(c);
            const teacher = selectedTeacherModel ? teacherValueFor(c, selectedTeacherModel) : null;
            const scoreLabel = selectedTeacherModel ? displayOptionalScore(teacher?.q_value) : displayScoreLabel(c);
            const probLabel = selectedTeacherModel ? displayOptionalProb(teacher?.prob) : displayProbLabel(probability);

            const barColor = isChosen && isGt ? '#8e44ad'
              : isChosen ? '#e74c3c'
              : isGt ? '#27ae60'
              : isTeacher ? '#8e44ad'
              : 'var(--accent)';

            const rowBg = isChosen && isGt ? 'rgba(142,68,173,0.08)'
              : isChosen ? 'rgba(231,76,60,0.07)'
              : isGt ? 'rgba(39,174,96,0.07)'
              : isTeacher ? 'rgba(142,68,173,0.08)'
              : idx % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.02)';

            return (
              <div key={idx} style={{
                display: 'flex', alignItems: 'center', gap: 0,
                padding: '3px 0',
                background: rowBg,
                borderRadius: 3,
              }}>
                {/* 第一列：牌名 + 标记 */}
                <div style={{
                  width: COL1_W, flexShrink: 0,
                  fontSize: 13, fontFamily: 'Menlo, monospace',
                  fontWeight: isChosen || isGt || isTeacher ? 700 : 400,
                  color: barColor,
                  display: 'flex', alignItems: 'center', gap: 3,
                }}>
                  {shortLabel(c.action)}
                  {isChosen && <span style={{ fontSize: 9, color: '#e74c3c' }}>★</span>}
                  {isGt && !isChosen && <span style={{ fontSize: 9, color: '#27ae60' }}>●</span>}
                  {isTeacher && <span style={{ fontSize: 9, color: '#8e44ad' }}>T</span>}
                </div>

                <div style={{
                  flex: 1,
                  textAlign: 'right',
                  fontSize: 13,
                  fontFamily: 'Menlo, monospace',
                  color: isChosen || isGt || isTeacher ? barColor : 'var(--text-muted)',
                  fontWeight: isChosen || isGt || isTeacher ? 700 : 400,
                }}>
                  {scoreLabel}
                </div>
                <div style={{
                  width: 66,
                  textAlign: 'right',
                  flexShrink: 0,
                  fontSize: 13,
                  fontFamily: 'Menlo, monospace',
                  color: isChosen || isGt || isTeacher ? barColor : 'var(--text-muted)',
                  fontWeight: isChosen || isGt || isTeacher ? 700 : 400,
                }}>
                  {probLabel}
                </div>
              </div>
            );
          })}
          {sorted.length === 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', paddingTop: 8 }}>无候选数据</div>
          )}
        </div>
      </div>
      )}

      {playerNames.length > 0 && onSwitchPlayer && currentPlayerId !== undefined && (
        <>
          <div style={dividerStyle} />
          <div style={{ ...sectionStyle, paddingTop: 8 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>切换主视角</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {playerNames.map((name, idx) => {
                const active = idx === currentPlayerId;
                return (
                  <button
                    key={idx}
                    onClick={() => onSwitchPlayer(idx)}
                    disabled={active}
                    style={{
                      ...switchBtnStyle,
                      borderColor: active ? 'var(--accent)' : 'var(--border)',
                      background: active ? 'rgba(52, 152, 219, 0.10)' : 'var(--card-bg)',
                      color: active ? 'var(--accent)' : 'var(--text-primary)',
                      cursor: active ? 'default' : 'pointer',
                    }}
                    title={`切换到 ${name}`}
                  >
                    <span style={{ fontWeight: 700 }}>P{idx}</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 样式常量
// ---------------------------------------------------------------------------
const COL1_W = 56;

const panelStyle = (compact: boolean): React.CSSProperties => ({
  width: '100%',
  minHeight: compact ? 240 : undefined,
  maxHeight: compact ? 300 : undefined,
  flex: compact ? undefined : 1,
  minWidth: 0,
  flexShrink: compact ? 0 : 1,
  height: compact ? 'auto' : undefined,
  background: 'var(--card-bg)',
  borderLeft: 'none',
  borderTop: compact ? '1px solid var(--sidepanel-border)' : 'none',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
});

const sectionStyle: React.CSSProperties = {
  padding: '8px 10px',
};

const dividerStyle: React.CSSProperties = {
  height: 1,
  background: 'var(--overlay-border)',
  flexShrink: 0,
};

const tableHeaderStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  fontSize: 10,
  fontWeight: 700,
  color: 'var(--text-muted)',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  paddingBottom: 4,
  borderBottom: '1px solid var(--border)',
  marginBottom: 4,
};

function teacherChipStyle(active: boolean): React.CSSProperties {
  const color = active ? '#8e44ad' : '#8a8f98';
  return {
    minHeight: 22,
    padding: '3px 8px',
    borderRadius: 5,
    border: `1px solid ${active ? 'rgba(142,68,173,0.55)' : 'rgba(127,127,127,0.26)'}`,
    background: active ? 'rgba(142,68,173,0.16)' : 'rgba(127,127,127,0.10)',
    color,
    fontSize: 10,
    fontWeight: active ? 800 : 650,
    cursor: 'pointer',
    lineHeight: 1.2,
  };
}

const switchBtnStyle: React.CSSProperties = {
  width: '100%',
  borderRadius: 8,
  border: '1px solid var(--border)',
  padding: '8px 10px',
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  fontSize: 12,
  textAlign: 'left',
};
