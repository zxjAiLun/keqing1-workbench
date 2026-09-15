"""P4-M11 U32 smoke: the direct-PG endpoint registered as `p4m11_u32`.

Delivery scope for this integration:

* U32 loads through the same engine loader as the DQN-era checkpoints,
* it produces legal actions and finishes a whole stored replay,
* review shows its action ordering, and
* the Q-difference features (benefit loss / error severity) are NOT applied to
  it, because its output is a policy action score, not a calibrated Q.

U32 is a candidate: it must not take the default or any alias.

The play smoke at the bottom drives the *battle* entry (the one
``/battle/start_4bot`` and ``/battle/advance`` use) rather than the stored-replay
review path, so it shows the model moving a live hand instead of only reading
stored decisions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from inference.mortal_bot import (
    SCORE_SEMANTICS_ACTION_SCORE,
    SCORE_SEMANTICS_CALIBRATED_Q,
    MortalReviewBot,
    _model_dimensions,
    _score_semantics_of,
)
from workbench.gateway.api.playwithyou import NETWORK_TO_SPEC, PLAYWITHYOU_MODEL_CATALOG
from workbench.replay.bot import _BOT_CLASSES, _MORTAL_BOT_TYPES
from workbench.replay.server import (
    _GUI_MORTAL_MODEL_LABELS,
    _build_runtime_teacher_report,
    _load_teacher_report_entries,
    _q_loss,
)
from workbench.runtime.resolver import (
    MORTAL_CHECKPOINTS,
    resolve_bot_spec,
    score_semantics_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "p4m11_u32"
PLAYER_ID = 0
# Frozen identity of the P4-M11 evaluation export, see U32_eval_weights.provenance.json.
U32_SHA256 = "3703c943a64a00ca128f4a5f5f989d446dc5814c7e70dc50527d2756039a8add"
U32_LABEL = "P4-M11 U32 (policy)"


def _require_cuda():
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA required to load the Mortal engine")


def _require_bundle(spec: str) -> Path:
    """Resolve a spec against the shared authoritative bundles, or skip.

    The published checkpoints live under the shared data root, not in the repo,
    so CI has no copy of them.  Absent assets must skip, never fail.
    """
    try:
        _kind, path = resolve_bot_spec(spec, REPO_ROOT)
    except Exception as exc:  # pragma: no cover - depends on the local data root
        pytest.skip(f"authoritative bundle for {spec!r} is unavailable: {exc}")
    return Path(path)


def _u32_checkpoint() -> Path:
    return _require_bundle(MODEL_ID)


def _stored_replay_events() -> list[dict]:
    from project_data import data_root

    candidates = sorted((Path(data_root()) / "replays").glob("*/events.jsonl"))
    if not candidates:
        pytest.skip("no stored replay available")
    path = min(candidates, key=lambda item: item.stat().st_size)
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


_PLAYED: dict | None = None


def _play_u32_once() -> dict:
    """Play one stored replay with U32 through the production replay path."""
    global _PLAYED
    if _PLAYED is None:
        _require_cuda()
        from replay.bot import render_replay_json
        from replay.normalize import normalize_replay_decisions
        from workbench.replay.api import run_replay_single_raw

        bot = run_replay_single_raw(
            _stored_replay_events(),
            player_id=PLAYER_ID,
            checkpoint=str(_u32_checkpoint()),
            input_type="url",
            bot_type=MODEL_ID,
        )
        _PLAYED = {
            "bot": bot,
            "decisions": normalize_replay_decisions(render_replay_json(bot)),
        }
    return _PLAYED


# --------------------------------------------------------------------------
# 1. registration: independent id, K0 keeps the default and the aliases
# --------------------------------------------------------------------------


def test_p4m11_u32_is_registered_as_a_candidate_and_keeps_k0_default():
    assert score_semantics_for(MODEL_ID) == SCORE_SEMANTICS_ACTION_SCORE
    assert score_semantics_for("70k") == SCORE_SEMANTICS_CALIBRATED_Q
    assert score_semantics_for("ext_mortal") == SCORE_SEMANTICS_CALIBRATED_Q

    assert MODEL_ID in {item["model_id"] for item in PLAYWITHYOU_MODEL_CATALOG}
    assert NETWORK_TO_SPEC[MODEL_ID] == MODEL_ID
    assert MODEL_ID in _BOT_CLASSES
    assert MODEL_ID in _MORTAL_BOT_TYPES
    assert _GUI_MORTAL_MODEL_LABELS[MODEL_ID] == U32_LABEL

    # candidate, not default: nothing that used to resolve elsewhere moved onto U32
    assert NETWORK_TO_SPEC["mortal"] == "mortal"
    assert MORTAL_CHECKPOINTS[MODEL_ID].name == "U32_eval_weights.pth"
    for spec in ("mortal", "70k", "ext_mortal", "weak", "weak_mortal"):
        assert MORTAL_CHECKPOINTS[spec] != MORTAL_CHECKPOINTS[MODEL_ID]


def test_p4m11_u32_resolves_to_the_frozen_export():
    checkpoint = _u32_checkpoint()
    assert checkpoint.name == "U32_eval_weights.pth"
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == U32_SHA256


# --------------------------------------------------------------------------
# 2. the checkpoint declares its own score semantics
# --------------------------------------------------------------------------


def test_u32_checkpoint_declares_its_own_action_score_semantics():
    torch = pytest.importorskip("torch")

    state = torch.load(_u32_checkpoint(), weights_only=True, map_location="cpu")
    assert state["training_contract"]["schema"] == "keqing.mortal.student_policy_v1"
    assert _model_dimensions(state) == (4, 192, 40)
    assert _score_semantics_of(state) == SCORE_SEMANTICS_ACTION_SCORE

    # negative control: a DQN-era checkpoint keeps the calibrated-Q reading
    state_70k = torch.load(_require_bundle("70k"), weights_only=True, map_location="cpu")
    assert "config" in state_70k
    assert _model_dimensions(state_70k)[1:] == (192, 40)
    assert _score_semantics_of(state_70k) == SCORE_SEMANTICS_CALIBRATED_Q


# --------------------------------------------------------------------------
# 3. Q-difference features are not applicable to a policy endpoint
# --------------------------------------------------------------------------


def test_benefit_loss_is_not_applicable_for_action_score_models():
    assert _q_loss(10.0, 2.0, SCORE_SEMANTICS_ACTION_SCORE) is None
    assert _q_loss(10.0, 2.0, SCORE_SEMANTICS_CALIBRATED_Q) == 8.0
    assert _q_loss(2.0, 10.0, SCORE_SEMANTICS_CALIBRATED_Q) == 0.0
    assert _q_loss(None, 2.0, SCORE_SEMANTICS_CALIBRATED_Q) is None
    assert _q_loss(10.0, None, SCORE_SEMANTICS_CALIBRATED_Q) is None
    # legacy/stored entries carry no semantics field: behaviour is unchanged
    assert _q_loss(10.0, 2.0, None) == 8.0


# --------------------------------------------------------------------------
# 4. load / legal actions / a finished replay / action ordering
# --------------------------------------------------------------------------


def test_p4m11_u32_loads_plays_a_full_replay_and_orders_actions():
    played = _play_u32_once()
    bot, decisions = played["bot"], played["decisions"]
    assert bot.score_semantics == SCORE_SEMANTICS_ACTION_SCORE

    log = decisions["log"]
    # ``is_obs`` entries watch the other seats: their ``chosen`` is that seat's
    # actual action while ``hand`` is still the reviewed player's, so only the
    # reviewed player's own decision windows can be checked for legality.
    own_entries = [entry for entry in log if not entry.get("is_obs")]
    assert len(own_entries) > 50, "U32 produced no decisions"

    kyokus = {(entry.get("bakaze"), entry.get("kyoku")) for entry in own_entries}
    assert len(kyokus) >= 2, "U32 did not play past the first kyoku"

    discards = 0
    for entry in own_entries:
        hand = list(entry.get("hand") or [])
        tsumo = entry.get("tsumo_pai")
        legal = set(hand) | ({tsumo} if tsumo else set())
        chosen = entry.get("chosen") or {}
        assert chosen.get("actor") in (None, PLAYER_ID), entry.get("step")
        for candidate in entry.get("candidates") or []:
            action = candidate.get("action") or {}
            if action.get("type") == "dahai":
                assert action.get("pai") in legal, (action, hand, tsumo)
        if chosen.get("type") == "dahai":
            assert chosen.get("pai") in legal, (chosen, hand, tsumo)
            discards += 1
    assert discards > 0, "U32 never completed a discard (no turn finished)"

    # review ordering: candidates are presented best-first
    for entry in log:
        scores = [
            float(candidate["final_score"])
            for candidate in entry.get("candidates") or []
            if candidate.get("final_score") is not None
        ]
        assert scores == sorted(scores, reverse=True), entry.get("step")


# --------------------------------------------------------------------------
# 5. the review payload says which kind of number the panel is showing
# --------------------------------------------------------------------------


def _report_entries(report: dict, tmp_path: Path, tag: str) -> list[dict]:
    report_path = tmp_path / f"{tag}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    _tag, _player, entries = _load_teacher_report_entries(
        report_path, {"player_id": PLAYER_ID, "log": []}
    )
    return entries


def test_review_labels_u32_as_action_scores_and_withholds_q_loss(tmp_path):
    decisions = _play_u32_once()["decisions"]
    checkpoint = _u32_checkpoint()

    report = _build_runtime_teacher_report(
        replay_id="p4m11_u32_smoke",
        model_type=MODEL_ID,
        player_id=PLAYER_ID,
        checkpoint=checkpoint,
        decisions=decisions,
    )
    entries = [entry for kyoku in report["review"]["kyokus"] for entry in kyoku["entries"]]
    loaded = _report_entries(report, tmp_path, "u32")
    assert entries and loaded

    assert all(entry["score_semantics"] == SCORE_SEMANTICS_ACTION_SCORE for entry in loaded)
    assert all(entry["q_loss"] is None for entry in loaded)

    # negative control: the same decisions under a calibrated-Q label DO produce
    # a benefit loss, so the gate above is wired rather than always-None
    control = _build_runtime_teacher_report(
        replay_id="p4m11_u32_smoke_control",
        model_type="70k",
        player_id=PLAYER_ID,
        checkpoint=checkpoint,
        decisions=decisions,
    )
    control_entries = _report_entries(control, tmp_path, "70k")
    assert all(entry["score_semantics"] == SCORE_SEMANTICS_CALIBRATED_Q for entry in control_entries)
    assert any(entry["q_loss"] is not None for entry in control_entries)


# --------------------------------------------------------------------------
# 6. the PLAY entry: U32's action is executed and the hand moves on
# --------------------------------------------------------------------------

PLAY_SEED = 20260915
PLAY_TURNS = 12


class _RecordingBot:
    """Wrap a runtime bot so the test can see that it was really asked to move."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[tuple[str, object]] = []

    def react(self, event, *args, **kwargs):
        chosen = self.inner.react(event, *args, **kwargs)
        self.calls.append((str((event or {}).get("type")), chosen))
        return chosen

    def reset(self):
        return self.inner.reset()

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def _drive_u32_hand(turns: int = PLAY_TURNS) -> tuple[object, dict, list]:
    """Drive real turns of a 4-bot hand through the battle entry's own driver.

    The replay test above only *reads* stored decisions -- the stored log decides
    the moves, so it never shows the model moving the game.  This runs the code
    path `/battle/start_4bot` and `/battle/advance` use: ``BotDriver.take_turn``
    calls ``bot.react``, validates the returned action against the legal set
    (``validate_and_apply``) and applies it to ``room.state``, which advances the
    hand.
    """
    import asyncio

    from gateway.battle import BattleConfig, BattleManager
    from gateway.bot_driver import BotDriver
    from inference.bot_registry import create_runtime_bot

    manager = BattleManager()
    config = BattleConfig(
        player_count=4,
        players=[{"id": f"bot_{i}", "name": f"{MODEL_ID}-{i + 1}号机", "type": "bot"} for i in range(4)],
        game_length="tonpu",
        allow_west_round=False,
    )
    room = manager.create_room(config, seed=PLAY_SEED)
    room.human_player_id = -1  # no human seat, same as /start_4bot
    manager.start_kyoku(room, seed=PLAY_SEED)
    room.bot_event_cursor = {}

    wrappers = {}
    for seat in range(4):
        bot = create_runtime_bot(
            bot_name=MODEL_ID,
            player_id=seat,
            project_root=REPO_ROOT,
            device="cuda",
            verbose=False,
        )
        bot.reset()
        wrappers[seat] = _RecordingBot(bot)

    driver = BotDriver(manager, lambda seat: wrappers[seat])
    # Feeds start_game/start_kyoku through the real event path, so the engine is
    # loaded here rather than inside take_turn's 5s react timeout.
    driver.sync_all_bots(room)

    transcript: list[dict] = []
    for _ in range(turns):
        if room.phase != "playing":
            break
        actor = room.state.actor_to_move
        if actor is None:
            break
        before = len(room.events)
        chosen = await driver.take_turn(room, actor)
        new_events = room.events[before:]
        transcript.append(
            {
                "actor": actor,
                "chosen": chosen,
                "new_events": new_events,
                "discards": [e.get("pai") for e in new_events if e.get("type") == "dahai"],
            }
        )
        await asyncio.sleep(0)

    return room, wrappers, transcript


@pytest.fixture(scope="module")
def _played_hand():
    import asyncio

    _require_cuda()
    _require_bundle(MODEL_ID)
    return asyncio.run(_drive_u32_hand())


def test_u32_is_selectable_in_the_play_entry():
    """`/battle/start_4bot` validates bot_model against this set."""
    from gateway.api.battle import SUPPORTED_BOT_MODELS

    assert MODEL_ID in SUPPORTED_BOT_MODELS
    # and it is still not the default this entry falls back to
    assert "mortal" in SUPPORTED_BOT_MODELS


def test_u32_actions_are_executed_and_advance_the_hand(_played_hand):
    room, wrappers, transcript = _played_hand

    # the model was actually asked to move (through the real event path)
    for seat, bot in wrappers.items():
        assert bot.calls, f"seat {seat} was never asked to act"
    assert any(
        event_type == "start_game"
        for bot in wrappers.values()
        for event_type, _ in bot.calls
    ), "the hand never reached the bots through start_game"

    assert transcript, "the driver produced no turns"

    # at least one turn ended with a discard that the driver applied, i.e. the
    # model's action reached room.state and moved the hand on
    moved = [turn for turn in transcript if turn["discards"]]
    assert moved, "no turn produced a discard; the action was never executed"

    # the hand advanced: the event stream grew and the discard count accumulated
    assert len(room.events) > 0
    total_discards = sum(len(turn["discards"]) for turn in transcript)
    assert total_discards >= 1
    assert sum(len(turn["new_events"]) for turn in transcript) > 0

    # it was the model's decision that was applied, not the driver's fallback:
    # the fallback only runs when react() raises or exceeds 5s, and it always
    # returns a legal action, so its absence has to be shown positively.  A
    # discard whose pai equals the pai the model asked for can only come from the
    # model -- the fallback would have picked the first legal pai instead.
    discarding_turns = [turn for turn in transcript if turn["discards"]]
    for turn in discarding_turns:
        assert turn["chosen"] is not None
        assert turn["chosen"].get("type") != "none"
    exact = [
        turn
        for turn in discarding_turns
        if turn["chosen"].get("pai") in turn["discards"]
    ]
    assert exact, (
        "the applied discards never matched the pai the model asked for: "
        f"chosen={[t['chosen'].get('pai') for t in discarding_turns]} "
        f"applied={[t['discards'] for t in discarding_turns]}"
    )


def test_driver_honours_the_requested_tile_and_substitutes_when_there_is_none():
    """What the play smoke above may and may not claim (no model, no CUDA).

    Measured here: at a discard decision the driver discards SOMETHING even when
    the bot returns ``{"type": "none"}`` -- ``action_dict_to_spec`` reads that as a
    discard and ``validate_and_apply`` then falls back to ``legal_dahai[0]``.  So
    "a discard appeared and the hand moved on" is NOT evidence that the model's
    decision ran; the attributable evidence is that the applied tile equals the
    tile the model asked for.  Both halves are pinned here so the smoke's claim
    cannot silently become the weaker one.
    """
    import asyncio

    from gateway.battle import BattleConfig, BattleManager
    from gateway.bot_driver import BotDriver

    def _run_turn(request_index: int | None) -> tuple[str | None, str | None]:
        """Play one turn; return (tile the stub asked for, tile actually discarded)."""
        manager = BattleManager()
        room = manager.create_room(
            BattleConfig(
                player_count=4,
                players=[{"id": f"bot_{i}", "name": f"P{i}", "type": "bot"} for i in range(4)],
                game_length="tonpu",
                allow_west_round=False,
            ),
            seed=PLAY_SEED,
        )
        room.human_player_id = -1
        manager.start_kyoku(room, seed=PLAY_SEED)
        room.bot_event_cursor = {}
        asked: dict[str, str | None] = {"pai": None}

        class _StubBot:
            def react(self, event, *args, **kwargs):
                if request_index is None:
                    return {"type": "none"}
                actor = room.state.actor_to_move
                hand = sorted(room.state.players[actor].hand)
                if not hand:
                    return {"type": "none"}
                tile = hand[0] if request_index == 0 else hand[-1]
                asked["pai"] = tile
                return {"type": "dahai", "actor": actor, "pai": tile}

            def reset(self):
                return None

        driver = BotDriver(manager, lambda _seat: _StubBot())
        driver.sync_all_bots(room)

        async def _drive() -> str | None:
            actor = room.state.actor_to_move
            before = len(room.events)
            await driver.take_turn(room, actor)
            discards = [e.get("pai") for e in room.events[before:] if e.get("type") == "dahai"]
            return discards[0] if discards else None

        return asked["pai"], asyncio.run(_drive())

    # (a) asking for nothing still produces a discard -> the weak claim is vacuous
    asked_none, applied_none = _run_turn(None)
    assert asked_none is None
    assert applied_none is not None, "the driver no longer substitutes a discard; re-check the trap"

    # (b) naming a tile does put *that* tile on the table, and two different
    #     requests produce two different discards -- so the strong claim holds
    asked_first, applied_first = _run_turn(0)
    asked_last, applied_last = _run_turn(-1)
    assert asked_first is not None and asked_last is not None
    assert applied_first == asked_first, (applied_first, asked_first)
    assert applied_last == asked_last, (applied_last, asked_last)
    assert applied_first != applied_last, (
        "both requests produced the same discard, so the driver is choosing the tile "
        "and the play smoke's pai match would be meaningless"
    )
