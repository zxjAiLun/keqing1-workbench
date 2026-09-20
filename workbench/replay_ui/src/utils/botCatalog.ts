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
    label: 'V2（历史）',
    shortLabel: 'V2',
    badge: '历史资产',
    description: '历史 mortal 别名仍指向 V2@74000；不再提供新建 Review 选项，不重指到 U32。',
  },
  {
    value: '70k',
    label: 'K0',
    shortLabel: 'K0',
    badge: '基准参考',
    description: 'K0 基准参考权重；内部 ID 保留 70k。DQN 动作价值估计，不是保证准确的真实收益。',
  },
  {
    value: 'ext_mortal',
    label: 'External Mortal',
    shortLabel: 'External',
    badge: '外部参考',
    description: '本地 2024-03-08 external 权重，用于参考；不是网站 Mortal 4.1a/b/c。',
  },
  {
    value: 'p4m11_u32',
    label: 'U32',
    shortLabel: 'U32',
    badge: '实战首选',
    description:
      '新对局默认首选。输出为 policy action score（非校准 Q），Review 可比较动作，但不显示 Q 差收益损失。',
  },
  {
    value: 'm0_72k',
    label: 'M0',
    shortLabel: 'M0',
    badge: '可选备选',
    description:
      'M0_control / seed 20260807 / 72k。DQN calibrated Q；可用于 Play/Review 对照，但不是默认、不是正式天梯晋级。',
  },
  {
    value: 'consensus_v1',
    label: '共识v1',
    shortLabel: '共识',
    badge: '赛季可用',
    description: 'distill_consensus_v3.pth。DQN calibrated Q，已开放赛季天梯/Play/Review。',
  },
  {
    value: 'nova_v1',
    label: 'novav1',
    shortLabel: 'nova',
    badge: '赛季可用',
    description: 'distill_nova.pth。DQN calibrated Q，已开放赛季天梯/Play/Review。',
  },
  {
    value: 'luckyj_v1',
    label: 'luckyjv1',
    shortLabel: 'luckyj',
    badge: '赛季可用',
    description: 'luckyj_clone_v1.pth。DQN calibrated Q，已开放赛季天梯/Play/Review。',
  },
  {
    value: 'unknown_v1',
    label: '未知v1',
    shortLabel: '未知',
    badge: '赛季可用',
    description: 'unknown1.pth。Policy action score，已开放赛季天梯/Play/Review。',
  },
  {
    value: 'nova_v2',
    label: 'novav2',
    shortLabel: 'nova2',
    badge: '赛季可用',
    description: 'distill_nova_v2.pth。DQN calibrated Q，已开放赛季天梯/Play/Review。',
  },
  {
    value: 'rulebase',
    label: 'rulebase',
    shortLabel: '基线',
    badge: 'Baseline',
    description: '规则基线，用于兼容对照和快速 sanity check。',
  },
];

const GUI_MODEL_ORDER: BotType[] = [
  'p4m11_u32',
  'm0_72k',
  '70k',
  'ext_mortal',
  'consensus_v1',
  'nova_v1',
  'luckyj_v1',
  'unknown_v1',
  'nova_v2',
];

export const GUI_BOT_CATALOG: BotCatalogEntry[] = BOT_CATALOG
  .filter((entry) => GUI_MODEL_ORDER.includes(entry.value))
  .sort((left, right) => GUI_MODEL_ORDER.indexOf(left.value) - GUI_MODEL_ORDER.indexOf(right.value));

// New-game preference is NOT the Review teacher default.
export const DEFAULT_BOT_TYPE: BotType = 'p4m11_u32';
export const DEFAULT_REVIEW_BOT_TYPE: BotType = 'ext_mortal';
export const DEFAULT_REVIEW_MODELS: BotType[] = ['ext_mortal', '70k'];
export const REVIEW_BOT_CATALOG = [
  'ext_mortal',
  '70k',
  'p4m11_u32',
  'm0_72k',
  'consensus_v1',
  'nova_v1',
  'luckyj_v1',
  'unknown_v1',
  'nova_v2',
].map((id) => BOT_CATALOG.find((entry) => entry.value === id)!);

export function defaultPlayModel(models: ReadonlyArray<{ model_id: string }>): string {
  // Never silently replace the preferred model if a stale server omits it.
  return models.some((model) => model.model_id === DEFAULT_BOT_TYPE) ? DEFAULT_BOT_TYPE : '';
}

export const BOT_CHECKPOINT_DEFAULTS: Record<BotType, string> = {
  mortal: 'artifacts/experiments/model_pool_2026_07/V2_population_mixed_v4_warmstart_2026_07/checkpoints/mortal_74000.pth',
  '70k': 'artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth',
  ext_mortal: 'artifacts/external_mortal_20240308_best_min.pth',
  p4m11_u32: 'mortal/authoritative/P4M11_U32_2026_09/models/P4M11_U32/U32_eval_weights.pth',
  m0_72k: 'mortal/authoritative/M0_72k_s20260807/models/M0_72k/mortal_72000.pth',
  consensus_v1: 'mortal/authoritative/external/distill_consensus_v3.pth',
  nova_v1: 'mortal/authoritative/external/distill_nova.pth',
  luckyj_v1: 'mortal/authoritative/external/luckyj_clone_v1.pth',
  unknown_v1: 'mortal/authoritative/external/unknown1.pth',
  nova_v2: 'mortal/authoritative/external/distill_nova_v2.pth',
  rulebase: '',
};

export function getBotCatalogEntry(botType: BotType): BotCatalogEntry {
  return BOT_CATALOG.find((entry) => entry.value === botType) ?? BOT_CATALOG[0];
}
