# Keqing1 Workbench (keqing-workbench)

Control-plane application for the Mortal-based Riichi Mahjong stack: Tenhou
Gateway, replay/review, participants/ladder operations, and the React UI.

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
cd workbench
python main.py local --port 8000        # ReplayUI + Battle API
python main.py tenhou                   # Gateway only
python launch_tenhou_bots.py --room ... # Participants
python scripts/participants/seed_participants.py ...
```

`scripts/setup-dev.ps1`:

1. creates the venv, installs Python dependencies, and installs this repo
   editable;
2. builds the `libriichi` runtime from the vendored Mortal crate (cargo);
3. installs the `keqing_core` wheel (explicit `-KeqingCoreWheel` first, then
   an already-installed copy, then `KEQING_DATA_ROOT/runtime/keqing_core`);
4. builds the Replay UI (`npm`).

Runtime state is rooted at `KEQING_DATA_ROOT`, which defaults to the shared
`keqing-data` directory beside the project folder when it exists (otherwise
the repository-local `data/`).
