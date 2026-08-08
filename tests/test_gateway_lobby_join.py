from __future__ import annotations

import asyncio

from gateway.responder import Helo
from gateway.utils.state import State


def test_state_parses_lobby_room_into_numeric_join_target() -> None:
    state = State(name="NoName", room="L2147_9")

    assert state.lobby_id == 2147
    assert state.room == "2147,9"


def test_helo_sends_lobby_then_join_for_lobby_rooms() -> None:
    state = State(name="NoName", room="L2147_9")
    sent: list[dict] = []

    async def send_to_tenhou(message: dict) -> None:
        sent.append(message)

    async def send_to_mjai(message: dict) -> dict:
        return message

    asyncio.run(Helo().process(state, {"tag": "HELO"}, send_to_tenhou, send_to_mjai))

    assert sent == [
        {"tag": "LOBBY", "id": "2147"},
        {"tag": "JOIN", "t": "2147,9"},
    ]
