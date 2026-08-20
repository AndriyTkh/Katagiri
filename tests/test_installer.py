"""Unit tests for the non-interactive logic in ``katagiri.installer``.

The shared venv's editable install maps the ``katagiri`` package to the *main*
checkout's ``src`` (this repo is a git worktree), where ``installer.py``
doesn't exist yet. ``import katagiri.installer`` would therefore fail even
though the file we're testing is right here on disk. Loading it explicitly by
file path sidesteps that: the module executes with this file's real path, so
its own ``from katagiri import config as config_mod`` resolves against
whatever ``katagiri`` package *is* importable (the main checkout), while every
function under test still comes from the copy in this worktree.

Nothing here touches the network, spawns a real subprocess, or writes a real
scheduled task -- ``subprocess.run`` is monkeypatched wherever a step would
otherwise shell out, and every filesystem interaction goes through a temp
``LOCALAPPDATA``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_INSTALLER_PATH = Path(__file__).resolve().parent.parent / "src" / "katagiri" / "installer.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location(
        "katagiri_installer_under_test", _INSTALLER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


def test_module_loads_by_path_and_finds_config():
    """The load-by-path trick works: config functions resolve, no ImportError."""
    assert installer.config_mod.APP_DIR_NAME == "Katagiri"


# ---------------------------------------------------------------------------
# config.toml merge: preserves other keys, never echoes the secret
# ---------------------------------------------------------------------------


def test_set_config_value_replaces_commented_line_in_place():
    text = (
        "# header\n"
        "\n"
        "# vault_path = \"\"\n"
        "\n"
        "# anki_data_dir = \"\"\n"
    )
    updated = installer.set_config_value(text, "vault_path", "C:/Users/me/Vault")
    assert 'vault_path = "C:/Users/me/Vault"' in updated
    # The other key's commented line is untouched.
    assert '# anki_data_dir = ""' in updated
    assert "# header" in updated


def test_set_config_value_appends_when_key_absent():
    text = "vault_path = \"C:/v\"\n"
    updated = installer.set_config_value(text, "obsidian_api_token", "secret-token-value")
    assert 'obsidian_api_token = "secret-token-value"' in updated
    assert 'vault_path = "C:/v"' in updated


def test_set_config_value_escapes_backslashes_safely():
    text = ""
    updated = installer.set_config_value(text, "vault_path", r"C:\Users\me\Vault")
    # json.dumps-style escaping: backslashes doubled inside the TOML string.
    assert r"C:\\Users\\me\\Vault" in updated


def test_apply_config_updates_preserves_unrelated_keys(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '# vault_path = ""\n'
        'db_path = "C:/db.sqlite"\n'
        '# obsidian_api_token = ""\n',
        encoding="utf-8",
    )
    installer.apply_config_updates(cfg_path, {"vault_path": "C:/Users/me/Vault"})
    text = cfg_path.read_text(encoding="utf-8")
    assert 'vault_path = "C:/Users/me/Vault"' in text
    assert 'db_path = "C:/db.sqlite"' in text  # untouched
    assert '# obsidian_api_token = ""' in text  # untouched


def test_apply_config_updates_fails_safe_on_non_utf8_existing_file(tmp_path):
    """YELLOW #4: an undecodable existing config.toml must raise a clear
    InstallerError -- no traceback, and no half-write."""
    cfg_path = tmp_path / "config.toml"
    original_bytes = b'vault_path = "C:/Vault"\n# \x81 stray byte\n'
    cfg_path.write_bytes(original_bytes)

    with pytest.raises(installer.InstallerError):
        installer.apply_config_updates(cfg_path, {"anki_data_dir": "C:/Anki"})

    # Nothing was written: the file is byte-for-byte what it was before.
    assert cfg_path.read_bytes() == original_bytes


def test_step_config_reports_action_needed_instead_of_crashing(tmp_path):
    cfg_dir = tmp_path / "Katagiri"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_bytes(b'vault_path = "C:/Vault"\n# \x81 stray byte\n')

    answers = iter(["C:/new/vault", "", ""])

    def fake_prompt(_msg: str) -> str:
        return next(answers)

    result = installer.step_config(cfg_path, assume_yes=False, prompt=fake_prompt)
    assert result.status == "ACTION NEEDED"
    assert "UTF-8" in result.detail or "utf-8" in result.detail.lower()


def test_apply_config_updates_skips_blank_values(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("vault_path = \"C:/v\"\n", encoding="utf-8")
    installer.apply_config_updates(cfg_path, {"anki_data_dir": ""})
    text = cfg_path.read_text(encoding="utf-8")
    assert "anki_data_dir" not in text


def test_step_config_never_echoes_token_to_stdout(tmp_path, capsys):
    cfg_path = tmp_path / "Katagiri" / "config.toml"
    secret = "sk-super-secret-token-value"

    answers = iter(["", "", secret])  # vault_path, anki_data_dir, token all via plain prompt

    def fake_prompt(_msg: str) -> str:
        return next(answers)

    installer.step_config(cfg_path, assume_yes=False, prompt=fake_prompt)

    out = capsys.readouterr().out
    assert secret not in out
    # But it really was written to the file.
    assert secret in cfg_path.read_text(encoding="utf-8")


def test_step_config_assume_yes_creates_file_without_prompting(tmp_path):
    cfg_path = tmp_path / "Katagiri" / "config.toml"

    def boom(_msg: str) -> str:
        raise AssertionError("must not prompt under --yes")

    result = installer.step_config(cfg_path, assume_yes=True, prompt=boom)
    assert result.status == "OK"
    assert cfg_path.exists()


# ---------------------------------------------------------------------------
# RawConfig parsing: read-only, no side effects
# ---------------------------------------------------------------------------


def test_read_raw_config_missing_file_returns_defaults(tmp_path):
    cfg_path = tmp_path / "Katagiri" / "config.toml"
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.vault_path is None
    assert cfg.anki_data_dir is None
    assert cfg.obsidian_api_token is None
    assert cfg.db_path == cfg_path.parent / "katagiri.db"
    assert not cfg_path.exists()  # reading never creates it


def test_read_raw_config_parses_set_values(tmp_path):
    cfg_dir = tmp_path / "Katagiri"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(
        'vault_path = "C:/Users/me/Vault"\n'
        'obsidian_api_token = "tok-123"\n',
        encoding="utf-8",
    )
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.vault_path == Path("C:/Users/me/Vault")
    assert cfg.obsidian_api_token == "tok-123"
    assert cfg.anki_data_dir is None


def test_read_raw_config_tolerates_broken_toml(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("this is not valid toml [[[", encoding="utf-8")
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.vault_path is None  # falls back to defaults rather than raising


def test_read_raw_config_tolerates_non_utf8_bytes(tmp_path):
    """A config.toml re-saved by a cp1252 editor must not crash --check (YELLOW #3)."""
    cfg_path = tmp_path / "config.toml"
    # 0x81 is undefined in cp1252 and not a valid UTF-8 continuation byte here,
    # so decoding this as UTF-8 raises UnicodeDecodeError.
    cfg_path.write_bytes(b'vault_path = "C:/Vault"\n# \x81 stray byte\n')
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.vault_path is None  # falls back to defaults rather than raising


def test_read_raw_config_nulls_out_a_token_with_control_char(tmp_path):
    """An invalid token must be treated as unset, never handed to a consumer
    that would forward it as an HTTP header (YELLOW #2)."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('obsidian_api_token = "bad\\ntoken"\n', encoding="utf-8")
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.obsidian_api_token is None


def test_read_raw_config_nulls_out_a_non_latin1_token(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('obsidian_api_token = "tok-\u2014-value"\n', encoding="utf-8")
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.obsidian_api_token is None


def test_read_raw_config_keeps_a_valid_token(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('obsidian_api_token = "perfectly-fine-token"\n', encoding="utf-8")
    cfg = installer.read_raw_config(cfg_path)
    assert cfg.obsidian_api_token == "perfectly-fine-token"


# ---------------------------------------------------------------------------
# Secret validation (RED #1 / YELLOW #2)
# ---------------------------------------------------------------------------


def test_validate_secret_value_flags_control_character():
    assert installer.validate_secret_value("bad\ntoken") is not None


def test_validate_secret_value_flags_non_latin1():
    assert installer.validate_secret_value("tok-\u2014-value") is not None


def test_validate_secret_value_accepts_plain_token():
    assert installer.validate_secret_value("plain-ascii-token-123") is None


def test_check_obsidian_connection_with_invalid_token_raises_nothing(monkeypatch, capsys):
    """RED #1: a token with a control char must never reach urlopen (which
    would raise ValueError quoting the raw value), and the value must not
    appear anywhere in the returned message or on stdout/stderr."""
    bad_token = "bad\ntoken\x01with-control-chars"

    def boom(*a, **k):
        raise AssertionError("must not attempt a network call for an invalid token")

    monkeypatch.setattr(installer.urllib.request, "urlopen", boom)

    ok, detail = installer.check_obsidian_connection(bad_token)

    assert ok is False
    assert bad_token not in detail
    captured = capsys.readouterr()
    assert bad_token not in captured.out
    assert bad_token not in captured.err


def test_check_obsidian_connection_catches_valueerror_from_urlopen(monkeypatch):
    """Belt-and-braces path: even if validation somehow let something through,
    a ValueError raised deep inside urlopen must be caught and replaced with a
    generic, value-free message."""
    token = "looks-fine-but-urlopen-blows-up"

    def raise_value_error(*a, **k):
        raise ValueError(f"header value contains secret {token} and a newline")

    monkeypatch.setattr(installer.urllib.request, "urlopen", raise_value_error)

    ok, detail = installer.check_obsidian_connection(token)

    assert ok is False
    assert token not in detail


def test_step_obsidian_reports_action_needed_for_invalid_token(tmp_path):
    cfg = _raw_config(tmp_path)
    # obsidian_api_token bypasses read_raw_config's own sanitizing here, to
    # exercise check_obsidian_connection's independent validation directly.
    object.__setattr__(cfg, "obsidian_api_token", "bad\ntoken")
    result = installer.step_obsidian(cfg)
    assert result.status == "ACTION NEEDED"
    assert "bad\ntoken" not in result.detail


# ---------------------------------------------------------------------------
# Step-skip logic with unset config values
# ---------------------------------------------------------------------------


def _raw_config(tmp_path, **overrides):
    cfg_path = tmp_path / "config.toml"
    base = dict(
        config_file=cfg_path,
        scratch_root=tmp_path / "scratch",
        db_path=tmp_path / "katagiri.db",
        vault_path=None,
        anki_data_dir=None,
        obsidian_api_token=None,
    )
    base.update(overrides)
    return installer.RawConfig(**base)


def test_step_anki_skips_when_anki_data_dir_unset(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run a subprocess when unset")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    result = installer.step_anki(cfg)
    assert result.status == "SKIP"
    assert "AnkiMorphs" in result.detail or "anki_data_dir" in result.detail


def test_step_md_search_skips_when_vault_path_unset(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run a subprocess when unset")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    result = installer.step_md_search(cfg)
    assert result.status == "SKIP"


def test_step_obsidian_skips_when_token_unset(tmp_path):
    cfg = _raw_config(tmp_path)
    result = installer.step_obsidian(cfg)
    assert result.status == "SKIP"


def test_step_schtasks_skipped_under_assume_yes(tmp_path, capsys):
    result = installer.step_schtasks(assume_yes=True)
    assert result.status == "SKIP"


def test_step_schtasks_prints_mpv_conf_line(capsys):
    installer.step_schtasks(assume_yes=True)
    out = capsys.readouterr().out
    assert installer.MPV_CONF_LINE in out


def test_check_pythonw_available_reports_reason_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "_pythonw_path", lambda: tmp_path / "nonexistent-pythonw.exe")
    problem = installer.check_pythonw_available()
    assert problem is not None
    assert "pythonw.exe" in problem


def test_check_pythonw_available_ok_when_present(monkeypatch, tmp_path):
    fake = tmp_path / "pythonw.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(installer, "_pythonw_path", lambda: fake)
    assert installer.check_pythonw_available() is None


def test_step_schtasks_skips_mpv_task_without_prompting_when_pythonw_missing(
    tmp_path, monkeypatch, capsys
):
    """YELLOW #5: a missing pythonw.exe must skip mpv-logger registration with
    ACTION NEEDED, and never even offer to create the dead task."""
    monkeypatch.setattr(installer, "_pythonw_path", lambda: tmp_path / "no-such-pythonw.exe")

    prompts_asked: list[str] = []

    def fake_prompt(msg: str) -> str:
        prompts_asked.append(msg)
        return "y"  # would create every task it's asked about

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_schtasks(assume_yes=False, prompt=fake_prompt)

    assert not any("mpv seek logger" in msg for msg in prompts_asked)
    assert "pythonw.exe" in result.detail
    out = capsys.readouterr().out
    assert "ACTION NEEDED" in out
    # The other two tasks were still offered and created normally.
    assert result.status == "OK"
    assert "created" in result.detail


# ---------------------------------------------------------------------------
# Subprocess command construction
# ---------------------------------------------------------------------------


def test_jmdict_import_argv_uses_current_interpreter():
    argv = installer.jmdict_import_argv()
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "katagiri.jmdict_import", "all"]


def test_anki_sync_argv():
    assert installer.anki_sync_argv() == [sys.executable, "-m", "katagiri.anki_sync", "run"]


def test_fts_index_argv():
    assert installer.fts_index_argv() == [sys.executable, "-m", "katagiri.fts_index", "rebuild"]


def test_md_search_argv_includes_root():
    argv = installer.md_search_argv(Path("C:/Vault"))
    assert argv == [
        sys.executable,
        "-m",
        "katagiri.md_search",
        "rebuild",
        "--root",
        str(Path("C:/Vault")),
    ]


def test_backup_verify_argv_includes_path():
    argv = installer.backup_verify_argv("C:/backups/katagiri.20260101T000000.db")
    assert argv[-1] == "C:/backups/katagiri.20260101T000000.db"
    assert argv[-2] == "verify"


def test_step_jmdict_runs_subprocess_when_not_imported(tmp_path, monkeypatch):
    db_path = tmp_path / "katagiri.db"  # doesn't exist -> _ro_query_scalar returns None
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_jmdict(db_path)

    assert result.status == "OK"
    assert captured["argv"] == [sys.executable, "-m", "katagiri.jmdict_import", "all"]
    assert captured["env"]["PYTHONUTF8"] == "1"


def test_step_jmdict_skips_subprocess_when_already_imported(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "katagiri.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jmdict_entry (id INTEGER)")
    conn.execute("INSERT INTO jmdict_entry VALUES (1)")
    conn.commit()
    conn.close()

    def boom(*a, **k):
        raise AssertionError("must not re-import when already populated")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    result = installer.step_jmdict(db_path)
    assert result.status == "OK"
    assert "already imported" in result.detail


def test_step_backup_parses_snapshot_path_and_verifies(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "create":
            return subprocess.CompletedProcess(
                argv, 0, stdout="database snapshot: C:/backups/katagiri.snap.db\n", stderr=""
            )
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_backup()

    assert result.status == "OK"
    assert calls[0][-1] == "create"
    assert calls[1][-2:] == ["verify", "C:/backups/katagiri.snap.db"]


def test_step_backup_reports_action_needed_when_create_fails(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="disk full")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_backup()
    assert result.status == "ACTION NEEDED"
    assert "disk full" in result.detail


def test_parse_backup_snapshot_path():
    stdout = "database snapshot: C:/x/y.db\nvault snapshot:    C:/x/z.zip\n"
    assert installer._parse_backup_snapshot_path(stdout) == "C:/x/y.db"


def test_parse_backup_snapshot_path_missing_line():
    assert installer._parse_backup_snapshot_path("nothing here\n") is None


# ---------------------------------------------------------------------------
# Doctor state detection against a temp LOCALAPPDATA
# ---------------------------------------------------------------------------


def test_probe_config_missing(tmp_path):
    status = installer.probe_config(tmp_path / "config.toml")
    assert status.status == "MISSING"


def test_probe_config_ready(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    status = installer.probe_config(cfg_path)
    assert status.status == "READY"


def test_probe_jmdict_missing_when_db_absent(tmp_path):
    status = installer.probe_jmdict(tmp_path / "katagiri.db")
    assert status.status == "MISSING"


def test_probe_jmdict_ready_when_rows_present(tmp_path):
    import sqlite3

    db_path = tmp_path / "katagiri.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE jmdict_entry (id INTEGER)")
    conn.execute("INSERT INTO jmdict_entry VALUES (1)")
    conn.commit()
    conn.close()
    status = installer.probe_jmdict(db_path)
    assert status.status == "READY"


def test_probe_anki_manual_step_when_unset(tmp_path):
    cfg = _raw_config(tmp_path)
    status = installer.probe_anki(cfg)
    assert status.status == "MANUAL STEP"


def test_probe_anki_missing_when_set_but_not_synced(tmp_path):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "anki")
    status = installer.probe_anki(cfg)
    assert status.status == "MISSING"


def test_probe_md_manual_step_when_vault_unset(tmp_path):
    cfg = _raw_config(tmp_path)
    status = installer.probe_md(cfg)
    assert status.status == "MANUAL STEP"


def test_probe_backup_missing_when_no_snapshots(tmp_path):
    cfg = _raw_config(tmp_path)
    status = installer.probe_backup(cfg)
    assert status.status == "MISSING"


def test_probe_backup_ready_when_snapshot_exists(tmp_path):
    cfg_path = tmp_path / "config.toml"
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    (backups_dir / "katagiri.20260101T000000.db").write_bytes(b"x")
    cfg = _raw_config(tmp_path, config_file=cfg_path)
    status = installer.probe_backup(cfg)
    assert status.status == "READY"


def test_probe_yomitan_missing_when_no_dict(tmp_path):
    cfg = _raw_config(tmp_path)
    status = installer.probe_yomitan(cfg)
    assert status.status == "MISSING"


def test_probe_yomitan_ready_when_dict_present(tmp_path):
    out_dir = tmp_path / "scratch" / "yomitan"
    out_dir.mkdir(parents=True)
    (out_dir / "katagiri-known-2026-01-01-5.zip").write_bytes(b"x")
    cfg = _raw_config(tmp_path)
    status = installer.probe_yomitan(cfg)
    assert status.status == "READY"


def test_doctor_exit_code_zero_when_nothing_missing():
    statuses = [
        installer.ComponentStatus("a", "READY"),
        installer.ComponentStatus("b", "MANUAL STEP"),
    ]
    assert installer.doctor_exit_code(statuses) == 0


def test_doctor_exit_code_one_when_something_missing():
    statuses = [
        installer.ComponentStatus("a", "READY"),
        installer.ComponentStatus("b", "MISSING"),
    ]
    assert installer.doctor_exit_code(statuses) == 1


# ---------------------------------------------------------------------------
# --check: read-only, no prompts, correct exit codes
# ---------------------------------------------------------------------------


def test_check_exits_nonzero_on_fresh_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer.config_mod.reset_config_cache()

    def boom(*a, **k):
        raise AssertionError("must not prompt under --check")

    monkeypatch.setattr("builtins.input", boom)

    exit_code = installer.main(["--check"])

    assert exit_code == 1
    # --check must not create the config file it just reported as missing.
    assert not (tmp_path / "Katagiri" / "config.toml").exists()


def test_check_does_not_invoke_subprocess_run_for_module_steps(tmp_path, monkeypatch):
    """--check only ever shells out to read-only probes (schtasks /Query),
    never to a step that would import/sync/rebuild anything."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    installer.config_mod.reset_config_cache()

    seen = []
    real_run = subprocess.run

    def spying_run(argv, *a, **k):
        seen.append(argv)
        return real_run(argv, *a, **k)

    monkeypatch.setattr(installer.subprocess, "run", spying_run)
    installer.main(["--check"])

    for argv in seen:
        assert argv[:2] != [sys.executable, "-m"], f"unexpected module invocation: {argv}"
