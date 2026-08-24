"""009-T003: the asbplayer bridge's transport core, hosted in-process.

This module replaces the Go binary at
``asbplayer/scripts/web-socket-server/main.go`` (commit ``37495e22``) with an
``aiohttp`` server running inside the Katagiri MCP process. The Go source is
the *authority* for every behavioral question — research.md R1 enumerates the
contract fact by fact, with a ``main.go`` line for each — and this file
reproduces that contract rather than a more sensible one, with exactly the
divergences research.md R4 lists as known Go defects (each commented with its
``G-`` number where it applies).

**What lives here (T003).** The transport: the listener's lifecycle, the
``GET /ws`` upgrade, the read loop, and the publish/await correlation layer.
The five relay HTTP endpoints and ``POST /disconnect-ws-clients`` are T004's;
the AnkiConnect proxy on ``/`` and the ``addNote`` intercept are T005's. Both
extend :meth:`AsbplayerBridgeServer._build_app`.

**Threading model** (plan.md decision 9, research.md R6). ``mcp_server.main()``
is synchronous and ends in a blocking ``server.run(transport="stdio")``, so the
bridge cannot borrow the host's event loop — there isn't one. It therefore owns
a *private* :mod:`asyncio` loop running ``run_forever`` on a daemon thread, and
:meth:`AsbplayerBridgeServer.start` blocks until the socket is actually bound so
its caller gets a real address back rather than a promise. The daemon flag keeps
a wedged shutdown from hanging process exit (FR-012's "released when the host
process exits"); the explicit :meth:`AsbplayerBridgeServer.stop` — which closes
the site, drains the runner, stops the loop and *joins* the thread — is the half
that matters for tests. A suite that leaks a listener is how port 8766 ends up
occupied for the next developer (FR-012, SC-006), which is also why no automated
test may bind 8766: every test passes ``port=0``, exactly as
``tests/test_media_mokuro.py`` already does for the mokuro bridge.

The seam between the two worlds is narrow and deliberate:

* Everything touching the client set or the pending registry runs **on the loop
  thread only**, so neither needs a lock — the Go version's shared
  ``sync.Mutex`` (``main.go:84-96``) has no counterpart here because there is no
  second thread to exclude.
* :meth:`~AsbplayerBridgeServer.publish_async` /
  :meth:`~AsbplayerBridgeServer.publish_and_await_async` are the in-loop API,
  used by the HTTP handlers T004/T005 add (they are already coroutines on this
  loop).
* :meth:`~AsbplayerBridgeServer.publish` /
  :meth:`~AsbplayerBridgeServer.publish_and_await` are the same operations for
  callers on *another* thread, via ``run_coroutine_threadsafe``. Calling them
  from the loop thread would self-deadlock, so they refuse to.

**Correlation** (plan.md decision 7, research.md G-4). The Go bridge funnels
every client reply through one unbuffered channel that each waiter drains in a
loop, so a reply belonging to request A can be eaten and discarded by request
B's loop. It never bites today only because ``media_asbplayer.py`` keeps one
request outstanding at a time. This module keeps a **per-``messageId`` registry
of pending futures** instead: a reply is delivered to its own waiter or to
nobody. That is the one place the reimplementation deliberately diverges from
the reference, and it is a strict improvement with no observable contract
change.

**Logging** (FR-017, research.md G-5). Standard library logging on the module
logger, which the host configures to **stderr** — never stdout, which would
corrupt the MCP stdio transport. Metadata only: a peer address, a command name,
a messageId, a count. Never an AnkiConnect note field, never a reply body, never
a credential. The Go request logger re-reads and prints request bodies
(``main.go:461-469``); that is a defect, not a contract.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Mapping

from aiohttp import WSMsgType, web

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

#: Configured by the host process to stderr. This module never writes to
#: stdout: stdout is the MCP stdio transport (rule 5 / FR-017).
_log = logging.getLogger("katagiri.asbplayer_bridge")


# ---------------------------------------------------------------------------
# Constants — the protocol surface (research.md R1.1/R1.2/R1.5)
# ---------------------------------------------------------------------------

#: The one route that upgrades (``main.go:479``).
WS_PATH: Final = "/ws"

#: App-level keepalive, distinct from WebSocket protocol-level ping/pong: the
#: extension sends the literal text ``PING`` and expects the literal text
#: ``PONG`` back, and the message must never reach the JSON parser
#: (``main.go:118-119``; research.md R1.1; FR-004).
KEEPALIVE_PING: Final = "PING"
KEEPALIVE_PONG: Final = "PONG"

#: ``time.After(5 * time.Second)`` in ``publishMessageAndAwaitResponse``
#: (``main.go:164``; research.md R1.2; FR-003). Expiry is reported to the
#: caller as "no answer", which every Go caller turns into a 500.
REPLY_TIMEOUT_S: Final = 5.0

#: Go's six environment knobs and their defaults (``main.go:441-446``;
#: research.md R1.5). ``HOST``'s 127.0.0.1 default is the local commit
#: ``37495e22`` change — upstream 1.20.2 bound every interface.
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8766  # == media_asbplayer.DEFAULT_PORT, the client half
DEFAULT_ANKI_CONNECT_URL: Final = "http://127.0.0.1:8765"
DEFAULT_POST_MINE_ACTION: Final = 2
DEFAULT_INTERCEPT_FIELD: Final = ""
DEFAULT_INTERCEPT_VALUE: Final = ""

#: Bare ``HOST`` and ``PORT`` are how the Go binary was configured and an
#: operator's ``.env``-shaped knowledge must keep working (FR-010) — but inside
#: Katagiri's own process they are dangerously generic names that some unrelated
#: tool may already own. So the bind host is read from a ``KATAGIRI_``-prefixed
#: alias *first* and falls back to bare ``HOST``; likewise for the port. The
#: three non-address knobs keep their bare Go names only, since nothing else in
#: this process reads them.
ENV_HOST_ALIAS: Final = "KATAGIRI_ASBPLAYER_BRIDGE_HOST"
ENV_HOST: Final = "HOST"
ENV_PORT_ALIAS: Final = "KATAGIRI_ASBPLAYER_BRIDGE_PORT"
ENV_PORT: Final = "PORT"
ENV_ANKI_CONNECT_URL: Final = "ANKI_CONNECT_URL"
ENV_POST_MINE_ACTION: Final = "POST_MINE_ACTION"
ENV_INTERCEPT_FIELD: Final = "INTERCEPT_FIELD"
ENV_INTERCEPT_VALUE: Final = "INTERCEPT_VALUE"

#: aiohttp defaults to a 4 MiB inbound message cap. ``get-subtitles`` replies
#: carry whole subtitle tracks and the spec calls out payloads "well past
#: 64 KiB" as a case that must not truncate (spec Edge Cases), so the cap is
#: raised rather than left to bite in the field. It stays *finite*: this is a
#: loopback listener any local page can reach, and an unbounded cap would make
#: a single frame an out-of-memory lever.
MAX_WS_MESSAGE_BYTES: Final = 64 * 1024 * 1024

#: Bounded waits on the loop-thread seam, so a wedged loop surfaces as an error
#: instead of hanging the MCP server's startup or a test's teardown.
_START_TIMEOUT_S: Final = 10.0
_STOP_TIMEOUT_S: Final = 10.0
_THREAD_JOIN_TIMEOUT_S: Final = 5.0

#: **SO_REUSEADDR, decided deliberately (research.md O-2).** The two platforms
#: mean opposite things by this flag:
#:
#: * On POSIX, ``SO_REUSEADDR`` only lets a fresh listener bind past the
#:   ``TIME_WAIT`` remnants of *closed* connections; it cannot steal a port from
#:   a live listener. Without it, restarting the MCP server while a browser had
#:   recently been connected can be refused. So: **on**.
#: * On Windows, ``SO_REUSEADDR`` means something much stronger — it lets a
#:   second process bind a port another process is *actively listening on*, with
#:   the winner of subsequent connections undefined. That would silently break
#:   FR-011/US3-3, whose whole posture is "if something already owns 8766 —
#:   including a Go bridge the operator started — say so and stand down". A
#:   refused bind is exactly the signal we want. Windows also does not gate a
#:   new listener on a prior connection's ``TIME_WAIT``, so the flag buys
#:   nothing here anyway. So: **off**.
#:
#: This happens to match asyncio's own platform default, which is why it is
#: written out and passed explicitly: a default that is silently right is
#: indistinguishable from one nobody thought about, and O-2 asks for a decision.
#: The stop/start cycle the TG3 gate exercises is what validates it.
_REUSE_ADDRESS: Final = os.name == "posix"

_LOOP_THREAD_NAME: Final = "katagiri-asbplayer-bridge"


# ---------------------------------------------------------------------------
# Configuration (research.md R1.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """The six knobs ``main.go:441-449`` reads, with the Go defaults.

    None of the six is a secret — the Go binary prints all of them in its
    startup banner (``main.go:448``) — so :meth:`describe` is safe to log.
    ``anki_connect_url`` is nonetheless kept out of any *response* body, since
    FR-017's rule is about what leaves the process, not about secrecy grades.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    anki_connect_url: str = DEFAULT_ANKI_CONNECT_URL
    post_mine_action: int = DEFAULT_POST_MINE_ACTION
    intercept_field: str = DEFAULT_INTERCEPT_FIELD
    intercept_value: str = DEFAULT_INTERCEPT_VALUE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BridgeConfig":
        """Read the configuration the Go bridge read, from the same names.

        ``env`` is injectable so tests never mutate the real process
        environment (house pattern: every clock and side channel in this
        package is passed in).
        """
        source: Mapping[str, str] = os.environ if env is None else env

        def get(name: str, fallback: str) -> str:
            value = source.get(name)
            return fallback if value is None else value

        host = source.get(ENV_HOST_ALIAS)
        if host is None:
            host = get(ENV_HOST, DEFAULT_HOST)

        raw_port = source.get(ENV_PORT_ALIAS)
        if raw_port is None:
            raw_port = get(ENV_PORT, str(DEFAULT_PORT))

        return cls(
            host=host,
            port=_parse_port(raw_port),
            anki_connect_url=get(ENV_ANKI_CONNECT_URL, DEFAULT_ANKI_CONNECT_URL),
            # Go's ``strconv.Atoi`` result is used with its error discarded
            # (``main.go:444``), so an unparsable value lands on 0 — "no
            # post-mine action" — rather than on the default 2. Reproduced: it
            # is contract (a caller could be relying on ``POST_MINE_ACTION=off``
            # meaning off), not one of R4's defects.
            post_mine_action=_parse_int(
                get(ENV_POST_MINE_ACTION, str(DEFAULT_POST_MINE_ACTION)), fallback=0
            ),
            intercept_field=get(ENV_INTERCEPT_FIELD, DEFAULT_INTERCEPT_FIELD),
            intercept_value=get(ENV_INTERCEPT_VALUE, DEFAULT_INTERCEPT_VALUE),
        )

    def describe(self) -> str:
        """One metadata-only line, the analogue of Go's startup banner."""
        return (
            f"host={self.host} port={self.port} "
            f"anki_connect_url={self.anki_connect_url} "
            f"post_mine_action={self.post_mine_action} "
            f"intercept_field={self.intercept_field!r} "
            f"intercept_value_set={bool(self.intercept_value)}"
        )


def _parse_int(raw: str, *, fallback: int) -> int:
    try:
        return int(raw.strip())
    except (AttributeError, ValueError):
        return fallback


def _parse_port(raw: str) -> int:
    """A port outside 0..65535 (or unparsable) falls back to the default.

    Go never validated this — ``e.Start(host + ":" + port)`` simply failed at
    bind time with an opaque error. Failing over to the documented default and
    logging it is friendlier and cannot surprise anyone: an operator who set a
    real port still gets it.
    """
    port = _parse_int(raw, fallback=-1)
    if 0 <= port <= 65535:
        return port
    _log.warning(
        "asbplayer bridge: ignoring unusable port %r, using %d", raw, DEFAULT_PORT
    )
    return DEFAULT_PORT


def is_loopback(host: str) -> bool:
    """True when binding ``host`` cannot be reached from off this machine.

    Hostname forms are handled by name rather than resolved: resolution is a
    network operation and the only names that matter here are the two the
    documentation and the Go ``.env.example`` use.
    """
    candidate = host.strip().strip("[]")
    if not candidate:
        # Empty host means "every interface" to the socket layer — the exact
        # exposure upstream 1.20.2 had before ``37495e22``.
        return False
    if candidate.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        # An unrecognised name is treated as *not* loopback: FR-009's warning
        # is meant to over-report rather than stay quiet about a real exposure.
        return False


# ---------------------------------------------------------------------------
# Envelope (research.md R1.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClientCommand:
    """Server→client: ``{"command", "messageId", "body"}`` (``main.go:56-60``).

    ``messageId`` is a fresh UUID v4 string per command (``uuid.NewString()``,
    e.g. ``main.go:244``) — FR-002.
    """

    command: str
    message_id: str
    body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, command: str, body: Mapping[str, Any] | None = None) -> "ClientCommand":
        return cls(
            command=command,
            message_id=str(uuid.uuid4()),
            body=dict(body or {}),
        )

    def to_json(self) -> str:
        return json.dumps(
            {"command": self.command, "messageId": self.message_id, "body": self.body}
        )


@dataclass(frozen=True, slots=True)
class ClientResponse:
    """Client→server: ``{"command", "messageId", "body"}`` (``main.go:61-65``).

    Only ``messageId`` and ``body`` are consumed by the relays; ``body`` is kept
    as the *raw decoded JSON value* (``json.RawMessage`` in Go) and is never
    inspected here — the relay endpoints hand it back verbatim, error object and
    all, which ``media_asbplayer.py`` relies on (research.md R1.4).
    """

    command: str
    message_id: str
    body: Any


def parse_client_response(raw: str) -> ClientResponse | None:
    """Decode one client text frame, or ``None`` if it is not a response.

    ``None`` is the "silently drop it" path: Go's ``json.Unmarshal`` error
    branch (``main.go:120-125``) neither answers nor disconnects, and neither
    does this — the connection stays open (spec Edge Cases). A frame that parses
    as JSON but is not an object, or carries no string ``messageId``, is in the
    same bucket: there is nobody it could be routed to.
    """
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    message_id = decoded.get("messageId")
    if not isinstance(message_id, str) or not message_id:
        return None
    command = decoded.get("command")
    return ClientResponse(
        command=command if isinstance(command, str) else "",
        message_id=message_id,
        body=decoded.get("body"),
    )


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


class AsbplayerBridgeServer:
    """The in-process replacement for the Go bridge's transport half.

    Nothing is bound by ``__init__`` — the socket is opened only inside
    :meth:`start`, the same deferral ``MokuroBridgeServer`` uses so that
    constructing one is always safe in a test.

    Typical use::

        server = AsbplayerBridgeServer()
        host, port = server.start(port=0)      # never 8766 in a test
        try:
            reply = server.publish_and_await(
                ClientCommand.new("get-bound-media")
            )
        finally:
            server.stop()

    or as a context manager, which starts and stops around the block.
    """

    def __init__(
        self,
        *,
        config: BridgeConfig | None = None,
        reply_timeout_s: float = REPLY_TIMEOUT_S,
    ) -> None:
        self.config = config if config is not None else BridgeConfig()
        self.reply_timeout_s = reply_timeout_s

        # Loop-thread-confined state. No lock: every mutation happens in a
        # coroutine on ``self._loop``'s single thread, which is why the Go
        # forwarder's shared mutex has no counterpart here.
        self._clients: set[web.WebSocketResponse] = set()
        self._pending: dict[str, asyncio.Future[ClientResponse]] = {}

        # Lifecycle state, written by start()/stop() on the caller's thread.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._bound: tuple[str, int] | None = None

    # -- introspection ----------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._bound is not None

    @property
    def bound_address(self) -> tuple[str, int]:
        """``(host, port)`` actually bound.

        Raises before :meth:`start`. Reporting the *requested* port instead
        would silently lie about ``port=0`` having been resolved to a real
        OS-assigned one (the reasoning ``MokuroBridgeServer.port`` already
        records).
        """
        if self._bound is None:
            raise RuntimeError("AsbplayerBridgeServer has not been started")
        return self._bound

    @property
    def host(self) -> str:
        return self.bound_address[0]

    @property
    def port(self) -> int:
        return self.bound_address[1]

    @property
    def client_count(self) -> int:
        """Connected WebSocket clients. Read from any thread (a ``len`` of a
        set is atomic under the GIL and only ever shrinks or grows by one)."""
        return len(self._clients)

    # -- lifecycle --------------------------------------------------------

    def start(self, host: str | None = None, port: int | None = None) -> tuple[str, int]:
        """Bind, serve on a private loop, and return the bound ``(host, port)``.

        Blocks until the socket is bound, so a caller that gets an address back
        knows the listener is live (and a caller that gets an exception knows
        nothing was bound — including the "port already occupied" case FR-011
        cares about, which surfaces here as ``OSError`` for the launcher to
        report rather than fight).

        Calling it on an already-started server is a no-op that returns the
        existing address, mirroring ``MokuroBridgeServer.start``.
        """
        if self._bound is not None:
            return self._bound

        resolved_host = self.config.host if host is None else host
        resolved_port = self.config.port if port is None else port

        # FR-009 / US3-2, carrying over local commit 37495e22: loopback is the
        # default and anything else is deliberate, visible, and named.
        if not is_loopback(resolved_host):
            _log.warning(
                "asbplayer bridge binding NON-LOOPBACK address %s:%s — the "
                "WebSocket endpoint and the AnkiConnect proxy will be reachable "
                "from other machines on this network",
                resolved_host,
                resolved_port,
            )

        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=self._run_loop, args=(loop,), name=_LOOP_THREAD_NAME, daemon=True
        )
        thread.start()
        self._loop = loop
        self._thread = thread

        try:
            bound = asyncio.run_coroutine_threadsafe(
                self._start_site(resolved_host, resolved_port), loop
            ).result(timeout=_START_TIMEOUT_S)
        except BaseException:
            # Bind failed (occupied port, bad address, ...). Tear the loop and
            # thread back down before propagating, or a failed start leaks the
            # very thread FR-012 exists to stop leaking.
            self._shutdown_loop()
            raise

        self._bound = bound
        _log.info(
            # ASCII only: this goes to stderr, which on a Windows console is
            # still cp1252 unless the operator changed it.
            "asbplayer bridge listening on %s:%d (in-process); config: %s",
            bound[0],
            bound[1],
            self.config.describe(),
        )
        return bound

    def stop(self) -> None:
        """Close every client, release the socket, stop the loop, join the
        thread. Idempotent; safe to call on a server that never started."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self._stop_site(), loop).result(
                    timeout=_STOP_TIMEOUT_S
                )
            except Exception:  # pragma: no cover - teardown must not raise
                _log.warning("asbplayer bridge: error while closing the site", exc_info=True)
        self._shutdown_loop()
        self._bound = None
        self._clients.clear()
        self._pending.clear()
        _log.info("asbplayer bridge stopped")

    def __enter__(self) -> "AsbplayerBridgeServer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- lifecycle internals ----------------------------------------------

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            asyncio.set_event_loop(None)

    def _shutdown_loop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = self._thread = None
        self._runner = self._site = None
        if loop is None:
            return
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_S)
            if thread.is_alive():  # pragma: no cover - wedged loop
                _log.warning(
                    "asbplayer bridge: loop thread did not exit within %.0fs",
                    _THREAD_JOIN_TIMEOUT_S,
                )
                return
        if not loop.is_closed():
            loop.close()

    def _build_app(self) -> web.Application:
        """The route table.

        T003 registers the upgrade only. T004 adds the five relay endpoints and
        ``POST /disconnect-ws-clients``; T005 adds ``GET/POST/OPTIONS /`` — in
        ``main.go:479-488``'s order, which matters only for readability since
        aiohttp matches on the exact path.
        """
        app = web.Application(client_max_size=MAX_WS_MESSAGE_BYTES)
        app.router.add_get(WS_PATH, self._handle_ws)
        app.router.add_post(
            "/disconnect-ws-clients", self._handle_disconnect_ws_clients
        )
        app.router.add_post(
            "/asbplayer/load-subtitles", self._handle_load_subtitles
        )
        app.router.add_post("/asbplayer/seek", self._handle_seek)
        app.router.add_get("/asbplayer/bound-media", self._handle_bound_media)
        app.router.add_get("/asbplayer/subtitles", self._handle_subtitles)
        app.router.add_get(
            "/asbplayer/playback-state", self._handle_playback_state
        )
        return app

    # -- the six relay routes (research.md R1.3/R1.4) ----------------------

    async def _read_json_body(self, request: web.Request) -> Any:
        """Parse the request body as JSON, or raise the 400 both POST relays
        answer on an unparsable body (``main.go:320-324``/``345-349``)."""
        raw = await request.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc

    async def _await_or_500(self, command: ClientCommand) -> ClientResponse:
        """Publish-and-await, turning "no answer" (deadline or no client) into
        the 500 every relay endpoint answers for that case (``main.go:332-335``
        style ``ok`` checks)."""
        response = await self.publish_and_await_async(command)
        if response is None:
            raise web.HTTPInternalServerError()
        return response

    async def _handle_disconnect_ws_clients(self, request: web.Request) -> web.Response:
        """``POST /disconnect-ws-clients``: close and forget every client;
        200 with an empty body (``main.go:430-439``)."""
        await self.disconnect_all_clients()
        return web.Response(status=200, text="")

    async def _handle_load_subtitles(self, request: web.Request) -> web.Response:
        """``POST /asbplayer/load-subtitles``: body ``{"files": [...]}`` ->
        ``load-subtitles``; reply awaited and its body discarded — 200 with an
        **empty string** body, not JSON (``main.go:316-339``)."""
        body = await self._read_json_body(request)
        files = body.get("files") if isinstance(body, dict) else None
        command = ClientCommand.new("load-subtitles", {"files": files})
        await self._await_or_500(command)
        return web.Response(status=200, text="")

    async def _handle_seek(self, request: web.Request) -> web.Response:
        """``POST /asbplayer/seek``: body ``{"timestamp": float, "mediaId":
        str}`` -> ``seek-timestamp``; ``mediaId`` only when non-empty; reply
        awaited and discarded — 200 with an empty string body
        (``main.go:341-370``). ``timestamp`` is seconds, never normalized to
        the playback-state reply's milliseconds (research.md R1.3 units
        caution)."""
        body = await self._read_json_body(request)
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="expected a JSON object")
        command_body: dict[str, Any] = {"timestamp": body.get("timestamp")}
        media_id = body.get("mediaId")
        if media_id:
            command_body["mediaId"] = media_id
        command = ClientCommand.new("seek-timestamp", command_body)
        await self._await_or_500(command)
        return web.Response(status=200, text="")

    async def _handle_bound_media(self, request: web.Request) -> web.Response:
        """``GET /asbplayer/bound-media`` -> ``get-bound-media``; 200 with the
        reply's ``body`` relayed verbatim as a raw JSON blob, never inspected
        (``main.go:372-383``)."""
        command = ClientCommand.new("get-bound-media", {})
        response = await self._await_or_500(command)
        return _json_blob(response.body)

    async def _handle_subtitles(self, request: web.Request) -> web.Response:
        """``GET /asbplayer/subtitles``: optional query ``mediaId`` (non-empty)
        and ``trackNumbers`` (comma-separated, non-numeric entries dropped,
        key present only if at least one parsed) -> ``get-subtitles``; 200 with
        the reply's ``body`` relayed verbatim (``main.go:385-411``)."""
        command_body: dict[str, Any] = {}
        media_id = request.query.get("mediaId")
        if media_id:
            command_body["mediaId"] = media_id
        raw_track_numbers = request.query.get("trackNumbers")
        if raw_track_numbers:
            parsed: list[int] = []
            for token in raw_track_numbers.split(","):
                try:
                    parsed.append(int(token.strip()))
                except ValueError:
                    continue
            if parsed:
                command_body["trackNumbers"] = parsed
        command = ClientCommand.new("get-subtitles", command_body)
        response = await self._await_or_500(command)
        return _json_blob(response.body)

    async def _handle_playback_state(self, request: web.Request) -> web.Response:
        """``GET /asbplayer/playback-state``: optional query ``mediaId`` (non-
        empty) -> ``get-playback-state``; 200 with the reply's ``body`` relayed
        verbatim (``main.go:413-428``). The reply's own units are integer
        milliseconds — never normalized here (research.md R1.3 units
        caution)."""
        command_body: dict[str, Any] = {}
        media_id = request.query.get("mediaId")
        if media_id:
            command_body["mediaId"] = media_id
        command = ClientCommand.new("get-playback-state", command_body)
        response = await self._await_or_500(command)
        return _json_blob(response.body)

    async def _start_site(self, host: str, port: int) -> tuple[str, int]:
        runner = web.AppRunner(
            self._build_app(),
            handle_signals=False,  # the host process owns signal handling
            access_log=None,  # G-5: no per-request logging of bodies or URLs
        )
        await runner.setup()
        site = web.TCPSite(
            runner,
            host,
            port,
            reuse_address=_REUSE_ADDRESS,  # see _REUSE_ADDRESS (research.md O-2)
            shutdown_timeout=_STOP_TIMEOUT_S,
        )
        try:
            await site.start()
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        return _first_address(runner.addresses, fallback=(host, port))

    async def _stop_site(self) -> None:
        # Clients first: closing the socket out from under a live upgrade
        # leaves aiohttp waiting on the connection during cleanup.
        await self.disconnect_all_clients()
        for pending in list(self._pending.values()):
            if not pending.done():
                pending.cancel()
        self._pending.clear()
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        self._site = self._runner = None

    # -- the WebSocket endpoint (research.md R1.1) -------------------------

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """``GET /ws``: upgrade, then read until the peer goes away.

        **Origin is not checked**, matching the Go upgrader's
        ``CheckOrigin: func(...) bool { return true }`` (``main.go:23-25``,
        research.md R1.1). This is not an oversight inherited by accident: the
        real client is a browser *extension*, whose Origin is a
        ``chrome-extension://<unstable id>`` that differs per browser, per
        install channel and per user, so an allow-list would reject the actual
        user agent this bridge exists for. The control that does the work is the
        loopback bind (FR-009) — the same posture the mokuro bridge could not
        take, because *it* is reached by an ordinary web page whose Origin is
        stable and checkable.
        """
        ws = web.WebSocketResponse(
            max_msg_size=MAX_WS_MESSAGE_BYTES,
            # Protocol-level ping/pong and the close handshake are the transport
            # layer's business, distinct from the app-level text PING/PONG
            # below (spec Edge Cases).
            autoping=True,
            heartbeat=None,
        )
        await ws.prepare(request)

        peer = request.remote or "?"
        self._clients.add(ws)
        _log.debug("asbplayer bridge: client connected from %s (%d total)", peer, len(self._clients))

        try:
            async for msg in ws:
                if msg.type is WSMsgType.TEXT:
                    await self._on_text(ws, msg.data)
                elif msg.type is WSMsgType.ERROR:
                    # Go breaks its read loop on any read error (main.go:110-116).
                    _log.debug("asbplayer bridge: socket error from %s", peer)
                    break
                # BINARY and everything else: the extension never sends them and
                # Go would have failed to unmarshal them. Ignored, socket kept.
        finally:
            self._clients.discard(ws)
            _log.debug(
                "asbplayer bridge: client disconnected from %s (%d left)",
                peer,
                len(self._clients),
            )
        return ws

    async def _on_text(self, ws: web.WebSocketResponse, data: str) -> None:
        # FR-004 / R1.1: the *exact* text PING is a keepalive answered with the
        # text PONG, and is never handed to the JSON parser. The equality is
        # deliberately exact — Go compares ``string(msg) == "PING"``, so a
        # whitespace-padded or lowercase variant falls through to the parser and
        # is dropped there, and matching loosely here would be a divergence.
        if data == KEEPALIVE_PING:
            await ws.send_str(KEEPALIVE_PONG)
            return

        response = parse_client_response(data)
        if response is None:
            # Silently dropped, connection kept open (main.go:120-125). Logged
            # at debug with a length only — the frame's content is untrusted
            # third-party data and may carry note fields (FR-017).
            _log.debug("asbplayer bridge: ignoring unparsable %d-byte frame", len(data))
            return
        self._deliver(response)

    def _deliver(self, response: ClientResponse) -> None:
        """Route one reply to its waiter, or drop it.

        G-4 divergence: Go pushes every reply onto one shared channel that each
        waiter drains in a loop, so request B can consume and discard request
        A's reply. Here a reply reaches exactly the future registered under its
        own ``messageId``, and a reply with no waiter — the late-arrival case in
        spec Edge Cases — is discarded without disturbing anything in flight.
        """
        pending = self._pending.get(response.message_id)
        if pending is None or pending.done():
            _log.debug(
                "asbplayer bridge: discarding reply for %s (no waiter)",
                response.message_id,
            )
            return
        pending.set_result(response)

    async def disconnect_all_clients(self) -> int:
        """Close and forget every client; returns how many were closed.

        The transport half of ``POST /disconnect-ws-clients``
        (``main.go:430-439``), which T004 wires to its route, and also what
        :meth:`stop` uses to drain the socket set.
        """
        clients = list(self._clients)
        self._clients.clear()
        for ws in clients:
            try:
                await ws.close()
            except Exception:  # pragma: no cover - a dead socket is already gone
                _log.debug("asbplayer bridge: error closing a client", exc_info=True)
        if clients:
            _log.debug("asbplayer bridge: forcefully disconnected %d client(s)", len(clients))
        return len(clients)

    # -- publish / correlate (research.md R1.2) ----------------------------

    async def publish_async(self, command: ClientCommand) -> int:
        """Broadcast ``command`` to **every** connected client; return the count
        that accepted it.

        The broadcast is contract, not an implementation choice: Go's
        ``publishMessage`` loops the whole client map (``main.go:131-145``), and
        spec Edge Cases calls out that a second connected tab must see the
        command too. There is no per-client addressing anywhere in the protocol.

        Where Go needs a per-client write mutex (gorilla permits one concurrent
        writer), aiohttp's writer emits each frame as one write from this single
        loop thread, so no lock is needed. A client whose send fails is dropped
        from the set — it is gone, and keeping it would poison every later
        broadcast.
        """
        payload = command.to_json()
        delivered = 0
        for ws in list(self._clients):
            try:
                await ws.send_str(payload)
            except Exception:
                self._clients.discard(ws)
                _log.debug(
                    "asbplayer bridge: dropping client that failed a %s send",
                    command.command,
                )
                continue
            delivered += 1
        _log.debug(
            "asbplayer bridge: published %s (%s) to %d client(s)",
            command.command,
            command.message_id,
            delivered,
        )
        return delivered

    async def publish_and_await_async(
        self, command: ClientCommand, timeout: float | None = None
    ) -> ClientResponse | None:
        """Broadcast and wait for the first reply carrying the same
        ``messageId``. ``None`` means **no answer** — which every relay endpoint
        turns into a 500, exactly as the Go callers turn a closed channel into
        ``echo.NewHTTPError(http.StatusInternalServerError, nil)``.

        ``None`` covers three cases, all indistinguishable to the Go caller too:

        * the deadline expired (5 s — ``main.go:164``, FR-003);
        * the publish failed outright (Go closes the channel immediately,
          ``main.go:150-153``);
        * nobody was connected. Go would have published to an empty map and then
          burned the full 5 s before giving up; answering right away is the same
          500 sooner, and spec Edge Cases explicitly sanctions "times out or
          fails fast" for the no-peer case. It also keeps the test suite from
          paying five real seconds for a case it exercises repeatedly.

        The waiter is registered *before* the publish, so a client that replies
        faster than this coroutine resumes still finds its future.
        """
        deadline = self.reply_timeout_s if timeout is None else timeout
        loop = asyncio.get_running_loop()
        pending: asyncio.Future[ClientResponse] = loop.create_future()
        self._pending[command.message_id] = pending
        try:
            delivered = await self.publish_async(command)
            if delivered == 0:
                _log.debug(
                    "asbplayer bridge: no client to answer %s", command.command
                )
                return None
            return await asyncio.wait_for(pending, timeout=deadline)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _log.info(
                "asbplayer bridge: no answer for %s within %.1fs",
                command.command,
                deadline,
            )
            return None
        except Exception:
            _log.warning(
                "asbplayer bridge: failed to publish %s", command.command, exc_info=True
            )
            return None
        finally:
            # Unregister last: any reply arriving after this point has no waiter
            # and is discarded by _deliver, harmlessly.
            self._pending.pop(command.message_id, None)

    # -- the same two, for callers on another thread -----------------------

    def publish(self, command: ClientCommand) -> int:
        """Thread-safe :meth:`publish_async` for callers off the loop thread."""
        return self._call_on_loop(self.publish_async(command), timeout=_STOP_TIMEOUT_S)

    def publish_and_await(
        self, command: ClientCommand, timeout: float | None = None
    ) -> ClientResponse | None:
        """Thread-safe :meth:`publish_and_await_async` for callers off the loop
        thread. The outer wait is the reply deadline plus a small margin, so the
        inner ``wait_for`` is always what actually expires."""
        deadline = self.reply_timeout_s if timeout is None else timeout
        return self._call_on_loop(
            self.publish_and_await_async(command, timeout=deadline),
            timeout=deadline + 5.0,
        )

    def _call_on_loop(self, coro: Any, *, timeout: float) -> Any:
        loop = self._loop
        if loop is None or self._bound is None:
            coro.close()
            raise RuntimeError("AsbplayerBridgeServer has not been started")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            coro.close()
            raise RuntimeError(
                "called from the bridge's own loop thread — await the "
                "*_async variant instead of the blocking wrapper"
            )
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)


def _json_blob(body: Any) -> web.Response:
    """200 with ``body`` re-serialized as the whole response, never inspected.

    ``body`` is already the decoded JSON value from the client's reply
    (``parse_client_response``) — an object, array, number, string, bool, or
    ``None``. Re-encoding it is a byte-identical round trip for JSON's
    canonical values and is what Go's ``c.JSONBlob`` achieves by writing the
    original bytes back out; there is nothing here that looks at what kind of
    value ``body`` is, including an error object (research.md R1.4).
    """
    return web.json_response(body)


def _first_address(
    addresses: "Iterable[Any]", *, fallback: tuple[str, int]
) -> tuple[str, int]:
    """``AppRunner.addresses`` yields sockaddr tuples (4 long for IPv6)."""
    for sockaddr in addresses:
        if isinstance(sockaddr, (tuple, list)) and len(sockaddr) >= 2:
            return str(sockaddr[0]), int(sockaddr[1])
    return fallback


__all__ = [
    "AsbplayerBridgeServer",
    "BridgeConfig",
    "ClientCommand",
    "ClientResponse",
    "DEFAULT_ANKI_CONNECT_URL",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "KEEPALIVE_PING",
    "KEEPALIVE_PONG",
    "REPLY_TIMEOUT_S",
    "WS_PATH",
    "is_loopback",
    "parse_client_response",
]
