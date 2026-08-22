"""Find and launch the Obsidian desktop app.

Mirrors :mod:`katagiri.anki_launch`. Deliberately does not import from
:mod:`katagiri.anki_snapshot` -- that module's own docstring makes it a
read-only, Anki-specific bridge, and its process check is Anki-specific
(``anki.exe``). This module needs the same kind of check for a different
process, so it gets its own small copy rather than stretching that one's
charter to a second app.

No network call, ever: this module never installs Obsidian or any plugin --
it only looks for an already-installed ``Obsidian.exe`` and starts it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WINGET_HINT: str = "winget install --id Obsidian.Obsidian -e --source winget"

_PROCESS_NAME = "obsidian.exe"
_PROCESS_QUERY_TIMEOUT_S = 10


def obsidian_is_running() -> bool:
    """Best-effort check for a running Obsidian, using ``tasklist`` (stdlib only).

    A wrong answer only costs a missed/extra launch attempt, never a
    correctness problem, so every failure to determine this -- no
    ``tasklist``, a timeout, a non-Windows host -- returns ``False`` rather
    than raising.
    """
    if os.name != "nt":
        return False
    kwargs: dict[str, Any] = {}
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if no_window is not None:
        kwargs["creationflags"] = no_window
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/NH"],
            capture_output=True,
            text=True,
            timeout=_PROCESS_QUERY_TIMEOUT_S,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return _PROCESS_NAME in (completed.stdout or "").lower()


def find_obsidian_exe() -> Path | None:
    """Best-effort locate ``Obsidian.exe``: common install dirs, then PATH."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        exe = Path(local_appdata) / "Obsidian" / "Obsidian.exe"
        if exe.is_file():
            return exe
    which = shutil.which("obsidian")
    return Path(which) if which else None


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Outcome of one :func:`launch_obsidian` call."""

    launched: bool
    already_running: bool
    path: Path | None
    reason: str | None


def launch_obsidian() -> LaunchResult:
    """Start Obsidian if it isn't already running."""
    if obsidian_is_running():
        return LaunchResult(launched=False, already_running=True, path=None, reason=None)
    exe = find_obsidian_exe()
    if exe is None:
        return LaunchResult(
            launched=False,
            already_running=False,
            path=None,
            reason=f"Obsidian not found. Install it ({WINGET_HINT}) and try again.",
        )
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
    except OSError as exc:
        return LaunchResult(launched=False, already_running=False, path=exe, reason=str(exc))
    return LaunchResult(launched=True, already_running=False, path=exe, reason=None)
