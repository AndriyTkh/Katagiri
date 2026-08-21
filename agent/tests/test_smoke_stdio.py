"""T017: integration smoke test -- spawn the REAL katagiri MCP server over
stdio via ``MultiServerMCPClient``, and check the two halves T004's day-1
spike showed can fail *independently*:

1. ``list_tools()`` -- the featured-subset tools :mod:`katagiri_agent.clients`
   binds to the model are actually present on the server's discovery
   surface.
2. a real tool **call** round-trips -- discovery succeeding is no proof of
   this (T004's spike docstring: "Connection closed" can surface on the
   call, after ``list_tools()`` already succeeded).

Each half is its own test function on purpose, so a failure in one never
hides a pass or fail in the other.

CRITICAL ISOLATION -- this test must never touch the real learner profile
(the personal DB, the personal vault, the personal Obsidian token):

- ``KATAGIRI_CONFIG`` is pointed at a throwaway ``config.toml`` written
  under ``tmp_path`` (same key shape as
  ``tests/demo_fixtures/demo-config.toml.example``: ``vault_path`` +
  ``db_path`` + ``scratch_root``, ``obsidian_api_token`` left unset), never
  at ``%LOCALAPPDATA%\\Katagiri``.
- ``LOCALAPPDATA`` is *also* overridden, in the spawned subprocess's own
  env only, to a second scratch directory. ``KATAGIRI_CONFIG`` only
  redirects where ``config.toml`` is read from
  (``katagiri.config.config_path()``); the shared log file's directory
  (``katagiri.applog.logs_dir()``) is derived from ``config_dir()``, which
  always resolves from ``LOCALAPPDATA`` and does **not** honor
  ``KATAGIRI_CONFIG`` -- so logging still needs its own override to stay
  off the real ``logs/`` tree.
- The round-tripped call is ``ping``: the one katagiri tool that opens no
  database connection and reads no configuration at all (see
  ``src/katagiri/mcp_server.py``'s ``ping()``: "No side effects, no I/O.").
  It is registered on the server even though it is not in
  :data:`katagiri_agent.clients.KATAGIRI_FEATURED_TOOLS` -- picked
  specifically because this test's isolation guarantee then does not even
  depend on the scratch config being well-formed; the scratch
  config/LOCALAPPDATA plumbing above is defense in depth for anything a
  future call here might touch.

Never let this subprocess default to ``%LOCALAPPDATA%\\Katagiri``.
"""

from __future__ import annotations

import asyncio
import functools
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from katagiri_agent import config as agent_config
from katagiri_agent.clients import KATAGIRI_FEATURED_TOOLS


def run_async(fn):
    """Run an ``async def test_...`` synchronously via ``asyncio.run``.

    No ``pytest-asyncio`` dependency in this project (a separate uv
    subproject on purpose -- see ``katagiri_agent.clients``'s module
    docstring), so this is the whole bridge needed instead. Mirrors the
    same helper ``test_resilience.py`` defines for the same reason; kept as
    its own copy here rather than imported, so this file has no import-time
    dependency on a sibling test module still being written in parallel.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _write_scratch_katagiri_config(tmp_path: Path) -> Path:
    """A throwaway ``config.toml``, same key shape as the demo template.

    Mirrors ``tests/demo_fixtures/demo-config.toml.example`` (``vault_path``
    + ``db_path`` + ``scratch_root``, ``obsidian_api_token`` unset) but
    written fresh under ``tmp_path`` so this test never depends on
    ``scripts/build_demo_db.py`` having been run on the machine executing
    it.
    """
    root = tmp_path / "scratch_katagiri"
    db_path = root / "katagiri.db"
    vault_path = root / "vault"
    scratch_root = root / "scratch"
    vault_path.mkdir(parents=True, exist_ok=True)
    scratch_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    config_path = root / "config.toml"
    config_path.write_text(
        f'vault_path = "{vault_path.as_posix()}"\n'
        f'db_path = "{db_path.as_posix()}"\n'
        f'scratch_root = "{scratch_root.as_posix()}"\n'
        "# obsidian_api_token intentionally left unset -- this scratch "
        "profile must never carry a usable credential.\n",
        encoding="utf-8",
    )
    return config_path


@pytest.fixture
def isolated_katagiri_connection(tmp_path, monkeypatch) -> dict[str, Any]:
    """The katagiri stdio connection dict, wired for a fully isolated run.

    Starts from :func:`katagiri_agent.config.katagiri_connection` -- T012's
    own connection builder, the one production code actually uses -- with
    ``KATAGIRI_CONFIG`` monkeypatched to the scratch config above, so this
    test exercises the real recipe rather than a hand-rolled one. That
    function only ever forwards ``PYTHONUTF8`` and (when set)
    ``KATAGIRI_CONFIG`` into the subprocess env (see its docstring), so
    ``LOCALAPPDATA`` is layered on afterward, here, for the logging-directory
    reason explained in this module's docstring.

    Skips (rather than fails) when ``KATAGIRI_PYTHON`` is not configured in
    ``agent/.env`` -- this test spawns a real subprocess and cannot run at
    all without that interpreter path, distinct from a real assertion
    failure.
    """
    config_path = _write_scratch_katagiri_config(tmp_path)
    scratch_localappdata = tmp_path / "scratch_localappdata"
    scratch_localappdata.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KATAGIRI_CONFIG", str(config_path))

    try:
        connection = agent_config.katagiri_connection()
    except agent_config.ConfigError as exc:
        pytest.skip(f"katagiri stdio connection not configured: {exc}")

    env = dict(connection.get("env") or {})
    env["LOCALAPPDATA"] = str(scratch_localappdata)
    connection = dict(connection)
    connection["env"] = env
    return connection


def _first_structured(call_result: Any) -> dict[str, Any] | None:
    """Pull a plain dict out of a ``CallToolResult``, structured or not.

    Same tolerance as ``agent/scripts/spike_stdio_call.py``'s helper of the
    same name (the day-1 spike this task's docstring points at) -- copied
    rather than imported, since that file is a standalone script, not an
    importable module.
    """
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


@run_async
async def test_featured_tools_are_listed(isolated_katagiri_connection):
    """Half 1: every katagiri tool in the featured subset is discoverable.

    Discovery (``list_tools()``) succeeding is the half that can pass even
    when the call path is broken (T004's spike found exactly that failure
    mode on Windows) -- this test only ever exercises discovery, never a
    call, so a regression here is never masked by half 2 passing.
    """
    client = MultiServerMCPClient({"katagiri": isolated_katagiri_connection})

    async with client.session("katagiri") as session:
        result = await session.list_tools()

    tool_names = {tool.name for tool in result.tools}
    missing = KATAGIRI_FEATURED_TOOLS - tool_names
    assert not missing, (
        f"featured katagiri tool(s) missing from server discovery: "
        f"{sorted(missing)} (discovered {len(tool_names)} tools total: "
        f"{sorted(tool_names)})"
    )


@run_async
async def test_ping_round_trips(isolated_katagiri_connection):
    """Half 2: a real tool CALL round-trips over the spawned stdio pipe.

    ``ping`` is read-only and touches no database or configuration (see
    this module's docstring for why it is the safe choice here). A
    well-shaped ``{"status": "ok", "katagiri_version": ..., "python": ...}``
    coming back is checked on shape, matching how ``agent/scripts/
    spike_stdio_call.py`` verified the same call path -- this test is that
    spike, pinned down as a permanent regression check.
    """
    client = MultiServerMCPClient({"katagiri": isolated_katagiri_connection})

    async with client.session("katagiri") as session:
        call_result = await session.call_tool("ping", {})

    assert not call_result.isError, f"ping call reported an error: {call_result!r}"
    payload = _first_structured(call_result)
    assert payload is not None, f"ping call returned no readable payload: {call_result!r}"
    assert payload.get("status") == "ok"
    assert "katagiri_version" in payload
    assert "python" in payload
