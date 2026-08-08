from __future__ import annotations

from collections import Counter

import keqing_core

from gateway.battle import BattleConfig, BattleManager


def _room_for_kakan():
    manager = BattleManager()
    room = manager.create_room(
        BattleConfig(
            players=[
                {"id": 0, "name": "p0", "type": "bot"},
                {"id": 1, "name": "p1", "type": "bot"},
                {"id": 2, "name": "p2", "type": "bot"},
                {"id": 3, "name": "p3", "type": "bot"},
            ]
        ),
        seed=1,
    )
    manager.start_kyoku(room, seed=1)
    start_kyoku = next(event for event in room.events if event["type"] == "start_kyoku")
    start_kyoku["tehais"][0] = [
        "1m",
        "2m",
        "3m",
        "4m",
        "5m",
        "6m",
        "7m",
        "8m",
        "9m",
        "1p",
        "2p",
        "8s",
        "8s",
    ]
    start_kyoku["tehais"][1] = [
        "1m",
        "2m",
        "3m",
        "4m",
        "5m",
        "6m",
        "7m",
        "8m",
        "9m",
        "1p",
        "2p",
        "3p",
        "8s",
    ]
    room.state.players[0].hand = Counter({"8s": 1, "1m": 1, "2m": 1, "3m": 1})
    room.state.players[0].melds = [
        {
            "type": "pon",
            "pai": "8s",
            "pai_raw": "8s",
            "consumed": ["8s", "8s"],
            "target": 1,
        }
    ]
    room.state.last_tsumo[0] = "8s"
    room.state.last_tsumo_raw[0] = "8s"
    room.state.actor_to_move = 0
    room.events.extend(
        [
            {"type": "dahai", "actor": 1, "pai": "8s", "tsumogiri": False},
            {"type": "pon", "actor": 0, "target": 1, "pai": "8s", "consumed": ["8s", "8s"]},
            {"type": "tsumo", "actor": 0, "pai": "8s"},
        ]
    )
    return manager, room


def _native_replay_snapshot_available() -> bool:
    return getattr(keqing_core, "_RUST_REPLAY_STATE_SNAPSHOT_JSON", None) is not None


def test_kakan_enters_chankan_response_window_before_acceptance() -> None:
    manager, room = _room_for_kakan()

    manager.handle_meld(room, "kakan", 0, "8s", ["8s", "8s", "8s"], target=1)

    assert room.pending_kakan is not None
    assert room.state.last_kakan is not None
    assert room.state.actor_to_move == 1
    assert room.state.players[0].hand["8s"] == 1
    assert room.events[-1]["type"] == "kakan"

    if _native_replay_snapshot_available():
        snapshot = keqing_core.replay_state_snapshot(room.events, 0)
        assert snapshot["last_kakan"]["actor"] == 0
        assert snapshot["actor_to_move"] == 0
        assert snapshot["hand"].count("8s") == 1


def test_kakan_accept_after_all_passes_does_not_remove_added_tile_twice() -> None:
    manager, room = _room_for_kakan()
    manager.handle_meld(room, "kakan", 0, "8s", ["8s", "8s", "8s"], target=1)

    manager.apply_action(room, 1, {"type": "none"})
    manager.apply_action(room, 2, {"type": "none"})
    assert room.pending_kakan is not None

    manager.apply_action(room, 3, {"type": "none"})

    assert room.pending_kakan is None
    assert room.pending_rinshan is True
    assert room.state.pending_rinshan_actor == 0
    assert room.state.actor_to_move == 0
    assert room.state.players[0].hand["8s"] == 0
    assert room.state.players[0].melds[0]["type"] == "kakan"
    assert any(event["type"] == "kakan_accepted" for event in room.events)
    if _native_replay_snapshot_available():
        snapshot = keqing_core.replay_state_snapshot(room.events, 0)
        assert "8s" not in snapshot["hand"]
        assert snapshot["pending_rinshan_actor"] == 0
