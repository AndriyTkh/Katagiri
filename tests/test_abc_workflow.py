r"""kata-abcwf: one learner session, three phases, one server process.

This is the sibling of ``test_averify.py`` / ``test_bverify.py`` / ``test_cverify.py``
and is read the same way: a *cold* verification harness, not a unit-test suite.
Nothing here mocks a Katagiri module. But where those three files each gate one
phase in isolation, this file gates the seam *between* them: a real learner does
not run Phase A, close the process, run Phase B in a fresh one, and so on — they
open one session and move between dictionary lookups, vault reads and prose
search without the server ever restarting. Nothing in the per-phase gates proves
that sequence works, because none of them runs more than one phase's tools
against the same subprocess.

The scenario, in order, against one ``katagiri.mcp_server`` subprocess spoken to
over JSON-RPC on its stdin/stdout:

1. ``ping`` — the server is alive and reports a version.
2. ``security_status`` — a loopback-only report, with no credential in it.
3. Phase A: a real JMdict lookup, a ``search_db`` hit on a seeded sentence,
   ``known_word`` true/false, ``known_set_stats``/``recent_events`` matching the
   seed, and ``stop_gate_status``'s mechanical PASS/FAIL shape.
4. Phase B: ``vault_list``/``vault_file``/``obsidian_active_note`` answered by a
   loopback stub standing in for obsidian-local-rest-api, and a cumulative scan
   proving the configured token never reached any frame collected so far.
5. Phase C: ``search_notes`` answers the shared fixture question with the vault
   stub already torn down (Obsidian "closed"), tag/field filters work, and the
   same question is answerable through both ``search_db`` (state) and
   ``search_notes`` (prose).
6. Regression: ``ping`` and ``stop_gate_status`` still answer after the whole
   session, proving the one process survived all of the above.

Two vendored dependencies are real rather than faked, because faking either one
would remove exactly the seam this file exists to check:

* the real vendored JMdict, present in the session's database (skipped, loudly,
  only if ``vendor/jmdict`` is genuinely absent) — supplied by conftest's
  ``real_jmdict_template``, which is a copy of a real import of the real zip,
  not a fake, so what the lookup answers from is the shipped dictionary;
* the real vendored UniDic dictionary via fugashi, needed by *both*
  ``search_db``'s sentence FTS index and ``search_notes``'s prose index — the
  whole module skips, loudly, if it is absent, since neither phase's search step
  can be exercised without it and a partial run would prove less than it
  appears to.

The fixture vault is ``tests/fixtures/vault/`` (the frozen six-file corpus
``test_md_search.py`` and ``test_cverify.py`` own), copied into a throwaway
directory because Phase C's own rebuild writes index state next to it. Nothing
here edits or deletes a fixture file, unlike ``test_cverify.py`` section 7.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from katagiri import events, fts_index, jmdict_import, obsidian_proxy
from katagiri import config as config_mod
from katagiri import tokenizer as tok
from katagiri.db import open_db

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_VAULT = REPO_ROOT / "tests" / "fixtures" / "vault"

# Mirrored from test_cverify.py: fugashi itself is an ordinary pip dependency,
# always installed, so this line's real job is a clean skip message rather than
# a bare ImportError if something is badly wrong with the environment.
fugashi = pytest.importorskip("fugashi")


def _dicdir_available() -> bool:
    try:
        tok.dicdir_path()
    except tok.TokenizerError:
        return False
    return True


# ``mcp``: this file spawns the real katagiri.mcp_server subprocess, so conftest
# orders it into the last band, after the cheap unit groups have had their
# chance to fail fast.
#
# The skipif is whole-file, rather than per-test, because both the Phase A
# sentence index and the Phase C prose index need the vendored dictionary:
# there is no partial version of "one session, three phases" that means
# anything with the middle phase's search silently absent.
pytestmark = [
    pytest.mark.mcp,
    pytest.mark.skipif(
        not _dicdir_available(),
        reason=(
            "vendored UniDic 3.1.0 is absent (vendor/unidic/unidic); both "
            "search_db's sentence index and search_notes' prose index need it — "
            "see vendor/README.md"
        ),
    ),
]

PROTOCOL_VERSION = "2026-07-28"

#: The full A+B+C tool contract. Mirrored rather than imported from
#: katagiri.tool_registry, matching every sibling gate file: a gate that asks
#: the product what its own contract is cannot notice the contract drifting.
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
        "search_notes",
    }
)

#: Distinctive on purpose, like every sibling gate's canary: a substring
#: appearing anywhere collected is a leak and nothing else.
CANARY_TOKEN = "SECRET-ABCWF-CANARY"

_TS = "T00:00:00Z"

#: Seeded words: the first is marked known through the real event path, the
#: second is left alone so known_word has a real "not known" to answer with.
KNOWN_WORD_ID = "w-abcwf-taberu"
KNOWN_WORD_SURFACE = "食べる"
KNOWN_WORD_READING = "たべる"
UNKNOWN_WORD_ID = "w-abcwf-tango"
UNKNOWN_WORD_SURFACE = "単語"
UNKNOWN_WORD_READING = "たんご"
FIXTURE_WORDS: tuple[tuple[str, str, str], ...] = (
    (KNOWN_WORD_ID, KNOWN_WORD_SURFACE, KNOWN_WORD_READING),
    (UNKNOWN_WORD_ID, UNKNOWN_WORD_SURFACE, UNKNOWN_WORD_READING),
)

#: One sentence item, seeded directly (the way test_averify.py seeds its
#: frozen vault sentences), so search_db has a real row to find. The text is
#: the same long sentence test_cverify.py's SHARED_QUESTION test relies on, so
#: the morph-boundary behaviour it proves is not being re-guessed here.
SENTENCE_ID = "s-abcwf-01"
SENTENCE_TEXT = "毎日日本語を勉強しています。"

#: Two characters, so it also proves the routing: FTS5's trigram tokenizer
#: matches nothing below three characters, so both search_db and search_notes
#: must route this to the unicode61 word index or find nothing at all. Present
#: in the seeded sentence above *and* in two of the fixture vault's notes.
SHARED_QUESTION = "勉強"
JAPANESE_NOTE = "02-japanese-prose.md"
MIXED_NOTE = "03-mixed-en-jp.md"
GRAMMAR_NOTE = "01-grammar-conditionals.md"
DAILY_NOTE = MIXED_NOTE
TAG_FILTER_TERM = "conditional"

#: JMdict's own sequence number for 食べる, from the vendored dictionary —
#: mirrored from test_averify.py, which is where this number is proven, not
#: guessed at here.
TABERU_SEQ = 1358280


def _vendor_jmdict_available() -> tuple[bool, str]:
    """Is the real vendored JMdict zip present, and if not, why not.

    A presence check through the module's own lookup function rather than a
    hand-rolled glob, so this file learns about a moved or renamed vendor
    layout the same way jmdict_import itself would.
    """
    try:
        jmdict_import.default_jmdict_zip()
    except jmdict_import.VendorFileError as exc:
        return False, str(exc)
    return True, ""


#: The four tables a JMdict import fills. Counted straight out of the database
#: below, because the dictionary arrives as a file copy of conftest's template
#: rather than as an import call that could hand back an ImportResult.
_JMDICT_TABLES = ("jmdict_entry", "jmdict_kanji", "jmdict_reading", "jmdict_sense")


def _jmdict_counts(conn) -> dict[str, int]:
    """Row counts for the imported dictionary, asserting none of them is zero.

    Same claim the old ``ImportResult`` carried: the dictionary in this database
    is really populated, so a lookup that finds nothing later is a product bug
    and not an empty table nobody noticed.
    """
    counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in _JMDICT_TABLES
    }
    empty = sorted(table for table, count in counts.items() if count == 0)
    assert not empty, (
        f"the JMdict template left {', '.join(empty)} empty; the dictionary did "
        f"not arrive intact (counts: {counts})"
    )
    return counts


# ---------------------------------------------------------------------------
# The loopback stub standing in for obsidian-local-rest-api
# ---------------------------------------------------------------------------

#: What the stub serves. The Today-equivalent path is the real vault-relative
#: name the product's own B1 exporter writes (test_cverify.py's fixture vault
#: carries a frozen copy of exactly this file, though the stub here answers
#: independently of it, over HTTP rather than disk). The second is nested and
#: non-ASCII, so the read that succeeds is not only the easiest possible one.
STUB_TODAY_PATH = ".derived/today.md"
STUB_ACTIVE_PATH = "Notes/学習ログ.md"
STUB_FILES: dict[str, str] = {
    STUB_TODAY_PATH: "# Today\n\nReviews due: 3. Streak: 1 day.\n",
    STUB_ACTIVE_PATH: "# 学習ログ\n\n食べる appears on this page. Nothing else.\n",
}
STUB_UNAUTHORIZED_BODY = b'{"errorCode": 40100, "message": "unauthorized"}'


class _VaultStub(HTTPServer):
    """A loopback stand-in for obsidian-local-rest-api on the product's own port.

    Mirrored from test_bverify.py's ``_VaultStub`` rather than imported, so this
    file keeps working, and keeps meaning the same thing, if that one is
    retired. Bound to the real port because the proxy has no port override.
    """

    allow_reuse_address = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)

    @property
    def seen(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.requests)


class _VaultStubHandler(BaseHTTPRequestHandler):
    """GET-only routing for the three paths the proxy knows how to ask for."""

    server: _VaultStub  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence. The default writes every request line to real stderr."""

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

        path = urllib.parse.unquote(self.path)
        if path == "/active/":
            body = STUB_FILES[STUB_ACTIVE_PATH].encode("utf-8")
            self._respond(200, body, "text/markdown")
            return
        if path == "/vault/":
            listing = json.dumps({"files": sorted(STUB_FILES)}, ensure_ascii=False)
            self._respond(200, listing.encode("utf-8"), "application/json")
            return
        relative = path[len("/vault/"):] if path.startswith("/vault/") else None
        if relative in STUB_FILES:
            self._respond(200, STUB_FILES[relative].encode("utf-8"), "text/markdown")
            return
        self._respond(404, b'{"errorCode": 40400}', "application/json")


def _obsidian_is_listening() -> bool:
    """Is something already accepting connections on the plugin's port?

    Mirrored from test_bverify.py / test_cverify.py: a client connect, never a
    bind, and used both to decide whether the stub fixture can stand up at all
    and to decide whether the "Obsidian is closed" contrast later in this file
    can be observed.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((obsidian_proxy.OBSIDIAN_HOST, obsidian_proxy.OBSIDIAN_PORT)) == 0


@pytest.fixture
def vault_stub():
    """The stub, serving on 27123 in a background thread, or a skip saying why.

    Function-scoped on purpose: it is used by exactly one test in the Phase B
    section, and the resulting teardown — before any Phase C test runs — is
    what makes "the prose path works with the stub stopped" true rather than
    asserted.
    """
    if _obsidian_is_listening():
        pytest.skip(
            f"{obsidian_proxy.BASE_URL} is already in use, most likely a real "
            "Obsidian with the Local REST API plugin enabled. The proxy has no "
            "port override, so the stub cannot move to another port. Close "
            "Obsidian, or disable the plugin, to run the Phase B vault-read step."
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
    thread = threading.Thread(target=server.serve_forever, name="abcwf-vault-stub", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# The cold environment: one database, one copied vault, one built prose index
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cold(request, tmp_path_factory) -> dict[str, Any]:
    """A migrated database, a copy of the fixture vault, and a built prose index.

    Session-scoped so one MCP subprocess can serve the whole ordered scenario —
    which is the point of this file. The seeding connection is closed before
    anything is spawned: the server and the cold ``md_search rebuild`` process
    each open the same file themselves.
    """
    root = tmp_path_factory.mktemp("abcworkflow")
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

    jmdict_available, jmdict_reason = _vendor_jmdict_available()

    if jmdict_available:
        # The dictionary arrives as a file copy of conftest's session template
        # (a real import of the real vendored zip, done once per run) instead of
        # a ~20s import repeated here. It has to land *before* open_db(): the
        # copy replaces the whole file, so anything written to db_path first —
        # the migration stamp, the seeds below — would be thrown away. The
        # fixture is pulled through getfixturevalue rather than declared as a
        # parameter so that an unvendored checkout still reaches the graceful
        # "jmdict absent" path below instead of skipping this whole module.
        template = request.getfixturevalue("real_jmdict_template")
        template.materialize(db_path)

    try:
        conn = open_db()
        try:
            for item_id, kanji, reading in FIXTURE_WORDS:
                conn.execute(
                    "INSERT INTO item (id, kind, kanji, reading, created_ts) "
                    "VALUES (?, 'word', ?, ?, ?)",
                    (item_id, kanji, reading, f"2026-01-01{_TS}"),
                )
            conn.execute(
                "INSERT INTO item (id, kind, kanji, created_ts) "
                "VALUES (?, 'sentence', ?, ?)",
                (SENTENCE_ID, SENTENCE_TEXT, f"2026-01-01{_TS}"),
            )
            mark = events.mark_item(conn, KNOWN_WORD_ID, "known", note="abcwf")

            if jmdict_available:
                jmdict_result = _jmdict_counts(conn)
            else:
                jmdict_result = None

            # Needed either way: the sentence FTS index below refuses to build
            # without dict_version/tokenizer_version stamped, and this stamp is
            # about the tokenizer's own UniDic dictionary, not JMdict.
            tok.stamp_versions(conn)
            index_result = fts_index.rebuild_index(conn)
        finally:
            conn.close()

        assert config_mod.get_config().obsidian_api_token == CANARY_TOKEN

        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(app_data)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        rebuild = subprocess.run(
            [sys.executable, "-m", "katagiri.md_search", "rebuild", "--root", str(vault)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(REPO_ROOT),
            timeout=300,
        )
        assert rebuild.returncode == 0, (
            f"the cold md_search rebuild failed:\n{rebuild.stderr[-3000:]}"
        )

        yield {
            "root": root,
            "app_data": app_data,
            "db_path": db_path,
            "vault": vault,
            "mark": mark,
            "jmdict_available": jmdict_available,
            "jmdict_reason": jmdict_reason,
            "jmdict_result": jmdict_result,
            "index": index_result,
        }
    finally:
        config_mod.reset_config_cache()
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous


# ---------------------------------------------------------------------------
# The one MCP subprocess the whole session shares
# ---------------------------------------------------------------------------


class _StdioClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over a pipe.

    Mirrored from test_averify.py (via test_bverify.py / test_cverify.py)
    rather than imported, so this gate keeps working, and keeps meaning the
    same thing, if any of those files is retired.
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

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
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


def _event_rows(payload: Any) -> list[dict[str, Any]]:
    """The list of event rows out of a ``recent_events`` payload.

    Accepts either a bare JSON array or an ``{"result": [...]}`` wrapper: MCP's
    structured-content channel wraps a top-level array under ``result`` for
    some SDK versions, and this file's claim is about the rows themselves, not
    about which of the two equally valid wire shapes carries them.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        return payload["result"]
    raise AssertionError(f"recent_events payload has an unexpected shape: {payload!r}")


def _tool_payload(response: dict[str, Any]) -> Any:
    """The structured result of a tools/call, whichever field carries it."""
    assert "error" not in response, response
    result = response["result"]
    assert result.get("isError") is not True, result
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    blocks = [
        block["text"] for block in result.get("content", []) if block.get("type") == "text"
    ]
    assert blocks, f"no readable content in {result}"
    return json.loads(blocks[0])


def _call(client: _StdioClient, name: str, arguments: dict[str, Any] | None = None) -> Any:
    """One tools/call, returning its payload. Every tool call in this file goes
    through here, so the one place that knows the wire shape is this one."""
    response = client.call("tools/call", {"name": name, "arguments": arguments or {}})
    return _tool_payload(response)


def _assert_clean(payload: Any, raw: str, *, where: str) -> None:
    """No canary, and no traceback, in either the structure or the raw frame."""
    blob = json.dumps(payload, ensure_ascii=False)
    for haystack, what in ((blob, "the payload"), (raw, "the raw frame")):
        assert CANARY_TOKEN not in haystack, f"the obsidian_api_token leaked into {what} of {where}"
        assert "SECRET-ABCWF" not in haystack, f"a token fragment reached {what} of {where}"
        assert "ABCWF-CANARY" not in haystack, f"a token fragment reached {what} of {where}"
    for marker in ("Traceback (most recent call last)", 'File "'):
        assert marker not in blob, f"{where} answered with a raw traceback"


@pytest.fixture(scope="session")
def mcp_client(cold):
    """One server subprocess, handshake completed, for the whole ordered scenario."""
    client = _StdioClient(cold["app_data"])
    stderr_seen: list[str] = []
    try:
        initialized = client.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kata-abcwf", "version": "1"},
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
    assert "starting katagiri" in stderr, stderr[-2000:]
    assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]


# ---------------------------------------------------------------------------
# 1. ping: the server is alive, and it lists exactly the A+B+C contract
# ---------------------------------------------------------------------------


def test_01_ping_reports_server_alive_with_the_full_contract_listed(mcp_client):
    listed = mcp_client.call("tools/list")
    names = {tool["name"] for tool in listed["result"]["tools"]}
    # Subset, not equality: the shared venv's katagiri package is the main
    # checkout's (an editable install), which on this branch may already carry
    # later-phase tools this file does not exercise. What this scenario needs
    # is that the whole A+B+C surface is present and additive, not that
    # nothing else has landed since.
    assert CONTRACT_TOOLS <= names, "the A+B+C contract must still be present"

    payload = _call(mcp_client, "ping")
    _assert_clean(payload, mcp_client.last_raw, where="ping")
    assert payload["status"] == "ok", payload
    assert payload["katagiri_version"], "a version must be present, not blank"
    assert payload["python"], "a python version must be present, not blank"


# ---------------------------------------------------------------------------
# 2. security_status: a loopback-only report, no credential in it
# ---------------------------------------------------------------------------


def test_02_security_status_is_loopback_only_and_carries_no_token(mcp_client):
    payload = _call(mcp_client, "security_status")
    _assert_clean(payload, mcp_client.last_raw, where="security_status")

    assert payload["changed_anything"] is False
    assert str(obsidian_proxy.OBSIDIAN_PORT) in {str(p) for p in payload["checked_ports"]}
    assert isinstance(payload["all_loopback_only"], bool)
    assert isinstance(payload["note"], str) and payload["note"]


# ---------------------------------------------------------------------------
# 3. Phase A: lookup, search_db, known_word, known_set_stats/recent_events,
#    stop_gate_status
# ---------------------------------------------------------------------------


def test_03a_lookup_answers_from_the_real_jmdict_when_vendored(mcp_client, cold):
    payload = _call(mcp_client, "lookup", {"surface": KNOWN_WORD_SURFACE})
    _assert_clean(payload, mcp_client.last_raw, where="lookup")
    assert payload["surface"] == KNOWN_WORD_SURFACE

    if not cold["jmdict_available"]:
        pytest.skip(
            "vendor/jmdict is absent, so lookup cannot answer with real senses "
            f"({cold['jmdict_reason']}); found=false is still the honest answer "
            "and is asserted below instead."
        )

    assert payload["found"] is True, payload
    assert payload["note"] is None
    seqs = [entry["seq"] for entry in payload["entries"]]
    assert TABERU_SEQ in seqs, seqs
    entry = next(e for e in payload["entries"] if e["seq"] == TABERU_SEQ)
    assert any(form["text"] == KNOWN_WORD_SURFACE for form in entry["kanji"])
    assert any("eat" in (sense["gloss"] or "") for sense in entry["senses"]), entry["senses"]


def test_03b_search_db_finds_the_seeded_sentence(mcp_client):
    payload = _call(mcp_client, "search_db", {"query": SHARED_QUESTION})
    _assert_clean(payload, mcp_client.last_raw, where="search_db")

    assert payload["route"] == "words", payload["route_reason"]
    assert payload["index_empty"] is False
    hit_ids = {hit["item_id"] for hit in payload["hits"]}
    assert SENTENCE_ID in hit_ids, payload["hits"]


def test_03c_known_word_is_true_for_the_marked_item_and_false_for_the_unmarked_one(mcp_client):
    marked = _call(mcp_client, "known_word", {"query": KNOWN_WORD_ID})
    _assert_clean(marked, mcp_client.last_raw, where="known_word (marked)")
    assert marked["found"] is True
    assert marked["is_known"] is True
    assert marked["source"] == "manual"
    assert marked["manual_mark"] == "known"

    unmarked = _call(mcp_client, "known_word", {"query": UNKNOWN_WORD_ID})
    _assert_clean(unmarked, mcp_client.last_raw, where="known_word (unmarked)")
    assert unmarked["found"] is True
    assert unmarked["is_known"] is False, "a seeded but unmarked item is a real 'not known'"

    absent = _call(mcp_client, "known_word", {"query": "w-abcwf-never-seeded"})
    assert absent["found"] is False
    assert absent["is_known"] is None, "'never heard of it' must not read as 'not known'"


def test_03d_known_set_stats_and_recent_events_match_the_seeds(mcp_client, cold):
    stats = _call(mcp_client, "known_set_stats")
    _assert_clean(stats, mcp_client.last_raw, where="known_set_stats")

    # Two words plus one sentence, exactly one manual mark.
    assert stats["total"] == 3
    assert stats["known"] == 1
    assert stats["unknown"] == 2
    assert stats["suspect"] == 0
    assert stats["latest_marks_by_value"] == {"known": 1}
    assert stats["by_kind"]["word"] == {"total": 2, "known": 1}
    assert stats["by_kind"]["sentence"] == {"total": 1, "known": 0}
    assert stats["by_source"]["manual"] == {"total": 1, "known": 1}

    events_payload = _call(mcp_client, "recent_events", {"limit": 10})
    _assert_clean(events_payload, mcp_client.last_raw, where="recent_events")
    rows = _event_rows(events_payload)
    assert rows
    mark_events = [row for row in rows if row["type"] == "mark_known"]
    assert any(row["item_id"] == KNOWN_WORD_ID for row in mark_events), rows
    assert any(row["id"] == cold["mark"]["event_id"] for row in rows), rows


def test_03e_stop_gate_status_has_the_mechanical_pass_fail_shape(mcp_client):
    payload = _call(mcp_client, "stop_gate_status")
    _assert_clean(payload, mcp_client.last_raw, where="stop_gate_status")

    assert set(payload) >= {
        "pass",
        "failing_criterion",
        "study_days_in_window",
        "window_start",
        "window_end",
        "probe_battery_recorded",
        "required_study_days",
        "window_length_days",
        "excluded_pause_days",
        "study_day_keys",
        "ignored_pause_events",
    }
    assert payload["required_study_days"] == 14
    assert payload["window_length_days"] == 18
    assert isinstance(payload["pass"], bool)
    # One session, one manual mark: nowhere near 14 of 14/18 study days.
    assert payload["pass"] is False
    assert payload["study_days_in_window"] >= 1


# ---------------------------------------------------------------------------
# 4. Phase B: vault_list/vault_file/obsidian_active_note over the loopback
#    stub, then a cumulative scan for the token across everything so far
# ---------------------------------------------------------------------------


def test_04a_vault_tools_answer_through_the_loopback_stub(mcp_client, vault_stub):
    listing = _call(mcp_client, "vault_list")
    _assert_clean(listing, mcp_client.last_raw, where="vault_list")
    assert listing["ok"] is True, listing
    assert set(listing["files"]) == set(STUB_FILES), listing

    today = _call(mcp_client, "vault_file", {"path": STUB_TODAY_PATH})
    _assert_clean(today, mcp_client.last_raw, where="vault_file")
    assert today["ok"] is True, today
    assert today["content"] == STUB_FILES[STUB_TODAY_PATH]
    assert today["untrusted"] is True

    active = _call(mcp_client, "obsidian_active_note")
    _assert_clean(active, mcp_client.last_raw, where="obsidian_active_note")
    assert active["ok"] is True, active
    assert active["content"] == STUB_FILES[STUB_ACTIVE_PATH]
    assert active["untrusted"] is True

    # The stub saw the real credential; the client never did (checked above).
    seen = vault_stub.seen
    assert len(seen) >= 3, f"the stub was reached {len(seen)} time(s)"
    assert all(entry["authorization"] == f"Bearer {CANARY_TOKEN}" for entry in seen), seen


def test_04b_the_token_never_appears_in_any_output_collected_so_far(mcp_client):
    """A successful vault read is exactly the path that could carry the token
    on the way back — so the scan runs *after* the stub round, over every
    frame this one client has collected since the session began."""
    assert mcp_client.stdout_lines
    for line in mcp_client.stdout_lines:
        text = line.decode("utf-8")
        assert json.loads(text).get("jsonrpc") == "2.0", text[:200]
        assert CANARY_TOKEN not in text, "the token leaked into a collected frame"
        assert "Bearer" not in text, "the Authorization scheme leaked into a frame"


# ---------------------------------------------------------------------------
# 5. Phase C: search_notes with the stub stopped, filters, cross-view coherence
# ---------------------------------------------------------------------------


def test_05a_search_notes_answers_with_the_vault_stub_stopped(mcp_client):
    """The vault_stub fixture above is function-scoped and already torn down,
    so nothing is listening on :27123 here unless a real Obsidian is — proving
    the prose path needs neither the stub nor the real plugin."""
    if _obsidian_is_listening():
        pytest.skip(
            f"{obsidian_proxy.BASE_URL} is accepting connections, most likely a "
            "real Obsidian with the Local REST API plugin enabled on this "
            "machine. The contrast this test proves — vault tools unreachable "
            "while search_notes still answers — needs the port genuinely "
            "closed; close Obsidian (or disable the plugin) to observe it."
        )

    for name, arguments in (
        ("vault_file", {"path": JAPANESE_NOTE}),
        ("vault_list", {}),
        ("obsidian_active_note", {}),
    ):
        payload = _call(mcp_client, name, arguments)
        _assert_clean(payload, mcp_client.last_raw, where=f"{name} (stub stopped)")
        assert payload["ok"] is False, f"{name} reported success with the stub stopped"
        assert payload["error"] in {obsidian_proxy.UNREACHABLE, obsidian_proxy.TIMED_OUT}, payload

    prose = _call(mcp_client, "search_notes", {"query": SHARED_QUESTION})
    _assert_clean(prose, mcp_client.last_raw, where="search_notes (stub stopped)")
    names = {PurePosixPath(hit["path"]).name for hit in prose["hits"]}
    assert names == {JAPANESE_NOTE, MIXED_NOTE}, prose


def test_05b_search_notes_tags_and_fields_filters_work(mcp_client):
    by_tag = _call(mcp_client, "search_notes", {"tags": [TAG_FILTER_TERM]})
    _assert_clean(by_tag, mcp_client.last_raw, where="search_notes (tags)")
    tag_names = {PurePosixPath(hit["path"]).name for hit in by_tag["hits"]}
    assert tag_names == {GRAMMAR_NOTE}, by_tag
    assert by_tag["route"] is None, "a pure frontmatter query has no text to route"

    by_field = _call(mcp_client, "search_notes", {"fields": {"type": "daily"}})
    _assert_clean(by_field, mcp_client.last_raw, where="search_notes (fields)")
    field_names = {PurePosixPath(hit["path"]).name for hit in by_field["hits"]}
    assert field_names == {DAILY_NOTE}, by_field


def test_05c_the_same_question_is_answerable_via_search_db_and_search_notes(mcp_client):
    state_view = _call(mcp_client, "search_db", {"query": SHARED_QUESTION})
    _assert_clean(state_view, mcp_client.last_raw, where="search_db (cross-view)")
    assert SENTENCE_ID in {hit["item_id"] for hit in state_view["hits"]}, state_view

    prose_view = _call(mcp_client, "search_notes", {"query": SHARED_QUESTION})
    _assert_clean(prose_view, mcp_client.last_raw, where="search_notes (cross-view)")
    prose_names = {PurePosixPath(hit["path"]).name for hit in prose_view["hits"]}
    assert {JAPANESE_NOTE, MIXED_NOTE} <= prose_names, prose_view

    # Both routed the same short query the same way, which is only true
    # because the two indexes share the length-routing rule rather than by
    # coincidence.
    assert state_view["route"] == prose_view["route"] == "words"


# ---------------------------------------------------------------------------
# 6. Regression: the one process is still answering after the whole session
# ---------------------------------------------------------------------------


def test_06_ping_and_stop_gate_status_still_answer_after_the_whole_session(mcp_client):
    ping = _call(mcp_client, "ping")
    _assert_clean(ping, mcp_client.last_raw, where="ping (regression)")
    assert ping["status"] == "ok"

    gate = _call(mcp_client, "stop_gate_status")
    _assert_clean(gate, mcp_client.last_raw, where="stop_gate_status (regression)")
    assert isinstance(gate["pass"], bool)
