# Keqing Workbench

Control-plane application for the Mortal-based Riichi Mahjong stack: Tenhou
Gateway, replay/review, participants/ladder operations, and the React UI.

This repository was split from `keqing1` at commit `b714e5c` (initial split).
The training repository is `keqing-mortal`; runtime data shared between them
lives in `KEQING_DATA_ROOT` (defaults to the shared `keqing-data` directory
beside the project folder).

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
- `tests/` — Workbench test suite (plus the duplicated `mahjong_env` tests).

`keqing_core` is installed as the runtime wheel built by the Mortal repo; this
repository does not vendor its source.

## Quick start (Windows)

```powershell
.\scripts\setup-dev.ps1    # venv + deps + keqing_core wheel + libriichi runtime
cd workbench
python main.py local --port 8000        # ReplayUI + Battle API
python main.py tenhou                   # Gateway only
python launch_tenhou_bots.py --room ... # Participants
python scripts/participants/seed_participants.py ...
```

Runtime state is rooted at `KEQING_DATA_ROOT`, which defaults to the shared
`keqing-data` directory beside the project folder when it exists (otherwise
the repository-local `data/`).
