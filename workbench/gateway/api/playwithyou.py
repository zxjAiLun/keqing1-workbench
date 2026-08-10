"""Play-with-you backend: summon Mortal-weight bot accounts into a Tenhou
private room (e.g. L2147) as NoName guests, driven from the GUI.

This mirrors mjai.ekyu.moe's "Play with you": the user types a Tenhou lobby
ID, picks a Speed, chooses a Mortal network per AI seat, and clicks a button.
The backend spawns ``workbench/launch_tenhou_bots.py`` as one owned subprocess
(which boots one local mjai-gateway) and streams its logs back to the GUI.

Design notes:
* Only ONE play-with-you session may be active at a time. The launcher always
  starts its own gateway on TCP 11600, so a second concurrent session would
  fail to bind that port. We reject new starts while one is running (the user
  must stop the previous one first).
 * UI sends human-friendly network ids (``mortal`` / ``70k`` / ``ext_mortal`` /
  ``none`` / ``custom``); we translate them into launcher specs
  (named checkpoints or absolute ``.pth`` paths) here, keeping the frontend
  dumb.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from project_data import data_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/battle/playwithyou")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = PROJECT_ROOT / "workbench" / "launch_tenhou_bots.py"

# "Speed" selector -> per-turn think delay (seconds) handed to the bots.
SPEED_PRESETS: Dict[str, float] = {
    "slow": 2.0,
    "normal": 0.8,
    "fast": 0.3,
    "turbo": 0.0,
}

# UI network id -> launcher spec. Custom paths are resolved to absolute files;
# the rest are named local checkpoints.
NETWORK_TO_SPEC: Dict[str, str] = {
    "mortal": "mortal",
    "70k": "70k",
    "ext_mortal": "ext_mortal",
}

# R11-B：Play-with-you 运行时模型目录。只暴露具名 Mortal checkpoints
# （70k / ext_mortal）；不读 Participants registry，后续从 keqing-data
# authoritative 扩展时再扩充此目录。
PLAYWITHYOU_MODEL_CATALOG: list[dict] = [
    {"model_id": "70k", "label": "70k"},
    {"model_id": "ext_mortal", "label": "ext_mortal"},
]
_PLAYWITHYOU_MODEL_IDS = {entry["model_id"] for entry in PLAYWITHYOU_MODEL_CATALOG}

# Hard cap on remembered log lines per session (bound memory; GUI shows a tail).
MAX_LOG_LINES = 4000
PWY_LOG_DIR = data_path("logs", "playwithyou")

_LOCK = threading.Lock()
# 单进程内 source 文件 create-if-absent 的并发保护（P2-3：并发 confirm 不得互相覆盖）。
_SOURCE_WRITE_LOCK = threading.Lock()
# session_id -> PWYSession
SESSIONS: Dict[str, "PWYSession"] = {}
# Keep a short history of finished sessions so the GUI can still show the last run.
_HISTORY: List[str] = []
MAX_HISTORY = 5

# Marker arguments make orphan cleanup precise: never kill a gateway a user
# started manually outside this page.
SESSION_TOKEN = "playwithyou-session"
GATEWAY_OWNER_TOKEN = "playwithyou-gateway"


# ---------------------------------------------------------------------------
# Session model
# ---------------------------------------------------------------------------


class PWYSession:
    def __init__(
        self,
        *,
        session_id: str,
        proc: subprocess.Popen[str],
        command: List[str],
        lobby_id: str,
        specs: List[str],
        names: List[str],
        speed: str,
        device: str,
        started_at: float,
    ) -> None:
        self.session_id = session_id
        self.proc = proc
        self.command = command
        self.lobby_id = lobby_id
        self.specs = specs
        self.names = names
        self.speed = speed
        self.device = device
        self.started_at = started_at
        self.capture_dir: Optional[Path] = None
        self.binding: Optional[dict] = None
        # P2（UX Repair 2）：roster 模式冻结后的四人阵容（account/model/expected_name）
        self.frozen_roster: Optional[List[dict]] = None
        self.log_lines: List[str] = []
        # Keep the original launcher output outside the FastAPI process.  The
        # status endpoint is intentionally in-memory for simplicity, but a
        # server restart or an abrupt launcher exit must not erase the useful
        # evidence needed to diagnose a live Tenhou table.
        PWY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = PWY_LOG_DIR / f"{started_at:.0f}-{session_id}.log"
        self._reader = threading.Thread(target=self._pump_logs, daemon=True)
        self._reader.start()

    def _pump_logs(self) -> None:
        assert self.proc.stdout is not None
        try:
            with self.log_path.open("a", encoding="utf-8", buffering=1) as log_file:
                for line in self.proc.stdout:
                    clean_line = line.rstrip("\n")
                    self.log_lines.append(clean_line)
                    log_file.write(clean_line + "\n")
                    if len(self.log_lines) > MAX_LOG_LINES:
                        # Drop oldest in bulk to keep the append cheap.
                        del self.log_lines[: MAX_LOG_LINES // 2]
        except Exception:
            logger.exception("failed to persist play-with-you launcher output")
        rc = self.proc.poll()
        ended_line = (
            f"[session ended with returncode {rc}]"
            if rc is not None
            else "[session ended]"
        )
        self.log_lines.append(ended_line)
        try:
            with self.log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(ended_line + "\n")
        except Exception:
            logger.exception("failed to persist play-with-you session end")

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def tail(self, n: int = 200) -> List[str]:
        return self.log_lines[-n:]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_spec(network: str, custom_paths: Dict[int, str], slot: int) -> Optional[str]:
    """Map a UI network id to a launcher spec, or None for "none"."""
    network = (network or "none").strip().lower()
    if network in ("none", ""):
        return None
    if network == "custom":
        raw = (custom_paths or {}).get(slot, "")
        if not raw or not raw.strip():
            raise ValueError(f"slot {slot + 1}: custom network selected but no path given")
        path = Path(raw.strip())
        if not path.is_absolute() or not path.exists():
            raise FileNotFoundError(
                f"slot {slot + 1}: custom checkpoint not found: {raw}"
            )
        return str(path.resolve())
    if network in NETWORK_TO_SPEC:
        return NETWORK_TO_SPEC[network]
    # Allow passing a raw launcher spec (named checkpoint or explicit path) too.
    return network


def _ladder_data_root() -> Path:
    raw = os.environ.get("KEQING_LADDER_DATA_ROOT", "").strip()
    return Path(raw) if raw else data_path("ladder")


def _ladder_config_dir() -> Path:
    from replay import ladder as ladder_data

    return ladder_data.resolve_config_dir(PROJECT_ROOT)


def _spec_to_model_id(spec: str, project_root: Path) -> Optional[str]:
    """把 launcher spec 映射到正式赛季的 model_id（用于账号归属校验）。

    已知命名 spec 直接映射；自定义 checkpoint 需在调用方与 registry
    checkpoint 逐字比较 resolved path。
    """
    known = {"70k": "70k", "ext_mortal": "ext_mortal", "V3_74000": "V3_74000", "V2_74000": "V2_74000"}
    if spec in known:
        return known[spec]
    if spec == "mortal":
        return None  # 不在正式赛季模型池
    return None  # 自定义路径由调用方与 registry checkpoint 比对


def _validate_roster_bindings(roster_bindings: List[dict], specs: List[str]) -> None:
    """P1-2：呼出前可信冻结 roster。

    - 已登记账号必须存在且启用、账号不重复（有账号时才校验）；
    - launcher 参与者必须选模型身份+产物；有账号时 controller 可启动且
      identity 属于账号；launcher_slot 唯一、expected_raw_name 唯一；
    - Play-with-you simplification：launcher 可**不带账号**（只呼出模型，
      账号/坐席由赛后 intake 决定），此时跳过账号/controller 校验。
    - 未识别外部参与者（无账号且未呼出）必须 resolution_required=true。
    """
    from participants import registry as participant_registry

    known_ids = [str(entry.get("account_id") or "").strip() for entry in roster_bindings if entry.get("account_id")]
    if len(set(known_ids)) != len(known_ids):
        raise ValueError("已登记账号必须互不重复")
    for account_id in known_ids:
        account = participant_registry.get_account(account_id)
        if account is None:
            raise ValueError(f"账号不存在: {account_id}")
        if not account.enabled:
            raise ValueError(f"账号已停用: {account_id}")

    launched_slots: List[int] = []
    raw_names: List[str] = []
    for entry in roster_bindings:
        slot = entry.get("launcher_slot")
        if slot is None:
            if not entry.get("account_id") and not entry.get("resolution_required") and not entry.get("model_id"):
                raise ValueError("未识别的外部参与者必须设置 resolution_required=true")
            continue
        if slot in launched_slots:
            raise ValueError(f"launcher_slot 重复: {slot}")
        launched_slots.append(slot)
        account_id = str(entry.get("account_id") or "").strip()
        identity_id = entry.get("model_identity_id")
        artifact_id = entry.get("model_artifact_id")
        model_id = str(entry.get("model_id") or "").strip()
        if model_id:
            # R11-B：model-only launcher——model_id 必须在运行时模型目录中。
            if model_id not in _PLAYWITHYOU_MODEL_IDS:
                raise ValueError(
                    f"未知模型: {model_id!r}（可选: {sorted(_PLAYWITHYOU_MODEL_IDS)}）"
                )
            continue
        if not (identity_id and artifact_id):
            if not account_id:
                # model-only launcher 必须显式选模型
                raise ValueError(f"launcher 参与者必须选择模型身份与产物（slot {slot}）")
            controller = str(entry.get("controller_type") or "")
            if controller == "external_agent":
                # 外部代理没有 networks fallback：无 artifact 不能由本系统启动
                raise ValueError(
                    f"由本系统呼出的外部代理必须选择可启动的模型产物（slot {slot}）"
                )
            if controller != "local_model":
                raise ValueError(
                    f"launcher 参与者 controller_type 必须为 local_model 或 external_agent（slot {slot}）"
                )
            # account-backed local_model 未显式选模型 → 走旧 networks[slot] fallback（backcompat）
            raw_name = str(entry.get("expected_raw_name") or "").strip()
            if raw_name:
                if raw_name in raw_names:
                    raise ValueError(f"expected_raw_name 重复: {raw_name}")
                raw_names.append(raw_name)
            continue
        if account_id:
            # account-backed：controller 必须可启动 + identity 必须属于账号
            controller = str(entry.get("controller_type") or "")
            if controller not in ("local_model", "external_agent"):
                raise ValueError(
                    f"launcher 参与者 controller_type 必须为 local_model 或 external_agent（slot {slot}）"
                )
            if not participant_registry.identity_belongs_to_account(identity_id, account_id):
                raise ValueError(f"模型身份 {identity_id} 不属于账号 {account_id}")
        if not participant_registry.artifact_belongs_to_identity(identity_id, artifact_id):
            raise ValueError(f"模型产物 {artifact_id} 不属于身份 {identity_id}")
        raw_name = str(entry.get("expected_raw_name") or "").strip()
        if raw_name:
            if raw_name in raw_names:
                raise ValueError(f"expected_raw_name 重复: {raw_name}")
            raw_names.append(raw_name)
    if len(launched_slots) != len(specs):
        raise ValueError(f"launcher 数量与启动配置不一致（{len(launched_slots)} vs {len(specs)}）")


def _resolve_artifact_path(artifact, project_root: Path) -> Path:
    """artifact 的规范化绝对路径。

    相对路径依次尝试 ``project_root`` 与共享 keqing-data 根
    （``data_root()``），再回落到 authoritative Mortal 模型目录按文件名匹配，
    见 ``inference.bot_registry.resolve_model_checkpoint``。
    """
    from inference.bot_registry import resolve_model_checkpoint

    try:
        return resolve_model_checkpoint(str(artifact.artifact_path or ""), project_root)
    except FileNotFoundError as exc:
        raise ValueError(f"artifact checkpoint 不存在: {exc}") from exc


def _artifact_spec_for_launcher(identity_id: str, artifact_id: str) -> str:
    """Play-with-you simplification：account-less launcher——只校验 artifact 属于
    identity 并返回绝对 checkpoint 路径（不涉及账号归属）。"""
    from participants import registry as participant_registry

    identity = participant_registry.get_model_identity(identity_id)
    if identity is None:
        raise ValueError(f"model identity not found: {identity_id}")
    artifact = next((a for a in identity.artifacts if a.model_artifact_id == artifact_id), None)
    if artifact is None:
        raise ValueError(f"model identity {identity_id} 无产物 {artifact_id}")
    path = _resolve_artifact_path(artifact, PROJECT_ROOT)
    if not path.exists():
        raise ValueError(f"artifact checkpoint 不存在: {path}")
    return str(path)


def _artifact_spec_for_seat(account_id: str, identity_id: str, artifact_id: str) -> str:
    """seat 直选 identity+artifact → 返回 artifact 绝对路径作为 checkpoint 来源（P1-3）。

    旧 network spec（networks[slot]）只做 backend compatibility；生产 UI 的
    launcher 请求直接由所选 artifact 派生冻结 checkpoint。
    """
    from participants import registry as participant_registry

    identity = participant_registry.get_model_identity(identity_id)
    if identity is None:
        raise ValueError(f"model identity not found: {identity_id}")
    if not participant_registry.identity_belongs_to_account(identity_id, account_id):
        raise ValueError(f"账号 {account_id} 不能使用模型身份 {identity_id}")
    artifact = next((a for a in identity.artifacts if a.model_artifact_id == artifact_id), None)
    if artifact is None:
        raise ValueError(f"model identity {identity_id} 无产物 {artifact_id}")
    path = _resolve_artifact_path(artifact, PROJECT_ROOT)
    if not path.exists():
        raise ValueError(f"artifact checkpoint 不存在: {path}")
    return str(path)


def _freeze_launcher_models(roster_bindings: List[dict], specs: List[str]) -> tuple[List[dict], List[str]]:
    """为 launcher 参与者冻结模型身份/产物（P1-2 checkpoint 精确匹配）。

    每个 launcher 先以真实 ``resolve_bot_spec`` 得到实际 checkpoint 绝对路径，
    再按 artifact.artifact_path 精确匹配（禁止凭 spec 名称或 artifacts[0] 猜测）：
    - 未提供 identity/artifact：必须唯一匹配到一个合法 artifact；
    - 已提供：必须与实际 resolved_path 精确一致；
    - ``mortal`` 自动识别 V2 candidate / 70k fallback；
    - custom 必须匹配已登记 artifact，否则启动前拒绝。

    返回 ``(frozen_roster, frozen_launcher_specs)``：
    - ``frozen_roster`` 保持**输入 roster 顺序**（仅回填模型字段 + resolved_checkpoint_path）；
    - ``frozen_launcher_specs`` 为按 launcher_slot 排序的**绝对 checkpoint 路径**，
      直接传给 launcher（child/runtime 不再按动态名字重新解析）。
    """
    from inference.bot_registry import resolve_bot_spec
    from participants import registry as participant_registry

    # launcher_slot → spec（specs 已按 launcher_slot 排序）
    launched_slots = sorted(
        int(entry["launcher_slot"])
        for entry in roster_bindings
        if entry.get("launcher_slot") is not None
    )
    if len(launched_slots) != len(specs):
        raise ValueError("launcher 数量与启动配置不一致")
    slot_to_spec = dict(zip(launched_slots, specs))

    frozen: List[dict] = []
    for entry in roster_bindings:
        slot = entry.get("launcher_slot")
        if slot is None:
            frozen.append(entry)
            continue
        spec = slot_to_spec[int(slot)]
        account_id = str(entry.get("account_id") or "").strip()
        req_identity = entry.get("model_identity_id")
        req_artifact = entry.get("model_artifact_id")
        model_id = str(entry.get("model_id") or "").strip()
        if model_id:
            # R11-B：model-only launcher——checkpoint 已由 start 阶段 resolve_bot_spec
            # 冻结为绝对路径（launcher spec 与 frozen 路径同源）。
            frozen.append(
                {
                    **entry,
                    "model_id": model_id,
                    "resolved_checkpoint_path": str(Path(spec).resolve()),
                }
            )
            continue
        if not account_id:
            # Play-with-you simplification：account-less launcher——所选 artifact 即 checkpoint
            if not (req_identity and req_artifact):
                raise ValueError(f"launcher slot {slot} 必须选择模型身份与产物")
            identity = participant_registry.get_model_identity(req_identity)
            if identity is None:
                raise ValueError(f"model identity not found: {req_identity}")
            artifact = next((a for a in identity.artifacts if a.model_artifact_id == req_artifact), None)
            if artifact is None:
                raise ValueError(f"model identity {req_identity} 无产物 {req_artifact}")
            resolved_path = _resolve_artifact_path(artifact, PROJECT_ROOT)
            frozen.append(
                {
                    **entry,
                    "model_identity_id": req_identity,
                    "model_artifact_id": req_artifact,
                    "resolved_checkpoint_path": str(resolved_path),
                }
            )
            continue
        try:
            _kind, resolved_path = resolve_bot_spec(spec, PROJECT_ROOT)
        except (ValueError, FileNotFoundError) as exc:
            raise ValueError(f"launcher 账号 {account_id} 的 spec {spec!r} 无法解析: {exc}") from exc
        resolved_path = Path(str(resolved_path)).resolve()

        # 候选：账号可绑定的 identity 中，artifact 绝对路径与真实 checkpoint 精确一致
        candidates: List[tuple] = []
        for identity in participant_registry.list_models():
            if not participant_registry.identity_belongs_to_account(identity.model_identity_id, account_id):
                continue
            for artifact in identity.artifacts:
                try:
                    if _resolve_artifact_path(artifact, PROJECT_ROOT) == resolved_path:
                        candidates.append((identity, artifact))
                except (TypeError, ValueError):
                    continue

        req_identity = entry.get("model_identity_id")
        req_artifact = entry.get("model_artifact_id")
        if req_identity or req_artifact:
            # 已提供：必须与实际 resolved_path 精确一致
            matching = [
                (identity, artifact) for (identity, artifact) in candidates
                if identity.model_identity_id == req_identity and artifact.model_artifact_id == req_artifact
            ]
            if not matching:
                raise ValueError(
                    f"launcher 账号 {account_id} 的模型产物与实际 checkpoint 不一致（spec={spec}）"
                )
            identity, artifact = matching[0]
        else:
            if len(candidates) != 1:
                raise ValueError(
                    f"launcher 账号 {account_id} 的 checkpoint 无法唯一匹配模型产物"
                    f"（{len(candidates)} 个候选，spec={spec}），请显式指定"
                )
            identity, artifact = candidates[0]
        frozen.append(
            {
                **entry,
                "model_identity_id": identity.model_identity_id,
                "model_artifact_id": artifact.model_artifact_id,
                "resolved_checkpoint_path": str(resolved_path),
            }
        )
    frozen_launcher_specs = [
        str(entry["resolved_checkpoint_path"])
        for entry in sorted(
            (entry for entry in frozen if entry.get("launcher_slot") is not None),
            key=lambda entry: int(entry["launcher_slot"]),
        )
    ]
    return frozen, frozen_launcher_specs


def _rollback_roster_start(capture_dir: Optional[Path], alias_ids: List[str]) -> None:
    """回滚本次 roster 启动：删除 capture 目录与已注册 session aliases（P2-1）。"""
    import shutil as _shutil

    if capture_dir is not None:
        _shutil.rmtree(capture_dir, ignore_errors=True)
    if not alias_ids:
        return
    from participants import aliases as participant_aliases

    for alias_id in alias_ids:
        try:
            participant_aliases.delete_alias(alias_id)
        except Exception:
            pass


def _validate_ladder_capture(capture: LadderCaptureRequest, specs: List[str]) -> None:
    """启动 subprocess 前的正式天梯绑定校验（C2 gate）。"""
    if not capture.enabled:
        return
    if capture.mode != "confirm":
        raise ValueError("本轮 ladder_capture 只支持 mode=confirm")
    if len(specs) != 3:
        raise ValueError("正式天梯捕获要求恰好 1 人类 + 3 bot（quantity=3）")
    season_id = (capture.season_id or "").strip()
    if not season_id:
        raise ValueError("ladder_capture.season_id 不能为空")

    from replay import ladder as ladder_data

    try:
        season = ladder_data.get_season_config(_ladder_config_dir(), season_id)
    except ladder_data.SeasonNotFoundError as exc:
        raise ValueError(f"赛季不存在: {season_id}") from exc
    if str(season.get("status") or "") != "running":
        raise ValueError(f"赛季 {season_id} 不是 running 状态，不能正式计分")
    scoring = season.get("scoring") if isinstance(season.get("scoring"), dict) else {}
    if scoring.get("system") != "tenhou_rank_progression":
        raise ValueError(f"赛季 {season_id} 未启用 tenhou_rank_progression")
    ingest = season.get("ingest") if isinstance(season.get("ingest"), dict) else {}
    if not ingest.get("sources_root"):
        raise ValueError(f"赛季 {season_id} 缺少 ingest.sources_root")

    registered = {
        str(account.get("account_id"))
        for model in season.get("models", []) or []
        if isinstance(model, dict)
        for account in model.get("accounts", []) or []
        if isinstance(account, dict)
    }
    human_id = (capture.human_account_id or "").strip()
    bot_ids = [str(item).strip() for item in capture.bot_account_ids if str(item).strip()]
    all_ids = [human_id, *bot_ids]
    if not human_id or len(bot_ids) != 3:
        raise ValueError("ladder_capture 需要恰好 1 个 human + 3 个 bot 账号")
    if len(set(all_ids)) != 4:
        raise ValueError("四个正式账号必须互不重复")
    missing = sorted(set(all_ids) - registered)
    if missing:
        raise ValueError(f"正式账号未在赛季 {season_id} 注册: {missing}")

    # bot 账号所属模型与所选 spec 一致（C2：不得选 V3 checkpoint 绑 70k@01）
    model_by_account: Dict[str, str] = {}
    for model in season.get("models", []) or []:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("model_id") or "")
        for account in model.get("accounts", []) or []:
            if isinstance(account, dict):
                model_by_account[str(account.get("account_id") or "")] = model_id
    # human 账号必须属于 model_id=human（C23：Nick 的成绩不得记到 70k@04）
    if model_by_account.get(human_id) != "human":
        raise ValueError(
            f"人类账号 {human_id} 必须属于 model_id=human（当前属于 "
            f"{model_by_account.get(human_id) or '未知'}），拒绝正式捕获"
        )
    for spec, account_id in zip(specs, bot_ids, strict=True):
        model_id = _spec_to_model_id(spec, PROJECT_ROOT)
        if model_id is None:
            # 自定义 checkpoint：resolved path 必须与 registry checkpoint 完全一致
            from inference.bot_registry import resolve_bot_spec

            _kind, resolved_path = resolve_bot_spec(spec, PROJECT_ROOT)
            expected = ""
            for model in season.get("models", []) or []:
                if isinstance(model, dict) and model.get("model_id") == model_by_account.get(account_id):
                    expected = str(model.get("checkpoint") or "")
                    break
            if resolved_path is None or Path(str(resolved_path)).resolve() != Path(expected).resolve():
                raise ValueError(
                    f"custom checkpoint 与 {account_id} 的注册 checkpoint 不一致，拒绝正式捕获"
                )
            continue
        if model_by_account.get(account_id) != model_id:
            raise ValueError(
                f"账号 {account_id} 属于模型 {model_by_account.get(account_id)}，"
                f"不能绑定 spec {spec!r}（{model_id}）"
            )


def _current_session() -> Optional[PWYSession]:
    with _LOCK:
        # Prefer a running session, else the most recently created.
        running = [s for s in SESSIONS.values() if s.running]
        if running:
            return max(running, key=lambda s: s.started_at)
        if _HISTORY:
            last_id = _HISTORY[-1]
            return SESSIONS.get(last_id)
    return None


def _binding_view(session: Optional[PWYSession]) -> Optional[LadderCaptureView]:
    if session is None or session.binding is None:
        return None
    return LadderCaptureView(
        enabled=True,
        season_id=str(session.binding.get("season_id") or ""),
        human_account_id=str(session.binding.get("human_account_id") or ""),
        bot_account_ids=[str(item) for item in session.binding.get("bot_account_ids") or []],
        mode=str(session.binding.get("mode") or "confirm"),
    )


def _kill_tree(pid: int) -> None:
    """Best-effort kill of a process and its descendants."""
    if os.name == "nt":
        subprocess.call(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _find_owned_pids() -> List[int]:
    """Return only processes owned by this GUI's Play-with-you launcher.

    The command line contains one of our dedicated ownership markers. This is
    intentionally narrower than matching every ``gateway/main.py`` process.
    """
    pids: List[int] = []
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                [
                    "wmic",
                    "process",
                    "where",
                    "commandline like '%playwithyou%'",
                    "get",
                    "CommandLine,ProcessId",
                    "/format:list",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return pids
        command_line = ""
        for line in out.splitlines():
            if line.startswith("CommandLine="):
                command_line = line.removeprefix("CommandLine=")
            elif line.startswith("ProcessId="):
                pid_s = line.removeprefix("ProcessId=")
                if pid_s.isdigit() and _is_owned_command(command_line):
                    pids.append(int(pid_s))
                command_line = ""
        return [pid for pid in pids if _pid_is_alive(pid)]
    # POSIX best-effort: match our marker, which is passed to both launcher and
    # its gateway child.
    for marker in (
        f"--session-token {SESSION_TOKEN}",
        f"--owner-token {GATEWAY_OWNER_TOKEN}",
    ):
        try:
            out = subprocess.check_output(["pgrep", "-f", marker], text=True)
            pids.extend(int(pid_s) for pid_s in out.split())
        except Exception:
            pass
    return sorted(pid for pid in set(pids) if _pid_is_alive(pid))


def _is_owned_command(command_line: str) -> bool:
    """Recognise our exact command-line arguments, not a log/query string."""
    return bool(
        re.search(rf"(?:^|\s)--session-token\s+{re.escape(SESSION_TOKEN)}(?:\s|$)", command_line)
        or re.search(rf"(?:^|\s)--owner-token\s+{re.escape(GATEWAY_OWNER_TOKEN)}(?:\s|$)", command_line)
    )


def _pid_is_alive(pid: int) -> bool:
    """Exclude the short-lived wmic/pgrep query process from its own result."""
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Existing process owned by another Windows user/session.
        return True
    except OSError:
        return False
    return True


def _kill_owned_artifacts() -> None:
    """Stop only launcher/gateway artifacts marked as owned by this GUI."""
    for pid in _find_owned_pids():
        _kill_tree(pid)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StartPlayWithYouRequest(BaseModel):
    lobby_id: str = "2147"
    speed: str = "normal"
    quantity: int = 1
    # Per-slot network id, length 4. Slots beyond `quantity` are ignored; "none" = no bot.
    networks: List[str] = ["mortal", "none", "none", "none"]
    # Absolute custom checkpoint paths keyed by slot index (0-based) for "custom".
    custom_paths: Dict[int, str] = {}
    device: str = "cuda"
    name_prefix: str = "NoName"
    tenhou_cookie: Optional[str] = None
    ladder_capture: Optional["LadderCaptureRequest"] = None
    # R10-E：预期四人阵容（与 launcher 数量分离）。提供时采用通用捕获流程，
    # 不要求恰好 1 人类 + 3 bot。
    roster: List["ParticipantBindingRequest"] = []
    # R11-B：Play-with-you 只呼出模型（1-4 个）。model_id 来自运行时模型目录
    # （GET /models），不预绑账号/坐席；后台按 model_id → resolve_bot_spec 冻结
    # 绝对 checkpoint 路径。提供时优先于 roster/quantity。
    launchers: List["PlayWithYouLauncherRequest"] = []


class PlayWithYouLauncherRequest(BaseModel):
    # R11-B：运行时模型目录中的 model_id（如 "70k" / "ext_mortal"）。
    # 独立于 ParticipantBindingRequest——R10 legacy 的 identity/artifact 语义
    # 不混入 model-only 呼出路径。
    model_id: str


class ParticipantBindingRequest(BaseModel):
    # R10 legacy roster/account-bound：参与者的账号/身份/产物预绑定。
    # R11-B 的 model-only 呼出请用 PlayWithYouLauncherRequest，不要在此混入 model_id。
    account_id: Optional[str] = None
    controller_type: Optional[str] = None
    model_identity_id: Optional[str] = None
    model_artifact_id: Optional[str] = None
    launcher_slot: Optional[int] = None  # 本系统实际呼出的 slot；None = 不启动
    expected_raw_name: Optional[str] = None  # NoName-1 等
    resolution_required: bool = False


class LadderCaptureRequest(BaseModel):
    enabled: bool = False
    season_id: str = ""
    human_account_id: str = ""
    bot_account_ids: List[str] = []
    mode: str = "confirm"


class LadderCaptureView(BaseModel):
    enabled: bool = False
    season_id: str = ""
    human_account_id: str = ""
    bot_account_ids: List[str] = []
    mode: str = "confirm"


class PlayWithYouStatus(BaseModel):
    session_id: Optional[str] = None
    running: bool = False
    lobby_id: Optional[str] = None
    speed: Optional[str] = None
    device: Optional[str] = None
    bots: List[Dict[str, str]] = []  # [{name, spec}]
    log_tail: List[str] = []
    started_at: Optional[float] = None
    ladder_capture: Optional[LadderCaptureView] = None
    # P2（UX Repair 2）：roster 模式冻结阵容（account/controller/model/expected_name）
    frozen_roster: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models", response_model=dict)
def list_playwithyou_models() -> dict:
    """R11-B：Play-with-you 运行时模型目录。

    只暴露具名 Mortal checkpoints（70k / ext_mortal），不读 Participants
    ModelIdentity/Artifact。前端据此渲染"模型下拉"。
    """
    return {
        "schema": "keqing.playwithyou.models.v1",
        "models": PLAYWITHYOU_MODEL_CATALOG,
    }


@router.post("/start", response_model=PlayWithYouStatus)
def start_playwithyou(req: StartPlayWithYouRequest) -> PlayWithYouStatus:
    if not LAUNCHER.exists():
        raise HTTPException(status_code=500, detail=f"launcher missing: {LAUNCHER}")

    # Single active session: refuse to start a second one (gateway port clash).
    with _LOCK:
        for existing in SESSIONS.values():
            if existing.running:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"已有运行中的会话 ({existing.session_id})，请先停止再呼出新的账号。"
                    ),
                )

    # Do not silently kill a process left by a previous GUI/backend instance.
    # Surface it and let the user choose Stop; this avoids unexpectedly
    # terminating an unrelated manual gateway.
    if _find_owned_pids():
        raise HTTPException(
            status_code=409,
            detail="检测到上一轮天凤呼出的遗留进程，请先点击「停止呼出」再重新启动。",
        )

    lobby_id = (req.lobby_id or "2147").strip().lstrip("Ll").strip() or "2147"
    if not (lobby_id.isdigit() and 1 <= int(lobby_id) <= 9999):
        raise HTTPException(
            status_code=400, detail="Lobby ID 必须是 1-9999 之间的数字（例如 2147）。"
        )

    quantity = max(1, min(4, int(req.quantity)))
    speed = req.speed if req.speed in SPEED_PRESETS else "normal"
    think_delay = SPEED_PRESETS[speed]

    networks = list(req.networks)
    while len(networks) < 4:
        networks.append("none")

    # Resolve launcher specs. R10-E roster 模式：从预期四人阵容的 launcher_slot 推导，
    # 与 quantity 解耦（Nick human_ui + 2 本地 bot + 外部 Mortal → quantity=2 合法）。
    roster = list(req.roster)
    launchers_mode = bool(req.launchers)
    roster_bindings: List[dict] = []
    launcher_specs: List[str] = []
    try:
        if req.launchers:
            # R11-B：Play-with-you 只呼出模型（1-4 个）。model_id → resolve_bot_spec
            # → 绝对 checkpoint 路径，直接作为 launcher spec（冻结 A、运行 B 不可能发生）。
            if not (1 <= len(req.launchers) <= 4):
                raise ValueError("一次呼出 1-4 个模型")
            from inference.bot_registry import resolve_bot_spec

            for index, ln in enumerate(req.launchers):
                model_id = str(ln.model_id or "").strip()
                if model_id not in _PLAYWITHYOU_MODEL_IDS:
                    raise ValueError(
                        f"未知模型: {model_id!r}（可选: {sorted(_PLAYWITHYOU_MODEL_IDS)}）"
                    )
                _kind, resolved = resolve_bot_spec(model_id, PROJECT_ROOT)
                if resolved is None:
                    raise ValueError(f"模型 {model_id} 无法解析 checkpoint")
                roster_bindings.append(
                    {
                        "model_id": model_id,
                        "launcher_slot": index,
                    }
                )
                launcher_specs.append(str(resolved))
        elif roster:
            if len(roster) != 4:
                raise ValueError("roster 必须恰好 4 位预期参与者")
            slot_specs: List[tuple[int, str]] = []
            for entry in roster:
                slot = entry.launcher_slot
                roster_bindings.append(entry.model_dump())
                if slot is None:
                    continue
                if not (0 <= slot <= 3):
                    raise ValueError(f"launcher_slot 必须在 [0,3]: {slot}")
                if entry.model_identity_id and entry.model_artifact_id:
                    if str(entry.account_id or "").strip():
                        # account-backed：checkpoint 来自所选 artifact（带账号归属校验）
                        spec = _artifact_spec_for_seat(
                            str(entry.account_id or ""),
                            entry.model_identity_id,
                            entry.model_artifact_id,
                        )
                    else:
                        # account-less R10：只呼出模型，不预绑账号
                        spec = _artifact_spec_for_launcher(
                            entry.model_identity_id, entry.model_artifact_id
                        )
                else:
                    # backend compatibility：旧 network spec 推导
                    spec = _resolve_spec(networks[slot], req.custom_paths, slot)
                if spec is None:
                    raise ValueError(f"roster slot {slot} 未配置有效模型")
                slot_specs.append((slot, spec))
            slot_specs.sort(key=lambda item: item[0])
            launcher_specs = [spec for _, spec in slot_specs]
            if not launcher_specs:
                raise ValueError("roster 至少需要一个由本系统呼出的 launcher slot")
        else:
            for slot in range(quantity):
                spec = _resolve_spec(networks[slot], req.custom_paths, slot)
                if spec is not None:
                    launcher_specs.append(spec)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    specs = launcher_specs
    # 传给 launcher 的 --bots：默认原始 spec；roster 模式在冻结后替换为绝对 checkpoint 路径。
    launcher_command_specs = specs
    if not specs:
        raise HTTPException(
            status_code=400,
            detail="没有选择任何模型（所有 AI 槽位都是 none）。请至少选择一个 Mortal 网络。",
        )
    if len(specs) > 4:
        raise HTTPException(status_code=400, detail="最多呼出 4 个账号。")

    count = len(specs)
    name_prefix = (req.name_prefix or "").strip() or "NoName"
    names = [name_prefix] if count == 1 else [f"{name_prefix}-{i + 1}" for i in range(count)]
    device = req.device if req.device in ("cuda", "cpu") else "cuda"

    # session_id 提前生成（R9-3）：binding 冻结必须在启动 launcher 之前。
    session_id = uuid.uuid4().hex[:12]

    # R9-3 正式天梯捕获 / R10-E 通用 roster 捕获：启动前完成校验并冻结 binding。
    capture_dir: Optional[Path] = None
    if roster or launchers_mode:
        # P2：roster 与旧正式天梯绑定互斥，不能静默忽略 season。
        if req.ladder_capture is not None and req.ladder_capture.enabled:
            raise HTTPException(
                status_code=400,
                detail="通用四人阵容（roster）与「计入正式天梯」不能同时开启；正式计分将由 ledger projection 决定（R10-F）。",
            )
        from gateway.playwithyou_capture import capture_dir_for_session
        from participants import aliases as participant_aliases
        from participants.paths import data_lock as participants_data_lock
        from participants import ledger as participants_ledger
        from participants.schemas import ExternalAliasCreate as ParticipantAliasCreate

        capture_dir = None
        registered_alias_ids: List[str] = []
        try:
            # P1-3：账号/模型校验 + 模型冻结 + 会话别名注册在同一个 participants
            # data_lock 临界区内完成（防并发删除产生 stale alias）。
            with participants_data_lock():
                participants_ledger.recover_pending_transaction_locked()
                _validate_roster_bindings(roster_bindings, specs)
                # P1-1：返回与原 roster 同序，仅回填模型字段
                roster_bindings, frozen_launcher_specs = _freeze_launcher_models(roster_bindings, specs)
                # P1-1（UX Repair 2）：launcher 名字真相源 = 按 launcher_slot 排序
                # 生成的 names[index]（1 个 bot → NoName；多个 → NoName-1/2/...）。
                # UI 提供的 expected_raw_name 不得覆盖真实 launcher 名称。
                launched_sorted = sorted(
                    (entry for entry in roster_bindings if entry.get("launcher_slot") is not None),
                    key=lambda entry: int(entry["launcher_slot"]),
                )
                for index, entry in enumerate(launched_sorted):
                    entry["expected_raw_name"] = (
                        names[index] if index < len(names) else f"NoName-{index + 1}"
                    )
                # P1：child/runtime 必须加载与 artifact 匹配的同一绝对 checkpoint 路径，
                # 不再让子进程按动态名字（mortal/70k）重新解析。
                launcher_command_specs = frozen_launcher_specs

                capture_dir = capture_dir_for_session(_ladder_data_root(), session_id)
                (capture_dir / "pending").mkdir(parents=True, exist_ok=True)
                (capture_dir / "ignored").mkdir(parents=True, exist_ok=True)
                (capture_dir / "errors").mkdir(parents=True, exist_ok=True)
                binding = {
                    "session_id": session_id,
                    "season_id": "",
                    "human_account_id": "",
                    "bot_account_ids": [],
                    "mode": "roster",
                    "roster": roster_bindings,
                    "frozen_at": time.time(),
                }
                tmp_binding = capture_dir / "binding.tmp"
                tmp_binding.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(tmp_binding, capture_dir / "binding.json")

                # R10-E：session-scoped 别名——NoName-{n} → 具体账号 + 模型版本。
                # 按 launcher_slot 顺序（与真实 bot 顺序一致）；注册失败不 fail-open。
                # P1-1：external_id 用已回填的真实 launcher 名称（names[index]）。
                launched = sorted(
                    (entry for entry in roster_bindings if entry.get("launcher_slot") is not None),
                    key=lambda entry: int(entry["launcher_slot"]),
                )
                for index, entry in enumerate(launched):
                    # Play-with-you simplification：无账号 launcher → account_id=None
                    account_id = (str(entry.get("account_id") or "").strip() or None)
                    expected_name = str(entry.get("expected_raw_name") or names[index] or f"NoName-{index + 1}")
                    alias = participant_aliases.register_alias_locked(
                        ParticipantAliasCreate(
                            provider="tenhou",
                            external_id=expected_name,
                            account_id=account_id,
                            model_identity_id=entry.get("model_identity_id"),
                            model_artifact_id=entry.get("model_artifact_id"),
                            scope="session",
                            session_id=session_id,
                        )
                    )
                    registered_alias_ids.append(alias.alias_id)
        except (ValueError, FileNotFoundError) as exc:
            _rollback_roster_start(capture_dir, registered_alias_ids)
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            _rollback_roster_start(capture_dir, registered_alias_ids)
            raise HTTPException(status_code=400, detail="roster 会话别名注册失败，已回滚本次启动")
    elif req.ladder_capture is not None and req.ladder_capture.enabled:
        try:
            _validate_ladder_capture(req.ladder_capture, specs)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        from gateway.playwithyou_capture import capture_dir_for_session

        capture_dir = capture_dir_for_session(_ladder_data_root(), session_id)
        (capture_dir / "pending").mkdir(parents=True, exist_ok=True)
        (capture_dir / "ignored").mkdir(parents=True, exist_ok=True)
        (capture_dir / "errors").mkdir(parents=True, exist_ok=True)
        binding = {
            "session_id": session_id,
            "season_id": req.ladder_capture.season_id,
            "human_account_id": req.ladder_capture.human_account_id,
            "bot_account_ids": [str(item) for item in req.ladder_capture.bot_account_ids],
            "mode": req.ladder_capture.mode,
            "frozen_at": time.time(),
        }
        tmp_binding = capture_dir / "binding.tmp"
        tmp_binding.write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_binding, capture_dir / "binding.json")

    command = [
        sys.executable,
        "-u",
        str(LAUNCHER),
        "--room",
        f"L{lobby_id}",
        "--bots",
        *launcher_command_specs,
        "--device",
        device,
        "--start-gateway",
        "--stagger-seconds",
        "1.5",
        "--think-delay",
        str(think_delay),
        "--name-prefix",
        name_prefix,
        "--session-token",
        SESSION_TOKEN,
        "--gateway-owner-token",
        GATEWAY_OWNER_TOKEN,
    ]
    if capture_dir is not None:
        command += ["--ladder-capture-dir", str(capture_dir)]
    if req.tenhou_cookie:
        command += ["--tenhou-cookie", req.tenhou_cookie]

    logger.info("spawning playwithyou: %s", " ".join(command))

    try:
        popen_kwargs = {
            "cwd": str(PROJECT_ROOT),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            command,
            **popen_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        # P2-1：Popen 失败也要回滚 roster 启动（binding + session aliases）
        if roster:
            _rollback_roster_start(capture_dir, registered_alias_ids)
        raise HTTPException(status_code=500, detail=f"启动失败: {exc}")

    session = PWYSession(
        session_id=session_id,
        proc=proc,
        command=command,
        lobby_id=lobby_id,
        specs=specs,
        names=names,
        speed=speed,
        device=device,
        started_at=time.time(),
    )
    if req.ladder_capture is not None and req.ladder_capture.enabled:
        session.capture_dir = capture_dir
        session.binding = binding
    if roster or launchers_mode:
        # P2：roster/launchers 模式把冻结阵容放进 session，status 可回传（UI 不再猜本地草稿）
        session.frozen_roster = roster_bindings
    with _LOCK:
        SESSIONS[session_id] = session
        _HISTORY.append(session_id)
        if len(_HISTORY) > MAX_HISTORY:
            _HISTORY.pop(0)

    return PlayWithYouStatus(
        session_id=session_id,
        running=True,
        lobby_id=lobby_id,
        speed=speed,
        device=device,
        bots=[{"name": n, "spec": s} for n, s in zip(names, specs)],
        log_tail=session.tail(50),
        started_at=session.started_at,
        ladder_capture=_binding_view(session),
        frozen_roster=session.frozen_roster,
    )


@router.get("/status", response_model=PlayWithYouStatus)
def playwithyou_status() -> PlayWithYouStatus:
    session = _current_session()
    if session is not None and session.running:
        return PlayWithYouStatus(
            session_id=session.session_id,
            running=True,
            lobby_id=session.lobby_id,
            speed=session.speed,
            device=session.device,
            bots=[{"name": n, "spec": s} for n, s in zip(session.names, session.specs)],
            log_tail=session.tail(200),
            started_at=session.started_at,
            ladder_capture=_binding_view(session),
            frozen_roster=session.frozen_roster,
        )
    # No in-memory session, but a launcher may still be alive as an orphan
    # (e.g. the backend process was restarted and lost its session record).
    # Surface it so the GUI keeps offering a Stop button that will clean it up.
    if _find_owned_pids():
        return PlayWithYouStatus(
            running=True,
            lobby_id=None,
            speed=None,
            device=None,
            bots=[],
            log_tail=[
                "（检测到遗留的 bot 进程，可能是上次后端重启后残留；"
                "点击「停止呼出」即可清理）"
            ],
            started_at=None,
        )
    return PlayWithYouStatus(running=False)


@router.post("/stop", response_model=PlayWithYouStatus)
def stop_playwithyou() -> PlayWithYouStatus:
    session = _current_session()
    if session is not None and session.running:
        pid = session.proc.pid
        try:
            # Kill the process tree while the launcher is still alive, so its
            # local gateway child cannot escape as an orphan.
            _kill_tree(pid)
            try:
                session.proc.wait(timeout=5)
            except Exception:
                pass
            if session.proc.poll() is None:
                session.proc.kill()
                try:
                    session.proc.wait(timeout=5)
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"停止失败: {exc}")
    # Also clean a marked launcher/gateway left after a backend restart. This
    # deliberately excludes any manually launched gateway.
    _kill_owned_artifacts()
    return PlayWithYouStatus(
        session_id=session.session_id if session else None,
        running=False,
        lobby_id=session.lobby_id if session else None,
        speed=session.speed if session else None,
        device=session.device if session else None,
        bots=[{"name": n, "spec": s} for n, s in zip(session.names, session.specs)]
        if session
        else [],
        started_at=session.started_at if session else None,
    )


# ---------------------------------------------------------------------------
# R9-3: ladder capture review & confirm
# ---------------------------------------------------------------------------

def _capture_root() -> Path:
    return _ladder_data_root() / "captures" / "playwithyou"


def _discover_captures() -> List[dict]:
    """扫描持久化捕获目录，重发现 pending/ignored/errors（不依赖 SESSIONS 内存）。

    每个子目录只接受对应状态集合：
    - pending/  只允许 pending_confirmation / published / accepted_publish_failed
    - ignored/  只返回 ignored
    - errors/   只允许 incomplete / conflict
    状态与所在子目录不一致的条目视为不一致状态，不对外暴露。
    """
    from gateway.playwithyou_capture import STATE_BY_SUBDIR

    captures: List[dict] = []
    root = _capture_root()
    if not root.is_dir():
        return captures
    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue
        for subdir, allowed in STATE_BY_SUBDIR.items():
            folder = session_dir / subdir
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                state = payload.get("state")
                if state not in allowed:
                    continue  # 状态与目录不一致，不暴露
                captures.append(
                    {
                        "capture_id": payload.get("capture_id") or path.stem,
                        "session_id": payload.get("session_id") or session_dir.name,
                        "state": state,
                        "season_id": payload.get("season_id"),
                        "match": payload.get("match"),
                        "tenhou_log_url": payload.get("tenhou_log_url"),
                        "observer_accounts": payload.get("observer_accounts") or [],
                        "score_observers": payload.get("score_observers") or [],
                        "roster": payload.get("roster") or [],
                        "evidence_warning": payload.get("evidence_warning"),
                        "conflict_reason": payload.get("conflict_reason"),
                        "publish_error": payload.get("publish_error"),
                        "_path": str(path),
                    }
                )
    return captures


def _capture_file(capture_id: str) -> Path:
    for entry in _discover_captures():
        if entry["capture_id"] == capture_id:
            return Path(entry["_path"])
    raise HTTPException(status_code=404, detail=f"capture 不存在: {capture_id}")


class SourceConflictError(Exception):
    """同 match_id 已存在不同内容的 source 文件，拒绝覆盖。"""


def _safe_source_filename(match_id: str) -> str:
    import hashlib

    digest = hashlib.sha256(match_id.encode("utf-8")).hexdigest()[:16]
    return f"tenhou-{digest}.jsonl"


def write_source_idempotent(path: Path, line: str) -> str:
    """一局一个 JSONL 文件的幂等写入（进程内并发安全）。

    - 文件不存在：原子创建，返回 ``"created"``；
    - 已存在且与 merge 内容键一致（同 match_id + 同 game_length + 同 canonical
      players，忽略 occurred_at）：no-op，保留现有 occurred_at，返回 ``"existing"``；
    - 已存在但玩家/分数不同：抛 SourceConflictError，绝不改写旧 source（C17/C31）。

    内容键与 ``ladder_ingest.merge_ladder_matches`` 的去重语义完全一致（P2-2）。
    """
    import json as _json
    import os as _os

    from replay import ladder_ingest

    def _content_key_of(line_text: str) -> tuple:
        match = ladder_ingest.parse_match_record(
            _json.loads(line_text),
            source_type="playwithyou",
            source_ref=path.name,
        )
        return (match.match_id, match.game_length, ladder_ingest.canonical_players(match))

    with _SOURCE_WRITE_LOCK:
        if path.is_file():
            existing = path.read_text(encoding="utf-8")
            if _content_key_of(existing) == _content_key_of(line):
                return "existing"
            raise SourceConflictError(f"同 match_id 已存在不同内容: {path.name}")
        tmp = path.with_name(f".{path.name}.{_os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(line, encoding="utf-8")
        _os.replace(tmp, path)
        return "created"


def _confirm_capture(capture_id: str) -> dict:
    from gateway.playwithyou_capture import CAPTURE_SCHEMA

    path = _capture_file(capture_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"capture 无法解析: {exc}") from exc
    if payload.get("schema") != CAPTURE_SCHEMA:
        raise HTTPException(status_code=400, detail="capture schema 无效")
    state = payload.get("state")
    if state == "published":
        # 幂等：已确认的对局重复 confirm 为 no-op（source 文件已存在，publisher 可 skip）。
        return {"capture_id": capture_id, "state": "published"}
    if state not in ("pending_confirmation", "accepted_publish_failed"):
        raise HTTPException(status_code=409, detail=f"capture 状态不允许确认: {state}")

    season_id = str(payload.get("season_id") or "")
    match = payload.get("match") or {}
    players = match.get("players") or []
    if len(players) != 4:
        raise HTTPException(status_code=400, detail="capture match 需要恰好四名玩家")

    from replay import ladder as ladder_data

    try:
        season = ladder_data.get_season_config(_ladder_config_dir(), season_id)
    except ladder_data.SeasonNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if str(season.get("status") or "") != "running":
        raise HTTPException(status_code=409, detail=f"赛季 {season_id} 不是 running")
    from replay import ladder_ingest

    try:
        sources_root = ladder_ingest.resolve_ingest_sources_root(PROJECT_ROOT, season)
    except ladder_ingest.LadderIngestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 写 source 前先按 ingest 契约校验（P1-2/C39-C42）：非法记录绝不进入正式 source。
    registered_accounts = {
        str(account.get("account_id"))
        for model in season.get("models", []) or []
        if isinstance(model, dict)
        for account in model.get("accounts", []) or []
        if isinstance(account, dict)
    }
    try:
        candidate = ladder_ingest.parse_match_record(
            match,
            source_type="playwithyou",
            source_ref=f"capture:{capture_id}",
        )
        ladder_ingest.validate_match(candidate, registered_accounts)
    except ladder_ingest.LadderIngestError as exc:
        raise HTTPException(status_code=409, detail=f"capture 校验失败，未写入 source: {exc}") from exc

    pwy_source = sources_root / "playwithyou"
    pwy_source.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "match_id": match.get("match_id"),
            "occurred_at": match.get("occurred_at"),
            "game_length": match.get("game_length") or "hanchan",
            "players": players,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    target = pwy_source / _safe_source_filename(str(match.get("match_id") or capture_id))
    try:
        write_source_idempotent(target, line)
    except SourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # 同步发布（本轮不引入后台队列）。
    registry_path = _ladder_config_dir() / f"{season_id}.json"
    if not registry_path.is_file():
        raise HTTPException(status_code=500, detail=f"赛季注册表不存在: {registry_path}")
    try:
        from replay.publish_ladder_snapshot import publish_snapshot

        result = publish_snapshot(
            registry_path=registry_path,
            log_dirs=[],
        )
    except Exception as exc:
        # source 已写入、publish 失败：保留 accepted，标记 accepted_publish_failed。
        _set_capture_state(path, "accepted_publish_failed", reason=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"已确认但发布失败（可 retry-publish）: {exc}",
        ) from exc

    _set_capture_state(path, "published")
    return {
        "capture_id": capture_id,
        "state": "published",
        "snapshot_dir": result.get("snapshot_dir"),
        "games": result.get("games"),
    }


def _set_capture_state(path: Path, state: str, *, reason: str = "") -> None:
    import os as _os

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload["state"] = state
    if reason:
        payload["publish_error"] = reason
    elif state == "published":
        # retry 成功后清除旧的 publish_error（P3/C32）。
        payload.pop("publish_error", None)
    tmp = path.with_name(f".{path.name}.{_os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _os.replace(tmp, path)


@router.get("/captures")
def list_ladder_captures() -> dict:
    captures = [
        {key: value for key, value in entry.items() if not key.startswith("_")}
        for entry in _discover_captures()
    ]
    return {"captures": captures}


@router.post("/captures/{capture_id}/confirm")
def confirm_ladder_capture(capture_id: str) -> dict:
    return _confirm_capture(capture_id)


@router.post("/captures/{capture_id}/ignore")
def ignore_ladder_capture(capture_id: str) -> dict:
    """把捕获标记为 ignored（先原子改写 payload.state，再移动到 ignored/）。

    之后不可再 confirm（state 校验拒绝），也不会进入 sources（C18）。
    """
    path = _capture_file(capture_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"capture 无法解析: {exc}") from exc
    if payload.get("state") in {"published", "accepted_publish_failed"}:
        # 一旦正式 source 已 accepted，只能 retry-publish，不能通过 ignore 撤销（P2-1/C30）。
        raise HTTPException(status_code=409, detail="已确认（含发布失败待重试）的捕获不能忽略")
    payload["state"] = "ignored"
    _set_capture_state(path, "ignored")
    ignored_dir = path.parent.parent / "ignored"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    import os as _os

    _os.replace(path, ignored_dir / path.name)
    return {"capture_id": capture_id, "state": "ignored"}


@router.post("/captures/{capture_id}/retry-publish")
def retry_publish_ladder_capture(capture_id: str) -> dict:
    path = _capture_file(capture_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"capture 无法解析: {exc}") from exc
    if payload.get("state") != "accepted_publish_failed":
        raise HTTPException(status_code=409, detail=f"只有 accepted_publish_failed 可 retry: {payload.get('state')}")
    return _confirm_capture(capture_id)
