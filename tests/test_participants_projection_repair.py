# -*- coding: utf-8 -*-
"""R10-F Repair：fingerprint、generation CAS、season move、exclusive、自动消费。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from participants import ledger, projection, registry
from participants.schemas import AccountCreate, MatchCreate, MatchRevise, MatchSeat, ModelIdentityCreate
from replay import ladder_ingest

SEASON = "official-ladder-v1"


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    # 正式资格 gate 需要有效赛季配置（测试账号全部为成员）
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = _season_config(tmp_path)
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    return root


def _accounts():
    registry.create_model_identity(
        ModelIdentityCreate(
            model_identity_id="70k",
            label="70k",
            kind="local_model",
            artifact_path="artifacts/mortal_training/checkpoints/mortal_default_70k_promoted_candidate.pth",
            stage="promoted",
        )
    )
    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("70k@03", "managed_bot"),
    ):
        registry.create_account(
            AccountCreate(
                account_id=account_id,
                display_name=account_id,
                account_type=account_type,
                model_identity_id="70k" if account_type != "human" else None,
            )
        )


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


def _season_config(tmp_path, *, exclusive=True) -> dict:
    return {
        "schema": "keqing.ladder.season.v1",
        "season_id": SEASON,
        "report_dir": str(tmp_path / "reports" / SEASON),
        "status": "running",
        "scoring": {
            "system": "tenhou_rank_progression",
            "version": "test-v1",
        },
        "ingest": {
            "sources_root": str(tmp_path / "sources"),
            "participants": {"enabled": True, "exclusive": exclusive},
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


def _write_season_config(tmp_path, monkeypatch, *, exclusive=True) -> dict:
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    season_cfg = _season_config(tmp_path, exclusive=exclusive)
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    return season_cfg


def _mock_publish(monkeypatch, tmp_path):
    from replay import publish_ladder_snapshot

    def _publish(**kw):
        return {"snapshot_dir": str(tmp_path / "snap"), "games": 1}

    monkeypatch.setattr(publish_ladder_snapshot, "publish_snapshot", _publish)


# ---------------------------------------------------------------------------
# P1-1：participants ledger 纳入 publisher fingerprint
# ---------------------------------------------------------------------------

def test_participants_projection_fingerprint_changes(participants_root):
    _accounts()
    match = ledger.create_match(_match_create(), registry)
    fp1 = ladder_ingest.participants_projection_fingerprint(SEASON)
    # 新增一场 → 指纹变化
    ledger.create_match(_match_create(occurred_at="2026-08-05T10:00:00+08:00"), registry)
    fp2 = ladder_ingest.participants_projection_fingerprint(SEASON)
    assert fp1 != fp2
    # 只改 note（不影响天梯）→ 指纹不变
    ledger.revise_match(match.match_id, MatchRevise(note="只是备注"), registry)
    assert ladder_ingest.participants_projection_fingerprint(SEASON) == fp2


def test_publish_uses_ledger_extra_fingerprint(participants_root, monkeypatch, tmp_path):
    from replay import publish_ladder_snapshot as pls

    _accounts()
    ledger.create_match(_match_create(), registry)

    season = _season_config(tmp_path, exclusive=True)
    extra = pls._participants_extra_fingerprint(season)
    assert extra == ladder_ingest.participants_projection_fingerprint(SEASON)
    # 未启用 → None（普通 ingest season 不受影响）
    season_no = dict(season)
    season_no["ingest"] = {"sources_root": str(tmp_path / "sources")}
    assert pls._participants_extra_fingerprint(season_no) is None
    # compute_source_fingerprint 合并 extra 后变化
    fp_a = pls.compute_source_fingerprint([], ingest_root=tmp_path / "sources", extra_fingerprint="aaa")
    fp_b = pls.compute_source_fingerprint([], ingest_root=tmp_path / "sources", extra_fingerprint="bbb")
    assert fp_a != fp_b


# ---------------------------------------------------------------------------
# P1-2：generation CAS（lost-update 防护）
# ---------------------------------------------------------------------------

def test_project_season_lost_update_keeps_dirty(participants_root, monkeypatch, tmp_path):
    from replay import publish_ladder_snapshot

    _accounts()
    ledger.create_match(_match_create(), registry)
    _write_season_config(tmp_path, monkeypatch)

    # 发布期间发生新的 ledger 写入（generation 变）
    def _publish_with_concurrent_write(**kw):
        ledger.create_match(_match_create(occurred_at="2026-08-06T10:00:00+08:00"), registry)
        return {"snapshot_dir": str(tmp_path / "snap"), "games": 1}

    monkeypatch.setattr(publish_ladder_snapshot, "publish_snapshot", _publish_with_concurrent_write)
    result = projection.project_season(SEASON)
    assert result["state"] == "needs_rebuild"
    # dirty 保留 + 新比赛仍 pending
    assert ledger.ladder_dirty_path(SEASON).exists()
    matches = ledger.list_matches(status="active").matches
    assert all(m.ladder_projection_state == "pending" for m in matches if m.season_id == SEASON)
    # 第二次投影（无并发写入）→ ready + 清 dirty
    _mock_publish(monkeypatch, tmp_path)
    result2 = projection.project_season(SEASON)
    assert result2["state"] == "ready"
    assert not ledger.ladder_dirty_path(SEASON).exists()
    assert all(m.ladder_projection_state == "ready" for m in ledger.list_matches(status="active").matches if m.season_id == SEASON)


# ---------------------------------------------------------------------------
# P1-3：season move → 双 dirty / 显式清空
# ---------------------------------------------------------------------------

def test_revise_season_move_dirty_both(participants_root, monkeypatch, tmp_path):
    _accounts()
    # 正式资格 gate 要求 season 存在：为 season-a / season-b 建配置（含成员）
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    for sid in ("season-a", "season-b"):
        cfg = _season_config(tmp_path)
        cfg["season_id"] = sid
        (configs / f"{sid}.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))

    match = ledger.create_match(_match_create(season_id="season-a"), registry)
    ledger.clear_ladder_dirty("season-a")
    assert not ledger.ladder_dirty_path("season-b").exists()

    # A → B：A、B 都 dirty
    ledger.revise_match(match.match_id, MatchRevise(season_id="season-b", rating_eligible=True), registry)
    assert ledger.ladder_dirty_path("season-a").exists()
    assert ledger.ladder_dirty_path("season-b").exists()
    assert ledger.get_match(match.match_id).season_id == "season-b"

    # B → null（显式）：B dirty，season 清空
    ledger.clear_ladder_dirty("season-b")
    ledger.revise_match(match.match_id, MatchRevise(season_id=None, rating_eligible=False), registry)
    assert ledger.ladder_dirty_path("season-b").exists()
    updated = ledger.get_match(match.match_id)
    assert updated.season_id is None
    assert updated.ladder_projection_state == "not_applicable"

    # 省略字段（不传 season_id）→ 不清空、不标 dirty
    ledger.clear_ladder_dirty("season-b")
    ledger.revise_match(match.match_id, MatchRevise(note="仅备注"), registry)
    assert ledger.get_match(match.match_id).season_id is None
    assert not ledger.ladder_dirty_path("season-b").exists()


# ---------------------------------------------------------------------------
# P1-4：participants exclusive 排他
# ---------------------------------------------------------------------------

def test_exclusive_mode_skips_legacy_sources(participants_root, tmp_path):
    _accounts()
    sources = tmp_path / "sources"
    manual_dir = sources / "manual_tenhou"
    manual_dir.mkdir(parents=True)
    # 无效 legacy 文件——若被读取会抛 LadderIngestError
    (manual_dir / "bad.jsonl").write_text("{invalid json\n", encoding="utf-8")
    ledger.create_match(_match_create(), registry)

    season = _season_config(tmp_path, exclusive=True)
    output = tmp_path / "out"
    # exclusive：legacy source 不被读取 → 不因坏文件抛错
    result = ladder_ingest.build_ingest_report(season=season, sources_root=sources, output_dir=output)
    assert result is not None


# ---------------------------------------------------------------------------
# P1-6：自动 dirty consumer
# ---------------------------------------------------------------------------

def test_run_dirty_projection_auto_consumes(participants_root, monkeypatch, tmp_path):
    _accounts()
    ledger.create_match(_match_create(), registry)
    _write_season_config(tmp_path, monkeypatch)
    _mock_publish(monkeypatch, tmp_path)
    assert ledger.ladder_dirty_path(SEASON).exists()

    results = projection.run_dirty_projection()
    assert results and results[0]["state"] == "ready"
    assert not ledger.ladder_dirty_path(SEASON).exists()
    assert all(m.ladder_projection_state == "ready" for m in ledger.list_matches(status="active").matches if m.season_id == SEASON)


def test_projection_gate_requires_participants_enabled(participants_root, monkeypatch, tmp_path):
    _accounts()
    ledger.create_match(_match_create(), registry)
    # 普通 ingest season（无 participants.enabled）
    season_cfg = _season_config(tmp_path, exclusive=True)
    season_cfg["ingest"] = {"sources_root": str(tmp_path / "sources")}
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    _mock_publish(monkeypatch, tmp_path)

    result = projection.project_season(SEASON)
    assert result["state"] == "error"
    assert "未启用 participants 投影" in result.get("reason", "")
    # dirty 保留、不误清
    assert ledger.ladder_dirty_path(SEASON).exists()


# ---------------------------------------------------------------------------
# R10-F Repair 2：begin barrier / single-flight / failure CAS / wake
# ---------------------------------------------------------------------------

def test_begin_barrier_blocks_mid_mutation(participants_root, monkeypatch):
    """P1-1：dirty 已写但 Match 事务未完成时，投影 begin barrier 必须阻塞到提交完成。"""
    import threading
    import time

    _accounts()
    started = threading.Event()
    release = threading.Event()
    original = ledger.mark_ladder_dirty

    def _slow_mark(season_id):
        original(season_id)
        started.set()
        release.wait(5)  # 模拟 dirty 后、pending/match 提交前被挂起

    monkeypatch.setattr(ledger, "mark_ladder_dirty", _slow_mark)

    def _mutation():
        ledger.create_match(_match_create(), registry)

    t = threading.Thread(target=_mutation)
    t.start()
    assert started.wait(5), "mutation 未开始"

    begin_gen: dict = {}

    def _begin():
        begin_gen["gen"] = ledger.begin_ladder_projection(SEASON)

    b = threading.Thread(target=_begin)
    b.start()
    time.sleep(0.3)
    assert "gen" not in begin_gen, "begin barrier 未阻塞在未提交事务上"
    release.set()
    t.join()
    b.join()
    assert "gen" in begin_gen
    assert begin_gen["gen"] == ledger.read_ladder_generation(SEASON)
    # 投影此时读到的是已提交的 11 局
    assert len(ledger.list_matches(status="active").matches) == 1


def test_single_flight_prevents_concurrent_publishers(participants_root, monkeypatch, tmp_path):
    """P1-2：worker + manual 同时 project 同一 generation → 只有一个进入 publisher。"""
    import threading

    from replay import publish_ladder_snapshot

    _accounts()
    ledger.create_match(_match_create(), registry)
    _write_season_config(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    publish_calls = {"n": 0}

    def _slow_publish(**kw):
        publish_calls["n"] += 1
        entered.set()
        release.wait(5)
        return {"snapshot_dir": str(tmp_path / "snap"), "games": 1}

    monkeypatch.setattr(publish_ladder_snapshot, "publish_snapshot", _slow_publish)

    first: dict = {}

    def _run_first():
        first["r"] = projection.project_season(SEASON)

    t = threading.Thread(target=_run_first)
    t.start()
    assert entered.wait(5), "第一个 publisher 未进入"

    # 第二个调用不进入 publisher → already_running
    r2 = projection.project_season(SEASON)
    assert r2["state"] == "already_running"
    assert publish_calls["n"] == 1

    release.set()
    t.join()
    assert first["r"]["state"] == "ready"
    assert not ledger.ladder_dirty_path(SEASON).exists()
    assert all(m.ladder_projection_state == "ready" for m in ledger.list_matches(status="active").matches if m.season_id == SEASON)


def test_stale_failure_does_not_overwrite_ready(participants_root, monkeypatch, tmp_path):
    """P1-2：过期失败回写（旧 generation）不能覆盖已成功的 ready。"""
    _accounts()
    ledger.create_match(_match_create(), registry)
    _write_season_config(tmp_path, monkeypatch)
    _mock_publish(monkeypatch, tmp_path)

    result = projection.project_season(SEASON)
    assert result["state"] == "ready"
    assert not ledger.ladder_dirty_path(SEASON).exists()

    # 用旧 generation 回写 error → CAS 拒绝，状态保持 ready
    assert ledger.mark_season_projection_error(SEASON, "stale-generation") is False
    matches = ledger.list_matches(status="active").matches
    assert all(m.ladder_projection_state == "ready" for m in matches if m.season_id == SEASON)


def test_gate_requires_exclusive(participants_root, monkeypatch, tmp_path):
    """配置建议：projection 门禁要求 enabled + exclusive（缺 exclusive 拒绝）。"""
    _accounts()
    ledger.create_match(_match_create(), registry)
    season_cfg = _season_config(tmp_path, exclusive=True)
    season_cfg["ingest"]["participants"] = {"enabled": True, "exclusive": False}
    configs = tmp_path / "configs"
    configs.mkdir(exist_ok=True)
    (configs / f"{SEASON}.json").write_text(json.dumps(season_cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs))
    _mock_publish(monkeypatch, tmp_path)

    result = projection.project_season(SEASON)
    assert result["state"] == "error"
    assert "exclusive" in result.get("reason", "")
    assert ledger.ladder_dirty_path(SEASON).exists()


def test_request_projection_none_wakes(participants_root):
    """P2-2：A→null 后 request_projection(None) 也唤醒 worker（扫描全部 dirty marker）。"""
    projection._wake.clear()
    projection.request_projection(None)
    assert projection._wake.is_set()


# ---------------------------------------------------------------------------
# R10-F Repair 3：projection lease 锁 ownership / worker 重入
# ---------------------------------------------------------------------------

def test_lease_lock_not_reclaimed_from_live_owner(participants_root):
    """P1：owner 存活且 mtime 被调旧 → 竞争者不得 reclaim。"""
    import os as _os
    import time as _time

    from participants.paths import try_lease_lock

    lock_path = participants_root / "projection_locks" / "s1.lock"
    with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok:
        assert ok is True
        # 人为把 mtime 调旧超过 lease（模拟 30s 阈值误删场景）
        old = _time.time() - 60
        _os.utime(lock_path, (old, old))
        # owner（本进程）仍存活 → B 拿不到锁
        with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok2:
            assert ok2 is False
    # owner 释放后正常可获取
    with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok3:
        assert ok3 is True


def test_lease_lock_release_does_not_delete_other_owner(participants_root):
    """P1：旧 owner 退出不得删除后来 owner 的锁（token 校验）。"""
    import json as _json

    from participants.paths import try_lease_lock

    lock_path = participants_root / "projection_locks" / "s2.lock"
    with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok:
        assert ok is True
        # 模拟锁被 owner B 替换（B 的 token + 已死 PID）
        lock_path.write_text(_json.dumps({"pid": 99999999, "token": "owner-b"}), encoding="utf-8")
        # A 退出：token 不匹配 → 不删除 B 的锁
    assert lock_path.exists(), "旧 owner 不应删除后来 owner 的锁"


def test_lease_lock_reclaim_after_owner_dead(participants_root):
    """P1：owner 已失活且 lease 过期 → 竞争者可以 reclaim。"""
    import json as _json
    import os as _os
    import time as _time

    from participants.paths import try_lease_lock

    lock_path = participants_root / "projection_locks" / "s3.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(_json.dumps({"pid": 99999999, "token": "dead-owner"}), encoding="utf-8")
    _os.utime(lock_path, (_time.time() - 60, _time.time() - 60))
    with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok:
        assert ok is True


def test_worker_start_stop_start_reentrant(participants_root):
    """P2-1：同一进程 start → stop → start 可重入。"""
    projection.start_worker()
    assert projection._thread is not None and projection._thread.is_alive()
    projection.stop_worker()
    assert projection._thread is None
    projection.start_worker()
    assert projection._thread is not None and projection._thread.is_alive()
    projection.stop_worker()
    assert projection._thread is None


# ---------------------------------------------------------------------------
# R10-F Repair 4：reclaim 串行化 / worker shutdown 保引用
# ---------------------------------------------------------------------------

def test_two_reclaimers_single_winner(participants_root):
    """P1：两个 reclaimer 同时 reclaim 同一 dead lease → 恰好一个 winner。"""
    import json as _json
    import os as _os
    import threading
    import time as _time

    from participants.paths import try_lease_lock

    lock_path = participants_root / "projection_locks" / "s4.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(_json.dumps({"pid": 99999999, "token": "dead-a"}), encoding="utf-8")
    _os.utime(lock_path, (_time.time() - 60, _time.time() - 60))

    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)
    hold_event = threading.Event()

    def _contender(name):
        barrier.wait()
        with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok:
            results[name] = ok
            if ok:
                hold_event.wait(0.5)
            else:
                hold_event.set()

    threads = [threading.Thread(target=_contender, args=(name,)) for name in ("B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [name for name, ok in results.items() if ok]
    assert len(winners) == 1, f"应恰好一个 winner，得到 {results}"
    assert len(results) == 2


def test_worker_shutdown_keeps_reference_during_long_publish(participants_root, monkeypatch):
    """P2：长 publisher 时 stop → 保留 _thread 引用；start 登记 deferred restart，
    旧线程退出后恢复恰好一个新 worker（Repair 5 语义）。"""
    import threading
    import time

    entered = threading.Event()
    release = threading.Event()

    def _slow_projection():
        entered.set()
        release.wait(5)

    monkeypatch.setattr(projection, "run_dirty_projection", _slow_projection)
    projection.start_worker()
    assert entered.wait(5), "worker 未进入长任务"
    old_thread = projection._thread
    projection.stop_worker()
    # join 超时：线程仍在长 publisher 中 → 保留引用 + stop 标记
    assert projection._thread is old_thread and projection._thread.is_alive()
    # start 不产生第二个 worker，只登记 deferred restart
    projection.start_worker()
    assert projection._thread is old_thread
    assert projection._thread.is_alive()
    # 放行后旧线程退出 → deferred restart 启动新 worker
    release.set()
    deadline = time.time() + 5
    while projection._thread is old_thread and time.time() < deadline:
        time.sleep(0.05)
    assert projection._thread is not old_thread
    assert projection._thread is not None and projection._thread.is_alive()
    projection.stop_worker()
    assert projection._thread is None


# ---------------------------------------------------------------------------
# R10-F Repair 5：crash-safe reclaim mutex / deferred worker restart
# ---------------------------------------------------------------------------

_RECLAIM_HELPER = """
import sys, time
from pathlib import Path
from participants.paths import try_lease_lock

lock_path = Path(sys.argv[1])
go = Path(sys.argv[2])
while not go.exists():
    time.sleep(0.005)
with try_lease_lock(lock_path, lease_seconds=5, heartbeat_interval=0) as ok:
    print("ACQUIRED" if ok else "NOT_ACQUIRED", flush=True)
    if ok:
        time.sleep(0.3)
"""


def test_two_process_reclaimers_single_winner_with_stale_reclaim(participants_root, tmp_path):
    """P1：预置 expired+dead 主锁 + orphan reclaim 文件 → 两独立进程 reclaim → 恰好一个 winner。"""
    import os as _os
    import subprocess
    import sys as _sys
    import time as _time

    lock_path = participants_root / "projection_locks" / "s5.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 99999999, "token": "dead-a"}), encoding="utf-8")
    _os.utime(lock_path, (_time.time() - 60, _time.time() - 60))
    # orphan reclaim 文件：文件存在但无人持有 advisory lock（模拟 reclaimer 崩溃遗留）
    reclaim_path = lock_path.with_name(lock_path.name + ".reclaim")
    reclaim_path.write_text("orphan", encoding="utf-8")

    helper = tmp_path / "reclaim_helper.py"
    helper.write_text(_RECLAIM_HELPER, encoding="utf-8")
    go = tmp_path / "go"

    root = Path(__file__).resolve().parents[1]
    env = {
        **_os.environ,
        # R10 layout split：participants 已迁至 workbench/（pyproject pythonpath 同规则）
        "PYTHONPATH": str(root / "workbench") + _os.pathsep + str(root / "src") + _os.pathsep + _os.environ.get("PYTHONPATH", ""),
        "KEQING_PARTICIPANT_DATA_ROOT": str(participants_root),
    }
    procs = [
        subprocess.Popen(
            [_sys.executable, str(helper), str(lock_path), str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        for _ in range(2)
    ]
    go.write_text("go", encoding="utf-8")
    results = [p.communicate(timeout=15) for p in procs]
    outputs = [r[0].strip() for r in results]
    errs = [r[1].strip() for r in results]
    assert outputs.count("ACQUIRED") == 1, f"应恰好一个 winner，得到 outputs={outputs}, errs={errs}"
    assert outputs.count("NOT_ACQUIRED") == 1


def test_worker_deferred_restart(participants_root, monkeypatch):
    """P2：长 publisher 时 stop→start 登记 deferred restart，旧线程退出后恢复新 worker。"""
    import threading
    import time

    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def _blocking_projection():
        calls["n"] += 1
        entered.set()
        release.wait(5)

    monkeypatch.setattr(projection, "run_dirty_projection", _blocking_projection)
    projection.start_worker()
    assert entered.wait(5), "旧 worker 未进入长任务"
    projection.stop_worker()  # join 2s 超时：旧线程仍在长任务
    assert projection._thread is not None and projection._thread.is_alive()
    projection.start_worker()  # 登记 deferred restart
    release.set()  # 放行旧线程 → 退出 → _maybe_restart 启动新 worker
    deadline = time.time() + 5
    while calls["n"] < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert calls["n"] >= 2, "旧线程退出后应自动启动新 worker 并再次进入投影"
    assert projection._thread is not None and projection._thread.is_alive()
    projection.stop_worker()
