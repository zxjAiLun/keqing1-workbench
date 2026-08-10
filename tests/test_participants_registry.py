# -*- coding: utf-8 -*-
"""participants registry：账号与模型身份/产物的 CRUD 与持久化。"""
from __future__ import annotations

import pytest

from participants import registry
from participants.schemas import (
    AccountCreate,
    AccountUpdate,
    ModelArtifactCreate,
    ModelIdentityCreate,
    ModelIdentityUpdate,
)


@pytest.fixture(autouse=True)
def participants_root(tmp_path, monkeypatch):
    root = tmp_path / "participants"
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(root))
    return root


def test_create_and_get_account():
    account = registry.create_account(
        AccountCreate(display_name="Nick", account_type="human")
    )
    assert account.account_id.startswith("account:nick")
    assert account.default_controller == "human_ui"
    fetched = registry.get_account(account.account_id)
    assert fetched is not None and fetched.display_name == "Nick"


def test_duplicate_account_id_rejected():
    registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick", account_type="human"))
    with pytest.raises(ValueError, match="已存在"):
        registry.create_account(AccountCreate(account_id="nick@01", display_name="Nick2", account_type="human"))


def test_update_account_preserves_id_and_references():
    account = registry.create_account(AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot"))
    updated = registry.update_account(
        account.account_id, AccountUpdate(display_name="70k v2", enabled=False)
    )
    assert updated.display_name == "70k v2"
    assert updated.enabled is False
    assert updated.account_id == "70k@01"
    assert registry.get_account("70k@01").display_name == "70k v2"


def test_soft_delete_when_referenced_hard_delete_when_not(participants_root):
    a = registry.create_account(AccountCreate(account_id="tmp@01", display_name="Tmp", account_type="external_bot"))
    # 未被引用 → 硬删
    result = registry.delete_account(a.account_id, referenced=False)
    assert result["deleted"] is True
    assert registry.get_account(a.account_id) is None
    # 被引用 → 软删（enabled=False）
    b = registry.create_account(AccountCreate(account_id="tmp@02", display_name="Tmp2", account_type="external_bot"))
    result2 = registry.delete_account(b.account_id, referenced=True)
    assert result2["disabled"] is True
    assert registry.get_account(b.account_id).enabled is False


def test_list_accounts_filters():
    registry.create_account(AccountCreate(account_id="a@01", display_name="Alice", account_type="human"))
    registry.create_account(AccountCreate(account_id="b@01", display_name="Bob", account_type="managed_bot"))
    registry.update_account("b@01", AccountUpdate(enabled=False))
    all_accounts = registry.list_accounts()
    assert len(all_accounts) == 2
    enabled_only = registry.list_accounts(enabled=True)
    assert [a.account_id for a in enabled_only] == ["a@01"]
    q = registry.list_accounts(q="alice")
    assert [a.account_id for a in q] == ["a@01"]


def test_unknown_controller_type_rejected_by_pydantic():
    with pytest.raises(Exception):
        registry.create_account(
            AccountCreate(account_id="x@01", display_name="X", account_type="human", default_controller="nonsense")  # type: ignore[arg-type]
        )


def test_model_identity_and_artifact_crud():
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    identity = registry.create_model_identity(
        ModelIdentityCreate(label="70k", kind="local_model", account_id="70k@01", artifact_path="checkpoints/70k.pth")
    )
    assert identity.kind == "local_model"
    assert len(identity.artifacts) == 1
    first = identity.artifacts[0]
    assert first.model_artifact_id.startswith(f"{identity.model_identity_id}@")

    # 新增产物 → 自动设为 current，旧产物降级
    second = registry.add_model_artifact(identity.model_identity_id, ModelArtifactCreate(label="70k-fixed.pth", artifact_path="checkpoints/70k-fixed.pth"))
    refreshed = registry.get_model_identity(identity.model_identity_id)
    current = [art for art in refreshed.artifacts if art.is_current]
    assert len(current) == 1 and current[0].model_artifact_id == second.model_artifact_id


def test_identity_belongs_to_account_helpers():
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot"))
    identity = registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="model-70k", label="70k", kind="local_model", account_id="70k@01", artifact_path="ckpt.pth")
    )
    assert registry.identity_belongs_to_account("model-70k", "70k@01")
    assert not registry.identity_belongs_to_account("model-70k", "other@01")
    assert registry.artifact_belongs_to_identity("model-70k", identity.artifacts[0].model_artifact_id)
    assert not registry.artifact_belongs_to_identity("model-70k", "nope")


def test_account_model_binding_reference_integrity():
    """R11-D Repair：Account.model_identity_id 必须指向已存在身份（fail closed）；
    account-scoped 身份只能被其绑定账号引用。"""
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot"))
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", account_id="70k@01", artifact_path="ckpt.pth")
    )
    # 不存在身份 → 拒绝
    with pytest.raises(ValueError, match="模型身份不存在"):
        registry.create_account(
            AccountCreate(account_id="ghost@01", display_name="ghost", account_type="managed_bot", model_identity_id="model:typo")
        )
    # account-scoped 身份被其他账号引用 → 拒绝
    with pytest.raises(ValueError, match="不能引用账号专属身份"):
        registry.create_account(
            AccountCreate(account_id="70k@02", display_name="70k-2", account_type="managed_bot", model_identity_id="70k")
        )
    # update 同样 fail closed
    with pytest.raises(ValueError, match="模型身份不存在"):
        registry.update_account("70k@01", AccountUpdate(model_identity_id="model:typo"))
    # 自身账号引用 account-scoped 身份 → 允许
    updated = registry.update_account("70k@01", AccountUpdate(model_identity_id="70k"))
    assert updated.model_identity_id == "70k"


def test_global_identity_conversion_guard():
    """R11-D Repair：被多个 Account 反向引用的 global 身份不能转成 account-scoped。"""
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k-global", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot", model_identity_id="70k-global")
    )
    registry.create_account(
        AccountCreate(account_id="70k@02", display_name="70k-2", account_type="managed_bot", model_identity_id="70k-global")
    )
    with pytest.raises(ValueError, match="不能转成账号专属身份"):
        registry.update_model_identity("70k-global", ModelIdentityUpdate(account_id="70k@01"))


def test_global_identity_requires_account_model_binding():
    """R11-D：global identity（account_id=None）不再是 wildcard。

    - 账号显式绑定该模型 → 兼容；
    - 未绑定/真人/其他模型账号 → 不兼容；
    - 未指定 identity_id → 不约束（普通人工导入不受影响）。
    """
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k-global", label="70k", kind="local_model", artifact_path="ckpt.pth")
    )
    registry.create_account(
        AccountCreate(account_id="70k@01", display_name="70k-1", account_type="managed_bot", model_identity_id="70k-global")
    )
    registry.create_account(
        AccountCreate(account_id="70k@02", display_name="70k-2", account_type="managed_bot", model_identity_id="70k-global")
    )
    registry.create_account(AccountCreate(account_id="keqing1", display_name="Nick", account_type="human"))
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="mortal4.1b", label="Mortal4.1b", kind="external_agent")
    )
    registry.create_account(
        AccountCreate(account_id="mortal41b", display_name="Mortal4.1b", account_type="external_bot", model_identity_id="mortal4.1b")
    )

    # 绑定该模型的账号 → 兼容
    assert registry.identity_belongs_to_account("70k-global", "70k@01")
    assert registry.identity_belongs_to_account("70k-global", "70k@02")
    # 未绑定（真人）→ 不兼容
    assert not registry.identity_belongs_to_account("70k-global", "keqing1")
    # 绑定其他模型 → 不兼容
    assert not registry.identity_belongs_to_account("70k-global", "mortal41b")
    # identity_id=None → 不约束
    assert registry.identity_belongs_to_account(None, "keqing1")


# ---------------------------------------------------------------------------
# P1-2：stale lock 回收 / P1-4：引用完整性
# ---------------------------------------------------------------------------

def test_stale_lock_recovered(participants_root):
    """预建 mtime 早于 stale_after 的锁 → 下一次写操作应回收并成功。"""
    import os
    import time

    participants_root.mkdir(parents=True, exist_ok=True)
    lock = participants_root / "participants.lock"
    lock.write_text("9999", encoding="utf-8")
    old = time.time() - 60  # 60 秒前 > stale_after(30s)
    os.utime(lock, (old, old))
    registry.create_account(AccountCreate(account_id="x@01", display_name="X", account_type="human"))
    assert registry.get_account("x@01") is not None
    assert not lock.exists(), "stale lock 应被回收"


def test_identity_rejects_nonexistent_account():
    with pytest.raises(ValueError, match="账号不存在"):
        registry.create_model_identity(
            ModelIdentityCreate(label="x", kind="local_model", account_id="ghost@01")
        )


def test_update_identity_rejects_nonexistent_account():
    registry.create_account(AccountCreate(account_id="a@01", display_name="A", account_type="human"))
    identity = registry.create_model_identity(ModelIdentityCreate(label="m", kind="local_model"))
    from participants.schemas import ModelIdentityUpdate

    with pytest.raises(ValueError, match="账号不存在"):
        registry.update_model_identity(identity.model_identity_id, ModelIdentityUpdate(account_id="ghost@01"))


def test_artifact_requires_identity():
    # artifact 非空但 identity 为空 → 不允许悬空 artifact
    assert registry.artifact_belongs_to_identity(None, "artifact-x") is False
    assert registry.artifact_belongs_to_identity(None, None) is True


def test_identity_reference_blocks_hard_delete():
    registry.create_account(AccountCreate(account_id="a@02", display_name="A2", account_type="human"))
    registry.create_model_identity(ModelIdentityCreate(model_identity_id="mid", label="m", kind="local_model", account_id="a@02"))
    result = registry.delete_account("a@02", referenced=True)
    assert result["disabled"] is True
    assert registry.get_account("a@02").enabled is False


def test_set_current_artifact_demotes_others():
    """R10 UX Repair P1-4：显式把指定 artifact 设为 current，其余降级。"""
    registry.create_account(AccountCreate(account_id="70k@01", display_name="70k", account_type="managed_bot"))
    identity = registry.create_model_identity(
        ModelIdentityCreate(label="70k", kind="local_model", account_id="70k@01", artifact_path="ckpt-a.pth")
    )
    art_b = registry.add_model_artifact(identity.model_identity_id, ModelArtifactCreate(label="b", artifact_path="ckpt-b.pth"))
    art_a = identity.artifacts[0]

    # 当前 current 是 b（新增自动设 current）；把 a 设为 current → a 是唯一 current
    registry.set_current_model_artifact(identity.model_identity_id, art_a.model_artifact_id)
    refreshed = registry.get_model_identity(identity.model_identity_id)
    current = [art for art in refreshed.artifacts if art.is_current]
    assert len(current) == 1 and current[0].model_artifact_id == art_a.model_artifact_id
    assert not any(art.model_artifact_id == art_b.model_artifact_id and art.is_current for art in refreshed.artifacts)

    # 不存在的 artifact → 404 语义（KeyError）
    with pytest.raises(KeyError):
        registry.set_current_model_artifact(identity.model_identity_id, "nope")
