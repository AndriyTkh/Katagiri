"""Configuration loading for Katagiri.

Machine-specific paths and local secrets live **outside the repository**, under
``%LOCALAPPDATA%\\Katagiri`` (D-22). The config file is ``config.toml``; on first
load it is created with commented-out defaults so the operator can see the
available knobs without any value being guessed for them.

Two kinds of key exist, and the split is deliberate: ``_PATH_KEYS`` are coerced to
``Path``, ``_SECRET_KEYS`` stay strings and are kept out of ``Config.__repr__``.

Config *values* are never logged — they are local filesystem paths (vault, Anki
profile, scratch) and one credential (the Obsidian REST API key). Errors
reference the config file path and the offending key *name* only, never a value.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

APP_DIR_NAME: Final = "Katagiri"
CONFIG_FILE_NAME: Final = "config.toml"

_PATH_KEYS: Final = ("vault_path", "anki_data_dir", "scratch_root", "db_path")

# Keys that are plain strings rather than paths. Kept as a separate tuple so the
# path coercion in :func:`_coerce_path` cannot reach a credential and turn it into
# a ``Path``, and so that adding one is a visible, deliberate edit.
_SECRET_KEYS: Final = ("obsidian_api_token",)

_KNOWN_KEYS: Final = _PATH_KEYS + _SECRET_KEYS

_log = logging.getLogger("katagiri.config")

_TEMPLATE_HEADER: Final = """\
# Katagiri configuration.
#
# This file lives outside the repository on purpose: it holds machine-specific
# absolute paths (and, later, local credentials). Never commit it, never paste
# its contents into an issue or a log.
#
# Every key below is optional and shown with its built-in default. Uncomment a
# key to override it. Use forward slashes or escaped backslashes in TOML
# strings, e.g. "C:/Users/me/Vault" or "C:\\\\Users\\\\me\\\\Vault".
"""

# One commented template block per known key. A config.toml written before a key
# existed gets that key's block appended on load (see ``_append_missing_blocks``),
# so this dict — not one monolithic template string — is the unit of migration.
# Placeholders ({scratch_root}, {db_path}) are filled from ``_defaults``.
_KEY_BLOCKS: Final[dict[str, str]] = {
    "vault_path": """\
# Obsidian (or plain markdown) vault that Katagiri reads/writes study notes in.
# Required before any vault-backed tool will work; no default is invented.
# vault_path = ""
""",
    "anki_data_dir": """\
# Anki data directory (the folder containing your Anki profiles).
# anki_data_dir = ""
""",
    "scratch_root": """\
# Scratch space for intermediate artefacts (temp exports, caches).
# scratch_root = "{scratch_root}"
""",
    "db_path": """\
# SQLite database used for Katagiri's own state.
# db_path = "{db_path}"
""",
    "obsidian_api_token": """\
# API key for the Obsidian "Local REST API" plugin (Settings -> Local REST API).
# This is a credential: Katagiri holds it so the agent never does, and uses it
# only for GET-shaped vault reads against http://127.0.0.1:27123. It is never
# logged, never returned by a tool, and never written back to this file.
# Leave it unset and the Obsidian tools report themselves unconfigured.
# obsidian_api_token = ""
""",
}

# A key "is present" in config.toml when any line sets it or shows it commented
# out ("key = ..." or "# key = ..."). Only then is its template block skipped by
# the append-on-load migration.
_KEY_LINE_RES: Final[dict[str, re.Pattern[str]]] = {
    key: re.compile(rf"^[ \t]*(?:#[ \t]*)?{re.escape(key)}[ \t]*=", re.MULTILINE)
    for key in _KNOWN_KEYS
}


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

    ``obsidian_api_token`` is a credential and is excluded from ``repr``: this
    object is passed around freely and a dataclass repr is exactly the kind of
    thing that ends up in a log line or an exception message.
    """

    config_file: Path
    scratch_root: Path
    db_path: Path
    vault_path: Path | None = None
    anki_data_dir: Path | None = None
    obsidian_api_token: str | None = field(default=None, repr=False)

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


def _render_block(key: str, defaults: dict[str, Path]) -> str:
    return _KEY_BLOCKS[key].format(
        scratch_root=defaults["scratch_root"].as_posix(),
        db_path=defaults["db_path"].as_posix(),
    )


def write_default_config(path: Path) -> None:
    """Create ``path`` with commented-out defaults. Never overwrites."""
    if path.exists():
        return
    defaults = _defaults(path.parent)
    body = _TEMPLATE_HEADER + "".join(
        "\n" + _render_block(key, defaults) for key in _KEY_BLOCKS
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"Could not create the default configuration file at {path}: {exc}"
        ) from exc


def _append_missing_blocks(path: Path, text: str) -> None:
    """Append commented template blocks for keys the file predates.

    ``write_default_config`` never overwrites, so a config.toml created before a
    key was added to the template silently lacks its commented block and the
    operator has no visible knob to discover it exists (kata-obi, hit in
    practice with ``obsidian_api_token``). Existing lines — values and comments
    alike — are never touched; new blocks are appended at the end. A failed
    append is logged and ignored: an absent commented key is functionally
    identical to a present one, so it must never block loading.
    """
    missing = [key for key in _KEY_BLOCKS if not _KEY_LINE_RES[key].search(text)]
    if not missing:
        return
    defaults = _defaults(path.parent)
    chunks = [] if not text or text.endswith("\n") else ["\n"]
    chunks.extend("\n" + _render_block(key, defaults) for key in missing)
    try:
        with path.open("a", encoding="utf-8", newline="") as fh:
            fh.write("".join(chunks))
    except OSError as exc:
        _log.warning(
            "config.toml at %s lacks template block(s) for new key(s) %s, and "
            "appending them failed (%s). The keys default to unset; add them by "
            "hand to make the knobs visible.",
            path,
            ", ".join(missing),
            exc,
        )
        return
    _log.info(
        "Appended commented template block(s) for new configuration key(s) %s "
        "to %s. Existing lines were not modified.",
        ", ".join(missing),
        path,
    )


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


def _coerce_secret(key: str, value: Any, source: Path) -> str | None:
    """A plain string setting, or ``None`` when it is absent or blank.

    The error names the key and the *type* or *shape* problem it found — never the
    value. These keys hold credentials, and an error message is one of the few
    things in this process that reliably reaches a log and a model.

    A secret is also checked for being *sendable*, and that check is a security
    boundary rather than tidiness. These values end up in an HTTP header
    (``Authorization: Bearer <token>``). ``http.client.putheader`` refuses a
    header value that holds a control character or a byte outside latin-1, and it
    raises a ``ValueError`` **whose message quotes the offending value**. A token
    pasted with an embedded newline or a smart quote would therefore turn into a
    traceback carrying the credential. Rejecting it here, at load time, with a
    message that names only the key, keeps that traceback from ever existing.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            f"Configuration key '{key}' in {source} must be a string, got "
            f"{type(value).__name__}. (Its value is not shown: this key holds a "
            "credential.)"
        )
    text = value.strip()
    if not text:
        return None
    if any(char < " " or char == "\x7f" for char in text):
        raise ConfigError(
            f"Configuration key '{key}' in {source} contains a control character "
            "(an embedded newline or tab, most likely a copy-paste artefact). It "
            "could not be sent in an HTTP header, so it is refused here. Re-copy "
            "the value onto a single line. (Its value is not shown: this key "
            "holds a credential.)"
        )
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        # `from None`: UnicodeEncodeError's own message quotes the character it
        # choked on, which is a fragment of the credential.
        raise ConfigError(
            f"Configuration key '{key}' in {source} contains a character outside "
            "latin-1 (a smart quote or a dash substituted by an editor, most "
            "likely). It could not be sent in an HTTP header, so it is refused "
            "here. Re-copy the value as plain text. (Its value is not shown: this "
            "key holds a credential.)"
        ) from None
    return text


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
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not read the configuration file {path}: {exc}") from exc

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"The configuration file {path} is not valid TOML: {exc}"
        ) from exc

    unknown = sorted(set(raw) - set(_KNOWN_KEYS))
    if unknown:
        raise ConfigError(
            f"Unknown configuration key(s) in {path}: {', '.join(unknown)}. "
            f"Supported keys: {', '.join(_KNOWN_KEYS)}."
        )

    # Only a file that parsed cleanly is migrated: appending to a broken file
    # would bury the operator's syntax error under fresh template text.
    _append_missing_blocks(path, text)

    defaults = _defaults(path.parent)
    values = {key: _coerce_path(key, raw.get(key), path) for key in _PATH_KEYS}
    secrets = {key: _coerce_secret(key, raw.get(key), path) for key in _SECRET_KEYS}

    return Config(
        config_file=path,
        scratch_root=values["scratch_root"] or defaults["scratch_root"],
        db_path=values["db_path"] or defaults["db_path"],
        vault_path=values["vault_path"],
        anki_data_dir=values["anki_data_dir"],
        obsidian_api_token=secrets["obsidian_api_token"],
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached accessor for the process-wide configuration."""
    return load_config()


def reset_config_cache() -> None:
    """Clear the cached config (tests, or after the operator edits the file)."""
    get_config.cache_clear()
