"""Regression tests for Play-with-you session stop / orphan cleanup.

These guard the fix that makes Stop reliable even after a backend restart:
* ``_find_owned_pids`` discovers only launcher/gateway processes carrying the
  dedicated Play-with-you ownership marker, without depending on the in-memory
  SESSIONS dict.
* ``/stop`` always mops up owned orphaned bot artifacts, not just a tracked session.
* ``/status`` surfaces an orphaned launcher so the GUI keeps showing the Stop
  button even when the backend lost its session record.
"""
import json
import sys
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

# Make ``src`` importable regardless of cwd.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import gateway.api.playwithyou as pw  # noqa: E402


WMIC_LIST = (
    "CommandLine=python launch_tenhou_bots.py --session-token playwithyou-session\r\n"
    "ProcessId=1234\r\n\r\n"
    "CommandLine=python gateway/main.py --owner-token playwithyou-gateway\r\n"
    "ProcessId=5678\r\n"
)


def test_find_owned_pids_windows(monkeypatch):
    monkeypatch.setattr(pw.os, "name", "nt")
    monkeypatch.setattr(
        pw.subprocess,
        "check_output",
        mock.Mock(return_value=WMIC_LIST),
    )
    monkeypatch.setattr(pw, "_pid_is_alive", lambda _pid: True)
    assert pw._find_owned_pids() == [1234, 5678]


def test_find_owned_pids_posix(monkeypatch):
    monkeypatch.setattr(pw.os, "name", "posix")
    monkeypatch.setattr(
        pw.subprocess,
        "check_output",
        mock.Mock(return_value="111\n222\n"),
    )
    monkeypatch.setattr(pw, "_pid_is_alive", lambda _pid: True)
    assert pw._find_owned_pids() == [111, 222]
    assert pw.subprocess.check_output.call_args_list == [
        mock.call(["pgrep", "-f", f"--session-token {pw.SESSION_TOKEN}"], text=True),
        mock.call(["pgrep", "-f", f"--owner-token {pw.GATEWAY_OWNER_TOKEN}"], text=True),
    ]


def test_find_owned_pids_handles_wmic_error(monkeypatch):
    monkeypatch.setattr(pw.os, "name", "nt")
    monkeypatch.setattr(
        pw.subprocess,
        "check_output",
        mock.Mock(side_effect=Exception("wmic not available")),
    )
    # Must fail open (return []) rather than raise into the request path.
    assert pw._find_owned_pids() == []


def test_find_owned_pids_discards_dead_query_process(monkeypatch):
    monkeypatch.setattr(pw.os, "name", "nt")
    monkeypatch.setattr(
        pw.subprocess,
        "check_output",
        mock.Mock(
            return_value=(
                "CommandLine=wmic process where commandline like playwithyou\r\n"
                "ProcessId=1234\r\n"
            )
        ),
    )
    monkeypatch.setattr(pw, "_pid_is_alive", lambda _pid: False)
    assert pw._find_owned_pids() == []


def test_stop_always_kills_orphans(monkeypatch):
    """Stop must clean up orphaned bot processes even with no tracked session
    (the backend-restart scenario)."""
    monkeypatch.setattr(pw, "SESSIONS", {})
    monkeypatch.setattr(pw, "_current_session", lambda: None)
    killed = []
    monkeypatch.setattr(pw, "_kill_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [9999])

    status = pw.stop_playwithyou()

    assert status.running is False
    assert killed == [9999], "orphaned launcher pid should have been force-killed"


def test_status_surfaces_orphan_launcher(monkeypatch):
    """When no in-memory session exists but a launcher is still alive, /status
    must report running=True so the GUI offers a Stop button."""
    monkeypatch.setattr(pw, "SESSIONS", {})
    monkeypatch.setattr(pw, "_current_session", lambda: None)
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [4242])

    status = pw.playwithyou_status()

    assert status.running is True
    assert "遗留" in status.log_tail[0]


def test_status_reports_finished_session_with_log_url(monkeypatch):
    """R11-C：对局结束后 /status 仍回传 session_id + tenhou_log_url（awaiting_import），
    前端据此自动进入导入引导；session_id 只作内部关联键，不再要求用户复制。"""
    import time

    class FakeProc:
        stdout: list[str] = []

        def poll(self):
            return 0  # launcher 已退出 → 对局结束

    session = pw.PWYSession(
        session_id="sess-r11c",
        proc=FakeProc(),
        command=["python", "launch_tenhou_bots.py"],
        lobby_id="2147",
        specs=["70k"],
        names=["NoName"],
        speed="normal",
        device="cpu",
        started_at=time.time(),
    )
    session.frozen_roster = [
        {
            "model_id": "70k",
            "launcher_slot": 0,
            "expected_raw_name": "NoName",
            "resolved_checkpoint_path": "E:\\checkpoints\\70k.pth",
        }
    ]
    monkeypatch.setattr(pw, "_current_session", lambda: session)
    monkeypatch.setattr(
        pw,
        "_session_log_url",
        lambda sid: "https://tenhou.net/3/?log=20260810gm-0001-2147-abc12345",
    )

    status = pw.playwithyou_status()

    assert status.running is False
    assert status.session_id == "sess-r11c"
    assert status.tenhou_log_url == "https://tenhou.net/3/?log=20260810gm-0001-2147-abc12345"
    assert status.frozen_roster is not None
    assert status.frozen_roster[0]["model_id"] == "70k"
    # 自然退出（termination_reason 未显式设置）→ natural_exit
    assert status.termination_reason == "natural_exit"


def test_status_manual_stop_termination_reason(monkeypatch):
    """R11-C Repair：手动停止后 status 标记 manual_stop——前端不显示赛后导入 CTA。"""
    import time

    class FakeProc:
        pid = 7777
        stdout: list[str] = []
        killed = False
        calls = 0

        def poll(self):
            # 第一次调用：仍在运行（stop 的 running 分支成立）；kill 后：已退出
            self.calls += 1
            return None if self.calls == 1 else 0

        def kill(self):
            FakeProc.killed = True

        def wait(self, timeout=None):
            return 0

    session = pw.PWYSession(
        session_id="sess-stop",
        proc=FakeProc(),
        command=["python", "launch_tenhou_bots.py"],
        lobby_id="2147",
        specs=["70k"],
        names=["NoName"],
        speed="normal",
        device="cpu",
        started_at=time.time(),
    )
    monkeypatch.setattr(pw, "_current_session", lambda: session)
    monkeypatch.setattr(pw, "_kill_tree", lambda pid: None)
    monkeypatch.setattr(pw, "_kill_owned_artifacts", lambda: None)
    monkeypatch.setattr(pw, "_session_log_url", lambda sid: None)

    status = pw.stop_playwithyou()

    assert status.running is False
    assert status.termination_reason == "manual_stop"
    assert status.tenhou_log_url is None


def test_status_ordering_prefers_orphan_over_finished_session(monkeypatch):
    """R11-C Repair：finished session 不能遮住仍存活的 owned orphan（保留 Stop 入口）。"""
    import time

    class FakeProc:
        stdout: list[str] = []

        def poll(self):
            return 0  # 已结束

    session = pw.PWYSession(
        session_id="sess-finished",
        proc=FakeProc(),
        command=["python", "launch_tenhou_bots.py"],
        lobby_id="2147",
        specs=["70k"],
        names=["NoName"],
        speed="normal",
        device="cpu",
        started_at=time.time(),
    )
    monkeypatch.setattr(pw, "_current_session", lambda: session)
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [4242])

    status = pw.playwithyou_status()

    # orphan 优先：running=True、无 session_id、有 Stop 提示
    assert status.running is True
    assert status.session_id is None
    assert "遗留" in status.log_tail[0]


def test_session_log_url_only_returns_awaiting_import(monkeypatch, tmp_path):
    """R11-C：_session_log_url 只认 awaiting_import 状态（pending 目录）；其他状态不返回。"""
    from gateway.playwithyou_capture import capture_dir_for_session

    root = tmp_path / "captures"
    capture_dir = capture_dir_for_session(root, "sess-1")
    pending = capture_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "c1.json").write_text(
        json.dumps(
            {
                "capture_id": "c1",
                "session_id": "sess-1",
                "state": "awaiting_import",
                "tenhou_log_url": "https://tenhou.net/3/?log=abc",
            }
        ),
        encoding="utf-8",
    )
    # 非 awaiting_import 状态（如 pending_confirmation）不提供链接
    (pending / "c2.json").write_text(
        json.dumps(
            {
                "capture_id": "c2",
                "session_id": "sess-1",
                "state": "pending_confirmation",
                "tenhou_log_url": "https://tenhou.net/3/?log=def",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pw, "_capture_root", lambda: root / "captures" / "playwithyou")

    assert pw._session_log_url("sess-1") == "https://tenhou.net/3/?log=abc"
    assert pw._session_log_url("sess-other") is None


def test_start_rejects_owned_orphan_without_killing_it(monkeypatch):
    monkeypatch.setattr(pw, "SESSIONS", {})
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [4242])
    killed = []
    monkeypatch.setattr(pw, "_kill_owned_artifacts", lambda: killed.append(True))

    try:
        pw.start_playwithyou(pw.StartPlayWithYouRequest())
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "遗留" in str(exc.detail)
    else:
        raise AssertionError("owned orphan must require an explicit Stop")
    assert killed == []


def test_start_passes_ownership_markers_and_name_prefix(monkeypatch):
    class FakeProc:
        pid = 2468
        stdout = []

        def poll(self):
            return None

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(pw, "SESSIONS", {})
    monkeypatch.setattr(pw, "_HISTORY", [])
    monkeypatch.setattr(pw, "_find_owned_pids", lambda: [])
    monkeypatch.setattr(pw.subprocess, "Popen", fake_popen)

    status = pw.start_playwithyou(
        pw.StartPlayWithYouRequest(name_prefix="NoName", device="cpu")
    )

    assert status.running is True
    command = captured["command"]
    assert ["--session-token", pw.SESSION_TOKEN] == command[
        command.index("--session-token") : command.index("--session-token") + 2
    ]
    assert ["--gateway-owner-token", pw.GATEWAY_OWNER_TOKEN] == command[
        command.index("--gateway-owner-token") : command.index("--gateway-owner-token") + 2
    ]
    assert ["--name-prefix", "NoName"] == command[
        command.index("--name-prefix") : command.index("--name-prefix") + 2
    ]
    assert command[command.index("--bots") + 1] == "mortal"
