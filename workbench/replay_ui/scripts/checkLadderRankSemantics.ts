// workbench/replay_ui/scripts/checkLadderRankSemantics.ts
// 第八轮：版本化计分引擎 + 天凤段位 UI 语义的结构化静态检查（轻量，不引入 Vitest）。
//
// 检查：
// 1. 天梯默认排序为段位（rank），不再是 PT；
// 2. 类型包含段位字段（rank_ordinal / pt_target 可空 / transitions / avg_pt_delta）；
// 3. scoring 类型包含 tier_policy / positive_pt_tables；
// 4. 天梯表展示段位名与 PT 升段进度；
// 5. 模型聚合展示最高/中位段位与段位分布，不再无条件平均 PT；
// 6. 账号页展示段位变化与个人计分档位（pt_tier / positive_pt）；
// 7. report builder 不再用固定七段常量计算 ledger，且不再有卓别字段；
// 8. 新测试文件在 pytest python_files 白名单；
// 9. manifest 写入 resolved scoring 契约；
// 10. 计分引擎不再模拟房间资格（无 highest_common_eligible / _eligible / table_room）。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(import.meta.dirname, '../src');
const REPO = resolve(import.meta.dirname, '../../..');

function read(rel: string): string {
  return readFileSync(resolve(SRC, rel), 'utf8');
}

let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

// 1. 天梯默认排序为段位，不是 PT
const ladderPage = read('pages/LadderPage.tsx');
check(/SORT_OPTIONS\s*=\s*\[[^\]]*\{ value: 'rank'/.test(ladderPage), 'LadderPage 排序首项应为段位（rank）');
check(/useState\('rank'\)/.test(ladderPage), 'LadderPage 默认排序应为 rank');
check(!/useState\('pt'\)/.test(ladderPage), 'LadderPage 不应默认按 PT 排序');

// 2. 类型包含段位字段
const types = read('types/ladder.ts');
check(/rank_ordinal/.test(types), 'types 应包含 rank_ordinal');
check(/pt_target: number \| null/.test(types), 'LadderAccountRow.pt_target 应为 number | null（天凤位无目标）');
check(/promotions\?:/.test(types), 'LadderAccountRow 应包含 promotions');
check(/demotions\?:/.test(types), 'LadderAccountRow 应包含 demotions');
check(/avg_pt_delta\?:/.test(types), 'LadderAccountRow 应包含 avg_pt_delta');
check(/highest_rank_id\?:/.test(types), 'LadderAccountRow 应包含 highest_rank_id');
check(/rank_distribution\?:/.test(types), 'LadderModelSummary 应包含 rank_distribution');
check(/median_rank_name\?:/.test(types), 'LadderModelSummary 应包含 median_rank_name');

// 3. scoring 类型包含个人档位描述（不含房间资格）
check(/system\?: string/.test(types), 'LadderSeasonScoring 应包含 system');
check(/version\?: string/.test(types), 'LadderSeasonScoring 应包含 version');
check(/tier_policy\?: string/.test(types), 'LadderSeasonScoring 应包含 tier_policy');
check(/positive_pt_tables\?:/.test(types), 'LadderSeasonScoring 应包含 positive_pt_tables');

// 4. 天梯表展示段位名与升段进度
check(/row\.rank_name/.test(ladderPage), 'LadderPage 表应展示段位名');
check(/pt_target !== null/.test(ladderPage), 'LadderPage 应处理可空 pt_target（天凤位）');
check(/天凤位/.test(ladderPage), 'LadderPage 应展示天凤位状态');

// 5. 模型聚合展示最高/中位段位与分布
check(/highest_rank_name/.test(ladderPage), 'LadderPage 模型卡应展示最高段位');
check(/median_rank_name/.test(ladderPage), 'LadderPage 模型卡应展示中位段位');
check(/rank_distribution/.test(ladderPage), 'LadderPage 模型卡应展示段位分布');

// 6. 账号页展示段位变化与个人计分档位
const accountPage = read('pages/LadderAccountPage.tsx');
check(/rank_before/.test(accountPage), '账号页应展示赛前段位');
check(/rank_after/.test(accountPage), '账号页应展示赛后段位');
check(/transition/.test(accountPage), '账号页应展示段位 transition');
check(/pt_tier/.test(accountPage), '账号页应展示个人计分档位（pt_tier）');
check(/positive_pt/.test(accountPage), '账号页应展示正分档位（positive_pt）');
check(!/table_room/.test(accountPage), '账号页不应再展示卓别（table_room）');
check(/promotions/.test(accountPage), '账号页应展示升段次数');
check(/demotions/.test(accountPage), '账号页应展示降段次数');

// 7. report builder 使用个人档位结算，无卓别字段
const builder = readFileSync(resolve(REPO, 'training/mortal/build_platform_account_report.py'), 'utf8');
check(/rank_system\.apply_result/.test(builder), 'report builder 应经 rank_system.apply_result 计算 PT');
check(/create_rank_system/.test(builder), 'report builder 应使用 create_rank_system');
check(/pre_states/.test(builder), 'report builder 应使用赛前状态快照（避免污染桌均值）');
check(/pt_tier/.test(builder), 'report builder ledger 应记录 pt_tier');
check(!/table_room/.test(builder), 'report builder 不应再写 table_room');
check(/match_context/.test(builder), 'report builder 应使用 match_context（共享上下文只含 game_length/桌均R）');

// 8. 新测试文件在 pytest 白名单
const pyproject = readFileSync(resolve(REPO, 'pyproject.toml'), 'utf8');
check(/test_rank_systems\.py/.test(pyproject), 'pyproject 应包含 test_rank_systems.py');
check(/test_report_builder_rank\.py/.test(pyproject), 'pyproject 应包含 test_report_builder_rank.py');

// 9. manifest 写入 resolved scoring 契约
const publisher = readFileSync(resolve(REPO, 'training/mortal/publish_ladder_snapshot.py'), 'utf8');
check(/scoring_config_hash/.test(publisher), 'manifest 应写入 scoring_config_hash');
check(/scoring_system/.test(publisher), 'manifest 应写入 scoring_system');

// 10. 计分引擎不再模拟房间资格
const tenhou = readFileSync(resolve(REPO, 'workbench/replay/rank_systems/tenhou.py'), 'utf8');
check(/progression_tier/.test(tenhou), 'tenhou profile 应包含 progression_tier');
check(/positive_pt_for/.test(tenhou), 'tenhou profile 应包含 positive_pt_for');
check(!/highest_common_eligible/.test(tenhou), 'tenhou profile 不应含 highest_common_eligible');
check(!/def _eligible/.test(tenhou), 'tenhou profile 不应含 _eligible');
check(!/room_policy/.test(tenhou), 'tenhou profile 不应含 room_policy');
check(/tenhou_rank_progression/.test(tenhou), 'tenhou profile 应为 tenhou_rank_progression');

if (failures > 0) {
  console.error(`ladder rank semantics FAILED (${failures} issues)`);
  process.exit(1);
}
console.log('ladder rank semantics OK (rank sort, tier types, progression tiers, builder engine)');
