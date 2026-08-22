"""Unit tests for ``katagiri.obsidian_launch``.

Nothing here starts a real process or touches the network: ``subprocess.Popen``,
``subprocess.run``, ``shutil.which``, and ``obsidian_is_running`` are all
monkeypatched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from katagiri import obsidian_launch


def test_obsidian_is_running_false_off_windows(monkeypatch):
    monkeypatch.setattr(obsidian_launch.os, "name", "posix")
    assert obsidian_launch.obsidian_is_running() is False


def test_obsidian_is_running_true_when_tasklist_finds_it(monkeypatch):
    monkeypatch.setattr(obsidian_launch.os, "name", "nt")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout='"Obsidian.exe","1234","Console","1","100,000 K"\n', stderr=""
        )

    monkeypatch.setattr(obsidian_launch.subprocess, "run", fake_run)
    assert obsidian_launch.obsidian_is_running() is True


def test_obsidian_is_running_false_when_tasklist_finds_nothing(monkeypatch):
    monkeypatch.setattr(obsidian_launch.os, "name", "nt")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 0, stdout="INFO: No tasks are running which match the specified criteria.\n", stderr=""
        )

    monkeypatch.setattr(obsidian_launch.subprocess, "run", fake_run)
    assert obsidian_launch.obsidian_is_running() is False


def test_obsidian_is_running_false_when_tasklist_unavailable(monkeypatch):
    monkeypatch.setattr(obsidian_launch.os, "name", "nt")

    def boom(argv, **kwargs):
        raise OSError("no tasklist")

    monkeypatch.setattr(obsidian_launch.subprocess, "run", boom)
    assert obsidian_launch.obsidian_is_running() is False


def test_find_obsidian_exe_checks_localappdata(tmp_path, monkeypatch):
    exe = tmp_path / "Obsidian" / "Obsidian.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert obsidian_launch.find_obsidian_exe() == exe


def test_find_obsidian_exe_falls_back_to_which(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(obsidian_launch.shutil, "which", lambda name: "C:/on/path/obsidian.exe")
    assert obsidian_launch.find_obsidian_exe() == Path("C:/on/path/obsidian.exe")


def test_find_obsidian_exe_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(obsidian_launch.shutil, "which", lambda name: None)
    assert obsidian_launch.find_obsidian_exe() is None


def test_launch_obsidian_already_running(monkeypatch):
    monkeypatch.setattr(obsidian_launch, "obsidian_is_running", lambda: True)

    def boom(*a, **k):
        raise AssertionError("must not look for/launch obsidian when already running")

    monkeypatch.setattr(obsidian_launch, "find_obsidian_exe", boom)
    result = obsidian_launch.launch_obsidian()
    assert result == obsidian_launch.LaunchResult(
        launched=False, already_running=True, path=None, reason=None
    )


def test_launch_obsidian_not_found(monkeypatch):
    monkeypatch.setattr(obsidian_launch, "obsidian_is_running", lambda: False)
    monkeypatch.setattr(obsidian_launch, "find_obsidian_exe", lambda: None)
    result = obsidian_launch.launch_obsidian()
    assert result.launched is False
    assert result.already_running is False
    assert result.path is None
    assert "winget" in result.reason


def test_launch_obsidian_success(monkeypatch):
    exe = Path("C:/fake/Obsidian.exe")
    monkeypatch.setattr(obsidian_launch, "obsidian_is_running", lambda: False)
    monkeypatch.setattr(obsidian_launch, "find_obsidian_exe", lambda: exe)

    calls = []

    class FakePopen:
        def __init__(self, argv, cwd=None):
            calls.append((argv, cwd))

    monkeypatch.setattr(obsidian_launch.subprocess, "Popen", FakePopen)
    result = obsidian_launch.launch_obsidian()
    assert result == obsidian_launch.LaunchResult(
        launched=True, already_running=False, path=exe, reason=None
    )
    assert calls == [([str(exe)], str(exe.parent))]


def test_launch_obsidian_popen_oserror(monkeypatch):
    exe = Path("C:/fake/Obsidian.exe")
    monkeypatch.setattr(obsidian_launch, "obsidian_is_running", lambda: False)
    monkeypatch.setattr(obsidian_launch, "find_obsidian_exe", lambda: exe)

    def boom(argv, cwd=None):
        raise OSError("nope")

    monkeypatch.setattr(obsidian_launch.subprocess, "Popen", boom)
    result = obsidian_launch.launch_obsidian()
    assert result.launched is False
    assert result.already_running is False
    assert result.path == exe
    assert result.reason == "nope"
