# -*- coding: utf-8 -*-
"""R14-B: Runtime Process Supervision & State Persistence

核心职责：
1. 维护 Season 本地进程运行记录（processes.json），记录 PID、状态、启动/停止时间及退出码；
2. 提供跨进程文件锁（runtime_lock），确保 spawn / stop 操作的原子性与幂等性；
3. PID 存活性检测与崩溃状态同步（sync_process_states），支持 Workbench 重启后自动识别 stale / dead 进程；
4. 严格只消费 persisted materialization='frozen' 的 Runtime Manifest 启动本地进程；
5. Popen 失败时留下明确的 terminal failure 证据记录。
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from participants.paths import atomic_write_text, now_iso
from replay.ladder import SeasonRegistryError, _load_registry_file
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


def runtime_records_dir(season_id: str) -> Path:
    """每个赛季独立的 runtime 数据存储目录。"""
    return ladder_data_root() / "runtime" / "seasons" / season_id


def runtime_records_file(season_id: str) -> Path:
    return runtime_records_dir(season_id) / "processes.json"


def runtime_logs_dir(season_id: str) -> Path:
    return ladder_data_root() / "runtime" / "logs" / season_id


@contextlib.contextmanager
def runtime_lock(season_id: str, timeout_seconds: float = 10.0) -> Iterator[None]:
    """基于文件的排他跨进程运行时锁。"""
    lock_dir = runtime_records_dir(season_id)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".runtime.lock"
    start = time.time()
    fd: int | None = None
    while True:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            if time.time() - start > timeout_seconds:
                # 尝试检查锁是否过期（超过 60 秒的锁文件视作 stale 恢复）
                try:
                    mtime = lock_file.stat().st_mtime
                    if time.time() - mtime > 60.0:
                        lock_file.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise TimeoutError(f"获取赛季 {season_id} 的运行时锁超时") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            lock_file.unlink(missing_ok=True)


def is_pid_alive(pid: int | None) -> bool:
    """安全检测指定 PID 是否仍在运行（排除僵尸进程与已终止句柄）。"""
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
            return exit_code.value == 259  # STILL_ACTIVE
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
            # 进程存在但无权限发送信号，依然属于运行中
            return True
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

        return True


def load_process_records_locked(season_id: str) -> list[SeasonRuntimeProcessRecord]:
    """读取指定赛季的进程记录文件。"""
    path = runtime_records_file(season_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [SeasonRuntimeProcessRecord.model_validate(item) for item in data]
        return []
    except Exception:
        return []


def save_process_records_locked(season_id: str, records: list[SeasonRuntimeProcessRecord]) -> None:
    """原子写保存进程记录。"""
    path = runtime_records_file(season_id)
    payload = json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False)
    atomic_write_text(path, payload)


def sync_process_states_locked(season_id: str) -> list[SeasonRuntimeProcessRecord]:
    """同步所有已记录进程的存活状态（识别已退出、崩溃或丢失的 PID）。"""
    records = load_process_records_locked(season_id)
    mutated = False
    now = now_iso()

    for r in records:
        if r.state in ("starting", "healthy"):
            if not is_pid_alive(r.pid):
                # 进程已经停止
                r.state = "stopped"
                r.stopped_at = r.stopped_at or now
                if r.exit_code is None:
                    r.exit_code = -1  # 标记未知/外部终止
                mutated = True

    if mutated:
        save_process_records_locked(season_id, records)

    return records


def get_season_runtime_status(
    configs_dir: Path,
    season_id: str,
) -> SeasonRuntimeStatusResponse:
    """只读查询赛季运行时状态与进程列表。"""
    from replay.ladder import SeasonNotFoundError

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
) -> SeasonRuntimeStatusResponse:
    """为指定 running 赛季启动所有 local_model 参与者的运行时进程。

    严格门禁：
    1. Season 必须为 status='running'；
    2. 必须具备持久化且自洽的 runtime_manifest (materialization='frozen')；
    3. 同一个账号不可重复启动（如果已处于 healthy/starting 状态）；
    4. 执行命令直接来自 Manifest 的 resolved_checkpoint_path；
    5. Popen 失败必须原子写入 terminal failure record。
    """
    from replay.ladder import SeasonNotFoundError, SeasonRegistryError

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
            if existing and existing.state in ("starting", "healthy") and is_pid_alive(existing.pid):
                continue

            runtime_id = make_runtime_id(season_id, manifest.manifest_id, aid)
            cmd = build_process_command(participant, project_root=project_root, python_executable=py_exe)
            log_file = logs_dir / f"{aid.replace(':', '_')}.log"

            rec = SeasonRuntimeProcessRecord(
                runtime_id=runtime_id,
                season_id=season_id,
                manifest_id=manifest.manifest_id,
                account_id=aid,
                controller_type=participant.controller_type,
                state="starting",
                started_at=now_iso(),
                command=cmd,
                log_path=str(log_file),
            )

            try:
                # 独立会话/进程组运行
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
                rec.state = "healthy"
            except Exception as exc:
                rec.state = "failed"
                rec.failure_reason = f"进程启动失败: {exc}"
                rec.stopped_at = now_iso()

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
    """幂等停止指定赛季的所有运行中本地进程。"""
    from replay.ladder import SeasonNotFoundError

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
            if r.state in ("starting", "healthy") and is_pid_alive(r.pid) and r.pid:
                try:
                    if os.name == "nt":
                        # Windows 终止进程
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(r.pid)],
                            capture_output=True,
                            check=False,
                        )
                    else:
                        os.kill(r.pid, signal.SIGTERM)
                except Exception:
                    pass

                # 短暂等待并确认退出
                start_wait = time.time()
                while is_pid_alive(r.pid) and (time.time() - start_wait < timeout_seconds):
                    time.sleep(0.05)

                if is_pid_alive(r.pid):
                    try:
                        if os.name != "nt":
                            os.kill(r.pid, signal.SIGKILL)
                    except Exception:
                        pass

                r.state = "stopped"
                r.stopped_at = now
                r.exit_code = 0

        save_process_records_locked(season_id, records)

        return SeasonRuntimeStatusResponse(
            season_id=season_id,
            manifest_id=manifest_id,
            season_status=status,
            processes=records,
            is_active=False,
        )
