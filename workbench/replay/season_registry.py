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
import shutil
import time
from pathlib import Path
from typing import Any

from .ladder import (
    SEASON_SCHEMA,
    SeasonRegistryError,
    get_season_config,
    list_season_configs,
    read_registry,
    validate_registry_payload,
)

SEASON_STATUSES = ("draft", "running", "completed", "archived")

# season_id 安全字符集：字母/数字开头，后续允许字母/数字/下划线/连字符，最长 64。
SEASON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# status → 允许迁移到的状态（生命周期）。
# ``completed`` 是"已结束/暂停"状态，允许管理员 reopen（completed → running）；
# ``archived`` 永久锁定，永不 reopen。
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"running"},
    "running": {"completed", "archived"},
    "completed": {"archived", "running"},
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
    del configs_dir
    return f"seasons/{season_id}/snapshots/placeholder"


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
    """迁移赛季状态（draft→running ⇄ completed→archived）。

    - 非法迁移 → 409（SeasonRegistryError）；
    - ``completed → running`` 是管理员 reopen（修正误结束）：
      - archived 永不 reopen；
      - reopen 不自动抢占当前赛季（default 保持 false，由用户显式"设为当前"）；
      - 写入 ``reopened_at`` 审计字段；
    - 当前 default 赛季离开 running 时自动摘除 default（需要新赛季时显式"设为当前"）；
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
        if current == "completed" and status == "running":
            # R11-E Post-release Repair：reopen 审计 + 不自动抢占当前
            updated["reopened_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
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


def delete_season(
    configs_dir: Path,
    season_id: str,
    *,
    participants_data_root: Path | None = None,
) -> dict[str, Any]:
    """R11 post-release：删除赛季——服务级事务（防 TOCTOU）。

    顺序取得固定锁（ledger ``data_lock`` → registry lock），**在锁内**：
    1. 重新读取 season；
    2. 重新校验 default==false 且 status != running
       （防止"删除前刚被 reopen 成 running"的竞态）；
    3. 重新 count Match Ledger 引用（不信任调用方传入的旧数字；
       data_lock 已排除进行中的 intake，不会产生 dangling Match）；
    4. reference > 0 → 拒绝；
    5. 删除 season registry JSON + Workbench 认领的派生数据
       （dirty marker / projection lock / ``ladder/seasons/<id>`` 快照目录——
       该目录必须是 Workbench 布局内的固定路径，绝不按 registry report_dir
       递归删除任意路径）；清理失败记录 warning，不静默。
    """
    from participants import ledger as participants_ledger
    from participants.paths import data_lock as participants_data_lock

    warnings: list[str] = []
    with participants_data_lock(), _registry_lock(configs_dir):
        # crash-recovery：此前进程崩溃可能留下 pending intake/ledger 事务
        # （match.season_id 已冻结但 matches.jsonl 尚未落账）。先恢复再 count，
        # 否则该 pending Match 会变成指向已删除赛季的 dangling reference。
        participants_ledger.recover_pending_transaction_locked()
        season = get_season_config(configs_dir, season_id)
        status = str(season.get("status") or "draft")
        if season.get("default") is True:
            raise SeasonRegistryError("当前正式赛季不可删除（请先设其他赛季为当前）")
        if status == "running":
            raise SeasonRegistryError("running 赛季不可删除（请先结束/归档）")
        referenced = participants_ledger.count_matches_for_season_locked(season_id)
        if referenced > 0:
            raise SeasonRegistryError(
                f"该赛季仍被 {referenced} 场历史对局引用，拒绝删除"
            )

        path = _season_path(configs_dir, season_id)
        path.unlink(missing_ok=True)

        from workbench.runtime.resolver import data_path as _data_path, ladder_data_root

        participants_root = participants_data_root or _data_path("participants")
        dirty = participants_root / f"ladder_dirty_{season_id}.json"
        try:
            dirty.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"dirty marker 清理失败: {exc}")
        lock = participants_root / "projection_locks" / f"{season_id}.lock"
        try:
            lock.unlink(missing_ok=True)
        except OSError as exc:
            warnings.append(f"projection lock 清理失败: {exc}")
        owned_snapshots = ladder_data_root() / "seasons" / season_id
        if owned_snapshots.is_dir():
            try:
                shutil.rmtree(owned_snapshots)
            except OSError as exc:
                warnings.append(f"快照目录清理失败: {exc}")

        result: dict[str, Any] = {"season_id": season_id, "deleted": True}
        if warnings:
            result["cleanup_warnings"] = warnings
        return result


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


def _account_target_key(account) -> str:
    """Account 的赛季分组键：human → "human"；AI → 其 model_identity_id。"""
    if account.account_type == "human":
        return "human"
    identity_id = account.model_identity_id
    if not identity_id:
        raise ValueError(f"AI 账号 {account.account_id} 未绑定模型身份，无法加入赛季")
    return identity_id


def _build_enrollment_groups(
    season: dict[str, Any],
    account_ids: list[str],
    accounts_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    """按 Participants Account **重建**赛季成员分组（R11-E2 Repair）。

    membership 完全由 requested account_ids 决定——旧成员若不在请求中即被移除
    （draft 可删）；每个 group 的 metadata（model_id / model_identity_id /
    checkpoint / 其他 snapshot 字段）只在身份匹配时复用，绝不残留旧 membership。

    - human Account → model_id="human" 组；
    - AI Account → 按其 Account.model_identity_id 归组（无绑定 → fail closed）；
    - 旧 legacy group（无 model_identity_id）：成员推导唯一一致时自动 backfill，
      不一致 → fail closed，绝不猜；
    - 新 AI group：model_id=model_identity_id（稳定），checkpoint 冻结唯一 current
      artifact；已有 group 的 checkpoint 绝不因 Participants 变化而漂移；
    - 成员冻结 display_name（历史格式）。
    """
    from participants import registry as participants_registry

    get_identity = participants_registry.get_model_identity

    # 1. 保留旧 group metadata（membership 全部清空）
    legacy_groups: list[dict[str, Any]] = []
    for m in season.get("models", []) if isinstance(season.get("models"), list) else []:
        group = dict(m)
        group["accounts"] = []
        existing_members = [
            accounts_by_id[a["account_id"]]
            for a in (m.get("accounts") or []) if isinstance(a, dict) and a.get("account_id") in accounts_by_id
        ]
        key = group.get("model_identity_id")
        if key is None:
            # legacy group：成员推导唯一一致才允许 backfill
            identities = {a.model_identity_id for a in existing_members if a.model_identity_id}
            if len(identities) > 1:
                raise ValueError(
                    f"赛季 group {group.get('model_id')} 推导出多个模型身份 "
                    f"({sorted(identities)})，无法确定归属，需人工修复注册表"
                )
            if len(identities) == 1:
                group["model_identity_id"] = next(iter(identities))
        legacy_groups.append(group)

    # 2. 按 requested account_ids 重建 membership
    for aid in account_ids:
        account = accounts_by_id[aid]
        target_key = _account_target_key(account)

        matched = None
        for group in legacy_groups:
            g_identity = group.get("model_identity_id")
            if g_identity == target_key or (target_key == "human" and group.get("model_id") == "human"):
                matched = group
                break
        if matched is None:
            identity = None if target_key == "human" else get_identity(target_key)
            matched = {
                "model_id": "human" if target_key == "human" else target_key,
                "model_identity_id": None if target_key == "human" else target_key,
                "accounts": [],
            }
            checkpoint = _freeze_checkpoint(identity)
            if checkpoint is not None:
                matched["checkpoint"] = checkpoint
            legacy_groups.append(matched)

        matched["accounts"].append(
            {"account_id": account.account_id, "display_name": account.display_name}
        )

    # 3. 删除空组，并按 model_id 排序
    groups = [g for g in legacy_groups if g["accounts"]]
    groups.sort(key=lambda g: str(g.get("model_id", "")))
    return groups


def _check_running_invariants(
    season: dict[str, Any],
    account_ids: list[str],
    accounts_by_id: dict[str, Any],
) -> None:
    """R11-E2 Repair：running 赛季 group-level diff。

    - 现有成员 ID 必须仍在请求中（不可移除）；
    - 现有成员的赛季 group 不得改变（按当前 Participants 绑定推导的目标组
      与 season snapshot 组一致才算通过；绑定漂移 → 拒绝，绝不换组）。
    """
    requested = set(account_ids)
    requested_target = {aid: _account_target_key(accounts_by_id[aid]) for aid in account_ids}

    existing: dict[str, tuple[str | None, str | None]] = {}
    for m in season.get("models", []) if isinstance(season.get("models"), list) else []:
        group_identity = m.get("model_identity_id")
        members = [a for a in (m.get("accounts") or []) if isinstance(a, dict) and a.get("account_id")]
        if group_identity is None:
            identities = {
                accounts_by_id[a["account_id"]].model_identity_id
                for a in members if a["account_id"] in accounts_by_id
            }
            if len(identities) > 1:
                raise ValueError(
                    f"赛季 group {m.get('model_id')} 推导出多个模型身份 "
                    f"({sorted(identities)})，无法确定归属，需人工修复注册表"
                )
            if len(identities) == 1:
                group_identity = next(iter(identities))
        for a in members:
            aid = a["account_id"]
            if aid not in accounts_by_id:
                raise ValueError(f"赛季成员账号不存在: {aid}")
            key = group_identity if group_identity else ("human" if m.get("model_id") == "human" else None)
            existing[aid] = (key, m.get("model_id"))

    for aid, (group_key, _model_id) in existing.items():
        if aid not in requested:
            raise SeasonRegistryError(f"running 赛季只能增加账号，不能移除现有成员: {aid}")
        target = requested_target[aid]
        if group_key != target:
            raise SeasonRegistryError(
                f"running 赛季不允许换组：账号 {aid} 当前在 {group_key or '未分组'}，"
                f"按 Participants 应为 {target}"
            )


def set_season_enrollment(
    configs_dir: Path,
    season_id: str,
    account_ids: list[str],
    *,
    participants_registry=None,
) -> dict[str, Any]:
    """R11-E2：按 Participants Account 编辑赛季参赛阵容。

    权限矩阵（backend 硬 gate，不信任前端）：
    - draft    → 可增加 / 移除（membership 完全按请求重建）；
    - running  → 只允许增加（不可移除 / 换组，group-level diff）；
    - completed / archived → 只读（拒绝修改）。

    写前验证：最终 payload 先过 ``validate_registry_payload``（重复账号/重复
    model/schema/default 合同等），通过后才原子写——绝不先写坏 canonical JSON
    再在 read_registry 阶段报错。
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
            _check_running_invariants(season, account_ids, accounts_by_id)

        groups = _build_enrollment_groups(season, account_ids, accounts_by_id)
        updated = dict(season)
        updated["models"] = groups
        updated["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        # 写前验证：非法 payload 绝不落盘
        validate_registry_payload(updated)
        path = _season_path(configs_dir, season_id)
        _atomic_write_json(path, updated)
        return read_registry(path)
