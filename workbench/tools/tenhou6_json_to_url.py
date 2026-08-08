"""Convert tenhou.net/6 JSON into tenhou.net/6/#json= URL lines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _compact_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _with_default_title(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("title"):
        return payload
    rule = dict(payload.get("rule") or {})
    return {"title": [str(rule.get("disp") or "Mortal"), ""], **payload}


def _naga_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _with_default_title(payload)
    rule = dict(normalized.get("rule") or {})
    rule.pop("aka51", None)
    rule.pop("aka52", None)
    rule.pop("aka53", None)
    rule.setdefault("aka", 1)
    if "disp" in rule and isinstance(rule["disp"], str):
        rule["disp"] = rule["disp"].replace(" ", "")
    return {**normalized, "rule": rule}


def tenhou6_json_to_url(payload: dict, *, ts: int | None = None, encode: bool = True) -> str:
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    json_part = quote(compact, safe="") if encode else compact
    suffix = "" if ts is None else f"&ts={int(ts)}"
    return f"https://tenhou.net/6/#json={json_part}{suffix}"


def split_kyoku_urls(payload: dict, *, ts: int | None = None, encode: bool = False) -> list[str]:
    logs = list(payload.get("log") or [])
    urls: list[str] = []
    for kyoku_log in logs:
        single = {key: value for key, value in payload.items() if key != "log"}
        single["log"] = [kyoku_log]
        urls.append(tenhou6_json_to_url(_naga_payload(single), ts=ts, encode=encode))
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="tenhou6 JSON file")
    parser.add_argument("output", type=Path, nargs="?", help="optional output .txt file")
    parser.add_argument("--ts", type=int, default=None, help="optional initial viewer step")
    parser.add_argument("--split-kyoku", action="store_true", help="write one tenhou.net/6 URL per kyoku")
    parser.add_argument("--raw-json", action="store_true", help="do not URL-encode the JSON fragment")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if args.split_kyoku:
        lines = split_kyoku_urls(payload, ts=args.ts, encode=not args.raw_json)
    else:
        lines = [tenhou6_json_to_url(_naga_payload(payload), ts=args.ts, encode=not args.raw_json)]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
