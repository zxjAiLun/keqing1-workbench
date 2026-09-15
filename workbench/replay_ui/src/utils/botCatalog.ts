import type { BotType } from '../types/bot';

export interface BotCatalogEntry {
  value: BotType;
  label: string;
  shortLabel: string;
  badge: string;
  description: string;
}

export const BOT_CATALOG: BotCatalogEntry[] = [
  {
    value: 'mortal',
    label: 'Mortal candidate',
    shortLabel: 'candidate',
    badge: 'V2 candidate',
    description: 'V2@74000 存在时使用该权重，否则回退到 70k。',
  },
  {
    value: '70k',
    label: 'Mortal 70k',
    shortLabel: '70k',
    badge: '70k 锚点',
    description: '固定 70k 训练步锚点权重。',
  },
  {
    value: 'ext_mortal',
    label: 'External Mortal',
    shortLabel: 'External',
    badge: '外部参考',
    description: 'artifacts/external_mortal_20240308_best_min.pth。外部 Mortal 参考权重。',
  },
  {
    value: 'p4m11_u32',
    label: 'P4-M11 U32 (policy)',
    shortLabel: 'U32',
    badge: 'policy 候选',
    description:
      'P4-M11 直连策略梯度端点（U32）。输出是 policy action score（非校准 Q），Review 不显示收益损失；候选，不是默认模型。',
  },
  {
    value: 'rulebase',
    label: 'rulebase',
    shortLabel: '基线',
    badge: 'Baseline',
    description: '规则基线，用于兼容对照和快速 sanity check。',
  },
];

const GUI_MODEL_ORDER: BotType[] = ['ext_mortal', '70k', 'mortal', 'p4m11_u32'];

export const GUI_BOT_CATALOG: BotCatalogEntry[] = BOT_CATALOG
  .filter((entry) => entry.value !== 'rulebase')
  .sort((left, right) => GUI_MODEL_ORDER.indexOf(left.value) - GUI_MODEL_ORDER.indexOf(right.value));

export const DEFAULT_BOT_TYPE: BotType = 'mortal';

export const BOT_CHECKPOINT_DEFAULTS: Record<BotType, string> = {
  mortal: 'artifacts/experiments/model_pool_2026_07/V2_population_mixed_v4_warmstart_2026_07/checkpoints/mortal_74000.pth',
  '70k': 'artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth',
  ext_mortal: 'artifacts/external_mortal_20240308_best_min.pth',
  p4m11_u32: 'mortal/authoritative/P4M11_U32_2026_09/models/P4M11_U32/U32_eval_weights.pth',
  rulebase: '',
};

export function getBotCatalogEntry(botType: BotType): BotCatalogEntry {
  return BOT_CATALOG.find((entry) => entry.value === botType) ?? BOT_CATALOG[0];
}
