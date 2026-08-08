"""Regression tests for the tenhou6 daiminkan 0-placeholder conversion bug.

Root cause: in tenhou6 logs a daiminkan meld occupies a *take* slot and its
paired *discard* slot holds a ``0`` placeholder (no normal discard happens on
a kan turn).  The converter left that ``0`` in place, so after consuming the
rinshan draw it tried to decode ``0`` as a tile and crashed with
``ValueError: invalid tenhou6 tile code: 0``.  Because the convlog fallback
binary is absent in most environments, the whole tenhou6_to_mjson path then
failed with "tenhou6_to_mjson 转换失败", breaking tenhou.net/6/#json= imports
for any log containing a daiminkan.

These tests only import the pure-Python converter (no torch), so they run in
every environment.
"""
from __future__ import annotations

from convert.tenhou6_utils import tenhou6_to_mjai_events


def _kyoku(takes_seat3, discards_seat3, result=None):
    """Build a minimal single-kyoku tenhou6 log with seat 3 as the actor."""
    result = result or ["流局", [0, 0, 0, 0]]
    tehais = [[11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24]] * 4
    empty_takes = [21, 22]
    empty_discards = [60, 60]
    return [
        [0, 0, 0],                    # meta: round 0, honba 0, kyotaku 0
        [25000, 25000, 25000, 25000],  # scores
        [39],                          # dora marker
        [],                            # ura dora
        tehais[0], empty_takes, empty_discards,   # seat 0
        tehais[1], empty_takes, empty_discards,   # seat 1
        tehais[2], empty_takes, empty_discards,   # seat 2
        tehais[3], takes_seat3, discards_seat3,   # seat 3 (actor under test)
        result,
    ]


def _t6(log):
    return {
        "name": ["A", "B", "C", "D"],
        "rule": {"aka51": 1, "aka52": 1, "aka53": 1},
        "log": [log],
    }


def test_daiminkan_zero_placeholder_does_not_crash():
    """A daiminkan's paired discard slot holds 0; conversion must not crash."""
    # Seat 3: draw, then daiminkan on 2p (meld in takes), 0 placeholder in
    # discards, then rinshan draw 31 and its tsumogiri discard.
    takes = [22, "222222m22", 31]
    discards = [38, 0, 60]
    events = tenhou6_to_mjai_events(_t6(_kyoku(takes, discards)))

    types = [e["type"] for e in events]
    assert "daiminkan" in types
    # The 0 placeholder must not produce a dahai event.
    daiminkan_i = types.index("daiminkan")
    # After the kan: rinshan tsumo, then exactly one dahai (the rinshan tile).
    after = types[daiminkan_i + 1:]
    assert after[0] == "tsumo"
    assert "dahai" in after


def test_daiminkan_event_sequence_semantics():
    """daiminkan -> rinshan tsumo -> dora -> dahai(tsumogiri) in order."""
    takes = [22, "222222m22", 31]
    discards = [38, 0, 60]
    events = tenhou6_to_mjai_events(_t6(_kyoku(takes, discards)))

    seq = [e for e in events if e["type"] in ("daiminkan", "tsumo", "dahai", "dora")]
    # Find the daiminkan and check the following events.
    idx = next(i for i, e in enumerate(seq) if e["type"] == "daiminkan")
    kan = seq[idx]
    assert kan["actor"] == 3
    assert kan["pai"] == "2p"
    assert kan["consumed"] == ["2p", "2p", "2p"]

    following = [e["type"] for e in seq[idx + 1:]]
    # rinshan draw, then (dora), then the discard of the drawn tile
    assert following[0] == "tsumo"
    assert "dahai" in following
    # The rinshan discard must be tsumogiri of the drawn tile.
    rinshan_tsumo = seq[idx + 1]
    rinshan_dahai = next(e for e in seq[idx + 1:] if e["type"] == "dahai")
    assert rinshan_dahai["actor"] == 3
    assert rinshan_dahai["pai"] == rinshan_tsumo["pai"]
    assert rinshan_dahai["tsumogiri"] is True


def test_full_sample_with_daiminkan_converts():
    """The real-world sample log (with pon + daiminkan) converts end to end."""
    log = [
        [1, 0, 0], [25000, 25000, 30200, 19800], [18, 37], [],
        [24, 36, 21, 44, 29, 24, 12, 36, 39, 22, 42, 46, 19],
        [11, 13, 17, 44, 29, 38, 12, 37, 41, 14, 41, 28, 16, 33, 45, 52, "44p4444"],
        [39, 42, 29, 46, 60, 21, 12, 36, 60, 11, 60, 60, 22, 60, 60, 19, 52],
        [18, 17, 33, 16, 51, 53, 33, 41, 35, 16, 39, 36, 16],
        [27, 19, 25, 23, 43, 29, 27, 27, 18, 19, 46, 42, 43, 39, 43, 14, 15],
        [39, 41, 35, 16, 16, 43, 29, 23, 18, 25, 60, 60, 60, 60, 60, "r19", 60],
        [26, 34, 25, 26, 28, 24, 19, 38, 23, 15, 44, 32, 47],
        [44, 45, 24, 21, 43, 46, 37, 23, 42, "c252426", 47, 34, 15, 26, 41, 17],
        [47, 60, 38, 19, 60, 15, 60, 34, 32, 42, 46, 60, 60, 21, 60, 44],
        [35, 34, 12, 37, 35, 26, 22, 13, 21, 34, 32, 45, 28],
        [42, 14, 38, 22, 11, 29, 14, 11, 32, 13, 12, "p343434", 22, "222222m22", 31, 45, 31],
        [60, 45, 21, 32, 60, 60, 60, 60, 60, 60, 60, 37, 38, 0, 60, 60, 60],
        ["和了", [3000, -2000, 0, 0], [0, 1, 0, "30符2飜2000点", "自風 北(1飜)", "ドラ(1飜)"]],
    ]
    events = tenhou6_to_mjai_events(_t6(log))
    types = [e["type"] for e in events]
    assert types[0] == "start_game"
    assert types[-1] == "end_game"
    assert types.count("daiminkan") == 1
    assert types.count("pon") == 2
    assert types.count("chi") == 1
    assert "hora" in types
