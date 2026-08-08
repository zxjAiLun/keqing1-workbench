from __future__ import annotations

from typing import Optional

import keqing_core

from inference.default_context import DefaultDecisionContextBuilder
from inference.review import DefaultRuntimeReviewExporter, same_action
from inference.contracts import DecisionResult, ModelAuxOutputs, ScoredCandidate
from mahjong_env.legal_actions import enumerate_legal_actions
from mahjong_env.state import GameState


def inject_shanten_waits(snap: dict, *, hand_list: list, melds_list: list, model_version: str) -> None:
    from mahjong_env.replay import _calc_shanten_waits

    shanten, waits_cnt, waits_tiles, _ = _calc_shanten_waits(hand_list, melds_list)
    snap["shanten"] = shanten
    snap["waits_count"] = waits_cnt
    snap["waits_tiles"] = waits_tiles


class RulebaseBot:
    """Rust rulebase bot wrapper.

    决策逻辑完全下沉到 `keqing_core`，Python 这里只负责上下文构建与 review 日志。
    """

    def __init__(self, player_id: int, verbose: bool = False):
        self.player_id = player_id
        self.verbose = verbose
        self.decision_log: list[dict] = []
        self.game_state = GameState()
        self._context_builder = DefaultDecisionContextBuilder(
            model_version="rulebase",
            riichi_state=None,
            inject_shanten_waits=inject_shanten_waits,
            enumerate_legal_actions_fn=lambda snap, seat: enumerate_legal_actions(
                snap, seat
            ),
        )
        self._review_exporter = DefaultRuntimeReviewExporter()
        self.model = None

    def reset(self) -> None:
        self.decision_log.clear()
        self.game_state = GameState()

    def set_player_id(self, player_id: int) -> None:
        player_id = int(player_id)
        if self.player_id == player_id:
            return
        self.player_id = player_id
        self.reset()

    def react(self, event: dict, gt_action: Optional[dict] = None) -> Optional[dict]:
        actor = self.player_id
        ctx = self._context_builder.build(self.game_state, actor, event)
        if ctx is None:
            return None

        legal_actions = ctx.legal_actions
        if not legal_actions:
            return None

        try:
            chosen = keqing_core.choose_rulebase_action(
                ctx.runtime_snap,
                actor,
                legal_actions,
            )
        except RuntimeError as exc:
            if not keqing_core.is_missing_rust_capability_error(exc):
                raise
            chosen = _choose_rulebase_fallback(ctx.runtime_snap, actor, legal_actions)
        if chosen is None:
            chosen = legal_actions[0]

        decision = DecisionResult(
            chosen=chosen,
            candidates=self._score_candidates(legal_actions, chosen),
            model_value=0.0,
            model_aux=ModelAuxOutputs(),
        )
        entry = self._review_exporter.build_decision_entry(
            step=len(self.decision_log),
            ctx=ctx,
            decision=decision,
            gt_action=gt_action,
            actor=actor,
        )
        self.decision_log.append(entry)
        return chosen

    @staticmethod
    def _score_candidates(
        legal_actions: list[dict],
        chosen: dict,
    ) -> list[ScoredCandidate]:
        scores: list[ScoredCandidate] = []
        for action in legal_actions:
            if same_action(action, chosen):
                score = 1000.0
            elif action.get("type") == "none":
                score = -10.0
            else:
                score = 0.0
            scores.append(ScoredCandidate(action=action, logit=score, final_score=score))
        return scores


def _choose_rulebase_fallback(snap: dict, actor: int, legal_actions: list[dict]) -> dict | None:
    for action_type in ("hora", "reach", "ryukyoku"):
        for action in legal_actions:
            if action.get("type") == action_type:
                return action

    reached = snap.get("reached") or []
    if actor < len(reached) and bool(reached[actor]):
        last_tsumo = snap.get("last_tsumo") or []
        drawn = last_tsumo[actor] if actor < len(last_tsumo) else None
        for action in legal_actions:
            if action.get("type") == "dahai" and action.get("pai") == drawn and bool(action.get("tsumogiri")):
                return action

    if any(bool(value) for idx, value in enumerate(reached) if idx != actor):
        genbutsu = {
            discard.get("pai")
            for idx, discards in enumerate(snap.get("discards") or [])
            if idx != actor and idx < len(reached) and bool(reached[idx])
            for discard in discards
            if isinstance(discard, dict)
        }
        for action in legal_actions:
            if action.get("type") == "dahai" and action.get("pai") in genbutsu:
                return action

    for action in legal_actions:
        if action.get("type") == "none":
            return action
    for action in legal_actions:
        if action.get("type") == "dahai":
            return action
    return legal_actions[0] if legal_actions else None
