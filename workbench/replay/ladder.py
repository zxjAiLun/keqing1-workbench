"""Model ladder & account profile data loaders.

Reads versioned season registries from ``workbench/configs/ladder/seasons/*.json`` and
the platform account reports produced by
``training/mortal/build_platform_account_report.py``.

The ladder never reads raw replay logs: it only consumes the aggregated
``account_summary.json`` / ``rating_curve.csv`` / ``account_ledger.jsonl``
artifacts, so the GUI never loads thousands of hanchans at once.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path
from typing import Any

from workbench.runtime.resolver import ladder_registry_root, resolve_ladder_path

SEASON_SCHEMA = "keqing.ladder.season.v1"
REPORT_SCHEMA = "keqing.mortal.platform_account_report.v1"
REPORT_SCHEMA_V2 = "keqing.mortal.platform_account_report.v2"
REPORT_SCHEMAS = (REPORT_SCHEMA, REPORT_SCHEMA_V2)

LADDER_SORTS = ("rank", "pt", "rating", "avg_rank", "games")

# v1 报告（固定七段）的 legacy 回退段位序（初段=11 ... 七段=17）。
_LEGACY_RANK_ORDINAL = 17


class LadderError(Exception):
    """Base class for ladder data failures."""


class SeasonRegistryError(LadderError):
    """Season registry file is missing or invalid."""


class SeasonNotFoundError(LadderError):
    """Requested season does not exist."""


class SeasonDataError(LadderError):
    """Season report artifacts are missing or invalid.

    ``code`` / ``state`` / ``retryable`` 供机器可读的 readiness 契约：
    - code: 稳定错误码（season_report_dir_missing / season_report_decode_error / ...）
    - state: ready / not_published / invalid / registry_mismatch
    - retryable: 后续发布/轮询是否可能自愈
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "season_data_invalid",
        state: str = "invalid",
        retryable: bool = True,
    ):
        super().__init__(message)
        self.code = code
        self.state = state
        self.retryable = retryable


# 稳定 readiness 状态与错误码（与前端 LadderReadinessState 对齐）
READINESS_STATES = ("ready", "not_published", "invalid", "registry_mismatch")

SEASON_READINESS_MESSAGES: dict[str, str] = {
    "ready": "赛季数据已就绪",
    "season_report_dir_missing": "赛季数据尚未发布",
    "season_required_file_missing": "赛季报告缺少必需文件",
    "season_report_decode_error": "赛季报告无法解析",
    "season_report_schema_invalid": "赛季报告格式无效",
    "season_snapshot_unreadable": "赛季快照不可读",
    "season_report_registry_mismatch": "赛季身份契约不一致",
}


def season_data_problem(exc: SeasonDataError) -> dict[str, Any]:
    """把 SeasonDataError 序列化为结构化 readiness/错误原因。"""
    return {
        "state": exc.state,
        "code": exc.code,
        "message": SEASON_READINESS_MESSAGES.get(exc.code, "赛季数据异常"),
        "detail": str(exc),
        "retryable": exc.retryable,
    }


class AccountNotFoundError(LadderError):
    """Requested account does not exist in the season."""


class ModelNotFoundError(LadderError):
    """Requested model does not exist in the season."""


_json_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _read_json_cached(path: Path) -> Any:
    key = str(path)
    mtime = path.stat().st_mtime
    with _cache_lock:
        cached = _json_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    with _cache_lock:
        _json_cache[key] = (mtime, data)
    return data


# ---------------------------------------------------------------------------
# Season registry
# ---------------------------------------------------------------------------

def _validate_registry(raw: Any, filename: str) -> dict[str, Any]:
    """校验赛季注册表结构，使其成为账号身份的权威来源。

    任何无效 model/account 都会抛出 SeasonRegistryError，不做静默跳过。
    """
    def _fail(reason: str) -> SeasonRegistryError:
        return SeasonRegistryError(f"赛季注册表 {filename}: {reason}")

    if not isinstance(raw, dict) or raw.get("schema") != SEASON_SCHEMA:
        raise _fail("schema 无效")
    season_id = raw.get("season_id")
    if not isinstance(season_id, str) or not season_id.strip():
        raise _fail("season_id 必须是非空字符串")
    report_dir = raw.get("report_dir")
    if not isinstance(report_dir, str) or not report_dir.strip():
        raise _fail(f"season {season_id}: report_dir 必须是非空字符串")
    # 可选 default 标记：必须是布尔值（字符串 "true" 视为无效注册表）
    if "default" in raw:
        if not isinstance(raw.get("default"), bool):
            raise _fail(f"season {season_id}: default 必须是布尔值")
    models = raw.get("models")
    if not isinstance(models, list):
        raise _fail(f"season {season_id}: models 必须是数组")
    seen_models: set[str] = set()
    seen_accounts: set[str] = set()
    for model_index, model in enumerate(models):
        if not isinstance(model, dict):
            raise _fail(f"season {season_id}: models[{model_index}] 必须是对象")
        model_id = model.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise _fail(f"season {season_id}: models[{model_index}] 的 model_id 必须是非空字符串")
        if model_id in seen_models:
            raise _fail(f"season {season_id}: 重复 model_id '{model_id}'")
        seen_models.add(model_id)

        # R13-B: 结构化校验 execution_provenance
        exec_prov_raw = model.get("execution_provenance")
        if exec_prov_raw is not None:
            if not isinstance(exec_prov_raw, dict):
                raise _fail(f"season {season_id} / model {model_id}: execution_provenance 必须是对象")
            from workbench.participants.schemas import FrozenExecutionProvenance
            try:
                frozen_prov = FrozenExecutionProvenance.model_validate(exec_prov_raw)
            except Exception as exc:
                raise _fail(f"season {season_id} / model {model_id}: execution_provenance 结构无效: {exc}") from exc

            if frozen_prov.kind == "human":
                if model_id != "human":
                    raise _fail(f"season {season_id} / model {model_id}: human provenance 只能用于 model_id='human'")
            elif frozen_prov.kind == "local_artifact":
                expected_ident = model.get("model_identity_id") or model_id
                if frozen_prov.model_identity_id != expected_ident:
                    raise _fail(
                        f"season {season_id} / model {model_id}: local_artifact model_identity_id '{frozen_prov.model_identity_id}' 与 group '{expected_ident}' 不一致"
                    )
            elif frozen_prov.kind == "external_revision":
                expected_ident = model.get("model_identity_id") or model_id
                if frozen_prov.model_identity_id != expected_ident:
                    raise _fail(
                        f"season {season_id} / model {model_id}: external_revision model_identity_id '{frozen_prov.model_identity_id}' 与 group '{expected_ident}' 不一致"
                    )

        accounts = model.get("accounts")
        if not isinstance(accounts, list):
            raise _fail(f"season {season_id} / model {model_id}: accounts 必须是数组")
        for account_index, account in enumerate(accounts):
            if not isinstance(account, dict):
                raise _fail(f"season {season_id} / model {model_id}: accounts[{account_index}] 必须是对象")
            account_id = account.get("account_id")
            if not isinstance(account_id, str) or not account_id.strip():
                raise _fail(f"season {season_id} / model {model_id}: accounts[{account_index}] 的 account_id 必须是非空字符串")
            if account_id in seen_accounts:
                raise _fail(f"season {season_id}: 重复 account_id '{account_id}'（出现于 model {model_id}）")
            seen_accounts.add(account_id)
    return raw


def _load_registry_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SeasonRegistryError(f"赛季注册表无法解析: {path.name}") from exc
    return _validate_registry(raw, path.name)


def validate_registry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """R11-E2 Repair：写前验证（覆盖 canonical JSON 之前）。

    mutation 端必须先把最终 payload 通过注册表结构校验（schema / season_id /
    report_dir / default / models / accounts 唯一性等），通过后才允许原子写。
    """
    return _validate_registry(payload, "<enrollment>")


def read_registry(path: Path) -> dict[str, Any]:
    """读取并校验单个赛季注册表文件（供发布器等生产端复用）。"""
    return _load_registry_file(path)


def list_season_configs(configs_dir: Path) -> list[dict[str, Any]]:
    """Parse every season registry file, sorted by season_id.

    这是注册表的统一入口：同时校验 season_id 全局唯一与 default 全局唯一，
    因此 catalog 与实体端点（load_ladder / load_account / load_model）共享同一契约。
    """
    if not configs_dir.exists():
        return []
    seasons = [_load_registry_file(path) for path in sorted(configs_dir.glob("*.json"))]
    seen_ids: set[str] = set()
    for season in seasons:
        season_id = str(season["season_id"])
        if season_id in seen_ids:
            raise SeasonRegistryError(f"season_id 全局重复: {season_id}")
        seen_ids.add(season_id)
    seasons.sort(key=lambda item: str(item.get("season_id", "")))
    validate_default_season_contract(seasons)
    return seasons


def get_season_config(configs_dir: Path, season_id: str) -> dict[str, Any]:
    for season in list_season_configs(configs_dir):
        if season.get("season_id") == season_id:
            return season
    raise SeasonNotFoundError(f"season 不存在: {season_id}")


def _explicit_defaults(seasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [season for season in seasons if season.get("default") is True]


def validate_default_season_contract(seasons: list[dict[str, Any]]) -> None:
    """校验 default 合同（R11-E1 升级版）。

    - 全局最多一个显式 default；
    - explicit default 必须 ``status == "running"``
      （draft/completed/archived 带 default=true 视为注册表损坏）。
    """
    defaults = _explicit_defaults(seasons)
    if len(defaults) > 1:
        ids = ", ".join(str(season.get("season_id")) for season in defaults)
        raise SeasonRegistryError(f"season default 重复（最多一个）: {ids}")
    if len(defaults) == 1:
        season = defaults[0]
        if str(season.get("status") or "") != "running":
            raise SeasonRegistryError(
                f"赛季 {season.get('season_id')} 标记为 default 但状态不是 running"
            )


def resolve_default_season(seasons: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """解析默认赛季，返回 ``(default_season_id, default_source)``。

    - 1 个显式 default（且已通过 validate_default_season_contract 校验 running）：
      该赛季为默认，source="registry"
    - 0 个显式 default：**没有当前赛季，不得猜测**（R11-E1：取消 single_season 自动默认）
    - 2 个及以上显式 default：调用前应已通过 validate_default_season_contract 拒绝。

    默认赛季是运维选择，不按字典序 / 最新 updated_at / data-ready / status 推断。
    """
    defaults = _explicit_defaults(seasons)
    if len(defaults) == 1:
        return str(defaults[0].get("season_id")), "registry"
    if len(defaults) > 1:
        raise SeasonRegistryError("season default 重复（最多一个）")
    return None, None


def _registry_models(season: dict[str, Any]) -> list[dict[str, Any]]:
    models = season.get("models")
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, dict)]


def _registry_account_index(season: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """account_id -> {display_name, model_id, checkpoint}"""
    index: dict[str, dict[str, Any]] = {}
    for model in _registry_models(season):
        model_id = str(model.get("model_id", ""))
        checkpoint = model.get("checkpoint")
        accounts = model.get("accounts")
        if not isinstance(accounts, list):
            continue
        for account in accounts:
            if not isinstance(account, dict):
                continue
            account_id = str(account.get("account_id", ""))
            if not account_id:
                continue
            index[account_id] = {
                "display_name": str(account.get("display_name") or account_id),
                "model_id": model_id,
                "checkpoint": checkpoint,
            }
    return index


# ---------------------------------------------------------------------------
# 外部注册表 / 数据根边界（Live Ladder Data Plane）
# ---------------------------------------------------------------------------

def resolve_config_dir(project_root: Path) -> Path:
    """赛季注册表目录。

    正常 runtime 读取 ``KEQING_DATA_ROOT/ladder/registries``。仓库 configs
    仅作 seed/example，不得作为生产 fallback。
    """
    del project_root
    return ladder_registry_root()


def resolve_report_dir(project_root: Path, raw_dir: str) -> Path:
    """解析注册表 report_dir。

    - 绝对路径原样使用（动态赛季通常直接指向 keqing-data 下的快照）；
    - 相对路径默认相对 project_root；设置 ``KEQING_LADDER_DATA_ROOT`` 时相对该数据根。
    """
    # Direct loader callers in tests/tools may supply an isolated project root
    # with no runtime data configuration.  Preserve that explicit fixture mode;
    # the Workbench runtime passes its real repository root and always uses the
    # portable ladder-data contract below.
    if (
        not os.environ.get("KEQING_LADDER_DATA_ROOT", "").strip()
        and not os.environ.get("KEQING_DATA_ROOT", "").strip()
        and project_root.resolve() != Path(__file__).resolve().parents[2]
        and not Path(raw_dir).is_absolute()
    ):
        return (project_root / raw_dir).resolve()
    return resolve_ladder_path(raw_dir)


# ---------------------------------------------------------------------------
# Report artifacts
# ---------------------------------------------------------------------------

def _report_dir(project_root: Path, season: dict[str, Any]) -> Path:
    raw_dir = str(season.get("report_dir") or "")
    if not raw_dir:
        raise SeasonDataError(
            f"赛季 {season.get('season_id')} 未配置 report_dir",
            code="season_report_dir_missing",
            state="not_published",
        )
    report_dir = resolve_report_dir(project_root, raw_dir)
    if not report_dir.exists():
        raise SeasonDataError(
            f"赛季报告目录不存在: {raw_dir}",
            code="season_report_dir_missing",
            state="not_published",
        )
    return report_dir


def _load_account_summary(report_dir: Path) -> dict[str, Any]:
    summary_path = report_dir / "account_summary.json"
    if not summary_path.exists():
        raise SeasonDataError(
            "赛季缺少 account_summary.json，请先运行 build_platform_account_report.py",
            code="season_required_file_missing",
            state="not_published",
        )
    try:
        report = _read_json_cached(summary_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SeasonDataError(
            f"account_summary.json 无法解析: {summary_path}",
            code="season_report_decode_error",
            state="invalid",
        ) from exc
    if not isinstance(report, dict) or report.get("schema") not in REPORT_SCHEMAS:
        raise SeasonDataError(
            "account_summary.json schema 无效",
            code="season_report_schema_invalid",
            state="invalid",
        )
    return report


def _validate_report_accounts(season: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """校验 report 账号集合与注册表的一致性（注册表为权威身份表）。

    - report 每个账号必须已在注册表声明（report ⊆ registry）；
    - report 内不得有重复 account_id；
    - report 的 model_label 非空时必须与注册表 model_id 一致；
    - 注册表已声明但 report 没有的账号：允许（视作该账号在赛季内 0 场 /
      无历史 report）——completed 历史赛季同样适用（R11 post-release 修复：
      早期注册表扩充账号后，旧 report 没有这些账号并不代表数据损坏）。
    - report 中出现未注册/错模型账号：任何状态都拒绝。
    """
    season_id = season.get("season_id")
    rows = report.get("accounts")
    if not isinstance(rows, list):
        raise SeasonDataError(
            f"season {season_id}: account_summary.json 的 accounts 必须是数组",
            code="season_report_decode_error",
            state="invalid",
        )
    registry_index = _registry_account_index(season)
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SeasonDataError(
                f"season {season_id}: report 账号行必须是对象",
                code="season_report_decode_error",
                state="invalid",
            )
        account_id = row.get("account_id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise SeasonDataError(
                f"season {season_id}: report 账号行缺少非空 account_id",
                code="season_report_decode_error",
                state="invalid",
            )
        if account_id in seen:
            raise SeasonDataError(
                f"season {season_id}: report 内重复 account_id '{account_id}'",
                code="season_report_registry_mismatch",
                state="registry_mismatch",
            )
        seen.add(account_id)
        registry = registry_index.get(account_id)
        if registry is None:
            raise SeasonDataError(
                f"season {season_id}: report 账号 '{account_id}' 未在注册表声明",
                code="season_report_registry_mismatch",
                state="registry_mismatch",
            )
        model_label = row.get("model_label")
        if isinstance(model_label, str) and model_label.strip() and model_label != registry["model_id"]:
            raise SeasonDataError(
                f"season {season_id}: 账号 '{account_id}' 的 model_label '{model_label}' "
                f"与注册表 model_id '{registry['model_id']}' 不一致",
                code="season_report_registry_mismatch",
                state="registry_mismatch",
            )
        validated.append(row)
    return validated


def _load_validated_report(project_root: Path, season: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report_dir = _report_dir(project_root, season)
    _validate_snapshot_files(report_dir)
    report = _load_account_summary(report_dir)
    rows = _validate_report_accounts(season, report)
    return report, rows


SNAPSHOT_REQUIRED_FILES = ("account_summary.json", "account_ledger.jsonl", "rating_curve.csv")


def _validate_snapshot_files(snapshot_dir: Path) -> None:
    """校验快照必需三件套存在且可读（零场快照允许内容为空，但文件必须存在）。

    catalog 与实体端点、publisher 共用同一份"就绪"定义。
    """
    for name in SNAPSHOT_REQUIRED_FILES:
        path = snapshot_dir / name
        if not path.is_file():
            raise SeasonDataError(
                f"快照缺少必需文件: {name}",
                code="season_required_file_missing",
                state="not_published",
            )
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.read(1)
        except (OSError, UnicodeError) as exc:
            raise SeasonDataError(
                f"快照文件不可读: {name}",
                code="season_snapshot_unreadable",
                state="invalid",
            ) from exc


def validate_snapshot(season: dict[str, Any], snapshot_dir: Path) -> list[dict[str, Any]]:
    """校验一个已构建好的快照目录是否满足注册表契约（供发布器复用）。"""
    _validate_snapshot_files(snapshot_dir)
    report = _load_account_summary(snapshot_dir)
    return _validate_report_accounts(season, report)


def _summary_mtime(report_dir: Path) -> float | None:
    summary_path = report_dir / "account_summary.json"
    try:
        return summary_path.stat().st_mtime
    except OSError:
        return None


def _attach_snapshot_meta(season_pub: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    season_pub["snapshot_id"] = report_dir.name
    season_pub["updated_at"] = _summary_mtime(report_dir)
    return season_pub


def _enrich_account_row(
    row: dict[str, Any],
    registry_index: dict[str, dict[str, Any]],
    scoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account_id = str(row.get("account_id", ""))
    # 注册表为权威身份表；账号存在性已由 _validate_report_accounts 保证
    registry = registry_index[account_id]
    games = int(row.get("games") or 0)
    rank_1 = int(row.get("rank_1") or 0)
    rank_4 = int(row.get("rank_4") or 0)

    rank_id = row.get("rank_id")
    if isinstance(rank_id, str) and rank_id:
        # v2 报告：版本化计分引擎产出的真实段位状态
        rank_ordinal = int(row.get("rank_ordinal") or 0)
        pt_initial = row.get("pt_initial")
        pt_current = float(row.get("pt_current") or 0.0)
        raw_target = row.get("pt_target")
        pt_target = float(raw_target) if raw_target is not None else None
        pt_progress = row.get("pt_progress")
        rank_name = row.get("rank_name")
        promotions = int(row.get("promotions") or 0)
        demotions = int(row.get("demotions") or 0)
        highest_rank_id = row.get("highest_rank_id")
        tenhou_reached = bool(row.get("tenhou_reached"))
        total_pt_delta = row.get("total_pt_delta")
        avg_pt_delta = row.get("avg_pt_delta")
    else:
        # v1 报告（legacy fixed 七段）：回退为固定段位语义
        rank_id = None
        rank_ordinal = _LEGACY_RANK_ORDINAL
        pt_initial = scoring.get("pt_initial") if scoring else None
        pt_current = float(row.get("pt_current") or 0.0)
        raw_target = row.get("pt_target") or (scoring.get("pt_target") if scoring else None)
        pt_target = float(raw_target) if raw_target is not None else None
        pt_progress = (pt_current / pt_target) if pt_target else None
        rank_name = row.get("rank_name")
        promotions = 0
        demotions = 0
        # v1 报告没有段位状态：不推导"历史最高"，避免与当前七段重复展示。
        highest_rank_id = None
        tenhou_reached = False
        total_pt_delta = None
        avg_pt_delta = None

    return {
        "account_id": account_id,
        "display_name": registry.get("display_name") or account_id,
        "model_id": registry["model_id"],
        "checkpoint": registry.get("checkpoint"),
        "games": games,
        "rank_id": rank_id,
        "rank_name": rank_name,
        "rank_ordinal": rank_ordinal,
        "pt_initial": pt_initial,
        "pt_current": pt_current,
        "pt_target": pt_target,
        "pt_gap": (pt_target - pt_current) if pt_target is not None else None,
        "pt_progress": pt_progress,
        "promotions": promotions,
        "demotions": demotions,
        "highest_rank_id": highest_rank_id,
        "tenhou_reached": tenhou_reached,
        "total_pt_delta": total_pt_delta,
        "avg_pt_delta": avg_pt_delta,
        "rating": float(row.get("rating") or 0.0),
        "rank_1": rank_1,
        "rank_2": int(row.get("rank_2") or 0),
        "rank_3": int(row.get("rank_3") or 0),
        "rank_4": rank_4,
        "rank_1_rate": (rank_1 / games) if games else None,
        "rank_4_rate": (rank_4 / games) if games else None,
        "avg_rank": row.get("avg_rank"),
        "avg_rank_pt": row.get("avg_rank_pt"),
        "agari_rate": row.get("agari_rate"),
        "houjuu_rate": row.get("houjuu_rate"),
        "fuuro_rate": row.get("fuuro_rate"),
        "riichi_rate": row.get("riichi_rate"),
        "agari_rate_after_fuuro": row.get("agari_rate_after_fuuro"),
        "houjuu_rate_after_fuuro": row.get("houjuu_rate_after_fuuro"),
        "agari_rate_after_riichi": row.get("agari_rate_after_riichi"),
        "houjuu_rate_after_riichi": row.get("houjuu_rate_after_riichi"),
        "avg_point_per_agari": row.get("avg_point_per_agari"),
        "total_delta_score": row.get("total_delta_score"),
        # R12-A：详细统计覆盖率（完整牌谱场数 / 总局数 / 覆盖率）。
        "stats_games": row.get("stats_games"),
        "stats_rounds": row.get("stats_rounds"),
        "stats_total_games": row.get("stats_total_games"),
        "stats_coverage": row.get("stats_coverage"),
    }


def _summarize_models(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展示性模型聚合：段位分布 + 最高/中位段位 + 平均 Rating + 总场数。

    不同段位的 PT 不能直接平均：仅当模型内全部账号处于同一段位时才计算
    ``avg_pt``（v1 legacy fixed 赛季保留该展示），跨段位时置 None。
    """
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_model.setdefault(str(row["model_id"]), []).append(row)
    summaries: list[dict[str, Any]] = []
    for model_id, items in by_model.items():
        games = sum(int(item["games"]) for item in items)

        def _weighted(key: str) -> float | None:
            values = [
                (float(item[key]), int(item["games"]))
                for item in items
                if item.get(key) is not None and int(item["games"]) > 0
            ]
            total_games = sum(count for _, count in values)
            if not values or total_games == 0:
                return None
            return sum(value * count for value, count in values) / total_games

        rank_names = [str(item.get("rank_name") or item.get("rank_id") or "?") for item in items]
        rank_ordinals = [int(item.get("rank_ordinal") or 0) for item in items]
        distinct_ranks = {item.get("rank_id") for item in items}
        # v1 legacy（全部无 rank_id，同一固定段位）与 v2 单一段位均可平均 PT。
        same_rank = len(distinct_ranks) <= 1

        highest = max(items, key=lambda item: int(item.get("rank_ordinal") or 0))
        summaries.append({
            "model_id": model_id,
            "accounts": len(items),
            "games": games,
            "avg_pt": _weighted("pt_current") if same_rank else None,
            "avg_rating": _weighted("rating"),
            "avg_rank": _weighted("avg_rank"),
            "avg_rank_pt": _weighted("avg_rank_pt"),
            "rank_distribution": {name: rank_names.count(name) for name in sorted(set(rank_names))},
            "highest_rank_id": highest.get("rank_id"),
            "highest_rank_name": highest.get("rank_name"),
            "highest_rank_ordinal": int(highest.get("rank_ordinal") or 0),
            "median_rank_ordinal": _median(rank_ordinals),
            "median_rank_name": _rank_name_for_ordinal(rank_ordinals, rank_names),
        })
    summaries.sort(
        key=lambda item: (
            -(int(item.get("highest_rank_ordinal") or 0)),
            item["avg_rating"] is None,
            -(item["avg_rating"] or 0.0),
        )
    )
    return summaries


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _rank_name_for_ordinal(ordinals: list[int], names: list[str]) -> str | None:
    if not ordinals:
        return None
    ordered = sorted(zip(ordinals, names), key=lambda item: item[0])
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid][1]
    return ordered[mid - 1][1]


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def _season_public(
    season: dict[str, Any],
    report: dict[str, Any] | None = None,
    *,
    is_default: bool = False,
) -> dict[str, Any]:
    registry_models = _registry_models(season)
    payload: dict[str, Any] = {
        "season_id": season.get("season_id"),
        "title": season.get("title"),
        "status": season.get("status"),
        "games_expected": season.get("games_expected"),
        "notes": season.get("notes"),
        "models": [model.get("model_id") for model in registry_models],
        "accounts": sum(len(model.get("accounts") or []) for model in registry_models),
        "is_default": is_default,
    }
    if report is not None:
        payload["games"] = report.get("games")
        payload["report_schema"] = report.get("schema")
        scoring = report.get("scoring")
        if isinstance(scoring, dict):
            payload["scoring"] = {
                "system": scoring.get("system"),
                "version": scoring.get("version"),
                "game_length": scoring.get("game_length"),
                "room_policy": scoring.get("room_policy"),
                "membership": scoring.get("membership"),
                "tier_policy": scoring.get("tier_policy"),
                "positive_pt_tables": scoring.get("positive_pt_tables"),
                "initial_rank": scoring.get("initial_rank"),
                "initial_rating": scoring.get("initial_rating"),
                "pt_profile": scoring.get("pt_profile"),
                "pt_rank_deltas": scoring.get("pt_rank_deltas"),
                "pt_initial": scoring.get("pt_initial"),
                "pt_target": scoring.get("pt_target"),
                "rating_initial": scoring.get("rating_initial"),
                "rating_formula": scoring.get("rating_formula"),
                "rank_name": scoring.get("rank_name"),
            }
    return payload


def list_seasons(project_root: Path, configs_dir: Path) -> list[dict[str, Any]]:
    """列出已注册赛季（每项含 is_default 派生标记）。

    保持返回 list 以兼容既有调用方；带默认信息的完整目录见
    :func:`list_seasons_catalog`。
    """
    catalog = list_seasons_catalog(project_root, configs_dir)
    return catalog["seasons"]


def list_seasons_catalog(project_root: Path, configs_dir: Path) -> dict[str, Any]:
    """赛季目录：默认赛季 + 每赛季 is_default/readiness 派生信息。

    ``default_source``：registry / single_season / null（无默认时）。
    """
    seasons_configs = list_season_configs(configs_dir)
    validate_default_season_contract(seasons_configs)
    default_id, default_source = resolve_default_season(seasons_configs)

    seasons: list[dict[str, Any]] = []
    for season in seasons_configs:
        is_default = season.get("season_id") == default_id
        entry = _season_public(season, is_default=is_default)
        try:
            report, _rows = _load_validated_report(project_root, season)
        except SeasonDataError as exc:
            # 报告缺失或与注册表不一致：该赛季标记为未就绪，不影响整个清单
            report = None
            entry["data_ready"] = False
            entry["readiness"] = season_data_problem(exc)
        else:
            entry = _season_public(season, report, is_default=is_default)
            entry["data_ready"] = True
            entry["readiness"] = {
                "state": "ready",
                "code": "ready",
                "message": "赛季数据已就绪",
                "retryable": False,
            }
            entry["games"] = report.get("games")
            _attach_snapshot_meta(entry, _report_dir(project_root, season))
        seasons.append(entry)

    return {
        "schema": "keqing.ladder.seasons.v1",
        "default_season_id": default_id,
        "default_source": default_source,
        "seasons": seasons,
    }


def load_ladder(project_root: Path, configs_dir: Path, season_id: str, sort: str = "rank") -> dict[str, Any]:
    if sort not in LADDER_SORTS:
        sort = "rank"
    season = get_season_config(configs_dir, season_id)
    report, validated_rows = _load_validated_report(project_root, season)
    registry_index = _registry_account_index(season)
    scoring = report.get("scoring") if isinstance(report.get("scoring"), dict) else None
    rows = [_enrich_account_row(row, registry_index, scoring=scoring) for row in validated_rows]
    if sort == "rank":
        # 规范排序：段位序 DESC -> PT DESC -> Rating DESC -> account_id ASC
        rows.sort(key=lambda row: (-int(row["rank_ordinal"]), -row["pt_current"], -row["rating"], row["account_id"]))
    elif sort == "pt":
        rows.sort(key=lambda row: row["pt_current"], reverse=True)
    elif sort == "rating":
        rows.sort(key=lambda row: row["rating"], reverse=True)
    elif sort == "avg_rank":
        rows.sort(key=lambda row: (row["avg_rank"] is None, row["avg_rank"] or 0.0))
    else:  # games
        rows.sort(key=lambda row: row["games"], reverse=True)
    for index, row in enumerate(rows, 1):
        row["rank_position"] = index
    return {
        "season": _attach_snapshot_meta(_season_public(season, report), _report_dir(project_root, season)),
        "sort": sort,
        "accounts": rows,
        "models": _summarize_models(rows),
    }


def _read_rating_curve(report_dir: Path, account_id: str, max_points: int) -> list[dict[str, Any]]:
    curve_path = report_dir / "rating_curve.csv"
    if not curve_path.exists():
        return []
    points: list[dict[str, Any]] = []
    with curve_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("account_id") != account_id:
                continue
            try:
                point: dict[str, Any] = {
                    "games": int(float(row.get("games") or 0)),
                    "rating": float(row.get("rating") or 0.0),
                    "pt": float(row.get("pt") or 0.0),
                }
                if row.get("rank_id"):
                    point["rank_id"] = row["rank_id"]
                if row.get("rank_name"):
                    point["rank_name"] = row["rank_name"]
                if row.get("pt_target"):
                    point["pt_target"] = float(row["pt_target"])
                if row.get("rank_before"):
                    point["rank_before"] = row["rank_before"]
                if row.get("rank_after"):
                    point["rank_after"] = row["rank_after"]
                if row.get("transition"):
                    point["transition"] = row["transition"]
                points.append(point)
            except (TypeError, ValueError):
                continue
    if max_points <= 0:
        return []
    if max_points == 1:
        return points[-1:] if points else []
    if len(points) > max_points:
        stride = (len(points) - 1) / (max_points - 1)
        sampled = [points[round(i * stride)] for i in range(max_points - 1)]
        sampled.append(points[-1])
        seen: set[int] = set()
        deduped: list[dict[str, Any]] = []
        for point in sampled:
            marker = int(point["games"])
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(point)
        points = deduped
    return points


def _read_recent_games(report_dir: Path, account_id: str, limit: int) -> list[dict[str, Any]]:
    ledger_path = report_dir / "account_ledger.jsonl"
    if not ledger_path.exists() or limit <= 0:
        return []
    games: list[dict[str, Any]] = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("account_id") != account_id:
                continue
            games.append({
                "game_index": row.get("game_index"),
                "rank": row.get("rank"),
                "final_score": row.get("final_score"),
                "score_delta": row.get("score_delta"),
                "game_length": row.get("game_length"),
                "pt_tier": row.get("pt_tier"),
                "positive_pt": row.get("positive_pt"),
                "rank_before": row.get("rank_before"),
                "pt_before": row.get("pt_before"),
                "pt_delta": row.get("pt_delta"),
                "transition": row.get("transition"),
                "rank_after": row.get("rank_after"),
                "pt_after": row.get("pt_after"),
                "rating_before": row.get("rating_before"),
                "rating_after": row.get("rating_after"),
                "source_log": row.get("source_log"),
            })
    return list(reversed(games[-limit:]))


def load_account(
    project_root: Path,
    configs_dir: Path,
    season_id: str,
    account_id: str,
    *,
    recent_limit: int = 50,
    curve_max_points: int = 240,
) -> dict[str, Any]:
    season = get_season_config(configs_dir, season_id)
    report_dir = _report_dir(project_root, season)
    report, validated_rows = _load_validated_report(project_root, season)
    registry_index = _registry_account_index(season)
    scoring = report.get("scoring") if isinstance(report.get("scoring"), dict) else None
    rows = [_enrich_account_row(row, registry_index, scoring=scoring) for row in validated_rows]
    match = next((row for row in rows if row["account_id"] == account_id), None)
    if match is None:
        raise AccountNotFoundError(f"account 不存在: {account_id}")
    return {
        "season": _attach_snapshot_meta(_season_public(season, report), report_dir),
        "account": match,
        "rank_distribution": [match["rank_1"], match["rank_2"], match["rank_3"], match["rank_4"]],
        "curve": _read_rating_curve(report_dir, account_id, curve_max_points),
        "recent_games": _read_recent_games(report_dir, account_id, recent_limit),
    }


def _load_league_summary(project_root: Path, season: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    raw_path = season.get("league_summary")
    if not raw_path:
        return None
    summary_path = (project_root / str(raw_path)).resolve()
    if not summary_path.exists():
        return None
    try:
        payload = _read_json_cached(summary_path)
    except (OSError, json.JSONDecodeError):
        return None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, dict):
        return None
    entry = models.get(model_id)
    if not isinstance(entry, dict):
        return None
    return {
        "schema": payload.get("schema"),
        "games_total": payload.get("games_total"),
        "lineups": payload.get("lineups"),
        "model": entry,
    }


def load_model(project_root: Path, configs_dir: Path, season_id: str, model_id: str) -> dict[str, Any]:
    season = get_season_config(configs_dir, season_id)
    report_dir = _report_dir(project_root, season)
    report, validated_rows = _load_validated_report(project_root, season)
    registry_index = _registry_account_index(season)
    scoring = report.get("scoring") if isinstance(report.get("scoring"), dict) else None
    rows = [_enrich_account_row(row, registry_index, scoring=scoring) for row in validated_rows]
    model_rows = [row for row in rows if row["model_id"] == model_id]
    if not model_rows:
        raise ModelNotFoundError(f"model 不存在: {model_id}")
    model_rows.sort(key=lambda row: (-int(row["rank_ordinal"]), -row["pt_current"], -row["rating"], row["account_id"]))
    summary = next((item for item in _summarize_models(rows) if item["model_id"] == model_id), None)
    registry_model = next((m for m in _registry_models(season) if m.get("model_id") == model_id), {})
    return {
        "season": _attach_snapshot_meta(_season_public(season, report), report_dir),
        "model": {
            "model_id": model_id,
            "checkpoint": registry_model.get("checkpoint"),
            "accounts": model_rows,
            "summary": summary,
        },
        "league_summary": _load_league_summary(project_root, season, model_id),
    }
