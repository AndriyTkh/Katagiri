"""Configuration loading for Katagiri.

Machine-specific paths and any future secrets live **outside the repository**,
under ``%LOCALAPPDATA%\\Katagiri``. The config file is ``config.toml``; on first
load it is created with commented-out defaults so the operator can see the
available knobs without any value being guessed for them.

Config *values* are never logged — they are local filesystem paths (vault,
Anki profile, scratch) and are treated as private. Errors reference the config
file path and the offending key name only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

APP_DIR_NAME: Final = "Katagiri"
CONFIG_FILE_NAME: Final = "config.toml"

_PATH_KEYS: Final = ("vault_path", "anki_data_dir", "scratch_root", "db_path")

_DEFAULT_CONFIG_TEMPLATE: Final = """\
# Katagiri configuration.
#
# This file lives outside the repository on purpose: it holds machine-specific
# absolute paths (and, later, local credentials). Never commit it, never paste
# its contents into an issue or a log.
#
# Every key below is optional and shown with its built-in default. Uncomment a
# key to override it. Use forward slashes or escaped backslashes in TOML
# strings, e.g. "C:/Users/me/Vault" or "C:\\\\Users\\\\me\\\\Vault".

# Obsidian (or plain markdown) vault that Katagiri reads/writes study notes in.
# Required before any vault-backed tool will work; no default is invented.
# vault_path = ""

# Anki data directory (the folder containing your Anki profiles).
# anki_data_dir = ""

# Scratch space for intermediate artefacts (temp exports, caches).
# scratch_root = "{scratch_root}"

# SQLite database used for Katagiri's own state.
# db_path = "{db_path}"
"""


class ConfigError(RuntimeError):
    """Raised when Katagiri's configuration cannot be loaded or is invalid."""


def local_app_data() -> Path:
    """Return ``%LOCALAPPDATA%`` as a path, or raise with an explicit message."""
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        raise ConfigError(
            "LOCALAPPDATA is not set, so Katagiri cannot locate its "
            "configuration directory. Katagiri targets Windows and stores "
            "config under %LOCALAPPDATA%\\Katagiri."
        )
    return Path(raw)


def config_dir() -> Path:
    """Directory holding ``config.toml`` — ``%LOCALAPPDATA%\\Katagiri``."""
    return local_app_data() / APP_DIR_NAME


def config_path() -> Path:
    """Full path to ``config.toml``."""
    return config_dir() / CONFIG_FILE_NAME


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved Katagiri configuration.

    ``vault_path`` and ``anki_data_dir`` are ``None`` until the operator sets
    them; tools that need them must raise a clear error rather than guessing.
    """

    config_file: Path
    scratch_root: Path
    db_path: Path
    vault_path: Path | None = None
    anki_data_dir: Path | None = None

    def require_vault_path(self) -> Path:
        return self._require("vault_path", self.vault_path)

    def require_anki_data_dir(self) -> Path:
        return self._require("anki_data_dir", self.anki_data_dir)

    def _require(self, key: str, value: Path | None) -> Path:
        if value is None:
            raise ConfigError(
                f"Configuration key '{key}' is not set. Set it in "
                f"{self.config_file} (uncomment the key and give it an "
                f"absolute path), then restart the Katagiri MCP server."
            )
        return value


def _defaults(base: Path) -> dict[str, Path]:
    return {
        "scratch_root": base / "scratch",
        "db_path": base / "katagiri.db",
    }


def write_default_config(path: Path) -> None:
    """Create ``path`` with commented-out defaults. Never overwrites."""
    if path.exists():
        return
    defaults = _defaults(path.parent)
    body = _DEFAULT_CONFIG_TEMPLATE.format(
        scratch_root=defaults["scratch_root"].as_posix(),
        db_path=defaults["db_path"].as_posix(),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Could not create the default configuration file at {path}: {exc}"
        ) from exc


def _coerce_path(key: str, value: Any, source: Path) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            f"Configuration key '{key}' in {source} must be a string path, "
            f"got {type(value).__name__}."
        )
    text = value.strip()
    if not text:
        return None
    return Path(text).expanduser()


def load_config(*, create_missing: bool = True) -> Config:
    """Load configuration from ``%LOCALAPPDATA%\\Katagiri\\config.toml``.

    Creates the file with commented defaults if it is absent (unless
    ``create_missing=False``). Unknown keys are rejected so typos surface
    immediately instead of being silently ignored.
    """
    path = config_path()

    if not path.exists():
        if not create_missing:
            raise ConfigError(
                f"Configuration file {path} does not exist. Start the Katagiri "
                "MCP server once to have a commented template written for you."
            )
        write_default_config(path)

    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except OSError as exc:
        raise ConfigError(f"Could not read the configuration file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"The configuration file {path} is not valid TOML: {exc}"
        ) from exc

    unknown = sorted(set(raw) - set(_PATH_KEYS))
    if unknown:
        raise ConfigError(
            f"Unknown configuration key(s) in {path}: {', '.join(unknown)}. "
            f"Supported keys: {', '.join(_PATH_KEYS)}."
        )

    defaults = _defaults(path.parent)
    values = {key: _coerce_path(key, raw.get(key), path) for key in _PATH_KEYS}

    return Config(
        config_file=path,
        scratch_root=values["scratch_root"] or defaults["scratch_root"],
        db_path=values["db_path"] or defaults["db_path"],
        vault_path=values["vault_path"],
        anki_data_dir=values["anki_data_dir"],
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached accessor for the process-wide configuration."""
    return load_config()


def reset_config_cache() -> None:
    """Clear the cached config (tests, or after the operator edits the file)."""
    get_config.cache_clear()
