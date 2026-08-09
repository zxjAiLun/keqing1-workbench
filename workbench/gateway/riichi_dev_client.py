from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import re
import socket
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import websockets
from dotenv import load_dotenv
from websockets.exceptions import ConnectionClosed
from riichienv import Observation, Observation3P, RiichiEnv

from mahjong_env.action_space import IDX_TO_TILE_NAME
from mahjong_env.tiles import normalize_tile
from mahjong.tile import FIVE_RED_MAN, FIVE_RED_PIN, FIVE_RED_SOU

logger = logging.getLogger(__name__)
load_dotenv()

DEFAULT_BASE_URL = "wss://game.riichi.dev"
DEFAULT_VALIDATE_PATH = "/ws/validate"
DEFAULT_RANKED_PATH = "/ws/ranked"
_ROUND_WINDS = ("E", "S", "W", "N")
DEFAULT_ACTION_DEADLINE_MS = 2500.0
_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "no_proxy",
)
_HTTP_PROXY_SCHEMES = {"http"}
_MELD_TYPE_MAP = {
    "Chi": "chi",
    "Pon": "pon",
    "Kan": "daiminkan",
    "ClosedKan": "ankan",
    "AddedKan": "kakan",
}


def _resolve_ws_url(base_url: str, queue: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.startswith("https://"):
        normalized = "wss://" + normalized.removeprefix("https://")
    elif normalized.startswith("http://"):
        normalized = "ws://" + normalized.removeprefix("http://")
    if queue == "validate":
        return f"{normalized}{DEFAULT_VALIDATE_PATH}"
    if queue == "ranked":
        return f"{normalized}{DEFAULT_RANKED_PATH}"
    raise ValueError(f"unsupported queue: {queue}")


def _resolve_default_token(bot_name: str) -> str:
    return os.getenv("LATTEKEY", os.getenv("RIICHI_BOT_TOKEN", ""))


def _resolve_default_token_with_source(bot_name: str) -> tuple[str, str]:
    if os.getenv("LATTEKEY"):
        return os.getenv("LATTEKEY", ""), "LATTEKEY"
    return os.getenv("RIICHI_BOT_TOKEN", ""), "RIICHI_BOT_TOKEN"


def _redact_proxy_value(value: str) -> str:
    if "@" not in value:
        return value
    scheme, sep, rest = value.partition("://")
    if not sep:
        return "<set-with-credentials>"
    _userinfo, at, host = rest.rpartition("@")
    if not at:
        return value
    return f"{scheme}://<redacted>@{host}"


def _proxy_env_for_log() -> dict[str, str]:
    return {
        key: _redact_proxy_value(value)
        for key in _PROXY_ENV_KEYS
        if (value := os.getenv(key))
    }


def _redact_url_for_log(value: str | None) -> str | None:
    if not value:
        return None
    return _redact_proxy_value(value)


def _websockets_connect_supports_proxy_arg() -> bool:
    try:
        return "proxy" in inspect.signature(websockets.connect).parameters
    except (TypeError, ValueError):
        return False


def _websockets_connect_source_mentions_proxy() -> bool:
    try:
        source = inspect.getsource(websockets.connect)
    except (OSError, TypeError):
        return False
    lowered = source.lower()
    return "proxy" in lowered or "getproxies" in lowered


def _websocket_diagnostics(*, disable_ws_proxy: bool, ws_proxy: str | None = None) -> dict[str, Any]:
    proxy_arg_supported = _websockets_connect_supports_proxy_arg()
    proxy_env = _proxy_env_for_log()
    proxy_disabled_by_kwarg = bool(disable_ws_proxy and proxy_arg_supported)
    proxy_env_used_by_websockets = bool(
        proxy_env
        and (
            (proxy_arg_supported and not disable_ws_proxy)
            or (not proxy_arg_supported and _websockets_connect_source_mentions_proxy())
        )
    )
    return {
        "websockets_version": getattr(websockets, "__version__", "unknown"),
        "proxy_env": proxy_env,
        "explicit_ws_proxy": _redact_url_for_log(ws_proxy),
        "proxy_arg_supported": proxy_arg_supported,
        "proxy_disabled_by_kwarg": proxy_disabled_by_kwarg,
        "proxy_env_used_by_websockets": proxy_env_used_by_websockets and not ws_proxy,
        "manual_http_connect_proxy": bool(ws_proxy),
    }


def _proxy_basic_auth_header(parsed_proxy: urllib.parse.ParseResult) -> str | None:
    if parsed_proxy.username is None:
        return None
    username = urllib.parse.unquote(parsed_proxy.username)
    password = urllib.parse.unquote(parsed_proxy.password or "")
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _open_http_connect_socket(
    *,
    ws_url: str,
    proxy_url: str,
    timeout: float | None,
) -> socket.socket:
    parsed_ws = urllib.parse.urlparse(ws_url)
    if parsed_ws.scheme != "wss":
        raise ValueError(f"HTTP CONNECT proxy is only supported for wss URLs: {ws_url!r}")
    target_host = parsed_ws.hostname
    if not target_host:
        raise ValueError(f"WebSocket URL missing host: {ws_url!r}")
    target_port = parsed_ws.port or 443

    parsed_proxy = urllib.parse.urlparse(proxy_url)
    if parsed_proxy.scheme not in _HTTP_PROXY_SCHEMES:
        raise ValueError(
            "ws_proxy currently supports HTTP CONNECT proxies only; "
            f"got scheme={parsed_proxy.scheme!r}"
        )
    proxy_host = parsed_proxy.hostname
    if not proxy_host:
        raise ValueError(f"proxy URL missing host: {proxy_url!r}")
    proxy_port = parsed_proxy.port or 8080

    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        request_lines = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
            "Proxy-Connection: Keep-Alive",
        ]
        auth_header = _proxy_basic_auth_header(parsed_proxy)
        if auth_header is not None:
            request_lines.append(f"Proxy-Authorization: {auth_header}")
        request = ("\r\n".join(request_lines) + "\r\n\r\n").encode("ascii")
        sock.sendall(request)

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("HTTP proxy closed while establishing CONNECT tunnel")
            response += chunk
            if len(response) > 65536:
                raise ConnectionError("HTTP proxy CONNECT response exceeded 64 KiB")
        status_line = response.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or parts[1] != "200":
            raise ConnectionError(f"HTTP proxy CONNECT failed: {status_line}")
        sock.setblocking(False)
        return sock
    except Exception:
        sock.close()
        raise


def _format_connection_closed_message(queue: str, code: int, reason: str | None) -> str:
    return (
        f"{queue} connection closed by server "
        f"(code={code}, reason={reason or 'none'}). "
        "Likely causes: bot is not active yet, another session is using the same bot, "
        f"or the server rejected the {queue} queue request."
    )


def _resolve_model_path(
    *,
    bot_name: str,
    project_root: str | Path,
    model_path: str | Path | None,
) -> Path | None:
    if bot_name == "rulebase":
        return None
    if model_path is not None:
        return Path(model_path)
    # Named Mortal checkpoints now live under the shared keqing-data root
    # (authoritative models dir).  Reuse bot_registry's family-relative anchor
    # for the "mortal" default so V2/V3-style collisions never get guessed.
    from inference.bot_registry import MORTAL_CHECKPOINTS, resolve_model_checkpoint

    if bot_name == "mortal":
        anchor = MORTAL_CHECKPOINTS["70k"]
    else:
        anchor = Path("artifacts") / "models" / bot_name / "best.pth"
    try:
        return resolve_model_checkpoint(anchor, project_root)
    except FileNotFoundError:
        return None


def _decode_jwt_payload_unverified(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        decoded = json.loads(payload.decode("utf-8"))
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _log_startup_self_check(
    *,
    queue: str,
    bot_name: str,
    model_version: str | None,
    token: str,
    token_source: str,
    project_root: Path,
    model_path: Path | None,
    validation_safe: bool,
) -> None:
    should_log = logger.isEnabledFor(logging.DEBUG)
    token_payload = _decode_jwt_payload_unverified(token)
    token_name = token_payload.get("name") if token_payload else None
    token_bot_id = token_payload.get("bot_id") if token_payload else None
    token_type = token_payload.get("type") if token_payload else None
    token_summary = (
        f"name={token_name or 'unknown'} "
        f"type={token_type or 'unknown'} "
        f"bot_id={token_bot_id or 'unknown'}"
    )
    if should_log:
        logger.info(
            "startup self-check: queue=%s bot_name=%s model_version=%s token_source=%s token_identity=%s",
            queue,
            bot_name,
            model_version or bot_name,
            token_source,
            token_summary,
        )
        logger.info("startup self-check: project_root=%s", project_root)
    if validation_safe:
        if should_log:
            logger.info("startup self-check: validation_safe=true, model checkpoint skipped")
        return
    if model_path is None:
        if should_log:
            logger.info("startup self-check: model checkpoint skipped for bot_name=%s", bot_name)
        return
    if should_log:
        logger.info(
            "startup self-check: model_path=%s exists=%s",
            model_path,
            model_path.exists(),
        )
    if not model_path.exists():
        raise SystemExit(f"model checkpoint not found: {model_path}")


def _decode_observation(message: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    encoded = message.get("observation")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("request_action message missing observation")

    player_count = message.get("player_count")
    if player_count == 3:
        obs = Observation3P.deserialize_from_base64(encoded)
    elif player_count == 4:
        obs = Observation.deserialize_from_base64(encoded)
    else:
        try:
            obs = Observation.deserialize_from_base64(encoded)
        except Exception:
            obs = Observation3P.deserialize_from_base64(encoded)
    return obs, _normalize_observation_state(obs, obs.to_dict())


def _hash_observation_payload(encoded: str | None) -> str | None:
    if not encoded:
        return None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _audit_message_meta(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": message.get("type"),
        "seat": message.get("seat"),
        "player_count": message.get("player_count"),
        "has_observation": isinstance(message.get("observation"), str) and bool(message.get("observation")),
        "observation_sha256": message.get("_observation_sha256"),
        "decode_error": message.get("_observation_decode_error"),
    }


def _enrich_message_for_audit(message: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(message)
    encoded = enriched.get("observation")
    if isinstance(encoded, str) and encoded:
        enriched["_observation_sha256"] = _hash_observation_payload(encoded)
        if "_normalized_observation" not in enriched:
            try:
                _obs, normalized = _decode_observation(enriched)
                enriched["_normalized_observation"] = normalized
                enriched["_new_events"] = _observation_new_events(_obs)
            except Exception as exc:
                enriched["_observation_decode_error"] = f"{type(exc).__name__}: {exc}"
    return enriched


def _sanitize_action(action: dict[str, Any], actor_hint: int | None) -> dict[str, Any]:
    action_type = action.get("type", "none")
    if action_type == "none":
        actor = action.get("actor", actor_hint)
        out = {"type": "none"}
        if actor is not None:
            out["actor"] = actor
        return out
    if action_type == "ryukyoku":
        return {"type": "ryukyoku"}

    if action_type == "hora":
        actor = action.get("actor", actor_hint)
        out = {"type": "hora", "actor": actor}
        target = action.get("target")
        if target is not None:
            out["target"] = target
        if target is not None and target != actor and action.get("pai") is not None:
            out["pai"] = action["pai"]
        return out
    if action_type == "reach":
        actor = action.get("actor", actor_hint)
        return {"type": "reach", "actor": actor}
    if action_type == "dahai":
        actor = action.get("actor", actor_hint)
        out = {"type": "dahai", "actor": actor, "pai": action["pai"]}
        if "tsumogiri" in action:
            out["tsumogiri"] = bool(action["tsumogiri"])
        return out
    if action_type == "chi":
        actor = action.get("actor", actor_hint)
        out = {
            "type": "chi",
            "actor": actor,
            "pai": action["pai"],
            "consumed": list(action["consumed"]),
        }
        if "target" in action:
            out["target"] = action["target"]
        return out
    if action_type == "pon":
        actor = action.get("actor", actor_hint)
        out = {
            "type": "pon",
            "actor": actor,
            "pai": action["pai"],
            "consumed": list(action["consumed"]),
        }
        if "target" in action:
            out["target"] = action["target"]
        return out
    if action_type == "daiminkan":
        actor = action.get("actor", actor_hint)
        out = {
            "type": "daiminkan",
            "actor": actor,
            "pai": action["pai"],
            "consumed": list(action["consumed"]),
        }
        if "target" in action:
            out["target"] = action["target"]
        return out
    if action_type == "ankan":
        actor = action.get("actor", actor_hint)
        out = {
            "type": "ankan",
            "actor": actor,
            "consumed": list(action["consumed"]),
        }
        pai = action.get("pai")
        if pai is None and out["consumed"]:
            pai = out["consumed"][0]
        if pai is not None:
            out["pai"] = pai
        return out
    if action_type == "kakan":
        actor = action.get("actor", actor_hint)
        return {
            "type": "kakan",
            "actor": actor,
            "pai": action["pai"],
            "consumed": list(action["consumed"]),
        }

    out = dict(action)
    if "actor" not in out and actor_hint is not None:
        out["actor"] = actor_hint
    return out


def _action_to_mjai_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        return dict(action)
    if hasattr(action, "to_mjai"):
        mjai = action.to_mjai()
        if isinstance(mjai, str):
            return json.loads(mjai)
        if isinstance(mjai, dict):
            return dict(mjai)
    if isinstance(action, str):
        return json.loads(action)
    raise TypeError(f"unsupported action payload type: {type(action)!r}")


def _meld_type_key(meld_type: Any) -> str | None:
    if meld_type is None:
        return None
    name = getattr(meld_type, "name", None)
    if isinstance(name, str):
        return name
    text = str(meld_type)
    return text.rsplit(".", 1)[-1] if text else None


def _last_unanswered_self_tsumo_pai(
    new_events: list[dict[str, Any]] | None,
    actor: int | None,
) -> str | None:
    if actor is None:
        return None
    for event in reversed(new_events or []):
        event_type = event.get("type")
        if event.get("actor") != actor:
            continue
        if event_type == "tsumo":
            pai = event.get("pai")
            return pai if isinstance(pai, str) and pai != "?" else None
        if event_type in {"dahai", "chi", "pon", "daiminkan", "ankan", "kakan", "hora"}:
            return None
    return None


def _reach_followup_tsumo_pai(
    *,
    new_events: list[dict[str, Any]] | None,
    state: Mapping[str, Any] | None,
    actor: int | None,
) -> str | None:
    if actor is None or not state:
        return None
    has_self_reach = any(
        event.get("actor") == actor and event.get("type") == "reach"
        for event in (new_events or [])
    )
    if not has_self_reach:
        return None
    hand = state.get("hand")
    if isinstance(hand, Sequence) and not isinstance(hand, (str, bytes)) and hand:
        pai = hand[-1]
        return pai if isinstance(pai, str) and pai != "?" else None
    return None


def _last_dahai_event(events: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for event in reversed(events or []):
        if event.get("type") == "dahai":
            return event
    return None


def _riichi_dev_wire_action(
    action: Any,
    *,
    actor_hint: int | None = None,
    new_events: list[dict[str, Any]] | None = None,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = _sanitize_action(_action_to_mjai_dict(action), actor_hint)
    if out.get("type") == "none":
        return {"type": "none"}
    if out.get("type") == "dahai" and "tsumogiri" not in out:
        actor = out.get("actor", actor_hint)
        last_tsumo = _last_unanswered_self_tsumo_pai(new_events, actor)
        if last_tsumo is None:
            last_tsumo = _reach_followup_tsumo_pai(
                new_events=new_events,
                state=state,
                actor=actor,
            )
        out["tsumogiri"] = bool(last_tsumo is not None and out.get("pai") == last_tsumo)
    if out.get("type") in {"chi", "pon", "daiminkan"} and "target" not in out:
        last_dahai = _last_dahai_event(new_events)
        target = last_dahai.get("actor") if last_dahai is not None else None
        if target is None:
            logger.warning(
                "riichi.dev wire action missing call target; sending none instead: action=%s",
                _action_log_text(out),
            )
            return {"type": "none"}
        out["target"] = target
    return out


def _action_to_mjai_wire_payload(
    action: Any,
    *,
    actor_hint: int | None = None,
    new_events: list[dict[str, Any]] | None = None,
    state: Mapping[str, Any] | None = None,
) -> str:
    return json.dumps(
        _riichi_dev_wire_action(
            action,
            actor_hint=actor_hint,
            new_events=new_events,
            state=state,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _observation_new_events(obs: Any) -> list[dict[str, Any]]:
    raw_events = getattr(obs, "new_events", None)
    if raw_events is None:
        raw_events = getattr(obs, "events", [])
    if callable(raw_events):
        raw_events = raw_events()
    events: list[dict[str, Any]] = []
    for event in raw_events or []:
        if isinstance(event, dict):
            events.append(dict(event))
        elif isinstance(event, str):
            events.append(json.loads(event))
        else:
            events.append(_action_to_mjai_dict(event))
    return events


def _action_exact_key(action: dict[str, Any]) -> str:
    normalized = dict(action)
    if "consumed" in normalized:
        normalized["consumed"] = sorted(str(tile) for tile in normalized["consumed"])
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _same_meld_action(candidate: dict[str, Any], legal: dict[str, Any]) -> bool:
    action_type = candidate.get("type")
    if action_type not in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
        return False
    if legal.get("type") != action_type:
        return False
    if candidate.get("actor") != legal.get("actor"):
        return False
    if candidate.get("pai") != legal.get("pai"):
        return False
    return sorted(str(tile) for tile in candidate.get("consumed", [])) == sorted(
        str(tile) for tile in legal.get("consumed", [])
    )


def _same_meld_echo_action(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    action_type = expected.get("type")
    if action_type != observed.get("type"):
        return False
    if expected.get("actor") != observed.get("actor"):
        return False
    if expected.get("pai") != observed.get("pai"):
        return False
    if "target" in expected and "target" in observed and expected.get("target") != observed.get("target"):
        return False
    if action_type == "chi":
        return True
    return _same_meld_action(expected, observed)


def _same_hora_action(candidate: dict[str, Any], legal: dict[str, Any]) -> bool:
    if candidate.get("type") != "hora" or legal.get("type") != "hora":
        return False
    if candidate.get("actor") != legal.get("actor"):
        return False
    if "target" in legal and "target" in candidate and legal.get("target") != candidate.get("target"):
        return False
    if "pai" in legal and "pai" in candidate and legal.get("pai") != candidate.get("pai"):
        return False
    return True


def _same_dahai_tile(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left == right
    return normalize_tile(str(left)) == normalize_tile(str(right))


def _action_log_text(action: dict[str, Any] | None) -> str:
    if action is None:
        return "null"
    return json.dumps(action, sort_keys=True, ensure_ascii=False)


_ECHO_TRACKED_ACTION_TYPES = {
    "reach",
    "dahai",
    "chi",
    "pon",
    "daiminkan",
    "ankan",
    "kakan",
    "hora",
}


class RiichiDevActionEchoMismatch(RuntimeError):
    pass


class RiichiDevStaleActionDeadline(RuntimeError):
    pass


class RiichiDevServerIllegalAction(RuntimeError):
    pass


def _echo_tracked_action(action: dict[str, Any] | None) -> dict[str, Any] | None:
    if not action or action.get("type") not in _ECHO_TRACKED_ACTION_TYPES:
        return None
    return _sanitize_action(action, action.get("actor"))


def _actions_equivalent_for_echo(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    expected = _sanitize_action(expected, expected.get("actor"))
    observed = _sanitize_action(observed, expected.get("actor"))
    if expected.get("type") != observed.get("type"):
        return False
    if expected.get("actor") != observed.get("actor"):
        return False
    action_type = expected.get("type")
    if action_type == "dahai":
        return _same_dahai_tile(expected.get("pai"), observed.get("pai"))
    if action_type in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
        return _same_meld_echo_action(expected, observed)
    if action_type == "hora":
        return _same_hora_action(expected, observed)
    return expected == observed


def _looks_like_server_timeout_fallback(
    expected: dict[str, Any],
    observed: dict[str, Any],
) -> bool:
    if expected.get("actor") != observed.get("actor"):
        return False
    if observed.get("type") != "dahai":
        return False
    return bool(observed.get("tsumogiri"))


def _first_echo_candidate(
    events: list[dict[str, Any]],
    expected: dict[str, Any],
) -> dict[str, Any] | None:
    actor = expected.get("actor")
    if actor is None:
        return None
    for event in events:
        if event.get("type") == "start_kyoku":
            return None
        if event.get("actor") != actor:
            continue
        if event.get("type") in _ECHO_TRACKED_ACTION_TYPES:
            return event
    return None


def _server_illegal_action_offenders(event: dict[str, Any]) -> set[int]:
    offenders: set[int] = set()
    penalized = event.get("penalized")
    if isinstance(penalized, Mapping):
        for key, value in penalized.items():
            if not value:
                continue
            try:
                offenders.add(int(key))
            except (TypeError, ValueError):
                continue
    elif isinstance(penalized, Sequence) and not isinstance(penalized, str):
        offenders.update(idx for idx, value in enumerate(penalized) if value)

    reason = str(event.get("reason") or "")
    for match in re.finditer(r"\bPlayer\s+([0-3])\b", reason, flags=re.IGNORECASE):
        offenders.add(int(match.group(1)))

    deltas = event.get("deltas")
    if not offenders and isinstance(deltas, Sequence) and not isinstance(deltas, str):
        for idx, delta in enumerate(deltas):
            if isinstance(delta, (int, float)) and delta <= -8000:
                offenders.add(idx)
    return offenders


def _server_illegal_action_event(
    events: list[dict[str, Any]],
    *,
    seat: int | None = None,
) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") != "ryukyoku":
            continue
        reason = str(event.get("reason") or "")
        penalized = event.get("penalized")
        has_penalty = False
        if isinstance(penalized, Mapping):
            has_penalty = any(bool(value) for value in penalized.values())
        elif isinstance(penalized, Sequence) and not isinstance(penalized, str):
            has_penalty = any(bool(value) for value in penalized)
        else:
            has_penalty = bool(penalized)
        if not (has_penalty or "Illegal Action" in reason or "Penalty" in reason):
            continue
        if seat is None:
            return event
        offenders = _server_illegal_action_offenders(event)
        if not offenders or int(seat) in offenders:
            return event
    return None


def _warn_mortal_legality_fallback(
    *,
    reason: str,
    actor: int | None,
    candidate: dict[str, Any] | None,
    fallback: dict[str, Any],
    legal_actions: list[dict[str, Any]],
) -> None:
    legal_sample = legal_actions[:8]
    logger.warning(
        "mortal legality guard fallback: reason=%s actor=%s candidate=%s fallback=%s legal_count=%d legal_sample=%s",
        reason,
        actor,
        _action_log_text(candidate),
        _action_log_text(fallback),
        len(legal_actions),
        json.dumps(legal_sample, sort_keys=True, ensure_ascii=False),
    )


def _legalize_action(
    action: dict[str, Any] | None,
    *,
    legal_actions: list[dict[str, Any]],
    actor: int | None,
) -> dict[str, Any]:
    normalized_legal = [
        _sanitize_action(_action_to_mjai_dict(item), actor)
        for item in legal_actions
    ]
    if not normalized_legal:
        return {"type": "none"}

    candidate = _sanitize_action(action or {"type": "none"}, actor)
    reason = "missing_action" if action is None else "illegal_action"
    candidate_key = _action_exact_key(candidate)
    for legal in normalized_legal:
        if _action_exact_key(legal) == candidate_key:
            return legal

    if candidate.get("type") == "dahai":
        for legal in normalized_legal:
            if (
                legal.get("type") == "dahai"
                and legal.get("actor") == candidate.get("actor")
                and _same_dahai_tile(legal.get("pai"), candidate.get("pai"))
            ):
                return legal
    if candidate.get("type") in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
        for legal in normalized_legal:
            if _same_meld_action(candidate, legal):
                return legal
    if candidate.get("type") == "hora":
        for legal in normalized_legal:
            if _same_hora_action(candidate, legal):
                return legal

    for legal in normalized_legal:
        if legal.get("type") == "none":
            _warn_mortal_legality_fallback(
                reason=reason,
                actor=actor,
                candidate=action,
                fallback=legal,
                legal_actions=normalized_legal,
            )
            return legal
    fallback = normalized_legal[0]
    _warn_mortal_legality_fallback(
        reason=reason,
        actor=actor,
        candidate=action,
        fallback=fallback,
        legal_actions=normalized_legal,
    )
    return fallback


def _select_observation_action(obs: Any, action: dict[str, Any], actor: int) -> Any:
    selected = obs.select_action_from_mjai(json.dumps(action, ensure_ascii=False))
    if selected is not None:
        return selected

    legal_actions = [_action_to_mjai_dict(item) for item in obs.legal_actions()]
    fallback = _legalize_action(action, legal_actions=legal_actions, actor=actor)
    selected = obs.select_action_from_mjai(json.dumps(fallback, ensure_ascii=False))
    if selected is None:
        raise RuntimeError(f"action could not be converted by RiichiEnv: {fallback}")
    return selected


def _tile136_to_str(tile136: int) -> str:
    if tile136 == FIVE_RED_MAN:
        return "5mr"
    if tile136 == FIVE_RED_PIN:
        return "5pr"
    if tile136 == FIVE_RED_SOU:
        return "5sr"
    return IDX_TO_TILE_NAME[int(tile136) // 4]


def _normalize_observation_state(obs: Any, raw_state: dict[str, Any]) -> dict[str, Any]:
    actor = int(getattr(obs, "player_id", raw_state.get("player_id", 0)))
    raw_melds = raw_state.get("melds") or [[], [], [], []]
    melds: list[list[dict[str, Any]]] = []
    for seat, seat_melds in enumerate(raw_melds):
        normalized_seat: list[dict[str, Any]] = []
        for meld in seat_melds:
            meld_type_name = _meld_type_key(getattr(meld, "meld_type", None))
            tiles = [_tile136_to_str(int(tile)) for tile in list(meld.tiles)]
            called_tile = getattr(meld, "called_tile", None)
            pai = (
                _tile136_to_str(int(called_tile))
                if called_tile is not None
                else (tiles[0] if tiles else None)
            )
            consumed = list(tiles)
            if called_tile is not None and pai in consumed:
                consumed.remove(pai)
            normalized_seat.append(
                {
                    "type": _MELD_TYPE_MAP.get(meld_type_name, "pon"),
                    "pai": pai,
                    "consumed": consumed,
                    "target": int(meld.from_who) if getattr(meld, "opened", False) else None,
                }
            )
        melds.append(normalized_seat)

    discards = [
        [_tile136_to_str(int(tile)) for tile in seat_discards]
        for seat_discards in (raw_state.get("discards") or [[], [], [], []])
    ]
    hands = raw_state.get("hands") or [[], [], [], []]
    dora_indicators = raw_state.get("dora_indicators") or []
    riichi_declared = raw_state.get("riichi_declared") or [False, False, False, False]
    scores = raw_state.get("scores") or [25000, 25000, 25000, 25000]

    return {
        "actor": actor,
        "hand": [_tile136_to_str(int(tile)) for tile in hands[actor]],
        "discards": discards,
        "melds": melds,
        "scores": list(scores),
        "dora_markers": [_tile136_to_str(int(tile)) for tile in dora_indicators],
        "reached": list(riichi_declared),
        "bakaze": _ROUND_WINDS[int(raw_state.get("round_wind", 0)) % 4],
        "kyoku": int(raw_state.get("kyoku", raw_state.get("kyoku_index", 1) or 1)),
        "honba": int(raw_state.get("honba", 0)),
        "kyotaku": int(raw_state.get("riichi_sticks", 0)),
        "oya": int(raw_state.get("oya", 0)),
    }


class RiichiDevDecisionAgent:
    def reset(self) -> None:
        return None

    def start_game(self, seat: int) -> None:
        return None

    def observe(self, obs: Any) -> None:
        return None

    def act(self, obs: Any):
        raise NotImplementedError

    def select_action(self, message: dict[str, Any], seat: int | None) -> dict[str, Any] | str:
        raise NotImplementedError


class RiichiDevAuditLogger:
    def __init__(self, log_path: str | Path):
        self.path = Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _state_summary(message: dict[str, Any]) -> dict[str, Any] | None:
        observation = message.get("_normalized_observation")
        if not isinstance(observation, dict):
            return None
        actor = observation.get("actor", 0)
        melds = observation.get("melds") or [[], [], [], []]
        discards = observation.get("discards") or [[], [], [], []]
        reached = observation.get("reached") or [False, False, False, False]
        return {
            "actor": actor,
            "bakaze": observation.get("bakaze"),
            "kyoku": observation.get("kyoku"),
            "honba": observation.get("honba"),
            "kyotaku": observation.get("kyotaku"),
            "oya": observation.get("oya"),
            "hand": observation.get("hand"),
            "melds": melds[actor] if actor < len(melds) else [],
            "discards": discards[actor] if actor < len(discards) else [],
            "reached": reached[actor] if actor < len(reached) else False,
            "dora_markers": observation.get("dora_markers", []),
            "scores": observation.get("scores", []),
        }

    def write(self, payload: dict[str, Any]) -> None:
        record = dict(payload)
        record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_connection_start(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        ws_url: str,
        diagnostics: dict[str, Any],
        open_timeout: float,
        ping_interval: float,
        ping_timeout: float,
    ) -> None:
        self.write(
            {
                "kind": "connection_start",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "ws_url": ws_url,
                "diagnostics": diagnostics,
                "open_timeout": open_timeout,
                "ping_interval": ping_interval,
                "ping_timeout": ping_timeout,
            }
        )

    def log_request_action(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        request_seq: int | None,
        message: dict[str, Any],
        response: dict[str, Any],
        latency_ms: float,
        wire_payload: str | None = None,
    ) -> None:
        possible_actions = message.get("possible_actions") or []
        self.write(
            {
                "kind": "request_action",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "request_seq": request_seq,
                "message_type": message.get("type"),
                "message_meta": _audit_message_meta(message),
                "response": response,
                "wire_payload": wire_payload,
                "latency_ms": round(latency_ms, 3),
                "possible_action_count": len(possible_actions),
                "possible_actions": possible_actions,
                "new_events": message.get("_new_events"),
                "state": self._state_summary(message),
                "normalized_observation": message.get("_normalized_observation"),
            }
        )

    def log_send_result(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        request_seq: int | None,
        response: dict[str, Any],
        success: bool,
        wire_payload: str | None = None,
        error: str | None = None,
        decision_latency_ms: float | None = None,
        send_latency_ms: float | None = None,
        total_latency_ms: float | None = None,
        deadline_ms: float | None = None,
    ) -> None:
        self.write(
            {
                "kind": "send_result",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "request_seq": request_seq,
                "success": success,
                "response": response,
                "wire_payload": wire_payload,
                "error": error,
                "decision_latency_ms": round(decision_latency_ms, 3)
                if decision_latency_ms is not None
                else None,
                "send_latency_ms": round(send_latency_ms, 3)
                if send_latency_ms is not None
                else None,
                "total_latency_ms": round(total_latency_ms, 3)
                if total_latency_ms is not None
                else None,
                "deadline_ms": round(deadline_ms, 3) if deadline_ms is not None else None,
            }
        )

    def log_protocol_action(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        trigger_message: dict[str, Any],
        response: dict[str, Any],
        latency_ms: float,
        wire_payload: str | None = None,
    ) -> None:
        self.write(
            {
                "kind": "protocol_action",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "trigger_type": trigger_message.get("type"),
                "message_meta": _audit_message_meta(trigger_message),
                "response": response,
                "wire_payload": wire_payload,
                "latency_ms": round(latency_ms, 3),
                "state": self._state_summary(trigger_message),
                "normalized_observation": trigger_message.get("_normalized_observation"),
            }
        )

    def log_agent_error(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        request_seq: int | None,
        message: dict[str, Any],
        error: str,
        traceback_text: str,
    ) -> None:
        self.write(
            {
                "kind": "agent_error",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "request_seq": request_seq,
                "message_meta": _audit_message_meta(message),
                "possible_actions": message.get("possible_actions") or [],
                "new_events": message.get("_new_events"),
                "state": self._state_summary(message),
                "normalized_observation": message.get("_normalized_observation"),
                "error": error,
                "traceback": traceback_text,
            }
        )

    def log_echo_mismatch(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        request_seq: int | None,
        last_request_seq: int | None,
        expected: dict[str, Any],
        observed: dict[str, Any],
        message: dict[str, Any],
        wire_payload: str | None,
        classification: str = "echo_mismatch",
    ) -> None:
        self.write(
            {
                "kind": "echo_mismatch",
                "classification": classification,
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "request_seq": request_seq,
                "last_request_seq": last_request_seq,
                "expected": expected,
                "observed": observed,
                "last_wire_payload": wire_payload,
                "message_meta": _audit_message_meta(message),
                "new_events": message.get("_new_events"),
                "state": self._state_summary(message),
                "normalized_observation": message.get("_normalized_observation"),
            }
        )

    def log_server_illegal_action(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        request_seq: int | None,
        illegal_event: dict[str, Any],
        message: dict[str, Any],
    ) -> None:
        self.write(
            {
                "kind": "server_illegal_action",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "request_seq": request_seq,
                "illegal_event": illegal_event,
                "message_meta": _audit_message_meta(message),
                "new_events": message.get("_new_events"),
                "state": self._state_summary(message),
                "normalized_observation": message.get("_normalized_observation"),
            }
        )

    def log_unconfirmed_action_boundary(
        self,
        *,
        queue: str,
        bot_name: str,
        model_version: str | None,
        seat: int | None,
        boundary_message: dict[str, Any],
        last_request_seq: int | None,
        expected: dict[str, Any],
        wire_payload: str | None,
    ) -> None:
        self.write(
            {
                "kind": "unconfirmed_action_boundary",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "boundary_message": boundary_message,
                "last_request_seq": last_request_seq,
                "expected": expected,
                "last_wire_payload": wire_payload,
            }
        )

    def log_event(self, *, queue: str, bot_name: str, model_version: str | None, seat: int | None, message: dict[str, Any]) -> None:
        self.write(
            {
                "kind": "event",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "message": message,
            }
        )

    def log_disconnect(self, *, queue: str, bot_name: str, model_version: str | None, seat: int | None, code: int, reason: str) -> None:
        self.write(
            {
                "kind": "disconnect",
                "queue": queue,
                "bot_name": bot_name,
                "model_version": model_version,
                "seat": seat,
                "code": code,
                "reason": reason,
            }
        )


@dataclass(frozen=True)
class DecisionAgentSpec:
    model_version: str
    hidden_dim: int = 256
    num_res_blocks: int = 4
    beam_k: int = 3
    beam_lambda: float = 1.0
    score_delta_lambda: float = 0.20
    win_prob_lambda: float = 0.20
    dealin_prob_lambda: float = 0.25
    rank_pt_lambda: float = 0.0


DEFAULT_DECISION_AGENT_SPEC = DecisionAgentSpec(model_version="mortal")
DECISION_AGENT_SPECS: dict[str, DecisionAgentSpec] = {}


class ValidationSafeAgent(RiichiDevDecisionAgent):
    def __init__(self) -> None:
        self._seat: int | None = None
        self._last_tsumo: str | None = None

    def reset(self) -> None:
        self._seat = None
        self._last_tsumo = None

    def act(self, obs: Any):
        legal_actions = [_action_to_mjai_dict(action) for action in obs.legal_actions()]
        actor = int(getattr(obs, "player_id", 0))
        if self._last_tsumo:
            candidate = {
                "type": "dahai",
                "actor": actor,
                "pai": self._last_tsumo,
                "tsumogiri": True,
            }
            if any(_action_to_mjai_dict(a) == candidate for a in legal_actions):
                return _select_observation_action(obs, candidate, actor)
        for action_type in ("hora", "reach", "dahai", "none"):
            for action in legal_actions:
                if action.get("type") == action_type:
                    return _select_observation_action(obs, action, actor)
        if legal_actions:
            return _select_observation_action(obs, legal_actions[0], actor)
        raise RuntimeError("validation-safe local observation has no legal actions")

    def select_action(self, message: dict[str, Any], seat: int | None) -> dict[str, Any] | str:
        mtype = message.get("type")
        if mtype == "start_game":
            if seat is not None:
                self._seat = int(seat)
            return {"type": "none"}
        if mtype == "tsumo":
            pai = message.get("pai")
            actor = message.get("actor")
            if pai and pai != "?" and actor == seat:
                self._last_tsumo = pai
            return {"type": "none"}
        if mtype != "request_action":
            return {"type": "none"}

        actor = seat if seat is not None else self._seat
        legal_actions = [_action_to_mjai_dict(a) for a in (message.get("possible_actions") or [])]
        if self._last_tsumo and actor is not None:
            candidate = {
                "type": "dahai",
                "actor": actor,
                "pai": self._last_tsumo,
                "tsumogiri": True,
            }
            if any(a == candidate for a in legal_actions):
                self._last_tsumo = None
                return candidate
        self._last_tsumo = None
        return _sanitize_action({"type": "none"}, actor)


class RulebaseObservationAgent(RiichiDevDecisionAgent):
    def __init__(self) -> None:
        self._pending_reach_discard: dict[str, Any] | None = None

    def reset(self) -> None:
        self._pending_reach_discard = None

    def _remember_reach_followup(
        self,
        *,
        snap: dict[str, Any],
        actor: int,
        legal_actions: list[dict[str, Any]],
        chosen: dict[str, Any],
    ) -> None:
        self._pending_reach_discard = None
        if chosen.get("type") != "reach":
            return

        import keqing_core

        followup_legal_actions = [
            action for action in legal_actions if action.get("type") != "reach"
        ]
        if not followup_legal_actions:
            return
        followup = keqing_core.choose_rulebase_action(
            snap,
            actor,
            followup_legal_actions,
        )
        if followup and followup.get("type") == "dahai":
            self._pending_reach_discard = _sanitize_action(followup, actor)

    def choose_mjai_action(
        self,
        *,
        snap: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        actor: int,
    ) -> dict[str, Any]:
        import keqing_core

        if not legal_actions:
            self._pending_reach_discard = None
            return _sanitize_action({"type": "none"}, actor)
        chosen = keqing_core.choose_rulebase_action(snap, actor, legal_actions)
        if chosen is None:
            self._pending_reach_discard = None
            return _sanitize_action({"type": "none"}, actor)
        self._remember_reach_followup(
            snap=snap,
            actor=actor,
            legal_actions=legal_actions,
            chosen=chosen,
        )
        return _sanitize_action(chosen, actor)

    def act(self, obs: Any):
        actor = int(getattr(obs, "player_id", 0))
        snap = _normalize_observation_state(obs, obs.to_dict())
        legal_actions = [_action_to_mjai_dict(action) for action in obs.legal_actions()]
        chosen = self.choose_mjai_action(
            snap=snap,
            legal_actions=legal_actions,
            actor=actor,
        )
        return _select_observation_action(obs, chosen, actor)

    def select_action(self, message: dict[str, Any], seat: int | None) -> dict[str, Any] | str:
        mtype = message.get("type")
        if mtype != "request_action":
            if (
                mtype == "reach"
                and seat is not None
                and message.get("actor") == seat
                and self._pending_reach_discard is not None
            ):
                discard = self._pending_reach_discard
                self._pending_reach_discard = None
                return discard
            return {"type": "none"}
        _obs, snap = _decode_observation(message)
        legal_actions = list(message.get("possible_actions") or [])
        actor = seat if seat is not None else int(snap.get("actor", 0))
        if not legal_actions:
            legal_actions = [{"type": "none"}]
        return self.choose_mjai_action(
            snap=snap,
            legal_actions=[_action_to_mjai_dict(action) for action in legal_actions],
            actor=actor,
        )


class MortalObservationAgent(RiichiDevDecisionAgent):
    def __init__(
        self,
        *,
        model_path: str | Path,
        project_root: str | Path,
        device: str = "cuda",
        verbose: bool = False,
        preload: bool = False,
    ) -> None:
        self._model_path = Path(model_path)
        self._mortal_root = Path(project_root) / "third_party" / "Mortal"
        self._device = device
        self._verbose = verbose
        self._seat: int | None = None
        self._bot: Any | None = None
        if preload:
            t0 = time.perf_counter()
            self._ensure_bot(0)
            logger.info(
                "preloaded Mortal model for riichi.dev serving in %.1fms",
                (time.perf_counter() - t0) * 1000.0,
            )

    def reset(self) -> None:
        if self._bot is not None:
            self._bot.reset()

    def _ensure_bot(self, seat: int) -> None:
        if self._bot is not None and self._seat == seat:
            return
        if self._bot is not None and hasattr(self._bot, "set_player_id"):
            self._bot.set_player_id(seat)
            self._seat = seat
            return
        if str(self._device).startswith("cuda"):
            import torch

            if torch.cuda.is_available():
                torch.empty(1, device=self._device).sum().item()
        from inference.mortal_bot import MortalReviewBot

        self._seat = seat
        self._bot = MortalReviewBot(
            player_id=seat,
            model_path=self._model_path,
            mortal_root=self._mortal_root,
            device=self._device,
            verbose=self._verbose,
            enable_review_log=False,
        )

    def start_game(self, seat: int) -> None:
        self._ensure_bot(seat)

    def _reset_for_new_kyoku(self) -> None:
        if self._bot is not None:
            self._bot.reset()

    @staticmethod
    def _current_kyoku_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        last_start_kyoku = None
        for idx, event in enumerate(events):
            if event.get("type") == "start_kyoku":
                last_start_kyoku = idx
        if last_start_kyoku is None:
            return events

        sync_start = last_start_kyoku
        if (
            last_start_kyoku > 0
            and events[last_start_kyoku - 1].get("type") == "start_game"
        ):
            sync_start = last_start_kyoku - 1

        if sync_start > 0:
            logger.debug(
                "mortal observation event stream includes previous kyoku tail; "
                "dropping %d pre-start_kyoku events before syncing from latest start_kyoku",
                sync_start,
            )
        return events[sync_start:]

    def _react_new_events(self, events: list[dict[str, Any]], seat: int) -> dict[str, Any] | None:
        self._ensure_bot(seat)
        if self._bot is None:
            return None

        reaction: dict[str, Any] | None = None
        for event in self._current_kyoku_events(events):
            if event.get("type") == "start_kyoku":
                self._reset_for_new_kyoku()
            response = self._bot.react(event)
            reaction = _action_to_mjai_dict(response) if response is not None else None
        return reaction

    def choose_mjai_action(
        self,
        *,
        new_events: list[dict[str, Any]],
        legal_actions: list[dict[str, Any]],
        actor: int,
    ) -> dict[str, Any]:
        try:
            reaction = self._react_new_events(new_events, actor)
        except RuntimeError as exc:
            logger.warning(
                "mortal observation sync failed; falling back to legal action: "
                "actor=%s error=%s new_event_count=%d recent_events=%s",
                actor,
                exc,
                len(new_events),
                json.dumps(new_events[-8:], ensure_ascii=False),
                exc_info=True,
            )
            return _legalize_action(None, legal_actions=legal_actions, actor=actor)
        return _legalize_action(reaction, legal_actions=legal_actions, actor=actor)

    def observe(self, obs: Any) -> None:
        actor = int(getattr(obs, "player_id", 0))
        self._react_new_events(_observation_new_events(obs), actor)

    def act(self, obs: Any):
        actor = int(getattr(obs, "player_id", 0))
        legal_actions = [_action_to_mjai_dict(action) for action in obs.legal_actions()]
        chosen = self.choose_mjai_action(
            new_events=_observation_new_events(obs),
            legal_actions=legal_actions,
            actor=actor,
        )
        action = obs.select_action_from_mjai(json.dumps(chosen, ensure_ascii=False))
        if action is None:
            raise RuntimeError(f"Mortal selected action could not be converted by RiichiEnv: {chosen}")
        return action

    def select_action(self, message: dict[str, Any], seat: int | None) -> dict[str, Any] | str:
        mtype = message.get("type")
        if mtype == "request_action":
            obs, snap = _decode_observation(message)
            actor = seat if seat is not None else int(snap.get("actor", 0))
            new_events = _observation_new_events(obs)
            legal_actions = list(message.get("possible_actions") or [])
            if not legal_actions:
                legal_actions = [_action_to_mjai_dict(action) for action in obs.legal_actions()]
            else:
                legal_actions = [_action_to_mjai_dict(action) for action in legal_actions]
            chosen = self.choose_mjai_action(
                new_events=new_events,
                legal_actions=legal_actions,
                actor=actor,
            )
            action = obs.select_action_from_mjai(json.dumps(chosen, ensure_ascii=False))
            if action is None:
                logger.warning(
                    "mortal riichienv conversion fallback: actor=%s chosen=%s",
                    actor,
                    _action_log_text(chosen),
                )
                legal_actions = [_action_to_mjai_dict(action) for action in obs.legal_actions()]
                fallback = _legalize_action(chosen, legal_actions=legal_actions, actor=actor)
                action = obs.select_action_from_mjai(json.dumps(fallback, ensure_ascii=False))
                if action is None:
                    raise RuntimeError(
                        f"Mortal selected action could not be converted by RiichiEnv: {chosen}"
                    )
            return action.to_mjai()

        return {"type": "none"}


class ObservationScoringAgent(RiichiDevDecisionAgent):
    """Archived checkpoint scoring agent.

    The active riichi.dev client supports only Mortal and rulebase agents after
    the project contraction. This class remains as a fail-closed import
    compatibility seam for old callers.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "ObservationScoringAgent is archived. Use bot_name='mortal' or "
            "bot_name='rulebase' with create_riichi_dev_agent."
        )


def create_riichi_dev_agent(
    *,
    bot_name: str,
    project_root: str | Path,
    model_path: str | Path | None,
    device: str,
    verbose: bool,
    model_version: str | None = None,
    rank_pt_lambda: float | None = None,
    validation_safe: bool = False,
    preload_mortal: bool = False,
) -> RiichiDevDecisionAgent:
    if validation_safe:
        return ValidationSafeAgent()
    if bot_name == "rulebase":
        return RulebaseObservationAgent()
    if bot_name == "mortal":
        resolved_model_path = _resolve_model_path(
            bot_name=bot_name,
            project_root=project_root,
            model_path=model_path,
        )
        if resolved_model_path is None:
            raise ValueError("mortal requires a Mortal checkpoint path")
        return MortalObservationAgent(
            model_path=resolved_model_path,
            project_root=project_root,
            device=device,
            verbose=verbose,
            preload=preload_mortal,
        )
    raise ValueError(f"unsupported riichi.dev bot_name after Mortal cleanup: {bot_name}")


@dataclass(slots=True)
class RiichiDevClientConfig:
    token: str
    bot_name: str = "mortal"
    model_version: str | None = None
    queue: str = "ranked"
    base_url: str = DEFAULT_BASE_URL
    ws_url_override: str | None = None
    model_path: Path | None = None
    project_root: Path = Path.cwd()
    device: str = "cuda"
    rank_pt_lambda: float | None = None
    verbose: bool = False
    origin: str | None = None
    user_agent: str = "keqing1-riichi-dev-client/0.1"
    audit_log_path: Path | None = None
    open_timeout: float = 10.0
    ping_interval: float = 20.0
    ping_timeout: float = 20.0
    auto_reconnect: bool = True
    reconnect_delay_sec: float = 1.0
    action_deadline_ms: float = DEFAULT_ACTION_DEADLINE_MS
    preload_mortal: bool = False
    disable_ws_proxy: bool = True
    ws_proxy: str | None = None

    def ws_url(self) -> str:
        if self.ws_url_override:
            return self.ws_url_override
        return _resolve_ws_url(self.base_url, self.queue)


class RiichiDevBotClient:
    def __init__(
        self,
        config: RiichiDevClientConfig,
        *,
        agent: RiichiDevDecisionAgent | None = None,
    ):
        self.config = config
        self.agent = agent or create_riichi_dev_agent(
            bot_name=config.bot_name,
            project_root=config.project_root,
            model_path=config.model_path,
            device=config.device,
            verbose=config.verbose,
            model_version=config.model_version,
            rank_pt_lambda=config.rank_pt_lambda,
            validation_safe=False,
            preload_mortal=config.preload_mortal,
        )
        self.seat: int | None = None
        self._saw_start_game = False
        self._saw_end_game = False
        self._request_seq = 0
        self._last_sent_action: dict[str, Any] | None = None
        self._last_sent_request_seq: int | None = None
        self._last_sent_wire_payload: str | None = None
        self._must_check_server_state = False
        default_audit_path = Path("logs/riichi_dev") / f"{self.config.queue}-{self.config.bot_name}.jsonl"
        self.audit_logger = RiichiDevAuditLogger(self.config.audit_log_path or default_audit_path)

    def _reset_session_state(self) -> None:
        self.seat = None
        self._saw_start_game = False
        self._saw_end_game = False
        self._request_seq = 0
        self._last_sent_action = None
        self._last_sent_request_seq = None
        self._last_sent_wire_payload = None
        self._must_check_server_state = False
        self.agent.reset()

    def _remember_sent_action(
        self,
        *,
        action: dict[str, Any] | None,
        request_seq: int | None,
        wire_payload: str | None,
    ) -> None:
        wire_action: dict[str, Any] | None = None
        if wire_payload is not None:
            try:
                parsed_wire_action = json.loads(wire_payload)
            except json.JSONDecodeError:
                parsed_wire_action = None
            if isinstance(parsed_wire_action, dict):
                wire_action = parsed_wire_action
        if wire_action is None and action is not None:
            wire_action = _riichi_dev_wire_action(action, actor_hint=action.get("actor"))
        tracked = _echo_tracked_action(wire_action)
        if tracked is None:
            return
        self._last_sent_action = tracked
        self._last_sent_request_seq = request_seq
        self._last_sent_wire_payload = wire_payload

    def _clear_last_sent_action(self) -> None:
        self._last_sent_action = None
        self._last_sent_request_seq = None
        self._last_sent_wire_payload = None
        self._must_check_server_state = False

    def _in_active_game(self) -> bool:
        return self._saw_start_game and not self._saw_end_game

    def _websocket_connect_kwargs(self, headers: dict[str, str]) -> dict[str, Any]:
        # websockets >= 14 renamed the client handshake-header kwarg from
        # extra_headers to additional_headers and added a dedicated
        # user_agent_header.  Split User-Agent out; copy so we never mutate the
        # caller's dict.
        request_headers = dict(headers)
        user_agent = request_headers.pop("User-Agent", None)
        kwargs: dict[str, Any] = {
            "additional_headers": request_headers,
            "origin": self.config.origin,
            "open_timeout": self.config.open_timeout,
            "ping_interval": self.config.ping_interval,
            "ping_timeout": self.config.ping_timeout,
        }
        if user_agent is not None:
            kwargs["user_agent_header"] = user_agent
        if self.config.ws_proxy:
            ws_url = self.config.ws_url()
            parsed_ws = urllib.parse.urlparse(ws_url)
            kwargs["sock"] = _open_http_connect_socket(
                ws_url=ws_url,
                proxy_url=self.config.ws_proxy,
                timeout=self.config.open_timeout,
            )
            if parsed_ws.hostname:
                kwargs["server_hostname"] = parsed_ws.hostname
        if self.config.disable_ws_proxy and _websockets_connect_supports_proxy_arg():
            kwargs["proxy"] = None
        return kwargs

    def _log_connection_start(self) -> None:
        diagnostics = _websocket_diagnostics(
            disable_ws_proxy=self.config.disable_ws_proxy,
            ws_proxy=self.config.ws_proxy,
        )
        if self.config.verbose:
            logger.info(
                "websocket diagnostics: websockets=%s proxy_env=%s "
                "explicit_ws_proxy=%s proxy_arg_supported=%s "
                "proxy_disabled_by_kwarg=%s proxy_env_used_by_websockets=%s "
                "manual_http_connect_proxy=%s",
                diagnostics["websockets_version"],
                diagnostics["proxy_env"] or {},
                diagnostics["explicit_ws_proxy"],
                diagnostics["proxy_arg_supported"],
                diagnostics["proxy_disabled_by_kwarg"],
                diagnostics["proxy_env_used_by_websockets"],
                diagnostics["manual_http_connect_proxy"],
            )
        self.audit_logger.log_connection_start(
            queue=self.config.queue,
            bot_name=self.config.bot_name,
            model_version=self.config.model_version,
            seat=self.seat,
            ws_url=self.config.ws_url(),
            diagnostics=diagnostics,
            open_timeout=self.config.open_timeout,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_timeout,
        )

    def _check_last_action_echo(self, message: dict[str, Any], request_seq: int | None) -> None:
        if message.get("type") != "request_action":
            return
        expected = self._last_sent_action
        enriched = _enrich_message_for_audit(message)
        events = enriched.get("_new_events") or []
        illegal_event = _server_illegal_action_event(events, seat=self.seat)
        if illegal_event is not None:
            error = (
                "riichi.dev reported an illegal action in the server event stream; "
                f"aborting before sending another action. event={_action_log_text(illegal_event)}"
            )
            logger.error(error)
            self.audit_logger.log_server_illegal_action(
                queue=self.config.queue,
                bot_name=self.config.bot_name,
                model_version=self.config.model_version,
                seat=self.seat,
                request_seq=request_seq,
                illegal_event=illegal_event,
                message=enriched,
            )
            raise RiichiDevServerIllegalAction(error)

        if expected is None:
            self._must_check_server_state = False
            return
        observed = _first_echo_candidate(events, expected)
        if observed is None:
            return
        if _actions_equivalent_for_echo(expected, observed):
            self._clear_last_sent_action()
            return
        if _looks_like_server_timeout_fallback(expected, observed):
            classification = "server_timeout_or_disconnect_fallback"
            error = (
                "riichi.dev appears to have applied its timeout/disconnect fallback "
                "instead of the previous action; local websocket send completed, but "
                "the server observation shows a self tsumogiri fallback. Aborting "
                "before sending another action because riichi.dev actions have no "
                "request id and continuing would desync Mortal state. "
                f"expected={_action_log_text(expected)} observed={_action_log_text(observed)}"
            )
        else:
            classification = "echo_mismatch"
            error = (
                "riichi.dev action echo mismatch; server observation did not echo the "
                "previous sent action. Aborting before sending another action because "
                "riichi.dev actions have no request id and a stale in-flight action can "
                "be applied to the next request window. "
                f"expected={_action_log_text(expected)} observed={_action_log_text(observed)}"
            )
        logger.error(error)
        self.audit_logger.log_echo_mismatch(
            queue=self.config.queue,
            bot_name=self.config.bot_name,
            model_version=self.config.model_version,
            seat=self.seat,
            request_seq=request_seq,
            last_request_seq=self._last_sent_request_seq,
            expected=expected,
            observed=observed,
            message=enriched,
            wire_payload=self._last_sent_wire_payload,
            classification=classification,
        )
        raise RiichiDevActionEchoMismatch(error)

    async def _run_once(self) -> None:
        self._reset_session_state()
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "User-Agent": self.config.user_agent,
        }
        logger.info("connecting to %s", self.config.ws_url())
        self._log_connection_start()
        async with websockets.connect(
            self.config.ws_url(),
            **self._websocket_connect_kwargs(headers),
        ) as websocket:
            async for raw_message in websocket:
                message = json.loads(raw_message)
                request_seq: int | None = None
                post_send_deadline_error: str | None = None
                send_latency_ms: float | None = None
                total_latency_ms: float | None = None
                if message.get("type") == "request_action":
                    self._request_seq += 1
                    request_seq = self._request_seq
                    self._check_last_action_echo(message, request_seq)
                audit_message_for_wire: dict[str, Any] | None = None
                if message.get("type") == "request_action":
                    audit_message_for_wire = _enrich_message_for_audit(message)
                t0 = time.perf_counter()
                try:
                    response = self.handle_message(message)
                except Exception as exc:
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    audit_message = _enrich_message_for_audit(message)
                    self.audit_logger.log_agent_error(
                        queue=self.config.queue,
                        bot_name=self.config.bot_name,
                        model_version=self.config.model_version,
                        seat=self.seat,
                        request_seq=request_seq,
                        message=audit_message,
                        error=f"{type(exc).__name__}: {exc}",
                        traceback_text=traceback.format_exc(),
                    )
                    if self.config.verbose:
                        logger.exception(
                            "agent decision failed for request_seq=%s latency_ms=%.1f",
                            request_seq,
                            latency_ms,
                        )
                    raise
                latency_ms = (time.perf_counter() - t0) * 1000.0
                audit_response = _action_to_mjai_dict(response) if response is not None else None
                wire_payload = (
                    _action_to_mjai_wire_payload(
                        response,
                        actor_hint=self.seat,
                        new_events=(audit_message_for_wire or {}).get("_new_events"),
                        state=(audit_message_for_wire or {}).get("_normalized_observation"),
                    )
                    if response is not None
                    else None
                )
                if response is not None:
                    elapsed_before_send_ms = (time.perf_counter() - t0) * 1000.0
                    action_deadline_ms = float(self.config.action_deadline_ms or 0.0)
                    if (
                        message.get("type") == "request_action"
                        and action_deadline_ms > 0.0
                        and elapsed_before_send_ms > action_deadline_ms
                    ):
                        error = (
                            "decision exceeded riichi.dev safety deadline "
                            f"elapsed_ms={elapsed_before_send_ms:.1f} "
                            f"deadline_ms={action_deadline_ms:.1f}; not sending stale action"
                        )
                        logger.warning(
                            "dropping late riichi.dev response and aborting connection: "
                            "request_seq=%s %s payload=%s",
                            request_seq,
                            error,
                            wire_payload,
                        )
                        audit_message = _enrich_message_for_audit(message)
                        self.audit_logger.log_request_action(
                            queue=self.config.queue,
                            bot_name=self.config.bot_name,
                            model_version=self.config.model_version,
                            seat=self.seat,
                            request_seq=request_seq,
                            message=audit_message,
                            response=audit_response,
                            latency_ms=latency_ms,
                            wire_payload=wire_payload,
                        )
                        self.audit_logger.log_send_result(
                            queue=self.config.queue,
                            bot_name=self.config.bot_name,
                            model_version=self.config.model_version,
                            seat=self.seat,
                            request_seq=request_seq,
                            response=audit_response,
                            success=False,
                            wire_payload=wire_payload,
                            error=error,
                            decision_latency_ms=latency_ms,
                            total_latency_ms=elapsed_before_send_ms,
                            deadline_ms=action_deadline_ms,
                        )
                        raise RiichiDevStaleActionDeadline(error)
                    send_start = time.perf_counter()
                    try:
                        await websocket.send(wire_payload)
                    except Exception as exc:
                        send_latency_ms = (time.perf_counter() - send_start) * 1000.0
                        total_latency_ms = (time.perf_counter() - t0) * 1000.0
                        self.audit_logger.log_send_result(
                            queue=self.config.queue,
                            bot_name=self.config.bot_name,
                            model_version=self.config.model_version,
                            seat=self.seat,
                            request_seq=request_seq,
                            response=audit_response,
                            success=False,
                            wire_payload=wire_payload,
                            error=f"{type(exc).__name__}: {exc}",
                            decision_latency_ms=latency_ms,
                            send_latency_ms=send_latency_ms,
                            total_latency_ms=total_latency_ms,
                            deadline_ms=action_deadline_ms or None,
                        )
                        raise
                    send_latency_ms = (time.perf_counter() - send_start) * 1000.0
                    total_latency_ms = (time.perf_counter() - t0) * 1000.0
                    if (
                        message.get("type") == "request_action"
                        and action_deadline_ms > 0.0
                        and total_latency_ms > action_deadline_ms
                    ):
                        post_send_deadline_error = (
                            "response crossed riichi.dev safety deadline after websocket send "
                            f"total_ms={total_latency_ms:.1f} "
                            f"decision_ms={latency_ms:.1f} "
                            f"send_ms={send_latency_ms:.1f} "
                            f"deadline_ms={action_deadline_ms:.1f}; exiting to avoid stale action reuse"
                        )
                        logger.warning(post_send_deadline_error)
                    self._remember_sent_action(
                        action=audit_response,
                        request_seq=request_seq,
                        wire_payload=wire_payload,
                    )
                    self._must_check_server_state = True

                audit_message = _enrich_message_for_audit(message)
                if message.get("type") == "request_action" and response is not None:
                    self.audit_logger.log_request_action(
                        queue=self.config.queue,
                        bot_name=self.config.bot_name,
                        model_version=self.config.model_version,
                        seat=self.seat,
                        request_seq=request_seq,
                        message=audit_message,
                        response=audit_response,
                        latency_ms=latency_ms,
                        wire_payload=wire_payload,
                    )
                elif response is not None:
                    self.audit_logger.log_protocol_action(
                        queue=self.config.queue,
                        bot_name=self.config.bot_name,
                        model_version=self.config.model_version,
                        seat=self.seat,
                        trigger_message=audit_message,
                        response=audit_response,
                        latency_ms=latency_ms,
                        wire_payload=wire_payload,
                    )
                elif message.get("type") in {"start_game", "start_kyoku", "end_kyoku", "end_game", "validation_result", "error"}:
                    self.audit_logger.log_event(
                        queue=self.config.queue,
                        bot_name=self.config.bot_name,
                        model_version=self.config.model_version,
                        seat=self.seat,
                        message=audit_message,
                    )
                if response is not None:
                    self.audit_logger.log_send_result(
                        queue=self.config.queue,
                        bot_name=self.config.bot_name,
                        model_version=self.config.model_version,
                        seat=self.seat,
                        request_seq=request_seq,
                        response=audit_response,
                        success=post_send_deadline_error is None,
                        wire_payload=wire_payload,
                        error=post_send_deadline_error,
                        decision_latency_ms=latency_ms,
                        send_latency_ms=send_latency_ms,
                        total_latency_ms=total_latency_ms,
                        deadline_ms=float(self.config.action_deadline_ms or 0.0) or None,
                    )
                    if self.config.verbose:
                        logger.info("recv: %s", message.get("type"))
                        logger.info(
                            "send: %s latency_ms=%.1f send_ms=%.1f total_ms=%.1f",
                            wire_payload,
                            latency_ms,
                            send_latency_ms or 0.0,
                            total_latency_ms or latency_ms,
                        )
                    if post_send_deadline_error is not None:
                        raise RiichiDevStaleActionDeadline(post_send_deadline_error)

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | str | None:
        mtype = message.get("type")
        if mtype == "start_game":
            self._clear_last_sent_action()
            if self._saw_start_game and not self._saw_end_game:
                logger.warning(
                    "riichi.dev sent start_game before end_game; resetting local session state "
                    "to avoid carrying stale bot state"
                )
                self.agent.reset()
                self._request_seq = 0
            self._saw_start_game = True
            self._saw_end_game = False
            seat = message.get("id", message.get("seat"))
            if seat is not None:
                self.seat = int(seat)
                self.agent.start_game(self.seat)
            return None
        if mtype == "end_game":
            self._saw_end_game = True
            self._clear_last_sent_action()
            return None
        if mtype in {"end_kyoku", "start_kyoku"}:
            if self._last_sent_action is not None:
                logger.debug(
                    "riichi.dev sent %s before echoing previous action; "
                    "treating kyoku boundary as normal confirmation boundary. "
                    "last_request_seq=%s expected=%s wire_payload=%s",
                    mtype,
                    self._last_sent_request_seq,
                    _action_log_text(self._last_sent_action),
                    self._last_sent_wire_payload,
                )
                self.audit_logger.log_unconfirmed_action_boundary(
                    queue=self.config.queue,
                    bot_name=self.config.bot_name,
                    model_version=self.config.model_version,
                    seat=self.seat,
                    boundary_message=message,
                    last_request_seq=self._last_sent_request_seq,
                    expected=self._last_sent_action,
                    wire_payload=self._last_sent_wire_payload,
                )
            self._clear_last_sent_action()
            self.agent.reset()
            return None
        if mtype != "request_action":
            return None
        return self.agent.select_action(message, self.seat)

    def _should_retry_disconnect(self, code: int) -> bool:
        retryable_disconnect = code in {1005, 1006}
        retry_after_game = self._saw_end_game and retryable_disconnect
        retry_ranked_queue_wait = (
            self.config.queue == "ranked"
            and not self._saw_start_game
            and retryable_disconnect
        )
        return self.config.auto_reconnect and (retry_after_game or retry_ranked_queue_wait)

    def _should_retry_state_error(self) -> bool:
        if self._in_active_game():
            return False
        return self.config.auto_reconnect and self.config.queue == "ranked"

    async def run(self) -> None:
        while True:
            try:
                await self._run_once()
            except ConnectionClosed as exc:
                self.audit_logger.log_disconnect(
                    queue=self.config.queue,
                    bot_name=self.config.bot_name,
                    model_version=self.config.model_version,
                    seat=self.seat,
                    code=exc.code,
                    reason=exc.reason or "",
                )
                if self._should_retry_disconnect(exc.code):
                    if self.config.verbose:
                        logger.info(
                            "server closed %s connection (code=%s); reconnecting in %.1fs",
                            self.config.queue,
                            exc.code,
                            self.config.reconnect_delay_sec,
                        )
                    await asyncio.sleep(self.config.reconnect_delay_sec)
                    continue
                raise SystemExit(
                    _format_connection_closed_message(self.config.queue, exc.code, exc.reason)
                ) from None
            except TimeoutError:
                should_retry_timeout = (
                    self.config.auto_reconnect
                    and self.config.queue == "ranked"
                    and not self._saw_start_game
                )
                if should_retry_timeout:
                    if self.config.verbose:
                        logger.info(
                            "opening %s connection timed out; reconnecting in %.1fs",
                            self.config.queue,
                            self.config.reconnect_delay_sec,
                        )
                    await asyncio.sleep(self.config.reconnect_delay_sec)
                    continue
                raise SystemExit(
                    f"timed out while opening {self.config.queue} connection"
                ) from None
            except (
                RiichiDevActionEchoMismatch,
                RiichiDevStaleActionDeadline,
                RiichiDevServerIllegalAction,
            ) as exc:
                should_retry_state_error = self._should_retry_state_error()
                if should_retry_state_error:
                    if self.config.verbose:
                        logger.warning(
                            "detected riichi.dev pre-game/post-game state-risk condition; "
                            "reconnecting in %.1fs: %s",
                            self.config.reconnect_delay_sec,
                            exc,
                        )
                    await asyncio.sleep(self.config.reconnect_delay_sec)
                    continue
                raise SystemExit(str(exc)) from None
            if not self.config.auto_reconnect or not self._saw_end_game:
                return
            if self.config.verbose:
                logger.info(
                    "%s session ended cleanly after end_game; reconnecting in %.1fs",
                    self.config.queue,
                    self.config.reconnect_delay_sec,
                )
            await asyncio.sleep(self.config.reconnect_delay_sec)


def run_local_game(
    *,
    agent: RiichiDevDecisionAgent | None = None,
    agents: Mapping[int, RiichiDevDecisionAgent] | Sequence[RiichiDevDecisionAgent] | None = None,
    game_mode: int = 2,
    seed: int | None = 42,
    max_steps: int = 10000,
) -> dict[str, Any]:
    if agent is None and agents is None:
        raise ValueError("run_local_game requires agent or agents")

    def local_agent_for(pid: int) -> RiichiDevDecisionAgent:
        if agents is None:
            assert agent is not None
            return agent
        return agents[pid]

    reset_seen: set[int] = set()
    reset_candidates = agents.values() if isinstance(agents, Mapping) else agents
    if reset_candidates is None:
        reset_candidates = [agent]
    for local_agent in reset_candidates:
        if local_agent is None:
            continue
        marker = id(local_agent)
        if marker in reset_seen:
            continue
        reset_seen.add(marker)
        local_agent.reset()

    env = RiichiEnv(game_mode=game_mode, seed=seed)
    observations = env.get_observations()
    step_count = 0

    while not env.done():
        actions_to_step: dict[int, Any] = {}
        for pid, obs in observations.items():
            actions = obs.legal_actions()
            if not actions:
                local_agent_for(int(pid)).observe(obs)
                continue
            actions_to_step[int(pid)] = local_agent_for(int(pid)).act(obs)
        if not actions_to_step:
            raise RuntimeError("local game stalled without any legal action")
        observations = env.step(actions_to_step)
        step_count += 1
        if step_count >= max_steps:
            raise RuntimeError(f"local game exceeded max_steps={max_steps}")

    return {
        "scores": list(env.scores()),
        "ranks": list(env.ranks()),
        "step_count": step_count,
        "mjai_log_length": len(env.mjai_log),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run supported keqing bots on riichi.dev")
    parser.add_argument(
        "--bot-name",
        default="mortal",
        help="bot family / checkpoint namespace to run (mortal, rulebase)",
    )
    parser.add_argument(
        "--model-version",
        default="",
        help="optional adapter/model-version override; defaults to --bot-name",
    )
    parser.add_argument(
        "--token",
        default="",
        help="riichi.dev bot token; if omitted, resolve from env based on --bot-name",
    )
    parser.add_argument(
        "--mode",
        choices=("online", "local"),
        default="online",
        help="run against riichi.dev over WebSocket or locally with RiichiEnv",
    )
    parser.add_argument(
        "--queue",
        choices=("validate", "ranked"),
        default="ranked",
        help="target riichi.dev queue (default: ranked)",
    )
    parser.add_argument(
        "--validation-safe",
        action="store_true",
        help="use a minimal tsumogiri/pass agent for bot activation robustness",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("RIICHI_BASE_URL", DEFAULT_BASE_URL),
        help="WebSocket base URL, e.g. wss://game.riichi.dev",
    )
    parser.add_argument(
        "--ws-url",
        default=os.getenv("RIICHI_WS_URL", ""),
        help="full WebSocket URL override; takes precedence over --base-url/--queue",
    )
    parser.add_argument(
        "--model-path",
        default="",
        help="checkpoint path override for model-backed bots",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="project root for resolving default model checkpoints",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("RIICHI_BOT_DEVICE", "cuda"),
        help="inference device preference",
    )
    parser.add_argument(
        "--rank-pt-lambda",
        type=float,
        default=0.0,
        help="archived checkpoint-scoring option; ignored by Mortal/rulebase",
    )
    parser.add_argument(
        "--origin",
        default=os.getenv("RIICHI_BOT_ORIGIN", ""),
        help="optional Origin header for the WebSocket handshake",
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("RIICHI_BOT_USER_AGENT", "keqing1-riichi-dev-client/0.1"),
        help="User-Agent header for the WebSocket handshake",
    )
    parser.add_argument(
        "--audit-log-path",
        default="",
        help="optional local jsonl audit log path; default logs/riichi_dev/<queue>-<bot>.jsonl",
    )
    parser.add_argument("--game-mode", type=int, default=2, help="local RiichiEnv game mode")
    parser.add_argument("--seed", type=int, default=42, help="local RiichiEnv seed")
    parser.add_argument("--max-steps", type=int, default=10000, help="local test safety cap")
    parser.add_argument(
        "--no-auto-reconnect",
        action="store_true",
        help=(
            "exit after the current online session instead of reconnecting before matchmaking "
            "or for the next game; active games are never reconnected"
        ),
    )
    parser.add_argument(
        "--reconnect-delay-sec",
        type=float,
        default=1.0,
        help=(
            "delay before reconnecting before matchmaking or after a clean end_game; "
            "active games are never reconnected"
        ),
    )
    parser.add_argument(
        "--action-deadline-ms",
        type=float,
        default=float(os.getenv("RIICHI_ACTION_DEADLINE_MS", DEFAULT_ACTION_DEADLINE_MS)),
        help="drop request_action responses after this local deadline to avoid stale WebSocket actions",
    )
    parser.add_argument(
        "--allow-ws-proxy",
        action="store_true",
        help=(
            "allow websockets versions with proxy support to use proxy environment variables; "
            "by default the client disables WebSocket proxy use when the installed library supports it"
        ),
    )
    parser.add_argument(
        "--ws-proxy",
        default=os.getenv("RIICHI_WS_PROXY", ""),
        help=(
            "explicit HTTP CONNECT proxy for WebSocket traffic, e.g. http://127.0.0.1:7890; "
            "also configurable with RIICHI_WS_PROXY"
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


async def _async_main(args: argparse.Namespace) -> None:
    project_root = Path(args.project_root)
    explicit_model_path = Path(args.model_path) if args.model_path else None

    if args.mode == "local":
        isolated_agents = args.bot_name == "mortal" and not args.validation_safe
        agent = None
        agents = None
        if isolated_agents:
            agents = {
                pid: create_riichi_dev_agent(
                    bot_name=args.bot_name,
                    project_root=project_root,
                    model_path=explicit_model_path,
                    device=args.device,
                    verbose=args.verbose,
                    model_version=args.model_version or None,
                    rank_pt_lambda=args.rank_pt_lambda,
                    validation_safe=args.validation_safe,
                )
                for pid in range(4)
            }
        else:
            agent = create_riichi_dev_agent(
                bot_name=args.bot_name,
                project_root=project_root,
                model_path=explicit_model_path,
                device=args.device,
                verbose=args.verbose,
                model_version=args.model_version or None,
                rank_pt_lambda=args.rank_pt_lambda,
                validation_safe=args.validation_safe,
            )
        result = run_local_game(
            agent=agent,
            agents=agents,
            game_mode=args.game_mode,
            seed=args.seed,
            max_steps=args.max_steps,
        )
        print(json.dumps(result, ensure_ascii=False))
        return

    token_source = "cli"
    if not args.token:
        args.token, token_source = _resolve_default_token_with_source(args.bot_name)

    if not args.token:
        raise SystemExit("missing bot token; pass --token or set LATTEKEY/RIICHI_BOT_TOKEN in project .env")

    resolved_model_path = _resolve_model_path(
        bot_name=args.bot_name,
        project_root=project_root,
        model_path=explicit_model_path,
    )
    _log_startup_self_check(
        queue=args.queue,
        bot_name=args.bot_name,
        model_version=args.model_version or None,
        token=args.token,
        token_source=token_source,
        project_root=project_root,
        model_path=resolved_model_path,
        validation_safe=args.validation_safe,
    )

    agent = create_riichi_dev_agent(
        bot_name=args.bot_name,
        project_root=project_root,
        model_path=explicit_model_path,
        device=args.device,
        verbose=args.verbose,
        model_version=args.model_version or None,
        rank_pt_lambda=args.rank_pt_lambda,
        validation_safe=args.validation_safe,
        preload_mortal=args.bot_name == "mortal",
    )

    config = RiichiDevClientConfig(
        token=args.token,
        bot_name=args.bot_name,
        model_version=args.model_version or None,
        queue=args.queue,
        base_url=args.base_url,
        ws_url_override=args.ws_url or None,
        model_path=explicit_model_path,
        project_root=project_root,
        device=args.device,
        rank_pt_lambda=args.rank_pt_lambda,
        verbose=args.verbose,
        origin=args.origin or None,
        user_agent=args.user_agent,
        audit_log_path=Path(args.audit_log_path) if args.audit_log_path else None,
        auto_reconnect=not args.no_auto_reconnect,
        reconnect_delay_sec=args.reconnect_delay_sec,
        action_deadline_ms=args.action_deadline_ms,
        preload_mortal=args.bot_name == "mortal",
        disable_ws_proxy=not args.allow_ws_proxy,
        ws_proxy=args.ws_proxy or None,
    )
    client = RiichiDevBotClient(config, agent=agent)
    await client.run()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    asyncio.run(_async_main(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
