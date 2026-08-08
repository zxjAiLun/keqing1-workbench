from __future__ import annotations

from typing import Optional

from .mapper import replay_tile_danger_context_from_entry, resolve_replay_tile_danger


def _danger_reason_label(hit) -> str:
    if hit.table_slug == "各巡目的数牌危险度":
        return f"巡目危险度: {hit.column}"
    if hit.table_slug == "各巡目的字牌危险度":
        return f"字牌危险度: {hit.column}"
    if hit.table_slug == "one_chance的危险度":
        return f"One Chance: {hit.row_key}"
    return f"{hit.table_title}: {hit.row_key} / {hit.column}"


def lookup_replay_dahai_danger_demo(pai: str, entry: Optional[dict] = None):
    if entry is None:
        return resolve_replay_tile_danger(
            replay_tile_danger_context_from_entry({}, pai, actor=0)
        )
    return resolve_replay_tile_danger(replay_tile_danger_context_from_entry(entry, pai))


def annotate_replay_candidate_demo(candidate: dict, entry: Optional[dict] = None) -> dict:
    action = candidate.get("action") or {}
    if action.get("type") != "dahai":
        return candidate
    pai = action.get("pai")
    if not isinstance(pai, str):
        return candidate
    try:
        hit = lookup_replay_dahai_danger_demo(pai, entry)
    except FileNotFoundError:
        return candidate
    if hit is None:
        return candidate
    annotated = dict(candidate)
    danger_prior = {
        "kind": "danger",
        "table_slug": hit.table_slug,
        "table_title": hit.table_title,
        "row_key": hit.row_key,
        "column": hit.column,
        "value": hit.value,
        "unit": hit.unit,
        "source_label": f"{hit.table_title} / {hit.row_key} / {hit.column}",
        "reason": _danger_reason_label(hit),
    }
    annotated["danger_prior"] = danger_prior
    annotated["danger_prior_source"] = danger_prior["source_label"]
    return annotated
