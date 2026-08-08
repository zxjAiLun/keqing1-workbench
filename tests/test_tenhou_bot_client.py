from __future__ import annotations

from pathlib import Path
from unittest import mock

import gateway.tenhou_bot_client as tbc


class DummyBot:
    def __init__(self, player_id: int):
        self.player_id = player_id
        self.events = []
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def react(self, event: dict):
        self.events.append(event)
        if event.get("type") == "start_game":
            return None
        return {"type": "none", "actor": self.player_id}


def test_handle_hello_returns_gateway_handshake() -> None:
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(
            name="NoName",
            room="L2147",
            bot_name="rulebase",
            project_root=Path('.'),
        )
    )

    assert client.handle_message({"type": "hello"}) == {
        "type": "hello",
        "protocol": "mjsonp",
        "protocol_version": 3,
        "name": "NoName",
        "room": "L2147_9",
    }


def test_start_game_creates_runtime_bot_and_defaults_to_none(monkeypatch, tmp_path) -> None:
    created = {}
    fake_ckpt = tmp_path / "best.pth"
    fake_ckpt.write_bytes(b"")

    def fake_create_runtime_bot(**kwargs):
        created.update(kwargs)
        return DummyBot(kwargs["player_id"])

    monkeypatch.setattr(tbc, "create_runtime_bot_for_gateway", fake_create_runtime_bot)
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(
            name="bot-b",
            room="2147_0",
            bot_name="mortal",
            project_root=Path('/tmp/project'),
            model_path=fake_ckpt,
        )
    )

    response = client.handle_message({"type": "start_game", "id": 2, "names": []})

    assert created["player_id"] == 2
    assert created["bot_name"] == "mortal"
    assert response == {"type": "none", "actor": 2}


def test_same_seat_start_game_resets_existing_bot(monkeypatch, tmp_path) -> None:
    bot = DummyBot(1)
    fake_ckpt = tmp_path / "best.pth"
    fake_ckpt.write_bytes(b"")

    def fake_create_runtime_bot(**kwargs):
        return bot

    monkeypatch.setattr(tbc, "create_runtime_bot_for_gateway", fake_create_runtime_bot)
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(
            name="bot-c",
            room="2147_0",
            project_root=Path('.'),
            model_path=fake_ckpt,
        )
    )

    client.handle_message({"type": "start_game", "id": 1, "names": []})
    client.handle_message({"type": "start_game", "id": 1, "names": []})

    assert bot.reset_count == 1


def test_hidden_opponent_tsumo_is_buffered_until_discard(monkeypatch, tmp_path) -> None:
    fake_ckpt = tmp_path / "best.pth"
    fake_ckpt.write_bytes(b"")
    bot = DummyBot(0)

    def fake_create_runtime_bot(**kwargs):
        return bot

    monkeypatch.setattr(tbc, "create_runtime_bot_for_gateway", fake_create_runtime_bot)
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(name="NoName", room="L2147", model_path=fake_ckpt)
    )

    client.handle_message({"type": "start_game", "id": 0, "names": []})
    bot.events.clear()

    assert client.handle_message({"type": "tsumo", "actor": 1, "pai": "?"}) == {"type": "none"}
    assert bot.events == []

    response = client.handle_message(
        {"type": "dahai", "actor": 1, "pai": "4m", "tsumogiri": False}
    )

    assert [event["type"] for event in bot.events] == ["tsumo", "dahai"]
    assert bot.events[0]["pai"] == "4m"
    assert response == {"type": "none", "actor": 0}


def test_public_opponent_meld_marks_skip_hand_update(monkeypatch, tmp_path) -> None:
    fake_ckpt = tmp_path / "best.pth"
    fake_ckpt.write_bytes(b"")
    bot = DummyBot(0)

    def fake_create_runtime_bot(**kwargs):
        return bot

    monkeypatch.setattr(tbc, "create_runtime_bot_for_gateway", fake_create_runtime_bot)
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(name="NoName", room="L2147", model_path=fake_ckpt)
    )

    client.handle_message({"type": "start_game", "id": 0, "names": []})
    bot.events.clear()
    client.handle_message(
        {"type": "chi", "actor": 1, "target": 0, "pai": "6m", "consumed": ["5m", "7m"]}
    )

    assert bot.events[-1]["skip_hand_update"] is True


def test_public_opponent_post_meld_discard_marks_skip_hand_update(monkeypatch, tmp_path) -> None:
    fake_ckpt = tmp_path / "best.pth"
    fake_ckpt.write_bytes(b"")
    bot = DummyBot(0)

    def fake_create_runtime_bot(**kwargs):
        return bot

    monkeypatch.setattr(tbc, "create_runtime_bot_for_gateway", fake_create_runtime_bot)
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(name="NoName", room="L2147", model_path=fake_ckpt)
    )

    client.handle_message({"type": "start_game", "id": 0, "names": []})
    bot.events.clear()
    client.handle_message(
        {"type": "pon", "actor": 1, "target": 0, "pai": "8p", "consumed": ["8p", "8p"]}
    )
    client.handle_message({"type": "dahai", "actor": 1, "pai": "6m", "tsumogiri": False})

    assert bot.events[-1]["type"] == "dahai"
    assert bot.events[-1]["skip_hand_update"] is True


def test_run_does_not_reconnect_after_connection_failure(monkeypatch) -> None:
    client = tbc.GatewayBotClient(
        tbc.BotClientConfig(name="NoName", room="L2147", bot_name="rulebase")
    )
    calls = []
    monkeypatch.setattr(client, "_preload_once", lambda: calls.append("preload"))

    def fail_once(_stop_event):
        calls.append("serve")
        raise OSError("simulated socket drop")

    monkeypatch.setattr(client, "_serve_once", fail_once)
    client.run()

    assert calls == ["preload", "serve"]


def test_gateway_subprocess_receives_selected_port_and_owner_token(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return mock.Mock()

    monkeypatch.setattr(tbc.subprocess, "Popen", fake_popen)
    tbc.start_gateway_subprocess(
        project_root=tmp_path,
        port=19001,
        owner_token="playwithyou-gateway",
    )

    assert "--port" in captured["command"]
    assert captured["command"][captured["command"].index("--port") + 1] == "19001"
    assert captured["command"][-2:] == ["--owner-token", "playwithyou-gateway"]
