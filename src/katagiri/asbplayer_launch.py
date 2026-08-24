"""Managed startup for the local asbplayer WebSocket bridge.

009 replaced the standalone Go binary with an in-process bridge
(:mod:`katagiri.asbplayer_bridge`): there is no longer a child process to spawn,
no Go toolchain to find, and no checkout directory to point at. This module
keeps its pre-009 public contract — :class:`BridgeLaunchResult`,
:func:`bridge_is_healthy`, :func:`bridge_port_is_occupied`,
:func:`ensure_asbplayer_bridge` — unchanged in name and shape (plan.md decision
6), because ``mcp_server.py``'s startup block and
``tests/test_mcp_tools.py``'s ``test_main_serves_stdio_and_nothing_else``
monkeypatch depend on it staying that way. Only the body of
``ensure_asbplayer_bridge`` changed: a healthy port is reused exactly as
before; an occupied-but-unhealthy port is still left strictly alone (spec US3
acceptance 3 — the escape hatch for an operator still running the old Go
bridge by hand); otherwise the bridge is started in-process via
:mod:`katagiri.asbplayer_bridge`, imported lazily so a caller who never starts
a bridge never pays for importing that module's heavier dependencies.

``bridge_dir`` on the result is now vestigial and always ``None`` — there is
no checkout directory to report. ``asbplayer_bridge_dir`` in config.toml is
read only to notice it is still set and log that it is obsolete-but-accepted
(FR-014); it is never acted on.

The bridge is only ever contacted on loopback by default. A non-default,
non-loopback bind is a deliberate operator choice made through the same
environment surface :class:`katagiri.asbplayer_bridge.BridgeConfig` reads, and
is logged as a warning by that module when it happens (FR-009).
"""

from __future__ import annotations

import atexit
import http.client
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from katagiri.config import ConfigError, get_config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from katagiri.asbplayer_bridge import AsbplayerBridgeServer

_log = logging.getLogger("katagiri.asbplayer_launch")

_HOST = "127.0.0.1"
_PORT = 8766
_HEALTH_TIMEOUT_S = 0.5

_OCCUPIED_UNHEALTHY_REASON = (
    "asbplayer bridge port 8766 is already occupied but did not pass its "
    "health check; it was not started again."
)

#: The running in-process server, once started by :func:`ensure_asbplayer_bridge`.
#: ``None`` until then, and after :func:`stop_asbplayer_bridge` runs. Tests read
#: this directly (there is no other way to reach the bound port for teardown).
_server: "AsbplayerBridgeServer | None" = None


@dataclass(frozen=True, slots=True)
class BridgeLaunchResult:
    """Outcome of checking or starting the managed local bridge.

    ``bridge_dir`` is always ``None`` post-009: the bridge no longer runs from
    a checkout directory, so there is nothing to report there.
    """

    launched: bool
    already_running: bool
    bridge_dir: Path | None
    reason: str | None


def bridge_is_healthy() -> bool:
    """Whether the fixed loopback port answers HTTP at all.

    Any HTTP response — even an AnkiConnect-forwarding error — proves the
    bridge process itself is up; only a failed connection means it is not.
    """
    conn = http.client.HTTPConnection(_HOST, _PORT, timeout=_HEALTH_TIMEOUT_S)
    try:
        conn.request("GET", "/")
        conn.getresponse().read()
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()
    return True


def bridge_port_is_occupied() -> bool:
    """Whether anything is already accepting connections on the loopback port."""
    try:
        with socket.create_connection((_HOST, _PORT), timeout=0.2):
            return True
    except OSError:
        return False


def ensure_asbplayer_bridge() -> BridgeLaunchResult:
    """Reuse a healthy bridge, stand down for an occupied one, or start ours.

    An occupied but unhealthy port is intentionally left alone: starting a
    second server would only hide the problem and could not bind the port
    anyway (FR-011, spec US3 acceptance 3 — this is how a learner running the
    old Go bridge by hand keeps working).

    Otherwise the bridge is started **in this process** via
    :mod:`katagiri.asbplayer_bridge` (FR-001, FR-013): no Go toolchain lookup,
    no child process, no separate source file to run. The host/port it binds
    come from that module's own environment surface
    (:meth:`BridgeConfig.from_env`), which
    defaults to loopback and honors a documented host override with its own
    warning for a non-loopback bind (FR-009).
    """
    if bridge_is_healthy():
        return BridgeLaunchResult(
            launched=False, already_running=True, bridge_dir=None, reason=None
        )
    if bridge_port_is_occupied():
        return BridgeLaunchResult(
            launched=False,
            already_running=False,
            bridge_dir=None,
            reason=_OCCUPIED_UNHEALTHY_REASON,
        )

    _report_obsolete_bridge_dir_if_configured()

    from katagiri.asbplayer_bridge import AsbplayerBridgeServer, BridgeConfig

    server = AsbplayerBridgeServer(config=BridgeConfig.from_env())
    try:
        server.start()
    except OSError as exc:
        return BridgeLaunchResult(
            launched=False,
            already_running=False,
            bridge_dir=None,
            reason=f"Could not start the in-process asbplayer bridge: {exc}",
        )

    global _server
    _server = server
    atexit.register(stop_asbplayer_bridge)
    return BridgeLaunchResult(
        launched=True, already_running=False, bridge_dir=None, reason=None
    )


def stop_asbplayer_bridge() -> None:
    """Stop the in-process bridge started by :func:`ensure_asbplayer_bridge`.

    Idempotent, and safe to call whether or not a bridge was ever started —
    the daemon-thread listener :mod:`katagiri.asbplayer_bridge` starts would be
    released when the host process exits regardless (FR-012), but an explicit
    stop is what lets tests release the port between cases instead of waiting
    on process exit.
    """
    global _server
    server, _server = _server, None
    if server is not None:
        server.stop()


def _report_obsolete_bridge_dir_if_configured() -> None:
    """Log once if ``asbplayer_bridge_dir`` is still set (FR-014).

    The key stays loadable and produces no error; it is simply never acted on
    any more. A :class:`ConfigError` here means configuration could not be
    read at all, which is not this function's concern to raise on — startup
    proceeds and the in-process bridge is attempted regardless.
    """
    try:
        bridge_dir = get_config().asbplayer_bridge_dir
    except ConfigError:
        return
    if bridge_dir is not None:
        _log.warning(
            "config.toml sets 'asbplayer_bridge_dir' (%s), but it is obsolete: "
            "the asbplayer bridge now runs in-process and no longer starts "
            "from a checkout directory. The key is accepted but ignored; "
            "remove it from config.toml.",
            bridge_dir,
        )
