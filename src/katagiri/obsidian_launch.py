"""Find and launch the Obsidian desktop app.

Mirrors :mod:`katagiri.anki_launch`. Deliberately does not import from
:mod:`katagiri.anki_snapshot` -- that module's own docstring makes it a
read-only, Anki-specific bridge, and its process check is Anki-specific
(``anki.exe``). This module needs the same kind of check for a different
process, so it gets its own small copy rather than stretching that one's
charter to a second app.

No network call, ever: this module never installs Obsidian or any plugin --
it only looks for an already-installed ``Obsidian.exe`` and starts it.

:func:`ensure_obsidian_mcp_ready` additionally waits for the Local REST API
plugin's own port to accept a connection, one TCP handshake at a time -- it
never speaks HTTP or touches the vault, so it stays true to "no network call"
above in spirit (nothing here reads or writes vault content). Its caller is
responsible for the loopback-exposure check (``mcp_server.security_scan``
already covers this port); this module only answers "is something there".
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

from katagiri.obsidian_proxy import OBSIDIAN_HOST, OBSIDIAN_PORT

WINGET_HINT: str = "winget install --id Obsidian.Obsidian -e --source winget"

_PROCESS_NAME = "obsidian.exe"
_PROCESS_QUERY_TIMEOUT_S = 10
_MCP_READY_TIMEOUT_S = 20.0
_MCP_POLL_INTERVAL_S = 0.5


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
    """Best-effort locate ``Obsidian.exe``: common install dirs, then PATH.

    Checks the legacy per-user location first, then the ``Programs`` subdir
    the current installer (and winget) actually use -- same two-tier
    autodetect as :func:`katagiri.anki_launch.find_anki_exe`.
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        exe = Path(local_appdata) / "Obsidian" / "Obsidian.exe"
        if exe.is_file():
            return exe
        exe = Path(local_appdata) / "Programs" / "Obsidian" / "Obsidian.exe"
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


def _endpoint_up(host: str, port: int, timeout: float = 1.0) -> bool:
    """Best-effort TCP connect check. Never raises; a closed port is just ``False``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class McpReadinessResult:
    """Outcome of one :func:`ensure_obsidian_mcp_ready` call."""

    launched: bool
    already_running: bool
    endpoint_ready: bool
    reason: str | None


def ensure_obsidian_mcp_ready(
    host: str = OBSIDIAN_HOST,
    port: int = OBSIDIAN_PORT,
    timeout: float = _MCP_READY_TIMEOUT_S,
) -> McpReadinessResult:
    """Launch Obsidian if needed, then wait for its Local REST API port to answer.

    Never raises -- katagiri's own startup must proceed regardless of
    Obsidian's fate, same contract as :func:`launch_obsidian`.
    ``endpoint_ready=False`` is the caller's signal to log a warning instead
    of a success line; it does not mean anything was left half-started.

    This only confirms *something* is listening on ``host:port`` -- it is a
    bare TCP check, not an HTTPS request, so it cannot and does not verify
    the Local REST API plugin (vs. some other process) is what answered, nor
    whether the port is loopback-only. Loopback exposure is a distinct
    concern already owned by ``mcp_server.security_scan`` (this port is in
    its ``HARDENED_PORTS``); callers that newly rely on this endpoint being
    reachable by other agents should run that check too before trusting it.
    """
    if _endpoint_up(host, port):
        return McpReadinessResult(False, True, True, None)

    launch = launch_obsidian()
    if launch.reason is not None and not launch.already_running:
        # Not installed, or Popen itself failed -- polling would never help.
        return McpReadinessResult(False, False, False, launch.reason)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _endpoint_up(host, port):
            return McpReadinessResult(launch.launched, launch.already_running, True, None)
        time.sleep(_MCP_POLL_INTERVAL_S)

    return McpReadinessResult(
        launch.launched,
        launch.already_running,
        False,
        f"Obsidian's Local REST API endpoint ({host}:{port}) did not answer "
        f"within {timeout:.0f}s. Check Settings > Community plugins > Local "
        "REST API is installed and enabled, then reconnect the MCP client.",
    )
