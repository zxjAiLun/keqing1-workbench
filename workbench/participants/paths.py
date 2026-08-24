# -*- coding: utf-8 -*-
"""participants 数据平面：数据根解析、原子写、写锁与时间戳工具。

沿用 ladder 数据纪律：纯文件 JSON/JSONL + 临时文件 + ``os.replace`` 原子切换；
跨进程写操作由 ``O_CREAT|O_EXCL`` 锁文件保护（参考 ``training/mortal/publish_ladder_snapshot.py``）。
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workbench.runtime.resolver import data_path

# workbench/participants/paths.py -> workbench -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TZ_OFFSET = timezone(timedelta(hours=8))  # 默认 +08:00


def data_root() -> Path:
    env = os.environ.get("KEQING_PARTICIPANT_DATA_ROOT")
    if env:
        return Path(env)
    return data_path("participants")


def ensure_data_root() -> Path:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def now_iso() -> str:
    """当前时间 ISO-8601，带 +08:00 偏移。"""
    return datetime.now(TZ_OFFSET).isoformat(timespec="seconds")


def atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + ``os.replace`` 原子写入。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def read_json(path: Path, default):
    """读取 JSON；文件不存在返回 default，损坏时抛 ValueError。"""
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, schema: str, payload) -> None:
    """写入带 schema/updated_at 的版本化 JSON。"""
    text = json.dumps(
        {"schema": schema, "updated_at": now_iso(), **payload},
        ensure_ascii=False,
        indent=2,
    )
    atomic_write_text(path, text)


@contextlib.contextmanager
def file_lock(lock_path: Path, timeout: float = 8.0, stale_after: float = 30.0):
    """跨进程写锁：``O_CREAT|O_EXCL`` 独占创建锁文件。

    - 等不到锁且超过 ``timeout`` 秒时抛 ``TimeoutError``；
    - 锁文件 mtime 早于 ``stale_after`` 秒视为 stale，删除后重试。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            acquired = True
        except FileExistsError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"lock not acquired in {timeout}s: {lock_path}")
            # stale 判定：st_mtime 是 epoch 时间戳，必须用 time.time()（而非 monotonic）比较
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_after:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def data_lock():
    """整个 participants 数据根的单写锁（registry/ledger 共享）。"""
    lock_path = ensure_data_root() / "participants.lock"
    with file_lock(lock_path):
        yield


@contextlib.contextmanager
def try_file_lock(lock_path: Path, stale_after: float = 30.0):
    """��阻塞跨进程锁（single-flight）：已占用立即 yield False，不会等待。

    - 成功获取：yield True，退出时删除锁文件；
    - 已被占用：yield False；锁文件过期则清除并重试一次。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    for _attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime <= stale_after:
                    break  # 被活跃持有者占用
                lock_path.unlink()  # stale：清除后重试
            except FileNotFoundError:
                break
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _pid_is_alive(pid: int) -> bool:
    """跨平台 PID 存活检查（lease reclaim 用）。"""
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return True  # 无法确认失活 → 保守视为存活
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _try_advisory_lock(lock_path: Path):
    """OS 自动释放的 advisory lock（reclaim 互斥用，Repair 5）。

    进程退出时内核自动释放锁——不需要 stale 删除协议，也就不存在
    "两个 reclaimer 互删对方刚创建的 reclaim 锁" 的竞态。
    返回持有锁的 fd（调用方负责 close 释放）；获取失败返回 None。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, IOError):
        try:
            os.close(fd)
        except OSError:
            pass
        return None


@contextlib.contextmanager
def try_lease_lock(
    lock_path: Path,
    lease_seconds: float = 30.0,
    heartbeat_interval: float = 5.0,
):
    """带 owner 身份的跨进程 lease 锁（长任务 single-flight，R10-F Repair 3/4）。

    - 获取：``O_EXCL`` 创建，写入 ``{"pid": ..., "token": ...}``；
    - 持有期间：后台线程定期 touch mtime（heartbeat，``heartbeat_interval<=0`` 关闭）；
    - 竞争者：锁文件 mtime 距今 < ``lease_seconds`` → 活跃 lease，yield False；
      超过 ``lease_seconds`` 且 **owner PID 已确认失活** → reclaim（换新 token）；
      mtime 旧但 PID 仍存活 → 不 reclaim（活跃长任务不被误删）；
    - reclaim 串行化（P1/Repair4/5）：dead-owner 判定与删除在 ``<lock>.reclaim``
      **OS advisory lock** 临界区内重新执行——reclaimer 崩溃时内核自动释放，
      无需 stale 删除协议，两个 reclaimer 不会互删对方刚创建的新 lease；
    - 释放：仅当锁文件中的 ``token`` 仍等于自己的 token 才 unlink——
      旧 owner 绝不会删除后来 owner 的锁。
    """
    import json as _json
    import threading as _threading
    import uuid as _uuid

    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    reclaim_path = lock_path.with_name(lock_path.name + ".reclaim")
    token = _uuid.uuid4().hex
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, _json.dumps({"pid": os.getpid(), "token": token}).encode("ascii"))
            os.close(fd)
            acquired = True
        except FileExistsError:
            # lease 被占（或过期）：先获取 reclaim advisory lock，
            # 串行化 dead-owner 判定与删除（OS 自动释放，无 stale 竞态）
            reclaim_fd = _try_advisory_lock(reclaim_path)
            if reclaim_fd is None:
                yield False  # 另一个 reclaimer 正在处理 → already_running
                return
            try:
                # 在 reclaim 临界区内重新读取/重新判定（防 TOCTOU）
                try:
                    age = time.time() - lock_path.stat().st_mtime
                except FileNotFoundError:
                    continue  # 锁已被他人取走 → 重新争抢主锁
                if age <= lease_seconds:
                    yield False  # 仍是活跃 lease
                    return
                try:
                    owner = _json.loads(lock_path.read_text(encoding="utf-8"))
                    owner_alive = _pid_is_alive(int(owner.get("pid") or 0))
                except (OSError, ValueError, TypeError):
                    owner_alive = True  # 无法读取 → 保守视为存活，不 reclaim
                if owner_alive:
                    yield False
                    return
                # 确认 dead + expired：在 reclaim 临界区内删除（C 无法插队误删 B 的新锁）
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
            finally:
                try:
                    os.close(reclaim_fd)  # 释放 advisory lock（文件保留，内核负责回收）
                except OSError:
                    pass
            # 回到主锁争抢

    stop = _threading.Event()

    def _heartbeat() -> None:
        while not stop.is_set():
            try:
                os.utime(lock_path, None)  # touch mtime，保持 lease 活跃
            except OSError:
                pass
            stop.wait(heartbeat_interval)

    heartbeat_thread: _threading.Thread | None = None
    if heartbeat_interval and heartbeat_interval > 0:
        heartbeat_thread = _threading.Thread(target=_heartbeat, daemon=True)
        heartbeat_thread.start()
    try:
        yield True
    finally:
        stop.set()
        # 仅当 token 仍匹配才删除（防止删除后来 owner 的锁）
        try:
            payload = _json.loads(lock_path.read_text(encoding="utf-8"))
            if payload.get("token") == token:
                lock_path.unlink()
        except (OSError, ValueError):
            pass
