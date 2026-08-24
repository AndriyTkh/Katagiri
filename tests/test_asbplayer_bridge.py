"""T006: the asbplayer bridge protocol suite.

Drives the real ``AsbplayerBridgeServer`` over a real loopback socket on an
ephemeral port (``port=0``, never 8766 — binding rule 4, research.md O-2),
with a scripted WebSocket client standing in for the extension and a stub
AnkiConnect upstream (also on an ephemeral port). Every command, endpoint,
and branch in research.md R1/R3 gets an assertion (SC-002).

Every test leaves no listener behind: the ``bridge`` fixture starts and stops
around each test via try/finally.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from aiohttp import WSMsgType
import aiohttp

from katagiri.asbplayer_bridge import (
    REPLY_TIMEOUT_S,
    AsbplayerBridgeServer,
    BridgeConfig,
    ClientCommand,
    is_loopback,
)

# ---------------------------------------------------------------------------
# A tiny stub AnkiConnect, on an ephemeral port, using the stdlib server so
# it is a completely independent implementation from the bridge under test.
# ---------------------------------------------------------------------------


class _StubAnkiConnectHandler(BaseHTTPRequestHandler):
    server: "_StubAnkiConnect"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    def _record(self, method: str) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(
            {
                "method": method,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            }
        )
        return body

    def _respond(self) -> None:
        status, headers, body = self.server.next_response()
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._record("GET")
        self._respond()

    def do_POST(self) -> None:  # noqa: N802
        self._record("POST")
        self._respond()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._record("OPTIONS")
        self._respond()


class _StubAnkiConnect:
    """A scripted upstream: queue canned (status, headers, body) answers."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._responses: list[tuple[int, list[tuple[str, str]], bytes]] = []
        self._default = (200, [("Content-Type", "application/json")], b'{"result": null, "error": null}')
        self._httpd = HTTPServer(("127.0.0.1", 0), _StubAnkiConnectHandler)
        self._httpd.requests = self.requests  # type: ignore[attr-defined]
        self._httpd.next_response = self._next_response  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def _next_response(self) -> tuple[int, list[tuple[str, str]], bytes]:
        if self._responses:
            return self._responses.pop(0)
        return self._default

    def queue(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
        self._responses.append((status, headers, body))

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5.0)


@pytest.fixture()
def stub_anki():
    stub = _StubAnkiConnect()
    try:
        yield stub
    finally:
        stub.stop()


@pytest.fixture()
def bridge(stub_anki: _StubAnkiConnect):
    """A running bridge on an ephemeral port, pointed at the stub upstream."""
    server = AsbplayerBridgeServer(
        config=BridgeConfig(host="127.0.0.1", port=0, anki_connect_url=stub_anki.url)
    )
    server.start(port=0)
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# A scripted WebSocket client, run on its own asyncio loop/thread so tests
# can stay ordinary synchronous functions while still driving a real socket.
# ---------------------------------------------------------------------------


class _WsClient:
    """Runs an aiohttp ClientSession + WS connection on a private loop."""

    def __init__(self, url: str) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._session, self._ws = self._run(self._connect(url))

    async def _connect(self, url: str):
        session = aiohttp.ClientSession()
        ws = await session.ws_connect(url, timeout=10)
        return session, ws

    def _run(self, coro: Any, timeout: float = 10.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def send_text(self, text: str) -> None:
        self._run(self._ws.send_str(text))

    def send_json_message(self, message_id: str, command: str, body: Any) -> None:
        self.send_text(json.dumps({"command": command, "messageId": message_id, "body": body}))

    def recv(self, timeout: float = 10.0) -> tuple[int, str]:
        async def _recv():
            msg = await self._ws.receive(timeout=timeout)
            return msg.type, msg.data

        typ, data = self._run(_recv(), timeout=timeout + 2)
        return typ, data

    def try_recv(self, timeout: float = 1.0) -> tuple[int, str] | None:
        try:
            return self.recv(timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None

    def close(self) -> None:
        async def _close():
            await self._ws.close()
            await self._session.close()

        try:
            self._run(_close(), timeout=5.0)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)


def _ws_url(bridge: AsbplayerBridgeServer) -> str:
    return f"ws://{bridge.host}:{bridge.port}/ws"


def _make_client(bridge: AsbplayerBridgeServer) -> _WsClient:
    return _WsClient(_ws_url(bridge))


# ---------------------------------------------------------------------------
# Transport: /ws upgrade, PING/PONG, correlation
# ---------------------------------------------------------------------------


def test_ws_upgrade_and_client_count(bridge: AsbplayerBridgeServer) -> None:
    """Connecting to /ws succeeds and is reflected in client_count."""
    assert bridge.client_count == 0
    client = _make_client(bridge)
    try:
        # Give the loop thread a moment to register the connection.
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)
        assert bridge.client_count == 1
    finally:
        client.close()


def test_ping_gets_text_pong(bridge: AsbplayerBridgeServer) -> None:
    """Literal text PING is answered with literal text PONG."""
    client = _make_client(bridge)
    try:
        client.send_text("PING")
        typ, data = client.recv()
        assert typ is WSMsgType.TEXT
        assert data == "PONG"
    finally:
        client.close()


def test_ping_is_not_treated_as_a_response(bridge: AsbplayerBridgeServer) -> None:
    """A PING must not satisfy a pending publish_and_await — it is not JSON,
    has no messageId, and so cannot be delivered to any waiter."""
    client = _make_client(bridge)
    try:
        # Wait for connection.
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        async def _await_with_short_timeout():
            return await bridge.publish_and_await_async(
                ClientCommand.new("get-bound-media"), timeout=0.3
            )

        fut = asyncio.run_coroutine_threadsafe(_await_with_short_timeout(), bridge._loop)
        # Drain the command frame the publish just sent, then answer with a
        # PING instead of the expected reply.
        typ, data = client.recv()
        assert json.loads(data)["command"] == "get-bound-media"
        client.send_text("PING")
        typ, data = client.recv()
        assert data == "PONG"
        result = fut.result(timeout=5.0)
        assert result is None  # PING did not satisfy the waiter; it timed out.
    finally:
        client.close()


# ---------------------------------------------------------------------------
# The six commands' exact envelopes/bodies
# ---------------------------------------------------------------------------


def _post_json(bridge: AsbplayerBridgeServer, path: str, body: Any) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _get(bridge: AsbplayerBridgeServer, path: str) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _serve_one_reply(client: _WsClient, body: Any) -> dict[str, Any]:
    """Wait for the server's command frame, reply with `body`, return the
    envelope that was received (for assertion on command/messageId shape)."""
    typ, data = client.recv(timeout=10)
    assert typ is WSMsgType.TEXT
    envelope = json.loads(data)
    assert set(envelope.keys()) == {"command", "messageId", "body"}
    assert isinstance(envelope["messageId"], str) and envelope["messageId"]
    client.send_json_message(envelope["messageId"], envelope["command"], body)
    return envelope


def test_load_subtitles_envelope_and_empty_body(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(
                bridge, "/asbplayer/load-subtitles", {"files": [{"name": "a.srt", "base64": "xx"}]}
            )

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {"ok": True})
        t.join(timeout=10)

        assert envelope["command"] == "load-subtitles"
        assert envelope["body"] == {"files": [{"name": "a.srt", "base64": "xx"}]}
        assert result["status"] == 200
        assert result["body"] == b""  # empty string body, not JSON
    finally:
        client.close()


def test_seek_envelope_with_media_id_and_seconds(bridge: AsbplayerBridgeServer) -> None:
    """timestamp is seconds (float), mediaId only included when non-empty."""
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(
                bridge, "/asbplayer/seek", {"timestamp": 12.5, "mediaId": "m1"}
            )

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {})
        t.join(timeout=10)

        assert envelope["command"] == "seek-timestamp"
        assert envelope["body"] == {"timestamp": 12.5, "mediaId": "m1"}
        assert result["status"] == 200
        assert result["body"] == b""
    finally:
        client.close()


def test_seek_omits_media_id_when_empty(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(
                bridge, "/asbplayer/seek", {"timestamp": 3.0, "mediaId": ""}
            )

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {})
        t.join(timeout=10)

        assert envelope["body"] == {"timestamp": 3.0}
        assert "mediaId" not in envelope["body"]
    finally:
        client.close()


def test_bound_media_envelope_and_verbatim_body(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(bridge, "/asbplayer/bound-media")

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {"mediaId": "abc", "duration": 42})
        t.join(timeout=10)

        assert envelope["command"] == "get-bound-media"
        assert envelope["body"] == {}
        assert result["status"] == 200
        assert json.loads(result["body"]) == {"mediaId": "abc", "duration": 42}
    finally:
        client.close()


def test_subtitles_with_media_id_and_track_numbers(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(
                bridge, "/asbplayer/subtitles?mediaId=abc&trackNumbers=1,2,x,3"
            )

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, [{"text": "hi"}])
        t.join(timeout=10)

        assert envelope["command"] == "get-subtitles"
        # non-numeric entries dropped, order preserved
        assert envelope["body"] == {"mediaId": "abc", "trackNumbers": [1, 2, 3]}
        assert result["status"] == 200
        assert json.loads(result["body"]) == [{"text": "hi"}]
    finally:
        client.close()


def test_subtitles_omits_track_numbers_when_none_parse(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(bridge, "/asbplayer/subtitles?trackNumbers=x,y")

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, [])
        t.join(timeout=10)

        assert envelope["body"] == {}
        assert "trackNumbers" not in envelope["body"]
        assert "mediaId" not in envelope["body"]
    finally:
        client.close()


def test_playback_state_envelope_and_milliseconds_passthrough(
    bridge: AsbplayerBridgeServer,
) -> None:
    """Reply's own units are integer milliseconds; never normalized here."""
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(
                bridge, "/asbplayer/playback-state?mediaId=m9"
            )

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {"timestampMs": 123456, "playing": True})
        t.join(timeout=10)

        assert envelope["command"] == "get-playback-state"
        assert envelope["body"] == {"mediaId": "m9"}
        assert result["status"] == 200
        assert json.loads(result["body"]) == {"timestampMs": 123456, "playing": True}
    finally:
        client.close()


def test_playback_state_omits_media_id_when_absent(bridge: AsbplayerBridgeServer) -> None:
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(bridge, "/asbplayer/playback-state")

        t = threading.Thread(target=_http_call)
        t.start()
        envelope = _serve_one_reply(client, {"timestampMs": 0})
        t.join(timeout=10)

        assert envelope["body"] == {}
    finally:
        client.close()


def test_relay_reply_with_error_object_still_200(bridge: AsbplayerBridgeServer) -> None:
    """A reply body containing an error object still yields 200 — the bridge
    never inspects the reply body (research.md R1.4)."""
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(bridge, "/asbplayer/bound-media")

        t = threading.Thread(target=_http_call)
        t.start()
        _serve_one_reply(client, {"error": "no media bound"})
        t.join(timeout=10)

        assert result["status"] == 200
        assert json.loads(result["body"]) == {"error": "no media bound"}
    finally:
        client.close()


# ---------------------------------------------------------------------------
# messageId correlation: mismatched reply ignored, late reply discarded
# without disturbing a second in-flight request (G-4).
# ---------------------------------------------------------------------------


def test_mismatched_reply_ignored(bridge: AsbplayerBridgeServer) -> None:
    """A reply whose messageId does not match any pending request is dropped;
    the real request still needs its own (correctly addressed) reply."""
    client = _make_client(bridge)
    try:
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _get(bridge, "/asbplayer/bound-media")

        t = threading.Thread(target=_http_call)
        t.start()

        typ, data = client.recv(timeout=10)
        envelope = json.loads(data)

        # Send a reply under a bogus, unrelated messageId first.
        client.send_json_message("not-a-real-id", envelope["command"], {"bogus": True})
        # Then the real reply.
        client.send_json_message(envelope["messageId"], envelope["command"], {"real": True})
        t.join(timeout=10)

        assert result["status"] == 200
        assert json.loads(result["body"]) == {"real": True}
    finally:
        client.close()


def test_late_reply_does_not_disturb_second_in_flight_request(
    bridge: AsbplayerBridgeServer,
) -> None:
    """Request A times out (its deadline is shortened via reply_timeout_s).
    Its late reply arrives after a second request B is already in flight and
    must be discarded without satisfying B's waiter."""
    short_server = AsbplayerBridgeServer(
        config=BridgeConfig(host="127.0.0.1", port=0),
        reply_timeout_s=0.3,
    )
    short_server.start(port=0)
    client = _WsClient(_ws_url(short_server))
    try:
        loop = short_server._loop

        async def _request_a():
            return await short_server.publish_and_await_async(
                ClientCommand.new("get-bound-media")
            )

        fut_a = asyncio.run_coroutine_threadsafe(_request_a(), loop)

        # Read A's command off the wire but do not answer it yet.
        typ, data = client.recv(timeout=10)
        envelope_a = json.loads(data)

        # Let A's deadline expire.
        result_a = fut_a.result(timeout=5.0)
        assert result_a is None  # A timed out — no waiter registered any more.

        # Now start B.
        async def _request_b():
            return await short_server.publish_and_await_async(
                ClientCommand.new("get-bound-media")
            )

        fut_b = asyncio.run_coroutine_threadsafe(_request_b(), loop)
        typ, data = client.recv(timeout=10)
        envelope_b = json.loads(data)
        assert envelope_b["messageId"] != envelope_a["messageId"]

        # A's late reply arrives now — must be dropped, not delivered to B.
        client.send_json_message(envelope_a["messageId"], "get-bound-media", {"stale": True})

        # B's own reply.
        client.send_json_message(envelope_b["messageId"], "get-bound-media", {"fresh": True})

        result_b = fut_b.result(timeout=5.0)
        assert result_b is not None
        assert result_b.body == {"fresh": True}
    finally:
        client.close()
        short_server.stop()


# ---------------------------------------------------------------------------
# The 5-second deadline
# ---------------------------------------------------------------------------


def test_default_reply_timeout_constant_is_five_seconds() -> None:
    assert REPLY_TIMEOUT_S == 5.0


def test_reply_deadline_is_injectable_and_produces_500(bridge: AsbplayerBridgeServer) -> None:
    """A connected client that never answers must eventually 500, on a
    shortened deadline rather than the real 5s (using reply_timeout_s)."""
    short_server = AsbplayerBridgeServer(
        config=BridgeConfig(host="127.0.0.1", port=0),
        reply_timeout_s=0.3,
    )
    short_server.start(port=0)
    client = _WsClient(_ws_url(short_server))
    try:
        start = time.monotonic()
        status, _body = _get(short_server, "/asbplayer/bound-media")
        elapsed = time.monotonic() - start
        assert status == 500
        assert elapsed < 3.0  # nowhere near the real 5s default
    finally:
        client.close()
        short_server.stop()


def test_no_client_connected_fails_fast_not_after_full_deadline(
    bridge: AsbplayerBridgeServer,
) -> None:
    """No connected client: 500 without waiting out the deadline at all."""
    start = time.monotonic()
    status, _body = _get(bridge, "/asbplayer/bound-media")
    elapsed = time.monotonic() - start
    assert status == 500
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# Broadcast to two connected clients
# ---------------------------------------------------------------------------


def test_broadcast_reaches_two_clients(bridge: AsbplayerBridgeServer) -> None:
    client_a = _make_client(bridge)
    client_b = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert bridge.client_count == 2

        async def _publish():
            return await bridge.publish_async(ClientCommand.new("seek-timestamp", {"timestamp": 1.0}))

        delivered = asyncio.run_coroutine_threadsafe(_publish(), bridge._loop).result(timeout=5.0)
        assert delivered == 2

        typ_a, data_a = client_a.recv(timeout=5)
        typ_b, data_b = client_b.recv(timeout=5)
        assert json.loads(data_a)["command"] == "seek-timestamp"
        assert json.loads(data_b)["command"] == "seek-timestamp"
    finally:
        client_a.close()
        client_b.close()


# ---------------------------------------------------------------------------
# Relay endpoints' 400/500 shapes, empty-string bodies
# ---------------------------------------------------------------------------


def test_load_subtitles_400_on_unparsable_body(bridge: AsbplayerBridgeServer) -> None:
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("POST", "/asbplayer/load-subtitles", body=b"{not json", headers={})
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        conn.close()


def test_seek_400_on_unparsable_body(bridge: AsbplayerBridgeServer) -> None:
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("POST", "/asbplayer/seek", body=b"[not-an-object", headers={})
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        conn.close()


def test_seek_400_on_non_object_json_body(bridge: AsbplayerBridgeServer) -> None:
    """A syntactically valid JSON body that is not an object is still a 400
    for /asbplayer/seek, which requires a dict to read timestamp/mediaId from."""
    status, _body = _post_json(bridge, "/asbplayer/seek", [1, 2, 3])
    assert status == 400


def test_load_subtitles_500_on_no_answer(bridge: AsbplayerBridgeServer) -> None:
    """No client connected: publish_and_await returns None -> 500."""
    status, body = _post_json(bridge, "/asbplayer/load-subtitles", {"files": []})
    assert status == 500


def test_bound_media_500_on_no_answer(bridge: AsbplayerBridgeServer) -> None:
    status, body = _get(bridge, "/asbplayer/bound-media")
    assert status == 500


def test_disconnect_ws_clients_returns_200_empty_body(bridge: AsbplayerBridgeServer) -> None:
    client_a = _make_client(bridge)
    client_b = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert bridge.client_count == 2

        conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
        try:
            conn.request("POST", "/disconnect-ws-clients")
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200
            assert body == b""
        finally:
            conn.close()

        deadline = time.time() + 2.0
        while bridge.client_count != 0 and time.time() < deadline:
            time.sleep(0.02)
        assert bridge.client_count == 0
    finally:
        client_a.close()
        client_b.close()


# ---------------------------------------------------------------------------
# AnkiConnect proxy: non-addNote forward
# ---------------------------------------------------------------------------


def test_anki_get_forwards_headers_and_status(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    stub_anki.queue(
        200, [("Content-Type", "application/json"), ("X-Custom", "yes")], b'{"result": 6, "error": null}'
    )
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("GET", "/", headers={"X-Test": "abc"})
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert resp.getheader("X-Custom") == "yes"
        assert json.loads(body) == {"result": 6, "error": None}
    finally:
        conn.close()

    assert stub_anki.requests[-1]["method"] == "GET"
    assert stub_anki.requests[-1]["headers"].get("X-Test") == "abc"


def test_anki_post_non_addnote_forwards_with_headers_and_status_intact(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": "ok", "error": null}')
    request_body = {"action": "deckNames", "params": {}}
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request(
            "POST",
            "/",
            body=json.dumps(request_body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Custom-Header": "present"},
        )
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert json.loads(body) == {"result": "ok", "error": None}
    finally:
        conn.close()

    forwarded = stub_anki.requests[-1]
    assert forwarded["method"] == "POST"
    assert forwarded["headers"].get("X-Custom-Header") == "present"
    assert json.loads(forwarded["body"]) == request_body


def test_anki_post_addnote_no_client_forwards(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    """addNote with zero connected clients: pass-through condition trips on
    client_count == 0, so it forwards even though action == addNote."""
    stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 55, "error": null}')
    request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}
    status, body = _post_json(bridge, "/", request_body)
    assert status == 200
    assert json.loads(body) == {"result": 55, "error": None}
    assert stub_anki.requests[-1]["method"] == "POST"


def test_anki_options_forwards_empty_body_and_relays_headers(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    stub_anki.queue(
        200,
        [("Access-Control-Allow-Origin", "*"), ("Content-Type", "text/plain")],
        b"",
    )
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("OPTIONS", "/")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert resp.getheader("Access-Control-Allow-Origin") == "*"
        assert body == b""
    finally:
        conn.close()

    assert stub_anki.requests[-1]["method"] == "OPTIONS"
    assert stub_anki.requests[-1]["body"] == b""


def test_anki_get_500_json_null_on_forward_failure(bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect) -> None:
    """A failed forward answers 500 with a JSON null body."""
    stub_anki.stop()  # upstream now unreachable
    status, body = _get(bridge, "/")
    assert status == 500
    assert json.loads(body) is None


def test_anki_post_500_on_forward_failure(bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect) -> None:
    stub_anki.stop()
    status, body = _post_json(bridge, "/", {"action": "deckNames", "params": {}})
    assert status == 500


def test_anki_post_400_on_unparsable_body(bridge: AsbplayerBridgeServer) -> None:
    conn = http.client.HTTPConnection(bridge.host, bridge.port, timeout=10)
    try:
        conn.request("POST", "/", body=b"{not json", headers={})
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
    finally:
        conn.close()


def test_anki_post_verbatim_json_blob_passthrough(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    """A non-addNote body is forwarded byte-for-byte, verbatim JSON blob and
    all (including keys the bridge never inspects)."""
    stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": [1,2,3], "error": null}')
    request_body = {"action": "findNotes", "params": {"query": "deck:current"}, "extra": {"nested": True}}
    status, body = _post_json(bridge, "/", request_body)
    assert status == 200
    assert json.loads(body) == {"result": [1, 2, 3], "error": None}
    assert json.loads(stub_anki.requests[-1]["body"]) == request_body


# ---------------------------------------------------------------------------
# addNote intercept: match / mismatch
# ---------------------------------------------------------------------------


def test_addnote_intercept_default_matches_everything(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    """Default INTERCEPT_FIELD/VALUE are both empty -> intercept every addNote
    while a client is connected."""
    client = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 77, "error": null}')
        request_body = {
            "action": "addNote",
            "params": {"note": {"fields": {"Front": "x", "Back": "y"}}},
        }
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(bridge, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        # POST_MINE_ACTION default is 2: forward-first branch, no publish wait
        # needed for the HTTP response, but we should still see the
        # mine-subtitle command land on the socket.
        typ, data = client.recv(timeout=10)
        envelope = json.loads(data)
        t.join(timeout=10)

        assert envelope["command"] == "mine-subtitle"
        assert envelope["body"]["fields"] == {"Front": "x", "Back": "y"}
        assert envelope["body"]["postMineAction"] == 2
        assert result["status"] == 200
    finally:
        client.close()


def test_addnote_intercept_field_mismatch_forwards(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    """A configured INTERCEPT_FIELD/VALUE that does not match the note's field
    value: do not intercept, forward as a normal addNote."""
    server = AsbplayerBridgeServer(
        config=BridgeConfig(
            host="127.0.0.1",
            port=0,
            anki_connect_url=stub_anki.url,
            intercept_field="Tag",
            intercept_value="mine-me",
        )
    )
    server.start(port=0)
    client = _WsClient(_ws_url(server))
    try:
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 99, "error": null}')
        request_body = {
            "action": "addNote",
            "params": {"note": {"fields": {"Tag": "not-a-match"}}},
        }
        status, body = _post_json(server, "/", request_body)
        assert status == 200
        assert json.loads(body) == {"result": 99, "error": None}
        assert stub_anki.requests[-1]["method"] == "POST"
        assert json.loads(stub_anki.requests[-1]["body"]) == request_body
    finally:
        client.close()
        server.stop()


def test_addnote_intercept_field_match_intercepts(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    server = AsbplayerBridgeServer(
        config=BridgeConfig(
            host="127.0.0.1",
            port=0,
            anki_connect_url=stub_anki.url,
            intercept_field="Tag",
            intercept_value="mine-me",
            post_mine_action=0,
        )
    )
    server.start(port=0)
    client = _WsClient(_ws_url(server))
    try:
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        request_body = {
            "action": "addNote",
            "params": {"note": {"fields": {"Tag": "mine-me"}}},
        }
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(server, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        _serve_one_reply(client, {"published": True})
        t.join(timeout=10)

        assert result["status"] == 200
        assert json.loads(result["body"]) == -1
        assert stub_anki.requests == []  # never forwarded
    finally:
        client.close()
        server.stop()


# ---------------------------------------------------------------------------
# POST_MINE_ACTION == 2: forwarded first, noteId attached, no await
# ---------------------------------------------------------------------------


def test_post_mine_action_2_forwards_first_attaches_note_id_no_await(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    """default post_mine_action is 2. AnkiConnect is called first; its result
    becomes noteId on the mine-subtitle command; the HTTP response returns
    without waiting for the client's reply to that command."""
    client = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 4242, "error": null}')
        request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}

        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(bridge, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        t.join(timeout=10)  # HTTP response must complete without any WS reply

        assert result["status"] == 200
        assert json.loads(result["body"]) == {"result": 4242, "error": None}

        # The mine-subtitle command was still published (fire-and-forget) with
        # noteId attached — read it off the socket now.
        typ, data = client.recv(timeout=5)
        envelope = json.loads(data)
        assert envelope["command"] == "mine-subtitle"
        assert envelope["body"]["noteId"] == 4242
        assert envelope["body"]["postMineAction"] == 2
    finally:
        client.close()


def test_post_mine_action_2_omits_note_id_when_unparsable(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    client = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": null, "error": null}')
        request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}
        status, body = _post_json(bridge, "/", request_body)
        assert status == 200

        typ, data = client.recv(timeout=5)
        envelope = json.loads(data)
        assert "noteId" not in envelope["body"]
    finally:
        client.close()


# ---------------------------------------------------------------------------
# non-2 with published:true -> 200, body -1, stub upstream never called
# ---------------------------------------------------------------------------


def test_post_mine_action_non2_published_true_never_calls_upstream(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect
) -> None:
    server = AsbplayerBridgeServer(
        config=BridgeConfig(host="127.0.0.1", port=0, anki_connect_url=stub_anki.url, post_mine_action=0)
    )
    server.start(port=0)
    client = _WsClient(_ws_url(server))
    try:
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(server, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        _serve_one_reply(client, {"published": True})
        t.join(timeout=10)

        assert result["status"] == 200
        assert json.loads(result["body"]) == -1
        assert stub_anki.requests == [], "the stub upstream must never be called on published:true"
    finally:
        client.close()
        server.stop()


# ---------------------------------------------------------------------------
# non-2 with published:false / malformed / absent -> forwarded after all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply_body",
    [
        {"published": False},
        {"published": "not-a-bool"},
        {"unrelated": True},
        [1, 2, 3],
        "not even an object",
    ],
    ids=["false", "malformed-type", "absent", "non-object-list", "non-object-string"],
)
def test_post_mine_action_non2_falls_back_to_forward(
    bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect, reply_body: Any
) -> None:
    server = AsbplayerBridgeServer(
        config=BridgeConfig(host="127.0.0.1", port=0, anki_connect_url=stub_anki.url, post_mine_action=1)
    )
    server.start(port=0)
    client = _WsClient(_ws_url(server))
    try:
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 321, "error": null}')
        request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}
        result: dict[str, Any] = {}

        def _http_call():
            result["status"], result["body"] = _post_json(server, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        _serve_one_reply(client, reply_body)
        t.join(timeout=10)

        assert result["status"] == 200
        assert json.loads(result["body"]) == {"result": 321, "error": None}
        assert stub_anki.requests, "expected the original addNote to be forwarded"
        assert json.loads(stub_anki.requests[-1]["body"]) == request_body
    finally:
        client.close()
        server.stop()


def test_post_mine_action_non2_no_answer_500(bridge: AsbplayerBridgeServer, stub_anki: _StubAnkiConnect) -> None:
    """No connected client and post_mine_action != 2: publish_and_await
    returns None -> 500 (the client_count==0 check is upstream of the
    intercept, so with zero clients this actually falls into the plain
    forward branch instead — use a client that connects then never answers
    to hit the true awaited-and-empty case)."""
    server = AsbplayerBridgeServer(
        config=BridgeConfig(
            host="127.0.0.1",
            port=0,
            anki_connect_url=stub_anki.url,
            post_mine_action=1,
        ),
        reply_timeout_s=0.3,
    )
    server.start(port=0)
    client = _WsClient(_ws_url(server))
    try:
        deadline = time.time() + 2.0
        while server.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        request_body = {"action": "addNote", "params": {"note": {"fields": {"Front": "x"}}}}
        status, _body = _post_json(server, "/", request_body)
        assert status == 500
    finally:
        client.close()
        server.stop()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_stop_releases_the_port() -> None:
    server = AsbplayerBridgeServer(config=BridgeConfig(host="127.0.0.1", port=0))
    host, port = server.start(port=0)
    server.stop()
    assert not server.is_running
    # The port should be free again: a bare connect must fail (refused).
    import socket

    with pytest.raises(OSError):
        s = socket.create_connection((host, port), timeout=1.0)
        s.close()


def test_stop_start_cycle_rebinds() -> None:
    server = AsbplayerBridgeServer(config=BridgeConfig(host="127.0.0.1", port=0))
    try:
        host1, port1 = server.start(port=0)
        server.stop()
        host2, port2 = server.start(port=0)
        assert server.is_running
        # A fresh bind on an ephemeral port succeeded again.
        status, _ = _get(server, "/asbplayer/bound-media")
        assert status == 500  # no client, but the server answered at all
    finally:
        server.stop()


def test_default_bind_is_loopback() -> None:
    config = BridgeConfig()
    assert is_loopback(config.host)
    assert config.host == "127.0.0.1"


def test_non_loopback_override_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    server = AsbplayerBridgeServer(config=BridgeConfig(host="0.0.0.0", port=0))
    caplog.set_level(logging.WARNING, logger="katagiri.asbplayer_bridge")
    try:
        server.start(port=0)
        assert any(
            "NON-LOOPBACK" in record.message or "non-loopback" in record.message.lower()
            for record in caplog.records
        )
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# No-stdout and no-note-body-in-logs
# ---------------------------------------------------------------------------


def test_no_stdout_output(
    bridge: AsbplayerBridgeServer,
    stub_anki: _StubAnkiConnect,
    capsys: pytest.CaptureFixture,
) -> None:
    """Nothing the bridge does writes to stdout — stdout is the MCP stdio
    transport (FR-017)."""
    client = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 1, "error": null}')
        request_body = {
            "action": "addNote",
            "params": {"note": {"fields": {"Front": "canary-field-value-xyz"}}},
        }

        def _http_call():
            _post_json(bridge, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        t.join(timeout=10)

        # Drain any pending mine-subtitle command so the connection stays clean.
        client.try_recv(timeout=1.0)
    finally:
        client.close()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_note_field_never_appears_in_logs(
    bridge: AsbplayerBridgeServer,
    stub_anki: _StubAnkiConnect,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plant a canary field value in a note; assert it appears in no log
    record at all (FR-017 / G-5)."""
    canary = "SECRET-CANARY-VALUE-abcdef123456"
    caplog.set_level(logging.DEBUG, logger="katagiri.asbplayer_bridge")

    client = _make_client(bridge)
    try:
        deadline = time.time() + 2.0
        while bridge.client_count == 0 and time.time() < deadline:
            time.sleep(0.02)

        stub_anki.queue(200, [("Content-Type", "application/json")], b'{"result": 1, "error": null}')
        request_body = {
            "action": "addNote",
            "params": {"note": {"fields": {"Front": canary}}},
        }

        def _http_call():
            _post_json(bridge, "/", request_body)

        t = threading.Thread(target=_http_call)
        t.start()
        t.join(timeout=10)
        client.try_recv(timeout=1.0)
    finally:
        client.close()

    for record in caplog.records:
        assert canary not in record.getMessage()
