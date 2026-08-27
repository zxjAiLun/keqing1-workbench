"""R14-C Repair 2/3 (P1-4 / P1-2 / P2): Runtime Execution Session — R14-B → R14-C evidence bridge.

每次 ``spawn_season_runtime`` 产生新的 spawn epoch 时，supervisor 原子持久化一个
``keqing.runtime.execution_session.v1`` 文件，冻结：

- ``season_id`` / ``manifest_id`` / ``season_authority_hash``；
- 精确的 participant set（account + frozen provenance + controller，typed schema）；
- 每个 spawnable participant 的进程事实（PID/birth_identity/runtime_id）。

R14-C intake 只有从这份 execution session 反查到 Tenhou log，且 schema、season、
manifest、authority hash、participant set、exact log observation 全部完全一致时
才能产生 RuntimeMatchBinding。旧 Play-with-you ``binding.json`` 保持 legacy，
不再被当成 R14 runtime evidence。

capture sink（轻量、进程安全）：每个 bot worker 把自己观察到的 Tenhou log
归属写到 ``<session_dir>/logs/<log-id>/<account-id>.json``——每
``(session, log, observer)`` 独立、不可覆盖：同一三元组重复写幂等，冲突写
fail-closed。一个 runtime session 连续产生多局时每局证据都保留。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workbench.participants.schemas import ControllerType, FrozenExecutionProvenance
from workbench.runtime.resolver.ladder import ladder_data_root

EXECUTION_SESSION_SCHEMA = "keqing.runtime.execution_session.v1"
OBSERVED_LOG_SCHEMA = "keqing.runtime.observed_log.v1"


# ---------------------------------------------------------------------------
# Schema (typed, fail-closed on read)
# ---------------------------------------------------------------------------

class ExecutionSessionParticipant(BaseModel):
    """Execution session 中单个 participant 的冻结事实（typed provenance）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    controller_type: ControllerType
    execution_provenance: FrozenExecutionProvenance
    runtime_id: str | None = None
    pid: int | None = None
    birth_identity: str | None = None


class RuntimeExecutionSession(BaseModel):
    """一次 R14-B spawn epoch 的不可变执行会话记录（typed schema, fail-closed）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["keqing.runtime.execution_session.v1"] = EXECUTION_SESSION_SCHEMA
    session_id: str
    season_id: str
    manifest_id: str
    season_authority_hash: str
    participants: list[ExecutionSessionParticipant] = Field(default_factory=list)
    created_at: str


class ObservedRuntimeLog(BaseModel):
    """单个 worker 观察到的 Tenhou log 归属记录（typed schema, fail-closed）。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["keqing.runtime.observed_log.v1"] = OBSERVED_LOG_SCHEMA
    session_id: str
    account_id: str
    tenhou_log_id: str
    tenhou_log_url: str = ""
    observed_at: str


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


def _safe_segment(raw: str) -> str:
    return re.sub(r"[^0-9a-zA-Z_\-]", "_", str(raw))


def observed_log_dir(session_id: str, tenhou_log_id: str) -> Path:
    """单局观测证据目录：每 (session, log) 一个目录，每 observer 一个文件。"""
    return execution_session_dir(session_id) / "logs" / _safe_segment(tenhou_log_id)


def observed_log_file(session_id: str, tenhou_log_id: str, account_id: str) -> Path:
    return observed_log_dir(session_id, tenhou_log_id) / f"{_safe_segment(account_id)}.json"


# ---------------------------------------------------------------------------
# Persistence (session file; called by supervisor inside runtime_lock)
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
    return path


def load_execution_session(session_id: str) -> RuntimeExecutionSession | None:
    """读取 execution session；不存在或损坏（含 schema 不符）返回 None。"""
    path = execution_session_file(session_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return RuntimeExecutionSession.model_validate(raw)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Capture sink (per-worker, per-log, non-overwritable single-file writes)
# ---------------------------------------------------------------------------

def record_observed_log(
    session_id: str,
    account_id: str,
    *,
    tenhou_log_id: str,
    tenhou_log_url: str | None = None,
    observed_at: str | None = None,
) -> Path:
    """记录单个 bot worker 对某一局 Tenhou log 的观测归属。

    每 ``(session, log, observer)`` 独立文件——同一 runtime session 连续产生
    多局时每局证据都保留，绝不覆盖：

    - 写前验证 session 存在且 account 属于该 session（fail-closed）；
    - 同一三元组重复写幂等（内容一致时 no-op）；
    - 内容冲突（同 account 同 log 不同 session/url 归属）→ ValueError。
    """
    session = load_execution_session(session_id)
    if session is None:
        raise ValueError(
            f"execution session {session_id} 不存在或已损坏，无法记录观测证据"
        )
    if account_id not in {p.account_id for p in session.participants}:
        raise ValueError(
            f"账号 {account_id} 不属于 execution session {session_id}，"
            "不能以该 session 名义记录观测证据"
        )

    from workbench.participants.paths import now_iso

    record = ObservedRuntimeLog(
        session_id=session_id,
        account_id=account_id,
        tenhou_log_id=str(tenhou_log_id),
        tenhou_log_url=str(tenhou_log_url or ""),
        observed_at=observed_at or now_iso(),
    )
    path = observed_log_file(session_id, str(tenhou_log_id), account_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = ObservedRuntimeLog.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"观测证据文件损坏，拒绝覆盖: {path}"
            ) from exc
        if (
            existing.session_id == record.session_id
            and existing.account_id == record.account_id
            and existing.tenhou_log_id == record.tenhou_log_id
        ):
            # 幂等重复写（URL/时间戳可能因重放略有不同——归属性质不变）
            return path
        raise ValueError(
            f"观测证据冲突: {path} 已存在不同归属的记录，拒绝覆盖"
        )

    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(record.model_dump(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_observed_log(
    session_id: str,
    tenhou_log_id: str,
    account_id: str,
) -> ObservedRuntimeLog | None:
    """读取单个观测证据；不存在返回 None。"""
    path = observed_log_file(session_id, tenhou_log_id, account_id)
    if not path.exists():
        return None
    try:
        return ObservedRuntimeLog.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def session_observer_ids(session_id: str, tenhou_log_id: str) -> list[str]:
    """返回已对该 log 记录观测证据的 account id 列表（sorted，原始 account id）。

    文件名经过 ``_safe_segment`` 转义，account id 从证据文件内容中读取。
    """
    log_dir = observed_log_dir(session_id, tenhou_log_id)
    if not log_dir.is_dir():
        return []
    observers: set[str] = set()
    for path in log_dir.glob("*.json"):
        try:
            record = ObservedRuntimeLog.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if record.tenhou_log_id == str(tenhou_log_id):
            observers.add(record.account_id)
    return sorted(observers)


def find_sessions_for_log(tenhou_log_id: str) -> list[RuntimeExecutionSession]:
    """反查包含该 log 观测证据的所有（去重）execution session。

    多个不同 session 对同一 log 都有证据 = ambiguous，由调用方 fail-closed。
    """
    root = execution_sessions_root()
    if not root.is_dir():
        return []
    sessions: dict[str, RuntimeExecutionSession] = {}
    for session_dir in root.iterdir():
        if not session_dir.is_dir():
            continue
        # 只看该 session 下这一 log 的证据目录
        log_dir = session_dir / "logs" / _safe_segment(tenhou_log_id)
        if not log_dir.is_dir() or not any(log_dir.glob("*.json")):
            continue
        session = load_execution_session(session_dir.name)
        if session is not None and session.session_id not in sessions:
            sessions[session.session_id] = session
    return list(sessions.values())


__all__ = [
    "EXECUTION_SESSION_SCHEMA",
    "OBSERVED_LOG_SCHEMA",
    "ExecutionSessionParticipant",
    "ObservedRuntimeLog",
    "RuntimeExecutionSession",
    "execution_session_dir",
    "execution_session_file",
    "execution_sessions_root",
    "find_sessions_for_log",
    "load_execution_session",
    "load_observed_log",
    "make_execution_session_id",
    "observed_log_dir",
    "observed_log_file",
    "persist_execution_session_locked",
    "record_observed_log",
    "session_observer_ids",
]
