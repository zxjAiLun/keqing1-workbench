from __future__ import annotations

from collections import Counter

from gateway.battle import BattleConfig, BattleManager
from gateway.api import battle as battle_api


def _make_room():
    manager = BattleManager()
    room = manager.create_room(
        BattleConfig(
            player_count=4,
            players=[{"id": i, "name": f"P{i}", "type": "bot"} for i in range(4)],
        )
    )
    room.phase = "playing"
    room.state.actor_to_move = 0
    room.state.last_discard = None
    room.state.last_kakan = None
    room.state.last_tsumo = [None, None, None, None]
    room.state.last_tsumo_raw = [None, None, None, None]
    room.human_player_id = 0
    room.wall = ["1m"] * 30
    room.wall_index = 0
    return manager, room


def test_pending_post_call_discard_detects_multiple_open_meld_shapes():
    manager, room = _make_room()
    room.state.players[0].hand = Counter({"1m": 4, "2m": 4})

    assert battle_api._has_pending_post_call_discard(room, 0) is True

    room.state.players[0].hand = Counter({"1m": 4, "2m": 4, "3m": 2})

    assert battle_api._has_pending_post_call_discard(room, 0) is False


def test_prepare_player_state_does_not_draw_after_human_chi_with_existing_melds(monkeypatch):
    manager, room = _make_room()
    room.state.players[0].hand = Counter({"1m": 4, "2m": 4})

    called = {"draw": 0}

    def fake_draw(_room, actor):
        called["draw"] += 1
        _room.state.last_tsumo[actor] = "9p"
        return "9p"

    monkeypatch.setattr(battle_api.manager, "draw", fake_draw)

    state = battle_api._prepare_player_state(room, 0)

    assert called["draw"] == 0
    assert state["tsumo_pai"] is None


def test_handle_meld_chi_does_not_mark_live_replay_draw_actor():
    manager, room = _make_room()
    room.state.players[0].hand = Counter({"2m": 1, "4m": 1, "7p": 1})
    room.state.last_discard = {"actor": 3, "pai": "3m"}

    manager.handle_meld(room, "chi", actor=0, pai="3m", consumed=["2m", "4m"], target=3)

    assert room.replay_draw_actor is None


def test_get_state_for_player_keeps_draw_tile_only_in_tsumo_slot():
    manager, room = _make_room()
    room.state.players[0].hand = Counter(
        {
            "1m": 1,
            "2m": 1,
            "3m": 1,
            "4m": 1,
            "5m": 1,
            "6m": 1,
            "7m": 1,
            "8m": 1,
            "9m": 1,
            "1p": 1,
            "2p": 1,
            "3p": 1,
            "S": 2,
        }
    )
    room.state.last_tsumo[0] = "S"
    room.state.last_tsumo_raw[0] = "S"

    state = manager.get_state_for_player(room, 0)

    assert state["tsumo_pai"] == "S"
    assert state["hand"].count("S") == 1
    assert len(state["hand"]) == 13
