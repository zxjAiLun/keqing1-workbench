from __future__ import annotations

import logging

from inference.review import same_action
from mahjong_env.tiles import normalize_tile

_logger = logging.getLogger(__name__)

_RESPONSE_ACTION_TYPES = {"chi", "pon", "daiminkan", "ankan", "kakan", "hora"}
_RESPONSE_PRIORITY = {
    "none": 0,
    "chi": 1,
    "pon": 2,
    "daiminkan": 2,
    "ankan": 2,
    "kakan": 2,
    "hora": 3,
}


def _same_response_source(left: dict, right: dict) -> bool:
    """Whether two response actions refer to the same discard source."""
    if left.get("target") is not None and right.get("target") is not None:
        if int(left["target"]) != int(right["target"]):
            return False
    left_pai = left.get("pai")
    right_pai = right.get("pai")
    if left_pai is None or right_pai is None:
        return True
    return normalize_tile(str(left_pai)) == normalize_tile(str(right_pai))


def _higher_priority_response_intercepted(
    pending_action: dict,
    current_action: dict,
    player_id: int | None,
) -> bool:
    """Detect a response window closed by another player's higher-priority call."""
    if not pending_action or not current_action:
        return False
    if current_action.get("actor") == player_id:
        return False
    pending_type = str(pending_action.get("type", ""))
    current_type = str(current_action.get("type", ""))
    if pending_type not in {"chi", "pon"}:
        return False
    if current_type not in {"pon", "daiminkan", "hora"}:
        return False
    if _RESPONSE_PRIORITY.get(current_type, 0) <= _RESPONSE_PRIORITY.get(pending_type, 0):
        return False
    return _same_response_source(pending_action, current_action)


def _unique_kakan_target_meld(
    melds, actor: int, added_tile: str
) -> tuple[dict | None, str | None]:
    """定位要被升级为 kakan 的原 pon（或已修复的 kakan）。

    返回 (meld, issue)：唯一匹配时 meld 非空、issue 为 None；
    找不到或多匹配时 meld 为 None，issue 说明原因，绝不猜测。
    """
    if not isinstance(melds, list) or not (0 <= actor < len(melds)):
        return None, "missing_actor_melds"
    matches = [
        meld
        for meld in (melds[actor] or [])
        if isinstance(meld, dict)
        and meld.get("type") in {"pon", "kakan"}
        and normalize_tile(str(meld.get("pai", ""))) == normalize_tile(added_tile)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "multiple_melds"
    return None, "no_matching_meld"


def _repair_kakan_snapshots(decisions: dict, events: list[dict] | None) -> None:
    """Repair cached snapshots written by a native runtime without kakan_accepted."""
    if not events:
        return

    accepted: list[tuple[tuple[str, int, int], int, str]] = []
    current_kyoku: tuple[str, int, int] | None = None
    for event in events:
        if event.get("type") == "start_kyoku":
            current_kyoku = (
                str(event.get("bakaze", "")),
                int(event.get("kyoku", 0)),
                int(event.get("honba", 0)),
            )
        elif event.get("type") == "kakan_accepted" and current_kyoku is not None:
            accepted.append((current_kyoku, int(event.get("actor", -1)), str(event.get("pai", ""))))

    consumed_accepts: set[int] = set()
    active: tuple[tuple[str, int, int], str, int] | None = None
    for entry in decisions.get("log", []):
        key_data = entry.get("kyoku_key") or {}
        entry_key = (
            str(key_data.get("bakaze", entry.get("bakaze", ""))),
            int(key_data.get("kyoku", entry.get("kyoku", 0))),
            int(key_data.get("honba", entry.get("honba", 0))),
        )
        # 小局边界：active 不跨局继承（N8）
        if active is not None and active[0] != entry_key:
            active = None

        if active is not None:
            _, added_tile, actor = active
            melds = entry.get("melds")
            target, issue = _unique_kakan_target_meld(melds, actor, added_tile)
            if target is None:
                _logger.warning(
                    "[kakan repair] step=%s actor=%s added=%s: %s，不做手牌/副露修改",
                    entry.get("step"), actor, added_tile, issue,
                )
            elif target.get("type") == "kakan":
                # 已修复的快照：幂等 no-op（N4/N5）
                pass
            else:
                # 先验证，后修改（P1/P2）：canonical 完整校验通过才原子提交，
                # 失败时 hand/melds/candidates 全部保持不变。
                base_consumed = list(target.get("consumed") or [])
                called_tile = str(target.get("pai_raw") or target.get("pai") or added_tile)
                canonical_consumed = [*base_consumed, called_tile, added_tile]
                family = normalize_tile(added_tile)
                canonical_valid = (
                    len(base_consumed) == 2
                    and len(canonical_consumed) == 4
                    and all(normalize_tile(str(tile)) == family for tile in canonical_consumed)
                )
                if not canonical_valid:
                    _logger.warning(
                        "[kakan repair] step=%s actor=%s added=%s: canonical 校验失败 "
                        "(base=%s called=%s) → 完整 no-op",
                        entry.get("step"), actor, added_tile, base_consumed, called_tile,
                    )
                else:
                    # 原子执行：删 hand 残留加杠牌（存在才删）、pon 升级、
                    # 写 canonical consumed / pai / pai_raw、过滤失效候选
                    hand = list(entry.get("hand") or [])
                    for index, tile in enumerate(hand):
                        if normalize_tile(str(tile)) == normalize_tile(added_tile):
                            hand.pop(index)
                            break
                    entry["hand"] = hand

                    target["type"] = "kakan"
                    target["consumed"] = canonical_consumed
                    target["pai"] = added_tile
                    target["pai_raw"] = added_tile

                    entry["candidates"] = [
                        candidate
                        for candidate in (entry.get("candidates") or [])
                        if not (
                            isinstance(candidate.get("action"), dict)
                            and candidate["action"].get("type") in {"dahai", "kakan"}
                            and candidate["action"].get("pai") is not None
                            and normalize_tile(str(candidate["action"]["pai"])) == normalize_tile(added_tile)
                            and not any(normalize_tile(str(tile)) == normalize_tile(added_tile) for tile in hand)
                        )
                    ]

        gt_action = entry.get("gt_action") or {}
        if gt_action.get("type") != "kakan":
            continue
        for index, (accepted_key, accepted_actor, accepted_tile) in enumerate(accepted):
            if index in consumed_accepts:
                continue
            if (
                accepted_key == entry_key
                and accepted_actor == int(gt_action.get("actor", -1))
                and normalize_tile(accepted_tile) == normalize_tile(str(gt_action.get("pai", "")))
            ):
                consumed_accepts.add(index)
                active = (entry_key, accepted_tile, accepted_actor)
                break


def normalize_replay_decisions(
    decisions: dict,
    meta: dict | None = None,
    events: list[dict] | None = None,
) -> dict:
    if not isinstance(decisions, dict):
        return decisions

    _repair_kakan_snapshots(decisions, events)
    log = decisions.get("log", [])
    player_id = decisions.get("player_id")
    pending_idx: int | None = None
    for idx, entry in enumerate(log):
        if not entry.get("is_obs") and entry.get("gt_action") is None:
            pending_idx = idx
        if pending_idx is None or idx == pending_idx:
            continue
        pending = log[pending_idx]
        if pending.get("gt_action") is not None:
            pending_idx = None
            continue
        chosen = pending.get("chosen") or {}
        candidates = pending.get("candidates", [])
        current_action = entry.get("gt_action") or entry.get("chosen") or {}
        has_none_candidate = any(
            c.get("action", {}).get("type") == "none" for c in candidates
        )
        has_non_none_candidate = any(
            c.get("action", {}).get("type") != "none" for c in candidates
        )
        if chosen.get("type") == "none" and has_non_none_candidate:
            pending["gt_action"] = {"type": "none", "actor": player_id}
            pending_idx = None
        elif chosen.get("type") in _RESPONSE_ACTION_TYPES and has_none_candidate:
            if _higher_priority_response_intercepted(chosen, current_action, player_id):
                # The player had a real response opportunity, but the same
                # discard was consumed by a higher-priority call first.
                pending["comparison_exempt"] = "response_preempted"
                pending["comparison_exempt_by"] = dict(current_action)
                pending["gt_action"] = {"type": "none", "actor": player_id}
            elif same_action(current_action, chosen):
                pending["gt_action"] = {
                    **current_action,
                    "actor": current_action.get("actor", chosen.get("actor", player_id)),
                }
            else:
                pending["gt_action"] = {"type": "none", "actor": player_id}
            pending_idx = None

    # Cached decisions may already contain an explicit none gt_action from an
    # older normalizer. Re-check those response windows for historical replays.
    for idx, pending in enumerate(log):
        if pending.get("is_obs") or pending.get("comparison_exempt"):
            continue
        chosen = pending.get("chosen") or {}
        if chosen.get("type") not in {"chi", "pon"}:
            continue
        if not any(
            (candidate.get("action") or {}).get("type") == "none"
            for candidate in pending.get("candidates", [])
        ):
            continue
        if idx + 1 >= len(log):
            continue
        current_action = log[idx + 1].get("gt_action") or log[idx + 1].get("chosen") or {}
        if _higher_priority_response_intercepted(chosen, current_action, player_id):
            pending["comparison_exempt"] = "response_preempted"
            pending["comparison_exempt_by"] = dict(current_action)
            pending["gt_action"] = {"type": "none", "actor": player_id}

    own_log = [e for e in log if not e.get("is_obs")]
    comparable_log = [entry for entry in own_log if not entry.get("comparison_exempt")]
    total_ops = len(comparable_log)
    match_count = sum(
        1 for e in comparable_log if same_action(e.get("chosen"), e.get("gt_action"))
    )
    return {
        **decisions,
        "log": log,
        "total_ops": total_ops,
        "match_count": match_count,
        "bot_type": (meta or {}).get("bot_type", decisions.get("bot_type")),
        "player_names": decisions.get("player_names") or (meta or {}).get("player_names"),
        "external_review_links": decisions.get("external_review_links") or (meta or {}).get("external_review_links") or {},
    }


__all__ = ["normalize_replay_decisions"]
