from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest import mock

from gateway import settings as gateway_settings

from workbench import launch_tenhou_bots as launcher


def _gateway_args(**overrides):
    values = {
        "gateway_port": gateway_settings.PORT,
        "gateway_port_range_size": gateway_settings.PORT_RANGE_SIZE,
        "gateway_host": "127.0.0.1",
        "gateway_debug": False,
        "gateway_log_dir": "logs",
        "gateway_owner_token": "playwithyou-gateway",
        "tenhou_uri": None,
        "tenhou_origin": None,
        "tenhou_cookie": None,
        "tenhou_helo_json": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_gateway_default_range_avoids_windows_dynamic_ranges() -> None:
    assert gateway_settings.PORT == 21600
    assert gateway_settings.PORT_RANGE_SIZE == 32
    assert gateway_settings.PORT > 15000
    assert gateway_settings.PORT + gateway_settings.PORT_RANGE_SIZE <= 49152


def test_bind_probe_rejects_port_owned_by_non_listening_outbound_socket() -> None:
    """Regression for Edge/proxy using 11600 as an outbound source port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as outbound:
        # Edge's network service showed a Bound 0.0.0.0 entry before the
        # corresponding established proxy connection.  It is not a listener,
        # but it still prevents the gateway from binding the wildcard address.
        outbound.bind(("0.0.0.0", 0))
        source_port = outbound.getsockname()[1]
        assert launcher._can_bind_gateway_port(source_port) is False


def test_owned_gateway_skips_unbindable_candidate(monkeypatch) -> None:
    proc = mock.Mock()
    proc.poll.return_value = None
    started_ports: list[int] = []

    monkeypatch.setattr(
        launcher,
        "_can_bind_gateway_port",
        lambda port: port != gateway_settings.PORT,
    )

    def fake_start_gateway_subprocess(**kwargs):
        started_ports.append(kwargs["port"])
        return proc

    monkeypatch.setattr(launcher, "start_gateway_subprocess", fake_start_gateway_subprocess)
    monkeypatch.setattr(launcher, "_wait_for_gateway_ready", lambda *_args: True)

    selected_proc, selected_port = launcher._start_owned_gateway(
        _gateway_args(gateway_port_range_size=2)
    )

    assert selected_proc is proc
    assert selected_port == gateway_settings.PORT + 1
    assert started_ports == [gateway_settings.PORT + 1]


def test_owned_gateway_retries_when_bind_race_kills_child(monkeypatch) -> None:
    failed = mock.Mock()
    failed.poll.return_value = 10048
    ready = mock.Mock()
    ready.poll.return_value = None
    processes = iter((failed, ready))
    started_ports: list[int] = []

    monkeypatch.setattr(launcher, "_can_bind_gateway_port", lambda _port: True)

    def fake_start_gateway_subprocess(**kwargs):
        started_ports.append(kwargs["port"])
        return next(processes)

    monkeypatch.setattr(launcher, "start_gateway_subprocess", fake_start_gateway_subprocess)
    monkeypatch.setattr(
        launcher,
        "_wait_for_gateway_ready",
        lambda proc, *_args: proc is ready,
    )

    selected_proc, selected_port = launcher._start_owned_gateway(
        _gateway_args(gateway_port_range_size=2)
    )

    assert selected_proc is ready
    assert selected_port == gateway_settings.PORT + 1
    assert started_ports == [gateway_settings.PORT, gateway_settings.PORT + 1]
