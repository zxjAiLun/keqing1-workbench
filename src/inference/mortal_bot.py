from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
import sys
from typing import Any, Optional

import torch

from inference.default_context import DefaultDecisionContextBuilder
from inference.review import DefaultRuntimeReviewExporter
from inference.contracts import DecisionResult, ModelAuxOutputs, ScoredCandidate
from mahjong_env.legal_actions import enumerate_legal_actions
from mahjong_env.state import GameState
from mahjong_env.tiles import normalize_tile

MORTAL_ACTION_SPACE = 46
MORTAL_DISCARD_ID_TO_TILE = (
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
    "5mr", "5pr", "5sr",
)
_MORTAL_UNSUPPORTED_ANNOUNCE_EVENTS = {"kakan_accepted"}


def sanitize_event_for_mortal(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("type", ""))
    if event_type in _MORTAL_UNSUPPORTED_ANNOUNCE_EVENTS:
        return None
    if event_type == "none" and "actor" in event:
        return None
    return dict(event)


def inject_shanten_waits(snap: dict, *, hand_list: list, melds_list: list, model_version: str) -> None:
    from mahjong_env.replay import _calc_shanten_waits

    shanten, waits_cnt, waits_tiles, _ = _calc_shanten_waits(hand_list, melds_list)
    snap["shanten"] = shanten
    snap["waits_count"] = waits_cnt
    snap["waits_tiles"] = waits_tiles


class MortalReviewBot:
    """Mortal checkpoint-backed bot for replay review.

    This wrapper keeps Mortal's native mjai action conversion authoritative, then
    adapts the returned q-values into the existing review decision log format.
    """

    def __init__(
        self,
        player_id: int,
        model_path: str | Path,
        *,
        mortal_root: str | Path = Path("third_party/Mortal"),
        device: str | torch.device = "cpu",
        verbose: bool = False,
        enable_amp: bool = False,
        enable_rule_based_agari_guard: bool = True,
        enable_review_log: bool = True,
        model_version: Optional[str] = None,
        shared_mortal_engine: Any | None = None,
        shared_model: Any | None = None,
    ) -> None:
        self.player_id = int(player_id)
        self.verbose = bool(verbose)
        self.device = torch.device(device if torch.cuda.is_available() or str(device) == "cpu" else "cpu")
        self.model_path = Path(model_path)
        self.mortal_root = Path(mortal_root)
        self._model_version = model_version or "mortal"
        self._enable_amp = bool(enable_amp)
        self._enable_rule_based_agari_guard = bool(enable_rule_based_agari_guard)
        self._enable_review_log = bool(enable_review_log)
        self.decision_log: list[dict[str, Any]] = []
        self.game_state = GameState()
        self.model = shared_model
        self._mortal_engine = shared_mortal_engine

        self._mortal_bot = self._load_native_mortal_bot(
            enable_amp=self._enable_amp,
            enable_rule_based_agari_guard=self._enable_rule_based_agari_guard,
        )
        self._context_builder = DefaultDecisionContextBuilder(
            model_version="mortal",
            riichi_state=None,
            inject_shanten_waits=inject_shanten_waits,
            enumerate_legal_actions_fn=lambda snap, seat: enumerate_legal_actions(snap, seat),
        )
        self._review_exporter = DefaultRuntimeReviewExporter()

    def reset(self) -> None:
        self.decision_log.clear()
        self.game_state = GameState()
        self._mortal_bot = self._load_native_mortal_bot(
            enable_amp=self._enable_amp,
            enable_rule_based_agari_guard=self._enable_rule_based_agari_guard,
        )
        self._context_builder = DefaultDecisionContextBuilder(
            model_version="mortal",
            riichi_state=None,
            inject_shanten_waits=inject_shanten_waits,
            enumerate_legal_actions_fn=lambda snap, seat: enumerate_legal_actions(snap, seat),
        )

    def set_player_id(self, player_id: int) -> None:
        player_id = int(player_id)
        if self.player_id == player_id:
            return
        self.player_id = player_id
        self.reset()

    @torch.no_grad()
    def react(self, event: dict[str, Any], gt_action: Optional[dict] = None) -> Optional[dict]:
        sanitized_event = sanitize_event_for_mortal(event)
        line = json.dumps(sanitized_event, ensure_ascii=False) if sanitized_event is not None else None
        if not self._enable_review_log:
            if line is None:
                return None
            reaction_line = self._mortal_bot.react(line)
            if reaction_line is None:
                return None
            return json.loads(reaction_line)

        ctx = self._context_builder.build(self.game_state, self.player_id, event)
        if line is None:
            return None
        reaction_line = self._mortal_bot.react(line)
        if ctx is None:
            return None
        legal_actions = ctx.legal_actions
        non_none = [action for action in legal_actions if action.get("type") != "none"]
        if not legal_actions:
            return None
        if reaction_line is None and not non_none:
            return {"type": "none", "actor": self.player_id}
        if reaction_line is None:
            return None

        reaction = json.loads(reaction_line)
        meta = dict(reaction.pop("meta", {}) or {})
        q_values, action_mask = _expand_compact_mortal_meta(meta)
        candidates = _score_mortal_candidates(ctx.legal_actions, q_values=q_values, action_mask=action_mask)
        decision = DecisionResult(
            chosen=reaction,
            candidates=candidates,
            model_value=0.0,
            model_aux=ModelAuxOutputs(),
        )
        entry = self._review_exporter.build_decision_entry(
            step=len(self.decision_log),
            ctx=ctx,
            decision=decision,
            gt_action=gt_action,
            actor=self.player_id,
        )
        entry["mortal_meta"] = {
            "mask_bits": meta.get("mask_bits"),
            "q_values": [float(value) for value in (meta.get("q_values") or [])],
            "expanded_q_values": list(q_values),
            "action_mask": list(action_mask),
            "is_greedy": meta.get("is_greedy"),
            "shanten": meta.get("shanten"),
            "at_furiten": meta.get("at_furiten"),
            "eval_time_ns": meta.get("eval_time_ns"),
            "batch_size": meta.get("batch_size"),
        }
        self.decision_log.append(entry)
        if self.verbose:
            print(f"[Mortal {self.player_id}] {reaction}")
        return reaction

    def _load_native_mortal_bot(
        self,
        *,
        enable_amp: bool,
        enable_rule_based_agari_guard: bool,
    ):
        mortal_python_dir = (self.mortal_root / "mortal").resolve()
        if not mortal_python_dir.exists():
            raise FileNotFoundError(f"Mortal python directory does not exist: {mortal_python_dir}")
        if str(mortal_python_dir) not in sys.path:
            sys.path.insert(0, str(mortal_python_dir))

        from engine import MortalEngine  # noqa: PLC0415
        from libriichi.mjai import Bot  # noqa: PLC0415
        from model import Brain, DQN  # noqa: PLC0415

        if self._mortal_engine is not None:
            return Bot(self._mortal_engine, self.player_id)

        state = torch.load(self.model_path, weights_only=True, map_location=torch.device("cpu"))
        cfg = state["config"]
        version = int(cfg["control"].get("version", 4))
        conv_channels = int(cfg["resnet"]["conv_channels"])
        num_blocks = int(cfg["resnet"]["num_blocks"])

        brain = Brain(version=version, conv_channels=conv_channels, num_blocks=num_blocks).eval()
        dqn = DQN(version=version).eval()
        brain.load_state_dict(state["mortal"])
        dqn.load_state_dict(state["current_dqn"])
        self.model = brain
        engine = MortalEngine(
            brain,
            dqn,
            version=version,
            is_oracle=False,
            device=self.device,
            enable_amp=bool(enable_amp),
            enable_quick_eval=False,
            enable_rule_based_agari_guard=bool(enable_rule_based_agari_guard),
            name="mortal-review",
        )
        self._mortal_engine = engine
        return Bot(engine, self.player_id)


def _expand_compact_mortal_meta(meta: dict[str, Any]) -> tuple[list[float], list[bool]]:
    mask_bits = int(meta.get("mask_bits", 0) or 0)
    compact_q = [float(value) for value in (meta.get("q_values") or [])]
    q_values = [float("-inf")] * MORTAL_ACTION_SPACE
    action_mask = [False] * MORTAL_ACTION_SPACE
    compact_idx = 0
    for action_id in range(MORTAL_ACTION_SPACE):
        if not (mask_bits & (1 << action_id)):
            continue
        action_mask[action_id] = True
        if compact_idx >= len(compact_q):
            raise RuntimeError("Mortal review meta q_values shorter than mask_bits")
        q_values[action_id] = compact_q[compact_idx]
        compact_idx += 1
    if compact_idx != len(compact_q):
        raise RuntimeError("Mortal review meta q_values longer than mask_bits")
    return q_values, action_mask


def _score_mortal_candidates(
    legal_actions: list[dict[str, Any]],
    *,
    q_values: list[float],
    action_mask: list[bool],
) -> list[ScoredCandidate]:
    candidates: list[ScoredCandidate] = []
    for action in legal_actions:
        action_ids = _mortal_action_ids_for_mjai(action)
        scored_ids = [action_id for action_id in action_ids if action_mask[action_id]]
        if scored_ids:
            score = max(float(q_values[action_id]) for action_id in scored_ids)
        else:
            score = -1e9
        if not isfinite(score):
            score = -1e9
        candidates.append(
            ScoredCandidate(
                action=action,
                logit=score,
                final_score=score,
                meta={
                    "mortal_action_ids": list(action_ids),
                    "mortal_scored_action_ids": scored_ids,
                },
            )
        )
    candidates.sort(key=lambda candidate: candidate.final_score, reverse=True)
    return candidates


def _mortal_action_ids_for_mjai(action: dict[str, Any]) -> tuple[int, ...]:
    action_type = action.get("type")
    if action_type == "dahai":
        return _mortal_discard_action_ids(str(action.get("pai", "")))
    if action_type == "reach":
        return (37,)
    if action_type == "chi":
        chi_id = _mortal_chi_action_id(action)
        return () if chi_id is None else (chi_id,)
    if action_type == "pon":
        return (41,)
    if action_type in {"daiminkan", "ankan", "kakan"}:
        return (42,)
    if action_type == "hora":
        return (43,)
    if action_type == "ryukyoku":
        return (44,)
    if action_type in {"none", "pass"}:
        return (45,)
    return ()


def _mortal_discard_action_ids(tile: str) -> tuple[int, ...]:
    if not tile:
        return ()
    exact = tuple(
        action_id
        for action_id, mortal_tile in enumerate(MORTAL_DISCARD_ID_TO_TILE)
        if mortal_tile == tile
    )
    if exact:
        return exact
    normalized = normalize_tile(tile)
    return tuple(
        action_id
        for action_id, mortal_tile in enumerate(MORTAL_DISCARD_ID_TO_TILE)
        if normalize_tile(mortal_tile) == normalized
    )


def _mortal_chi_action_id(action: dict[str, Any]) -> int | None:
    pai = _numbered_tile(action.get("pai"))
    if pai is None:
        return None
    consumed = sorted(
        item for item in (_numbered_tile(tile) for tile in action.get("consumed", [])) if item is not None
    )
    if len(consumed) != 2:
        return None
    suit, number = pai
    if any(tile_suit != suit for tile_suit, _ in consumed):
        return None
    consumed_numbers = [tile_number for _, tile_number in consumed]
    if consumed_numbers == [number + 1, number + 2]:
        return 38
    if consumed_numbers == [number - 1, number + 1]:
        return 39
    if consumed_numbers == [number - 2, number - 1]:
        return 40
    return None


def _numbered_tile(tile: Any) -> tuple[str, int] | None:
    if not isinstance(tile, str) or len(tile) < 2:
        return None
    normalized = normalize_tile(tile)
    if len(normalized) != 2 or normalized[1] not in {"m", "p", "s"}:
        return None
    try:
        number = int(normalized[0])
    except ValueError:
        return None
    return normalized[1], number


__all__ = ["MortalReviewBot"]
