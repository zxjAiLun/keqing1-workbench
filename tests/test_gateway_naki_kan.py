"""Regression tests for Naki.process kan timing fix.

Root cause: when the bot (actor==0) declared a kan (ankan/kakan/daiminkan),
Naki.process sent an immediate discard 'D' to Tenhou *before* the rinshan
(dead-wall) draw arrived.  Tenhou rejected the out-of-turn discard with ERR,
disconnecting the bot.

Fix: skip the discard for kan-type melds; the subsequent rinshan T-message is
handled by Tsumo.process which drives the discard normally.

These tests verify:
  1. ankan/kakan/daiminkan → state updated, event forwarded, NO 'D' sent.
  2. pon/chi → state updated, event forwarded, 'D' still sent (unchanged).
  3. Opponent melds → no 'D' sent (unchanged, actor != 0).
"""
from __future__ import annotations

import asyncio

from gateway.responder import Naki
from gateway.utils.state import State


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_naki(state: State, message: dict, bot_response: dict):
    """Run Naki.process and capture messages sent to Tenhou and mjai."""
    sent_to_tenhou: list[dict] = []
    sent_to_mjai: list[dict] = []

    async def send_to_tenhou(event):
        sent_to_tenhou.append(event)

    async def send_to_mjai(event):
        sent_to_mjai.append(event)
        return bot_response

    asyncio.run(Naki().process(state, message, send_to_tenhou, send_to_mjai))
    return sent_to_tenhou, sent_to_mjai


def _make_state(hand: list[int]) -> State:
    state = State(name="test", room="0_0")
    state.hand = list(hand)
    state.live_wall = 50
    return state


# ---------------------------------------------------------------------------
# Meld 'm' encoding reference (from decoder.py):
#   ankan  1m: m = (hai0=0) << 8 | target=0  → 0
#   kakan  1m: m = (t=0)<<9 | (added=0)<<5 | (1<<4) | target=0  → 16
#   daiminkan 1m from player 1: m = (hai0=0)<<8 | target=1  → 1
#   pon    1m from player 1: m = (t=0)<<9 | (unused=3)<<5 | (1<<3) | target=1  → 105
#   chi  1-2-3m from player 3: m = (T=0)<<10 | 0 | (1<<2) | target=3  → 7
# ---------------------------------------------------------------------------


# --- Kan types: must NOT send discard ---

def test_ankan_no_premature_discard():
    """Ankan (concealed kan) of 1m: no D sent, state updated correctly."""
    # Hand has all four 1m (indices 0-3) plus 5m, 5m, 6s
    state = _make_state([0, 1, 2, 3, 16, 17, 20])
    msg = {'tag': 'N', 'm': '0', 'who': '0'}
    bot_response = {'type': 'none'}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    # No discard sent to Tenhou — the rinshan draw hasn't arrived yet
    assert sent_to_tenhou == [], f"ankan must not send D, got: {sent_to_tenhou}"
    # Event forwarded to bot engine
    assert len(sent_to_mjai) == 1
    assert sent_to_mjai[0]['type'] == 'ankan'
    assert sent_to_mjai[0]['actor'] == 0
    # State: four 1m removed from hand, meld registered
    assert state.hand == [16, 17, 20]
    assert len(state.melds) == 1
    assert state.melds[0].meld_type == 'ankan'


def test_kakan_no_premature_discard():
    """Kakan (added kan) of 1m: no D sent, only the added tile removed."""
    # Hand has one 1m to add (index 0) plus 5m, 5m, 6s
    state = _make_state([0, 16, 17, 20])
    msg = {'tag': 'N', 'm': '16', 'who': '0'}
    bot_response = {'type': 'none'}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    assert sent_to_tenhou == [], f"kakan must not send D, got: {sent_to_tenhou}"
    assert len(sent_to_mjai) == 1
    assert sent_to_mjai[0]['type'] == 'kakan'
    assert sent_to_mjai[0]['actor'] == 0
    # Only the added tile (index 0) removed
    assert state.hand == [16, 17, 20]
    assert len(state.melds) == 1
    assert state.melds[0].meld_type == 'kakan'


def test_daiminkan_no_premature_discard():
    """Daiminkan (open kan) of 1m from player 1: no D sent."""
    # Hand has three 1m (indices 1,2,3) plus 5m, 5m, 6s
    state = _make_state([1, 2, 3, 16, 17, 20])
    msg = {'tag': 'N', 'm': '1', 'who': '0'}
    bot_response = {'type': 'none'}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    assert sent_to_tenhou == [], f"daiminkan must not send D, got: {sent_to_tenhou}"
    assert len(sent_to_mjai) == 1
    assert sent_to_mjai[0]['type'] == 'daiminkan'
    assert sent_to_mjai[0]['actor'] == 0
    # Three tiles from hand removed (exposed = tiles[1:] = [1,2,3])
    assert state.hand == [16, 17, 20]
    assert len(state.melds) == 1
    assert state.melds[0].meld_type == 'daiminkan'


def test_ankan_ignores_bot_dahai_response():
    """Even if the bot erroneously returns a dahai, no D is sent for kan."""
    state = _make_state([0, 1, 2, 3, 16, 17, 20])
    msg = {'tag': 'N', 'm': '0', 'who': '0'}
    # Bot incorrectly tries to discard immediately
    bot_response = {'type': 'dahai', 'pai': '5m', 'tsumogiri': False}

    sent_to_tenhou, _ = _run_naki(state, msg, bot_response)

    assert sent_to_tenhou == [], "kan must swallow even a dahai response"


# --- Non-kan types: must still send discard ---

def test_pon_still_discards():
    """Pon of 1m from player 1: D is still sent after meld."""
    # Hand has two 1m (indices 1,2) plus 5m, 5m, 6s
    state = _make_state([1, 2, 16, 17, 20])
    msg = {'tag': 'N', 'm': '105', 'who': '0'}
    bot_response = {'type': 'dahai', 'pai': '5m', 'tsumogiri': False}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    # Pon must still produce a discard
    assert len(sent_to_tenhou) == 1
    assert sent_to_tenhou[0]['tag'] == 'D'
    assert sent_to_tenhou[0]['p'] // 4 == 4  # a 5m tile
    # Event forwarded
    assert sent_to_mjai[0]['type'] == 'pon'
    # State: exposed tiles removed; discard not yet removed (awaits Tenhou ack)
    assert state.hand == [16, 17, 20]
    assert len(state.melds) == 1


def test_chi_still_discards():
    """Chi 1-2-3m from player 3: D is still sent after meld."""
    # Hand has tiles for chi (indices 4,8 = 2m,3m) plus 5m, 5m, 6s
    state = _make_state([4, 8, 16, 17, 20])
    msg = {'tag': 'N', 'm': '7', 'who': '0'}
    bot_response = {'type': 'dahai', 'pai': '5m', 'tsumogiri': False}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    assert len(sent_to_tenhou) == 1
    assert sent_to_tenhou[0]['tag'] == 'D'
    assert sent_to_tenhou[0]['p'] // 4 == 4  # a 5m tile
    assert sent_to_mjai[0]['type'] == 'chi'
    # exposed = tiles[1:] = [4, 8] removed; discard not yet removed
    assert state.hand == [16, 17, 20]
    assert len(state.melds) == 1


# --- Opponent meld: unchanged behavior ---

def test_opponent_meld_no_discard():
    """Opponent's pon (actor != 0): no D sent, no state mutation."""
    state = _make_state([16, 17, 20, 21, 24])
    # Pon by player 2 (who=2), m encoding for pon of 5m from player 1:
    # t=4 (5m base=16, t//3*4=4 → t_orig=12+r, r=0 → t_orig=12)
    # unused=0, m = (12<<9) | (0<<5) | (1<<3) | target=1 = 6144+8+1 = 6153
    msg = {'tag': 'N', 'm': '6153', 'who': '2'}
    bot_response = {'type': 'none'}

    sent_to_tenhou, sent_to_mjai = _run_naki(state, msg, bot_response)

    assert sent_to_tenhou == []
    assert len(sent_to_mjai) == 1
    assert sent_to_mjai[0]['actor'] == 2
    # Hand unchanged for opponent melds
    assert state.hand == [16, 17, 20, 21, 24]
    assert len(state.melds) == 0
