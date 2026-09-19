# 2026-09-19 模型产品口径对齐

## 当前决定

- U32：新实战首选；M0：可选备选；70k：展示名 K0，内部 ID 不变。
- Review 默认 External Mortal + K0，与实战默认独立；可手选 U32/M0。
- 新建 Review（React、旧 HTML、multi-teacher API、Review CLI）提供 External、K0、U32 和 M0；V2/mortal 仅保留历史。
- 历史 V2 报告和严格 checkpoint 解析保留；旧 HTTP/Python API 显式调用兼容保留。
- U32 policy action score 不显示 Q 差收益损失；DQN 的 Q 是模型价值估计，不保证真实收益。

## 覆盖入口

`workbench/model_catalog.py` 维护 Python 产品默认/标签，不修改 registry 身份。
前端 `botCatalog.ts` 维护 Play 与 Review 独立目录/默认；`modelDisplay.ts` 只转换展示，
不改 teacher key、路由 ID 或 checkpoint。

Play 首槽、Battle / Bot Battle、Tenhou launcher、BotClientConfig、riichi.dev 均默认具名 U32。
M0 checkpoint、resolver ID、Review/Play 可选项与旧报告读取兼容保留，但不是默认或正式天梯晋级；M0 与 V2@74000 是不同历史 checkpoint，不能互换。
riichi.dev 原有显式 `mortal` → K0 特例不变；主 resolver 的 `mortal` → V2 不变。
赛季 worker 消费冻结路径，历史赛季和产物不跟随默认值。
Review 面板、统计、历史列表、天梯模型/账号页、Participants 预置、Windows 历史列表同步命名。

## 共享登记展示更新（非仓库内数据）

通过 Participants 更新接口，仅修改：
- `model:mortal-70k.label` → K0
- `model:p4m11_u32.label` → U32，note 中过期“非默认”表述改为最终决定
- `account:70k_1号机` / `account:70k_2号机` 的 display_name → K0_1号机 / K0_2号机

已逐字段核验身份/产物关联不变；artifacts、external_revisions 内容不变；
`aliases.json`、`matches.jsonl`、`match_revisions.jsonl` SHA-256 不变。
本地备份和验证：`artifacts/model-policy-20260919-before/`（不提交）。
不新增平台别名、不改 Tenhou 原名、不迁移成绩、不新建 M0 天梯身份；M0 也没有当前 Participants 准入产物。

## 验证

- 相关 Python **217 passed**：product policy/CLI、riichi.dev、U32/M0 replay smoke、
  Participants roster/UX、Review correctness、teacher reports、rulebase battle、responder、Play stop。
- 前端 `check:model-product-policy`、`check:review-diff-semantics`、`check:replay-semantics`、
  `check:participant-semantics`、`check:review-e2e` 均通过；`npm run build` 通过。
  Vite 仍提示已有的大 chunk 与旧 caniuse 数据，不是构建失败。
- 12 项逐项负向对照：Play 默认、Review 默认、V2 allow-list、M0 准入、M0 弃用准入、K0 标签、历史标签解码、
  V2 picker、Play 首槽消费、Review 初始选择、K0 展示、缺 U32 时静默回退、
  riichi.dev 错指 K0、Review CLI 重加 V2。各自失败，恢复文件与 patched 备份字节相同。
  本地证据：`artifacts/model-policy-20260919-negative-controls/`。
- 测试覆盖真实 ASGI 路由/Form 解析、默认模型传入 resolver/推理调用和报表语义；
  未把“目录中存在一个值”等同于消费者已采用该默认。

## 未通过 / 未验证（单列）

- 额外扩展 `test_season_runtime_launcher.py` 时：124 passed / 1 skipped / 1 failed。
  失败为 `test_r14c_partial_respawn_forbidden_409` 在 Windows 调用不存在的 `signal.SIGKILL`。
  该文件与 HEAD 字节/内容未改，非本轮模型口径变更；未修此无关进程测试。
- 此前已知 `test_review_vertical_smoke.py` FakeMortalBot 缺 device 参数问题本轮未跑、未修。
- 检查时 :8000 无监听，没有擅自启动服务；没有浏览器手工点选验收。
  下次用 `.venv-win/Scripts/python.exe workbench/main.py` 启动即可加载新代码和已构建 UI。
- 没跑新比赛、没训练、没改 checkpoint；未改相邻 Experiment/训练仓库和历史研究报告。
