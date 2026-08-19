r"""kata-bvf: the Phase B gate, verified end to end over the real stdio surface.

This is the sibling of ``tests/test_averify.py`` and is read the same way: a
*cold* verification harness, not a unit-test suite. Nothing here mocks a Katagiri
module. Where A-verify proves the Phase A pipeline's arithmetic, B-verify is
**cumulative** — it re-asserts the A-level protocol invariants (the handshake
completes, the tool list is exactly the contract, stdout carries protocol frames
only, the startup line lands on stderr) and then extends them to the two things
Phase B added:

* ``today_export`` — the aggregate exporter that writes ``<vault>/.derived/Today.md``;
* ``obsidian_proxy`` plus its three MCP tools (``vault_file``, ``vault_list``,
  ``obsidian_active_note``) — a GET-only proxy in front of
  obsidian-local-rest-api on ``127.0.0.1:27123``.

The A-verify database is rebuilt here deliberately *cheaply* — migrate, seed a
handful of items, mark one known — rather than by re-importing the vendored
JMdict zip. Nothing asserted below reads the dictionary, and a ~25s import that
no assertion depends on would only make this gate slower to run, not stronger.
The parts that must be real are real: a genuine ``katagiri.mcp_server``
subprocess spoken to over JSON-RPC on its stdin/stdout, a genuine
``python -m katagiri.today_export`` subprocess, and a genuine ``config.toml``
under a temporary ``%LOCALAPPDATA%``.

Both halves of B-verify's read scenario are exercised against a **loopback stub**
that stands in for obsidian-local-rest-api (:class:`_VaultStub`): ``Today.md`` and
an arbitrary nested note are read *successfully* over the wire, and the stub
records the ``Authorization`` header of everything that arrives. Without it the
gate would only ever have seen the unreachable path, which proves that a failure
does not leak a token but says nothing about whether a success does — and the
success path is the one that actually carries the credential.

The stub binds :data:`obsidian_proxy.OBSIDIAN_PORT` itself, because the proxy has
no port override: scheme, host and port are module constants, which is one of the
properties being verified. So the stub cannot move aside, and the two tests that
need it **skip** — loudly, naming why — when something is already listening on
27123, which on a developer's machine means a real Obsidian with the plugin
enabled. Everything else in this file runs regardless.

**The load-bearing claim of this file is the token boundary.** "Direct-HTTP
bypass refused" is asserted here with one binding definition: *the agent can
never authenticate to :27123 itself, because the token never crosses the MCP
boundary.* That is not a claim about intent or about a docstring — it is checked
four ways, and all of them have to hold:

1. The configured token is a canary (:data:`CANARY_TOKEN`). Every tool that
   touches Obsidian is called over the wire, and the canary must appear in **no**
   response byte — nor in ``security_status`` or ``stop_gate_status``, the two
   tools whose job is to describe local state and which therefore have the most
   plausible reason to leak one.
2. Nothing in the declared contract — no tool name, no description, no input or
   output schema, anywhere in the raw ``tools/list`` frame — mentions ``PUT``,
   ``PATCH``, ``DELETE`` or ``command_execute``. There is no write verb for a
   prompt-injected model to aim at, and no argument shaped like a URL, a method
   or a header through which it could reach one.
3. The package ships no HTTP *server* at all (scanned over every ``.py`` under
   ``src/katagiri``), and exactly one HTTP *client*, ``obsidian_proxy``. So there
   is no second transport on which an agent could arrive holding a token it read
   somewhere else, and no other module that could have sent one out.
4. On a *successful* read, the canary is observed **at the stub** — on the
   ``Authorization`` header of a GET that arrived from ``127.0.0.1`` — and is
   absent from every byte the MCP client received. The same stub then answers 401
   to a direct request made without that header and to one made with a wrong
   token, and 200 to one made with the right token. That last control is what
   makes the refusal mean something: the 401s are about the credential, not about
   a path or a method, and the credential is the thing that provably never crossed
   the boundary. So a replay assembled from everything the agent has been handed
   cannot authenticate — demonstrated, not inferred from the absence of a path.

No marker is applied to anything here, matching ``test_averify.py``: the repo's
``slow`` marker means "skips only if vendor data absent", and nothing in this
file reads vendor data. The subprocesses cost seconds, not minutes.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from katagiri import events, mcp_server, obsidian_proxy, today_export
from katagiri import config as config_mod
from katagiri.db import open_db

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "src" / "katagiri"

#: The value written into ``config.toml`` as ``obsidian_api_token``. Distinctive
#: on purpose: it is searched for, byte for byte, in every response this file
#: collects, so a substring of it appearing anywhere is a leak and nothing else.
CANARY_TOKEN = "SECRET-BVF-CANARY"

PROTOCOL_VERSION = "2026-07-28"

#: The full tool contract as of Phase B. Mirrored here rather than imported from
#: ``katagiri.tool_registry`` on purpose — a gate that asks the product what its
#: contract is cannot notice the contract changing. Mirrored from
#: ``test_averify.py`` rather than imported from it for the same reason, one level
#: up: this file must fail on its own if a tool is added, removed or renamed.
CONTRACT_TOOLS = frozenset(
    {
        "ping",
        "known_word",
        "known_set_stats",
        "recent_events",
        "search_db",
        "lookup",
        "stop_gate_status",
        "security_status",
        "vault_file",
        "vault_list",
        "obsidian_active_note",
    }
)

#: The three Phase B additions, called individually below.
VAULT_TOOLS = ("vault_file", "vault_list", "obsidian_active_note")

#: Every ``error`` code the proxy is allowed to answer with. A code outside this
#: set means the proxy invented an outcome its own contract does not declare.
KNOWN_PROXY_ERRORS = frozenset(
    {
        None,
        obsidian_proxy.UNCONFIGURED,
        obsidian_proxy.UNREACHABLE,
        obsidian_proxy.TIMED_OUT,
        obsidian_proxy.HTTP_ERROR,
        obsidian_proxy.BAD_RESPONSE,
        obsidian_proxy.LISTING_TOO_LARGE,
    }
)

#: What a leaked traceback looks like on the wire.
TRACEBACK_MARKERS = ("Traceback (most recent call last)", 'File "', "  File \"")

_TS = "T00:00:00Z"

#: Items seeded so the render and the known-set section have something real to
#: describe. The first is the one that gets marked known, which also makes today
#: an artifact day for the streak section.
FIXTURE_WORDS: tuple[tuple[str, str, str], ...] = (
    ("w-bvf-taberu", "食べる", "たべる"),
    ("w-bvf-benkyou", "勉強", "べんきょう"),
)
KNOWN_WORD_ID = FIXTURE_WORDS[0][0]


# ---------------------------------------------------------------------------
# Write-verb and transport scanning
# ---------------------------------------------------------------------------

#: Word-boundary anchored on purpose. A naive substring scan for "PUT" matches
#: ``outputSchema`` and ``inputSchema`` — both of which are in every MCP frame —
#: so it would either fail always or be relaxed into meaninglessness. ``\b`` puts
#: the check where it belongs: the *verb*, standing alone.
WRITE_VERB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PUT", re.compile(r"\bput\b", re.IGNORECASE)),
    ("PATCH", re.compile(r"\bpatch\b", re.IGNORECASE)),
    ("DELETE", re.compile(r"\bdelete\b", re.IGNORECASE)),
    ("command_execute", re.compile(r"command[_\-\s]*execute", re.IGNORECASE)),
)

#: HTTP *server* constructs. Any one of these inside ``src/katagiri`` would mean
#: the process can be reached over a socket, which is the premise the whole token
#: boundary rests on: an agent that can only speak stdio to this server cannot
#: also be the thing that opened a listening port.
HTTP_SERVER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("http.server / HTTPServer", re.compile(r"\bhttp\.server\b|\bHTTPServer\b")),
    ("socketserver", re.compile(r"\bsocketserver\b|\bBaseHTTPRequestHandler\b")),
    ("uvicorn", re.compile(r"\buvicorn\b", re.IGNORECASE)),
    ("fastapi", re.compile(r"\bfastapi\b", re.IGNORECASE)),
    ("starlette", re.compile(r"\bstarlette\b", re.IGNORECASE)),
    ("flask", re.compile(r"\bflask\b", re.IGNORECASE)),
    ("aiohttp web", re.compile(r"\baiohttp\b|\bweb\.Application\b|\brun_app\b")),
    ("werkzeug / bottle / tornado", re.compile(r"\bwerkzeug\b|\bbottle\b|\btornado\b")),
    ("socket bind/listen", re.compile(r"\.bind\(\s*\(|\.listen\(|\bserve_forever\b")),
    ("asyncio server", re.compile(r"\b(start_server|create_server|loop\.run_forever)\b")),
    ("non-stdio MCP transport", re.compile(r"transport\s*=\s*[\"'](?!stdio)")),
    ("sse / streamable-http transport", re.compile(r"sse_app|streamable[_\-]http")),
)

#: HTTP *client* constructs. Allowed in exactly one module. ``urllib.parse`` is
#: deliberately not here: three modules use ``quote`` to build a SQLite file URI,
#: which involves no network at all.
#: Anchored on the two shapes a client actually takes — an import at the start of a
#: line, or an attribute *call* — rather than on the bare dotted name. A bare-name
#: scan reads prose as code: ``config.py`` documents why a malformed token must
#: never reach ``http.client.putheader``'s error message (that message quotes the
#: value it choked on), and a module explaining a credential hazard is describing
#: the risk, not taking it. Anchoring is not a relaxation — a client has to be
#: imported to be used, and used to matter, so both remain caught; what stops
#: matching is a sentence.
HTTP_CLIENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "urllib.request",
        re.compile(
            r"^[ \t]*(?:import|from)[ \t]+urllib\.request\b"
            r"|\burllib\.request\.[A-Za-z_]\w*\s*\(",
            re.MULTILINE,
        ),
    ),
    (
        "http.client",
        re.compile(
            r"^[ \t]*(?:import|from)[ \t]+http\.client\b"
            r"|\bhttp\.client\.[A-Za-z_]\w*\s*\(",
            re.MULTILINE,
        ),
    ),
    (
        "requests / httpx",
        re.compile(
            r"^[ \t]*(?:import|from)[ \t]+(?:requests|httpx)\b"
            r"|\b(?:requests|httpx)\.(?:get|post|put|patch|delete|request|"
            r"Client|Session)\s*\(",
            re.MULTILINE,
        ),
    ),
)
HTTP_CLIENT_ALLOWLIST = frozenset({"obsidian_proxy.py"})


def package_sources() -> list[Path]:
    """Every ``.py`` file the wheel ships, found by walking rather than by list."""
    found = sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert found, f"no python sources found under {PACKAGE_ROOT}"
    return found


# ---------------------------------------------------------------------------
# The cold environment: a temporary %LOCALAPPDATA% carrying the canary token
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cold(tmp_path_factory) -> dict[str, Any]:
    """A migrated database, a vault, and a ``config.toml`` holding the canary.

    Module-scoped so one MCP subprocess can serve every wire assertion. The
    seeding connection is closed before anything is spawned: the server opens the
    same file itself, and a gate should not depend on two connections sharing a
    WAL.
    """
    root = tmp_path_factory.mktemp("bverify")
    app_data = root / "AppData"
    (app_data / "Katagiri").mkdir(parents=True)
    db_path = root / "katagiri.db"
    vault = root / "vault"
    vault.mkdir()
    (app_data / "Katagiri" / "config.toml").write_text(
        f'db_path = "{db_path.as_posix()}"\n'
        f'scratch_root = "{(root / "scratch").as_posix()}"\n'
        f'vault_path = "{vault.as_posix()}"\n'
        f'obsidian_api_token = "{CANARY_TOKEN}"\n',
        encoding="utf-8",
    )

    previous = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(app_data)
    config_mod.reset_config_cache()
    try:
        conn = open_db()
        try:
            for item_id, kanji, reading in FIXTURE_WORDS:
                conn.execute(
                    "INSERT INTO item (id, kind, kanji, reading, created_ts) "
                    "VALUES (?, 'word', ?, ?, ?)",
                    (item_id, kanji, reading, f"2026-01-01{_TS}"),
                )
            events.mark_item(conn, KNOWN_WORD_ID, "known", note="bverify")
        finally:
            conn.close()

        # The token really is in the file the server will read. Asserted here so
        # that a canary-absent result later cannot be explained by a config that
        # never carried one.
        assert config_mod.get_config().obsidian_api_token == CANARY_TOKEN

        yield {
            "root": root,
            "app_data": app_data,
            "db_path": db_path,
            "vault": vault,
        }
    finally:
        config_mod.reset_config_cache()
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous


class _StdioClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over a pipe.

    Mirrored from ``test_averify.py`` rather than imported, so that this gate
    keeps working — and keeps meaning the same thing — if that file is retired.
    """

    def __init__(self, app_data: Path) -> None:
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(app_data)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "katagiri.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(REPO_ROOT),
        )
        self._next_id = 0
        self.stdout_lines: list[bytes] = []

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self.process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError(
                "the MCP server closed stdout before answering; stderr was:\n"
                + self._drain_stderr()
            )
        self.stdout_lines.append(line)
        return json.loads(line.decode("utf-8"))

    def call(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": {} if params is None else params,
            }
        )
        response = self._read()
        assert response["jsonrpc"] == "2.0", response
        assert response["id"] == self._next_id, response
        return response

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    @property
    def last_raw(self) -> str:
        """The most recent stdout frame, exactly as the server wrote it.

        Scanned rather than a re-serialised dict: the bytes on the wire are what a
        model would receive, and a round-trip through ``json.dumps`` could
        normalise away the very thing being looked for.
        """
        assert self.stdout_lines, "nothing has been read from stdout yet"
        return self.stdout_lines[-1].decode("utf-8")

    def _drain_stderr(self) -> str:
        assert self.process.stderr is not None
        return self.process.stderr.read().decode("utf-8", "replace")

    def close(self) -> str:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            self.process.kill()
            self.process.wait(timeout=15)
        stderr = self._drain_stderr()
        assert self.process.stdout is not None
        self.process.stdout.close()
        self.process.stderr.close()
        return stderr


def _tool_payload(response: dict[str, Any]) -> Any:
    """The structured result of a ``tools/call``, whichever field carries it."""
    assert "error" not in response, response
    result = response["result"]
    assert result.get("isError") is not True, result
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    blocks = [
        block["text"]
        for block in result.get("content", [])
        if block.get("type") == "text"
    ]
    assert blocks, f"no readable content in {result}"
    return json.loads(blocks[0])


@pytest.fixture(scope="module")
def mcp_client(cold):
    """One server subprocess, handshake completed, for every wire assertion."""
    client = _StdioClient(cold["app_data"])
    stderr_seen: list[str] = []
    try:
        initialized = client.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kata-bvf", "version": "1"},
            },
        )
        assert "error" not in initialized, initialized
        result = initialized["result"]
        assert result["serverInfo"]["name"] == "katagiri"
        assert result["protocolVersion"]
        assert "tools" in result["capabilities"]
        client.notify("notifications/initialized")
        yield client
    finally:
        stderr_seen.append(client.close())

    stderr = stderr_seen[0]
    # A-verify's invariant, restated: the startup line exists, and it is on
    # stderr — never on the stream the protocol owns.
    assert "starting katagiri" in stderr, stderr[-2000:]
    # And no traceback escaped into the log while Phase B's tools ran.
    assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]


def _obsidian_is_listening() -> bool:
    """Is something *already* accepting connections on the plugin's port?

    A client connect, never a bind. The scan above is about ``src/katagiri``: the
    *package* may not open a listening socket, because the whole boundary rests on
    stdio being the only way in. A test standing in for the third-party plugin is
    not the package, which is why :class:`_VaultStub` may bind 27123 while nothing
    shipped in the wheel may.

    Two callers, two uses. The failure-path test uses it to decide how strict the
    "Obsidian is not running" branch may be — the canary and shape assertions hold
    either way, but a developer with Obsidian genuinely open would otherwise see a
    spurious failure on a machine where the boundary is in fact intact. The stub
    fixture uses it to decide whether the port is free to stand in on at all.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((obsidian_proxy.OBSIDIAN_HOST,
                                obsidian_proxy.OBSIDIAN_PORT)) == 0


# ---------------------------------------------------------------------------
# 1. The cold handshake lists exactly the contract, vault tools included
# ---------------------------------------------------------------------------


def test_the_cold_handshake_lists_exactly_the_contract_tools(mcp_client):
    """The A6 contract plus Phase B's three, and nothing else.

    Equality, not containment, in both directions: a missing vault tool means
    Phase B did not land, and an extra tool means something reached the agent
    without passing through ``tool_registry``.
    """
    listed = mcp_client.call("tools/list")
    tools = listed["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert names == CONTRACT_TOOLS, "the tool contract is additive-only"
    for tool in VAULT_TOOLS:
        assert tool in names, f"Phase B's {tool} is not on the wire"

    # Every listed tool is a real, described, schema-carrying tool rather than a
    # name with nothing behind it.
    for tool in tools:
        assert tool.get("description"), tool["name"]
        assert isinstance(tool.get("inputSchema"), dict), tool["name"]


# ---------------------------------------------------------------------------
# 2. No write verb anywhere in the declared contract
# ---------------------------------------------------------------------------


def test_the_wire_contract_mentions_no_write_verb_at_all(mcp_client):
    """Scanned over the raw ``tools/list`` frame: names, descriptions, schemas.

    This is the structural half of "GET-only". The proxy module's own docstring
    explains that no function takes a method, a URL or a header — this asserts the
    consequence at the boundary the agent actually sees. If a write-shaped tool or
    argument is ever registered, it has to show up in this frame, and it fails
    here before it can be called.
    """
    listed = mcp_client.call("tools/list")
    raw = mcp_client.last_raw
    assert '"tools"' in raw, raw[:400]

    for label, pattern in WRITE_VERB_PATTERNS:
        match = pattern.search(raw)
        assert match is None, (
            f"the tool contract mentions {label} at offset {match.start()}: "
            f"...{raw[max(0, match.start() - 120):match.end() + 120]}..."
        )

    # The same scan, field by field, so a future frame that nests things
    # differently cannot slip a verb past the flat search above.
    for tool in listed["result"]["tools"]:
        blob = json.dumps(tool, ensure_ascii=False)
        for label, pattern in WRITE_VERB_PATTERNS:
            assert pattern.search(blob) is None, f"{tool['name']} mentions {label}"

    # And no tool takes an argument through which a request could be steered.
    for tool in listed["result"]["tools"]:
        properties = (tool.get("inputSchema") or {}).get("properties") or {}
        for argument in properties:
            assert argument.lower() not in {
                "method",
                "url",
                "uri",
                "headers",
                "header",
                "body",
                "data",
                "endpoint",
                "port",
                "host",
                "command",
            }, f"{tool['name']} takes a request-shaped argument: {argument}"


# ---------------------------------------------------------------------------
# 3. Stdio only: the package ships no HTTP server, and one HTTP client
# ---------------------------------------------------------------------------


def test_the_package_ships_no_http_server_construct_anywhere():
    """Walked with ``rglob``, not read from a list of files someone maintained.

    The premise of the whole token boundary is that stdio is the only way in. A
    listening socket anywhere in the package — a debug endpoint, an SSE transport,
    a "just for testing" ``HTTPServer`` — would mean the token is reachable by
    something other than the one process the operator launched.
    """
    offences: list[str] = []
    for path in package_sources():
        text = path.read_text(encoding="utf-8")
        for label, pattern in HTTP_SERVER_PATTERNS:
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                offences.append(f"{path.relative_to(REPO_ROOT)}:{line}: {label}")
    assert not offences, "HTTP server constructs found:\n" + "\n".join(offences)

    # The positive half of the same statement: the one transport there is, is
    # stdio, and it is named literally.
    server_source = (PACKAGE_ROOT / "mcp_server.py").read_text(encoding="utf-8")
    assert 'server.run(transport="stdio")' in server_source
    assert len(re.findall(r"\.run\(transport=", server_source)) == 1


def test_only_the_obsidian_proxy_is_an_http_client():
    """``urllib`` as a client is allowed in exactly one module — and it is a GET.

    ``urllib.parse.quote`` elsewhere is not a network call (three modules use it
    to build a SQLite ``file:`` URI), which is why the patterns name
    ``urllib.request`` and ``http.client`` specifically rather than ``urllib``, and
    why each is anchored on an import or a call rather than on the dotted name (see
    :data:`HTTP_CLIENT_PATTERNS`).

    The last block is what keeps that anchoring honest: the one module that really
    is a client has to match, or this test would be passing because the patterns
    stopped matching anything.
    """
    offenders: dict[str, list[str]] = {}
    for path in package_sources():
        if path.name in HTTP_CLIENT_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [label for label, pattern in HTTP_CLIENT_PATTERNS if pattern.search(text)]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, f"unexpected HTTP client usage: {offenders}"

    proxy = (PACKAGE_ROOT / "obsidian_proxy.py").read_text(encoding="utf-8")
    # Not a vacuous scan: the allowlisted module is a client, and says so in the
    # shapes the patterns look for. If this fails, the loop above proved nothing.
    assert "urllib.request" in [
        label for label, pattern in HTTP_CLIENT_PATTERNS if pattern.search(proxy)
    ], "the client patterns no longer match the one real HTTP client"

    # The allowed client is a GET, to loopback, with the method as a literal.
    assert 'method="GET"' in proxy
    assert re.search(r"method\s*=\s*[\"'](?!GET)", proxy) is None
    assert obsidian_proxy.OBSIDIAN_HOST == "127.0.0.1"
    assert obsidian_proxy.OBSIDIAN_SCHEME == "http"
    assert obsidian_proxy.BASE_URL == "http://127.0.0.1:27123"


# ---------------------------------------------------------------------------
# 4. The token never crosses the MCP boundary
# ---------------------------------------------------------------------------


def _assert_clean(payload: Any, raw: str, *, where: str) -> None:
    """No canary, and no traceback, in either the structure or the raw frame."""
    blob = json.dumps(payload, ensure_ascii=False)
    for haystack, what in ((blob, "the payload"), (raw, "the raw frame")):
        assert CANARY_TOKEN not in haystack, (
            f"the obsidian_api_token leaked into {what} of {where}"
        )
        # A partial leak is a leak: the canary's distinctive stem must not appear
        # in any form, truncated, split or re-cased.
        assert "SECRET-BVF" not in haystack, f"a token fragment reached {what} of {where}"
        assert "BVF-CANARY" not in haystack, f"a token fragment reached {what} of {where}"
    for marker in TRACEBACK_MARKERS:
        assert marker not in blob, f"{where} answered with a raw traceback"


def test_the_vault_tools_answer_without_ever_carrying_the_token(mcp_client):
    """Call all three vault tools over the wire with a canary token configured.

    Obsidian is not running under the fixture, so each call is the failure path —
    which is the interesting one: it is where a naive implementation echoes the
    request (and its ``Authorization`` header) into an error message. Each answer
    must be a structured dict naming what happened, with a declared error code,
    and no trace of the credential.
    """
    listening = _obsidian_is_listening()
    calls = {
        "vault_file": {"path": "Notes/Today.md"},
        "vault_list": {},
        "obsidian_active_note": {},
    }

    for name, arguments in calls.items():
        response = mcp_client.call(
            "tools/call", {"name": name, "arguments": arguments}
        )
        raw = mcp_client.last_raw
        payload = _tool_payload(response)
        _assert_clean(payload, raw, where=name)

        assert isinstance(payload, dict), payload
        assert set(payload) >= {"ok", "status", "error", "note"}, payload
        assert payload["error"] in KNOWN_PROXY_ERRORS, payload["error"]
        assert isinstance(payload["note"], str)

        if not listening:
            # Nothing is on :27123, so the only honest answers are "cannot reach
            # it" and "not configured" — and a token *is* configured, so it is the
            # first. A success here would mean the proxy invented an answer.
            assert payload["ok"] is False, payload
            assert payload["error"] in {
                obsidian_proxy.UNREACHABLE,
                obsidian_proxy.TIMED_OUT,
            }, payload
            assert payload["note"], "a failure with no explanation is not an answer"
            assert str(obsidian_proxy.OBSIDIAN_PORT) in payload["note"] or (
                obsidian_proxy.BASE_URL in payload["note"]
            ), payload["note"]

    # The content-shaped answers must still flag their data as untrusted even
    # when there is no data: the flag is a property of the channel, not of a body.
    for name in ("vault_file", "obsidian_active_note"):
        payload = _tool_payload(
            mcp_client.call(
                "tools/call",
                {
                    "name": name,
                    "arguments": {"path": "Notes/Today.md"} if name == "vault_file" else {},
                },
            )
        )
        assert payload["untrusted"] is True, payload


def test_the_status_tools_do_not_leak_the_token_either(mcp_client):
    """``security_status`` and ``stop_gate_status``, the two that describe state.

    ``security_status`` is the one with a real reason to mention :27123 at all —
    it checks that port's binding — so it is exactly where a configuration dump
    would be most excusable and most damaging. It may name the port; it may not
    name the key.
    """
    security = _tool_payload(
        mcp_client.call("tools/call", {"name": "security_status", "arguments": {}})
    )
    _assert_clean(security, mcp_client.last_raw, where="security_status")
    assert security["changed_anything"] is False
    assert str(obsidian_proxy.OBSIDIAN_PORT) in {
        str(port) for port in security["checked_ports"]
    }

    gate = _tool_payload(
        mcp_client.call("tools/call", {"name": "stop_gate_status", "arguments": {}})
    )
    _assert_clean(gate, mcp_client.last_raw, where="stop_gate_status")
    assert gate["pass"] is False, "one seeded artifact day is not 14 study days"

    # Cumulative with A-verify: every frame this file has collected so far is a
    # protocol frame, and none of them carries the credential.
    assert mcp_client.stdout_lines
    for line in mcp_client.stdout_lines:
        text = line.decode("utf-8")
        assert json.loads(text).get("jsonrpc") == "2.0", text[:200]
        assert CANARY_TOKEN not in text


# ---------------------------------------------------------------------------
# 4b. A read that succeeds: the stub sees the token, the agent never does
# ---------------------------------------------------------------------------

#: What the stub serves, under which names. Two files because B-verify is
#: specified as "reads Today.md and an arbitrary note" — and the arbitrary one is
#: nested, spaced and non-ASCII on purpose, so the read that succeeds is not only
#: the easiest possible one. Neither body contains the canary: the stub must never
#: be the reason a leak assertion passes or fails.
STUB_TODAY_PATH = "Today.md"
STUB_ARBITRARY_PATH = "Notes/雑記 note.md"
STUB_FILES: dict[str, str] = {
    STUB_TODAY_PATH: "# Today\n\nA stub Today page, served over loopback.\n",
    STUB_ARBITRARY_PATH: "# 雑記\n\n食べる appears on this page. Nothing else.\n",
}

#: What the stub answers a request with no usable credential. The plugin's own
#: code is 401, and the point of mirroring it is that the refusal in the test below
#: is the *server's*, decided from the header it received, rather than something
#: this file arranged.
STUB_UNAUTHORIZED_BODY = b'{"errorCode": 40100, "message": "unauthorized"}'


class _VaultStub(HTTPServer):
    """A loopback stand-in for obsidian-local-rest-api on the product's own port.

    Bound to :data:`obsidian_proxy.OBSIDIAN_PORT` rather than to an ephemeral one,
    because the proxy has no port override — scheme, host and port are module
    constants, and that is one of the properties this file verifies. The cost is
    that the port must be free; the fixture skips when it is not.

    ``allow_reuse_address`` is switched off deliberately. On Windows the default
    ``SO_REUSEADDR`` lets a second socket bind an address another process is
    already using, which would let this stub come up *beside* a real Obsidian and
    split traffic with it — a test that half-works and reports whichever half it
    got. Failing to bind is the answer that can be acted on.
    """

    allow_reuse_address = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        #: One entry per arriving request: method, path, peer, Authorization.
        self.requests: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)

    @property
    def seen(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.requests)


class _VaultStubHandler(BaseHTTPRequestHandler):
    """GET-only routing for the three paths the proxy knows how to ask for.

    Every arriving request is recorded *before* it is authorised, so an
    unauthenticated attempt is evidence too. Authorisation is a plain equality
    check against the canary: this stub is not modelling the plugin's security, it
    is answering one question — did the credential arrive, and from where.
    """

    server: _VaultStub  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence. The default writes every request line to the real stderr."""

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        authorization = self.headers.get("Authorization")
        self.server.record(
            {
                "method": self.command,
                "path": self.path,
                "peer": self.client_address[0],
                "authorization": authorization,
            }
        )
        if authorization != f"Bearer {CANARY_TOKEN}":
            self._respond(401, STUB_UNAUTHORIZED_BODY, "application/json")
            return

        # Unquoted once, as a real server does: the proxy percent-encodes the
        # vault path on the way out.
        path = urllib.parse.unquote(self.path)
        if path == "/active/":
            body = STUB_FILES[STUB_TODAY_PATH].encode("utf-8")
            self._respond(200, body, "text/markdown")
            return
        if path == "/vault/":
            listing = json.dumps({"files": sorted(STUB_FILES)}, ensure_ascii=False)
            self._respond(200, listing.encode("utf-8"), "application/json")
            return
        relative = path[len("/vault/"):] if path.startswith("/vault/") else None
        if relative in STUB_FILES:
            self._respond(
                200, STUB_FILES[relative].encode("utf-8"), "text/markdown"
            )
            return
        self._respond(404, b'{"errorCode": 40400}', "application/json")


@pytest.fixture(scope="module")
def vault_stub():
    """The stub, serving on 27123 in a background thread, or a skip saying why.

    The skip is not a hidden pass: the port being occupied is the one condition
    under which this stub cannot exist, and it is reported with the reason rather
    than as a green test. Everything else in this file — the contract scan, the
    transport scan, the failure-path canary checks — runs either way.
    """
    if _obsidian_is_listening():
        pytest.skip(
            f"{obsidian_proxy.BASE_URL} is already in use, most likely a real "
            "Obsidian with the Local REST API plugin enabled. The proxy has no "
            "port override (host and port are module constants, which is itself "
            "one of the properties this file verifies), so the stub cannot move "
            "to another port. Close Obsidian, or disable the plugin, to run the "
            "successful-read and direct-bypass checks."
        )
    try:
        server = _VaultStub(
            (obsidian_proxy.OBSIDIAN_HOST, obsidian_proxy.OBSIDIAN_PORT),
            _VaultStubHandler,
        )
    except OSError as exc:  # pragma: no cover - lost a race for the port
        pytest.skip(
            f"could not bind {obsidian_proxy.BASE_URL} for the vault stub "
            f"({type(exc).__name__}); something claimed the port between the "
            "probe and the bind."
        )
    thread = threading.Thread(
        target=server.serve_forever, name="bvf-vault-stub", daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _direct_get(
    url_path: str, *, authorization: str | None = None
) -> tuple[int, bytes]:
    """One GET straight at :27123, the way an agent would have to make it.

    Built with ``urllib`` *here in the test*, deliberately: the package is asserted
    to hold exactly one HTTP client, and the question this answers is what happens
    to a request that does not go through it. The proxy handler is emptied for the
    same reason it is in the product — an environment proxy must not be able to see
    this request either, or the test would be measuring a proxy's behaviour.
    """
    request = urllib.request.Request(
        obsidian_proxy.BASE_URL + url_path, method="GET"
    )
    if authorization is not None:
        request.add_header("Authorization", authorization)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        exc.close()
        return int(exc.code), body


def test_a_successful_vault_read_carries_the_token_only_toward_loopback(
    mcp_client, vault_stub
):
    """The success path, which is the one that actually carries the credential.

    B-verify's scenario is "reads Today.md and an arbitrary note", and until this
    test existed the gate had only ever seen the unreachable branch — where no
    request is made, so no header exists to leak. Here every read returns real
    content, and the credential is checked in both directions at once: present on
    the ``Authorization`` header of a GET the stub received from ``127.0.0.1``, and
    absent from every byte the MCP client was handed.

    The listing and the active note are called too, so the whole read surface is
    exercised warm rather than only the one tool the scenario names.
    """
    for path in (STUB_TODAY_PATH, STUB_ARBITRARY_PATH):
        response = mcp_client.call(
            "tools/call", {"name": "vault_file", "arguments": {"path": path}}
        )
        raw = mcp_client.last_raw
        payload = _tool_payload(response)
        _assert_clean(payload, raw, where=f"vault_file({path})")

        assert payload["ok"] is True, payload
        assert payload["status"] == 200, payload
        assert payload["error"] is None, payload
        assert payload["content"] == STUB_FILES[path], payload["content"]
        assert payload["truncated"] is False, payload
        # A real read still flags its body as data the caller must not obey.
        assert payload["untrusted"] is True, payload

    listing = _tool_payload(
        mcp_client.call("tools/call", {"name": "vault_list", "arguments": {}})
    )
    _assert_clean(listing, mcp_client.last_raw, where="vault_list (warm)")
    assert listing["ok"] is True, listing
    assert set(listing["files"]) == set(STUB_FILES), listing

    active = _tool_payload(
        mcp_client.call(
            "tools/call", {"name": "obsidian_active_note", "arguments": {}}
        )
    )
    _assert_clean(active, mcp_client.last_raw, where="obsidian_active_note (warm)")
    assert active["ok"] is True, active
    assert active["content"] == STUB_FILES[STUB_TODAY_PATH], active
    assert active["untrusted"] is True, active

    # --- what the stub saw ---------------------------------------------------
    seen = vault_stub.seen
    assert len(seen) >= 4, f"the stub was reached {len(seen)} time(s)"
    assert {entry["method"] for entry in seen} == {"GET"}, (
        "the proxy sent a method other than GET"
    )
    # Loopback, and only loopback: the stub is bound to 127.0.0.1, so a request it
    # accepted cannot have come from anywhere else — the peer address is asserted
    # rather than assumed so that a future stub bound more widely fails here.
    assert {entry["peer"] for entry in seen} == {obsidian_proxy.OBSIDIAN_HOST}
    assert all(
        entry["authorization"] == f"Bearer {CANARY_TOKEN}" for entry in seen
    ), "the credential did not reach the plugin, so these reads proved nothing"


def test_no_replay_of_what_the_agent_received_can_authenticate_directly(
    mcp_client, vault_stub
):
    """The bypass, attempted for real against the same stub, and refused.

    Structure alone ("there is no write verb, so there is no path") says nothing
    about what an agent could do with a credential it read somewhere. So: drive the
    whole read surface, collect every byte the client has ever received, and check
    the canary is in none of it — that is the premise. Then make the request an
    agent would have to make, without it, and read the stub's answer: 401.

    The third attempt is the control, and it is what makes the other two mean
    something. The same URL with the real token answers 200, so the refusals are
    about the credential rather than about a wrong path, a wrong method, or a stub
    that refuses everything.
    """
    for name, arguments in (
        ("vault_file", {"path": STUB_TODAY_PATH}),
        ("vault_list", {}),
        ("obsidian_active_note", {}),
    ):
        payload = _tool_payload(
            mcp_client.call("tools/call", {"name": name, "arguments": arguments})
        )
        _assert_clean(payload, mcp_client.last_raw, where=f"{name} (bypass setup)")
        assert payload["ok"] is True, payload

    # Everything the agent has ever been handed on this connection, in one blob.
    everything = b"".join(mcp_client.stdout_lines).decode("utf-8")
    assert CANARY_TOKEN not in everything, "the credential crossed the boundary"
    assert "SECRET-BVF" not in everything and "BVF-CANARY" not in everything
    assert "Bearer" not in everything, (
        "not even the header's shape reached the agent, so there is nothing to copy"
    )

    url_path = "/vault/" + urllib.parse.quote(STUB_TODAY_PATH, safe="/")
    before = len(vault_stub.seen)

    # 1. No credential at all — everything an agent holding only the above has.
    unauthenticated, _ = _direct_get(url_path)
    assert unauthenticated == 401, (
        "the plugin served a vault file to a request carrying no credential"
    )
    # 2. A guessed one. Reversed rather than random so the assertion cannot pass
    #    because the guess was implausibly short.
    guessed, _ = _direct_get(url_path, authorization=f"Bearer {CANARY_TOKEN[::-1]}")
    assert guessed == 401

    # 3. The control: the same request, with the token Katagiri holds, works.
    authorised, body = _direct_get(
        url_path, authorization=f"Bearer {CANARY_TOKEN}"
    )
    assert authorised == 200, (
        "the control failed, so the 401s above are not evidence about credentials"
    )
    assert body.decode("utf-8") == STUB_FILES[STUB_TODAY_PATH]

    # All three really arrived, and were judged on the header they carried.
    attempts = vault_stub.seen[before:]
    assert [entry["authorization"] for entry in attempts] == [
        None,
        f"Bearer {CANARY_TOKEN[::-1]}",
        f"Bearer {CANARY_TOKEN}",
    ], attempts


# ---------------------------------------------------------------------------
# 5. Today.md: cold render, clean overwrite, refused clobber
# ---------------------------------------------------------------------------


def _run_export(app_data: Path, vault: Path) -> subprocess.CompletedProcess[str]:
    """``python -m katagiri.today_export --vault <vault>``, as a real process."""
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(app_data)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "katagiri.today_export", "--vault", str(vault)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(REPO_ROOT),
        timeout=180,
    )


def test_today_export_renders_overwrites_and_refuses_a_handwritten_page(
    cold, tmp_path
):
    """The exporter's three behaviours, in the order that makes them provable.

    A first cold run must produce the page with its generated header; a second
    must overwrite it without complaint; and a hand-made headerless ``Today.md``
    must survive untouched. The last one is the whole point of the header — it is
    what stops a derived-file writer from eating a note the learner wrote — so it
    is asserted on file *content*, not just on an exit code.

    Its own vault (``tmp_path``) rather than the fixture's, so the refusal branch
    cannot leave a poisoned ``Today.md`` behind for anything else to trip over.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / today_export.DERIVED_DIR_NAME / today_export.TODAY_FILENAME

    # --- first, cold run -----------------------------------------------------
    first = _run_export(cold["app_data"], vault)
    assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr[-2000:]!r}"
    assert "wrote" in first.stdout
    assert target.is_file(), f"{target} was not written"

    raw = target.read_bytes()
    head = raw[:1024].decode("utf-8", "replace")
    assert head.startswith("---"), head[:120]
    frontmatter = head.split("---")[1]
    assert "generated: true" in frontmatter, frontmatter
    assert f"generator: {today_export.GENERATOR}" in frontmatter
    assert "type: derived" in frontmatter
    # The rule the refusal below depends on, read back through the product's own
    # predicate rather than through this file's idea of what frontmatter is.
    assert today_export.is_generated_note(raw.decode("utf-8")) is True
    assert "# Today" in raw.decode("utf-8")
    # Written where it is confined to, and nowhere else in the vault.
    assert sorted(p.name for p in vault.iterdir()) == [today_export.DERIVED_DIR_NAME]

    # The page is derived, not a source of truth, and says so where a human will
    # see it before editing.
    assert "rewritten every time" in raw.decode("utf-8")

    # --- second run: a clean overwrite ---------------------------------------
    second = _run_export(cold["app_data"], vault)
    assert second.returncode == 0, second.stderr[-2000:]
    rewritten = target.read_text(encoding="utf-8")
    assert today_export.is_generated_note(rewritten) is True
    assert rewritten.startswith("---")
    # No half-written scratch file survived the atomic replace.
    litter = [
        path.name
        for path in (vault / today_export.DERIVED_DIR_NAME).iterdir()
        if path.name != today_export.TODAY_FILENAME
    ]
    assert litter == [], f"the atomic write left litter behind: {litter}"

    # Both runs are in the append-only log, and neither logged any content.
    conn = open_db(cold["db_path"])
    try:
        rows = conn.execute(
            "SELECT payload FROM event WHERE type = ? ORDER BY id",
            (today_export.TODAY_EVENT_TYPE,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2, "one event per successful export"
    for row in rows:
        payload = json.loads(row["payload"])
        assert payload["path"] == today_export.TODAY_FILENAME
        assert payload["sections"] == len(today_export.SECTIONS)
        assert CANARY_TOKEN not in row["payload"]

    # --- a hand-made page is left alone --------------------------------------
    handwritten = "# My own Today\n\nI wrote this by hand. Do not clobber it.\n"
    target.write_text(handwritten, encoding="utf-8")
    assert today_export.is_generated_note(handwritten) is False

    refused = _run_export(cold["app_data"], vault)
    assert refused.returncode != 0, (
        "a headerless Today.md was overwritten; the generated-header guard is not "
        "holding"
    )
    assert "error:" in refused.stdout.lower() or "error" in refused.stderr.lower()
    assert target.read_text(encoding="utf-8") == handwritten, (
        "the hand-written page was modified"
    )
    # The refusal itself must not have left a scratch file in .derived.
    assert sorted(
        path.name for path in (vault / today_export.DERIVED_DIR_NAME).iterdir()
    ) == [today_export.TODAY_FILENAME]


# ---------------------------------------------------------------------------
# 6. The Obsidian port is on the hardening list
# ---------------------------------------------------------------------------


def test_the_obsidian_port_is_in_the_hardened_port_list():
    """27123 is checked by ``security_status``, and it is the port the proxy uses.

    Both halves asserted together on purpose: a hardening list that names a port
    the proxy no longer talks to, or a proxy that talks to a port the hardening
    list does not name, would each read as "checked" while checking nothing.
    """
    assert 27123 in mcp_server.HARDENED_PORTS
    assert obsidian_proxy.OBSIDIAN_PORT in mcp_server.HARDENED_PORTS
    assert obsidian_proxy.OBSIDIAN_PORT == 27123
    assert str(obsidian_proxy.OBSIDIAN_PORT) in mcp_server.FIREWALL_COMMAND
