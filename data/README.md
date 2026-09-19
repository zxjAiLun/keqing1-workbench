# Local data fallback (not the current shared data root)

This directory is intentionally Git-ignored except for this guide.
Workbench uses `KEQING_DATA_ROOT` when set, otherwise the existing shared
`E:/AUbuntuProject/keqing-data` directory; only if that shared directory is
absent does it fall back to repository-local `data/`. Experiment training
continues to use its explicit `artifacts/` paths.

```text
keqing-data/ (shared root)
  mortal/authoritative/ published model bundles
  teacher-reports/ original external teacher reports
  runs/          shared run records
  ladder/        registry-adjacent snapshots and reports
    captures/    Play-with-you captures
  participants/  account/model/match ledger files
  replays/       uploaded and generated replay data
  logs/          local runtime logs
```

Existing `artifacts/` content is deliberately not copied or deleted by this
layout change. Participant, replay, ladder, and Play-with-you log state now
resolve below the shared root by default. Active experiment outputs stay
under Experiment `artifacts/`; local Review output stays under Workbench
`artifacts/replay_model_reviews/`. Do not create a second model/data tree just
to match an old README example.
