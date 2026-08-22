"""Managed startup for the local asbplayer WebSocket bridge.

The bridge is only ever contacted on loopback.  Katagiri starts it solely from
the explicitly configured checkout directory, never from a user-supplied
command or a guessed location.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from katagiri.config import ConfigError, get_config
from katagiri.media_asbplayer import (
    AsbplayerClient,
    AsbplayerError,
    RawSocketWsPeer,
    get_bound_media,
)

_HOST = "127.0.0.1"
_PORT = 8766


@dataclass(frozen=True, slots=True)
class BridgeLaunchResult:
    """Outcome of checking or starting the configured local bridge."""

    launched: bool
    already_running: bool
    bridge_dir: Path | None
    reason: str | None


def bridge_is_healthy() -> bool:
    """Whether the fixed loopback port answers the expected asbplayer command."""
    peer: RawSocketWsPeer | None = None
    try:
        peer = RawSocketWsPeer(_HOST, _PORT)
        get_bound_media(AsbplayerClient(peer))
    except (AsbplayerError, OSError):
        return False
    finally:
        if peer is not None:
            peer.close()
    return True


def bridge_port_is_occupied() -> bool:
    """Whether anything is already accepting connections on the loopback port."""
    try:
        with socket.create_connection((_HOST, _PORT), timeout=0.2):
            return True
    except OSError:
        return False


def ensure_asbplayer_bridge() -> BridgeLaunchResult:
    """Reuse a healthy bridge or start the explicitly configured local checkout.

    An occupied but unhealthy port is intentionally left alone: starting a
    second process would only hide the problem and could not bind the port.
    """
    if bridge_is_healthy():
        return BridgeLaunchResult(False, True, None, None)
    if bridge_port_is_occupied():
        return BridgeLaunchResult(
            False,
            False,
            None,
            "asbplayer bridge port 8766 is already occupied but did not pass its "
            "health check; it was not started again.",
        )
    try:
        bridge_dir = get_config().asbplayer_bridge_dir
    except ConfigError:
        return BridgeLaunchResult(
            False,
            False,
            None,
            "Could not read Katagiri configuration. Set 'asbplayer_bridge_dir' to "
            "the local bridge checkout containing main.go, then restart Katagiri.",
        )
    if bridge_dir is None:
        return BridgeLaunchResult(
            False,
            False,
            None,
            "Set 'asbplayer_bridge_dir' in Katagiri's config.toml to the local "
            "bridge checkout containing main.go, then restart Katagiri.",
        )
    if not (bridge_dir / "main.go").is_file():
        return BridgeLaunchResult(
            False,
            False,
            bridge_dir,
            "The configured asbplayer bridge directory has no main.go; point "
            "'asbplayer_bridge_dir' at the local bridge checkout.",
        )
    go = shutil.which("go")
    if go is None:
        return BridgeLaunchResult(
            False,
            False,
            bridge_dir,
            "Go was not found on PATH. Install Go, then restart Katagiri to start "
            "the configured asbplayer bridge.",
        )

    kwargs: dict[str, object] = {
        "cwd": str(bridge_dir),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if os.name == "nt" and no_window is not None:
        kwargs["creationflags"] = no_window
    try:
        subprocess.Popen([go, "run", "main.go"], **kwargs)
    except OSError:
        return BridgeLaunchResult(
            False,
            False,
            bridge_dir,
            "Could not start the configured asbplayer bridge. Confirm Go can run "
            "main.go in that checkout, then restart Katagiri.",
        )
    return BridgeLaunchResult(True, False, bridge_dir, None)
