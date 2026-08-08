import convert.tenhou6_utils as tenhou6_utils
from convert.tenhou6_utils import _decode_kakan, _result_events, _take_meld_matches_discard, tenhou6_to_mjai_events
from replay.bot import _load_events_from_source


def test_tenhou6_kakan_marker_can_appear_inside_meld_string() -> None:
    assert _decode_kakan("46k464646") == ("F", ["F", "F", "F"])
    assert _decode_kakan("4545k4545") == ("P", ["P", "P", "P"])
    assert _decode_kakan("51k151515") == ("5m", ["5mr", "5m", "5m"])


def test_windows_convlog_never_executes_linux_binary_directly(monkeypatch, tmp_path) -> None:
    linux_binary = tmp_path / "convlog"
    linux_binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(tenhou6_utils, "CONVLOG_BIN", linux_binary)
    monkeypatch.setattr(tenhou6_utils.os, "name", "nt")
    monkeypatch.setattr(tenhou6_utils.shutil, "which", lambda _name: None)

    assert tenhou6_utils._convlog_command(tmp_path / "input.json", tmp_path / "output.mjson") is None


def test_windows_convlog_uses_wsl_for_linux_binary(monkeypatch, tmp_path) -> None:
    linux_binary = tmp_path / "convlog"
    linux_binary.write_bytes(b"\x7fELF")
    monkeypatch.setattr(tenhou6_utils, "CONVLOG_BIN", linux_binary)
    monkeypatch.setattr(tenhou6_utils.os, "name", "nt")
    monkeypatch.setattr(tenhou6_utils.shutil, "which", lambda _name: "C:/Windows/System32/wsl.exe")
    monkeypatch.setattr(tenhou6_utils, "_wsl_path", lambda path: f"/mnt/e/{path.name}")

    command = tenhou6_utils._convlog_command(tmp_path / "input.json", tmp_path / "output.mjson")

    assert command == [
        "wsl.exe",
        "--",
        "/mnt/e/convlog",
        "/mnt/e/input.json",
        "/mnt/e/output.mjson",
    ]


def test_tenhou6_review_input_converts_to_mjai_events() -> None:
    tenhou6 = {
        "dan": ["雀豪★2", "雀聖★2", "雀豪★1", "雀豪★1"],
        "lobby": 0,
        "log": [
            [
                [5, 1, 0],
                [22700, 29300, 35000, 13000],
                [25],
                [18],
                [14, 46, 27, 11, 16, 13, 41, 25, 13, 21, 52, 26, 37],
                [41, 41],
                [21, 46],
                [33, 21, 38, 16, 28, 36, 35, 34, 39, 29, 31, 18, 16],
                [35, 37, 27],
                [21, 18, "r35"],
                [24, 32, 12, 24, 38, 43, 29, 46, 22, 26, 15, 17, 34],
                [42, 28, 26],
                [43, 29, 32],
                [11, 25, 38, 22, 26, 11, 22, 44, 28, 47, 33, 44, 21],
                [16, 14],
                [38, 47],
                [
                    "和了",
                    [0, 13300, -12300, 0],
                    [
                        1,
                        2,
                        1,
                        "満貫12000点",
                        "一気通貫(2飜)",
                        "立直(1飜)",
                        "一発(1飜)",
                        "裏ドラ(0飜)",
                    ],
                ],
            ]
        ],
        "name": ["Aさん", "Bさん", "Cさん", "Dさん"],
        "rate": [269.0, 3644.0, 1206.0, 2300.0],
        "ratingc": "PF4",
        "rule": {"aka": 0, "aka51": 1, "aka52": 1, "aka53": 1, "disp": "玉の間南喰赤"},
        "sx": ["C", "C", "C", "C"],
    }

    events = _load_events_from_source(tenhou6, input_type="tenhou6")

    assert events[0] == {
        "type": "start_game",
        "names": ["Aさん", "Bさん", "Cさん", "Dさん"],
        "kyoku_first": 0,
        "aka_flag": True,
    }
    assert any(event["type"] == "hora" for event in events)
    assert events[-1] == {"type": "end_game"}


def test_tenhou6_double_ron_uses_each_result_pair() -> None:
    result = [
        "和了",
        [0, -8300, 9300, 0],
        [2, 1, 2, "30符4飜7700点", "断幺九(1飜)", "ドラ(2飜)"],
        [8600, -8600, 0, 0],
        [0, 1, 0, "満貫8000点", "立直(1飜)", "赤ドラ(1飜)"],
    ]

    assert _result_events(result) == [
        {
            "type": "hora",
            "actor": 2,
            "target": 1,
            "deltas": [0, -8300, 9300, 0],
        },
        {
            "type": "hora",
            "actor": 0,
            "target": 1,
            "deltas": [8600, -8600, 0, 0],
        },
    ]


def test_tenhou6_meld_must_match_current_discard() -> None:
    assert _take_meld_matches_discard("p414141", "E") is True
    assert _take_meld_matches_discard("p414141", "2m") is False
    assert _take_meld_matches_discard("c353334", "5s") is True


def test_tenhou6_chi_waits_for_kamicha_same_tile_discard() -> None:
    hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 24]
    kyoku = [
        [0, 0, 0],
        [25000, 25000, 25000, 25000],
        [11],
        [],
        hand,
        [28, 37],
        [41, 60],
        hand,
        ["p414141", "c373638"],
        [43, 42],
        hand,
        [11],
        [19],
        hand,
        [32],
        [37],
        ["流局", [0, 0, 0, 0]],
    ]

    events = tenhou6_to_mjai_events({
        "name": ["P0", "P1", "P2", "P3"],
        "rule": {},
        "log": [kyoku],
    })

    chi_index = next(index for index, event in enumerate(events) if event["type"] == "chi")
    assert events[chi_index] == {
        "type": "chi",
        "actor": 1,
        "target": 0,
        "pai": "7s",
        "consumed": ["6s", "8s"],
    }
    assert events[chi_index - 1] == {
        "type": "dahai",
        "actor": 0,
        "pai": "7s",
        "tsumogiri": True,
    }
