// src/replay_ui/src/components/Participants/modelPresets.ts
// R10 UX Repair P1-5：预置模型（来自 bot_registry MORTAL_CHECKPOINTS / 正式赛季注册表）。
export interface ModelPreset {
  id: string;
  label: string;
  kind: 'local_model' | 'external_agent';
  artifact_path: string;
}

export const MODEL_PRESETS: ModelPreset[] = [
  {
    id: '70k',
    label: 'Mortal 70k',
    kind: 'local_model',
    artifact_path: 'artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth',
  },
  {
    id: 'V2_74000',
    label: 'V2 74000',
    kind: 'local_model',
    artifact_path: 'artifacts/experiments/model_pool_2026_07/V2_population_mixed_v4_warmstart_2026_07/checkpoints/mortal_74000.pth',
  },
  {
    id: 'V3_74000',
    label: 'V3 74000',
    kind: 'local_model',
    artifact_path: 'artifacts/experiments/model_pool_2026_07/V3_final_rank_mc_warmstart_2026_07/checkpoints/mortal_74000.pth',
  },
  {
    id: 'ext_mortal',
    label: 'External Mortal',
    kind: 'external_agent',
    artifact_path: 'artifacts/external_mortal_20240308_best_min.pth',
  },
];
