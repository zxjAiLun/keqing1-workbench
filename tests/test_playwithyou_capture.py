"""Lock R9-3 Play-with-you ladder capture semantics (fixtures; no live Tenhou).

Collector gates C4-C9 and API gates C1-C3 / C10-C14.  C15 (players order ->
seat canonical replay) and C16 (cross-source dedup) are covered by
test_ladder_ingest.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for entry in (str(_ROOT), str(_ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from gateway.api import playwithyou as pwy  # noqa: E402
from gateway.playwithyou_capture import (  # noqa: E402
    CaptureBinding,
    PlayWithYouCaptureCollector,
    canonicalize_scores,
    capture_dir_for_session,
    extract_tenhou_log_seat,
    extract_tenhou_match_id,
)


def _binding(session_id: str = "abc123") -> CaptureBinding:
    return CaptureBinding(
        session_id=session_id,
        season_id="official-ladder-v1",
        human_account_id="nick@01",
        bot_account_ids=("70k@01", "70k@02", "70k@03"),
        mode="confirm",
    )


def _start_game(tw: int, *, match_id: str = "20260804gm-abc-xyz", tenhou_log_seat=None) -> dict:
    """真实桥语义：id 恒为 0（本地 MJAI 视角），全局座位来自 tw / tenhou_log_seat。"""
    message = {
        "type": "start_game",
        "id": 0,
        "names": ["NoName-1", "Nick", "NoName-2", "NoName-3"],
        "log": f"https://tenhou.net/3/?log={match_id}&tw={tw}",
    }
    if tenhou_log_seat is not None:
        message["tenhou_log_seat"] = tenhou_log_seat
    return message


def _local_scores(global_scores: list[int], tw: int) -> list[int]:
    """把全局分数转成 tw 座位 observer 看到的本地旋转视角。"""
    return [global_scores[(tw + i) % 4] for i in range(4)]


def _end_game(global_scores: list[int], tw: int) -> dict:
    return {"type": "end_game", "scores": _local_scores(global_scores, tw)}


def _collector(tmp_path: Path) -> PlayWithYouCaptureCollector:
    capture_dir = capture_dir_for_session(tmp_path / "data", "abc123")
    return PlayWithYouCaptureCollector(binding=_binding(), capture_dir=capture_dir)


def _read_pending(capture_dir: Path) -> list[dict]:
    pending = capture_dir / "pending"
    if not pending.is_dir():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(pending.glob("*.json"))
    ]


def _read_state(capture_dir: Path) -> dict:
    return json.loads((capture_dir / "state.json").read_text(encoding="utf-8"))


# --- Collector: identity mapping -------------------------------------------

def test_extract_tenhou_match_id_from_url() -> None:
    assert extract_tenhou_match_id("https://tenhou.net/3/?log=20260804gm-abc-xyz&tw=2") == (
        "tenhou:20260804gm-abc-xyz"
    )
    assert extract_tenhou_match_id(None) is None
    assert extract_tenhou_match_id("https://example.com/nolog") is None


def test_extract_tenhou_log_seat_from_url() -> None:
    assert extract_tenhou_log_seat("https://tenhou.net/3/?log=abc&tw=2") == 2
    assert extract_tenhou_log_seat("https://tenhou.net/3/?log=abc") is None
    assert extract_tenhou_log_seat(None) is None


def test_canonicalize_scores_roundtrip() -> None:
    global_scores = [42100, 28300, 18100, 11500]
    for tw in (0, 1, 2, 3):
        local = _local_scores(global_scores, tw)
        assert canonicalize_scores(local, tw) == tuple(global_scores)


def test_collector_derives_human_seat(tmp_path: Path) -> None:
    """C4+C26：observer 的全局 seat 来自 tw（id 全为 0），剩余 seat 推导为人类。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    assert collector._seat_of == {"70k@01": 2, "70k@02": 0, "70k@03": 3}  # noqa: SLF001
    assert collector._human_seat() == 1  # noqa: SLF001
    assert collector._account_for_seat(1) == "nick@01"  # noqa: SLF001


def test_tenhou_log_seat_consistent_with_tw_accepted(tmp_path: Path) -> None:
    """C44：explicit tenhou_log_seat 与 URL tw 一致 -> 接受。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=3, tenhou_log_seat=3))
    assert collector._seat_of == {"70k@01": 3}  # noqa: SLF001


def test_collector_rotated_perspectives_still_canonical(tmp_path: Path) -> None:
    """C5+C6+C27：observer 旋转视角规范化后一致；三份相同 end_game 只生成一个 pending。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    collector.observe("70k@02", _end_game(global_scores, tw=0))
    collector.observe("70k@03", _end_game(global_scores, tw=3))
    collector.finalize()

    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    players = pending[0]["match"]["players"]
    by_seat = {p["seat"]: p["account_id"] for p in players}
    assert by_seat[0] == "70k@02"
    assert by_seat[1] == "nick@01"
    assert by_seat[2] == "70k@01"
    assert by_seat[3] == "70k@03"
    final_by_seat = {p["seat"]: p["final_score"] for p in players}
    assert final_by_seat == {0: 42100, 1: 28300, 2: 18100, 3: 11500}


def test_collector_one_observer_enough_when_other_disconnects(tmp_path: Path) -> None:
    """C7：一个 observer 掉线，另一个完整 end_game 仍可形成 pending。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    collector.finalize()
    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    assert pending[0]["score_observers"] == ["70k@01"]


def test_collector_score_conflict_rejected(tmp_path: Path) -> None:
    """C8：规范化后分数冲突进入 conflict，不得确认。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game([42100, 28300, 18100, 11500], tw=2))
    collector.observe("70k@02", _end_game([40000, 30000, 20000, 10000], tw=0))
    collector.finalize()
    assert _read_state(collector.capture_dir)["state"] == "conflict"
    assert _read_pending(collector.capture_dir) == []


def test_collector_interrupted_game_no_pending(tmp_path: Path) -> None:
    """C9：没有 end_game 的中断局不计分。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.finalize()
    assert _read_state(collector.capture_dir)["state"] == "incomplete"
    assert _read_pending(collector.capture_dir) == []


# --- API: bindings & confirm ------------------------------------------------

@pytest.fixture()
def season_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    sources_root = tmp_path / "data" / "sources"
    season = {
        "schema": "keqing.ladder.season.v1",
        "season_id": "official-ladder-v1",
        "status": "running",
        "default": False,
        "report_dir": "seasons/official-ladder-v1/snapshots",
        "scoring": {
            "system": "tenhou_rank_progression",
            "version": "v1",
            "game_length": "hanchan",
        },
        "ingest": {"sources_root": str(sources_root)},
        "models": [
            {"model_id": "human", "accounts": [{"account_id": "nick@01", "display_name": "Nick"}]},
            {"model_id": "70k", "accounts": [
                {"account_id": "70k@01", "display_name": "70k-1"},
                {"account_id": "70k@02", "display_name": "70k-2"},
                {"account_id": "70k@03", "display_name": "70k-3"},
                {"account_id": "70k@04", "display_name": "70k-4"},
            ]},
        ],
    }
    (config_dir / "official-ladder-v1.json").write_text(json.dumps(season, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "data"))
    return {"root": tmp_path, "configs": config_dir, "season": season}


def _capture_request(**overrides) -> pwy.LadderCaptureRequest:
    defaults = {
        "enabled": True,
        "season_id": "official-ladder-v1",
        "human_account_id": "nick@01",
        "bot_account_ids": ["70k@01", "70k@02", "70k@03"],
        "mode": "confirm",
    }
    defaults.update(overrides)
    return pwy.LadderCaptureRequest(**defaults)


def test_capture_disabled_flow_unchanged() -> None:
    """C1：capture disabled 时原流程完全不变。"""
    assert pwy._validate_ladder_capture(
        pwy.LadderCaptureRequest(enabled=False), ["70k", "70k", "70k"]
    ) is None


def test_invalid_season_or_account_rejected_before_start(season_env) -> None:
    """C2：非法 season/account/checkpoint 在启动前拒绝。"""
    with pytest.raises(ValueError, match="season_id 不能为空"):
        pwy._validate_ladder_capture(_capture_request(season_id=""), ["70k", "70k", "70k"])
    with pytest.raises(ValueError, match="赛季不存在"):
        pwy._validate_ladder_capture(_capture_request(season_id="missing"), ["70k", "70k", "70k"])
    with pytest.raises(ValueError, match="未在赛季"):
        pwy._validate_ladder_capture(
            _capture_request(bot_account_ids=["ghost@01", "70k@02", "70k@03"]),
            ["70k", "70k", "70k"],
        )
    # bot 模型与 spec 不一致：绑定已注册的 70k 账号却选 ext_mortal spec
    with pytest.raises(ValueError, match="不能绑定 spec"):
        pwy._validate_ladder_capture(
            _capture_request(),
            ["ext_mortal", "ext_mortal", "ext_mortal"],
        )


def test_capture_dir_lives_outside_sources_root(season_env) -> None:
    """C12：capture 目录不在 sources_root 内，pending 文件不改变 ingest 指纹。"""
    from replay import publish_ladder_snapshot as publisher

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "m.json").write_text(json.dumps({"match_id": "m"}), encoding="utf-8")
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    assert not str(sources_root.resolve()).startswith(str(capture_dir.resolve()))
    assert publisher._ordered_source_stats(sources_root) == []  # pending 不影响指纹


def test_confirm_writes_single_jsonl_and_publishes(tmp_path: Path, season_env) -> None:
    """C10+C11：confirm 原子写一局一个 JSONL；重复 confirm 幂等。"""
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:20260804gm-abc-xyz",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:20260804gm-abc-xyz",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 1, "final_score": 42100},
                {"account_id": "70k@01", "seat": 2, "final_score": 28300},
                {"account_id": "70k@02", "seat": 0, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "https://tenhou.net/3/?log=20260804gm-abc-xyz&tw=2",
        "observer_accounts": ["70k@01", "70k@02", "70k@03"],
        "score_observers": ["70k@01", "70k@02"],
    }
    (pending_dir / "tenhou_20260804gm-abc-xyz.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    result = pwy_api._confirm_capture("abc123:tenhou:20260804gm-abc-xyz")
    assert result["state"] == "published"
    assert result["games"] == 1

    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    source_files = list((sources_root / "playwithyou").glob("*.jsonl"))
    assert len(source_files) == 1
    lines = source_files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # 一局一行
    row = json.loads(lines[0])
    assert row["match_id"] == "tenhou:20260804gm-abc-xyz"
    assert len(row["players"]) == 4
    assert "table_room" not in row

    # C11：重复 confirm 幂等，不产生第二局
    pwy_api._confirm_capture("abc123:tenhou:20260804gm-abc-xyz")
    assert len(list((sources_root / "playwithyou").glob("*.jsonl"))) == 1


def test_publish_failure_keeps_accepted_source_and_can_retry(
    tmp_path: Path, season_env, monkeypatch
) -> None:
    """C13：publish 失败保留 accepted source，可 retry-publish。"""
    import replay.publish_ladder_snapshot as publisher_mod
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:fail",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:fail",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "tenhou_fail.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _boom(**kwargs):
        raise RuntimeError("simulated publish failure")

    # 仅在此 context 内替换 publisher；退出后恢复真实实现且 env 保持不变
    with monkeypatch.context() as context:
        context.setattr(publisher_mod, "publish_snapshot", _boom)
        with pytest.raises(Exception, match="simulated publish failure"):
            pwy_api._confirm_capture("abc123:tenhou:fail")

    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    assert len(list((sources_root / "playwithyou").glob("*.jsonl"))) == 1  # accepted source 保留
    state_file = capture_dir / "pending" / "tenhou_fail.json"
    assert json.loads(state_file.read_text(encoding="utf-8"))["state"] == "accepted_publish_failed"

    # retry-publish：真实发布成功
    result = pwy_api._confirm_capture("abc123:tenhou:fail")
    assert result["state"] == "published"


def test_rediscover_pending_after_restart(tmp_path: Path, season_env) -> None:
    """C14：后端重启后从磁盘重发现 pending（不依赖 SESSIONS 内存）。"""
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    (pending_dir / "m.json").write_text(
        json.dumps(
            {
                "schema": CAPTURE_SCHEMA,
                "capture_id": "abc123:tenhou:m",
                "session_id": "abc123",
                "state": "pending_confirmation",
                "season_id": "official-ladder-v1",
                "match": {"match_id": "tenhou:m", "players": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captures = pwy._discover_captures()
    assert "abc123:tenhou:m" in [entry["capture_id"] for entry in captures]


# --- R9-3 review fixes: C17-C24 -------------------------------------------

def test_conflict_confirm_never_rewrites_existing_source(tmp_path: Path, season_env) -> None:
    """C17：conflict confirm 不得改写已有 source。"""
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])

    def _payload(match_id: str, nick_score: int) -> dict:
        return {
            "schema": CAPTURE_SCHEMA,
            "capture_id": f"abc123:{match_id}",
            "session_id": "abc123",
            "state": "pending_confirmation",
            "season_id": "official-ladder-v1",
            "match": {
                "match_id": match_id,
                "occurred_at": "2026-08-04T07:00:00Z",
                "game_length": "hanchan",
                "players": [
                    {"account_id": "nick@01", "seat": 0, "final_score": nick_score},
                    {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                    {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                    {"account_id": "70k@03", "seat": 3, "final_score": 11500},
                ],
            },
            "tenhou_log_url": "x",
            "observer_accounts": [],
            "score_observers": ["70k@01"],
        }

    # 先确认一局 A
    (pending_dir / "m_a.json").write_text(json.dumps(_payload("tenhou:mA", 42100), ensure_ascii=False), encoding="utf-8")
    pwy_api._confirm_capture("abc123:tenhou:mA")
    source_file = next((sources_root / "playwithyou").glob("*.jsonl"))
    original = source_file.read_text(encoding="utf-8")
    original_mtime = source_file.stat().st_mtime_ns

    # 同 match_id 内容 B 的 confirm：409，且 source 必须严格等于 A、mtime 不变
    (pending_dir / "m_b.json").write_text(
        json.dumps(_payload("tenhou:mA", 99999), ensure_ascii=False), encoding="utf-8"
    )
    # m_b 的 capture_id 唯一（= "abc123:tenhou:mA-b"），但 match_id 与已确认的 A 相同
    payload_b = json.loads((pending_dir / "m_b.json").read_text(encoding="utf-8"))
    payload_b["capture_id"] = "abc123:tenhou:mA-b"
    (pending_dir / "m_b.json").write_text(json.dumps(payload_b, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Exception, match="已存在不同内容"):
        pwy_api._confirm_capture("abc123:tenhou:mA-b")
    assert source_file.read_text(encoding="utf-8") == original
    assert source_file.stat().st_mtime_ns == original_mtime


def test_ignore_then_confirm_rejected(tmp_path: Path, season_env) -> None:
    """C18：ignore 后不可再次 confirm；sources 无新增文件。"""
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:m",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:m",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "m.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    pwy_api.ignore_ladder_capture("abc123:tenhou:m")
    # GET captures 返回 state=ignored
    captures = pwy._discover_captures()
    entry = next(c for c in captures if c["capture_id"] == "abc123:tenhou:m")
    assert entry["state"] == "ignored"
    # confirm 被拒绝（409），sources 无新增
    with pytest.raises(Exception, match="状态不允许确认"):
        pwy_api._confirm_capture("abc123:tenhou:m")
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    assert list((sources_root / "playwithyou").glob("*.jsonl")) == []


def test_observer_log_id_mismatch_conflict(tmp_path: Path) -> None:
    """C19：三个 observer 中一个 log ID 不同 -> conflict，不生成 pending。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=0, match_id="AAAA"))
    collector.observe("70k@02", _start_game(tw=2, match_id="AAAA"))
    collector.observe("70k@03", _start_game(tw=3, match_id="BBBB"))
    collector.finalize()
    assert _read_state(collector.capture_dir)["state"] == "conflict"
    assert _read_pending(collector.capture_dir) == []


def test_observer_seat_rebind_conflict(tmp_path: Path) -> None:
    """C20：observer 第二次上报不同全局 seat -> conflict。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=0))
    collector.observe("70k@01", _start_game(tw=2))
    collector.finalize()
    assert _read_state(collector.capture_dir)["state"] == "conflict"


def test_end_game_progressive_provisional_without_finalize(tmp_path: Path) -> None:
    """C21+C33：第一份 end_game 后不 finalize -> provisional_result 已落盘（不可确认）。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    # 不调用 finalize：provisional pending 已渐进落盘
    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    assert pending[0]["state"] == "provisional_result"  # C33：未封口，不可确认
    # 重新创建 collector（模拟重启恢复）也能看到同一 provisional
    recovered = _collector(tmp_path)
    assert len(_read_pending(recovered.capture_dir)) == 1


def test_incomplete_and_conflict_visible_after_restart(tmp_path: Path) -> None:
    """C22+C29：incomplete/conflict 后端重启后仍可见（errors/ 目录，无需 finalize）。"""
    import os

    os.environ["KEQING_LADDER_DATA_ROOT"] = str(tmp_path / "data")

    # incomplete（无 end_game）
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2, match_id="INCOMPLETE"))
    collector.finalize()
    # conflict 且不调用 finalize（C29）：_set_conflict 已立即写 errors/
    collector2 = _collector(tmp_path)
    collector2.observe("70k@01", _start_game(tw=0))
    collector2.observe("70k@01", _start_game(tw=2))

    states = {entry["state"] for entry in pwy._discover_captures()}
    assert "incomplete" in states
    assert "conflict" in states


def test_human_account_must_be_human_model(season_env) -> None:
    """C23：human_account_id 非 human 模型 -> 启动前拒绝。"""
    with pytest.raises(ValueError, match="必须属于 model_id=human"):
        pwy._validate_ladder_capture(
            _capture_request(human_account_id="70k@04"),
            ["70k", "70k", "70k"],
        )


def test_relative_sources_root_resolution_matches_publisher(
    tmp_path: Path, season_env, monkeypatch
) -> None:
    """C24：API 与 publisher 对相对 sources_root 解析一致（无 data root 时基于仓库根）。"""
    from replay import ladder_ingest

    monkeypatch.delenv("KEQING_LADDER_DATA_ROOT", raising=False)
    season = dict(season_env["season"])
    season["ingest"] = {"sources_root": "relative/sources"}
    resolved = ladder_ingest.resolve_ingest_sources_root(Path(season_env["root"]), season)
    assert resolved == (Path(season_env["root"]) / "relative" / "sources").resolve()
    # 设置 data root 后相对它解析
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "dataroot"))
    resolved2 = ladder_ingest.resolve_ingest_sources_root(Path(season_env["root"]), season)
    assert resolved2 == (tmp_path / "dataroot" / "relative" / "sources").resolve()


# --- R9-3 review fixes round 2: C28, C30-C32 ------------------------------

def test_concurrent_conflicting_scores_must_conflict(tmp_path: Path) -> None:
    """C28：两个线程通过 Barrier 同时提交不同分数，最终必定 conflict、无 pending。"""
    import threading

    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def report(account: str, scores: list[int], tw: int) -> None:
        try:
            barrier.wait(timeout=5)
            collector.observe(account, _end_game(scores, tw))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    a = threading.Thread(target=report, args=("70k@01", global_scores, 2))
    b = threading.Thread(target=report, args=("70k@02", [40000, 30000, 20000, 10000], 0))
    a.start()
    b.start()
    a.join(timeout=10)
    b.join(timeout=10)
    assert not errors
    assert _read_state(collector.capture_dir)["state"] == "conflict"
    assert _read_pending(collector.capture_dir) == []


def test_conflict_visible_without_finalize(tmp_path: Path) -> None:
    """C29：冲突后强制退出、不调用 finalize，重启仍可见（errors/ 已即时写出）。"""
    import os

    os.environ["KEQING_LADDER_DATA_ROOT"] = str(tmp_path / "data")
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=0))
    collector.observe("70k@01", _start_game(tw=2))
    # 不调用 finalize
    states = {entry["state"] for entry in pwy._discover_captures()}
    assert "conflict" in states


def test_accepted_publish_failed_cannot_be_ignored(tmp_path: Path, season_env, monkeypatch) -> None:
    """C30：accepted_publish_failed 不允许 ignore（source 已写入，只能 retry）。"""
    import replay.publish_ladder_snapshot as publisher_mod
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:fail2",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:fail2",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "fail2.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _boom(**kwargs):
        raise RuntimeError("boom")

    with monkeypatch.context() as context:
        context.setattr(publisher_mod, "publish_snapshot", _boom)
        with pytest.raises(Exception, match="boom"):
            pwy_api._confirm_capture("abc123:tenhou:fail2")
    with pytest.raises(Exception, match="不能忽略"):
        pwy_api.ignore_ladder_capture("abc123:tenhou:fail2")
    # source 仍在（已 accepted）
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    assert len(list((sources_root / "playwithyou").glob("*.jsonl"))) == 1


def test_concurrent_confirms_do_not_overwrite(tmp_path: Path, season_env) -> None:
    """C31：并发不同内容 confirm 不得互相覆盖（source 写锁）。"""
    import threading

    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])

    def _payload(capture_suffix: str, nick_score: int) -> dict:
        return {
            "schema": CAPTURE_SCHEMA,
            "capture_id": f"abc123:tenhou:cc-{capture_suffix}",
            "session_id": "abc123",
            "state": "pending_confirmation",
            "season_id": "official-ladder-v1",
            "match": {
                "match_id": "tenhou:cc",
                "occurred_at": "2026-08-04T07:00:00Z",
                "game_length": "hanchan",
                "players": [
                    {"account_id": "nick@01", "seat": 0, "final_score": nick_score},
                    {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                    {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                    {"account_id": "70k@03", "seat": 3, "final_score": 11500},
                ],
            },
            "tenhou_log_url": "x",
            "observer_accounts": [],
            "score_observers": ["70k@01"],
        }

    (pending_dir / "cc_a.json").write_text(json.dumps(_payload("a", 42100), ensure_ascii=False), encoding="utf-8")
    (pending_dir / "cc_b.json").write_text(json.dumps(_payload("b", 99999), ensure_ascii=False), encoding="utf-8")

    barrier = threading.Barrier(2)
    outcomes: list[BaseException] = []

    def confirm(capture_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            pwy_api._confirm_capture(capture_id)
        except BaseException as exc:  # noqa: BLE001
            outcomes.append(exc)

    ta = threading.Thread(target=confirm, args=("abc123:tenhou:cc-a",))
    tb = threading.Thread(target=confirm, args=("abc123:tenhou:cc-b",))
    ta.start()
    tb.start()
    ta.join(timeout=15)
    tb.join(timeout=15)
    # 至少一个成功、另一个要么幂等要么 conflict；但 source 文件必须是单一确定内容
    source_files = list((sources_root / "playwithyou").glob("*.jsonl"))
    assert len(source_files) == 1
    row = json.loads(source_files[0].read_text(encoding="utf-8").splitlines()[0])
    nick = next(p for p in row["players"] if p["account_id"] == "nick@01")
    assert nick["final_score"] in (42100, 99999)  # 最终内容确定，未被混写


def test_retry_clears_publish_error(tmp_path: Path, season_env, monkeypatch) -> None:
    """C32：retry 成功后 publish_error 被清除。"""
    import replay.publish_ladder_snapshot as publisher_mod
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:retry",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:retry",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "retry.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _boom(**kwargs):
        raise RuntimeError("boom")

    with monkeypatch.context() as context:
        context.setattr(publisher_mod, "publish_snapshot", _boom)
        with pytest.raises(Exception, match="boom"):
            pwy_api._confirm_capture("abc123:tenhou:retry")
    state_file = capture_dir / "pending" / "retry.json"
    assert json.loads(state_file.read_text(encoding="utf-8"))["state"] == "accepted_publish_failed"
    assert json.loads(state_file.read_text(encoding="utf-8"))["publish_error"]

    # retry 成功：publish_error 必须被清除
    pwy_api._confirm_capture("abc123:tenhou:retry")
    published = json.loads(state_file.read_text(encoding="utf-8"))
    assert published["state"] == "published"
    assert "publish_error" not in published


# --- R9-3 review fixes round 3: seal model + source admission (C33-C45) ----

def test_first_end_game_only_provisional(tmp_path: Path) -> None:
    """C33：第一份 end_game 只生成 provisional，不可 confirm。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    assert pending[0]["state"] == "provisional_result"
    assert pending[0]["match"]["occurred_at"]  # occurred_at 已冻结


def test_all_observers_seal_pending(tmp_path: Path) -> None:
    """C34：三份一致结果后才 seal 为 pending_confirmation。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    assert _read_pending(collector.capture_dir)[0]["state"] == "provisional_result"
    collector.observe("70k@02", _end_game(global_scores, tw=0))
    collector.observe("70k@03", _end_game(global_scores, tw=3))
    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    assert pending[0]["state"] == "pending_confirmation"


def test_single_observer_sealed_at_finalize(tmp_path: Path) -> None:
    """C35：单 observer 场景在 finalize 时 seal。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    collector.finalize()
    pending = _read_pending(collector.capture_dir)
    assert len(pending) == 1
    assert pending[0]["state"] == "pending_confirmation"


def test_confirm_then_finalize_does_not_overwrite(tmp_path: Path, season_env) -> None:
    """C36：confirm 后 observe/finalize，published 不被覆盖。"""
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA, capture_dir_for_session

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:sealed",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:sealed",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "tenhou_sealed.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pwy_api._confirm_capture("abc123:tenhou:sealed")
    assert json.loads((pending_dir / "tenhou_sealed.json").read_text(encoding="utf-8"))["state"] == "published"

    # launcher 继续 observe/finalize：_already_terminal 阻止覆盖回 pending
    collector = PlayWithYouCaptureCollector(
        binding=_binding(),
        capture_dir=capture_dir,
    )
    global_scores = [42100, 28300, 18100, 11500]
    collector.observe("70k@01", _start_game(tw=1, match_id="sealed"))
    collector.observe("70k@02", _start_game(tw=0, match_id="sealed"))
    collector.observe("70k@03", _start_game(tw=3, match_id="sealed"))
    collector.observe("70k@01", _end_game(global_scores, tw=1))
    collector.finalize()
    state = json.loads((pending_dir / "tenhou_sealed.json").read_text(encoding="utf-8"))
    assert state["state"] == "published"  # 不被覆盖回 pending


def test_ignore_then_finalize_does_not_recreate(tmp_path: Path, season_env) -> None:
    """C37：ignore 后再 observe/finalize，不重新生成 pending。"""
    from gateway.api import playwithyou as pwy_api
    from gateway.playwithyou_capture import CAPTURE_SCHEMA, capture_dir_for_session

    capture_dir = capture_dir_for_session(Path(season_env["root"]) / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:ignored2",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:ignored2",
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "tenhou_ignored2.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pwy_api.ignore_ladder_capture("abc123:tenhou:ignored2")
    assert list(pending_dir.glob("*.json")) == []

    collector = PlayWithYouCaptureCollector(binding=_binding(), capture_dir=capture_dir)
    global_scores = [42100, 28300, 18100, 11500]
    collector.observe("70k@01", _start_game(tw=1, match_id="ignored2"))
    collector.observe("70k@02", _start_game(tw=0, match_id="ignored2"))
    collector.observe("70k@03", _start_game(tw=3, match_id="ignored2"))
    collector.observe("70k@01", _end_game(global_scores, tw=1))
    collector.finalize()
    assert list(pending_dir.glob("*.json")) == []  # 不复活


def test_occurred_at_frozen_after_first_generation(tmp_path: Path) -> None:
    """C38：occurred_at 在首次生成后永久不变。"""
    global_scores = [42100, 28300, 18100, 11500]
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=2))
    collector.observe("70k@02", _start_game(tw=0))
    collector.observe("70k@03", _start_game(tw=3))
    collector.observe("70k@01", _end_game(global_scores, tw=2))
    first = _read_pending(collector.capture_dir)[0]["match"]["occurred_at"]
    collector.observe("70k@02", _end_game(global_scores, tw=0))
    collector.observe("70k@03", _end_game(global_scores, tw=3))
    collector.finalize()
    second = _read_pending(collector.capture_dir)[0]["match"]["occurred_at"]
    assert second == first


def test_explicit_seat_conflicts_with_url_tw(tmp_path: Path) -> None:
    """C45：explicit tenhou_log_seat 与 URL tw 不同 -> conflict。"""
    collector = _collector(tmp_path)
    collector.observe("70k@01", _start_game(tw=0, tenhou_log_seat=3))
    collector.finalize()
    assert _read_state(collector.capture_dir)["state"] == "conflict"
    assert _read_pending(collector.capture_dir) == []


# --- Source admission validation (C39-C43) ---------------------------------

def _write_confirm_payload(tmp_path: Path, *, match_id: str = "tenhou:admit", players: list | None = None) -> dict:
    """写一个 hand-crafted pending capture 并返回 capture_id。"""
    from gateway.playwithyou_capture import CAPTURE_SCHEMA, capture_dir_for_session

    if players is None:
        players = [
            {"account_id": "nick@01", "seat": 0, "final_score": 42100},
            {"account_id": "70k@01", "seat": 1, "final_score": 28300},
            {"account_id": "70k@02", "seat": 2, "final_score": 18100},
            {"account_id": "70k@03", "seat": 3, "final_score": 11500},
        ]
    capture_dir = capture_dir_for_session(tmp_path / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": f"abc123:{match_id}",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": match_id,
            "occurred_at": "2026-08-04T07:00:00Z",
            "game_length": "hanchan",
            "players": players,
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / f"{match_id.replace(':', '_')}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return f"abc123:{match_id}"


def _assert_rejected_without_source(tmp_path: Path, season_env, capture_id: str) -> None:
    from gateway.api import playwithyou as pwy_api

    with pytest.raises(Exception, match="校验失败"):
        pwy_api._confirm_capture(capture_id)
    sources_root = Path(season_env["season"]["ingest"]["sources_root"])
    assert list((sources_root / "playwithyou").glob("*.jsonl")) == []


def test_duplicate_account_rejected_no_source(tmp_path: Path, season_env) -> None:
    """C39：重复 account -> 拒绝，source 不存在。"""
    players = [
        {"account_id": "nick@01", "seat": 0, "final_score": 42100},
        {"account_id": "nick@01", "seat": 1, "final_score": 28300},
        {"account_id": "70k@02", "seat": 2, "final_score": 18100},
        {"account_id": "70k@03", "seat": 3, "final_score": 11500},
    ]
    capture_id = _write_confirm_payload(tmp_path, match_id="tenhou:dup", players=players)
    _assert_rejected_without_source(tmp_path, season_env, capture_id)


def test_seat_missing_rejected_no_source(tmp_path: Path, season_env) -> None:
    """C40：seat 缺失或重复 -> 拒绝，source 不存在。"""
    players = [
        {"account_id": "nick@01", "seat": 0, "final_score": 42100},
        {"account_id": "70k@01", "seat": 0, "final_score": 28300},
        {"account_id": "70k@02", "seat": 2, "final_score": 18100},
        {"account_id": "70k@03", "seat": 3, "final_score": 11500},
    ]
    capture_id = _write_confirm_payload(tmp_path, match_id="tenhou:seat", players=players)
    _assert_rejected_without_source(tmp_path, season_env, capture_id)


def test_unregistered_account_rejected_no_source(tmp_path: Path, season_env) -> None:
    """C41：未注册 account -> 拒绝，source 不存在。"""
    players = [
        {"account_id": "nick@01", "seat": 0, "final_score": 42100},
        {"account_id": "ghost@01", "seat": 1, "final_score": 28300},
        {"account_id": "70k@02", "seat": 2, "final_score": 18100},
        {"account_id": "70k@03", "seat": 3, "final_score": 11500},
    ]
    capture_id = _write_confirm_payload(tmp_path, match_id="tenhou:ghost", players=players)
    _assert_rejected_without_source(tmp_path, season_env, capture_id)


def test_bad_match_metadata_rejected_no_source(tmp_path: Path, season_env) -> None:
    """C42：occurred_at / match_id 非法 -> 拒绝，source 不存在。"""
    from gateway.playwithyou_capture import CAPTURE_SCHEMA, capture_dir_for_session

    capture_dir = capture_dir_for_session(tmp_path / "data", "abc123")
    pending_dir = capture_dir / "pending"
    pending_dir.mkdir(parents=True)
    payload = {
        "schema": CAPTURE_SCHEMA,
        "capture_id": "abc123:tenhou:badmeta",
        "session_id": "abc123",
        "state": "pending_confirmation",
        "season_id": "official-ladder-v1",
        "match": {
            "match_id": "tenhou:badmeta",
            "occurred_at": "not-a-date",
            "game_length": "hanchan",
            "players": [
                {"account_id": "nick@01", "seat": 0, "final_score": 42100},
                {"account_id": "70k@01", "seat": 1, "final_score": 28300},
                {"account_id": "70k@02", "seat": 2, "final_score": 18100},
                {"account_id": "70k@03", "seat": 3, "final_score": 11500},
            ],
        },
        "tenhou_log_url": "x",
        "observer_accounts": [],
        "score_observers": ["70k@01"],
    }
    (pending_dir / "tenhou_badmeta.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    _assert_rejected_without_source(tmp_path, season_env, "abc123:tenhou:badmeta")


# ---------------------------------------------------------------------------
# R10-E：通用四人阵容（roster）宽松捕获
# ---------------------------------------------------------------------------

def _roster_binding(session_id: str = "roster1") -> CaptureBinding:
    return CaptureBinding(
        session_id=session_id,
        season_id="",
        human_account_id="",
        bot_account_ids=(),
        mode="roster",
        roster=(
            {"account_id": "nick@01", "controller_type": "human_ui", "launcher_slot": None, "expected_raw_name": "Nick"},
            {"account_id": "70k@01", "controller_type": "local_model", "launcher_slot": 0, "expected_raw_name": "NoName-1"},
            {"account_id": "70k@02", "controller_type": "local_model", "launcher_slot": 1, "expected_raw_name": "NoName-2"},
            {"account_id": "mortal@01", "controller_type": "external_agent", "launcher_slot": None, "expected_raw_name": "Mortal-4.1b"},
        ),
    )


def test_roster_mode_start_game_alone_is_log_captured(tmp_path):
    """R10-E（P1-4）：开局仅捕获 log → log_captured，绝不提前出现可导入的 awaiting_import。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster1")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding(), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1, match_id="20260804gm-abc-xyz"))
    assert collector._state == "log_captured"
    assert list((capture_dir / "pending").glob("*.json")) == [], "未结束不得生成 pending import"


def test_roster_mode_awaiting_import_after_end_game(tmp_path):
    """R10-E：任一合法 end_game 后才进入 awaiting_import（不要求 3 observer 共识）。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster1")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding(), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1, match_id="20260804gm-abc-xyz"))
    collector.observe("70k@01", _end_game([25000, 25000, 25000, 25000], 1))
    assert collector._state == "awaiting_import"
    state = json.loads((capture_dir / "state.json").read_text(encoding="utf-8"))
    assert state["match_id"] == "tenhou:20260804gm-abc-xyz"
    assert len(state["roster"]) == 4
    pending = json.loads(next((capture_dir / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert pending["state"] == "awaiting_import"
    assert pending["tenhou_log_url"].startswith("https://tenhou.net")
    assert len(pending["roster"]) == 4


def test_roster_mode_finalize_without_end_game_incomplete(tmp_path):
    """R10-E（P1-4）：无 end_game 时 finalize → incomplete 且不生成 pending import。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster1")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding(), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1, match_id="20260804gm-abc-xyz"))
    assert collector._state == "log_captured"
    collector.finalize()
    assert collector._state == "incomplete"
    assert list((capture_dir / "pending").glob("*.json")) == []


def test_roster_mode_score_conflict_is_warning_not_blocker(tmp_path):
    """R10-E（P1-4）：observer 分数不一致 → evidence_warning，仍可导入。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster1")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding(), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1, match_id="20260804gm-abc-xyz"))
    collector.observe("70k@01", _end_game([25000, 25000, 25000, 25000], 1))
    collector.observe("70k@02", _start_game(2, match_id="20260804gm-abc-xyz"))
    collector.observe("70k@02", _end_game([26000, 24000, 25000, 25000], 2))
    assert collector._state == "awaiting_import"
    assert collector._evidence_warning is not None
    state = json.loads((capture_dir / "state.json").read_text(encoding="utf-8"))
    assert "evidence_warning" in state


def test_roster_mode_log_id_conflict_blocks(tmp_path):
    """R10-E（P1-4）：observer log_id 不一致 → conflict，无 pending。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster1")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding(), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1, match_id="20260804gm-aaa-aaa"))
    collector.observe("70k@02", _start_game(2, match_id="20260804gm-bbb-bbb"))
    assert collector._state == "conflict"
    assert list((capture_dir / "pending").glob("*.json")) == []


def test_roster_mode_does_not_wait_for_score_consensus(tmp_path):
    """roster 模式只有 1 个 observer 的分数也不阻塞（不再等三份共识才封口）。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster2")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding("roster2"), capture_dir=capture_dir)
    collector.observe("70k@01", _start_game(1))
    collector.observe("70k@01", _end_game([25000, 25000, 25000, 25000], 1))
    assert collector._state == "awaiting_import"
    # 不会生成 players 封口
    pending = json.loads(next((capture_dir / "pending").glob("*.json")).read_text(encoding="utf-8"))
    assert pending["state"] == "awaiting_import"
    assert pending["match"]["players"] == []


def test_roster_mode_no_log_no_awaiting(tmp_path):
    """roster 模式但尚未捕获到 log → 停留在原状态，不误判 awaiting_import。"""
    capture_dir = capture_dir_for_session(tmp_path / "data", "roster3")
    collector = PlayWithYouCaptureCollector(binding=_roster_binding("roster3"), capture_dir=capture_dir)
    collector.observe("70k@01", {"type": "start_game", "id": 0})
    assert collector._state != "awaiting_import"
