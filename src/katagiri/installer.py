r"""Katagiri first-run installer / doctor.

Runs the machine-specific setup a fresh checkout needs before Katagiri's MCP
tools are useful: a config file with real paths, vendored dictionary data,
the derived JMdict/kanjium tables, an optional Anki mirror, the search
indexes, an optional Yomitan overlay, the Obsidian bridge, optional scheduled
tasks, and a backup rehearsal. Every step is idempotent and safe to re-run.

Usage::

    python -m katagiri.installer            # interactive wizard
    python -m katagiri.installer --yes       # accept defaults, no prompts, no schtasks
    python -m katagiri.installer --check     # doctor only: report, change nothing

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
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from katagiri import config as config_mod

TOTAL_STEPS = 10

MPV_CONF_LINE = r"input-ipc-server=\\.\pipe\mpv-katagiri"

ANKIMORPHS_URL = "https://github.com/mortii/anki-morphs"

EPILOGUE = (
    "Daily start: open Claude Code in this repository and run /katagiri-study.\n"
    "Obsidian only needs to be running for vault-backed tools (notes search,\n"
    "active-note lookup) -- everything else works with Obsidian closed."
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

    token = raw.get("obsidian_api_token")
    token = token.strip() if isinstance(token, str) and token.strip() else None
    if token is not None and validate_secret_value(token) is not None:
        # An invalid token (control char, or a byte outside latin-1 -- the same
        # rules config.py enforces on write) must never reach a consumer that
        # would forward it as an HTTP header value. Treating it as unset here
        # means every downstream probe/step takes the "not configured" branch
        # instead of the "send it to Obsidian" branch.
        token = None

    return RawConfig(
        config_file=cfg_path,
        scratch_root=_path("scratch_root") or default_scratch,
        db_path=_path("db_path") or default_db,
        vault_path=_path("vault_path"),
        anki_data_dir=_path("anki_data_dir"),
        obsidian_api_token=token,
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
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
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


def check_obsidian_connection(token: str) -> tuple[bool, str]:
    # Validated before it ever becomes a header: http.client.putheader raises
    # ValueError with the raw value quoted in its message for a control
    # character or a non-latin1 byte, and that must not happen with a
    # credential in play. No network call is attempted for an invalid token.
    problem = validate_secret_value(token)
    if problem is not None:
        return (
            False,
            f"obsidian_api_token is invalid ({problem}); not sent. Re-copy "
            "the token as plain text from Obsidian's Local REST API plugin "
            "settings.",
        )
    request = urllib.request.Request("http://127.0.0.1:27123/", method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return True, f"reachable (HTTP {response.status})"
    except urllib.error.URLError as exc:
        return (
            False,
            f"could not reach the Local REST API plugin ({exc.reason}). "
            "Open Obsidian and enable the 'Local REST API' community plugin.",
        )
    except ValueError:
        # Belt-and-braces: some header-safety edge case our own check missed.
        # ValueError's message would quote the raw token, so it is never
        # forwarded -- only this fixed, value-free message is.
        return False, "obsidian_api_token contains characters that could not be sent as an HTTP header."
    except OSError as exc:
        return False, f"connection check failed: {exc}"


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


def probe_anki(cfg: RawConfig) -> ComponentStatus:
    if cfg.anki_data_dir is None:
        return ComponentStatus(
            "anki mirror",
            "MANUAL STEP",
            f"install Anki + AnkiMorphs ({ANKIMORPHS_URL}), then set anki_data_dir",
        )
    value = _ro_query_scalar(
        cfg.db_path, "SELECT value FROM metadata WHERE key = ?", ("anki_sync_last_revlog_id",)
    )
    if value is not None:
        return ComponentStatus("anki mirror", "READY", f"cursor at revlog id {value}")
    return ComponentStatus("anki mirror", "MISSING", "not synced yet")


def probe_fts(db_path: Path) -> ComponentStatus:
    count = _ro_query_scalar(db_path, "SELECT COUNT(*) FROM sentence_text")
    if count:
        return ComponentStatus("sentence search (fts)", "READY", f"{count} sentence(s) indexed")
    return ComponentStatus("sentence search (fts)", "MISSING", "index empty")


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
    if not cfg.obsidian_api_token:
        return ComponentStatus(
            "obsidian bridge",
            "MANUAL STEP",
            "obsidian_api_token not set; install the 'Local REST API' plugin",
        )
    ok, detail = check_obsidian_connection(cfg.obsidian_api_token)
    return ComponentStatus("obsidian bridge", "READY" if ok else "MANUAL STEP", detail)


def probe_backup(cfg: RawConfig) -> ComponentStatus:
    backups_dir = cfg.config_file.parent / "backups"
    if backups_dir.exists() and any(backups_dir.glob("*.db")):
        return ComponentStatus("backup", "READY", "snapshot present")
    return ComponentStatus("backup", "MISSING", "no snapshot yet")


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


def step_anki(cfg: RawConfig) -> StepResult:
    if cfg.anki_data_dir is None:
        return StepResult(
            "SKIP",
            f"anki_data_dir not set. Install Anki and AnkiMorphs ({ANKIMORPHS_URL}), "
            "then set anki_data_dir and re-run.",
        )
    proc = _run(anki_sync_argv())
    if proc.returncode == 0:
        return StepResult("OK", "synced")
    return StepResult(
        "ACTION NEEDED", f"anki_sync exited {proc.returncode}: {_truncated(proc.stderr)}"
    )


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
    if not cfg.obsidian_api_token:
        return StepResult(
            "SKIP",
            "obsidian_api_token not set. Install the 'Local REST API' community plugin "
            "in Obsidian, copy its API key, and re-run to set obsidian_api_token.",
        )
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


# ---------------------------------------------------------------------------
# Wizard runner
# ---------------------------------------------------------------------------


def _print_step(n: int, total: int, name: str, result: StepResult) -> None:
    suffix = f" ({result.detail})" if result.detail else ""
    print(f"[{n}/{total}] {name} ... {result.status}{suffix}")


def run_wizard(cfg_path: Path, repo_root: Path, *, assume_yes: bool) -> None:
    n = 1

    result = step_config(cfg_path, assume_yes=assume_yes)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[0], result)
    n += 1

    cfg = read_raw_config(cfg_path)

    result = step_vendor(repo_root)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[1], result)
    n += 1

    result = step_jmdict(cfg.db_path)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[2], result)
    n += 1

    result = step_anki(cfg)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[3], result)
    n += 1

    fts_result = step_fts(cfg.db_path)
    md_result = step_md_search(cfg)
    index_ok = fts_result.status == "OK" and md_result.status in ("OK", "SKIP")
    combined = StepResult(
        "OK" if index_ok else "ACTION NEEDED",
        f"fts: {fts_result.status} ({fts_result.detail}); "
        f"markdown: {md_result.status} ({md_result.detail})",
    )
    _print_step(n, TOTAL_STEPS, STEP_LABELS[4], combined)
    n += 1

    result = step_yomitan()
    _print_step(n, TOTAL_STEPS, STEP_LABELS[5], result)
    n += 1

    result = step_obsidian(cfg)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[6], result)
    n += 1

    result = step_schtasks(assume_yes=assume_yes)
    _print_step(n, TOTAL_STEPS, STEP_LABELS[7], result)
    n += 1

    result = step_backup()
    _print_step(n, TOTAL_STEPS, STEP_LABELS[8], result)
    n += 1

    cfg = read_raw_config(cfg_path)
    statuses = collect_doctor_statuses(cfg, repo_root)
    print(f"\n[{n}/{TOTAL_STEPS}] Doctor summary")
    print(render_doctor_table(statuses))
    print()
    print(EPILOGUE)


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg_path = config_mod.config_path()
    repo_root = _repo_root()

    if args.check:
        cfg = read_raw_config(cfg_path)
        statuses = collect_doctor_statuses(cfg, repo_root)
        print(render_doctor_table(statuses))
        return doctor_exit_code(statuses)

    run_wizard(cfg_path, repo_root, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
