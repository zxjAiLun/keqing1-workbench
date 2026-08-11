# -*- coding: utf-8 -*-
"""R11-E1：Season Registry 生命周期 + 不变量（canonical runtime registry 写侧）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "workbench"))
sys.path.insert(0, str(ROOT / "src"))

from replay import season_registry as sr  # noqa: E402
from replay.ladder import SeasonNotFoundError, SeasonRegistryError  # noqa: E402


@pytest.fixture
def configs_dir(tmp_path):
    return tmp_path / "registries"


def _write_season(configs_dir: Path, season_id: str, **overrides):
    payload = {
        "schema": "keqing.ladder.season.v1",
        "season_id": season_id,
        "title": season_id,
        "status": "running",
        "default": False,
        "report_dir": str(configs_dir / "reports" / season_id),
        "scoring": {"system": "tenhou_rank_progression", "version": "v1"},
        "ingest": {"sources_root": "sources", "participants": {"enabled": True, "exclusive": True}},
        "models": [],
        **overrides,
    }
    (configs_dir / f"{season_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def test_create_season_is_draft_with_empty_enrollment(configs_dir):
    season = sr.create_season(configs_dir, season_id="season-x")
    assert season["status"] == "draft"
    assert season["default"] is False
    assert season["models"] == []
    assert season["schema"] == "keqing.ladder.season.v1"
    assert (configs_dir / "season-x.json").exists()


def test_create_season_rejects_duplicate_and_empty(configs_dir):
    sr.create_season(configs_dir, season_id="season-x")
    with pytest.raises(ValueError, match="赛季已存在"):
        sr.create_season(configs_dir, season_id="season-x")
    with pytest.raises(ValueError, match="season_id"):
        sr.create_season(configs_dir, season_id="  ")


def test_season_id_rejects_path_traversal_and_bad_chars(configs_dir):
    """R11-E1 Repair：season_id 必须落在 registry 目录内的普通 JSON 文件。"""
    for bad in ("../evil", "a/b", "a\\b", "a b", ".hidden", "..", "", "a.b"):
        with pytest.raises(ValueError, match="season_id"):
            sr.create_season(configs_dir, season_id=bad)
    # containment 兜底：_season_path 对非法 id 同样拒绝
    with pytest.raises(ValueError, match="season_id"):
        sr._season_path(configs_dir, "../evil")
    # 合法 id 仍可创建，且文件确实落在 registry 目录内
    season = sr.create_season(configs_dir, season_id="season-2026_08")
    assert (configs_dir / "season-2026_08.json").exists()


def test_concurrent_set_default_keeps_single_default(configs_dir):
    """R11-E1 Repair：并发 set_default 不产生双 default（registry mutation 锁）。"""
    import threading

    for sid in ("s1", "s2"):
        sr.create_season(configs_dir, season_id=sid)
        sr.set_season_status(configs_dir, sid, "running")

    results: list[Exception | None] = [None, None]

    def _set(idx: int, sid: str):
        try:
            sr.set_default_season(configs_dir, sid)
        except Exception as exc:  # noqa: BLE001
            results[idx] = exc

    threads = [
        threading.Thread(target=_set, args=(0, "s1")),
        threading.Thread(target=_set, args=(1, "s2")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r is None for r in results)
    defaults = [s for s in sr.list_season_configs(configs_dir) if s.get("default") is True]
    assert len(defaults) == 1


# ---------------------------------------------------------------------------
# lifecycle transitions
# ---------------------------------------------------------------------------

def test_status_transitions_are_forward_only(configs_dir):
    """R11-E Post-release Repair：completed 允许 reopen（completed→running）；
    archived 永久锁定；非法迁移（倒退/跳级）全部拒绝。"""
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_season_status(configs_dir, "s1", "completed")
    sr.set_season_status(configs_dir, "s1", "archived")
    assert sr.get_season(configs_dir, "s1")["status"] == "archived"
    # archived 永不 reopen
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s1", "running")

    _write_season(configs_dir, "s2")  # running
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s2", "draft")
    sr.set_season_status(configs_dir, "s2", "completed")
    with pytest.raises(SeasonRegistryError, match="状态迁移非法"):
        sr.set_season_status(configs_dir, "s2", "draft")
    # completed → running：reopen 合法
    sr.set_season_status(configs_dir, "s2", "running")
    assert sr.get_season(configs_dir, "s2")["status"] == "running"


def test_reopen_does_not_claim_current_and_audits(configs_dir):
    """R11-E Post-release Repair：reopen 后 default 保持 false（不自动抢占当前），
    且写入 reopened_at 审计字段。"""
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_season_status(configs_dir, "s1", "completed")
    assert sr.get_season(configs_dir, "s1")["default"] is False

    reopened = sr.set_season_status(configs_dir, "s1", "running")
    assert reopened["status"] == "running"
    assert reopened["default"] is False
    assert reopened.get("reopened_at")
    # 显式"设为当前"才抢占
    sr.set_default_season(configs_dir, "s1")
    assert sr.get_season(configs_dir, "s1")["default"] is True


# ---------------------------------------------------------------------------
# R11 post-release：删除赛季（服务级事务，防 TOCTOU）
# ---------------------------------------------------------------------------

def test_delete_season_running_and_current_refused(configs_dir):
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    with pytest.raises(SeasonRegistryError, match="running 赛季不可删除"):
        sr.delete_season(configs_dir, "s1")
    sr.set_default_season(configs_dir, "s1")
    with pytest.raises(SeasonRegistryError, match="当前正式赛季不可删除"):
        sr.delete_season(configs_dir, "s1")


def test_delete_after_reopen_refused(configs_dir):
    """TOCTOU 语义：删除前被 reopen 成 running 的赛季不可删（锁内重读）。"""
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_season_status(configs_dir, "s1", "completed")
    sr.set_season_status(configs_dir, "s1", "running")  # reopen
    with pytest.raises(SeasonRegistryError, match="running 赛季不可删除"):
        sr.delete_season(configs_dir, "s1")
    assert (configs_dir / "s1.json").exists()


def test_delete_refused_when_ledger_references(configs_dir, tmp_path, monkeypatch):
    """锁内重新 count：真实 Match Ledger 引用 → 拒绝硬删除（不产生 dangling Match）。"""
    from participants import ledger, registry
    from participants.schemas import AccountCreate, MatchCreate, MatchSeat

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))
    sr.create_season(configs_dir, season_id="s1")
    for i in range(4):
        registry.create_account(AccountCreate(account_id=f"p{i}", display_name=f"p{i}", account_type="human"))
    sr.set_season_enrollment(configs_dir, "s1", [f"p{i}" for i in range(4)], participants_registry=registry)
    sr.set_season_status(configs_dir, "s1", "running")
    ledger.create_match(
        MatchCreate(
            occurred_at="2026-08-11T12:00:00+08:00",
            game_length="hanchan",
            season_id="s1",
            rating_eligible=True,
            source="manual",
            seats=[MatchSeat(seat=i, account_id=f"p{i}", controller_type="human_ui") for i in range(4)],
            final_scores=[30000, 25000, 20000, 25000],
        ),
        registry,
    )
    sr.set_season_status(configs_dir, "s1", "completed")
    with pytest.raises(SeasonRegistryError, match="1 场历史对局引用"):
        sr.delete_season(configs_dir, "s1")
    assert (configs_dir / "s1.json").exists()


def test_delete_waits_for_ledger_lock(configs_dir, tmp_path, monkeypatch):
    """固定锁序：delete 必须取得 ledger data_lock 后才读/删——
    data_lock 被占时阻塞，释放后完成（与进行中的 intake 互斥）。"""
    import threading
    import time as _time

    from participants.paths import data_lock

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    sr.create_season(configs_dir, season_id="s1")
    results: list = []

    def _delete():
        try:
            results.append(sr.delete_season(configs_dir, "s1"))
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    with data_lock():
        t = threading.Thread(target=_delete)
        t.start()
        _time.sleep(0.4)
        assert results == [], "delete must block while ledger lock is held"
    t.join(timeout=10)
    assert len(results) == 1
    assert results[0]["deleted"] is True
    assert not (configs_dir / "s1.json").exists()


def test_delete_season_draft_without_reference_cleans_owned_state(configs_dir, tmp_path):
    sr.create_season(configs_dir, season_id="s1")
    assert (configs_dir / "s1.json").exists()
    result = sr.delete_season(configs_dir, "s1")
    assert result["deleted"] is True
    assert not (configs_dir / "s1.json").exists()


def test_delete_season_cleans_dirty_lock_and_owned_snapshots(configs_dir, tmp_path, monkeypatch):
    """删除时清理 Workbench 认领的派生数据（dirty marker / projection lock /
    ladder/seasons/<id> 快照目录）；不触碰任意 report_dir 指向的外部位置。"""
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_season_status(configs_dir, "s1", "completed")

    participants_root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(participants_root))
    from project_data import data_path as _data_path

    dirty = participants_root / "ladder_dirty_s1.json"
    lock = participants_root / "projection_locks" / "s1.lock"
    dirty.parent.mkdir(parents=True, exist_ok=True)
    lock.parent.mkdir(parents=True, exist_ok=True)
    dirty.write_text("{}", encoding="utf-8")
    lock.write_text("", encoding="utf-8")
    # Workbench 认领的快照目录 + 外部目录（模拟历史 report_dir 指向外部）
    owned = _data_path("ladder") / "seasons" / "s1"
    owned.mkdir(parents=True, exist_ok=True)
    (owned / "account_summary.json").write_text("{}", encoding="utf-8")
    external = tmp_path / "external-report"
    external.mkdir(parents=True)
    (external / "keep.txt").write_text("keep", encoding="utf-8")

    sr.delete_season(configs_dir, "s1", participants_data_root=participants_root)
    assert not (configs_dir / "s1.json").exists()
    assert not dirty.exists()
    assert not lock.exists()
    assert not owned.exists()
    assert (external / "keep.txt").exists()  # 外部目录绝不被删


def test_api_delete_season_endpoint(configs_dir, monkeypatch):
    """R11 post-release：DELETE /api/ladder/seasons/{id}（无引用可删 / 有引用 409）。"""
    from replay import server

    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    sr.create_season(configs_dir, season_id="s1")
    resp = _run(server.delete_ladder_season("s1"))
    assert resp.status_code == 200
    assert not (configs_dir / "s1.json").exists()
    resp = _run(server.delete_ladder_season("s1"))
    assert resp.status_code == 404


def test_complete_default_season_clears_default(configs_dir):
    sr.create_season(configs_dir, season_id="s1")
    sr.set_season_status(configs_dir, "s1", "running")
    sr.set_default_season(configs_dir, "s1")
    assert sr.get_season(configs_dir, "s1")["default"] is True
    sr.set_season_status(configs_dir, "s1", "completed")
    assert sr.get_season(configs_dir, "s1")["default"] is False


# ---------------------------------------------------------------------------
# single default invariant
# ---------------------------------------------------------------------------

def test_single_default_invariant(configs_dir):
    for sid in ("s1", "s2"):
        sr.create_season(configs_dir, season_id=sid)
        sr.set_season_status(configs_dir, sid, "running")
    sr.set_default_season(configs_dir, "s1")
    sr.set_default_season(configs_dir, "s2")
    # 只有一个 default（s2），s1 被自动摘除
    defaults = [s for s in sr.list_season_configs(configs_dir) if s.get("default") is True]
    assert [s["season_id"] for s in defaults] == ["s2"]
    assert sr.get_season(configs_dir, "s1")["default"] is False


def test_only_running_season_can_be_default(configs_dir):
    sr.create_season(configs_dir, season_id="draft-season")
    with pytest.raises(SeasonRegistryError, match="只有 running 赛季可以设为当前"):
        sr.set_default_season(configs_dir, "draft-season")


def test_missing_season_raises(configs_dir):
    with pytest.raises(SeasonNotFoundError):
        sr.get_season(configs_dir, "ghost")
    with pytest.raises(SeasonNotFoundError):
        sr.set_season_status(configs_dir, "ghost", "running")


# ---------------------------------------------------------------------------
# R11-E2：enrollment
# ---------------------------------------------------------------------------

@pytest.fixture
def participants_env(tmp_path, monkeypatch):
    """隔离的 Participants registry（KEQING_PARTICIPANT_DATA_ROOT）。"""
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    from participants import registry
    from participants.schemas import AccountCreate, ModelIdentityCreate

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:mortal-70k", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:mortal41b", label="Mortal 4.1b", kind="external_agent")
    )
    registry.create_account(AccountCreate(account_id="account:keqing1", display_name="keqing1", account_type="human"))
    registry.create_account(
        AccountCreate(account_id="account:70k_1号机", display_name="70k_1号机", account_type="managed_bot", model_identity_id="model:mortal-70k")
    )
    registry.create_account(
        AccountCreate(account_id="account:70k_2号机", display_name="70k_2号机", account_type="managed_bot", model_identity_id="model:mortal-70k")
    )
    registry.create_account(
        AccountCreate(account_id="account:mortal41b", display_name="mortal4.1b", account_type="external_bot", model_identity_id="model:mortal41b")
    )
    return registry


def _season_draft(configs_dir, season_id="v3"):
    sr.create_season(configs_dir, season_id=season_id)
    return season_id


def test_enrollment_groups_accounts_by_participants(participants_env, configs_dir):
    """R11-E2：参赛阵容从 Participants Account 分组——human→human，AI→身份组。"""
    sid = _season_draft(configs_dir)
    season = sr.set_season_enrollment(
        configs_dir,
        sid,
        ["account:keqing1", "account:70k_1号机", "account:70k_2号机", "account:mortal41b"],
        participants_registry=participants_env,
    )
    by_model = {m["model_id"]: {a["account_id"] for a in m["accounts"]} for m in season["models"]}
    assert by_model["human"] == {"account:keqing1"}
    assert by_model["model:mortal-70k"] == {"account:70k_1号机", "account:70k_2号机"}
    assert by_model["model:mortal41b"] == {"account:mortal41b"}
    # 新 AI group 冻结唯一 current artifact 为 checkpoint
    g70k = next(m for m in season["models"] if m["model_id"] == "model:mortal-70k")
    assert g70k["checkpoint"] == "ckpt.pth"
    assert g70k["model_identity_id"] == "model:mortal-70k"


def test_enrollment_rejects_unknown_and_duplicate_accounts(participants_env, configs_dir):
    sid = _season_draft(configs_dir)
    with pytest.raises(ValueError, match="账号不存在"):
        sr.set_season_enrollment(configs_dir, sid, ["account:ghost"], participants_registry=participants_env)
    with pytest.raises(ValueError, match="不能重复"):
        sr.set_season_enrollment(
            configs_dir, sid, ["account:keqing1", "account:keqing1"], participants_registry=participants_env
        )


def test_enrollment_rejects_ai_account_without_model_binding(participants_env, configs_dir):
    from participants.schemas import AccountCreate

    participants_env.create_account(AccountCreate(account_id="account:unbound", display_name="unbound", account_type="managed_bot"))
    sid = _season_draft(configs_dir)
    with pytest.raises(ValueError, match="未绑定模型身份"):
        sr.set_season_enrollment(configs_dir, sid, ["account:unbound"], participants_registry=participants_env)


def test_enrollment_running_is_add_only(participants_env, configs_dir):
    sid = _season_draft(configs_dir)
    sr.set_season_status(configs_dir, sid, "running")
    sr.set_season_enrollment(configs_dir, sid, ["account:keqing1"], participants_registry=participants_env)
    # 增加新账号 → 允许
    sr.set_season_enrollment(
        configs_dir, sid, ["account:keqing1", "account:70k_1号机"], participants_registry=participants_env
    )
    # 移除现有成员 → 拒绝
    with pytest.raises(SeasonRegistryError, match="只能增加账号"):
        sr.set_season_enrollment(configs_dir, sid, ["account:keqing1"], participants_registry=participants_env)


def test_enrollment_completed_is_read_only(participants_env, configs_dir):
    sid = _season_draft(configs_dir)
    sr.set_season_status(configs_dir, sid, "running")
    sr.set_season_status(configs_dir, sid, "completed")
    with pytest.raises(SeasonRegistryError, match="不可修改参赛阵容"):
        sr.set_season_enrollment(configs_dir, sid, ["account:keqing1"], participants_registry=participants_env)


def test_enrollment_legacy_group_backfill_unique_consistent(participants_env, configs_dir):
    """R11-E2：legacy group（无 model_identity_id）成员推导唯一一致时自动 backfill。"""
    from pathlib import Path as _P

    import json as _json

    sid = "legacy"
    sr.create_season(configs_dir, season_id=sid)
    path = configs_dir / "legacy.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["models"] = [
        {
            "model_id": "70k",
            "checkpoint": "artifacts/old.pth",
            "accounts": [{"account_id": "account:70k_1号机"}],
        }
    ]
    path.write_text(_json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    season = sr.set_season_enrollment(
        configs_dir, sid, ["account:70k_1号机", "account:70k_2号机"], participants_registry=participants_env
    )
    g70k = next(m for m in season["models"] if m["model_id"] == "70k")
    assert g70k["model_identity_id"] == "model:mortal-70k"
    assert g70k["checkpoint"] == "artifacts/old.pth"  # 已有 checkpoint 不漂移
    assert {a["account_id"] for a in g70k["accounts"]} == {"account:70k_1号机", "account:70k_2号机"}


def test_enrollment_legacy_group_ambiguous_backfill_fails_closed(participants_env, configs_dir):
    """R11-E2：legacy group 内推导出多个 identity → fail closed，绝不猜。"""
    import json as _json

    sid = "ambig"
    sr.create_season(configs_dir, season_id=sid)
    path = configs_dir / "ambig.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["models"] = [
        {
            "model_id": "mixed",
            "accounts": [
                {"account_id": "account:70k_1号机"},
                {"account_id": "account:mortal41b"},
            ],
        }
    ]
    path.write_text(_json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="推导出多个模型身份"):
        sr.set_season_enrollment(
            configs_dir, sid, ["account:70k_1号机", "account:mortal41b"], participants_registry=participants_env
        )


def test_api_enrollment_endpoint(participants_env, configs_dir, monkeypatch):
    """R11-E2：PUT /enrollment 端点（draft 可改；completed → 409）。"""
    from replay import server

    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", configs_dir)
    sid = _season_draft(configs_dir)
    resp = _run(server.set_ladder_season_enrollment(sid, {"account_ids": ["account:keqing1"]}))
    assert resp.status_code == 200
    body = _body(resp)
    assert body["models"][0]["model_id"] == "human"
    # 未知账号 → 409
    resp = _run(server.set_ladder_season_enrollment(sid, {"account_ids": ["account:ghost"]}))
    assert resp.status_code == 409
    # 不存在的赛季 → 404
    resp = _run(server.set_ladder_season_enrollment("ghost", {"account_ids": []}))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# R11-E2 Repair：draft 可删 / running 换组 gate / 写前验证
# ---------------------------------------------------------------------------

def _all_accounts(configs_dir, sid) -> set[str]:
    season = sr.get_season(configs_dir, sid)
    return {a["account_id"] for m in season["models"] for a in m["accounts"]}


def test_draft_enrollment_can_remove_members(participants_env, configs_dir):
    """R11-E2 Repair：draft [A,B] → [A] 时 B 必须从赛季 JSON 消失。"""
    sid = _season_draft(configs_dir)
    sr.set_season_enrollment(
        configs_dir, sid,
        ["account:keqing1", "account:70k_1号机"],
        participants_registry=participants_env,
    )
    assert _all_accounts(configs_dir, sid) == {"account:keqing1", "account:70k_1号机"}

    sr.set_season_enrollment(configs_dir, sid, ["account:keqing1"], participants_registry=participants_env)
    assert _all_accounts(configs_dir, sid) == {"account:keqing1"}


def test_running_enrollment_add_only(participants_env, configs_dir):
    """R11-E2 Repair：running [A] → [A,B] 成功。"""
    sid = _season_draft(configs_dir)
    sr.set_season_status(configs_dir, sid, "running")
    sr.set_season_enrollment(configs_dir, sid, ["account:keqing1"], participants_registry=participants_env)
    sr.set_season_enrollment(
        configs_dir, sid, ["account:keqing1", "account:70k_1号机"], participants_registry=participants_env
    )
    assert _all_accounts(configs_dir, sid) == {"account:keqing1", "account:70k_1号机"}


def test_running_rejects_regroup_on_binding_drift(participants_env, configs_dir):
    """R11-E2 Repair：running 中 Account 绑定漂移 → 409 且 season 文件逐字节不变。"""
    from participants.schemas import ModelIdentityCreate

    sid = _season_draft(configs_dir)
    sr.set_season_status(configs_dir, sid, "running")
    sr.set_season_enrollment(configs_dir, sid, ["account:70k_1号机"], participants_registry=participants_env)
    before = (configs_dir / f"{sid}.json").read_bytes()

    # Participants 把该 Account 的驱动模型改成新 identity
    participants_env.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:new-70k", label="new70k", kind="local_model", artifact_path="new.pth")
    )
    from participants.schemas import AccountUpdate

    participants_env.update_account("account:70k_1号机", AccountUpdate(model_identity_id="model:new-70k"))

    with pytest.raises(SeasonRegistryError, match="不允许换组"):
        sr.set_season_enrollment(configs_dir, sid, ["account:70k_1号机"], participants_registry=participants_env)
    assert (configs_dir / f"{sid}.json").read_bytes() == before


def test_draft_regroups_member_into_new_identity_group_once(participants_env, configs_dir):
    """R11-E2 Repair：draft 中绑定漂移 → 重新保存后成员只在新 group 出现一次。"""
    from participants.schemas import AccountUpdate, ModelIdentityCreate

    sid = _season_draft(configs_dir)
    sr.set_season_enrollment(configs_dir, sid, ["account:70k_1号机"], participants_registry=participants_env)
    participants_env.create_model_identity(
        ModelIdentityCreate(model_identity_id="model:new-70k", label="new70k", kind="local_model", artifact_path="new.pth")
    )
    participants_env.update_account("account:70k_1号机", AccountUpdate(model_identity_id="model:new-70k"))

    sr.set_season_enrollment(configs_dir, sid, ["account:70k_1号机"], participants_registry=participants_env)
    season = sr.get_season(configs_dir, sid)
    memberships = [
        (m["model_id"], a["account_id"])
        for m in season["models"]
        for a in m["accounts"]
    ]
    assert memberships == [("model:new-70k", "account:70k_1号机")]


def test_enrollment_members_freeze_display_name(participants_env, configs_dir):
    """R11-E2 P2：enrollment 成员冻结 display_name（历史格式）。"""
    sid = _season_draft(configs_dir)
    season = sr.set_season_enrollment(
        configs_dir, sid, ["account:70k_1号机", "account:keqing1"], participants_registry=participants_env
    )
    members = {a["account_id"]: a.get("display_name") for m in season["models"] for a in m["accounts"]}
    assert members["account:70k_1号机"] == "70k_1号机"
    assert members["account:keqing1"] == "keqing1"


# ---------------------------------------------------------------------------
# API-level（server 端点）
# ---------------------------------------------------------------------------

def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _body(response) -> dict:
    import json as _json

    return _json.loads(response.body.decode("utf-8"))


@pytest.fixture
def manager_server(tmp_path, monkeypatch):
    from replay import server

    monkeypatch.setattr(server, "_LADDER_SEASONS_DIR", tmp_path / "registries")
    return server


def test_api_season_lifecycle(manager_server):
    server = manager_server
    # 创建（draft, default=False, 空阵容）
    resp = _run(server.create_ladder_season({"season_id": "s1", "title": "Season One"}))
    assert resp.status_code == 200
    season = _body(resp)
    assert season["status"] == "draft"
    assert season["default"] is False
    # 开始 → running
    assert _body(_run(server.start_ladder_season("s1")))["status"] == "running"
    # 设为当前
    assert _body(_run(server.set_default_ladder_season("s1")))["default"] is True
    # 结束 → completed，且自动摘除 default
    done = _body(_run(server.complete_ladder_season("s1")))
    assert done["status"] == "completed"
    assert done["default"] is False


def test_api_season_lifecycle_conflicts_are_409(manager_server):
    server = manager_server
    _run(server.create_ladder_season({"season_id": "s1"}))
    # draft 不能设为当前 → 409
    resp = _run(server.set_default_ladder_season("s1"))
    assert resp.status_code == 409
    assert "只有 running" in _body(resp)["error"]
    # 非法迁移（draft → completed）→ 409
    resp = _run(server.complete_ladder_season("s1"))
    assert resp.status_code == 409
    assert "状态迁移非法" in _body(resp)["error"]
    # 重复创建 → 409
    resp = _run(server.create_ladder_season({"season_id": "s1"}))
    assert resp.status_code == 409
    assert "赛季已存在" in _body(resp)["error"]
    # 未找到 → 404
    resp = _run(server.start_ladder_season("ghost"))
    assert resp.status_code == 404
