# 模型入口与消费边界

2026-09-19 最终决定：**U32 实战首选，M0 可选备选，70k 展示名统一为 K0**。
新建 Review 去除 V2/mortal 选项；历史权重、模型 ID、成绩和报告身份不改写。

## 选哪个名字

| 名称/spec | 实际含义 | 分数解释 |
| --- | --- | --- |
| `p4m11_u32`（U32） | 新对局 / Play 实战首选 | policy action score；Review 不显示 Q 差收益损失 |
| `m0_72k`（M0） | 可选备选；M0_control / seed 20260807 / 72k。**不是 V2@74000** | DQN calibrated Q；不是默认、不是正式天梯晋级 |
| `consensus_v1`（共识v1） | 外部蒸馏模型；distill_consensus_v3.pth | DQN calibrated Q；已开放赛季天梯/Play/Review |
| `nova_v1`（novav1） | 外部蒸馏模型；distill_nova.pth | DQN calibrated Q；已开放赛季天梯/Play/Review |
| `luckyj_v1`（luckyjv1） | 外部模型；luckyj_clone_v1.pth | DQN calibrated Q；已开放赛季天梯/Play/Review |
| `unknown_v1`（未知v1） | 外部模型；unknown1.pth (PolicyNet 2层MLP) | policy action score；已开放赛季天梯/Play/Review |
| `nova_v2`（novav2） | 外部蒸馏模型；distill_nova_v2.pth | DQN calibrated Q；已开放赛季天梯/Play/Review |
| `70k`（K0） | 基准参考；内部 ID、路径和历史关联保留 | DQN 动作价值估计 |
| `ext_mortal` | 本地 2024-03-08 external 参考权重，不是网站 4.1a/b/c | DQN 动作价值估计 |
| `mortal` | 历史 V2 别名，缺失时仍保留旧 K0 回退；不重指 U32 | 不再提供新建 Review 选项 |
| `weak` / `weak_mortal` | 历史兼容别名，实际指向 external | 不能从 weak 名称推断强度 |

### 默认选择与历史边界

- Play 首槽、Battle / Bot Battle、新建 Tenhou / riichi.dev CLI 对局默认显式选择 `p4m11_u32`。
  U32 未出现在运行目录时不静默换成 K0；用户需明确选择。
- Review 教师独立：默认 **External Mortal + K0**；U32、M0 可手动加入动作对照。
  新建 Review 的 UI / multi-teacher API / CLI 接受 External、K0、U32、M0，不接受 V2；历史 V2/M0 Review 仍可打开，
  底层严格 checkpoint 解析、旧 HTTP/Python API 和显式路径兼容保留；Review CLI 默认 External。
- riichi.dev 的显式 `mortal` 是该旧客户端独有的 K0 兼容入口，仍保留原行为；
  新默认用具名 U32，不靠改变旧别名实现。赛季 worker 始终消费冻结路径，不跟随默认值。
- `70k` → **K0** 是展示改名，不迁移 model/account/artifact ID，不重写旧报告、赛季或比赛账本。
- 本批复评已收口。选择 U32 是实用决定，不是已证明 U32/M0/V2 实力分档。
  M0 保留为可选对照，不作为默认或正式天梯晋级；不据此追加训练或对局。
- Python 当前入口口径在 `workbench/model_catalog.py`；前端在 `botCatalog.ts`，
  `modelDisplay.ts` 只处理展示，绝不参与权重解析或教师匹配。

## 实际发布位置

以下相对共享数据根（本机 `E:/AUbuntuProject/keqing-data`）：

- K0：`mortal/authoritative/D3_top2_discard_v1_2026_08/models/K0_70k/mortal_default_70k_promoted_candidate.pth`
- external：同一发布包下 `models/ext_mortal/external_mortal_20240308_best_min.pth`
- V2：同一发布包下 `models/V2_74000/mortal_74000.pth`
- U32：`mortal/authoritative/P4M11_U32_2026_09/models/P4M11_U32/U32_eval_weights.pth`
- M0：`mortal/authoritative/M0_72k_s20260807/models/M0_72k/mortal_72000.pth`（历史、可选）
- 共识v1：`mortal/authoritative/external/distill_consensus_v3.pth`
- novav1：`mortal/authoritative/external/distill_nova.pth`
- luckyjv1：`mortal/authoritative/external/luckyj_clone_v1.pth`
- 未知v1：`mortal/authoritative/external/unknown1.pth`
- novav2：`mortal/authoritative/external/distill_nova_v2.pth`

U32 本次文件 SHA-256 核对为 `3703c943a64a00ca128f4a5f5f989d446dc5814c7e70dc50527d2756039a8add`，
与既有发布身份一致。没有重新导出、覆盖或加载 GPU。
M0 是独立于 V2@74000 的历史 checkpoint；二者不能互称或互换。其 SHA-256 为
`de7f6da7c0c07b89d658554050f2112f09fd9c021247104d5db44228db04823d`，与 Gate-E 记录一致。

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
