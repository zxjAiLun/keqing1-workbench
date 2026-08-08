from __future__ import annotations

from pathlib import Path
import json
import asyncio

import gateway.riichi_dev_client as rdc


class FakeObservation:
    def __init__(self, player_id: int = 2):
        self.player_id = player_id

    def to_dict(self):
        return {
            "actor": self.player_id,
            "hand": ["1m"] * 14,
            "melds": [[], [], [], []],
            "discards": [[], [], [], []],
            "scores": [25000, 25000, 25000, 25000],
            "dora_markers": ["1p"],
            "reached": [False, False, False, False],
        }

    def legal_actions(self):
        return []


class StubAgent(rdc.RiichiDevDecisionAgent):
    def __init__(self, action):
        self.action = action
        self.calls = []
        self.start_game_calls = []

    def start_game(self, seat):
        self.start_game_calls.append(seat)

    def select_action(self, message, seat):
        self.calls.append((message, seat))
        return dict(self.action)


class SequenceAgent(rdc.RiichiDevDecisionAgent):
    def __init__(self, actions):
        self._actions = iter(actions)
        self.calls = []

    def select_action(self, message, seat):
        self.calls.append((message, seat))
        return dict(next(self._actions))


def test_normalize_observation_state_reads_riichienv_meld_type_from_string() -> None:
    class FakeMeldType:
        def __str__(self):
            return "MeldType.Chi"

    class FakeMeld:
        meld_type = FakeMeldType()
        tiles = [0, 4, 8]
        called_tile = 4
        from_who = 0
        opened = True

    fake_obs = FakeObservation(player_id=1)
    normalized = rdc._normalize_observation_state(
        fake_obs,
        {
            "hand": ["1m"] * 13,
            "melds": [[], [FakeMeld()], [], []],
            "discards": [[], [], [], []],
            "scores": [25000, 25000, 25000, 25000],
            "dora_markers": ["1p"],
            "reached": [False, False, False, False],
        },
    )

    assert normalized["melds"][1] == [
        {"type": "chi", "pai": "2m", "consumed": ["1m", "3m"], "target": 0}
    ]


def test_client_tracks_seat_and_only_replies_to_request_action() -> None:
    agent = StubAgent({"type": "dahai", "pai": "3m", "actor": 1})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )

    assert client.handle_message({"type": "start_game", "id": 1}) is None
    assert client.seat == 1
    assert agent.start_game_calls == [1]
    assert client.handle_message({"type": "tsumo", "actor": 1, "pai": "4m"}) is None

    response = client.handle_message(
        {"type": "request_action", "possible_actions": [{"type": "none"}]}
    )

    assert response == {"type": "dahai", "pai": "3m", "actor": 1}
    assert agent.calls[0][1] == 1


def test_client_resets_agent_on_unexpected_new_start_game(caplog) -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )
    caplog.set_level("DEBUG", logger=rdc.logger.name)

    client.handle_message({"type": "start_game", "id": 1})
    client._request_seq = 5
    client.handle_message({"type": "start_game", "id": 3})

    assert agent.reset_calls == 1
    assert client.seat == 3
    assert client._request_seq == 0
    assert "start_game before end_game" in caplog.text


def test_client_resets_agent_on_kyoku_boundaries() -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )

    assert client.handle_message({"type": "start_kyoku"}) is None
    assert client.handle_message({"type": "end_kyoku"}) is None

    assert agent.reset_calls == 2


def test_client_kyoku_boundary_with_unconfirmed_action_is_not_warning(caplog) -> None:
    agent = StubAgent({"type": "none"})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )
    client._last_sent_action = {"type": "dahai", "actor": 0, "pai": "F", "tsumogiri": False}
    client._last_sent_request_seq = 81
    client._last_sent_wire_payload = '{"type":"dahai","actor":0,"pai":"F","tsumogiri":false}'
    caplog.set_level("WARNING", logger=rdc.logger.name)

    assert client.handle_message({"type": "end_kyoku"}) is None

    assert "before echoing previous action" not in caplog.text
    assert client._last_sent_action is None


def test_sanitize_none_keeps_actor_when_available() -> None:
    assert rdc._sanitize_action({"type": "none", "actor": 3}, actor_hint=3) == {
        "type": "none",
        "actor": 3,
    }


def test_sanitize_hora_keeps_tsumo_target() -> None:
    assert rdc._sanitize_action({"type": "hora", "actor": 1, "target": 1}, actor_hint=1) == {
        "type": "hora",
        "actor": 1,
        "target": 1,
    }


def test_sanitize_hora_keeps_ron_fields() -> None:
    assert rdc._sanitize_action(
        {"type": "hora", "actor": 1, "target": 3, "pai": "C"},
        actor_hint=1,
    ) == {
        "type": "hora",
        "actor": 1,
        "target": 3,
        "pai": "C",
    }


def test_sanitize_hora_does_not_invent_target() -> None:
    assert rdc._sanitize_action({"type": "hora", "actor": 1}, actor_hint=1) == {
        "type": "hora",
        "actor": 1,
    }


def test_sanitize_ryukyoku_drops_actor() -> None:
    assert rdc._sanitize_action({"type": "ryukyoku", "actor": 0}, actor_hint=0) == {
        "type": "ryukyoku"
    }


def test_sanitize_dahai_keeps_only_protocol_fields() -> None:
    assert rdc._sanitize_action(
        {"type": "dahai", "actor": 0, "pai": "3m", "tsumogiri": True, "foo": 1},
        actor_hint=0,
    ) == {
        "type": "dahai",
        "actor": 0,
        "pai": "3m",
        "tsumogiri": True,
    }


def test_sanitize_ankan_keeps_required_fields() -> None:
    assert rdc._sanitize_action(
        {"type": "ankan", "actor": 0, "consumed": ["F", "F", "F", "F"], "extra": 1},
        actor_hint=0,
    ) == {
        "type": "ankan",
        "actor": 0,
        "consumed": ["F", "F", "F", "F"],
        "pai": "F",
    }


def test_sanitize_chi_keeps_required_fields() -> None:
    assert rdc._sanitize_action(
        {"type": "chi", "actor": 0, "target": 3, "pai": "4p", "consumed": ["5p", "6p"], "x": 1},
        actor_hint=0,
    ) == {
        "type": "chi",
        "actor": 0,
        "target": 3,
        "pai": "4p",
        "consumed": ["5p", "6p"],
    }


def test_sanitize_pon_without_target_does_not_crash() -> None:
    assert rdc._sanitize_action(
        {"type": "pon", "actor": 0, "pai": "5sr", "consumed": ["5s", "5s"]},
        actor_hint=0,
    ) == {
        "type": "pon",
        "actor": 0,
        "pai": "5sr",
        "consumed": ["5s", "5s"],
    }


def test_action_to_mjai_dict_accepts_json_string() -> None:
    assert rdc._action_to_mjai_dict('{"type":"none"}') == {"type": "none"}


def test_action_to_mjai_wire_payload_minifies_dict() -> None:
    assert (
        rdc._action_to_mjai_wire_payload({"type": "dahai", "actor": 0, "pai": "9s"})
        == '{"type":"dahai","actor":0,"pai":"9s","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_infers_tsumogiri_from_latest_self_tsumo() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            {"type": "dahai", "actor": 0, "pai": "9s"},
            actor_hint=0,
            new_events=[{"type": "tsumo", "actor": 0, "pai": "9s"}],
        )
        == '{"type":"dahai","actor":0,"pai":"9s","tsumogiri":true}'
    )


def test_action_to_mjai_wire_payload_prefers_tsumogiri_for_drawn_duplicate_tile() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            {"type": "dahai", "actor": 0, "pai": "2s"},
            actor_hint=0,
            new_events=[{"type": "tsumo", "actor": 0, "pai": "2s"}],
            state={"hand": ["3m", "4m", "2s", "2s"]},
        )
        == '{"type":"dahai","actor":0,"pai":"2s","tsumogiri":true}'
    )


def test_action_to_mjai_wire_payload_preserves_explicit_tsumogiri() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            {"type": "dahai", "actor": 1, "pai": "9m", "tsumogiri": False},
            actor_hint=1,
            new_events=[{"type": "tsumo", "actor": 1, "pai": "P"}],
        )
        == '{"type":"dahai","actor":1,"pai":"9m","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_preserves_riichienv_dahai_string_without_context_completion() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":0,"pai":"9s"}',
        )
        == '{"type":"dahai","actor":0,"pai":"9s","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_preserves_riichienv_dahai_string_with_context() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":0,"pai":"9s"}',
            actor_hint=0,
            new_events=[{"type": "tsumo", "actor": 0, "pai": "9s"}],
        )
        == '{"type":"dahai","actor":0,"pai":"9s","tsumogiri":true}'
    )


def test_action_to_mjai_wire_payload_infers_reach_followup_tsumogiri_from_state() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":0,"pai":"P"}',
            actor_hint=0,
            new_events=[{"type": "reach", "actor": 0}],
            state={"hand": ["3m", "4p", "P"]},
        )
        == '{"type":"dahai","actor":0,"pai":"P","tsumogiri":true}'
    )


def test_action_to_mjai_wire_payload_does_not_mark_tedashi_when_different_from_latest_self_tsumo() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":0,"pai":"8m"}',
            actor_hint=0,
            new_events=[{"type": "tsumo", "actor": 0, "pai": "7p"}],
        )
        == '{"type":"dahai","actor":0,"pai":"8m","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_marks_false_after_call_without_draw() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":1,"pai":"3p"}',
            actor_hint=1,
            new_events=[
                {"type": "tsumo", "actor": 1, "pai": "3p"},
                {"type": "dahai", "actor": 1, "pai": "9m", "tsumogiri": False},
                {"type": "dahai", "actor": 0, "pai": "2p", "tsumogiri": True},
                {"type": "chi", "actor": 1, "target": 0, "pai": "2p", "consumed": ["3p", "4p"]},
            ],
        )
        == '{"type":"dahai","actor":1,"pai":"3p","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_marks_true_after_kan_rinshan_draw() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":2,"pai":"7s"}',
            actor_hint=2,
            new_events=[
                {"type": "kakan", "actor": 2, "pai": "7s", "consumed": ["7s"]},
                {"type": "tsumo", "actor": 2, "pai": "7s"},
            ],
        )
        == '{"type":"dahai","actor":2,"pai":"7s","tsumogiri":true}'
    )


def test_action_to_mjai_wire_payload_ignores_stale_self_tsumo_for_wire_shape() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":0,"pai":"2p"}',
            actor_hint=0,
            new_events=[
                {"type": "tsumo", "actor": 0, "pai": "2p"},
                {"type": "dahai", "actor": 0, "pai": "9s"},
                {"type": "tsumo", "actor": 0, "pai": "5s"},
            ],
        )
        == '{"type":"dahai","actor":0,"pai":"2p","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_completes_meld_target_from_latest_dahai() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"pon","actor":3,"pai":"C","consumed":["C","C"]}',
            actor_hint=3,
            new_events=[{"type": "dahai", "actor": 2, "pai": "C", "tsumogiri": False}],
        )
        == '{"type":"pon","actor":3,"pai":"C","consumed":["C","C"],"target":2}'
    )


def test_action_to_mjai_wire_payload_preserves_explicit_meld_target() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            {"type": "chi", "actor": 1, "target": 0, "pai": "2p", "consumed": ["3p", "4p"]},
            actor_hint=1,
        )
        == '{"type":"chi","actor":1,"pai":"2p","consumed":["3p","4p"],"target":0}'
    )


def test_action_to_mjai_wire_payload_missing_call_target_fails_closed(caplog) -> None:
    caplog.set_level("DEBUG", logger=rdc.logger.name)

    assert (
        rdc._action_to_mjai_wire_payload(
            {"type": "chi", "actor": 1, "pai": "2p", "consumed": ["3p", "4p"]},
            actor_hint=1,
            new_events=[],
        )
        == '{"type":"none"}'
    )
    assert "missing call target" in caplog.text


def test_action_to_mjai_wire_payload_preserves_explicit_hora_target() -> None:
    assert (
        rdc._action_to_mjai_wire_payload({"type": "hora", "actor": 2, "target": 0}, actor_hint=2)
        == '{"type":"hora","actor":2,"target":0}'
    )


def test_action_to_mjai_wire_payload_keeps_riichienv_ankan_shape_with_pai() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"ankan","actor":0,"consumed":["7s","7s","7s","7s"]}',
            actor_hint=0,
        )
        == '{"type":"ankan","actor":0,"consumed":["7s","7s","7s","7s"],"pai":"7s"}'
    )


def test_action_to_mjai_wire_payload_preserves_post_call_discard_wire_shape() -> None:
    assert (
        rdc._action_to_mjai_wire_payload(
            '{"type":"dahai","actor":3,"pai":"P"}',
            actor_hint=3,
            new_events=[
                {"type": "dahai", "actor": 2, "pai": "C", "tsumogiri": False},
                {"type": "pon", "actor": 3, "target": 2, "pai": "C", "consumed": ["C", "C"]},
            ],
        )
        == '{"type":"dahai","actor":3,"pai":"P","tsumogiri":false}'
    )


def test_action_to_mjai_wire_payload_strips_actor_from_none() -> None:
    assert (
        rdc._action_to_mjai_wire_payload({"type": "none", "actor": 0}, actor_hint=0)
        == '{"type":"none"}'
    )


def test_ws_url_override_takes_precedence() -> None:
    cfg = rdc.RiichiDevClientConfig(
        token="t",
        queue="validate",
        base_url="wss://riichi.dev",
        ws_url_override="wss://games.riichi.dev/ws/validate",
        model_path=Path("fake.pth"),
    )

    assert cfg.ws_url() == "wss://games.riichi.dev/ws/validate"


def test_resolve_ws_url_accepts_https_base_url() -> None:
    assert (
        rdc._resolve_ws_url("https://riichi.dev", "validate")
        == "wss://riichi.dev/ws/validate"
    )


def test_resolve_default_token_prefers_lattekey(monkeypatch) -> None:
    monkeypatch.setenv("LATTEKEY", "latte-token")

    assert rdc._resolve_default_token("mortal") == "latte-token"


def test_resolve_default_token_uses_lattekey_for_other_bots(monkeypatch) -> None:
    monkeypatch.setenv("LATTEKEY", "latte-token")
    assert rdc._resolve_default_token("rulebase") == "latte-token"


def test_resolve_default_token_with_source_prefers_lattekey(monkeypatch) -> None:
    monkeypatch.setenv("LATTEKEY", "latte-token")

    assert rdc._resolve_default_token_with_source("mortal") == ("latte-token", "LATTEKEY")


def test_resolve_model_path_uses_mortal_default_checkpoint() -> None:
    assert rdc._resolve_model_path(
        bot_name="mortal",
        project_root=Path("/tmp/project"),
        model_path=None,
    ) == Path("/tmp/project/artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth")


def test_decode_jwt_payload_unverified_reads_name_and_bot_id() -> None:
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJuYW1lIjoibW9jaGEiLCJ0eXBlIjoiYm90IiwiYm90X2lkIjoiYm90LTEyMyJ9."
        "sig"
    )

    payload = rdc._decode_jwt_payload_unverified(token)

    assert payload == {"name": "mocha", "type": "bot", "bot_id": "bot-123"}


def test_create_agent_supports_rulebase() -> None:
    agent = rdc.create_riichi_dev_agent(
        bot_name="rulebase",
        project_root=Path("."),
        model_path=None,
        device="cpu",
        verbose=False,
    )

    assert isinstance(agent, rdc.RulebaseObservationAgent)


def test_create_agent_supports_mortal(monkeypatch) -> None:
    created = {}

    class FakeMortalAgent:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(rdc, "MortalObservationAgent", FakeMortalAgent)
    agent = rdc.create_riichi_dev_agent(
        bot_name="mortal",
        project_root=Path("/tmp/project"),
        model_path=None,
        device="cpu",
        verbose=True,
    )

    assert isinstance(agent, FakeMortalAgent)
    assert created["model_path"] == Path("/tmp/project/artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth")
    assert created["project_root"] == Path("/tmp/project")
    assert created["device"] == "cpu"
    assert created["verbose"] is True


def test_local_game_returns_summary(monkeypatch) -> None:
    class FakeAction:
        def to_mjai(self):
            return {"type": "none"}

    class FakeObs:
        def __init__(self, pid):
            self.player_id = pid

        def legal_actions(self):
            return [FakeAction()] if self.player_id == 0 else []

    class FakeEnv:
        def __init__(self, game_mode=None, seed=None):
            self._done = False
            self.mjai_log = [{"type": "start_game"}, {"type": "end_game"}]

        def get_observations(self):
            return {i: FakeObs(i) for i in range(4)}

        def done(self):
            return self._done

        def step(self, action):
            self._done = True
            return {i: FakeObs(i) for i in range(4)}

        def scores(self):
            return [25000, 25000, 25000, 25000]

        def ranks(self):
            return [1, 2, 3, 4]

    class FakeAgent(rdc.RiichiDevDecisionAgent):
        def act(self, obs):
            return object()

        def select_action(self, message, seat):
            return {"type": "none"}

    monkeypatch.setattr(rdc, "RiichiEnv", FakeEnv)
    result = rdc.run_local_game(agent=FakeAgent(), game_mode=2, seed=42)

    assert result["scores"] == [25000, 25000, 25000, 25000]
    assert result["ranks"] == [1, 2, 3, 4]
    assert result["step_count"] == 1


def test_local_game_uses_agent_for_acting_seat(monkeypatch) -> None:
    class FakeAction:
        pass

    class FakeObs:
        def __init__(self, player_id, legal):
            self.player_id = player_id
            self._legal = legal

        def legal_actions(self):
            return [FakeAction()] if self._legal else []

    class FakeEnv:
        def __init__(self, game_mode=None, seed=None):
            self._done = False
            self.mjai_log = []

        def get_observations(self):
            return {1: FakeObs(1, False), 2: FakeObs(2, True)}

        def done(self):
            return self._done

        def step(self, actions):
            assert list(actions) == [2]
            self._done = True
            return {}

        def scores(self):
            return [25000, 25000, 25000, 25000]

        def ranks(self):
            return [1, 2, 3, 4]

    class FakeAgent(rdc.RiichiDevDecisionAgent):
        def __init__(self):
            self.act_calls = 0
            self.observe_calls = 0
            self.reset_calls = 0

        def reset(self):
            self.reset_calls += 1

        def observe(self, obs):
            self.observe_calls += 1

        def act(self, obs):
            self.act_calls += 1
            return FakeAction()

        def select_action(self, message, seat):
            return {"type": "none"}

    agents = {pid: FakeAgent() for pid in range(4)}
    monkeypatch.setattr(rdc, "RiichiEnv", FakeEnv)

    rdc.run_local_game(agents=agents, game_mode=2, seed=42)

    assert agents[2].act_calls == 1
    assert agents[1].observe_calls == 1
    assert agents[0].act_calls == 0
    assert all(agent.reset_calls == 1 for agent in agents.values())


def test_rulebase_agent_replies_with_pending_reach_discard(monkeypatch) -> None:
    fake_obs = FakeObservation(player_id=2)

    def fake_decode(message):
        assert message["observation"] == "encoded"
        snap = fake_obs.to_dict()
        snap["actor"] = 2
        return fake_obs, snap

    class FakeRulebaseModule:
        def __init__(self):
            self.calls = 0

        def choose_rulebase_action(self, snap, actor, legal_actions):
            self.calls += 1
            if any(action.get("type") == "reach" for action in legal_actions):
                return {"type": "reach", "actor": actor}
            return {"type": "dahai", "actor": actor, "pai": "7p", "tsumogiri": False}

    import sys
    import types

    fake_module = FakeRulebaseModule()
    monkeypatch.setattr(rdc, "_decode_observation", fake_decode)
    monkeypatch.setitem(
        sys.modules,
        "keqing_core",
        types.SimpleNamespace(choose_rulebase_action=fake_module.choose_rulebase_action),
    )

    agent = rdc.RulebaseObservationAgent()
    action = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [
                {"type": "reach", "actor": 2},
                {"type": "dahai", "actor": 2, "pai": "7p", "tsumogiri": False},
            ],
        },
        seat=2,
    )
    followup = agent.select_action({"type": "reach", "actor": 2}, seat=2)

    assert action == {"type": "reach", "actor": 2}
    assert followup == {"type": "dahai", "actor": 2, "pai": "7p", "tsumogiri": False}
    assert fake_module.calls == 2


def test_mortal_agent_syncs_observation_events_and_legalizes_action(monkeypatch) -> None:
    import sys
    import types

    seen = []

    class FakeMortalBot:
        def __init__(self, **kwargs):
            seen.append(("init", kwargs))

        def reset(self):
            seen.append(("reset", None))

        def react(self, event):
            seen.append(("event", dict(event)))
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False}
            return None

    class FakeAction:
        def __init__(self, payload):
            self.payload = payload

        def to_mjai(self):
            return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [
                {"type": "start_game", "id": 0},
                {"type": "start_kyoku"},
                {"type": "tsumo", "actor": 0, "pai": "5m"},
            ]

        events = [{"type": "fallback_events_should_not_be_used"}]

        def legal_actions(self):
            return [
                FakeAction(
                    {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False}
                )
            ]

        def select_action_from_mjai(self, payload):
            return json.loads(payload)

    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=True,
    )
    chosen = agent.act(FakeObs())

    assert chosen == {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False}
    assert seen[0][0] == "init"
    assert seen[0][1]["mortal_root"] == Path("/tmp/project/third_party/Mortal")
    assert seen[0][1]["enable_review_log"] is False
    assert [item[0] for item in seen if item[0] == "reset"] == ["reset"]
    assert [item[1]["type"] for item in seen if item[0] == "event"] == [
        "start_game",
        "start_kyoku",
        "tsumo",
    ]

    repeated = agent.act(FakeObs())
    assert repeated == {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False}
    assert [item[1]["type"] for item in seen if item[0] == "event"] == [
        "start_game",
        "start_kyoku",
        "tsumo",
        "start_game",
        "start_kyoku",
        "tsumo",
    ]
    assert [item[0] for item in seen if item[0] == "reset"] == ["reset", "reset"]


def test_mortal_agent_drops_stale_events_before_current_kyoku(monkeypatch, caplog) -> None:
    import sys
    import types

    seen = []

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            seen.append(("reset", None))

        def react(self, event):
            seen.append(("event", dict(event)))
            if event == {"type": "dahai", "actor": 1, "pai": "7s", "tsumogiri": True}:
                raise AssertionError("stale previous-kyoku dahai was fed into Mortal")
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 1, "pai": "6s", "tsumogiri": True}
            return None

    class FakeAction:
        def __init__(self, payload):
            self.payload = payload

        def to_mjai(self):
            return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    class FakeObs:
        player_id = 1

        def new_events(self):
            return [
                {"type": "dahai", "actor": 1, "pai": "7s", "tsumogiri": True},
                {"type": "tsumo", "actor": 2, "pai": "?"},
                {"type": "dahai", "actor": 2, "pai": "7s", "tsumogiri": False},
                {"type": "ryukyoku", "reason": "exhaustive_draw"},
                {"type": "end_kyoku"},
                {"type": "start_kyoku", "kyoku": 1, "honba": 1},
                {"type": "tsumo", "actor": 0, "pai": "?"},
                {"type": "dahai", "actor": 0, "pai": "S", "tsumogiri": False},
                {"type": "tsumo", "actor": 1, "pai": "6s"},
            ]

        def select_action_from_mjai(self, payload):
            parsed = json.loads(payload)
            assert parsed == {
                "type": "dahai",
                "actor": 1,
                "pai": "6s",
            }
            return FakeAction(parsed)

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 1}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    caplog.set_level("DEBUG", logger=rdc.logger.name)
    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )

    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 1, "pai": "6s"}],
        },
        seat=1,
    )

    assert response == '{"type":"dahai","actor":1,"pai":"6s"}'
    assert [item[1]["type"] for item in seen if item[0] == "event"] == [
        "start_kyoku",
        "tsumo",
        "dahai",
        "tsumo",
    ]
    assert [item[0] for item in seen if item[0] == "reset"] == ["reset"]
    assert "previous kyoku tail" in caplog.text
    assert "dropping 5 pre-start_kyoku events" in caplog.text


def test_mortal_agent_kyoku_tail_logs_debug_not_warning(caplog) -> None:
    caplog.set_level("DEBUG", logger=rdc.logger.name)

    events = [
        {"type": "hora", "actor": 0, "target": 3},
        {"type": "end_kyoku"},
        {"type": "start_kyoku", "kyoku": 2},
        {"type": "tsumo", "actor": 1, "pai": "6s"},
    ]

    assert rdc.MortalObservationAgent._current_kyoku_events(events) == events[2:]
    assert "dropping 2 pre-start_kyoku events" in caplog.text
    assert all(record.levelname != "WARNING" for record in caplog.records)


def test_mortal_agent_sync_runtime_error_falls_back_to_legal_action(monkeypatch, caplog) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            raise RuntimeError("native sync exploded")

    class FakeAction:
        def __init__(self, payload):
            self.payload = payload

        def to_mjai(self):
            return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    class FakeObs:
        player_id = 1

        def new_events(self):
            return [{"type": "tsumo", "actor": 1, "pai": "6s"}]

        def select_action_from_mjai(self, payload):
            parsed = json.loads(payload)
            assert parsed == {"type": "dahai", "actor": 1, "pai": "6s"}
            return FakeAction(parsed)

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 1}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    caplog.set_level("WARNING", logger=rdc.logger.name)
    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )

    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 1, "pai": "6s"}],
        },
        seat=1,
    )

    assert response == '{"type":"dahai","actor":1,"pai":"6s"}'
    assert "mortal observation sync failed" in caplog.text
    assert "native sync exploded" in caplog.text
    assert "mortal legality guard fallback" in caplog.text


def test_mortal_agent_select_action_sends_possible_action_wire_shape(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "9s", "tsumogiri": True}
            return None

    class FakeAction:
        def __init__(self, payload):
            self.payload = payload

        def to_mjai(self):
            return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "tsumo", "actor": 0, "pai": "9s"}]

        def select_action_from_mjai(self, payload):
            parsed = json.loads(payload)
            assert parsed == {"type": "dahai", "actor": 0, "pai": "9s"}
            return FakeAction(parsed)

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 0}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 0, "pai": "9s"}],
        },
        seat=0,
    )

    assert response == '{"type":"dahai","actor":0,"pai":"9s"}'


def test_mortal_agent_select_action_uses_platform_possible_action_wire_payload(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "1m", "tsumogiri": False}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":0,"pai":"1m","type":"dahai"}'

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "tsumo", "actor": 0, "pai": "5sr"}]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {"type": "dahai", "actor": 0, "pai": "1m"}
            return FakeAction()

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 0}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 0, "pai": "1m"}],
        },
        seat=0,
    )

    assert json.loads(response) == {
        "actor": 0,
        "pai": "1m",
        "type": "dahai",
    }


def test_mortal_agent_select_action_uses_riichienv_wire_for_unique_self_draw(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "N", "tsumogiri": True}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":0,"pai":"N","type":"dahai"}'

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "tsumo", "actor": 0, "pai": "N"}]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {"type": "dahai", "actor": 0, "pai": "N"}
            return FakeAction()

    monkeypatch.setattr(
        rdc,
        "_decode_observation",
        lambda message: (
            FakeObs(),
            {
                "actor": 0,
                "hand": [
                    "1m",
                    "1m",
                    "2m",
                    "5m",
                    "6m",
                    "7m",
                    "3p",
                    "4p",
                    "4s",
                    "5s",
                    "6s",
                    "8s",
                    "8s",
                    "N",
                ],
            },
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 0, "pai": "N"}],
        },
        seat=0,
    )

    assert json.loads(response) == {
        "actor": 0,
        "pai": "N",
        "type": "dahai",
    }


def test_mortal_agent_select_action_uses_riichienv_wire_for_duplicate_tile(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "1s", "tsumogiri": True}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":0,"pai":"1s","type":"dahai"}'

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "tsumo", "actor": 0, "pai": "1s"}]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {"type": "dahai", "actor": 0, "pai": "1s"}
            return FakeAction()

    monkeypatch.setattr(
        rdc,
        "_decode_observation",
        lambda message: (FakeObs(), {"actor": 0, "hand": ["1s", "1s", "2m"]}),
    )
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 0, "pai": "1s"}],
        },
        seat=0,
    )

    assert json.loads(response) == {
        "actor": 0,
        "pai": "1s",
        "type": "dahai",
    }


def test_mortal_agent_select_action_uses_riichienv_wire_for_red_five(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "tsumo":
                return {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": True}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":0,"pai":"5mr","type":"dahai"}'

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "tsumo", "actor": 0, "pai": "5mr"}]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {"type": "dahai", "actor": 0, "pai": "5mr"}
            return FakeAction()

    monkeypatch.setattr(
        rdc,
        "_decode_observation",
        lambda message: (FakeObs(), {"actor": 0, "hand": ["5mr", "2m", "3m"]}),
    )
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 0, "pai": "5mr"}],
        },
        seat=0,
    )

    assert json.loads(response) == {
        "actor": 0,
        "pai": "5mr",
        "type": "dahai",
    }


def test_mortal_agent_select_action_uses_riichienv_none_wire_on_call_pass(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            return None

    class FakeAction:
        def __init__(self, payload):
            self.payload = payload

        def to_mjai(self):
            return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    class FakeObs:
        player_id = 0

        def new_events(self):
            return [{"type": "dahai", "actor": 2, "pai": "1p"}]

        def select_action_from_mjai(self, payload):
            parsed = json.loads(payload)
            assert parsed == {"type": "none", "actor": 0}
            return FakeAction(parsed)

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 0}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [
                {"type": "pon", "actor": 0, "pai": "1p", "consumed": ["1p", "1p"]},
                {"type": "none", "actor": 0},
            ],
        },
        seat=0,
    )

    assert response == '{"type":"none","actor":0}'


def test_mortal_agent_select_action_uses_riichienv_pon_wire(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "dahai":
                return {"type": "pon", "actor": 3, "pai": "C", "consumed": ["C", "C"]}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":3,"consumed":["C","C"],"pai":"C","type":"pon"}'

    class FakeObs:
        player_id = 3

        def new_events(self):
            return [{"type": "dahai", "actor": 2, "pai": "C", "tsumogiri": False}]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {
                "type": "pon",
                "actor": 3,
                "pai": "C",
                "consumed": ["C", "C"],
            }
            return FakeAction()

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 3}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [
                {"type": "pon", "actor": 3, "pai": "C", "consumed": ["C", "C"]},
                {"type": "none", "actor": 3},
            ],
        },
        seat=3,
    )

    assert json.loads(response) == {
        "actor": 3,
        "consumed": ["C", "C"],
        "pai": "C",
        "type": "pon",
    }


def test_mortal_agent_select_action_completes_post_pon_discard_for_wire(monkeypatch) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "pon":
                return {"type": "dahai", "actor": 3, "pai": "P"}
            return None

    class FakeAction:
        def to_mjai(self):
            return '{"actor":3,"pai":"P","type":"dahai"}'

    class FakeObs:
        player_id = 3

        def new_events(self):
            return [
                {"type": "dahai", "actor": 2, "pai": "C", "tsumogiri": False},
                {"type": "pon", "actor": 3, "target": 2, "pai": "C", "consumed": ["C", "C"]},
            ]

        def select_action_from_mjai(self, payload):
            assert json.loads(payload) == {"type": "dahai", "actor": 3, "pai": "P"}
            return FakeAction()

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 3}))
    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )

    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )
    response = agent.select_action(
        {
            "type": "request_action",
            "observation": "encoded",
            "possible_actions": [{"type": "dahai", "actor": 3, "pai": "P"}],
        },
        seat=3,
    )

    assert json.loads(response) == {
        "actor": 3,
        "pai": "P",
        "type": "dahai",
    }


def test_mortal_agent_ignores_informational_reach_messages(monkeypatch) -> None:
    import sys
    import types

    seen = []

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            seen.append(dict(event))
            return {"type": "dahai", "actor": 0, "pai": "5m"}

    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )
    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )

    assert agent.select_action({"type": "reach", "actor": 0}, seat=0) == {"type": "none"}
    assert seen == []


def test_mortal_agent_does_not_reuse_stale_reaction(monkeypatch, caplog) -> None:
    import sys
    import types

    class FakeMortalBot:
        def __init__(self, **kwargs):
            pass

        def reset(self):
            pass

        def react(self, event):
            if event.get("type") == "dahai":
                return {"type": "hora", "actor": 2, "target": 0}
            return None

    monkeypatch.setitem(
        sys.modules,
        "inference.mortal_bot",
        types.SimpleNamespace(MortalReviewBot=FakeMortalBot),
    )
    agent = rdc.MortalObservationAgent(
        model_path=Path("mortal.pth"),
        project_root=Path("/tmp/project"),
        device="cpu",
        verbose=False,
    )

    chosen = agent.choose_mjai_action(
        new_events=[
            {"type": "dahai", "actor": 0, "pai": "5m"},
            {"type": "tsumo", "actor": 2, "pai": "7m"},
        ],
        legal_actions=[{"type": "dahai", "actor": 2, "pai": "7m"}, {"type": "none"}],
        actor=2,
    )

    assert chosen == {"type": "none", "actor": 2}
    assert "mortal legality guard fallback" not in caplog.text


def test_client_does_not_send_none_for_protocol_reach() -> None:
    agent = StubAgent({"type": "none"})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )
    client.seat = 1

    assert client.handle_message({"type": "reach", "actor": 1}) is None
    assert agent.calls == []


def test_mortal_legalize_warns_on_illegal_fallback(caplog) -> None:
    caplog.set_level("WARNING", logger=rdc.logger.name)

    fallback = rdc._legalize_action(
        {"type": "dahai", "actor": 0, "pai": "9m", "tsumogiri": False},
        legal_actions=[{"type": "none"}],
        actor=0,
    )

    assert fallback == {"type": "none", "actor": 0}
    assert "mortal legality guard fallback" in caplog.text
    assert "illegal_action" in caplog.text
    assert '"pai": "9m"' in caplog.text


def test_mortal_legalize_keeps_platform_dahai_wire_shape() -> None:
    chosen = rdc._legalize_action(
        {"type": "dahai", "actor": 3, "pai": "4p", "tsumogiri": False},
        legal_actions=[{"type": "dahai", "actor": 3, "pai": "4p"}],
        actor=3,
    )

    assert chosen == {"type": "dahai", "actor": 3, "pai": "4p"}


def test_echo_compare_ignores_dahai_tsumogiri_metadata_when_tile_matches() -> None:
    assert rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 0, "pai": "1s"},
        {"type": "dahai", "actor": 0, "pai": "1s", "tsumogiri": False},
    )
    assert rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 0, "pai": "1s"},
        {"type": "dahai", "actor": 0, "pai": "1s", "tsumogiri": True},
    )


def test_echo_compare_ignores_same_tile_tsumogiri_difference() -> None:
    assert rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": False},
        {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": True},
    )


def test_echo_compare_rejects_different_dahai_tile_even_with_tsumogiri() -> None:
    assert not rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 0, "pai": "2p", "tsumogiri": False},
        {"type": "dahai", "actor": 0, "pai": "3p", "tsumogiri": True},
    )


def test_echo_compare_treats_red_five_as_same_dahai_tile() -> None:
    assert rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 0, "pai": "5m"},
        {"type": "dahai", "actor": 0, "pai": "5mr", "tsumogiri": False},
    )
    assert rdc._actions_equivalent_for_echo(
        {"type": "dahai", "actor": 1, "pai": "5pr"},
        {"type": "dahai", "actor": 1, "pai": "5p", "tsumogiri": True},
    )


def test_echo_compare_accepts_alternate_legal_chi_consumed_shape() -> None:
    assert rdc._actions_equivalent_for_echo(
        {"type": "chi", "actor": 0, "pai": "4p", "consumed": ["5pr", "6p"], "target": 3},
        {"type": "chi", "actor": 0, "pai": "4p", "consumed": ["3p", "5pr"], "target": 3},
    )


def test_echo_compare_rejects_chi_with_different_target() -> None:
    assert not rdc._actions_equivalent_for_echo(
        {"type": "chi", "actor": 0, "pai": "4p", "consumed": ["5pr", "6p"], "target": 3},
        {"type": "chi", "actor": 0, "pai": "4p", "consumed": ["3p", "5pr"], "target": 2},
    )


def test_mortal_legalize_matches_red_five_dahai_to_platform_shape() -> None:
    chosen = rdc._legalize_action(
        {"type": "dahai", "actor": 0, "pai": "5m"},
        legal_actions=[
            {"type": "dahai", "actor": 0, "pai": "5mr"},
            {"type": "dahai", "actor": 0, "pai": "6m"},
        ],
        actor=0,
    )

    assert chosen == {"type": "dahai", "actor": 0, "pai": "5mr"}


def test_mortal_legalize_accepts_meld_when_only_target_differs(caplog) -> None:
    caplog.set_level("WARNING", logger=rdc.logger.name)

    legal = {"type": "pon", "actor": 2, "pai": "P", "consumed": ["P", "P"]}
    chosen = rdc._legalize_action(
        {"type": "pon", "actor": 2, "target": 1, "pai": "P", "consumed": ["P", "P"]},
        legal_actions=[legal, {"type": "none"}],
        actor=2,
    )

    assert chosen == legal
    assert "mortal legality guard fallback" not in caplog.text


def test_mortal_legalize_accepts_riichienv_hora_without_target(caplog) -> None:
    caplog.set_level("WARNING", logger=rdc.logger.name)

    legal = {"type": "hora", "actor": 2}
    chosen = rdc._legalize_action(
        {"type": "hora", "actor": 2, "target": 0},
        legal_actions=[legal, {"type": "none"}],
        actor=2,
    )

    assert chosen == legal
    assert "mortal legality guard fallback" not in caplog.text


def test_riichi_dev_client_leaves_rank_pt_lambda_unset_when_config_omits_override(monkeypatch) -> None:
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return StubAgent({"type": "none"})

    monkeypatch.setattr(rdc, "create_riichi_dev_agent", fake_create_agent)

    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            bot_name="mortal",
            model_path=Path("fake.pth"),
        )
    )

    assert isinstance(client.agent, StubAgent)
    assert captured["rank_pt_lambda"] is None


def test_riichi_dev_client_config_rank_pt_lambda_overrides_spec(monkeypatch) -> None:
    captured = {}

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return StubAgent({"type": "none"})

    monkeypatch.setattr(rdc, "create_riichi_dev_agent", fake_create_agent)

    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            bot_name="mortal",
            model_path=Path("fake.pth"),
            rank_pt_lambda=0.25,
        )
    )

    assert isinstance(client.agent, StubAgent)
    assert captured["rank_pt_lambda"] == 0.25


def test_audit_logger_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    logger = rdc.RiichiDevAuditLogger(log_path)
    logger.write({"kind": "event", "message": {"type": "start_game"}})

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["kind"] == "event"
    assert payload["message"]["type"] == "start_game"
    assert "ts" in payload


def test_enrich_message_for_audit_adds_observation_hash_without_state_on_decode_failure(monkeypatch) -> None:
    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (_ for _ in ()).throw(ValueError("bad obs")))

    enriched = rdc._enrich_message_for_audit({"type": "request_action", "observation": "encoded"})

    assert enriched["_observation_sha256"]
    assert "ValueError: bad obs" == enriched["_observation_decode_error"]
    assert "_normalized_observation" not in enriched


def test_enrich_message_for_audit_records_observation_new_events(monkeypatch) -> None:
    class FakeObs:
        def new_events(self):
            return ['{"type":"tsumo","actor":1,"pai":"P"}']

    monkeypatch.setattr(rdc, "_decode_observation", lambda message: (FakeObs(), {"actor": 1}))

    enriched = rdc._enrich_message_for_audit({"type": "request_action", "observation": "encoded"})

    assert enriched["_normalized_observation"] == {"actor": 1}
    assert enriched["_new_events"] == [{"type": "tsumo", "actor": 1, "pai": "P"}]


def test_client_logs_request_action(tmp_path: Path) -> None:
    agent = StubAgent({"type": "dahai", "pai": "3m", "actor": 1})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )
    client.handle_message({"type": "start_game", "id": 1})
    message = {
        "type": "request_action",
        "possible_actions": [{"type": "none"}],
        "_normalized_observation": {
            "actor": 1,
            "hand": ["1m"],
            "melds": [[], [], [], []],
            "discards": [[], [], [], []],
            "reached": [False, False, False, False],
            "dora_markers": [],
            "scores": [25000] * 4,
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": 0,
        },
    }
    response = client.handle_message(message)
    client.audit_logger.log_request_action(
        queue=client.config.queue,
        bot_name=client.config.bot_name,
        model_version=client.config.model_version,
        seat=client.seat,
        request_seq=7,
        message=message,
        response=response,
        latency_ms=12.34,
    )

    lines = (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["kind"] == "request_action"
    assert payload["response"] == {"type": "dahai", "pai": "3m", "actor": 1}
    assert payload["possible_actions"] == [{"type": "none"}]
    assert payload["possible_action_count"] == 1
    assert payload["request_seq"] == 7
    assert payload["message_meta"]["type"] == "request_action"
    assert payload["state"]["hand"] == ["1m"]
    assert payload["normalized_observation"]["actor"] == 1


def test_audit_logger_logs_send_result(tmp_path: Path) -> None:
    logger = rdc.RiichiDevAuditLogger(tmp_path / "riichi.jsonl")

    logger.log_send_result(
        queue="ranked",
        bot_name="mortal",
        model_version=None,
        seat=0,
        request_seq=11,
        response={"type": "dahai", "actor": 0, "pai": "S"},
        success=False,
        error="RuntimeError: socket closed",
    )

    payload = json.loads((tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["kind"] == "send_result"
    assert payload["request_seq"] == 11
    assert payload["success"] is False
    assert "socket closed" in payload["error"]


def test_client_logs_connection_diagnostics_and_disables_supported_proxy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple, dict]] = []

    class FakeWebSocket:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            calls.append((args, kwargs))
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setenv("HTTP_PROXY", "http://user:pass@127.0.0.1:7890")
    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_websockets_connect_supports_proxy_arg", lambda: True)

    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            disable_ws_proxy=True,
        ),
        agent=StubAgent({"type": "none"}),
    )

    asyncio.run(client._run_once())

    assert calls
    assert calls[0][1]["proxy"] is None
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    connection_start = [record for record in records if record["kind"] == "connection_start"][-1]
    diagnostics = connection_start["diagnostics"]
    assert diagnostics["proxy_arg_supported"] is True
    assert diagnostics["proxy_disabled_by_kwarg"] is True
    assert diagnostics["proxy_env_used_by_websockets"] is False
    assert diagnostics["proxy_env"]["HTTP_PROXY"] == "http://<redacted>@127.0.0.1:7890"


def test_client_websocket_connect_kwargs_uses_explicit_http_proxy(monkeypatch) -> None:
    fake_sock = object()
    calls: list[dict[str, object]] = []

    def fake_open_http_connect_socket(*, ws_url, proxy_url, timeout):
        calls.append({"ws_url": ws_url, "proxy_url": proxy_url, "timeout": timeout})
        return fake_sock

    monkeypatch.setattr(rdc, "_open_http_connect_socket", fake_open_http_connect_socket)
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            ws_proxy="http://127.0.0.1:7890",
            open_timeout=4.0,
        ),
        agent=StubAgent({"type": "none"}),
    )

    kwargs = client._websocket_connect_kwargs({"Authorization": "Bearer t"})

    assert kwargs["sock"] is fake_sock
    assert kwargs["server_hostname"] == "game.riichi.dev"
    assert calls == [
        {
            "ws_url": "wss://game.riichi.dev/ws/ranked",
            "proxy_url": "http://127.0.0.1:7890",
            "timeout": 4.0,
        }
    ]


def test_audit_logger_logs_protocol_action(tmp_path: Path) -> None:
    logger = rdc.RiichiDevAuditLogger(tmp_path / "riichi.jsonl")

    logger.log_protocol_action(
        queue="ranked",
        bot_name="mortal",
        model_version=None,
        seat=0,
        trigger_message={"type": "reach", "actor": 0},
        response={"type": "dahai", "actor": 0, "pai": "4m", "tsumogiri": False},
        latency_ms=1.23,
    )

    payload = json.loads((tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["kind"] == "protocol_action"
    assert payload["trigger_type"] == "reach"
    assert payload["response"]["pai"] == "4m"


def test_client_ignores_reach_event_without_agent_call() -> None:
    class ReachFollowupAgent(rdc.RiichiDevDecisionAgent):
        def __init__(self):
            self.calls = []

        def select_action(self, message, seat):
            self.calls.append((message, seat))
            if message.get("type") == "reach":
                return {"type": "dahai", "actor": seat, "pai": "4m", "tsumogiri": False}
            return {"type": "reach", "actor": seat}

    agent = ReachFollowupAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(token="t", model_path=Path("fake.pth")),
        agent=agent,
    )

    client.handle_message({"type": "start_game", "id": 1})
    response = client.handle_message({"type": "reach", "actor": 1})

    assert response is None
    assert agent.calls == []


def test_validation_safe_agent_prefers_tsumogiri_when_legal() -> None:
    agent = rdc.ValidationSafeAgent()
    agent._last_tsumo = "5m"
    action = agent.select_action(
        {
            "type": "request_action",
            "possible_actions": [
                {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": True},
                {"type": "none"},
            ],
        },
        seat=0,
    )

    assert action == {"type": "dahai", "actor": 0, "pai": "5m", "tsumogiri": True}


def test_validation_safe_agent_falls_back_to_none() -> None:
    agent = rdc.ValidationSafeAgent()
    action = agent.select_action(
        {"type": "request_action", "possible_actions": [{"type": "none"}]},
        seat=0,
    )

    assert action == {"type": "none", "actor": 0}


def test_disconnect_message_mentions_validate_queue(tmp_path: Path) -> None:
    message = rdc._format_connection_closed_message("validate", 1005, None)

    assert "validate connection closed by server" in message
    assert "validate queue request" in message


def test_log_startup_self_check_rejects_missing_model_path(caplog) -> None:
    try:
        rdc._log_startup_self_check(
            queue="validate",
            bot_name="mortal",
            model_version=None,
            token=(
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJuYW1lIjoibW9jaGEiLCJ0eXBlIjoiYm90IiwiYm90X2lkIjoiYm90LTEyMyJ9."
                "sig"
            ),
            token_source="LATTEKEY",
            project_root=Path("/tmp/project"),
            model_path=Path("/tmp/project/missing.pth"),
            validation_safe=False,
        )
    except SystemExit as exc:
        assert "model checkpoint not found" in str(exc)
    else:
        raise AssertionError("expected missing model checkpoint to stop startup")


def test_log_startup_self_check_is_silent_without_debug(caplog) -> None:
    rdc._log_startup_self_check(
        queue="validate",
        bot_name="mortal",
        model_version=None,
        token=(
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJuYW1lIjoibW9jaGEiLCJ0eXBlIjoiYm90IiwiYm90X2lkIjoiYm90LTEyMyJ9."
            "sig"
        ),
        token_source="LATTEKEY",
        project_root=Path("."),
        model_path=None,
        validation_safe=True,
    )

    assert "startup self-check" not in caplog.text


def test_client_auto_reconnects_after_end_game_1005_and_resets_agent(tmp_path: Path) -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1

    class FakeConnectionClosed(Exception):
        def __init__(self, code: int, reason: str | None):
            self.code = code
            self.reason = reason

    class FakeWebSocket:
        def __init__(self, messages):
            self._messages = iter(messages)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            return None

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return self

        async def __aenter__(self):
            return FakeWebSocket(['{"type":"start_game","id":0}', '{"type":"end_game"}'])

        async def __aexit__(self, exc_type, exc, tb):
            if self.calls == 1:
                raise FakeConnectionClosed(1005, None)
            raise StopAsyncIteration

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    original_connect = rdc.websockets.connect
    original_connection_closed = rdc.ConnectionClosed
    original_sleep = rdc.asyncio.sleep
    rdc.websockets.connect = fake_connect
    rdc.ConnectionClosed = FakeConnectionClosed
    rdc.asyncio.sleep = lambda _: original_sleep(0)
    try:
        try:
            asyncio.run(client.run())
        except StopAsyncIteration:
            pass
    finally:
        rdc.websockets.connect = original_connect
        rdc.ConnectionClosed = original_connection_closed
        rdc.asyncio.sleep = original_sleep

    assert fake_connect.calls == 2
    assert agent.reset_calls == 2


def test_client_checks_request_action_state_before_send_and_audits_after(monkeypatch, tmp_path: Path) -> None:
    order: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","possible_actions":[{"type":"none"}]}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            order.append(f"send:{payload}")

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def fake_enrich(message):
        order.append(f"enrich:{message.get('type')}")
        return dict(message)

    agent = StubAgent({"type": "none"})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    asyncio.run(client._run_once())

    send_index = order.index('send:{"type":"none"}')
    request_enrich_indexes = [
        idx for idx, item in enumerate(order) if item == "enrich:request_action"
    ]
    assert request_enrich_indexes[0] < send_index
    assert send_index < request_enrich_indexes[-1]


def test_client_send_result_records_send_and_total_latency(monkeypatch, tmp_path: Path) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                ['{"type":"request_action","possible_actions":[{"type":"none"}]}']
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    agent = StubAgent({"type": "none"})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())

    asyncio.run(client._run_once())

    assert sent == ['{"type":"none"}']
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    send_result = [record for record in records if record["kind"] == "send_result"][-1]
    assert send_result["success"] is True
    assert send_result["decision_latency_ms"] is not None
    assert send_result["send_latency_ms"] is not None
    assert send_result["total_latency_ms"] is not None
    assert send_result["deadline_ms"] == rdc.DEFAULT_ACTION_DEADLINE_MS


def test_client_preserves_riichienv_dahai_wire_payload_from_request_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sent: list[str] = []

    class StringAgent(rdc.RiichiDevDecisionAgent):
        def select_action(self, message, seat):
            return '{"type":"dahai","actor":0,"pai":"7p"}'

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","marker":"drawn"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "drawn":
            enriched["_new_events"] = [{"type": "tsumo", "actor": 0, "pai": "7p"}]
        else:
            enriched["_new_events"] = []
        return enriched

    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=StringAgent(),
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    asyncio.run(client._run_once())

    assert sent == ['{"type":"dahai","actor":0,"pai":"7p","tsumogiri":true}']


def test_client_drops_late_request_action_before_send(monkeypatch, tmp_path: Path, caplog) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                ['{"type":"request_action","possible_actions":[{"type":"none"}]}']
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    times = iter([0.0, 3.0, 3.0])
    agent = StubAgent({"type": "none"})
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            action_deadline_ms=2500.0,
        ),
        agent=agent,
    )

    caplog.set_level("WARNING", logger=rdc.logger.name)
    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc.time, "perf_counter", lambda: next(times))

    try:
        asyncio.run(client._run_once())
    except rdc.RiichiDevStaleActionDeadline:
        pass
    else:
        raise AssertionError("expected stale action deadline")

    assert sent == []
    assert "dropping late riichi.dev response" in caplog.text
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    send_result = [record for record in records if record["kind"] == "send_result"][-1]
    assert send_result["success"] is False
    assert "not sending stale action" in send_result["error"]


def test_client_aborts_echo_mismatch_before_sending_next_action(
    monkeypatch,
    tmp_path: Path,
    caplog,
) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"mismatch"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "mismatch":
            enriched["_new_events"] = [
                {"type": "dahai", "actor": 0, "pai": "3p", "tsumogiri": True}
            ]
        else:
            enriched["_new_events"] = []
        return enriched

    agent = SequenceAgent(
        [
            {"type": "dahai", "actor": 0, "pai": "3s", "tsumogiri": False},
            {"type": "none"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    caplog.set_level("ERROR", logger=rdc.logger.name)
    try:
        asyncio.run(client._run_once())
    except rdc.RiichiDevActionEchoMismatch:
        pass
    else:
        raise AssertionError("expected echo mismatch to abort before the next send")

    assert sent == ['{"type":"dahai","actor":0,"pai":"3s","tsumogiri":false}']
    assert "timeout/disconnect fallback" in caplog.text
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mismatch = [record for record in records if record["kind"] == "echo_mismatch"][-1]
    assert mismatch["last_request_seq"] == 1
    assert mismatch["request_seq"] == 2
    assert mismatch["expected"] == {
        "type": "dahai",
        "actor": 0,
        "pai": "3s",
        "tsumogiri": False,
    }
    assert mismatch["observed"] == {
        "type": "dahai",
        "actor": 0,
        "pai": "3p",
        "tsumogiri": True,
    }
    assert mismatch["classification"] == "server_timeout_or_disconnect_fallback"


def test_client_does_not_reconnect_active_game_echo_mismatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sent: list[str] = []
    sleeps: list[float] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"mismatch"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "mismatch":
            enriched["_new_events"] = [
                {"type": "dahai", "actor": 0, "pai": "3p", "tsumogiri": True}
            ]
        else:
            enriched["_new_events"] = []
        return enriched

    async def fake_sleep(delay):
        sleeps.append(delay)

    agent = SequenceAgent(
        [
            {"type": "dahai", "actor": 0, "pai": "3s", "tsumogiri": False},
            {"type": "none"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    monkeypatch.setattr(rdc.websockets, "connect", fake_connect)
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)
    monkeypatch.setattr(rdc.asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(client.run())
    except SystemExit as exc:
        assert "timeout/disconnect fallback" in str(exc)
    else:
        raise AssertionError("expected active game echo mismatch to exit")

    assert fake_connect.calls == 1
    assert sleeps == []
    assert sent == ['{"type":"dahai","actor":0,"pai":"3s","tsumogiri":false}']
    assert len(agent.calls) == 1


def test_client_clears_matching_echo_and_continues(monkeypatch, tmp_path: Path) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"match"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "match":
            enriched["_new_events"] = [
                {"type": "dahai", "actor": 0, "pai": "3s", "tsumogiri": False}
            ]
        else:
            enriched["_new_events"] = []
        return enriched

    agent = SequenceAgent(
        [
            {"type": "dahai", "actor": 0, "pai": "3s", "tsumogiri": False},
            {"type": "none"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    asyncio.run(client._run_once())

    assert sent == [
        '{"type":"dahai","actor":0,"pai":"3s","tsumogiri":false}',
        '{"type":"none"}',
    ]
    assert client._last_sent_action is None


def test_server_illegal_action_event_detects_penalized_result() -> None:
    penalized_event = {
        "type": "ryukyoku",
        "deltas": [4000, -12000, 4000, 4000],
        "penalized": [False, True, False, False],
    }

    assert rdc._server_illegal_action_event([penalized_event]) == penalized_event


def test_server_illegal_action_event_filters_by_seat() -> None:
    illegal_event = {
        "type": "ryukyoku",
        "reason": "Error: Illegal Action by Player 1",
        "deltas": [4000, -8000, 2000, 2000],
    }

    assert rdc._server_illegal_action_event([illegal_event], seat=0) is None
    assert rdc._server_illegal_action_event([illegal_event], seat=1) == illegal_event


def test_client_aborts_when_server_reports_illegal_action(monkeypatch, tmp_path: Path) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":1}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"illegal"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    illegal_event = {
        "type": "ryukyoku",
        "reason": "Error: Illegal Action by Player 1",
        "deltas": [4000, -12000, 4000, 4000],
    }

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "illegal":
            enriched["_new_events"] = [illegal_event]
        else:
            enriched["_new_events"] = []
        return enriched

    agent = SequenceAgent(
        [
            {"type": "none"},
            {"type": "dahai", "actor": 1, "pai": "1m"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    try:
        asyncio.run(client._run_once())
    except rdc.RiichiDevServerIllegalAction:
        pass
    else:
        raise AssertionError("expected server illegal action abort")

    assert sent == ['{"type":"none"}']
    assert len(agent.calls) == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    state_error = [record for record in records if record["kind"] == "server_illegal_action"][-1]
    assert state_error["seat"] == 1
    assert state_error["request_seq"] == 2
    assert state_error["illegal_event"] == illegal_event


def test_client_continues_when_other_player_is_penalized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":0}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"other_illegal"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    other_illegal_event = {
        "type": "ryukyoku",
        "reason": "Error: Illegal Action by Player 1",
        "deltas": [4000, -8000, 2000, 2000],
    }

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "other_illegal":
            enriched["_new_events"] = [
                other_illegal_event,
                {"type": "end_kyoku"},
                {"type": "start_kyoku"},
            ]
        else:
            enriched["_new_events"] = []
        return enriched

    agent = SequenceAgent(
        [
            {"type": "none"},
            {"type": "dahai", "actor": 0, "pai": "1m"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    asyncio.run(client._run_once())

    assert sent == [
        '{"type":"none"}',
        '{"type":"dahai","actor":0,"pai":"1m","tsumogiri":false}',
    ]
    assert len(agent.calls) == 2


def test_client_does_not_reconnect_active_game_state_error(monkeypatch, tmp_path: Path) -> None:
    sent: list[str] = []
    sleeps: list[float] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":1}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"request_action","marker":"illegal"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    illegal_event = {
        "type": "ryukyoku",
        "reason": "Error: Illegal Action by Player 1",
    }

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "illegal":
            enriched["_new_events"] = [illegal_event]
        else:
            enriched["_new_events"] = []
        return enriched

    async def fake_sleep(delay):
        sleeps.append(delay)

    agent = SequenceAgent(
        [
            {"type": "none"},
            {"type": "dahai", "actor": 1, "pai": "1m"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    monkeypatch.setattr(rdc.websockets, "connect", fake_connect)
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)
    monkeypatch.setattr(rdc.asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(client.run())
    except SystemExit as exc:
        assert "riichi.dev reported an illegal action" in str(exc)
    else:
        raise AssertionError("expected active game state error to exit")

    assert fake_connect.calls == 1
    assert sleeps == []
    assert sent == ['{"type":"none"}']
    assert len(agent.calls) == 1


def test_client_aborts_server_illegal_after_end_kyoku_cleared_echo(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sent: list[str] = []

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(
                [
                    '{"type":"start_game","id":1}',
                    '{"type":"request_action","marker":"first"}',
                    '{"type":"end_kyoku"}',
                    '{"type":"request_action","marker":"illegal"}',
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            sent.append(payload)

    class FakeConnect:
        def __call__(self, *args, **kwargs):
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    illegal_event = {
        "type": "ryukyoku",
        "reason": "Error: Illegal Action by Player 1",
    }

    def fake_enrich(message):
        enriched = dict(message)
        if message.get("marker") == "illegal":
            enriched["_new_events"] = [
                illegal_event,
                {"type": "end_kyoku"},
                {"type": "start_kyoku"},
            ]
        else:
            enriched["_new_events"] = []
        return enriched

    agent = SequenceAgent(
        [
            {"type": "dahai", "actor": 1, "pai": "4s"},
            {"type": "dahai", "actor": 1, "pai": "1m"},
        ]
    )
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
        ),
        agent=agent,
    )

    monkeypatch.setattr(rdc.websockets, "connect", FakeConnect())
    monkeypatch.setattr(rdc, "_enrich_message_for_audit", fake_enrich)

    try:
        asyncio.run(client._run_once())
    except rdc.RiichiDevServerIllegalAction:
        pass
    else:
        raise AssertionError("expected server illegal action abort")

    assert sent == ['{"type":"dahai","actor":1,"pai":"4s","tsumogiri":false}']
    assert len(agent.calls) == 1
    records = [
        json.loads(line)
        for line in (tmp_path / "riichi.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    state_error = [record for record in records if record["kind"] == "server_illegal_action"][-1]
    assert state_error["request_seq"] == 2
    assert state_error["illegal_event"] == illegal_event


def test_client_does_not_reconnect_active_game_disconnect(tmp_path: Path) -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1

    class FakeConnectionClosed(Exception):
        def __init__(self, code: int, reason: str | None):
            self.code = code
            self.reason = reason

    class FakeWebSocket:
        def __init__(self):
            self._messages = iter(['{"type":"start_game","id":0}'])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            return None

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return self

        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, exc_type, exc, tb):
            raise FakeConnectionClosed(1006, None)

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    original_connect = rdc.websockets.connect
    original_connection_closed = rdc.ConnectionClosed
    rdc.websockets.connect = fake_connect
    rdc.ConnectionClosed = FakeConnectionClosed
    try:
        try:
            asyncio.run(client.run())
        except SystemExit as exc:
            assert "ranked connection closed by server" in str(exc)
        else:
            raise AssertionError("expected active game disconnect to exit")
    finally:
        rdc.websockets.connect = original_connect
        rdc.ConnectionClosed = original_connection_closed

    assert fake_connect.calls == 1
    assert agent.reset_calls == 1


def test_client_auto_reconnects_ranked_queue_wait_1006_before_start_game(tmp_path: Path) -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1

    class FakeConnectionClosed(Exception):
        def __init__(self, code: int, reason: str | None):
            self.code = code
            self.reason = reason

    class FakeWebSocket:
        def __init__(self, messages):
            self._messages = iter(messages)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                raise StopAsyncIteration

        async def send(self, payload):
            return None

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            return self

        async def __aenter__(self):
            return FakeWebSocket([])

        async def __aexit__(self, exc_type, exc, tb):
            if self.calls == 1:
                raise FakeConnectionClosed(1006, None)
            raise StopAsyncIteration

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    original_connect = rdc.websockets.connect
    original_connection_closed = rdc.ConnectionClosed
    original_sleep = rdc.asyncio.sleep
    rdc.websockets.connect = fake_connect
    rdc.ConnectionClosed = FakeConnectionClosed
    rdc.asyncio.sleep = lambda _: original_sleep(0)
    try:
        try:
            asyncio.run(client.run())
        except StopAsyncIteration:
            pass
    finally:
        rdc.websockets.connect = original_connect
        rdc.ConnectionClosed = original_connection_closed
        rdc.asyncio.sleep = original_sleep

    assert fake_connect.calls == 2
    assert agent.reset_calls == 2


def test_client_auto_reconnects_ranked_open_timeout_before_start_game(tmp_path: Path) -> None:
    class ResetAwareAgent(StubAgent):
        def __init__(self):
            super().__init__({"type": "none"})
            self.reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1

    class FakeConnect:
        def __init__(self):
            self.calls = 0

        def __call__(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError
            raise StopAsyncIteration

    agent = ResetAwareAgent()
    client = rdc.RiichiDevBotClient(
        rdc.RiichiDevClientConfig(
            token="t",
            queue="ranked",
            model_path=Path("fake.pth"),
            audit_log_path=tmp_path / "riichi.jsonl",
            reconnect_delay_sec=0.0,
        ),
        agent=agent,
    )

    fake_connect = FakeConnect()
    original_connect = rdc.websockets.connect
    original_sleep = rdc.asyncio.sleep
    rdc.websockets.connect = fake_connect
    rdc.asyncio.sleep = lambda _: original_sleep(0)
    try:
        try:
            asyncio.run(client.run())
        except StopAsyncIteration:
            pass
    finally:
        rdc.websockets.connect = original_connect
        rdc.asyncio.sleep = original_sleep

    assert fake_connect.calls == 2
    assert agent.reset_calls == 2
