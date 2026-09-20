// Product-choice regression; IDs stay stable while defaults and labels change.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  BOT_CATALOG, GUI_BOT_CATALOG, REVIEW_BOT_CATALOG, DEFAULT_BOT_TYPE,
  DEFAULT_REVIEW_BOT_TYPE, DEFAULT_REVIEW_MODELS, defaultPlayModel,
} from '../src/utils/botCatalog.ts';
import { modelDisplayName } from '../src/utils/modelDisplay.ts';

assert.equal(DEFAULT_BOT_TYPE, 'p4m11_u32');
assert.equal(DEFAULT_REVIEW_BOT_TYPE, 'ext_mortal');
assert.deepEqual(DEFAULT_REVIEW_MODELS, ['ext_mortal', '70k']);
assert.deepEqual(GUI_BOT_CATALOG.map((m) => m.value), [
  'p4m11_u32',
  'm0_72k',
  '70k',
  'ext_mortal',
  'consensus_v1',
  'nova_v1',
  'luckyj_v1',
  'unknown_v1',
  'nova_v2',
]);
assert.deepEqual(REVIEW_BOT_CATALOG.map((m) => m.value), [
  'ext_mortal',
  '70k',
  'p4m11_u32',
  'm0_72k',
  'consensus_v1',
  'nova_v1',
  'luckyj_v1',
  'unknown_v1',
  'nova_v2',
]);
assert.equal(BOT_CATALOG.find((m) => m.value === '70k')?.label, 'K0');
assert.equal(BOT_CATALOG.find((m) => m.value === 'p4m11_u32')?.badge, '实战首选');
assert.equal(BOT_CATALOG.find((m) => m.value === 'm0_72k')?.badge, '可选备选');
assert.equal(defaultPlayModel([{ model_id: '70k' }, { model_id: 'p4m11_u32' }]), 'p4m11_u32');
assert.equal(defaultPlayModel([{ model_id: '70k' }]), '');
assert.equal(defaultPlayModel([]), '');

// Display adapters do not mutate persisted/selection keys or conflate teachers.
const oldModels = ['70k', 'V2 candidate', 'P4-M11 U32 (policy)', 'M0 72k (control)', 'Mortal 4.1b'];
assert.deepEqual(oldModels.map(modelDisplayName), ['K0', 'V2（历史）', 'U32', 'M0', 'Mortal 4.1b']);
assert.equal(oldModels[0], '70k');
assert.equal(modelDisplayName('Mortal 70k'), 'K0');
assert.equal(modelDisplayName('model:mortal-70k'), 'K0');
assert.equal(modelDisplayName('unknown'), 'unknown');

const source = (name: string) => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8');
const upload = source('components/Upload/UploadForm.tsx');
assert.ok(upload.includes('useState<BotType[]>([...DEFAULT_REVIEW_MODELS])'));
assert.ok(upload.includes('REVIEW_BOT_CATALOG.map((bot) => {'));
assert.ok(upload.includes('setSelectedModels(REVIEW_BOT_CATALOG.map((bot) => bot.value))'));
assert.ok(source('pages/PlayWithYouPage.tsx').includes('const first = defaultPlayModel(resp.models)'));
for (const page of ['BattlePage', 'BotBattlePage']) {
  assert.ok(source(`pages/${page}.tsx`).includes('useState<BotType>(DEFAULT_BOT_TYPE)'));
}
assert.ok(source('api/replayApi.ts').includes('botType: BotType = DEFAULT_REVIEW_BOT_TYPE'));
for (const consumer of ['pages/ReviewHistoryPage.tsx', 'pages/LadderPage.tsx',
  'pages/LadderAccountPage.tsx', 'pages/LadderModelPage.tsx',
  'components/DecisionPanel/ReplayDecisionPanel.tsx', 'components/ReviewWorkspace/ReplayStatsDialog.tsx']) {
  assert.ok(source(consumer).includes('modelDisplayName('), `missing display adapter: ${consumer}`);
}
const legacy = readFileSync(new URL('../../replay/templates/replay_page.html', import.meta.url), 'utf8');
assert.ok(!legacy.includes('<option value="mortal"'));
assert.ok(legacy.includes('<option value="p4m11_u32"'));
console.log('PASS model product policy: new-game U32 / Review independent / V2 history only / K0 display');
