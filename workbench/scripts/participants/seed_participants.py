# -*- coding: utf-8 -*-
"""R10-A 迁移：从 ladder 注册表种子账号/模型，可选回填历史 replay 到统一账本。

用法：
    python workbench/scripts/participants/seed_participants.py [--dry-run] [--backfill-replays]

- 账号/模型 upsert 按稳定 id，幂等；re-run 无副作用。
- ``--backfill-replays``：读 ``data/replays/*/meta.json``（player_names +
  final_scores）建 ``source="imported"`` 对局；未知玩家名自动建
  ``external_bot/manual_only`` 占位账号（``migrated_from_replay=true``）。
- ``migration_state.json`` 记录已摄入的 registry 文件名与 replay 指纹（mtime+size），
  防止重复摄入。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_WORKBENCH_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _WORKBENCH_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_WORKBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from participants import ledger, registry  # noqa: E402
from participants.paths import data_root, now_iso, read_json, write_json  # noqa: E402
from participants.schemas import (  # noqa: E402
    MIGRATION_STATE_SCHEMA,
    AccountCreate,
    MatchCreate,
    MatchSeat,
    ModelIdentityCreate,
)


def _resolve_registry_paths() -> list[Path]:
    paths: list[Path] = []
    in_repo = _WORKBENCH_ROOT / "configs" / "ladder" / "seasons"
    if in_repo.exists():
        paths.extend(sorted(in_repo.glob("*.json")))
    env_dir = os.environ.get("KEQING_LADDER_CONFIG_DIR")
    if env_dir:
        ext = Path(env_dir)
        if ext.exists():
            paths.extend(sorted(ext.glob("*.json")))
    return paths


def _model_meta(model_id: str) -> tuple[str, str]:
    """(account_type, default_controller) 按 model_id 推导。"""
    if model_id == "human":
        return "human", "human_ui"
    if model_id == "ext_mortal":
        return "external_bot", "external_agent"
    return "managed_bot", "local_model"


def _content_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_state(state: dict) -> dict:
    """旧版 migration state 升级：seeded_registries 若为 list，则重置为 dict 重新处理。"""
    seeded = state.get("seeded_registries")
    if not isinstance(seeded, dict):
        state["seeded_registries"] = {}
    return state


def _mark_ingested(state: dict, replay_id: str, fingerprint: dict) -> None:
    """标记已摄入（或永久跳过），并清理同 replay 的历史 skipped 记录（P2）。"""
    state.setdefault("ingested_replays", {})[replay_id] = fingerprint
    state.setdefault("skipped_replays", {}).pop(replay_id, None)


def seed_registries(state: dict, *, dry_run: bool) -> list[str]:
    created_accounts = 0
    created_models = 0
    seeded = _normalize_state(state).setdefault("seeded_registries", {})  # {str(path): content_sha256}
    for path in _resolve_registry_paths():
        key = str(path)
        digest = _content_sha256(path)
        if seeded.get(key) == digest:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = raw.get("models", [])
        for model in models:
            model_id = model.get("model_id") or ""
            account_type, default_controller = _model_meta(model_id)
            for acc in model.get("accounts", []):
                account_id = acc.get("account_id")
                if not account_id:
                    continue
                if registry.account_exists(account_id):
                    continue
                if not dry_run:
                    registry.create_account(
                        AccountCreate(
                            account_id=account_id,
                            display_name=acc.get("display_name") or account_id,
                            account_type=account_type,
                            default_controller=default_controller,
                        )
                    )
                created_accounts += 1
            # 模型身份：全局（account_id=None），artifact 取 checkpoint
            if model_id and not registry.get_model_identity(model_id):
                if not dry_run:
                    registry.create_model_identity(
                        ModelIdentityCreate(
                            model_identity_id=model_id,
                            label=model_id,
                            kind="local_model" if account_type == "managed_bot" else "external_agent",
                            artifact_path=model.get("checkpoint"),
                        )
                    )
                created_models += 1
        if not dry_run:
            seeded[key] = digest
    return [f"registry {created_accounts} 账号 / {created_models} 模型"]


def _game_length(kyoku_count: int) -> str:
    if kyoku_count <= 4:
        return "tonpu"
    return "hanchan"


def _slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-")
    return slug or "unknown"


def _name_hash(name: str) -> str:
    """稳定短 hash（sha256 前 6 位），避免不同原名撞出同一占位账号。"""
    import hashlib

    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]


def _replays_dir() -> Path:
    """回放目录：默认 data/replays，可用 KEQING_PARTICIPANT_REPLAYS_DIR 覆盖（测试用）。"""
    env = os.environ.get("KEQING_PARTICIPANT_REPLAYS_DIR")
    if env:
        return Path(env)
    return data_root().parent / "replays"


def backfill_replays(state: dict, *, dry_run: bool) -> list[str]:
    replays_dir = _replays_dir()
    if not replays_dir.exists():
        return ["无 replay 目录"]
    ingested = state.setdefault("ingested_replays", {})
    report: list[str] = []
    for meta_path in sorted(replays_dir.glob("*/meta.json")):
        stat = meta_path.stat()
        fingerprint = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
        replay_id = meta_path.parent.name
        if ingested.get(replay_id) == fingerprint:
            continue
        if ledger.list_matches(source_ref=replay_id).total > 0:
            _mark_ingested(state, replay_id, fingerprint)
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        player_names = meta.get("player_names") or []
        final_scores = meta.get("final_scores") or []
        if len(player_names) != 4 or len(final_scores) != 4:
            report.append(f"{replay_id}: 跳过（玩家/分数不完整）")
            _mark_ingested(state, replay_id, fingerprint)
            continue
        if player_names == ["E", "S", "W", "N"]:
            report.append(f"{replay_id}: 跳过（无真实玩家名）")
            _mark_ingested(state, replay_id, fingerprint)
            continue
        # 座位账号：按显示名匹配，未知名自动建占位账号
        seats: list[MatchSeat] = []
        for seat, name in enumerate(player_names):
            account = None
            matched = [a for a in registry.list_accounts() if a.display_name == name]
            if len(matched) == 1:
                account = matched[0]
            elif len(matched) > 1:
                report.append(f"{replay_id}: 显示名 '{name}' 命中多个账号，跳过（需要 alias map）")
                seats = []
                break
            else:
                placeholder_id = f"imp_{_slugify_name(name)}-{_name_hash(name)}"
                account = registry.get_account(placeholder_id)
                if account is None and not dry_run:
                    registry.create_account(
                        AccountCreate(
                            account_id=placeholder_id,
                            display_name=name,
                            account_type="external_bot",
                            default_controller="manual_only",
                            note=f"migrated_from_replay:{replay_id}",
                            migrated_from_replay=True,
                        )
                    )
                account = registry.get_account(placeholder_id)
            if account is not None:
                seats.append(MatchSeat(seat=seat, account_id=account.account_id))
        if len(seats) != 4:
            # 可修复错误（歧义/占位失败）不得标记为已摄入，记 skipped_replays 允许重试
            report.append(f"{replay_id}: 跳过（可重试：占位账号不足 / 名字歧义）")
            skipped = state.setdefault("skipped_replays", {})
            skipped[replay_id] = {"fingerprint": fingerprint, "reason": "unresolved_seats"}
            continue
        if not dry_run:
            ledger.create_match(
                MatchCreate(
                    occurred_at=meta.get("created_at") or now_iso(),
                    game_length=_game_length(int(meta.get("kyoku_count") or 0)),
                    source="imported",
                    source_ref=replay_id,
                    data_completeness="result_only",
                    replay_id=replay_id,
                    seats=seats,
                    final_scores=final_scores,
                ),
                registry,
            )
        _mark_ingested(state, replay_id, fingerprint)
        report.append(f"{replay_id}: 已回填")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="R10-A 迁移：种子账号/模型，可选回填 replay")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    parser.add_argument("--backfill-replays", action="store_true", help="回填 data/replays 到统一账本")
    args = parser.parse_args()

    state_path = data_root() / "migration_state.json"
    state = _normalize_state(read_json(state_path, {"seeded_registries": {}, "ingested_replays": {}}))

    print("== 种子注册表 ==")
    for line in seed_registries(state, dry_run=args.dry_run):
        print(" ", line)

    if args.backfill_replays:
        print("== 回填 replay ==")
        for line in backfill_replays(state, dry_run=args.dry_run):
            print(" ", line)

    if not args.dry_run:
        write_json(state_path, MIGRATION_STATE_SCHEMA, state)
        print(f"已写入 migration_state（{len(state.get('seeded_registries', []))} registry, "
              f"{len(state.get('ingested_replays', {}))} replay）")
    else:
        print("dry-run：未写入任何数据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
