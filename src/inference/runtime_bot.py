"""Archived RuntimeBot stub.

The active runtime surface is MortalReviewBot plus RulebaseBot. RuntimeBot was
kept for stale imports only and fails closed when instantiated.
"""

from __future__ import annotations

from mahjong_env.legal_actions import enumerate_legal_actions


def inject_shanten_waits(snap: dict, *, hand_list: list, melds_list: list, model_version: str) -> None:
    from mahjong_env.replay import _calc_shanten_waits

    shanten, waits_cnt, waits_tiles, _ = _calc_shanten_waits(hand_list, melds_list)
    snap["shanten"] = shanten
    snap["waits_count"] = waits_cnt
    snap["waits_tiles"] = waits_tiles


class RuntimeBot:
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "RuntimeBot is archived. Use inference.mortal_bot.MortalReviewBot "
            "or inference.bot_registry.create_runtime_bot(bot_name='mortal')."
        )


__all__ = ["RuntimeBot", "enumerate_legal_actions", "inject_shanten_waits"]
