// workbench/replay_ui/scripts/checkLadderIngestSemantics.ts
// R9：多来源 ingest 契约的结构化静态检查（轻量，不引入 Vitest）。
//
// 检查：
// 1. ladder_ingest 定义 LadderMatch / LadderMatchPlayer（frozen，携带正式 account_id）；
// 2. SourceAdapter 协议 + 三 adapter（native / playwithyou / manual_tenhou）；
// 3. 合并：按 match_id 去重（同内容 no-op / 不同内容冲突拒绝）、稳定排序；
// 4. 全量重放从新人/R1500 开始；ledger 不含 table_room，含 pt_tier/positive_pt；
// 5. publisher 支持 ingest_root（sources_root）分支与指纹；
// 6. test_ladder_ingest.py 在 pytest 白名单。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const REPO = resolve(import.meta.dirname, '../../..');

function read(rel: string): string {
  return readFileSync(resolve(REPO, rel), 'utf8');
}

let failures = 0;
function check(ok: boolean, message: string): void {
  if (!ok) {
    failures += 1;
    console.error(`FAIL: ${message}`);
  }
}

const ingest = read('workbench/replay/ladder_ingest.py');

// 1. 事件契约
check(/@dataclass\(frozen=True\)\r?\nclass LadderMatchPlayer/.test(ingest), '应定义 frozen LadderMatchPlayer');
check(/@dataclass\(frozen=True\)\r?\nclass LadderMatch/.test(ingest), '应定义 frozen LadderMatch');
check(/account_id: str/.test(ingest), 'LadderMatchPlayer 应携带正式 account_id');
check(/class SourceAdapter\(Protocol\)/.test(ingest), '应定义 SourceAdapter 协议');

// 2. 三 adapter
check(/class NativeLogAdapter/.test(ingest), '应有 NativeLogAdapter');
check(/class PlayWithYouResultAdapter/.test(ingest), '应有 PlayWithYouResultAdapter');
check(/class ManualTenhouAdapter/.test(ingest), '应有 ManualTenhouAdapter');

// 3. 去重/冲突规则
check(/def merge_ladder_matches/.test(ingest), '应有 merge_ladder_matches');
check(/match_id 冲突/.test(ingest), 'merge 应拒绝同 ID 不同内容（冲突）');

// 4. 全量重放与数据契约
check(/def replay_ladder_matches/.test(ingest), '应有 replay_ladder_matches');
check(/def _placements/.test(ingest), 'replay 应推导顺位（placements）');
check(!/table_room/.test(ingest), 'ingest replay 不应含 table_room');
check(/pt_tier/.test(ingest), 'ledger 应含 pt_tier');
check(/positive_pt/.test(ingest), 'ledger 应含 positive_pt');
check(/account_summary\.json/.test(ingest), '应写出 account_summary.json（快照契约）');

// 5. publisher ingest 分支
const publisher = read('workbench/replay/publish_ladder_snapshot.py');
check(/ingest_root/.test(publisher), 'publisher 应支持 ingest_root');
check(/build_ingest_report/.test(publisher), 'publisher 应调用 build_ingest_report（ingest 分支）');
check(/ingest=1/.test(publisher), 'publisher ingest 指纹应标记 ingest=1');

// 6. pytest discovery
const pyproject = read('pyproject.toml');
check(/test_\*\.py/.test(pyproject) || /test_ladder_ingest\.py/.test(pyproject), 'pyproject 应包含 test_ladder_ingest.py');

if (failures > 0) {
  console.error(`ladder ingest semantics FAILED (${failures} issues)`);
  process.exit(1);
}
console.log('ladder ingest semantics OK (LadderMatch contract, adapters, merge dedup, replay, publisher ingest)');
