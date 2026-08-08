from __future__ import annotations

from tools.analyze_riichi_dev_opportunities import summarize_riichi_dev_audit


def test_summarize_riichi_dev_audit_counts_opportunities_and_choices() -> None:
    records = [
        {
            "kind": "request_action",
            "possible_actions": [
                {"type": "reach", "actor": 0},
                {"type": "dahai", "actor": 0, "pai": "5m"},
            ],
            "response": {"type": "dahai", "actor": 0, "pai": "5m"},
        },
        {
            "kind": "request_action",
            "possible_actions": [
                {"type": "pon", "actor": 0, "pai": "P", "consumed": ["P", "P"]},
                {"type": "none"},
            ],
            "response": {"type": "none"},
        },
        {
            "kind": "request_action",
            "possible_actions": [
                {"type": "chi", "actor": 0, "pai": "3m", "consumed": ["1m", "2m"]},
                {"type": "none"},
            ],
            "response": {"type": "chi", "actor": 0, "pai": "3m", "consumed": ["1m", "2m"]},
        },
        {
            "kind": "request_action",
            "possible_actions": [{"type": "hora", "actor": 0, "target": 0}],
            "response": {"type": "hora", "actor": 0, "target": 0},
        },
        {
            "kind": "send_result",
            "success": True,
            "response": {"type": "chi"},
        },
    ]

    summary = summarize_riichi_dev_audit(records, path="dummy.jsonl")

    assert summary["request_action_count"] == 4
    assert summary["opportunity_counts"]["reach"] == 1
    assert summary["opportunity_counts"]["meld_any"] == 2
    assert summary["opportunity_counts"]["chi"] == 1
    assert summary["opportunity_counts"]["pon"] == 1
    assert summary["chosen_when_possible_counts"]["reach"] == 0
    assert summary["chosen_when_possible_counts"]["meld_any"] == 1
    assert summary["chosen_when_possible_counts"]["chi"] == 1
    assert summary["chosen_when_possible_counts"]["pon"] == 0
    assert summary["chosen_when_possible_counts"]["hora"] == 1
    assert summary["response_type_counts"]["none"] == 1
    assert summary["send_success_count"] == 1

