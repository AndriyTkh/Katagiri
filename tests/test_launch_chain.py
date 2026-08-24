"""T006 (007-setup-observability, US1): the .mcp.json launch chain, end to end.

``.mcp.json`` (repo root) registers exactly one connection::

    { "command": "python",
      "args": ["agent/scripts/setup.py", "--stdio-bootstrap"],
      "env": {"PYTHONUTF8": "1"} }

Every prior gate in this suite (``test_averify.py`` / ``test_bverify.py`` /
``test_cverify.py`` / ``test_abc_workflow.py``) spawns ``python -m
katagiri.mcp_server`` *directly*, skipping the one hop an MCP client actually
takes: ``setup.py --stdio-bootstrap`` runs the whole non-interactive setup
walk first (stderr-only, since stdout is reserved for the JSON-RPC stream —
see ``STDIO_BOOTSTRAP``/``_OUT`` near the top of ``agent/scripts/setup.py``),
then execs into the real server, inheriting this process's stdio untouched.
Nothing in the per-module gates proves that handoff itself works: a change
to the bootstrap's stdout/stderr routing, or to the exec in ``launch_server``,
could break every real MCP client while every existing gate stayed green.

This file is that missing link: it drives the client side of exactly that
chain and checks the three things a real client depends on:

1. the ``initialize`` handshake completes and the server answers a real
   ``tools/call`` afterwards -- the bootstrap step actually reaches and
   execs the server rather than exiting first;
2. stdout carries *only* JSON-RPC frames -- every non-empty line emitted
   during the whole session (bootstrap included) parses as JSON, so no
   ``print()`` from the setup steps ever leaked onto the wire a client reads
   as protocol;
3. stderr carries the bootstrap's own step banners and the server's startup
   line -- proving the six numbered setup steps really ran before the exec,
   not that stdio was simply proxied to an already-running server.

Sandboxing: ``setup.py`` resolves ``AGENT_DIR``/``REPO_ROOT``/``ENV_FILE``
from its own ``__file__``, so the only way to control which ``agent/.env``
it reads and writes -- without ever touching this checkout's real,
gitignored ``agent/.env`` -- is to run a copy of the script from a fake
repo root (mirrored from ``test_bootstrap_log.py``'s ``_make_sandbox``).
That copy's ``.env`` is pre-seeded with ``KATAGIRI_PYTHON`` pointing at the
*real* checkout's venv, so the server this file's client actually talks to
is the real, editable-installed ``katagiri.mcp_server`` -- the resolution of
vendor data, the config file, and the database all happen inside that
server via its own ``__file__``/``LOCALAPPDATA``, never via the sandbox's
cwd, so this substitution is faithful to what .mcp.json really launches.
``LOCALAPPDATA`` is always overridden to a per-run temp directory, so no
run ever touches the real ``%LOCALAPPDATA%\\Katagiri`` data home, and
``KATAGIRI_DATA_HOME``/``KATAGIRI_CONFIG`` are cleared so neither can smuggle
the real data home back in. ``uv`` is hidden from PATH so ``step_deps``'
``uv sync`` (slow, network-touching, irrelevant here) never runs.

One subprocess for the whole file (module-scoped fixture): the bootstrap's
own startup work -- most visibly a ``netstat`` scan inside
``security_scan()`` -- can legitimately take on the order of ten to thirty
seconds on a loaded Windows box before the first JSON-RPC frame appears, so
every wait in this file budgets a full 60s rather than re-paying that cost
per test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.mcp

TIMEOUT = 60
PROTOCOL_VERSION = "2026-07-28"

REAL_REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SCRIPT_SRC = REAL_REPO_ROOT / "agent" / "scripts" / "setup.py"
REAL_VENV_PYTHON = REAL_REPO_ROOT / ".venv" / "Scripts" / "python.exe"


def _bootstrap_python() -> str:
    """Interpreter that runs the sandboxed setup.py copy.

    The script is stdlib-only (any Python 3.9+ works); the primary
    checkout's own venv is preferred so this matches how a real MCP client
    (any Python on PATH) plus ``KATAGIRI_PYTHON`` actually resolves the
    server interpreter.
    """
    if REAL_VENV_PYTHON.exists():
        return str(REAL_VENV_PYTHON)
    return sys.executable  # pragma: no cover - only if .venv is missing


def _path_without_uv() -> str:
    """The current PATH with uv's directory removed.

    Keeps ``step_deps()`` from actually invoking ``uv sync`` (slow,
    network-touching, and irrelevant to whether the stdio chain launches)
    without patching the script itself: ``shutil.which("uv")`` finds
    nothing in the child. Mirrored from ``test_bootstrap_log.py``.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    uv_path = shutil.which("uv")
    if uv_path:
        uv_dir = str(Path(uv_path).resolve().parent)
        parts = [p for p in parts if p and str(Path(p).resolve()) != uv_dir]
    return os.pathsep.join(parts)


def _make_sandbox(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway ``<root>/agent/scripts/setup.py`` skeleton.

    ``agent/.env`` is pre-seeded so ``KATAGIRI_PYTHON``/``KATAGIRI_MODULE``
    already point at the real checkout's venv and server module before the
    script's own defaulting runs -- the point of this file is the stdio
    chain, not re-proving step_defaults() (that's T005's job).

    Returns ``(script_path, appdata_dir)``; ``appdata_dir`` is an existing,
    writable directory suitable for ``LOCALAPPDATA``.
    """
    root = tmp_path / "sandbox_repo"
    scripts_dir = root / "agent" / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "agent" / "pyproject.toml").write_text(
        '[project]\nname = "agent-sandbox"\nversion = "0"\n', encoding="utf-8"
    )
    script = scripts_dir / "setup.py"
    script.write_text(SETUP_SCRIPT_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "agent" / ".env").write_text(
        f"KATAGIRI_PYTHON={REAL_VENV_PYTHON.as_posix()}\n"
        "KATAGIRI_MODULE=katagiri.mcp_server\n",
        encoding="utf-8",
    )
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    return script, appdata


def _kill_tree(pid: int) -> None:
    """Kill ``pid`` and every descendant it spawned.

    ``setup.py --stdio-bootstrap`` does not exec into the server on Windows
    (there is no fork+exec-replace here): it launches ``python -m
    katagiri.mcp_server`` as a *child* via a blocking ``subprocess.run`` and
    that child inherits this test's stdout/stderr pipe handles. Killing only
    the top-level ``setup.py`` PID (``Popen.kill()``) leaves that grandchild
    alive holding the write end of those pipes open, so any later
    ``.read()``/``.readline()`` on them blocks forever even though the
    process we know about has exited -- this is the exact shape of the hang
    that killed the previous attempt at this file. ``taskkill /T`` walks the
    whole process tree started under this PID, so the grandchild's handles
    are actually released.
    """
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
    )


class _StdioClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over a pipe.

    Mirrored from ``test_abc_workflow.py``'s ``_StdioClient`` (via
    ``test_averify.py`` / ``test_bverify.py`` / ``test_cverify.py``) rather
    than imported, so this gate keeps meaning the same thing if any of those
    files is retired. The one difference: this client speaks to whatever
    process ``argv`` execs into (``setup.py --stdio-bootstrap``), not to
    ``katagiri.mcp_server`` directly -- the bootstrap hop is exactly what
    this file exists to exercise.
    """

    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        self._next_id = 0
        self.stdout_lines: list[bytes] = []
        self._tree_killed = False
        # Continuous background stderr drain -- NOT an optional nicety.
        # setup.py's own banners (six numbered steps plus the presence
        # report) run comfortably past a Windows anonymous pipe's default
        # buffer, and this client only reads stdout in lockstep with its
        # own requests. Without something permanently draining stderr in
        # parallel, the child's own `print(..., file=sys.stderr)` blocks
        # the instant that pipe fills -- setup.py stalls before it ever
        # reaches `launch_server()`, no JSON-RPC line ever appears on
        # stdout, and every read-with-timeout in this file times out
        # waiting on a process that isn't stuck on OUR pipe, it's stuck on
        # the other one. This is the actual shape of "sat alive
        # indefinitely" from the previous attempt: killing the tree after
        # the fact doesn't fix it, the pipe has to be drained *while the
        # child is running*.
        self._stderr_chunks: list[bytes] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._pump_stderr, daemon=True
        )
        self._stderr_thread.start()

    def _pump_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            for chunk in iter(lambda: self.process.stderr.read(4096), b""):
                with self._stderr_lock:
                    self._stderr_chunks.append(chunk)
        except (OSError, ValueError):  # pragma: no cover - pipe torn down
            pass

    def kill_tree(self) -> None:
        """Idempotent: safe to call more than once (a dead PID just makes
        ``taskkill`` fail quietly, which we don't care about)."""
        _kill_tree(self.process.pid)
        self._tree_killed = True

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self.process.stdin.flush()

    def _readline_with_timeout(self, timeout: float) -> bytes:
        """``readline()`` that gives up after ``timeout`` seconds instead of
        hanging the whole suite if the bootstrap chain ever wedges."""
        assert self.process.stdout is not None
        box: dict[str, bytes] = {}

        def _read() -> None:
            box["line"] = self.process.stdout.readline()

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            # Kill the WHOLE tree, not just setup.py's own PID: a plain
            # Popen.kill() would leave the server grandchild alive and
            # still holding stdout's write end open, so a later read for
            # trailing output would hang too. (Safe to call _drain_stderr()
            # below regardless of ordering -- it only reads the background
            # pump thread's buffer, never the pipe itself.)
            self.kill_tree()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
            raise AssertionError(
                f"no stdout line within {timeout}s of the bootstrap chain; "
                f"stderr so far:\n{self._drain_stderr()}"
            )
        return box.get("line", b"")

    def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = TIMEOUT
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
        line = self._readline_with_timeout(timeout)
        if not line:
            raise AssertionError(
                "the bootstrap chain closed stdout before answering; stderr was:\n"
                + self._drain_stderr()
            )
        self.stdout_lines.append(line)
        response = json.loads(line.decode("utf-8"))
        assert response["jsonrpc"] == "2.0", response
        assert response["id"] == self._next_id, response
        return response

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _drain_stderr(self) -> str:
        """Everything the background pump thread has captured so far --
        safe to call at any time, live process or dead, because it never
        touches the pipe itself (that's the pump thread's job)."""
        with self._stderr_lock:
            data = b"".join(self._stderr_chunks)
        return data.decode("utf-8", "replace")

    def _read_stdout_remaining_with_timeout(self, timeout: float) -> bytes:
        """``.read()`` that gives up after ``timeout`` seconds rather than
        blocking forever on a pipe whose write end a stray process still
        holds open."""
        assert self.process.stdout is not None
        box: dict[str, bytes] = {}

        def _read() -> None:
            box["data"] = self.process.stdout.read()

        thread = threading.Thread(target=_read, daemon=True)
        thread.start()
        thread.join(timeout)
        return box.get("data", b"")

    def close(self) -> str:
        """Close stdin (the chain's own shutdown trigger), wait for exit,
        then unconditionally kill the whole process tree before reading
        anything else.

        Closing stdin lets the server see EOF and shut down on its own in
        the happy path, but nothing here trusts that to be sufficient:
        ``subprocess.run`` inside ``setup.py`` means the server is a real
        child process (not an exec-replace), so even a clean exit of
        setup.py's own PID can leave that child alive holding this
        process's stdout pipe handle open. Reading it before the tree is
        confirmed dead is exactly how the previous attempt at this file
        hung. ``kill_tree()`` is idempotent, so paying for it
        unconditionally -- even after a graceful exit -- costs nothing but
        guarantees the read below terminates. (stderr needs no such care:
        the background pump thread has been draining it continuously since
        construction, see ``_pump_stderr``.)
        """
        assert self.process.stdin is not None
        try:
            self.process.stdin.close()
        except OSError:  # pragma: no cover - already closed/broken pipe
            pass
        try:
            self.process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged chain
            pass
        self.kill_tree()
        try:
            self.process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - taskkill lag
            pass
        trailing = self._read_stdout_remaining_with_timeout(TIMEOUT)
        if trailing:
            self.stdout_lines.extend(line for line in trailing.splitlines() if line)
        self._stderr_thread.join(TIMEOUT)
        stderr = self._drain_stderr()
        self.process.stdout.close()
        self.process.stderr.close()
        return stderr


# ---------------------------------------------------------------------------
# One bootstrap-chain session, driven once, checked from every angle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bootstrap_session(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Runs ``setup.py --stdio-bootstrap`` once, all the way through: the
    ``initialize`` handshake, one real ``tools/call`` to prove the server is
    actually answering (not just that a pipe opened), then a clean shutdown.
    Everything observed is captured for the test functions below to assert
    on independently, so the (slow) subprocess is paid for exactly once.
    """
    if not REAL_VENV_PYTHON.exists():
        pytest.skip(
            f"primary checkout venv not found at {REAL_VENV_PYTHON}; run `uv sync`"
        )

    tmp_path = tmp_path_factory.mktemp("launch_chain")
    script, appdata = _make_sandbox(tmp_path)

    env = dict(os.environ)
    env["PATH"] = _path_without_uv()
    env["PYTHONUTF8"] = "1"
    env["LOCALAPPDATA"] = str(appdata)
    # Never let an inherited override smuggle the real data home back in.
    env.pop("KATAGIRI_DATA_HOME", None)
    env.pop("KATAGIRI_CONFIG", None)

    # Exactly .mcp.json's argv shape: ["python", "agent/scripts/setup.py",
    # "--stdio-bootstrap"], run from the (sandboxed) repo root.
    client = _StdioClient(
        [_bootstrap_python(), "agent/scripts/setup.py", "--stdio-bootstrap"],
        cwd=script.parent.parent.parent,
        env=env,
    )
    # Everything from here on must guarantee the tree comes down: an
    # assertion raised out of a bare `client.call()` (protocol mismatch, a
    # malformed response, ...) would otherwise skip `close()` entirely and
    # leak the whole bootstrap -> server chain for the rest of the run --
    # this `finally` is the harness-level backstop for that.
    try:
        initialize_response = client.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kata-launch-chain", "version": "1"},
            },
        )
        client.notify("notifications/initialized")
        tools_list_response = client.call("tools/list")
        stderr = client.close()
    except BaseException:
        client.kill_tree()
        raise

    return {
        "appdata": appdata,
        "initialize_response": initialize_response,
        "tools_list_response": tools_list_response,
        "stdout_lines": list(client.stdout_lines),
        "stderr": stderr,
    }


# ---------------------------------------------------------------------------
# 1. The initialize handshake completes, and the server answers afterwards
# ---------------------------------------------------------------------------


def test_initialize_handshake_completes(bootstrap_session):
    response = bootstrap_session["initialize_response"]
    assert "error" not in response, response
    result = response["result"]
    assert result["serverInfo"]["name"] == "katagiri"
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]


def test_server_answers_a_real_tool_call_after_the_handoff(bootstrap_session):
    """Not just a handshake: the bootstrap's exec really lands on a live
    server that can list its own tools, proving stdin/stdout were handed
    over intact rather than the process exiting right after `initialize`."""
    response = bootstrap_session["tools_list_response"]
    assert "error" not in response, response
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert "ping" in names
    assert names, "tools/list answered with no tools at all"


# ---------------------------------------------------------------------------
# 2. stdout carries only JSON-RPC frames
# ---------------------------------------------------------------------------


def test_every_nonempty_stdout_line_parses_as_json(bootstrap_session):
    """The setup walk's own output (``say``/``warn``/``ok``/``header``) is
    routed to stderr under ``--stdio-bootstrap`` (see ``_OUT`` in
    ``agent/scripts/setup.py``) precisely so stdout stays pure JSON-RPC for
    whatever client is reading it. This is the direct check of that
    contract: every line stdout produced across the whole session --
    bootstrap included, not just the two responses this file asked for --
    must parse as JSON, with nothing else riding along on the wire.
    """
    lines = bootstrap_session["stdout_lines"]
    assert lines, "no stdout at all was captured"
    for raw in lines:
        text = raw.decode("utf-8")
        if not text.strip():
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - the bug this guards
            raise AssertionError(
                f"a non-JSON line reached stdout: {text!r} ({exc})"
            ) from exc


def test_stdout_carries_exactly_the_two_responses_requested(bootstrap_session):
    """No extra frames snuck onto stdout beyond the ``initialize`` and
    ``tools/list`` responses this session actually asked for."""
    lines = [line for line in bootstrap_session["stdout_lines"] if line.strip()]
    assert len(lines) == 2, (
        f"expected exactly 2 stdout frames (initialize + tools/list), got "
        f"{len(lines)}: {[line.decode('utf-8', 'replace') for line in lines]}"
    )


# ---------------------------------------------------------------------------
# 3. stderr carries the bootstrap's own step banners and the server startup
# ---------------------------------------------------------------------------


def test_stderr_carries_the_bootstrap_steps_and_server_startup(bootstrap_session):
    stderr = bootstrap_session["stderr"]
    # The setup walk's own numbered steps (proves setup.py really ran, not
    # that .mcp.json's argv skipped straight to the server module).
    assert "1/6 Tooling" in stderr, stderr[-3000:]
    assert "2/6 Dependencies" in stderr, stderr[-3000:]
    # The handoff line from launch_server(), then the server's own startup.
    assert "[bootstrap] launching katagiri.mcp_server" in stderr, stderr[-3000:]
    assert "starting katagiri" in stderr, stderr[-3000:]
    assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]


def test_sandbox_never_touches_the_real_data_home(bootstrap_session):
    """The server's own startup line names the data home it resolved; this
    pins that resolution to the sandboxed LOCALAPPDATA, not the operator's
    real ``%LOCALAPPDATA%\\Katagiri``."""
    stderr = bootstrap_session["stderr"]
    appdata = bootstrap_session["appdata"]
    assert "katagiri db" in stderr, stderr[-3000:]
    assert str(appdata) in stderr, (
        f"server startup line did not mention the sandboxed data home "
        f"{appdata}:\n{stderr[-3000:]}"
    )
    assert (appdata / "Katagiri").is_dir(), (
        "the sandboxed LOCALAPPDATA never got a Katagiri/ subdirectory -- "
        "the server did not actually write into the sandbox"
    )
