# -*- coding: utf-8 -*-
"""participants registry：账号与模型身份/产物的 CRUD + JSON 持久化。

- 账号（``accounts.json``）：稳定 ``account_id`` 不可变；显示名可改；历史对局只引用 ID。
- 模型（``models.json``）：ModelIdentity（逻辑模型）+ ModelArtifact（物理产物，可选）。
  同一账号可绑定不同模型版本（identity/artifact 分离）。
写操作持全局 ``data_lock``；读操作直接读盘（文件小，保持简单）。
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path

from .paths import data_root, now_iso, read_json, write_json, data_lock
from .schemas import (
    ACCOUNTS_SCHEMA,
    MODELS_SCHEMA,
    Account,
    AccountCreate,
    AccountUpdate,
    ControllerType,
    ModelArtifact,
    ModelArtifactCreate,
    ModelIdentity,
    ModelIdentityCreate,
    ModelIdentityUpdate,
)

_write_lock = threading.RLock()


def _accounts_path() -> Path:
    return data_root() / "accounts.json"


def _models_path() -> Path:
    return data_root() / "models.json"


# ---------------------------------------------------------------------------
# 内部读写
# ---------------------------------------------------------------------------

def _read_accounts() -> dict:
    return read_json(_accounts_path(), {"accounts": []})


def _read_models() -> dict:
    return read_json(_models_path(), {"identities": [], "artifacts": []})


def _default_controller_for(account_type: str) -> ControllerType:
    if account_type == "human":
        return "human_ui"
    if account_type == "managed_bot":
        return "local_model"
    return "external_agent"


def _slugify(name: str) -> str:
    out = []
    for ch in name.strip().lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch.isspace():
            out.append("-")
    slug = "".join(out).strip("-") or "account"
    return f"account:{slug}"


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------

def list_accounts(*, enabled: bool | None = None, q: str | None = None) -> list[Account]:
    accounts = _read_accounts()["accounts"]
    result = []
    for raw in accounts:
        account = Account.model_validate(raw)
        if enabled is not None and account.enabled != enabled:
            continue
        if q and q.strip().lower() not in account.display_name.lower() and q.strip() not in account.account_id:
            continue
        result.append(account)
    result.sort(key=lambda a: (a.account_id,))
    return result


def get_account(account_id: str) -> Account | None:
    for raw in _read_accounts()["accounts"]:
        if raw.get("account_id") == account_id:
            return Account.model_validate(raw)
    return None


def account_exists(account_id: str) -> bool:
    return any(raw.get("account_id") == account_id for raw in _read_accounts()["accounts"])


def create_account(payload: AccountCreate) -> Account:
    with _write_lock, data_lock():
        return create_account_locked(payload)


def _validate_model_binding(model_identity_id: str | None, account_id: str) -> None:
    """R11-D Repair：Account.model_identity_id 引用完整性（fail closed）。

    - 绑定必须指向已存在的 ModelIdentity；
    - account-scoped identity（account_id 非空）只能被其绑定账号引用；
    - global identity（account_id=None）可被任意 Account 反向引用。
    """
    if model_identity_id is None:
        return
    identity = get_model_identity(model_identity_id)
    if identity is None:
        raise ValueError(f"模型身份不存在: {model_identity_id}")
    if identity.account_id is not None and identity.account_id != account_id:
        raise ValueError(
            f"账号 {account_id} 不能引用账号专属身份 {model_identity_id}（属于 {identity.account_id}）"
        )


def create_account_locked(payload: AccountCreate) -> Account:
    """锁内创建账号：调用方已持有 data_lock 时使用（intake 事务 / 恢复路径）。"""
    account_id = payload.account_id or _slugify(payload.display_name)
    _validate_model_binding(payload.model_identity_id, account_id)
    now = now_iso()
    store = _read_accounts()
    if any(raw.get("account_id") == account_id for raw in store["accounts"]):
        raise ValueError(f"account_id 已存在: {account_id}")
    account = Account(
        account_id=account_id,
        display_name=payload.display_name,
        account_type=payload.account_type,
        default_controller=payload.default_controller or _default_controller_for(payload.account_type),
        model_identity_id=payload.model_identity_id,
        avatar=payload.avatar,
        note=payload.note,
        migrated_from_replay=payload.migrated_from_replay,
        created_at=now,
        updated_at=now,
    )
    store["accounts"].append(account.model_dump())
    write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
    return account


def create_account_if_missing_locked(payload: AccountCreate) -> Account:
    """锁内幂等创建（pending 恢复用）：已存在则校验与冻结创建意图一致，不一致报冲突。"""
    account_id = payload.account_id or _slugify(payload.display_name)
    existing = get_account(account_id)
    if existing is None:
        return create_account_locked(payload)
    expected_controller = payload.default_controller or _default_controller_for(payload.account_type)
    if (
        existing.display_name != payload.display_name
        or existing.account_type != payload.account_type
        or existing.default_controller != expected_controller
        or not existing.enabled
    ):
        raise ValueError(
            f"pending 恢复冲突：账号 {account_id} 与创建意图不一致（display_name/account_type/controller/enabled）"
        )
    return existing


def update_account(account_id: str, payload: AccountUpdate) -> Account:
    with _write_lock, data_lock():
        store = _read_accounts()
        for idx, raw in enumerate(store["accounts"]):
            if raw.get("account_id") != account_id:
                continue
            raw_fields = payload.model_dump(exclude_unset=True)
            new_binding = raw_fields.get("model_identity_id", None)
            if "model_identity_id" in raw_fields:
                _validate_model_binding(new_binding, account_id)
            current = Account.model_validate(raw)
            updated = current.model_copy(
                update={
                    "display_name": payload.display_name if payload.display_name is not None else current.display_name,
                    "enabled": payload.enabled if payload.enabled is not None else current.enabled,
                    "default_controller": payload.default_controller if payload.default_controller is not None else current.default_controller,
                    "model_identity_id": raw_fields.get("model_identity_id", current.model_identity_id),
                    "avatar": payload.avatar if "avatar" in raw_fields else current.avatar,
                    "note": payload.note if "note" in raw_fields else current.note,
                    "updated_at": now_iso(),
                }
            )
            store["accounts"][idx] = updated.model_dump()
            write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
            return updated
    raise KeyError(f"account not found: {account_id}")


def delete_account(account_id: str, *, referenced: bool = False) -> dict:
    """删除账号。被对局引用时软删（enabled=False），否则硬删。"""
    with _write_lock, data_lock():
        store = _read_accounts()
        remaining = [raw for raw in store["accounts"] if raw.get("account_id") != account_id]
        if len(remaining) == len(store["accounts"]):
            raise KeyError(f"account not found: {account_id}")
        if referenced:
            for raw in store["accounts"]:
                if raw.get("account_id") == account_id:
                    raw["enabled"] = False
                    raw["updated_at"] = now_iso()
            write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
            return {"deleted": False, "disabled": True, "account_id": account_id}
        store["accounts"] = remaining
        write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
        return {"deleted": True, "disabled": False, "account_id": account_id}


def delete_account_guarded(account_id: str, reference_checker) -> dict:
    """统一���锁删除（P1-2 防 TOCTOU）：锁内检查引用 → 决定软删/硬删 → 写入。

    ``reference_checker`` 由调用方提供（覆盖当前 match + revision + identity 引用），
    在同一个 ``data_lock`` 临界区内求值，避免"检查"与"写入"之间被并发插入引用。
    """
    with _write_lock, data_lock():
        store = _read_accounts()
        if not any(raw.get("account_id") == account_id for raw in store["accounts"]):
            raise KeyError(f"account not found: {account_id}")
        referenced = bool(reference_checker())
        if referenced:
            for raw in store["accounts"]:
                if raw.get("account_id") == account_id:
                    raw["enabled"] = False
                    raw["updated_at"] = now_iso()
            write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
            return {"deleted": False, "disabled": True, "account_id": account_id}
        store["accounts"] = [raw for raw in store["accounts"] if raw.get("account_id") != account_id]
        write_json(_accounts_path(), ACCOUNTS_SCHEMA, store)
        return {"deleted": True, "disabled": False, "account_id": account_id}


# ---------------------------------------------------------------------------
# 模型身份 / 产物
# ---------------------------------------------------------------------------

def list_models() -> list[ModelIdentity]:
    store = _read_models()
    by_id = {raw["model_identity_id"]: raw for raw in store["identities"]}
    for art_raw in store["artifacts"]:
        ident = by_id.get(art_raw.get("model_identity_id"))
        if ident is not None:
            ident.setdefault("artifacts", []).append(art_raw)
    result = [ModelIdentity.model_validate(raw) for raw in store["identities"]]
    for identity in result:
        identity.artifacts.sort(key=lambda a: a.created_at, reverse=True)
    result.sort(key=lambda m: (m.model_identity_id,))
    return result


def get_model_identity(model_identity_id: str) -> ModelIdentity | None:
    for identity in list_models():
        if identity.model_identity_id == model_identity_id:
            return identity
    return None


def create_model_identity(payload: ModelIdentityCreate) -> ModelIdentity:
    identity_id = payload.model_identity_id or f"model:{_slugify(payload.label).replace('account:', '')}"
    now = now_iso()
    with _write_lock, data_lock():
        if payload.account_id is not None and not account_exists(payload.account_id):
            raise ValueError(f"绑定的账号不存在: {payload.account_id}")
        store = _read_models()
        if any(raw.get("model_identity_id") == identity_id for raw in store["identities"]):
            raise ValueError(f"model_identity_id 已存在: {identity_id}")
        identity_raw = {
            "model_identity_id": identity_id,
            "label": payload.label,
            "kind": payload.kind,
            "account_id": payload.account_id,
            "is_current": True,
            "created_at": now,
            "retired_at": None,
            "note": payload.note,
        }
        store["identities"].append(identity_raw)
        if payload.artifact_path:
            art_id = f"{identity_id}@{uuid.uuid4().hex[:6]}"
            store["artifacts"].append(
                {
                    "model_artifact_id": art_id,
                    "label": Path(payload.artifact_path).name,
                    "model_identity_id": identity_id,
                    "artifact_path": payload.artifact_path,
                    "hash": None,
                    "is_current": True,
                    "created_at": now,
                    "retired_at": None,
                }
            )
        write_json(_models_path(), MODELS_SCHEMA, store)
    identity = get_model_identity(identity_id)
    assert identity is not None
    return identity


def add_model_artifact(identity_id: str, payload: ModelArtifactCreate) -> ModelArtifact:
    now = now_iso()
    art_id = payload.model_artifact_id or f"{identity_id}@{uuid.uuid4().hex[:6]}"
    with _write_lock, data_lock():
        store = _read_models()
        if not any(raw.get("model_identity_id") == identity_id for raw in store["identities"]):
            raise KeyError(f"model identity not found: {identity_id}")
        if any(raw.get("model_artifact_id") == art_id for raw in store["artifacts"]):
            raise ValueError(f"model_artifact_id 已存在: {art_id}")
        # 新增产物设为 current，其余降级
        for raw in store["artifacts"]:
            if raw.get("model_identity_id") == identity_id and raw.get("is_current"):
                raw["is_current"] = False
        store["artifacts"].append(
            {
                "model_artifact_id": art_id,
                "label": payload.label,
                "model_identity_id": identity_id,
                "artifact_path": payload.artifact_path,
                "hash": None,
                "is_current": True,
                "created_at": now,
                "retired_at": None,
            }
        )
        write_json(_models_path(), MODELS_SCHEMA, store)
    return ModelArtifact.model_validate(store["artifacts"][-1])


def set_current_model_artifact(identity_id: str, artifact_id: str) -> ModelArtifact:
    """把指定 artifact 设为 current（同一 identity 其余产物全部降级，R10 UX Repair P1-4）。"""
    with _write_lock, data_lock():
        store = _read_models()
        if not any(raw.get("model_identity_id") == identity_id for raw in store["identities"]):
            raise KeyError(f"model identity not found: {identity_id}")
        target = next(
            (
                raw
                for raw in store["artifacts"]
                if raw.get("model_artifact_id") == artifact_id
                and raw.get("model_identity_id") == identity_id
            ),
            None,
        )
        if target is None:
            raise KeyError(f"model artifact not found: {artifact_id}")
        for raw in store["artifacts"]:
            if raw.get("model_identity_id") == identity_id:
                raw["is_current"] = raw.get("model_artifact_id") == artifact_id
        write_json(_models_path(), MODELS_SCHEMA, store)
    return ModelArtifact.model_validate(target)


def update_model_identity(identity_id: str, payload: ModelIdentityUpdate) -> ModelIdentity:
    with _write_lock, data_lock():
        if payload.account_id is not None and not account_exists(payload.account_id):
            raise ValueError(f"绑定的账号不存在: {payload.account_id}")
        store = _read_models()
        for idx, raw in enumerate(store["identities"]):
            if raw.get("model_identity_id") != identity_id:
                continue
            # R11-D Repair：global identity 已被多个 Account 反向引用时，禁止
            # 转成 account-scoped——否则其他绑定账号会瞬间失效。
            raw_fields = payload.model_dump(exclude_unset=True)
            new_account_id = raw_fields.get("account_id", None)
            if "account_id" in raw_fields and new_account_id is not None and raw.get("account_id") is None:
                referencing = [
                    acc["account_id"]
                    for acc in _read_accounts()["accounts"]
                    if acc.get("model_identity_id") == identity_id
                ]
                if referencing:
                    raise ValueError(
                        f"模型身份 {identity_id} 已被账号 {sorted(referencing)} 反向引用，"
                        "不能转成账号专属身份"
                    )
            dump = raw_fields
            raw.update(dump)
            store["identities"][idx] = raw
            write_json(_models_path(), MODELS_SCHEMA, store)
            return ModelIdentity.model_validate(raw)
    raise KeyError(f"model identity not found: {identity_id}")


def identity_belongs_to_account(identity_id: str | None, account_id: str) -> bool:
    """R11-D 语义：Account ↔ ModelIdentity 严格双向兼容。

    - ``identity_id=None``：模型证据缺省 → 不约束（普通人工导入不受影响）。
    - account-scoped identity（``account_id`` 非空）：只兼容该账号。
    - global identity（``account_id=None``）：**不再是 wildcard**——兼容性由
      Account 的 ``model_identity_id`` 反向绑定决定
      （``account.model_identity_id == identity.model_identity_id``）。
    """
    if identity_id is None:
        return True
    identity = get_model_identity(identity_id)
    if identity is None:
        return False
    if identity.account_id is not None:
        return identity.account_id == account_id
    account = get_account(account_id)
    if account is None:
        return False
    return account.model_identity_id == identity.model_identity_id


def identity_references_account(account_id: str) -> bool:
    """模型身份是否绑定到该账号（删除保护）。"""
    return any(identity.account_id == account_id for identity in list_models())


def artifact_belongs_to_identity(identity_id: str | None, artifact_id: str | None) -> bool:
    # artifact 非空时必须指定 identity（不允许悬空 artifact）
    if artifact_id is not None and identity_id is None:
        return False
    if identity_id is None or artifact_id is None:
        return True
    identity = get_model_identity(identity_id)
    if identity is None:
        return False
    return any(art.model_artifact_id == artifact_id for art in identity.artifacts)
