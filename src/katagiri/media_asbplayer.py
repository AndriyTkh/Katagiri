"""E-T009: the asbplayer channel — :class:`~katagiri.media_channel.MediaChannel`
over the asbplayer WebSocket bridge's HTTP query surface
(``http://127.0.0.1:8766`` by default).

Why this channel looks different from mpv's
---------------------------------------------
:mod:`katagiri.media_mpv` has a real playhead: mpv's IPC answers "where are we,
right now" directly. asbplayer historically did not — its bridge had no
live-position query (upstream issue #1087; research.md), so "what did she just
say?" could not be answered the mpv way. That gap is what F-05
(decisions-ledger.md) closed on our side: the patched local asbplayer
build/bridge answers ``get-playback-state``, and :func:`get_playback_state`
turns that into a real live anchor (``source="live"``) — the same question mpv
answers, finally askable here too.

A *stock* extension/bridge does not have that endpoint (the bridge answers
HTTP 404), and this module treats that as the ordinary case rather than an
error: :meth:`AsbplayerChannel._probe_playback_state` swallows every failure
of the probe and the two pre-existing anchor strategies below carry on
unchanged. So everything that follows still describes what happens whenever no
live position is available — which is every probe against an unpatched
asbplayer:

1. **The anchor comes from the event log, not the socket.** The last
   ``mining``/``copy`` event in the ``event`` table (docs/db-schema.md) is the
   freshest evidence of "where the learner's attention was" that this process
   has. Its wall-clock timestamp is matched against asbplayer's own
   ``get-subtitles`` reply, whose entries this module treats as carrying a
   ``shownAt`` wall-clock field (asbplayer's own answer to not having a
   playhead: it timestamps each subtitle line as it captures it, so a real
   moment in this log can still be correlated to a video-relative
   ``start``/``end`` window even without ever querying "the current position"
   directly). That correlation is what turns a wall-clock timestamp into the
   millisecond-into-the-media ``anchor_ms`` the rest of :mod:`media_channel`
   expects (the same unit ``media_heartbeat.anchor_ms`` uses).
2. **Manual anchors are a first-class fallback, and F-05 needs their use
   counted.** :meth:`AsbplayerChannel.set_manual_anchor` lets the learner
   override a stale/wrong derived anchor; every time a probe actually resolves
   against a manual anchor (persistent override or a one-shot
   ``manual_anchor_ms`` kwarg to :meth:`~katagiri.media_channel.MediaChannel.
   media_context`), :attr:`AsbplayerChannel.manual_anchor_uses` increments and
   a ``media_manual_anchor`` event is appended — durable data, not an
   in-process tally that dies with the server, because the upstream-PR
   decision (F-05, decisions-ledger.md) is meant to fire on that data.

Anchor precedence, highest first
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. the one-shot ``manual_anchor_ms`` kwarg (``source="manual"``, counted);
2. a live ``get-playback-state`` reading (``source="live"``, never counted —
   it is not a manual anchor), but only when its ``mediaId`` agrees with the
   bound media's: a reading for some other tab is not this window's position,
   and is discarded rather than allowed to outrank everything below;
3. the persistent :meth:`AsbplayerChannel.set_manual_anchor` override
   (``source="manual"``, counted);
4. the event-log derivation of (1) above (``source`` is the event's type).

Live evidence beating the persistent override is a deliberate change from the
pre-F-05 order, where that override beat everything. An override is set once
and then goes stale silently — including overrides set back when no live feed
existed at all — while a playhead read this second is fresher by construction;
pinning the window to a moment the learner has long since played past is the
worse answer. The one-shot kwarg still outranks the live reading because there
it is the caller stating a position *for this call*, not a leftover.

Protocol surface, deliberately small and versioned
----------------------------------------------------
The asbplayer WebSocket-server bridge (the local Go checkout started by
:mod:`katagiri.asbplayer_launch`) plays two distinct roles, and this module is
a client of only one of them:

* ``ws://host:port/ws`` is where asbplayer's *browser extension* connects, as
  a WebSocket client, to receive pushed commands and answer them. This module
  never speaks that protocol — Katagiri is not the extension.
* ``GET /asbplayer/bound-media``, ``GET /asbplayer/subtitles`` and
  ``GET /asbplayer/playback-state`` are plain HTTP endpoints the bridge
  exposes for external tools: it relays the request to whichever extension is
  connected over ``/ws``, waits for the reply, and returns that reply's JSON
  body verbatim. This is the surface :class:`AsbplayerClient` queries.
  ``playback-state`` is the F-05 addition and exists only on the patched
  build; a stock bridge 404s it (see the top of this docstring).

:data:`PROTOCOL_SURFACE_VERSION` names the one thing this module assumes about
that reply shape: the ``bound-media`` reply carries a ``media`` list, one
entry per tab/file the bridge knows about (``{"id", "title"?, "active", ...}``);
"bound" means exactly one entry has ``active: true``, and none doing so is
the ordinary idle case. The ``subtitles`` reply carries a ``subtitles`` list
of ``{"text", "start", "end", "shownAt"?}`` objects. The ``playback-state``
reply carries a ``playbackState`` key that is either ``null`` (nothing
playing, or the target is not a streaming video element) or
``{"mediaId", "timestampMs", "playing"}`` — integer milliseconds, matching
``get-subtitles``' units and *not* ``seek-timestamp``'s seconds.
:func:`get_bound_media`, :func:`get_subtitles` and
:func:`get_playback_state` are
the only three places that read those shapes, and all fail closed — raising
:class:`AsbplayerProtocolError` rather than guessing — when a *required* key
is missing outright (upstream drift), while tolerating individual malformed
subtitle entries by skipping them (a bad line should not cost the whole
window). A caller two hops away (an MCP tool) sees a clear refusal instead of
a crash or a silently wrong answer. A bridge that answers with an HTTP 5xx
(routinely: no extension is currently connected to ``/ws``, so the bridge's
own request to it timed out) is treated the same as "asbplayer unreachable" —
routine, not a protocol error.

Envelope and privacy
---------------------
Every text field this module hands to :class:`~katagiri.media_channel.
MediaChannel` (`subtitle text`, `title`) is *raw* — the envelope is applied by
``media_now``/``media_context`` in the base class, never here (see that
module's docstring on why the two methods are guarded against being
overridden). Unlike mpv, asbplayer's ``id`` is a token the bridge itself
assigns per bound tab/file, never a filesystem path or a URL with a session
token in its query string — there is nothing in it to reduce with
:func:`katagiri.mpv_seek_logger.basename` the way mpv's channel must for a
real path.
"""

from __future__ import annotations

import http.client
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Final, Protocol

from katagiri import db
from katagiri.events import TS_FORMAT, append_event, recent_events
from katagiri.media_channel import MediaChannel, RawContext, RawLine, RawMoment
from katagiri.mpv_seek_logger import basename

_log = logging.getLogger("katagiri.media_asbplayer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Loopback only, per the A6 hardening note (spec.md Edge Cases: "Ports
#: :8766 (asbplayer) ... bound to 127.0.0.1 only") — this module is the
#: *client* half of that story, and it has no reason to ever dial out.
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8766
DEFAULT_REQUEST_TIMEOUT_S: Final = 2.0

#: Lines of context before/after the anchored line, matching the "subtitle
#: window centers on T" shape research.md/quickstart.md describe for US2.
DEFAULT_CONTEXT_BEFORE: Final = 2
DEFAULT_CONTEXT_AFTER: Final = 2

#: Event types the anchor is derived from — "the last mining/copy event"
#: (spec.md FR-002). `mining` is the pre-existing vocabulary
#: (docs/db-schema.md); `copy` is reserved for a future asbplayer-side
#: clipboard-mine hook, kept in the tuple now so adding that hook later needs
#: no change here.
ANCHOR_EVENT_TYPES: Final[tuple[str, ...]] = ("mining", "copy")

#: F-05 durable data point: appended every time a probe actually resolves
#: against a manual anchor. Open vocabulary, like every other `event.type`
#: (docs/db-schema.md) — no migration needed to add it.
MANUAL_ANCHOR_EVENT: Final = "media_manual_anchor"
MANUAL_ANCHOR_SESSION_ID: Final = "asbplayer-manual-anchor"

#: This module's entire assumption about the bridge's HTTP reply shape (see
#: module docstring). Bump this — and update :func:`get_bound_media`/
#: :func:`get_subtitles`/:func:`get_playback_state` together — if upstream's
#: reply shape changes.
#:
#: * v2: ``bound-media`` + ``subtitles``.
#: * v3: adds ``playback-state`` (F-05, upstream issue #1087). Additive: a
#:   stock bridge that only implements v2 still works, because the
#:   playback-state probe degrades to "no live anchor" rather than failing.
PROTOCOL_SURFACE_VERSION: Final = 3

REQUEST_GET_BOUND_MEDIA: Final = "get-bound-media"
REQUEST_GET_SUBTITLES: Final = "get-subtitles"
REQUEST_GET_PLAYBACK_STATE: Final = "get-playback-state"
SUPPORTED_COMMANDS: Final[frozenset[str]] = frozenset(
    {REQUEST_GET_BOUND_MEDIA, REQUEST_GET_SUBTITLES, REQUEST_GET_PLAYBACK_STATE}
)

#: The bridge's HTTP query surface (see module docstring) — one path per
#: supported command, never guessed or built from user input.
_COMMAND_PATHS: Final[dict[str, str]] = {
    REQUEST_GET_BOUND_MEDIA: "/asbplayer/bound-media",
    REQUEST_GET_SUBTITLES: "/asbplayer/subtitles",
    REQUEST_GET_PLAYBACK_STATE: "/asbplayer/playback-state",
}


# ---------------------------------------------------------------------------
# Failures, as values (mirrors mpv_seek_logger's MpvDisconnected/ProtocolError)
# ---------------------------------------------------------------------------


class AsbplayerError(RuntimeError):
    """Base for every failure this module raises deliberately."""


class AsbplayerUnavailable(AsbplayerError):
    """asbplayer is not reachable — routine (bridge or extension not up yet).

    Also raised for an HTTP 5xx from the bridge itself: routinely, that means
    no browser extension is currently connected over ``/ws`` for the bridge
    to relay the query to, which is functionally the same "nothing to report"
    state as the bridge process being down outright.
    """


class AsbplayerDisconnected(AsbplayerError):
    """The HTTP request to the bridge failed after a connection was made."""


class AsbplayerProtocolError(AsbplayerError):
    """asbplayer answered, but not in the shape this module depends on.

    Raised instead of guessing — see the module docstring's "fail closed"
    paragraph. Never raised for an individually malformed subtitle entry
    (those are skipped); only for a shape drift big enough that trusting the
    reply at all would be a guess.
    """


# ---------------------------------------------------------------------------
# Request/reply client — one short-lived HTTP GET per command
# ---------------------------------------------------------------------------


class CommandClient(Protocol):
    """The whole seam between this module and a live asbplayer bridge.

    Tests supply a scripted double for this Protocol; the real implementation
    is :class:`AsbplayerClient`.
    """

    def request(self, command: str) -> dict[str, Any]:
        """Ask the bridge for ``command``'s current answer, already parsed."""

    def close(self) -> None:
        """Release any held connection. Must tolerate being called twice."""


class AsbplayerClient:
    """One outstanding request at a time, over a keep-alive HTTP connection
    to the bridge's HTTP query surface (see module docstring).

    No third-party HTTP library is used — the standard library's
    :mod:`http.client` is a complete fit for "one GET, read the JSON body."
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout_s = timeout_s
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self._host, self._port, timeout=self._timeout_s
            )
        return self._conn

    def request(self, command: str) -> dict[str, Any]:
        if command not in SUPPORTED_COMMANDS:
            raise ValueError(
                f"unsupported asbplayer command {command!r}; this module only "
                f"speaks {sorted(SUPPORTED_COMMANDS)}."
            )
        path = _COMMAND_PATHS[command]
        conn = self._connection()
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            raise AsbplayerDisconnected(
                f"Request to the asbplayer bridge failed for {command!r}: {exc}"
            ) from exc

        if response.status >= 500:
            # Routinely: no extension is connected over /ws for the bridge to
            # relay this query to, so its own wait timed out. Not a protocol
            # drift — see AsbplayerUnavailable's docstring.
            raise AsbplayerUnavailable(
                f"asbplayer bridge returned HTTP {response.status} for "
                f"{command!r} (likely no extension connected)."
            )
        if response.status != 200:
            raise AsbplayerProtocolError(
                f"asbplayer bridge returned unexpected HTTP {response.status} "
                f"for {command!r}."
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AsbplayerProtocolError(
                f"asbplayer bridge sent non-JSON for {command!r}."
            ) from exc
        if not isinstance(parsed, dict):
            raise AsbplayerProtocolError(
                f"asbplayer bridge's reply to {command!r} was not a JSON object."
            )
        return parsed


# ---------------------------------------------------------------------------
# Shape validation — the two places PROTOCOL_SURFACE_VERSION lives in code
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoundMedia:
    """What :func:`get_bound_media` extracted from the active ``media`` entry."""

    media_id: str | None
    title: str | None


def get_bound_media(client: CommandClient) -> BoundMedia | None:
    """``get-bound-media``, validated. ``None`` means nothing is bound.

    The real reply is a ``media`` list — one entry per tab/file asbplayer
    knows about, each with an ``id`` the bridge assigned and an ``active``
    flag — not a single object naming one thing (see module docstring's note
    on :data:`PROTOCOL_SURFACE_VERSION`). "Bound" means exactly one entry has
    ``active: true``; none doing so is the ordinary idle case, not an error.
    ``id`` is already an opaque bridge-generated token, never a URL, so unlike
    mpv's file path it carries nothing to privacy-reduce with
    :func:`~katagiri.mpv_seek_logger.basename`.

    Fails closed with :class:`AsbplayerProtocolError` when the ``media`` key
    is missing outright (the reply shape drifted) rather than treating a
    missing key the same as an empty list (nothing bound) — those are
    different facts and must not collapse into the same code path.
    """
    reply = client.request(REQUEST_GET_BOUND_MEDIA)
    if reply.get("error"):
        return None
    media_list = reply.get("media")
    if media_list is None:
        raise AsbplayerProtocolError(
            f"asbplayer's {REQUEST_GET_BOUND_MEDIA!r} reply has no 'media' "
            "field; the upstream protocol may have drifted "
            f"(surface v{PROTOCOL_SURFACE_VERSION})."
        )
    if not isinstance(media_list, list):
        raise AsbplayerProtocolError("asbplayer's 'media' field was not a list.")

    active_entries = [m for m in media_list if isinstance(m, dict) and m.get("active")]
    if not active_entries:
        return None
    active = active_entries[0]

    media_id = active.get("id")
    if not isinstance(media_id, str) or not media_id.strip():
        raise AsbplayerProtocolError(
            "asbplayer's active media entry has no string 'id'."
        )
    title = active.get("title")
    if title is not None and not isinstance(title, str):
        raise AsbplayerProtocolError("asbplayer's 'title' field was not a string.")
    return BoundMedia(media_id=media_id, title=title)


@dataclass(frozen=True, slots=True)
class SubtitleEntry:
    """One line asbplayer reported, already validated and type-coerced."""

    text: str
    start_ms: int
    end_ms: int
    shown_at_ms: int | None


def _coerce_ms(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(round(float(value)))
        except ValueError:
            return None
    return None


def _coerce_wall_ms(value: Any) -> int | None:
    """``shownAt`` as epoch milliseconds, accepting a number or an ISO stamp."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        pass
    iso = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def get_subtitles(client: CommandClient) -> tuple[SubtitleEntry, ...]:
    """``get-subtitles``, validated. Individually malformed lines are skipped.

    Fails closed only on whole-shape drift — ``subtitles`` missing or not a
    list — the same distinction :func:`get_bound_media` draws.
    """
    reply = client.request(REQUEST_GET_SUBTITLES)
    if reply.get("error"):
        return ()
    raw_list = reply.get("subtitles")
    if raw_list is None:
        raise AsbplayerProtocolError(
            f"asbplayer's {REQUEST_GET_SUBTITLES!r} reply has no 'subtitles' "
            f"field; the upstream protocol may have drifted (surface "
            f"v{PROTOCOL_SURFACE_VERSION})."
        )
    if not isinstance(raw_list, list):
        raise AsbplayerProtocolError(
            "asbplayer's 'subtitles' field was not a list."
        )

    entries: list[SubtitleEntry] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        start_ms = _coerce_ms(item.get("start"))
        end_ms = _coerce_ms(item.get("end"))
        if start_ms is None or end_ms is None:
            continue
        entries.append(
            SubtitleEntry(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                shown_at_ms=_coerce_wall_ms(item.get("shownAt")),
            )
        )
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class PlaybackState:
    """A live playhead reading, straight from the video element.

    ``timestamp_ms`` is milliseconds into the media — the same unit as
    :attr:`SubtitleEntry.start_ms` and ``media_heartbeat.anchor_ms``, so it can
    be used as an ``anchor_ms`` with no conversion (see the units note in the
    module docstring's protocol-surface section).
    """

    media_id: str
    timestamp_ms: int
    playing: bool


def get_playback_state(client: CommandClient) -> PlaybackState | None:
    """``get-playback-state``, validated. ``None`` means "no live position".

    The bridge answers ``{"playbackState": null}`` whenever nothing matches,
    the target is not a streaming video element, or the tab did not answer —
    all of which are the ordinary "no live anchor right now" case, not errors.
    An ``error`` reply is treated the same way, matching
    :func:`get_bound_media`.

    Fails closed with :class:`AsbplayerProtocolError` on shape drift: a reply
    with no ``playbackState`` key *at all* (as opposed to an explicit ``null``
    — different facts, kept apart the way :func:`get_bound_media` keeps a
    missing ``media`` key apart from an empty list), or a present state object
    missing its string ``mediaId`` / numeric ``timestampMs``. ``playing`` is
    advisory only, so anything that is not literally ``true`` reads as
    ``False`` rather than failing the whole reading.

    Callers on the probe path should go through
    :meth:`AsbplayerChannel._probe_playback_state`, which turns every one of
    these failures — including the HTTP 404 a stock, unpatched bridge answers
    with — back into ``None``.
    """
    reply = client.request(REQUEST_GET_PLAYBACK_STATE)
    if reply.get("error"):
        return None
    if "playbackState" not in reply:
        raise AsbplayerProtocolError(
            f"asbplayer's {REQUEST_GET_PLAYBACK_STATE!r} reply has no "
            "'playbackState' field; the upstream protocol may have drifted "
            f"(surface v{PROTOCOL_SURFACE_VERSION})."
        )
    state = reply["playbackState"]
    if state is None:
        return None
    if not isinstance(state, dict):
        raise AsbplayerProtocolError(
            "asbplayer's 'playbackState' field was neither null nor an object."
        )

    media_id = state.get("mediaId")
    if not isinstance(media_id, str) or not media_id.strip():
        raise AsbplayerProtocolError(
            "asbplayer's playback state has no string 'mediaId'."
        )
    timestamp_ms = _coerce_ms(state.get("timestampMs"))
    if timestamp_ms is None:
        raise AsbplayerProtocolError(
            "asbplayer's playback state has no numeric 'timestampMs'."
        )
    return PlaybackState(
        media_id=media_id,
        timestamp_ms=timestamp_ms,
        playing=state.get("playing") is True,
    )


def _select_window(
    entries: tuple[SubtitleEntry, ...],
    anchor_ms: int,
    *,
    before: int,
    after: int,
) -> tuple[SubtitleEntry, ...]:
    """Up to ``before + 1 + after`` lines, ordered by position, around the
    line nearest ``anchor_ms`` — "the subtitle window centers on T"."""
    if not entries:
        return ()
    ordered = sorted(entries, key=lambda e: e.start_ms)
    center = min(range(len(ordered)), key=lambda i: abs(ordered[i].start_ms - anchor_ms))
    lo = max(0, center - max(before, 0))
    hi = min(len(ordered), center + max(after, 0) + 1)
    return tuple(ordered[lo:hi])


def _nearest_by_shown_at(
    entries: tuple[SubtitleEntry, ...], wall_ms: int
) -> SubtitleEntry | None:
    candidates = [e for e in entries if e.shown_at_ms is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs(e.shown_at_ms - wall_ms))  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Anchor resolution — see the module docstring's precedence list
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnchorResult:
    """Where the anchor came from, for the caller to decide whether to count
    a manual use. ``source`` is ``"manual"``, ``"live"`` (a
    :func:`get_playback_state` reading — never counted as manual), an event
    ``type`` from :data:`ANCHOR_EVENT_TYPES`, or ``"none"`` when nothing was
    derivable."""

    anchor_ms: int | None
    source: str
    event_id: str | None = None


def _wall_ms_from_stamp(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        parsed = datetime.strptime(stamp, TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _last_anchor_event(
    conn: sqlite3.Connection, event_types: tuple[str, ...]
) -> dict[str, Any] | None:
    """Most recent row across every type in ``event_types``, by id (ULID)."""
    best: dict[str, Any] | None = None
    for event_type in event_types:
        rows = recent_events(conn, limit=1, type=event_type)
        if not rows:
            continue
        row = rows[0]
        if best is None or str(row.get("id", "")) > str(best.get("id", "")):
            best = row
    return best


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


class AsbplayerChannel(MediaChannel):
    """asbplayer over the bridge's HTTP query surface. See module docstring
    for the anchor story and the WS-vs-HTTP protocol split."""

    kind = "asbplayer"

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        connect: Callable[[], CommandClient] | None = None,
        open_conn: Callable[[], sqlite3.Connection] = db.connect,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        context_before: int = DEFAULT_CONTEXT_BEFORE,
        context_after: int = DEFAULT_CONTEXT_AFTER,
        anchor_event_types: tuple[str, ...] = ANCHOR_EVENT_TYPES,
    ) -> None:
        self._connect = connect or (
            lambda: AsbplayerClient(host, port, timeout_s=request_timeout_s)
        )
        self._open_conn = open_conn
        self._request_timeout_s = request_timeout_s
        self._context_before = context_before
        self._context_after = context_after
        self._anchor_event_types = anchor_event_types
        self._client: CommandClient | None = None

        #: Persistent override set by :meth:`set_manual_anchor`; ``None``
        #: means "derive automatically" (the default).
        self._manual_override_ms: int | None = None
        #: F-05 in-process tally, mirrored durably by MANUAL_ANCHOR_EVENT.
        self.manual_anchor_uses: int = 0

    def close(self) -> None:
        """Release the HTTP connection, if one is open. Tolerates double-close."""
        self._drop_client()

    # -- manual anchor override ---------------------------------------------

    def set_manual_anchor(self, anchor_ms: int) -> None:
        """Override the derived anchor with a learner-supplied one.

        Does not by itself count as a "use" (spec.md: "its use is counted") —
        counting happens each time a probe actually resolves against it, in
        :meth:`_apply_manual_anchor`, so a manual anchor left set across many
        probes counts each application, not just the one time it was set.
        """
        if isinstance(anchor_ms, bool) or not isinstance(anchor_ms, int) or anchor_ms < 0:
            raise ValueError(
                "anchor_ms must be a non-negative integer millisecond offset."
            )
        self._manual_override_ms = anchor_ms

    def clear_manual_anchor(self) -> None:
        """Revert to automatic (event-log-derived) anchoring."""
        self._manual_override_ms = None

    @property
    def manual_anchor_active(self) -> bool:
        return self._manual_override_ms is not None

    def _apply_manual_anchor(self, conn: sqlite3.Connection, anchor_ms: int) -> None:
        self.manual_anchor_uses += 1
        try:
            append_event(
                conn,
                type=MANUAL_ANCHOR_EVENT,
                session_id=MANUAL_ANCHOR_SESSION_ID,
                media_ref=None,
                payload={"channel": self.kind, "anchor_ms": int(anchor_ms)},
            )
        except sqlite3.Error:
            _log.warning(
                "Could not persist manual-anchor usage; the F-05 data point "
                "for this call is lost (in-process counter still advanced).",
                exc_info=True,
            )

    def _resolve_anchor(
        self,
        conn: sqlite3.Connection,
        subtitles: tuple[SubtitleEntry, ...],
        *,
        override_ms: int | None,
        live_state: PlaybackState | None = None,
    ) -> AnchorResult:
        """Apply the module docstring's precedence list: one-shot kwarg, then
        the live playhead, then the persistent override, then the event log."""
        if override_ms is not None:
            return AnchorResult(anchor_ms=int(override_ms), source="manual")
        if live_state is not None:
            # Fresher than any override set earlier — and not a manual anchor,
            # so it must never feed the F-05 tally.
            return AnchorResult(anchor_ms=int(live_state.timestamp_ms), source="live")
        if self._manual_override_ms is not None:
            return AnchorResult(anchor_ms=int(self._manual_override_ms), source="manual")

        row = _last_anchor_event(conn, self._anchor_event_types)
        if row is None:
            return AnchorResult(anchor_ms=None, source="none")

        event_type = str(row.get("type") or "none")
        event_id = row.get("id")
        wall_ms = _wall_ms_from_stamp(row.get("ts_device") or row.get("ts_server"))
        if wall_ms is None:
            return AnchorResult(anchor_ms=None, source=event_type, event_id=event_id)

        nearest = _nearest_by_shown_at(subtitles, wall_ms)
        if nearest is None:
            return AnchorResult(anchor_ms=None, source=event_type, event_id=event_id)
        return AnchorResult(anchor_ms=nearest.start_ms, source=event_type, event_id=event_id)

    # -- connection lifecycle -------------------------------------------------

    def _ensure_client(self) -> CommandClient:
        if self._client is None:
            self._client = self._connect()
        return self._client

    def _drop_client(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    # -- sampling -------------------------------------------------------------

    def _probe_playback_state(self, client: CommandClient) -> PlaybackState | None:
        """The live playhead if this bridge has one, ``None`` otherwise.

        Swallows *every* :class:`AsbplayerError` (and ``OSError``) on purpose,
        unlike the rest of :meth:`_sample`: a stock bridge without the
        endpoint answers HTTP 404, which
        :meth:`AsbplayerClient.request` raises as
        :class:`AsbplayerProtocolError`. That is an expected outcome here, not
        a drifted protocol — letting it escape would fail the whole probe and
        drop a connection the other two commands are still answering on.
        """
        try:
            return get_playback_state(client)
        except (AsbplayerError, OSError) as exc:
            _log.debug(
                "asbplayer has no live playback state (%s); anchoring from the "
                "manual override / event log instead.",
                exc,
            )
            return None

    def _sample(
        self, *, manual_override_ms: int | None
    ) -> tuple[BoundMedia, tuple[SubtitleEntry, ...], AnchorResult] | None:
        """One probe: bound media + subtitles + live state + resolved anchor,
        or ``None``
        if asbplayer is unreachable or nothing is bound (routine, not an
        error — mirrors mpv's "idle" treatment)."""
        client = self._ensure_client()
        try:
            bound = get_bound_media(client)
            subtitles = get_subtitles(client)
        except (
            AsbplayerUnavailable,
            AsbplayerDisconnected,
            AsbplayerProtocolError,
            OSError,
        ) as exc:
            _log.warning("asbplayer probe failed (%s); will reconnect.", exc)
            self._drop_client()
            return None
        if bound is None:
            return None

        # One extra GET per probe, on its own error budget: a bridge that
        # cannot answer it is the stock-build case, not a failed sample.
        live_state = self._probe_playback_state(client)
        if (
            live_state is not None
            and bound.media_id
            and live_state.media_id != bound.media_id
        ):
            # The playhead the bridge volunteered belongs to some *other*
            # tab than the bound one, so it is not this window's position.
            # Anchoring on it would silently point the subtitle window at a
            # different video — and under the precedence above that wrong
            # anchor would outrank both the persistent override and the
            # event log, i.e. fail invisibly. Discard it and let the
            # pre-existing chain answer.
            _log.debug(
                "asbplayer's live playback state is for media %r, not the "
                "bound %r; ignoring it and anchoring from the manual "
                "override / event log instead.",
                live_state.media_id,
                bound.media_id,
            )
            live_state = None

        conn = self._open_conn()
        try:
            anchor = self._resolve_anchor(
                conn,
                subtitles,
                override_ms=manual_override_ms,
                live_state=live_state,
            )
            if anchor.source == "manual" and anchor.anchor_ms is not None:
                self._apply_manual_anchor(conn, anchor.anchor_ms)
        finally:
            conn.close()
        return bound, subtitles, anchor

    # -- MediaChannel interface ------------------------------------------------

    def _probe_now(self) -> RawMoment | None:
        sample = self._sample(manual_override_ms=None)
        if sample is None:
            return None
        bound, subtitles, anchor = sample

        displayed_text: str | None = None
        if anchor.anchor_ms is not None and subtitles:
            nearest = min(
                subtitles, key=lambda e: abs(e.start_ms - anchor.anchor_ms)
            )
            if nearest.start_ms <= anchor.anchor_ms <= nearest.end_ms:
                displayed_text = nearest.text

        return RawMoment(
            media_id=bound.media_id,
            anchor_ms=anchor.anchor_ms,
            displayed_text=displayed_text,
            title=bound.title,
            locator=f"asbplayer:{bound.media_id}" if bound.media_id else "asbplayer",
            detail={"anchor_source": anchor.source},
        )

    def _probe_context(self, **kwargs: Any) -> RawContext | None:
        manual_override = kwargs.get("manual_anchor_ms")
        before = int(kwargs.get("context_before", self._context_before))
        after = int(kwargs.get("context_after", self._context_after))

        sample = self._sample(manual_override_ms=manual_override)
        if sample is None:
            return None
        bound, subtitles, anchor = sample

        if anchor.anchor_ms is None:
            return RawContext(media_id=bound.media_id, anchor_ms=None, lines=())

        window = _select_window(subtitles, anchor.anchor_ms, before=before, after=after)
        lines = tuple(
            RawLine(
                text=entry.text,
                start_ms=entry.start_ms,
                end_ms=entry.end_ms,
                locator=(
                    f"asbplayer:{bound.media_id}:{entry.start_ms}"
                    if bound.media_id
                    else "asbplayer:sub"
                ),
                detail={"anchor_source": anchor.source},
            )
            for entry in window
        )
        return RawContext(media_id=bound.media_id, anchor_ms=anchor.anchor_ms, lines=lines)


__all__ = [
    "ANCHOR_EVENT_TYPES",
    "DEFAULT_CONTEXT_AFTER",
    "DEFAULT_CONTEXT_BEFORE",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "MANUAL_ANCHOR_EVENT",
    "MANUAL_ANCHOR_SESSION_ID",
    "PROTOCOL_SURFACE_VERSION",
    "REQUEST_GET_BOUND_MEDIA",
    "REQUEST_GET_PLAYBACK_STATE",
    "REQUEST_GET_SUBTITLES",
    "SUPPORTED_COMMANDS",
    "AnchorResult",
    "AsbplayerChannel",
    "AsbplayerClient",
    "AsbplayerDisconnected",
    "AsbplayerError",
    "AsbplayerProtocolError",
    "AsbplayerUnavailable",
    "BoundMedia",
    "CommandClient",
    "PlaybackState",
    "SubtitleEntry",
    "get_bound_media",
    "get_playback_state",
    "get_subtitles",
]
