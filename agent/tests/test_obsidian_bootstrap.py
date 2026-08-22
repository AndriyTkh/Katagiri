"""Tests for ``katagiri_agent.obsidian_bootstrap`` (dev convenience only).

No real socket, process, or Obsidian install involved -- every check below
monkeypatches the module's own small stdlib wrappers, never the stdlib
itself, so these run anywhere (including CI with nothing named Obsidian on
the machine).
"""

from __future__ import annotations

import pytest

from katagiri_agent import obsidian_bootstrap as boot


def test_already_up_returns_without_launching(monkeypatch):
    monkeypatch.setattr(boot, "_endpoint_up", lambda host, port: True)
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: (_ for _ in ()).throw(AssertionError("should not check")))

    result = boot.ensure_ready("https://127.0.0.1:27124/mcp/")

    assert result.already_up is True
    assert result.launched is False


def test_running_but_endpoint_not_up_yet_waits_without_relaunching(monkeypatch):
    calls = {"popen": 0}
    # First call (pre-check) sees it down, then flips up on the poll loop.
    seq = iter([False, True])
    monkeypatch.setattr(boot, "_endpoint_up", lambda host, port: next(seq))
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: True)
    monkeypatch.setattr(boot.time, "sleep", lambda s: None)
    monkeypatch.setattr(boot.subprocess, "Popen", lambda *a, **k: calls.__setitem__("popen", calls["popen"] + 1))

    result = boot.ensure_ready("https://127.0.0.1:27124/mcp/", timeout=5)

    assert result.already_up is False
    assert result.launched is False
    assert calls["popen"] == 0


def test_not_running_and_exe_found_launches_then_waits(monkeypatch, tmp_path):
    exe = tmp_path / "Obsidian.exe"
    exe.write_bytes(b"")
    seq = iter([False, True])
    monkeypatch.setattr(boot, "_endpoint_up", lambda host, port: next(seq))
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: False)
    monkeypatch.setattr(boot, "_find_obsidian_exe", lambda: exe)
    monkeypatch.setattr(boot.time, "sleep", lambda s: None)
    launched = {}
    monkeypatch.setattr(
        boot.subprocess,
        "Popen",
        lambda args, cwd=None: launched.update(args=args, cwd=cwd),
    )

    result = boot.ensure_ready("https://127.0.0.1:27124/mcp/", timeout=5)

    assert result.launched is True
    assert launched["args"] == [str(exe)]


def test_not_running_and_exe_missing_raises_with_winget_hint(monkeypatch):
    monkeypatch.setattr(boot, "_endpoint_up", lambda host, port: False)
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: False)
    monkeypatch.setattr(boot, "_find_obsidian_exe", lambda: None)

    with pytest.raises(boot.ObsidianNotReady, match="winget"):
        boot.ensure_ready("https://127.0.0.1:27124/mcp/")


def test_launched_but_endpoint_never_comes_up_times_out(monkeypatch, tmp_path):
    exe = tmp_path / "Obsidian.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(boot, "_endpoint_up", lambda host, port: False)
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: False)
    monkeypatch.setattr(boot, "_find_obsidian_exe", lambda: exe)
    monkeypatch.setattr(boot.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(boot.time, "sleep", lambda s: None)

    with pytest.raises(boot.ObsidianNotReady, match="27124"):
        boot.ensure_ready("https://127.0.0.1:27124/mcp/", timeout=0.01)


def test_url_without_explicit_port_defaults_to_27124(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        boot,
        "_endpoint_up",
        lambda host, port: seen.update(host=host, port=port) or True,
    )
    monkeypatch.setattr(boot, "_obsidian_is_running", lambda: (_ for _ in ()).throw(AssertionError))

    boot.ensure_ready("https://127.0.0.1/mcp/")

    assert seen == {"host": "127.0.0.1", "port": 27124}
