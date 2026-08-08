from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from mahjong_env.replay import read_mjai_jsonl


def validate_mjai_jsonl(path: str | Path) -> List[str]:
    events = read_mjai_jsonl(path)
    errors: List[str] = []
    if not events:
        return ["empty mjai log"]
    if events[0].get("type") != "start_game":
        errors.append("first event must be start_game")
    has_start_kyoku = any(e.get("type") == "start_kyoku" for e in events)
    if not has_start_kyoku:
        errors.append("missing start_kyoku")
    has_progress = any(e.get("type") in {"tsumo", "dahai"} for e in events)
    if not has_progress:
        errors.append("missing tsumo/dahai events")
    terminal = any(e.get("type") in {"hora", "ryukyoku"} for e in events)
    if not terminal:
        errors.append("missing hora/ryukyoku terminal event")
    return errors


def summarize_mjai(path: str | Path) -> Dict[str, int]:
    events = read_mjai_jsonl(path)
    counts: Dict[str, int] = {}
    for e in events:
        t = e["type"]
        counts[t] = counts.get(t, 0) + 1
    return counts

