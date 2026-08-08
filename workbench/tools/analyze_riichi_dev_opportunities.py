from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

MELD_TYPES = {"chi", "pon", "daiminkan", "ankan", "kakan"}
KAN_TYPES = {"daiminkan", "ankan", "kakan"}
TRACKED_OPPORTUNITIES = (
    "reach",
    "hora",
    "meld_any",
    "chi",
    "pon",
    "kan_any",
    "daiminkan",
    "ankan",
    "kakan",
)


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _possible_type_set(record: dict[str, Any]) -> set[str]:
    return {
        str(action.get("type"))
        for action in (record.get("possible_actions") or [])
        if isinstance(action, dict) and action.get("type")
    }


def _response_type(record: dict[str, Any]) -> str:
    response = record.get("response") or {}
    if isinstance(response, dict) and response.get("type"):
        return str(response["type"])
    return "missing"


def summarize_riichi_dev_audit(records: list[dict[str, Any]], *, path: str | None = None) -> dict[str, Any]:
    request_records = [record for record in records if record.get("kind") == "request_action"]
    send_records = [record for record in records if record.get("kind") == "send_result"]
    disconnect_records = [record for record in records if record.get("kind") == "disconnect"]
    agent_error_records = [record for record in records if record.get("kind") == "agent_error"]

    response_type_counts: Counter[str] = Counter()
    opportunity_counts: Counter[str] = Counter()
    chosen_when_possible_counts: Counter[str] = Counter()
    opportunity_response_breakdown: dict[str, Counter[str]] = {
        key: Counter() for key in TRACKED_OPPORTUNITIES
    }

    for record in request_records:
        response_type = _response_type(record)
        response_type_counts[response_type] += 1
        possible_types = _possible_type_set(record)

        opportunities = {
            "reach": "reach" in possible_types,
            "hora": "hora" in possible_types,
            "meld_any": any(t in possible_types for t in MELD_TYPES),
            "chi": "chi" in possible_types,
            "pon": "pon" in possible_types,
            "kan_any": any(t in possible_types for t in KAN_TYPES),
            "daiminkan": "daiminkan" in possible_types,
            "ankan": "ankan" in possible_types,
            "kakan": "kakan" in possible_types,
        }

        for key, active in opportunities.items():
            if not active:
                continue
            opportunity_counts[key] += 1
            opportunity_response_breakdown[key][response_type] += 1

        if opportunities["reach"] and response_type == "reach":
            chosen_when_possible_counts["reach"] += 1
        if opportunities["hora"] and response_type == "hora":
            chosen_when_possible_counts["hora"] += 1
        if opportunities["meld_any"] and response_type in MELD_TYPES:
            chosen_when_possible_counts["meld_any"] += 1
        if opportunities["chi"] and response_type == "chi":
            chosen_when_possible_counts["chi"] += 1
        if opportunities["pon"] and response_type == "pon":
            chosen_when_possible_counts["pon"] += 1
        if opportunities["kan_any"] and response_type in KAN_TYPES:
            chosen_when_possible_counts["kan_any"] += 1
        if opportunities["daiminkan"] and response_type == "daiminkan":
            chosen_when_possible_counts["daiminkan"] += 1
        if opportunities["ankan"] and response_type == "ankan":
            chosen_when_possible_counts["ankan"] += 1
        if opportunities["kakan"] and response_type == "kakan":
            chosen_when_possible_counts["kakan"] += 1

    chosen_rates = {
        key: _safe_rate(chosen_when_possible_counts[key], opportunity_counts[key])
        for key in TRACKED_OPPORTUNITIES
    }
    opportunity_rates = {
        key: _safe_rate(opportunity_counts[key], len(request_records))
        for key in TRACKED_OPPORTUNITIES
    }
    send_success_count = sum(1 for record in send_records if record.get("success") is True)
    send_failure_count = sum(1 for record in send_records if record.get("success") is False)

    return {
        "path": path,
        "total_records": len(records),
        "request_action_count": len(request_records),
        "response_type_counts": dict(response_type_counts),
        "opportunity_counts": {key: int(opportunity_counts[key]) for key in TRACKED_OPPORTUNITIES},
        "opportunity_rates": opportunity_rates,
        "chosen_when_possible_counts": {
            key: int(chosen_when_possible_counts[key]) for key in TRACKED_OPPORTUNITIES
        },
        "chosen_when_possible_rates": chosen_rates,
        "opportunity_response_breakdown": {
            key: dict(counter) for key, counter in opportunity_response_breakdown.items()
        },
        "send_result_count": len(send_records),
        "send_success_count": send_success_count,
        "send_failure_count": send_failure_count,
        "disconnect_count": len(disconnect_records),
        "agent_error_count": len(agent_error_records),
    }


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _format_pct(value: float) -> str:
    return f"{value * 100.0:.2f}%"


def print_summary(summary: dict[str, Any]) -> None:
    print(f"# {summary.get('path') or 'riichi.dev audit'}")
    print(f"request_action_count: {summary['request_action_count']}")
    print(f"response_type_counts: {summary['response_type_counts']}")
    print(
        "send_results: "
        f"{summary['send_result_count']} "
        f"(success={summary['send_success_count']}, failure={summary['send_failure_count']})"
    )
    print(
        f"disconnect_count: {summary['disconnect_count']} | "
        f"agent_error_count: {summary['agent_error_count']}"
    )
    print("opportunity summary:")
    for key in TRACKED_OPPORTUNITIES:
        opp = summary["opportunity_counts"].get(key, 0)
        opp_rate = summary["opportunity_rates"].get(key, 0.0)
        chosen = summary["chosen_when_possible_counts"].get(key, 0)
        chosen_rate = summary["chosen_when_possible_rates"].get(key, 0.0)
        print(
            f"  - {key:10s} "
            f"opp={opp:6d} ({_format_pct(opp_rate):>8s}) "
            f"chosen={chosen:6d} ({_format_pct(chosen_rate):>8s})"
        )
    print("response breakdown when opportunity exists:")
    for key in ("reach", "meld_any", "chi", "pon", "kan_any", "hora"):
        print(f"  - {key}: {summary['opportunity_response_breakdown'].get(key, {})}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze opportunity-based action rates from riichi.dev audit logs")
    parser.add_argument("paths", nargs="+", help="JSONL audit log paths")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON summary")
    args = parser.parse_args()

    summaries = [
        summarize_riichi_dev_audit(load_jsonl_records(path), path=path)
        for path in args.paths
    ]
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return

    for idx, summary in enumerate(summaries):
        if idx:
            print()
        print_summary(summary)


if __name__ == "__main__":
    main()
