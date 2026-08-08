# -*- coding: utf-8 -*-
"""Teacher overlay 事件身份对齐测试（P1：replay_id + source_event_index + actor + decision_kind）。

覆盖：
- runtime teacher report 携带事件身份字段；
- _attach_teacher_report_overlays 按事件身份挂载，锁定 step 3 / 8 / 30；
- 跨 replay 报告（TA1）、player_id 不匹配（TA2）、身份一致（TA3）、
  文件名伪装但 JSON replay_id 不符（TA4）均正确拒绝/通过；
- 多匹配（ambiguous）、重复身份（duplicate_teacher_identity）、
  零匹配（unmatched）不被静默挂载；
- 旧报告（无身份字段）走 legacy fallback，mixed/legacy 语义正确。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from replay.server import _attach_teacher_report_overlays, _build_runtime_teacher_report

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "review_state"
DECISIONS_PATH = FIXTURE_DIR / "replay_97508ebc_p3" / "decisions.json"
EXPECTED_REPLAY_ID = "replay_97508ebc_1785858273"


def _load_decisions() -> dict:
    return json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))


def _write_report(tmp_path: Path, decisions: dict, model: str, replay_id: str = EXPECTED_REPLAY_ID) -> Path:
    report = _build_runtime_teacher_report(
        replay_id=replay_id,
        model_type=model,
        player_id=3,
        checkpoint=Path("/nonexistent/checkpoint.pth"),
        decisions=decisions,
    )
    path = tmp_path / f"{model.replace(' ', '_')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def _own_entry(decisions: dict, step: int) -> dict:
    return next(e for e in decisions["log"] if e.get("step") == step)


def _review_pai_set(review: dict) -> set:
    return {
        (c.get("action") or {}).get("pai")
        for c in review.get("candidates", [])
        if isinstance(c.get("action"), dict) and c["action"].get("pai")
    }


def test_runtime_report_entries_carry_event_identity(tmp_path):
    decisions = _load_decisions()
    report = json.loads(_write_report(tmp_path, decisions, "External Mortal").read_text(encoding="utf-8"))
    assert report.get("replay_id") == EXPECTED_REPLAY_ID
    entries = [
        entry
        for kyoku in report["review"]["kyokus"]
        for entry in kyoku.get("entries", [])
    ]
    assert len(entries) > 0
    for entry in entries:
        assert "source_event_index" in entry
        assert "actor" in entry
        assert "decision_kind" in entry
    step3 = next(e for e in entries if e.get("step") == 3)
    assert step3["decision_kind"] == "draw_discard"
    assert step3["actor"] == 3
    assert step3["source_event_index"] == 9


def test_attach_by_event_identity_locks_steps_3_8_30(tmp_path):
    decisions = _load_decisions()
    report_paths = [
        _write_report(tmp_path, decisions, "External Mortal"),
        _write_report(tmp_path, decisions, "70k"),
    ]
    attached = _attach_teacher_report_overlays(decisions, report_paths, expected_replay_id=EXPECTED_REPLAY_ID)
    overlays = attached["teacher_review_overlays"]
    assert len(overlays) == 2
    for overlay in overlays:
        assert overlay["alignment"] == "exact_event_identity"
        assert overlay["replay_verified"] is True
        assert overlay["alignment_stats"]["exact_event_identity"] > 0
        assert overlay["alignment_stats"]["ambiguous"] == 0
        assert overlay["alignment_stats"]["unmatched"] == 0
        assert overlay["alignment_stats"]["duplicate_teacher_identity"] == 0

    for step in (3, 8, 30):
        entry = _own_entry(attached, step)
        reviews = entry.get("teacher_reviews") or []
        assert len(reviews) == 2, f"step {step} 应挂上两份 teacher review（得到 {len(reviews)}）"
        models = {review["model"] for review in reviews}
        assert models == {"External Mortal", "70k"}, f"step {step} 模型集合应为两份"
        hand_set = set(entry.get("hand") or [])
        if entry.get("tsumo_pai"):
            hand_set.add(entry["tsumo_pai"])
        for review in reviews:
            actual = review.get("actual_action")
            gt = entry.get("gt_action")
            assert actual is not None and gt is not None
            assert actual.get("type") == gt.get("type") and actual.get("pai") == gt.get("pai"), (
                f"step {step} teacher actual {actual} 应与本地 gt_action {gt} 相同"
            )
            for pai in _review_pai_set(review):
                assert pai in hand_set, f"step {step} teacher 候选 {pai} 不属于当前 entry 手牌"


def test_ta1_cross_replay_report_rejected(tmp_path):
    """TA1：replay A 打开时挂 replay B 报告 → 0 条挂载 + replay_mismatch。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k", replay_id="replay_other_game")
    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment"] == "replay_mismatch"
    assert overlay["expected_replay_id"] == EXPECTED_REPLAY_ID
    assert overlay["actual_replay_id"] == "replay_other_game"
    assert overlay["attached_decision_count"] == 0
    step3 = _own_entry(attached, 3)
    assert not (step3.get("teacher_reviews") or []), "跨 replay 报告不得挂载任何 entry"


def test_ta2_player_id_mismatch_rejected(tmp_path):
    """TA2：replay_id 相同但 player_id 不同 → 拒绝。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["player_id"] = 2
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert "error" in overlay and "player_id" in overlay["error"]


def test_ta3_same_replay_player_identity_exact(tmp_path):
    """TA3：replay_id / player / 事件身份都一致 → exact。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment"] == "exact_event_identity"
    assert overlay["alignment_stats"]["exact_event_identity"] > 0


def test_ta4_filename_spoof_but_json_replay_id_mismatch(tmp_path):
    """TA4：文件名伪装成 replay A，但 JSON replay_id=B → 以 JSON 为准拒绝。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k", replay_id="replay_spoofed")
    # 伪装文件名：即使路径叫 replay_97508ebc_1785858273__70k__p3.json 也不予信任
    spoofed = tmp_path / f"{EXPECTED_REPLAY_ID}__70k__p3.json"
    report_path.rename(spoofed)
    attached = _attach_teacher_report_overlays(decisions, [spoofed], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment"] == "replay_mismatch"
    assert overlay["actual_replay_id"] == "replay_spoofed"


def test_identity_mismatch_is_not_silently_attached(tmp_path):
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    step3_teacher = None
    for kyoku in report["review"]["kyokus"]:
        for entry in kyoku["entries"]:
            if entry.get("step") == 3:
                step3_teacher = entry
    assert step3_teacher is not None
    step3_teacher["source_event_index"] = 3
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment_stats"]["unmatched"] >= 1
    step3_local = _own_entry(attached, 3)
    assert all(review["model"] != "70k" for review in (step3_local.get("teacher_reviews") or [])), (
        "错误身份不得挂载到 step 3"
    )


def test_ambiguous_identity_is_rejected(tmp_path):
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    duplicated = dict(decisions)
    log = list(decisions["log"])
    clone = dict(_own_entry(decisions, 3))
    clone["step"] = 9999
    log.append(clone)
    duplicated["log"] = log

    attached = _attach_teacher_report_overlays(duplicated, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment_stats"]["ambiguous"] >= 1, "多候选应标记 ambiguous"
    step3_local = _own_entry(attached, 3)
    assert all(review["model"] != "70k" for review in (step3_local.get("teacher_reviews") or [])), (
        "ambiguous 不得静默选择第一个挂载"
    )


def test_duplicate_teacher_identity_not_silently_dropped(tmp_path):
    """P3：报告内两条相同 identity → 第二条计入 duplicate_teacher_identity。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # 复制 step 3 的 teacher entry，制造报告内重复身份
    step3_teacher = None
    for kyoku in report["review"]["kyokus"]:
        for entry in kyoku["entries"]:
            if entry.get("step") == 3:
                step3_teacher = entry
    assert step3_teacher is not None
    report["review"]["kyokus"][0]["entries"].append(dict(step3_teacher))
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment_stats"]["duplicate_teacher_identity"] >= 1
    assert overlay["alignment"] == "mixed", "重复身份不应被顶层标成纯 exact"
    step3_local = _own_entry(attached, 3)
    reviews = [r for r in (step3_local.get("teacher_reviews") or []) if r["model"] == "70k"]
    assert len(reviews) == 1, "重复身份只挂载一条，不得重复附加"


def test_mixed_alignment_not_masked_by_exact(tmp_path):
    """P2-3：exact 与 legacy 并存 → 顶层 alignment = mixed。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entries = [entry for kyoku in report["review"]["kyokus"] for entry in kyoku["entries"]]
    # 去掉最后 2 条的身份字段 → 走 legacy
    for entry in entries[-2:]:
        entry.pop("source_event_index", None)
        entry.pop("actor", None)
        entry.pop("decision_kind", None)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment_stats"]["exact_event_identity"] > 0
    assert overlay["alignment_stats"]["legacy_step"] > 0
    assert overlay["alignment"] == "mixed"


def test_legacy_step_fallback_is_degraded(tmp_path):
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for kyoku in report["review"]["kyokus"]:
        for entry in kyoku["entries"]:
            entry.pop("source_event_index", None)
            entry.pop("actor", None)
            entry.pop("decision_kind", None)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment"] == "legacy_step"
    assert overlay["alignment_stats"]["legacy_step"] > 0
    assert overlay["alignment_stats"]["exact_event_identity"] == 0
    step8 = _own_entry(attached, 8)
    assert any(review["model"] == "70k" for review in (step8.get("teacher_reviews") or [])), (
        "旧报告应通过 step legacy fallback 挂载到 step 8"
    )


def test_v1_missing_top_level_replay_id_blocks_exact(tmp_path):
    """V1：删除报告顶层 replay_id、保留 entry identity → 不得 exact，只走 legacy。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "replay_id" in report
    del report["replay_id"]
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["replay_verified"] is False
    assert overlay["alignment_stats"]["exact_event_identity"] == 0
    assert overlay["alignment_stats"]["identity_carrying_entries"] > 0, "身份三元组仍应被统计"
    assert overlay["alignment_stats"]["legacy_step"] > 0, "缺 replay_id 时只能走 legacy"
    assert overlay["alignment"] != "exact_event_identity"


def test_v2_stripped_replay_id_cannot_bypass_cross_replay(tmp_path):
    """V2：replay B 报告删掉顶层 replay_id、保留会与 A 碰撞的身份 → 不得 exact 挂载。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k", replay_id="replay_other_game")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["replay_id"] == "replay_other_game"
    del report["replay_id"]
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["replay_verified"] is False
    assert overlay["alignment_stats"]["exact_event_identity"] == 0, "删掉 replay_id 不得通过身份 exact 挂载"
    assert overlay["alignment"] != "exact_event_identity"
    # legacy step 挂载是允许的降级路径；关键是"身份碰撞"不能成为 exact 依据
    step3 = _own_entry(attached, 3)
    if step3.get("teacher_reviews"):
        assert overlay["alignment_stats"]["legacy_step"] > 0, "若 step 3 被挂载，只能来自 legacy_step"


def test_v3_no_expected_replay_id_no_self_certification(tmp_path):
    """V3：expected_replay_id=None、报告自带 replay_id → 不得"自证"为 verified。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    attached = _attach_teacher_report_overlays(decisions, [report_path])  # 不传 expected_replay_id
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["replay_verified"] is False, "报告自身 replay_id 不构成验证"
    assert overlay["alignment_stats"]["exact_event_identity"] == 0
    assert overlay["alignment"] != "exact_event_identity"


def test_v4_duplicate_identity_yields_mixed(tmp_path):
    """V4：exact 条目 + duplicate identity → 顶层 alignment=mixed。"""
    decisions = _load_decisions()
    report_path = _write_report(tmp_path, decisions, "70k")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    step3_teacher = None
    for kyoku in report["review"]["kyokus"]:
        for entry in kyoku["entries"]:
            if entry.get("step") == 3:
                step3_teacher = entry
    assert step3_teacher is not None
    report["review"]["kyokus"][0]["entries"].append(dict(step3_teacher))
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    attached = _attach_teacher_report_overlays(decisions, [report_path], expected_replay_id=EXPECTED_REPLAY_ID)
    overlay = attached["teacher_review_overlays"][0]
    assert overlay["alignment_stats"]["exact_event_identity"] > 0
    assert overlay["alignment_stats"]["duplicate_teacher_identity"] >= 1
    assert overlay["alignment"] == "mixed", "存在重复身份时不得标为纯 exact"
