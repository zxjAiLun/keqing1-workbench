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
