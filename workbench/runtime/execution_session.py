"""R14-C Repair 2 (P1-4): Runtime Execution Session — R14-B → R14-C evidence bridge.

每次 ``spawn_season_runtime`` 产生新的 spawn epoch 时，supervisor 原子持久化一个
``keqing.runtime.execution_session.v1`` 文件，冻结：

- ``season_id`` / ``manifest_id`` / ``season_authority_hash``；
- 精确的 participant set（account + frozen provenance + controller）；
- 每个 spawnable participant 的进程事实（PID/birth_identity/runtime_id）。

R14-C intake 只有从这份 execution session 反查到 Tenhou log，且 schema、season、
manifest、authority hash、participant set 全部完全一致时才能产生
RuntimeMatchBinding。旧 Play-with-you ``binding.json`` 保持 legacy，不再被
当成 R14 runtime evidence。

capture sink（轻量、进程安全）：每个 bot worker 把自己观察到的 Tenhou log
归属写到 ``<session_dir>/logs/<account>.json``（原子写、单文件、无跨进程状态），
R14-C 用它把 log_id 反查到 execution session。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workbench.runtime.resolver.ladder import ladder_data_root

EXECUTION_SESSION_SCHEMA = "keqing.runtime.execution_session.v1"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class ExecutionSessionParticipant(BaseModel):
    """Execution session 中单个 participant 的冻结事实。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    controller_type: str
    execution_provenance: dict[str, Any]
    runtime_id: str | None = None
    pid: int | None = None
    birth_identity: str | None = None


class RuntimeExecutionSession(BaseModel):
    """一次 R14-B spawn epoch 的不可变执行会话记录。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EXECUTION_SESSION_SCHEMA
    session_id: str
    season_id: str
    manifest_id: str
    season_authority_hash: str
    participants: list[ExecutionSessionParticipant] = Field(default_factory=list)
    created_at: str


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def execution_sessions_root() -> Path:
    """所有 execution session 的 canonical 根目录。"""
    return ladder_data_root() / "runtime" / "execution_sessions"


def execution_session_dir(session_id: str) -> Path:
    safe = re.sub(r"[^0-9a-zA-Z_\-]", "_", str(session_id))
    return execution_sessions_root() / safe


def execution_session_file(session_id: str) -> Path:
    return execution_session_dir(session_id) / "session.json"


def execution_session_logs_dir(session_id: str) -> Path:
    return execution_session_dir(session_id) / "logs"


# ---------------------------------------------------------------------------
# Persistence (called by supervisor inside runtime_lock)
# ---------------------------------------------------------------------------

def make_execution_session_id(season_id: str, manifest_id: str) -> str:
    """每次 spawn epoch 的唯一 session id（时间戳 + 随机后缀）。"""
    ts = time.strftime("%Y%m%d%H%M%S")
    import uuid

    clean_manifest = manifest_id.replace(":", "_")
    return f"rtx_{season_id}_{clean_manifest}_{ts}_{uuid.uuid4().hex[:8]}"


def persist_execution_session_locked(session: RuntimeExecutionSession) -> Path:
    """原子持久化 execution session（调用方持有 runtime_lock）。"""
    path = execution_session_file(session.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{session.session_id}.tmp")
    tmp.write_text(
        json.dumps(session.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    logs_dir = execution_session_logs_dir(session.session_id)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return path


def load_execution_session(session_id: str) -> RuntimeExecutionSession | None:
    """读取 execution session；不存在或损坏返回 None。"""
    path = execution_session_file(session_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeExecutionSession.model_validate(raw)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Capture sink (per-worker, process-safe single-file writes)
# ---------------------------------------------------------------------------

def record_observed_log(
    session_id: str,
    account_id: str,
    *,
    tenhou_log_id: str,
    tenhou_log_url: str | None = None,
    observed_at: str | None = None,
) -> Path:
    """单个 bot worker 记录自己观察到的 Tenhou log 归属（原子单文件写）。

    供 R14-C 从 log_id 反查 execution session。每个 worker 只写自己的
    ``<session>/logs/<account>.json``，无跨进程共享状态。
    """
    from workbench.participants.paths import now_iso

    logs_dir = execution_session_logs_dir(session_id)
    logs_dir.mkdir(parents=True, exist_ok=True)
    safe_account = re.sub(r"[^0-9a-zA-Z_\-]", "_", str(account_id))
    path = logs_dir / f"{safe_account}.json"
    payload = {
        "schema": "keqing.runtime.observed_log.v1",
        "session_id": session_id,
        "account_id": account_id,
        "tenhou_log_id": str(tenhou_log_id),
        "tenhou_log_url": str(tenhou_log_url or ""),
        "observed_at": observed_at or now_iso(),
    }
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def find_sessions_for_log(tenhou_log_id: str) -> list[RuntimeExecutionSession]:
    """从所有 execution session 的 worker log 观测记录反查包含该 log 的 session。"""
    root = execution_sessions_root()
    if not root.is_dir():
        return []
    sessions: list[RuntimeExecutionSession] = []
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        logs_dir = session_dir / "logs"
        if not logs_dir.is_dir():
            continue
        for path in logs_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(payload.get("tenhou_log_id") or "") != str(tenhou_log_id):
                continue
            session = load_execution_session(session_dir.name)
            if session is not None:
                sessions.append(session)
    return sessions


__all__ = [
    "EXECUTION_SESSION_SCHEMA",
    "ExecutionSessionParticipant",
    "RuntimeExecutionSession",
    "execution_session_dir",
    "execution_session_file",
    "execution_session_logs_dir",
    "execution_sessions_root",
    "find_sessions_for_log",
    "load_execution_session",
    "make_execution_session_id",
    "persist_execution_session_locked",
    "record_observed_log",
]
