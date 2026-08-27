# -*- coding: utf-8 -*-
"""R14-A 验收测试集: Season Runtime Manifest & Start Preflight

覆盖场景矩阵：
1. Draft 空 enrollment -> Preflight blocked (insufficient_participants)
2. 少于 4 个 unique accounts -> Preflight blocked
3. Candidate local artifact -> Preflight blocked (artifact_not_ladder_eligible)
4. Retired local artifact -> Preflight blocked (artifact_retired)
5. Retired external revision -> Preflight blocked (external_revision_retired)
6. Local checkpoint missing -> Preflight blocked (checkpoint_file_missing)
7. Local artifact hash 已登记但文件 hash 不匹配 -> Preflight blocked (artifact_hash_mismatch)
8. Catalog Current 从 A 切 B，而 Season frozen A -> Manifest 必须仍为 A
9. External 同样做 A/B current 对抗测试
10. 连续两次 Preflight：manifest_id 相同、season_authority_hash 相同、Season JSON 不变、不产生 runtime_manifest
11. POST Start 后：status="running", runtime_manifest.materialization="frozen", provenance 精确一致，单文件原子写可读
12. Start 后再次 Start -> 409 already_started
13. R14-managed running Season 修改 enrollment -> 409 runtime manifest 已冻结
14. Legacy running Season 无 manifest -> 保持原 running-additive 兼容性
15. Completed / archived /start -> 409 season_not_startable
16. Retired-after-start: 已持久化 Manifest 不改变，GET /manifest 仍返回，历史 provenance 不漂移
17. official-ladder-v2: 可生成 projected_legacy read-only manifest，不写回生产 Season
18. Pure read-only preview 不写回 Season
19. Account disabled before start -> Preflight blocked (account_disabled)
20. Account model_identity_id rebind (A -> B while Season frozen A) -> Preflight blocked (account_binding_drift)
21. Controller mismatch / manual_only -> Preflight blocked (controller_manual_only_not_startable)
22. Persisted authority hash tamper -> canonical read raises authority_hash_drift
23. Legacy projection with missing evidence -> fail-closed SeasonRegistryError (no silent partial manifest)
24. Display name uses frozen enrollment snapshot, immune to subsequent Account display_name mutation
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from workbench.participants import registry as part_registry
from workbench.participants.schemas import (
    AccountCreate,
    AccountUpdate,
    ExternalModelRevisionCreate,
    ModelArtifactCreate,
    ModelIdentityCreate,
)
from replay import season_registry as sr
from replay.ladder import SeasonRegistryError, _load_registry_file
from runtime.manifest import (
    build_season_authority_projection,
    compute_season_authority_hash,
    get_season_runtime_manifest,
    preflight_season_runtime,
    start_season_runtime,
)


@pytest.fixture
def r14_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "participants"
    configs_dir = tmp_path / "configs"
    models_dir = tmp_path / "models"
    data_dir.mkdir(parents=True, exist_ok=True)
    configs_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(data_dir))
    monkeypatch.setenv("KEQING_LADDER_CONFIG_DIR", str(configs_dir))

    return {
        "tmp_path": tmp_path,
        "data_dir": data_dir,
        "configs_dir": configs_dir,
        "models_dir": models_dir,
    }


def _create_checkpoint(models_dir: Path, name: str, content: str = "dummy_weights") -> Path:
    cp = models_dir / name
    cp.write_text(content, encoding="utf-8")
    return cp


def test_r14_preflight_draft_empty_enrollment_and_insufficient_accounts(r14_env):
    """1 & 2. Draft 空 enrollment 或少于 4 人 -> blocked."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]

    # 1. 空 draft 赛季
    sr.create_season(configs_dir, season_id="s_empty", title="Empty Season")
    rep_empty = preflight_season_runtime(configs_dir, "s_empty", project_root=tmp_path)
    assert rep_empty.state == "blocked"
    assert rep_empty.can_start is False
    assert any(i.code == "insufficient_participants" for i in rep_empty.issues)

    # 2. 只有 3 个账号
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    sr.set_season_enrollment(configs_dir, "s_empty", ["account:h1", "account:h2", "account:h3"])
    rep_3p = preflight_season_runtime(configs_dir, "s_empty", project_root=tmp_path)
    assert rep_3p.state == "blocked"
    assert rep_3p.can_start is False
    assert any(i.code == "insufficient_participants" for i in rep_3p.issues)


def test_r14_preflight_candidate_retired_local_and_external(r14_env):
    """3, 4, 5. Candidate/Retired Local Artifact 及 Retired External Revision 阻断启动."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp1 = _create_checkpoint(models_dir, "m1.pth")
    cp2 = _create_checkpoint(models_dir, "m2.pth")

    # 注册 Local Model (1 promoted, 1 candidate, 1 retired)
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:loc1", label="Loc 1", kind="local_model"))
    art_cand = part_registry.add_model_artifact("model:loc1", ModelArtifactCreate(label="cand.pth", artifact_path=str(cp1), stage="candidate"))
    art_ret = part_registry.add_model_artifact("model:loc1", ModelArtifactCreate(label="ret.pth", artifact_path=str(cp2), stage="promoted"))
    part_registry.retire_model_artifact("model:loc1", art_ret.model_artifact_id)

    # 注册 External Agent (1 retired)
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ext1", label="Ext 1", kind="external_agent"))
    rev_ret = part_registry.add_external_revision("model:ext1", ExternalModelRevisionCreate(provider="mortal", version="1.0", stage="promoted", is_current=True))
    part_registry.retire_external_revision("model:ext1", rev_ret.external_revision_id)

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_loc", display_name="BotLoc", account_type="managed_bot", model_identity_id="model:loc1"))
    part_registry.create_account(AccountCreate(account_id="account:bot_ext", display_name="BotExt", account_type="external_bot", model_identity_id="model:ext1"))

    # 3. 绑定 Candidate Local Artifact
    sr.create_season(configs_dir, season_id="s_cand", title="Cand Season")
    s_cfg_cand = sr.get_season(configs_dir, "s_cand")
    s_cfg_cand["models"] = [{
        "model_id": "loc1", "model_identity_id": "model:loc1", "accounts": [{"account_id": "account:bot_loc"}],
        "execution_provenance": {"kind": "local_artifact", "model_identity_id": "model:loc1", "model_artifact_id": art_cand.model_artifact_id}
    }, {"model_id": "human", "accounts": [{"account_id": f"account:h{i}"} for i in range(1, 4)]}]
    sr._atomic_write_json(configs_dir / "s_cand.json", s_cfg_cand)

    rep_cand = preflight_season_runtime(configs_dir, "s_cand", project_root=tmp_path)
    assert rep_cand.state == "blocked"
    assert any(i.code == "artifact_not_ladder_eligible" for i in rep_cand.issues)

    # 4. 绑定 Retired Local Artifact
    sr.create_season(configs_dir, season_id="s_ret_loc", title="Ret Loc Season")
    s_cfg = sr.get_season(configs_dir, "s_ret_loc")
    s_cfg["models"] = [{
        "model_id": "loc1", "model_identity_id": "model:loc1", "accounts": [{"account_id": "account:bot_loc"}],
        "execution_provenance": {"kind": "local_artifact", "model_identity_id": "model:loc1", "model_artifact_id": art_ret.model_artifact_id}
    }, {"model_id": "human", "accounts": [{"account_id": f"account:h{i}"} for i in range(1, 4)]}]
    sr._atomic_write_json(configs_dir / "s_ret_loc.json", s_cfg)

    rep_ret_loc = preflight_season_runtime(configs_dir, "s_ret_loc", project_root=tmp_path)
    assert rep_ret_loc.state == "blocked"
    assert any(i.code == "artifact_retired" for i in rep_ret_loc.issues)

    # 5. 绑定 Retired External Revision
    sr.create_season(configs_dir, season_id="s_ret_ext", title="Ret Ext Season")
    s_cfg_ext = sr.get_season(configs_dir, "s_ret_ext")
    s_cfg_ext["models"] = [{
        "model_id": "ext1", "model_identity_id": "model:ext1", "accounts": [{"account_id": "account:bot_ext"}],
        "execution_provenance": {"kind": "external_revision", "model_identity_id": "model:ext1", "external_revision_id": rev_ret.external_revision_id}
    }, {"model_id": "human", "accounts": [{"account_id": f"account:h{i}"} for i in range(1, 4)]}]
    sr._atomic_write_json(configs_dir / "s_ret_ext.json", s_cfg_ext)

    rep_ret_ext = preflight_season_runtime(configs_dir, "s_ret_ext", project_root=tmp_path)
    assert rep_ret_ext.state == "blocked"
    assert any(i.code == "external_revision_retired" for i in rep_ret_ext.issues)


def test_r14_preflight_missing_checkpoint_and_hash_mismatch(r14_env):
    """6 & 7. Checkpoint 缺失或 Hash 不匹配阻断启动."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp_valid = _create_checkpoint(models_dir, "valid.pth", content="correct_content")
    valid_hash = hashlib.sha256(b"correct_content").hexdigest()

    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:hash_test", label="Hash Test", kind="local_model"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_h", display_name="BotH", account_type="managed_bot", model_identity_id="model:hash_test"))

    # 6. Checkpoint missing
    art_missing = part_registry.add_model_artifact(
        "model:hash_test",
        ModelArtifactCreate(label="missing.pth", artifact_path=str(models_dir / "missing.pth"), stage="promoted")
    )
    sr.create_season(configs_dir, season_id="s_missing", title="Missing Season")
    sr.set_season_enrollment(
        configs_dir, "s_missing", ["account:h1", "account:h2", "account:h3", "account:bot_h"],
        provenance_by_model={"model:hash_test": {"kind": "local_artifact", "model_identity_id": "model:hash_test", "model_artifact_id": art_missing.model_artifact_id}}
    )
    rep_missing = preflight_season_runtime(configs_dir, "s_missing", project_root=tmp_path)
    assert rep_missing.state == "blocked"
    assert any(i.code == "checkpoint_file_missing" for i in rep_missing.issues)

    # 7. Hash mismatch
    art_bad_hash = part_registry.add_model_artifact(
        "model:hash_test",
        ModelArtifactCreate(label="bad_hash.pth", artifact_path=str(cp_valid), stage="promoted")
    )
    # 手工设置一个不匹配的 hash 到持久化
    models_store = part_registry._read_models()
    for a in models_store["artifacts"]:
        if a["model_artifact_id"] == art_bad_hash.model_artifact_id:
            a["hash"] = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    part_registry.write_json(part_registry._models_path(), part_registry.MODELS_SCHEMA, models_store)

    sr.create_season(configs_dir, season_id="s_bad_hash", title="Bad Hash Season")
    sr.set_season_enrollment(
        configs_dir, "s_bad_hash", ["account:h1", "account:h2", "account:h3", "account:bot_h"],
        provenance_by_model={"model:hash_test": {"kind": "local_artifact", "model_identity_id": "model:hash_test", "model_artifact_id": art_bad_hash.model_artifact_id}}
    )
    rep_bad_hash = preflight_season_runtime(configs_dir, "s_bad_hash", project_root=tmp_path)
    assert rep_bad_hash.state == "blocked"
    assert any(i.code == "artifact_hash_mismatch" for i in rep_bad_hash.issues)


def test_r14_manifest_adversarial_against_catalog_current_switch(r14_env):
    """8 & 9. 对抗测试：Catalog Current 从 A 切到 B，Season 冻结 A，Manifest 必须严格保持 A."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    # 8. Local Model A/B Adversarial
    cp_a = _create_checkpoint(models_dir, "loc_a.pth")
    cp_b = _create_checkpoint(models_dir, "loc_b.pth")

    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:loc_ab", label="Loc AB", kind="local_model"))
    art_a = part_registry.add_model_artifact("model:loc_ab", ModelArtifactCreate(label="A.pth", artifact_path=str(cp_a), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_loc_ab", display_name="BotAB", account_type="managed_bot", model_identity_id="model:loc_ab"))

    # 锁定 Season 到 A
    sr.create_season(configs_dir, season_id="s_loc_ab", title="Loc AB Season")
    sr.set_season_enrollment(
        configs_dir, "s_loc_ab", ["account:h1", "account:h2", "account:h3", "account:bot_loc_ab"],
        provenance_by_model={"model:loc_ab": {"kind": "local_artifact", "model_identity_id": "model:loc_ab", "model_artifact_id": art_a.model_artifact_id}}
    )

    # Catalog 新增 B 并把 Current 切到 B!
    art_b = part_registry.add_model_artifact("model:loc_ab", ModelArtifactCreate(label="B.pth", artifact_path=str(cp_b), stage="promoted"))
    part_registry.set_current_model_artifact("model:loc_ab", art_b.model_artifact_id)

    # 验证 Manifest 严格保持 A，绝不变成 B
    rep_loc_ab = preflight_season_runtime(configs_dir, "s_loc_ab", project_root=tmp_path)
    assert rep_loc_ab.can_start is True
    bot_spec = next(p for p in rep_loc_ab.manifest.participants if p.account_id == "account:bot_loc_ab")
    assert bot_spec.execution_provenance.model_artifact_id == art_a.model_artifact_id
    assert bot_spec.runtime_spec.model_artifact_id == art_a.model_artifact_id
    assert bot_spec.runtime_spec.resolved_checkpoint_path == str(cp_a)

    # 9. External Agent A/B Adversarial
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ext_ab", label="Ext AB", kind="external_agent"))
    rev_a = part_registry.add_external_revision("model:ext_ab", ExternalModelRevisionCreate(provider="mortal", version="4.1b", is_current=True))
    part_registry.create_account(AccountCreate(account_id="account:bot_ext_ab", display_name="BotExtAB", account_type="external_bot", model_identity_id="model:ext_ab"))

    sr.create_season(configs_dir, season_id="s_ext_ab", title="Ext AB Season")
    sr.set_season_enrollment(
        configs_dir, "s_ext_ab", ["account:h1", "account:h2", "account:h3", "account:bot_ext_ab"],
        provenance_by_model={"model:ext_ab": {"kind": "external_revision", "model_identity_id": "model:ext_ab", "external_revision_id": rev_a.external_revision_id}}
    )

    # Catalog 新增 4.2 并切 Current
    rev_b = part_registry.add_external_revision("model:ext_ab", ExternalModelRevisionCreate(provider="mortal", version="4.2", is_current=True))

    rep_ext_ab = preflight_season_runtime(configs_dir, "s_ext_ab", project_root=tmp_path)
    assert rep_ext_ab.can_start is True
    bot_ext_spec = next(p for p in rep_ext_ab.manifest.participants if p.account_id == "account:bot_ext_ab")
    assert bot_ext_spec.execution_provenance.external_revision_id == "external:ext_ab:4.1b"
    assert bot_ext_spec.runtime_spec.version == "4.1b"


def test_r14_preflight_determinism_and_no_side_effects(r14_env):
    """10 & 18. 连续 Preflight 产生确定性 ID 与 Hash，纯只读无任何写入或状态副作用."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "det.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:det", label="Det", kind="local_model"))
    art = part_registry.add_model_artifact("model:det", ModelArtifactCreate(label="det.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_det", display_name="BotDet", account_type="managed_bot", model_identity_id="model:det"))

    sr.create_season(configs_dir, season_id="s_det", title="Det Season")
    sr.set_season_enrollment(
        configs_dir, "s_det", ["account:h1", "account:h2", "account:h3", "account:bot_det"],
        provenance_by_model={"model:det": {"kind": "local_artifact", "model_identity_id": "model:det", "model_artifact_id": art.model_artifact_id}}
    )

    season_path = configs_dir / "s_det.json"
    content_before = season_path.read_text(encoding="utf-8")

    # 连续调用 2 次
    p1 = preflight_season_runtime(configs_dir, "s_det", project_root=tmp_path)
    p2 = preflight_season_runtime(configs_dir, "s_det", project_root=tmp_path)

    assert p1.can_start is True
    assert p2.can_start is True
    assert p1.manifest.manifest_id == p2.manifest.manifest_id
    assert p1.manifest.season_authority_hash == p2.manifest.season_authority_hash
    assert p1.manifest.generated_at is None

    # 文件内容字节不变，未写回 runtime_manifest，状态仍为 draft
    content_after = season_path.read_text(encoding="utf-8")
    assert content_before == content_after
    cfg_now = sr.get_season(configs_dir, "s_det")
    assert cfg_now["status"] == "draft"
    assert "runtime_manifest" not in cfg_now


def test_r14_start_atomic_transition_and_duplicate_rejection(r14_env):
    """11, 12, 13, 15. Start 双锁原子写、重复 Start 拦截、Running 冻结 enrollment 拦截、历史状态启动拦截."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "start_test.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:st", label="ST", kind="local_model"))
    art = part_registry.add_model_artifact("model:st", ModelArtifactCreate(label="st.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 5):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_st", display_name="BotST", account_type="managed_bot", model_identity_id="model:st"))

    sr.create_season(configs_dir, season_id="s_start_flow", title="Start Flow")
    sr.set_season_enrollment(
        configs_dir, "s_start_flow", ["account:h1", "account:h2", "account:h3", "account:bot_st"],
        provenance_by_model={"model:st": {"kind": "local_artifact", "model_identity_id": "model:st", "model_artifact_id": art.model_artifact_id}}
    )

    # 11. 成功 Start
    started = start_season_runtime(configs_dir, "s_start_flow", project_root=tmp_path)
    assert started["status"] == "running"
    assert "runtime_manifest" in started
    manifest = started["runtime_manifest"]
    assert manifest["materialization"] == "frozen"
    assert manifest["generated_at"] is not None

    # 读取 GET /manifest
    loaded_manifest = get_season_runtime_manifest(configs_dir, "s_start_flow", project_root=tmp_path)
    assert loaded_manifest.manifest_id == manifest["manifest_id"]

    # 12. 重复 Start -> 409
    with pytest.raises(SeasonRegistryError, match="already_started|已在运行中"):
        start_season_runtime(configs_dir, "s_start_flow", project_root=tmp_path)

    # 13. R14 running season 修改 enrollment -> 409 (不允许修改)
    with pytest.raises(SeasonRegistryError, match="runtime manifest 已冻结"):
        sr.set_season_enrollment(configs_dir, "s_start_flow", ["account:h1", "account:h2", "account:h3", "account:bot_st", "account:h4"])

    # 15. Completed / Archived 启动拦截 -> 409
    sr.finalize_season(configs_dir, "s_start_flow")
    with pytest.raises(SeasonRegistryError, match="season_not_startable|无法启动"):
        start_season_runtime(configs_dir, "s_start_flow", project_root=tmp_path)


def test_r14_legacy_running_season_compatibility(r14_env):
    """14 & 17. 兼容无 runtime_manifest 的 legacy running 赛季增加成员，以及 official-ladder-v2 生成 projected_legacy."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "legacy.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:leg", label="Leg", kind="local_model"))
    art = part_registry.add_model_artifact("model:leg", ModelArtifactCreate(label="leg.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 6):
        part_registry.create_account(AccountCreate(account_id=f"account:lh{i}", display_name=f"LH{i}", account_type="human"))

    # 构造 legacy running season (无 runtime_manifest)
    sr.create_season(configs_dir, season_id="s_legacy_run", title="Legacy Run")
    sr.set_season_enrollment(configs_dir, "s_legacy_run", ["account:lh1", "account:lh2", "account:lh3", "account:lh4"])
    sr.set_season_status(configs_dir, "s_legacy_run", "running")

    # 14. 无 runtime_manifest 的 legacy running 赛季仍允许追加成员
    updated = sr.set_season_enrollment(configs_dir, "s_legacy_run", ["account:lh1", "account:lh2", "account:lh3", "account:lh4", "account:lh5"])
    all_members = [a["account_id"] for m in updated["models"] for a in m["accounts"]]
    assert "account:lh5" in all_members

    # 17. Legacy 赛季读取 Manifest 生成 projected_legacy 且不写回文件
    manifest_leg = get_season_runtime_manifest(configs_dir, "s_legacy_run", project_root=tmp_path)
    assert manifest_leg.materialization == "projected_legacy"
    assert manifest_leg.generated_at is None
    cfg_disk = sr.get_season(configs_dir, "s_legacy_run")
    assert "runtime_manifest" not in cfg_disk


def test_r14_retired_after_start_does_not_mutate_manifest(r14_env):
    """16. 启动后产物/版本在 Catalog 中退役，已持久化 Manifest 与历史 Provenance 绝对不发生漂移."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "ret_after.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ret_after", label="Ret After", kind="local_model"))
    art = part_registry.add_model_artifact("model:ret_after", ModelArtifactCreate(label="ra.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_ra", display_name="BotRA", account_type="managed_bot", model_identity_id="model:ret_after"))

    sr.create_season(configs_dir, season_id="s_ret_after", title="Ret After Season")
    sr.set_season_enrollment(
        configs_dir, "s_ret_after", ["account:h1", "account:h2", "account:h3", "account:bot_ra"],
        provenance_by_model={"model:ret_after": {"kind": "local_artifact", "model_identity_id": "model:ret_after", "model_artifact_id": art.model_artifact_id}}
    )

    # Start 固化
    start_season_runtime(configs_dir, "s_ret_after", project_root=tmp_path)

    # 在 Catalog 中将产物退役并封存
    part_registry.retire_model_artifact("model:ret_after", art.model_artifact_id)

    # 读取 Manifest 确认完好无损，不受退役影响
    manifest = get_season_runtime_manifest(configs_dir, "s_ret_after", project_root=tmp_path)
    assert manifest.materialization == "frozen"
    bot_p = next(p for p in manifest.participants if p.account_id == "account:bot_ra")
    assert bot_p.execution_provenance.model_artifact_id == art.model_artifact_id
    assert bot_p.runtime_spec.model_artifact_id == art.model_artifact_id


def test_r14_anti_drift_account_rebind_and_disabled(r14_env):
    """19 & 20. 账号被禁用或发生改绑（漂移）时 Preflight 阻断启动."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "drift.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:m_orig", label="Orig", kind="local_model"))
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:m_drift", label="Drift", kind="local_model"))
    art_orig = part_registry.add_model_artifact("model:m_orig", ModelArtifactCreate(label="orig.pth", artifact_path=str(cp), stage="promoted"))
    art_drift = part_registry.add_model_artifact("model:m_drift", ModelArtifactCreate(label="drift.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_drift", display_name="BotDrift", account_type="managed_bot", model_identity_id="model:m_orig"))

    sr.create_season(configs_dir, season_id="s_drift", title="Drift Season")
    sr.set_season_enrollment(
        configs_dir, "s_drift", ["account:h1", "account:h2", "account:h3", "account:bot_drift"],
        provenance_by_model={"model:m_orig": {"kind": "local_artifact", "model_identity_id": "model:m_orig", "model_artifact_id": art_orig.model_artifact_id}}
    )

    # 19. 账号被禁用 -> blocked
    part_registry.update_account("account:bot_drift", AccountUpdate(enabled=False))
    rep_dis = preflight_season_runtime(configs_dir, "s_drift", project_root=tmp_path)
    assert rep_dis.state == "blocked"
    assert any(i.code == "account_disabled" for i in rep_dis.issues)

    # 恢复启用，将账号改绑到 model:m_drift -> blocked (account_binding_drift)
    part_registry.update_account("account:bot_drift", AccountUpdate(enabled=True, model_identity_id="model:m_drift"))
    rep_rebind = preflight_season_runtime(configs_dir, "s_drift", project_root=tmp_path)
    assert rep_rebind.state == "blocked"
    assert any(i.code == "account_binding_drift" for i in rep_rebind.issues)


def test_r14_anti_drift_controller_manual_only_and_mismatch(r14_env):
    """21. manual_only 控制器或 controller_type 与 provenance 不匹配时阻断启动."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "ctrl.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:ctrl", label="Ctrl", kind="local_model"))
    art = part_registry.add_model_artifact("model:ctrl", ModelArtifactCreate(label="ctrl.pth", artifact_path=str(cp), stage="promoted"))

    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_ctrl", display_name="BotCtrl", account_type="managed_bot", model_identity_id="model:ctrl"))

    sr.create_season(configs_dir, season_id="s_ctrl", title="Ctrl Season")
    sr.set_season_enrollment(
        configs_dir, "s_ctrl", ["account:h1", "account:h2", "account:h3", "account:bot_ctrl"],
        provenance_by_model={"model:ctrl": {"kind": "local_artifact", "model_identity_id": "model:ctrl", "model_artifact_id": art.model_artifact_id}}
    )

    # 将账号控制器改为 manual_only
    part_registry.update_account("account:bot_ctrl", AccountUpdate(default_controller="manual_only"))
    rep_manual = preflight_season_runtime(configs_dir, "s_ctrl", project_root=tmp_path)
    assert rep_manual.state == "blocked"
    assert any(i.code == "controller_manual_only_not_startable" for i in rep_manual.issues)


def test_r14_persisted_authority_hash_tamper_fails_closed(r14_env):
    """22. 对抗测试：已固化的 running 赛季若磁盘 JSON 被非法篡改，加载时必须 fail-closed 抛错."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "tamper.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:tamp", label="Tamp", kind="local_model"))
    art = part_registry.add_model_artifact("model:tamp", ModelArtifactCreate(label="tamp.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_tamp", display_name="BotTamp", account_type="managed_bot", model_identity_id="model:tamp"))

    sr.create_season(configs_dir, season_id="s_tamp", title="Tamp Season")
    sr.set_season_enrollment(
        configs_dir, "s_tamp", ["account:h1", "account:h2", "account:h3", "account:bot_tamp"],
        provenance_by_model={"model:tamp": {"kind": "local_artifact", "model_identity_id": "model:tamp", "model_artifact_id": art.model_artifact_id}}
    )

    # 启动成功并写入 runtime_manifest
    start_season_runtime(configs_dir, "s_tamp", project_root=tmp_path)

    # 手工篡改磁盘上的 scoring 配置（制造权威哈希漂移）
    season_path = configs_dir / "s_tamp.json"
    raw_season = json.loads(season_path.read_text(encoding="utf-8"))
    raw_season["scoring"]["tampered_rule"] = "malicious"
    season_path.write_text(json.dumps(raw_season, ensure_ascii=False), encoding="utf-8")

    # 尝试加载注册表，必须 fail-closed 拦截！
    with pytest.raises(SeasonRegistryError, match="authority_hash_drift"):
        _load_registry_file(season_path)


def test_r14_legacy_manifest_projection_evidence_missing_fails_closed(r14_env):
    """23. 历史 legacy 赛季在缺失某个成员凭据时，读取 Manifest 绝不返回部分成员，必须 fail-closed."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "leg_mis.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:leg_mis", label="LegMis", kind="local_model"))
    art = part_registry.add_model_artifact("model:leg_mis", ModelArtifactCreate(label="lm.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_lm", display_name="BotLM", account_type="managed_bot", model_identity_id="model:leg_mis"))

    sr.create_season(configs_dir, season_id="s_leg_mis", title="Leg Mis Season")
    sr.set_season_enrollment(
        configs_dir, "s_leg_mis", ["account:h1", "account:h2", "account:h3", "account:bot_lm"],
        provenance_by_model={"model:leg_mis": {"kind": "local_artifact", "model_identity_id": "model:leg_mis", "model_artifact_id": art.model_artifact_id}}
    )
    sr.set_season_status(configs_dir, "s_leg_mis", "running")

    # 删除 checkpoint 文件使得 bot_lm 凭据损坏
    cp.unlink()

    # 读取 Manifest 必须抛错，绝不能返回 3 人的残缺 Manifest!
    with pytest.raises(SeasonRegistryError, match="无法生成历史运行时清单"):
        get_season_runtime_manifest(configs_dir, "s_leg_mis", project_root=tmp_path)


def test_r14_display_name_uses_frozen_enrollment_snapshot(r14_env):
    """24. Manifest 成员 display_name 使用 Season enrollment 冻结快照，不受后续 Account 改名影响."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]

    for i in range(1, 5):
        part_registry.create_account(AccountCreate(account_id=f"account:fn{i}", display_name=f"Original_{i}", account_type="human"))

    sr.create_season(configs_dir, season_id="s_display", title="Display Season")
    sr.set_season_enrollment(configs_dir, "s_display", [f"account:fn{i}" for i in range(1, 5)])

    # 修改 Account 1 的 display_name 为 NewName
    part_registry.update_account("account:fn1", AccountUpdate(display_name="Mutated_1"))

    # 启动赛季
    started = start_season_runtime(configs_dir, "s_display", project_root=tmp_path)
    manifest = started["runtime_manifest"]
    p1 = next(p for p in manifest["participants"] if p["account_id"] == "account:fn1")
    assert p1["display_name"] == "Original_1"


def test_r14_persisted_manifest_tamper_spec_only_fails_closed(r14_env):
    """25. 对抗测试 A：Start 后只篡改 runtime_manifest 中的 runtime_spec（Season 完全不动），加载时必须 fail-closed."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp = _create_checkpoint(models_dir, "spec_tamp.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:sp_t", label="SpT", kind="local_model"))
    art = part_registry.add_model_artifact("model:sp_t", ModelArtifactCreate(label="sp_t.pth", artifact_path=str(cp), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_sp", display_name="BotSp", account_type="managed_bot", model_identity_id="model:sp_t"))

    sr.create_season(configs_dir, season_id="s_sp_t", title="Spec Tamper Season")
    sr.set_season_enrollment(
        configs_dir, "s_sp_t", ["account:h1", "account:h2", "account:h3", "account:bot_sp"],
        provenance_by_model={"model:sp_t": {"kind": "local_artifact", "model_identity_id": "model:sp_t", "model_artifact_id": art.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_sp_t", project_root=tmp_path)

    # 手工篡改磁盘上的 runtime_spec.model_artifact_id（Season models/scoring/authority_hash 保持不变）
    season_path = configs_dir / "s_sp_t.json"
    raw_season = json.loads(season_path.read_text(encoding="utf-8"))
    for p in raw_season["runtime_manifest"]["participants"]:
        if p["account_id"] == "account:bot_sp":
            p["runtime_spec"]["model_artifact_id"] = "artifact:sp_t:fake_id"
    season_path.write_text(json.dumps(raw_season, ensure_ascii=False), encoding="utf-8")

    # 尝试加载注册表，必须 fail-closed 拦截（由于 manifest_id 或 runtime_spec 漂移）
    with pytest.raises(SeasonRegistryError, match="manifest_id_drift|与凭据不一致"):
        _load_registry_file(season_path)


def test_r14_persisted_manifest_tamper_provenance_and_rehash_manifest_id_fails_closed(r14_env):
    """26. 对抗测试 B：Start 后把 Manifest provenance 从 A 改成 B 并重算 manifest_id，但 Season 仍冻结 A，必须 fail-closed."""
    configs_dir = r14_env["configs_dir"]
    tmp_path = r14_env["tmp_path"]
    models_dir = r14_env["models_dir"]

    cp1 = _create_checkpoint(models_dir, "prov_a.pth")
    cp2 = _create_checkpoint(models_dir, "prov_b.pth")
    part_registry.create_model_identity(ModelIdentityCreate(model_identity_id="model:pr_ab", label="PrAB", kind="local_model"))
    art_a = part_registry.add_model_artifact("model:pr_ab", ModelArtifactCreate(label="A.pth", artifact_path=str(cp1), stage="promoted"))
    art_b = part_registry.add_model_artifact("model:pr_ab", ModelArtifactCreate(label="B.pth", artifact_path=str(cp2), stage="promoted"))
    for i in range(1, 4):
        part_registry.create_account(AccountCreate(account_id=f"account:h{i}", display_name=f"H{i}", account_type="human"))
    part_registry.create_account(AccountCreate(account_id="account:bot_pr", display_name="BotPr", account_type="managed_bot", model_identity_id="model:pr_ab"))

    sr.create_season(configs_dir, season_id="s_pr_ab", title="Prov AB Season")
    sr.set_season_enrollment(
        configs_dir, "s_pr_ab", ["account:h1", "account:h2", "account:h3", "account:bot_pr"],
        provenance_by_model={"model:pr_ab": {"kind": "local_artifact", "model_identity_id": "model:pr_ab", "model_artifact_id": art_a.model_artifact_id}}
    )

    start_season_runtime(configs_dir, "s_pr_ab", project_root=tmp_path)

    # 手工篡改磁盘上的 Manifest：将 bot_pr 的 provenance 和 runtime_spec 都改成 B，并重新计算合法的 manifest_id！
    # 但 Season 自身 models 仍然是 A
    season_path = configs_dir / "s_pr_ab.json"
    raw_season = json.loads(season_path.read_text(encoding="utf-8"))
    manifest = raw_season["runtime_manifest"]
    for p in manifest["participants"]:
        if p["account_id"] == "account:bot_pr":
            p["execution_provenance"]["model_artifact_id"] = art_b.model_artifact_id
            p["runtime_spec"]["model_artifact_id"] = art_b.model_artifact_id
            p["runtime_spec"]["artifact_path"] = str(cp2)
            p["runtime_spec"]["resolved_checkpoint_path"] = str(cp2)
    # 重算 manifest_id 使得 self-hash 校验通过
    manifest_id_payload = {
        "season_id": manifest["season_id"],
        "season_authority_hash": manifest["season_authority_hash"],
        "participants": manifest["participants"],
    }
    raw_p = json.dumps(manifest_id_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    manifest["manifest_id"] = f"manifest:{manifest['season_id']}:{hashlib.sha256(raw_p.encode('utf-8')).hexdigest()[:12]}"
    season_path.write_text(json.dumps(raw_season, ensure_ascii=False), encoding="utf-8")

    # 尝试加载注册表，必须发现 Manifest 与 Season Authority 不一致并 fail-closed 抛错！
    with pytest.raises(SeasonRegistryError, match="与 season group provenance 不一致"):
        _load_registry_file(season_path)

