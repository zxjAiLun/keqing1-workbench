"""R11-E1：Season Registry 生命周期管理。

Canonical runtime registry 是 ``KEQING_LADDER_CONFIG_DIR`` 下的 season JSON
（Workbench 是生命周期/校验/UI 的唯一 owner；仓库 configs 只是 seed/example）。

生命周期与不变量（E1 锁死）：
- status: ``draft → running → completed → archived``，不允许倒退；
- 同一时刻**最多一个** default，且 default 必须 ``running``；
  当前 default 赛季结束/归档时自动摘除 default（新的当前赛季由"设为当前"显式指定）；
- ``completed / archived`` 历史配置锁定：普通生命周期操作不得破坏其内容；
- 新建赛季：``status=draft``、``default=False``、空参赛阵容（enrollment 由 E2 编辑）；
- 所有 mutation（create/set_status/set_default）在跨进程文件锁内完成
  read-validate-modify-write，防止并发产生双 default / 生命周期倒退。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .ladder import (
    SEASON_SCHEMA,
    SeasonRegistryError,
    get_season_config,
    list_season_configs,
    read_registry,
)

SEASON_STATUSES = ("draft", "running", "completed", "archived")

# season_id 安全字符集：字母/数字开头，后续允许字母/数字/下划线/连字符，最长 64。
SEASON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# status → 允许迁移到的状态（生命周期单向）
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"running"},
    "running": {"completed", "archived"},
    "completed": {"archived"},
    "archived": set(),
}

DEFAULT_SCORING: dict[str, Any] = {
    "system": "tenhou_rank_progression",
    "version": "v1",
    "game_length": "hanchan",
}

_REGISTRY_LOCK_FILE = ".season-registry.lock"


def _validate_season_id(season_id: str) -> str:
    season_id = (season_id or "").strip()
    if not SEASON_ID_RE.match(season_id):
        raise ValueError(
            "season_id 只能以字母/数字开头，包含字母/数字/下划线/连字符，最长 64 字符"
        )
    return season_id


def _season_path(configs_dir: Path, season_id: str) -> Path:
    """返回 registry 目录内的 season 文件路径（含 containment 兜底校验）。

    绝不接受路径穿越：season 只能写成 registry 目录下的一个普通 JSON。
    """
    season_id = _validate_season_id(season_id)
    path = (configs_dir / f"{season_id}.json").resolve()
    root = configs_dir.resolve()
    if path.parent != root or path.name != f"{season_id}.json":
        raise ValueError("season_id 非法：必须落在 registry 目录内")
    return path


@contextlib.contextmanager
def _registry_lock(configs_dir: Path):
    """跨进程 registry mutation 锁（O_CREAT|O_EXCL + stale 回收）。

    canonical registry 是外部持久数据（未来 publisher/admin script 也可能写），
    因此用文件锁而不是进程内锁。锁内完成 read-validate-modify-write。
    """
    configs_dir.mkdir(parents=True, exist_ok=True)
    lock_path = configs_dir / _REGISTRY_LOCK_FILE
    deadline = time.monotonic() + 8.0
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            acquired = True
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 30.0:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"season registry 锁获取超时: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _report_dir_for(configs_dir: Path, season_id: str) -> str:
    """新赛季 report_dir 占位（发布时由 publisher 原子切换为真实快照目录）。"""
    return str((configs_dir.parent / "seasons" / season_id / "snapshots" / "placeholder").resolve())


def create_season(
    configs_dir: Path,
    *,
    season_id: str,
    title: str | None = None,
    scoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建赛季（status=draft, default=False, 空阵容）。

    校验：season_id 安全字符集 + 不得与现有赛季冲突；mutation 全程持锁。
    """
    season_id = _validate_season_id(season_id)
    with _registry_lock(configs_dir):
        existing = [s for s in list_season_configs(configs_dir) if s.get("season_id") == season_id]
        if existing:
            raise ValueError(f"赛季已存在: {season_id}")

        payload: dict[str, Any] = {
            "schema": SEASON_SCHEMA,
            "season_id": season_id,
            "title": title or f"Season {season_id}",
            "status": "draft",
            "default": False,
            "report_dir": _report_dir_for(configs_dir, season_id),
            "scoring": {**DEFAULT_SCORING, **(scoring or {})},
            "ingest": {
                "sources_root": f"seasons/{season_id}/sources",
                "participants": {"enabled": True, "exclusive": True},
            },
            "models": [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }
        path = _season_path(configs_dir, season_id)
        _atomic_write_json(path, payload)
        return read_registry(path)


def set_season_status(configs_dir: Path, season_id: str, status: str) -> dict[str, Any]:
    """迁移赛季状态（draft→running→completed→archived）。

    - 非法迁移 → 409（SeasonRegistryError）；
    - 当前 default 赛季离开 running 时自动摘除 default（需要新赛季时显式"设为当前"）。
    - mutation 全程持锁（防止并发基于旧状态校验通过后互相覆盖）。
    """
    status = (status or "").strip()
    if status not in SEASON_STATUSES:
        raise SeasonRegistryError(f"非法赛季状态: {status!r}（可选: {SEASON_STATUSES}）")
    with _registry_lock(configs_dir):
        season = get_season_config(configs_dir, season_id)
        current = str(season.get("status") or "draft")
        allowed = _STATUS_TRANSITIONS.get(current, set())
        if status not in allowed:
            raise SeasonRegistryError(f"赛季 {season_id} 状态迁移非法: {current} → {status}")

        updated = dict(season)
        updated["status"] = status
        if status != "running" and updated.get("default") is True:
            # 不变量：default 必须 running——结束/归档当前赛季即摘除 default
            updated["default"] = False
        updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        path = _season_path(configs_dir, season_id)
        _atomic_write_json(path, updated)
        return read_registry(path)


def set_default_season(configs_dir: Path, season_id: str) -> dict[str, Any]:
    """把赛季设为当前 default（同一时刻最多一个；只有 running 可设为 default）。

    自动摘除其他赛季的 default 标记。mutation 全程持锁，防止两个 writer
    并发读到"无 default"后各自写入造成双 default。
    """
    with _registry_lock(configs_dir):
        season = get_season_config(configs_dir, season_id)
        if str(season.get("status") or "draft") != "running":
            raise SeasonRegistryError(
                f"只有 running 赛季可以设为当前（{season_id} 当前状态: {season.get('status')}）"
            )

        for other in list_season_configs(configs_dir):
            if other.get("season_id") == season_id:
                continue
            if other.get("default") is True:
                other = dict(other)
                other["default"] = False
                other["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                _atomic_write_json(_season_path(configs_dir, str(other["season_id"])), other)

        updated = dict(season)
        updated["default"] = True
        updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        path = _season_path(configs_dir, season_id)
        _atomic_write_json(path, updated)
        return read_registry(path)


def get_season(configs_dir: Path, season_id: str) -> dict[str, Any]:
    return get_season_config(configs_dir, season_id)


# ---------------------------------------------------------------------------
# R11-E2：参赛阵容（enrollment）
# ---------------------------------------------------------------------------

def _freeze_checkpoint(identity) -> str | None:
    """新 group 的 checkpoint 冻结：ModelIdentity 有唯一 current artifact 时
    冻结其 artifact_path；否则 None（"赛季要求哪个 checkpoint"独立于
    Participants 当前 artifact 的后续变化）。"""
    if identity is None:
        return None
    current = [a for a in identity.artifacts if a.is_current and a.artifact_path]
    if len(current) == 1:
        return current[0].artifact_path
    return None


def _build_enrollment_groups(
    season: dict[str, Any],
    account_ids: list[str],
    accounts_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """按 Participants Account 构建赛季成员分组（models[].accounts 只是
    season snapshot/投影，不是模型归属的 canonical source）。

    - human Account → model_id="human" 组；
    - AI Account → 按其 Account.model_identity_id 归组（无绑定 → fail closed）；
    - 旧 legacy group（无 model_identity_id）：成员推导唯一一致时自动 backfill，
      不一致 → fail closed，绝不猜；
    - 新 AI group：model_id=model_identity_id（稳定），checkpoint 冻结唯一 current
      artifact；已有 group 的 checkpoint 绝不因 Participants 变化而漂移。
    """
    def _backfill(group: dict[str, Any], members: list[Any]) -> str | None:
        """legacy group 无 model_identity_id：从成员推导，唯一一致才返回。"""
        identities = {a.model_identity_id for a in members if a.model_identity_id}
        if len(identities) == 1:
            return next(iter(identities))
        if len(identities) > 1:
            raise ValueError(
                f"赛季 group {group.get('model_id')} 推导出多个模型身份 "
                f"({sorted(identities)})，无法确定归属，需人工修复注册表"
            )
        return None

    from participants import registry as participants_registry

    get_identity = participants_registry.get_model_identity

    # 1. 拷贝既有 group 结构（保留顺序与 checkpoint 等字段）
    groups = [dict(m) for m in season.get("models", []) if isinstance(m, dict)]
    group_index: dict[str, dict[str, Any]] = {}
    for group in groups:
        group["accounts"] = [dict(a) for a in group.get("accounts", [])]
        existing_members = [accounts_by_id[a["account_id"]] for a in group["accounts"] if a.get("account_id") in accounts_by_id]
        key = group.get("model_identity_id")
        if key is None:
            # legacy group：backfill（唯一一致才允许）
            key = _backfill(group, existing_members)
            if key is not None:
                group["model_identity_id"] = key
        group_index[group.get("model_id")] = group

    # 2. 按参与者账号重排：human → human 组；AI → 身份组
    for aid in account_ids:
        account = accounts_by_id[aid]
        if account.account_type == "human":
            model_key = "human"
        else:
            identity_id = account.model_identity_id
            if not identity_id:
                raise ValueError(f"AI 账号 {aid} 未绑定模型身份，无法加入赛季")
            identity = get_identity(identity_id)
            if identity is None:
                raise ValueError(f"账号 {aid} 绑定的模型身份不存在: {identity_id}")
            model_key = identity_id  # 新 group 用稳定 model_identity_id 作 model_id

        # 已有 legacy group 若模型归属一致则复用（保持旧 model_id）
        matched = None
        for group in groups:
            g_identity = group.get("model_identity_id")
            if g_identity == model_key or (g_identity is None and model_key == "human" and group.get("model_id") == "human"):
                matched = group
                break
        if matched is None:
            identity = None if model_key == "human" else get_identity(model_key)
            matched = {
                "model_id": "human" if model_key == "human" else model_key,
                "model_identity_id": None if model_key == "human" else model_key,
                "accounts": [],
            }
            checkpoint = _freeze_checkpoint(identity)
            if checkpoint is not None:
                matched["checkpoint"] = checkpoint
            groups.append(matched)

        member_ids = {a["account_id"] for a in matched["accounts"]}
        if aid not in member_ids:
            matched["accounts"].append({"account_id": aid})

    # 3. 移除空组（除保留的 legacy human 组语义外的空组清理），并按 model_id 排序
    groups = [g for g in groups if g["accounts"]]
    groups.sort(key=lambda g: str(g.get("model_id", "")))
    return groups


def set_season_enrollment(
    configs_dir: Path,
    season_id: str,
    account_ids: list[str],
    *,
    participants_registry=None,
) -> dict[str, Any]:
    """R11-E2：按 Participants Account 编辑赛季参赛阵容。

    权限矩阵（backend 硬 gate，不信任前端）：
    - draft    → 可增加 / 移除；
    - running  → 只允许增加（不可移除 / 换组）；
    - completed / archived → 只读（拒绝修改）。

    校验：账号存在；human 只能进 human 组；AI 必须已有有效模型绑定；
    同一 Account 只能出现一次。
    """
    from participants import registry as default_registry

    participants_registry = participants_registry or default_registry
    account_ids = [str(a).strip() for a in account_ids]
    if len(set(account_ids)) != len(account_ids):
        raise ValueError("参赛账号不能重复")

    with _registry_lock(configs_dir):
        season = get_season_config(configs_dir, season_id)
        status = str(season.get("status") or "draft")
        if status in ("completed", "archived"):
            raise SeasonRegistryError("completed/archived 赛季不可修改参赛阵容")

        accounts_by_id = {a.account_id: a for a in participants_registry.list_accounts()}
        missing = [aid for aid in account_ids if aid not in accounts_by_id]
        if missing:
            raise ValueError(f"账号不存在: {sorted(missing)}")

        if status == "running":
            existing = {
                acc["account_id"]
                for m in season.get("models", []) if isinstance(m, dict)
                for acc in m.get("accounts", []) if isinstance(acc, dict)
            }
            removed = existing - set(account_ids)
            if removed:
                raise SeasonRegistryError(
                    f"running 赛季只能增加账号，不能移除现有成员: {sorted(removed)}"
                )

        groups = _build_enrollment_groups(season, account_ids, accounts_by_id)
        updated = dict(season)
        updated["models"] = groups
        updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        path = _season_path(configs_dir, season_id)
        _atomic_write_json(path, updated)
        return read_registry(path)
