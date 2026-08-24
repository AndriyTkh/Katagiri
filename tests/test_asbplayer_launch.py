"""Focused contract tests for managed asbplayer bridge startup.

009 replaced the Go-spawn launcher with one that starts the bridge
in-process (:mod:`katagiri.asbplayer_bridge`). Five of the seven pre-009 tests
asserted Go-specific behavior (``main.go`` probing, Go-on-PATH, the
``["go", "run", "main.go"]`` argv) that no longer exists; only reuse-healthy
and leave-occupied-alone survive, re-asserted against the new implementation.

Every test that actually starts a server binds on an **ephemeral** port,
injected via the same environment surface
:meth:`katagiri.asbplayer_bridge.BridgeConfig.from_env` reads — never the real
8766 (binding rule 4, research.md O-2) — and stops it again in a ``finally``
so no listener leaks between tests.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from katagiri import asbplayer_launch


def _configured(path: Path | None) -> SimpleNamespace:
    return SimpleNamespace(asbplayer_bridge_dir=path)


@pytest.fixture(autouse=True)
def _no_leaked_bridge():
    """Belt-and-suspenders: stop whatever a test started, even on failure."""
    yield
    asbplayer_launch.stop_asbplayer_bridge()


def test_healthy_bridge_is_reused_without_starting_a_process(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: True)

    def boom(*args, **kwargs):
        raise AssertionError("a healthy bridge must not be started again")

    monkeypatch.setattr(asbplayer_launch, "get_config", boom)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result == asbplayer_launch.BridgeLaunchResult(
        launched=False, already_running=True, bridge_dir=None, reason=None
    )


def test_occupied_unhealthy_port_is_left_untouched(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: True)

    def boom(*args, **kwargs):
        raise AssertionError("an occupied bridge port must not get a second server")

    monkeypatch.setattr(asbplayer_launch, "get_config", boom)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is False
    assert result.already_running is False
    assert result.bridge_dir is None
    assert "already occupied" in result.reason


def test_bridge_starts_in_process_on_an_ephemeral_port(monkeypatch):
    """Reuse-healthy and leave-occupied-alone both say no; the real start path
    binds a real (ephemeral, injected) socket via the in-process bridge."""
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(None))
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_PORT", "0")

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result == asbplayer_launch.BridgeLaunchResult(
        launched=True, already_running=False, bridge_dir=None, reason=None
    )
    assert asbplayer_launch._server is not None
    assert asbplayer_launch._server.is_running
    # A real, distinct port was assigned by the OS -- never the well-known 8766.
    assert asbplayer_launch._server.port != 8766


def test_bridge_binds_loopback_by_default(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(None))
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_PORT", "0")

    asbplayer_launch.ensure_asbplayer_bridge()

    assert asbplayer_launch._server.host == "127.0.0.1"


def test_bridge_honors_host_override_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(None))
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_PORT", "0")
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_HOST", "0.0.0.0")

    with caplog.at_level("WARNING"):
        result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is True
    assert asbplayer_launch._server.host == "0.0.0.0"
    assert any(
        "NON-LOOPBACK" in record.message or "non-loopback" in record.message.lower()
        for record in caplog.records
    )


def test_stop_releases_the_port(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(None))
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_PORT", "0")

    asbplayer_launch.ensure_asbplayer_bridge()
    host, port = asbplayer_launch._server.host, asbplayer_launch._server.port

    asbplayer_launch.stop_asbplayer_bridge()

    assert asbplayer_launch._server is None
    # The OS must have the port back: a fresh bind on it must succeed.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, port))


def test_obsolete_bridge_dir_is_reported_and_not_honored(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(tmp_path))
    monkeypatch.setenv("KATAGIRI_ASBPLAYER_BRIDGE_PORT", "0")

    with caplog.at_level("WARNING"):
        result = asbplayer_launch.ensure_asbplayer_bridge()

    # The bridge starts normally regardless of what asbplayer_bridge_dir says.
    assert result.launched is True
    assert result.bridge_dir is None
    assert any(
        "asbplayer_bridge_dir" in record.message and "obsolete" in record.message
        for record in caplog.records
    )
    # And nothing in tmp_path (e.g. a main.go probe) was ever touched.
    assert list(tmp_path.iterdir()) == []


def test_module_contains_no_go_toolchain_or_subprocess_machinery():
    """FR-013 / SC-008: no Go toolchain lookup, no child process, anywhere.

    Checks concrete constructs the old Go-spawn launcher used, not the word
    "Go" in prose -- this module's own docstrings legitimately explain what it
    replaced.
    """
    source = Path(asbplayer_launch.__file__).read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "import shutil" not in source
    assert "subprocess." not in source
    assert "shutil." not in source
    assert "main.go" not in source
    assert "CREATE_NO_WINDOW" not in source
    # The Go-on-PATH lookup and the ["go", "run", "main.go"] argv.
    assert re.search(r'["\']go["\']', source) is None
    assert "go run" not in source
