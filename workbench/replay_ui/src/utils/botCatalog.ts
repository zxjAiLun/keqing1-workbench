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
    value: 'rulebase',
    label: 'rulebase',
    shortLabel: '基线',
    badge: 'Baseline',
    description: '规则基线，用于兼容对照和快速 sanity check。',
  },
];

const GUI_MODEL_ORDER: BotType[] = ['ext_mortal', '70k', 'mortal'];

export const GUI_BOT_CATALOG: BotCatalogEntry[] = BOT_CATALOG
  .filter((entry) => entry.value !== 'rulebase')
  .sort((left, right) => GUI_MODEL_ORDER.indexOf(left.value) - GUI_MODEL_ORDER.indexOf(right.value));

export const DEFAULT_BOT_TYPE: BotType = 'mortal';

export const BOT_CHECKPOINT_DEFAULTS: Record<BotType, string> = {
  mortal: 'artifacts/experiments/model_pool_2026_07/V2_population_mixed_v4_warmstart_2026_07/checkpoints/mortal_74000.pth',
  '70k': 'artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth',
  ext_mortal: 'artifacts/external_mortal_20240308_best_min.pth',
  rulebase: '',
};

export function getBotCatalogEntry(botType: BotType): BotCatalogEntry {
  return BOT_CATALOG.find((entry) => entry.value === botType) ?? BOT_CATALOG[0];
}
