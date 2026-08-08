from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any

from inference.runtime_bot import RuntimeBot


def load_event_rows(path: str | Path) -> list[dict[str, Any]]:
    event_path = Path(path)
    opener = gzip.open if event_path.suffix == ".gz" else open
    with opener(event_path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def resolve_model_path(
    *,
    bot_name: str,
    model_path: str | Path | None,
    project_root: str | Path,
) -> Path:
    if model_path is not None:
        return Path(model_path)
    return Path(project_root) / "artifacts" / "models" / bot_name / "best.pth"


def inspect_event_decision(
    *,
    log_path: str | Path,
    event_idx: int,
    bot_name: str,
    model_path: str | Path | None,
    project_root: str | Path,
    device: str,
    model_version: str | None = None,
    rank_pt_lambda: float = 0.0,
    context_before: int = 8,
    context_after: int = 8,
) -> dict[str, Any]:
    rows = load_event_rows(log_path)
    if event_idx < 0 or event_idx >= len(rows):
        raise IndexError(f"event_idx out of range: {event_idx} (rows={len(rows)})")

    target_event = rows[event_idx]
    actor = target_event.get("actor")
    if actor is None:
        raise ValueError(f"target event has no actor: idx={event_idx} event={target_event}")

    checkpoint = resolve_model_path(
        bot_name=bot_name,
        model_path=model_path,
        project_root=project_root,
    )
    bot = RuntimeBot(
        player_id=int(actor),
        model_path=checkpoint,
        device=device,
        rank_pt_lambda=rank_pt_lambda,
        model_version=model_version or bot_name,
    )

    for event in rows[:event_idx]:
        bot.react(event)

    predicted_action = bot.react(target_event)
    actual_followup = rows[event_idx + 1] if event_idx + 1 < len(rows) else None
    context_start = max(0, event_idx - context_before)
    context_end = min(len(rows), event_idx + context_after + 1)
    context = [
        {"idx": idx, "event": rows[idx]}
        for idx in range(context_start, context_end)
    ]

    return {
        "log_path": str(log_path),
        "event_idx": event_idx,
        "bot_name": bot_name,
        "model_version": model_version or bot_name,
        "model_path": str(checkpoint),
        "device": device,
        "rank_pt_lambda": rank_pt_lambda,
        "actor": actor,
        "target_event": target_event,
        "predicted_action": predicted_action,
        "actual_followup_event": actual_followup,
        "context": context,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay an mjai jsonl/jsonl.gz to a target event and inspect the local model decision there."
    )
    parser.add_argument("--log-path", required=True, help="Path to mjai jsonl or jsonl.gz")
    parser.add_argument("--event-idx", type=int, required=True, help="Target event index to inspect")
    parser.add_argument("--bot-name", default="xmodel1", help="Bot/model family name")
    parser.add_argument("--model-version", default="", help="Optional model-version override")
    parser.add_argument("--model-path", default="", help="Explicit checkpoint path")
    parser.add_argument("--project-root", default=".", help="Project root for default checkpoint resolution")
    parser.add_argument("--device", default="cpu", help="Inference device")
    parser.add_argument("--rank-pt-lambda", type=float, default=0.0, help="Runtime placement rerank scale")
    parser.add_argument("--context-before", type=int, default=8, help="How many prior events to print")
    parser.add_argument("--context-after", type=int, default=8, help="How many later events to print")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    result = inspect_event_decision(
        log_path=args.log_path,
        event_idx=args.event_idx,
        bot_name=args.bot_name,
        model_path=args.model_path or None,
        project_root=args.project_root,
        device=args.device,
        model_version=args.model_version or None,
        rank_pt_lambda=args.rank_pt_lambda,
        context_before=args.context_before,
        context_after=args.context_after,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
