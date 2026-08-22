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

from katagiri import db as katagiri_db

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
    """RED #1: a token with a control char must never reach obsidian_proxy
    (which would attempt an actual request), and the value must not appear
    anywhere in the returned message or on stdout/stderr."""
    bad_token = "bad\ntoken\x01with-control-chars"

    import katagiri.obsidian_proxy as obsidian_proxy

    def boom(*a, **k):
        raise AssertionError("must not attempt a network call for an invalid token")

    monkeypatch.setattr(obsidian_proxy, "list_vault_dir", boom)

    ok, detail = installer.check_obsidian_connection(bad_token)

    assert ok is False
    assert bad_token not in detail
    captured = capsys.readouterr()
    assert bad_token not in captured.out
    assert bad_token not in captured.err


def test_check_obsidian_connection_uses_the_obsidian_proxy_seam(monkeypatch):
    """The bridge check must go through ``katagiri.obsidian_proxy`` -- the
    package's one allowed HTTP client -- rather than opening a socket itself.
    Mocking the proxy's ``list_vault_dir`` and asserting on its return value
    is what replaces the old direct ``urlopen`` mock now that installer.py
    has no HTTP client of its own (see test_bverify.py /
    test_only_the_obsidian_proxy_is_an_http_client, which this file must not
    break)."""
    token = "looks-fine-token"

    import katagiri.obsidian_proxy as obsidian_proxy

    calls = []

    def fake_list_vault_dir():
        calls.append(True)
        return {
            "ok": True,
            "status": 200,
            "error": None,
            "note": "",
            "files": [],
            "file_count": 0,
            "truncated": False,
            "path": "",
        }

    monkeypatch.setattr(obsidian_proxy, "list_vault_dir", fake_list_vault_dir)

    ok, detail = installer.check_obsidian_connection(token)

    assert ok is True
    assert "200" in detail
    assert calls == [True]


def test_check_obsidian_connection_passes_through_a_proxy_failure_note(monkeypatch):
    """Belt-and-braces path: ``obsidian_proxy`` itself absorbs a header-safety
    failure (or any other request failure) into a fixed, value-free note --
    that is asserted against ``obsidian_proxy`` directly in
    tests/test_obsidian_proxy.py. All this checks is that the installer
    surfaces that note unchanged rather than losing it or re-deriving its own,
    and that the token never appears in it even if the mock were sloppy."""
    token = "looks-fine-but-proxy-fails"

    import katagiri.obsidian_proxy as obsidian_proxy

    def fake_list_vault_dir():
        return {
            "ok": False,
            "status": None,
            "error": obsidian_proxy.UNCONFIGURED,
            "note": obsidian_proxy.TOKEN_UNUSABLE_NOTE,
            "files": [],
            "file_count": 0,
            "truncated": False,
            "path": "",
        }

    monkeypatch.setattr(obsidian_proxy, "list_vault_dir", fake_list_vault_dir)

    ok, detail = installer.check_obsidian_connection(token)

    assert ok is False
    assert token not in detail
    assert detail == obsidian_proxy.TOKEN_UNUSABLE_NOTE


def test_step_obsidian_reports_action_needed_for_invalid_token(tmp_path, monkeypatch):
    import katagiri.obsidian_launch as obsidian_launch_mod

    monkeypatch.setattr(
        obsidian_launch_mod,
        "launch_obsidian",
        lambda: obsidian_launch_mod.LaunchResult(
            launched=False, already_running=True, path=None, reason=None
        ),
    )
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


def test_step_anki_skips_when_anki_not_found(tmp_path, monkeypatch):
    import katagiri.anki_launch as anki_launch_mod

    cfg = _raw_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run a subprocess when unset")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    monkeypatch.setattr(installer, "_detect_anki_data_dir", lambda: None)
    monkeypatch.setattr(anki_launch_mod, "find_anki_exe", lambda: None)
    result = installer.step_anki(cfg)
    assert result.status == "SKIP"
    assert "winget" in result.detail.lower()


def test_step_anki_skips_when_anki_installed_but_no_profile(tmp_path, monkeypatch):
    import katagiri.anki_launch as anki_launch_mod

    cfg = _raw_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run a subprocess when unset")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    monkeypatch.setattr(installer, "_detect_anki_data_dir", lambda: None)
    monkeypatch.setattr(
        anki_launch_mod, "find_anki_exe", lambda: Path("C:/fake/anki.exe")
    )
    result = installer.step_anki(cfg)
    assert result.status == "SKIP"
    assert "AnkiMorphs" in result.detail


def test_step_anki_auto_detects_and_persists_anki_data_dir(tmp_path, monkeypatch):
    import katagiri.anki_launch as anki_launch_mod

    cfg = _raw_config(tmp_path)
    installer.config_mod.write_default_config(cfg.config_file)
    detected = tmp_path / "Anki2"
    monkeypatch.setattr(installer, "_detect_anki_data_dir", lambda: detected)

    launch_calls = []
    monkeypatch.setattr(
        anki_launch_mod,
        "launch_anki",
        lambda: launch_calls.append(True)
        or anki_launch_mod.LaunchResult(
            launched=False, already_running=True, path=None, reason=None
        ),
    )

    calls = []

    def fake_run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "_run", fake_run)
    result = installer.step_anki(cfg)

    assert result.status == "OK"
    assert calls == [installer.anki_sync_argv()]
    assert launch_calls == [True]
    saved = installer.read_raw_config(cfg.config_file)
    assert saved.anki_data_dir == detected


def test_maybe_downgrade_anki_returns_none_without_ankimorphs(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")

    def boom(*a, **k):
        raise AssertionError("must not check Anki's version without AnkiMorphs installed")

    monkeypatch.setattr(installer, "_installed_anki_version", boom)
    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=boom)
    assert detail is None


def _write_camel_wrapper(anki_data_dir: Path, addon_folder: str = "472573498") -> None:
    wrapper = anki_data_dir / "addons21" / addon_folder / "morphemizers" / "camel_wrapper.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("from aqt.package import venv_binary\n")


def test_maybe_downgrade_anki_returns_none_when_version_unknown(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: None)

    def boom(*a, **k):
        raise AssertionError("must not prompt when the version can't be determined")

    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=boom)
    assert detail is None


def test_maybe_downgrade_anki_returns_none_when_already_supported(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: (26, 5, 3))

    def boom(*a, **k):
        raise AssertionError("must not prompt when Anki is already at/below the supported version")

    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=boom)
    assert detail is None


def test_maybe_downgrade_anki_declines_without_running_winget_install(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: (26, 8, 1))

    def boom(*a, **k):
        raise AssertionError("must not run winget install when the user declines")

    monkeypatch.setattr(installer, "_run", boom)
    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=lambda _: "n")
    assert detail == "AnkiMorphs needs Anki <= 26.05; downgrade declined"


def test_maybe_downgrade_anki_treats_eof_as_decline(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: (26, 8, 1))

    def raise_eof(_):
        raise EOFError

    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=raise_eof)
    assert detail == "AnkiMorphs needs Anki <= 26.05; downgrade declined"


def test_maybe_downgrade_anki_runs_winget_install_on_yes(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: (26, 8, 1))

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "_run", fake_run)
    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=lambda _: "y")
    assert detail == "downgraded Anki to 26.05 for AnkiMorphs"
    assert calls == [
        [
            "winget", "install", "--id", "Anki.Anki", "-e",
            "--version", "26.05",
            "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements",
        ]
    ]


def test_maybe_downgrade_anki_reports_winget_install_failure(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    _write_camel_wrapper(cfg.anki_data_dir)
    monkeypatch.setattr(installer, "_installed_anki_version", lambda: (26, 8, 1))

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="access denied")

    monkeypatch.setattr(installer, "_run", fake_run)
    detail = installer._maybe_downgrade_anki_for_ankimorphs(cfg, prompt=lambda _: "y")
    assert detail == "Anki downgrade to 26.05 failed: access denied"


def test_installed_anki_version_parses_winget_list_output(monkeypatch):
    def fake_run(argv, **kwargs):
        assert argv == ["winget", "list", "--id", "Anki.Anki", "--exact"]
        return subprocess.CompletedProcess(
            argv, 0, stdout="Name  Id        Version Source\nAnki  Anki.Anki 26.08.1 winget\n", stderr=""
        )

    monkeypatch.setattr(installer, "_run", fake_run)
    assert installer._installed_anki_version() == (26, 8, 1)


def test_installed_anki_version_none_when_winget_unavailable(monkeypatch):
    def boom(*a, **k):
        raise OSError("winget not found")

    monkeypatch.setattr(installer, "_run", boom)
    assert installer._installed_anki_version() is None


def test_step_anki_folds_downgrade_detail_into_result(tmp_path, monkeypatch):
    import katagiri.anki_launch as anki_launch_mod

    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "Anki2")
    (cfg.anki_data_dir).mkdir()
    monkeypatch.setattr(anki_launch_mod, "launch_anki", lambda: anki_launch_mod.LaunchResult(
        launched=False, already_running=True, path=None, reason=None
    ))
    monkeypatch.setattr(
        installer, "_maybe_downgrade_anki_for_ankimorphs", lambda cfg, prompt: "downgraded Anki to 26.05 for AnkiMorphs"
    )

    def fake_run(argv):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "_run", fake_run)
    result = installer.step_anki(cfg)
    assert result.status == "OK"
    assert result.detail == "synced; downgraded Anki to 26.05 for AnkiMorphs"


def test_step_md_search_skips_when_vault_path_unset(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path)

    def boom(*a, **k):
        raise AssertionError("must not run a subprocess when unset")

    monkeypatch.setattr(installer.subprocess, "run", boom)
    result = installer.step_md_search(cfg)
    assert result.status == "SKIP"


def test_step_search_indexes_runs_stamp_before_fts_before_md(tmp_path, monkeypatch):
    """On a fresh DB, fts_index/md_search rebuild raise VersionsNotStampedError
    until ``katagiri.tokenizer stamp`` has run once; the combined search-index
    step must therefore call stamp first, and only then the two rebuilds, in
    that order."""
    cfg = _raw_config(tmp_path, vault_path=tmp_path / "vault")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_search_indexes(cfg)

    assert result.status == "OK"
    assert len(calls) == 3
    assert calls[0] == installer.tokenizer_stamp_argv()
    assert calls[1] == installer.fts_index_argv()
    assert calls[2] == installer.md_search_argv(cfg.vault_path)


def test_step_search_indexes_skips_both_rebuilds_when_stamp_fails(tmp_path, monkeypatch):
    """If stamping fails (e.g. the vendored dictionary is missing), neither
    rebuild is attempted -- both would fail the same way, less usefully."""
    cfg = _raw_config(tmp_path, vault_path=tmp_path / "vault")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "stamp":
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="vendor missing")
        raise AssertionError("must not rebuild an index when stamping failed")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    result = installer.step_search_indexes(cfg)

    assert result.status == "ACTION NEEDED"
    assert "vendor missing" in result.detail
    assert len(calls) == 1
    assert calls[0] == installer.tokenizer_stamp_argv()


def test_step_obsidian_skips_when_token_unset(tmp_path, monkeypatch):
    import katagiri.obsidian_launch as obsidian_launch_mod

    monkeypatch.setattr(obsidian_launch_mod, "find_obsidian_exe", lambda: None)
    cfg = _raw_config(tmp_path)
    result = installer.step_obsidian(cfg)
    assert result.status == "SKIP"


def test_step_obsidian_launches_obsidian_and_notes_when_not_found(tmp_path, monkeypatch):
    import katagiri.obsidian_launch as obsidian_launch_mod

    monkeypatch.setattr(obsidian_launch_mod, "obsidian_is_running", lambda: False)
    monkeypatch.setattr(obsidian_launch_mod, "find_obsidian_exe", lambda: None)
    cfg = _raw_config(tmp_path)
    result = installer.step_obsidian(cfg)
    assert result.status == "SKIP"
    assert "winget" in result.detail.lower()


def test_step_obsidian_launches_obsidian_when_found(tmp_path, monkeypatch):
    import katagiri.obsidian_launch as obsidian_launch_mod

    calls = []
    monkeypatch.setattr(
        obsidian_launch_mod,
        "launch_obsidian",
        lambda: calls.append(True)
        or obsidian_launch_mod.LaunchResult(
            launched=True, already_running=False, path=Path("C:/fake/Obsidian.exe"), reason=None
        ),
    )
    cfg = _raw_config(tmp_path)
    result = installer.step_obsidian(cfg)
    assert calls == [True]
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


def test_tokenizer_stamp_argv():
    assert installer.tokenizer_stamp_argv() == [
        sys.executable, "-m", "katagiri.tokenizer", "stamp"
    ]


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


def test_probe_anki_manual_step_when_unset(tmp_path, monkeypatch):
    import katagiri.anki_launch as anki_launch_mod

    cfg = _raw_config(tmp_path)
    monkeypatch.setattr(installer, "_detect_anki_data_dir", lambda: None)
    monkeypatch.setattr(anki_launch_mod, "find_anki_exe", lambda: None)
    status = installer.probe_anki(cfg)
    assert status.status == "MANUAL STEP"


def test_probe_anki_missing_when_detected_but_not_saved(tmp_path, monkeypatch):
    cfg = _raw_config(tmp_path)
    monkeypatch.setattr(installer, "_detect_anki_data_dir", lambda: tmp_path / "Anki2")
    status = installer.probe_anki(cfg)
    assert status.status == "MISSING"
    assert "re-run" in status.detail


def test_probe_anki_missing_when_set_but_not_synced(tmp_path):
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "anki")
    status = installer.probe_anki(cfg)
    assert status.status == "MISSING"
    assert "not synced yet" in status.detail


def test_probe_anki_missing_on_a_migrated_but_never_synced_db(tmp_path):
    """A real (migrated) database with no ``mirror_meta`` row must still read
    as MISSING -- the tri-state fix must not accidentally treat "the table
    exists" as "a sync happened"."""
    db_path = tmp_path / "katagiri.db"
    katagiri_db.open_db(db_path).close()
    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "anki", db_path=db_path)
    status = installer.probe_anki(cfg)
    assert status.status == "MISSING"
    assert "not synced yet" in status.detail


def test_probe_anki_ready_after_sync_on_an_empty_collection(tmp_path):
    """RED: a successful ``anki_sync run`` against a genuinely empty Anki
    collection stamps ``mirror_meta`` (via ``snapshot_anki``, unconditionally)
    but never writes the revlog cursor (``anki_sync.sync_anki`` only persists
    that once there is at least one day of reviews to append). The probe must
    read this as READY with an empty-collection message, not MISSING."""
    db_path = tmp_path / "katagiri.db"
    conn = katagiri_db.open_db(db_path)
    conn.execute(
        "INSERT INTO mirror_meta(id, snapshot_ts, collection_mtime, "
        "anki_schema_version) VALUES (1, '2026-01-01T00:00:00Z', NULL, NULL)"
    )
    conn.commit()
    conn.close()

    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "anki", db_path=db_path)
    status = installer.probe_anki(cfg)
    assert status.status == "READY"
    assert status.detail == "synced (empty collection, 0 cards)"


def test_probe_anki_ready_with_cards_after_sync(tmp_path):
    db_path = tmp_path / "katagiri.db"
    conn = katagiri_db.open_db(db_path)
    conn.execute(
        "INSERT INTO mirror_meta(id, snapshot_ts, collection_mtime, "
        "anki_schema_version) VALUES (1, '2026-01-01T00:00:00Z', NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO anki_cards(card_id, note_id, deck, ivl, due, reps, "
        "lapses, mod) VALUES (1, 1, 'Default', 30, 100, 5, 0, 0)"
    )
    conn.execute(
        "INSERT INTO anki_cards(card_id, note_id, deck, ivl, due, reps, "
        "lapses, mod) VALUES (2, 1, 'Default', 10, 50, 2, 1, 0)"
    )
    conn.commit()
    conn.close()

    cfg = _raw_config(tmp_path, anki_data_dir=tmp_path / "anki", db_path=db_path)
    status = installer.probe_anki(cfg)
    assert status.status == "READY"
    assert status.detail == "2 cards"


# ---------------------------------------------------------------------------
# probe_fts: built-but-empty vs. never-built (both are 0 rows)
# ---------------------------------------------------------------------------


def test_probe_fts_missing_when_db_absent(tmp_path):
    status = installer.probe_fts(tmp_path / "katagiri.db")
    assert status.status == "MISSING"
    assert "not built yet" in status.detail


def test_probe_fts_missing_when_not_yet_stamped(tmp_path):
    """A real, migrated database that has never run ``katagiri.tokenizer
    stamp`` (and therefore never successfully rebuilt the index, since
    ``fts_index.current_versions`` refuses without it) must read as MISSING,
    even though ``sentence_text`` and the metadata table both already exist
    from migration."""
    db_path = tmp_path / "katagiri.db"
    katagiri_db.open_db(db_path).close()
    status = installer.probe_fts(db_path)
    assert status.status == "MISSING"
    assert "not built yet" in status.detail


def _stamp_versions(conn) -> None:
    for key in ("dict_version", "tokenizer_version"):
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value, updated_ts) "
            "VALUES (?, ?, ?)",
            (key, "3.1.0", "2026-01-01T00:00:00Z"),
        )


def test_probe_fts_ready_when_stamped_but_empty(tmp_path):
    """RED: a rebuild on a fresh DB stamps versions and legitimately produces
    zero rows until something is mined. That must read as READY, not
    MISSING -- a row count alone cannot tell "built empty" from "never
    built"."""
    db_path = tmp_path / "katagiri.db"
    conn = katagiri_db.open_db(db_path)
    _stamp_versions(conn)
    conn.commit()
    conn.close()

    status = installer.probe_fts(db_path)
    assert status.status == "READY"
    assert status.detail == "built (0 sentences yet — populated by mining)"


def test_probe_fts_ready_with_sentences(tmp_path):
    db_path = tmp_path / "katagiri.db"
    conn = katagiri_db.open_db(db_path)
    _stamp_versions(conn)
    conn.execute("INSERT INTO sentence_text(item_id, jp) VALUES ('s1', '日本語')")
    conn.execute("INSERT INTO sentence_text(item_id, jp) VALUES ('s2', '勉強')")
    conn.commit()
    conn.close()

    status = installer.probe_fts(db_path)
    assert status.status == "READY"
    assert status.detail == "2 sentences"


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


# ---------------------------------------------------------------------------
# Wizard concurrency lock: refuse a second instance, always released
# ---------------------------------------------------------------------------


def test_wizard_lock_round_trip_acquire_then_release(tmp_path):
    lock_path = tmp_path / "installer.lock"
    fh = installer._acquire_wizard_lock(lock_path)
    installer._release_wizard_lock(fh)

    # Released cleanly: acquiring again on the same path afterward also works.
    fh2 = installer._acquire_wizard_lock(lock_path)
    installer._release_wizard_lock(fh2)


def test_wizard_lock_contention_raises_installer_error(tmp_path):
    import msvcrt

    lock_path = tmp_path / "installer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held_fh = open(lock_path, "a+b")
    held_fh.seek(0)
    msvcrt.locking(held_fh.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        with pytest.raises(installer.InstallerError) as excinfo:
            installer._acquire_wizard_lock(lock_path)
        assert str(lock_path) in str(excinfo.value)
    finally:
        held_fh.seek(0)
        msvcrt.locking(held_fh.fileno(), msvcrt.LK_UNLCK, 1)
        held_fh.close()


def test_run_wizard_refuses_to_start_when_lock_already_held(tmp_path, monkeypatch):
    import msvcrt

    cfg_path = tmp_path / "config.toml"
    lock_path = cfg_path.parent / "installer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held_fh = open(lock_path, "a+b")
    held_fh.seek(0)
    msvcrt.locking(held_fh.fileno(), msvcrt.LK_NBLCK, 1)

    def boom(*a, **k):
        raise AssertionError("must not run steps when lock is held")

    monkeypatch.setattr(installer, "_run_wizard_steps", boom)

    try:
        with pytest.raises(installer.InstallerError):
            installer.run_wizard(cfg_path, tmp_path, assume_yes=True)
    finally:
        held_fh.seek(0)
        msvcrt.locking(held_fh.fileno(), msvcrt.LK_UNLCK, 1)
        held_fh.close()


def test_run_wizard_releases_lock_even_when_a_step_raises(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    lock_path = cfg_path.parent / "installer.lock"

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(installer, "_run_wizard_steps", boom)

    with pytest.raises(RuntimeError):
        installer.run_wizard(cfg_path, tmp_path, assume_yes=True)

    # The `finally` released it: acquiring immediately afterward must succeed,
    # which proves run_wizard didn't leak the OS lock on a step failure.
    fh = installer._acquire_wizard_lock(lock_path)
    installer._release_wizard_lock(fh)
