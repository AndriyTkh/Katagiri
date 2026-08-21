"""E-T009: the asbplayer channel — :class:`~katagiri.media_channel.MediaChannel`
over asbplayer's WebSocket server (``ws://127.0.0.1:8766`` by default).

Why this channel looks different from mpv's
---------------------------------------------
:mod:`katagiri.media_mpv` has a real playhead: mpv's IPC answers "where are we,
right now" directly. asbplayer does not — its WS surface has no live-position
query (upstream issue #1087; research.md), so "what did she just say?" cannot
be answered the mpv way. Two decisions in research.md/spec.md follow from that:

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

Protocol surface, deliberately small and versioned
----------------------------------------------------
:data:`PROTOCOL_SURFACE_VERSION` names the one thing this module assumes about
asbplayer's wire format: :data:`REQUEST_GET_BOUND_MEDIA` answers with a
``url`` field (``None``/empty when nothing is bound) and an optional ``title``;
:data:`REQUEST_GET_SUBTITLES` answers with a ``subtitles`` list of
``{"text", "start", "end", "shownAt"?}`` objects. :func:`get_bound_media` and
:func:`get_subtitles` are the only two places that read those shapes, and both
fail closed — raising :class:`AsbplayerProtocolError` rather than guessing —
when a *required* key is missing outright (upstream drift), while tolerating
individual malformed subtitle entries by skipping them (a bad line should not
cost the whole window). A caller two hops away (an MCP tool) sees a clear
refusal instead of a crash or a silently wrong answer.

No third-party WebSocket library is used — the project has none, and adding one
for a client that speaks two commands would be a larger dependency than the
client itself. :class:`RawSocketWsPeer` is a minimal RFC 6455 client: it does
the HTTP upgrade handshake and masks/unmasks text frames, nothing else
(fragmented frames are refused rather than reassembled — see the class
docstring). This mirrors how :mod:`katagiri.mpv_seek_logger` speaks mpv's own
JSON IPC with the standard library only, and :class:`AsbplayerClient` mirrors
:class:`~katagiri.mpv_seek_logger.MpvClient`'s request/reply shape.

Envelope and privacy
---------------------
Every text field this module hands to :class:`~katagiri.media_channel.
MediaChannel` (`subtitle text`, `title`) is *raw* — the envelope is applied by
``media_now``/``media_context`` in the base class, never here (see that
module's docstring on why the two methods are guarded against being
overridden). Media URLs are reduced with :func:`katagiri.mpv_seek_logger.
basename` before they ever reach a :class:`~katagiri.media_channel.RawMoment`
or the event log — the same privacy boundary mpv's channel uses, because a
streaming URL's query string can carry a session token and its path can carry
exactly the kind of directory structure that module's docstring warns about.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import socket
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
DEFAULT_PATH: Final = "/"
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

#: This module's entire assumption about asbplayer's wire shape (see module
#: docstring). Bump this — and update :func:`get_bound_media`/
#: :func:`get_subtitles` together — if upstream's reply shape changes.
PROTOCOL_SURFACE_VERSION: Final = 1

REQUEST_GET_BOUND_MEDIA: Final = "get-bound-media"
REQUEST_GET_SUBTITLES: Final = "get-subtitles"
SUPPORTED_COMMANDS: Final[frozenset[str]] = frozenset(
    {REQUEST_GET_BOUND_MEDIA, REQUEST_GET_SUBTITLES}
)

_WS_GUID: Final = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_OPCODE_TEXT: Final = 0x1
_OPCODE_CLOSE: Final = 0x8


# ---------------------------------------------------------------------------
# Failures, as values (mirrors mpv_seek_logger's MpvDisconnected/ProtocolError)
# ---------------------------------------------------------------------------


class AsbplayerError(RuntimeError):
    """Base for every failure this module raises deliberately."""


class AsbplayerUnavailable(AsbplayerError):
    """asbplayer is not reachable — routine (extension not running yet)."""


class AsbplayerDisconnected(AsbplayerError):
    """The WS connection ended after having been established."""


class AsbplayerProtocolError(AsbplayerError):
    """asbplayer answered, but not in the shape this module depends on.

    Raised instead of guessing — see the module docstring's "fail closed"
    paragraph. Never raised for an individually malformed subtitle entry
    (those are skipped); only for a shape drift big enough that trusting the
    reply at all would be a guess.
    """


# ---------------------------------------------------------------------------
# Minimal RFC 6455 client — handshake + masked text frames, nothing else
# ---------------------------------------------------------------------------


class WsPeer(Protocol):
    """The whole seam between this module and a live asbplayer socket.

    Tests supply a scripted double for this Protocol (mirroring
    :class:`katagiri.mpv_seek_logger.Transport`); the real implementation is
    :class:`RawSocketWsPeer`.
    """

    def send_text(self, text: str) -> None:
        """Send one text frame."""

    def recv_text(self, *, timeout: float) -> str | None:
        """Read one text frame, or ``None`` on timeout or a close frame."""

    def close(self) -> None:
        """Release the socket. Must tolerate being called twice."""


def _apply_mask(data: bytes, mask_key: bytes) -> bytes:
    if not mask_key:
        return data
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))


def _encode_client_frame(payload: bytes, *, opcode: int = _OPCODE_TEXT) -> bytes:
    """One masked client->server frame. Client frames MUST be masked (RFC 6455 §5.1)."""
    header = bytearray()
    header.append(0x80 | opcode)  # FIN=1, no fragmentation from this client
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)  # MASK=1
    elif length < (1 << 16):
        header.append(0x80 | 126)
        header += length.to_bytes(2, "big")
    else:
        header.append(0x80 | 127)
        header += length.to_bytes(8, "big")
    mask_key = secrets.token_bytes(4)
    header += mask_key
    return bytes(header) + _apply_mask(payload, mask_key)


class RawSocketWsPeer:
    """:class:`WsPeer` over a real TCP socket, speaking just enough RFC 6455.

    No fragmentation support: a server frame with ``FIN=0`` raises
    :class:`AsbplayerProtocolError` rather than being reassembled. asbplayer's
    two-command replies are small JSON objects, not a case that needs
    multi-frame messages, and refusing a shape this module was not built to
    handle is exactly the "fail closed" contract the module docstring
    describes.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        path: str = DEFAULT_PATH,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        self._handshake(host, port, path)

    # -- handshake ----------------------------------------------------------

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            self._sock.sendall(request)
            status, headers = self._read_http_headers()
        except OSError as exc:
            raise AsbplayerUnavailable(
                f"asbplayer WS handshake failed: {exc}"
            ) from exc

        if status != 101:
            raise AsbplayerUnavailable(
                f"asbplayer WS handshake failed: HTTP {status} (expected 101)."
            )
        expected_accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise AsbplayerProtocolError(
                "asbplayer WS handshake Sec-WebSocket-Accept did not match; "
                "refusing a connection that may not be a real WS peer."
            )

    def _read_http_headers(self) -> tuple[int, dict[str, str]]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise AsbplayerUnavailable(
                    "asbplayer closed the connection during the WS handshake."
                )
            data += chunk
        head, _, rest = data.partition(b"\r\n\r\n")
        self._buffer = rest
        lines = head.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
        return status, headers

    # -- framing --------------------------------------------------------------

    def send_text(self, text: str) -> None:
        try:
            self._sock.sendall(_encode_client_frame(text.encode("utf-8")))
        except OSError as exc:
            raise AsbplayerDisconnected(f"Writing to asbplayer failed: {exc}") from exc

    def recv_text(self, *, timeout: float) -> str | None:
        self._sock.settimeout(timeout)
        try:
            opcode, payload = self._read_frame()
        except socket.timeout:
            return None
        except OSError as exc:
            raise AsbplayerDisconnected(f"Reading from asbplayer failed: {exc}") from exc
        if opcode == _OPCODE_CLOSE:
            return None
        if opcode != _OPCODE_TEXT:
            raise AsbplayerProtocolError(
                f"Unexpected WS opcode {opcode:#x} from asbplayer (expected a "
                "text frame)."
            )
        return payload.decode("utf-8", errors="replace")

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._sock.recv(max(4096, size))
            if not chunk:
                raise AsbplayerDisconnected("asbplayer closed the WS connection.")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(2)
        first, second = header[0], header[1]
        fin = first & 0x80
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        mask_key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = _apply_mask(payload, mask_key)
        if not fin:
            raise AsbplayerProtocolError(
                "Fragmented WS frames from asbplayer are not supported by "
                "this small client."
            )
        return opcode, payload

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def connect_ws(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    path: str = DEFAULT_PATH,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
) -> WsPeer:
    """Open a real WS connection to asbplayer. Raises while it is absent."""
    return RawSocketWsPeer(host, port, path=path, timeout=timeout)


# ---------------------------------------------------------------------------
# Request/reply client — mirrors MpvClient's shape
# ---------------------------------------------------------------------------


class AsbplayerClient:
    """One outstanding request at a time; no pipelining, no request ids.

    asbplayer's two-command surface has no need for either: this module never
    issues a second request before the first has answered.
    """

    def __init__(self, peer: WsPeer, *, timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S) -> None:
        self._peer = peer
        self._timeout_s = timeout_s

    def close(self) -> None:
        self._peer.close()

    def request(self, command: str) -> dict[str, Any]:
        if command not in SUPPORTED_COMMANDS:
            raise ValueError(
                f"unsupported asbplayer command {command!r}; this module only "
                f"speaks {sorted(SUPPORTED_COMMANDS)}."
            )
        self._peer.send_text(json.dumps({"command": command}, ensure_ascii=False))
        raw = self._peer.recv_text(timeout=self._timeout_s)
        if raw is None:
            raise AsbplayerDisconnected(f"No reply to {command!r} from asbplayer.")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AsbplayerProtocolError(
                f"asbplayer sent non-JSON for {command!r}."
            ) from exc
        if not isinstance(parsed, dict):
            raise AsbplayerProtocolError(
                f"asbplayer's reply to {command!r} was not a JSON object."
            )
        return parsed


# ---------------------------------------------------------------------------
# Shape validation — the two places PROTOCOL_SURFACE_VERSION lives in code
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BoundMedia:
    """What :func:`get_bound_media` extracted, already privacy-reduced."""

    media_id: str | None
    title: str | None


def get_bound_media(client: AsbplayerClient) -> BoundMedia | None:
    """``get-bound-media``, validated. ``None`` means nothing is bound.

    Fails closed with :class:`AsbplayerProtocolError` when the ``url`` key is
    missing outright (the reply shape drifted) rather than treating a missing
    key the same as an explicit null (nothing bound) — those are different
    facts and must not collapse into the same code path.
    """
    reply = client.request(REQUEST_GET_BOUND_MEDIA)
    if reply.get("error"):
        return None
    if "url" not in reply:
        raise AsbplayerProtocolError(
            f"asbplayer's {REQUEST_GET_BOUND_MEDIA!r} reply has no 'url' "
            "field; the upstream protocol may have drifted "
            f"(surface v{PROTOCOL_SURFACE_VERSION})."
        )
    url = reply["url"]
    if url is None or (isinstance(url, str) and not url.strip()):
        return None
    if not isinstance(url, str):
        raise AsbplayerProtocolError("asbplayer's 'url' field was not a string.")
    title = reply.get("title")
    if title is not None and not isinstance(title, str):
        raise AsbplayerProtocolError("asbplayer's 'title' field was not a string.")
    return BoundMedia(media_id=basename(url), title=basename(title) if title else None)


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


def get_subtitles(client: AsbplayerClient) -> tuple[SubtitleEntry, ...]:
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
# Anchor resolution — event log first, manual override wins when set
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnchorResult:
    """Where the anchor came from, for the caller to decide whether to count
    a manual use. ``source`` is ``"manual"``, an event ``type`` from
    :data:`ANCHOR_EVENT_TYPES`, or ``"none"`` when nothing was derivable."""

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
    """asbplayer over its WS server. See module docstring for the anchor story."""

    kind = "asbplayer"

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
        connect: Callable[[], WsPeer] | None = None,
        open_conn: Callable[[], sqlite3.Connection] = db.connect,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        context_before: int = DEFAULT_CONTEXT_BEFORE,
        context_after: int = DEFAULT_CONTEXT_AFTER,
        anchor_event_types: tuple[str, ...] = ANCHOR_EVENT_TYPES,
    ) -> None:
        self._connect = connect or (
            lambda: connect_ws(host, port, path=path, timeout=request_timeout_s)
        )
        self._open_conn = open_conn
        self._request_timeout_s = request_timeout_s
        self._context_before = context_before
        self._context_after = context_after
        self._anchor_event_types = anchor_event_types
        self._client: AsbplayerClient | None = None

        #: Persistent override set by :meth:`set_manual_anchor`; ``None``
        #: means "derive automatically" (the default).
        self._manual_override_ms: int | None = None
        #: F-05 in-process tally, mirrored durably by MANUAL_ANCHOR_EVENT.
        self.manual_anchor_uses: int = 0

    def close(self) -> None:
        """Release the WS connection, if one is open. Tolerates double-close."""
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
    ) -> AnchorResult:
        manual_ms = override_ms if override_ms is not None else self._manual_override_ms
        if manual_ms is not None:
            return AnchorResult(anchor_ms=int(manual_ms), source="manual")

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

    def _ensure_client(self) -> AsbplayerClient | None:
        if self._client is not None:
            return self._client
        try:
            peer = self._connect()
        except (OSError, AsbplayerUnavailable):
            return None
        self._client = AsbplayerClient(peer, timeout_s=self._request_timeout_s)
        return self._client

    def _drop_client(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    # -- sampling -------------------------------------------------------------

    def _sample(
        self, *, manual_override_ms: int | None
    ) -> tuple[BoundMedia, tuple[SubtitleEntry, ...], AnchorResult] | None:
        """One probe: bound media + subtitles + resolved anchor, or ``None``
        if asbplayer is unreachable or nothing is bound (routine, not an
        error — mirrors mpv's "idle" treatment)."""
        client = self._ensure_client()
        if client is None:
            return None
        try:
            bound = get_bound_media(client)
            subtitles = get_subtitles(client)
        except (AsbplayerDisconnected, AsbplayerProtocolError, OSError) as exc:
            _log.warning("asbplayer probe failed (%s); will reconnect.", exc)
            self._drop_client()
            return None
        if bound is None:
            return None

        conn = self._open_conn()
        try:
            anchor = self._resolve_anchor(
                conn, subtitles, override_ms=manual_override_ms
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
    "DEFAULT_PATH",
    "DEFAULT_PORT",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "MANUAL_ANCHOR_EVENT",
    "MANUAL_ANCHOR_SESSION_ID",
    "PROTOCOL_SURFACE_VERSION",
    "REQUEST_GET_BOUND_MEDIA",
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
    "RawSocketWsPeer",
    "SubtitleEntry",
    "WsPeer",
    "connect_ws",
    "get_bound_media",
    "get_subtitles",
]
