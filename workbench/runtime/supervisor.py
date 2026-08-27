# -*- coding: utf-8 -*-
"""R14-B: Runtime Process Supervision & State Persistence (Repair 3 终极封板版)

核心职责与架构防护：
1. 进程归属三态仲裁 (Ownership Tri-State Model):
   - `OWNED_LIVE`: 具有有效 pid 与 birth_identity，且经 is_pid_alive_and_matched 严格确认为归属于当前系统记录的活进程；
   - `NOT_LIVE`: 进程未运行或已确认退出死亡；
   - `OWNERSHIP_UNKNOWN`: 记录中存在 pid 但缺少 birth_identity，且该 pid 在当前系统存活（疑似 PID 复用或历史损坏记录）。
   -> Lifecycle (Complete/Archive/Delete) 及 Duplicate Spawn 对 `OWNED_LIVE` 与 `OWNERSHIP_UNKNOWN` 统一 fail-closed 拦截；
   -> **Stop 操作仅允许对 `OWNED_LIVE` 下发停机信号；对 `OWNERSHIP_UNKNOWN` 坚决禁止下发信号，绝不误杀无关进程**。
2. 运行时锁 `runtime_lock` 自洽性保证：
   - 获取锁前强制校验 `get_process_birth_identity(my_pid)`，若无法获取 birth_identity 则直接抛出 `RuntimeSupervisorStateError` fail-closed，杜绝创建无 owner 凭证的锁。
3. 全局严格确定性锁顺序 (Global Lock Hierarchy)：
   `participants_data_lock` -> `_registry_lock` -> `runtime_lock`；
4. 真实审计退出码与停机模式 (SIGTERM graceful 0 vs SIGKILL forced -9)；
5. 损坏账本 Fail-Closed。
"""
from __future__ import annotations

import contextlib
import enum
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


class ProcessOwnershipStatus(enum.Enum):
    OWNED_LIVE = "owned_live"
    NOT_LIVE = "not_live"
    OWNERSHIP_UNKNOWN = "ownership_unknown"


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


def evaluate_process_ownership(record: SeasonRuntimeProcessRecord) -> ProcessOwnershipStatus:
    """基于 OS 事实与 birth_identity 精确判定进程归属三态。

    - `pid is None or pid <= 0`: NOT_LIVE
    - `birth_identity` 有效且 `is_pid_alive_and_matched(pid, birth_identity) == True`: OWNED_LIVE
    - `birth_identity` 有效但 `is_pid_alive_and_matched(pid, birth_identity) == False`: NOT_LIVE
    - `birth_identity` 缺失但 `is_pid_alive_and_matched(pid, None) == True`: OWNERSHIP_UNKNOWN
    - `birth_identity` 缺失且 `is_pid_alive_and_matched(pid, None) == False`: NOT_LIVE
    """
    if record.pid is None or record.pid <= 0:
        return ProcessOwnershipStatus.NOT_LIVE

    if record.birth_identity:
        if is_pid_alive_and_matched(record.pid, record.birth_identity):
            return ProcessOwnershipStatus.OWNED_LIVE
        return ProcessOwnershipStatus.NOT_LIVE

    # birth_identity 缺失
    if is_pid_alive_and_matched(record.pid, None):
        return ProcessOwnershipStatus.OWNERSHIP_UNKNOWN
    return ProcessOwnershipStatus.NOT_LIVE


def record_has_live_owned_process(record: SeasonRuntimeProcessRecord) -> bool:
    """只要进程是 OWNED_LIVE 或 OWNERSHIP_UNKNOWN，在生命周期与防重门禁中均视为需要保护/拦截。"""
    return evaluate_process_ownership(record) in (
        ProcessOwnershipStatus.OWNED_LIVE,
        ProcessOwnershipStatus.OWNERSHIP_UNKNOWN,
    )


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
    - 当前进程若无法取得合法 birth_identity，直接抛错 fail-closed，禁止创建无法证明身份的锁；
    - 记录 {"pid": ..., "birth_identity": ..., "token": ...}；
    - 仅当 mtime 超时且经 is_pid_alive_and_matched 确认 owner 已死，在 OS advisory lock 临界区内安全回收；
    - 退出时仅当 token 匹配才删除锁文件。
    """
    my_pid = os.getpid()
    my_birth = get_process_birth_identity(my_pid)
    if not my_birth:
        raise RuntimeSupervisorStateError(f"当前进程 (PID={my_pid}) 无法获取 birth_identity，禁止获取运行时锁 (fail-closed)")

    lock_dir = runtime_records_dir(season_id)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / ".runtime.lock"
    reclaim_path = lock_dir / ".runtime.lock.reclaim"
    token = uuid.uuid4().hex
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
            break
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
            time.sleep(0.02)

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
        ownership = evaluate_process_ownership(r)
        if r.state in ("starting", "healthy"):
            if ownership == ProcessOwnershipStatus.NOT_LIVE:
                r.state = "stopped"
                r.stopped_at = r.stopped_at or now
                if r.exit_code is None:
                    r.exit_code = -1  # 标记外部终止/未知退出
                mutated = True
            elif ownership == ProcessOwnershipStatus.OWNERSHIP_UNKNOWN:
                r.state = "failed"
                r.failure_reason = "进程缺少 birth_identity 无法验证归属 (ownership_unknown)"
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

    is_active = any(evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE for r in records)

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

            # R14-C Repair 2 (P1-4): 本次 spawn epoch 的 execution session。
            # 只有实际 spawn 了至少一个进程才创建新 session（幂等 re-spawn 不刷新）。
            from runtime.execution_session import (
                ExecutionSessionParticipant,
                RuntimeExecutionSession,
                make_execution_session_id,
                persist_execution_session_locked,
            )

            spawned_any = any(
                participant.account_id not in records_by_account
                or not record_has_live_owned_process(records_by_account[participant.account_id])
                for participant in plan.spawnable_participants
            )
            session_id: str | None = None
            _pending_session_participants: dict[str, ExecutionSessionParticipant] = {}
            if spawned_any:
                session_id = make_execution_session_id(season_id, manifest.manifest_id)

            # 启动每个 spawnable 参与者
            for participant in plan.spawnable_participants:
                aid = participant.account_id
                existing = records_by_account.get(aid)

                # 防重复 spawn：只要底层 OS PID 存活 (OWNED_LIVE 或 OWNERSHIP_UNKNOWN) 即阻断
                if existing and record_has_live_owned_process(existing):
                    continue

                runtime_id = make_runtime_id(season_id, manifest.manifest_id, aid)
                cmd = build_process_command(
                    participant,
                    project_root=project_root,
                    python_executable=py_exe,
                    custom_command_builder=custom_command_builder,
                    session_id=session_id,
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

                # R14-C Repair 2 (P1-4): 记录 execution session participant
                # （无论 spawn 成败——成败由进程事实反映，session 冻结的是
                # "这一 epoch 试图启动谁、用什么 provenance"）。
                if session_id is not None:
                    _pending_session_participants[aid] = ExecutionSessionParticipant(
                        account_id=aid,
                        controller_type=str(participant.controller_type),
                        execution_provenance=participant.execution_provenance.model_dump(),
                        runtime_id=runtime_id,
                        pid=rec.pid,
                        birth_identity=rec.birth_identity,
                    )

            # R14-C Repair 2 (P1-4): 本次 spawn epoch 结束——原子持久化
            # execution session（冻结 season/manifest/authority hash/participant set）
            if session_id is not None and _pending_session_participants:
                persist_execution_session_locked(
                    RuntimeExecutionSession(
                        session_id=session_id,
                        season_id=season_id,
                        manifest_id=manifest.manifest_id,
                        season_authority_hash=manifest.season_authority_hash,
                        participants=list(_pending_session_participants.values())
                        + [
                            ExecutionSessionParticipant(
                                account_id=p.account_id,
                                controller_type=str(p.controller_type),
                                execution_provenance=p.execution_provenance.model_dump(),
                            )
                            for p in plan.unmanaged_participants
                        ],
                        created_at=now_iso(),
                    )
                )

            final_records = list(records_by_account.values())
            save_process_records_locked(season_id, final_records)

            is_active = any(evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE for r in final_records)
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
    """幂等停止指定赛季的所有运行中本地进程（仅对 OWNED_LIVE 进程发信号；OWNERSHIP_UNKNOWN 抛错拒绝杀进程）。"""
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

        # 检查是否存在 OWNERSHIP_UNKNOWN 记录：坚决禁止发信号误杀无辜进程，必须抛出错误并要求人工介入
        unknown_records = [r for r in records if evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNERSHIP_UNKNOWN]
        if unknown_records:
            unknown_pids = ", ".join(f"{r.account_id}(PID={r.pid})" for r in unknown_records)
            raise SeasonRegistryError(
                f"赛季 {season_id} 存在缺失 birth_identity 的疑似残留 PID ({unknown_pids})，"
                "为防止误杀系统无关进程，禁止执行自动停机，请手动确认该 PID 状态 (ownership_unknown)"
            )

        for r in records:
            ownership = evaluate_process_ownership(r)
            if ownership == ProcessOwnershipStatus.OWNED_LIVE and r.pid:
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
                while evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE and (time.time() - start_wait < timeout_seconds):
                    time.sleep(0.05)

                # 3. 超时强制 SIGKILL
                if evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE:
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
                    while evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE and (time.time() - start_kill_wait < 0.5):
                        time.sleep(0.05)

                # 4. 最终状态判定与真实退出码审计
                if evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE:
                    r.state = "failed"
                    r.failure_reason = "停止进程失败: SIGKILL 后进程仍存活"
                else:
                    r.state = "stopped"
                    r.stopped_at = now
                    r.exit_code = -9 if used_sigkill else 0

        save_process_records_locked(season_id, records)
        is_active = any(evaluate_process_ownership(r) == ProcessOwnershipStatus.OWNED_LIVE for r in records)

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

    基于 OS 事实严密判定：若有任何 OWNED_LIVE 或 OWNERSHIP_UNKNOWN 进程，抛出 SeasonRegistryError (HTTP 409)。
    """
    records = sync_process_states_locked(season_id)
    live_records = [r for r in records if record_has_live_owned_process(r)]
    if live_records:
        accounts = ", ".join(r.account_id for r in live_records)
        raise SeasonRegistryError(
            f"赛季 {season_id} 当前仍有正在运行或归属未知的本地模型进程 ({accounts})，"
            "请先停止或处理进程后再进行生命周期变更 (runtime_active)"
        )
