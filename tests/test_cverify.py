r"""kata-cvf: the Phase C gate, verified end to end over the real stdio surface.

Third in the chain after ``tests/test_averify.py`` and ``tests/test_bverify.py``,
and read the same way: a *cold* verification harness, not a unit-test suite.
Nothing here mocks a Katagiri module. Every assertion is made either against a
genuine ``katagiri.mcp_server`` subprocess spoken to over JSON-RPC on its
stdin/stdout, against a genuine ``python -m katagiri.md_search`` subprocess, or
against the package's own source files on disk.

**Cumulative, then new.** A-verify proved the Phase A pipeline's arithmetic;
B-verify re-asserted A's protocol invariants and added the vault proxy's token
boundary. C-verify re-asserts the parts of both that Phase C could plausibly
break — the handshake completes, the tool list is *exactly* the contract, no
write verb appears anywhere in it, the package still ships no HTTP server and
exactly one HTTP client, the Obsidian token still never crosses the MCP
boundary, stdout carries protocol frames only and the startup line lands on
stderr — and then adds the five outcomes Phase C is answerable for
(``specs/002-phase-c-prose-search/quickstart.md``:21–27):

1. the same fixture question answered via ``search_db`` (state view) **and** via
   markdown search (prose view);
2. the markdown path succeeding with Obsidian fully closed — no dependency on
   :27123 at all;
3. editing one note re-indexing exactly that file, asserted on the report a cold
   ``rebuild`` process printed, and a deleted note leaving no ghost hits;
4. frontmatter queryable apart from the body, and malformed frontmatter
   non-fatal — both over the wire rather than in-process;
5. scenarios A..B still green.

**The load-bearing claim of this file is that the two search paths are two views
of one question, and that the prose view needs nothing but the disk.** "Obsidian
closed" is not asserted as a docstring or an intention. It is checked three ways:

* ``md_search.py`` imports no HTTP client and names no port — scanned as source,
  so a future refactor that "helpfully" reads the vault through the REST bridge
  fails here rather than at a learner's desk with Obsidian shut;
* the prose tool answers the fixture question over the wire in a session where
  the vault tools are simultaneously answering ``unreachable`` — same server,
  same call sequence, one path working and the other honestly failing;
* the shared question (:data:`SHARED_QUESTION`) comes back from *both* paths with
  the *same* routing decision, which is only true because ``search_notes`` and
  ``search_db_query`` route on the same rule rather than by coincidence.

The fixture vault is ``tests/fixtures/vault/`` — the same frozen six-file corpus
``tests/test_md_search.py`` owns — copied into a throwaway directory, because
section 7 mutates it on purpose. The unique terms it relies on
(``thunderstruck``, ``ghosthunter``, ``窓の近く``, ``幽霊``) are mirrored below
rather than imported from that module, matching the gate convention used for
:data:`CONTRACT_TOOLS`: a gate that asks another test file what the fixture
contains cannot notice the fixture changing underneath it.

Unlike A- and B-verify this file **does** need vendor data: the prose index is
built from fugashi-segmented shadow text, so the vendored UniDic must be present.
The module skips with that reason rather than failing, exactly as
``test_md_search.py`` does.

Section 7 runs last on purpose and is the only part that changes the fixture
vault: it edits one note and deletes another, in that order, and the closing test
re-asks the shared question afterwards so a mutation cannot quietly invalidate
the premise of section 4.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from katagiri import events, md_search, mcp_server, obsidian_proxy
from katagiri import config as config_mod
from katagiri import tokenizer as tok
from katagiri.db import open_db

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "src" / "katagiri"

fugashi = pytest.importorskip("fugashi")


def _dicdir_available() -> bool:
    try:
        tok.dicdir_path()
    except tok.TokenizerError:
        return False
    return True


#: The prose index is a function of the tokenizer, so this whole gate needs the
#: vendored dictionary. Skipped with the reason rather than failed — mirrored
#: from ``test_md_search.py`` rather than imported, like everything else here.
pytestmark = pytest.mark.skipif(
    not _dicdir_available(),
    reason=(
        "vendored UniDic 3.1.0 is absent (vendor/unidic/unidic); see "
        "vendor/README.md"
    ),
)


# ---------------------------------------------------------------------------
# 0. Constants
# ---------------------------------------------------------------------------

#: The value written into ``config.toml`` as ``obsidian_api_token``. Distinctive
#: on purpose: it is searched for, byte for byte, in every response this file
#: collects, so a substring of it appearing anywhere is a leak and nothing else.
CANARY_TOKEN = "SECRET-CVF-CANARY"

PROTOCOL_VERSION = "2026-07-28"

#: The contract as it stood at the end of Phase B. Mirrored rather than imported
#: from ``katagiri.tool_registry`` on purpose — a gate that asks the product what
#: its contract is cannot notice the contract changing — and mirrored from
#: ``test_bverify.py`` rather than imported from it for the same reason one level
#: up: this file must fail on its own if a tool is added, removed or renamed.
CONTRACT_TOOLS_THROUGH_B = frozenset(
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

#: What a leaked traceback looks like on the wire.
TRACEBACK_MARKERS = ("Traceback (most recent call last)", 'File "', "  File \"")

_TS = "T00:00:00Z"

#: Seeded so ``search_db`` has *state* to answer the shared question from. The
#: first is the shared question itself and is the one marked known, which also
#: makes today an artifact day.
FIXTURE_WORDS: tuple[tuple[str, str, str], ...] = (
    ("w-cvf-benkyou", "勉強", "べんきょう"),
    ("w-cvf-tango", "単語", "たんご"),
)
KNOWN_WORD_ID = FIXTURE_WORDS[0][0]

# --- the frozen fixture vault, mirrored -----------------------------------

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"

#: Five hand-written notes plus one generated file under ``.derived/``.
MARKDOWN_FILES = 6

#: The question this gate is built around. Two characters, so it is also the
#: query length that proves both paths route the same way: FTS5's trigram
#: tokenizer indexes 3-character windows and matches *nothing* below that, so a
#: path that failed to route 勉強 to its word index would answer "no hits"
#: silently — on either side.
SHARED_QUESTION = "勉強"

#: The same sentence appears in the Japanese note, the mixed note and the
#: generated dashboard, so a long query is a real multi-note answer.
LONG_JP = "毎日日本語を勉強しています"

#: Unique to exactly one fixture file each, which is what makes an assertion
#: about them provable rather than suggestive.
BODY_ONLY_MALFORMED = "thunderstruck"
ONLY_IN_JP_NOTE = "窓の近く"
GHOST_EN = "ghosthunter"
GHOST_JP = "幽霊"

#: ``conditional`` is a tag on 01 *and* prose in 01 and 03 — one word, two
#: different questions, which is the whole point of frontmatter being separable.
TAG_AND_PROSE_TERM = "conditional"

JAPANESE_NOTE = "02-japanese-prose.md"
MIXED_NOTE = "03-mixed-en-jp.md"
GRAMMAR_NOTE = "01-grammar-conditionals.md"
MALFORMED_NOTE = "04-malformed-frontmatter.md"
GHOST_NOTE = "05-scratch-ghost.md"
DERIVED_NOTE = "today.md"

#: Written into the edited note in section 7. ASCII and ≥3 characters so it
#: routes through trigram, and absent from every fixture file, so finding it
#: proves the edit was indexed rather than that something else matched.
EDIT_BEACON = "cverifybeacon"


# ---------------------------------------------------------------------------
# PHASE2: confirm against final adapter
# ---------------------------------------------------------------------------
#
# TG-C3 (tasks.md T008) registers the prose tool while this file is being
# drafted, and tasks.md says its name is "finalized here" (``e.g. search_notes``).
# Everything this file assumes about that adapter is therefore funnelled through
# the five seams below, so phase 2 is a review of one block rather than a diff
# across twenty assertions. Each seam names what has to be re-checked once the
# adapter has landed:
#
#   1. PROSE_TOOL / PHASE_C_TOOLS — the tool's final name, and whether Phase C
#      registered one tool or several (a ``*_reindex`` companion would belong in
#      PHASE_C_TOOLS too, and would change CONTRACT_TOOLS).
#   2. _prose_arguments — the adapter's argument *names* and shapes. The
#      underlying function is
#      ``md_search.search_notes(conn, query, *, tags, fields, path_prefix,
#      include_generated, limit)``; a thin adapter is expected to mirror it, but
#      ``fields`` is a mapping and an MCP input schema may well flatten or rename
#      it. Only this function may know.
#   3. _prose_hits / _prose_names / _prose_route — the envelope's key names. The
#      expected envelope is the function's own return value passed through:
#      ``{query, limit, route, route_reason, filters, hits, hit_count,
#      indexed_notes, index_empty, note}`` with each hit
#      ``{path, title, generated, frontmatter, frontmatter_ok, excerpt,
#      source_index}``.
#   4. _reindex — section 7 drives ``python -m katagiri.md_search rebuild``
#      rather than a tool, because no re-index tool is known to be registered. If
#      T008 added one, this is where it would move, and the "cold subagent"
#      property would be preserved either way.
#   5. The re-index *log* assertion. ``md_search.main`` prints the report to
#      stderr but never calls ``logging_setup.setup_logging``, so the INFO line
#      ``md index incremental: ... indexed=1 ...`` that ``MdIndexResult``'s
#      docstring calls the other half of the SC-003 evidence is dropped by the
#      logging machinery in a CLI run. The rendered report *is* on stderr and is
#      asserted strictly below; :func:`_assert_log_line` holds the stricter
#      assertion for the day the CLI configures logging.

#: The Phase C addition(s), by name.
PROSE_TOOL = "search_notes"
PHASE_C_TOOLS: tuple[str, ...] = (PROSE_TOOL,)

#: The whole contract as of Phase C: additive-only, and asserted as equality.
CONTRACT_TOOLS = CONTRACT_TOOLS_THROUGH_B | frozenset(PHASE_C_TOOLS)


def _prose_arguments(
    query: str | None = None,
    *,
    tags: list[str] | None = None,
    fields: dict[str, str] | None = None,
    path_prefix: str | None = None,
    include_generated: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """The ``arguments`` object for one call to the prose tool.

    PHASE2 seam 2. Keys are omitted when unset rather than sent as ``null``, so
    the adapter's own defaults are what the wire exercises — ``include_generated``
    defaulting to false is a Phase C behaviour this file asserts, and it can only
    be asserted if the call does not set it.
    """
    arguments: dict[str, Any] = {}
    if query is not None:
        arguments["query"] = query
    if tags is not None:
        arguments["tags"] = list(tags)
    if fields is not None:
        arguments["fields"] = dict(fields)
    if path_prefix is not None:
        arguments["path_prefix"] = path_prefix
    if include_generated is not None:
        arguments["include_generated"] = include_generated
    if limit is not None:
        arguments["limit"] = limit
    return arguments


def _prose_hits(payload: Any) -> list[dict[str, Any]]:
    """The hit list out of a prose envelope. PHASE2 seam 3."""
    assert isinstance(payload, dict), payload
    hits = payload["hits"]
    assert isinstance(hits, list), payload
    return hits


def _prose_names(payload: Any) -> set[str]:
    """File names of the hits.

    Names rather than the vault-relative paths the module returns: every fixture
    file has a distinct basename, so this is unambiguous, and it keeps the
    ``.derived/`` prefix out of assertions that are not about generated files.
    """
    return {PurePosixPath(hit["path"]).name for hit in _prose_hits(payload)}


def _prose_route(payload: Any) -> str | None:
    """Which index answered. PHASE2 seam 3."""
    assert isinstance(payload, dict), payload
    return payload["route"]


# ---------------------------------------------------------------------------
# 1. The cold environment: a temporary %LOCALAPPDATA%, a vault, a built index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cold(tmp_path_factory) -> dict[str, Any]:
    """A migrated database, a copy of the fixture vault, and a built prose index.

    Module-scoped so one MCP subprocess can serve every wire assertion, and so
    section 7's mutations are seen by the same server the earlier sections used —
    which is the point of an incremental re-index scenario.

    The index is built here, in-process, through the product's own
    :func:`md_search.rebuild_md_index`. Section 7 then re-runs it as a *cold
    process*, and the first thing that run asserts is that it finds nothing to do
    — which is only true if a separate process agrees, stamp for stamp, with what
    this fixture wrote.

    The seeding connection is closed before anything is spawned: the server opens
    the same file itself, and a gate should not depend on two connections sharing
    a WAL.
    """
    root = tmp_path_factory.mktemp("cverify")
    app_data = root / "AppData"
    (app_data / "Katagiri").mkdir(parents=True)
    db_path = root / "katagiri.db"
    vault = root / "vault"
    shutil.copytree(FIXTURE_VAULT, vault)
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
            events.mark_item(conn, KNOWN_WORD_ID, "known", note="cverify")
            # Not optional: a rebuild stamps every row it writes and refuses to
            # run unstamped, because shadow_text is a function of the tokenizer.
            tok.stamp_versions(conn)
            report = md_search.rebuild_md_index(conn, root=vault)
        finally:
            conn.close()

        # The corpus really is the frozen six-file fixture, so a later "no hits"
        # cannot be explained by an index that was never built.
        assert report.files_indexed == MARKDOWN_FILES, report.render()
        assert report.files_failed == 0, report.render()
        assert report.frontmatter_errors == 1, "the malformed note, and only it"
        assert report.generated_files == 1, "`.derived/today.md`, and only it"

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

    Mirrored from ``test_bverify.py`` (which mirrored it from ``test_averify.py``)
    rather than imported, so that this gate keeps working — and keeps meaning the
    same thing — if either of those files is retired.
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
                "clientInfo": {"name": "kata-cvf", "version": "1"},
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
    # A-verify's invariant, restated a phase later: the startup line exists, and
    # it is on stderr — never on the stream the protocol owns.
    assert "starting katagiri" in stderr, stderr[-2000:]
    # And no traceback escaped into the log while Phase C's tool ran. The prose
    # path touches the filesystem and FTS5, so this is not a formality here.
    assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]


def _prose_search(mcp_client: _StdioClient, query: str | None = None, **filters: Any):
    """One prose call over the wire, returning its envelope.

    Every prose assertion in this file goes through here, so PHASE2 seams 2 and 3
    are the only places the adapter's shape is known.
    """
    response = mcp_client.call(
        "tools/call",
        {"name": PROSE_TOOL, "arguments": _prose_arguments(query, **filters)},
    )
    return _tool_payload(response)


def _obsidian_is_listening() -> bool:
    """Is something *already* accepting connections on the plugin's port?

    A client connect, never a bind. Mirrored from ``test_bverify.py``, where it
    decides how strict the "Obsidian is not running" branch may be, and used here
    for the mirror-image question: Phase C's promise is that the *prose* path does
    not care either way, so the prose assertions run unconditionally, and only the
    contrast test — prose answering while the vault tools cannot reach :27123 —
    needs the port to actually be closed. On a developer's machine with Obsidian
    genuinely open that test would otherwise fail for a reason that says nothing
    about Katagiri, so it skips, loudly, naming why.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(
            (obsidian_proxy.OBSIDIAN_HOST, obsidian_proxy.OBSIDIAN_PORT)
        ) == 0


# ---------------------------------------------------------------------------
# 2. Source scanning (mirrored from B-verify, extended to Phase C's module)
# ---------------------------------------------------------------------------

#: Word-boundary anchored on purpose. A naive substring scan for "PUT" matches
#: ``outputSchema`` and ``inputSchema`` — both of which are in every MCP frame —
#: so it would either fail always or be relaxed into meaninglessness.
WRITE_VERB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PUT", re.compile(r"\bput\b", re.IGNORECASE)),
    ("PATCH", re.compile(r"\bpatch\b", re.IGNORECASE)),
    ("DELETE", re.compile(r"\bdelete\b", re.IGNORECASE)),
    ("command_execute", re.compile(r"command[_\-\s]*execute", re.IGNORECASE)),
)

#: HTTP *server* constructs. Any one of these inside ``src/katagiri`` would mean
#: the process can be reached over a socket, which is the premise the token
#: boundary rests on.
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

#: HTTP *client* constructs, anchored on the two shapes a client actually takes —
#: an import at the start of a line, or an attribute *call* — so that a module
#: which merely writes prose about ``http.client`` is not read as one.
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
        path for path in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in path.parts
    )
    assert found, f"no python sources found under {PACKAGE_ROOT}"
    return found


def _assert_clean(payload: Any, raw: str, *, where: str) -> None:
    """No canary, and no traceback, in either the structure or the raw frame."""
    blob = json.dumps(payload, ensure_ascii=False)
    for haystack, what in ((blob, "the payload"), (raw, "the raw frame")):
        assert CANARY_TOKEN not in haystack, (
            f"the obsidian_api_token leaked into {what} of {where}"
        )
        # A partial leak is a leak: the canary's distinctive stem must not appear
        # in any form, truncated, split or re-cased.
        assert "SECRET-CVF" not in haystack, f"a token fragment reached {what} of {where}"
        assert "CVF-CANARY" not in haystack, f"a token fragment reached {what} of {where}"
    for marker in TRACEBACK_MARKERS:
        assert marker not in blob, f"{where} answered with a raw traceback"


# ---------------------------------------------------------------------------
# 3. Cumulative: the A- and B-verify invariants, re-asserted at C
# ---------------------------------------------------------------------------


def test_the_cold_handshake_lists_exactly_the_phase_c_contract(mcp_client):
    """The eleven tools Phase B ended with, plus Phase C's, and nothing else.

    Equality, not containment, in both directions: a missing prose tool means
    Phase C did not land on the wire, and an extra tool means something reached
    the agent without passing through ``tool_registry``.
    """
    listed = mcp_client.call("tools/list")
    tools = listed["result"]["tools"]
    names = {tool["name"] for tool in tools}

    assert names == CONTRACT_TOOLS, "the tool contract is additive-only"
    assert CONTRACT_TOOLS_THROUGH_B <= names, "a Phase B tool went missing at C"
    for tool in PHASE_C_TOOLS:
        assert tool in names, f"Phase C's {tool} is not on the wire"

    # Every listed tool is a real, described, schema-carrying tool rather than a
    # name with nothing behind it.
    for tool in tools:
        assert tool.get("description"), tool["name"]
        assert isinstance(tool.get("inputSchema"), dict), tool["name"]


def test_the_wire_contract_still_mentions_no_write_verb_at_all(mcp_client):
    """B-verify's structural half of "GET-only", re-run with Phase C registered.

    Phase C is the first phase whose module *does* delete rows — the incremental
    indexer removes vanished notes — so the risk this re-run addresses is real: a
    tool description copied out of ``md_search``'s docstrings would drag the word
    into the agent-facing contract, where it is exactly the shape a prompt-injected
    model would aim at. The index's internals may say "delete"; the contract the
    agent reads may not.
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


def test_the_package_still_ships_no_http_server_construct_anywhere():
    """Walked with ``rglob``, so Phase C's new module is scanned by construction.

    The premise of the whole token boundary is that stdio is the only way in. A
    listening socket anywhere in the package would mean the token is reachable by
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


def test_the_obsidian_proxy_is_still_the_only_http_client():
    """One module may speak HTTP, and Phase C did not become a second one.

    The allowlist is asserted as-is rather than extended: ``md_search`` reads the
    vault from disk, so if it ever appears here the "works with Obsidian closed"
    property has already been lost, whatever the docstrings say.
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
    assert 'method="GET"' in proxy
    assert re.search(r"method\s*=\s*[\"'](?!GET)", proxy) is None
    assert obsidian_proxy.BASE_URL == "http://127.0.0.1:27123"
    assert obsidian_proxy.OBSIDIAN_PORT in mcp_server.HARDENED_PORTS


def test_the_obsidian_token_still_never_crosses_the_mcp_boundary(mcp_client):
    """Every frame collected so far is a protocol frame, and none carries the key.

    B-verify establishes this against the vault tools; the reason to re-assert it
    at C is the prose tool, which is the first tool to return arbitrary *file
    content* from the same vault. An excerpt is a substring of a note chosen by
    the product, and a naive implementation that widened it — or that reported an
    error by echoing its configuration — would show up here.
    """
    prose = _prose_search(mcp_client, SHARED_QUESTION)
    _assert_clean(prose, mcp_client.last_raw, where=PROSE_TOOL)

    for name in ("security_status", "stop_gate_status"):
        payload = _tool_payload(
            mcp_client.call("tools/call", {"name": name, "arguments": {}})
        )
        _assert_clean(payload, mcp_client.last_raw, where=name)

    assert mcp_client.stdout_lines
    for line in mcp_client.stdout_lines:
        text = line.decode("utf-8")
        assert json.loads(text).get("jsonrpc") == "2.0", text[:200]
        assert CANARY_TOKEN not in text
        assert "Bearer" not in text


# ---------------------------------------------------------------------------
# 4. Scenario A: one question, two views
# ---------------------------------------------------------------------------


def test_the_same_question_is_answered_by_search_db_and_by_markdown_search(mcp_client):
    """SC-001, stated as one call each: state answers, prose answers, both real.

    勉強 is seeded as an item *and* written in two fixture notes, so the two paths
    are answering the same question from different material rather than one path
    quietly serving both. Each answer is asserted to be non-empty and to name
    where it came from — an empty result from either side would satisfy a weaker
    "both tools returned successfully" check while proving nothing.
    """
    state = _tool_payload(
        mcp_client.call(
            "tools/call",
            {"name": "search_db", "arguments": {"query": SHARED_QUESTION}},
        )
    )
    _assert_clean(state, mcp_client.last_raw, where="search_db")

    assert state["hit_count"] >= 1, "the state view found nothing to answer with"
    item_ids = {hit["item_id"] for hit in state["hits"]}
    assert KNOWN_WORD_ID in item_ids, state["hits"]
    # Provenance is named on every hit: an item surface, an alias, a prefix, or
    # one of the *sentence* indexes. The sentence index names are matched by
    # prefix rather than mirrored as literals because the point here is only that
    # the state view answered out of state — ``fts_md_*`` appearing in this set
    # would mean the two paths had been quietly merged.
    for hit in state["hits"]:
        source = hit["source_index"]
        assert source in {"item_exact", "item_prefix", "alias"} or source.startswith(
            "fts_sentence"
        ), state["hits"]

    prose = _prose_search(mcp_client, SHARED_QUESTION)
    _assert_clean(prose, mcp_client.last_raw, where=PROSE_TOOL)

    assert _prose_names(prose) == {JAPANESE_NOTE, MIXED_NOTE}, prose
    assert prose["hit_count"] == 2, prose
    assert prose["indexed_notes"] == MARKDOWN_FILES, prose
    assert prose["index_empty"] is False, prose

    # The two answers are about the same word and are not the same answer: one
    # names items, the other names files. That is the dual-search design.
    assert "path" not in json.dumps(state["hits"], ensure_ascii=False)
    for hit in _prose_hits(prose):
        assert hit["path"], hit
        assert "item_id" not in hit, hit


def test_both_search_paths_route_the_short_japanese_query_the_same_way(mcp_client):
    """The routing rule is shared, and 勉強 is the query that proves it matters.

    Two characters, so FTS5's trigram tokenizer has no window for it and matches
    nothing — silently. If either path routed on its own rule, one of them would
    answer "no hits" for a word that is demonstrably present, and a learner would
    read that as an answer.
    """
    state = _tool_payload(
        mcp_client.call(
            "tools/call",
            {"name": "search_db", "arguments": {"query": SHARED_QUESTION}},
        )
    )
    prose = _prose_search(mcp_client, SHARED_QUESTION)

    assert len(SHARED_QUESTION) < md_search.TRIGRAM_MIN_CHARS, (
        "the shared question is no longer a short query, so this test is vacuous"
    )
    assert state["route"] == "words", state["route_reason"]
    assert _prose_route(prose) == "words", prose["route_reason"]
    # Each answer explains its own routing rather than only reporting it.
    assert str(md_search.TRIGRAM_MIN_CHARS) in state["route_reason"]
    assert str(md_search.TRIGRAM_MIN_CHARS) in prose["route_reason"]


def test_a_long_japanese_query_reaches_the_same_notes_through_trigram(mcp_client):
    """The other side of the routing rule, on running prose rather than a word.

    The sentence is in the Japanese note and the mixed one — and in the generated
    dashboard, which must not appear (see section 6). So this is simultaneously
    the long-query route and a check that route and default filtering compose.
    """
    prose = _prose_search(mcp_client, LONG_JP)
    _assert_clean(prose, mcp_client.last_raw, where=f"{PROSE_TOOL}({LONG_JP})")

    assert len(LONG_JP) >= md_search.TRIGRAM_MIN_CHARS
    assert _prose_route(prose) == "trigram", prose["route_reason"]
    assert _prose_names(prose) == {JAPANESE_NOTE, MIXED_NOTE}, prose
    assert all(hit["excerpt"] for hit in _prose_hits(prose)), (
        "a prose hit with no excerpt is a path, not an answer"
    )


# ---------------------------------------------------------------------------
# 5. Scenario B: the prose path with Obsidian closed
# ---------------------------------------------------------------------------


def test_the_prose_index_reads_the_vault_from_disk_and_not_over_http():
    """Asserted on ``md_search.py`` itself, because this is a property of the code.

    The behavioural test below can only observe that the prose path *worked* while
    :27123 was closed. This one observes that it could not have gone there at all:
    no HTTP client import, no port, no base URL, and no dependency on the proxy
    module. Together they are the difference between "Obsidian happened to be
    unnecessary" and "Obsidian cannot be necessary".
    """
    source = (PACKAGE_ROOT / "md_search.py").read_text(encoding="utf-8")

    for label, pattern in HTTP_CLIENT_PATTERNS:
        assert pattern.search(source) is None, (
            f"the prose index imports or calls an HTTP client ({label}); it must "
            "read the vault from disk"
        )
    assert str(obsidian_proxy.OBSIDIAN_PORT) not in source
    assert obsidian_proxy.BASE_URL not in source
    assert not re.search(r"^[ \t]*(?:import|from)[ \t]+.*obsidian_proxy", source, re.M)

    # The positive half: it opens files, and it walks the vault root.
    assert "def iter_markdown_files" in source
    assert "def vault_root" in source


def test_the_markdown_path_answers_while_obsidian_is_closed(mcp_client):
    """One session, two paths, opposite outcomes — which is the scenario itself.

    With nothing on :27123 the vault tools must fail honestly (``unreachable`` or
    ``timed out``, never a fabricated success) while the prose tool answers the
    same fixture question in full. Asserting both in one client, back to back, is
    what makes "the markdown path does not depend on Obsidian" an observation
    rather than an inference from two separate runs.

    Skipped — loudly, with the reason — when something *is* listening, which on a
    developer's machine means a real Obsidian with the plugin enabled. The prose
    assertions elsewhere in this file run either way; it is only the contrast that
    needs the port genuinely closed.
    """
    if _obsidian_is_listening():
        pytest.skip(
            f"{obsidian_proxy.BASE_URL} is accepting connections, most likely a "
            "real Obsidian with the Local REST API plugin enabled. Phase C's "
            "scenario is 'the markdown path succeeds with Obsidian fully closed', "
            "and the contrast it rests on — the vault tools failing while the "
            "prose tool answers — cannot be observed while the port is open. "
            "Close Obsidian, or disable the plugin, and run this gate again."
        )

    for name, arguments in (
        ("vault_file", {"path": f"{JAPANESE_NOTE}"}),
        ("vault_list", {}),
        ("obsidian_active_note", {}),
    ):
        payload = _tool_payload(
            mcp_client.call("tools/call", {"name": name, "arguments": arguments})
        )
        _assert_clean(payload, mcp_client.last_raw, where=f"{name} (obsidian closed)")
        assert payload["ok"] is False, (
            f"{name} reported success with nothing listening on "
            f"{obsidian_proxy.BASE_URL}"
        )
        assert payload["error"] in {
            obsidian_proxy.UNREACHABLE,
            obsidian_proxy.TIMED_OUT,
        }, payload
        assert payload["note"], "a failure with no explanation is not an answer"

    # Same server, same session, immediately afterwards: the prose path answers.
    prose = _prose_search(mcp_client, SHARED_QUESTION)
    _assert_clean(prose, mcp_client.last_raw, where=f"{PROSE_TOOL} (obsidian closed)")
    assert _prose_names(prose) == {JAPANESE_NOTE, MIXED_NOTE}, prose

    # And the English question the fixture was written around, so the closed-vault
    # claim is not carried by one Japanese query alone.
    english = _prose_search(mcp_client, TAG_AND_PROSE_TERM)
    assert {GRAMMAR_NOTE, MIXED_NOTE} <= _prose_names(english), english


# ---------------------------------------------------------------------------
# 6. Scenario D: frontmatter apart from the body, malformed non-fatal
# ---------------------------------------------------------------------------


def test_frontmatter_is_queryable_apart_from_body_text_over_the_wire(mcp_client):
    """One word, two questions — asked through the tool rather than the function.

    ``conditional`` is a tag on 01 and prose in 01 and 03. Filtering on the tag
    must not drag in the note that only mentions it in prose. ``test_md_search.py``
    proves this of the function; the gate's job is to prove the *adapter* did not
    flatten the two into one search on the way to the agent.
    """
    by_tag = _prose_search(mcp_client, tags=[TAG_AND_PROSE_TERM])
    by_body = _prose_search(mcp_client, TAG_AND_PROSE_TERM)

    assert _prose_names(by_tag) == {GRAMMAR_NOTE}, by_tag
    assert {GRAMMAR_NOTE, MIXED_NOTE} <= _prose_names(by_body), by_body
    assert _prose_names(by_tag) != _prose_names(by_body), (
        "the tag filter and the body search returned the same set, so one of them "
        "is not doing what it claims"
    )
    # A pure frontmatter query has no text to route, and says so.
    assert _prose_route(by_tag) is None, by_tag["route_reason"]
    assert by_tag["filters"]["tags"] == [TAG_AND_PROSE_TERM], by_tag["filters"]


def test_a_scalar_frontmatter_field_filters_on_its_own_over_the_wire(mcp_client):
    """``type`` is a field, not a substring of one blob of frontmatter text."""
    dailies = _prose_search(mcp_client, fields={"type": "daily"})

    assert _prose_names(dailies) == {MIXED_NOTE}, dailies
    (hit,) = _prose_hits(dailies)
    assert hit["frontmatter"]["type"] == "daily", hit
    assert hit["frontmatter_ok"] is True, hit


def test_malformed_frontmatter_is_flagged_over_the_wire_but_still_searchable(mcp_client):
    """Flagged, not dropped: the operator can find it, the searcher is unaffected.

    ``thunderstruck`` exists in exactly one fixture file, and that file's
    frontmatter block never closes. The whole corpus is still searchable around
    it, which is the "non-fatal" half of the claim.
    """
    broken = _prose_search(mcp_client, BODY_ONLY_MALFORMED)
    _assert_clean(broken, mcp_client.last_raw, where=f"{PROSE_TOOL} (malformed)")

    assert _prose_names(broken) == {MALFORMED_NOTE}, broken
    (hit,) = _prose_hits(broken)
    assert hit["frontmatter_ok"] is False, hit
    assert hit["frontmatter"] == {}, "an unclosed block yields no fields at all"

    # So its half-written `tags: [grammar` is not a tag either.
    assert _prose_hits(_prose_search(mcp_client, BODY_ONLY_MALFORMED, tags=["grammar"])) == []

    # And the flag discriminates, or it says nothing about the broken note.
    intact = _prose_search(mcp_client, tags=[TAG_AND_PROSE_TERM])
    assert all(hit["frontmatter_ok"] is True for hit in _prose_hits(intact)), intact


def test_generated_notes_stay_out_of_a_prose_answer_by_default(mcp_client):
    """``.derived/`` output is in the corpus and out of the answer unless asked for.

    The dashboard carries the same Japanese sentence as the hand-written notes on
    purpose, so the generated flag is the *only* thing that can keep it out — and
    the default is what a learner's question actually hits.
    """
    default = _prose_search(mcp_client, SHARED_QUESTION)
    assert DERIVED_NOTE not in _prose_names(default), default
    assert _prose_names(default) == {JAPANESE_NOTE, MIXED_NOTE}, default
    assert all(hit["generated"] is False for hit in _prose_hits(default)), default

    included = _prose_search(mcp_client, SHARED_QUESTION, include_generated=True)
    assert DERIVED_NOTE in _prose_names(included), included
    by_name = {PurePosixPath(h["path"]).name: h for h in _prose_hits(included)}
    assert by_name[DERIVED_NOTE]["generated"] is True, by_name[DERIVED_NOTE]
    assert by_name[DERIVED_NOTE]["path"] == f".derived/{DERIVED_NOTE}"
    assert by_name[JAPANESE_NOTE]["generated"] is False


# ---------------------------------------------------------------------------
# 7. Scenario C: incremental re-index, and no ghosts
#
# Last on purpose: these are the only tests that change the fixture vault, and
# they change it for the module-scoped server every later assertion shares. They
# run in file order — edit, then delete, then the closing re-ask — and the closing
# test exists so a mutation cannot quietly invalidate section 4's premise.
# ---------------------------------------------------------------------------


def _reindex(
    cold: dict[str, Any], *, full: bool = False
) -> subprocess.CompletedProcess[str]:
    """``python -m katagiri.md_search rebuild``, as a real, cold process.

    A subprocess rather than a function call, for the same reason every other
    "cold" claim in this family is a subprocess: it carries no state from this
    test session, opens the database through the product's own config path, and
    prints the report a human running the quickstart would read.

    PHASE2 seam 4: if T008 registered a re-index tool, this is where the scenario
    would move onto the wire.
    """
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(cold["app_data"])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    argv = ["rebuild", "--root", str(cold["vault"])]
    if full:
        argv.append("--full")
    return subprocess.run(
        [sys.executable, "-m", "katagiri.md_search", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(REPO_ROOT),
        timeout=300,
    )


#: The counter lines of ``MdIndexResult.render()``, which is what a cold rebuild
#: prints to stderr. Parsed rather than eyeballed so the assertion below names a
#: number rather than a substring of a sentence.
_REPORT_LINE = re.compile(r"^(?P<key>[a-z ]+?)\s*:\s*(?P<value>.*)$", re.MULTILINE)


def _report(completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """The rendered report, as a mapping, from a finished rebuild process."""
    assert completed.returncode == 0, (
        f"the rebuild failed ({completed.returncode}):\n{completed.stderr[-3000:]}"
    )
    parsed = {
        match.group("key").strip(): match.group("value").strip()
        for match in _REPORT_LINE.finditer(completed.stderr)
    }
    for required in ("scanned", "indexed", "removed", "unchanged", "failed"):
        assert required in parsed, (
            f"the rebuild printed no {required!r} line; stderr was:\n"
            f"{completed.stderr[-3000:]}"
        )
    return parsed


def _count(report: dict[str, str], key: str) -> int:
    """One counter out of the report. Values may carry a trailing gloss."""
    match = re.match(r"(\d+)", report[key])
    assert match, f"{key} is not a count: {report[key]!r}"
    return int(match.group(1))


def _assert_log_line(stderr: str, expected: str) -> None:
    """The INFO line ``rebuild_md_index`` logs, when the CLI configures logging.

    PHASE2 seam 5. ``MdIndexResult``'s docstring names two pieces of SC-003
    evidence — the returned report *and* the stderr line logged from it — but
    ``md_search.main`` never calls ``logging_setup.setup_logging``, so in a CLI run
    the INFO record is dropped before it reaches a handler. The rendered report is
    asserted strictly by the caller; this stays advisory until the CLI configures
    logging, at which point the ``if`` comes out and the assertion stands alone.
    """
    if "md index" in stderr:
        assert expected in stderr, (
            f"the rebuild logged a line that does not say {expected!r}:\n"
            f"{stderr[-2000:]}"
        )


def _touch_later(path: Path, seconds: int = 10) -> None:
    """Push a file's mtime forward so change detection cannot tie on a clock tick."""
    stamp = path.stat().st_mtime + seconds
    os.utime(path, (stamp, stamp))


def test_editing_one_note_reindexes_only_that_file_and_shows_the_new_text(
    cold, mcp_client
):
    """SC-003, end to end: one edit, one re-indexed file, one changed answer.

    Three things in the order that makes each provable. First a cold rebuild with
    nothing changed, which must report zero indexed — that is what proves a
    separate process agrees with the index the fixture built, so the ``1`` below
    is an edit rather than a disagreement about stamps. Then the edit, and the
    report: exactly one file re-indexed, the other five untouched, and the whole
    vault still scanned, because scanning is cheap and re-tokenizing is what must
    not happen. Then the wire: the new text is findable, the replaced text is not.
    """
    baseline = _report(_reindex(cold))
    assert _count(baseline, "indexed") == 0, (
        "a cold process disagreed with the index this module built, so the "
        "incremental count below would not be evidence about the edit"
    )
    assert _count(baseline, "unchanged") == MARKDOWN_FILES
    assert _count(baseline, "scanned") == MARKDOWN_FILES

    note = cold["vault"] / JAPANESE_NOTE
    # Rewritten rather than appended to: the replaced sentence has to disappear,
    # and an append could not prove that.
    note.write_text(
        "---\n"
        "title: 勉強ノート\n"
        "tags: [japanese, vocab]\n"
        "date: 2026-08-18\n"
        "type: note\n"
        "lang: ja\n"
        "---\n"
        "\n"
        "# 勉強ノート\n"
        "\n"
        "毎日日本語を勉強しています。\n"
        "\n"
        f"{EDIT_BEACON} はこの版にしかない。\n",
        encoding="utf-8",
    )
    _touch_later(note)

    completed = _reindex(cold)
    report = _report(completed)

    assert _count(report, "indexed") == 1, (
        f"only the edited note may be re-indexed; report was:\n{completed.stderr}"
    )
    assert _count(report, "unchanged") == MARKDOWN_FILES - 1
    assert _count(report, "removed") == 0
    assert _count(report, "failed") == 0
    assert _count(report, "scanned") == MARKDOWN_FILES
    assert report["mode"] == "incremental", report["mode"]
    _assert_log_line(completed.stderr, "indexed=1")

    # The edit is visible through the tool, and the replaced sentence is gone.
    beacon = _prose_search(mcp_client, EDIT_BEACON)
    assert _prose_names(beacon) == {JAPANESE_NOTE}, beacon
    assert _prose_hits(_prose_search(mcp_client, ONLY_IN_JP_NOTE)) == [], (
        "the replaced text is still being served, so the re-index did not replace "
        "the note's rows"
    )


def test_a_deleted_note_leaves_no_ghost_hits_anywhere(cold, mcp_client):
    """Delete the note, re-index, ask again: the corpus shrinks and forgets it.

    Both of its unique terms are asked for — one English, one Japanese — because
    the two go down different routes, and a ghost that survives in one index while
    being cleaned out of the other is exactly the failure this checks for.
    """
    present = _prose_search(mcp_client, GHOST_EN)
    assert _prose_names(present) == {GHOST_NOTE}, (
        "the ghost note was not findable before it was deleted, so its absence "
        "afterwards would prove nothing"
    )

    (cold["vault"] / GHOST_NOTE).unlink()
    completed = _reindex(cold)
    report = _report(completed)

    assert _count(report, "removed") == 1, completed.stderr
    assert _count(report, "scanned") == MARKDOWN_FILES - 1
    assert _count(report, "indexed") == 0, "a deletion is not a re-index"

    assert _prose_hits(_prose_search(mcp_client, GHOST_EN)) == [], "ghost hit (en)"
    assert _prose_hits(_prose_search(mcp_client, GHOST_JP)) == [], "ghost hit (ja)"

    # The corpus itself is smaller, so the emptiness above is a removal rather
    # than a query that stopped matching.
    after = _prose_search(mcp_client, SHARED_QUESTION)
    assert after["indexed_notes"] == MARKDOWN_FILES - 1, after
    assert after["index_empty"] is False, after


def test_the_shared_question_still_answers_after_the_vault_changed(mcp_client):
    """The closing cumulative check: C's own mutations did not break C's premise.

    Section 7 rewrote one note and deleted another. The dual-search claim has to
    survive that — the state view is untouched by vault edits, and the prose view
    must still find the same two notes, one of which now has different words in
    it. If this fails, the earlier sections passed against a corpus that no longer
    exists.
    """
    state = _tool_payload(
        mcp_client.call(
            "tools/call",
            {"name": "search_db", "arguments": {"query": SHARED_QUESTION}},
        )
    )
    assert KNOWN_WORD_ID in {hit["item_id"] for hit in state["hits"]}, state

    prose = _prose_search(mcp_client, SHARED_QUESTION)
    _assert_clean(prose, mcp_client.last_raw, where=f"{PROSE_TOOL} (post-mutation)")
    assert _prose_names(prose) == {JAPANESE_NOTE, MIXED_NOTE}, prose
    assert _prose_route(prose) == state["route"] == "words", (prose, state)
