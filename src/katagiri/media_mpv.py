"""E-T005: the mpv channel — :class:`~katagiri.media_channel.MediaChannel` over
mpv's JSON IPC named pipe.

mpv is the primary consumption surface (plan.md; ``CHANNEL_PRECEDENCE`` in
``media_channel.py`` puts it first), so this is the first channel implemented.
It answers two questions the learner asks while watching a local file:
"what is playing, and where" (:meth:`MediaChannel.media_now`) and "what did
she just say" (:meth:`MediaChannel.media_context`).

Connection reuse
-----------------
:mod:`katagiri.mpv_seek_logger` already speaks mpv's JSON IPC protocol over
the same named pipe (``\\\\.\\pipe\\mpv-katagiri``) — it is the one existing
piece of mpv IPC code in this codebase, shipped pre-gate as the sole D6
exemption. Rather than opening a second implementation of that protocol, this
module imports its pieces directly: :class:`~katagiri.mpv_seek_logger.
MpvClient` (request/reply framing, demultiplexing async ``event`` messages
from replies), :func:`~katagiri.mpv_seek_logger.connect_pipe` /
:class:`~katagiri.mpv_seek_logger.NamedPipeTransport` (the raw
``open(path, "r+b", buffering=0)`` duplex handle), and
:func:`~katagiri.mpv_seek_logger.basename` (the privacy boundary: only the
last path segment of a played file or its title is ever surfaced — nothing
upstream of the last separator). ``MpvClient.get_property`` already tolerates
"idle" (returns ``None`` rather than raising) and this module treats that
the same way the seek logger does: no media loaded is not an error.

What "context" means for mpv
-----------------------------
spec.md's acceptance scenario for this channel is "subtitle lines around the
playhead return inside the untrusted-data envelope" (FR-004/FR-005 anchor
screenshot and lyrics to the same subtitle pipeline). mpv's IPC exposes the
*currently displayed* subtitle line directly as properties — ``sub-text``,
``sub-start``, ``sub-end`` — but has no property for "the N lines before and
after this one"; reaching a true multi-line window would mean parsing the
subtitle file itself (the ``sub_lines`` table shape in db-schema.md), which
belongs to a subtitle-import pipeline this task does not build. So for this
MVP, the "window" :meth:`MpvChannel._probe_context` returns is exactly the
one line mpv currently has on screen (zero lines if none is showing) — a
faithful, honestly-scoped subset of the eventual multi-line window, not a
fabricated one. Both :meth:`_probe_now` and :meth:`_probe_context` sample the
same ``sub-text``/``sub-start``/``sub-end`` properties: ``_probe_now``'s
``displayed_text`` is "what line is on screen right now" (the resume-pointer
sense :class:`~katagiri.media_channel.RawMoment` documents), and
``_probe_context`` wraps that same line as the (single-element) context
window.

Heartbeat
---------
``media_now``/``media_context`` are the pure envelope-enforcing methods
:mod:`katagiri.media_channel` defines and this module must not override (see
that module's docstring). Persisting the `media_heartbeat` row is therefore a
separate, explicit step this module owns: :func:`write_heartbeat` stores a
successful moment's :meth:`~katagiri.media_channel.MediaMoment.heartbeat_row`
into the single-row table, and :meth:`MpvChannel.probe_and_persist` does both
in one call (the shape a caller — e.g. the future MCP tool registration in
T007 — actually wants). Liveness of that *persisted* row is answered by
:meth:`MpvChannel.heartbeat_is_live`, which reuses
:meth:`~katagiri.media_channel.HeartbeatRow.is_live` (itself
:func:`~katagiri.media_channel.is_live`/:func:`~katagiri.media_channel.
is_stale` — the one staleness mechanism, never reinvented here). This is
deliberately a *second* read path from ``media_now()``'s own freshness: a
probe that succeeds right now is trivially "live" by the timestamp it just
stamped, which would make a "stale heartbeat" scenario untestable through
``media_now`` alone. ``heartbeat_is_live`` instead asks "how old is the row
we last actually wrote", which can be stale even while a live pipe would
answer a fresh probe immediately afterwards — exactly the "stale heartbeat
never reported live" contract T005 asks for.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, Final

from datetime import datetime

from katagiri.events import utc_now_stamp
from katagiri.media_channel import (
    DEFAULT_STALE_THRESHOLD_MS,
    HeartbeatRow,
    MediaChannel,
    MediaMoment,
    RawContext,
    RawLine,
    RawMoment,
)
from katagiri.mpv_seek_logger import (
    MpvClient,
    MpvDisconnected,
    MpvProtocolError,
    PIPE_PATH,
    Transport,
    basename,
    connect_pipe,
)

#: mpv properties this channel polls. Same names mpv_seek_logger already
#: polls for time-pos/path/media-title; sub-text/-start/-end are the
#: currently-displayed-subtitle triad (see module docstring).
_PROP_TIME_POS: Final = "time-pos"
_PROP_PATH: Final = "path"
_PROP_TITLE: Final = "media-title"
_PROP_SUB_TEXT: Final = "sub-text"
_PROP_SUB_START: Final = "sub-start"
_PROP_SUB_END: Final = "sub-end"


def _as_float(value: Any) -> float | None:
    """Best-effort float coercion of an mpv property value.

    Mirrors ``mpv_seek_logger._as_float``: mpv answers numeric properties as
    JSON numbers over IPC, but ``bool`` is a ``float`` subtype-adjacent trap
    in Python (``isinstance(True, int)`` is ``True``) and idle/missing values
    arrive as ``None`` — neither should silently become ``0.0``.
    """
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


def _as_ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


class MpvChannel(MediaChannel):
    """mpv over its JSON IPC named pipe. See module docstring for scope."""

    kind = "mpv"

    def __init__(
        self,
        *,
        pipe_path: str = PIPE_PATH,
        connect: Callable[[], Transport] | None = None,
    ) -> None:
        self._pipe_path = pipe_path
        self._connect = connect or (lambda: connect_pipe(pipe_path))
        self._client: MpvClient | None = None

    def close(self) -> None:
        """Release the pipe handle, if one is open. Tolerates being called
        when nothing is connected — mirrors :class:`Transport.close`'s own
        "must tolerate being called twice" contract."""
        if self._client is not None:
            self._client.close()
        self._client = None

    # -- connection lifecycle -------------------------------------------------

    def _ensure_client(self) -> MpvClient | None:
        """The live client, connecting lazily. ``None`` means mpv is not
        reachable right now — entirely routine (mpv not running yet), not an
        error, exactly as :mod:`mpv_seek_logger`'s own reconnect loop treats
        a missing pipe."""
        if self._client is not None:
            return self._client
        try:
            transport = self._connect()
        except OSError:
            return None
        self._client = MpvClient(transport)
        return self._client

    def _drop_client(self) -> None:
        """Discard a client that just proved unusable, so the next probe
        reconnects from scratch rather than repeating a broken read."""
        if self._client is not None:
            self._client.close()
        self._client = None

    # -- MediaChannel interface ------------------------------------------------

    def _probe_now(self) -> RawMoment | None:
        sample = self._sample()
        if sample is None:
            return None
        file_name, anchor_ms, title, sub_text = sample
        display_title = basename(title) if isinstance(title, str) and title else file_name
        return RawMoment(
            media_id=file_name,
            anchor_ms=anchor_ms,
            displayed_text=sub_text,
            title=display_title,
            locator=f"mpv:{file_name}" if file_name else "mpv",
        )

    def _probe_context(self, **kwargs: Any) -> RawContext | None:
        sample = self._sample()
        if sample is None:
            return None
        file_name, anchor_ms, _title, sub_text = sample
        lines: tuple[RawLine, ...]
        if sub_text:
            lines = (
                RawLine(
                    text=sub_text,
                    start_ms=self._last_sub_start_ms,
                    end_ms=self._last_sub_end_ms,
                    locator=f"mpv:{file_name}:sub" if file_name else "mpv:sub",
                ),
            )
        else:
            lines = ()
        return RawContext(media_id=file_name, anchor_ms=anchor_ms, lines=lines)

    # -- probing ----------------------------------------------------------------

    def _sample(self) -> tuple[str | None, int | None, Any, str | None] | None:
        """One poll of mpv's state, or ``None`` if nothing is playing/reachable.

        Returns ``(file_name, anchor_ms, title, sub_text)``. ``file_name`` is
        already reduced to a basename (the privacy boundary mpv_seek_logger
        established); ``title`` is returned raw so callers can decide how to
        reduce it (``_probe_now`` reduces it the same way).
        """
        client = self._ensure_client()
        if client is None:
            return None
        try:
            time_pos = _as_float(client.get_property(_PROP_TIME_POS))
            path = client.get_property(_PROP_PATH)
            title = client.get_property(_PROP_TITLE)
            sub_text_raw = client.get_property(_PROP_SUB_TEXT)
            sub_start = _as_float(client.get_property(_PROP_SUB_START))
            sub_end = _as_float(client.get_property(_PROP_SUB_END))
        except (MpvDisconnected, MpvProtocolError, OSError):
            self._drop_client()
            return None

        if time_pos is None or not isinstance(path, str) or not path:
            # Pipe connected, but idle: nothing loaded. Not an error.
            self._last_sub_start_ms = None
            self._last_sub_end_ms = None
            return None

        file_name = basename(path)
        anchor_ms = _as_ms(time_pos)
        sub_text = sub_text_raw if isinstance(sub_text_raw, str) and sub_text_raw.strip() else None
        self._last_sub_start_ms = _as_ms(sub_start) if sub_text else None
        self._last_sub_end_ms = _as_ms(sub_end) if sub_text else None
        return file_name, anchor_ms, title, sub_text

    # Set by _sample(); declared here so a probe that never ran still has the
    # attribute (idle-before-first-probe reads as "no timing info", not AttributeError).
    _last_sub_start_ms: int | None = None
    _last_sub_end_ms: int | None = None

    # -- heartbeat persistence ----------------------------------------------

    def probe_and_persist(
        self,
        conn: sqlite3.Connection,
        *,
        now: Callable[[], str] = utc_now_stamp,
    ) -> MediaMoment | None:
        """:meth:`media_now` plus writing the result into `media_heartbeat`.

        Every successful probe carries its own fresh timestamp, so persisting
        it is what lets :meth:`heartbeat_is_live` later answer "is this still
        live" without re-touching mpv. Returns the (enveloped) moment, or
        ``None`` without writing anything when nothing is playing.
        """
        moment = self.media_now(now=now)
        if moment is not None:
            write_heartbeat(conn, moment.heartbeat_row())
        return moment

    def heartbeat_is_live(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime,
        threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
    ) -> bool:
        """Whether the *persisted* `media_heartbeat` row is still fresh.

        Independent of whether mpv's pipe would answer a probe issued right
        now — a row written 20s ago is stale at a 15s threshold even though a
        fresh probe this instant would stamp its own live timestamp. This is
        the "stale heartbeat never reported live" contract: a crashed or
        wedged writer leaves a row that ages out on its own, per
        :func:`~katagiri.media_channel.is_stale`.
        """
        row = read_heartbeat(conn)
        if row is None:
            return False
        return row.is_live(now=now, threshold_ms=threshold_ms)


# ---------------------------------------------------------------------------
# `media_heartbeat` read/write — the single-row table, no second mechanism
# ---------------------------------------------------------------------------


def write_heartbeat(conn: sqlite3.Connection, row: HeartbeatRow) -> None:
    """Upsert ``row`` into the single ``id = 1`` `media_heartbeat` row."""
    conn.execute(
        """
        INSERT INTO media_heartbeat (id, media_id, anchor_ms, displayed_text, updated_ts)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            media_id = excluded.media_id,
            anchor_ms = excluded.anchor_ms,
            displayed_text = excluded.displayed_text,
            updated_ts = excluded.updated_ts
        """,
        (row.media_id, row.anchor_ms, row.displayed_text, row.updated_ts),
    )


def read_heartbeat(conn: sqlite3.Connection) -> HeartbeatRow | None:
    """The current `media_heartbeat` row, or ``None`` if nothing was ever written.

    Reads by position, not by column name, so this works whether ``conn``
    uses ``sqlite3.Row`` (the normal :func:`katagiri.db.connect` pragma set)
    or a plain connection, as a test double might.
    """
    cursor = conn.execute(
        "SELECT media_id, anchor_ms, displayed_text, updated_ts "
        "FROM media_heartbeat WHERE id = 1"
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return HeartbeatRow(
        media_id=row[0],
        anchor_ms=row[1],
        displayed_text=row[2],
        updated_ts=row[3],
    )


__all__ = [
    "MpvChannel",
    "read_heartbeat",
    "write_heartbeat",
]
