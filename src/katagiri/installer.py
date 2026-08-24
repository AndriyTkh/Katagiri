r"""Katagiri first-run installer / doctor.

Runs the machine-specific setup a fresh checkout needs before Katagiri's MCP
tools are useful: a config file with real paths, vendored dictionary data,
the derived JMdict/kanjium tables, an optional Anki mirror, the search
indexes, an optional Yomitan overlay, the Obsidian bridge, optional scheduled
tasks, and a backup rehearsal. Every step is idempotent and safe to re-run.

Usage (``setup.bat`` at the repo root wraps ``uv sync`` + the first form)::

    python -m katagiri.installer            # interactive wizard + post-setup menu
    python -m katagiri.installer --yes       # accept defaults, no prompts, no schtasks
    python -m katagiri.installer --check     # doctor only: report, change nothing

Interactive runs offer Retry/Skip/Abort on any failed step, then a post-setup
menu to re-run steps, re-check status, or launch the MCP server (in a new
console via ``run-mcp.bat``). The wizard always exits 0; ``--check`` is the
mode whose exit code (1 on any MISSING component) is meant for scripting.

Design notes for anyone editing this file:

* This module must not import other Katagiri modules at top level (other than
  ``config``). The rest of the package is imported lazily, inside the step
  that needs it, so a partially-set-up checkout never fails to *start* the
  installer just because one optional dependency's module can't be imported
  yet.
* Every subprocess call sets ``PYTHONUTF8=1``: the default Windows console
  code page (cp1252) cannot represent the Japanese text several of these
  child processes may emit, and a ``UnicodeEncodeError`` from a grandchild
  process is a worse failure mode than the installer itself printing plain
  ASCII status lines (also enforced here: no emoji, anywhere).
* The Obsidian API token is never printed, logged, or persisted anywhere but
  ``config.toml``. Status text refers to it only as "(set)" / "(unset)".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from katagiri import config as config_mod

# stdlib ``logging`` rather than ``katagiri.applog.get_logger``: the no-imports
# rule above is what lets a half-set-up checkout still start the installer. The
# name is the same child the helper would build, so the records land in the
# shared log file once the ``__main__`` block has configured it.
_log = logging.getLogger("katagiri.installer")

TOTAL_STEPS = 12

MPV_CONF_LINE = r"input-ipc-server=\\.\pipe\mpv-katagiri"

ANKIMORPHS_URL = "https://github.com/mortii/anki-morphs"

# Anki 26.08 removed the API AnkiMorphs used to install its spaCy/CAMeL venv
# deps; the AnkiMorphs maintainer's fix is a hard version cap, not a patch on
# their side -- see https://github.com/mortii/anki-morphs/issues/421.
ANKIMORPHS_MAX_ANKI_VERSION = (26, 5)
ANKIMORPHS_PINNED_ANKI_VERSION = "26.05"
ANKIMORPHS_COMPAT_ISSUE_URL = "https://github.com/mortii/anki-morphs/issues/421"

EPILOGUE = (
    "Daily start: open Claude Code in this repository and run /katagiri-study.\n"
    "Obsidian only needs to be running for vault-backed tools (notes search,\n"
    "active-note lookup) -- everything else works with Obsidian closed."
)

WIZARD_PREAMBLE = (
    "\nSetting up, and why:\n"
    "  - config.toml            where every path/token below gets saved\n"
    "  - vendor data + JMdict   the dictionaries word lookups run against\n"
    "  - Anki                   flashcards: spaced-repetition review of what you've learned\n"
    "  - search indexes         sentence search + your Obsidian notes, indexed for lookup\n"
    "  - Yomitan overlay        browser popup dictionary for known/unknown words\n"
    "  - Obsidian bridge        your notebook: vault-backed notes and search\n"
    "  - scheduled tasks        optional background sync/backup (asks before creating any)\n"
    "  - backup rehearsal       proves a database snapshot can be created and restored\n"
)

STEP_LABELS = (
    "Config",
    "Vendor data",
    "JMdict/kanjium import",
    "Anki sync",
    "Search indexes (fts + markdown)",
    "Yomitan dictionary",
    "Obsidian bridge check",
    "Scheduled tasks (optional)",
    "Backup rehearsal",
    "Irodori study schedule (optional)",
    "Browser companion check (optional)",
)


# ---------------------------------------------------------------------------
# Small result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepResult:
    """Outcome of one wizard step. ``status`` is one of OK/SKIP/ACTION NEEDED."""

    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    """One row of the doctor table. ``status`` is READY/MISSING/MANUAL STEP."""

    name: str
    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RawConfig:
    """A read-only snapshot of config.toml, parsed without side effects.

    Unlike :func:`katagiri.config.load_config`, reading this never creates the
    file, never migrates it, and never raises on an unknown key -- the
    installer's doctor mode must be able to inspect a config file (or the
    absence of one) without changing anything on disk.
    """

    config_file: Path
    scratch_root: Path
    db_path: Path
    vault_path: Path | None = None
    anki_data_dir: Path | None = None
    obsidian_api_token: str | None = None
    mokuro_shared_secret: str | None = None


def _repo_root() -> Path:
    """Repository root, derived from this file's own location.

    Deliberately not a hardcoded path: this file may live in any checkout
    (a git worktree included), and every scheduled-task command built below
    must point at wherever *this* installer actually is.
    """
    return Path(__file__).resolve().parents[2]


def validate_secret_value(value: str) -> str | None:
    """``None`` if ``value`` is safe to send as an HTTP header value, else a
    short, value-free reason it isn't.

    Mirrors :func:`katagiri.config._coerce_secret`'s control-char/latin-1
    checks (that function is private to ``config``, so the rule is
    reimplemented here rather than reached into). Both checks exist for the
    same reason: ``http.client.putheader`` raises ``ValueError`` with the raw
    header value quoted in its message for exactly these two cases, and that
    traceback must never have a chance to happen with a credential in it.
    """
    if any(ch < " " or ch == "\x7f" for ch in value):
        return "contains a control character"
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return "contains a character outside latin-1"
    return None


def read_raw_config(cfg_path: Path) -> RawConfig:
    """Parse ``cfg_path`` if it exists; never creates or modifies it."""
    base = cfg_path.parent
    default_scratch = base / "scratch"
    default_db = base / "katagiri.db"

    raw: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
            raw = {}

    def _path(key: str) -> Path | None:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value.strip()).expanduser()
        return None

    def _secret(key: str) -> str | None:
        value = raw.get(key)
        value = value.strip() if isinstance(value, str) and value.strip() else None
        if value is not None and validate_secret_value(value) is not None:
            # An invalid secret (control char, or a byte outside latin-1 -- the
            # same rules config.py enforces on write) must never reach a
            # consumer that would forward it as an HTTP header value. Treating
            # it as unset here means every downstream probe/step takes the
            # "not configured" branch instead of the "send it" branch.
            return None
        return value

    return RawConfig(
        config_file=cfg_path,
        scratch_root=_path("scratch_root") or default_scratch,
        db_path=_path("db_path") or default_db,
        vault_path=_path("vault_path"),
        anki_data_dir=_path("anki_data_dir"),
        obsidian_api_token=_secret("obsidian_api_token"),
        mokuro_shared_secret=_secret("mokuro_shared_secret"),
    )


# ---------------------------------------------------------------------------
# config.toml merge (never overwrites unrelated lines, never echoes secrets)
# ---------------------------------------------------------------------------


def _key_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)


def _toml_line(key: str, value: str) -> str:
    # json.dumps escapes quotes/backslashes/control characters exactly the way
    # a TOML basic string requires for ordinary text, so it doubles as a safe
    # TOML string literal here.
    return f"{key} = {json.dumps(value)}"


def set_config_value(text: str, key: str, value: str) -> str:
    """Return ``text`` with ``key`` set to ``value``, preserving everything else.

    Replaces the first live-or-commented ``key = ...`` line in place if one
    exists; otherwise appends a new line. Every other line -- values, comments,
    blank lines -- is left untouched.
    """
    line = _toml_line(key, value)
    pattern = _key_pattern(key)
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1)
    sep = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{sep}{line}\n"


class InstallerError(RuntimeError):
    """Raised for a step-level failure the installer can explain in one line.

    Callers convert this into a StepResult rather than letting it propagate as
    a traceback -- everything named in the message is a path or a key, never a
    secret value.
    """


def _acquire_wizard_lock(lock_path: Path) -> Any:
    """Refuse to start the wizard if another instance already holds this lock.

    Two concurrent wizards would both read-modify-write ``config.toml`` (and,
    via ``fetch_taekim.py``, ``CHECKSUMS.sha256``) with no coordination, so
    the second writer can silently clobber the first's update. This uses an
    OS-level advisory lock (``msvcrt.locking``) rather than a PID file: the
    lock is released by Windows itself the moment this process's handle
    closes, including on a crash or ``taskkill /F``, so there is no stale-lock
    state a later run could get stuck behind.
    """
    import msvcrt

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        raise InstallerError(
            f"Another katagiri installer is already running (lock held on "
            f"{lock_path}). Wait for it to finish, or close it, before "
            "running this one."
        ) from None
    return fh


def _release_wizard_lock(fh: Any) -> None:
    import msvcrt

    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    fh.close()


def apply_config_updates(path: Path, updates: dict[str, str]) -> None:
    """Merge non-empty ``updates`` into ``path``'s config.toml.

    Fails fast, before writing anything, if the existing file can't be read as
    UTF-8: merging into a mis-decoded reading of it would silently corrupt
    every line the merge didn't touch, and that's worse than refusing to run.
    """
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise InstallerError(
                f"Could not read {path} as UTF-8 ({exc}). The file may have "
                "been saved with a different encoding; fix it by hand (or "
                "delete it and re-run the installer to get a fresh one) "
                "before setting configuration values."
            ) from None
    else:
        text = ""
    for key, value in updates.items():
        if not value:
            continue
        text = set_config_value(text, key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _normalize_path_value(raw: str) -> str:
    """Windows-friendly input -> the forward-slash form config.toml expects."""
    return Path(raw.strip()).expanduser().as_posix()


# ---------------------------------------------------------------------------
# Read-only DB probing (no migration, no writes -- safe under --check)
# ---------------------------------------------------------------------------


def _ro_query_scalar(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> Any | None:
    """Best-effort read-only scalar query. Any failure -> None, never raises.

    A missing database file, an un-migrated schema, or a locked file are all
    equally "nothing to report" for doctor purposes -- never a reason for the
    installer itself to crash.
    """
    if not db_path.exists():
        return None
    uri = f"file:{urllib.parse.quote(db_path.as_posix())}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.DatabaseError:
        return None
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------


def _module_argv(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def jmdict_import_argv() -> list[str]:
    return _module_argv("katagiri.jmdict_import", "all")


def anki_sync_argv() -> list[str]:
    return _module_argv("katagiri.anki_sync", "run")


def tokenizer_stamp_argv() -> list[str]:
    return _module_argv("katagiri.tokenizer", "stamp")


def fts_index_argv() -> list[str]:
    return _module_argv("katagiri.fts_index", "rebuild")


def md_search_argv(vault_path: Path) -> list[str]:
    return _module_argv("katagiri.md_search", "rebuild", "--root", str(vault_path))


def yomitan_export_argv() -> list[str]:
    return _module_argv("katagiri.yomitan_export", "gen")


def backup_create_argv() -> list[str]:
    return _module_argv("katagiri.backup", "create")


def backup_verify_argv(snapshot_path: str) -> list[str]:
    return _module_argv("katagiri.backup", "verify", snapshot_path)


def irodori_import_argv() -> list[str]:
    return _module_argv("katagiri.irodori_import", "all")


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return env


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("env", _subprocess_env())
    return subprocess.run(argv, **kwargs)


def _truncated(text: str | None, limit: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


# ---------------------------------------------------------------------------
# Vendor data verification
# ---------------------------------------------------------------------------


def _iter_checksum_entries(manifest_text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in manifest_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            continue
        digest, relpath = parts
        entries.append((digest.strip().lower(), relpath.strip()))
    return entries


def verify_vendor(repo_root: Path) -> ComponentStatus:
    manifest = repo_root / "vendor" / "CHECKSUMS.sha256"
    if not manifest.exists():
        return ComponentStatus("vendor data", "MISSING", f"{manifest} not found")

    try:
        entries = _iter_checksum_entries(manifest.read_text(encoding="utf-8"))
    except OSError as exc:
        return ComponentStatus("vendor data", "MISSING", f"could not read manifest: {exc}")

    if not entries:
        return ComponentStatus(
            "vendor data", "MISSING", "no components listed in CHECKSUMS.sha256"
        )

    missing: list[str] = []
    mismatched: list[str] = []
    for digest, relpath in entries:
        path = repo_root / relpath
        if not path.exists():
            missing.append(relpath)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest().lower()
        if actual != digest:
            mismatched.append(relpath)

    if not missing and not mismatched:
        return ComponentStatus("vendor data", "READY", f"{len(entries)} file(s) verified")

    bits = []
    if missing:
        bits.append("missing: " + ", ".join(missing))
    if mismatched:
        bits.append("checksum mismatch: " + ", ".join(mismatched))
    return ComponentStatus("vendor data", "MISSING", "; ".join(bits))


# ---------------------------------------------------------------------------
# Obsidian bridge check
# ---------------------------------------------------------------------------


def check_obsidian_connection(token: str | None) -> tuple[bool, str]:
    """Ping obsidian-local-rest-api through ``katagiri.obsidian_proxy``.

    This module must not become a second HTTP client: ``obsidian_proxy`` is
    the only one the package is allowed to have (enforced by
    ``tests/test_bverify.py::test_only_the_obsidian_proxy_is_an_http_client``
    and its Phase C twin, which scan every ``.py`` under ``src/katagiri``), so
    the bridge check goes through it -- ``list_vault_dir()`` is the lightest
    read it offers, a ``GET /vault/`` that needs no path argument, which makes
    it the closest thing to a ping. ``obsidian_proxy`` is imported lazily,
    matching this module's rule that nothing but ``config`` is imported at top
    level.

    ``token`` is the *explicit* ``obsidian_api_token`` from config.toml, or
    ``None`` when it isn't set -- in which case ``obsidian_proxy`` still tries
    the ping, auto-discovering the key and TLS certificate from the plugin's
    own ``data.json`` (see ``obsidian_proxy._token``/``_tls_context``). An
    explicit token is validated before anything is sent: an invalid one (a
    control character, or a byte outside latin-1) is refused right here, the
    same check ``obsidian_proxy`` itself would fail on, so no request is
    attempted and no value-quoting exception has a chance to fire. There is
    nothing to validate for an auto-discovered key -- it never passes through
    this function.

    ``config``'s cached accessor is reset first: this check exists to find out
    whether *this run's* credentials work, and an lru_cache populated before
    ``step_config`` wrote the file (or before an operator's own edit) would
    silently ping with stale ones instead.
    """
    if token:
        problem = validate_secret_value(token)
        if problem is not None:
            return (
                False,
                f"obsidian_api_token is invalid ({problem}); not sent. Re-copy "
                "the token as plain text from Obsidian's Local REST API plugin "
                "settings.",
            )

    from katagiri import obsidian_proxy

    config_mod.reset_config_cache()
    result = obsidian_proxy.list_vault_dir()
    if result["ok"]:
        return True, f"reachable (HTTP {result['status']})"

    note = result.get("note") or "could not reach the Local REST API plugin."
    if result.get("error") == obsidian_proxy.UNREACHABLE:
        note = f"{note} Open Obsidian and enable the 'Local REST API' community plugin."
    return False, note


# ---------------------------------------------------------------------------
# Scheduled task commands (argv lists, never shell strings)
# ---------------------------------------------------------------------------


def schtasks_backup_command() -> tuple[str, list[str]]:
    name = "Katagiri Daily Backup"
    tr = f'cmd /c cd /d "{_repo_root()}" && uv run python -m katagiri.backup create'
    argv = ["schtasks", "/Create", "/TN", name, "/SC", "DAILY", "/ST", "21:00", "/F", "/TR", tr]
    return name, argv


def schtasks_anki_command() -> tuple[str, list[str]]:
    name = "Katagiri Anki Sync"
    tr = f'cmd /c cd /d "{_repo_root()}" && uv run python -m katagiri.anki_sync run'
    argv = ["schtasks", "/Create", "/TN", name, "/SC", "DAILY", "/ST", "22:30", "/F", "/TR", tr]
    return name, argv


def _pythonw_path() -> Path:
    return Path(sys.executable).with_name("pythonw.exe")


def check_pythonw_available() -> str | None:
    """``None`` if the mpv-logger task can point at a real interpreter, else why not."""
    pythonw = _pythonw_path()
    if not pythonw.exists():
        return f"pythonw.exe not found next to {sys.executable}"
    return None


def schtasks_mpv_command() -> tuple[str, list[str]]:
    name = "Katagiri mpv seek logger"
    pythonw = _pythonw_path()
    tr = f'"{pythonw}" -m katagiri.mpv_seek_logger run'
    argv = ["schtasks", "/Create", "/TN", name, "/SC", "ONLOGON", "/TR", tr, "/RL", "LIMITED", "/F"]
    return name, argv


SCHTASK_NAMES = ("Katagiri Daily Backup", "Katagiri Anki Sync", "Katagiri mpv seek logger")

# The optional fourth element is a precheck: called before the task is even
# offered, it returns None when the task can be registered or a reason it
# can't. This is how a missing pythonw.exe turns into a reported ACTION
# NEEDED instead of a scheduled task that points at a file that isn't there.
SCHTASK_BUILDERS = (
    ("nightly database snapshot at 21:00", schtasks_backup_command, None),
    ("nightly Anki review import at 22:30", schtasks_anki_command, None),
    ("mpv seek logger at logon", schtasks_mpv_command, check_pythonw_available),
)


def probe_schtasks() -> ComponentStatus:
    installed = []
    for name in SCHTASK_NAMES:
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", name],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except OSError as exc:
            return ComponentStatus("scheduled tasks", "MANUAL STEP", f"schtasks unavailable: {exc}")
        if result.returncode == 0:
            installed.append(name)
    if len(installed) == len(SCHTASK_NAMES):
        return ComponentStatus("scheduled tasks", "READY", "all 3 installed")
    missing = [n for n in SCHTASK_NAMES if n not in installed]
    return ComponentStatus("scheduled tasks", "MANUAL STEP", "not installed: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# Doctor probes (read-only; used by both --check and the wizard's step 10)
# ---------------------------------------------------------------------------


def probe_config(cfg_path: Path) -> ComponentStatus:
    if cfg_path.exists():
        return ComponentStatus("config", "READY", str(cfg_path))
    return ComponentStatus("config", "MISSING", f"{cfg_path} does not exist yet")


def probe_jmdict(db_path: Path) -> ComponentStatus:
    count = _ro_query_scalar(db_path, "SELECT COUNT(*) FROM jmdict_entry")
    if count:
        return ComponentStatus("jmdict/kanjium import", "READY", f"{count} entries")
    return ComponentStatus("jmdict/kanjium import", "MISSING", "not imported yet")


def _detect_anki_data_dir() -> Path | None:
    """Best-effort locate Anki's profiles folder at its default Windows path.

    Only checks ``%APPDATA%\\Anki2`` for a subdirectory holding
    ``prefs21.db`` (the marker Anki writes once a profile is initialized) --
    pure filesystem/env reads, no subprocess, no network. Returns ``None``
    if Anki isn't installed, was moved, or has no profile yet.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    base = Path(appdata) / "Anki2"
    if not base.is_dir():
        return None
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and (entry / "prefs21.db").is_file():
            return base
    return None


def _anki_manual_step_detail() -> str:
    """Distinguish "not installed" from "installed, no profile yet" for the doctor/wizard messages."""
    from katagiri.anki_launch import WINGET_HINT, find_anki_exe

    if find_anki_exe() is None:
        return f"Anki not found. Install it ({WINGET_HINT}), then re-run."
    return (
        "Anki is installed but has no profile yet. Open Anki once to create one, "
        f"install AnkiMorphs ({ANKIMORPHS_URL}), then re-run."
    )


def probe_anki(cfg: RawConfig) -> ComponentStatus:
    """READY once a sync has *run*, not once the mirror holds a card.

    ``sync_anki`` calls ``snapshot_anki`` unconditionally, before it ever looks
    at the revlog cursor, and ``snapshot_anki`` stamps ``mirror_meta`` (an
    ``INSERT OR REPLACE`` of the single ``id = 1`` row) on every successful
    run -- including one against a genuinely empty Anki collection, where
    ``anki_sync``'s own revlog cursor (the ``anki_sync_last_revlog_id``
    ``metadata`` key ``anki_sync status`` reads) is never written at all: it
    only advances/persists once there is at least one day of reviews to
    append (see ``anki_sync.sync_anki``'s early return when ``not groups and
    new_cursor == cursor``). Counting ``anki_cards`` rows has the same "0 means
    two different things" problem as counting the mirror rows themselves, so
    ``mirror_meta`` presence is the state discriminator and the row count is
    only used for the message.
    """
    if cfg.anki_data_dir is None:
        detected = _detect_anki_data_dir()
        if detected is not None:
            return ComponentStatus(
                "anki mirror", "MISSING", f"found at {detected}; re-run install to save it"
            )
        return ComponentStatus("anki mirror", "MANUAL STEP", _anki_manual_step_detail())
    synced = _ro_query_scalar(cfg.db_path, "SELECT 1 FROM mirror_meta WHERE id = 1")
    if synced is None:
        return ComponentStatus("anki mirror", "MISSING", "not synced yet")
    count = _ro_query_scalar(cfg.db_path, "SELECT COUNT(*) FROM anki_cards")
    if count:
        return ComponentStatus("anki mirror", "READY", f"{count} cards")
    return ComponentStatus(
        "anki mirror", "READY", "synced (empty collection, 0 cards)"
    )


def probe_fts(db_path: Path) -> ComponentStatus:
    """READY once the index's version provenance is stamped, not once it holds
    a row.

    A freshly rebuilt index legitimately holds zero ``sentence_text`` rows
    until something is mined -- a *built, empty* index -- which a
    ``COUNT(*)`` cannot tell apart from an index that was never built: both
    are 0. ``dict_version``/``tokenizer_version`` in ``metadata`` are what
    ``katagiri.tokenizer stamp`` writes and what ``fts_index.current_versions``
    (and therefore ``fts_index.rebuild``) require before touching the index
    (see ``step_search_indexes``, which now stamps before every rebuild), so
    their presence is the actual "has this step run" signal.
    """
    from katagiri.fts_index import DICT_VERSION_KEY, TOKENIZER_VERSION_KEY

    dict_version = _ro_query_scalar(
        db_path, "SELECT value FROM metadata WHERE key = ?", (DICT_VERSION_KEY,)
    )
    tokenizer_version = _ro_query_scalar(
        db_path, "SELECT value FROM metadata WHERE key = ?", (TOKENIZER_VERSION_KEY,)
    )
    if not dict_version or not tokenizer_version:
        return ComponentStatus("sentence search (fts)", "MISSING", "not built yet")

    count = _ro_query_scalar(db_path, "SELECT COUNT(*) FROM sentence_text")
    if count:
        return ComponentStatus("sentence search (fts)", "READY", f"{count} sentences")
    return ComponentStatus(
        "sentence search (fts)",
        "READY",
        "built (0 sentences yet — populated by mining)",
    )


def probe_md(cfg: RawConfig) -> ComponentStatus:
    if cfg.vault_path is None:
        return ComponentStatus("markdown index", "MANUAL STEP", "vault_path not set")
    count = _ro_query_scalar(cfg.db_path, "SELECT COUNT(*) FROM md_note")
    if count:
        return ComponentStatus("markdown index", "READY", f"{count} note(s) indexed")
    return ComponentStatus("markdown index", "MISSING", "index empty")


def probe_yomitan(cfg: RawConfig) -> ComponentStatus:
    out_dir = cfg.scratch_root / "yomitan"
    if out_dir.exists():
        dicts = sorted(out_dir.glob("katagiri-known-*.zip"))
        if dicts:
            return ComponentStatus("yomitan dictionary", "READY", dicts[-1].name)
    return ComponentStatus("yomitan dictionary", "MISSING", "not generated yet")


def probe_obsidian(cfg: RawConfig) -> ComponentStatus:
    if not cfg.obsidian_api_token and cfg.vault_path is None:
        return ComponentStatus(
            "obsidian bridge",
            "MANUAL STEP",
            "vault_path not set; install the 'Local REST API' plugin and set "
            "vault_path so its key and certificate can be auto-discovered "
            "(or set obsidian_api_token explicitly)",
        )
    ok, detail = check_obsidian_connection(cfg.obsidian_api_token)
    return ComponentStatus("obsidian bridge", "READY" if ok else "MANUAL STEP", detail)


def probe_backup(cfg: RawConfig) -> ComponentStatus:
    backups_dir = cfg.config_file.parent / "backups"
    if backups_dir.exists() and any(backups_dir.glob("*.db")):
        return ComponentStatus("backup", "READY", "snapshot present")
    return ComponentStatus("backup", "MISSING", "no snapshot yet")


def probe_irodori(db_path: Path) -> ComponentStatus:
    count = _ro_query_scalar(
        db_path, "SELECT COUNT(*) FROM item WHERE home_topic LIKE 'irodori-l%'"
    )
    if count:
        return ComponentStatus("irodori study schedule", "READY", f"{count} word(s) seeded")
    # Optional and consent-gated (like scheduled tasks): "not installed" is not
    # a problem to flag via doctor_exit_code, just a MANUAL STEP the operator
    # can opt into later by re-running the wizard.
    return ComponentStatus(
        "irodori study schedule", "MANUAL STEP", "optional, not installed (declined or skipped)"
    )


_COMPANION_FALLBACK_NAMES: Final = ("Yomitan", "asbplayer", "mokuro page-change bridge")


def probe_companions(cfg: RawConfig) -> list[ComponentStatus]:
    """Browser companion doctor rows: Yomitan, asbplayer, mokuro bridge (008).

    ``katagiri.companions`` is imported lazily, inside this function, mirroring
    :func:`probe_anki` / :func:`_anki_manual_step_detail`'s precedent --
    installer.py's top-level import rule is "config only".

    Status mapping is load-bearing: ``present`` -> ``READY``; ``absent`` or
    ``undetermined`` -> ``MANUAL STEP`` -- **never** ``MISSING``, because
    :func:`doctor_exit_code` returns 1 on any ``MISSING`` row and spec 008's
    SC-004 requires ``--check`` exit codes to stay byte-identical to pre-008
    for every companion state (a browser companion is optional and its
    absence is a manual follow-up, not an installer failure). Any exception
    out of the detector -- a companion library bug, an unreadable profile
    tree the module itself failed to guard -- is caught here and reported as
    an ``undetermined``-shaped ``MANUAL STEP`` row instead of crashing the
    doctor on a browser it did not expect (FR-009: ``--check`` stays
    read-only and prompt-free even on that path).

    Detail text carries the evidence (profile path / reason / port state),
    truncated with :func:`_truncated` to fit :func:`render_doctor_table`.
    """
    try:
        from katagiri.companions import (
            EXTENSION_QUERIES,
            VERDICT_PRESENT,
            detect_extensions,
            mokuro_companion_status,
        )

        rows: list[ComponentStatus] = []
        extension_rows, _scan = detect_extensions(EXTENSION_QUERIES)
        for row in extension_rows:
            status = "READY" if row.verdict == VERDICT_PRESENT else "MANUAL STEP"
            rows.append(ComponentStatus(row.name, status, _truncated(row.detail)))

        mokuro_row = mokuro_companion_status(cfg)
        mokuro_status = "READY" if mokuro_row.verdict == VERDICT_PRESENT else "MANUAL STEP"
        rows.append(ComponentStatus(mokuro_row.name, mokuro_status, _truncated(mokuro_row.detail)))
        return rows
    except Exception as exc:  # noqa: BLE001 - the doctor must never crash on this probe
        detail = _truncated(f"companion detection failed unexpectedly: {exc}")
        return [ComponentStatus(name, "MANUAL STEP", detail) for name in _COMPANION_FALLBACK_NAMES]


def collect_doctor_statuses(cfg: RawConfig, repo_root: Path) -> list[ComponentStatus]:
    return [
        probe_config(cfg.config_file),
        verify_vendor(repo_root),
        probe_jmdict(cfg.db_path),
        probe_anki(cfg),
        probe_fts(cfg.db_path),
        probe_md(cfg),
        probe_yomitan(cfg),
        probe_obsidian(cfg),
        probe_schtasks(),
        probe_backup(cfg),
        probe_irodori(cfg.db_path),
        *probe_companions(cfg),
    ]


def render_doctor_table(statuses: list[ComponentStatus]) -> str:
    name_width = max((len(s.name) for s in statuses), default=8)
    status_width = max((len(s.status) for s in statuses), default=6)
    lines = ["", "Doctor summary", "-" * 14]
    for s in statuses:
        detail = f"  {s.detail}" if s.detail else ""
        lines.append(f"{s.name.ljust(name_width)}  {s.status.ljust(status_width)}{detail}")
    return "\n".join(lines)


def doctor_exit_code(statuses: list[ComponentStatus]) -> int:
    """0 when nothing needs attention, 1 when any component is MISSING."""
    return 1 if any(s.status == "MISSING" for s in statuses) else 0


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


CONFIG_PROMPTS = (
    ("vault_path", "Obsidian/markdown vault path"),
    ("anki_data_dir", "Anki data directory"),
    ("obsidian_api_token", "Obsidian Local REST API token"),
)


def step_config(
    cfg_path: Path,
    *,
    assume_yes: bool,
    prompt: Any = input,
    secret_prompt: Any = None,
) -> StepResult:
    created = not cfg_path.exists()
    if created:
        config_mod.write_default_config(cfg_path)

    if assume_yes:
        return StepResult("OK", "created config.toml" if created else "config.toml present")

    current = read_raw_config(cfg_path)
    updates: dict[str, str] = {}
    for key, label in CONFIG_PROMPTS:
        is_secret = key == "obsidian_api_token"
        current_value = getattr(current, key)
        shown = "(set)" if is_secret and current_value else (str(current_value) if current_value else "(unset)")
        asker = secret_prompt if (is_secret and secret_prompt is not None) else prompt
        try:
            answer = asker(f"  {label} [{shown}], Enter to keep: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        answer = (answer or "").strip()
        if not answer:
            continue
        updates[key] = answer if is_secret else _normalize_path_value(answer)

    if updates:
        try:
            apply_config_updates(cfg_path, updates)
        except InstallerError as exc:
            return StepResult("ACTION NEEDED", str(exc))
        detail = ("created and set " if created else "set ") + ", ".join(sorted(updates))
    else:
        detail = "created, no values entered" if created else "unchanged"
    return StepResult("OK", detail)


def step_vendor(repo_root: Path) -> StepResult:
    status = verify_vendor(repo_root)
    return StepResult("OK" if status.status == "READY" else "ACTION NEEDED", status.detail)


def _vendor_problem_relpaths(repo_root: Path) -> list[str]:
    """Manifest-relative paths that are missing or fail their checksum.

    Same walk as :func:`verify_vendor`, but returning the paths themselves so
    the download recovery below can hand them to ``katagiri.vendor_fetch``.
    """
    manifest = repo_root / "vendor" / "CHECKSUMS.sha256"
    try:
        entries = _iter_checksum_entries(manifest.read_text(encoding="utf-8"))
    except OSError:
        return []
    problems: list[str] = []
    for digest, relpath in entries:
        path = repo_root / relpath
        if not path.exists():
            problems.append(relpath)
        elif hashlib.sha256(path.read_bytes()).hexdigest().lower() != digest:
            problems.append(relpath)
    return problems


def _vendor_download_recovery(repo_root: Path, *, prompt: Any = input) -> bool:
    """Offer to download the missing vendor files (consent-gated, Irodori
    pattern -- see ``katagiri.vendor_fetch``'s module docstring).

    Called only when the interactive vendor step reported ACTION NEEDED,
    before the generic Retry/Skip/Abort prompt; never under ``--yes``.
    Returns True when the operator consented and a fetch ran (the caller
    should re-run the step to show the fresh result); False when there is
    nothing fetchable or the operator declined.
    """
    from katagiri import vendor_fetch

    fetchable = [
        p
        for p in _vendor_problem_relpaths(repo_root)
        if p in vendor_fetch.ARTIFACTS_BY_RELPATH
    ]
    if not fetchable:
        return False
    print(
        f"      {len(fetchable)} of the missing vendor file(s) can be downloaded "
        "from their official sources (see vendor/README.md for sources and licenses)."
    )
    try:
        answer = prompt(
            "      Download missing vendor files now "
            f"({vendor_fetch.format_size_estimate(fetchable)} from official sources)? [y/N]: "
        )
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if (answer or "").strip().lower() != "y":
        return False
    failures = vendor_fetch.fetch_missing(repo_root, fetchable)
    if failures:
        print(f"      {len(failures)} download(s) failed; details above.")
    return True


def step_jmdict(db_path: Path) -> StepResult:
    count = _ro_query_scalar(db_path, "SELECT COUNT(*) FROM jmdict_entry")
    if count:
        return StepResult("OK", f"already imported ({count} entries)")
    proc = _run(jmdict_import_argv())
    if proc.returncode == 0:
        return StepResult("OK", "imported")
    return StepResult(
        "ACTION NEEDED", f"jmdict_import exited {proc.returncode}: {_truncated(proc.stderr)}"
    )


def _parse_dotted_version(text: str) -> tuple[int, ...] | None:
    """Best-effort ``"26.08.1"`` -> ``(26, 8, 1)``. ``None`` for anything else."""
    if not re.fullmatch(r"\d+(?:\.\d+)*", text.strip()):
        return None
    return tuple(int(part) for part in text.strip().split("."))


def _installed_anki_version() -> tuple[int, ...] | None:
    """Best-effort installed Anki version via ``winget`` (no other reliable
    stdlib source on Windows). Never raises: winget missing, not on PATH,
    unparsable output, or a timeout all mean "unknown", not "incompatible" --
    a downgrade is only ever offered when this positively confirms a version
    above the AnkiMorphs cutoff.
    """
    try:
        proc = _run(["winget", "list", "--id", "Anki.Anki", "--exact"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if "Anki.Anki" not in line:
            continue
        for token in line.split():
            version = _parse_dotted_version(token)
            if version is not None:
                return version
    return None


def _ankimorphs_camel_wrapper_paths(anki_data_dir: Path) -> list[Path]:
    """Locate AnkiMorphs' ``camel_wrapper.py`` under any installed addon folder.

    Matched by file path, not addon folder name: AnkiWeb-shared installs use
    a numeric folder id (see the traceback in
    https://github.com/mortii/anki-morphs/issues/421), not the literal name
    "ankimorphs".
    """
    addons = anki_data_dir / "addons21"
    if not addons.is_dir():
        return []
    return sorted(addons.glob("*/morphemizers/camel_wrapper.py"))


def _maybe_downgrade_anki_for_ankimorphs(cfg: RawConfig, *, prompt: Any) -> str | None:
    """Offer to downgrade Anki when AnkiMorphs is installed and Anki is newer
    than the version AnkiMorphs still supports.

    Always asks first -- even under ``--yes`` -- unlike the other optional
    steps' "skip silently under --yes" convention: this uninstalls the user's
    current Anki and installs a pinned older one, more invasive than anything
    else this wizard touches. Returns ``None`` when there's nothing to offer
    (no AnkiMorphs found, version unknown, or already at/below the supported
    version); otherwise a detail string describing what happened.
    """
    if cfg.anki_data_dir is None:
        return None
    if not _ankimorphs_camel_wrapper_paths(cfg.anki_data_dir):
        return None
    version = _installed_anki_version()
    if version is None or version[:2] <= ANKIMORPHS_MAX_ANKI_VERSION:
        return None

    print(
        f"  AnkiMorphs is installed but Anki {'.'.join(map(str, version))} broke the API it "
        f"needs ({ANKIMORPHS_COMPAT_ISSUE_URL}). AnkiMorphs only supports Anki "
        f"{ANKIMORPHS_PINNED_ANKI_VERSION} and below."
    )
    try:
        answer = prompt(
            f"  Downgrade Anki to {ANKIMORPHS_PINNED_ANKI_VERSION} so AnkiMorphs works again? [y/N]: "
        )
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if (answer or "").strip().lower() != "y":
        return f"AnkiMorphs needs Anki <= {ANKIMORPHS_PINNED_ANKI_VERSION}; downgrade declined"

    proc = _run(
        [
            "winget", "install", "--id", "Anki.Anki", "-e",
            "--version", ANKIMORPHS_PINNED_ANKI_VERSION,
            "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements",
        ],
        timeout=300,
    )
    if proc.returncode != 0:
        # winget reports most errors on stdout, not stderr -- fall back so the
        # detail never ends in a bare "failed: " with no reason.
        reason = _truncated(
            (proc.stderr or "").strip() or (proc.stdout or "").strip()
        ) or f"winget exited {proc.returncode}"
        return (
            f"Anki downgrade to {ANKIMORPHS_PINNED_ANKI_VERSION} failed: {reason}; "
            f"AnkiMorphs stays broken until Anki <= {ANKIMORPHS_PINNED_ANKI_VERSION}"
        )
    return f"downgraded Anki to {ANKIMORPHS_PINNED_ANKI_VERSION} for AnkiMorphs"


def step_anki(cfg: RawConfig, *, prompt: Any = input) -> StepResult:
    if cfg.anki_data_dir is None:
        detected = _detect_anki_data_dir()
        if detected is None:
            return StepResult("SKIP", _anki_manual_step_detail())
        try:
            apply_config_updates(cfg.config_file, {"anki_data_dir": _normalize_path_value(str(detected))})
        except InstallerError as exc:
            return StepResult("ACTION NEEDED", str(exc))
        cfg = replace(cfg, anki_data_dir=detected)

    downgrade_detail = _maybe_downgrade_anki_for_ankimorphs(cfg, prompt=prompt)

    from katagiri import anki_launch

    print("  Opening Anki so you know it's there -- it's what runs your flashcards.")
    anki_launch.launch_anki()

    proc = _run(anki_sync_argv())
    if proc.returncode == 0:
        detail = "synced" if not downgrade_detail else f"synced; {downgrade_detail}"
        return StepResult("OK", detail)
    detail = f"anki_sync exited {proc.returncode}: {_truncated(proc.stderr)}"
    if downgrade_detail:
        detail = f"{detail}; {downgrade_detail}"
    return StepResult("ACTION NEEDED", detail)


def step_fts(db_path: Path) -> StepResult:
    proc = _run(fts_index_argv())
    if proc.returncode == 0:
        return StepResult("OK", "rebuilt")
    return StepResult(
        "ACTION NEEDED", f"fts_index exited {proc.returncode}: {_truncated(proc.stderr)}"
    )


def step_md_search(cfg: RawConfig) -> StepResult:
    if cfg.vault_path is None:
        return StepResult("SKIP", "vault_path not set")
    proc = _run(md_search_argv(cfg.vault_path))
    if proc.returncode == 0:
        return StepResult("OK", "rebuilt")
    return StepResult(
        "ACTION NEEDED", f"md_search exited {proc.returncode}: {_truncated(proc.stderr)}"
    )


def step_stamp() -> StepResult:
    proc = _run(tokenizer_stamp_argv())
    if proc.returncode == 0:
        return StepResult("OK", "stamped")
    return StepResult(
        "ACTION NEEDED",
        f"tokenizer stamp exited {proc.returncode}: {_truncated(proc.stderr)}",
    )


def step_search_indexes(cfg: RawConfig) -> StepResult:
    """Stamp tokenizer/dictionary provenance, then rebuild fts + markdown.

    On a fresh database ``fts_index rebuild`` and ``md_search rebuild`` both
    raise ``VersionsNotStampedError`` ("Run katagiri.tokenizer.stamp_versions
    (conn) first") until provenance has been recorded once, so the stamp has
    to run first. ``stamp_versions`` is idempotent, so this is also safe on a
    database that was already stamped by an earlier run.

    If stamping itself fails -- most likely the vendored dictionary is
    missing -- neither rebuild is attempted: both would fail the same way,
    less informatively.
    """
    stamp_result = step_stamp()
    if stamp_result.status != "OK":
        return StepResult(
            "ACTION NEEDED", f"tokenizer stamp: {stamp_result.detail}"
        )

    fts_result = step_fts(cfg.db_path)
    md_result = step_md_search(cfg)
    index_ok = fts_result.status == "OK" and md_result.status in ("OK", "SKIP")
    return StepResult(
        "OK" if index_ok else "ACTION NEEDED",
        f"fts: {fts_result.status} ({fts_result.detail}); "
        f"markdown: {md_result.status} ({md_result.detail})",
    )


def step_yomitan() -> StepResult:
    try:
        import importlib

        importlib.import_module("katagiri.yomitan_export")
    except ImportError as exc:
        return StepResult("SKIP", f"yomitan_export not importable: {exc}")

    proc = _run(yomitan_export_argv())
    combined = _truncated((proc.stdout or "") + (proc.stderr or ""), 800)
    if proc.returncode == 0:
        return StepResult("OK", combined or "generated")
    return StepResult("ACTION NEEDED", f"yomitan_export exited {proc.returncode}: {combined}")


def step_obsidian(cfg: RawConfig) -> StepResult:
    from katagiri import obsidian_launch

    print("  Opening Obsidian so you know it's there -- it's your main notebook.")
    launch = obsidian_launch.launch_obsidian()

    if not cfg.obsidian_api_token and cfg.vault_path is None:
        detail = (
            "vault_path not set. Install the 'Local REST API' community plugin "
            "in Obsidian and set vault_path so its key and certificate can be "
            "auto-discovered (or set obsidian_api_token explicitly)."
        )
        if launch.reason and not launch.already_running:
            detail = f"{detail} ({launch.reason})"
        return StepResult("SKIP", detail)
    ok, detail = check_obsidian_connection(cfg.obsidian_api_token)
    return StepResult("OK" if ok else "ACTION NEEDED", detail)


def step_schtasks(*, assume_yes: bool, prompt: Any = input) -> StepResult:
    print("  Manual: add this line to %APPDATA%\\mpv\\mpv.conf so mpv exposes its IPC pipe:")
    print(f"    {MPV_CONF_LINE}")

    if assume_yes:
        return StepResult("SKIP", "skipped under --yes")

    created: list[str] = []
    problems: list[str] = []
    for label, builder, precheck in SCHTASK_BUILDERS:
        if precheck is not None:
            problem = precheck()
            if problem is not None:
                print(f"    ACTION NEEDED: {label} not offered: {problem}")
                problems.append(f"{label}: {problem}")
                continue
        name, argv = builder()
        try:
            answer = prompt(f"  Create scheduled task for {label} ({name})? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if (answer or "").strip().lower() != "y":
            continue
        proc = _run(argv)
        if proc.returncode == 0:
            created.append(name)
        else:
            print(f"    ACTION NEEDED: could not create '{name}': {_truncated(proc.stderr)}")

    if created:
        detail = "created: " + ", ".join(created)
        if problems:
            detail += "; skipped: " + "; ".join(problems)
        return StepResult("OK", detail)
    if problems:
        return StepResult("ACTION NEEDED", "; ".join(problems))
    return StepResult("SKIP", "no scheduled tasks created")


def _parse_backup_snapshot_path(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith("database snapshot:"):
            return line.split(":", 1)[1].strip()
    return None


def step_backup() -> StepResult:
    create_proc = _run(backup_create_argv())
    if create_proc.returncode != 0:
        return StepResult(
            "ACTION NEEDED",
            f"backup create exited {create_proc.returncode}: {_truncated(create_proc.stderr)}",
        )

    snapshot_path = _parse_backup_snapshot_path(create_proc.stdout or "")
    if not snapshot_path:
        return StepResult("ACTION NEEDED", "backup create did not report a snapshot path")

    verify_proc = _run(backup_verify_argv(snapshot_path))
    if verify_proc.returncode == 0:
        return StepResult("OK", f"created and verified {Path(snapshot_path).name}")
    return StepResult(
        "ACTION NEEDED",
        f"backup verify exited {verify_proc.returncode}: {_truncated(verify_proc.stderr)}",
    )


def step_irodori(db_path: Path, *, prompt: Any = input) -> StepResult:
    """Offer to seed a starter study schedule from the Irodori Table of Contents.

    Always asks -- even under ``--yes`` -- same as
    ``_maybe_downgrade_anki_for_ankimorphs``: this reaches out to the network
    (fetching the official, freely published Japan Foundation TOC PDF, never
    the copyrighted lesson content itself; see
    ``katagiri.irodori_import``/``vendor/README.md``), which no other default
    step in this wizard does, so it needs its own explicit yes regardless of
    ``--yes``. Declining or hitting EOF/Ctrl-C skips silently -- this is
    optional flavor content, not required setup.
    """
    count = _ro_query_scalar(
        db_path, "SELECT COUNT(*) FROM item WHERE home_topic LIKE 'irodori-l%'"
    )
    if count:
        return StepResult("OK", f"already seeded ({count} word(s))")

    print(
        "  Irodori is a free Japanese textbook published by the Japan Foundation.\n"
        "  This can download its table of contents (lesson titles + word lists, not\n"
        "  the copyrighted lesson content) from irodori.jpf.go.jp and seed a starter\n"
        "  study schedule from it."
    )
    try:
        answer = prompt("  Download it and seed a starter study schedule? [y\\N]: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if (answer or "").strip().lower() != "y":
        return StepResult("SKIP", "declined")

    proc = _run(irodori_import_argv())
    if proc.returncode == 0:
        return StepResult("OK", "seeded")
    return StepResult(
        "ACTION NEEDED",
        f"irodori_import exited {proc.returncode}: {_truncated(proc.stderr)}",
    )


def _detect_companion_statuses(cfg: RawConfig) -> list[Any]:
    """Detect Yomitan/asbplayer/mokuro, in catalog order; exception-safe.

    Mirrors :func:`probe_companions`'s safety net (an unexpected detector bug
    or unreadable profile tree must not crash the installer -- 008 spec
    FR-009/FR-011) but returns the richer ``CompanionStatus`` rows (verdict +
    a name step_companions can pair back to ``COMPANION_CATALOG`` for the
    handoff text) instead of probe_companions's doctor-table
    ``ComponentStatus`` shape.
    """
    try:
        from katagiri.companions import EXTENSION_QUERIES, detect_extensions, mokuro_companion_status

        extension_rows, _scan = detect_extensions(EXTENSION_QUERIES)
        return [*extension_rows, mokuro_companion_status(cfg)]
    except Exception as exc:  # noqa: BLE001 - detection must never crash the wizard step
        from katagiri.companions import COMPANION_CATALOG, VERDICT_UNDETERMINED, CompanionStatus

        detail = _truncated(f"companion detection failed unexpectedly: {exc}")
        return [
            CompanionStatus(entry.name, VERDICT_UNDETERMINED, detail) for entry in COMPANION_CATALOG
        ]


def _companion_step_detail(statuses: list[Any]) -> str:
    return "; ".join(f"{s.name}: {s.verdict}" for s in statuses)


def _report_companion_statuses(statuses: list[Any]) -> bool:
    """Print each companion's verdict, then the handoff for any absent one.

    Returns True when every companion is ``present``. Handoff text is only
    printed for the ``absent`` verdict (spec FR-005: "for every companion
    reported absent") -- ``undetermined`` rows are reported as-is, since
    telling the operator to install something we could not actually check
    for would misstate what was found (spec US3).
    """
    from katagiri.companions import COMPANION_CATALOG, VERDICT_ABSENT, VERDICT_PRESENT, render_handoff

    catalog_by_name = {entry.name: entry for entry in COMPANION_CATALOG}
    all_present = True
    for s in statuses:
        detail = f" ({s.detail})" if s.detail else ""
        print(f"  {s.name}: {s.verdict}{detail}")
        if s.verdict != VERDICT_PRESENT:
            all_present = False
    for s in statuses:
        if s.verdict == VERDICT_ABSENT:
            entry = catalog_by_name.get(s.name)
            if entry is not None:
                print(render_handoff(entry))
    return all_present


def step_companions(cfg: RawConfig, *, assume_yes: bool, prompt: Any = input) -> StepResult:
    """Browser companion check: report + handoff, with a re-check/skip loop.

    Katagiri never installs a browser extension or userscript (spec FR-006):
    this step only ever prints verdicts and, for anything absent, the
    catalog's install URL(s) and numbered manual steps (FR-005) -- the
    operator does the install themselves, in their own browser, then asks
    this step to look again.

    Under ``--yes`` the report/handoff is printed exactly once with no
    prompt and no wait (FR-008); an absent *optional* companion must never
    turn an unattended install into ACTION NEEDED, so under ``--yes`` this
    step can only ever finish OK (everything present) or SKIP (something
    still needs the operator -- surfaced in the summary, not treated as a
    failure). Interactively, re-check (US2 acceptance 2) re-runs only the
    detection above -- no earlier wizard step -- and skip (US2 acceptance 3)
    ends the step with the outcome so far.
    """
    statuses = _detect_companion_statuses(cfg)
    all_present = _report_companion_statuses(statuses)

    if assume_yes:
        return StepResult("OK" if all_present else "SKIP", _companion_step_detail(statuses))

    while not all_present:
        try:
            answer = prompt("  [R]e-check / [S]kip? [S]: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if (answer or "").strip().lower() == "r":
            statuses = _detect_companion_statuses(cfg)
            all_present = _report_companion_statuses(statuses)
            continue
        return StepResult("SKIP", _companion_step_detail(statuses))

    return StepResult("OK", _companion_step_detail(statuses))


# ---------------------------------------------------------------------------
# Wizard runner
# ---------------------------------------------------------------------------


def _print_step(n: int, total: int, name: str, result: StepResult) -> None:
    suffix = f" ({result.detail})" if result.detail else ""
    print(f"[{n}/{total}] {name} ... {result.status}{suffix}")
    # Same text to the shared log: a wizard run is the thing most likely to be
    # reported after the fact, when the console has long since been closed.
    _log.info("step %d/%d %s: %s%s", n, total, name, result.status, suffix)


class _WizardAborted(Exception):
    """Raised when the operator picks [A]bort at a failed step.

    Caught by ``_run_wizard_steps``, which still prints the doctor summary on
    the way out -- an abort should leave the operator knowing exactly where
    setup stands, not staring at a bare prompt.
    """


# Extra one-line guidance printed when a step fails interactively. Keyed by
# STEP_LABELS entry; only steps whose most common failure is "the external
# app isn't running / isn't wired up yet" need one -- for those, fixing the
# external state and picking Retry is the expected recovery path.
_RETRY_HINTS = {
    STEP_LABELS[3]: (
        "Start Anki once so its data directory exists (or set anki_data_dir "
        "via the Config step), then Retry."
    ),
    STEP_LABELS[6]: (
        "Start Obsidian with the 'Local REST API' plugin enabled (or set "
        "vault_path so its key can be auto-discovered), then Retry."
    ),
}


def _wizard_step_runners(
    cfg_path: Path, repo_root: Path, *, assume_yes: bool
) -> list[tuple[str, Any]]:
    """The eleven wizard steps as ``(label, thunk)`` pairs, in execution order.

    Each thunk re-reads config.toml when invoked, so re-running a step (via
    Retry or the post-setup menu) after the Config step changed a path sees
    the new value instead of a stale snapshot.
    """

    def _cfg() -> RawConfig:
        return read_raw_config(cfg_path)

    return [
        (STEP_LABELS[0], lambda: step_config(cfg_path, assume_yes=assume_yes)),
        (STEP_LABELS[1], lambda: step_vendor(repo_root)),
        (STEP_LABELS[2], lambda: step_jmdict(_cfg().db_path)),
        (STEP_LABELS[3], lambda: step_anki(_cfg())),
        (STEP_LABELS[4], lambda: step_search_indexes(_cfg())),
        (STEP_LABELS[5], lambda: step_yomitan()),
        (STEP_LABELS[6], lambda: step_obsidian(_cfg())),
        (STEP_LABELS[7], lambda: step_schtasks(assume_yes=assume_yes)),
        (STEP_LABELS[8], lambda: step_backup()),
        (STEP_LABELS[9], lambda: step_irodori(_cfg().db_path)),
        (STEP_LABELS[10], lambda: step_companions(_cfg(), assume_yes=assume_yes)),
    ]


def _run_step_with_retry(
    n: int, total: int, label: str, runner: Any, *, prompt: Any = input, recover: Any = None
) -> StepResult:
    """Run one step; on ACTION NEEDED, offer Retry / Skip / Abort.

    Skip -- the default, and what EOF/Ctrl-C mean -- records the failure and
    moves on, exactly like the old single-pass behavior. Retry re-runs the
    same step (every step is idempotent, see module docstring). Abort raises
    :class:`_WizardAborted`.

    ``recover``, when given, is a step-specific recovery offer (currently only
    the vendor step's consent-gated download) tried once, before the first
    generic R/S/A prompt: returning True re-runs the step immediately; False
    falls through to R/S/A.
    """
    recovery_offered = False
    while True:
        result = runner()
        _print_step(n, total, label, result)
        if result.status != "ACTION NEEDED":
            return result
        if recover is not None and not recovery_offered:
            recovery_offered = True
            if recover():
                continue
        hint = _RETRY_HINTS.get(label)
        if hint:
            print(f"      {hint}")
        try:
            answer = prompt("      [R]etry / [S]kip / [A]bort setup? [S]: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        answer = (answer or "").strip().lower()
        if answer == "r":
            continue
        if answer == "a":
            raise _WizardAborted
        _log.info("step %d/%d %s: operator chose Skip after ACTION NEEDED", n, total, label)
        return result


def _step_recovery(label: str, repo_root: Path, *, prompt: Any = input) -> Any:
    """The ``recover`` hook for :func:`_run_step_with_retry`, per step label.

    Only the vendor step has one: the consent-gated download offer. Every
    other step returns None (plain R/S/A behavior).
    """
    if label == STEP_LABELS[1]:
        return lambda: _vendor_download_recovery(repo_root, prompt=prompt)
    return None


def _print_doctor_summary(cfg_path: Path, repo_root: Path) -> list[ComponentStatus]:
    cfg = read_raw_config(cfg_path)
    statuses = collect_doctor_statuses(cfg, repo_root)
    print(f"\n[{TOTAL_STEPS}/{TOTAL_STEPS}] Doctor summary")
    print(f"Data home: {config_mod.config_dir()}")
    print(render_doctor_table(statuses))
    # Mirror each component's end state to the shared log: the console table
    # is often long gone by the time a run needs to be reconstructed, and
    # unlike each step's own attempt(s) (see _print_step), this final state
    # previously existed only on stdout.
    for s in statuses:
        detail = f" ({s.detail})" if s.detail else ""
        _log.info("doctor %s: %s%s", s.name, s.status, detail)
    return statuses


def _launch_mcp_server(repo_root: Path) -> None:
    """Open run-mcp.bat in a new console window and return immediately.

    The MCP server is a stdio server: running it inside the installer's own
    console would wedge this process on a JSON-RPC stdin nobody is driving.
    A detached window makes it a visible smoke test instead, and lets the
    installer exit (releasing the wizard lock) while the server runs.
    """
    bat = repo_root / "run-mcp.bat"
    if not bat.exists():
        print(f"run-mcp.bat not found at {bat}; start the server manually with: uv run katagiri-mcp")
        return
    # ``start`` treats its first quoted argument as the window title, so a
    # title is always passed explicitly -- a repo path with spaces (which
    # subprocess would quote) must never be eaten as the title.
    subprocess.Popen(
        ["cmd", "/c", "start", "Katagiri MCP", "cmd", "/k", str(bat)],
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Launched the MCP server in a new console window ({bat}).")
    print(
        "It speaks MCP over stdio -- normally your MCP client (e.g. Claude "
        "Code) starts it; the new window is just a smoke test that it runs."
    )
    _log.info("launched MCP server console via %s", bat)


_MENU = (
    "\n[1-11] re-run a setup step   [A] run all steps again\n"
    "[D] doctor summary           [C] edit config (re-run config step)\n"
    "[L] launch MCP server        [Q] quit"
)


def _post_wizard_menu(
    cfg_path: Path,
    repo_root: Path,
    runners: list[tuple[str, Any]],
    statuses: list[ComponentStatus],
    *,
    prompt: Any = input,
) -> None:
    """Interactive post-setup menu (never reached under --yes/--check).

    Returns when the operator quits ([Q], or EOF/Ctrl-C) or launches the MCP
    server ([L]). The installer always exits 0 from here -- doctor problems
    are surfaced in the table, and ``--check`` exists for a status exit code.
    """
    if all(s.status == "READY" for s in statuses):
        print("\nAll components are READY.")
        try:
            answer = prompt("Launch MCP server now? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if (answer or "").strip().lower() == "y":
            _launch_mcp_server(repo_root)
            return

    print("\nSetup steps:")
    for i, (label, _runner) in enumerate(runners, start=1):
        print(f"  [{i}] {label}")

    while True:
        print(_MENU)
        try:
            choice = prompt("> ")
        except (EOFError, KeyboardInterrupt):
            return
        choice = (choice or "").strip().lower()
        if not choice:
            continue
        if choice == "q":
            return
        if choice == "l":
            _launch_mcp_server(repo_root)
            return
        if choice == "d":
            _print_doctor_summary(cfg_path, repo_root)
            continue
        if choice == "a":
            try:
                for i, (label, runner) in enumerate(runners, start=1):
                    _run_step_with_retry(
                        i, TOTAL_STEPS, label, runner, prompt=prompt,
                        recover=_step_recovery(label, repo_root, prompt=prompt),
                    )
            except _WizardAborted:
                print("  Stopped; back to the menu.")
            _print_doctor_summary(cfg_path, repo_root)
            continue
        if choice == "c":
            choice = "1"
        if choice.isdigit() and 1 <= int(choice) <= len(runners):
            i = int(choice)
            label, runner = runners[i - 1]
            try:
                _run_step_with_retry(
                    i, TOTAL_STEPS, label, runner, prompt=prompt,
                    recover=_step_recovery(label, repo_root, prompt=prompt),
                )
            except _WizardAborted:
                print("  Stopped; back to the menu.")
            continue
        print(f"  Unrecognized choice: {choice!r}")


def run_wizard(cfg_path: Path, repo_root: Path, *, assume_yes: bool) -> None:
    # The lock covers the whole session, post-setup menu included: a menu
    # re-run mutates config.toml and the database just like a first run does.
    lock_fh = _acquire_wizard_lock(cfg_path.parent / "installer.lock")
    try:
        _run_wizard_steps(cfg_path, repo_root, assume_yes=assume_yes)
    finally:
        _release_wizard_lock(lock_fh)


def _run_wizard_steps(cfg_path: Path, repo_root: Path, *, assume_yes: bool) -> None:
    print(WIZARD_PREAMBLE)
    runners = _wizard_step_runners(cfg_path, repo_root, assume_yes=assume_yes)

    aborted = False
    for n, (label, runner) in enumerate(runners, start=1):
        if assume_yes:
            # Non-interactive: record the status and continue, no prompts --
            # exactly the old behavior.
            _print_step(n, TOTAL_STEPS, label, runner())
            continue
        try:
            _run_step_with_retry(
                n, TOTAL_STEPS, label, runner,
                recover=_step_recovery(label, repo_root),
            )
        except _WizardAborted:
            aborted = True
            print(f"\nSetup aborted at step {n} ({label}); current status below.")
            _log.info("wizard aborted by operator at step %d (%s)", n, label)
            break

    statuses = _print_doctor_summary(cfg_path, repo_root)
    print()
    print(EPILOGUE)

    if assume_yes or aborted:
        return
    _post_wizard_menu(cfg_path, repo_root, runners, statuses)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.installer",
        description="Katagiri first-run installer / doctor.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="doctor only: report status, change nothing, prompt for nothing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="accept defaults, skip prompts, skip scheduled tasks",
    )
    parser.add_argument(
        "--data-home",
        metavar="PATH",
        default=None,
        help=(
            "resolve config.toml/db/logs under PATH for this run instead of "
            "%%LOCALAPPDATA%%\\Katagiri (side-by-side instances, D-46); creates "
            "PATH if missing and persists KATAGIRI_DATA_HOME=PATH into this "
            "checkout's agent/.env so later runs (installer, MCP server) pick "
            "up the same instance"
        ),
    )
    return parser


def _resolve_data_home(raw: str) -> Path:
    """Validate and create ``--data-home``'s target directory.

    Absolute-only, same rule ``config.config_dir()`` enforces for
    ``KATAGIRI_DATA_HOME`` (D-46): a relative or empty value could silently
    resolve underneath whatever directory the installer happens to be run
    from, which is exactly the "points a side-by-side instance at the real
    study database" failure that override exists to prevent.
    """
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise config_mod.ConfigError(
            f"--data-home must be an absolute path, got {raw!r}."
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _persist_data_home_env(repo_root: Path, data_home: Path) -> None:
    """Append/update ``KATAGIRI_DATA_HOME=<data_home>`` in ``agent/.env``.

    ``agent/.env`` is the untracked, per-checkout file ``agent/scripts/setup.py``
    already reads for instance wiring (``KATAGIRI_PYTHON``, ``KATAGIRI_MODULE``,
    ``KATAGIRI_CONFIG`` -- research.md D9); this keeps ``--data-home`` in the
    same place rather than touching the tracked ``.mcp.json``. Every other line
    is preserved verbatim: only the ``KATAGIRI_DATA_HOME=`` line is replaced in
    place if present, or appended if the file has none (or does not exist yet).
    """
    env_path = repo_root / "agent" / ".env"
    new_line = f"KATAGIRI_DATA_HOME={data_home.as_posix()}"

    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = existing.splitlines()
    pattern = re.compile(r"^KATAGIRI_DATA_HOME=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prescan_data_home(argv: list[str]) -> str | None:
    """Best-effort extraction of ``--data-home``'s raw value before argparse runs.

    Needed so the installer's log file -- attached by ``run_cli`` at
    ``__main__``-time, before ``main()`` gets a chance to parse arguments and
    set ``KATAGIRI_DATA_HOME`` -- lands under the override home instead of the
    default one (T019: log destination was being decided too early). This is
    deliberately tolerant: any shape it does not recognize (missing value,
    unknown spelling) is simply left for ``main()``'s real argparse pass and
    its existing validation/error reporting, since a mis-scan here would at
    worst leave logging at the (harmless) default until ``main()`` sorts it
    out for real.
    """
    for i, arg in enumerate(argv):
        if arg == "--data-home":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith("--data-home="):
            return arg.split("=", 1)[1]
    return None


def _prime_data_home_env_from_argv(argv: list[str]) -> None:
    """Set ``KATAGIRI_DATA_HOME`` from a valid ``--data-home`` flag, pre-argparse.

    Called before ``run_cli`` (hence before ``setup_logging()``) so the
    installer's own log file resolves under the override home from the very
    first line, rather than under the default home that ``main()`` would only
    move away from later (T019). Uses the same validation as ``main()``'s own
    ``--data-home`` handling (:func:`_resolve_data_home`); an invalid value is
    silently left alone here -- ``main()`` will re-validate and report it
    cleanly through its normal ``error: ...`` / exit-2 path, so nothing is
    lost by not duplicating that reporting in this pre-argparse pass.
    """
    raw = _prescan_data_home(argv)
    if not raw:
        return
    try:
        data_home = _resolve_data_home(raw)
    except config_mod.ConfigError:
        return
    os.environ["KATAGIRI_DATA_HOME"] = str(data_home)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = _repo_root()

    if args.data_home:
        try:
            data_home = _resolve_data_home(args.data_home)
        except config_mod.ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        # Set before any config access below so config_dir()/config_path()
        # (and everything derived from them: db_path, logs_dir()) resolve
        # under PATH for the rest of this run -- T011's KATAGIRI_DATA_HOME
        # seam in katagiri.config.config_dir(). Idempotent if
        # _prime_data_home_env_from_argv already set the same value from
        # __main__ (T019).
        os.environ["KATAGIRI_DATA_HOME"] = str(data_home)
        _persist_data_home_env(repo_root, data_home)

    try:
        cfg_path = config_mod.config_path()
    except config_mod.ConfigError as exc:
        # A bad KATAGIRI_DATA_HOME/KATAGIRI_CONFIG override (e.g. empty or
        # blank env var) must fail loudly but cleanly: nonzero exit, one
        # logged ERROR line, no raw traceback on stdout/stderr (T020). Logged
        # explicitly here -- rather than left to propagate into run_cli's
        # generic BaseException handler -- because that handler re-raises
        # after logging, which is right for genuine bugs but wrong for an
        # already-diagnosed, user-facing configuration error.
        _log.error("installer aborted: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        cfg = read_raw_config(cfg_path)
        statuses = collect_doctor_statuses(cfg, repo_root)
        print(f"Data home: {config_mod.config_dir()}")
        print(render_doctor_table(statuses))
        code = doctor_exit_code(statuses)
        _log.info("doctor finished with exit code %d", code)
        return code

    run_wizard(cfg_path, repo_root, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    # Production entry point: runs under the shared rotating log in
    # %LOCALAPPDATA%\Katagiri\logs (or the --data-home override, primed below
    # before logging is configured -- T019). Wired here rather than inside
    # main() so an in-process main() call does not install a process-wide
    # handler as a side effect. See katagiri.applog.run_cli.
    from katagiri.applog import run_cli

    _prime_data_home_env_from_argv(sys.argv[1:])
    raise SystemExit(run_cli("installer", main))
