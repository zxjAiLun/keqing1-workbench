"""P4-M11 U32 smoke: the direct-PG endpoint registered as `p4m11_u32`.

Delivery scope for this integration:

* U32 loads through the same engine loader as the DQN-era checkpoints,
* it produces legal actions and finishes a whole stored replay,
* review shows its action ordering, and
* the Q-difference features (benefit loss / error severity) are NOT applied to
  it, because its output is a policy action score, not a calibrated Q.

U32 is a candidate: it must not take the default or any alias.
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
