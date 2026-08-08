// src/replay_ui/src/components/Matches/labels.ts
import type { MatchSeat, SourceType } from '../../types/participants';

export const SOURCE_LABELS: Record<SourceType, string> = {
  native: '本地',
  imported: '导入',
  manual: '手动',
};

export const SEAT_WINDS = ['東', '南', '西', '北'];

export const EMPTY_SEATS: MatchSeat[] = SEAT_WINDS.map((_, seat) => ({
  seat: seat as 0 | 1 | 2 | 3,
  account_id: '',
  controller_type: null,
  model_identity_id: null,
  model_artifact_id: null,
}));

export const FORCE_REASON_OPTIONS = ['罚符', '中途结束', '外部结算', '人工修正'];
