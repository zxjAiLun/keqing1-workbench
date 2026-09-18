"""M0 smoke: the D1 project-owned population control registered as `m0_72k`.

Delivery scope for this integration:

* its own authoritative bundle (``M0_72k_s20260807``) with a SHA-verified copy,
* it resolves through every shared list the other named checkpoints go through,
  so it is actually selectable in Play / Review and not only importable,
* it keeps the DQN-era calibrated-Q reading (its scores ARE benefit estimates,
  unlike the P4-M11 U32 policy endpoint), and
* it stays a candidate: nothing that used to resolve elsewhere moves onto it.

Why the bundle pins the checkpoint: ``mortal_72000.pth`` is a per-step basename
shared by dozens of ``model_pool_2026_07`` checkpoints (M0/D1/D2/C/V/S0 runs and
more). Only the exact ``M0_control/seed_20260807`` file is M0, so the family
subpath ``M0_72k/mortal_72000.pth`` is the only safe way to address it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

import pytest

from inference.mortal_bot import SCORE_SEMANTICS_CALIBRATED_Q
from workbench.gateway.api.playwithyou import NETWORK_TO_SPEC, PLAYWITHYOU_MODEL_CATALOG
from workbench.replay.bot import _BOT_CLASSES, _MORTAL_BOT_TYPES
from workbench.replay.server import _GUI_MORTAL_MODEL_LABELS, _review_checkpoint_for_bot_type
from workbench.runtime.resolver import MORTAL_CHECKPOINTS, score_semantics_for

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "m0_72k"
M0_LABEL = "M0 72k (control)"
# Identity the P4-M13 gate-E existing-candidate re-evaluation recorded for the
# M0 challenger (ovt_existing_m0_solo/run_identity.json).
M0_SHA256 = "de7f6da7c0c07b89d658554050f2112f09fd9c021247104d5db44228db04823d"
M0_BUNDLE = "M0_72k_s20260807"


def _m0_checkpoint() -> Path:
    from inference.mortal_bot import MortalReviewBot  # noqa: F401  (import-time smoke)
    from workbench.runtime.resolver import resolve_bot_spec

    kind, path = resolve_bot_spec(MODEL_ID, REPO_ROOT)
    assert kind == "mortal"
    assert path is not None and path.is_file(), f"M0 checkpoint missing: {path}"
    return path


# --------------------------------------------------------------------------
# 1. registration: own id, pinned bundle, nothing else moved onto it
# --------------------------------------------------------------------------


def test_m0_72k_is_registered_as_a_candidate_and_keeps_defaults():
    # DQN-era checkpoint -> its scores are benefit estimates, not policy logits
    assert score_semantics_for(MODEL_ID) == SCORE_SEMANTICS_CALIBRATED_Q
    assert score_semantics_for("p4m11_u32") != SCORE_SEMANTICS_CALIBRATED_Q

    # present in every shared list a named checkpoint must appear in
    assert MODEL_ID in {item["model_id"] for item in PLAYWITHYOU_MODEL_CATALOG}
    assert NETWORK_TO_SPEC[MODEL_ID] == MODEL_ID
    assert MODEL_ID in _BOT_CLASSES
    assert MODEL_ID in _MORTAL_BOT_TYPES
    assert _GUI_MORTAL_MODEL_LABELS[MODEL_ID] == M0_LABEL

    # candidate, not default: existing specs must keep resolving where they did
    for spec in ("mortal", "70k", "ext_mortal", "weak", "weak_mortal", "p4m11_u32"):
        assert MORTAL_CHECKPOINTS[spec] != MORTAL_CHECKPOINTS[MODEL_ID]
        assert score_semantics_for(spec) == score_semantics_for(spec)  # unchanged lookup works

    # addressed by family subpath, never by the ambiguous bare basename
    relative = MORTAL_CHECKPOINTS[MODEL_ID]
    assert relative.name == "mortal_72000.pth"
    assert len(relative.parts) == 2, "must stay addressable only via M0_72k/..."


def test_m0_resolves_to_the_pinned_checkpoint():
    checkpoint = _m0_checkpoint()
    # the bundle is the pinned one, and the file inside it is the family subpath
    assert M0_BUNDLE in checkpoint.parts
    assert checkpoint.parts[-2:] == ("M0_72k", "mortal_72000.pth")
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == M0_SHA256


def test_m0_baeline_identity_matches_the_evaluation_record():
    """Bundle manifest records exactly the checkpoint the gate-E run scored."""
    manifest = Path(__file__).resolve().parents[1]  # repo root
    data_root = _m0_checkpoint().parents[4]  # .../mortal/authoritative/<bundle>/models/M0_72k/...
    # walk up to the bundle dir instead of guessing depth
    bundle_dir = _m0_checkpoint()
    while bundle_dir.parent.name != "models":
        bundle_dir = bundle_dir.parent
    bundle_dir = bundle_dir.parent.parent
    assert bundle_dir.name == M0_BUNDLE
    import json

    payload = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert payload["bundle"] == M0_BUNDLE
    assert payload["artifacts"]["M0_72k"]["sha256"] == M0_SHA256
    assert payload["artifacts"]["M0_72k"]["bytes"] == _m0_checkpoint().stat().st_size
    assert payload["provenance"]["score_semantics"].startswith("calibrated Q")
    assert manifest.is_dir() and data_root.is_dir()


# --------------------------------------------------------------------------
# 2. the GUI picker must know it, or no review can be launched for it
# --------------------------------------------------------------------------


def test_m0_is_selectable_in_the_gui_model_picker_but_is_not_the_default():
    """The GUI catalog is a second hand-maintained list (see the U32 incident).

    A model can be fully wired server-side and still be unreachable from the UI.
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
    assert f"'{MODEL_ID}'" in bot_types_ts, "the GUI BotType union does not know M0"

    # still a candidate: defaults must not move onto M0
    assert "DEFAULT_BOT_TYPE: BotType = 'mortal'" in catalog_ts
    upload = (REPO_ROOT / "workbench" / "replay_ui" / "src" / "components" / "Upload" / "UploadForm.tsx").read_text(
        encoding="utf-8"
    )
    default_selection = re.search(r"useState<BotType\[\]>\(\[(.*?)\]\)", upload)
    assert default_selection is not None
    assert MODEL_ID not in default_selection.group(1)

    # the picker's value must reach the checkpoint resolver
    assert _review_checkpoint_for_bot_type(MODEL_ID) == _m0_checkpoint()


# --------------------------------------------------------------------------
# 3. it really is a DQN-era checkpoint that the arena can load
# --------------------------------------------------------------------------


PLAYER_ID = 0
_PLAYED: dict | None = None


def _stored_replay_events() -> list:
    import json
    from project_data import data_root

    candidates = sorted((Path(data_root()) / "replays").glob("*/events.jsonl"))
    if not candidates:
        pytest.skip("no stored replay available")
    path = min(candidates, key=lambda item: item.stat().st_size)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _play_m0_once() -> dict:
    """Play one stored replay with M0 through the production replay path."""
    global _PLAYED
    if _PLAYED is None:
        from replay.bot import render_replay_json
        from replay.normalize import normalize_replay_decisions
        from workbench.replay.api import run_replay_single_raw

        bot = run_replay_single_raw(
            _stored_replay_events(),
            player_id=PLAYER_ID,
            checkpoint=str(_m0_checkpoint()),
            input_type="url",
            bot_type=MODEL_ID,
        )
        _PLAYED = {"bot": bot, "decisions": normalize_replay_decisions(render_replay_json(bot))}
    return _PLAYED


def test_m0_loads_and_plays_a_full_replay():
    """End-to-end: M0 runs the same engine path and emits legal decisions."""
    played = _play_m0_once()
    bot, decisions = played["bot"], played["decisions"]
    assert bot.score_semantics == SCORE_SEMANTICS_CALIBRATED_Q

    own_entries = [entry for entry in decisions["log"] if not entry.get("is_obs")]
    assert len(own_entries) > 50, "M0 produced no decisions"

    kyokus = {(entry.get("bakaze"), entry.get("kyoku")) for entry in own_entries}
    assert len(kyokus) >= 2, "M0 did not play past the first kyoku"

    discards = 0
    for entry in own_entries:
        hand = list(entry.get("hand") or [])
        tsumo = entry.get("tsumo_pai")
        legal = set(hand) | ({tsumo} if tsumo else set())
        chosen = entry.get("chosen") or {}
        for candidate in entry.get("candidates") or []:
            action = candidate.get("action") or {}
            if action.get("type") == "dahai":
                assert action.get("pai") in legal, (action, hand, tsumo)
        if chosen.get("type") == "dahai":
            assert chosen.get("pai") in legal, (chosen, hand, tsumo)
            discards += 1
    assert discards > 0, "M0 never completed a discard"

    # calibrated Q implies candidates are ordered best-first
    for entry in decisions["log"]:
        scores = [
            float(candidate["final_score"])
            for candidate in entry.get("candidates") or []
            if candidate.get("final_score") is not None
        ]
        assert scores == sorted(scores, reverse=True), entry.get("step")


def test_m0_checkpoint_declares_calibrated_q_semantics():
    torch = pytest.importorskip("torch")
    from inference.mortal_bot import _score_semantics_of

    # arena loader path: weights_only=True, same form the battle/review engines use
    state = torch.load(_m0_checkpoint(), weights_only=True, map_location="cpu")
    assert _score_semantics_of(state) == SCORE_SEMANTICS_CALIBRATED_Q
    # DQN-era shape: control.version=4 with the same head contract as V2/K0
    assert state["config"]["control"]["version"] == 4
    assert set(state["config"]["resnet"]) >= {"conv_channels", "num_blocks"}
    # Shape: not a student-policy export → DQN-era calibrated Q.
    # The discriminator is the training_contract SCHEMA, not the key's presence:
    # M0 is a full training checkpoint and does carry training_contract.
    contract = state.get("training_contract")
    if isinstance(contract, dict):
        assert contract.get("schema") != "keqing.mortal.student_policy_v1"
    # and the DQN head itself is intact
    assert "current_dqn" in state and "aux_net" in state
