// src/replay_ui/scripts/checkPlaywithyouCaptureSemantics.ts
// R9-3：Play-with-you 正式天梯捕获 UI 语义的结构化静态检查（轻量）。
//
// 检查：
// 1. 捕获面板顺位按 final_score 降序 + 同分 seat 升序（最高分=1位，最低分=4位）；
// 2. 不存在旧的升序排序表达式；
// 3. start 请求携带 ladder_capture 绑定；
// 4. 捕获面板支持 confirm / ignore / retry-publish 与状态展示。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(import.meta.dirname, '../src');

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

const page = read('pages/PlayWithYouPage.tsx');
const api = read('api/playwithyouApi.ts');

// 1. 顺位：final_score 降序 + 同分 seat 升序（C25）
check(
  /\.sort\(\(a, b\) => b\.final_score - a\.final_score \|\| a\.seat - b\.seat\)/.test(page),
  'PlayWithYouPage 顺位应按 final_score 降序 + 同分 seat 升序',
);

// 2. 不得出现旧升序排序
check(!/\.sort\(\(a, b\) => a\.final_score - b\.final_score\)/.test(page), '不得再使用升序排序表达式');

// 3. start 携带 ladder_capture 绑定（冻结于开局前）
check(/ladder_capture: captureEnabled/.test(page), 'start 请求应携带 ladder_capture 绑定');
check(/mode: "confirm"/.test(page), 'capture 模式应固定为 confirm');
check(/已冻结/.test(page), '状态区应展示冻结绑定');

// 4. 捕获面板操作与状态
check(/确认录入并发布/.test(page), '捕获面板应有确认录入按钮');
check(/忽略本局/.test(page), '捕获面板应有忽略按钮');
check(/重新发布/.test(page), '捕获面板应有重新发布（retry）按钮');
check(/accepted_publish_failed/.test(page), '捕获面板应处理 accepted_publish_failed 状态');
check(/tenhou_log_url/.test(page), '捕获面板应展示天凤牌谱链接');

// 5. API 客户端
check(/listLadderCaptures/.test(api), 'API 应有 listLadderCaptures');
check(/confirmLadderCapture/.test(api), 'API 应有 confirmLadderCapture');
check(/ignoreLadderCapture/.test(api), 'API 应有 ignoreLadderCapture');
check(/retryPublishLadderCapture/.test(api), 'API 应有 retryPublishLadderCapture');

if (failures > 0) {
  console.error(`playwithyou capture semantics FAILED (${failures} issues)`);
  process.exit(1);
}
console.log('playwithyou capture semantics OK (placement sort, binding, confirm/ignore/retry UI)');
