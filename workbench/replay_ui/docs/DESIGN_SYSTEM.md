# KEQING MJ Workbench — 设计系统规范 v2

> 方向：**深色一体化「雀馆分析台」**
> 参考人格：雀魂的质感 × Mortal 分析器的克制 × Linear 的信息密度
> 目标：全站单一视觉人格；牌桌（Review Workspace）是视觉主角，外壳为其服务。

---

## 1. 设计原则

1. **牌桌即舞台**：页面外壳（侧边栏、面板、工具栏）一律退后，低存在感；所有视觉重量集中在牌桌与分析数据上。
2. **深色唯一**：默认且主推深色主题。浅色仅作为兼容层保留（`data-theme="white"` 降级映射），新组件不再为浅色做专门设计。
3. **金为品牌，青红为语义**：金色只做品牌/强调（立直、胜利、主按钮、激活态），不用于表达正负；正负永远走青/红。
4. **克制动效**：交互反馈 0.12s–0.18s，页面级过渡 ≤0.3s；不用弹性曲线（spring 仅留给 framer-motion 的浮层入场）。
5. **令牌单一事实源**：一切颜色/间距/圆角/阴影必须来自令牌（CSS 变量 + `tokens.ts` 镜像），禁止裸写 hex。

---

## 2. 色彩体系

### 2.1 基底（暖调炭黑）

| 令牌 | 值 | 用途 |
|---|---|---|
| `--page-bg` | `#14110c` | 页面底色（暖黑，带 5% 琥珀倾向） |
| `--surface-1` | `#1c1913` | 卡片/面板底 |
| `--surface-2` | `#242019` | 悬浮面板、抽屉、下拉 |
| `--surface-3` | `#2d2820` | hover/pressed 态 |
| `--border` | `rgba(232,220,200,0.10)` | 默认描边（暖白 10%） |
| `--border-strong` | `rgba(232,220,200,0.18)` | 强调描边、分隔线 |

> 废弃 modern 主题的冷蓝黑 `#0b1120` 与玻璃拟态大阴影；玻璃感仅保留 `backdrop-blur` 于浮层。

### 2.2 品牌与语义色

| 令牌 | 值 | 用途 |
|---|---|---|
| `--accent`（和室金） | `#d4a853` | 主按钮、激活态、链接强调、立直/胜利 |
| `--accent-hover` | `#e2bc6d` | hover |
| `--accent-bg` | `rgba(212,168,83,0.14)` | 激活背景（导航、tab、chip） |
| `--accent-border` | `rgba(212,168,83,0.40)` | 激活描边 |
| `--positive`（青） | `#5ec8e8` | 正分、Q 值优势、和了 |
| `--negative`（红） | `#f07070` | 负分、恶手、放铳 |
| `--warning` | `#e8a94a` | 警告（与金色区分：仅用于告警文案/icon） |
| `--info` | `#7fa8d9` | 中性信息、次级链接 |
| `--success` | `#4fc38a` | 操作成功（toast、状态点） |

### 2.3 文字

| 令牌 | 值 |
|---|---|
| `--text-primary` | `#f0ead9`（暖纸白） |
| `--text-secondary` | `rgba(240,234,217,0.62)` |
| `--text-muted` | `rgba(240,234,217,0.38)` |
| `--text-faint` | `rgba(240,234,217,0.22)` |

### 2.4 座位色（功能色，保持不变）

东 `--seat-0: #f87171` / 南 `--seat-1: #7fb3f0` / 西 `--seat-2: #b49ae8` / 北 `--seat-3: #5fce9a`
（在暖黑底上把现有四色微调至同明度，避免蓝/紫过跳。）

---

## 3. 牌桌（Review Table）专项规范

牌桌是全站视觉主角，沿用天凤式纯平布局，但材质升级为「深绒桌布 + 黑金面板」。

### 3.1 桌布

| 令牌 | 值 | 说明 |
|---|---|---|
| `--table-bg` | `#1e3527`（深绒绿，默认） | 取代米白/深蓝平面 |
| `--table-texture-opacity` | `0.06` | 开启极淡呢绒纹理（当前为 0） |
| `--table-vignette` | 径向渐变 → `rgba(0,0,0,0.35)` | 四边压暗，聚焦中央 |
| `--table-radius` | `12px` | 桌布与外壳之间留出 8px 暖黑边，形成"装裱"感 |

桌布可选项（收敛现有 5 项为 3 项，全部深色系）：
- `felt` 深绒绿 `#1e3527`（默认）
- `ink` 墨蓝 `#16222e`
- `charcoal` 炭灰 `#1d1b18`

### 3.2 中央记分板

- 底板：`--center-bg: rgba(12,10,7,0.55)` + `backdrop-blur(6px)`，1px `--border` 描边，圆角 8px
- 场风/局数文字：`--text-primary`，金色仅用于"东/南"场风字与当前局高亮
- 分数：等宽数字（`font-variant-numeric: tabular-nums`），变动时 0.18s 闪动（青涨红跌）
- 立直棒/场供：金色小棒样式保留，描边改 `--accent-border`

### 3.3 牌（Tile）

- 白面牌保留现有 3D 结构（`--tile-bg: #f0ebe0` + 多层阶梯阴影），在深绒底上对比度天然成立
- 牌背默认色从深蓝 `#0f1e3c` 微调为 **墨蓝黑 `#101d30`**，与桌布协调；仍支持 JS 覆盖 RGB
- 选中态：`--tile-selected-ring` 金色描边 + 上抬 4px（现有行为保留）
- 可打提示（Q/P 高亮）：牌顶 mini bar 用青→红渐变映射 Q 值差，替代目前的单色条

### 3.4 外围控件（ctrlbar / overlay / 结算层）

统一语言：**深色玻璃 + 金描边**
- 底：`rgba(16,13,9,0.82)` + `backdrop-blur(8px)`
- 描边：`1px rgba(232,220,200,0.10)`；激活项描边 `--accent-border` + 底 `--accent-bg`
- 结算层面板保留黑幕风，但正/负分列固定用青/红，标题金色
- 所有浮层 z-index 走 `tokens.ts` 的 `z` 刻度，禁止裸写

### 3.5 分析可视化（Q/P、Logit Bar）

- Q 值条形：青（优）→ 红（劣）连续映射，背景轨 `rgba(240,234,217,0.08)`
- 选中打牌与 AI 推荐一致：牌下加 2px 金色下划线；分歧：红色下划线
- BotDecisionCard：面板 `--surface-2`，推荐动作 icon 金色，其余 `--text-secondary`

---

## 4. 字体与排版

| 角色 | 规格 |
|---|---|
| 字体族 | 现有系统栈不变；数字场景追加 `font-variant-numeric: tabular-nums` |
| 页面标题 | 20px / 800 |
| 区块标题 | 13px / 700 |
| 正文 | 13px / 400（从 12px 上调，深色底需要更大字号保证可读性） |
| 辅助文字 | 12px / `--text-secondary` |
| Eyebrow/标签 | 11px / 700 / 大写 / `--text-muted` |
| 分数/Q值 | 等宽数字，基准 14px，记分板 16px |

---

## 5. 间距 / 圆角 / 阴影 / 动效

- **间距**：沿用 4pt 网格（`tokens.ts` 的 `space` 不变）
- **圆角**：收敛为三档 —— `sm:6`（按钮、chip）/ `md:10`（卡片、面板）/ `lg:16`（浮层、结算面板）；废弃 `xl:20`
- **阴影**：深色主题下阴影只做"层级"不做"发光"：
  - `elev-1: 0 2px 8px rgba(0,0,0,0.35)`（卡片）
  - `elev-2: 0 8px 24px rgba(0,0,0,0.45)`（抽屉、下拉）
  - `elev-3: 0 16px 48px rgba(0,0,0,0.55)`（模态、结算层）
- **动效**：fast 0.12s / default 0.18s / slow 0.3s，统一 `ease-out`；浮层入场用 framer-motion（opacity + y:4→0）



---

## 6. 组件规范（核心）

### 6.1 按钮
- Primary：底 `--accent`，文字 `#1a150c`（深底金字反转为金底深字，保证对比度），hover `--accent-hover`
- Secondary：透明底 + `--border` 描边 + `--text-primary`；hover 底 `--surface-3`
- Ghost：无描边，`--text-secondary`，hover `--text-primary`
- Danger：`--negative` 描边/文字，实心仅用于确认 destructive 操作
- 高度统一 32px（sm 26 / lg 38），圆角 6px

### 6.2 卡片 / 面板
- 底 `--surface-1`，描边 `--border`，圆角 10px，阴影 `elev-1`
- 卡片标题 13px/700 `--text-primary`；右上角操作区 12px `--text-muted`
- 禁用玻璃拟态大阴影与 inset 高光（modern 主题遗留全部清除）

### 6.3 导航（Sidebar）
- 底：`--page-bg` 同色系加深 `#100e0a`，右侧 1px `--border`
- 分组标签：11px 大写 `--text-muted`
- 激活项：左侧 2px 金色指示条 + 底 `--accent-bg` + 文字 `--accent`
- 图标：lucide 16px，stroke 1.75

### 6.4 表格 / 列表
- 行高 40px，斑马纹 `rgba(240,234,217,0.02)`，hover `--surface-3`
- 表头 11px 大写 `--text-muted`，数字列右对齐 + tabular-nums
- 正/负数值强制青/红

### 6.5 表单
- 输入框：底 `rgba(240,234,217,0.04)`，描边 `--border`，focus 描边 `--accent` + 2px 金色外发光 `rgba(212,168,83,0.25)`
- 标签 12px `--text-secondary`

---

## 7. 工程实施路线

### Phase 0 — 令牌收敛（无视觉突变）
1. 重写 `globals.css` 的 `:root` 为深色令牌（§2 全表），`[data-theme="white"]` 改为旧值别名映射（临时兼容，标记 deprecated）
2. `tokens.ts` 补齐 JS 镜像（surface/elev/accent-bg 等），与 CSS 变量一一对应
3. 删除 modern 主题专属覆写段（`globals.css` 末尾 ~80 行 `[data-theme="modern"] *`）

### Phase 1 — 外壳统一
4. `Sidebar` / `MainLayout` / `PageScaffold` / `ui/*`（Button/Card/Toolbar/Drawer）迁到新令牌
5. 清除组件内裸写 hex（全局搜索 `#[0-9a-fA-F]{3,6}` 逐一替换）

### Phase 2 — 牌桌令牌化（重点）
6. 新建 `components/BattleBoard/tableStyles.ts`：把 `MahjongTable.tsx` 的 98 处内联样式收敛为语义化样式对象 + CSS 变量引用
7. 桌布切换 `tableclothOptions.ts` 收敛为 3 项深色系（§3.1），开启纹理与暗角
8. 中央记分板 / ctrlbar / 结算层按 §3.2–3.4 改造

### Phase 3 — 页面逐个迁移
9. 顺序：Review Workspace（含 DecisionPanel）→ Dashboard → Ladder → Matches/Participants → 其余
10. 每页迁移后跑 `lint` + 对应 `check:*` 语义脚本回归

### 验收标准
- 任意页面截图中不出现非令牌色值
- `MahjongTable.tsx` 内联 `style={{}}` 中不再出现裸 hex / rgba 字面量
- 深色主题下 WCAG AA：正文对比度 ≥ 4.5，辅助文字 ≥ 3.0

---

## 8. 迁移映射速查（旧 → 新）

| 旧值 | 新令牌 |
|---|---|
| `#0b1120`（modern page-bg） | `--page-bg: #14110c` |
| `rgba(255,255,255,0.05)` 卡片 | `--surface-1` |
| `#3b82f6` 蓝 accent | `--accent: #d4a853` |
| `#22c55e` success | `--success: #4fc38a`（仅状态） |
| `#f4efe3` 牌桌文字 | `--text-primary: #f0ead9` |
| 米白桌布 `#e8e0d0` | `--table-bg: #1e3527` |
| `#5ec8e8` / `#f07070` | 保留，正式命名为 `--positive` / `--negative` |
