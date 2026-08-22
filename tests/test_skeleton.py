"""Skeleton smoke tests: config bootstrap, stderr-only logging, ping tool."""

from __future__ import annotations

import logging
import sys

import pytest

from katagiri import __version__, config as config_mod
from katagiri.logging_setup import LOGGER_NAME, setup_logging
from katagiri.mcp_server import ping


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir and clear the config cache."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


def test_config_created_with_commented_defaults(local_app_data):
    cfg_path = local_app_data / "Katagiri" / "config.toml"
    assert not cfg_path.exists()

    cfg = config_mod.load_config()

    assert cfg_path.is_file()
    assert cfg.config_file == cfg_path
    # Defaults land under LOCALAPPDATA\Katagiri, not in the repo.
    assert cfg.db_path == local_app_data / "Katagiri" / "katagiri.db"
    assert cfg.scratch_root == local_app_data / "Katagiri" / "scratch"
    # Nothing is guessed for the operator's own directories.
    assert cfg.vault_path is None
    assert cfg.anki_data_dir is None

    text = cfg_path.read_text(encoding="utf-8")
    for key in ("vault_path", "anki_data_dir", "scratch_root", "db_path"):
        assert f"# {key}" in text, f"{key} should be present but commented out"


def test_config_is_cached_and_resettable(local_app_data):
    first = config_mod.get_config()
    assert config_mod.get_config() is first
    config_mod.reset_config_cache()
    assert config_mod.get_config() is not first


def test_config_reads_overrides(local_app_data):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    vault = local_app_data / "Vault"
    (cfg_dir / "config.toml").write_text(
        f'vault_path = "{vault.as_posix()}"\n', encoding="utf-8"
    )

    cfg = config_mod.load_config()

    assert cfg.vault_path == vault
    assert cfg.require_vault_path() == vault


def test_missing_required_key_raises_pointing_at_config(local_app_data):
    cfg = config_mod.load_config()
    with pytest.raises(config_mod.ConfigError) as exc:
        cfg.require_anki_data_dir()
    assert "anki_data_dir" in str(exc.value)
    assert str(cfg.config_file) in str(exc.value)


def test_unknown_key_rejected(local_app_data):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text('vaultpath = "x"\n', encoding="utf-8")
    with pytest.raises(config_mod.ConfigError, match="vaultpath"):
        config_mod.load_config()


def test_template_block_exists_for_every_known_key():
    assert set(config_mod._KEY_BLOCKS) == set(config_mod._KNOWN_KEYS)


def test_missing_template_keys_appended_on_load(local_app_data):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    vault = local_app_data / "Vault"
    original = (
        "# operator's own comment, must survive verbatim\n"
        f'vault_path = "{vault.as_posix()}"\n'
    )
    (cfg_dir / "config.toml").write_text(original, encoding="utf-8")

    cfg = config_mod.load_config()
    assert cfg.vault_path == vault

    text = (cfg_dir / "config.toml").read_text(encoding="utf-8")
    # Existing content is untouched: appended, never rewritten.
    assert text.startswith(original)
    for key in (
        "anki_data_dir",
        "scratch_root",
        "db_path",
        "obsidian_api_token",
        "obsidian_ca_bundle",
        "asbplayer_bridge_dir",
        "screenshot_scratch_root",
        "mokuro_shared_secret",
    ):
        assert f"# {key} = " in text, f"missing commented block for {key}"
    # The active key is not duplicated by a commented copy.
    assert text.count("vault_path") == 1

    # The migrated file still loads, and the appended blocks are comments only,
    # so no value changed.
    config_mod.reset_config_cache()
    again = config_mod.load_config()
    assert again.vault_path == vault
    assert again.obsidian_api_token is None


def test_commented_key_counts_as_present(local_app_data):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    original = (
        '# obsidian_api_token = ""\n'
        '# vault_path = ""\n'
        '# anki_data_dir = ""\n'
        '# scratch_root = ""\n'
        '# db_path = ""\n'
        '# obsidian_ca_bundle = ""\n'
        '# asbplayer_bridge_dir = ""\n'
        '# screenshot_scratch_root = ""\n'
        '# mokuro_shared_secret = ""\n'
    )
    (cfg_dir / "config.toml").write_text(original, encoding="utf-8")

    config_mod.load_config()

    text = (cfg_dir / "config.toml").read_text(encoding="utf-8")
    assert text == original, "all keys present (commented) -> nothing appended"


def test_fresh_default_config_is_stable_across_loads(local_app_data):
    config_mod.load_config()
    cfg_path = local_app_data / "Katagiri" / "config.toml"
    before = cfg_path.read_text(encoding="utf-8")

    config_mod.reset_config_cache()
    config_mod.load_config()

    assert cfg_path.read_text(encoding="utf-8") == before


def test_append_without_trailing_newline_stays_valid_toml(local_app_data):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    vault = local_app_data / "Vault"
    (cfg_dir / "config.toml").write_text(
        f'vault_path = "{vault.as_posix()}"', encoding="utf-8"  # no trailing \n
    )

    config_mod.load_config()

    config_mod.reset_config_cache()
    cfg = config_mod.load_config()  # must not raise TOMLDecodeError
    assert cfg.vault_path == vault


def test_append_failure_does_not_break_load(local_app_data, monkeypatch):
    cfg_dir = local_app_data / "Katagiri"
    cfg_dir.mkdir(parents=True)
    vault = local_app_data / "Vault"
    original = f'vault_path = "{vault.as_posix()}"\n'
    cfg_path = cfg_dir / "config.toml"
    cfg_path.write_text(original, encoding="utf-8")

    real_open = config_mod.Path.open

    def deny_append(self, mode="r", *args, **kwargs):
        if "a" in mode:
            raise PermissionError(13, "denied", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(config_mod.Path, "open", deny_append)

    cfg = config_mod.load_config()  # must not raise

    assert cfg.vault_path == vault
    assert cfg_path.read_text(encoding="utf-8") == original


def _stdout_bound(handler: logging.Handler) -> bool:
    stream = getattr(handler, "stream", None)
    if stream is None:
        return False
    if stream is sys.stdout or stream is sys.__stdout__:
        return True
    try:
        return stream.fileno() == 1
    except (OSError, ValueError, AttributeError):
        return False


def test_logging_uses_stderr_only():
    logger = setup_logging(logging.DEBUG)

    assert logger.name == LOGGER_NAME
    assert logger.handlers, "expected at least one handler"
    assert not logger.propagate, "must not propagate to a possibly-stdout root"
    for handler in logger.handlers:
        assert isinstance(handler, logging.StreamHandler)
        assert not _stdout_bound(handler)
        assert handler.stream is sys.stderr


def test_logging_setup_is_idempotent():
    before = len(setup_logging(logging.INFO).handlers)
    after = len(setup_logging(logging.INFO).handlers)
    assert before == after


def test_logging_setup_removes_a_stdout_handler():
    logger = logging.getLogger(LOGGER_NAME)
    bad = logging.StreamHandler(sys.stdout)
    logger.addHandler(bad)
    setup_logging(logging.INFO)
    assert bad not in logger.handlers
    assert bad not in logging.getLogger().handlers


def test_ping_tool_returns_ok():
    result = ping()
    assert result["status"] == "ok"
    assert result["katagiri_version"] == __version__
    assert result["python"].startswith("3.12")
