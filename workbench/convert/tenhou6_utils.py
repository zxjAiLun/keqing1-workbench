"""tenhou6 JSON → mjai JSONL 转换工具。"""

from __future__ import annotations

import json
import os
import re
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


def _normalize_marker(tile: str) -> str:
    """Red-five suffix is stripped ('5sr' -> '5s'); '?' placeholders are kept."""
    text = str(tile)
    if text == "?":
        return text
    return text[:-1] if text.endswith("r") else text


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


def _tile_codes(values: Any) -> list[str]:
    """tenhou6 tile-code list -> mjai tile faces（非法/空值一律跳过）。"""
    if not isinstance(values, list):
        return []
    tiles: list[str] = []
    for value in values:
        try:
            tiles.append(_tile_from_tenhou6(value))
        except (TypeError, ValueError):
            continue
    return tiles


def _start_kyoku_event(kyoku: list[Any], names: list[str], rule: dict[str, Any]) -> dict[str, Any]:
    del names, rule
    meta = kyoku[0]
    round_index = int(meta[0])
    round_base = (round_index // 4) * 4
    # tenhou6 每局字段：kyoku[2] = [開局宝牌指示牌, 杠宝牌指示牌...]，
    # kyoku[3] = [裏宝牌指示牌, 杠裏宝牌指示牌...]（后者只对已立直的和了有效）。
    dora_codes = kyoku[2] if len(kyoku) > 2 and isinstance(kyoku[2], list) else []
    ura_codes = kyoku[3] if len(kyoku) > 3 and isinstance(kyoku[3], list) else []
    return {
        "type": "start_kyoku",
        "bakaze": _ROUND_BY_OFFSET.get(round_base, "E"),
        "dora_marker": _tile_codes(dora_codes)[0] if dora_codes else None,
        # 里宝牌指示牌：缺失时输出空列表（已知无里宝牌），不是省略字段。
        "ura_dora_markers": _tile_codes(ura_codes),
        "kyoku": round_index % 4 + 1,
        "honba": int(meta[1]) if len(meta) > 1 else 0,
        "kyotaku": int(meta[2]) if len(meta) > 2 else 0,
        "oya": round_index % 4,
        "scores": [int(score) for score in kyoku[1]],
        "tehais": [[_tile_from_tenhou6(tile) for tile in kyoku[4 + seat * 3]] for seat in range(4)],
    }


# 天凤原生结算串的等级前缀（如 "三倍満36000点"）；自摸串形如 "満貫4000点∀"。
_TENHOU_LEVELS = (
    ("六倍満", "6x yakuman"),
    ("五倍満", "5x yakuman"),
    ("四倍満", "4x yakuman"),
    ("三倍満", "sanbaiman"),
    ("倍満", "baiman"),
    ("跳満", "haneman"),
    ("満貫", "mangan"),
    ("役満", "yakuman"),
)
# 结算串中出现限制役字样（"満貫4000点∀" / "三倍満36000点"）时视为限制手。
_TENHOU_LIMIT_RE = re.compile("|".join(level for level, _ in _TENHOU_LEVELS))
# 天凤役种名（日文）→ 本仓 yaku key（与 mahjong 库命名保持一致）。
_TENHOU_YAKU_ALIASES = {
    "立直": "Riichi",
    "一発": "Ippatsu",
    "門前清自摸和": "Menzen Tsumo",
    "断幺九": "Tanyao",
    "混全帯幺九": "Chantai",
    "純全帯幺九": "Junchan",
    "平和": "Pinfu",
    "一盃口": "Iipeiko",
    "二盃口": "Ryanpeikou",
    "三色同順": "Sanshoku Doujun",
    "三色同刻": "Sanshoku Doukou",
    "一気通貫": "Ittsu",
    "混一色": "Honitsu",
    "清一色": "Chinitsu",
    "対々和": "Toitoi",
    "三暗刻": "Sanankou",
    "三槓子": "Sankantsu",
    "混老頭": "Honroutou",
    "七対子": "Chiitoitsu",
    "小三元": "Shousangen",
    "混幺九": "Honroutou",
    "ドラ": "Dora",
    "裏ドラ": "Ura Dora",
    "赤ドラ": "Aka Dora",
    "役牌 白": "Yakuhai (haku)",
    "役牌 發": "Yakuhai (hatsu)",
    "役牌 中": "Yakuhai (chun)",
    "役牌 東": "Yakuhai (east)",
    "役牌 南": "Yakuhai (south)",
    "役牌 西": "Yakuhai (west)",
    "役牌 北": "Yakuhai (north)",
    "嶺上開花": "Rinshan",
    "搶槓": "Chankan",
    "海底摸月": "Haitei Raoyue",
    "河底撈魚": "Houtei Raoyui",
    "天和": "Tenhou",
    "地和": "Chiihou",
}


def _parse_tenhou_yaku_detail(text: str) -> tuple[str, str, int] | None:
    """'立直(1飜)' -> ('Riichi', 'Riichi', 1)；无法识别则返回 None。"""
    if not text.endswith("飜)") and not text.endswith("飜）"):
        return None
    open_index = max(text.rfind("("), text.rfind("（"))
    if open_index <= 0:
        return None
    name = text[:open_index].strip()
    digits = "".join(ch for ch in text[open_index:] if ch.isdigit())
    if not name or not digits:
        return None
    key = _TENHOU_YAKU_ALIASES.get(name, name)
    return (key, name, int(digits))


def _parse_tenhou_result_fields(fields: list[Any]) -> dict[str, Any]:
    """Parse Tenhou's native result detail (fields[3:]) into our settlement shape.

    Tenhou already states the authoritative limit/score label and the full yaku
    list, so when this succeeds we prefer it over recomputing the hand.  Returns
    ``{}`` when the shape is not recognised, leaving the caller free to fall back.
    """
    if len(fields) < 4 or not isinstance(fields[3], str):
        return {}
    parsed: dict[str, Any] = {"result_label": fields[3]}
    if _TENHOU_LIMIT_RE.search(fields[3]):
        parsed["is_limit"] = True
    yaku_details: list[dict[str, Any]] = []
    for item in fields[4:]:
        if not isinstance(item, str):
            continue
        detail = _parse_tenhou_yaku_detail(item)
        if detail is not None:
            yaku_details.append({"key": detail[0], "name": detail[1], "han": detail[2]})
    if yaku_details:
        parsed["yaku_details"] = yaku_details
        parsed["yaku"] = [d["name"] for d in yaku_details]
        parsed["han"] = sum(d["han"] for d in yaku_details)
    return parsed


def _result_events(result: list[Any], *, ura_dora_markers: list[str] | None = None) -> list[dict[str, Any]]:
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
        event: dict[str, Any] = {
            "type": "hora",
            "actor": actor,
            "target": target,
            "deltas": deltas,
        }
        # 天凤原生已给出等级/点数/役种（如 "三倍満36000点" + "裏ドラ(5飜)"），
        # 直接携带为权威结算，避免本地重算丢失里宝牌等信息。
        event.update(_parse_tenhou_result_fields(detail))
        # 里宝牌指示牌整局透传；打分层的立直守卫决定是否生效。
        event["ura_dora_markers"] = list(ura_dora_markers or [])
        events.append(event)
    return events


REACH_ACCEPT_TRIGGERS = ("tsumo", "chi", "pon", "daiminkan")


def insert_reach_accepted(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 upstream convlog 状态机补齐 ``reach_accepted``（幂等）。

    立直宣言与成立是两件事（R12-A Repair）：
    - ``reach`` 之后的首个 ``dahai`` 是宣言牌；
    - 牌局继续（下一次 take / call：tsumo/chi/pon/daiminkan 真正发生）前，
      才注入一次 ``reach_accepted(actor)``——立直成立，立直棒 1000 才被扣除；
    - 宣言牌直接被荣和、或局在宣言牌后结束（hora/ryukyoku/end_kyoku 紧跟），
      不注入——立直未成立；
    - 输入流已自带对应 ``reach_accepted`` 时不再注入（绝不扣两次 1000）。
    """
    out: list[dict[str, Any]] = []
    declared: set[int] = set()  # 已 reach，尚未见到宣言牌
    accepted: set[int] = set()  # 输入流已自带 reach_accepted 的立直
    pending: list[int] = []  # 宣言牌已出、等待成立判定的立直者
    for event in events:
        row = dict(event)
        event_type = row.get("type")
        raw_actor = row.get("actor")
        actor = int(raw_actor) if raw_actor is not None else -1
        if event_type == "reach" and actor >= 0:
            declared.add(actor)
        elif event_type == "dahai" and actor in declared:
            declared.discard(actor)
            if actor in accepted:
                accepted.discard(actor)  # 先 accepted 后宣言牌的异常顺序：不二次注入
            else:
                pending.append(actor)
        elif event_type == "reach_accepted" and actor in declared:
            declared.discard(actor)
            accepted.add(actor)
        if pending:
            if event_type == "reach_accepted" and actor in pending:
                pending.remove(actor)
                accepted.add(actor)
            elif event_type in REACH_ACCEPT_TRIGGERS:
                for declarer in pending:
                    out.append({"type": "reach_accepted", "actor": declarer})
                pending.clear()
            elif event_type in ("hora", "ryukyoku", "end_kyoku", "end_game"):
                pending.clear()  # 宣言牌即终局：立直未成立
        out.append(row)
    return out


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
    # 里宝牌指示牌在 kyoku[3]（第 1 个为开局里宝牌，其余为杠里宝牌）。
    # 该字段只对已立直的和了生效，此处整局透传，由打分层的立直守卫决定是否计入。
    ura_dora_markers = _tile_codes(kyoku[3] if len(kyoku) > 3 else [])

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

    events.extend(_result_events(kyoku[-1] if kyoku else [], ura_dora_markers=ura_dora_markers))
    events.append({"type": "end_kyoku"})
    # R12-A Repair：立直成立与否按 upstream convlog 状态机决定（宣言牌被荣和
    # 或局在宣言牌后结束 → 不成立）。canonical converter 统一产出正确语义，
    # 统计/Replay 等所有消费者共享同一套事件。
    return insert_reach_accepted(events)


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
