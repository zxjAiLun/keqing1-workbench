"""Final product choices: test API consumers, not only catalog declarations."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from inference.bot_registry import MORTAL_CHECKPOINTS, score_semantics_for
from workbench.model_catalog import DEFAULT_PLAY_MODEL, DEFAULT_REVIEW_MODELS


def post(app, path, data):
    """Exercise ASGI routing/Form parsing without the optional httpx2 package."""
    async def request():
        body = urlencode(data, doseq=True).encode()
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            sent.append(message)

        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        payload = json.loads(b"".join(m.get("body", b"") for m in sent))
        return status, payload
    return asyncio.run(request())


def test_new_game_defaults_do_not_repoint_historical_aliases(monkeypatch):
    from workbench.gateway.api.battle import StartBattleRequest, Start4BotRequest
    from workbench import launch_tenhou_bots as launcher
    from workbench.gateway.api.playwithyou import (
        _PLAYWITHYOU_MODEL_IDS, _resolve_spec,
        list_playwithyou_models, NETWORK_TO_SPEC, StartPlayWithYouRequest,
    )
    from workbench.gateway.tenhou_bot_client import BotClientConfig

    assert StartPlayWithYouRequest().networks == ["p4m11_u32", "none", "none", "none"]
    assert BotClientConfig().bot_name == "p4m11_u32"

    assert StartBattleRequest().bot_model == "p4m11_u32"
    assert Start4BotRequest().bot_model == "p4m11_u32"
    # Capture the real CLI parser, stopping before any gateway/bot launch.
    import argparse
    import pytest
    parse = argparse.ArgumentParser.parse_args
    parsed = []

    def capture(parser, *args, **kwargs):
        parsed.append(parse(parser, []))
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    with pytest.raises(SystemExit):
        launcher.main()
    from replay import server
    catalog = list_playwithyou_models()["models"]
    assert "m0_72k" in _PLAYWITHYOU_MODEL_IDS
    assert _resolve_spec("m0_72k", {}, 0) == "m0_72k"

    assert [m["model_id"] for m in catalog] == [
        "p4m11_u32",
        "m0_72k",
        "70k",
        "ext_mortal",
        "consensus_v1",
        "nova_v1",
        "luckyj_v1",
        "unknown_v1",
        "nova_v2",
    ]
    assert NETWORK_TO_SPEC["mortal"] == "mortal"
    assert MORTAL_CHECKPOINTS["mortal"] == Path("V2_74000/mortal_74000.pth")
    assert MORTAL_CHECKPOINTS["70k"] == Path("K0_70k/mortal_default_70k_promoted_candidate.pth")
    assert score_semantics_for("p4m11_u32") == "action_score"
    assert score_semantics_for("m0_72k") == "calibrated_q"


def test_m0_has_playwithyou_artifact_admission():
    from participants import registry
    from participants.artifact_policy import resolve_artifact_binding

    binding = resolve_artifact_binding(
        checkpoint="m0_72k",
        purpose="playwithyou_allowed",
        project_root=Path("."),
        registry=registry,
    )
    assert binding.model_identity_id == "model:m0_72k"
    assert binding.model_artifact_id == "model:m0_72k@de7f6d"
    assert not binding.artifact.is_ladder_eligible


def test_review_rejects_v2_before_fetch_or_inference(monkeypatch):
    from replay import server
    from replay import api

    def forbidden(*args, **kwargs):
        raise AssertionError("V2 must be refused before fetching or inference")

    monkeypatch.setattr(api, "run_replay_single_raw", forbidden)
    monkeypatch.setattr(server, "fetch_external_raw_reports", forbidden)
    status, payload = post(server.app, "/api/replay/multi-teacher", {
        "model_types": "mortal", "json_text": "[]", "mortal_url": "https://example.com/report.json",
    })
    assert status == 400
    assert "GUI review" in payload["error"]


def test_review_defaults_and_explicit_models_reach_the_resolver(tmp_path, monkeypatch):
    from replay import api, bot, server

    calls = []
    resolved = []
    monkeypatch.setattr(server, "BASE_DIR", tmp_path / "workbench" / "replay")

    def checkpoint(model):
        resolved.append(model)
        return tmp_path / f"{model}.pth"

    def run(events, **kwargs):
        calls.append(kwargs)
        return {"log": [], "player_id": 0, "player_names": ["A", "B", "C", "D"]}

    class Storage:
        def save(self, **kwargs):
            return "replay_test"

    async def events(**kwargs):
        return []

    monkeypatch.setattr(server, "_review_checkpoint_for_bot_type", checkpoint)
    monkeypatch.setattr(api, "run_replay_single_raw", run)
    monkeypatch.setattr(bot, "render_replay_json", lambda value: value)
    monkeypatch.setattr(server, "normalize_replay_decisions", lambda value: value)
    monkeypatch.setattr(server, "_events_from_replay_form", events)
    monkeypatch.setattr(server, "get_storage", lambda: Storage())

    status, payload = post(server.app, "/api/replay/multi-teacher", {"json_text": "[]"})
    assert status == 200, payload
    assert resolved == ["ext_mortal", "70k"] == list(DEFAULT_REVIEW_MODELS)
    assert [c["bot_type"] for c in calls] == resolved
    assert [m["label"] for m in payload["selected_teacher_models"]] == ["External Mortal", "K0"]
    calls.clear()
    resolved.clear()
    status, payload = post(server.app, "/api/replay/multi-teacher", {
        "model_types": ["p4m11_u32", "70k"], "json_text": "[]",
    })
    assert status == 200, payload
    assert resolved == ["p4m11_u32", "70k"]
    assert [c["checkpoint"] for c in calls] == [str(tmp_path / f"{m}.pth") for m in resolved]
    assert [(m["label"], m["score_semantics"]) for m in payload["selected_teacher_models"]] == [
        ("U32", "action_score"), ("K0", "calibrated_q"),
    ]


def test_historical_review_stays_readable_and_byte_identical(tmp_path):
    from replay.server import _list_review_history

    report_dir = tmp_path / "artifacts" / "replay_model_reviews"
    report_dir.mkdir(parents=True)
    labels = ["V2_candidate", "70k", "P4-M11_U32_(policy)", "M0_72k_(control)"]
    files = []
    for label in labels:
        path = report_dir / f"replay_old__{label}__p0.json"
        path.write_text(json.dumps({"review": {"model_tag": label}}), encoding="utf-8")
        files.append(path)
    before = {p: p.read_bytes() for p in files}

    class Storage:
        def list(self):
            return [{"replay_id": "replay_old", "player_names": ["old"], "created_at": "2026-01-01"}]

    history = _list_review_history(storage=Storage(), report_dir=report_dir, project_root=tmp_path)
    assert set(history[0]["models"]) == {"V2 candidate", "70k", "P4-M11 U32 (policy)", "M0 72k (control)"}
    assert len(history[0]["teacher_report_paths"]) == 4
    assert {p: p.read_bytes() for p in files} == before
