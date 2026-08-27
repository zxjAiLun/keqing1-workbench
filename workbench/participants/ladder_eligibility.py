# -*- coding: utf-8 -*-
"""正式天梯资格 gate（R10 Production UX Repair 1 / P1-2 / R13-B / R14-C Repair 1）。

任何把 ``rating_eligible`` 置 true 的入口（Tenhou intake confirm / manual Match
create / Match revise）都必须先通过本校验，防止「成绩记错模型」：

**Legacy Path** (无 persisted Frozen Runtime Manifest):
- 赛季存在 + running + 启用 participants 正式投影（enabled + exclusive）；
- 四个账号均为正式赛季成员（season.models[*].accounts）；
- 人类账号必须属于 ``model_id=human``；
- 通过 ``resolve_execution_provenance`` 在 Catalog Current 中校验 stage/capability；
- 校验通过后返回每个座位的规范执行凭据。

**R14 Frozen Manifest Path** (有 persisted Frozen Runtime Manifest):
- 赛季存在 + running + 启用 participants 正式投影；
- Manifest 通过 ``validate_persisted_manifest_against_season`` 自洽性校验；
- 四个座位的 account/provenance 精确等于 Manifest 中对应 participant 的 frozen provenance；
- **不重新检查 Catalog/Account 当前 lifecycle** (Start 时已完成审查；Start 后 retire/disable 不能改写 Frozen Authority)；
- 只有提供可信 runtime/capture session evidence 时才构建 RuntimeMatchBinding。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .execution_provenance import (
    CanonicalExecutionProvenance,
    resolve_execution_provenance,
)
from .schemas import FrozenExecutionProvenance, RuntimeMatchBinding

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LadderAdmissionResult:
    season_id: str
    provenance_by_seat: dict[int, CanonicalExecutionProvenance]
    runtime_binding: RuntimeMatchBinding | None = None


def _season_account_model(season: dict[str, Any], account_id: str) -> dict | None:
    """账号所属赛季模型（model_id + checkpoint + execution_provenance + model_artifact_id + external_revision_id）。"""
    for model in season.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        for account in model.get("accounts", []) or []:
            if isinstance(account, dict) and str(account.get("account_id") or "") == account_id:
                return {
                    "model_id": str(model.get("model_id") or ""),
                    "checkpoint": model.get("checkpoint"),
                    "model_artifact_id": model.get("model_artifact_id"),
                    "external_revision_id": model.get("external_revision_id"),
                    "execution_provenance": model.get("execution_provenance"),
                }
    return None


def _resolve_checkpoint(checkpoint: Any, project_root: Path) -> Path | None:
    if not checkpoint:
        return None
    path = Path(str(checkpoint))
    from workbench.runtime.resolver import resolve_model_checkpoint

    try:
        return resolve_model_checkpoint(path, project_root)
    except FileNotFoundError:
        if not path.is_absolute():
            path = project_root / path
        return path.resolve()


def _artifact_record(registry, artifact_id: str) -> Any | None:
    """按 artifact_id 在 participants registry 查找产物对象。"""
    for identity in registry.list_models():
        for artifact in identity.artifacts:
            if artifact.model_artifact_id == artifact_id:
                return artifact
    return None


def _artifact_path(registry, artifact_id: str, project_root: Path) -> Path | None:
    """按 artifact_id 在 participants registry 查找产物路径。"""
    artifact = _artifact_record(registry, artifact_id)
    if artifact is not None:
        return _resolve_checkpoint(artifact.artifact_path, project_root)
    return None


def ensure_ladder_eligibility(
    season_id: str | None,
    seats: list[Any],
    *,
    registry,
    session_id: str | None = None,
    tenhou_log_id: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> LadderAdmissionResult:
    """统一硬不变量（UX Repair 2/3 / P1-2 / R13-B / R14-C Repair 1）：

    ``rating_eligible == true`` ⇒ ``season_id`` 必须是非空有效字符串
    ⇒ 必须通过 ``validate_ladder_eligibility``。

    返回 **LadderAdmissionResult**（含 normalized season_id、逐座 CanonicalExecutionProvenance 与 RuntimeMatchBinding），
    调用方必须用返回值回写 Match 的 season_id、execution_provenance 及 runtime_binding。
    """
    normalized = str(season_id or "").strip()
    if not normalized:
        raise ValueError("rating_eligible=true 必须指定正式赛季（season_id 不能为空）")
    provenance_map, runtime_binding = validate_ladder_eligibility(
        normalized, seats, registry=registry, session_id=session_id,
        tenhou_log_id=tenhou_log_id, project_root=project_root
    )
    return LadderAdmissionResult(
        season_id=normalized,
        provenance_by_seat=provenance_map,
        runtime_binding=runtime_binding,
    )


# ---------------------------------------------------------------------------
# Season-level checks common to both paths
# ---------------------------------------------------------------------------

def _validate_season_common(season: dict[str, Any], season_id: str) -> None:
    """赛季公共前置校验：running + participants enabled + exclusive."""
    if str(season.get("status") or "") != "running":
        raise ValueError(f"赛季 {season_id} 不是 running 状态，不能计入正式天梯")
    ingest = season.get("ingest") if isinstance(season.get("ingest"), dict) else {}
    participants_cfg = ingest.get("participants")
    if not (
        isinstance(participants_cfg, dict)
        and participants_cfg.get("enabled")
        and participants_cfg.get("exclusive")
    ):
        raise ValueError(f"赛季 {season_id} 未启用 participants 正式投影（需 ingest.participants.enabled+exclusive）")


# ---------------------------------------------------------------------------
# R14 Frozen Manifest Path — Manifest participant provenance is sole authority
# ---------------------------------------------------------------------------

def _validate_frozen_manifest_path(
    season: dict[str, Any],
    season_id: str,
    seats: list[Any],
    manifest_raw: dict,
    *,
    session_id: str | None = None,
    tenhou_log_id: str | None = None,
) -> tuple[dict[int, CanonicalExecutionProvenance], RuntimeMatchBinding | None]:
    """R14-C Repair 1: Frozen Runtime Manifest 为唯一执行权威。

    不重新检查 Catalog/Account 当前 lifecycle（Artifact stage, Account enabled, 模型绑定）。
    Start 时已完成审查；Start 后 retire/disable 不能改写 Frozen Authority。

    仅校验：
    1. Manifest self-integrity (via validate_persisted_manifest_against_season)
    2. 四座 account/provenance 精确等于 Manifest participant 的 frozen provenance
    3. 有可信 session evidence 时才构造 RuntimeMatchBinding
    """
    from workbench.runtime.manifest import validate_persisted_manifest_against_season

    manifest = validate_persisted_manifest_against_season(season, manifest_raw)
    manifest_participants_by_account = {p.account_id: p for p in manifest.participants}

    provenance_by_seat: dict[int, CanonicalExecutionProvenance] = {}
    seat_accounts_map: dict[int, str] = {}
    seat_frozen_prov_map: dict[int, FrozenExecutionProvenance] = {}

    for seat in seats:
        seat_no = getattr(seat, "seat", None)
        if seat_no is None:
            raise ValueError("座位缺少 seat 编号")
        account_id = str(getattr(seat, "account_id", None) or "")
        if not account_id:
            raise ValueError(f"座位 {seat_no} 未指派账号")

        # 不要求当前 Account 仍 enabled/存在——Manifest 是权威
        season_model = _season_account_model(season, account_id)
        if season_model is None:
            raise ValueError(f"账号 {account_id} 未在正式赛季 {season_id} 注册")

        m_part = manifest_participants_by_account.get(account_id)
        if not m_part:
            raise ValueError(
                f"账号 {account_id} 未包含在当前赛季的冻结运行时清单 "
                f"(manifest_id={manifest.manifest_id}) 中"
            )

        # 从 Manifest participant 取出权威 frozen provenance
        manifest_frozen_prov = m_part.execution_provenance

        # 比较 seat 显式声明的 provenance 与 Manifest 的权威 provenance
        seat_art = getattr(seat, "model_artifact_id", None)
        seat_ident = getattr(seat, "model_identity_id", None)
        seat_rev = getattr(seat, "external_revision_id", None)

        # 如果 seat 带了显式 provenance 字段，必须与 Manifest 精确匹配
        if manifest_frozen_prov.kind == "human":
            if seat_art or seat_ident or seat_rev:
                raise ValueError(
                    f"座位 {seat_no} 账号 {account_id}: Manifest 标记为真人座位，"
                    "不能携带模型证据"
                )
        elif manifest_frozen_prov.kind == "local_artifact":
            if seat_art and seat_art != manifest_frozen_prov.model_artifact_id:
                raise ValueError(
                    f"座位 {seat_no} 模型产物 {seat_art} 与 Frozen Runtime Manifest "
                    f"不一致 (manifest={manifest_frozen_prov.model_artifact_id})"
                )
            if seat_ident and seat_ident != manifest_frozen_prov.model_identity_id:
                raise ValueError(
                    f"座位 {seat_no} 模型身份 {seat_ident} 与 Frozen Runtime Manifest "
                    f"不一致 (manifest={manifest_frozen_prov.model_identity_id})"
                )
        elif manifest_frozen_prov.kind == "external_revision":
            if seat_rev and seat_rev != manifest_frozen_prov.external_revision_id:
                raise ValueError(
                    f"座位 {seat_no} 外部版本 {seat_rev} 与 Frozen Runtime Manifest "
                    f"不一致 (manifest={manifest_frozen_prov.external_revision_id})"
                )
            if seat_ident and seat_ident != manifest_frozen_prov.model_identity_id:
                raise ValueError(
                    f"座位 {seat_no} 模型身份 {seat_ident} 与 Frozen Runtime Manifest "
                    f"不一致 (manifest={manifest_frozen_prov.model_identity_id})"
                )

        # 使用 Manifest 的 frozen provenance 作为本座位的权威凭据
        # 构造 CanonicalExecutionProvenance（用于 freeze_execution_provenance 输出）
        can_prov = CanonicalExecutionProvenance(
            kind=manifest_frozen_prov.kind,
            account_id=account_id,
            model_identity_id=manifest_frozen_prov.model_identity_id,
            model_artifact_id=manifest_frozen_prov.model_artifact_id,
            external_revision_id=manifest_frozen_prov.external_revision_id,
        )

        # R14-C Repair 2 (P1-2): controller 由 Manifest participant canonicalize
        # （seat 声明与 Manifest 冲突时以 Manifest 为准——执行类型是冻结事实）
        manifest_controller = str(m_part.controller_type or "")
        if manifest_controller and hasattr(seat, "controller_type"):
            seat.controller_type = manifest_controller  # type: ignore[attr-defined]

        # 双模块路径防御：manifest 可能通过 workbench.participants.schemas 导入
        # FrozenExecutionProvenance（与本模块相对导入不同类），统一 revalidate
        local_frozen_prov = FrozenExecutionProvenance.model_validate(
            manifest_frozen_prov.model_dump()
        )

        seat_accounts_map[int(seat_no)] = account_id
        provenance_by_seat[int(seat_no)] = can_prov
        seat_frozen_prov_map[int(seat_no)] = local_frozen_prov

    # R14-C Repair 2 (P1-4): RuntimeMatchBinding 只在有可信 R14-B execution
    # session evidence 时构造。session 必须是 supervisor spawn epoch 持久化的
    # keqing.runtime.execution_session.v1（旧 Play-with-you binding.json 是
    # legacy，不再被当成 R14 runtime evidence）。
    # 显式/自动发现 session 后证据缺失或错配 → fail-closed 抛错，绝不
    # 静默降级成 runtime_binding=None 后继续正式入账。
    runtime_binding: RuntimeMatchBinding | None = None
    if session_id is not None:
        _verify_execution_session_evidence(
            session_id, manifest, season_id, seat_accounts_map,
            tenhou_log_id=tenhou_log_id,
        )
        from workbench.participants.paths import now_iso

        runtime_binding = RuntimeMatchBinding(
            season_id=manifest.season_id,
            manifest_id=manifest.manifest_id,
            season_authority_hash=manifest.season_authority_hash,
            session_id=session_id,
            seat_provenance=seat_frozen_prov_map,
            seat_accounts=seat_accounts_map,
            admitted_at=now_iso(),
        )

    return provenance_by_seat, runtime_binding


def _verify_execution_session_evidence(
    session_id: str,
    manifest,
    season_id: str,
    seat_accounts_map: dict[int, str] | None = None,
    *,
    tenhou_log_id: str | None = None,
) -> None:
    """验证 R14-B execution session 与当前 Season/Manifest/seats/exact log 完全一致。

    Fail-closed（R14-C Repair 2/3 / P1-4 / P1-1 / P2）——任何不一致直接抛 ValueError：
    1. session 必须是 ``keqing.runtime.execution_session.v1``（supervisor
       spawn epoch 持久化）；旧 Play-with-you binding.json 不再被接受；
    2. ``season_id`` / ``manifest_id`` / ``season_authority_hash`` 必须与
       当前 persisted Manifest 精确一致；
    3. session participant 与 Match seats 逐 account exact compare
       ``controller_type + FrozenExecutionProvenance``（不只比账号集合）；
    4. 传入 ``tenhou_log_id`` 时，该 exact log 必须被 session 的**全部**
       ``local_model`` participants 观察过（每个都有独立 observation 证据），
       证明整组本地 worker 真进入了这一桌。
    """
    from workbench.runtime.execution_session import (
        load_execution_session,
        session_observer_ids,
    )

    session = load_execution_session(session_id)
    if session is None:
        raise ValueError(
            f"runtime session {session_id} 不是有效的 R14-B execution session "
            "(缺少 keqing.runtime.execution_session.v1 记录)——"
            "R14 赛季的 runtime-bound 对局必须有真实执行会话证据"
        )

    if session.season_id != season_id:
        raise ValueError(
            f"runtime session {session_id} 属于赛季 {session.season_id}，"
            f"与当前赛季 {season_id} 不一致"
        )
    if session.manifest_id != manifest.manifest_id:
        raise ValueError(
            f"runtime session {session_id} 的 manifest ({session.manifest_id}) "
            f"与当前 Frozen Runtime Manifest ({manifest.manifest_id}) 不一致"
        )
    if session.season_authority_hash != manifest.season_authority_hash:
        raise ValueError(
            f"runtime session {session_id} 的 season_authority_hash 与当前 "
            "Frozen Runtime Manifest 不一致"
        )

    # 逐 account exact compare controller + frozen provenance
    session_by_account = {p.account_id: p for p in session.participants}
    if seat_accounts_map is not None:
        seat_accounts = set(seat_accounts_map.values())
        if set(session_by_account) != seat_accounts:
            raise ValueError(
                f"runtime session {session_id} 的参与者集合 ({sorted(session_by_account)}) "
                f"与对局座位账号 ({sorted(seat_accounts)}) 不一致"
            )
        # 与 Manifest participant 逐 account 精确比对（session 是 Manifest 的
        # 执行快照——controller/provenance 不得有任何漂移，P2）
        manifest_by_account = {p.account_id: p for p in manifest.participants}
        for aid, s_part in session_by_account.items():
            m_part = manifest_by_account.get(aid)
            if m_part is None:
                raise ValueError(
                    f"runtime session {session_id} 的参与者 {aid} 不在当前 "
                    "Frozen Runtime Manifest 中"
                )
            if s_part.controller_type != m_part.controller_type:
                raise ValueError(
                    f"runtime session {session_id} 的参与者 {aid} controller "
                    f"({s_part.controller_type}) 与 Manifest "
                    f"({m_part.controller_type}) 不一致"
                )
            if s_part.execution_provenance != m_part.execution_provenance:
                raise ValueError(
                    f"runtime session {session_id} 的参与者 {aid} execution_provenance "
                    "与 Frozen Runtime Manifest 不一致"
                )

    # R14-C Repair 3 (P1-1): exact log observation——该局必须被 session 的
    # 全部 local_model participants 观察过（整组本地 worker 进入这一桌）。
    if tenhou_log_id:
        observers = set(session_observer_ids(session_id, tenhou_log_id))
        required_observers = {
            p.account_id for p in session.participants
            if p.controller_type == "local_model"
        }
        missing = required_observers - observers
        if missing:
            raise ValueError(
                f"runtime session {session_id} 没有观察到对局 {tenhou_log_id} 的"
                f"完整执行证据（缺少 local worker 观测: {sorted(missing)}）——"
                "该对局不能被证明来自这次 runtime execution"
            )


# ---------------------------------------------------------------------------
# Legacy Path — no persisted Frozen Runtime Manifest
# ---------------------------------------------------------------------------

def _validate_legacy_path(
    season: dict[str, Any],
    season_id: str,
    seats: list[Any],
    *,
    registry,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[int, CanonicalExecutionProvenance], None]:
    """Legacy 校验路径：无 persisted Frozen Runtime Manifest，通过 Catalog Current 校验。"""
    provenance_by_seat: dict[int, CanonicalExecutionProvenance] = {}

    for seat in seats:
        seat_no = getattr(seat, "seat", None)
        if seat_no is None:
            raise ValueError("座位缺少 seat 编号")
        account_id = str(getattr(seat, "account_id", None) or "")
        if not account_id:
            raise ValueError(f"座位 {seat_no} 未指派账号")
        account = registry.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        season_model = _season_account_model(season, account_id)
        if season_model is None:
            raise ValueError(f"账号 {account_id} 未在正式赛季 {season_id} 注册")

        # 提取赛季级别配置
        season_model_id = season_model.get("model_id")
        season_ckpt = season_model.get("checkpoint")
        season_art_id = season_model.get("model_artifact_id")
        season_rev_id = season_model.get("external_revision_id")
        season_exec_prov = season_model.get("execution_provenance")

        if isinstance(season_exec_prov, dict):
            if not season_art_id:
                season_art_id = season_exec_prov.get("model_artifact_id")
            if not season_rev_id:
                season_rev_id = season_exec_prov.get("external_revision_id")
            if not season_model_id and season_exec_prov.get("model_identity_id"):
                season_model_id = season_exec_prov.get("model_identity_id")

        if account.account_type == "human":
            if season_model_id != "human" and (
                not isinstance(season_exec_prov, dict) or season_exec_prov.get("kind") != "human"
            ):
                raise ValueError(
                    f"人类账号 {account_id} 必须属于 model_id=human（当前属于 {season_model_id or '未知'}）"
                )
            if (
                getattr(seat, "model_identity_id", None)
                or getattr(seat, "model_artifact_id", None)
                or getattr(seat, "external_revision_id", None)
            ):
                raise ValueError(f"正式人类座位 {account_id} 不能携带冻结 bot 模型产物")
            controller_type = getattr(seat, "controller_type", None)
            if controller_type and controller_type != "human_ui":
                raise ValueError(f"正式人类座位 {account_id} 的控制器必须为 human_ui")

        # 严格 identity 判定：
        seat_ident = getattr(seat, "model_identity_id", None)
        if seat_ident is not None:
            identity_id = seat_ident
        elif account.model_identity_id is not None:
            identity_id = account.model_identity_id
        else:
            identity_id = season_model_id if account.account_type != "human" else None

        controller_type = getattr(seat, "controller_type", None)
        artifact_id = getattr(seat, "model_artifact_id", None) or season_art_id
        external_revision_id = getattr(seat, "external_revision_id", None) or season_rev_id
        ckpt_for_resolution = season_ckpt if account.account_type == "managed_bot" else None

        resolved_prov = resolve_execution_provenance(
            account_id=account_id,
            controller_type=controller_type,
            identity_id=identity_id,
            artifact_id=artifact_id,
            external_revision_id=external_revision_id,
            checkpoint=ckpt_for_resolution,
            purpose="ladder_eligible",
            project_root=project_root,
            registry=registry,
        )

        # -----------------------------------------------------------------
        # 严格校验：Seat 实际 provenance 是否等于 Season freeze 的 provenance (R13-B Invariant 5)
        # -----------------------------------------------------------------
        if isinstance(season_exec_prov, dict):
            season_kind = season_exec_prov.get("kind")
            if season_kind and resolved_prov.kind != season_kind:
                raise ValueError(
                    f"座位 {seat_no} 执行类型 {resolved_prov.kind} 与赛季冻结类型 {season_kind} 不一致"
                )
            season_ident = season_exec_prov.get("model_identity_id")
            if season_ident and resolved_prov.model_identity_id != season_ident:
                raise ValueError(
                    f"座位 {seat_no} 模型身份 {resolved_prov.model_identity_id} 与赛季冻结身份 {season_ident} 不一致"
                )
            season_frozen_art = season_exec_prov.get("model_artifact_id")
            if season_frozen_art and resolved_prov.model_artifact_id != season_frozen_art:
                raise ValueError(
                    f"座位 {seat_no} 模型产物 {resolved_prov.model_artifact_id} 与赛季冻结产物 {season_frozen_art} 不一致"
                )
            season_frozen_rev = season_exec_prov.get("external_revision_id")
            if season_frozen_rev and resolved_prov.external_revision_id != season_frozen_rev:
                raise ValueError(
                    f"座位 {seat_no} 外部版本 {resolved_prov.external_revision_id} 与赛季冻结版本 {season_frozen_rev} 不一致"
                )

        if season_rev_id and resolved_prov.external_revision_id != season_rev_id:
            raise ValueError(
                f"座位 {seat_no} 外部版本 {resolved_prov.external_revision_id} 与赛季指定版本 {season_rev_id} 不一致"
            )
        if season_art_id and resolved_prov.model_artifact_id != season_art_id:
            raise ValueError(
                f"座位 {seat_no} 模型产物 {resolved_prov.model_artifact_id} 与赛季指定产物 {season_art_id} 不一致"
            )

        provenance_by_seat[int(seat_no)] = resolved_prov

    return provenance_by_seat, None


# ---------------------------------------------------------------------------
# Main entry point — dispatches to frozen-manifest or legacy path
# ---------------------------------------------------------------------------

def validate_ladder_eligibility(
    season_id: str,
    seats: list[Any],
    *,
    registry,
    session_id: str | None = None,
    tenhou_log_id: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> tuple[dict[int, CanonicalExecutionProvenance], RuntimeMatchBinding | None]:
    """正式天梯资格校验；不通过抛 ValueError（带中文原因）。返回 (逐座规范执行凭据, RuntimeMatchBinding).

    R14-C Repair 1: 有 persisted Frozen Runtime Manifest 时走 frozen-manifest path
    (Manifest 为唯一权威，不重检当前 Catalog/Account lifecycle);
    否则走 legacy path (通过 Catalog Current 校验)。
    R14-C Repair 3 (P1-1): ``tenhou_log_id`` 参与 execution session 证据校验
    ——该 exact log 必须被 session 的全部 local workers 观察过。
    """
    from workbench.replay import ladder as ladder_data

    configs_dir = ladder_data.resolve_config_dir(project_root)
    try:
        season = ladder_data.get_season_config(configs_dir, season_id)
    except ladder_data.SeasonNotFoundError as exc:
        raise ValueError(f"正式赛季不存在: {season_id}") from exc

    _validate_season_common(season, season_id)

    manifest_raw = season.get("runtime_manifest")
    if manifest_raw is not None:
        return _validate_frozen_manifest_path(
            season, season_id, seats, manifest_raw,
            session_id=session_id,
            tenhou_log_id=tenhou_log_id,
        )
    else:
        return _validate_legacy_path(
            season, season_id, seats,
            registry=registry, project_root=project_root,
        )


def season_has_frozen_manifest(season_id: str, project_root: Path = PROJECT_ROOT) -> bool:
    """判断赛季是否带有 persisted Frozen Runtime Manifest（R14 frozen-manifest path 的标志）。

    供 ledger 的通用校验层区分：R14 冻结权威赛季的 rating-eligible 写入不应被
    Start 之后的 Account disable / Artifact retire 等当前 lifecycle 变化阻塞。
    """
    season_id = (season_id or "").strip()
    if not season_id:
        return False
    from workbench.replay import ladder as ladder_data

    configs_dir = ladder_data.resolve_config_dir(project_root)
    try:
        season = ladder_data.get_season_config(configs_dir, season_id)
    except ladder_data.SeasonNotFoundError:
        return False
    return season.get("runtime_manifest") is not None


__all__ = [
    "LadderAdmissionResult",
    "ensure_ladder_eligibility",
    "season_has_frozen_manifest",
    "validate_ladder_eligibility",
]
