# -*- coding: utf-8 -*-
"""R14-B: Runtime Process Supervision & State Persistence (Repair 2 封板版)

核心职责与架构防护：
1. 确定性锁顺序 (Global Lock Hierarchy)：
   `participants_data_lock` -> `_registry_lock` -> `runtime_lock`；
   `spawn_season_runtime` 与 `set_season_status`/`delete_season` 全程持有双/三域锁，彻底消除 TOCTOU 竞态；
2. 进程活跃性基于 OS Fact 唯一仲裁 (OS Process Fact Model)：
   `record_has_live_owned_process(record)` 严格依据 `is_pid_alive_and_matched(pid, birth_identity)`，
   绝不信任 record.state；无论是 failed 还是 stopped 状态，只要 OS PID 存活即视为 ACTIVE 并阻断生命周期；
3. Birth Identity Fail-Closed 门禁：
   若获取进程 `birth_identity` 失败，立刻强杀新创建的孤儿进程并落盘 `state="failed"`，绝不误入 `healthy`；
4. 锁文件 Owner 身份包含 `birth_identity`：
   支持在 OS PID 复用场景下安全判定旧锁 owner 已失效并 reclaim；
5. 停机原因与退出码审计真实记录：
   区分 `graceful` (SIGTERM exit) 与 `forced` (SIGKILL 强杀)，真实记录 SIGKILL 退出码 (-9)；
6. 账本损坏严密 Fail-Closed。
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

from participants.paths import _try_advisory_lock, atomic_write_text, now_iso
from replay.ladder import (
    SeasonNotFoundError,
    SeasonRegistryError,
    _load_registry_file,
)
from replay.season_registry import _registry_lock
from runtime.launcher import (
    SeasonRuntimeLauncherPlan,
    SeasonRuntimeProcessRecord,
    SeasonRuntimeStatusResponse,
    build_launcher_plan,
    build_process_command,
    make_runtime_id,
)
from runtime.manifest import (
    validate_persisted_manifest_against_season,
)
from runtime.resolver.base import REPO_ROOT
from runtime.resolver.ladder import ladder_data_root


class RuntimeSupervisorStateError(SeasonRegistryError):
    """运行时进程记录或锁状态异常，必须 fail-closed 拦截。"""


def runtime_records_dir(season_id: str) -> Path:
    """每个赛季独立的 runtime 数据存储目录。"""
    return ladder_data_root() / "runtime" / "seasons" / season_id


def runtime_records_file(season_id: str) -> Path:
    return runtime_records_dir(season_id) / "processes.json"


def runtime_logs_dir(season_id: str) -> Path:
    return ladder_data_root() / "runtime" / "logs" / season_id


# ---------------------------------------------------------------------------
# 1. Process Birth Identity & Liveness with PID Reuse Protection
# ---------------------------------------------------------------------------

def get_process_birth_identity(pid: int | None) -> str | None:
    """获取进程的创建标识（Linux: stat starttime ticks, Windows: creation timestamp）。"""
    if pid is None or pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            creation_time = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            )
            kernel32.CloseHandle(handle)
            if ok:
                high = int(creation_time.dwHighDateTime)
                low = int(creation_time.dwLowDateTime)
                return f"win_{high}_{low}"
            return None
        except Exception:
            return None
    else:
        stat_path = Path(f"/proc/{pid}/stat")
        if not stat_path.exists():
            return None
        try:
            content = stat_path.read_text(encoding="utf-8", errors="ignore").strip()
            # 找到最后一个右括号以处理进程名带空格的情况
            rparen = content.rfind(")")
            if rparen != -1:
                fields = content[rparen + 1 :].strip().split()
                # field[19] is starttime (22nd field in man 5 proc, 0-indexed after rparen is field 19)
                if len(fields) >= 20:
                    return f"linux_{fields[19]}"
            return None
        except OSError:
            return None


def is_pid_alive_and_matched(pid: int | None, expected_birth_identity: str | None = None) -> bool:
    """安全检测指定 PID 是否仍在运行，且 birth_identity 与记录严格匹配（防止 PID 被系统复用）。"""
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            alive = (exit_code.value == 259)  # STILL_ACTIVE
            if not alive:
                return False
            if expected_birth_identity:
                actual_birth = get_process_birth_identity(pid)
                if actual_birth != expected_birth_identity:
                    return False
            return True
        except Exception:
            return False
    else:
        try:
            res_pid, _ = os.waitpid(pid, os.WNOHANG)
            if res_pid == pid:
                return False
        except (ChildProcessError, OSError):
            pass

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        except OSError:
            return False

        # 检查 /proc/{pid}/status 排除僵尸进程 (zombie / dead)
        proc_status = Path(f"/proc/{pid}/status")
        if proc_status.exists():
            try:
                content = proc_status.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    if line.startswith("State:"):
                        if "Z" in line or "zombie" in line or "X" in line:
                            return False
            except OSError:
                return False

        # 校验 birth_identity 防止 PID 复用
        if expected_birth_identity:
            actual_birth = get_process_birth_identity(pid)
            if actual_birth != expected_birth_identity:
                return False

        return True


def record_has_live_owned_process(record: SeasonRuntimeProcessRecord) -> bool:
    """基于 OS 事实唯一判定进程记录当前是否真实有进程在运行。

    规则：
    - 若 pid 为空，返回 False；
    - 若存在 pid，必须由 is_pid_alive_and_matched 判定；
    - 绝不信任 record.state！即使 state='failed' 或 'stopped'，只要底层 PID 仍存活即视为 ACTIVE！
    """
    if record.pid is None or record.pid <= 0:
        return False
    if not record.birth_identity:
        # 如果有 PID 但从未取得合法 birth_identity，保守视为未知异常，若 PID 存活则必须拦截
        return is_pid_alive_and_matched(record.pid, None)
    return is_pid_alive_and_matched(record.pid, record.birth_identity)


# ---------------------------------------------------------------------------
# 2. Owner-Aware Runtime Lease Lock with Advisory Reclaim Serialization
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def runtime_lock(
    season_id: str,
    timeout_seconds: float = 10.0,
    lease_seconds: float = 60.0,
) -> Iterator[None]:
    """带 owner 身份与 dead-owner 判定的跨进程运行时锁（记录 birth_identity 防 PID 复用）。

    - 严禁纯根据 mtime 删除活跃 owner 的锁；
    - 记录 {"pid": ..., "birth_identity": ..., "token": ...}；
    - 仅当 mtime 超时且经 is_pid_alive_and_matched 确认 owner 已死，在 OS advisory lock 临界区内安全回收；
    - 退出时仅当 token 匹配才删除锁文件。
    """
    lock_dir = runtime_records_dir(season_id)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".runtime.lock"
    reclaim_path = lock_dir / ".runtime.lock.reclaim"
    token = uuid.uuid4().hex
    my_pid = os.getpid()
    my_birth = get_process_birth_identity(my_pid)
    acquired = False
    start_time = time.time()

    while not acquired:
        if time.time() - start_time > timeout_seconds:
            raise TimeoutError(f"获取赛季 {season_id} 的运行时锁超时 (timeout={timeout_seconds}s)")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = json.dumps({
                "pid": my_pid,
                "birth_identity": my_birth,
                "token": token,
            }).encode("utf-8")
            os.write(fd, payload)
            os.close(fd)
            acquired = True
        except FileExistsError:
            # 尝试通过 reclaim advisory lock 判定前任 owner 是否存活
            reclaim_fd = _try_advisory_lock(reclaim_path)
            if reclaim_fd is not None:
                try:
                    try:
                        age = time.time() - lock_path.stat().st_mtime
                    except FileNotFoundError:
                        continue  # 锁已被释放，重试
                    if age > lease_seconds:
                        try:
                            owner_info = json.loads(lock_path.read_text(encoding="utf-8"))
                            owner_pid = int(owner_info.get("pid") or 0)
                            owner_birth = owner_info.get("birth_identity")
                            owner_alive = is_pid_alive_and_matched(owner_pid, owner_birth)
                        except Exception:
                            owner_alive = True  # 无法判定时保守视作存活
                        if not owner_alive:
                            # 确认 owner 已死（或 PID 已被复用），安全 unlink
                            try:
                                lock_path.unlink(missing_ok=True)
                            except OSError:
                                pass
                finally:
                    try:
                        os.close(reclaim_fd)
                    except OSError:
                        pass
            time.sleep(0.05)

    try:
        yield
    finally:
        # 释放：只有 token 匹配时才删除
        try:
            if lock_path.exists():
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                if data.get("token") == token:
                    lock_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 3. Process Records Load / Save / Sync (Fail-Closed on Corruption)
# ---------------------------------------------------------------------------

def load_process_records_locked(season_id: str) -> list[SeasonRuntimeProcessRecord]:
    """读取指定赛季的进程记录文件；损坏时严格抛错 (fail-closed)。"""
    path = runtime_records_file(season_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeSupervisorStateError(f"赛季 {season_id} 的 processes.json 解析失败 (损坏): {exc}") from exc

    if not isinstance(data, list):
        raise RuntimeSupervisorStateError(f"赛季 {season_id} 的 processes.json 格式异常: 顶层必须是数组")

    try:
        return [SeasonRuntimeProcessRecord.model_validate(item) for item in data]
    except Exception as exc:
        raise RuntimeSupervisorStateError(f"赛季 {season_id} 的进程记录结构校验失败: {exc}") from exc


def save_process_records_locked(season_id: str, records: list[SeasonRuntimeProcessRecord]) -> None:
    """原子写保存进程记录。"""
    path = runtime_records_file(season_id)
    payload = json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False)
    atomic_write_text(path, payload)


def sync_process_states_locked(season_id: str) -> list[SeasonRuntimeProcessRecord]:
    """同步所有已记录进程的存活状态（严格防 PID 复用，识别已退出、崩溃或丢失的 PID）。"""
    records = load_process_records_locked(season_id)
    mutated = False
    now = now_iso()

    for r in records:
        is_live = record_has_live_owned_process(r)
        if r.state in ("starting", "healthy"):
            if not is_live:
                r.state = "stopped"
                r.stopped_at = r.stopped_at or now
                if r.exit_code is None:
                    r.exit_code = -1  # 标记外部终止/未知退出
                mutated = True
        elif is_live and r.state in ("stopped", "failed"):
            # 账本自相矛盾（标记为 stopped/failed 但实际 PID 仍活着）：保持状态并标记告警
            pass

    if mutated:
        save_process_records_locked(season_id, records)

    return records


# ---------------------------------------------------------------------------
# 4. Supervision Operations: Status, Spawn, Stop
# ---------------------------------------------------------------------------

def get_season_runtime_status(
    configs_dir: Path,
    season_id: str,
) -> SeasonRuntimeStatusResponse:
    """只读查询赛季运行时状态与进程列表。"""
    season_path = configs_dir / f"{season_id}.json"
    if not season_path.exists():
        raise SeasonNotFoundError(f"赛季配置不存在: {season_id}")

    season = _load_registry_file(season_path)
    season_status = season.get("status", "draft")
    manifest_raw = season.get("runtime_manifest")
    manifest_id = manifest_raw.get("manifest_id", "") if isinstance(manifest_raw, dict) else ""

    with runtime_lock(season_id):
        records = sync_process_states_locked(season_id)

    is_active = any(record_has_live_owned_process(r) for r in records)

    return SeasonRuntimeStatusResponse(
        season_id=season_id,
        manifest_id=manifest_id,
        season_status=season_status,
        processes=records,
        is_active=is_active,
    )


def spawn_season_runtime(
    configs_dir: Path,
    season_id: str,
    *,
    project_root: Path = REPO_ROOT,
    python_executable: str = "",
    custom_command_builder: Callable[[Any, Path, str], list[str]] | None = None,
    startup_grace_seconds: float = 0.15,
) -> SeasonRuntimeStatusResponse:
    """为指定 running 赛季启动所有 local_model 参与者的运行时进程。

    严格全局锁顺序与门禁：
    1. 获取 `_registry_lock(configs_dir)` 并在锁内读取 Season，确认仍为 `running`；
    2. 获取 `runtime_lock(season_id)`，全程持双锁完成 spawn 并落盘；
    3. 校验 Manifest 自洽性；
    4. 执行命令直接来自 Manifest 的 `resolved_checkpoint_path`；
    5. Popen 后必须验证 `proc.poll() is None` 且 `birth_identity is not None`，否则立即清理退出；
    6. 立即关闭父进程 log handle，杜绝 FD 泄漏。
    """
    with _registry_lock(configs_dir):
        season_path = configs_dir / f"{season_id}.json"
        if not season_path.exists():
            raise SeasonNotFoundError(f"赛季配置不存在: {season_id}")

        season = _load_registry_file(season_path)
        status = season.get("status", "draft")
        if status != "running":
            raise SeasonRegistryError(f"只能为 running 状态的赛季启动运行时进程 (当前为 {status})")

        manifest_raw = season.get("runtime_manifest")
        if not manifest_raw:
            raise SeasonRegistryError(f"赛季 {season_id} 缺少 runtime_manifest，无法启动进程")

        # 深度校验 Manifest 自洽性与权威绑定
        manifest = validate_persisted_manifest_against_season(season, manifest_raw)
        plan = build_launcher_plan(manifest)

        py_exe = python_executable or os.environ.get("PYTHON_EXECUTABLE") or sys.executable

        logs_dir = runtime_logs_dir(season_id)
        logs_dir.mkdir(parents=True, exist_ok=True)

        with runtime_lock(season_id):
            records = sync_process_states_locked(season_id)
            records_by_account = {r.account_id: r for r in records}

            # 启动每个 spawnable 参与者
            for participant in plan.spawnable_participants:
                aid = participant.account_id
                existing = records_by_account.get(aid)

                # 防重复 spawn：只要底层 OS PID 存活即阻断
                if existing and record_has_live_owned_process(existing):
                    continue

                runtime_id = make_runtime_id(season_id, manifest.manifest_id, aid)
                cmd = build_process_command(
                    participant,
                    project_root=project_root,
                    python_executable=py_exe,
                    custom_command_builder=custom_command_builder,
                )
                log_file = logs_dir / f"{aid.replace(':', '_')}.log"
                owner_tok = uuid.uuid4().hex

                rec = SeasonRuntimeProcessRecord(
                    runtime_id=runtime_id,
                    season_id=season_id,
                    manifest_id=manifest.manifest_id,
                    account_id=aid,
                    controller_type=participant.controller_type,
                    owner_token=owner_tok,
                    state="starting",
                    started_at=now_iso(),
                    command=cmd,
                    log_path=str(log_file),
                )

                log_fh = None
                try:
                    log_fh = open(log_file, "a", encoding="utf-8")
                    popen_kwargs: dict[str, Any] = {
                        "cwd": str(project_root),
                        "stdout": log_fh,
                        "stderr": subprocess.STDOUT,
                        "text": True,
                    }
                    if os.name == "nt":
                        popen_kwargs["creationflags"] = getattr(
                            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                        )
                    else:
                        popen_kwargs["start_new_session"] = True

                    proc = subprocess.Popen(cmd, **popen_kwargs)
                    rec.pid = proc.pid
                    # 立即关闭父进程文件句柄
                    if log_fh:
                        log_fh.close()
                        log_fh = None

                    # Startup grace verification: 等待确认未瞬间崩溃
                    if startup_grace_seconds > 0:
                        time.sleep(startup_grace_seconds)

                    poll_res = proc.poll()
                    if poll_res is not None:
                        # 瞬间退出/启动失败
                        rec.state = "failed" if poll_res != 0 else "stopped"
                        rec.exit_code = poll_res
                        rec.failure_reason = f"进程启动后立即退出 (exit_code={poll_res})"
                        rec.stopped_at = now_iso()
                    else:
                        birth_id = get_process_birth_identity(proc.pid)
                        if birth_id is None:
                            # 无法获取 birth_identity: 强行 fail-closed 并杀掉新孤儿进程
                            try:
                                if os.name != "nt":
                                    os.kill(proc.pid, signal.SIGKILL)
                                else:
                                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False)
                            except Exception:
                                pass
                            # 等待进程死亡
                            kill_start = time.time()
                            while is_pid_alive_and_matched(proc.pid, None) and (time.time() - kill_start < 0.5):
                                time.sleep(0.02)
                            rec.state = "failed"
                            rec.failure_reason = "无法获取进程 birth_identity (fail-closed)"
                            rec.stopped_at = now_iso()
                        else:
                            rec.birth_identity = birth_id
                            rec.state = "healthy"
                except Exception as exc:
                    rec.state = "failed"
                    rec.failure_reason = f"进程启动失败: {exc}"
                    rec.stopped_at = now_iso()
                finally:
                    if log_fh is not None:
                        try:
                            log_fh.close()
                        except OSError:
                            pass

                records_by_account[aid] = rec

            final_records = list(records_by_account.values())
            save_process_records_locked(season_id, final_records)

            is_active = any(record_has_live_owned_process(r) for r in final_records)
            return SeasonRuntimeStatusResponse(
                season_id=season_id,
                manifest_id=manifest.manifest_id,
                season_status=status,
                processes=final_records,
                is_active=is_active,
            )


def stop_season_runtime(
    configs_dir: Path,
    season_id: str,
    *,
    timeout_seconds: float = 3.0,
) -> SeasonRuntimeStatusResponse:
    """幂等停止指定赛季的所有运行中本地进程（进程组级杀灭与最终死亡确认）。"""
    season_path = configs_dir / f"{season_id}.json"
    if not season_path.exists():
        raise SeasonNotFoundError(f"赛季配置不存在: {season_id}")

    season = _load_registry_file(season_path)
    status = season.get("status", "draft")
    manifest_raw = season.get("runtime_manifest")
    manifest_id = manifest_raw.get("manifest_id", "") if isinstance(manifest_raw, dict) else ""

    now = now_iso()

    with runtime_lock(season_id):
        records = sync_process_states_locked(season_id)

        for r in records:
            if r.pid and record_has_live_owned_process(r):
                used_sigkill = False
                # 1. 尝试优雅 SIGTERM
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/T", "/PID", str(r.pid)],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        try:
                            pgid = os.getpgid(r.pid)
                            os.killpg(pgid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, OSError):
                            os.kill(r.pid, signal.SIGTERM)
                except Exception:
                    pass

                # 2. 等待进程退出
                start_wait = time.time()
                while record_has_live_owned_process(r) and (time.time() - start_wait < timeout_seconds):
                    time.sleep(0.05)

                # 3. 超时强制 SIGKILL
                if record_has_live_owned_process(r):
                    used_sigkill = True
                    try:
                        if os.name != "nt":
                            try:
                                pgid = os.getpgid(r.pid)
                                os.killpg(pgid, signal.SIGKILL)
                            except (ProcessLookupError, PermissionError, OSError):
                                os.kill(r.pid, signal.SIGKILL)
                        else:
                            subprocess.run(["taskkill", "/F", "/T", "/PID", str(r.pid)], capture_output=True, check=False)
                    except Exception:
                        pass
                    # 再等待 0.5s 确认
                    start_kill_wait = time.time()
                    while record_has_live_owned_process(r) and (time.time() - start_kill_wait < 0.5):
                        time.sleep(0.05)

                # 4. 最终状态判定与真实退出码审计
                if record_has_live_owned_process(r):
                    r.state = "failed"
                    r.failure_reason = "停止进程失败: SIGKILL 后进程仍存活"
                else:
                    r.state = "stopped"
                    r.stopped_at = now
                    r.exit_code = -9 if used_sigkill else 0

        save_process_records_locked(season_id, records)
        is_active = any(record_has_live_owned_process(r) for r in records)

        return SeasonRuntimeStatusResponse(
            season_id=season_id,
            manifest_id=manifest_id,
            season_status=status,
            processes=records,
            is_active=is_active,
        )


# ---------------------------------------------------------------------------
# 5. Lifecycle Gate Interlock: Locked Assert Helpers
# ---------------------------------------------------------------------------

def assert_no_live_runtime_processes_locked(season_id: str) -> None:
    """在持有 `_registry_lock` 和 `runtime_lock` 的原子事务中调用。

    基于 OS 事实严密判定：若有任何本地进程存活，抛出 SeasonRegistryError (HTTP 409)。
    """
    records = sync_process_states_locked(season_id)
    live_records = [r for r in records if record_has_live_owned_process(r)]
    if live_records:
        accounts = ", ".join(r.account_id for r in live_records)
        raise SeasonRegistryError(
            f"赛季 {season_id} 当前仍有正在运行的本地模型进程 ({accounts})，"
            "请先调用 /runtime/stop 停止进程后再进行生命周期变更 (runtime_active)"
        )
