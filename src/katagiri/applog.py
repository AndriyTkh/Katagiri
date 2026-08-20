"""Unified on-disk logging for Katagiri — one file for every production run.

Katagiri is not one process. The MCP server runs for hours under a client, the
Anki sync runs from a scheduled task, ``jmdict_import`` runs from the installer
as a subprocess, and the installer itself runs by hand. Their diagnostics all
went to stderr (:mod:`katagiri.logging_setup`), which is exactly right for the
server's protocol contract and exactly useless afterwards: stderr belongs to
whoever spawned the process, and a scheduled task has no one to show it to.

This module adds a **second, additive** sink — a rotating file under
``%LOCALAPPDATA%\\Katagiri\\logs\\katagiri.log`` — so a week of real usage lands
in one place the owner can read after the fact.

Three rules make it safe to attach to a stdio MCP server:

*No stdout, ever.* stdout is the JSON-RPC wire. The stderr handler is installed
by :mod:`katagiri.logging_setup` (which also strips any stdout-bound handler it
finds); this module only ever adds a *file* handler on top. Nothing here writes
to a stream.

*No traceback leaks.* A failing handler normally prints its own traceback to
stderr via ``Handler.handleError`` — on the MCP server that is noise on the
channel the client scrapes for the startup line. :class:`_QuietRotatingFileHandler`
swallows it instead.

*Never raises.* Logging is a diagnostic, not a feature. If ``%LOCALAPPDATA%`` is
unset or the logs directory cannot be created or written, :func:`setup_logging`
attaches a :class:`logging.NullHandler` and returns normally. A run must never
fail because its log file could not be opened.

The directory is resolved through :func:`katagiri.config.config_dir`, so the log
sits beside ``config.toml`` and moves with it (tests point ``LOCALAPPDATA`` at a
tmp dir and get an isolated log for free).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Final

from katagiri.logging_setup import LOGGER_NAME, get_logger
from katagiri.logging_setup import setup_logging as setup_stderr_logging

__all__ = [
    "BACKUP_COUNT",
    "DEBUG_REPR_LIMIT",
    "ERROR_SUMMARY_LIMIT",
    "LEVEL_ENV_VAR",
    "LOGGER_NAME",
    "LOG_FILE_NAME",
    "LOGS_DIR_NAME",
    "MAX_BYTES",
    "exception_summary",
    "get_logger",
    "log_file_path",
    "log_level",
    "logs_dir",
    "run_cli",
    "setup_logging",
    "truncated_repr",
]

LOGS_DIR_NAME: Final = "logs"
LOG_FILE_NAME: Final = "katagiri.log"

#: Environment knob for verbosity. ``KATAGIRI_LOG_LEVEL=DEBUG`` is the single
#: switch that turns on argument/result reprs everywhere.
LEVEL_ENV_VAR: Final = "KATAGIRI_LOG_LEVEL"

MAX_BYTES: Final = 5 * 1024 * 1024
BACKUP_COUNT: Final = 3

#: Cap on any repr written at DEBUG. Tool arguments and results carry vault text
#: the learner or a web page wrote; a full dump is both large and untrusted.
DEBUG_REPR_LIMIT: Final = 500

#: Cap on the exception text an INFO line may carry. See :func:`exception_summary`.
ERROR_SUMMARY_LIMIT: Final = 200

_LOG_FORMAT: Final = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Marks the handler this module owns, so a second setup_logging() call is a
# no-op rather than a second file handler writing interleaved lines.
_MARKER: Final = "_katagiri_file"


class _QuietRotatingFileHandler(RotatingFileHandler):
    """A rotating handler whose own failures stay off stderr.

    ``logging.Handler.handleError`` prints a traceback to ``sys.stderr``. On the
    MCP server stderr is the channel the client (and this repo's verify tests)
    reads for the startup line, and a disk-full or locked-file error there would
    be indistinguishable from a real crash. A log we cannot write is not worth
    reporting on the one stream we must keep clean.
    """

    def handleError(self, record: logging.LogRecord) -> None:  # pragma: no cover
        pass


def logs_dir() -> Path:
    """``%LOCALAPPDATA%\\Katagiri\\logs`` — beside ``config.toml``, not in the repo."""
    # Imported lazily: config imports nothing from here, but keeping the edge
    # one-directional means applog can be imported from anywhere, including
    # modules that config itself may later want to use.
    from katagiri import config as config_mod

    return config_mod.config_dir() / LOGS_DIR_NAME


def log_file_path() -> Path:
    """Full path to the shared log file."""
    return logs_dir() / LOG_FILE_NAME


def log_level(default: int = logging.INFO) -> int:
    """Resolve the level from ``KATAGIRI_LOG_LEVEL``, falling back to ``default``.

    Accepts a name (``DEBUG``) or a number (``10``). An unparseable value falls
    back rather than raising: a typo in an environment variable must not stop the
    server from starting.
    """
    raw = os.environ.get(LEVEL_ENV_VAR, "").strip()
    if not raw:
        return default
    named = logging.getLevelName(raw.upper())
    if isinstance(named, int):
        return named
    try:
        return int(raw)
    except ValueError:
        return default


def _existing_file_handler(logger: logging.Logger) -> logging.Handler | None:
    for handler in logger.handlers:
        if getattr(handler, _MARKER, False):
            return handler
    return None


def _attach_file_handler(logger: logging.Logger) -> None:
    """Add the rotating file handler, or a NullHandler if the file is unusable."""
    handler: logging.Handler
    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Probe writability *now*. The handler is created with ``delay=True`` so
        # it does not hold the file open until something is actually logged, but
        # that also defers every permission error to the first emit — inside the
        # logging machinery, where it would be swallowed and the run would look
        # logged when it was not. One append here decides it up front.
        with path.open("a", encoding="utf-8"):
            pass
        handler = _QuietRotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    except Exception:  # noqa: BLE001 - see module docstring: never raises
        # No LOCALAPPDATA, a read-only profile, a file where the directory
        # should be. Degrade to a sink that accepts records and drops them; the
        # stderr handler installed above still carries the run's diagnostics.
        handler = logging.NullHandler()
    setattr(handler, _MARKER, True)
    logger.addHandler(handler)


def setup_logging(level: int | str | None = None) -> logging.Logger:
    """Configure the ``katagiri`` logger for stderr *and* the shared log file.

    Idempotent: repeated calls neither stack handlers nor reopen the file. This
    is a superset of :func:`katagiri.logging_setup.setup_logging` — that function
    still owns the stderr handler and the stdout guard, and is called first, so
    every existing stderr contract (notably the ``starting katagiri`` line the
    verify suites grep for) is unchanged. File logging is purely additive.

    ``level`` defaults to :func:`log_level`, i.e. ``KATAGIRI_LOG_LEVEL`` or INFO.
    """
    resolved = log_level() if level is None else level
    logger = setup_stderr_logging(resolved)
    if _existing_file_handler(logger) is None:
        _attach_file_handler(logger)
    return logger


def truncated_repr(value: Any, limit: int = DEBUG_REPR_LIMIT) -> str:
    """``repr(value)`` clipped to ``limit`` characters, with a marker when clipped.

    Used for the DEBUG-only dumps of tool arguments and results. Those carry
    untrusted vault text and can be megabytes; a bounded repr keeps a debug
    session from turning the log into a copy of the vault. A ``repr`` that itself
    raises is reported, not propagated.
    """
    try:
        text = repr(value)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        return f"<unreprable {type(value).__name__}: {type(exc).__name__}>"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated, {len(text)} chars>"


def exception_summary(exc: BaseException, limit: int = ERROR_SUMMARY_LIMIT) -> str:
    """``Type: message`` for an INFO line — first line only, and bounded.

    Only the *first* line, and that is the point rather than tidiness. A pydantic
    validation error spells the rejected arguments out in its body
    (``input_value={'word': ...}``), so logging an exception's full message at
    INFO would smuggle in exactly the untrusted tool arguments this module keeps
    behind DEBUG. The first line carries the diagnosis ("1 validation error for
    lookupArguments"); the body is echoed input. The full text is still written
    at DEBUG by the caller when the owner asks for it.
    """
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001 - diagnostics must not raise
        text = "<unprintable exception>"
    first = text.splitlines()[0] if text else ""
    if len(first) > limit:
        first = f"{first[:limit]}..."
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def run_cli(name: str, entry: Callable[[], int]) -> int:
    """Run a module ``main()`` under the shared log, bracketed by start/finish.

    Called from the ``if __name__ == "__main__"`` block of each CLI rather than
    from ``main()`` itself, and deliberately so: ``main()`` is also called
    in-process by the test suite, and installing a process-wide file handler as
    a side effect of a library call would leak logging state between tests. The
    ``__main__`` block *is* the production entry point — scheduled tasks, the
    install scripts, and the installer's own ``python -m`` subprocesses all go
    through it.

    Arguments are logged at DEBUG only; they can name vault and Anki paths.
    """
    setup_logging()
    log = get_logger(name)
    log.info("%s starting (katagiri CLI)", name)
    log.debug("%s argv %s", name, truncated_repr(sys.argv[1:]))
    started = time.perf_counter()
    try:
        code = entry()
    except SystemExit as exc:  # argparse's own exit path
        elapsed = (time.perf_counter() - started) * 1000
        log.info("%s exited early after %.0f ms (code %s)", name, elapsed, exc.code)
        raise
    except BaseException as exc:
        elapsed = (time.perf_counter() - started) * 1000
        log.error("%s failed after %.0f ms: %s", name, elapsed, exception_summary(exc))
        log.debug("%s failure detail %s", name, truncated_repr(str(exc)))
        raise
    elapsed = (time.perf_counter() - started) * 1000
    log.info("%s finished in %.0f ms (exit code %s)", name, elapsed, code)
    return code
