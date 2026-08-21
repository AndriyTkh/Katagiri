"""T027b: tests for ``katagiri_agent.__main__``, the CLI
``specs/005-mcp-assignment/quickstart.md`` already commits to.

Three things this file checks, matching the task's own ask:

1. Argument parsing -- the mutually exclusive ``--list-connections`` /
   ``--goal-note`` group, and the defaults on the rest of the flags.
2. ``--list-connections`` with a stubbed client -- no real MCP server, no
   network call, just proof the discovery/print loop is wired correctly.
3. Error exit on missing config -- ``ConfigError`` (never a raw traceback,
   never a secret) prints and the process exits non-zero.

No live network call anywhere here: every test either only touches
``argparse``, or hands ``run_list_connections`` a stub client whose
``get_tools`` is a plain async function over an in-memory list.
"""

from __future__ import annotations

import asyncio
import functools

import pytest

from katagiri_agent import __main__ as cli
from katagiri_agent.config import ConfigError


def run_async(fn):
    """Run an ``async def test_...`` synchronously via ``asyncio.run``.

    Same rationale and same shape as this project's other test files (no
    ``pytest-asyncio`` dependency) -- kept as its own copy per file by
    convention.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def test_no_mode_flag_is_an_error():
    # argparse's required mutually-exclusive group: bare invocation with
    # neither --list-connections nor --goal-note must not silently pick one.
    with pytest.raises(SystemExit):
        cli.parse_args([])


def test_both_mode_flags_together_is_an_error():
    with pytest.raises(SystemExit):
        cli.parse_args(["--list-connections", "--goal-note", "Goals.md"])


def test_parse_args_list_connections():
    args = cli.parse_args(["--list-connections"])
    assert args.list_connections is True
    assert args.goal_note is None


def test_parse_args_goal_note_defaults():
    args = cli.parse_args(["--goal-note", "Goals.md"])
    assert args.list_connections is False
    assert args.goal_note == "Goals.md"
    assert args.session_id is None
    assert args.tired is False
    assert args.checkpoint_db == str(cli.DEFAULT_CHECKPOINT_DB)
    assert args.thread_id == cli.DEFAULT_THREAD_ID


def test_parse_args_goal_note_overrides():
    args = cli.parse_args(
        [
            "--goal-note",
            "Goals.md",
            "--session-id",
            "sess-1",
            "--tired",
            "--checkpoint-db",
            "scratch.sqlite",
            "--thread-id",
            "thread-1",
        ]
    )
    assert args.session_id == "sess-1"
    assert args.tired is True
    assert args.checkpoint_db == "scratch.sqlite"
    assert args.thread_id == "thread-1"


# ---------------------------------------------------------------------------
# --list-connections, stubbed client -- no MCP server, no network call.
# ---------------------------------------------------------------------------


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubClient:
    """The one method ``run_list_connections`` needs: ``get_tools``."""

    def __init__(self, tools_by_server: dict[str, list[_StubTool]]) -> None:
        self._tools_by_server = tools_by_server
        self.requested_servers: list[str] = []

    async def get_tools(self, server_name: str) -> list[_StubTool]:
        self.requested_servers.append(server_name)
        return self._tools_by_server[server_name]


@run_async
async def test_run_list_connections_prints_both_servers(capsys):
    client = _StubClient(
        {
            "katagiri": [_StubTool("coverage"), _StubTool("lookup")],
            "obsidian": [_StubTool("vault_read"), _StubTool("vault_list")],
        }
    )

    code = await cli.run_list_connections(client=client)

    assert code == 0
    assert client.requested_servers == ["katagiri", "obsidian"]
    out = capsys.readouterr().out
    # Sorted, so the exact wording is deterministic regardless of dict order.
    assert "[katagiri] 2 tool(s): coverage, lookup" in out
    assert "[obsidian] 2 tool(s): vault_list, vault_read" in out


@run_async
async def test_run_list_connections_handles_empty_server():
    client = _StubClient({"katagiri": [], "obsidian": []})
    code = await cli.run_list_connections(client=client)
    assert code == 0


# ---------------------------------------------------------------------------
# Error exit on missing config -- honest failure, never a secret, never a
# raw traceback.
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_on_missing_config(monkeypatch, capsys):
    # This worktree ships no agent/.env (see agent/.env.example), so
    # KATAGIRI_PYTHON is naturally unset -- monkeypatch.delenv makes that
    # explicit and deterministic regardless of the machine running this.
    monkeypatch.delenv("KATAGIRI_PYTHON", raising=False)

    code = cli.main(["--goal-note", "Goals.md"])

    assert code == 2
    err = capsys.readouterr().err
    assert "KATAGIRI_PYTHON" in err
    # config.py's own convention: only the variable name is ever named, no
    # value -- there is no value to leak here, but this guards against a
    # future change accidentally printing one.
    assert "error:" in err


def test_config_error_message_never_traceback(monkeypatch, capsys):
    monkeypatch.delenv("KATAGIRI_PYTHON", raising=False)
    cli.main(["--goal-note", "Goals.md"])
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_main_list_connections_still_needs_config(monkeypatch, capsys):
    # --list-connections also builds a real client (config-dependent) when
    # no client is injected -- it is not exempt from the same honest-failure
    # path, since the point of this mode is to prove real connectivity.
    monkeypatch.delenv("KATAGIRI_PYTHON", raising=False)
    code = cli.main(["--list-connections"])
    assert code == 2


def test_main_reraises_unrelated_exceptions_as_generic_error(monkeypatch, capsys):
    async def _boom(argv=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_amain", _boom)
    code = cli.main(["--list-connections"])
    assert code == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err
    assert "boom" in err
