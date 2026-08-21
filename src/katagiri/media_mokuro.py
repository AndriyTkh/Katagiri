"""E-T010: the mokuro channel — manga page context over a small, self-hosted
localhost bridge.

Unlike mpv (this module's sibling: a client pulling state from an existing
IPC pipe over ``katagiri.media_mpv``), mokuro-reader is a browser app with no
IPC surface of its own. The integration this task builds, per plan.md's
Primary Dependencies and research.md/oss-components.md/audit-log.md's mokuro
notes, is the other direction: mokuro-reader fires a
``mokuro-reader:page.change`` ``CustomEvent`` (``title``/``volume``/
``currentPage``) on every page turn; a ~10-line userscript the learner installs
in the browser POSTs that event to a small HTTP server **this module runs** —
:class:`MokuroBridgeServer`. :class:`MokuroChannel` is the
:class:`~katagiri.media_channel.MediaChannel` that reads back whatever the
bridge last received.

Two hardening requirements come from spec.md's User Story 3 and from T004's
groundwork in ``config.py``/``mcp_server.py`` (``MOKURO_BRIDGE_PORT``,
``_SECRET_KEYS``, ``HARDENED_PORTS``) rather than being invented here:

1. **Shared secret + Origin validation on every request** (spec.md
   acceptance scenario 1). "localhost bridge reachable by any browser page
   without the checks" is research.md's own rationale for why both checks
   exist — a bare "listen on localhost" is not a security boundary by itself
   (any tab in the learner's browser can reach it), so the bridge fails
   *closed*: no configured secret means every request is rejected, not
   silently accepted. The secret is compared with :func:`hmac.compare_digest`
   (constant-time) and never appears in a response body or a log line —
   :meth:`_PageChangeRequestHandler.log_message` is silenced by default,
   matching the "never logged, never echoed" contract ``config.py`` already
   states for ``mokuro_shared_secret``.
2. **`volume-data.json` poller fallback** (spec.md acceptance scenario 2).
   mokuro-reader's "Local Folder" sync provider writes this file as a durable
   progress snapshot (audit-log.md: "progress per volume_uuid"); its exact
   internal shape is not part of any frozen contract the way the `.mokuro`
   schema is, so :func:`read_volume_data` reads it defensively (missing file,
   invalid JSON, or an unrecognised shape all resolve to "nothing usable"
   rather than a guess) and :class:`MokuroChannel` treats it as a fallback
   *source*, not a fallback *event*: see "Which source wins", below.

**`.mokuro` text layer.** The OCR'd manga text ``.mokuro`` carries is read
straight from the frozen schema (audit-log.md: "schema frozen since 0.2.0;
never compare the `version` field — it's the package version") via
:func:`load_mokuro_page`: ``{"pages": [{"blocks": [{"lines": [...]}]}]}``,
tolerant of any deviation (a missing key, wrong type, or out-of-range page
index all fall back to no text rather than raising) because malformed OCR
output must never crash a probe.

**Which source wins.** mpv's channel decides staleness against a continuous
heartbeat (mpv answers every poll immediately, so "no fresh answer" means
disconnected). mokuro's bridge has no such heartbeat — mokuro-reader only
fires an event *on a page turn*, so a page pushed five minutes ago and still
being read is not stale, it is exactly the same event mpv's channel would
still be reporting from thirty seconds ago. Applying mpv's staleness window
here would report the fallback poller as authoritative moments after every
push, which is backwards. Instead :meth:`MokuroChannel._current_source`
trusts the last bridge push indefinitely *unless* ``volume-data.json``'s own
file-modified time is newer than the push — the one signal available (with
no per-entry timestamp promised inside the file itself) that the reader kept
moving after the userscript stopped firing (tab closed, extension disabled,
browser restarted). This mirrors the injectable-clock discipline the rest of
Phase E already follows (``media_channel.py``'s ``is_stale``/``is_live``,
``envelope.py``'s ``Clock``): the wall clock is read through ``clock`` for
both the bridge's received-at stamps and the poller's mtime comparison, so
this decision is exercised in tests without sleeping.

**No pinned-port binding in tests.** :class:`MokuroBridgeServer` binds
nothing at construction time — the OS socket is opened only inside
:meth:`MokuroBridgeServer.start`, and every test in
``tests/test_media_mokuro.py`` passes ``port=0`` (OS-assigned) so the real
``config.MOKURO_BRIDGE_PORT`` (8767) is never touched by the suite, per this
task's own instruction and T004's ``HARDENED_PORTS`` loopback contract.

**No `media_heartbeat` write here.** That table is a *single*, shared "what
is on screen right now" row (docs/db-schema.md:109) rather than one row per
channel, and this task's scope (bridge + poller + text layer) does not ask
this module to decide who wins it when multiple channels are live — that
arbitration already exists at the ``MediaMoment``/``select_active_channel``
level (``media_channel.py``) and, if a persisted heartbeat is wanted for
mokuro specifically, belongs to the registration task (T012) that wires
multiple channels together, not to one channel's own module.
"""

from __future__ import annotations

import hmac
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Final

from katagiri.config import MOKURO_BRIDGE_PORT
from katagiri.media_channel import (
    MediaChannel,
    RawContext,
    RawLine,
    RawMoment,
)

if TYPE_CHECKING:
    from katagiri.config import Config

#: The bridge's single endpoint. Anything else 404s.
PAGE_CHANGE_PATH: Final = "/mokuro/page-change"

#: Header the userscript carries the shared secret in. Not a standard header
#: name (there is no standard one for this) — namespaced so it cannot be
#: confused with an unrelated ``X-Secret`` some other local tool might set.
SHARED_SECRET_HEADER: Final = "X-Katagiri-Mokuro-Secret"

#: Cap on OCR text blocks surfaced per page, mirroring the "mandatory window
#: ... cap ~40 lines" ceiling audit-log.md already applies to media context
#: generally — a page with an unreasonable number of detected blocks (a
#: corrupt or adversarial `.mokuro` file) still returns a bounded amount of
#: text rather than an unbounded one.
MAX_CONTEXT_LINES: Final = 40

#: Default Origin allow-list: loopback HTTP only. mokuro-reader is commonly
#: either self-hosted on a local dev server (``http://localhost:PORT``) or
#: run from ``http://127.0.0.1``; a hosted PWA deployment would run under a
#: different origin the operator must allow explicitly by passing their own
#: ``allowed_origin`` callable — this module does not guess a third-party
#: hosted origin on the operator's behalf.
_LOCALHOST_ORIGIN_RE: Final = re.compile(r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$")


def default_origin_allowed(origin: str) -> bool:
    """True for a bare ``http(s)://localhost`` / ``http(s)://127.0.0.1`` Origin.

    Deliberately rejects ``"null"`` (the Origin a ``file://`` page sends):
    accepting it would mean any locally-opened HTML file could reach the
    bridge, which is exactly the "reachable by any browser page" hole
    research.md's rationale calls out.
    """
    return bool(_LOCALHOST_ORIGIN_RE.match(origin))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Bridge state — what the userscript last (validly) pushed
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BridgeSnapshot:
    """One validated ``page.change`` push, plus when it was received.

    ``received_at`` uses the bridge's injected ``clock`` (never the bare
    system clock read inline), so :meth:`MokuroChannel._current_source`'s
    comparison against ``volume-data.json``'s mtime is reproducible in tests.
    """

    title: str | None
    volume: str | None
    page: int | None
    received_at: datetime


class MokuroBridgeState:
    """Thread-safe holder for the single most recent :class:`BridgeSnapshot`.

    One process, one bridge, one "current" snapshot — mirroring
    `media_heartbeat`'s own single-row shape (see module docstring) at the
    in-memory level. A lock guards it because the HTTP server's request
    threads write concurrently with whatever thread later calls
    :meth:`MokuroChannel.media_now`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: BridgeSnapshot | None = None

    def update(self, snapshot: BridgeSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self) -> BridgeSnapshot | None:
        with self._lock:
            return self._snapshot


# ---------------------------------------------------------------------------
# The bridge HTTP server
# ---------------------------------------------------------------------------


class _PageChangeRequestHandler(BaseHTTPRequestHandler):
    """Validates secret + Origin, then updates ``self.server.state``.

    ``self.server`` is the :class:`_BridgeHTTPServer` instance that accepted
    this connection (stdlib ``socketserver`` wiring) — that is where
    ``secret``/``state``/``allowed_origin``/``clock``/``quiet`` live, not on
    the handler class, so every request reads the same live configuration.
    """

    server: "_BridgeHTTPServer"

    def do_POST(self) -> None:  # noqa: N802 - stdlib-mandated name
        if self.path != PAGE_CHANGE_PATH:
            self._respond(404, {"error": "not found"})
            return

        if not self._secret_ok():
            self._respond(401, {"error": "unauthorized"})
            return
        if not self._origin_ok():
            self._respond(403, {"error": "forbidden origin"})
            return

        payload = self._read_json_body()
        if payload is None:
            self._respond(400, {"error": "invalid json body"})
            return

        title = payload.get("title")
        volume = payload.get("volume")
        page = payload.get("currentPage", payload.get("page"))
        title = title if isinstance(title, str) and title else None
        volume = volume if isinstance(volume, str) and volume else None
        page = page if isinstance(page, int) and not isinstance(page, bool) else None

        if title is None and volume is None and page is None:
            self._respond(400, {"error": "missing page-change fields"})
            return

        snapshot = BridgeSnapshot(
            title=title, volume=volume, page=page, received_at=self.server.clock()
        )
        self.server.state.update(snapshot)
        self._respond(200, {"ok": True})

    # -- validation -----------------------------------------------------------

    def _secret_ok(self) -> bool:
        expected = self.server.secret
        if not expected:
            # Fail closed: an unconfigured secret must never read as "no
            # check required" (research.md's exact rationale for this gate).
            return False
        supplied = self.headers.get(SHARED_SECRET_HEADER, "")
        return hmac.compare_digest(supplied, expected)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return False
        return self.server.allowed_origin(origin)

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return None
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # -- plumbing ---------------------------------------------------------------

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silenced by default (``server.quiet``) — a request's headers carry
        the shared secret, and stdlib's default log line is the kind of
        output that ends up captured somewhere. Never overridden to include
        headers even when un-silenced; only the request line/status."""
        if not self.server.quiet:
            super().log_message(format, *args)


class _BridgeHTTPServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` plus the per-process config the handler reads."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        secret: str | None,
        state: MokuroBridgeState,
        allowed_origin: Callable[[str], bool],
        clock: Callable[[], datetime],
        quiet: bool,
    ) -> None:
        super().__init__(address, _PageChangeRequestHandler)
        self.secret = secret
        self.state = state
        self.allowed_origin = allowed_origin
        self.clock = clock
        self.quiet = quiet


class MokuroBridgeServer:
    """Lifecycle wrapper: bind/serve/stop, nothing bound until :meth:`start`.

    Binding is deferred out of ``__init__`` specifically so constructing a
    :class:`MokuroChannel` (which owns one of these) never touches a socket
    on its own — only an explicit :meth:`start` does, which is what lets
    tests safely default-construct channels and opt into a real (port-0)
    listener only in the handful of tests that exercise the HTTP layer.
    """

    def __init__(
        self,
        *,
        secret: str | None,
        host: str = "127.0.0.1",
        port: int = MOKURO_BRIDGE_PORT,
        state: MokuroBridgeState | None = None,
        allowed_origin: Callable[[str], bool] = default_origin_allowed,
        clock: Callable[[], datetime] = _utc_now,
        quiet: bool = True,
    ) -> None:
        self.secret = secret
        self.host = host
        self.requested_port = port
        self.state = state if state is not None else MokuroBridgeState()
        self.allowed_origin = allowed_origin
        self.clock = clock
        self.quiet = quiet
        self._httpd: _BridgeHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        """The bound port. Raises before :meth:`start` — there is no port to
        report yet, and returning ``requested_port`` instead would silently
        lie about ``port=0`` having been resolved to a real OS-assigned one."""
        if self._httpd is None:
            raise RuntimeError("MokuroBridgeServer has not been started")
        return self._httpd.server_address[1]

    def start(self) -> None:
        if self._httpd is not None:
            return
        self._httpd = _BridgeHTTPServer(
            (self.host, self.requested_port),
            secret=self.secret,
            state=self.state,
            allowed_origin=self.allowed_origin,
            clock=self.clock,
            quiet=self.quiet,
        )
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "MokuroBridgeServer":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# `volume-data.json` poller fallback
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PollerSnapshot:
    """What :func:`read_volume_data` recovers from one `volume-data.json`."""

    title: str | None
    volume: str | None
    page: int | None


def _extract_page(entry: Mapping[str, Any]) -> int | None:
    for key in ("currentPage", "page", "current_page"):
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _extract_title(entry: Mapping[str, Any]) -> str | None:
    for key in ("title", "volumeName", "volume_title", "seriesTitle"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def read_volume_data(
    path: Path, *, preferred_volume: str | None = None
) -> PollerSnapshot | None:
    """Best-effort read of mokuro-reader's "Local Folder" progress snapshot.

    Its exact shape is not a frozen contract the way `.mokuro` is (audit-log.md
    only documents it as "progress per volume_uuid"), so this is deliberately
    defensive: a missing file, invalid JSON, or a shape this function does not
    recognise all resolve to ``None`` — a fallback that cannot be trusted to
    mean anything is the same, to a caller, as no fallback at all.

    ``preferred_volume`` disambiguates a file holding more than one volume's
    progress (multiple manga ever opened in mokuro-reader) — when it is not
    given (or not present in the file) and more than one entry exists, this
    returns ``None`` rather than guessing which volume is "current": nothing
    in the file's own content says so.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data:
        return None

    if preferred_volume is not None:
        entry = data.get(preferred_volume)
        if isinstance(entry, dict):
            return PollerSnapshot(
                title=_extract_title(entry),
                volume=preferred_volume,
                page=_extract_page(entry),
            )

    if len(data) == 1:
        ((volume_uuid, entry),) = data.items()
        if isinstance(entry, dict):
            return PollerSnapshot(
                title=_extract_title(entry), volume=volume_uuid, page=_extract_page(entry)
            )

    return None


# ---------------------------------------------------------------------------
# `.mokuro` text layer — the frozen OCR schema
# ---------------------------------------------------------------------------


def load_mokuro_page(mokuro_path: Path, page_index: int) -> list[str]:
    """OCR'd text lines for one page of a `.mokuro` file.

    Schema per audit-log.md/oss-components.md ("frozen since 0.2.0; never
    compare `version`"): ``{"pages": [{"blocks": [{"lines": [str, ...]}]}]}``.
    Any deviation — missing file, invalid JSON, wrong types, an out-of-range
    page index — returns ``[]`` rather than raising: malformed OCR output
    (or an adversarial `.mokuro` file) must never crash a probe, and a caller
    already treats "no lines" the same way it treats "no subtitle currently
    shown" elsewhere in this codebase (see ``media_mpv.py``).
    """
    try:
        text = mokuro_path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    pages = data.get("pages")
    if not isinstance(pages, list) or not (0 <= page_index < len(pages)):
        return []
    page = pages[page_index]
    if not isinstance(page, dict):
        return []
    blocks = page.get("blocks")
    if not isinstance(blocks, list):
        return []

    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_lines = block.get("lines")
        if not isinstance(block_lines, list):
            continue
        for line in block_lines:
            if isinstance(line, str) and line:
                lines.append(line)
    return lines[:MAX_CONTEXT_LINES]


# ---------------------------------------------------------------------------
# The channel
# ---------------------------------------------------------------------------


class MokuroChannel(MediaChannel):
    """mokuro over the bridge above, with the `volume-data.json` fallback.

    ``resolve_mokuro_path`` is how a caller tells this channel where a given
    ``(title, volume)`` pair's `.mokuro` file lives — deliberately injected
    rather than a config key this module invents itself (T010's scope is the
    bridge/poller/text-layer trio; a `.mokuro`-library-root config key, if
    wanted, is a decision for whichever task wires this channel into the
    running server). ``None`` (the default) means "no text layer available",
    which degrades exactly the way mpv's channel degrades when nothing is
    loaded: page/position metadata still resolves, ``displayed_text`` is
    ``None``.
    """

    kind = "mokuro"

    def __init__(
        self,
        *,
        secret: str | None,
        bridge_host: str = "127.0.0.1",
        bridge_port: int = MOKURO_BRIDGE_PORT,
        allowed_origin: Callable[[str], bool] = default_origin_allowed,
        volume_data_path: Path | None = None,
        resolve_mokuro_path: Callable[[str | None, str | None], Path | None] | None = None,
        clock: Callable[[], datetime] = _utc_now,
        quiet: bool = True,
    ) -> None:
        self._clock = clock
        self._state = MokuroBridgeState()
        self.bridge = MokuroBridgeServer(
            secret=secret,
            host=bridge_host,
            port=bridge_port,
            state=self._state,
            allowed_origin=allowed_origin,
            clock=clock,
            quiet=quiet,
        )
        self._volume_data_path = volume_data_path
        self._resolve_mokuro_path = resolve_mokuro_path
        # Sticky "last volume we had any signal for", so a multi-entry
        # volume-data.json can still be disambiguated once the bridge itself
        # has gone quiet (see read_volume_data's preferred_volume contract).
        self._last_known_volume: str | None = None

    @classmethod
    def from_config(
        cls,
        config: "Config",
        *,
        bridge_port: int | None = None,
        volume_data_path: Path | None = None,
        resolve_mokuro_path: Callable[[str | None, str | None], Path | None] | None = None,
    ) -> "MokuroChannel":
        """Build a channel wired to the real, pinned configuration.

        ``bridge_port`` defaults to :data:`~katagiri.config.MOKURO_BRIDGE_PORT`
        — the pinned production port — so tests calling this factory must
        pass ``bridge_port=0`` explicitly, the same discipline every other
        test in this module follows.
        """
        return cls(
            secret=config.mokuro_shared_secret,
            bridge_port=MOKURO_BRIDGE_PORT if bridge_port is None else bridge_port,
            volume_data_path=volume_data_path,
            resolve_mokuro_path=resolve_mokuro_path,
        )

    def close(self) -> None:
        """Stop the bridge server, if one is running. Tolerates being called
        when nothing was ever started (mirrors ``MpvChannel.close``'s "must
        tolerate being called twice" contract)."""
        self.bridge.stop()

    def __enter__(self) -> "MokuroChannel":
        self.bridge.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- source selection ("which is current" — see module docstring) --------

    def _poller_snapshot(self) -> tuple[PollerSnapshot | None, datetime | None]:
        if self._volume_data_path is None:
            return None, None
        try:
            mtime = datetime.fromtimestamp(
                self._volume_data_path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            return None, None
        snapshot = read_volume_data(
            self._volume_data_path, preferred_volume=self._last_known_volume
        )
        return snapshot, mtime

    def _current_source(self) -> tuple[str | None, str | None, int | None]:
        bridge = self._state.snapshot()
        poller, poll_mtime = self._poller_snapshot()

        if bridge is not None and (poll_mtime is None or bridge.received_at >= poll_mtime):
            self._last_known_volume = bridge.volume or self._last_known_volume
            return bridge.title, bridge.volume, bridge.page
        if poller is not None:
            self._last_known_volume = poller.volume or self._last_known_volume
            return poller.title, poller.volume, poller.page
        if bridge is not None:
            self._last_known_volume = bridge.volume or self._last_known_volume
            return bridge.title, bridge.volume, bridge.page
        return None, None, None

    def _page_lines(self, title: str | None, volume: str | None, page: int | None) -> list[str]:
        if self._resolve_mokuro_path is None or page is None:
            return []
        path = self._resolve_mokuro_path(title, volume)
        if path is None:
            return []
        return load_mokuro_page(path, page)

    # -- MediaChannel interface ------------------------------------------------

    def _probe_now(self) -> RawMoment | None:
        title, volume, page = self._current_source()
        if title is None and volume is None and page is None:
            return None
        lines = self._page_lines(title, volume, page)
        displayed_text = "\n".join(lines) if lines else None
        media_id = volume or title
        locator = f"mokuro:{media_id}:p{page}" if media_id else "mokuro"
        return RawMoment(
            media_id=media_id,
            anchor_ms=None,  # manga has no time playhead — page is the anchor
            displayed_text=displayed_text,
            title=title,
            locator=locator,
            detail={"page": page} if page is not None else None,
        )

    def _probe_context(self, **kwargs: Any) -> RawContext | None:
        title, volume, page = self._current_source()
        if title is None and volume is None and page is None:
            return None
        lines = self._page_lines(title, volume, page)
        media_id = volume or title
        base_locator = f"mokuro:{media_id}:p{page}" if media_id else f"mokuro:p{page}"
        raw_lines = tuple(
            RawLine(
                text=line,
                start_ms=None,
                end_ms=None,
                locator=f"{base_locator}:b{index}",
            )
            for index, line in enumerate(lines)
        )
        return RawContext(media_id=media_id, anchor_ms=None, lines=raw_lines)


__all__ = [
    "MAX_CONTEXT_LINES",
    "PAGE_CHANGE_PATH",
    "SHARED_SECRET_HEADER",
    "BridgeSnapshot",
    "MokuroBridgeServer",
    "MokuroBridgeState",
    "MokuroChannel",
    "PollerSnapshot",
    "default_origin_allowed",
    "load_mokuro_page",
    "read_volume_data",
]
