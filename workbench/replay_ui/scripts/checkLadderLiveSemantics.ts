// src/replay_ui/scripts/checkLadderLiveSemantics.ts
// Ladder 三页 Live 一致性的静态语义检查（轻量，不引入 Vitest）。
//
// 检查：
// 1. 三页（LadderPage / LadderAccountPage / LadderModelPage）均使用共享 useVisibleLiveQuery Hook；
// 2. 三页不再各自注册 setInterval；
// 3. ladderApi 的 fetch 使用 cache: 'no-store'；
// 4. ladderApi 四个方法均支持 AbortSignal；
// 5. 三页均使用共享 LadderSnapshotStatus 组件；
// 6. 轮询间隔仍为 30_000ms（useVisibleLiveQuery 默认 intervalMs）。
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SRC = resolve(import.meta.dirname, '../src');

const PAGES = [
  'pages/LadderPage.tsx',
  'pages/LadderAccountPage.tsx',
  'pages/LadderModelPage.tsx',
];
const HOOK = 'hooks/useVisibleLiveQuery.ts';
const API = 'api/ladderApi.ts';
const STATUS = 'components/Ladder/LadderSnapshotStatus.tsx';

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

// 1. 三页均使用共享 Hook
for (const page of PAGES) {
  const src = read(page);
  check(
    /useVisibleLiveQuery/.test(src),
    `${page} 应使用 useVisibleLiveQuery`,
  );
}

// 2. 三页不再各自注册 setInterval
for (const page of PAGES) {
  const src = read(page);
  check(
    !/setInterval/.test(src),
    `${page} 不应再自行注册 setInterval`,
  );
}

// 3. ladderApi 使用 cache: 'no-store'
const api = read(API);
check(api.includes("cache: 'no-store'"), 'ladderApi 应使用 cache: "no-store"');

// 4. ladderApi 四个方法均支持 AbortSignal
for (const method of ['listSeasons', 'getLadder', 'getAccount', 'getModel']) {
  const re = new RegExp(`${method}:\\s*\\([^)]*signal`);
  check(re.test(api), `ladderApi.${method} 应支持 AbortSignal`);
}

// 5. 三页均使用共享 snapshot status 组件
for (const page of PAGES) {
  const src = read(page);
  check(
    /LadderSnapshotStatus/.test(src),
    `${page} 应使用 LadderSnapshotStatus 组件`,
  );
}

// 6. 轮询间隔仍为 30 秒（hook 默认 30_000）
const hook = read(HOOK);
check(
  /intervalMs\s*=\s*30_000/.test(hook),
  'useVisibleLiveQuery 默认轮询间隔应为 30_000ms',
);

// 7. 三页均渲染查询首次错误（query.error 出现在 JSX 中），
//    避免 409/500 首次失败被误报为"未找到账号/模型"或空白。
for (const page of PAGES) {
  const src = read(page);
  check(
    /\.error\s*[)}]/.test(src) || /\?\? .*\.error/.test(src),
    `${page} 应展示查询 query.error（首次失败）`,
  );
  check(
    /\.error/.test(src),
    `${page} 应在渲染中引用 query.error`,
  );
}

// 8. Hook 的 visibility 限制只能针对 poll（initial 始终执行）
check(
  /mode === 'poll' && document\.visibilityState !== 'visible'/.test(hook),
  'useVisibleLiveQuery visibility guard 应仅针对 poll',
);

// 9. Hook 的 finally 必须带 active-request identity（owner-only 释放）
check(
  /activeRef\.current === request/.test(hook),
  'useVisibleLiveQuery finally 必须有 active-request identity 判断',
);

// 10. Hook 返回 data 前必须核对 state key（render 同步屏蔽旧实体）
check(
  /state\.key === queryKey/.test(hook),
  'useVisibleLiveQuery 返回 data 前应核对 state key',
);

// 11. 轮询 timer 必须使用 intervalMs，且 effect 依赖包含 intervalMs（动态周期重建）
check(
  /setInterval\([\s\S]*?intervalMs\)/.test(hook),
  'useVisibleLiveQuery setInterval 应使用 intervalMs',
);
check(
  /}, \[enabled, intervalMs, run\]\);/.test(hook),
  'useVisibleLiveQuery 轮询 effect 依赖应包含 intervalMs',
);

if (failures > 0) {
  console.error(`ladder live semantics FAILED (${failures} issues)`);
  process.exit(1);
}
console.log(`ladder live semantics OK (${PAGES.length} pages, ${STATUS}, ${HOOK}, ${API})`);
