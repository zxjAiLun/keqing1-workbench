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
    # 归档模块在函数内 import data_path，server 与 teacher_reports 则是模块级 from-import，三处都要隔离
    monkeypatch.setattr("workbench.runtime.resolver.data_path", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr("replay.server.data_path", lambda *parts: tmp_path.joinpath(*parts), raising=False)
    monkeypatch.setattr(teacher_reports, "data_path", lambda *parts: tmp_path.joinpath(*parts), raising=False)
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


def test_import_endpoint_archives_links_and_survives_partial_failure(isolated_roots, monkeypatch):
    """导入端点：一行一个链接；单条失败不阻断其余；重复贴同一链接不重复占盘。"""
    archive, replays = isolated_roots
    raw = _raw_report(model_tag="4.1b", player_id=2, decisions=4)

    from replay import server

    fetched: list[str] = []

    def fake_fetch(links: dict[str, str]) -> dict[str, dict]:
        fetched.extend(links.values())
        out = {}
        for kind, link in links.items():
            if "bad" in link:
                raise ValueError(f"下载 {kind} Review 报告失败：连接失败")
            out[kind] = raw
        return out

    monkeypatch.setattr("replay.external_reports.resolve_external_report_url", lambda url, kind: url)
    monkeypatch.setattr(server, "fetch_external_raw_reports", fake_fetch)

    good = "https://mjai.ekyu.moe/killerducky/?data=/report/30bfcaf5c9aff36c.json"
    bad = "https://mjai.ekyu.moe/report/bad.json"
    payload = json.loads(asyncio.run(server.import_teacher_reports(urls=f"{good}\n{bad}", replay_id="", player_id=-1)).body)

    assert payload["imported"] == 1 and payload["failed"] == 1
    assert payload["replay_exists"] is False and payload["orphan"] is False  # 没给 replay_id 就不算孤儿
    assert {item["status"] for item in payload["results"]} == {"archived", "error"}
    archived = next(item for item in payload["results"] if item["status"] == "archived")
    assert archived["model_tag"] == "Mortal 4.1b"
    assert archived["decision_count"] == 4
    assert len(fetched) == 2  # 两条都尝试过：失败一条不影响另一条
    assert len(teacher_reports.list_teacher_reports(root=archive)) == 1

    # 同一链接再导入一次：仍只有一条登记，fetch_count 增长（内容去重）
    asyncio.run(server.import_teacher_reports(urls=good, replay_id="", player_id=-1))
    entries = teacher_reports.list_teacher_reports(root=archive)
    assert len(entries) == 1
    assert entries[0]["fetch_count"] == 2

    # 空输入 → 400
    assert asyncio.run(server.import_teacher_reports(urls="  ", replay_id="", player_id=-1)).status_code == 400


def test_import_endpoint_links_report_to_existing_replay(isolated_roots, monkeypatch):
    """给了存在的牌谱：写副本 + 登记引用；不存在的牌谱标记 orphan 但仍归档。"""
    archive, replays = isolated_roots
    raw = _raw_report(model_tag="4.1b", player_id=0, decisions=2)
    replay_id = "replay_ABC"
    (replays / replay_id).mkdir(parents=True)

    from replay import server

    monkeypatch.setattr("replay.external_reports.resolve_external_report_url", lambda url, kind: url)
    monkeypatch.setattr(server, "fetch_external_raw_reports", lambda links: {"mortal": raw})

    class _Storage:
        def __init__(self):
            self.seen: dict = {}

        def load_meta(self, _replay_id):
            return {}

        def update_meta(self, _replay_id, updates):
            self.seen = updates
            return True

    storage = _Storage()
    monkeypatch.setattr(server, "get_storage", lambda: storage)

    payload = json.loads(asyncio.run(server.import_teacher_reports(
        urls="https://mjai.ekyu.moe/report/c87abfa74d78a178.json", replay_id=replay_id, player_id=-1,
    )).body)

    assert payload["replay_exists"] is True and payload["orphan"] is False
    assert payload["results"][0]["replay_ids"] == [replay_id]
    assert (replays / replay_id / "external_reports").is_dir()
    assert storage.seen["external_review_links"]["mortal"].endswith("c87abfa74d78a178.json")

    # 不存在的牌谱 → orphan，但仍归档
    monkeypatch.setattr(server, "get_storage", lambda: None)
    orphan = json.loads(asyncio.run(server.import_teacher_reports(
        urls="https://mjai.ekyu.moe/report/30bfcaf5c9aff36c.json", replay_id="replay_missing", player_id=-1,
    )).body)
    assert orphan["replay_exists"] is False and orphan["orphan"] is True
    assert orphan["imported"] == 1


def test_only_site_downloads_are_valid_raw_reports():
    """只有站点下载（含 mjai_log）才是原始报告；本地运行时教师产物不是。

    artifacts/replay_model_reviews 下有两类同名前缀文件：
      - *__Mortal_4.1b__*    = external_teacher_report.v1（站点下载，有 mjai_log）
      - *__External_Mortal__* = runtime_teacher_report.v1（本地跑的外部模型，无 mjai_log）
    回填/归档必须只认前者，否则本地重建会冒充原始语料。
    """
    site = {
        "schema": "keqing1.external_teacher_report.v1",
        "source": "mortal",
        "replay_id": "replay_demo",
        "player_id": 2,
        "mjai_log": [{"type": "start_kyoku"}, {"type": "tsumo"}],
        "review": {"model_tag": "Mortal 4.1b", "kyokus": [{"entries": [{"details": []}]}]},
    }
    runtime = {
        "schema": "keqing1.runtime_teacher_report.v1",
        "bot_type": "ext_mortal",
        "player_id": 2,
        "replay_id": "replay_demo",
        "review": {"model_tag": "External Mortal", "kyokus": [{"entries": [{"step": 1}]}]},
    }

    assert teacher_reports.is_raw_site_report(site) is True
    assert teacher_reports.is_raw_site_report(runtime) is False

    # 逐条守卫生效：只有同时满足 schema+source+mjai_log 才算原始报告。
    # 单独去掉任一条件都必须判 False —— 否则本地产物可能混进语料。
    for missing in ("schema", "source", "mjai_log"):
        weakened = {k: v for k, v in site.items() if k != missing}
        assert teacher_reports.is_raw_site_report(weakened) is False, f"缺少 {missing} 时仍被判为原始报告"
    assert teacher_reports.is_raw_site_report({**site, "mjai_log": []}) is False  # 空 mjai_log
    assert teacher_reports.is_raw_site_report({**site, "source": "unknown"}) is False  # 未知来源
    # 本地运行时产物确实缺原始报告的关键字段
    assert "mjai_log" not in runtime and "source" not in runtime

    # 剥掉本地包装 → 还原成原始报告（model_tag 前缀去掉）
    stripped = teacher_reports.strip_local_wrapping(site)
    assert set(stripped) == {"mjai_log", "review", "player_id"}
    assert stripped["review"]["model_tag"] == "4.1b"
    assert stripped["mjai_log"] == site["mjai_log"]

    # 剥包装后重新判定：mjai_log 还在，但 schema/source 已摘除 → 不再是「站点产物」标记
    assert "schema" not in stripped and "source" not in stripped

    # NAGA 前缀同样被剥掉
    naga = {**site, "source": "naga", "review": {"model_tag": "NAGA ニシキ"}}
    assert teacher_reports.strip_local_wrapping(naga)["review"]["model_tag"] == "ニシキ"

    # 没有 review / 非 dict 时不炸
    assert teacher_reports.strip_local_wrapping({"mjai_log": []}) == {"mjai_log": []}
