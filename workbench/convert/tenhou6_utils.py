"""tenhou6 JSON → mjai JSONL 转换工具。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

CONVLOG_BIN = (
    Path(__file__).parent.parent.parent
    / "third_party"
    / "mjai-reviewer"
    / "target"
    / "release"
    / "convlog"
)

_TSUMOGIRI = 60
_ROUND_BY_OFFSET = {0: "E", 4: "S", 8: "W", 12: "N"}
_HONORS_BY_CODE = {41: "E", 42: "S", 43: "W", 44: "N", 45: "P", 46: "F", 47: "C"}


def _tile_from_tenhou6(value: int | str) -> str:
    code = int(value)
    if code in _HONORS_BY_CODE:
        return _HONORS_BY_CODE[code]
    if code == 51:
        return "5mr"
    if code == 52:
        return "5pr"
    if code == 53:
        return "5sr"
    number = code % 10
    suit = code // 10
    if suit == 1:
        return f"{number}m"
    if suit == 2:
        return f"{number}p"
    if suit == 3:
        return f"{number}s"
    raise ValueError(f"invalid tenhou6 tile code: {value!r}")


def _meld_codes(raw: str) -> list[str]:
    compact = raw.replace("c", "").replace("p", "").replace("m", "").replace("a", "").replace("k", "")
    if len(compact) % 2 != 0:
        raise ValueError(f"invalid tenhou6 meld string: {raw!r}")
    return [compact[i : i + 2] for i in range(0, len(compact), 2)]


def _called_and_consumed(raw: str, marker: str) -> tuple[str, list[str]]:
    marker_pos = raw.index(marker)
    called_code = raw[marker_pos + 1 : marker_pos + 3]
    codes = _meld_codes(raw)
    consumed_codes = list(codes)
    try:
        consumed_codes.remove(called_code)
    except ValueError:
        pass
    return _tile_from_tenhou6(called_code), [_tile_from_tenhou6(code) for code in consumed_codes]


def _decode_kakan(raw: str) -> tuple[str, list[str]]:
    if "k" not in raw:
        raise ValueError(f"invalid tenhou6 kakan string: {raw!r}")
    pai, consumed = _called_and_consumed(raw, "k")
    if len(consumed) != 3:
        raise ValueError(f"invalid tenhou6 kakan string: {raw!r}")
    return pai, consumed


def _normalized_tile_face(tile: str) -> str:
    return tile[:-1] if tile in {"5mr", "5pr", "5sr"} else tile


def _take_meld_matches_discard(raw: str, discarded_pai: str) -> bool:
    marker = "c" if "c" in raw else "p" if "p" in raw else "m" if "m" in raw else None
    if marker is None:
        return False
    called_pai, _ = _called_and_consumed(raw, marker)
    return _normalized_tile_face(called_pai) == _normalized_tile_face(discarded_pai)


def _decode_discard(raw: int | str, last_draw: str | None) -> tuple[bool, str, bool]:
    reach = False
    value: int | str = raw
    if isinstance(raw, str) and raw.startswith("r"):
        reach = True
        value = raw[1:]
    code = int(value)
    if code == _TSUMOGIRI:
        if not last_draw:
            raise ValueError("tsumogiri discard without a previous draw")
        return reach, last_draw, True
    return reach, _tile_from_tenhou6(code), False


def _rule_has_aka(rule: dict[str, Any]) -> bool:
    return bool(rule.get("aka") or rule.get("aka51") or rule.get("aka52") or rule.get("aka53"))


def _start_kyoku_event(kyoku: list[Any], names: list[str], rule: dict[str, Any]) -> dict[str, Any]:
    del names, rule
    meta = kyoku[0]
    round_index = int(meta[0])
    round_base = (round_index // 4) * 4
    return {
        "type": "start_kyoku",
        "bakaze": _ROUND_BY_OFFSET.get(round_base, "E"),
        "dora_marker": _tile_from_tenhou6(kyoku[2][0]) if len(kyoku) > 2 and kyoku[2] else None,
        "kyoku": round_index % 4 + 1,
        "honba": int(meta[1]) if len(meta) > 1 else 0,
        "kyotaku": int(meta[2]) if len(meta) > 2 else 0,
        "oya": round_index % 4,
        "scores": [int(score) for score in kyoku[1]],
        "tehais": [[_tile_from_tenhou6(tile) for tile in kyoku[4 + seat * 3]] for seat in range(4)],
    }


def _result_events(result: list[Any]) -> list[dict[str, Any]]:
    if not result:
        return []
    kind = result[0]
    if kind == "流局":
        deltas = [int(delta) for delta in (result[1] if len(result) > 1 and isinstance(result[1], list) else [0, 0, 0, 0])]
        event: dict[str, Any] = {"type": "ryukyoku", "reason": "ryukyoku", "deltas": deltas}
        # tenhou6 流局 detail（第 3 段）携带四家听牌标记：[1,0,1,0] 或全 0。
        # 只要第 3 段是合法四项标记就始终写入（全 false = 已知不听，不是未知）。
        if len(result) > 2 and isinstance(result[2], list) and len(result[2]) == 4:
            tenpai = [int(v) == 1 for v in result[2]]
            event["tenpai"] = tenpai
        return [event]
    if kind != "和了":
        return []

    # Tenhou6 stores each winner as a (score deltas, detail) pair.  Double ron
    # therefore looks like ["和了", deltas1, detail1, deltas2, detail2].
    events: list[dict[str, Any]] = []
    for index in range(1, len(result) - 1, 2):
        raw_deltas = result[index]
        detail = result[index + 1]
        if not isinstance(raw_deltas, list) or len(raw_deltas) != 4:
            continue
        if not isinstance(detail, list) or len(detail) < 2:
            continue
        deltas = [int(delta) for delta in raw_deltas]
        actor = int(detail[0])
        target = int(detail[1])
        events.append(
            {
                "type": "hora",
                "actor": actor,
                "target": target,
                "deltas": deltas,
            }
        )
    return events


def _convert_kyoku_to_events(kyoku: list[Any], names: list[str], rule: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [_start_kyoku_event(kyoku, names, rule)]
    oya = int(events[0]["oya"])
    takes = [list(kyoku[5 + seat * 3]) for seat in range(4)]
    discards = [list(kyoku[6 + seat * 3]) for seat in range(4)]
    take_idx = [0, 0, 0, 0]
    discard_idx = [0, 0, 0, 0]
    last_draw: list[str | None] = [None, None, None, None]
    last_discard_actor: int | None = None
    extra_dora_markers = [_tile_from_tenhou6(value) for value in kyoku[2][1:]]
    pending_dora_marker: str | None = None
    actor = oya

    def queue_next_dora_marker() -> None:
        nonlocal pending_dora_marker
        if pending_dora_marker is None and extra_dora_markers:
            pending_dora_marker = extra_dora_markers.pop(0)

    def has_pending() -> bool:
        return any(take_idx[seat] < len(takes[seat]) or discard_idx[seat] < len(discards[seat]) for seat in range(4))

    def next_call_after(discarder: int, discarded_pai: str) -> int | None:
        for offset in (1, 2, 3):
            candidate = (discarder + offset) % 4
            if take_idx[candidate] >= len(takes[candidate]):
                continue
            next_take = takes[candidate][take_idx[candidate]]
            if not isinstance(next_take, str) or not _take_meld_matches_discard(next_take, discarded_pai):
                continue
            # Chi is only legal from the immediately preceding player's discard.
            # Without this guard, a future chi can be attached to an earlier
            # same-faced discard from the opposite player.
            if "c" in next_take and offset != 1:
                continue
            return candidate
        return None

    guard = 0
    while has_pending():
        guard += 1
        if guard > 1000:
            raise RuntimeError("tenhou6 conversion exceeded action guard")
        progressed = False

        if take_idx[actor] < len(takes[actor]):
            take = takes[actor][take_idx[actor]]
            if isinstance(take, str):
                target = last_discard_actor if last_discard_actor is not None else (actor + 3) % 4
                if take.startswith("c"):
                    pai, consumed = _called_and_consumed(take, "c")
                    events.append({"type": "chi", "actor": actor, "target": target, "pai": pai, "consumed": consumed})
                elif "p" in take:
                    pai, consumed = _called_and_consumed(take, "p")
                    events.append({"type": "pon", "actor": actor, "target": target, "pai": pai, "consumed": consumed})
                elif "m" in take:
                    pai, consumed = _called_and_consumed(take, "m")
                    events.append({"type": "daiminkan", "actor": actor, "target": target, "pai": pai, "consumed": consumed})
                    queue_next_dora_marker()
                else:
                    raise ValueError(f"unsupported tenhou6 take meld: {take!r}")
                take_idx[actor] += 1
                progressed = True
                if "m" in take:
                    # A daiminkan occupies a take slot, and its paired discard
                    # slot holds a 0 placeholder (no normal discard happens on
                    # a kan turn).  Consume it so the following rinshan draw
                    # lines up with the real discard; otherwise the converter
                    # later tries to decode 0 as a tile and crashes.
                    if discard_idx[actor] < len(discards[actor]) and discards[actor][discard_idx[actor]] == 0:
                        discard_idx[actor] += 1
                    continue
            else:
                pai = _tile_from_tenhou6(take)
                events.append({"type": "tsumo", "actor": actor, "pai": pai})
                if pending_dora_marker is not None:
                    events.append({"type": "dora", "dora_marker": pending_dora_marker})
                    pending_dora_marker = None
                last_draw[actor] = pai
                take_idx[actor] += 1
                progressed = True

        if discard_idx[actor] < len(discards[actor]):
            discard = discards[actor][discard_idx[actor]]
            if isinstance(discard, str) and "a" in discard:
                consumed = [_tile_from_tenhou6(code) for code in _meld_codes(discard)]
                events.append({"type": "ankan", "actor": actor, "consumed": consumed})
                queue_next_dora_marker()
                discard_idx[actor] += 1
                progressed = True
                continue
            if isinstance(discard, str) and "k" in discard:
                pai, consumed = _decode_kakan(discard)
                events.append(
                    {
                        "type": "kakan",
                        "actor": actor,
                        "pai": pai,
                        "consumed": consumed,
                    }
                )
                queue_next_dora_marker()
                discard_idx[actor] += 1
                progressed = True
                continue

            reach, pai, tsumogiri = _decode_discard(discard, last_draw[actor])
            if reach:
                events.append({"type": "reach", "actor": actor})
            events.append({"type": "dahai", "actor": actor, "pai": pai, "tsumogiri": tsumogiri})
            last_draw[actor] = None
            last_discard_actor = actor
            discard_idx[actor] += 1
            progressed = True

            caller = next_call_after(actor, pai)
            actor = caller if caller is not None else (actor + 1) % 4
            continue

        if not progressed:
            actor = (actor + 1) % 4

    events.extend(_result_events(kyoku[-1] if kyoku else []))
    events.append({"type": "end_kyoku"})
    return events


def tenhou6_to_mjai_events(t6_json: dict[str, Any]) -> list[dict[str, Any]]:
    names = [str(name) for name in t6_json.get("name", ["A", "B", "C", "D"])[:4]]
    while len(names) < 4:
        names.append(f"P{len(names)}")
    rule = t6_json.get("rule", {}) if isinstance(t6_json.get("rule"), dict) else {}
    events: list[dict[str, Any]] = [
        {
            "type": "start_game",
            "names": names,
            "kyoku_first": 0,
            "aka_flag": _rule_has_aka(rule),
        }
    ]
    for kyoku in t6_json.get("log", []):
        if isinstance(kyoku, list) and len(kyoku) >= 17:
            events.extend(_convert_kyoku_to_events(kyoku, names, rule))
    events.append({"type": "end_game"})
    return events


def _wsl_path(path: Path) -> str:
    result = subprocess.run(
        ["wsl.exe", "wslpath", "-a", path.resolve().as_posix()],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _convlog_command(input_path: Path, output_path: Path) -> list[str] | None:
    if os.name != "nt":
        return [str(CONVLOG_BIN), str(input_path), str(output_path)] if CONVLOG_BIN.exists() else None

    windows_binary = CONVLOG_BIN.with_suffix(".exe")
    if windows_binary.exists():
        return [str(windows_binary), str(input_path), str(output_path)]

    if CONVLOG_BIN.exists() and shutil.which("wsl.exe"):
        return [
            "wsl.exe",
            "--",
            _wsl_path(CONVLOG_BIN),
            _wsl_path(input_path),
            _wsl_path(output_path),
        ]
    return None


def tenhou6_to_mjson(t6_json: dict, output_path: Path) -> bool:
    """Write tenhou6 JSON to an mjai JSONL file. Returns success."""
    python_error: Exception | None = None
    try:
        events = tenhou6_to_mjai_events(t6_json)
        output_path.write_text(
            "\n".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events) + "\n",
            encoding="utf-8",
        )
        return True
    except Exception as exc:
        python_error = exc
        print(f"  WARN python tenhou6 converter failed, trying convlog: {exc}")

    tmp = output_path.with_suffix(".tmp.json")
    try:
        tmp.write_text(json.dumps(t6_json, ensure_ascii=False), encoding="utf-8")
        command = _convlog_command(tmp, output_path)
        if command is None:
            print(f"  ERROR no compatible convlog executable; python converter error: {python_error}")
            return False
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ERROR convlog: {result.stderr.strip()}")
            return False
        return True
    finally:
        if tmp.exists():
            tmp.unlink()
