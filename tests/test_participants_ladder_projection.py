# -*- coding: utf-8 -*-
"""R10-F：Ledger-driven Ladder Projection——dirty marker、adapter 过滤、投影 API。"""
from __future__ import annotations

import json

import pytest

from participants import api, ledger, registry
from participants.schemas import AccountCreate, MatchCreate, MatchRevise, MatchSeat, MatchVoid


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    # 正式资格 gate 需要有效赛季配置（测试账号全部为成员）：
    # 避免落到仓库版本化赛季（无 70k@03）导致 eligible Match 创建被拒。
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    # adapter 过滤测试用到 other-season：为它建同结构配置（gate 要求赛季存在）
    other_cfg = dict(season_cfg)
    other_cfg["season_id"] = "other-season"
    (configs / "other-season.json").write_text(json.dumps(other_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    return root


SEASON = "official-ladder-v1"


def _accounts():
    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("70k@03", "managed_bot"),
    ):
        registry.create_account(AccountCreate(account_id=account_id, display_name=account_id, account_type=account_type))


def _match_create(season_id=SEASON, rating_eligible=True, occurred_at="2026-08-04T10:00:00+08:00"):
    return MatchCreate(
        occurred_at=occurred_at,
        game_length="hanchan",
        season_id=season_id,
        rating_eligible=rating_eligible,
        seats=[
            MatchSeat(seat=0, account_id="nick@01"),
            MatchSeat(seat=1, account_id="70k@01"),
            MatchSeat(seat=2, account_id="70k@02"),
            MatchSeat(seat=3, account_id="70k@03"),
        ],
        final_scores=[30000, 20000, 25000, 25000],
        source="manual",
    )


def test_create_match_marks_dirty_and_pending(participants_root):
    _accounts()
    match = ledger.create_match(_match_create(), registry)
    assert match.season_id == SEASON
    assert match.rating_eligible is True
    assert match.ladder_projection_state == "pending"
    assert ledger.ladder_dirty_path(SEASON).exists()


def test_ineligible_match_no_dirty(participants_root):
    _accounts()
    match = ledger.create_match(_match_create(rating_eligible=False), registry)
    # P2-2：not eligible → not_applicable；season_id 非空仍标 dirty（可后续提升）
    assert ledger.ladder_dirty_path(SEASON).exists()
    assert match.ladder_projection_state == "not_applicable"


def test_revise_void_mark_dirty(participants_root):
    _accounts()
    match = ledger.create_match(_match_create(), registry)
    ledger.clear_ladder_dirty(SEASON)
    ledger.revise_match(match.match_id, MatchRevise(note="修正"), registry)
    assert ledger.ladder_dirty_path(SEASON).exists()
    ledger.clear_ladder_dirty(SEASON)
    ledger.void_match(match.match_id, MatchVoid(reason="作废"))
    assert ledger.ladder_dirty_path(SEASON).exists()


def test_ledger_adapter_filters(participants_root):
    from replay import ladder_ingest

    _accounts()
    eligible = ledger.create_match(_match_create(), registry)
    ledger.create_match(_match_create(rating_eligible=False), registry)
    ledger.create_match(_match_create(season_id="other-season"), registry)
    voided = ledger.create_match(_match_create(), registry)
    ledger.void_match(voided.match_id, MatchVoid(reason="作废"))

    adapter = ladder_ingest.ParticipantLedgerAdapter(season_id=SEASON)
    matches = list(adapter.iter_matches(participants_root))
    assert [m.match_id for m in matches] == [eligible.match_id]
    assert matches[0].source_type == "participants"
    assert matches[0].source_ref is None
    assert matches[0].players[0].account_id == "nick@01"
    assert matches[0].players[3].final_score == 25000


def test_project_season_api_success(participants_root, monkeypatch, tmp_path):
    from replay import publish_ladder_snapshot

    _accounts()
    match = ledger.create_match(_match_create(), registry)

    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    monkeypatch.setattr(
        publish_ladder_snapshot, "publish_snapshot",
        lambda **kw: {"snapshot_dir": str(tmp_path / "snap"), "games": 1},
    )

    resp = api.api_project_ladder_season(SEASON)
    assert resp["state"] == "ready"
    assert resp["games"] == 1
    assert not ledger.ladder_dirty_path(SEASON).exists()
    assert ledger.get_match(match.match_id).ladder_projection_state == "ready"


def test_project_season_api_failure_keeps_dirty(participants_root, monkeypatch, tmp_path):
    from fastapi import HTTPException
    from replay import publish_ladder_snapshot

    _accounts()
    match = ledger.create_match(_match_create(), registry)

    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": "artifacts/ladder/reports/official-ladder-v1",
        "status": "running",
        "scoring": {"system": "tenhou_rank_progression"},
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": True},
        },
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01"}]},
            {
                "model_id": "70k",
                "accounts": [
                    {"account_id": "70k@01"},
                    {"account_id": "70k@02"},
                    {"account_id": "70k@03"},
                ],
            },
        ],
    }
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    monkeypatch.setattr(
        publish_ladder_snapshot, "publish_snapshot",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(HTTPException) as exc:
        api.api_project_ladder_season(SEASON)
    assert exc.value.status_code == 502
    assert ledger.ladder_dirty_path(SEASON).exists()
    assert ledger.get_match(match.match_id).ladder_projection_state == "error"


def test_ladder_projection_status(participants_root):
    _accounts()
    ledger.create_match(_match_create(), registry)
    status = api.api_ladder_projection_status(SEASON)
    assert status["dirty"] is True
    assert status["eligible_count"] == 1
    assert status["states"].get("pending") == 1


def test_worker_cooldowns_failed_season(participants_root, monkeypatch):
    """R11-G Repair：worker 对 error 赛季冷却——失败后 60s 内不再自动重试
    （防止每 10s 触发"构建快照→写 tmp→切换失败"的风暴）。手动 project 不受限。"""
    from participants import projection

    calls: list[str] = []

    def _fake_project(season_id: str) -> dict:
        calls.append(season_id)
        return {"season_id": season_id, "state": "error", "reason": "boom"}

    monkeypatch.setattr(projection, "project_season", _fake_project)
    projection._failure_ts.clear()
    marker = projection.data_root() / "ladder_dirty_official-ladder-v2.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}", encoding="utf-8")

    # 第一次：project 被调用（失败）
    projection.run_dirty_projection()
    assert calls == ["official-ladder-v2"]
    # 冷却期内：不再调用
    projection.run_dirty_projection()
    projection.run_dirty_projection()
    assert calls == ["official-ladder-v2"]
    # 冷却过期后：恢复重试
    projection._failure_ts["official-ladder-v2"] -= projection._FAILURE_COOLDOWN_SECONDS + 1
    projection.run_dirty_projection()
    assert calls == ["official-ladder-v2", "official-ladder-v2"]
    marker.unlink(missing_ok=True)
