import json
from pathlib import Path

from replay.bot import _same_action


def test_replay_action_semantics_matrix_matches_backend_helper():
    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "replay_action_semantics_matrix.json"
    )
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        assert _same_action(case["left"], case["right"]) is case["same"], case["name"]
