# -*- coding: utf-8 -*-
"""R11-E1：Season Registry 生命周期 + 不变量（canonical runtime registry 写侧）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workbench"))
sys.path.insert(0, str(ROOT / "src"))

from replay import season_registry as sr  # noqa: E402
from replay.ladder import SeasonNotFoundError, SeasonRegistryError  # noqa: E402


@pytest.fixture
def configs_dir(tmp_path):
    return tmp_path / "registries"


def _write_season(configs_dir: Path, season_id: str, **overrides):
    payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": season_id,
        "title": season_id,
        "status": "running",
        "default": False,
        "report_dir": str(configs_dir / "reports" / season_id),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {"sources_root": "sources", "participants": {"enabled": True, "exclusive": True}},
        "models": [],
        **overrides,
    }
    (configs_dir / f"{season_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_season_is_draft_with_empty_enrollment(configs_dir):
    season = sr.create_season(configs_dir, season_id="season-x")
    assert season["status"] == "draft"
    assert season["default"] is False
    assert season["models"] == []
    assert season["schema"] == "keqing.ladder.season.v1"
    assert (configs_dir / "season-x.json").exists()


def test_create_season_rejects_duplicate_and_empty(configs_dir):
    sr.create_season(configs_dir, season_id="season-x")
    with pytest.raises(ValueError, match="赛季已存在"):
        sr.create_season(configs_dir, season_id="season-x")
    with pytest.raises(ValueError, match="season_id 不能为空"):
        sr.create_season(configs_dir, season_id="  ")


# ---------------------------------------------------------------------------
# lifecycle transitions
# ---------------------------------------------------------------------------

def test_status_transitions_are_forward_only(configs_dir):
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_season_status(configs_dir, "s1", "completed")
    sr.set_season_status(configs_dir, "s1", "archived")
    assert sr.get_season(configs_dir, "s1")["status"] == "archived"

    # 倒退迁移全部拒绝
    _write_season(configs_dir, "s2")  # running
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s2", "draft")
    sr.set_season_status(configs_dir, "s2", "completed")
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s2", "running")
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s2", "draft")


def test_complete_default_season_clears_default(configs_dir):
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_default_season(configs_dir, "s1")
    assert sr.get_season(configs_dir, "s1")["default"] is True
    sr.set_season_status(configs_dir, "s1", "completed")
    assert sr.get_season(configs_dir, "s1")["default"] is False


# ---------------------------------------------------------------------------
# single default invariant
# ---------------------------------------------------------------------------

def test_single_default_invariant(configs_dir):
    for sid in ("s1", "s2"):
        sr.create_season(configs_dir, season_id=sid)
        sr.set_season_status(configs_dir, sid, "running")
    sr.set_default_season(configs_dir, "s1")
    sr.set_default_season(configs_dir, "s2")
    # 只有一个 default（s2），s1 被自动摘除
    defaults = [s for s in sr.list_season_configs(configs_dir) if s.get("default") is True]
    assert [s["season_id"] for s in defaults] == ["s2"]
    assert sr.get_season(configs_dir, "s1")["default"] is False


def test_only_running_season_can_be_default(configs_dir):
    sr.create_season(configs_dir, season_id="draft-season")
    with pytest.raises(SeasonRegistryError, match="只有 running 赛季可以设为当前"):
        sr.set_default_season(configs_dir, "draft-season")


def test_missing_season_raises(configs_dir):
    with pytest.raises(SeasonNotFoundError):
        sr.get_season(configs_dir, "ghost")
    with pytest.raises(SeasonNotFoundError):
        sr.set_season_status(configs_dir, "ghost", "running")


# ---------------------------------------------------------------------------
# API-level（server 端点）
# ---------------------------------------------------------------------------

def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _body(response) -> dict:
    import json as _json

    return _json.loads(response.body.decode("utf-8"))


@pytest.fixture
def manager_server(tmp_path, monkeypatch):
    from replay import server

    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", tmp_path / "registries")
    return server


def test_api_season_lifecycle(manager_server):
    server = manager_server
    # 创建（draft, default=False, 空阵容）
    resp = _run(server.create_ladder_season({"season_id": "s1", "title": "Season One"}))
    assert resp.status_code == 200
    season = _body(resp)
    assert season["status"] == "draft"
    assert season["default"] is False
    # 开始 → running
    assert _body(_run(server.start_ladder_season("s1")))["status"] == "running"
    # 设为当前
    assert _body(_run(server.set_default_ladder_season("s1")))["default"] is True
    # 结束 → completed，且自动摘除 default
    done = _body(_run(server.complete_ladder_season("s1")))
    assert done["status"] == "completed"
    assert done["default"] is False


def test_api_season_lifecycle_conflicts_are_409(manager_server):
    server = manager_server
    _run(server.create_ladder_season({"season_id": "s1"}))
    # draft 不能设为当前 → 409
    resp = _run(server.set_default_ladder_season("s1"))
    assert resp.status_code == 409
    assert "只有 running" in _body(resp)["error"]
    # 非法迁移（draft → completed）→ 409
    resp = _run(server.complete_ladder_season("s1"))
    assert resp.status_code == 409
    assert "状态迁移非法" in _body(resp)["error"]
    # 重复创建 → 409
    resp = _run(server.create_ladder_season({"season_id": "s1"}))
    assert resp.status_code == 409
    assert "赛季已存在" in _body(resp)["error"]
    # 未找到 → 404
    resp = _run(server.start_ladder_season("ghost"))
    assert resp.status_code == 404
