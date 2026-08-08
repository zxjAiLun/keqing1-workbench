// src/replay_ui/src/components/Participants/labels.ts
import type { AccountType, ControllerType } from '../../types/participants';

export const ACCOUNT_TYPE_LABELS: Record<AccountType, string> = {
  human: '真人',
  managed_bot: '本地 AI',
  external_bot: '外部 AI',
};

export const CONTROLLER_LABELS: Record<ControllerType, string> = {
  human_ui: '真人操作',
  local_model: '本地模型',
  external_agent: '外部代理',
  manual_only: '仅登记',
};
