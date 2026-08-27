# -*- coding: utf-8 -*-
"""R14-B: Runtime Process Supervision & State Persistence

核心职责与架构防护：
1. 维护 Season 本地进程运行记录（processes.json），记录 PID、birth_identity、owner_token、状态、启动/停止时间及退出码；
2. 跨进程锁基于 owner-aware lease + OS advisory lock 机制，严禁纯 mtime 误删活跃锁；
3. PID 存活性与 birth_identity 双重校验（防 PID 复用误伤无辜进程）；
4. Startup grace verification: Popen 后进行启动确认（poll is None 且 birth_identity 确立才转 healthy）；
5. 损坏 processes.json 严格抛出 RuntimeSupervisorStateError fail-closed；
6. 幂等启停与进程组优雅杀灭 (SIGTERM -> SIGKILL -> 存活性确认)；
7. 资源清理：Popen 成功后父进程立即关闭日志文件句柄。
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
from replay.ladder import SeasonNotFoundError, SeasonRegistryError, _load_registry_file
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
    """安全检测指定 PID 是否仍在运行，且 birth_identity 与记录匹配（防止 PID 被系统复用）。"""
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


# ---------------------------------------------------------------------------
# 2. Owner-Aware Runtime Lease Lock with Advisory Reclaim Serialization
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def runtime_lock(
    season_id: str,
    timeout_seconds: float = 10.0,
    lease_seconds: float = 60.0,
) -> Iterator[None]:
    """带 owner 身份与 dead-owner 判定的跨进程运行时锁。

    - 严禁纯根据 mtime 删除活跃 owner 的锁；
    - 仅当 mtime 超时且确认 owner PID 已死亡时，在 OS advisory lock 临界区内安全回收；
    - 退出时仅当 token 匹配才删除锁文件。
    """
    lock_dir = runtime_records_dir(season_id)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".runtime.lock"
    reclaim_path = lock_dir / ".runtime.lock.reclaim"
    token = uuid.uuid4().hex
    my_pid = os.getpid()
    acquired = False
    start_time = time.time()

    while not acquired:
        if time.time() - start_time > timeout_seconds:
            raise TimeoutError(f"获取赛季 {season_id} 的运行时锁超时 (timeout={timeout_seconds}s)")
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = json.dumps({"pid": my_pid, "token": token}).encode("utf-8")
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
                            owner_alive = is_pid_alive_and_matched(owner_pid)
                        except Exception:
                            owner_alive = True  # 无法判定时保守视作存活
                        if not owner_alive:
                            # 确认 owner 已死，安全 unlink
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
        if r.state in ("starting", "healthy"):
            if not is_pid_alive_and_matched(r.pid, r.birth_identity):
                # 进程已经停止或 PID 被复用
                r.state = "stopped"
                r.stopped_at = r.stopped_at or now
                if r.exit_code is None:
                    r.exit_code = -1  # 标记外部终止/未知退出
                mutated = True

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

    is_active = any(r.state in ("starting", "healthy") for r in records)

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

    严格门禁：
    1. Season 必须为 status='running'；
    2. 必须具备持久化且自洽的 runtime_manifest (materialization='frozen')；
    3. 同一个账号不可重复启动（如果已处于 healthy/starting 状态）；
    4. 执行命令直接来自 Manifest 的 resolved_checkpoint_path；
    5. Popen 后必须进行启动验证 (poll is None 且 birth_identity 确认才转 healthy)；
    6. 立即关闭父进程 log handle，防止 FD 泄漏；
    7. Popen 失败或瞬间退出必须留下 terminal failure/stopped 审计证据。
    """
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

            # 防重复 spawn
            if existing and existing.state in ("starting", "healthy") and is_pid_alive_and_matched(existing.pid, existing.birth_identity):
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
                # 立即关闭父进程文件句柄，防止长期泄漏 FD
                if log_fh:
                    log_fh.close()
                    log_fh = None

                # Startup grace verification: 等待极短时间确认未瞬间崩溃
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

        is_active = any(r.state in ("starting", "healthy") for r in final_records)
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
            if r.state in ("starting", "healthy") and r.pid:
                # 只有 birth_identity 严格匹配才执行 kill，避免误杀复用 PID 的无关进程
                if not is_pid_alive_and_matched(r.pid, r.birth_identity):
                    r.state = "stopped"
                    r.stopped_at = r.stopped_at or now
                    if r.exit_code is None:
                        r.exit_code = -1
                    continue

                # 1. 尝试优雅 SIGTERM
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(r.pid)],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        # 尝试向整个 process group 发送 SIGTERM
                        try:
                            pgid = os.getpgid(r.pid)
                            os.killpg(pgid, signal.SIGTERM)
                        except (ProcessLookupError, PermissionError, OSError):
                            os.kill(r.pid, signal.SIGTERM)
                except Exception:
                    pass

                # 2. 等待进程退出
                start_wait = time.time()
                while is_pid_alive_and_matched(r.pid, r.birth_identity) and (time.time() - start_wait < timeout_seconds):
                    time.sleep(0.05)

                # 3. 超时强制 SIGKILL
                if is_pid_alive_and_matched(r.pid, r.birth_identity):
                    try:
                        if os.name != "nt":
                            try:
                                pgid = os.getpgid(r.pid)
                                os.killpg(pgid, signal.SIGKILL)
                            except (ProcessLookupError, PermissionError, OSError):
                                os.kill(r.pid, signal.SIGKILL)
                    except Exception:
                        pass
                    # 再等待 0.5s 确认
                    start_kill_wait = time.time()
                    while is_pid_alive_and_matched(r.pid, r.birth_identity) and (time.time() - start_kill_wait < 0.5):
                        time.sleep(0.05)

                # 4. 最终状态判定：绝不盲目假定 stopped
                if is_pid_alive_and_matched(r.pid, r.birth_identity):
                    r.state = "failed"
                    r.failure_reason = "停止进程失败: SIGKILL 后进程仍存活"
                else:
                    r.state = "stopped"
                    r.stopped_at = now
                    r.exit_code = 0

        save_process_records_locked(season_id, records)
        is_active = any(r.state in ("starting", "healthy") for r in records)

        return SeasonRuntimeStatusResponse(
            season_id=season_id,
            manifest_id=manifest_id,
            season_status=status,
            processes=records,
            is_active=is_active,
        )


# ---------------------------------------------------------------------------
# 5. Lifecycle Gate Interlock: Complete / Archive / Delete Hard Gate
# ---------------------------------------------------------------------------

def ensure_no_active_runtime_processes(season_id: str) -> None:
    """在执行 Complete / Archive / Delete 前调用，若有进程存活则抛出 SeasonRegistryError (HTTP 409)。"""
    with runtime_lock(season_id):
        records = sync_process_states_locked(season_id)
        active_records = [r for r in records if r.state in ("starting", "healthy")]
        if active_records:
            accounts = ", ".join(r.account_id for r in active_records)
            raise SeasonRegistryError(
                f"赛季 {season_id} 当前仍有正在运行的本地模型进程 ({accounts})，"
                "请先调用 /runtime/stop 停止进程后再进行生命周期变更 (runtime_active)"
            )
