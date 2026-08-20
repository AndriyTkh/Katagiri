"""Tests for the ``KATAGIRI_CONFIG`` environment override (T007, US3).

Additive only: when unset, ``config_path()`` must resolve exactly as before
(``%LOCALAPPDATA%\\Katagiri\\config.toml``). When set, it points Katagiri at
that file instead. ``get_config()`` is ``lru_cache``'d, so every test that
touches the environment must call ``reset_config_cache()`` afterward too —
otherwise a later test could observe a stale cached ``Config``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from katagiri import config as config_mod


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Reset the process-wide config cache before and after every test."""
    config_mod.reset_config_cache()
    yield
    config_mod.reset_config_cache()


def test_config_path_honors_katagiri_config_override(tmp_path, monkeypatch):
    override_path = tmp_path / "somewhere-else" / "custom-config.toml"
    monkeypatch.setenv("KATAGIRI_CONFIG", str(override_path))

    assert config_mod.config_path() == override_path


def test_config_path_unchanged_when_env_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("KATAGIRI_CONFIG", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    expected = tmp_path / config_mod.APP_DIR_NAME / config_mod.CONFIG_FILE_NAME
    assert config_mod.config_path() == expected


def test_load_config_reads_from_overridden_path(tmp_path, monkeypatch):
    override_path = tmp_path / "override-config.toml"
    override_path.write_text(
        'vault_path = "C:/Users/me/Vault"\n', encoding="utf-8"
    )
    monkeypatch.setenv("KATAGIRI_CONFIG", str(override_path))
    config_mod.reset_config_cache()

    cfg = config_mod.load_config(create_missing=False)

    assert cfg.config_file == override_path
    assert cfg.vault_path == Path("C:/Users/me/Vault")


def test_get_config_uses_override_and_cache_reset_picks_up_changes(
    tmp_path, monkeypatch
):
    first_path = tmp_path / "first.toml"
    first_path.write_text('vault_path = "C:/first"\n', encoding="utf-8")
    monkeypatch.setenv("KATAGIRI_CONFIG", str(first_path))
    config_mod.reset_config_cache()

    assert config_mod.get_config().vault_path == Path("C:/first")

    second_path = tmp_path / "second.toml"
    second_path.write_text('vault_path = "C:/second"\n', encoding="utf-8")
    monkeypatch.setenv("KATAGIRI_CONFIG", str(second_path))

    # Without a cache reset, get_config() would still be lru_cache'd from the
    # first call above -- proving the test (and the doc'd contract) actually
    # requires reset_config_cache(), not just a re-import.
    assert config_mod.get_config().vault_path == Path("C:/first")

    config_mod.reset_config_cache()
    assert config_mod.get_config().vault_path == Path("C:/second")


def test_missing_file_at_override_path_raises_naming_path_not_value(
    tmp_path, monkeypatch
):
    override_path = tmp_path / "does-not-exist" / "config.toml"
    monkeypatch.setenv("KATAGIRI_CONFIG", str(override_path))
    monkeypatch.setenv("SOME_SECRET_LOOKALIKE_VALUE", "sekrit-token-xyz")

    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load_config(create_missing=False)

    message = str(excinfo.value)
    assert str(override_path) in message
    assert "sekrit-token-xyz" not in message


def test_missing_file_at_override_path_never_leaks_secret_value(
    tmp_path, monkeypatch
):
    """A config file that exists but has a bad secret must still error by
    naming the key only -- never the value -- even when reached through the
    KATAGIRI_CONFIG override path."""
    override_path = tmp_path / "config.toml"
    override_path.write_text(
        'obsidian_api_token = "bad\\ntoken-with-newline"\n', encoding="utf-8"
    )
    monkeypatch.setenv("KATAGIRI_CONFIG", str(override_path))

    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load_config(create_missing=False)

    message = str(excinfo.value)
    assert "bad" not in message
    assert "token-with-newline" not in message
    assert "obsidian_api_token" in message
    assert str(override_path) in message
