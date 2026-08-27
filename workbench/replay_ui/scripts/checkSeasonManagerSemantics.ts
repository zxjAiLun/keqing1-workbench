// src/replay_ui/scripts/checkSeasonManagerSemantics.ts
// R15：Season Manager 终局摘要（Archive Summary）语义的结构化静态检查。
//
// 检查（只读消费冻结 API，不在 UI 重算权威事实）：
// 1. ladderApi 提供 finalizeSeason / getArchiveSummary client；
// 2. SeasonManagerPage 消费 getArchiveSummary（不直接 fetch archive-summary）；
// 3. sealed 与 preview 两种展示路径都存在（kind === 'sealed' / 'preview'）；
// 4. UI 不重算 seal：页面不得出现 archive_summary_hash 计算或 completed_at 拼接逻辑；
// 5. frozen 赛季结束按钮区分 finalize 语义（结束并封存摘要）；
// 6. types 包含 SeasonArchiveSummary / ArchiveSummaryResponse 契约；
// 7. LadderSeasonConfig 含 completed_at / archive_summary 只读字段。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(import.meta.dirname, '../src');

const PAGE = 'pages/SeasonManagerPage.tsx';
const API = 'api/ladderApi.ts';
const TYPES = 'types/ladder.ts';

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

const api = read(API);
const page = read(PAGE);
const types = read(TYPES);

// 1. API client
check(/finalizeSeason/.test(api), 'ladderApi 应提供 finalizeSeason');
check(/getArchiveSummary/.test(api), 'ladderApi 应提供 getArchiveSummary');
check(/\/finalize/.test(api), 'finalizeSeason 应指向 /finalize endpoint');
check(/\/archive-summary/.test(api), 'getArchiveSummary 应指向 /archive-summary endpoint');
check(/ArchiveSummaryResponse/.test(api), 'getArchiveSummary 应使用 ArchiveSummaryResponse 类型');

// 2. 页面消费 API client（不直接 fetch）
check(/ladderApi\.getArchiveSummary/.test(page), 'SeasonManagerPage 应通过 ladderApi.getArchiveSummary 消费终局摘要');
check(!/fetch\([^)]*archive-summary/.test(page), 'SeasonManagerPage 不应直接 fetch archive-summary');

// 3. sealed / preview 双路径
check(/kind === 'sealed'/.test(page), 'SeasonManagerPage 应展示 sealed（persisted 权威）摘要');
check(/kind === 'preview'/.test(page), 'SeasonManagerPage 应展示 preview（running 实时预览）');
check(/archive_summary_projection/.test(page), 'preview 路径应消费 archive_summary_projection（非权威投影）');

// 4. UI 不重算权威事实
check(!/sha256|SHA-?256|createHash/.test(page), 'SeasonManagerPage 不得在 UI 计算 seal hash');
check(!/completed_at\s*=\s*new Date/.test(page), 'SeasonManagerPage 不得在 UI 生成 completed_at');

// 5. frozen 结束语义
check(/结束并封存摘要/.test(page), 'frozen 赛季结束按钮应区分 finalize（封存摘要）语义');
check(/不可重新开启/.test(page), 'frozen 结束确认文案应说明 sealed 后不可 reopen');

// 6. 类型契约
check(/interface SeasonArchiveSummary/.test(types), 'types 应包含 SeasonArchiveSummary');
check(/ArchiveSummaryResponse/.test(types), 'types 应包含 ArchiveSummaryResponse（sealed|preview 联合）');
check(/archive_summary_hash:\s*string/.test(types), 'SeasonArchiveSummary 应含 archive_summary_hash');
check(/archive_summary_id:\s*string/.test(types), 'SeasonArchiveSummary 应含 archive_summary_id');

// 7. LadderSeasonConfig 只读字段
check(/completed_at\?:\s*string/.test(types), 'LadderSeasonConfig 应含 completed_at（只读）');
check(/archive_summary\?:\s*SeasonArchiveSummary \| null/.test(types), 'LadderSeasonConfig 应含 archive_summary（只读）');

if (failures > 0) {
  console.error(`season manager semantics: ${failures} failure(s)`);
  process.exit(1);
}
console.log('season manager semantics OK (archive summary read-only consumption, sealed/preview split, finalize semantics)');
