r"""Held-out validation fixtures for feature 007 (setup observability).

BINDING RULE (spec.md "Held-out rule"): this file and its sibling tests were
authored during planning, before task generation, and are excluded from every
implementation task's context. A gate failure is fixed by changing the
implementation, never by changing anything under this directory. See
MANIFEST.md.

Nothing here imports from ``tests/`` -- this suite is deliberately
self-contained, mirroring the repo's per-file ``_StdioClient`` convention
(tests/test_abc_workflow.py) rather than importing it, so the held-out gate
keeps meaning the same thing however the normal suite is refactored.

Isolation contract for every fixture below:

* every subprocess gets ``LOCALAPPDATA`` and ``KATAGIRI_CONFIG`` pointed at a
  throwaway sandbox under pytest's ``tmp_path``, so the real
  ``%LOCALAPPDATA%\Katagiri`` config / database / logs are never touched;
* every subprocess gets ``PYTHONUTF8=1`` (Windows console cp1252 cannot carry
  the Japanese text some children emit);
* every subprocess has a timeout and is killed on teardown;
* no Windows scheduled task is ever registered (``--yes`` suppresses the
  scheduled-task step; interactive runs answer its prompts, never "yes").
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

# --------------------------------------------------------------------------
# Opt-in guard (FR-012): the suite must be inert unless explicitly enabled.
# --------------------------------------------------------------------------

HOLDOUT_ENV_VAR = "KATAGIRI_HOLDOUT"

HOLDOUT_SKIP_REASON = (
    "held-out 007 stability suite: run it only at the feature gate with "
    "KATAGIRI_HOLDOUT=1 (see specs/007-setup-observability/holdout/MANIFEST.md)"
)


def holdout_enabled() -> bool:
    """True only when the gate explicitly opted in."""
    return os.environ.get(HOLDOUT_ENV_VAR) == "1"


@pytest.fixture(scope="session", autouse=True)
def _holdout_opt_in() -> None:
    """Backstop for the per-module ``skipif``: nothing runs without the flag."""
    if not holdout_enabled():
        pytest.skip(HOLDOUT_SKIP_REASON)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

#: Repo root: this file is <repo>/specs/007-setup-observability/holdout/conftest.py
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Planted in every sandbox config.toml as ``obsidian_api_token``. Must never
#: appear in a tool response, a raw JSON-RPC frame, stderr, or any log file.
CANARY_TOKEN = "HOLDOUT-CANARY-9f3a"

#: Fragments of the canary, checked separately so a partial/split leak is caught.
CANARY_FRAGMENTS = ("HOLDOUT-CANARY", "CANARY-9f3a")

#: Every sandbox directory name carries this, so "did anything land outside the
#: sandbox?" can be answered by name alone.
SANDBOX_MARKER = "kata-holdout"

#: Handshake identity this suite presents, echoed back by ``connection_status``.
CLIENT_NAME = "kata-holdout-gate"
CLIENT_VERSION = "007.1"

PROTOCOL_VERSION = "2024-11-05"

#: Installer subprocess budget. A sandbox seeded from the cached JMdict template
#: finishes a full ``--yes`` run in well under this on the reference machine.
INSTALLER_TIMEOUT = 60.0

#: Budget when the JMdict template cache is absent and step 3 must really
#: import the dictionary (only used by tests that allow an unseeded sandbox).
INSTALLER_COLD_TIMEOUT = 300.0

#: Budget for one server operation (spawn+handshake, one tools/call, shutdown).
SERVER_OP_TIMEOUT = 30.0

#: Bootstrap launcher budget (it must fail fast, before any server exec).
BOOTSTRAP_TIMEOUT = 60.0

#: SC-002: one diagnostic call answers in under five seconds.
DIAGNOSTIC_BUDGET_SECONDS = 5.0

TOTAL_INSTALLER_STEPS = 11


def jmdict_template() -> Path | None:
    """The suite-cached JMdict database, if this checkout has built one.

    Reusing it (spec Assumptions: "reuse the cached JMdict template
    mechanism") keeps a full ``--yes`` run at seconds instead of minutes. Its
    absence is not a failure: tests fall back to a real import under the cold
    timeout.
    """
    candidates = sorted((REPO_ROOT / "tests" / ".cache").glob("jmdict-*.db"))
    return candidates[-1] if candidates else None


# --------------------------------------------------------------------------
# Sandbox
# --------------------------------------------------------------------------


@dataclass
class Sandbox:
    """One throwaway ``%LOCALAPPDATA%`` for a single install / server run."""

    root: Path
    app_data: Path
    katagiri_dir: Path
    config_path: Path
    db_path: Path
    logs_dir: Path
    seeded: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)

    # -- environment --------------------------------------------------------

    def env(self, **extra: str) -> dict[str, str]:
        """A child environment redirected at this sandbox."""
        env = dict(os.environ)
        env.update(
            {
                "LOCALAPPDATA": str(self.app_data),
                "KATAGIRI_CONFIG": str(self.config_path),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.update(self.extra_env)
        env.update(extra)
        return env

    # -- content ------------------------------------------------------------

    def write_config(
        self, *, token: str | None = CANARY_TOKEN, anki_data_dir: Path | None = None
    ) -> None:
        """Write a minimal, valid config.toml naming this sandbox's database.

        ``token`` lands in ``obsidian_api_token``: the canary whose value must
        never surface anywhere (FR-011). ``anki_data_dir`` is how a test breaks
        one step's precondition on purpose (spec US1 acceptance scenario 2/3):
        an Anki profile directory that does not exist makes the Anki step fail
        without touching anything outside the sandbox.
        """
        lines = [f'db_path = "{self.db_path.as_posix()}"']
        if token is not None:
            lines.append(f'obsidian_api_token = "{token}"')
        if anki_data_dir is not None:
            lines.append(f'anki_data_dir = "{Path(anki_data_dir).as_posix()}"')
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def seed_db(self) -> bool:
        """Copy the cached JMdict database in. False when no cache exists."""
        template = jmdict_template()
        if template is None:
            return False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template, self.db_path)
        self.seeded = True
        return True

    def installer_timeout(self) -> float:
        return INSTALLER_TIMEOUT if self.seeded else INSTALLER_COLD_TIMEOUT

    # -- inspection ---------------------------------------------------------

    #: SQLite's own sidecars. A *read-only* open of a WAL database still stamps
    #: ``-shm``; that is the engine's bookkeeping, not a change to any Katagiri
    #: state, so a "changes nothing" claim excludes them and asserts on the
    #: database file itself instead.
    SQLITE_SIDECAR_SUFFIXES = ("-shm", "-wal", "-journal")

    def snapshot(
        self, *, ignore_logs: bool = True, ignore_sqlite_sidecars: bool = True
    ) -> dict[str, Any]:
        """Name -> fingerprint for every path in the sandbox.

        The log directory is ignorable on purpose: a doctor run is required to
        record itself (FR-010), so its own log file is the one thing a
        "changes nothing" claim cannot exclude.
        """
        out: dict[str, Any] = {}
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root)
            if ignore_logs and self.logs_dir.name in rel.parts:
                continue
            if ignore_sqlite_sidecars and path.name.endswith(self.SQLITE_SIDECAR_SUFFIXES):
                continue
            if path.is_dir():
                out[rel.as_posix()] = "DIR"
            else:
                stat = path.stat()
                out[rel.as_posix()] = (stat.st_size, stat.st_mtime_ns)
        return out

    def log_files(self) -> list[Path]:
        """Every log file this sandbox accumulated, wherever under it it landed."""
        if not self.root.exists():
            return []
        found = {
            path
            for pattern in ("**/*.log", "**/*.log.*", "**/logs/*")
            for path in self.root.glob(pattern)
            if path.is_file()
        }
        return sorted(found)

    def log_text(self) -> str:
        """Everything the sandbox's log files say, concatenated."""
        return "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in self.log_files()
        )

    def contains(self, other: str | Path) -> bool:
        """Is ``other`` a path inside this sandbox? (Windows: case-folded.)"""
        try:
            candidate = Path(other).resolve()
        except (OSError, ValueError):
            return False
        root = self.root.resolve()
        return str(candidate).casefold().startswith(str(root).casefold())


@pytest.fixture
def sandbox_factory(tmp_path: Path):
    """Make as many independent sandboxes as a test needs."""
    made: list[Sandbox] = []

    def make(*, config: bool = True, seed: bool = True, token: str | None = CANARY_TOKEN) -> Sandbox:
        root = tmp_path / f"{SANDBOX_MARKER}-{len(made) + 1}"
        app_data = root / "appdata"
        katagiri_dir = app_data / "Katagiri"
        katagiri_dir.mkdir(parents=True)
        box = Sandbox(
            root=root,
            app_data=app_data,
            katagiri_dir=katagiri_dir,
            config_path=katagiri_dir / "config.toml",
            db_path=katagiri_dir / "katagiri.db",
            logs_dir=katagiri_dir / "logs",
        )
        if seed:
            box.seed_db()
        if config:
            box.write_config(token=token)
        made.append(box)
        return box

    return make


@pytest.fixture
def sandbox(sandbox_factory) -> Sandbox:
    """The common case: seeded database, config with the canary token."""
    return sandbox_factory()


# --------------------------------------------------------------------------
# Installer runner
# --------------------------------------------------------------------------


def _completed_report(proc: subprocess.CompletedProcess[str], label: str) -> str:
    return (
        f"{label} exited {proc.returncode}\n"
        f"--- stdout ---\n{(proc.stdout or '')[-6000:]}\n"
        f"--- stderr ---\n{(proc.stderr or '')[-6000:]}"
    )


@pytest.fixture
def run_installer():
    """Run the real installer as a subprocess against a sandbox.

    ``stdin_text=None`` closes stdin, which is what "non-interactive" means on
    the wire: any prompt that would block gets EOF instead of hanging.
    """

    def run(
        box: Sandbox,
        *args: str,
        stdin_text: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        budget = box.installer_timeout() if timeout is None else timeout
        kwargs: dict[str, Any] = (
            {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
        )
        try:
            return subprocess.run(
                [sys.executable, "-m", "katagiri.installer", *args],
                cwd=str(REPO_ROOT),
                env=box.env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=budget,
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - a wedged installer
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            pytest.fail(
                f"installer {args} did not finish within {budget}s (a prompt or a "
                f"subprocess is blocking)\n--- stdout ---\n{stdout[-4000:]}\n"
                f"--- stderr ---\n{stderr[-4000:]}"
            )

    return run


def doctor_rows(stdout: str) -> list[tuple[str, str, str]]:
    """Parse the doctor table into ``(component, status, detail)`` triples.

    The table is the operator-facing contract of ``--check`` (FR-002: "the
    failing step is named in output"), so it is parsed rather than substring
    matched: a status word alone must not be enough to satisfy an assertion.
    """
    statuses = ("READY", "MISSING", "ACTION NEEDED", "MANUAL STEP", "SKIP", "OK")
    rows: list[tuple[str, str, str]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("-"):
            continue
        for status in statuses:
            index = line.find(status)
            if index <= 0:
                continue
            component = line[:index].strip()
            detail = line[index + len(status) :].strip()
            if component:
                rows.append((component, status, detail))
            break
    return rows


# --------------------------------------------------------------------------
# Stdio MCP client (mirrored from tests/test_abc_workflow.py, not imported)
# --------------------------------------------------------------------------


class StdioMcpClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over pipes.

    Both child streams are pumped by reader threads: a held-out test must never
    deadlock because a multi-kilobyte traceback filled the stderr pipe, and
    every read is bounded by ``timeout`` so a wedged server fails the test
    instead of hanging the gate.
    """

    def __init__(
        self,
        box: Sandbox,
        *,
        client_name: str = CLIENT_NAME,
        client_version: str = CLIENT_VERSION,
        timeout: float = SERVER_OP_TIMEOUT,
    ) -> None:
        self.sandbox = box
        self.client_name = client_name
        self.client_version = client_version
        self.timeout = timeout
        self.process = subprocess.Popen(
            [sys.executable, "-m", "katagiri.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=box.env(),
            cwd=str(REPO_ROOT),
        )
        self._next_id = 0
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue()
        self._stderr_chunks: list[bytes] = []
        self.stdout_lines: list[bytes] = []
        self.stderr_text = ""
        self._closed = False
        self._threads = [
            threading.Thread(target=self._pump_stdout, daemon=True),
            threading.Thread(target=self._pump_stderr, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    # -- plumbing -----------------------------------------------------------

    def _pump_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._stdout_queue.put(line)
        self._stdout_queue.put(None)

    def _pump_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr_chunks.append(line)

    def _stderr_so_far(self) -> str:
        return b"".join(self._stderr_chunks).decode("utf-8", "replace")

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        try:
            line = self._stdout_queue.get(timeout=self.timeout)
        except queue.Empty:  # pragma: no cover - a wedged server
            pytest.fail(
                f"the MCP server did not answer within {self.timeout}s; stderr so far:\n"
                + self._stderr_so_far()[-4000:]
            )
        if line is None:
            pytest.fail(
                "the MCP server closed stdout before answering; stderr was:\n"
                + self._stderr_so_far()[-4000:]
            )
        self.stdout_lines.append(line)
        return json.loads(line.decode("utf-8"))

    # -- protocol -----------------------------------------------------------

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

    def handshake(self) -> dict[str, Any]:
        response = self.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
        )
        assert "error" not in response, response
        result = response["result"]
        assert result["serverInfo"]["name"] == "katagiri", result
        assert result["protocolVersion"], result
        self.notify("notifications/initialized")
        return result

    def tool_names(self) -> set[str]:
        listed = self.call("tools/list")
        assert "error" not in listed, listed
        return {tool["name"] for tool in listed["result"]["tools"]}

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        response = self.call("tools/call", {"name": name, "arguments": arguments or {}})
        return tool_payload(response, name=name)

    @property
    def last_raw(self) -> str:
        assert self.stdout_lines, "nothing has been read from stdout yet"
        return self.stdout_lines[-1].decode("utf-8", "replace")

    @property
    def raw_stdout(self) -> str:
        return b"".join(self.stdout_lines).decode("utf-8", "replace")

    # -- teardown -----------------------------------------------------------

    def close(self) -> str:
        """Shut the server down and return everything it said on stderr."""
        if self._closed:
            return self.stderr_text
        self._closed = True
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
        except OSError:  # pragma: no cover
            pass
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            self.process.kill()
            self.process.wait(timeout=15)
        for thread in self._threads:
            thread.join(timeout=10)
        # Everything the server emitted on stdout, answered or not: the
        # protocol-cleanliness claim is about the whole stream, not just the
        # frames a test happened to read.
        while True:
            try:
                line = self._stdout_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                break
            self.stdout_lines.append(line)
        self.stderr_text = self._stderr_so_far()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:  # pragma: no cover
                    pass
        return self.stderr_text


def tool_payload(response: dict[str, Any], *, name: str = "tool") -> Any:
    """The structured result of a tools/call, whichever field carries it."""
    assert "error" not in response, f"{name} answered with a JSON-RPC error: {response}"
    result = response["result"]
    assert result.get("isError") is not True, f"{name} answered with isError: {result}"
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    blocks = [
        block["text"] for block in result.get("content", []) if block.get("type") == "text"
    ]
    assert blocks, f"no readable content in {name}'s result: {result}"
    return json.loads(blocks[0])


@pytest.fixture
def mcp_server():
    """Launch handshaken servers against sandboxes; kill them all at teardown."""
    clients: list[StdioMcpClient] = []

    def launch(
        box: Sandbox,
        *,
        client_name: str = CLIENT_NAME,
        client_version: str = CLIENT_VERSION,
        handshake: bool = True,
    ) -> StdioMcpClient:
        client = StdioMcpClient(box, client_name=client_name, client_version=client_version)
        clients.append(client)
        if handshake:
            client.handshake()
        return client

    yield launch

    for client in clients:
        try:
            client.close()
        finally:
            if client.process.poll() is None:  # pragma: no cover
                client.process.kill()


# --------------------------------------------------------------------------
# Bootstrap launcher (agent/scripts/setup.py) runner
# --------------------------------------------------------------------------


@dataclass
class BootstrapRepo:
    """A throwaway checkout skeleton the real bootstrap script can run inside."""

    repo_root: Path
    agent_dir: Path
    script: Path
    env_file: Path
    bogus_python: Path
    bogus_module: str


@pytest.fixture
def bootstrap_repo():
    """Copy the real bootstrap launcher into a sandbox and aim it at nothing.

    A copy, not the checkout itself: the script rewrites ``agent/.env`` beside
    itself, and this suite must not touch the developer's real one. The copy is
    the shipped file, so what is under test is the real implementation.
    """

    def make(box: Sandbox) -> BootstrapRepo:
        source = REPO_ROOT / "agent" / "scripts" / "setup.py"
        assert source.exists(), f"bootstrap launcher missing at {source}"
        repo_root = box.root / "repo"
        agent_dir = repo_root / "agent"
        (agent_dir / "scripts").mkdir(parents=True, exist_ok=True)
        script = agent_dir / "scripts" / "setup.py"
        shutil.copy2(source, script)
        (agent_dir / "pyproject.toml").write_text(
            '[project]\nname = "holdout-agent-skeleton"\nversion = "0"\n', encoding="utf-8"
        )
        bogus_python = box.root / "no-such-python-holdout.exe"
        bogus_module = "katagiri.holdout_no_such_module"
        env_file = agent_dir / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "PYTHONUTF8=1",
                    f"KATAGIRI_PYTHON={bogus_python}",
                    f"KATAGIRI_MODULE={bogus_module}",
                    f"KATAGIRI_CONFIG={box.config_path}",
                    "OBSIDIAN_TRANSPORT=streamable_http",
                    "OBSIDIAN_API_TOKEN=",
                    "OPENROUTER_API_KEY=",
                    "LANGSMITH_API_KEY=",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return BootstrapRepo(
            repo_root=repo_root,
            agent_dir=agent_dir,
            script=script,
            env_file=env_file,
            bogus_python=bogus_python,
            bogus_module=bogus_module,
        )

    return make


@pytest.fixture
def run_bootstrap():
    """Run the bootstrap launcher inside a sandbox checkout skeleton.

    ``uv`` is kept off the child's PATH deliberately: the dependency-sync phase
    is trusted third-party behavior (spec Assumptions) and running it here would
    cost minutes and a network. What this suite tests is the launcher's own
    phase reporting and its pre-exec failure record.
    """

    def run(
        box: Sandbox,
        repo: BootstrapRepo,
        *args: str,
        timeout: float = BOOTSTRAP_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = box.env(
            PATH=str(Path(system_root) / "System32"),
            KATAGIRI_PYTHON=str(repo.bogus_python),
            KATAGIRI_MODULE=repo.bogus_module,
        )
        try:
            return subprocess.run(
                [sys.executable, str(repo.script), *args],
                cwd=str(repo.repo_root),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            pytest.fail(
                f"bootstrap {args} did not finish within {timeout}s\n"
                f"--- stdout ---\n{stdout[-4000:]}\n--- stderr ---\n{stderr[-4000:]}"
            )

    return run


# --------------------------------------------------------------------------
# Shared assertions
# --------------------------------------------------------------------------


def assert_no_canary(blob: str, *, where: str) -> None:
    """No secret value, and no fragment of one, anywhere in ``blob`` (FR-011)."""
    assert CANARY_TOKEN not in blob, f"the obsidian_api_token leaked into {where}"
    for fragment in CANARY_FRAGMENTS:
        assert fragment not in blob, f"a fragment of the obsidian_api_token reached {where}"


def assert_no_traceback(blob: str, *, where: str) -> None:
    assert "Traceback (most recent call last)" not in blob, f"{where} carries a raw traceback"


def real_app_data_names() -> set[str] | None:
    """Names directly under the real ``%LOCALAPPDATA%\\Katagiri``, or None.

    None means the real directory does not exist on this machine -- itself a
    fact worth asserting is still true after a sandboxed run.
    """
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        return None
    real = Path(raw) / "Katagiri"
    if not real.is_dir():
        return None
    return {entry.name for entry in real.iterdir()}


def real_config_fingerprint() -> tuple[int, int] | None:
    """``(size, mtime_ns)`` of the real config.toml, or None when absent."""
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        return None
    real = Path(raw) / "Katagiri" / "config.toml"
    if not real.is_file():
        return None
    stat = real.stat()
    return (stat.st_size, stat.st_mtime_ns)


def elapsed_since(start: float) -> float:
    return time.monotonic() - start


@pytest.fixture(scope="session")
def holdout() -> SimpleNamespace:
    """Constants and helpers, handed to tests as a fixture rather than an import.

    Deliberate: ``from conftest import ...`` inside a test module resolves
    through ``sys.path``, where a second ``conftest.py`` (the normal suite's)
    can shadow this one when both directories are collected in one run. A
    fixture is unambiguous.
    """
    return SimpleNamespace(
        CANARY_TOKEN=CANARY_TOKEN,
        CANARY_FRAGMENTS=CANARY_FRAGMENTS,
        CLIENT_NAME=CLIENT_NAME,
        CLIENT_VERSION=CLIENT_VERSION,
        DIAGNOSTIC_BUDGET_SECONDS=DIAGNOSTIC_BUDGET_SECONDS,
        PROTOCOL_VERSION=PROTOCOL_VERSION,
        REPO_ROOT=REPO_ROOT,
        SANDBOX_MARKER=SANDBOX_MARKER,
        SERVER_OP_TIMEOUT=SERVER_OP_TIMEOUT,
        TOTAL_INSTALLER_STEPS=TOTAL_INSTALLER_STEPS,
        assert_no_canary=assert_no_canary,
        assert_no_traceback=assert_no_traceback,
        doctor_rows=doctor_rows,
        jmdict_template=jmdict_template,
        real_app_data_names=real_app_data_names,
        real_config_fingerprint=real_config_fingerprint,
        tool_payload=tool_payload,
    )
