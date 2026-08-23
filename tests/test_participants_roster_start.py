# -*- coding: utf-8 -*-
"""R10-E：start 端点 roster 模式——移除 quantity=3 门槛 + 持久化 roster + session 别名。"""
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gateway.api.playwithyou as pw  # noqa: E402


class FakeProc:
    pid = 9999
    stdout = io.StringIO("")

    def poll(self):
        return None

    def terminate(self):
        pass

    def kill(self):
        pass

    def communicate(self):
        return ("", "")


@pytest.fixture
def pw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(pw, "LAUNCHER", tmp_path / "launch_tenhou_bots.py")
    (tmp_path / "launch_tenhou_bots.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [])
    monkeypatch.setattr(
        pw, "_resolve_spec",
        lambda network, custom_paths, slot: "70k" if network == "mortal" else None,
    )
    # R11-C Repair 2：capture root 走 canonical helper（KEQING_LADDER_DATA_ROOT）
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    # P1-2：冻结用真实 resolve_bot_spec 解析 checkpoint 路径，再按 artifact 精确匹配
    checkpoint = tmp_path / "checkpoints" / "70k.pth"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text("", encoding="utf-8")

    def _fake_resolve_bot_spec(spec, root):
        return ("checkpoint", str(checkpoint))

    monkeypatch.setattr("inference.bot_registry.resolve_bot_spec", _fake_resolve_bot_spec)
    fake_subprocess = mock.Mock()
    fake_subprocess.Popen.return_value = FakeProc()
    monkeypatch.setattr(pw, "subprocess", fake_subprocess)
    monkeypatch.setattr(pw, "_ladder_config_dir", lambda: tmp_path / "configs")
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    pw.SESSIONS.clear()
    pw._HISTORY.clear()
    # P1-2：roster 校验要求参与者账号存在且启用
    from participants import registry
    from participants.schemas import AccountCreate, ModelIdentityCreate

    # R11-D：身份先建，账号再绑定（引用完整性 fail-closed）
    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path=str(checkpoint))
    )
    for account_id, account_type in (
        ("nick@01", "human"),
        ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"),
        ("mortal@01", "external_bot"),
    ):
        # R11-D：AI 账号显式绑定逻辑模型（global identity 不再是 wildcard）
        registry.create_account(
            AccountCreate(
                account_id=account_id,
                display_name=account_id,
                account_type=account_type,
                model_identity_id="70k" if account_id.startswith("70k") else None,
            )
        )
    return tmp_path


def _roster_request():
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    return StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0, expected_raw_name="NoName-1"),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1, expected_raw_name="NoName-2"),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )


def test_start_with_roster_quantity_two(pw_env):
    """R10-E：Nick human + 2 本地 bot + 外部 Mortal（quantity=2）可启动捕获。"""
    status = pw.start_playwithyou(_roster_request())
    assert status.running is True
    assert len(status.bots) == 2  # quantity=2 合法，不再要求恰好 1 人类 + 3 bot

    capture_dir = pw_env / "ladder" / "captures" / "playwithyou" / status.session_id
    binding = json.loads((capture_dir / "binding.json").read_text(encoding="utf-8"))
    assert binding["mode"] == "roster"
    assert len(binding["roster"]) == 4
    # P1-2：launcher 模型身份/产物已从 spec 冻结（70k → 70k identity + current artifact）
    launched = [e for e in binding["roster"] if e.get("launcher_slot") is not None]
    assert len(launched) == 2
    assert all(e.get("model_identity_id") == "70k" for e in launched)
    assert all(e.get("model_artifact_id") for e in launched)

    # session-scoped 别名：NoName-1 → 70k@01 / NoName-2 → 70k@02
    from participants import aliases

    session_aliases = [
        a for a in aliases.list_aliases()
        if a.scope == "session" and a.session_id == status.session_id
    ]
    assert len(session_aliases) == 2
    assert any(a.external_id == "NoName-1" and a.account_id == "70k@01" and a.model_identity_id == "70k" for a in session_aliases)
    assert any(a.external_id == "NoName-2" and a.account_id == "70k@02" and a.model_identity_id == "70k" for a in session_aliases)


def test_start_with_roster_rejects_invalid_length(pw_env):
    from fastapi import HTTPException
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "none", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0),
        ],
    )
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(req)
    assert exc.value.status_code == 400
    assert "4" in str(exc.value.detail)


def test_start_with_roster_requires_launcher_slot(pw_env):
    from fastapi import HTTPException
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["none", "none", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model"),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model"),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(req)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# R10-E Repair：真实 launcher 接线（P1-1）
# ---------------------------------------------------------------------------

def test_launcher_build_configs_roster_wiring(tmp_path, monkeypatch):
    """P1-1：真实 roster binding.json → _build_configs → 2 configs + collector 绑定账号。"""
    import argparse
    import sys as _sys

    from workbench import launch_tenhou_bots as launcher

    # 模拟 spec 解析与设备选择，聚焦 binding 接线
    monkeypatch.setattr(launcher, "resolve_bot_spec", lambda spec, root: ("checkpoint", tmp_path / f"{spec}.pth"))
    monkeypatch.setattr(launcher, "_pick_device", lambda device: "cpu")
    monkeypatch.setattr(launcher, "normalize_tenhou_room", lambda room, **kw: "L2147_9")

    capture_dir = tmp_path / "capture"
    (capture_dir / "pending").mkdir(parents=True)
    binding = {
        "session_id": "s_roster",
        "season_id": "",
        "human_account_id": "",
        "bot_account_ids": [],
        "mode": "roster",
        "roster": [
            {"account_id": "nick@01", "controller_type": "human_ui", "launcher_slot": None, "expected_raw_name": "Nick"},
            {"account_id": "70k@01", "controller_type": "local_model", "launcher_slot": 0, "expected_raw_name": "NoName-1", "model_identity_id": "70k", "model_artifact_id": "a1"},
            {"account_id": "70k@02", "controller_type": "local_model", "launcher_slot": 1, "expected_raw_name": "NoName-2", "model_identity_id": "70k", "model_artifact_id": "a2"},
            {"account_id": "mortal@01", "controller_type": "external_agent", "launcher_slot": None},
        ],
        "frozen_at": 0.0,
    }
    (capture_dir / "binding.json").write_text(json.dumps(binding, ensure_ascii=False), encoding="utf-8")

    args = argparse.Namespace(
        bots=["70k", "70k"],
        name_prefix="NoName",
        room="2147",
        device="cuda",
        game_type="hanchan",
        gateway_host="127.0.0.1",
        gateway_port=12101,
        bot_verbose=False,
        think_delay=0.0,
        ladder_capture_dir=str(capture_dir),
    )
    configs, collector = launcher._build_configs(args)
    assert len(configs) == 2
    assert collector is not None
    assert collector.binding.mode == "roster"
    assert len(collector.binding.roster) == 4
    # launcher slot 顺序 → config 顺序（NoName-1 → 70k@01，NoName-2 → 70k@02）
    assert configs[0].ladder_account_id == "70k@01"
    assert configs[1].ladder_account_id == "70k@02"
    assert configs[0].capture_sink is collector
    assert configs[1].capture_sink is collector


def test_launcher_roster_slot_sorting(tmp_path, monkeypatch):
    """P1-2：roster 顺序与 launcher_slot 顺序不同时，按 slot 对齐（NoName-1 不串线）。"""
    import argparse

    from workbench import launch_tenhou_bots as launcher

    monkeypatch.setattr(launcher, "resolve_bot_spec", lambda spec, root: ("checkpoint", tmp_path / f"{spec}.pth"))
    monkeypatch.setattr(launcher, "_pick_device", lambda device: "cpu")
    monkeypatch.setattr(launcher, "normalize_tenhou_room", lambda room, **kw: "L2147_9")

    capture_dir = tmp_path / "capture"
    (capture_dir / "pending").mkdir(parents=True)
    binding = {
        "session_id": "s_roster",
        "season_id": "",
        "human_account_id": "",
        "bot_account_ids": [],
        "mode": "roster",
        "roster": [
            {"account_id": "nick@01", "controller_type": "human_ui", "launcher_slot": None},
            {"account_id": "70k@02", "controller_type": "local_model", "launcher_slot": 1, "expected_raw_name": "NoName-2"},
            {"account_id": "70k@01", "controller_type": "local_model", "launcher_slot": 0, "expected_raw_name": "NoName-1"},
            {"account_id": "mortal@01", "controller_type": "external_agent", "launcher_slot": None},
        ],
        "frozen_at": 0.0,
    }
    (capture_dir / "binding.json").write_text(json.dumps(binding, ensure_ascii=False), encoding="utf-8")

    args = argparse.Namespace(
        bots=["70k", "70k"],
        name_prefix="NoName",
        room="2147",
        device="cuda",
        game_type="hanchan",
        gateway_host="127.0.0.1",
        gateway_port=12101,
        bot_verbose=False,
        think_delay=0.0,
        ladder_capture_dir=str(capture_dir),
    )
    configs, collector = launcher._build_configs(args)
    # 按 launcher_slot 排序：slot0 → 70k@01（NoName-1），slot1 → 70k@02（NoName-2）
    assert configs[0].ladder_account_id == "70k@01"
    assert configs[1].ladder_account_id == "70k@02"


def test_roster_and_ladder_capture_mutually_exclusive(pw_env):
    """P2：roster 与旧正式天梯绑定互斥 → 400。"""
    from fastapi import HTTPException
    from gateway.api.playwithyou import LadderCaptureRequest

    req = _roster_request()
    req.ladder_capture = LadderCaptureRequest(
        enabled=True, season_id="official-ladder-v1", human_account_id="nick@01",
        bot_account_ids=["70k@01", "70k@02", "70k@03"], mode="confirm",
    )
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(req)
    assert exc.value.status_code == 400
    assert "不能同时开启" in str(exc.value.detail)


def test_roster_rejects_unknown_account(pw_env):
    """P1-2：roster 引用的账号不存在 → 呼出前拒绝。"""
    from fastapi import HTTPException
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="ghost@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(req)
    assert exc.value.status_code == 400
    assert "ghost@01" in str(exc.value.detail)
    # 无残留：未创建 capture 目录 / session 别名
    from participants import aliases

    assert aliases.list_aliases() == []
    assert not list((pw_env / "ladder" / "captures" / "playwithyou").glob("*")) if (pw_env / "ladder" / "captures" / "playwithyou").exists() else True


def test_roster_rejects_disabled_account(pw_env):
    """P1-2：roster 引用的账号已停用 → 呼出前拒绝。"""
    from fastapi import HTTPException
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest
    from participants import registry
    from participants.schemas import AccountUpdate

    registry.update_account("70k@01", AccountUpdate(enabled=False))
    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(req)
    assert exc.value.status_code == 400
    assert "停用" in str(exc.value.detail)


def test_roster_freeze_preserves_original_order(pw_env):
    """P1-1：roster 原始顺序与 launcher_slot 顺序不同时，binding 保持原四人顺序，别名正确。"""
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1, expected_raw_name="NoName-2"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0, expected_raw_name="NoName-1"),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    status = pw.start_playwithyou(req)
    capture_dir = pw_env / "ladder" / "captures" / "playwithyou" / status.session_id
    binding = json.loads((capture_dir / "binding.json").read_text(encoding="utf-8"))
    # 保持原四人顺序（不交换成员）
    assert [e["account_id"] for e in binding["roster"]] == ["nick@01", "70k@02", "70k@01", "mortal@01"]
    launched = {int(e["launcher_slot"]): e for e in binding["roster"] if e.get("launcher_slot") is not None}
    assert launched[0]["account_id"] == "70k@01"
    assert launched[1]["account_id"] == "70k@02"
    # 会话别名：NoName-1 → 70k@01，NoName-2 → 70k@02（不串线）
    from participants import aliases

    by_name = {
        a.external_id: a for a in aliases.list_aliases()
        if a.scope == "session" and a.session_id == status.session_id
    }
    assert by_name["NoName-1"].account_id == "70k@01"
    assert by_name["NoName-2"].account_id == "70k@02"
    assert by_name["NoName-1"].model_identity_id == "70k"


def test_roster_popen_failure_rolls_back(pw_env):
    """P2-1：Popen 失败 → 500 且回滚 capture 目录与 session aliases。"""
    from fastapi import HTTPException

    pw.subprocess.Popen.side_effect = RuntimeError("boom")
    with pytest.raises(HTTPException) as exc:
        pw.start_playwithyou(_roster_request())
    assert exc.value.status_code == 500
    from participants import aliases

    assert aliases.list_aliases() == []
    captures_root = pw_env / "ladder" / "captures" / "playwithyou"
    if captures_root.exists():
        assert list(captures_root.glob("*")) == []


def test_roster_validation_failure_no_subprocess(pw_env):
    """P1-3：校验失败（账号缺失）→ 无 alias、无 capture、无 subprocess 启动。"""
    from fastapi import HTTPException
    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="ghost@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    with pytest.raises(HTTPException):
        pw.start_playwithyou(req)
    pw.subprocess.Popen.assert_not_called()
    from participants import aliases

    assert aliases.list_aliases() == []


def test_frozen_checkpoint_does_not_drift(tmp_path, monkeypatch):
    """P1：父进程冻结 checkpoint A 后 resolver 状态变 B——子进程/runtime 仍加载 A，
    binding 与别名记录 A 对应 artifact，绝不允许「冻结的是 A、实际加载的是 B」。"""
    import argparse

    from workbench import launch_tenhou_bots as launcher
    from participants import registry
    from participants.schemas import AccountCreate, ModelIdentityCreate

    checkpoint_a = tmp_path / "checkpoints" / "a.pth"
    checkpoint_b = tmp_path / "checkpoints" / "b.pth"
    checkpoint_a.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_a.write_text("")
    checkpoint_b.write_text("")

    monkeypatch.setattr(pw, "LAUNCHER", tmp_path / "launch_tenhou_bots.py")
    (tmp_path / "launch_tenhou_bots.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [])
    monkeypatch.setattr(
        pw, "_resolve_spec",
        lambda network, custom_paths, slot: "mortal" if network == "mortal" else None,
    )
    monkeypatch.setenv("KEQING_PARTICIPANT_DATA_ROOT", str(tmp_path / "participants"))
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    monkeypatch.setattr(pw, "_ladder_config_dir", lambda: tmp_path / "configs")
    fake_subprocess = mock.Mock()
    fake_subprocess.Popen.return_value = FakeProc()
    monkeypatch.setattr(pw, "subprocess", fake_subprocess)
    pw.SESSIONS.clear()
    pw._HISTORY.clear()

    registry.create_model_identity(
        ModelIdentityCreate(model_identity_id="70k", label="70k", kind="local_model", artifact_path=str(checkpoint_a))
    )
    for aid, atype in (
        ("nick@01", "human"), ("70k@01", "managed_bot"),
        ("70k@02", "managed_bot"), ("mortal@01", "external_bot"),
    ):
        registry.create_account(
            AccountCreate(
                account_id=aid,
                display_name=aid,
                account_type=atype,
                model_identity_id="70k" if aid.startswith("70k") else None,
            )
        )

    # 冻结期间（2 次 launcher 解析）返回 A；之后（子进程重新解析时）变 B，模拟漂移
    calls = {"n": 0}

    def _resolver(spec, root):
        calls["n"] += 1
        return ("mortal", checkpoint_a if calls["n"] <= 2 else checkpoint_b)

    monkeypatch.setattr("inference.bot_registry.resolve_bot_spec", _resolver)

    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    req = StartPlayWithYouRequest(
        networks=["mortal", "mortal", "none", "none"],
        roster=[
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui"),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=0, expected_raw_name="NoName-1"),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=1, expected_raw_name="NoName-2"),
            ParticipantBindingRequest(account_id="mortal@01", controller_type="external_agent"),
        ],
    )
    status = pw.start_playwithyou(req)

    # 父进程冻结在 A：binding 与 session 别名记录 A 对应 artifact
    capture_dir = pw_env_root = tmp_path / "ladder" / "captures" / "playwithyou" / status.session_id
    binding = json.loads((capture_dir / "binding.json").read_text(encoding="utf-8"))
    launched = [e for e in binding["roster"] if e.get("resolved_checkpoint_path")]
    assert len(launched) == 2
    assert all(e["resolved_checkpoint_path"] == str(checkpoint_a) for e in launched)

    # --bots 传 A 的绝对路径，绝不再传动态 spec 名
    command = pw.subprocess.Popen.call_args[0][0]
    bots_start = command.index("--bots") + 1
    bots_end = command.index("--device")
    bots_arg = command[bots_start:bots_end]
    assert bots_arg == [str(checkpoint_a), str(checkpoint_a)]
    assert "mortal" not in bots_arg

    # 子进程此时 resolver 已返回 B，但必须加载 A（从 binding 冻结路径）
    monkeypatch.setattr(launcher, "normalize_tenhou_room", lambda room, **kw: "L2147_9")
    monkeypatch.setattr(launcher, "_pick_device", lambda device: "cpu")
    child_args = argparse.Namespace(
        bots=bots_arg, name_prefix="NoName", room="2147", device="cuda",
        game_type="hanchan", gateway_host="127.0.0.1", gateway_port=12101,
        bot_verbose=False, think_delay=0.0, ladder_capture_dir=str(capture_dir),
    )
    configs, collector = launcher._build_configs(child_args)
    assert str(configs[0].model_path) == str(checkpoint_a)
    assert str(configs[1].model_path) == str(checkpoint_a)
    assert configs[0].resolved_model_path() == checkpoint_a
    assert collector.binding.roster


def test_discover_captures_exposes_roster_and_evidence(tmp_path, monkeypatch):
    """P2：_discover_captures 的 API 返回必须包含 roster 与 evidence_warning。"""
    # R11-C Repair 2：capture root 走 canonical helper（KEQING_LADDER_DATA_ROOT）
    monkeypatch.setenv("KEQING_LADDER_DATA_ROOT", str(tmp_path / "ladder"))
    from project_data import ladder_capture_root

    capture_dir = ladder_capture_root() / "s1" / "pending"
    capture_dir.mkdir(parents=True)
    payload = {
        "capture_id": "s1:tenhou:abc",
        "session_id": "s1",
        "state": "awaiting_import",
        "match": {"match_id": "tenhou:abc"},
        "tenhou_log_url": "https://tenhou.net/3/?log=abc",
        "roster": [{"account_id": "70k@01", "controller_type": "local_model", "launcher_slot": 0}],
        "evidence_warning": "observer 70k@01 规范化后分数不一致",
        "score_observers": ["70k@01"],
    }
    (capture_dir / "abc.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    captures = pw.list_ladder_captures()["captures"]
    assert len(captures) == 1
    assert captures[0]["roster"] == payload["roster"]
    assert captures[0]["evidence_warning"] == payload["evidence_warning"]
    assert captures[0]["score_observers"] == ["70k@01"]


def test_launcher_names_are_canonical(pw_env, tmp_path, monkeypatch):
    """UX Repair 2 / P1-1：launcher 名称由后端按 launcher_slot 生成（UI 不覆盖）。

    1 个 bot → NoName；sparse 2 个 → NoName-1/NoName-2；3 个 → NoName-1/2/3。
    """
    from participants import aliases
    from participants.schemas import AccountCreate

    registry = __import__("participants.registry", fromlist=["create_account"])
    registry.create_account(
        AccountCreate(account_id="70k@03", display_name="70k@03", account_type="managed_bot", model_identity_id="70k")
    )

    from gateway.api.playwithyou import ParticipantBindingRequest, StartPlayWithYouRequest

    def _start(launched_slots):
        networks = ["mortal" if i in launched_slots else "none" for i in range(4)]
        roster = [
            ParticipantBindingRequest(account_id="nick@01", controller_type="human_ui", launcher_slot=None),
            ParticipantBindingRequest(account_id="70k@01", controller_type="local_model", launcher_slot=1 if 1 in launched_slots else None),
            ParticipantBindingRequest(account_id="70k@02", controller_type="local_model", launcher_slot=2 if 2 in launched_slots else None),
            ParticipantBindingRequest(account_id="70k@03", controller_type="local_model", launcher_slot=3 if 3 in launched_slots else None),
        ]
        req = StartPlayWithYouRequest(
            networks=networks,
            roster=roster,
        )
        status = pw.start_playwithyou(req)
        return status.session_id

    def _names(session_id):
        return sorted(
            a.external_id
            for a in aliases.list_aliases()
            if a.scope == "session" and a.session_id == session_id
        )

    # 1 个 launched（slot 1）→ NoName
    sid1 = _start({1})
    assert _names(sid1) == ["NoName"]
    pw.SESSIONS.clear()
    pw._HISTORY.clear()

    # sparse 2 个 launched（slot 1, 3）→ NoName-1 / NoName-2（不是 NoName-2/NoName-4）
    sid2 = _start({1, 3})
    assert _names(sid2) == ["NoName-1", "NoName-2"]
    pw.SESSIONS.clear()
    pw._HISTORY.clear()

    # 3 个 launched（slot 1,2,3）→ NoName-1 / NoName-2 / NoName-3
    sid3 = _start({1, 2, 3})
    assert _names(sid3) == ["NoName-1", "NoName-2", "NoName-3"]
    pw.SESSIONS.clear()
    pw._HISTORY.clear()


def test_model_only_launchers_no_account(pw_env):
    """R11-B：launchers 只呼出模型（model_id），session alias 不绑账号。

    model_id → resolve_bot_spec → 冻结绝对 checkpoint 路径（frozen roster），
    launcher 实际收到同一路径；不再依赖 Participants ModelIdentity/Artifact。
    """
    from participants import aliases

    from gateway.api.playwithyou import PlayWithYouLauncherRequest, StartPlayWithYouRequest

    # 用仓库真实模型目录里的两个具名 checkpoint（70k / ext_mortal）
    req = StartPlayWithYouRequest(
        launchers=[
            PlayWithYouLauncherRequest(model_id="70k"),
            PlayWithYouLauncherRequest(model_id="ext_mortal"),
        ]
    )
    status = pw.start_playwithyou(req)
    # session alias：NoName-1/NoName-2 存在，account_id=None（模型事实走 frozen roster）
    by_name = {
        a.external_id: a
        for a in aliases.list_aliases()
        if a.scope == "session" and a.session_id == status.session_id
    }
    assert set(by_name) == {"NoName-1", "NoName-2"}
    assert by_name["NoName-1"].account_id is None
    # frozen roster：model_id + 冻结的绝对 checkpoint 路径
    assert status.frozen_roster is not None
    launched = [e for e in status.frozen_roster if e.get("launcher_slot") is not None]
    assert len(launched) == 2
    assert launched[0]["model_id"] == "70k"
    assert launched[1]["model_id"] == "ext_mortal"
    assert launched[0].get("account_id") is None
    # pw_env fixture 把 resolve_bot_spec 冻结为 tmp_path/checkpoints/70k.pth：
    # launcher spec 与 frozen 路径必须同源。
    assert launched[0]["resolved_checkpoint_path"] == str((pw_env / "checkpoints" / "70k.pth").resolve())
    pw.SESSIONS.clear()
    pw._HISTORY.clear()


def test_model_only_launchers_reject_unknown_model(pw_env):
    """R11-B：未知 model_id 必须在 start 前拒绝（不落到 launcher）。"""
    from gateway.api.playwithyou import PlayWithYouLauncherRequest, StartPlayWithYouRequest

    from fastapi import HTTPException

    req = StartPlayWithYouRequest(launchers=[PlayWithYouLauncherRequest(model_id="does-not-exist")])
    with pytest.raises(HTTPException, match="未知模型"):
        pw.start_playwithyou(req)


def test_playwithyou_models_catalog():
    """R11-B：catalog 只暴露具名 Mortal checkpoints（70k / ext_mortal），不读 Participants。"""
    from gateway.api.playwithyou import PLAYWITHYOU_MODEL_CATALOG, list_playwithyou_models

    assert {m["model_id"] for m in PLAYWITHYOU_MODEL_CATALOG} == {"70k", "ext_mortal"}
    payload = list_playwithyou_models()
    assert payload["models"] == PLAYWITHYOU_MODEL_CATALOG


def test_accountless_launcher_unique_observer_keys(tmp_path, monkeypatch):
    """P1-A：account-less launcher 的 observer key 必须唯一（NoName-N），
    两个 bot 上报不同真实 seat 时 collector 不 conflict。"""
    import argparse

    from workbench import launch_tenhou_bots as launcher
    from gateway.playwithyou_capture import PlayWithYouCaptureCollector

    monkeypatch.setattr(launcher, "resolve_bot_spec", lambda spec, root: ("checkpoint", tmp_path / f"{spec}.pth"))
    monkeypatch.setattr(launcher, "_pick_device", lambda device: "cpu")
    monkeypatch.setattr(launcher, "normalize_tenhou_room", lambda room, **kw: "L2147_9")

    capture_dir = tmp_path / "capture"
    (capture_dir / "pending").mkdir(parents=True)
    binding = {
        "session_id": "s_acl",
        "season_id": "",
        "human_account_id": "",
        "bot_account_ids": [],
        "mode": "roster",
        "roster": [
            {"account_id": None, "controller_type": None, "launcher_slot": 0, "expected_raw_name": "NoName-1", "model_identity_id": "v3", "model_artifact_id": "a1", "resolved_checkpoint_path": str(tmp_path / "v3.pth")},
            {"account_id": None, "controller_type": None, "launcher_slot": 1, "expected_raw_name": "NoName-2", "model_identity_id": "ext", "model_artifact_id": "a2", "resolved_checkpoint_path": str(tmp_path / "ext.pth")},
        ],
        "frozen_at": 0.0,
    }
    (capture_dir / "binding.json").write_text(json.dumps(binding, ensure_ascii=False), encoding="utf-8")

    args = argparse.Namespace(
        bots=["v3", "ext"], name_prefix="NoName", room="2147", device="cuda",
        game_type="hanchan", gateway_host="127.0.0.1", gateway_port=12101,
        bot_verbose=False, think_delay=0.0, ladder_capture_dir=str(capture_dir),
    )
    configs, collector = launcher._build_configs(args)
    assert collector is not None
    # observer key 唯一：NoName-1 / NoName-2（不是字符串 "None"）
    assert configs[0].ladder_account_id == "NoName-1"
    assert configs[1].ladder_account_id == "NoName-2"
    assert len({c.ladder_account_id for c in configs}) == 2

    # 两个 observer 各自上报不同真实 seat → 不 conflict
    collector.observe("NoName-1", {"type": "start_game", "tenhou_log_seat": 0, "log": "https://tenhou.net/3/?log=gm1&tw=0"})
    collector.observe("NoName-2", {"type": "start_game", "tenhou_log_seat": 1, "log": "https://tenhou.net/3/?log=gm1&tw=1"})
    assert collector._state != "conflict"

    # 同一 observer 上报不同 seat → 仍正确判冲突（证明 key 区分是必要的）
    collector.observe("NoName-1", {"type": "start_game", "tenhou_log_seat": 2, "log": "https://tenhou.net/3/?log=gm1&tw=2"})
    assert collector._state == "conflict"
    assert "重复上报不同全局 seat" in (collector._conflict_reason or "")
