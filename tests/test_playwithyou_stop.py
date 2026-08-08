"""Regression tests for Play-with-you session stop / orphan cleanup.

These guard the fix that makes Stop reliable even after a backend restart:
* ``_find_owned_pids`` discovers only launcher/gateway processes carrying the
  dedicated Play-with-you ownership marker, without depending on the in-memory
  SESSIONS dict.
* ``/stop`` always mops up owned orphaned bot artifacts, not just a tracked session.
* ``/status`` surfaces an orphaned launcher so the GUI keeps showing the Stop
  button even when the backend lost its session record.
"""
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
