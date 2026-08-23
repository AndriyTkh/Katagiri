"""Find and launch the Anki desktop app.

Separate from :mod:`katagiri.anki_snapshot`, whose own docstring makes it
read-only by contract ("no writes of any kind to anything Anki owns").
Starting the Anki process is not a read, so it gets its own narrow module
rather than stretching that one's charter.

No network call, ever: this module never installs Anki or any add-on --
it only looks for an already-installed ``anki.exe`` and starts it. See
``SETUP_PROMPT.md`` for the (agent-run, one-time) install step.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from katagiri.anki_snapshot import anki_is_running
from katagiri.config import ConfigError, load_config

WINGET_HINT: str = "winget install --id Anki.Anki -e --source winget"


def find_anki_exe() -> Path | None:
    """Locate ``anki.exe``: configured override first, then autodetect.

    When multiple Anki versions are installed side by side (e.g. an add-on
    like AnkiMorphs pinned to an older release than whatever is newest on
    PATH), autodetect order alone cannot tell them apart. ``anki_exe_path``
    in config.toml settles it explicitly; a configured path that no longer
    exists falls through to autodetect rather than failing outright, since a
    stale config entry should not be worse than having none.

    Autodetect itself checks the per-user install location the modern Anki
    installer (and winget) use by default, then the all-users
    ``Program Files`` location, then falls back to whatever ``anki`` resolves
    to on PATH.

    Reads config with ``create_missing=False``: this function is on the
    ``installer --check`` doctor path (via ``_anki_manual_step_detail``),
    which must never write ``config.toml`` as a side effect of a read-only
    check. An absent config file is treated the same as "no override
    configured" -- exactly like a ``ConfigError`` from a malformed one.
    """
    try:
        configured = load_config(create_missing=False).anki_exe_path
    except ConfigError:
        configured = None
    if configured is not None and configured.is_file():
        return configured
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        exe = Path(local_appdata) / "Programs" / "Anki" / "anki.exe"
        if exe.is_file():
            return exe
    program_files = os.environ.get("PROGRAMFILES")
    if program_files:
        exe = Path(program_files) / "Anki" / "anki.exe"
        if exe.is_file():
            return exe
    which = shutil.which("anki")
    return Path(which) if which else None


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Outcome of one :func:`launch_anki` call."""

    launched: bool
    already_running: bool
    path: Path | None
    reason: str | None


def launch_anki() -> LaunchResult:
    """Start Anki if it isn't already running.

    Fires the process and returns immediately -- never waits for Anki to
    exit, never touches ``collection.anki2`` itself (that stays
    ``anki_snapshot``'s job, and its read-only contract).
    """
    if anki_is_running():
        return LaunchResult(launched=False, already_running=True, path=None, reason=None)
    exe = find_anki_exe()
    if exe is None:
        return LaunchResult(
            launched=False,
            already_running=False,
            path=None,
            reason=f"Anki not found. Install it ({WINGET_HINT}) and try again.",
        )
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
    except OSError as exc:
        return LaunchResult(launched=False, already_running=False, path=exe, reason=str(exc))
    return LaunchResult(launched=True, already_running=False, path=exe, reason=None)
