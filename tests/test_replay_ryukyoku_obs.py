from __future__ import annotations

from pathlib import Path

import replay.bot as replay_bot


class _FakeState:
    def snapshot(self, player_id: int):
        return {
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "oya": 0,
            "scores": [25000, 25000, 25000, 25000],
            "hand": ["1m"] * 13,
            "discards": [[], [], [], []],
            "melds": [[], [], [], []],
            "dora_markers": ["1p"],
            "reached": [False, False, False, False],
            "actor_to_move": 0,
            "last_discard": None,
        }


class _FakeBot:
    def __init__(self, player_id: int, model_path: str | Path):
        self.player_id = player_id
        self.model_path = model_path
        self.decision_log = []
        self.game_state = _FakeState()
        self.player_names = []

    def react(self, event: dict):
        return None


def test_run_replay_records_ryukyoku_obs_without_actor(monkeypatch, tmp_path: Path):
    checkpoint = tmp_path / "fake.pth"
    checkpoint.write_text("ok", encoding="utf-8")

    monkeypatch.setitem(replay_bot._BOT_CLASSES, "testbot", _FakeBot)

    events = [
        {"type": "start_game", "names": ["A", "B", "C", "D"]},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "oya": 0,
            "scores": [25000, 25000, 25000, 25000],
            "tehais": [["1m"] * 13 for _ in range(4)],
            "dora_marker": "1p",
        },
        {
            "type": "ryukyoku",
            "scores": [26500, 26500, 23500, 23500],
            "deltas": [1500, 1500, -1500, -1500],
            "tenpai_players": [0, 1],
        },
    ]

    bot, _ = replay_bot.run_replay_from_source(
        events,
        player_id=0,
        checkpoint=checkpoint,
        input_type="url",
        bot_type="testbot",
    )

    ryukyoku_entries = [entry for entry in bot.decision_log if entry.get("chosen", {}).get("type") == "ryukyoku"]
    assert len(ryukyoku_entries) == 1
    entry = ryukyoku_entries[0]
    assert entry["is_obs"] is True
    assert entry["gt_action"]["type"] == "ryukyoku"
    assert entry["gt_action"]["tenpai_players"] == [0, 1]
    assert entry["obs_kind"] == "terminal"
    assert entry["board_phase"] == "after_action"
