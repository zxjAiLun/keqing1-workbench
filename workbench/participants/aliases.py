# -*- coding: utf-8 -*-
"""外部账号 alias registry — 身份解析的唯一依据。

存储 ``artifacts/participants/aliases.json``（schema keqing.participant.aliases.v1）。

范围语义：
- ``global``：平台级稳定别名（如 provider=tenhou, external_id=keqing1 → nick@01）。
  只有用户确认长期稳定时才允许注册为 global。
- ``session``：某次 play-with-you 会话产生的原始名 → 该会话呼出的具体 bot 与模型
  版本（session-scoped，绝不提升为全局规则，因为下一轮 NoName-1 可能是另一模型）。
- ``match``：仅当前对局有效的解析（unresolved 默认落在此范围）。
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from .paths import data_root, now_iso, atomic_write_text, data_lock
from .schemas import ALIASES_SCHEMA, ExternalAlias, ExternalAliasCreate

_write_lock = threading.RLock()


def _aliases_path() -> Path:
    return data_root() / "aliases.json"


def _read_aliases() -> dict:
    path = _aliases_path()
    if not path.exists():
        return {"schema": ALIASES_SCHEMA, "aliases": []}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _write_aliases(store: dict) -> None:
    import json

    atomic_write_text(_aliases_path(), json.dumps(store, ensure_ascii=False))


def list_aliases(
    *,
    provider: str | None = None,
    account_id: str | None = None,
    scope: str | None = None,
) -> list[ExternalAlias]:
    store = _read_aliases()
    result = []
    for raw in store.get("aliases", []):
        alias = ExternalAlias.model_validate(raw)
        if provider and alias.provider != provider:
            continue
        if account_id and alias.account_id != account_id:
            continue
        if scope and alias.scope != scope:
            continue
        result.append(alias)
    result.sort(key=lambda a: (a.provider, a.external_id, a.scope))
    return result


def get_alias(alias_id: str) -> ExternalAlias | None:
    for alias in list_aliases():
        if alias.alias_id == alias_id:
            return alias
    return None


def delete_alias(alias_id: str) -> bool:
    """删除别名（启动失败回滚用）。返回是否删除。"""
    with _write_lock, data_lock():
        store = _read_aliases()
        raw_list = store.setdefault("aliases", [])
        remaining = [raw for raw in raw_list if raw.get("alias_id") != alias_id]
        if len(remaining) == len(raw_list):
            return False
        store["aliases"] = remaining
        _write_aliases(store)
        return True


def register_alias(payload: ExternalAliasCreate) -> ExternalAlias:
    """注册别名。同一 (provider, external_id, scope, session_id, external_match_id) 视为同一键，upsert。"""
    with _write_lock, data_lock():
        return register_alias_locked(payload)


def register_alias_locked(payload: ExternalAliasCreate) -> ExternalAlias:
    """锁内注册别名：调用方已持有 data_lock 时使用（intake 事务 / 恢复路径）。"""
    now = now_iso()
    store = _read_aliases()
    raw_list = store.setdefault("aliases", [])
    key = (payload.provider, payload.external_id, payload.scope, payload.session_id, payload.external_match_id)
    for idx, raw in enumerate(raw_list):
        existing = ExternalAlias.model_validate(raw)
        if (
            existing.provider == key[0]
            and existing.external_id == key[1]
            and existing.scope == key[2]
            and existing.session_id == key[3]
            and existing.external_match_id == key[4]
        ):
            updated = existing.model_copy(
                update={
                    "display_name": payload.display_name,
                    "account_id": payload.account_id,
                    "model_identity_id": payload.model_identity_id,
                    "model_artifact_id": payload.model_artifact_id,
                    "confidence": payload.confidence,
                    "updated_at": now,
                }
            )
            raw_list[idx] = updated.model_dump()
            _write_aliases(store)
            return updated
    alias = ExternalAlias(
        alias_id=f"alias_{uuid.uuid4().hex[:8]}",
        provider=payload.provider,
        external_id=payload.external_id,
        display_name=payload.display_name,
        account_id=payload.account_id,
        model_identity_id=payload.model_identity_id,
        model_artifact_id=payload.model_artifact_id,
        scope=payload.scope,
        session_id=payload.session_id,
        external_match_id=payload.external_match_id,
        confidence=payload.confidence,
        created_at=now,
        updated_at=now,
    )
    raw_list.append(alias.model_dump())
    _write_aliases(store)
    return alias


def resolve_candidates(
    provider: str,
    external_id: str,
    *,
    session_id: str | None = None,
    external_match_id: str | None = None,
) -> list[ExternalAlias]:
    """按优先级返回候选别名：session（须匹配本次会话）→ global → match（须同一 external_match_id）。

    调用方据此决定：唯一命中自动采用；多候选/只有 unresolved → 人工确认。
    """
    candidates: list[ExternalAlias] = []
    for a in list_aliases(provider=provider):
        if a.external_id != external_id:
            continue
        if a.scope == "session":
            # session-scoped 绑定只在同一会话内有效
            if session_id and a.session_id == session_id:
                candidates.append(a)
            continue
        if a.scope == "match":
            # match-scoped 解析只作用于同一 external_match_id，绝不影响其他牌谱
            if external_match_id and a.external_match_id == external_match_id:
                candidates.append(a)
            continue
        if a.scope == "global" and a.confidence == "confirmed":
            candidates.append(a)

    def _rank(a: ExternalAlias) -> tuple[int, str]:
        if session_id and a.scope == "session" and a.session_id == session_id:
            return (0, a.account_id)
        if a.scope == "global":
            return (1, a.account_id)
        if a.scope == "match":
            return (2, a.account_id)
        return (3, a.account_id)

    candidates.sort(key=_rank)
    return candidates


def alias_references_account(account_id: str) -> bool:
    """账号是否被 alias 引用（删除保护）。所有 scope 都算——stale session/match
    alias 若指向已删除账号，会导致摄入时产生悬空引用。"""
    return any(a.account_id == account_id for a in list_aliases())


__all__ = [
    "list_aliases",
    "get_alias",
    "register_alias",
    "resolve_candidates",
    "alias_references_account",
]
