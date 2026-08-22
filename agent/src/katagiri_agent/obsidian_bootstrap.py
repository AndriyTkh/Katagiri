"""Auto-launch Obsidian for ``chat_graph.py`` -- dev convenience only.

Not part of the graded 005 flow (see ``chat_graph.py``'s own docstring) and
never imported by ``config.py``, ``clients.py``, ``graph.py``, or
``__main__.py``: those stay pure configuration/wiring with no side effects,
per ``clients.py``'s "no network call at import or construction time"
promise, and per ``config.py``'s own "no value ever logged" convention.

``katagiri_agent`` cannot import the primary checkout's
``katagiri.obsidian_launch`` (agent/ and the primary checkout are separate
uv projects with separate venvs -- see ``config.py``'s
``katagiri_connection`` docstring). The launch/find/is-running trio below is
therefore its own small stdlib-only copy, same rationale
``katagiri.obsidian_launch`` itself gives for not importing
``katagiri.anki_snapshot``: a different app, its own small copy, rather than
stretching a shared module's charter.

Point of this module: turn "the Obsidian Local REST API plugin isn't
running yet" from a 40-line ``httpx.ConnectError`` traceback (what
``langgraph dev`` prints when ``chat_graph.make_graph`` can't reach it) into
one clear line, after actually trying to fix it first (launch the app, wait
for the endpoint) rather than just failing faster.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

WINGET_HINT: str = "winget install --id Obsidian.Obsidian -e --source winget"

_PROCESS_NAME = "obsidian.exe"
_PROCESS_QUERY_TIMEOUT_S = 10
_POLL_INTERVAL_S = 0.5


class ObsidianNotReady(RuntimeError):
    """Raised when the Local REST API endpoint never came up in time."""


def _obsidian_is_running() -> bool:
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


def _find_obsidian_exe() -> Path | None:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        exe = Path(local_appdata) / "Obsidian" / "Obsidian.exe"
        if exe.is_file():
            return exe
    which = shutil.which("obsidian")
    return Path(which) if which else None


def _endpoint_up(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """What :func:`ensure_ready` actually did, for callers that want to log it."""

    already_up: bool
    launched: bool


def ensure_ready(url: str, timeout: float = 20.0) -> ReadinessResult:
    """Make sure something answers at ``url``'s host:port, launching Obsidian if not.

    Synchronous and quick to return once the endpoint is up (or was already
    up) -- safe to call from an async ``make_graph`` at startup, before
    ``langgraph dev``'s per-request blocking-call guard is active (the same
    window its own "slow graph import" warning tolerates).

    Raises :class:`ObsidianNotReady` -- a short, single-line message, never
    a traceback -- when Obsidian isn't installed, or the endpoint still
    isn't answering after ``timeout`` seconds.
    """
    parsed = urlsplit(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 27124

    if _endpoint_up(host, port):
        return ReadinessResult(already_up=True, launched=False)

    if _obsidian_is_running():
        launched = False
    else:
        exe = _find_obsidian_exe()
        if exe is None:
            raise ObsidianNotReady(
                "Obsidian isn't installed. Install it "
                f"({WINGET_HINT}), open the vault at "
                "docs/katagiri/katagiri, enable Settings > Community "
                "plugins > Local REST API, then retry."
            )
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        except OSError as exc:
            raise ObsidianNotReady(f"Could not launch Obsidian ({exe}): {exc}") from exc
        launched = True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _endpoint_up(host, port):
            return ReadinessResult(already_up=False, launched=launched)
        time.sleep(_POLL_INTERVAL_S)

    raise ObsidianNotReady(
        f"Obsidian's Local REST API endpoint ({host}:{port}) didn't come up "
        f"within {timeout:.0f}s. Open the vault at docs/katagiri/katagiri "
        "and check Settings > Community plugins > Local REST API is "
        "installed and enabled, then retry."
    )


__all__ = ["ObsidianNotReady", "ReadinessResult", "ensure_ready"]
