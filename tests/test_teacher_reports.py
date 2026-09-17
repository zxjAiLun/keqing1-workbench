# -*- coding: utf-8 -*-
"""教师报告归档（牌谱积累）测试。

覆盖：
  1. 按内容指纹去重：同一份报告重复归档只留一份文件，引用次数累加；
  2. 登记表字段（来源/教师标签/视角/获取时间/内容 sha256）；
  3. 牌谱目录副本随 replay_id 落地；
  4. write_external_teacher_reports 集成：给了 raws 就不再联网抓取，且会归档；
  5. 只读 API（/api/teacher-reports[/{id}]）。

口径（沿用 owner/Astra 的边界）：只存紧凑原始报告、不做 observation 展开；
一份报告只提供所选玩家的标签；展示概率不是训练目标。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from replay import external_reports, teacher_reports


def _raw_report(*, model_tag: str = "4.1b", player_id: int = 2, decisions: int = 3) -> dict:
    """最小但结构真实的报告（字段与站点报告一致：review.kyokus[].entries + mjai_log）。"""
    entries = [
        {
            "step": index,
            "source_event_index": index + 3,
            "actor": player_id,
            "decision_kind": "draw_discard",
            "chosen": {"type": "dahai", "actor": player_id, "pai": "1m", "tsumogiri": False},
            "gt_action": {"type": "dahai", "actor": player_id, "pai": "2m", "tsumogiri": False},
            "candidates": [
                {"action": {"type": "dahai", "actor": player_id, "pai": "1m"}, "final_score": -1.5, "prob": 0.9},
                {"action": {"type": "dahai", "actor": player_id, "pai": "2m"}, "final_score": -2.0, "prob": 0.1},
            ],
        }
        for index in range(decisions)
    ]
    return {
        "version": "1.0",
        "engine": "mortal",
        "player_id": player_id,
        "game_length": "hanchan",
        "mjai_log": [
            {"type": "start_game"},
            {
                "type": "start_kyoku",
                "bakaze": "E", "kyoku": 1, "honba": 0,
                "scores": [25000, 25000, 25000, 25000],
                "dora_marker": "1p",
                # tehais[player_id] 必须与本地 decisions 的首个决策手牌一致（_validate_first_kyoku 会校验）
                "tehais": [["1m", "2m"], [], [], []] if player_id == 0 else [[], [], ["1m", "2m"], []],
            },
        ],
        "review": {
            "model_tag": model_tag,
            "kyokus": [{"bakaze": "E", "kyoku": 1, "honba": 0, "entries": entries}],
        },
    }


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path, monkeypatch):
    """归档根与牌谱根都隔离到 tmp，避免污染 keqing-data。"""
    archive = tmp_path / "teacher-reports"
    replays = tmp_path / "replays"
    monkeypatch.setattr(teacher_reports, "teacher_reports_root", lambda: archive)
    monkeypatch.setattr("workbench.runtime.resolver.data_path", lambda *parts: tmp_path.joinpath(*parts))
    return archive, replays


def test_archives_raw_report_and_registers_provenance(isolated_roots):
    archive, _ = isolated_roots
    raw = _raw_report(model_tag="4.1b", player_id=2, decisions=5)

    entry = teacher_reports.archive_teacher_report(
        raw,
        source="mortal",
        source_url="https://mjai.ekyu.moe/report/30bfcaf5c9aff36c.json",
        model_tag="Mortal 4.1b",
        player_id=2,
        replay_id="replay_A",
        root=archive,
    )

    assert entry["report_id"] == teacher_reports.report_id_for(entry["content_sha256"])
    assert entry["source"] == "mortal"
    assert entry["model_tag"] == "Mortal 4.1b"
    assert entry["player_id"] == 2
    assert entry["kyoku_count"] == 1
    assert entry["decision_count"] == 5
    assert entry["replay_ids"] == ["replay_A"]
    assert entry["first_seen_at"] and entry["last_seen_at"]

    # 原始报告原文落盘，且与输入逐字段一致（只存紧凑原文，不展开）
    stored = json.loads((archive / f"{entry['content_sha256']}.json").read_text(encoding="utf-8"))
    assert stored == raw


def test_same_content_is_deduplicated_but_accumulates_replays(isolated_roots):
    archive, _ = isolated_roots
    raw = _raw_report()

    first = teacher_reports.archive_teacher_report(
        raw, source="mortal", source_url="u1", model_tag="Mortal 4.1b", player_id=2,
        replay_id="replay_A", root=archive,
    )
    second = teacher_reports.archive_teacher_report(
        raw, source="mortal", source_url="u2", model_tag="Mortal 4.1b", player_id=2,
        replay_id="replay_B", root=archive,
    )

    assert second["content_sha256"] == first["content_sha256"]
    assert second["replay_ids"] == ["replay_A", "replay_B"]
    assert second["fetch_count"] == 2
    assert len(teacher_reports.list_teacher_reports(root=archive)) == 1
    # 只有一份报告文件 + 一份登记表
    assert sorted(p.name for p in archive.iterdir()) == [
        f"{first['content_sha256']}.json",
        "index.json",
    ]

    # 同一 replay 重复引用不应重复登记
    third = teacher_reports.archive_teacher_report(
        raw, source="mortal", source_url="u1", model_tag="Mortal 4.1b", player_id=2,
        replay_id="replay_B", root=archive,
    )
    assert third["replay_ids"] == ["replay_A", "replay_B"]


def test_content_fingerprint_is_key_order_independent():
    left = {"review": {"model_tag": "4.1b", "kyokus": []}, "player_id": 0}
    right = {"player_id": 0, "review": {"kyokus": [], "model_tag": "4.1b"}}
    assert teacher_reports.report_content_sha256(left) == teacher_reports.report_content_sha256(right)


def test_list_filters_and_load_roundtrip(isolated_roots):
    archive, _ = isolated_roots
    a = teacher_reports.archive_teacher_report(
        _raw_report(model_tag="4.1b", player_id=2), source="mortal", model_tag="Mortal 4.1b",
        player_id=2, replay_id="replay_A", root=archive,
    )
    teacher_reports.archive_teacher_report(
        _raw_report(model_tag="4.1c", player_id=0, decisions=4), source="mortal", model_tag="Mortal 4.1c",
        player_id=0, replay_id="replay_A", root=archive,
    )

    assert len(teacher_reports.list_teacher_reports(root=archive)) == 2
    assert len(teacher_reports.list_teacher_reports(model_tag="Mortal 4.1b", root=archive)) == 1
    assert len(teacher_reports.list_teacher_reports(player_id=0, root=archive)) == 1
    assert len(teacher_reports.list_teacher_reports(source="naga", root=archive)) == 0

    loaded = teacher_reports.load_teacher_report(a["report_id"], root=archive)
    assert loaded is not None
    raw, entry = loaded
    assert entry["report_id"] == a["report_id"]
    assert raw["review"]["model_tag"] == "4.1b"
    assert teacher_reports.load_teacher_report("missing", root=archive) is None


def test_replay_copy_lives_next_to_the_replay(isolated_roots):
    archive, replays = isolated_roots
    raw = _raw_report()
    replay_id = "replay_356af54a_1789467629"

    path = teacher_reports.write_replay_report_copy(
        raw, replay_id=replay_id, label="Mortal 4.1b", player_id=2, replays_root=replays,
    )

    assert path.parent == replays / replay_id / "external_reports"
    assert path.name.endswith("__Mortal_4.1b__p2.json")
    assert json.loads(path.read_text(encoding="utf-8")) == raw


def test_write_external_teacher_reports_uses_prefetched_raws_and_archives(isolated_roots, tmp_path):
    """集成：给了 raws 就不再联网抓取；报告照旧写 output_dir，同时归档 + 写牌谱副本。"""
    archive, replays = isolated_roots
    raw = _raw_report(model_tag="4.1b", player_id=2)
    output_dir = tmp_path / "artifacts" / "replay_model_reviews"
    replay_id = "replay_A"
    # 报告与本地局必须一致（_validate_first_kyoku 是真实守卫，不能绕）
    local_decisions = {
        "log": [{
            "step": 0, "source_event_index": 3, "bakaze": "E", "kyoku": 1, "honba": 0,
            "scores": [25000, 25000, 25000, 25000], "dora_markers": ["1p"], "hand": ["1m", "2m"],
        }],
        "kyoku_order": [["E", 1, 0]],
    }
    raw["mjai_log"][1]["tehais"] = [[], [], ["1m", "2m"], []]

    def _forbidden(*args, **kwargs):  # pragma: no cover - 只用于断言不被调用
        raise AssertionError("给了 raws 时不应再联网抓取")

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(external_reports, "fetch_external_report", _forbidden)
        paths = external_reports.write_external_teacher_reports(
            replay_id=replay_id,
            player_id=2,
            decisions=local_decisions,
            links={"mortal": "https://mjai.ekyu.moe/report/30bfcaf5c9aff36c.json"},
            output_dir=output_dir,
            raws={"mortal": raw},
            archive_root=archive,
            replays_root=replays,
        )

    assert len(paths) == 1
    assert paths[0].parent == output_dir
    written = json.loads(paths[0].read_text(encoding="utf-8"))
    assert written["review"]["model_tag"] == "Mortal 4.1b"  # 报告本体仍带 Mortal 前缀
    assert written["schema"] == "keqing1.external_teacher_report.v1"

    entries = teacher_reports.list_teacher_reports(root=archive)
    assert len(entries) == 1
    assert entries[0]["replay_ids"] == [replay_id]
    assert entries[0]["model_tag"] == "Mortal 4.1b"
    assert (replays / replay_id / "external_reports").is_dir()


def test_teacher_report_endpoints_list_and_read(isolated_roots, monkeypatch):
    """只读端点：列表字段齐全，未知 report_id 返回 404。

    直接调用端点函数（本 venv 没装 httpx，用不了 TestClient；与
    tests/test_participants_server.py 直接调用 api 函数同一做法）。
    """
    archive, _ = isolated_roots
    raw = _raw_report(model_tag="4.1b", player_id=2, decisions=6)
    entry = teacher_reports.archive_teacher_report(
        raw, source="mortal",
        source_url="https://mjai.ekyu.moe/report/c87abfa74d78a178.json",
        model_tag="Mortal 4.1b", player_id=2, replay_id="replay_A", root=archive,
    )

    from replay import server

    listed = asyncio.run(server.list_teacher_reports())
    assert listed.status_code == 200
    payload = json.loads(listed.body)
    assert payload["count"] == 1
    assert payload["model_tags"] == ["Mortal 4.1b"]
    assert payload["sources"] == ["mortal"]
    item = payload["reports"][0]
    assert item["report_id"] == entry["report_id"]
    assert item["decision_count"] == 6
    assert item["source_url"].endswith("c87abfa74d78a178.json")
    assert item["replay_ids"] == ["replay_A"]

    detail = asyncio.run(server.get_teacher_report(entry["report_id"]))
    assert detail.status_code == 200
    assert json.loads(detail.body)["report"]["review"]["model_tag"] == "4.1b"

    missing = asyncio.run(server.get_teacher_report("deadbeef0000"))
    assert missing.status_code == 404
