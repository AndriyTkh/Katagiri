r"""Write-only rewind telemetry: record mpv seeks into Katagiri's event log.

When you rewind a line of dialogue you are telling yourself something was too
fast, too slurred, or too unfamiliar. That signal exists only while you are
watching; nothing can reconstruct it afterwards from the file. So this module
starts recording now and asks nothing of the rest of the system: it is
**write-only**. There is no MCP tool, no agent-facing read path, and no
analysis here — just capture, so the data is already there when the analysis
side is built.

It runs as its own process alongside mpv:

    python -m katagiri.mpv_seek_logger run
    python -m katagiri.mpv_seek_logger status

SETUP (once). Add this exact line to your ``mpv.conf`` — on Windows that is
``%APPDATA%\mpv\mpv.conf`` — so every mpv instance exposes its JSON IPC pipe::

    input-ipc-server=\\.\pipe\mpv-katagiri

Nothing else changes about how you launch mpv. The logger tolerates mpv not
running: it retries the pipe until mpv appears and reconnects after it closes.

OPTIONAL, at logon. Registering a scheduled task is the user's call, not this
module's, so nothing here writes to the scheduler. To do it by hand::

    schtasks /Create /TN "Katagiri mpv seek logger" /SC ONLOGON ^
        /TR "\"%LOCALAPPDATA%\\Programs\\Python\\Python312\\pythonw.exe\" -m katagiri.mpv_seek_logger run" ^
        /RL LIMITED /F

(Adjust the interpreter path to the environment that has Katagiri installed;
``pythonw.exe`` keeps it windowless. ``/RL LIMITED`` because this needs no
privileges. Delete it again with ``schtasks /Delete /TN "Katagiri mpv seek
logger" /F``.)

PROTOCOL. mpv's JSON IPC is newline-delimited JSON over a named pipe: requests
carry a ``request_id``, replies echo it, and asynchronous ``{"event": ...}``
notifications interleave freely. This module speaks it with the standard
library only — a plain ``open(r'\\.\pipe\mpv-katagiri', 'r+b')`` is a working
duplex handle on a Windows named pipe — so the slice adds no dependency.

Positions are sampled by *polling* ``get_property`` once per second rather than
by ``observe_property``. An observed ``time-pos`` fires a property-change
notification several times a second, which is a firehose to read, throttle and
test for no benefit here: a rewind is metres wide and one-second sampling sees
it. The cost is honest and bounded — ``from_s`` is the last sampled position, so
a measured delta carries up to one poll interval of error, which is why the
threshold (2 s) sits above that noise floor. A ``seek`` event is still what
*gates* detection, so ordinary playback advancing between samples is never
mistaken for a jump.

DEDUPE. Seek events are appended with **no** ``dedupe_key``, deliberately.
Rewinding to the same timestamp twice is not a duplicate — it is the strongest
signal this logger can capture, a line that needed two passes. A key that
distinguished those repeats would need a per-day counter, which cannot survive
a logger restart without reading the day's rows back out of the DB first: that
buys a read path, a race, and a failure mode where a restart silently collapses
genuine rewinds, in exchange for nothing. Nor is there anything to collapse:
this is a single local process appending only what it observed on the wire, so
unlike a retryable tool call there is no duplicate-write path to guard. Event
ids are ULIDs, so uniqueness is already guaranteed.

PRIVACY. Only the **basename** of the playing file is recorded, never its
directory — the event log is durable, append-only and backed up, and a media
library's folder tree says a great deal about its owner. ``media-title`` is
reduced the same way in case mpv fell back to something path-shaped. No
subtitle text, no file contents, nothing else from the machine.

DURABILITY. Records are batched in memory and flushed every 10 seconds over a
short-lived connection that is opened, committed and closed inside the flush.
The event DB is Katagiri's source of truth and a study session may be using it;
a logger is not important enough to hold a write lock across a whole film. If a
flush fails the batch is kept for the next one, and only a pathological backlog
(``MAX_PENDING``) is dropped, loudly.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Final, Iterable, Protocol

from katagiri import db, events
from katagiri.logging_setup import get_logger, setup_logging

PIPE_PATH: Final = r"\\.\pipe\mpv-katagiri"
MPV_CONF_LINE: Final = r"input-ipc-server=\\.\pipe\mpv-katagiri"

EVENT_TYPE: Final = "seek"
SESSION_PREFIX: Final = "mpv-logger-"

#: A jump of at least this many seconds counts. Below it, playback jitter and
#: the one-second sampling error are indistinguishable from a real seek.
DEFAULT_THRESHOLD_S: Final = 2.0
DEFAULT_POLL_INTERVAL_S: Final = 1.0
DEFAULT_FLUSH_INTERVAL_S: Final = 10.0
DEFAULT_RECONNECT_DELAY_S: Final = 3.0

#: Ticks a seek notification waits for the position to settle before it is
#: written off as jitter. One is enough: a reply can only be pre-seek by the
#: round trip it raced with.
SEEK_GRACE_TICKS: Final = 1

#: Lines one request may read past before the stream is declared unusable.
MAX_LINES_PER_REQUEST: Final = 500

#: Records held when flushing keeps failing. Past this the oldest go, because a
#: logger must not become the reason the machine runs out of memory.
MAX_PENDING: Final = 2000

STATUS_HOURS: Final = 24

_log = get_logger("mpv_seek_logger")


class MpvDisconnected(RuntimeError):
    """The IPC stream ended — mpv exited, or the pipe was closed under us."""


class MpvProtocolError(RuntimeError):
    """The IPC stream said something this module cannot make sense of."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class Transport(Protocol):
    """A newline-delimited duplex byte stream.

    The whole seam between this module and Windows. The real implementation is
    a named pipe; tests inject a scripted in-memory mpv, which is why none of
    the protocol or detection logic below needs mpv installed to be exercised.
    """

    def send(self, line: bytes) -> None:
        """Write one already-newline-terminated request."""

    def readline(self) -> bytes:
        """Read one line. Empty bytes means end of stream."""

    def close(self) -> None:
        """Release the handle. Must tolerate being called twice."""


class NamedPipeTransport:
    """:class:`Transport` over an mpv JSON IPC named pipe.

    Opened unbuffered on purpose: a raw handle's ``readline`` consumes exactly
    up to the newline, so a read can never block waiting for a buffer to fill
    with bytes mpv has no reason to send yet.
    """

    def __init__(self, path: str = PIPE_PATH) -> None:
        self.path = path
        self._handle = open(path, "r+b", buffering=0)  # noqa: SIM115 - lifetime is ours

    def send(self, line: bytes) -> None:
        self._handle.write(line)
        try:
            self._handle.flush()
        except (OSError, ValueError):  # unbuffered: nothing to flush anyway
            pass

    def readline(self) -> bytes:
        return self._handle.readline()

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass


def connect_pipe(path: str = PIPE_PATH) -> Transport:
    """Open the mpv pipe. Raises :class:`OSError` while mpv is absent."""
    return NamedPipeTransport(path)


# ---------------------------------------------------------------------------
# Protocol client
# ---------------------------------------------------------------------------


class MpvClient:
    """Request/reply over a :class:`Transport`, with events set aside.

    mpv interleaves asynchronous notifications with replies, so every read is
    a demultiplex: notifications are queued for :meth:`take_events` and only
    the reply bearing our ``request_id`` returns.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._request_id = 0
        self._events: deque[dict[str, Any]] = deque()

    def close(self) -> None:
        self._transport.close()

    def take_events(self) -> list[dict[str, Any]]:
        """Drain and return the notifications queued so far."""
        drained = list(self._events)
        self._events.clear()
        return drained

    def get_property(self, name: str) -> Any:
        """Value of mpv property ``name``, or ``None`` if it is unavailable.

        ``None`` covers the ordinary idle case (``time-pos`` has no value when
        nothing is loaded) as well as a genuinely unknown property; neither is
        worth an exception on a polling path.
        """
        self._request_id += 1
        request_id = self._request_id
        self._send({"command": ["get_property", name], "request_id": request_id})

        for _ in range(MAX_LINES_PER_REQUEST):
            message = self._read_json()
            if message is None:
                continue
            if "event" in message:
                self._events.append(message)
                continue
            if message.get("request_id") != request_id:
                # A reply to a request we already gave up on. Discard it rather
                # than mistake it for this one's answer.
                continue
            if message.get("error") == "success":
                return message.get("data")
            return None

        raise MpvProtocolError(
            f"No reply to get_property({name!r}) within "
            f"{MAX_LINES_PER_REQUEST} lines of IPC traffic."
        )

    def _send(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            self._transport.send(line)
        except OSError as exc:
            raise MpvDisconnected(f"Writing to the mpv pipe failed: {exc}") from exc

    def _read_json(self) -> dict[str, Any] | None:
        try:
            raw = self._transport.readline()
        except OSError as exc:
            raise MpvDisconnected(f"Reading from the mpv pipe failed: {exc}") from exc
        if not raw:
            raise MpvDisconnected("mpv closed the IPC stream.")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            _log.warning("Ignoring a non-JSON line from the mpv IPC stream.")
            return None
        return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def basename(value: str | None) -> str | None:
    r"""Last path segment of ``value``, with the directory discarded.

    Handles Windows and POSIX separators and strips a URL query or fragment, so
    ``C:\Users\me\Media\ep01.mkv`` and ``https://host/a/b.mkv?t=3`` both reduce
    to a bare name. This is the privacy boundary: nothing upstream of the last
    separator is ever recorded.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for cut in ("?", "#"):
        head, sep, _ = text.partition(cut)
        if sep:
            text = head
    text = text.replace("\\", "/").rstrip("/")
    if not text:
        return None
    return text.rsplit("/", 1)[-1] or None


@dataclass(frozen=True, slots=True)
class SeekRecord:
    """One observed jump, ready to be appended."""

    file: str | None
    title: str | None
    from_s: float
    to_s: float
    delta_s: float
    direction: str
    ts: str
    day: str

    def payload(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "title": self.title,
            "from_s": self.from_s,
            "to_s": self.to_s,
            "delta_s": self.delta_s,
            "direction": self.direction,
        }


class SeekTracker:
    """Turns a poll of mpv's state into :class:`SeekRecord` objects.

    Deliberately free of I/O and of the clock beyond the stamp function, so the
    detection rules are testable on their own terms.

    Detection is gated on mpv's ``seek`` notification: without one, a position
    that moved is just playback. With one, the jump is measured from the last
    *sampled* position to the position read after the seek. Because a reply can
    race the seek it belongs to, a notification whose jump is still sub-
    threshold is carried for :data:`SEEK_GRACE_TICKS` more polls (keeping the
    original pre-seek position) before being written off as jitter.
    """

    def __init__(
        self,
        *,
        threshold_s: float = DEFAULT_THRESHOLD_S,
        stamp: Callable[[], str] = events.utc_now_stamp,
        local_date: Callable[[], str] | None = None,
    ) -> None:
        if threshold_s <= 0:
            raise ValueError(f"threshold_s must be positive; got {threshold_s}.")
        self.threshold_s = float(threshold_s)
        self._stamp = stamp
        self._local_date = local_date or _local_date
        self._last_pos: float | None = None
        self._last_file: str | None = None
        self._pending: tuple[float, int] | None = None

    def poll(self, client: MpvClient) -> list[SeekRecord]:
        """Sample mpv once and return whatever seeks that revealed.

        ``time-pos`` is requested first so that any notification read on this
        tick is known to precede the position it is compared against.
        """
        time_pos = _as_float(client.get_property("time-pos"))
        path = client.get_property("path")
        title = client.get_property("media-title")
        seek_seen = any(
            message.get("event") == "seek" for message in client.take_events()
        )
        return self.observe(
            time_pos=time_pos, path=path, title=title, seek_seen=seek_seen
        )

    def observe(
        self,
        *,
        time_pos: float | None,
        path: Any,
        title: Any,
        seek_seen: bool,
    ) -> list[SeekRecord]:
        """Apply one sample. Split from :meth:`poll` so it can be driven directly."""
        file_name = basename(path if isinstance(path, str) else None)
        # A different file is a new timeline, not a seek within the old one.
        if file_name != self._last_file:
            self._last_file = file_name
            self._last_pos = time_pos
            self._pending = None
            return []

        if time_pos is None:
            # Idle or between files: no position to reason about, and holding a
            # stale one would misdate the next jump.
            self._last_pos = None
            self._pending = None
            return []

        if seek_seen:
            if self._last_pos is None:
                # Seeked before we ever sampled a position; there is no honest
                # ``from_s`` to record, so the jump is dropped rather than guessed.
                _log.debug("Seek seen before any position was sampled; ignored.")
            elif self._pending is None:
                self._pending = (self._last_pos, SEEK_GRACE_TICKS)
            # An already-pending seek keeps its original pre-seek position, so a
            # burst of rewinds is recorded as the one jump the learner made.

        records: list[SeekRecord] = []
        if self._pending is not None:
            from_s, grace = self._pending
            delta = time_pos - from_s
            if abs(delta) >= self.threshold_s:
                records.append(
                    self._record(
                        file_name,
                        title if isinstance(title, str) else None,
                        from_s,
                        time_pos,
                        delta,
                    )
                )
                self._pending = None
            elif grace > 0:
                self._pending = (from_s, grace - 1)
            else:
                self._pending = None

        self._last_pos = time_pos
        return records

    def _record(
        self,
        file_name: str | None,
        title: str | None,
        from_s: float,
        to_s: float,
        delta: float,
    ) -> SeekRecord:
        return SeekRecord(
            file=file_name,
            # Reduced the same way as the path: mpv falls back to the filename
            # for media-title, and a fallback could carry a directory with it.
            title=basename(title) if title else None,
            from_s=round(from_s, 3),
            to_s=round(to_s, 3),
            delta_s=round(delta, 3),
            direction="back" if delta < 0 else "forward",
            ts=self._stamp(),
            day=self._local_date(),
        )


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _local_date() -> str:
    """Today's date in local time — the calendar day a rewind belongs to."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def flush_records(
    records: Iterable[SeekRecord],
    open_conn: Callable[[], sqlite3.Connection] = db.connect,
) -> int:
    """Append ``records`` in one short transaction and return how many landed.

    The connection is opened and closed inside this call: the write lock is
    held for a batch, never for the length of a viewing session.
    """
    batch = list(records)
    if not batch:
        return 0

    conn = open_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for record in batch:
                events.append_event(
                    conn,
                    type=EVENT_TYPE,
                    session_id=f"{SESSION_PREFIX}{record.day}",
                    media_ref=None,
                    ts_device=record.ts,
                    payload=record.payload(),
                    # No dedupe_key: see the module docstring. Two rewinds to the
                    # same second are two rewinds.
                )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()
    return len(batch)


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------


def run_logger(
    *,
    pipe_path: str = PIPE_PATH,
    connect: Callable[[], Transport] | None = None,
    open_conn: Callable[[], sqlite3.Connection] | None = None,
    threshold_s: float = DEFAULT_THRESHOLD_S,
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
    reconnect_delay_s: float = DEFAULT_RECONNECT_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    max_ticks: int | None = None,
) -> int:
    """Poll mpv and record seeks until interrupted. Returns a process exit code.

    Ctrl+C is a clean stop, not a crash: the pending batch is flushed and 0 is
    returned. mpv coming and going is likewise routine — a closed pipe means
    wait ``reconnect_delay_s`` and try again, forever, because the logger's job
    is to be already running the next time you watch something.

    ``connect``/``open_conn``/``sleep``/``monotonic``/``max_ticks`` exist so the
    loop can be driven deterministically in tests. With ``open_conn`` left at
    ``None`` the schema is brought up to date once at startup and each flush
    then uses a plain connection.
    """
    connect_fn = connect or (lambda: connect_pipe(pipe_path))
    if open_conn is None:
        # Migrate once, here, rather than on every flush: a flush should be a
        # write and nothing else.
        db.open_db().close()
        open_conn = db.connect

    tracker = SeekTracker(threshold_s=threshold_s)
    pending: list[SeekRecord] = []
    client: MpvClient | None = None
    last_flush = monotonic()
    ticks = 0
    interrupted = False
    _log.info(
        "mpv seek logger started; polling %s every %.1fs (threshold %.1fs).",
        pipe_path,
        poll_interval_s,
        threshold_s,
    )

    try:
        while max_ticks is None or ticks < max_ticks:
            ticks += 1

            if client is None:
                try:
                    client = MpvClient(connect_fn())
                except OSError:
                    # mpv is not running. Entirely normal; say so once per
                    # attempt at debug level and keep waiting.
                    _log.debug("mpv IPC pipe unavailable; retrying.")
                    last_flush = _maybe_flush(
                        pending, open_conn, monotonic, last_flush, flush_interval_s
                    )
                    sleep(reconnect_delay_s)
                    continue
                _log.info("Connected to mpv IPC.")

            try:
                pending.extend(tracker.poll(client))
            except (MpvDisconnected, MpvProtocolError, OSError) as exc:
                _log.info("mpv IPC ended (%s); will reconnect.", exc)
                client.close()
                client = None
                tracker = SeekTracker(threshold_s=threshold_s)
                last_flush = _maybe_flush(
                    pending, open_conn, monotonic, last_flush, flush_interval_s
                )
                sleep(reconnect_delay_s)
                continue

            _trim_pending(pending)
            last_flush = _maybe_flush(
                pending, open_conn, monotonic, last_flush, flush_interval_s
            )
            sleep(poll_interval_s)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if pending:
            _flush(pending, open_conn)
        if client is not None:
            client.close()

    _log.info(
        "mpv seek logger stopped%s.", " on Ctrl+C" if interrupted else ""
    )
    return 0


def _maybe_flush(
    pending: list[SeekRecord],
    open_conn: Callable[[], sqlite3.Connection],
    monotonic: Callable[[], float],
    last_flush: float,
    flush_interval_s: float,
) -> float:
    now = monotonic()
    if now - last_flush < flush_interval_s:
        return last_flush
    if pending:
        _flush(pending, open_conn)
    return now


def _flush(
    pending: list[SeekRecord], open_conn: Callable[[], sqlite3.Connection]
) -> None:
    """Flush in place. A failure keeps the batch for the next attempt."""
    try:
        written = flush_records(pending, open_conn)
    except (sqlite3.Error, OSError) as exc:
        _log.warning(
            "Could not write %d seek event(s) yet (%s); keeping them for the "
            "next flush.",
            len(pending),
            exc,
        )
        return
    _log.debug("Wrote %d seek event(s).", written)
    pending.clear()


def _trim_pending(pending: list[SeekRecord]) -> None:
    if len(pending) <= MAX_PENDING:
        return
    dropped = len(pending) - MAX_PENDING
    del pending[:dropped]
    _log.error(
        "Dropped %d unwritten seek event(s): the backlog exceeded %d, so the "
        "event DB has been unwritable for a long time.",
        dropped,
        MAX_PENDING,
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(
    conn: sqlite3.Connection,
    *,
    hours: int = STATUS_HOURS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Seek counts over the last ``hours``. The only read path in this module.

    Filtered on ``ts_device`` — when the seek happened — rather than on
    ``ts_server``, so a batch flushed late (or after a reconnect) is counted in
    the window it belongs to.
    """
    if hours < 1:
        raise ValueError(f"hours must be at least 1; got {hours}.")
    moment = now or datetime.now(timezone.utc)
    since = (moment - timedelta(hours=hours)).strftime(events.TS_FORMAT)

    rows = conn.execute(
        "SELECT payload FROM event WHERE type = ? AND ts_device >= ? "
        "ORDER BY id DESC",
        (EVENT_TYPE, since),
    ).fetchall()

    back = 0
    forward = 0
    other = 0
    seconds_back = 0.0
    per_file: dict[str, int] = {}
    for row in rows:
        raw = row[0]
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        direction = payload.get("direction")
        if direction == "back":
            back += 1
            delta = _as_float(payload.get("delta_s"))
            if delta is not None:
                seconds_back += abs(delta)
            name = payload.get("file")
            if isinstance(name, str) and name:
                per_file[name] = per_file.get(name, 0) + 1
        elif direction == "forward":
            forward += 1
        else:
            other += 1

    top = sorted(per_file.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
    return {
        "hours": hours,
        "since": since,
        "total": len(rows),
        "back": back,
        "forward": forward,
        "unclassified": other,
        "seconds_rewound": round(seconds_back, 1),
        "top_files_by_rewind": [{"file": name, "back": count} for name, count in top],
    }


def format_status(summary: dict[str, Any]) -> str:
    """Render :func:`status` for a terminal."""
    lines = [
        f"Seeks in the last {summary['hours']}h (since {summary['since']}):",
        f"  rewinds  : {summary['back']}"
        f"  ({summary['seconds_rewound']:.0f}s replayed)",
        f"  forwards : {summary['forward']}",
    ]
    if summary["unclassified"]:
        lines.append(f"  unclassified: {summary['unclassified']}")
    if summary["top_files_by_rewind"]:
        lines.append("  most rewound:")
        for entry in summary["top_files_by_rewind"]:
            lines.append(f"    {entry['back']:>4}  {entry['file']}")
    elif not summary["total"]:
        lines.append("  nothing recorded yet.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.mpv_seek_logger",
        description=(
            "Record mpv rewinds into Katagiri's event log. Write-only: nothing "
            "reads this data yet."
        ),
    )
    parser.add_argument(
        "--verbose", action="store_true", help="debug-level logging on stderr"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="poll mpv until interrupted")
    run_cmd.add_argument("--pipe", default=PIPE_PATH, help=f"default: {PIPE_PATH}")
    run_cmd.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD_S)
    run_cmd.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL_S
    )
    run_cmd.add_argument(
        "--flush-interval", type=float, default=DEFAULT_FLUSH_INTERVAL_S
    )

    status_cmd = sub.add_parser("status", help="seek counts from the event log")
    status_cmd.add_argument("--hours", type=int, default=STATUS_HOURS)
    status_cmd.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Diagnostics go to stderr; ``status`` output to stdout.

    Unlike the MCP server this is an ordinary CLI process — stdout is a
    terminal here, never a JSON-RPC wire — so a report may be printed. Logging
    still goes to stderr, per :mod:`katagiri.logging_setup`.
    """
    args = build_parser().parse_args(argv)
    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    if args.command == "status":
        conn = db.open_db()
        try:
            summary = status(conn, hours=args.hours)
        finally:
            conn.close()
        text = (
            json.dumps(summary, ensure_ascii=False, indent=2)
            if args.json
            else format_status(summary)
        )
        print(text, file=sys.stdout)
        return 0

    return run_logger(
        pipe_path=args.pipe,
        threshold_s=args.threshold,
        poll_interval_s=args.poll_interval,
        flush_interval_s=args.flush_interval,
    )


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FLUSH_INTERVAL_S",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_THRESHOLD_S",
    "EVENT_TYPE",
    "MPV_CONF_LINE",
    "MAX_PENDING",
    "PIPE_PATH",
    "SESSION_PREFIX",
    "MpvClient",
    "MpvDisconnected",
    "MpvProtocolError",
    "NamedPipeTransport",
    "SeekRecord",
    "SeekTracker",
    "Transport",
    "basename",
    "build_parser",
    "connect_pipe",
    "flush_records",
    "format_status",
    "main",
    "run_logger",
    "status",
]
