from __future__ import annotations

import gzip
import json
from pathlib import Path

import tools.inspect_event_decision as inspect_mod


def test_load_event_rows_supports_jsonl_and_gz(tmp_path: Path) -> None:
    rows = [
        {"type": "start_game"},
        {"type": "tsumo", "actor": 1, "pai": "5m"},
    ]
    plain = tmp_path / "sample.jsonl"
    gz = tmp_path / "sample.jsonl.gz"
    plain.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    assert inspect_mod.load_event_rows(plain) == rows
    assert inspect_mod.load_event_rows(gz) == rows


def test_inspect_event_decision_replays_until_target_and_returns_prediction(tmp_path: Path, monkeypatch) -> None:
    rows = [
        {"type": "start_game"},
        {
            "type": "start_kyoku",
            "bakaze": "E",
            "kyoku": 1,
            "honba": 0,
            "kyotaku": 0,
            "oya": 0,
            "scores": [25000, 25000, 25000, 25000],
            "dora_marker": "1m",
            "tehais": [
                ["1m"] * 13,
                ["2m"] * 13,
                ["3m"] * 13,
                ["4m"] * 13,
            ],
        },
        {"type": "tsumo", "actor": 1, "pai": "5m"},
        {"type": "dahai", "actor": 1, "pai": "9m", "tsumogiri": False},
    ]
    path = tmp_path / "sample.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    captured_events: list[dict] = []

    class FakeRuntimeBot:
        def __init__(self, player_id, model_path, device="cpu", model_version=None, **kwargs):
            self.player_id = player_id
            self.model_path = Path(model_path)
            self.device = device
            self.model_version = model_version

        def react(self, event):
            captured_events.append(event)
            if event == rows[2]:
                return {"type": "dahai", "actor": 1, "pai": "P", "tsumogiri": False}
            return None

    monkeypatch.setattr(inspect_mod, "RuntimeBot", FakeRuntimeBot)

    result = inspect_mod.inspect_event_decision(
        log_path=path,
        event_idx=2,
        bot_name="mortal",
        model_path=None,
        project_root=tmp_path,
        device="cpu",
    )

    assert captured_events == rows[:3]
    assert result["predicted_action"] == {"type": "dahai", "actor": 1, "pai": "P", "tsumogiri": False}
    assert result["actual_followup_event"] == rows[3]
    assert result["context"][0]["idx"] == 0
    assert result["context"][-1]["idx"] == 3
    assert result["model_path"] == str(tmp_path / "artifacts" / "models" / "mortal" / "best.pth")
