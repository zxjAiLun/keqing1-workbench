"""Regression tests for the play-with-you opening sequence.

These guard the exact fixes that stopped all three AIs from dropping at game
start (see issue: "开局后三个ai立即全部掉线"):

  1. Taikyoku must emit start_game with `names` of length 4. The native
     libriichi Bot requires a 4-element names array; an empty list crashes
     every bot simultaneously at game start.
  2. Init must emit start_kyoku with kyoku 1-indexed (BoundedU8<1,4>) and raw
     point totals (e.g. 25000), not the 0-indexed seed value or *100.

The responder module is pure Python (no torch), so this runs without the model.
"""
from __future__ import annotations

from gateway import responder
from gateway.utils.state import State


def _run(process, state, message):
    sent = []
    received = {}

    async def send_to_mjai(event):
        sent.append(event)
        return received

    async def send_to_tenhou(event):
        return None

    import asyncio

    asyncio.run(process(state, message, send_to_tenhou, send_to_mjai))
    return sent


def test_un_populates_all_four_names_and_seat():
    state = State(name="NoName-2", room="L2147_9")
    msg = {
        "tag": "UN",
        "n0": "NoName-0",
        "n1": "NoName-1",
        "n2": "NoName-2",
        "n3": "NoName-3",
    }
    _run(responder.UN().process, state, msg)
    assert state.names == ["NoName-0", "NoName-1", "NoName-2", "NoName-3"]
    assert state.seat == 2


def test_un_with_duplicate_noname_keeps_first_local_seat():
    state = State(name="NoName", room="L2147_9")
    _run(
        responder.UN().process,
        state,
        {"tag": "UN", "n0": "NoName", "n1": "NoName", "n2": "NoName", "n3": "NoName"},
    )
    assert state.names == ["NoName", "NoName", "NoName", "NoName"]
    assert state.seat == 0


def test_taikyoku_start_game_carries_four_names():
    state = State(name="NoName-0", room="L2147_9")
    # Simulate UN having filled all 4 names.
    state.names = ["NoName-0", "NoName-1", "NoName-2", "NoName-3"]
    state.seat = 0
    msg = {"tag": "TAIKYOKU", "oya": "0", "log": "12345678"}

    sent = _run(responder.Taikyoku().process, state, msg)

    assert len(sent) == 1
    start_game = sent[0]
    assert start_game["type"] == "start_game"
    assert start_game["id"] == 0
    # THE fix: names must be a length-4 array, otherwise the native Bot raises
    # "invalid length 0, expected an array of length 4" and every bot drops.
    assert isinstance(start_game["names"], list)
    assert len(start_game["names"]) == 4
    assert start_game["names"] == ["NoName-0", "NoName-1", "NoName-2", "NoName-3"]


def test_taikyoku_start_game_exposes_tenhou_log_seat():
    """C44：bridge start_game 显式携带 tenhou_log_seat（与 URL tw 一致）。"""
    state = State(name="NoName-0", room="L2147_9")
    state.names = ["NoName-0", "NoName-1", "NoName-2", "NoName-3"]
    state.seat = 0
    # oya=2（本地视角庄家） -> 全局 seat = (4 - 2) % 4 = 2
    msg = {"tag": "TAIKYOKU", "oya": "2", "log": "12345678"}

    sent = _run(responder.Taikyoku().process, state, msg)
    start_game = sent[0]
    assert start_game["tenhou_log_seat"] == 2
    assert "&tw=2" in start_game["log"]


def test_taikyoku_start_game_falls_back_when_names_missing():
    state = State(name="NoName-0", room="L2147_9")
    # No names resolved yet (e.g. UN race); must still be length 4, not [].
    msg = {"tag": "TAIKYOKU", "oya": "0"}
    sent = _run(responder.Taikyoku().process, state, msg)
    assert len(sent[0]["names"]) == 4


def test_init_start_kyoku_kyoku_one_indexed_and_raw_scores():
    state = State(name="NoName-0", room="L2147_9")
    state.seat = 0
    # seed[0] is the 0-indexed round; Mortal expects 1-indexed kyoku.
    # seed[5] is the dora tile index. Live Tenhou `ten` uses 100-point units.
    msg = {
        "tag": "INIT",
        "oya": "0",
        "hai": "0,4,8,12,17,20,24,28,32,36,40,44,48",  # 13 distinct tiles
        "seed": "0,0,0,0,0,51",
        "ten": "250,250,250,250",
    }
    sent = _run(responder.Init().process, state, msg)

    assert len(sent) == 1
    sk = sent[0]
    assert sk["type"] == "start_kyoku"
    # THE fix: kyoku must be 1..4, not the raw 0-indexed seed value.
    assert sk["kyoku"] == 1
    assert 1 <= sk["kyoku"] <= 4
    # The mjai event gets raw totals after converting Tenhou's hundred units.
    assert sk["scores"] == [25000, 25000, 25000, 25000]
    # dora_marker must be a valid tile string.
    assert sk["dora_marker"] == "4p"
    # Dealer's 13 tiles decoded correctly.
    assert sk["tehais"][0] == [
        "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
        "1p", "2p", "3p", "4p",
    ]


def test_init_start_kyoku_seed_round_three_maps_to_kyoku_four():
    state = State(name="NoName-0", room="L2147_9")
    state.seat = 0
    msg = {
        "tag": "INIT",
        "oya": "0",
        "hai": "0,4,8,12,17,20,24,28,32,36,40,44,48",
        "seed": "3,0,0,0,0,51",
        "ten": "250,250,250,250",
    }
    sent = _run(responder.Init().process, state, msg)
    assert sent[0]["kyoku"] == 4  # (3 % 4) + 1 == 4


def test_init_preserves_already_normalised_score_input():
    state = State(name="NoName", room="L2147_9")
    msg = {
        "tag": "INIT",
        "oya": "0",
        "hai": "0,4,8,12,17,20,24,28,32,36,40,44,48",
        "seed": "0,0,0,0,0,51",
        "ten": "25000,25000,25000,25000",
    }
    sent = _run(responder.Init().process, state, msg)
    assert sent[0]["scores"] == [25000, 25000, 25000, 25000]


def test_reach_step_two_converts_tenhou_hundred_point_scores():
    state = State(name="NoName", room="L2147_9")
    sent = _run(
        responder.ReachStep2().process,
        state,
        {"tag": "REACH", "step": "2", "who": "1", "ten": "240,260,250,250"},
    )
    assert sent == [
        {
            "type": "reach_accepted",
            "actor": 1,
            "deltas": [0, -1000, 0, 0],
            "scores": [24000, 26000, 25000, 25000],
        }
    ]


def test_agari_carries_native_mortal_hora_fields():
    state = State(name="NoName", room="L2147_9")
    sent = _run(
        responder.Agari().process,
        state,
        {
            "tag": "AGARI",
            "who": "2",
            "fromWho": "1",
            "machi": "52",  # red 5p
            "sc": "250,0,240,-10,260,30,250,-20",
        },
    )
    assert sent == [
        {
            "type": "hora",
            "actor": 2,
            "target": 1,
            "pai": "5pr",
            "scores": [25000, 23000, 29000, 23000],
        },
        {"type": "end_kyoku"},
    ]


def test_tsumo_unavailable_reach_falls_back_to_tsumogiri():
    state = State(name="NoName", room="L2147_9")
    # 13 tiles before the T52 draw; T52 is the red 5p draw.
    state.hand = [0, 4, 8, 12, 17, 20, 24, 28, 32, 36, 40, 44, 48]
    state.live_wall = 70
    sent_to_tenhou = []

    async def send_to_mjai(_event):
        # gui_mortal can choose reach despite Tenhou having sent t=0.
        return {"type": "reach", "actor": 0}

    async def send_to_tenhou(event):
        sent_to_tenhou.append(event)

    import asyncio

    asyncio.run(
        responder.Tsumo().process(
            state,
            {"tag": "T52", "t": "0"},
            send_to_tenhou,
            send_to_mjai,
        )
    )
    assert sent_to_tenhou == [{"tag": "D", "p": 52}]


def test_discard_response_unavailable_call_passes():
    state = State(name="NoName", room="L2147_9")
    # Two 1m permit pon when the opponent discards 1m, but not chi.
    state.hand = [0, 1]
    sent_to_tenhou = []

    async def send_to_mjai(_event):
        return {
            "type": "chi",
            "actor": 0,
            "target": 1,
            "pai": "1m",
            "consumed": ["2m", "3m"],
        }

    async def send_to_tenhou(event):
        sent_to_tenhou.append(event)

    import asyncio

    asyncio.run(
        responder.Dahai().process(
            state,
            {"tag": "E0", "t": "1"},
            send_to_tenhou,
            send_to_mjai,
        )
    )
    assert sent_to_tenhou == [{"tag": "N"}]


def test_reach_step_missing_discard_uses_legal_fallback(monkeypatch):
    state = State(name="NoName", room="L2147_9")
    state.hand = [0, 4]
    sent_to_tenhou = []

    async def send_to_mjai(_event):
        return {"type": "none", "actor": 0}

    async def send_to_tenhou(event):
        sent_to_tenhou.append(event)

    monkeypatch.setattr(responder.ReachStep1, "cannot_dahai", lambda _self, _state: ["1m"])

    import asyncio

    asyncio.run(
        responder.ReachStep1().process(
            state,
            {"tag": "REACH", "step": "1", "who": "0"},
            send_to_tenhou,
            send_to_mjai,
        )
    )
    assert sent_to_tenhou == [{"tag": "D", "p": 4}]
