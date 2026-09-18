# 模型入口与消费边界

2026-09-18 已用当前 resolver 做路径解析（无模型推理），并核对 Play/Review 目录与共享 participants 登记。
这是当前快照，不改变任何别名、默认选择或权重。

## 选哪个名字

| 名称/spec | 实际含义 | 分数解释 |
| --- | --- | --- |
| `70k` | K0-70k，人类牌谱训练参照 | 原 DQN 动作价值估计；不是保证准确的真实收益 |
| `ext_mortal` | 本地 external 强对手 | 原 DQN 动作价值估计 |
| `p4m11_u32` | M11-U32，C4 后 32 轮 direct PG 端点 | policy action score；Review 不显示 Q 差收益损失 |
| `m0_72k` | M0：D1 自有种群对照（M0_control / seed 20260807） | 原 DQN 校准 Q；分数差可作为收益估计 |
| `mortal` | 历史兼容别名，优先 V2_74000，缺失时才回落 70k | 本机本次解析为 V2_74000，不能当成固定 70k |
| `weak` / `weak_mortal` | 历史兼容别名，实际指向 external | 不能从 weak 名称推断强度 |

要明确使用 K0 或 U32，请选择具名 `70k` / `p4m11_u32`。
CLI、页面默认值可能不同；本次未统一改动默认选择。

M0 是**备选**候选：Gate-E 现存复评 solo 对 K0 未越过 U32（M0 − U32 = -0.703 pt/局，
95% [-6.284, +4.966]）。已可在 Play/Review 选到，但不是默认。

## 实际发布位置

以下相对共享数据根（本机 `E:/AUbuntuProject/keqing-data`）：

- K0：`mortal/authoritative/D3_top2_discard_v1_2026_08/models/K0_70k/mortal_default_70k_promoted_candidate.pth`
- external：同一发布包下 `models/ext_mortal/external_mortal_20240308_best_min.pth`
- V2：同一发布包下 `models/V2_74000/mortal_74000.pth`
- U32：`mortal/authoritative/P4M11_U32_2026_09/models/P4M11_U32/U32_eval_weights.pth`

- M0：`mortal/authoritative/M0_72k_s20260807/models/M0_72k/mortal_72000.pth`

U32 本次文件 SHA-256 核对为 `3703c943a64a00ca128f4a5f5f989d446dc5814c7e70dc50527d2756039a8add`，
与既有发布身份一致。没有重新导出、覆盖或加载 GPU。
M0 SHA-256 核对为 `de7f6da7c0c07b89d658554050f2112f09fd9c021247104d5db44228db04823d`，
与 P4-M13 Gate-E 现存复评记录的 `m0_72k` challenger 一致（复制前后同值）。

⚠️ `mortal_72000.pth` 是**每步同名** basename，`model_pool_2026_07` 下有几十个同名文件
（M0/D1/D2/C/V/S0 各条线）。**只能**用 family 子路径 `M0_72k/mortal_72000.pth` 寻址，
不要用裸文件名 —— 将来若再复制一个同名文件进来，裸名解析会变成歧义并直接报错。

## 模型名字不等于文件名字

- **M11-U32**：已登记为 Play / Review / Ladder 可用，研究上通过 K0 实用门但未通过 external 门。
- **M12-U64**：M11-U32 再续 32 轮；研究目录中的文件仍叫 `U32.pth`，不是 M11 的权重。
- **M13-P32**：直接从 M11-U32 进行 PPO，不经过 M12-U64。本次没有发布成新可选模型。

同名文件必须结合运行目录和模型身份。共享 registry 的 artifact `stage=promoted` 是产品准入，
Experiment 的 K1 是研究 lineage；两者不是同一个“晋级”。
最新强度和打法以 [Experiment 研发总览](../../keqing1_experiment/training/docs/mortal/研发总览_当前.md) 为准。

## 不混淆两类 Review 数据

- `keqing-data/teacher-reports/`：网站原始教师报告，保留教师版本和来源，可作为后续研究输入。
- 本仓库 `artifacts/replay_model_reviews/`：本地分析/展示结果，不自动等于网站原始教师监督。
- 本地 external 权重不冒充网站 Mortal 4.1b/4.1c。报告文件多，不代表有同样多的独立牌谱或训练状态。

维护代码入口：`src/inference/bot_registry.py`（路径/别名）、
`workbench/gateway/api/playwithyou.py`（Play 可选模型）、
`workbench/replay/bot.py` 和 `server.py`（Review）、共享 `participants/models.json`（账户/天梯模型登记）。
