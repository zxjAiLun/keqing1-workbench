from types import SimpleNamespace

import inference.default_context as default_context
from inference.runtime_bot import RuntimeBot
from inference.mortal_bot import MortalReviewBot
from replay import bot as replay_bot


def test_replay_bot_classes_use_shared_runtime_bot():
    assert replay_bot._BOT_CLASSES["mortal"] is MortalReviewBot


def test_mortal_review_bot_skips_unsupported_kakan_accepted(monkeypatch):
    calls: list[str] = []

    class _FakeNativeBot:
        def react(self, line: str):
            calls.append(line)
            return None

    monkeypatch.setattr(
        MortalReviewBot,
        "_load_native_mortal_bot",
        lambda self, **kwargs: _FakeNativeBot(),
    )

    bot = MortalReviewBot(player_id=0, model_path="artifacts/mortal_training/mortal.pth")
    bot._context_builder = SimpleNamespace(build=lambda state, actor, event: None)

    assert bot.react({"type": "kakan_accepted", "actor": 0, "pai": "5m"}) is None
    assert calls == []


def test_default_context_builder_daiminkan_is_not_followup_decision_window(monkeypatch):
    builder = default_context.DefaultDecisionContextBuilder(
        model_version="mortal",
        riichi_state=None,
        inject_shanten_waits=lambda *args, **kwargs: None,
        enumerate_legal_actions_fn=lambda snap, seat: [],
    )

    monkeypatch.setattr(default_context, "apply_event", lambda state, event: None)
    builder._snapshot_for_actor = lambda state, actor, events=None: {
        "hand": [],
        "melds": [[], [], [], []],
    }

    state = object()

    assert builder.build(state, 0, {"type": "start_game", "names": ["A", "B", "C", "D"]}) is None
    assert (
        builder.build(
            state,
            0,
            {
                "type": "daiminkan",
                "actor": 0,
                "target": 1,
                "pai": "4m",
                "consumed": ["4m", "4m", "4m"],
            },
        )
        is None
    )


def test_default_context_builder_rejects_stale_native_hand_snapshot(monkeypatch):
    builder = default_context.DefaultDecisionContextBuilder(
        model_version="mortal",
        riichi_state=None,
        inject_shanten_waits=lambda *args, **kwargs: None,
        enumerate_legal_actions_fn=lambda snap, seat: [],
    )
    python_snapshot = {"hand": ["2m"], "melds": [[], [], [], []]}
    stale_snapshot = {"hand": ["2m", "3m"], "melds": [[], [], [], []]}
    state = SimpleNamespace(snapshot=lambda actor: python_snapshot)

    monkeypatch.setattr(default_context.keqing_core, "is_enabled", lambda: True)
    monkeypatch.setattr(
        default_context.keqing_core,
        "replay_state_snapshot",
        lambda events, actor: stale_snapshot,
    )

    assert builder._snapshot_for_actor(state, 0, events=[{"type": "kakan_accepted"}]) == python_snapshot
