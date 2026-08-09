from __future__ import annotations

import json
import asyncio

import pytest

from gateway.tenhou_bridge import (
    TENHOU_ROOM_PATTERN,
    TenhouBridgeConfig,
    is_valid_tenhou_room,
    normalize_tenhou_room,
)
from gateway.tenhou_bridge import TenhouBridge
from gateway.utils.state import State


def test_room_validation_accepts_numeric_and_lobby_rooms() -> None:
    assert is_valid_tenhou_room("2147_0")
    assert is_valid_tenhou_room("2147_9")
    assert is_valid_tenhou_room("2147_41")
    assert is_valid_tenhou_room("L1000_0")
    assert is_valid_tenhou_room("l1000_9")
    assert not is_valid_tenhou_room("2147")
    assert not is_valid_tenhou_room("L1000")
    assert not is_valid_tenhou_room("ROOM_0")
    assert is_valid_tenhou_room("2147_2")
    assert TENHOU_ROOM_PATTERN.match("L1000_0")


def test_normalize_tenhou_room_adds_default_suffix() -> None:
    assert normalize_tenhou_room("L2147") == "L2147_9"
    assert normalize_tenhou_room("2147") == "2147_9"
    assert normalize_tenhou_room("l2147_9") == "L2147_9"
    assert normalize_tenhou_room("L2147", default_suffix="1") == "L2147_1"


def test_build_helo_merges_extra_fields() -> None:
    cfg = TenhouBridgeConfig(sex="F", helo_extra={"tid": "abc", "auth": 1})

    assert cfg.build_helo(name="bot") == {
        "tag": "HELO",
        "name": "bot",
        "sx": "F",
        "tid": "abc",
        "auth": 1,
    }


def test_from_env_rejects_non_object_helo_json(monkeypatch) -> None:
    monkeypatch.setenv("TENHOU_HELO_JSON", json.dumps(["bad"]))

    with pytest.raises(ValueError):
        TenhouBridgeConfig.from_env()


def test_bridge_does_not_reconnect_after_connect_failure(monkeypatch) -> None:
    calls = []

    class FailingConnection:
        async def __aenter__(self):
            calls.append("connect")
            raise OSError("simulated network drop")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "gateway.tenhou_bridge.websockets.connect", lambda *_args, **_kwargs: FailingConnection()
    )

    async def send_to_mjai(_message):
        return {"type": "none"}

    bridge = TenhouBridge(state=State("NoName", "L2147_9"), send_to_mjai=send_to_mjai)
    with pytest.raises(OSError, match="simulated network drop"):
        asyncio.run(bridge.run())
    assert calls == ["connect"]


def test_bridge_connect_kwargs_use_websockets_14_api(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CapturingConnection:
        async def __aenter__(self):
            raise OSError("stop after connect")

        async def __aexit__(self, *_args):
            return False

    def fake_connect(uri: str, **kwargs) -> CapturingConnection:
        captured["uri"] = uri
        captured["kwargs"] = kwargs
        return CapturingConnection()

    monkeypatch.setattr("gateway.tenhou_bridge.websockets.connect", fake_connect)

    async def send_to_mjai(_message):
        return {"type": "none"}

    bridge = TenhouBridge(state=State("NoName", "L2147_9"), send_to_mjai=send_to_mjai)
    with pytest.raises(OSError):
        asyncio.run(bridge.run())

    kwargs = captured["kwargs"]
    # websockets >= 14 renamed extra_headers -> additional_headers and moved
    # User-Agent into user_agent_header.
    assert "extra_headers" not in kwargs
    assert kwargs["origin"] == "https://tenhou.net"
    headers = kwargs["additional_headers"]
    assert "Accept-Encoding" in headers
    assert "User-Agent" not in headers
    assert kwargs["user_agent_header"] == bridge.config.user_agent
