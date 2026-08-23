"""Unit tests for ``katagiri.anki_launch``.

Nothing here starts a real process or touches the network: ``subprocess.Popen``,
``shutil.which``, and ``anki_is_running`` are all monkeypatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from katagiri import anki_launch


def test_find_anki_exe_checks_localappdata(tmp_path, monkeypatch):
    exe = tmp_path / "Programs" / "Anki" / "anki.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    assert anki_launch.find_anki_exe() == exe


def test_find_anki_exe_checks_program_files(tmp_path, monkeypatch):
    exe = tmp_path / "Anki" / "anki.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    assert anki_launch.find_anki_exe() == exe


def test_find_anki_exe_falls_back_to_which(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setattr(anki_launch.shutil, "which", lambda name: "C:/on/path/anki.exe")
    assert anki_launch.find_anki_exe() == Path("C:/on/path/anki.exe")


def test_find_anki_exe_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setattr(anki_launch.shutil, "which", lambda name: None)
    assert anki_launch.find_anki_exe() is None


def test_find_anki_exe_does_not_create_config(tmp_path, monkeypatch):
    """``find_anki_exe`` sits on the ``installer --check`` read-only path
    (via ``_anki_manual_step_detail``); it must never write ``config.toml``
    as a side effect of looking up ``anki_exe_path``."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.setattr(anki_launch.shutil, "which", lambda name: None)
    from katagiri import config as config_mod

    config_mod.reset_config_cache()

    anki_launch.find_anki_exe()

    assert not (tmp_path / "Katagiri" / "config.toml").exists()


def test_launch_anki_already_running(monkeypatch):
    monkeypatch.setattr(anki_launch, "anki_is_running", lambda: True)

    def boom(*a, **k):
        raise AssertionError("must not look for/launch anki when already running")

    monkeypatch.setattr(anki_launch, "find_anki_exe", boom)
    result = anki_launch.launch_anki()
    assert result == anki_launch.LaunchResult(
        launched=False, already_running=True, path=None, reason=None
    )


def test_launch_anki_not_found(monkeypatch):
    monkeypatch.setattr(anki_launch, "anki_is_running", lambda: False)
    monkeypatch.setattr(anki_launch, "find_anki_exe", lambda: None)
    result = anki_launch.launch_anki()
    assert result.launched is False
    assert result.already_running is False
    assert result.path is None
    assert "winget" in result.reason


def test_launch_anki_success(monkeypatch):
    exe = Path("C:/fake/anki.exe")
    monkeypatch.setattr(anki_launch, "anki_is_running", lambda: False)
    monkeypatch.setattr(anki_launch, "find_anki_exe", lambda: exe)

    calls = []

    class FakePopen:
        def __init__(self, argv, cwd=None):
            calls.append((argv, cwd))

    monkeypatch.setattr(anki_launch.subprocess, "Popen", FakePopen)
    result = anki_launch.launch_anki()
    assert result == anki_launch.LaunchResult(
        launched=True, already_running=False, path=exe, reason=None
    )
    assert calls == [([str(exe)], str(exe.parent))]


def test_launch_anki_popen_oserror(monkeypatch):
    exe = Path("C:/fake/anki.exe")
    monkeypatch.setattr(anki_launch, "anki_is_running", lambda: False)
    monkeypatch.setattr(anki_launch, "find_anki_exe", lambda: exe)

    def boom(argv, cwd=None):
        raise OSError("nope")

    monkeypatch.setattr(anki_launch.subprocess, "Popen", boom)
    result = anki_launch.launch_anki()
    assert result.launched is False
    assert result.already_running is False
    assert result.path == exe
    assert result.reason == "nope"
