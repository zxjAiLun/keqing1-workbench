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
import re
import threading
import time

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
    _review_checkpoint_for_bot_type,
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


def test_u32_is_selectable_in_the_gui_model_picker_but_is_not_the_default():
    """The product's model picker is a second, hand-maintained list.

    A model can therefore be fully wired server-side and still be unreachable
    from the UI -- which is what happened to U32: ``_GUI_MORTAL_MODEL_LABELS``
    (the server's allow-list for ``POST /api/replay/multi-teacher``) knew it,
    while the GUI catalog did not, so no review could be launched for it.
    Lock the two lists together instead of trusting them to agree.
    """
    catalog_ts = (REPO_ROOT / "workbench" / "replay_ui" / "src" / "utils" / "botCatalog.ts").read_text(
        encoding="utf-8"
    )
    bot_types_ts = (REPO_ROOT / "workbench" / "replay_ui" / "src" / "types" / "bot.ts").read_text(
        encoding="utf-8"
    )
    listed = set(re.findall(r"value:\s*'([a-z0-9_]+)'", catalog_ts))
    missing = sorted(set(_GUI_MORTAL_MODEL_LABELS) - listed)
    assert not missing, f"review models missing from the GUI picker: {missing}"
    assert f"'{MODEL_ID}'" in bot_types_ts, "the GUI BotType union does not know U32"

    # selectable, but still a candidate: the defaults must not move onto U32
    assert "DEFAULT_BOT_TYPE: BotType = 'mortal'" in catalog_ts
    default_selection = re.search(r"useState<BotType\[\]>\(\[(.*?)\]\)", (
        REPO_ROOT / "workbench" / "replay_ui" / "src" / "components" / "Upload" / "UploadForm.tsx"
    ).read_text(encoding="utf-8"))
    assert default_selection is not None
    assert MODEL_ID not in default_selection.group(1)

    # the picker's value is what the review endpoint hands to the checkpoint resolver
    assert _review_checkpoint_for_bot_type(MODEL_ID) == _u32_checkpoint()


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
# bot_driver._call_bot wraps bot.react in asyncio.wait_for(..., timeout=5.0) and
# substitutes its own fallback action on timeout, on exception, and on an empty
# return.  A turn that hit any of those is not evidence about the model.
REACT_TIMEOUT_SECONDS = 5.0


class _RecordingBot:
    """Wraps a runtime bot and records the RAW return of every ``react()`` call.

    ``driver.take_turn()`` returns the driver's *post-fallback* action, so matching
    a discarded tile against it only proves the driver played what the driver
    chose -- a fallback has a concrete tile too.  The raw record is what can be
    attributed to the model.

    Decision calls are the ones the driver makes through ``asyncio.to_thread``;
    event-sync calls run on the loop thread.  That is what ties a recorded call to
    the turn that awaited it.
    """

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []

    def react(self, event, *args, **kwargs):
        entry = {
            "event_type": str((event or {}).get("type")),
            "decision": threading.current_thread() is not threading.main_thread(),
            "returned": None,
            "error": None,
            "seconds": None,
        }
        self.calls.append(entry)
        started = time.perf_counter()
        try:
            chosen = self.inner.react(event, *args, **kwargs)
        except BaseException as exc:  # recorded, then re-raised for the driver
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise
        entry["seconds"] = time.perf_counter() - started
        entry["returned"] = chosen
        return chosen

    def decisions(self) -> list[dict]:
        return [call for call in self.calls if call["decision"]]

    def reset(self):
        return self.inner.reset()

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def _drive_hand(create_inner, *, turns: int = PLAY_TURNS) -> tuple[object, dict, list]:
    """Drive real turns of a 4-bot hand through the battle entry's own driver.

    This runs the code path ``/battle/start_4bot`` and ``/battle/advance`` use:
    ``BotDriver.take_turn`` calls ``bot.react``, validates the returned action
    against the legal set (``validate_and_apply``) and applies it to
    ``room.state``, which advances the hand.

    ``create_inner(seat)`` supplies the underlying bot, so the same drive path can
    be pointed at U32 or at a stub for a control.
    """
    import asyncio

    from gateway.battle import BattleConfig, BattleManager
    from gateway.bot_driver import BotDriver

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

    wrappers = {seat: _RecordingBot(create_inner(seat)) for seat in range(4)}
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
        recorder = wrappers[actor]
        before_calls = len(recorder.decisions())
        before_events = len(room.events)
        chosen = await driver.take_turn(room, actor)
        new_events = room.events[before_events:]
        transcript.append(
            {
                "actor": actor,
                "chosen": chosen,
                "decisions": recorder.decisions()[before_calls:],
                "new_events": new_events,
                "discards": [e.get("pai") for e in new_events if e.get("type") == "dahai"],
            }
        )
        await asyncio.sleep(0)

    return room, wrappers, transcript


def _play_attribution_problems(transcript: list[dict], *, min_attributed: int = 1) -> list[str]:
    """Why these recorded turns do NOT prove U32 moved the hand.

    Every check below reads the model's *own* return out of the recorder.  The
    driver's post-fallback action is never used as the comparator, because a
    fallback carries a concrete tile of its own -- matching against it would pass
    even if the model crashed on every single decision.

    ``take_turn``'s return value is not enough on its own either: it is
    ``_call_bot``'s result, so it equals the model's return even when
    ``validate_and_apply`` later substitutes a different tile.  That case is caught
    by comparing the return against the tile that actually reached the table.

    Turns where nothing reached the table are not attributed: declining an offer
    (``{\"type\": \"none\"}``) or having nothing to answer (``None`` on a ``none``
    event) is legitimate model behaviour, and there is simply no action to attribute.
    """
    if not transcript:
        return ["the driver produced no turns"]

    problems: list[str] = []
    attributed = 0
    for index, turn in enumerate(transcript):
        decisions = turn["decisions"]
        if len(decisions) != 1:
            problems.append(f"turn {index}: {len(decisions)} model decision calls, expected exactly 1")
            continue

        call = decisions[0]
        # A crash or a timeout anywhere means the driver substituted its own action,
        # whether or not this particular turn changed the table.
        if call["error"] is not None:
            problems.append(f"turn {index}: the model raised, so the driver's fallback was applied ({call['error']})")
            continue
        if call["seconds"] is None:
            problems.append(f"turn {index}: react() had not finished when the turn ended (timeout fallback)")
            continue
        if call["seconds"] >= REACT_TIMEOUT_SECONDS:
            problems.append(
                f"turn {index}: react() took {call['seconds']:.1f}s, at or past the {REACT_TIMEOUT_SECONDS}s timeout"
            )
            continue

        if not turn["discards"]:
            continue  # nothing reached the table; nothing to attribute

        returned = call["returned"]
        if not isinstance(returned, dict) or not returned.get("type"):
            problems.append(
                f"turn {index}: the model returned nothing usable ({returned!r}) yet a tile reached the table, "
                "so the driver chose that tile"
            )
            continue

        chosen = turn["chosen"] or {}
        if chosen.get("type") != returned.get("type") or chosen.get("pai") != returned.get("pai"):
            problems.append(
                f"turn {index}: the driver reported {chosen.get('type')}/{chosen.get('pai')} "
                f"but the model returned {returned.get('type')}/{returned.get('pai')}"
            )
            continue

        pai = returned.get("pai")
        if pai is not None and pai in turn["discards"]:
            attributed += 1
        else:
            problems.append(
                f"turn {index}: the model asked for {pai!r} but {turn['discards']!r} reached the table "
                "(an illegal or unrecognised action was substituted)"
            )

    if attributed < min_attributed:
        problems.append(
            f"only {attributed} turn(s) had a table tile matching what the model itself returned "
            f"(need {min_attributed})"
        )
    return problems


def _make_u32(seat: int):
    from inference.bot_registry import create_runtime_bot

    bot = create_runtime_bot(
        bot_name=MODEL_ID,
        player_id=seat,
        project_root=REPO_ROOT,
        device="cuda",
        verbose=False,
    )
    bot.reset()
    return bot


@pytest.fixture(scope="module")
def _played_hand():
    import asyncio

    _require_cuda()
    _require_bundle(MODEL_ID)
    return asyncio.run(_drive_hand(_make_u32))


def test_u32_is_selectable_in_the_play_entry():
    """`/battle/start_4bot` validates bot_model against this set."""
    from gateway.api.battle import SUPPORTED_BOT_MODELS

    assert MODEL_ID in SUPPORTED_BOT_MODELS
    # and it is still not the default this entry falls back to
    assert "mortal" in SUPPORTED_BOT_MODELS


def test_u32_actions_are_executed_and_advance_the_hand(_played_hand):
    room, wrappers, transcript = _played_hand

    # the model was really asked to move, through the real event path
    for seat, bot in wrappers.items():
        assert bot.calls, f"seat {seat} was never asked to act"
    assert any(
        call["event_type"] == "start_game" for bot in wrappers.values() for call in bot.calls
    ), "the hand never reached the bots through start_game"

    assert transcript, "the driver produced no turns"

    # the hand advanced: the event stream grew and discards accumulated
    assert len(room.events) > 0
    assert sum(len(turn["new_events"]) for turn in transcript) > 0
    assert any(turn["discards"] for turn in transcript), "no turn produced a discard"

    # attribution: this reads the RAW model returns AND the tiles that actually
    # reached the table, so neither a driver fallback nor an illegal-action
    # substitution can satisfy it (see test_play_attribution_rejects_a_driver_fallback)
    problems = _play_attribution_problems(transcript, min_attributed=2)
    assert problems == [], "the play smoke is not attributable to U32:\n  - " + "\n  - ".join(problems)


class _RaisingBot:
    """A bot whose *decision* calls always raise, so the driver must fall back.

    Event-sync calls run on the loop thread and must not raise, or the driver
    crashes before it ever reaches a decision.
    """

    def __init__(self):
        self.forced_errors = 0

    def react(self, event, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            self.forced_errors += 1
            raise RuntimeError("forced decision failure (negative control)")
        return {"type": "none"}

    def reset(self):
        return None


def test_play_attribution_rejects_a_driver_fallback():
    """Negative control for the play smoke (no model, no CUDA, runs in CI).

    Forces every decision call to raise and lets the real driver apply its own
    fallback.  The hand still advances and tiles still reach the table, so an
    acceptance check that compares against ``take_turn``'s return value passes
    here -- that is exactly the hole this control closes.  The shared acceptance
    function must fail instead.
    """
    import asyncio

    bots: dict[int, _RaisingBot] = {}

    def _make(seat: int):
        bots[seat] = _RaisingBot()
        return bots[seat]

    room, _wrappers, transcript = asyncio.run(_drive_hand(_make, turns=4))
    forced = sum(bot.forced_errors for bot in bots.values())

    # the control is only meaningful if the fallback actually ran for every turn
    assert forced == len(transcript) > 0, (forced, len(transcript))
    assert any(turn["discards"] for turn in transcript), (
        "the fallback did not move the hand, so this control does not exercise the hole"
    )
    assert len(room.events) > 0

    problems = _play_attribution_problems(transcript)
    assert problems, "the acceptance function accepted a pure driver fallback"
    assert any("the model raised" in problem for problem in problems)
    assert any("had a table tile matching" in problem for problem in problems)
