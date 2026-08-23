"""Focused contract tests for managed asbplayer bridge startup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from katagiri import asbplayer_launch


def _configured(path: Path | None) -> SimpleNamespace:
    return SimpleNamespace(asbplayer_bridge_dir=path)


def test_healthy_bridge_is_reused_without_starting_a_process(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: True)

    def boom(*args, **kwargs):
        raise AssertionError("a healthy bridge must not be started again")

    monkeypatch.setattr(asbplayer_launch, "get_config", boom)
    monkeypatch.setattr(asbplayer_launch.subprocess, "Popen", boom)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result == asbplayer_launch.BridgeLaunchResult(
        launched=False, already_running=True, bridge_dir=None, reason=None
    )


def test_occupied_unhealthy_port_is_left_untouched(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: True)

    def boom(*args, **kwargs):
        raise AssertionError("an occupied bridge port must not get a second server")

    monkeypatch.setattr(asbplayer_launch.subprocess, "Popen", boom)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is False
    assert result.already_running is False
    assert "already occupied" in result.reason


def test_unconfigured_bridge_returns_the_config_key(monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(None))

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is False
    assert result.already_running is False
    assert "asbplayer_bridge_dir" in result.reason


def test_missing_main_go_is_not_started(tmp_path, monkeypatch):
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(tmp_path))

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is False
    assert result.bridge_dir == tmp_path
    assert "main.go" in result.reason


def test_missing_go_is_actionable(tmp_path, monkeypatch):
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(tmp_path))
    monkeypatch.setattr(asbplayer_launch.shutil, "which", lambda name: None)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result.launched is False
    assert "Go" in result.reason


def test_configured_bridge_starts_hidden_from_its_own_directory(tmp_path, monkeypatch):
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(tmp_path))
    monkeypatch.setattr(asbplayer_launch.shutil, "which", lambda name: "C:/Go/bin/go.exe")
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

    monkeypatch.setattr(asbplayer_launch.subprocess, "Popen", FakePopen)

    result = asbplayer_launch.ensure_asbplayer_bridge()

    assert result == asbplayer_launch.BridgeLaunchResult(
        launched=True, already_running=False, bridge_dir=tmp_path, reason=None
    )
    assert calls[0][0] == ["C:/Go/bin/go.exe", "run", "main.go"]
    assert calls[0][1]["cwd"] == str(tmp_path)
    assert calls[0][1]["stdout"] is asbplayer_launch.subprocess.DEVNULL
    assert calls[0][1]["stderr"] is asbplayer_launch.subprocess.DEVNULL
    assert calls[0][1]["env"]["HOST"] == "127.0.0.1"


def test_configured_bridge_launch_preserves_explicit_host_env(tmp_path, monkeypatch):
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    monkeypatch.setattr(asbplayer_launch, "bridge_is_healthy", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "bridge_port_is_occupied", lambda: False)
    monkeypatch.setattr(asbplayer_launch, "get_config", lambda: _configured(tmp_path))
    monkeypatch.setattr(asbplayer_launch.shutil, "which", lambda name: "C:/Go/bin/go.exe")
    monkeypatch.setenv("HOST", "0.0.0.0")
    calls = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

    monkeypatch.setattr(asbplayer_launch.subprocess, "Popen", FakePopen)

    asbplayer_launch.ensure_asbplayer_bridge()

    assert calls[0][1]["env"]["HOST"] == "0.0.0.0"
