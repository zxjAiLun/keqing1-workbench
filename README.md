# Keqing1 Workbench (keqing-workbench)

Control-plane application for the Mortal-based Riichi Mahjong stack: Tenhou
Gateway, replay/review, participants/ladder operations, and the React UI.

## 模型与资料入口

- [模型选择、别名与研究/产品状态](workbench/MODELS.md)：**U32 实战首选、M0 可选备选、K0 基准参考**（内部 ID 保留 `70k`）。`mortal` 保留历史 V2 身份，不改指 U32。
- 强度、打法和后续训练看相邻 Experiment 的 [研发总览](../keqing1_experiment/training/docs/mortal/研发总览_当前.md)，Workbench 不另维护研究裁决。
- 共享资产在 `E:/AUbuntuProject/keqing-data`：发布权重位于 `mortal/authoritative/`，网站教师原始报告位于 `teacher-reports/`；本地 Review 输出仍在本仓库 `artifacts/replay_model_reviews/`，二者不能混作蒸馏来源。
- 新对局默认 U32；Review 默认教师独立为 External Mortal + K0，U32/M0 可加入对照，V2 不再提供新建 Review 选项。历史记录与权重不改写；产品首选不等于已证明实力分档。

下方 setup 是准备新环境的流程，不是每次使用前必跑。已有 Windows CUDA 环境不因文档整理而重装。

This repository was split from `keqing1` at commit `b714e5c` (initial split).
The training repository is `keqing1_experiment`; runtime data shared between
them lives in `KEQING_DATA_ROOT` (defaults to the shared `keqing-data`
directory beside the project folder).

## Layout

- `workbench/` — `gateway/`, `participants/`, `replay/` (backend/API),
  `replay_ui/` (React interface), `convert/`, `tools/`, `scripts/`, `configs/`.
  Ladder/report publication is Workbench-owned: `replay/publish_ladder_snapshot.py`
  and `replay/build_platform_account_report.py` build Ladder snapshots and
  platform account reports from native logs.
- `src/inference/`, `src/static_tables/`, `src/project_data.py`,
  `src/main.py` — control-plane Python surface.
- `src/mahjong_env/` — Mahjong semantics shared with the Mortal repo. The
  initial split intentionally duplicates `mahjong_env`; no shared package is
  introduced until independent evolution demonstrates a real maintenance cost.
- `third_party/Mortal/` — upstream Mortal runtime for bot features
  (git-ignored; `target/` build output excluded).
- `third_party/libriichi/` — Python shims for the compiled `riichi`
  extension; the extension itself is built from the vendored Mortal crate.
- `tests/` — Workbench test suite (plus the duplicated `mahjong_env` tests).

`keqing_core` is installed as a runtime wheel, not vendored source. The wheel
is published by the `keqing1_experiment` setup into
`KEQING_DATA_ROOT/runtime/keqing_core`; this repo's setup resolves it from
there (or from an explicit `-KeqingCoreWheel` path).

## Quick start (Windows)

```powershell
.\scripts\setup-dev.ps1
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv run python workbench/main.py local --port 8000
uv run python workbench/main.py tenhou
uv run python workbench/launch_tenhou_bots.py --room ...
uv run python workbench/scripts/participants/seed_participants.py ...
```

`scripts/setup-dev.ps1`:

1. creates the uv-managed `.venv-win`, syncs `uv.lock`, and installs this repo;
2. builds the `libriichi` runtime from the vendored Mortal crate (cargo);
3. installs the `keqing_core` wheel (explicit `-KeqingCoreWheel` first, then
   an already-installed copy, then `KEQING_DATA_ROOT/runtime/keqing_core`);
4. builds the Replay UI (`npm`).

Runtime state is rooted at `KEQING_DATA_ROOT`, which defaults to the shared
`keqing-data` directory beside the project folder when it exists (otherwise
the repository-local `data/`).
