# -*- coding: utf-8 -*-
"""FastAPI Web 服务入口 — Replay Review 系统（支持持久化存储）。"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import numpy as np
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from replay.normalize import normalize_replay_decisions
from replay import ladder as ladder_data
from replay.ladder import SeasonNotFoundError, SeasonRegistryError
from replay.external_reports import write_external_teacher_reports, _action_actor, _decision_kind


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

BASE_DIR = Path(__file__).parent


def _recover_participants_pending_transaction() -> None:
    """启动时恢复未完成的 participants 账本事务（P2-1）。"""
    try:
        from participants import ledger as participants_ledger

        participants_ledger.recover_pending_transaction()
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "participants pending transaction recovery failed", exc_info=True
        )


@asynccontextmanager
async def _app_lifespan(_app):
    _recover_participants_pending_transaction()
    from participants import projection as participants_projection

    participants_projection.start_worker()
    try:
        yield
    finally:
        participants_projection.stop_worker()


app = FastAPI(title="Keqing Unified Server", description="立直麻将 Review + 对战服务", lifespan=_app_lifespan)

# ========== 合并 Battle Router ==========
from gateway.api.battle import router as battle_router
app.include_router(battle_router)

# ========== Play with you (Tenhou 在线呼出 Mortal 账号) ==========
from gateway.api.playwithyou import router as playwithyou_router
app.include_router(playwithyou_router)

# ========== Participants（账号 / 模型 / 统一对局账本）==========
from participants.api import router as participants_router

app.include_router(participants_router)

# ========== 静态资源 ==========

_REPLAY_UI_DIR = BASE_DIR.parent / "replay_ui"
_REACT_DIST = _REPLAY_UI_DIR / "dist"

_TILE_DIR_CANDIDATES = (
    _REACT_DIST / "tiles",
    _REPLAY_UI_DIR / "public" / "tiles",
)
_TILE_DIR = next((path for path in _TILE_DIR_CANDIDATES if path.is_dir()), None)

if _TILE_DIR is not None:
    app.mount("/tiles", StaticFiles(directory=str(_TILE_DIR)), name="tiles")

# 挂载 React 构建产物（生产环境）
if _REACT_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_REACT_DIST / "assets")), name="assets")

# ========== 存储相关 ==========

from replay.storage import get_storage


def _normalize_replay_events(events: list[dict] | None) -> list[dict]:
    if not events:
        return events or []

    from mahjong_env.replay_normalizer import normalize_replay_events
    from mahjong_env.legal_actions import enumerate_legal_action_specs
    from mahjong_env.scoring import score_hora
    from mahjong_env.state import GameState, apply_event

    normalized = normalize_replay_events(events)
    state = GameState()

    for idx, event in enumerate(normalized):
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "dahai":
            try:
                actor = int(event.get("actor", -1))
                snapshot = state.snapshot(actor) if 0 <= actor < 4 else None
                pending_reach = bool(snapshot and snapshot.get("pending_reach", [False] * 4)[actor])
                if pending_reach:
                    pending_dahai = [
                        spec for spec in enumerate_legal_action_specs(snapshot, actor)
                        if spec.type == "dahai"
                    ]
                    current_pai = str(event.get("pai", ""))
                    current_tsumogiri = bool(event.get("tsumogiri", False))
                    matches = any(
                        spec.pai == current_pai and bool(spec.tsumogiri) == current_tsumogiri
                        for spec in pending_dahai
                    )
                    if not matches and len(pending_dahai) == 1:
                        fixed = pending_dahai[0]
                        normalized[idx] = {
                            **event,
                            "pai": fixed.pai,
                            "tsumogiri": bool(fixed.tsumogiri),
                            "legacy_reach_discard_repaired": True,
                            "legacy_reach_discard_original": {
                                "pai": event.get("pai"),
                                "tsumogiri": event.get("tsumogiri"),
                            },
                        }
                        event = normalized[idx]
            except Exception:
                pass

        if event_type == "hora":
            actor = int(event.get("actor", 0))
            target = int(event.get("target", actor))
            is_tsumo = actor == target
            pai = event.get("pai")
            if not pai:
                if is_tsumo:
                    pai = state.last_tsumo_raw[actor] or state.last_tsumo[actor]
                elif state.last_discard:
                    pai = state.last_discard.get("pai_raw") or state.last_discard.get("pai")
            ura_markers = [str(m) for m in (event.get("ura_dora_markers") or event.get("ura_markers") or [])]
            if pai:
                try:
                    result = score_hora(
                        state,
                        actor=actor,
                        target=target,
                        pai=str(pai),
                        is_tsumo=is_tsumo,
                        ura_dora_markers=ura_markers,
                    )
                    deltas = list(result.deltas)
                    scores = [int(state.scores[i] + deltas[i]) for i in range(4)]
                    normalized[idx] = {
                        **event,
                        "pai": str(pai),
                        "is_tsumo": is_tsumo,
                        "han": result.han,
                        "fu": result.fu,
                        "yaku": result.yaku,
                        "yaku_details": result.yaku_details,
                        "cost": result.cost,
                        "deltas": deltas,
                        "scores": scores,
                        "honba": int(event.get("honba", state.honba)),
                        "kyotaku": int(event.get("kyotaku", state.kyotaku)),
                        "ura_dora_markers": ura_markers,
                    }
                    event = normalized[idx]
                except Exception:
                    pass

        try:
            apply_event(state, event)
        except Exception:
            continue

    return normalized


def _merge_terminal_event_details(decisions: dict, events: list[dict] | None) -> dict:
    if not isinstance(decisions, dict) or not events:
        return decisions

    event_lookup = {
        idx: event
        for idx, event in enumerate(events)
        if isinstance(event, dict)
    }

    for entry in decisions.get("log", []):
        source_event_index = entry.get("source_event_index")
        if source_event_index is None:
            continue
        event = event_lookup.get(int(source_event_index))
        if not event:
            continue
        event_type = event.get("type")
        for action_key in ("chosen", "gt_action"):
            action = entry.get(action_key)
            if isinstance(action, dict) and action.get("type") == event_type:
                entry[action_key] = {**action, **event}
    return decisions


def _resolve_optional_project_path(raw_path: str | None) -> Path | None:
    if not raw_path or not raw_path.strip():
        return None
    project_root = BASE_DIR.parent.parent.resolve()
    attachment_root = (Path.home() / ".codex" / "attachments").resolve()
    path = Path(raw_path.strip())
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    is_project_path = path == project_root or project_root in path.parents
    is_attachment_path = path == attachment_root or attachment_root in path.parents
    if not is_project_path and not is_attachment_path:
        raise ValueError(f"path is outside project root: {raw_path}")
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _float_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _ranked_teacher_candidates(details: list[dict]) -> list[dict]:
    ranked = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        action = detail.get("action")
        if not isinstance(action, dict):
            continue
        ranked.append(
            {
                "action": action,
                "q_value": _float_or_none(detail.get("q_value")),
                "prob": _float_or_none(detail.get("prob")),
            }
        )
    ranked.sort(
        key=lambda item: (
            item["prob"] if item["prob"] is not None else float("-inf"),
            item["q_value"] if item["q_value"] is not None else float("-inf"),
        ),
        reverse=True,
    )
    for idx, item in enumerate(ranked, start=1):
        item["rank"] = idx
    return ranked


def _infer_teacher_model_tag(report: dict, report_path: Path) -> str:
    review = report.get("review", {}) if isinstance(report, dict) else {}
    model_tag = review.get("model_tag") if isinstance(review, dict) else None
    if model_tag:
        return str(model_tag)
    for key in ("network", "model_tag", "mortal_model_tag", "engine"):
        value = report.get(key) if isinstance(report, dict) else None
        if value:
            return str(value)
    stem = report_path.stem
    if "__" in stem:
        parts = [part for part in stem.split("__") if part]
        if len(parts) >= 2:
            return parts[-2] if parts[-1].startswith("p") else parts[-1]
    return stem or "teacher"


def _load_teacher_report_entries(report_path: Path, decisions: dict) -> tuple[str, int | None, list[dict]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    review = report.get("review", {}) if isinstance(report, dict) else {}
    kyokus = review.get("kyokus", []) if isinstance(review, dict) else []
    model_tag = _infer_teacher_model_tag(report, report_path)
    report_player_id = report.get("player_id")
    decision_player_id = decisions.get("player_id")
    if (
        report_player_id is not None
        and decision_player_id is not None
        and int(report_player_id) != int(decision_player_id)
    ):
        raise ValueError(
            f"teacher report player_id {report_player_id} does not match replay player_id {decision_player_id}"
        )

    teacher_entries: list[dict] = []
    for kyoku_index, kyoku in enumerate(kyokus):
        for entry_index, entry in enumerate(kyoku.get("entries", [])):
            candidates = _ranked_teacher_candidates(list(entry.get("details", [])))
            actual_action = entry.get("actual")
            expected_action = entry.get("expected")
            actual_candidate = None
            expected_candidate = None
            for candidate in candidates:
                action = candidate.get("action")
                if actual_candidate is None and _teacher_same_action(action, actual_action):
                    actual_candidate = candidate
                if expected_candidate is None and _teacher_same_action(action, expected_action):
                    expected_candidate = candidate
                if actual_candidate is not None and expected_candidate is not None:
                    break
            best_candidate = candidates[0] if candidates else None
            actual_q = actual_candidate.get("q_value") if actual_candidate else None
            best_q = best_candidate.get("q_value") if best_candidate else None
            q_loss = None
            if actual_q is not None and best_q is not None:
                q_loss = max(0.0, float(best_q) - float(actual_q))
            teacher_entries.append(
                {
                    "model": model_tag,
                    "report_path": str(report_path),
                    "report_player_id": report_player_id,
                    "step": entry.get("step"),
                    "source_event_index": entry.get("source_event_index"),
                    "actor": entry.get("actor"),
                    "decision_kind": entry.get("decision_kind"),
                    "kyoku_index": kyoku_index,
                    "entry_index": entry_index,
                    "junme": entry.get("junme"),
                    "tiles_left": entry.get("tiles_left"),
                    "shanten": entry.get("shanten"),
                    "display_mode": entry.get("display_mode"),
                    "actual_action": actual_action,
                    "expected_action": expected_action,
                    "is_equal": entry.get("is_equal"),
                    "actual_q": actual_q,
                    "expected_q": expected_candidate.get("q_value") if expected_candidate else None,
                    "best_q": best_q,
                    "best_prob": best_candidate.get("prob") if best_candidate else None,
                    "q_loss": q_loss,
                    "candidates": candidates,
                    "top1": best_candidate,
                    "top2": candidates[1] if len(candidates) > 1 else None,
                }
            )
    return model_tag, report_player_id, teacher_entries


def _teacher_same_action(local_action, teacher_action) -> bool:
    try:
        from inference.review import same_action
    except Exception:
        same_action = None

    if same_action:
        try:
            return bool(same_action(local_action, teacher_action))
        except Exception:
            pass
    return local_action == teacher_action


def _attach_teacher_report_overlay(decisions: dict, report_path: Path | None) -> dict:
    if report_path is None or not isinstance(decisions, dict):
        return decisions
    return _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=decisions.get("replay_id"))


def _entry_identity(entry: dict) -> tuple[int, int, str] | None:
    """本地决策 entry 的事件身份：(source_event_index, actor, decision_kind)。"""
    sei = entry.get("source_event_index")
    if not isinstance(sei, int):
        return None
    actual = _actual_action_for_review(entry)
    actor = _action_actor(actual)
    kind = _decision_kind(actual)
    if actor is None or kind is None:
        return None
    return (sei, actor, kind)


def _teacher_identity_key(teacher_entry: dict) -> tuple[int, int, str] | None:
    """teacher 报告 entry 的事件身份主键（replay 已由 expected_replay_id 校验）。"""
    sei = teacher_entry.get("source_event_index")
    actor = teacher_entry.get("actor")
    kind = teacher_entry.get("decision_kind")
    if not isinstance(sei, int) or not isinstance(actor, int) or not kind:
        return None
    return (sei, actor, str(kind))


def _alignment_label(
    *,
    exact: int,
    legacy_step: int,
    legacy_action_order: int,
    ambiguous: int,
    unmatched: int,
    duplicate: int,
    replay_mismatch: bool,
) -> str:
    """顶层 alignment 语义：mixed 不会被 exact 掩盖。"""
    if replay_mismatch:
        return "replay_mismatch"
    if exact > 0:
        if (
            legacy_step > 0
            or legacy_action_order > 0
            or ambiguous > 0
            or unmatched > 0
            or duplicate > 0
        ):
            return "mixed"
        return "exact_event_identity"
    if legacy_step > 0:
        return "legacy_step"
    if legacy_action_order > 0:
        return "legacy_action_order"
    return "unmatched"


def _attach_teacher_report_overlays(
    decisions: dict,
    report_paths: list[Path],
    expected_replay_id: str | None = None,
) -> dict:
    if not report_paths or not isinstance(decisions, dict):
        return decisions

    # decisions 若自带 replay_id（submit 路径写入），作为 expected 兜底
    if expected_replay_id is None and decisions.get("replay_id"):
        expected_replay_id = str(decisions["replay_id"])

    own_entries = [
        entry
        for entry in decisions.get("log", [])
        if isinstance(entry, dict) and not entry.get("is_obs")
    ]
    own_entries_by_step = {
        int(entry["step"]): entry
        for entry in own_entries
        if isinstance(entry.get("step"), int)
    }
    own_entries_by_identity: dict[tuple[int, int, str], list[dict]] = {}
    for entry in own_entries:
        identity = _entry_identity(entry)
        if identity is not None:
            own_entries_by_identity.setdefault(identity, []).append(entry)
    overlays: list[dict] = []

    def _attach_to_entry(entry: dict, teacher_entry: dict) -> None:
        review_entry = {
            key: value
            for key, value in teacher_entry.items()
            if key != "candidates"
        }
        review_entry["candidate_count"] = len(teacher_entry["candidates"])
        if teacher_entry.get("display_mode"):
            review_entry["candidates"] = teacher_entry["candidates"]
        if review_entry.get("actual_action") is None:
            actual_action = _actual_action_for_review(entry)
            if actual_action is not None:
                actual_candidate = next(
                    (
                        candidate
                        for candidate in teacher_entry["candidates"]
                        if _teacher_same_action(candidate.get("action"), actual_action)
                    ),
                    None,
                )
                review_entry["actual_action"] = actual_action
                review_entry["actual_q"] = actual_candidate.get("q_value") if actual_candidate else None
                review_entry["actual_prob"] = actual_candidate.get("prob") if actual_candidate else None
                review_entry["is_equal"] = _teacher_same_action(actual_action, review_entry.get("expected_action"))
                best_q = review_entry.get("best_q")
                actual_q = review_entry.get("actual_q")
                if best_q is not None and actual_q is not None:
                    review_entry["q_loss"] = max(0.0, float(best_q) - float(actual_q))
        entry.setdefault("teacher_reviews", []).append(review_entry)
        entry.setdefault("teacher_review", review_entry)

        for candidate in entry.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            local_action = candidate.get("action")
            match = None
            if teacher_entry.get("display_mode") == "joint_reach_dahai":
                local_type = local_action.get("type") if isinstance(local_action, dict) else None
                if local_type == "dahai":
                    matches = [
                        item for item in teacher_entry["candidates"]
                        if (item.get("action") or {}).get("pai") == local_action.get("pai")
                    ]
                elif local_type == "reach":
                    matches = [
                        item for item in teacher_entry["candidates"]
                        if (item.get("action") or {}).get("type") == "reach"
                    ]
                else:
                    matches = []
                if matches:
                    probabilities = [item.get("prob") for item in matches if item.get("prob") is not None]
                    match = {
                        "q_value": None,
                        "prob": sum(float(value) for value in probabilities) if probabilities else None,
                        "rank": None,
                    }
            else:
                for teacher_candidate in teacher_entry["candidates"]:
                    if _teacher_same_action(local_action, teacher_candidate.get("action")):
                        match = teacher_candidate
                        break
            if match:
                teacher_value = {
                    "model": teacher_entry["model"],
                    "q_value": match.get("q_value"),
                    "prob": match.get("prob"),
                    "rank": match.get("rank"),
                }
                candidate.setdefault("teachers", []).append(teacher_value)
                candidate.setdefault("teacher", teacher_value)

    for report_path in report_paths:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as e:
            overlays.append({"error": str(e), "report_path": str(report_path)})
            continue

        report_replay_id = report.get("replay_id") if isinstance(report, dict) else None
        # replay_verified 只在 expected 与报告 JSON replay_id 都已知且完全一致时为 True。
        # expected 缺失或报告缺 replay_id 时不允许"报告自证"或依赖文件名，一律不视为 exact。
        replay_verified = bool(
            expected_replay_id is not None
            and report_replay_id is not None
            and str(report_replay_id) == str(expected_replay_id)
        )
        if expected_replay_id is not None and report_replay_id is not None:
            if str(report_replay_id) != str(expected_replay_id):
                # 跨 replay 报告：明确拒绝，不做任何挂载（P1：身份域闭合）
                overlays.append(
                    {
                        "model": _infer_teacher_model_tag(report, report_path) if isinstance(report, dict) else None,
                        "report_path": str(report_path),
                        "teacher_decision_count": 0,
                        "attached_decision_count": 0,
                        "alignment": "replay_mismatch",
                        "expected_replay_id": str(expected_replay_id),
                        "actual_replay_id": str(report_replay_id),
                    }
                )
                continue

        try:
            model_tag, report_player_id, teacher_entries = _load_teacher_report_entries(report_path, decisions)
        except Exception as e:
            overlays.append({"error": str(e), "report_path": str(report_path)})
            continue

        attached = 0
        exact_event_identity = 0
        legacy_step = 0
        legacy_action_order = 0
        ambiguous = 0
        unmatched = 0
        duplicate_teacher_identity = 0
        identity_carrying_entries = 0
        own_index = 0
        attached_entry_ids: set[int] = set()
        seen_teacher_identities: set[tuple[int, int, str]] = set()

        def _attach_once(entry: dict, teacher_entry: dict) -> bool:
            nonlocal attached
            if id(entry) in attached_entry_ids:
                return False
            _attach_to_entry(entry, teacher_entry)
            attached += 1
            attached_entry_ids.add(id(entry))
            return True

        for teacher_entry in teacher_entries:
            raw_identity = _teacher_identity_key(teacher_entry)
            if raw_identity is not None:
                identity_carrying_entries += 1
            # replay_verified=false 时即使 entry 携带身份三元组，
            # 也只能走 step/action-order legacy，绝不 exact（P1）。
            identity = raw_identity if replay_verified else None
            if identity is not None:
                if identity in seen_teacher_identities:
                    # 报告内重复身份：拒绝第二条，不静默消失（P3）
                    duplicate_teacher_identity += 1
                    continue
                seen_teacher_identities.add(identity)
                matches = own_entries_by_identity.get(identity, [])
                if len(matches) == 1:
                    if _attach_once(matches[0], teacher_entry):
                        exact_event_identity += 1
                    else:
                        duplicate_teacher_identity += 1
                elif len(matches) > 1:
                    # 多候选：拒绝静默选择，标记 ambiguous
                    ambiguous += 1
                else:
                    unmatched += 1
                continue

            # legacy fallback：step 主，其次 action-order 正向扫描
            teacher_step = teacher_entry.get("step")
            if isinstance(teacher_step, int):
                entry = own_entries_by_step.get(teacher_step)
                if entry is not None and _attach_once(entry, teacher_entry):
                    legacy_step += 1
                else:
                    unmatched += 1
                continue

            actual_action = teacher_entry.get("actual_action")
            expected_action = teacher_entry.get("expected_action")
            matched_legacy = False
            while own_index < len(own_entries):
                entry = own_entries[own_index]
                own_index += 1
                if _teacher_same_action(entry.get("gt_action"), actual_action) or _teacher_same_action(entry.get("chosen"), actual_action):
                    if _attach_once(entry, teacher_entry):
                        legacy_action_order += 1
                    matched_legacy = True
                    break
                if actual_action is None and _teacher_same_action(entry.get("gt_action"), expected_action):
                    if _attach_once(entry, teacher_entry):
                        legacy_action_order += 1
                    matched_legacy = True
                    break
            if not matched_legacy:
                unmatched += 1

        overlays.append(
            {
                "model": model_tag,
                "report_path": str(report_path),
                "report_player_id": report_player_id,
                "teacher_decision_count": len(teacher_entries),
                "attached_decision_count": attached,
                "alignment": _alignment_label(
                    exact=exact_event_identity,
                    legacy_step=legacy_step,
                    legacy_action_order=legacy_action_order,
                    ambiguous=ambiguous,
                    unmatched=unmatched,
                    duplicate=duplicate_teacher_identity,
                    replay_mismatch=False,
                ),
                "replay_verified": replay_verified,
                "alignment_stats": {
                    "exact_event_identity": exact_event_identity,
                    "legacy_step": legacy_step,
                    "legacy_action_order": legacy_action_order,
                    "ambiguous": ambiguous,
                    "unmatched": unmatched,
                    "duplicate_teacher_identity": duplicate_teacher_identity,
                    "identity_carrying_entries": identity_carrying_entries,
                },
            }
        )

    decisions["teacher_review_overlays"] = overlays
    if overlays:
        decisions["teacher_review_overlay"] = overlays[0]
    return decisions

def _infer_player_bot_type(player_name: str | None, fallback: str | None = None) -> str:
    raw = (player_name or "").lower()
    if "ext_mortal" in raw or "external mortal" in raw or "weak_mortal" in raw or "weak mortal" in raw:
        return "ext_mortal"
    if "70k" in raw:
        return "70k"
    if "mortal" in raw:
        return "mortal"
    if "rulebase" in raw:
        return "rulebase"
    return fallback or "mortal"


def _default_checkpoint_for_bot_type(bot_type: str) -> Path:
    from workbench.runtime.resolver import resolve_bot_spec
    project_root = BASE_DIR.parent.parent
    _kind, path = resolve_bot_spec(bot_type, project_root)
    if path is None:
        raise ValueError(f"No default checkpoint for bot_type: {bot_type}")
    return path


def _external_review_links(naga_url: str = "", mortal_url: str = "") -> dict[str, str]:
    links: dict[str, str] = {}
    for key, raw_value in (("naga", naga_url), ("mortal", mortal_url)):
        value = raw_value.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{key} Review 链接必须是有效的 http/https URL")
        links[key] = value
    return links


_GUI_MORTAL_MODEL_LABELS = {
    "mortal": "V2 candidate",
    "70k": "70k",
    "ext_mortal": "External Mortal",
}


def _fetch_tenhou_url_events(text: str) -> tuple[list[dict], int]:
    """从天凤链接下载牌谱并转为 mjai 事件列表。

    Returns (events, tw) 其中 tw 为链接中指定的视角座位。
    """
    from convert.link_converter import parse_log_url
    from replay.bot import _load_events_from_source

    info = parse_log_url(text)
    if info.get("site") != "tenhou":
        raise ValueError("仅支持天凤牌谱链接（tenhou.net）")
    log_id = info["log_id"]
    tw = int(info.get("tw", "0"))

    endpoint = f"https://tenhou.net/5/mjlog2json.cgi?{log_id}"
    req = Request(endpoint, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://tenhou.net/",
    })
    with urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="replace").strip()
    if not body:
        raise ValueError("天凤服务器返回空内容，牌谱可能不存在")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body[:120].replace("\n", " ")
        raise ValueError(f"天凤返回非 JSON 内容: '{preview}...'") from exc

    events = _load_events_from_source(data, input_type="tenhou6")
    return events, tw


async def _events_from_replay_form(
    *,
    files: list[UploadFile],
    json_text: str,
    input_type: str,
) -> list[dict]:
    has_files = files and any(f.filename for f in files if f.filename)
    if has_files:
        valid_files = [f for f in files if f.filename]
        if len(valid_files) > 1:
            raise ValueError("暂不支持多文件跑谱，请一次上传一个文件")
        text = (await valid_files[0].read()).decode("utf-8", errors="replace").strip()
        if not text:
            raise ValueError("上传文件为空")
    elif json_text.strip():
        text = json_text.strip()
    else:
        raise ValueError("请上传文件或粘贴 JSON 文本")

    if input_type == "url":
        events, _tw = _fetch_tenhou_url_events(text)
        return events

    if input_type == "tenhou6":
        from replay.bot import _load_events_from_source

        return _load_events_from_source(json.loads(text), input_type="tenhou6")

    if input_type == "mjai":
        lines = text.splitlines()
        if len(lines) > 1:
            return [json.loads(line) for line in lines if line.strip()]
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]

    raise ValueError(f"未知的 input_type：{input_type}")


def _candidate_q_value(candidate: dict) -> float | None:
    for key in ("final_score", "beam_score", "logit"):
        value = _float_or_none(candidate.get(key))
        if value is not None:
            return value
    return None


def _actual_action_for_review(entry: dict) -> dict | None:
    actual = entry.get("gt_action")
    if actual is not None:
        return actual
    chosen = entry.get("chosen")
    if isinstance(chosen, dict) and chosen.get("type") == "none":
        return {"type": "none"}
    return None


def _build_runtime_teacher_report(
    *,
    replay_id: str,
    model_type: str,
    player_id: int,
    checkpoint: Path,
    decisions: dict,
) -> dict:
    kyoku_map: dict[tuple, dict] = {}
    for entry in decisions.get("log", []):
        if not isinstance(entry, dict) or entry.get("is_obs"):
            continue
        actual_action = _actual_action_for_review(entry)
        if _decision_kind(actual_action) not in ("draw_discard", "reach", "call"):
            # pass/none 及 hora/ryukyoku 等终局动作无事件身份、无候选权重，
            # 不作为 teacher 决策挂载
            continue
        key = (
            entry.get("bakaze", ""),
            int(entry.get("kyoku", 0)),
            int(entry.get("honba", 0)),
        )
        kyoku = kyoku_map.setdefault(
            key,
            {
                "bakaze": key[0],
                "kyoku": key[1],
                "honba": key[2],
                "entries": [],
            },
        )
        details = []
        for candidate in entry.get("candidates", []):
            if not isinstance(candidate, dict) or not isinstance(candidate.get("action"), dict):
                continue
            details.append(
                {
                    "action": candidate.get("action"),
                    "q_value": _candidate_q_value(candidate),
                    "prob": _float_or_none(candidate.get("prob")),
                }
            )
        kyoku["entries"].append(
            {
                "step": entry.get("step"),
                "source_event_index": entry.get("source_event_index"),
                "actor": _action_actor(actual_action),
                "decision_kind": _decision_kind(actual_action),
                "junme": entry.get("junme"),
                "tiles_left": entry.get("tiles_left"),
                "shanten": entry.get("shanten"),
                "actual": actual_action,
                "expected": entry.get("chosen"),
                "is_equal": _teacher_same_action(actual_action, entry.get("chosen")),
                "details": details,
            }
        )
    return {
        "schema": "keqing1.runtime_teacher_report.v1",
        "replay_id": replay_id,
        "player_id": player_id,
        "bot_type": model_type,
        "checkpoint": str(checkpoint),
        "review": {
            "model_tag": _GUI_MORTAL_MODEL_LABELS.get(model_type, model_type),
            "kyokus": list(kyoku_map.values()),
        },
    }


def _write_runtime_teacher_report(
    *,
    replay_id: str,
    model_type: str,
    player_id: int,
    checkpoint: Path,
    decisions: dict,
) -> Path:
    project_root = BASE_DIR.parent.parent
    out_dir = project_root / "artifacts" / "replay_model_reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = _GUI_MORTAL_MODEL_LABELS.get(model_type, model_type)
    safe_model = safe_model.replace("@", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")
    report_path = out_dir / f"{replay_id}__{safe_model}__p{player_id}.json"
    report = _build_runtime_teacher_report(
        replay_id=replay_id,
        model_type=model_type,
        player_id=player_id,
        checkpoint=checkpoint,
        decisions=decisions,
    )
    report_path.write_text(
        json.dumps(_json_safe(report), cls=_NumpyEncoder, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    return report_path


def _list_review_history(
    *,
    storage=None,
    report_dir: Path | None = None,
    project_root: Path | None = None,
) -> list[dict]:
    storage = storage or get_storage()
    project_root = (project_root or BASE_DIR.parent.parent).resolve()
    report_dir = report_dir or (project_root / "artifacts" / "replay_model_reviews")
    metas = {item["replay_id"]: item for item in storage.list() if item.get("replay_id")}
    grouped: dict[tuple[str, int], dict] = {}

    if report_dir.exists():
        for report_path in report_dir.glob("*.json"):
            parts = report_path.stem.rsplit("__", 2)
            if len(parts) != 3 or not parts[2].startswith("p"):
                continue
            replay_id, safe_model, player_part = parts
            meta = metas.get(replay_id)
            if meta is None:
                continue
            try:
                player_id = int(player_part[1:])
            except ValueError:
                continue

            model = next(
                (
                    label
                    for label in _GUI_MORTAL_MODEL_LABELS.values()
                    if label.replace("@", "_").replace("/", "_").replace("\\", "_").replace(" ", "_") == safe_model
                ),
                safe_model,
            )
            key = (replay_id, player_id)
            item = grouped.setdefault(
                key,
                {
                    "replay_id": replay_id,
                    "created_at": meta.get("created_at", ""),
                    "player_id": player_id,
                    "player_name": "",
                    "player_names": meta.get("player_names", []),
                    "kyoku_count": meta.get("kyoku_count", 0),
                    "total_steps": meta.get("total_steps", 0),
                    "models": [],
                    "teacher_report_paths": [],
                    "external_review_links": meta.get("external_review_links", {}),
                },
            )
            names = item["player_names"]
            if 0 <= player_id < len(names):
                item["player_name"] = names[player_id]
            relative_path = report_path.resolve().relative_to(project_root).as_posix()
            item["models"].append(model)
            item["teacher_report_paths"].append(relative_path)

    model_order = {
        "External Mortal": 0,
        "70k": 1,
        "V2 candidate": 2,
    }
    history = list(grouped.values())
    for item in history:
        paired = sorted(
            zip(item["models"], item["teacher_report_paths"]),
            key=lambda pair: (model_order.get(pair[0], 100), pair[0]),
        )
        item["models"] = [pair[0] for pair in paired]
        item["teacher_report_paths"] = [pair[1] for pair in paired]
    history.sort(key=lambda item: item["created_at"], reverse=True)
    return history


_DEFAULT_BEHAVIOR_CASEBOOK = BASE_DIR.parent.parent / "artifacts" / "replay_model_reviews"
_DEFAULT_PAIRED_BEHAVIOR_CASEBOOK = _DEFAULT_BEHAVIOR_CASEBOOK
def _get_casebook_checkpoint(model_label: str) -> Path:
    if model_label == "70k":
        return _default_checkpoint_for_bot_type("70k")
    return _default_checkpoint_for_bot_type("mortal")


def _load_behavior_case_manifest(casebook_dir: Path = _DEFAULT_BEHAVIOR_CASEBOOK) -> list[dict]:
    manifest_path = casebook_dir / "manifest.jsonl"
    if not manifest_path.exists():
        return []
    rows = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["casebook_dir"] = str(casebook_dir)
        row["manifest_path"] = str(manifest_path)
        rows.append(row)
    return rows


def _load_paired_behavior_case_manifest(casebook_dir: Path = _DEFAULT_PAIRED_BEHAVIOR_CASEBOOK) -> list[dict]:
    manifest_path = casebook_dir / "paired_manifest.jsonl"
    if not manifest_path.exists():
        return []
    rows = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["casebook_dir"] = str(casebook_dir)
        row["manifest_path"] = str(manifest_path)
        rows.append(row)
    return rows


def _read_mjson_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _find_behavior_case(case_id: str) -> tuple[dict, Path] | None:
    casebook_dir = _DEFAULT_BEHAVIOR_CASEBOOK
    for row in _load_behavior_case_manifest(casebook_dir):
        if row.get("case_id") == case_id:
            return row, casebook_dir
    return None


def _find_paired_behavior_case(case_id: str) -> tuple[dict, Path] | None:
    casebook_dir = _DEFAULT_PAIRED_BEHAVIOR_CASEBOOK
    for row in _load_paired_behavior_case_manifest(casebook_dir):
        if row.get("case_id") == case_id:
            return row, casebook_dir
    return None


def _resolve_focus_replay_step(decisions: dict, focus_event_index: int | None) -> tuple[int | None, str]:
    if focus_event_index is None:
        return None, "missing"
    log = decisions.get("log", []) if isinstance(decisions, dict) else []
    exact = [
        idx
        for idx, entry in enumerate(log)
        if isinstance(entry, dict) and entry.get("source_event_index") == focus_event_index
    ]
    if exact:
        return int(exact[0]), "exact"
    candidates = [
        (abs(int(entry["source_event_index"]) - int(focus_event_index)), idx)
        for idx, entry in enumerate(log)
        if isinstance(entry, dict) and isinstance(entry.get("source_event_index"), int)
    ]
    if not candidates:
        return None, "missing"
    _distance, idx = min(candidates, key=lambda item: (item[0], item[1]))
    return int(idx), "nearest"


def _checkpoint_for_behavior_case(row: dict) -> Path:
    from workbench.runtime.resolver import resolve_model_checkpoint
    checkpoint_path = row.get("checkpoint_path")
    if isinstance(checkpoint_path, str) and checkpoint_path.strip():
        try:
            return resolve_model_checkpoint(checkpoint_path.strip(), BASE_DIR.parent.parent)
        except Exception:
            path = Path(checkpoint_path.strip())
            if not path.is_absolute():
                path = BASE_DIR.parent.parent / path
            return path
    model_label = str(row.get("model_label", ""))
    return _get_casebook_checkpoint(model_label)


def _resolve_casebook_path(casebook_dir: Path, raw_path: str) -> Path | None:
    path = (casebook_dir / raw_path).resolve()
    if not path.exists() or casebook_dir.resolve() not in path.parents:
        return None
    return path


def _checkpoint_from_raw(raw_path: str | None, *, fallback_model: str = "mortal") -> Path:
    from workbench.runtime.resolver import resolve_model_checkpoint
    if isinstance(raw_path, str) and raw_path.strip():
        try:
            return resolve_model_checkpoint(raw_path.strip(), BASE_DIR.parent.parent)
        except Exception:
            path = Path(raw_path.strip())
            if not path.is_absolute():
                path = BASE_DIR.parent.parent / path
            return path
    return _default_checkpoint_for_bot_type(fallback_model)


def _import_case_mjson(
    *,
    row: dict,
    mjson_path: Path,
    checkpoint: Path,
    player_id: int,
    focus_event_index: int | None,
) -> tuple[str, dict, int | None, str, list[dict]]:
    from replay.api import run_replay_single_raw
    from replay.bot import render_replay_json

    events = _read_mjson_events(mjson_path)
    bot = run_replay_single_raw(
        events,
        player_id=player_id,
        checkpoint=str(checkpoint),
        input_type="mjai",
        bot_type="mortal",
    )
    decisions = normalize_replay_decisions(render_replay_json(bot))
    decisions["bot_type"] = "mortal"
    decisions["casebook_case"] = row
    focus_replay_step, focus_resolution = _resolve_focus_replay_step(decisions, focus_event_index)
    decisions["casebook_focus"] = {
        "focus_event_index": focus_event_index,
        "focus_replay_step": focus_replay_step,
        "focus_resolution": focus_resolution,
    }
    normalized_events = _normalize_replay_events(events)
    decisions = _merge_terminal_event_details(decisions, normalized_events)
    storage = get_storage()
    replay_id = storage.save(
        events=normalized_events,
        decisions=decisions,
        bot_type="mortal",
        player_names=decisions.get("player_names"),
        checkpoint=str(checkpoint),
    )
    return replay_id, decisions, focus_replay_step, focus_resolution, normalized_events


# ========== 旧接口（兼容） ==========

@app.post("/api/replay")
async def replay(
    player_id: Annotated[int, Form()] = 0,
    checkpoint: Annotated[str, Form()] = "",
    bot_type: Annotated[str, Form()] = "mortal",
    files: Annotated[list[UploadFile], File()] = [],
    json_text: Annotated[str, Form()] = "",
    input_type: Annotated[str, Form()] = "url",
):
    """Python 只返回 JSON，前端 Vue 渲染。（兼容旧接口）"""
    from replay.api import run_replay_single_raw
    from replay.bot import render_replay_json
    storage = get_storage()

    has_files = files and any(f.filename for f in files if f.filename)
    events: list[dict] | None = None

    try:
        if has_files:
            valid_files = [f for f in files if f.filename]
            if len(valid_files) > 1:
                return JSONResponse(status_code=400, content={"error": "暂不支持多文件跑谱，请一次上传一个文件"})
            upload = valid_files[0]
            text = (await upload.read()).decode("utf-8", errors="replace").strip()
            if not text:
                return JSONResponse(status_code=400, content={"error": "上传文件为空"})
        elif not json_text.strip():
            return JSONResponse(status_code=400, content={"error": "请上传文件或粘贴 JSON 文本"})
        else:
            text = json_text.strip()

        if input_type == "url":
            events, tw = _fetch_tenhou_url_events(text)
            if player_id == 0:
                player_id = tw
            bot = run_replay_single_raw(events, player_id=player_id, checkpoint=checkpoint or None, input_type="mjai", bot_type=bot_type)

        elif input_type == "tenhou6":
            data = json.loads(text)
            bot = run_replay_single_raw(data, player_id=player_id, checkpoint=checkpoint or None, input_type="tenhou6", bot_type=bot_type)
            try:
                from replay.bot import _load_events_from_source

                events = _load_events_from_source(data, input_type="tenhou6")
            except Exception:
                events = None

        elif input_type == "mjai":
            lines = text.splitlines()
            if len(lines) > 1:
                events = [json.loads(l) for l in lines if l.strip()]
            else:
                # 单行：判断是 JSON 数组还是普通 JSON 对象
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        events = parsed  # JSON 数组直接使用
                    else:
                        events = [parsed]  # 单个 JSON 对象包装成列表
                except json.JSONDecodeError:
                    events = [json.loads(l) for l in lines if l.strip()]
            bot = run_replay_single_raw(events, player_id=player_id, checkpoint=checkpoint or None, input_type="mjai", bot_type=bot_type)

        else:
            return JSONResponse(status_code=400, content={"error": f"未知的 input_type：{input_type}"})

        normalized_events = _normalize_replay_events(events)
        result = normalize_replay_decisions(render_replay_json(bot))
        result = _merge_terminal_event_details(result, normalized_events)
        result["bot_type"] = bot_type
        if events:
            checkpoint_for_storage = checkpoint or (
                None if bot_type == "rulebase" else str(_default_checkpoint_for_bot_type(bot_type))
            )
            replay_id = storage.save(
                events=normalized_events,
                decisions=result,
                bot_type=bot_type,
                player_names=result.get("player_names"),
                checkpoint=checkpoint_for_storage,
            )
            result = {
                **result,
                "replay_id": replay_id,
            }
        return Response(
            content=json.dumps(_json_safe(result), cls=_NumpyEncoder, ensure_ascii=False, allow_nan=False),
            media_type="application/json",
        )

    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"error": f"JSON 解析失败: {e}"})
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={"error": str(e), "detail": traceback.format_exc()})


@app.post("/api/replay/multi-teacher")
async def replay_multi_teacher(
    player_id: Annotated[int, Form()] = 0,
    model_types: Annotated[list[str], Form()] = [],
    naga_url: Annotated[str, Form()] = "",
    mortal_url: Annotated[str, Form()] = "",
    files: Annotated[list[UploadFile], File()] = [],
    json_text: Annotated[str, Form()] = "",
    input_type: Annotated[str, Form()] = "url",
):
    """Run a replay with local Mortal checkpoints and attach local + external Q/P overlays."""
    from replay.api import run_replay_single_raw
    from replay.bot import render_replay_json

    selected_models = [model.strip() for model in model_types if model and model.strip()]
    if not selected_models:
        selected_models = ["ext_mortal", "70k", "mortal"]
    allowed_models = set(_GUI_MORTAL_MODEL_LABELS)
    invalid = [model for model in selected_models if model not in allowed_models]
    if invalid:
        return JSONResponse(
            status_code=400,
            content={"error": f"GUI review 只支持 Mortal checkpoint：{', '.join(sorted(allowed_models))}"},
        )

    try:
        external_links = _external_review_links(naga_url, mortal_url)
        # 天凤链接时从 URL 提取 tw 作为默认视角
        if input_type == "url" and player_id == 0 and json_text.strip():
            try:
                from convert.link_converter import parse_log_url
                _info = parse_log_url(json_text.strip())
                player_id = int(_info.get("tw", "0"))
            except Exception:
                pass
        events = await _events_from_replay_form(files=files, json_text=json_text, input_type=input_type)
        normalized_events = _normalize_replay_events(events)
        storage = get_storage()
        decisions_by_model: dict[str, dict] = {}
        checkpoints: dict[str, Path] = {}

        for model_type in selected_models:
            checkpoint = _default_checkpoint_for_bot_type(model_type)
            bot = run_replay_single_raw(
                events,
                player_id=player_id,
                checkpoint=str(checkpoint),
                input_type="url",
                bot_type=model_type,
            )
            decisions = normalize_replay_decisions(render_replay_json(bot))
            decisions = _merge_terminal_event_details(decisions, normalized_events)
            decisions["bot_type"] = model_type
            decisions["model_label"] = _GUI_MORTAL_MODEL_LABELS.get(model_type, model_type)
            decisions_by_model[model_type] = decisions
            checkpoints[model_type] = checkpoint

        base_model = selected_models[0]
        base_decisions = decisions_by_model[base_model]
        replay_id = storage.save(
            events=normalized_events,
            decisions=base_decisions,
            bot_type=base_model,
            player_names=base_decisions.get("player_names"),
            checkpoint=str(checkpoints[base_model]),
            external_review_links=external_links,
        )
        base_decisions["replay_id"] = replay_id

        report_paths = []
        for model_type in selected_models:
            report_path = _write_runtime_teacher_report(
                replay_id=replay_id,
                model_type=model_type,
                player_id=player_id,
                checkpoint=checkpoints[model_type],
                decisions=decisions_by_model[model_type],
            )
            report_paths.append(report_path)

        if external_links:
            report_paths.extend(
                write_external_teacher_reports(
                    replay_id=replay_id,
                    player_id=player_id,
                    decisions=base_decisions,
                    links=external_links,
                    output_dir=BASE_DIR.parent.parent / "artifacts" / "replay_model_reviews",
                )
            )

        attached = _attach_teacher_report_overlays(base_decisions, report_paths, expected_replay_id=replay_id)
        project_root = BASE_DIR.parent.parent.resolve()
        attached["teacher_report_paths"] = [
            path.resolve().relative_to(project_root).as_posix()
            for path in report_paths
        ]
        attached["selected_teacher_models"] = [
            {
                "type": model_type,
                "label": _GUI_MORTAL_MODEL_LABELS.get(model_type, model_type),
                "checkpoint": str(checkpoints[model_type]),
            }
            for model_type in selected_models
        ]
        attached["external_review_links"] = external_links
        return Response(
            content=json.dumps(_json_safe(attached), cls=_NumpyEncoder, ensure_ascii=False, allow_nan=False),
            media_type="application/json",
        )
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"error": f"JSON 解析失败: {e}"})
    except Exception as e:
        import traceback

        return JSONResponse(status_code=500, content={"error": str(e), "detail": traceback.format_exc()})


# ========== 持久化存储 API（新增） ==========

@app.post("/api/replay/save", response_class=JSONResponse)
async def save_replay(
    events: list[dict] = Form(...),
    decisions: dict = Form(...),
    bot_type: str = Form("mortal"),
    player_names: str = Form(""),
    checkpoint: str = Form(""),
):
    """手动保存回放到存储（内部使用或高级用户）。"""
    storage = get_storage()
    names = player_names.split(",") if player_names else None
    replay_id = storage.save(
        events=events,
        decisions=decisions,
        bot_type=bot_type,
        player_names=names,
        checkpoint=checkpoint or None,
    )
    return JSONResponse(content={"replay_id": replay_id, "status": "saved"})


@app.get("/api/replay/list", response_class=JSONResponse)
async def list_replays():
    """列出所有已保存的回放（按时间倒序）。"""
    storage = get_storage()
    metas = storage.list()
    return JSONResponse(content=metas)


@app.get("/api/replay/review-history", response_class=JSONResponse)
async def list_review_history():
    """列出已持久化且包含 teacher report 的 Review。"""
    return JSONResponse(content=_list_review_history())


@app.get("/api/replay/{replay_id}", response_class=JSONResponse)
async def get_replay(
    replay_id: str,
    player_id: int | None = None,
    teacher_report: str | None = None,
    teacher_reports: list[str] = Query(default=[]),
):
    """获取回放完整数据（decisions）。"""
    storage = get_storage()
    decisions = storage.load_decisions(replay_id)
    if decisions is None:
        return JSONResponse(status_code=404, content={"error": f"回放 {replay_id} 不存在"})
    meta = storage.load_meta(replay_id) or {}
    events = _normalize_replay_events(storage.load_events(replay_id))
    if (
        isinstance(decisions, dict)
        and player_id is not None
        and decisions.get("player_id") != player_id
    ):
        from replay.api import run_replay_single_raw
        from replay.bot import render_replay_json

        if not events:
            return JSONResponse(status_code=404, content={"error": f"回放 {replay_id} 事件不存在"})
        player_names = meta.get("player_names") or decisions.get("player_names") or []
        fallback_bot_type = meta.get("bot_type") or decisions.get("bot_type") or "mortal"
        player_name = player_names[player_id] if 0 <= player_id < len(player_names) else None
        bot_type = _infer_player_bot_type(player_name, fallback=fallback_bot_type)
        checkpoint = meta.get("checkpoint") or _default_checkpoint_for_bot_type(bot_type)
        bot = run_replay_single_raw(
            events,
            player_id=player_id,
            checkpoint=checkpoint,
            input_type="url",
            bot_type=bot_type,
        )
        decisions = render_replay_json(bot)
        decisions["bot_type"] = bot_type
    if isinstance(decisions, dict):
        decisions = normalize_replay_decisions(decisions, meta=meta, events=events)
        decisions = _merge_terminal_event_details(decisions, events)
        raw_teacher_reports = list(teacher_reports or [])
        if teacher_report:
            raw_teacher_reports.insert(0, teacher_report)
        report_paths: list[Path] = []
        report_errors: list[dict] = []
        for raw_report in raw_teacher_reports:
            for item in str(raw_report).split(","):
                item = item.strip()
                if not item:
                    continue
                try:
                    resolved = _resolve_optional_project_path(item)
                    if resolved is not None:
                        report_paths.append(resolved)
                except Exception as e:
                    report_errors.append({"error": str(e), "report_path": item})
        if report_paths:
            decisions = _attach_teacher_report_overlays(decisions, report_paths, expected_replay_id=replay_id)
        if report_errors:
            overlays = list(decisions.get("teacher_review_overlays", [])) + report_errors
            decisions["teacher_review_overlays"] = overlays
            decisions["teacher_review_overlay"] = overlays[0]
    return JSONResponse(content=_json_safe(decisions))


@app.get("/api/replay/{replay_id}/meta", response_class=JSONResponse)
async def get_replay_meta(replay_id: str):
    """获取回放元信息。"""
    storage = get_storage()
    meta = storage.load_meta(replay_id)
    if meta is None:
        return JSONResponse(status_code=404, content={"error": f"回放 {replay_id} 不存在"})
    return JSONResponse(content=meta)


@app.get("/api/replay/{replay_id}/events", response_class=JSONResponse)
async def get_replay_events(replay_id: str, from_step: int = 0, limit: int = 50):
    """分页获取 mjai 事件流。"""
    storage = get_storage()
    events = _normalize_replay_events(storage.load_events(replay_id))
    if not events:
        return JSONResponse(status_code=404, content={"error": f"回放 {replay_id} 事件不存在"})

    total = len(events)
    paged = events[from_step : from_step + limit]
    return JSONResponse(content={
        "events": paged,
        "total": total,
        "next_step": from_step + len(paged) if from_step + len(paged) < total else None,
    })


def _collect_replay_groups():
    """递归聚合项目中的回放导出清单。"""
    project_root = BASE_DIR.parent.parent
    groups = []

    manifest_paths = []
    for pattern in ("**/replays/manifest.json", "**/anomaly_replays/manifest.json"):
        manifest_paths.extend(project_root.glob(pattern))

    for manifest_path in sorted(
        manifest_paths,
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        export_dir = manifest_path.parent
        run_dir = export_dir.parent
        collection_type = export_dir.name

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        stats_path = run_dir / "stats.json"
        stats = None
        if stats_path.exists():
            try:
                stats = json.loads(stats_path.read_text(encoding="utf-8"))
            except Exception:
                stats = None

        groups.append(
            {
                "output_dir": run_dir.name,
                "output_dir_path": str(run_dir),
                "manifest_path": str(manifest_path),
                "collection_type": collection_type,
                "updated_at": manifest_path.stat().st_mtime,
                "stats": {
                    "games": stats.get("games"),
                    "completed_games": stats.get("completed_games"),
                    "error_games": len(stats.get("error_games", [])),
                    "seconds_per_game": stats.get("seconds_per_game"),
                    "avg_turns": stats.get("avg_turns"),
                }
                if isinstance(stats, dict)
                else None,
                "items": manifest,
            }
        )
    return groups


@app.get("/api/selfplay/replay-collections", response_class=JSONResponse)
async def list_selfplay_replay_collections():
    """列出项目内通用对局回放清单，供 ReplayUI 浏览。"""
    return JSONResponse(content={"groups": _collect_replay_groups()})


@app.get("/api/selfplay/anomaly-replays", response_class=JSONResponse)
async def list_selfplay_anomaly_replays():
    """兼容旧接口，返回相同的聚合对局回放清单。"""
    return JSONResponse(content={"groups": _collect_replay_groups()})


# ========== Model Ladder（模型天梯与账号） ==========

_LADDER_PROJECT_ROOT = BASE_DIR.parent.parent
# 默认读取 KEQING_DATA_ROOT/ladder/registries；Ladder 专用环境变量仅用于高级部署 override。
_LADDER_SEASONS_DIR = ladder_data.resolve_config_dir(_LADDER_PROJECT_ROOT)
# R11-A1 可观测性：启动即打印实际加载的赛季注册表，避免"到底读哪份配置"靠猜。
logging.getLogger(__name__).info(
    "Ladder registry: %s source=%s",
    _LADDER_SEASONS_DIR,
    "KEQING_LADDER_CONFIG_DIR"
    if os.environ.get("KEQING_LADDER_CONFIG_DIR", "").strip()
    else "KEQING_DATA_ROOT/ladder/registries",
)


@app.get("/api/ladder/seasons", response_class=JSONResponse)
async def list_ladder_seasons():
    """列出已注册的评测赛季（含默认赛季与每赛季 readiness）。"""
    try:
        catalog = ladder_data.list_seasons_catalog(_LADDER_PROJECT_ROOT, _LADDER_SEASONS_DIR)
    except ladder_data.SeasonRegistryError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(content=catalog)


# ---------------------------------------------------------------------------
# R11-E1：Season Registry 生命周期管理（canonical runtime registry 的写侧）
# ---------------------------------------------------------------------------

def _season_manager():
    """懒加载 season_registry（避免模块级循环导入）。"""
    from replay import season_registry

    return season_registry


def _manager_errors(exc: Exception) -> JSONResponse:
    """Season manager 错误映射：404 未找到 / 409 生命周期冲突 / 500 注册表损坏。"""
    from replay.ladder import SeasonNotFoundError, SeasonRegistryError

    if isinstance(exc, SeasonNotFoundError):
        return JSONResponse(status_code=404, content={"error": str(exc)})
    if isinstance(exc, (ValueError, SeasonRegistryError)):
        return JSONResponse(status_code=409, content={"error": str(exc)})
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/ladder/seasons", response_class=JSONResponse)
async def create_ladder_season(payload: dict):
    """创建赛季（status=draft, default=False, 空阵容；enrollment 由 E2 编辑）。"""
    sr = _season_manager()
    try:
        season = sr.create_season(
            _LADDER_SEASONS_DIR,
            season_id=str(payload.get("season_id") or ""),
            title=payload.get("title"),
            scoring=payload.get("scoring"),
        )
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.get("/api/ladder/seasons/{season_id}/preflight", response_class=JSONResponse)
async def preflight_ladder_season(season_id: str):
    """R14-A: 只读预检赛季是否具备启动资格（纯只读，无状态副作用）。"""
    from runtime.manifest import preflight_season_runtime
    report = preflight_season_runtime(_LADDER_SEASONS_DIR, season_id, project_root=_LADDER_PROJECT_ROOT)
    return JSONResponse(content=report.model_dump())


@app.get("/api/ladder/seasons/{season_id}/manifest", response_class=JSONResponse)
async def get_ladder_season_manifest(season_id: str):
    """R14-A: 获取赛季运行时清单（已固化或历史投影）。"""
    from runtime.manifest import get_season_runtime_manifest
    try:
        manifest = get_season_runtime_manifest(_LADDER_SEASONS_DIR, season_id, project_root=_LADDER_PROJECT_ROOT)
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=manifest.model_dump())


@app.post("/api/ladder/seasons/{season_id}/start", response_class=JSONResponse)
async def start_ladder_season(season_id: str):
    """开始赛季：draft → running，并固化 runtime_manifest（R14-A 强门禁原子写）。"""
    from runtime.manifest import start_season_runtime
    try:
        season = start_season_runtime(_LADDER_SEASONS_DIR, season_id, project_root=_LADDER_PROJECT_ROOT)
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.post("/api/ladder/seasons/{season_id}/complete", response_class=JSONResponse)
async def complete_ladder_season(season_id: str):
    """结束赛季：running → completed（当前 default 自动摘除 default）。"""
    sr = _season_manager()
    try:
        season = sr.set_season_status(_LADDER_SEASONS_DIR, season_id, "completed")
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.post("/api/ladder/seasons/{season_id}/archive", response_class=JSONResponse)
async def archive_ladder_season(season_id: str):
    """归档赛季：completed → archived（running 也可直接归档，同时摘除 default）。"""
    sr = _season_manager()
    try:
        season = sr.set_season_status(_LADDER_SEASONS_DIR, season_id, "archived")
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.post("/api/ladder/seasons/{season_id}/set-default", response_class=JSONResponse)
async def set_default_ladder_season(season_id: str):
    """设为当前赛季：只允许 running；自动摘除其他赛季的 default。"""
    sr = _season_manager()
    try:
        season = sr.set_default_season(_LADDER_SEASONS_DIR, season_id)
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.get("/api/ladder/seasons/{season_id}/config", response_class=JSONResponse)
async def get_ladder_season_config(season_id: str):
    """读取单个赛季注册表配置（manager 详情视图）。"""
    sr = _season_manager()
    try:
        season = sr.get_season(_LADDER_SEASONS_DIR, season_id)
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.delete("/api/ladder/seasons/{season_id}", response_class=JSONResponse)
async def delete_ladder_season(season_id: str):
    """删除赛季（R11 post-release）：服务级事务——data_lock + registry lock 内
    重新校验 running/default 并重新 count Match 引用（防 TOCTOU）。"""
    sr = _season_manager()
    try:
        result = sr.delete_season(_LADDER_SEASONS_DIR, season_id)
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=result)


@app.put("/api/ladder/seasons/{season_id}/enrollment", response_class=JSONResponse)
async def set_ladder_season_enrollment(season_id: str, payload: dict):
    """编辑赛季参赛阵容（R11-E2）。

    输入是 Participants Account ID 列表；backend 按权限矩阵校验并重新分组
    （draft 可增删 / running 只增 / completed+archived 只读）。
    """
    sr = _season_manager()
    account_ids = payload.get("account_ids") or []
    provenance_by_model = payload.get("provenance_by_model")
    if not isinstance(account_ids, list):
        return _manager_errors(ValueError("account_ids 必须是数组"))
    if provenance_by_model is not None and not isinstance(provenance_by_model, dict):
        return _manager_errors(ValueError("provenance_by_model 必须是对象"))
    try:
        season = sr.set_season_enrollment(
            _LADDER_SEASONS_DIR,
            season_id,
            account_ids,
            provenance_by_model=provenance_by_model,
        )
    except (ValueError, SeasonRegistryError, SeasonNotFoundError) as exc:
        return _manager_errors(exc)
    return JSONResponse(content=season)


@app.get("/api/ladder/seasons/{season_id}", response_class=JSONResponse)
async def get_ladder(season_id: str, sort: str = "pt"):
    """赛季天梯榜：账号排名 + 模型展示性聚合。"""
    try:
        payload = ladder_data.load_ladder(_LADDER_PROJECT_ROOT, _LADDER_SEASONS_DIR, season_id, sort=sort)
    except ladder_data.SeasonNotFoundError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ladder_data.SeasonDataError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc), "reason": ladder_data.season_data_problem(exc)})
    except ladder_data.SeasonRegistryError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(content=payload)


@app.get("/api/ladder/seasons/{season_id}/accounts/{account_id}", response_class=JSONResponse)
async def get_ladder_account(season_id: str, account_id: str, recent_games: int = 50, curve_points: int = 240):
    """账号详情：聚合指标 + PT/Rating 曲线 + 最近对局（不加载全部牌谱）。"""
    recent_limit = max(0, min(int(recent_games), 200))
    curve_max = max(0, min(int(curve_points), 1000))
    try:
        payload = ladder_data.load_account(
            _LADDER_PROJECT_ROOT,
            _LADDER_SEASONS_DIR,
            season_id,
            account_id,
            recent_limit=recent_limit,
            curve_max_points=curve_max,
        )
    except (ladder_data.SeasonNotFoundError, ladder_data.AccountNotFoundError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ladder_data.SeasonDataError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc), "reason": ladder_data.season_data_problem(exc)})
    except ladder_data.SeasonRegistryError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(content=payload)


@app.get("/api/ladder/seasons/{season_id}/models/{model_id}", response_class=JSONResponse)
async def get_ladder_model(season_id: str, model_id: str):
    """模型详情：账号横向对比 + 可选联赛聚合。"""
    try:
        payload = ladder_data.load_model(_LADDER_PROJECT_ROOT, _LADDER_SEASONS_DIR, season_id, model_id)
    except (ladder_data.SeasonNotFoundError, ladder_data.ModelNotFoundError) as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except ladder_data.SeasonDataError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc), "reason": ladder_data.season_data_problem(exc)})
    except ladder_data.SeasonRegistryError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    return JSONResponse(content=payload)


@app.get("/api/behavior-casebook", response_class=JSONResponse)
async def list_behavior_casebook():
    """列出默认主线行为诊断 casebook。"""
    casebook_dir = _DEFAULT_BEHAVIOR_CASEBOOK
    cases = _load_behavior_case_manifest(casebook_dir)
    paired_casebook_dir = _DEFAULT_PAIRED_BEHAVIOR_CASEBOOK
    paired_cases = _load_paired_behavior_case_manifest(paired_casebook_dir)
    case_counts: dict[str, int] = {}
    for row in cases:
        kind = str(row.get("case_kind", "unknown"))
        case_counts[kind] = case_counts.get(kind, 0) + 1
    paired_case_counts: dict[str, int] = {}
    for row in paired_cases:
        kind = str(row.get("case_kind", "unknown"))
        paired_case_counts[kind] = paired_case_counts.get(kind, 0) + 1
    updated_at = None
    manifest_path = casebook_dir / "manifest.jsonl"
    if manifest_path.exists():
        updated_at = manifest_path.stat().st_mtime
    paired_updated_at = None
    paired_manifest_path = paired_casebook_dir / "paired_manifest.jsonl"
    if paired_manifest_path.exists():
        paired_updated_at = paired_manifest_path.stat().st_mtime
    return JSONResponse(
        content={
            "casebook_dir": str(casebook_dir),
            "manifest_path": str(manifest_path),
            "updated_at": updated_at,
            "case_counts": case_counts,
            "cases": cases,
            "paired_casebook_dir": str(paired_casebook_dir),
            "paired_manifest_path": str(paired_manifest_path),
            "paired_updated_at": paired_updated_at,
            "paired_case_counts": paired_case_counts,
            "paired_cases": paired_cases,
        }
    )


@app.post("/api/behavior-casebook/import/{case_id}", response_class=JSONResponse)
async def import_behavior_case(case_id: str):
    """把 casebook 中的 mjson 导入为普通 replay，并返回可跳转的 replay id。"""
    found = _find_behavior_case(case_id)
    if found is None:
        return JSONResponse(status_code=404, content={"error": f"case {case_id} 不存在"})
    row, casebook_dir = found
    mjson_path = _resolve_casebook_path(casebook_dir, str(row.get("mjson_path", "")))
    if mjson_path is None:
        return JSONResponse(status_code=404, content={"error": f"case mjson 不存在: {row.get('mjson_path')}"})

    checkpoint = _checkpoint_for_behavior_case(row)
    player_id = int(row.get("actor", 0))
    focus_event_index = row.get("focus_event_index")
    focus_event_index = int(focus_event_index) if isinstance(focus_event_index, int) else None

    try:
        replay_id, _decisions, focus_replay_step, focus_resolution, _events = _import_case_mjson(
            row=row,
            mjson_path=mjson_path,
            checkpoint=checkpoint,
            player_id=player_id,
            focus_event_index=focus_event_index,
        )
        return JSONResponse(
            content={
                "replay_id": replay_id,
                "case": row,
                "player_id": player_id,
                "focus_event_index": focus_event_index,
                "focus_step": row.get("focus_step"),
                "focus_replay_step": focus_replay_step,
                "focus_resolution": focus_resolution,
                "game_board_url": (
                    f"/game-replay?id={replay_id}&player_id={player_id}"
                    f"&focus_event_index={focus_event_index}"
                    f"&focus_step={focus_replay_step if focus_replay_step is not None else ''}"
                    f"&focus_resolution={focus_resolution}"
                ),
            }
        )
    except Exception as e:
        import traceback

        return JSONResponse(status_code=500, content={"error": str(e), "detail": traceback.format_exc()})


@app.post("/api/behavior-casebook/import-paired/{case_id}/{side}", response_class=JSONResponse)
async def import_paired_behavior_case(case_id: str, side: str):
    """把 paired divergence case 的 left/right 侧导入为普通 replay。"""
    if side not in {"left", "right"}:
        return JSONResponse(status_code=400, content={"error": "side 必须是 left 或 right"})
    found = _find_paired_behavior_case(case_id)
    if found is None:
        return JSONResponse(status_code=404, content={"error": f"paired case {case_id} 不存在"})
    row, casebook_dir = found
    mjson_key = f"{side}_mjson_path"
    focus_key = f"{side}_focus_event_index"
    checkpoint_key = f"{side}_checkpoint_path"
    mjson_path = _resolve_casebook_path(casebook_dir, str(row.get(mjson_key, "")))
    if mjson_path is None:
        return JSONResponse(status_code=404, content={"error": f"paired case mjson 不存在: {row.get(mjson_key)}"})
    checkpoint = _checkpoint_from_raw(row.get(checkpoint_key))
    player_id = int(row.get("actor", 0))
    focus_event_index = row.get(focus_key)
    focus_event_index = int(focus_event_index) if isinstance(focus_event_index, int) else None
    case_payload = {**row, "paired_side": side}

    try:
        replay_id, _decisions, focus_replay_step, focus_resolution, _events = _import_case_mjson(
            row=case_payload,
            mjson_path=mjson_path,
            checkpoint=checkpoint,
            player_id=player_id,
            focus_event_index=focus_event_index,
        )
        return JSONResponse(
            content={
                "replay_id": replay_id,
                "case": row,
                "side": side,
                "player_id": player_id,
                "focus_event_index": focus_event_index,
                "focus_step": focus_replay_step,
                "focus_replay_step": focus_replay_step,
                "focus_resolution": focus_resolution,
                "game_board_url": (
                    f"/game-replay?id={replay_id}&player_id={player_id}"
                    f"&focus_event_index={focus_event_index}"
                    f"&focus_step={focus_replay_step if focus_replay_step is not None else ''}"
                    f"&focus_resolution={focus_resolution}"
                ),
            }
        )
    except Exception as e:
        import traceback

        return JSONResponse(status_code=500, content={"error": str(e), "detail": traceback.format_exc()})


@app.delete("/api/replay/{replay_id}", response_class=JSONResponse)
async def delete_replay(replay_id: str):
    """删除回放。"""
    storage = get_storage()
    deleted = storage.delete(replay_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": f"回放 {replay_id} 不存在"})
    return JSONResponse(content={"ok": True, "replay_id": replay_id})


# ========== 导出 & 健康检查 ==========

@app.post("/api/export-html")
async def export_html(data: str = Form("data")):
    """将 replay JSON 数据生成为独立离线 HTML 文件下载。"""
    try:
        replay_data = json.loads(data)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "无效的 JSON 数据"})

    template = (BASE_DIR / "templates" / "replay_view.html").read_text(encoding="utf-8")
    init_script = f'<script>window.__replayData = {json.dumps(replay_data, ensure_ascii=False)};</script>'
    html = template.replace('</body>', f'{init_script}</body>')

    pid = replay_data.get("player_id", 0)
    names = ["E", "S", "W", "N"]
    bakaze = ""
    if replay_data.get("kyoku_order"):
        first = replay_data["kyoku_order"][0]
        bakaze = first.get("bakaze", "E") if isinstance(first, dict) else "E"
    filename = f"replay_{bakaze}_{names[pid]}_{len(replay_data.get('log', []))}steps.html"

    from fastapi.responses import Response
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ========== 页面路由（React SPA）==========
# 注意：必须在所有 API 路由之后定义，避免 fallback 抢先匹配 /api/*

@app.get("/", response_class=HTMLResponse)
async def index():
    """主入口：React SPA（前端路由由 react-router-dom 处理）。"""
    return (_REACT_DIST / "index.html").read_text(encoding="utf-8")


@app.get("/replay", response_class=HTMLResponse)
async def replay_page():
    """兼容 /replay 路由（由 React Router 处理）。"""
    return (_REACT_DIST / "index.html").read_text(encoding="utf-8")


@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_fallback(path: str):
    """所有未匹配路由都 fallback 到 index.html，由 React Router 处理。"""
    return (_REACT_DIST / "index.html").read_text(encoding="utf-8")
