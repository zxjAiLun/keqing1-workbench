from __future__ import annotations

import gzip
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


NAGA_HOST = "naga.dmv.nico"
MORTAL_HOST = "mjai.ekyu.moe"
NAGA_MODEL_LABELS = ("NAGA ニシキ", "NAGA カガシ")
MORTAL_MODEL_LABEL = "Mortal 4.1c"

_TILE34 = {
    **{f"{index}m": index - 1 for index in range(1, 10)},
    **{f"{index}p": index + 8 for index in range(1, 10)},
    **{f"{index}s": index + 17 for index in range(1, 10)},
    "E": 27,
    "S": 28,
    "W": 29,
    "N": 30,
    "P": 31,
    "F": 32,
    "C": 33,
}
_TILE34.update({"5mr": 4, "5pr": 13, "5sr": 22})


def resolve_external_report_url(url: str, kind: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{kind} Review 链接必须使用 http/https")

    host = (parsed.hostname or "").lower()
    if kind == "naga":
        if host != NAGA_HOST:
            raise ValueError(f"NAGA Review 链接必须来自 {NAGA_HOST}")
        report_id = parse_qs(parsed.query).get("report_id", [""])[0].strip()
        if report_id:
            return f"https://{NAGA_HOST}/reports/{report_id}.json"
        if parsed.path.startswith("/reports/") and parsed.path.endswith(".json"):
            return url
        raise ValueError("NAGA Review 链接中未找到 report_id")

    if kind == "mortal":
        if host != MORTAL_HOST:
            raise ValueError(f"Mortal Review 链接必须来自 {MORTAL_HOST}")
        data_path = parse_qs(parsed.query).get("data", [""])[0].strip()
        if data_path:
            return urljoin(f"https://{MORTAL_HOST}/", data_path.lstrip("/"))
        if parsed.path.startswith("/report/") and parsed.path.endswith(".json"):
            return url
        raise ValueError("Mortal Review 链接中未找到 data 报告路径")

    raise ValueError(f"未知外部 Review 类型：{kind}")


def fetch_external_report(url: str, kind: str, *, timeout: int = 45, max_bytes: int = 16 * 1024 * 1024) -> dict:
    report_url = resolve_external_report_url(url, kind)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = Request(
                report_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; keqing1-review/1.0)",
                    "Accept": "application/json, */*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError(f"{kind} Review 报告超过 {max_bytes // (1024 * 1024)}MB 限制")
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"{kind} Review 报告根节点不是 JSON 对象")
            return value
        except Exception as exc:  # NAGA TLS occasionally closes the first handshake.
            last_error = exc
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
    raise ValueError(f"下载 {kind} Review 报告失败：{last_error}") from last_error


def _actual_action(entry: dict) -> dict | None:
    actual = entry.get("gt_action")
    if isinstance(actual, dict):
        return actual
    chosen = entry.get("chosen")
    if isinstance(chosen, dict) and chosen.get("type") == "none":
        return {"type": "none"}
    return None


def _action_actor(action: dict | None) -> int | None:
    if not isinstance(action, dict):
        return None
    actor = action.get("actor")
    if actor is None:
        return None
    try:
        return int(actor)
    except (TypeError, ValueError):
        return None


def _decision_kind(action: dict | None) -> str | None:
    """把实际动作归类为事件对齐用的 decision_kind。"""
    if not isinstance(action, dict):
        return None
    action_type = action.get("type")
    if action_type == "dahai":
        return "draw_discard"
    if action_type == "reach":
        return "reach"
    if action_type in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
        return "call"
    if action_type == "none":
        return "pass"
    return None


def _first_local_entry(decisions: dict) -> dict:
    entry = next(
        (
            item for item in decisions.get("log", [])
            if isinstance(item, dict) and not item.get("is_obs")
        ),
        None,
    )
    if entry is None:
        raise ValueError("本地 Review 没有可用于校验的决策")
    return entry


def _validate_first_kyoku(source: str, start: dict, decisions: dict, player_id: int) -> None:
    local = _first_local_entry(decisions)
    expected = (
        str(local.get("bakaze", "")),
        int(local.get("kyoku", 0)),
        int(local.get("honba", 0)),
        list(local.get("scores") or []),
        list(local.get("dora_markers") or []),
    )
    actual = (
        str(start.get("bakaze", "")),
        int(start.get("kyoku", 0)),
        int(start.get("honba", 0)),
        list(start.get("scores") or []),
        [start.get("dora_marker")] if start.get("dora_marker") is not None else list(start.get("dora_markers") or []),
    )
    tehais = start.get("tehais") or []
    source_hand = tehais[player_id] if player_id < len(tehais) else []
    if expected != actual or Counter(map(str, local.get("hand") or [])) != Counter(map(str, source_hand)):
        raise ValueError(f"{source} Review 报告与当前牌谱或玩家视角不一致")


def _same_hint(action: dict | None, hint: dict | None) -> bool:
    if not isinstance(action, dict) or not isinstance(hint, dict):
        return False
    if action.get("type") != hint.get("type"):
        return False
    for key in ("actor", "target", "pai"):
        if hint.get(key) is not None and action.get(key) != hint.get(key):
            return False
    return True


def _kyoku_key(entry: dict) -> tuple[str, int, int]:
    return str(entry.get("bakaze", "")), int(entry.get("kyoku", 0)), int(entry.get("honba", 0))


def _find_local_entry(
    own_entries: list[dict],
    start: int,
    key: tuple[str, int, int],
    hint: dict,
) -> tuple[int, dict] | None:
    for index in range(start, len(own_entries)):
        entry = own_entries[index]
        entry_key = _kyoku_key(entry)
        if entry_key != key:
            if entry_key[0:2] != key[0:2] and index > start:
                break
            continue
        if _same_hint(_actual_action(entry), hint):
            return index, entry
    return None


def _naga_huro_code(action: dict) -> str | None:
    action_type = action.get("type")
    if action_type == "none":
        return "0"
    if action_type == "pon":
        return "4"
    if action_type == "daiminkan":
        return "5"
    if action_type != "chi":
        return None
    called = _tile_rank(action.get("pai"))
    consumed = sorted(rank for rank in (_tile_rank(tile) for tile in action.get("consumed", [])) if rank is not None)
    if called is None or len(consumed) != 2:
        return None
    if called < consumed[0]:
        return "1"
    if consumed[0] < called < consumed[1]:
        return "2"
    return "3"


def _tile_rank(tile: Any) -> int | None:
    text = str(tile or "").replace("r", "")
    if len(text) != 2 or not text[0].isdigit():
        return None
    return int(text[0])


def _prob(value: Any) -> float | None:
    try:
        number = float(value) / 10000.0
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _naga_discard_details(entry: dict, event: dict, model_index: int) -> list[dict]:
    predictions = event.get("dahai_pred") or []
    tile_probs = predictions[model_index] if model_index < len(predictions) else []
    no_reach_prob = 1.0
    reach_values = event.get("reach") or []
    if model_index < len(reach_values):
        value = _prob(reach_values[model_index])
        if value is not None:
            no_reach_prob = value

    details: list[dict] = []
    for candidate in entry.get("candidates", []):
        action = candidate.get("action") if isinstance(candidate, dict) else None
        if not isinstance(action, dict):
            continue
        action_type = action.get("type")
        probability: float | None = None
        if action_type == "dahai":
            tile_index = _TILE34.get(str(action.get("pai")))
            if tile_index is not None and tile_index < len(tile_probs):
                probability = _prob(tile_probs[tile_index])
                if probability is not None:
                    probability *= no_reach_prob
        elif action_type == "reach" and model_index < len(reach_values):
            probability = 1.0 - no_reach_prob
        if probability is not None:
            details.append({"action": action, "q_value": None, "prob": probability})
    return details


def _reach_discard_actions(entry: dict, player_id: int) -> list[dict]:
    from mahjong_env.legal_actions import _reach_discard_candidates

    hand = Counter(map(str, entry.get("hand") or []))
    tsumo_pai = entry.get("tsumo_pai")
    if sum(hand.values()) % 3 == 1 and tsumo_pai:
        hand[str(tsumo_pai)] += 1
    return [
        {
            "type": "dahai",
            "actor": player_id,
            "pai": pai,
            "tsumogiri": tsumogiri,
        }
        for pai, tsumogiri in _reach_discard_candidates(hand, tsumo_pai, tsumo_pai)
    ]


def _naga_joint_reach_details(entry: dict, event: dict, player_id: int, model_index: int) -> list[dict]:
    predictions = event.get("dahai_pred") or []
    tile_probs = predictions[model_index] if model_index < len(predictions) else []
    reach_values = event.get("reach") or []
    if model_index >= len(reach_values):
        return []
    dama_prob = _prob(reach_values[model_index])
    if dama_prob is None:
        return []

    details: list[dict] = []
    for discard in _reach_discard_actions(entry, player_id):
        tile_index = _TILE34.get(str(discard.get("pai")))
        if tile_index is None or tile_index >= len(tile_probs):
            continue
        tile_prob = _prob(tile_probs[tile_index])
        if tile_prob is None:
            continue
        reach_action = {
            "type": "reach",
            "actor": player_id,
            "pai": discard["pai"],
            "tsumogiri": discard["tsumogiri"],
        }
        details.append({"action": discard, "q_value": None, "prob": tile_prob * dama_prob})
        details.append({"action": reach_action, "q_value": None, "prob": tile_prob * (1.0 - dama_prob)})
    return details


def _naga_huro_details(entry: dict, event: dict, player_id: int, model_index: int) -> list[dict]:
    per_player = (event.get("huro") or {}).get(str(player_id)) or []
    code_probs = per_player[model_index] if model_index < len(per_player) else {}
    details: list[dict] = []
    for candidate in entry.get("candidates", []):
        action = candidate.get("action") if isinstance(candidate, dict) else None
        if not isinstance(action, dict):
            continue
        code = _naga_huro_code(action)
        probability = _prob(code_probs.get(code)) if code is not None else None
        if probability is not None:
            details.append({"action": action, "q_value": None, "prob": probability})
    return details


def _next_call_hint(events: list[dict], event_index: int, player_id: int) -> dict:
    if event_index + 1 < len(events):
        message = ((events[event_index + 1].get("info") or {}).get("msg") or {})
        if message.get("actor") == player_id and message.get("type") in {"chi", "pon", "daiminkan"}:
            return {
                key: message.get(key)
                for key in ("type", "actor", "target", "pai", "consumed")
                if message.get(key) is not None
            }
    return {"type": "none"}


def build_naga_teacher_reports(raw: dict, decisions: dict, player_id: int, replay_id: str) -> list[dict]:
    naga_types = raw.get("naga_types") or {}
    model_names = [str(naga_types.get(str(index), f"model-{index + 1}")) for index in range(len(naga_types))]
    if not model_names or not isinstance(raw.get("pred"), list):
        raise ValueError("NAGA Review 报告缺少 pred/naga_types 数据")
    first_kyoku = raw["pred"][0] if raw["pred"] else []
    first_start = ((first_kyoku[0].get("info") or {}).get("msg") or {}) if first_kyoku else {}
    _validate_first_kyoku("NAGA", first_start, decisions, player_id)

    own_entries = [
        entry for entry in decisions.get("log", [])
        if isinstance(entry, dict) and not entry.get("is_obs")
    ]
    report_entries: list[list[dict]] = [[] for _ in model_names]
    cursor = 0

    for kyoku_events in raw["pred"]:
        if not isinstance(kyoku_events, list) or not kyoku_events:
            continue
        start_message = ((kyoku_events[0].get("info") or {}).get("msg") or {})
        key = (
            str(start_message.get("bakaze", "")),
            int(start_message.get("kyoku", 0)),
            int(start_message.get("honba", 0)),
        )
        for event_index, event in enumerate(kyoku_events):
            message = ((event.get("info") or {}).get("msg") or {})
            hint: dict | None = None
            detail_kind: str | None = None
            joint_actual: dict | None = None
            is_joint_reach = bool(event.get("reach"))
            if (
                message.get("actor") == player_id
                and message.get("type") in {"tsumo", "chi", "pon"}
                and message.get("real_dahai") not in {None, "?"}
                and event.get("dahai_pred")
            ):
                next_message = (
                    ((kyoku_events[event_index + 1].get("info") or {}).get("msg") or {})
                    if event_index + 1 < len(kyoku_events)
                    else {}
                )
                actual_reach = (
                    is_joint_reach
                    and next_message.get("type") == "reach"
                    and next_message.get("actor") == player_id
                )
                if actual_reach:
                    hint = {"type": "reach", "actor": player_id}
                    joint_actual = {
                        "type": "reach",
                        "actor": player_id,
                        "pai": message.get("real_dahai"),
                        "tsumogiri": bool(message.get("next_tsumogiri")),
                    }
                else:
                    hint = {"type": "dahai", "actor": player_id, "pai": message.get("real_dahai")}
                detail_kind = "joint_reach" if is_joint_reach else "dahai"
            elif str(player_id) in (event.get("huro") or {}):
                hint = _next_call_hint(kyoku_events, event_index, player_id)
                detail_kind = "huro"
            if hint is None:
                continue

            matched = _find_local_entry(own_entries, cursor, key, hint)
            if matched is None:
                continue
            index, local_entry = matched
            cursor = index + 1
            actual = joint_actual or _actual_action(local_entry)
            for model_index in range(len(model_names)):
                if detail_kind == "joint_reach":
                    details = _naga_joint_reach_details(local_entry, event, player_id, model_index)
                elif detail_kind == "dahai":
                    details = _naga_discard_details(local_entry, event, model_index)
                else:
                    details = _naga_huro_details(local_entry, event, player_id, model_index)
                if not details:
                    continue
                expected = max(details, key=lambda item: item.get("prob") or 0.0)["action"]
                report_entries[model_index].append(
                    {
                        "step": local_entry.get("step"),
                        "source_event_index": local_entry.get("source_event_index"),
                        "actor": _action_actor(actual),
                        "decision_kind": _decision_kind(actual),
                        "junme": local_entry.get("junme"),
                        "tiles_left": local_entry.get("tiles_left"),
                        "actual": actual,
                        "expected": expected,
                        "is_equal": _same_hint(actual, expected),
                        "display_mode": "joint_reach_dahai" if detail_kind == "joint_reach" else None,
                        "details": details,
                    }
                )

    reports = []
    for model_index, naga_name in enumerate(model_names):
        label = f"NAGA {naga_name}"
        reports.append(
            {
                "schema": "keqing1.external_teacher_report.v1",
                "source": "naga",
                "replay_id": replay_id,
                "player_id": player_id,
                "review": {
                    "model_tag": label,
                    "kyokus": [{"entries": report_entries[model_index]}],
                },
            }
        )
    return reports


def build_mortal_teacher_report(raw: dict, decisions: dict, player_id: int, replay_id: str) -> dict:
    report_player_id = raw.get("player_id")
    if report_player_id is None or int(report_player_id) != player_id:
        raise ValueError(
            f"Mortal Review 视角为 {report_player_id}，当前 GUI 视角为 {player_id}"
        )
    review = raw.get("review")
    if not isinstance(review, dict) or not isinstance(review.get("kyokus"), list):
        raise ValueError("Mortal Review 报告缺少 review.kyokus 数据")
    first_start = next(
        (event for event in raw.get("mjai_log", []) if isinstance(event, dict) and event.get("type") == "start_kyoku"),
        None,
    )
    if first_start is not None:
        _validate_first_kyoku("Mortal", first_start, decisions, player_id)
    result = dict(raw)
    result["schema"] = "keqing1.external_teacher_report.v1"
    result["source"] = "mortal"
    result["replay_id"] = replay_id
    result["review"] = dict(review)
    model_tag = str(review.get("model_tag") or "4.1c")
    result["review"]["model_tag"] = f"Mortal {model_tag}" if not model_tag.startswith("Mortal ") else model_tag
    return result


def write_external_teacher_reports(
    *,
    replay_id: str,
    player_id: int,
    decisions: dict,
    links: dict[str, str],
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict] = []
    if links.get("naga"):
        reports.extend(build_naga_teacher_reports(fetch_external_report(links["naga"], "naga"), decisions, player_id, replay_id))
    if links.get("mortal"):
        reports.append(
            build_mortal_teacher_report(
                fetch_external_report(links["mortal"], "mortal"),
                decisions,
                player_id,
                replay_id,
            )
        )

    paths: list[Path] = []
    for report in reports:
        label = str(report["review"]["model_tag"])
        safe_label = label.replace("@", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")
        path = output_dir / f"{replay_id}__{safe_label}__p{player_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        paths.append(path)
    return paths
