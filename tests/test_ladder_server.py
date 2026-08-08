from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from replay import ladder
from replay import server


def _season(season_id: str, **overrides) -> dict:
    base = {
        "schema": ladder.SEASON_SCHEMA,
        "season_id": season_id,
        "report_dir": f"artifacts/{season_id}",
        "status": "running",
        "models": [
            {"model_id": "m1", "checkpoint": "artifacts/m1.pth", "accounts": [
                {"account_id": "m1@01"}, {"account_id": "m1@02"},
            ]},
        ],
    }
    base.update(overrides)
    return base


def _write_env(tmp_path: Path, seasons: list[dict]) -> Path:
    configs_dir = tmp_path / "configs" / "ladder" / "seasons"
    configs_dir.mkdir(parents=True)
    for season in seasons:
        (configs_dir / f"{season['season_id']}.json").write_text(json.dumps(season), encoding="utf-8")
    return configs_dir


def _make_ready(project_root: Path, season: dict) -> None:
    report_dir = project_root / str(season["report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema": ladder.REPORT_SCHEMA, "games": 1, "accounts": [
        {"account_id": "m1@01", "model_label": "m1"},
        {"account_id": "m1@02", "model_label": "m1"},
    ]}
    (report_dir / "account_summary.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "account_ledger.jsonl").write_text("", encoding="utf-8")
    (report_dir / "rating_curve.csv").write_text("game_index,account_id,model_label,rating,pt,rank_name,games\n", encoding="utf-8")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def ladder_server(tmp_path: Path, monkeypatch):
    configs_dir = _write_env(tmp_path, [_season("dev-live", default=True)])
    _make_ready(tmp_path, _season("dev-live"))
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    return server


def test_season_list_has_top_level_default_fields(ladder_server):
    response = _run(ladder_server.list_ladder_seasons())
    body = json.loads(response.body.decode("utf-8"))
    assert body["schema"] == "keqing.ladder.seasons.v1"
    assert body["default_season_id"] == "dev-live"
    assert body["default_source"] == "registry"
    assert body["seasons"][0]["is_default"] is True


def test_409_error_stays_string_with_structured_reason(tmp_path, monkeypatch):
    configs_dir = _write_env(tmp_path, [_season("dev-live", default=True, report_dir="artifacts/missing")])
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    response = _run(server.get_ladder("dev-live"))
    assert response.status_code == 409
    body = json.loads(response.body.decode("utf-8"))
    # error 仍是字符串（向后兼容，不变成 [object Object]）
    assert isinstance(body["error"], str)
    assert "不存在" in body["error"]
    reason = body["reason"]
    assert reason["state"] == "not_published"
    assert reason["code"] == "season_report_dir_missing"
    assert isinstance(reason["message"], str)
    assert isinstance(reason["detail"], str)
    assert reason["retryable"] is True


def test_404_behavior_unchanged(tmp_path, monkeypatch):
    configs_dir = _write_env(tmp_path, [_season("dev-live")])
    _make_ready(tmp_path, _season("dev-live"))
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    # season 不存在
    response = _run(server.get_ladder("ghost"))
    assert response.status_code == 404
    assert "不存在" in json.loads(response.body.decode("utf-8"))["error"]
    # account 不存在
    response = _run(server.get_ladder_account("dev-live", "ghost@01"))
    assert response.status_code == 404
    # model 不存在
    response = _run(server.get_ladder_model("dev-live", "ghost"))
    assert response.status_code == 404


def test_registry_error_is_500(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs" / "ladder" / "seasons"
    configs_dir.mkdir(parents=True)
    (configs_dir / "bad.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    response = _run(server.list_ladder_seasons())
    assert response.status_code == 500


def test_season_list_readiness_not_ready(tmp_path, monkeypatch):
    configs_dir = _write_env(
        tmp_path,
        [
            _season("ready-season", default=False),
            _season("broken-season", report_dir="artifacts/missing"),
        ],
    )
    _make_ready(tmp_path, _season("ready-season"))
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    response = _run(server.list_ladder_seasons())
    body = json.loads(response.body.decode("utf-8"))
    by_id = {entry["season_id"]: entry for entry in body["seasons"]}
    assert by_id["ready-season"]["data_ready"] is True
    assert by_id["ready-season"]["readiness"]["state"] == "ready"
    assert by_id["ready-season"]["readiness"]["retryable"] is False
    assert by_id["broken-season"]["data_ready"] is False
    assert by_id["broken-season"]["readiness"]["state"] == "not_published"
    assert by_id["broken-season"]["readiness"]["code"] == "season_report_dir_missing"


def test_duplicate_default_fails_all_server_endpoints(tmp_path, monkeypatch):
    configs_dir = _write_env(
        tmp_path,
        [_season("aaa", default=True), _season("zzz", default=True)],
    )
    monkeypatch.setattr(server, "_LADDER_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    # catalog
    response = _run(server.list_ladder_seasons())
    assert response.status_code == 500
    # 实体端点不得绕过默认唯一性
    response = _run(server.get_ladder("aaa"))
    assert response.status_code == 500
    response = _run(server.get_ladder_account("aaa", "m1@01"))
    assert response.status_code == 500
    response = _run(server.get_ladder_model("aaa", "m1"))
    assert response.status_code == 500
