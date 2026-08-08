"""Convert mjai JSONL event streams to tenhou.net/6 JSON.

The converter targets reviewer input smoke tests. It preserves complete hanchan
action order well enough for mjai-reviewer's convlog to read the generated
Tenhou6 JSON back into mjai events.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

_TSUMOGIRI = 60
_ROUND_OFFSET = {"E": 0, "S": 4, "W": 8, "N": 12}
_HONORS = {"E": 41, "S": 42, "W": 43, "N": 44, "P": 45, "F": 46, "C": 47}


class Tenhou6Kyoku:
    def __init__(self, event: dict[str, Any]):
        bakaze = str(event.get("bakaze", "E"))
        kyoku = int(event.get("kyoku", 1))
        self.meta = [_ROUND_OFFSET.get(bakaze, 0) + kyoku - 1, int(event.get("honba", 0)), int(event.get("kyotaku", 0))]
        self.scores = [int(score) for score in event.get("scores", [25000, 25000, 25000, 25000])]
        self.dora_indicators = [_tile_to_tenhou6(str(event["dora_marker"]))] if event.get("dora_marker") else []
        self.ura_indicators: list[int] = []
        self.haipai = [[_tile_to_tenhou6(str(tile)) for tile in hand] for hand in event.get("tehais", [[], [], [], []])]
        while len(self.haipai) < 4:
            self.haipai.append([])
        self.takes: list[list[int | str]] = [[], [], [], []]
        self.discards: list[list[int | str]] = [[], [], [], []]
        self.results: list[Any] = []
        self.last_draw: list[int | None] = [None, None, None, None]
        self.reach_pending: list[bool] = [False, False, False, False]

    def as_tenhou6(self) -> list[Any]:
        entry: list[Any] = [self.meta, self.scores, self.dora_indicators, self.ura_indicators]
        for seat in range(4):
            entry.append(self.haipai[seat])
            entry.append(self.takes[seat])
            entry.append(self.discards[seat])
        entry.append(self.results)
        return entry


def _tile_to_tenhou6(tile: str) -> int:
    tile = tile.strip()
    if tile in _HONORS:
        return _HONORS[tile]
    if len(tile) < 2:
        raise ValueError(f"invalid tile {tile!r}")
    number = int(tile[0])
    suit = tile[1]
    is_red = len(tile) >= 3 and tile[2] == "r"
    if is_red and number == 5:
        if suit == "m":
            return 51
        if suit == "p":
            return 52
        if suit == "s":
            return 53
    if suit == "m":
        return 10 + number
    if suit == "p":
        return 20 + number
    if suit == "s":
        return 30 + number
    raise ValueError(f"invalid tile suit in {tile!r}")


def _code(tile: str | int) -> str:
    value = tile if isinstance(tile, int) else _tile_to_tenhou6(tile)
    return f"{int(value):02d}"


def _relative_target(actor: int, target: int) -> int:
    rel = (int(target) - int(actor)) % 4
    if rel not in {1, 2, 3}:
        raise ValueError(f"invalid call target actor={actor}, target={target}")
    return rel


def _pon_like_meld(marker: str, actor: int, target: int, called: str, consumed: Sequence[str]) -> str:
    rel = _relative_target(actor, target)
    called_code = _code(called)
    consumed_codes = [_code(tile) for tile in consumed]
    if len(consumed_codes) < 2:
        raise ValueError(f"{marker} meld needs at least two consumed tiles")
    if rel == 3:
        return f"{marker}{called_code}{consumed_codes[0]}{consumed_codes[1]}"
    if rel == 2:
        return f"{consumed_codes[0]}{marker}{called_code}{consumed_codes[1]}"
    return f"{consumed_codes[0]}{consumed_codes[1]}{marker}{called_code}"


def _chi_meld(called: str, consumed: Sequence[str]) -> str:
    if len(consumed) != 2:
        raise ValueError("chi meld needs exactly two consumed tiles")
    return f"c{_code(called)}{_code(consumed[0])}{_code(consumed[1])}"


def _daiminkan_meld(actor: int, target: int, called: str, consumed: Sequence[str]) -> str:
    rel = _relative_target(actor, target)
    tiles = [_code(tile) for tile in [called, *consumed]]
    if len(tiles) != 4:
        raise ValueError("daiminkan meld needs four tiles")
    if rel == 3:
        return "m" + "".join(tiles)
    if rel == 2:
        return f"{tiles[1]}m{tiles[0]}{tiles[2]}{tiles[3]}"
    return f"{tiles[1]}{tiles[2]}m{tiles[0]}{tiles[3]}"


def _ankan_meld(consumed: Sequence[str]) -> str:
    tiles = [_code(tile) for tile in consumed]
    if len(tiles) != 4:
        raise ValueError("ankan meld needs four consumed tiles")
    return f"{tiles[0]}{tiles[1]}{tiles[2]}a{tiles[3]}"


def _kakan_meld(added: str, consumed: Sequence[str]) -> str:
    tiles = [_code(tile) for tile in [added, *consumed]]
    if len(tiles) != 4:
        raise ValueError("kakan meld needs one added tile and three consumed tiles")
    return f"k{tiles[0]}{tiles[1]}{tiles[2]}{tiles[3]}"


def _score_label(event: dict[str, Any]) -> str:
    actor = int(event.get("actor", 0))
    target = int(event.get("target", actor))
    deltas = [int(delta) for delta in event.get("deltas", [0, 0, 0, 0])]
    if target == actor or event.get("tsumo"):
        losses = sorted(abs(delta) for seat, delta in enumerate(deltas) if seat != actor and delta < 0)
        if not losses:
            return f"{max([0, *deltas])}点"
        if len(set(losses)) == 1:
            return f"30符1飜{losses[0]}点∀"
        return f"30符1飜{losses[0]}-{losses[-1]}点"
    payment = abs(deltas[target]) if 0 <= target < len(deltas) else max([0, *deltas])
    return f"30符1飜{payment}点"


def _hora_detail(event: dict[str, Any]) -> list[Any]:
    actor = int(event.get("actor", 0))
    target = int(event.get("target", actor))
    return [actor, target, actor, _score_label(event)]


def convert_mjai_jsonl_to_tenhou6(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    names = ["NoName", "NoName", "NoName", "NoName"]
    logs: list[list[Any]] = []
    kyoku: Tenhou6Kyoku | None = None

    for event in events:
        event_type = event.get("type")
        if event_type == "start_game":
            raw_names = event.get("names")
            if isinstance(raw_names, list) and len(raw_names) >= 4:
                names = [str(name) for name in raw_names[:4]]
        elif event_type == "start_kyoku":
            if kyoku is not None:
                logs.append(kyoku.as_tenhou6())
            kyoku = Tenhou6Kyoku(event)
        elif event_type == "tsumo" and kyoku is not None:
            actor = int(event["actor"])
            tile = _tile_to_tenhou6(str(event["pai"]))
            kyoku.takes[actor].append(tile)
            kyoku.last_draw[actor] = tile
        elif event_type == "reach" and kyoku is not None:
            kyoku.reach_pending[int(event["actor"])] = True
        elif event_type == "dahai" and kyoku is not None:
            actor = int(event["actor"])
            tile = _tile_to_tenhou6(str(event["pai"]))
            if kyoku.reach_pending[actor]:
                kyoku.discards[actor].append(f"r{_TSUMOGIRI:02d}" if bool(event.get("tsumogiri")) else f"r{tile:02d}")
                kyoku.reach_pending[actor] = False
            elif bool(event.get("tsumogiri")):
                kyoku.discards[actor].append(_TSUMOGIRI)
            else:
                kyoku.discards[actor].append(tile)
            kyoku.last_draw[actor] = None
        elif event_type == "chi" and kyoku is not None:
            actor = int(event["actor"])
            kyoku.takes[actor].append(_chi_meld(str(event["pai"]), [str(tile) for tile in event.get("consumed", [])]))
        elif event_type == "pon" and kyoku is not None:
            actor = int(event["actor"])
            kyoku.takes[actor].append(_pon_like_meld("p", actor, int(event["target"]), str(event["pai"]), [str(tile) for tile in event.get("consumed", [])]))
        elif event_type == "daiminkan" and kyoku is not None:
            actor = int(event["actor"])
            kyoku.takes[actor].append(_daiminkan_meld(actor, int(event["target"]), str(event["pai"]), [str(tile) for tile in event.get("consumed", [])]))
        elif event_type == "ankan" and kyoku is not None:
            actor = int(event["actor"])
            kyoku.discards[actor].append(_ankan_meld([str(tile) for tile in event.get("consumed", [])]))
        elif event_type == "kakan" and kyoku is not None:
            actor = int(event["actor"])
            kyoku.discards[actor].append(_kakan_meld(str(event["pai"]), [str(tile) for tile in event.get("consumed", [])]))
        elif event_type == "dora" and kyoku is not None:
            kyoku.dora_indicators.append(_tile_to_tenhou6(str(event["dora_marker"])))
        elif event_type == "hora" and kyoku is not None:
            if event.get("ura_markers"):
                kyoku.ura_indicators.extend(_tile_to_tenhou6(str(tile)) for tile in event.get("ura_markers", []))
            if not kyoku.results:
                kyoku.results.append("和了")
            kyoku.results.append([int(delta) for delta in event.get("deltas", [0, 0, 0, 0])])
            kyoku.results.append(_hora_detail(event))
        elif event_type == "ryukyoku" and kyoku is not None:
            kyoku.results = ["流局", [int(delta) for delta in event.get("deltas", [0, 0, 0, 0])], []]
        elif event_type == "end_kyoku":
            if kyoku is not None:
                logs.append(kyoku.as_tenhou6())
                kyoku = None
        elif event_type == "end_game":
            pass

    if kyoku is not None:
        logs.append(kyoku.as_tenhou6())

    return {
        "title": ["Mortal", ""],
        "name": names,
        "rule": {"disp": "Mortal", "aka": 1},
        "log": logs,
    }


def load_mjai_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Convert mjai JSONL to tenhou.net/6 JSON")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = convert_mjai_jsonl_to_tenhou6(load_mjai_jsonl(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"converted {args.input} -> {args.output} ({len(result['log'])} kyoku)")


if __name__ == "__main__":
    main()
