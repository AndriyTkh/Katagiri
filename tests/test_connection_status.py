r"""007 / D-46: ``connection_status`` answered over the real MCP wire.

This is a *wire* gate, not a unit test of :func:`katagiri.mcp_server.connection_report`
(the contract's own module has no test of its own — the logic function is a plain
dict-in dict-out call, exercised here only through the adapter that actually ships).
Each test spawns a fresh ``katagiri.mcp_server`` subprocess against its own sandboxed
``%LOCALAPPDATA%``, completes the MCP handshake, calls ``connection_status``, and
checks the payload and stderr the way a real client would see them.

The ``_StdioClient`` class and the ``_assert_clean`` idiom are mirrored from
``tests/test_abc_workflow.py`` rather than imported, matching every sibling gate
file in this suite: this file keeps meaning the same thing, and keeps working, if
that one is ever retired.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.mcp

PROTOCOL_VERSION = "2026-07-28"

#: Distinctive on purpose, like every sibling gate's canary: a substring
#: appearing anywhere collected is a leak and nothing else.
CANARY_TOKEN = "SECRET-CONNSTATUS-CANARY"

_SUBPROCESS_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# The smallest honest MCP client: newline-delimited JSON-RPC over a pipe.
# Mirrored from test_abc_workflow.py / test_bverify.py / test_cverify.py.
# ---------------------------------------------------------------------------


class _StdioClient:
    def __init__(self, app_data: Path, extra_env: dict[str, str] | None = None) -> None:
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(app_data)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        if extra_env:
            env.update(extra_env)
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
    response = client.call("tools/call", {"name": name, "arguments": arguments or {}})
    return _tool_payload(response)


def _assert_clean(payload: Any, raw: str, *, where: str) -> None:
    """No canary, and no traceback, in either the structure or the raw frame."""
    blob = json.dumps(payload, ensure_ascii=False)
    for haystack, what in ((blob, "the payload"), (raw, "the raw frame")):
        assert CANARY_TOKEN not in haystack, f"a secret value leaked into {what} of {where}"
    for marker in ("Traceback (most recent call last)", 'File "'):
        assert marker not in blob, f"{where} answered with a raw traceback"


def _start_and_handshake(
    app_data: Path,
    *,
    client_name: str = "kata-connstatus",
    client_version: str = "1",
    extra_env: dict[str, str] | None = None,
) -> _StdioClient:
    client = _StdioClient(app_data, extra_env=extra_env)
    initialized = client.call(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": client_version},
        },
    )
    assert "error" not in initialized, initialized
    result = initialized["result"]
    assert result["serverInfo"]["name"] == "katagiri"
    assert "tools" in result["capabilities"]
    client.notify("notifications/initialized")
    return client


def _finish(client: _StdioClient) -> str:
    """Close the client and return its full stderr, asserting it started cleanly."""
    stderr = client.close()
    assert "starting katagiri" in stderr, stderr[-2000:]
    assert "Traceback (most recent call last)" not in stderr, stderr[-4000:]
    return stderr


# ---------------------------------------------------------------------------
# Sandbox helpers
# ---------------------------------------------------------------------------


def _make_sandbox(tmp_path: Path, name: str = "AppData") -> Path:
    """A fresh ``%LOCALAPPDATA%`` with nothing under ``Katagiri`` yet."""
    app_data = tmp_path / name
    (app_data / "Katagiri").mkdir(parents=True)
    return app_data


def _write_config(app_data: Path, body: str) -> Path:
    config_path = app_data / "Katagiri" / "config.toml"
    config_path.write_text(body, encoding="utf-8")
    return config_path


# ---------------------------------------------------------------------------
# 1. Every contract field present and typed, on a bare sandbox
# ---------------------------------------------------------------------------


def test_every_contract_field_is_present_and_typed(tmp_path):
    app_data = _make_sandbox(tmp_path)
    client = _start_and_handshake(app_data)
    try:
        payload = _call(client, "connection_status")
        _assert_clean(payload, client.last_raw, where="connection_status")
    finally:
        _finish(client)

    expected_types: dict[str, type | tuple[type, ...]] = {
        "status": str,
        "katagiri_version": str,
        "python_version": str,
        "transport": str,
        "entry_point": str,
        "pid": int,
        "cwd": str,
        "data_home": str,
        "data_home_source": str,
        "config_path": str,
        "config_exists": bool,
        "db_path": str,
        "db_available": bool,
        "log_file_path": str,
        "client_info": dict,
        "secrets": dict,
        "changed_anything": bool,
    }
    for field, expected_type in expected_types.items():
        assert field in payload, f"connection_status dropped the '{field}' field"
        assert isinstance(payload[field], expected_type), (
            f"'{field}' is {type(payload[field]).__name__}, expected {expected_type}"
        )

    assert payload["status"] == "ok"
    assert payload["transport"] == "stdio"
    assert payload["data_home_source"] in ("default", "env")
    assert payload["changed_anything"] is False
    assert set(payload["client_info"]) == {"name", "version"}
    for field in ("name", "version"):
        assert isinstance(payload["client_info"][field], str)


# ---------------------------------------------------------------------------
# 2. Paths point inside the sandbox; a bare sandbox has no config or DB
# ---------------------------------------------------------------------------


def test_paths_point_inside_the_sandbox_and_db_is_unavailable(tmp_path):
    app_data = _make_sandbox(tmp_path)
    client = _start_and_handshake(app_data)
    try:
        payload = _call(client, "connection_status")
        _assert_clean(payload, client.last_raw, where="connection_status")
    finally:
        _finish(client)

    resolved_app_data = str(app_data.resolve())
    assert payload["data_home"].startswith(str(app_data)) or resolved_app_data in payload[
        "data_home"
    ], payload["data_home"]
    assert payload["config_path"].startswith(str(app_data))
    assert payload["db_path"].startswith(str(app_data))
    assert payload["log_file_path"].startswith(str(app_data))

    # No katagiri.db was ever written into this sandbox (only the server's own
    # startup default-config write touches config.toml), so the tool must
    # answer honestly rather than pretend a database is reachable.
    assert payload["db_available"] is False


# ---------------------------------------------------------------------------
# 3. A garbage (non-sqlite) file at db_path is reported unavailable, not raised
# ---------------------------------------------------------------------------


def test_garbage_database_file_reports_unavailable(tmp_path):
    app_data = _make_sandbox(tmp_path)
    db_path = app_data / "Katagiri" / "katagiri.db"
    db_path.write_bytes(b"not a sqlite database, just garbage bytes")
    _write_config(
        app_data,
        f'db_path = "{db_path.as_posix()}"\n',
    )

    client = _start_and_handshake(app_data)
    try:
        payload = _call(client, "connection_status")
        _assert_clean(payload, client.last_raw, where="connection_status")
    finally:
        _finish(client)

    assert payload["config_exists"] is True
    assert payload["db_path"] == str(Path(db_path.as_posix()))
    assert payload["db_available"] is False


# ---------------------------------------------------------------------------
# 4. KATAGIRI_CONFIG pointed at a nonexistent file — the plain logic function
# ---------------------------------------------------------------------------
#
# ``katagiri.mcp_server.connection_report`` (D-46's plain, MCP-context-free
# logic function behind the ``connection_status`` adapter) is unit-testable
# directly, which is the only way to observe "no config on disk yet" as
# ``connection_status`` itself would answer it: the real subprocess's own
# ``main()`` logs a startup line that calls ``database_path()`` -> ``get_config()``
# *before* the transport even comes up, and that eagerly writes a default
# ``config.toml`` (a normal, intentional first-run convenience — see
# ``config.write_default_config``) — so by the time a wire test could call the
# tool, the file already exists. Calling the logic function directly, without
# going through ``main()``, is what isolates the tool's own read-only claim
# (FR-006) from that unrelated startup side effect.


@pytest.fixture(autouse=True)
def _reset_config_cache_for_direct_calls():
    """Isolate ``connection_report``'s direct-call tests from ``get_config``'s cache.

    Only touches process state (an in-memory ``lru_cache``), not the sandboxed
    subprocess tests above, which never call into this process's ``config`` module.
    """
    from katagiri import config as config_mod

    config_mod.reset_config_cache()
    yield
    config_mod.reset_config_cache()


def test_connection_report_with_katagiri_config_at_a_nonexistent_file(tmp_path, monkeypatch):
    from katagiri import mcp_server

    missing_config = tmp_path / "elsewhere" / "config.toml"
    assert not missing_config.parent.exists()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    monkeypatch.setenv("KATAGIRI_CONFIG", str(missing_config))

    report = mcp_server.connection_report()

    assert report["config_path"] == str(missing_config)
    assert report["config_exists"] is False
    assert report["db_path"] == str(missing_config.parent / "katagiri.db")
    assert report["db_available"] is False
    assert report["changed_anything"] is False

    # Read-only means read-only: neither the config file nor its parent
    # directory (nor a database inside it) may have been created to answer.
    assert not missing_config.exists()
    assert not missing_config.parent.exists()


def test_connection_report_garbage_database_reports_unavailable(tmp_path, monkeypatch):
    from katagiri import mcp_server

    app_data = _make_sandbox(tmp_path)
    db_path = app_data / "Katagiri" / "katagiri.db"
    db_path.write_bytes(b"not a sqlite database, just garbage bytes")
    _write_config(app_data, f'db_path = "{db_path.as_posix()}"\n')
    monkeypatch.setenv("LOCALAPPDATA", str(app_data))
    monkeypatch.delenv("KATAGIRI_CONFIG", raising=False)

    report = mcp_server.connection_report()

    assert report["config_exists"] is True
    assert report["db_path"] == str(Path(db_path.as_posix()))
    assert report["db_available"] is False


def test_connection_report_secret_set_but_absent_from_the_serialized_payload(
    tmp_path, monkeypatch
):
    from katagiri import mcp_server

    app_data = _make_sandbox(tmp_path)
    _write_config(app_data, f'obsidian_api_token = "{CANARY_TOKEN}"\n')
    monkeypatch.setenv("LOCALAPPDATA", str(app_data))
    monkeypatch.delenv("KATAGIRI_CONFIG", raising=False)

    report = mcp_server.connection_report()

    assert report["secrets"]["obsidian_api_token"] == "set"
    assert report["secrets"]["mokuro_shared_secret"] == "unset"
    blob = json.dumps(report, ensure_ascii=False)
    assert CANARY_TOKEN not in blob, "the secret's value leaked into the serialized report"


# ---------------------------------------------------------------------------
# 5. A secret configured in config.toml is reported "set" but never surfaces
# ---------------------------------------------------------------------------


def test_configured_secret_is_reported_set_but_its_value_never_appears(tmp_path):
    app_data = _make_sandbox(tmp_path)
    _write_config(
        app_data,
        f'obsidian_api_token = "{CANARY_TOKEN}"\n',
    )

    client = _start_and_handshake(app_data)
    try:
        payload = _call(client, "connection_status")
        raw = client.last_raw
    finally:
        stderr = _finish(client)

    assert CANARY_TOKEN not in raw, "the canary secret leaked into the raw wire frame"
    assert CANARY_TOKEN not in stderr, "the canary secret leaked into stderr"
    blob = json.dumps(payload, ensure_ascii=False)
    assert CANARY_TOKEN not in blob, "the canary secret leaked into the structured payload"

    assert payload["secrets"]["obsidian_api_token"] == "set"
    assert payload["secrets"]["mokuro_shared_secret"] == "unset"


# ---------------------------------------------------------------------------
# 6. clientInfo round-trips through the handshake
# ---------------------------------------------------------------------------


def test_client_info_round_trips_from_the_handshake(tmp_path):
    app_data = _make_sandbox(tmp_path)
    client = _start_and_handshake(
        app_data, client_name="my-test-client", client_version="9.9.9"
    )
    try:
        payload = _call(client, "connection_status")
        _assert_clean(payload, client.last_raw, where="connection_status")
    finally:
        _finish(client)

    assert payload["client_info"] == {"name": "my-test-client", "version": "9.9.9"}


# ---------------------------------------------------------------------------
# 7. Two side-by-side sandboxes are distinguishable by their path maps
# ---------------------------------------------------------------------------


def test_two_sandboxes_report_different_path_maps(tmp_path):
    app_data_a = _make_sandbox(tmp_path, name="AppDataA")
    app_data_b = _make_sandbox(tmp_path, name="AppDataB")

    client_a = _start_and_handshake(app_data_a)
    try:
        payload_a = _call(client_a, "connection_status")
        _assert_clean(payload_a, client_a.last_raw, where="connection_status (a)")
    finally:
        _finish(client_a)

    client_b = _start_and_handshake(app_data_b)
    try:
        payload_b = _call(client_b, "connection_status")
        _assert_clean(payload_b, client_b.last_raw, where="connection_status (b)")
    finally:
        _finish(client_b)

    assert payload_a["data_home"] != payload_b["data_home"]
    assert payload_a["config_path"] != payload_b["config_path"]
    assert payload_a["db_path"] != payload_b["db_path"]
    assert payload_a["log_file_path"] != payload_b["log_file_path"]
    # Different OS processes: pids need not differ in theory (a reused pid
    # after a process exits is possible), but the resolved data homes always
    # must, which is the FR-016 guarantee this test exists to pin.
