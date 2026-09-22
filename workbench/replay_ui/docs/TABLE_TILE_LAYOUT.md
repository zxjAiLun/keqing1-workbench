# Review 牌桌副露与牌河布局（2026-09-22）

## 问题与修复

旧实现为左右家单独修正了普通牌的朝向，但横牌仍使用另一套反向矩阵；对家/下家的组内顺序也没有随玩家视角旋转。`alignItems: center` 与半张牌的加杠重叠，使混合碰杠的外沿和牌面参差不齐。

现在统一使用玩家局部坐标：先从玩家自己的左向右排牌，再同时旋转 **位置、包围盒和牌面**。

| 屏幕位置 | 内部名称 | 普通牌 / 横牌 | 组内左→右的屏幕方向 | 对齐外沿 |
|---|---|---|---|---|
| 下方（本人） | south | 0° / 90° | 左→右 | 底边 |
| 右侧（下家） | west | 270° / 0° | 下→上 | 右边 |
| 上方（对家） | north | 180° / 270° | 右→左 | 顶边 |
| 左侧（上家） | east | 90° / 180° | 上→下 | 左边 |

这里的 `east` / `west` 是历史上的屏幕位置名，不是玩家的绝对风位。

- 鸣牌来源按绝对 actor/target 计算，切换 review 视角不改变来源。
- 下家 P1 碰对家 P2 的东风：被鸣牌在右侧副露的最上端，牌面为屏幕 0°；不能沿用旧的 180°。
- 所有玩家最早的副露都在自己的最右端，后鸣的组向自己的左侧追加。
- 组内间距 1px、组间 8px；暗手与副露分隔独立。
- 加杠的第四张在被鸣横牌靠桌心的一侧完整显示，间隔 1px；整个双层占位计入组尺寸，不遮挡、不溢出。
- 保留大明杠四张平排、暗杠两端牌背的现有表达；不改变回放数据、赤牌身份、pre/post 或模型建议。
- 四条牌河复用同一几何逻辑：六张一行，固定行距，立直横牌与普通牌对齐玩家侧外沿，拐角为横牌额外宽度预留空间。

## 参考

参考 [killerducky/killer_mortal_gui](https://github.com/killerducky/killer_mortal_gui) 的 `master`：

- [`style.css`](https://github.com/killerducky/killer_mortal_gui/blob/master/style.css)：`pov-p0..3`、`rotate` / `float`、牌河局部方向与包围盒；
- [`index.js`](https://github.com/killerducky/killer_mortal_gui/blob/master/index.js)：按相对来源插入被鸣牌、新副露前插、加杠锚定原横牌。

采用其四家视角与整齐对齐的思路，而非复制整套界面或改变本项目的大明杠/暗杠表示。

## 实现位置

- `src/components/BattleBoard/seatLayout.ts`：座位朝向、局部顺序、加杠偏移、底部手牌预留宽度。
- `src/components/BattleBoard/tableTileLayout.ts`：纯函数生成副露与牌河的坐标和占位。
- `src/components/BattleBoard/MahjongTable.tsx`：实际 DOM 使用上述布局，不再分别维护四条牌河。
- `src/components/BattleBoard/tableLayout.ts`：统一间距与中心定位常量。

## 验证

在 `workbench/replay_ui` 下执行：

```sh
npm run check:table-tile-layout
npm run check:review-daiminkan
npm run check:replay-semantics
npm run check:review-diff-semantics
npm run check:review-e2e
npm run build
```

结果：

- 几何回归 792 个场景通过：四个绝对 actor × 四个 review 视角 × 三种来源 × 三种尺寸，吃/碰/大明杠/加杠/暗杠、赤牌、完整牌面、外沿对齐、24 张牌河、立直位置与四河拐角。
- 原副露、回放动作、差异、真实牌谱状态 E2E 检查全部通过。
- 浏览器 11 个完整牌桌场景通过：四个视角、多副露、最宽四杠+摸牌、翻开暗手、浅/深主题、820px 窗口；验证实际 CSS 旋转、来源位置、组/牌外沿、无牌面重叠、图片加载和无 JS 异常。
- 在 `http://127.0.0.1:8000` 的已构建实际 review 页面检查现有牌谱 `replay_63f72488_1790038369?player_id=0&step=183&phase=post`：三家副露成功呈现，无 JS 异常；服务已使用新生成的资源。
- 隔离临时目录负向对照：横牌顺时针改反向后测试失败，恢复即通过；未修改工作树来执行该对照。
- 新增/修改的布局 helper 与检查脚本 ESLint 全绿。`MahjongTable.tsx` 的 2 个 `set-state-in-effect` 错误及 2 个依赖告警在 HEAD 原版中同样存在，本次未新增，也未扩大范围修改旧 Hook 行为。

本机浏览器报告、测试脚本、截图与构建日志位于（忽略跟踪的）`artifacts/table-layout-20260922/`，包括 `browser-report.json`、`owner-pon.png`、`mixed-light.png`、`real-review.png` 与 `negative-control.json`。
