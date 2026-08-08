from __future__ import annotations

from collections import Counter

from gateway.battle import BattleConfig, BattleManager
from gateway.bot_driver import BotDriver


class _DummyBot:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def react(self, event, gt_action=None):
        self.events.append(event)
        return None

    def reset(self):
        self.events.clear()


def _make_room():
    manager = BattleManager()
    room = manager.create_room(
        BattleConfig(
            player_count=4,
            players=[{"id": i, "name": f"P{i}", "type": "bot"} for i in range(4)],
        )
    )
    return manager, room


def test_sync_all_bots_replays_start_events():
    manager, room = _make_room()
    bots = {i: _DummyBot() for i in range(4)}
    driver = BotDriver(manager, lambda seat: bots[seat])

    manager.start_kyoku(room, seed=7)
    room.bot_event_cursor = {}
    driver.sync_all_bots(room)

    for seat in range(4):
        assert [event["type"] for event in bots[seat].events[:2]] == [
            "start_game",
            "start_kyoku",
        ]
        assert room.bot_event_cursor[seat] == len(room.events)


def test_build_snap_and_event_uses_chi_event_for_post_call_discard():
    manager, room = _make_room()
    bots = {i: _DummyBot() for i in range(4)}
    driver = BotDriver(manager, lambda seat: bots[seat])

    manager.start_kyoku(room, seed=7)
    room.state.players[0].hand = Counter({"2m": 1, "4m": 1, "7p": 1, "8p": 1, "9p": 1})
    room.state.last_discard = {"actor": 3, "pai": "3m"}
    manager.handle_meld(room, "chi", actor=0, pai="3m", consumed=["2m", "4m"], target=3)

    snap, event, trigger_event_index = driver._build_snap_and_event(room, 0)

    assert snap["tsumo_pai"] is None
    assert event["type"] == "chi"
    assert event["actor"] == 0
    assert trigger_event_index == len(room.events) - 1


def test_sync_current_trigger_event_is_not_preconsumed():
    manager, room = _make_room()
    bots = {i: _DummyBot() for i in range(4)}
    driver = BotDriver(manager, lambda seat: bots[seat])

    manager.start_kyoku(room, seed=7)
    room.bot_event_cursor = {}
    driver.sync_all_bots(room)

    manager.draw(room, 0)
    discard_event = manager.discard(room, 0, room.state.last_tsumo[0], tsumogiri=True)
    assert discard_event is not None

    trigger_index = len(room.events) - 1
    driver._sync_bot_events(room, 1, upto=trigger_index)

    assert room.bot_event_cursor[1] == trigger_index
    assert [event["type"] for event in bots[1].events] == [
        "start_game",
        "start_kyoku",
        "tsumo",
    ]
