# Workbench

This directory owns the control-plane application:

- `gateway/`, `participants/`, and `replay/` — backend/API behavior;
- `replay_ui/` — React interface;
- `convert/`, `tools/`, `scripts/`, and `configs/` — Workbench operations.

Ladder/report publication is Workbench-owned: `replay/publish_ladder_snapshot.py`
and `replay/build_platform_account_report.py` build Ladder snapshots and
platform account reports from native logs. Training only writes eval results;
Workbench reads them.

Run it with `uv run python workbench/main.py local --port 8000`.
Runtime state is rooted at `KEQING_DATA_ROOT`, which defaults to the sibling
repository directory `data/`.

## Running from this directory

Entry scripts insert `workbench/`, `src/`, and the repository root on
`sys.path` themselves, so you can launch from this directory without
returning to the repository root:

```bash
# from workbench/
uv run python main.py local --port 8000        # ReplayUI + Battle API
uv run python main.py tenhou                   # Tenhou Gateway only
uv run python launch_tenhou_bots.py --room ... # Participants
uv run python scripts/participants/seed_participants.py ...
```

`src/` packages (`keqing_core`, `mahjong_env`, `inference`, ...) resolve
through the editable install created by `uv sync`; keep using `uv run` (or
the project venv) rather than a bare system python.
