// Display-only aliases. Never use these labels for identity, report alignment,
// checkpoint lookup or teacher selection keys (historical files stay unchanged).
const MODEL_DISPLAY_NAMES: Record<string, string> = {
  '70k': 'K0',
  'Mortal 70k': 'K0',
  'model:mortal-70k': 'K0',
  p4m11_u32: 'U32',
  'model:p4m11_u32': 'U32',
  'P4-M11 U32 (policy)': 'U32',
  m0_72k: 'M0',
  'M0 72k (control)': 'M0',
  mortal: 'V2（历史）',
  'V2 candidate': 'V2（历史）',
  ext_mortal: 'External Mortal',
  consensus_v1: '共识v1',
  'model:consensus_v1': '共识v1',
  nova_v1: 'novav1',
  'model:nova_v1': 'novav1',
  luckyj_v1: 'luckyjv1',
  'model:luckyj_v1': 'luckyjv1',
  unknown_v1: '未知v1',
  'model:unknown_v1': '未知v1',
  nova_v2: 'novav2',
  'model:nova_v2': 'novav2',
};

export function modelDisplayName(value: string): string {
  return MODEL_DISPLAY_NAMES[value] ?? value;
}
