#!/usr/bin/env python3
"""Publish an atomic Ladder snapshot for a dynamic (dev) season.

hidden staging build -> compact snapshot -> validation -> manifest ->
atomic materialize -> atomic registry switch.

- 每次发布生成一个全新的不可变快照目录，绝不原地覆写在线目录；
- 完整报告先在隐藏 staging/build 构建，再从 build 提取线上所需产物，
  在 staging/snapshot 形成紧凑快照（默认不含 account_logs 派生副本）；
- 校验通过后 ``os.replace`` 将 snapshot staging 原子改名为最终快照；
- registry 通过 ``.tmp + os.replace`` 原子切换 ``report_dir``；
- registry 切换失败时删除本次已 materialize 的孤儿快照；
- API 保持纯只读，本脚本由训练线在固定间隔触发。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from project_data import encode_ladder_path, ladder_data_root, resolve_ladder_path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_WORKBENCH_DIR = _REPO_ROOT / "workbench"
if str(_WORKBENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_DIR))

from replay import ladder  # noqa: E402

MANIFEST_SCHEMA = "keqing.ladder.snapshot.v1"

# 线上 runtime/UI 实际消费、快照必须保留的产物
SNAPSHOT_REQUIRED_FILES = (
    "account_summary.json",
    "account_ledger.jsonl",
    "rating_curve.csv",
)
# 建议保留的派生报告（便于人工查看，runtime 不依赖）
SNAPSHOT_RECOMMENDED_FILES = (
    "account_summary.csv",
    "account_summary.md",
    "per_game_results.csv",
    "detailed_stats.json",
    "detailed_stats.md",
)

# 默认保留的最近有效快照数；0 表示禁用自动清理
DEFAULT_RETAIN_SNAPSHOTS = 24

# report 派生逻辑契约版本：未来算法更新时递增，避免相同源日志复用旧产物
# v2：版本化计分引擎（tenhou_4p_ranked / legacy fixed profile）取代固定七段常量
# v3：R12-A——ingest 路径接入 libriichi 详细统计（旧快照的统计字段全为 None，
#     相同输入指纹也会强制重建一次以回填行为指标与覆盖率字段）
# v4：R12-A Repair——reach_accepted 按 upstream convlog 状态机成立（宣言牌被
#     荣和/局在宣言牌后结束 → 不成立，下一次 take/call 前才注入）。该修复改变
#     detailed-stats 派生语义但 Ledger / artifact 不变，必须强制重建所有 v3 快照。
SNAPSHOT_BUILD_CONTRACT_VERSION = "v4"


class PublishError(Exception):
    """发布流程错误（completed 赛季、目录冲突等）。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True,
                        help="动态赛季注册表 JSON 路径（如 keqing-data/ladder/registries/dev-live.json）")
    parser.add_argument("--log-dir", action="append", type=Path, default=[],
                        help="输入 mjai 日志目录，可重复；ingest 赛季不需要（使用 season.ingest.sources_root）")
    parser.add_argument("--snapshot-root", type=Path, default=None,
                        help="快照根目录；默认 <KEQING_LADDER_DATA_ROOT>/seasons/<season_id>/snapshots")
    parser.add_argument("--mortal-root", type=Path, default=Path("third_party/Mortal"))
    parser.add_argument("--platform-model-label", default=None,
                        help="强制所有座位为 MODEL@01-04（临时演示用）")
    parser.add_argument("--rank-points", default="90,45,0,-135")
    parser.add_argument("--preserve-log-dir-order", action="store_true")
    parser.add_argument("--interleave-log-dirs", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只构建并校验，不切换 registry")
    parser.add_argument("--keep-account-logs", action="store_true",
                        help="调试用：在快照中保留 account_logs/ 派生副本（默认丢弃）")
    parser.add_argument("--retain-snapshots", type=int, default=DEFAULT_RETAIN_SNAPSHOTS,
                        help=f"成功发布后保留的最近有效快照数（默认 {DEFAULT_RETAIN_SNAPSHOTS}；0 表示禁用自动清理）")
    return parser.parse_args()


def load_registry(registry_path: Path) -> dict[str, Any]:
    try:
        return ladder.read_registry(registry_path)
    except ladder.SeasonRegistryError as exc:
        raise PublishError(str(exc)) from exc


def default_snapshot_root(data_root: Path | None, season_id: str) -> Path:
    base = data_root or ladder_data_root()
    return base / "seasons" / season_id / "snapshots"


def build_snapshot(
    *,
    log_dirs: list[Path],
    snapshot_dir: Path,
    mortal_root: Path,
    platform_model_label: str | None,
    rank_points: str,
    preserve_log_dir_order: bool,
    interleave_log_dirs: bool,
    scoring_config: dict[str, Any] | None = None,
    season: dict[str, Any] | None = None,
    ingest_root: Path | None = None,
    build_report: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """在全新快照目录构建完整 report（staging）。

    - ingest 赛季（season 带 ingest 配置）：走多来源 LadderMatch 合并 + 全量重放；
    - 否则：走原有 mjai 日志 report builder。
    """
    if ingest_root is not None and season is not None:
        from replay import ladder_ingest

        report = ladder_ingest.build_ingest_report(
            season=season,
            sources_root=ingest_root,
            output_dir=snapshot_dir,
            # R12-A：详细统计复用同一 libriichi Stat 后端
            mortal_root=mortal_root,
        )
    else:
        from replay import build_platform_account_report as account_report

        builder = build_report or account_report.build_report
        report = builder(
            log_dirs=log_dirs,
            output_dir=snapshot_dir,
            mortal_root=mortal_root,
            platform_model_label=platform_model_label,
            # 复用仓库严格解析：恰好四个顺位值，错误立即拒绝
            rank_points=account_report.parse_rank_points(rank_points),
            preserve_log_dir_order=preserve_log_dir_order,
            interleave_log_dirs=interleave_log_dirs,
            scoring_config=scoring_config,
        )
    if not isinstance(report, dict):
        raise PublishError("构建脚本未返回 report 字典")
    return report


def write_manifest(
    snapshot_dir: Path,
    *,
    season_id: str,
    snapshot_id: str,
    report: dict[str, Any],
    registry_path: Path,
    previous_report_dir: str | None,
    keep_account_logs: bool = False,
    source_fingerprint: str = "",
    registry_contract: str = "",
    source_log_dirs: list[Path] | None = None,
    source_file_count: int = 0,
    source_total_bytes: int = 0,
    build_duration_seconds: float = 0.0,
    materialize_duration_seconds: float = 0.0,
    snapshot_total_bytes: int = 0,
    platform_model_label: str | None = None,
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
    resolved_scoring: dict[str, Any] | None = None,
    scoring_hash: str = "",
) -> Path:
    scoring = resolved_scoring or {}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "season_id": season_id,
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "games": report.get("games"),
        "accounts": len(report.get("accounts") or []),
        "compact": True,
        "kept_account_logs": bool(keep_account_logs),
        "build_contract_version": SNAPSHOT_BUILD_CONTRACT_VERSION,
        # 实际解析后的计分 profile（report/stat/manifest 同一份）。
        "scoring": scoring,
        "scoring_system": scoring.get("system"),
        "scoring_version": scoring.get("version"),
        "scoring_config_hash": scoring_hash,
        "room_policy": scoring.get("room_policy"),
        # Tenhou 动态 profile 无固定 PT 表 -> null；legacy 记真实 4-tuple。
        "rank_points": manifest_rank_points(scoring),
        "platform_model_label": platform_model_label,
        "preserve_log_dir_order": bool(preserve_log_dir_order),
        "interleave_log_dirs": bool(interleave_log_dirs),
        "source_fingerprint": source_fingerprint,
        "registry_contract": registry_contract,
        "source_log_dirs": [str(path) for path in (source_log_dirs or [])],
        "source_file_count": source_file_count,
        "source_total_bytes": source_total_bytes,
        "build_duration_seconds": build_duration_seconds,
        "materialize_duration_seconds": materialize_duration_seconds,
        "snapshot_total_bytes": snapshot_total_bytes,
        "source_registry": str(registry_path),
        "previous_report_dir": previous_report_dir,
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _patch_manifest_bytes(snapshot_dir: Path, *, snapshot_total_bytes: int) -> None:
    """重写 manifest 的 snapshot_total_bytes（manifest 就位后递归统计）。"""
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_total_bytes"] = snapshot_total_bytes
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _lock_path(registry_path: Path) -> Path:
    return registry_path.with_name(registry_path.name + ".lock")


def _tmp_path_for(registry_path: Path) -> Path:
    """唯一临时文件路径（含 PID + 随机后缀），避免多个发布共享同一 tmp。"""
    return registry_path.with_name(f"{registry_path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")


def _registry_contract(season: dict[str, Any]) -> str:
    """除 report_dir 外的注册表契约（模型/账号/状态/checkpoint 等）。"""
    clone = dict(season)
    clone.pop("report_dir", None)
    return json.dumps(clone, sort_keys=True, ensure_ascii=False)


def scoring_config_hash(scoring_config: dict[str, Any] | None) -> str:
    """赛季 scoring 配置的规范化指纹（manifest 中锁定计分契约）。

    基于解析后的 effective config（默认值已展开），而非未经展开的原始字典。
    """
    payload = scoring_config or {}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def manifest_rank_points(resolved_scoring_block: dict[str, Any]) -> str | None:
    """manifest 中实际生效的 rank_points（来自 profile 的 resolved scoring block）。

    - Tenhou 动态 profile：pt_rank_deltas 为 None -> manifest 记 null；
    - legacy fixed profile：记真实的 4-tuple PT 表。
    """
    deltas = resolved_scoring_block.get("pt_rank_deltas")
    if deltas is None:
        return None

    def _fmt(value: Any) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)

    return ",".join(_fmt(value) for value in deltas)


@contextmanager
def _registry_lock(registry_path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    """per-registry 排他锁：O_CREAT|O_EXCL 原子获取，超时抛 PublishError。"""
    lock_path = _lock_path(registry_path)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise PublishError(f"registry 锁获取超时: {lock_path}（可能有其他发布进行中）")
            time.sleep(0.2)
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def conditional_switch_registry(
    registry_path: Path,
    *,
    expected_season: dict[str, Any],
    expected_report_dir: str | None,
    new_report_dir: Path,
    timeout: float = 30.0,
) -> None:
    """加锁下的条件原子切换。

    - 切换前确认当前 report_dir 仍等于发布启动时看到的 previous_report_dir；
    - 除 report_dir 外的注册表契约（模型/账号/状态/checkpoint）发生变化则拒绝；
    - 使用唯一 tmp 文件，写盘 -> 结构校验 -> os.replace；
    - 冲突时抛 PublishError，由调用方清理本次 staging，线上 registry 与快照保持不变。
    """
    with _registry_lock(registry_path, timeout=timeout):
        current = ladder.read_registry(registry_path)
        current_report = str(current.get("report_dir") or "")
        if str(expected_report_dir or "") != current_report:
            raise PublishError(
                f"registry 已推进（当前 report_dir={current_report!r}，期望 {expected_report_dir!r}），拒绝旧发布覆盖"
            )
        if _registry_contract(current) != _registry_contract(expected_season):
            raise PublishError("registry 契约（模型/账号/状态/checkpoint）在构建期间发生变化，拒绝旧发布覆盖")

        updated = dict(current)
        try:
            updated["report_dir"] = encode_ladder_path(new_report_dir)
        except ValueError:
            # Explicit --snapshot-root is a diagnostics/test escape hatch.  A
            # normal publisher root always lives under ladder_data_root and is
            # therefore persisted portably.
            updated["report_dir"] = str(new_report_dir)
        tmp_path = _tmp_path_for(registry_path)
        tmp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            ladder.read_registry(tmp_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        try:
            os.replace(tmp_path, registry_path)
        except OSError as exc:
            # R11-G Repair：切换失败必须清理 tmp（否则每次失败都留下一个 tmp 文件，
            # worker 高频重试时会在 registry 目录堆积数百个孤儿 tmp）。
            tmp_path.unlink(missing_ok=True)
            raise PublishError(
                f"registry 原子切换失败（{exc}），线上 registry 保持不变"
            ) from exc


def _dir_total_bytes(directory: Path) -> int:
    """递归统计目录内全部文件的总字节数（含 manifest、account_logs 等）。"""
    if not directory.is_dir():
        return 0
    total = 0
    for path in directory.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _materialize_compact_snapshot(
    build_dir: Path,
    snapshot_stage: Path,
    *,
    keep_account_logs: bool,
) -> Path:
    """从完整 build 目录提取线上所需产物到紧凑 snapshot staging。

    - 必需产物（runtime/API 消费）必须存在；
    - 建议产物存在则复制，便于人工查看；
    - account_logs/ 是 build 阶段为计算详细统计而生成的派生副本，
      原始 mjai 日志归训练线所有，默认不写入快照。
    """
    snapshot_stage.mkdir(parents=True)
    for name in SNAPSHOT_REQUIRED_FILES:
        src = build_dir / name
        if not src.is_file():
            raise ladder.SeasonDataError(f"快照缺少必需文件: {name}")
        shutil.copy2(src, snapshot_stage / name)
    for name in SNAPSHOT_RECOMMENDED_FILES:
        src = build_dir / name
        if src.is_file():
            shutil.copy2(src, snapshot_stage / name)
    if keep_account_logs:
        logs_src = build_dir / "account_logs"
        if logs_src.is_dir():
            shutil.copytree(logs_src, snapshot_stage / "account_logs")
    return snapshot_stage


def _read_manifest(snapshot_dir: Path) -> dict[str, Any] | None:
    """读取快照目录的 manifest；无 manifest 或 schema 不合法时返回 None。"""
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        return None
    return manifest


def _ordered_log_stats(
    log_dirs: list[Path],
    *,
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
) -> list[tuple[str, int, int]]:
    """按 report builder 的真实处理顺序收集源日志指纹元组。

    复用 ``build_platform_account_report.iter_log_files`` 得到与评分/PT ledger
    完全一致的顺序，避免 ``[A,B]`` 与 ``[B,A]`` 在 preserve/interleave 模式下
    被误判为相同输入。
    """
    from replay import build_platform_account_report as account_report

    ordered_files = account_report.iter_log_files(
        log_dirs,
        preserve_log_dir_order=preserve_log_dir_order,
        interleave_log_dirs=interleave_log_dirs,
    )
    stats: list[tuple[str, int, int]] = []
    for path in ordered_files:
        try:
            st = path.stat()
        except OSError:
            continue
        stats.append((str(path.resolve()), st.st_size, st.st_mtime_ns))
    return stats


def _ordered_source_stats(ingest_root: Path) -> list[tuple[str, int, int]]:
    """ingest 赛季：递归收集 sources_root 下全部 source 文件（路径/大小/mtime）。"""
    stats: list[tuple[str, int, int]] = []
    if not ingest_root.is_dir():
        return stats
    for path in sorted(ingest_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        stats.append((str(path.resolve()), st.st_size, st.st_mtime_ns))
    return stats


def compute_source_fingerprint(
    log_dirs: list[Path],
    *,
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
    ingest_root: Path | None = None,
    extra_fingerprint: str | None = None,
) -> str:
    """源日志输入指纹：覆盖 builder 实际处理顺序的路径、大小、mtime_ns。

    相同的日志文件集合（含 ordering/interleave 参数）产生相同指纹；
    用于避免人工重复执行、resume 无新增日志、调度器重复触发时的重复重建。

    ingest 赛季使用 sources_root 全量文件；否则使用 mjai 日志目录。
    ``extra_fingerprint`` 追加合并（R10-F：participants ledger 投影指纹——
    sources_root 不动但 ledger 变化时也必须触发重建）。
    """
    if ingest_root is not None:
        stats = _ordered_source_stats(ingest_root)
        hasher = hashlib.sha256()
        hasher.update(b"ingest=1;")
        for path, size, mtime_ns in stats:
            hasher.update(f"{path}\0{size}\0{mtime_ns}\n".encode("utf-8"))
    else:
        stats = _ordered_log_stats(
            log_dirs,
            preserve_log_dir_order=preserve_log_dir_order,
            interleave_log_dirs=interleave_log_dirs,
        )
        hasher = hashlib.sha256()
        hasher.update(("preserve=%s;interleave=%s;" % (preserve_log_dir_order, interleave_log_dirs)).encode("ascii"))
        for path, size, mtime_ns in stats:
            hasher.update(f"{path}\0{size}\0{mtime_ns}\n".encode("utf-8"))
    if extra_fingerprint:
        hasher.update(b"extra=" + extra_fingerprint.encode("ascii"))
    return hasher.hexdigest()


def _should_skip_unchanged(
    season: dict[str, Any],
    manifest: dict[str, Any],
    *,
    source_fingerprint: str,
    rank_points: str | None,
    platform_model_label: str | None,
    keep_account_logs: bool,
    preserve_log_dir_order: bool,
    interleave_log_dirs: bool,
    scoring_hash: str,
) -> bool:
    """当前 snapshot manifest 与本次发布条件一致时返回 True（跳过重建）。

    需要 season contract、build contract version、source_fingerprint、
    生效 rank_points、scoring_config_hash、platform_model_label、keep_account_logs、
    log ordering/interleave 参数全部一致。

    ``rank_points`` 为已解析 profile 的 manifest 形式（Tenhou 动态 profile 为 None）。
    """
    if manifest.get("season_id") != season.get("season_id"):
        return False
    if _registry_contract(season) != _registry_contract_from_manifest(manifest):
        return False
    if manifest.get("build_contract_version") != SNAPSHOT_BUILD_CONTRACT_VERSION:
        return False
    if manifest.get("source_fingerprint") != source_fingerprint:
        return False
    if manifest.get("rank_points") != rank_points:
        return False
    if manifest.get("scoring_config_hash") != scoring_hash:
        return False
    if manifest.get("platform_model_label") != (platform_model_label or None):
        return False
    if bool(manifest.get("kept_account_logs")) != keep_account_logs:
        return False
    if bool(manifest.get("preserve_log_dir_order")) != preserve_log_dir_order:
        return False
    if bool(manifest.get("interleave_log_dirs")) != interleave_log_dirs:
        return False
    return True


def _registry_contract_from_manifest(manifest: dict[str, Any]) -> str:
    raw = manifest.get("registry_contract")
    return raw if isinstance(raw, str) else ""


def enforce_retention(
    snapshot_root: Path,
    *,
    season_id: str,
    retain: int,
    current_snapshot_dir: Path,
    previous_report_dir: str | None,
    protected_snapshots: set[Path],
) -> dict[str, list[str]]:
    """清理 snapshots 根目录下超出保留上限的旧快照。

    只在 registry 成功切换之后调用；任何清理失败都记录 warning 并返回
    ``retention_errors``，绝不把已成功发布的切换标记为失败。

    永不删除：
    - 当前 registry 指向的快照；
    - manifest 中记录的 previous snapshot（保留回滚点）；
    - 本次发布保护集（如 dry-run 快照）；
    - 没有合法 ``keqing.ladder.snapshot.v1`` manifest 的目录；
    - season_id 不一致的目录；
    - 隐藏 staging 目录；
    - snapshots 根目录中的其他人工文件。

    返回 ``{retention_deleted: [...], retention_errors: [...]}``。
    """
    result: dict[str, list[str]] = {"retention_deleted": [], "retention_errors": []}
    if retain <= 0 or not snapshot_root.exists():
        return result

    protected: set[Path] = set(protected_snapshots)
    protected.add(current_snapshot_dir.resolve())
    if previous_report_dir:
        protected.add(Path(previous_report_dir).resolve())

    # 收集本 season 的合法快照（带 manifest、season_id 一致、非隐藏）
    candidates: list[tuple[str, Path]] = []
    for entry in snapshot_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        manifest = _read_manifest(entry)
        if manifest is None:
            continue
        if manifest.get("season_id") != season_id:
            continue
        candidates.append((str(manifest.get("created_at") or entry.name), entry))

    # 按 created_at 排序（desc），保留最近 retain 个
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    keep = candidates[:retain]
    keep_paths = {path.resolve() for _created, path in keep}
    for _created, entry in candidates[retain:]:
        resolved = entry.resolve()
        if resolved in protected or resolved in keep_paths:
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            result["retention_errors"].append(f"{entry}: {exc}")
        else:
            result["retention_deleted"].append(str(entry))
    return result


def _snapshot_name_for(now: datetime, random_hex: str) -> str:
    """最终快照目录名：UTC 时间戳（含微秒）+ 随机后缀，天然唯一。

    ``<YYYYmmdd-HHMMSS>-<microseconds>-<uuid8>``，避免两个 publisher
    同秒争用同一最终目录。
    """
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond:06d}-{random_hex}"


def _staging_root_for(snapshot_root: Path, snapshot_name: str) -> Path:
    """隐藏 staging 根目录：完整 build + 紧凑 snapshot staging 都放其下。"""
    return snapshot_root / f".{snapshot_name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.staging"


def _participants_extra_fingerprint(season: dict[str, Any]) -> str | None:
    """participants ledger 语义投影指纹（P1-1）。

    赛季 ingest 配置 ``participants.enabled`` 时，把 ledger 的 eligible 比赛
    纳入 publisher fingerprint——否则 sources_root 不变但 matches.jsonl 变化
    时会被 unchanged-skip，新比赛永远进不了榜单。
    """
    ingest_cfg = season.get("ingest") if isinstance(season.get("ingest"), dict) else None
    participants_cfg = ingest_cfg.get("participants") if ingest_cfg else None
    if not isinstance(participants_cfg, dict) or not participants_cfg.get("enabled"):
        return None
    from replay import ladder_ingest

    season_id = str(season.get("season_id") or "")
    return ladder_ingest.participants_projection_fingerprint(season_id)


def publish_snapshot(
    *,
    registry_path: Path,
    log_dirs: list[Path],
    snapshot_root: Path | None = None,
    mortal_root: Path = Path("third_party/Mortal"),
    platform_model_label: str | None = None,
    rank_points: str = "90,45,0,-135",
    preserve_log_dir_order: bool = False,
    interleave_log_dirs: bool = False,
    dry_run: bool = False,
    keep_account_logs: bool = False,
    retain_snapshots: int = DEFAULT_RETAIN_SNAPSHOTS,
    build_report: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """隐藏 staging 构建 -> 紧凑快照 -> 校验 -> manifest -> 原子发布（staging 流程）。

    返回 dict：{snapshot_dir, games, dry_run, registry_switched}。
    校验失败时删除本次 staging 目录并抛出原异常，旧快照与旧 registry 不受影响；
    registry 切换失败时删除本次已 materialize 的孤儿快照，旧 registry 保持可用。
    """
    season = load_registry(registry_path)
    season_id = str(season["season_id"])
    if str(season.get("status") or "") == "completed":
        raise PublishError(f"season {season_id}: completed 赛季不可发布快照")
    if retain_snapshots < 0:
        raise PublishError(f"retain_snapshots 不能为负: {retain_snapshots}")

    # 版本化计分 profile 从赛季注册表的 scoring 块读取（legacy 赛季缺省走 fixed）。
    scoring_config = season.get("scoring") if isinstance(season.get("scoring"), dict) else None

    # scoring preflight：在任何 skip 判断之前解析 effective config，
    # 冲突立即失败——否则"已有可跳过快照"的路径会绕过 builder 的冲突检查。
    from replay import build_platform_account_report as account_report

    try:
        effective_scoring, resolved_block = account_report.resolve_effective_scoring_contract(
            scoring_config, rank_points
        )
    except ValueError as exc:
        raise PublishError(str(exc)) from exc
    scoring_hash = scoring_config_hash(effective_scoring)
    expected_rank_points = manifest_rank_points(resolved_block)

    # ingest 赛季：sources_root 解析与 API 共用同一实现（相对路径基于
    # KEQING_LADDER_DATA_ROOT，未设置时基于仓库根）。
    ingest_root: Path | None = None
    ingest_cfg = season.get("ingest") if isinstance(season.get("ingest"), dict) else None
    if ingest_cfg:
        from replay import ladder_ingest

        ingest_root = ladder_ingest.resolve_ingest_sources_root(_REPO_ROOT, season)

    root = snapshot_root or default_snapshot_root(None, season_id)

    previous_report_dir = season.get("report_dir")
    previous_snapshot: Path | None = None
    if previous_report_dir:
        previous_snapshot = resolve_ladder_path(str(previous_report_dir))

    source_fingerprint = compute_source_fingerprint(
        log_dirs,
        preserve_log_dir_order=preserve_log_dir_order,
        interleave_log_dirs=interleave_log_dirs,
        ingest_root=ingest_root,
        # R10-F：participants ledger 投影指纹——sources_root 不动但 ledger 变化也要重建
        extra_fingerprint=_participants_extra_fingerprint(season),
    )
    # 非 ingest 赛季必须有 mjai 日志输入；ingest 赛季不需要 --log-dir。
    if ingest_root is None and not log_dirs:
        raise PublishError("非 ingest 赛季至少需要一个 --log-dir")
    # dry-run 始终真正构建与校验；skip 前必须确认当前快照三件套仍完整可读。
    if not dry_run and previous_snapshot is not None and previous_snapshot.is_dir():
        prev_manifest = _read_manifest(previous_snapshot)
        if prev_manifest is not None and _should_skip_unchanged(
            season,
            prev_manifest,
            source_fingerprint=source_fingerprint,
            rank_points=expected_rank_points,
            platform_model_label=platform_model_label,
            keep_account_logs=keep_account_logs,
            preserve_log_dir_order=preserve_log_dir_order,
            interleave_log_dirs=interleave_log_dirs,
            scoring_hash=scoring_hash,
        ):
            try:
                ladder.validate_snapshot(season, previous_snapshot)
            except ladder.SeasonDataError:
                # 当前快照损坏：忽略 skip，重新完整构建以自愈
                pass
            else:
                return {
                    "snapshot_dir": str(previous_snapshot),
                    "games": prev_manifest.get("games"),
                    "dry_run": dry_run,
                    "registry_switched": False,
                    "skipped_unchanged": True,
                    "retention_deleted": [],
                    "retention_errors": [],
                }

    # 最终快照 ID 天然唯一：UTC 时间戳（含微秒）+ 随机后缀，
    # 避免两个 publisher 同秒争用同一最终目录。
    snapshot_name = _snapshot_name_for(datetime.now(timezone.utc), uuid.uuid4().hex[:8])
    snapshot_dir = root / snapshot_name

    staging_root = _staging_root_for(root, snapshot_dir.name)
    build_dir = staging_root / "build"
    snapshot_stage = staging_root / "snapshot"
    staging_root.mkdir(parents=True)

    # 发布所有权：只有本次调用确实成功 os.replace 才拥有该最终目录；
    # registry 一旦切换，任何后续异常都不得删除当前线上快照。
    materialized_by_this_publish = False
    registry_switched = False

    try:
        build_started = time.monotonic()
        report = build_snapshot(
            log_dirs=log_dirs,
            snapshot_dir=build_dir,
            mortal_root=mortal_root,
            platform_model_label=platform_model_label,
            rank_points=rank_points,
            preserve_log_dir_order=preserve_log_dir_order,
            interleave_log_dirs=interleave_log_dirs,
            scoring_config=scoring_config,
            season=season,
            ingest_root=ingest_root,
            build_report=build_report,
        )
        build_duration = time.monotonic() - build_started

        materialize_started = time.monotonic()
        _materialize_compact_snapshot(
            build_dir=build_dir,
            snapshot_stage=snapshot_stage,
            keep_account_logs=keep_account_logs,
        )
        ladder.validate_snapshot(season, snapshot_stage)
        materialize_duration = time.monotonic() - materialize_started

        if ingest_root is not None:
            source_stats = _ordered_source_stats(ingest_root)
        else:
            source_stats = _ordered_log_stats(
                log_dirs,
                preserve_log_dir_order=preserve_log_dir_order,
                interleave_log_dirs=interleave_log_dirs,
            )
        write_manifest(
            snapshot_stage,
            season_id=season_id,
            snapshot_id=snapshot_dir.name,
            report=report,
            registry_path=registry_path,
            previous_report_dir=str(previous_snapshot) if previous_snapshot else None,
            keep_account_logs=keep_account_logs,
            source_fingerprint=source_fingerprint,
            registry_contract=_registry_contract(season),
            source_log_dirs=[ingest_root] if ingest_root is not None else log_dirs,
            source_file_count=len(source_stats),
            source_total_bytes=sum(int(size) for _path, size, _mtime in source_stats),
            build_duration_seconds=round(build_duration, 4),
            materialize_duration_seconds=round(materialize_duration, 4),
            snapshot_total_bytes=0,  # 占位；manifest 就位后重算
            platform_model_label=platform_model_label,
            preserve_log_dir_order=preserve_log_dir_order,
            interleave_log_dirs=interleave_log_dirs,
            # 以本次实际构建出的 report.scoring 为权威 resolved profile。
            resolved_scoring=report.get("scoring") if isinstance(report.get("scoring"), dict) else resolved_block,
            scoring_hash=scoring_hash,
        )
        # manifest 已在目录中，递归统计整个快照（含 manifest/account_logs）。
        # 第一次写入占位(0)改变 manifest 自身大小，第二轮统计即稳定。
        for _round in range(2):
            _patch_manifest_bytes(snapshot_stage, snapshot_total_bytes=_dir_total_bytes(snapshot_stage))
        # 只有完整构建与校验通过后，才把 snapshot staging 原子改名为最终快照
        os.replace(snapshot_stage, snapshot_dir)
        materialized_by_this_publish = True
        if dry_run:
            return {
                "snapshot_dir": str(snapshot_dir),
                "games": report.get("games"),
                "dry_run": True,
                "registry_switched": False,
                "skipped_unchanged": False,
                "retention_deleted": [],
                "retention_errors": [],
            }
        conditional_switch_registry(
            registry_path,
            expected_season=season,
            expected_report_dir=str(previous_report_dir) if previous_report_dir else None,
            new_report_dir=snapshot_dir,
        )
        registry_switched = True
        try:
            retention = enforce_retention(
                root,
                season_id=season_id,
                retain=retain_snapshots,
                current_snapshot_dir=snapshot_dir,
                previous_report_dir=str(previous_snapshot) if previous_snapshot else None,
                protected_snapshots=set(),
            )
        except Exception as exc:
            # retention 降级为结果项，绝不把已成功的发布标记为失败
            retention = {
                "retention_deleted": [],
                "retention_errors": [f"retention scan failed: {exc}"],
            }
        return {
            "snapshot_dir": str(snapshot_dir),
            "games": report.get("games"),
            "dry_run": False,
            "registry_switched": True,
            "skipped_unchanged": False,
            **retention,
        }
    except Exception:
        # 仅当本次调用确实 materialize 且 registry 尚未切换时才可删除该目录；
        # 切换成功后或目录不属于本调用时，绝不删除（避免误删线上/他人快照）。
        if materialized_by_this_publish and not registry_switched:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise
    finally:
        # 无论成败都清理完整 build staging（含 account_logs 派生副本）
        shutil.rmtree(staging_root, ignore_errors=True)


def main() -> None:
    args = parse_args()
    result = publish_snapshot(
        registry_path=args.registry,
        log_dirs=args.log_dir,
        snapshot_root=args.snapshot_root,
        mortal_root=args.mortal_root,
        platform_model_label=args.platform_model_label,
        rank_points=args.rank_points,
        preserve_log_dir_order=args.preserve_log_dir_order,
        interleave_log_dirs=args.interleave_log_dirs,
        dry_run=args.dry_run,
        keep_account_logs=args.keep_account_logs,
        retain_snapshots=args.retain_snapshots,
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
