"""Logging setup for Katagiri.

**stdout is forbidden.** Katagiri runs as an MCP server over *stdio* transport:
stdout is the JSON-RPC wire. Any byte written to stdout that is not a
well-formed JSON-RPC message corrupts the protocol stream and the client
disconnects. Therefore every log record — and every diagnostic of any kind —
goes to **stderr only**.

`setup_logging` attaches exactly one `StreamHandler(sys.stderr)` to the
`katagiri` logger and refuses to leave any stdout-bound handler in place.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

LOGGER_NAME: Final = "katagiri"
_LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def _is_stdout_handler(handler: logging.Handler) -> bool:
    stream = getattr(handler, "stream", None)
    if stream is None:
        return False
    if stream is sys.stdout or stream is sys.__stdout__:
        return True
    # Compare by file descriptor too: wrappers (e.g. io.TextIOWrapper around
    # fd 1) are not identity-equal to sys.stdout but are just as fatal.
    try:
        return stream.fileno() == 1
    except (OSError, ValueError, AttributeError):
        return False


def setup_logging(level: int | str = logging.INFO) -> logging.Logger:
    """Configure the ``katagiri`` logger to write to stderr only.

    Idempotent: repeated calls do not stack handlers. Any pre-existing
    stdout-bound handler on the logger is removed, and propagation to the root
    logger is disabled so a stdout handler installed elsewhere cannot leak
    Katagiri records onto the MCP wire.
    """
    logger = logging.getLogger(LOGGER_NAME)

    # Strip stdout handlers from our logger *and* from root: a library that
    # called logging.basicConfig() with a stdout stream would otherwise corrupt
    # the MCP stdio wire.
    for target in (logger, logging.getLogger()):
        for handler in list(target.handlers):
            if _is_stdout_handler(handler):
                target.removeHandler(handler)

    if not any(getattr(h, "_katagiri_stderr", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        handler._katagiri_stderr = True  # type: ignore[attr-defined]
        logger.addHandler(handler)

    logger.setLevel(level)
    # Do not propagate: the root logger may have stdout handlers we do not own.
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the ``katagiri`` logger (stderr-only by construction)."""
    if name is None or name == LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
