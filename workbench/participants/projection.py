# -*- coding: utf-8 -*-
"""R10-F：天梯投影自动消费 worker（P1-6）与并发一致性。

- ``project_season``：
  - P2 门禁：赛季必须 ``ingest.participants.enabled + exclusive``（正式合同）；
  - P1-2 跨进程 per-season single-flight（``try_file_lock``）；
  - P1-1 begin barrier（``ledger.begin_ladder_projection``：锁内恢复 pending 后
    冻结 generation）；
  - 完成后 CAS（generation 未变才清 dirty + ready）；失败回写也带 CAS
    （``ledger.mark_season_projection_error``）。
- ``run_dirty_projection``：扫描全部 dirty marker，逐赛季投影（coalesce 多次 dirty）。
- worker：启动扫描 + mutation 唤醒 + 定时兜底；lifespan 管理（start/stop）。
"""
from __future__ import annotations

import threading
from pathlib import Path

from . import ledger
from .paths import data_root, try_lease_lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_wake = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_stop = threading.Event()
_restart_pending = False


def _dirty_markers() -> list[Path]:
    root = data_root()
    if not root.is_dir():
        return []
    return sorted(root.glob("ladder_dirty_*.json"))


def _projection_lock_path(season_id: str) -> Path:
    return data_root() / "projection_locks" / f"{season_id}.lock"


def project_season(season_id: str) -> dict:
    """投影单个赛季（per-season single-flight lease）。返回 {season_id, state, ...}。"""
    with try_lease_lock(_projection_lock_path(season_id)) as acquired:
        if not acquired:
            # 另一个 worker/手动请求正在投影同一赛季：不重复进入 publisher
            return {
                "season_id": season_id,
                "state": "already_running",
                "reason": "该赛季投影正在进行中",
            }
        return _project_season_locked(season_id)


def _project_season_locked(season_id: str) -> dict:
    from replay import ladder as ladder_data

    configs_dir = ladder_data.resolve_config_dir(PROJECT_ROOT)
    registry_path = configs_dir / f"{season_id}.json"
    if not registry_path.is_file():
        ledger.mark_season_projection_error(season_id, ledger.read_ladder_generation(season_id))
        return {"season_id": season_id, "state": "error", "reason": f"赛季配置不存在: {season_id}"}
    try:
        season = ladder_data.get_season_config(configs_dir, season_id)
    except ladder_data.SeasonNotFoundError as exc:
        ledger.mark_season_projection_error(season_id, ledger.read_ladder_generation(season_id))
        return {"season_id": season_id, "state": "error", "reason": str(exc)}
    ingest = season.get("ingest") if isinstance(season.get("ingest"), dict) else {}
    participants_cfg = ingest.get("participants")
    # P2-1 gate：正式合同要求 enabled + exclusive（缺 exclusive 会重新打开双计窗口）
    if not (
        isinstance(participants_cfg, dict)
        and participants_cfg.get("enabled")
        and participants_cfg.get("exclusive")
    ):
        ledger.mark_season_projection_error(season_id, ledger.read_ladder_generation(season_id))
        return {
            "season_id": season_id,
            "state": "error",
            "reason": "赛季未启用 participants 投影（需 ingest.participants.enabled + exclusive）",
        }

    # P1-1：begin barrier——锁内恢复 pending 后冻结 generation
    start_generation = ledger.begin_ladder_projection(season_id)
    try:
        from replay.publish_ladder_snapshot import publish_snapshot

        result = publish_snapshot(registry_path=registry_path, log_dirs=[])
    except Exception as exc:  # noqa: BLE001
        # P1-2：失败回写带 generation CAS，防止过期失败覆盖更新的 ready/pending
        ledger.mark_season_projection_error(season_id, start_generation)
        return {"season_id": season_id, "state": "error", "reason": str(exc)}

    # P1-1/P1-2：完成 CAS（发布期间新写入 → 保留 dirty + pending）
    if ledger.complete_ladder_projection(season_id, start_generation):
        return {
            "season_id": season_id,
            "state": "ready",
            "snapshot_dir": result.get("snapshot_dir"),
            "games": result.get("games"),
        }
    return {
        "season_id": season_id,
        "state": "needs_rebuild",
        "reason": "发布期间有新的账本写入，dirty 已保留",
    }


def run_dirty_projection() -> list[dict]:
    """扫描并投影全部 dirty 赛季（worker / 启动恢复 / 手动重试共用）。"""
    results: list[dict] = []
    for marker in _dirty_markers():
        season_id = marker.name[len("ladder_dirty_"):-len(".json")]
        results.append(project_season(season_id))
    return results


def request_projection(season_id: str | None) -> None:
    """Match mutation 后唤醒 worker（dirty 已由 ledger 写入）。

    不依赖 season 参数——worker 会扫描全部 dirty marker（P2-2：A→null 也唤醒）。
    """
    _wake.set()


def start_worker() -> None:
    """启动后台 worker（幂等，可 start→stop→start 重入）。

    P2（Repair5）：若旧 worker 仍在长 publisher 中且已置 stop，则登记
    deferred restart——旧线程退出后由 ``_maybe_restart`` 自动启动新 worker，
    不会出现"同进程重启后 worker 归零"。
    """
    global _thread, _restart_pending
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            if _stop.is_set():
                _restart_pending = True  # 等旧线程退出后重启
            return
        _thread = None
        _restart_pending = False
        _stop.clear()  # P2-1：重入时必须清掉上次的 stop 标记
        _wake.clear()
        _thread = threading.Thread(target=_loop, name="ladder-projection", daemon=True)
        _thread.start()


def _loop() -> None:
    while not _stop.is_set():
        try:
            run_dirty_projection()
        except Exception:  # noqa: BLE001
            pass
        _wake.wait(timeout=10)
        _wake.clear()
    _maybe_restart()


def _maybe_restart() -> None:
    """旧 worker 退出后处理 deferred restart（P2/Repair5）。"""
    global _thread, _restart_pending
    with _thread_lock:
        if _thread is not threading.current_thread():
            return  # 已被 stop_worker 清理或已被新线程替换，不重复启动
        if not _restart_pending:
            _thread = None
            return
        _restart_pending = False
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_loop, name="ladder-projection", daemon=True)
        _thread.start()


def stop_worker() -> None:
    """停止后台 worker（lifespan shutdown）。

    P2（Repair5）：若原线程仍在长 publisher 中，join 超时后**保留 _thread 引用
    与 stop 标记**——之后的 start_worker 看到存活线程登记 deferred restart。
    join 期间释放 _thread_lock，避免旧线程退出路径（_maybe_restart）被锁阻塞。
    """
    global _thread
    with _thread_lock:
        if _thread is None:
            return
        _stop.set()
        _wake.set()
        target = _thread
    if target.is_alive():
        target.join(timeout=2.0)
    with _thread_lock:
        if target.is_alive():
            return  # 长任务仍在运行：保留引用，stop 标记保持设置
        if _thread is target:
            _thread = None


__all__ = [
    "project_season",
    "run_dirty_projection",
    "request_projection",
    "start_worker",
    "stop_worker",
]
