"""T027b: the CLI ``specs/005-mcp-assignment/quickstart.md`` already commits to.

Two invocation forms, matching quickstart.md's Step 2 and Step 4/6 exactly:

- ``python -m katagiri_agent --list-connections`` -- connect to both MCP
  servers, print each one's discovered tool names, exit.
- ``python -m katagiri_agent --goal-note "<vault path>"`` -- assemble the
  client, the featured tools, the pinned model, and the diagnostic-branch
  graph (:mod:`katagiri_agent.graph`), then run it once against that goal
  note path, checkpointed through :class:`katagiri_agent.checkpoint.
  AsyncBridgeSqliteSaver`.

Deliberately thin: this module owns argument parsing and wiring only. Every
real decision (featured-subset membership, connection config, the graph's
branch table, the resilience taxonomy) is made in the module that already
owns it -- :mod:`katagiri_agent.clients`, :mod:`katagiri_agent.config`,
:mod:`katagiri_agent.graph`, :mod:`katagiri_agent.checkpoint`.

Honest failure output: a :class:`katagiri_agent.config.ConfigError` (a
missing/blank ``agent/.env`` value) prints that error's own message --
which only ever names an environment variable, never a value, per
``config.py``'s own "no value is ever logged" convention -- and the process
exits non-zero. Any other real failure is reported the same way: one line
naming the exception type and message, never a raw traceback that might
carry incidental local state onto the recording.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Sequence

from katagiri_agent import checkpoint as checkpoint_module
from katagiri_agent import clients
from katagiri_agent import graph as graph_module
from katagiri_agent.config import AgentSettings, ConfigError

DEFAULT_CHECKPOINT_DB: Path = Path("agent") / ".checkpoints" / "katagiri_agent.sqlite"
DEFAULT_THREAD_ID = "katagiri-agent-cli"


def build_parser() -> argparse.ArgumentParser:
    """The one ``ArgumentParser`` both CLI modes share.

    ``--list-connections`` and ``--goal-note`` are mutually exclusive and
    one of them is required -- there is no third bare-invocation mode.
    """
    parser = argparse.ArgumentParser(
        prog="katagiri_agent",
        description=(
            "katagiri-agent CLI: discover both MCP connections, or run the "
            "diagnostic-branch graph once against a goal note."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--list-connections",
        action="store_true",
        help=(
            "Connect to both MCP servers (katagiri, obsidian), print each "
            "one's discovered tool names, then exit."
        ),
    )
    mode.add_argument(
        "--goal-note",
        metavar="PATH",
        help=(
            "Run the full graph once, reading this vault-relative path as "
            "the goal note (e.g. 'Goals.md')."
        ),
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Continue this session id instead of letting start_session begin a fresh one.",
    )
    parser.add_argument(
        "--tired",
        action="store_true",
        help="Pass tired=True into start_session (tired-mode minimum).",
    )
    parser.add_argument(
        "--checkpoint-db",
        default=str(DEFAULT_CHECKPOINT_DB),
        help=f"SqliteSaver checkpoint file for --goal-note runs (default: {DEFAULT_CHECKPOINT_DB}).",
    )
    parser.add_argument(
        "--thread-id",
        default=DEFAULT_THREAD_ID,
        help=f"Checkpoint thread id for --goal-note runs (default: {DEFAULT_THREAD_ID!r}).",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


async def run_list_connections(client: Any | None = None) -> int:
    """``--list-connections``: discover both servers, print tool names.

    Uses ``client.get_tools(server_name=...)`` directly -- the same
    discovery call :func:`katagiri_agent.clients.load_featured_tools` makes
    before narrowing -- so this mode reports each *server's* full surface,
    never the client-side featured-subset narrowing. ``client`` is
    injectable so a test can hand in a stub with no real MCP connection.
    """
    client = client if client is not None else clients.build_mcp_client()
    for server in ("katagiri", "obsidian"):
        tools = await client.get_tools(server_name=server)
        names = sorted(tool.name for tool in tools)
        print(f"[{server}] {len(names)} tool(s): {', '.join(names)}")
    return 0


async def run_goal_note(
    args: argparse.Namespace,
    *,
    client: Any | None = None,
    settings: AgentSettings | None = None,
) -> int:
    """``--goal-note PATH``: assemble everything and run :func:`build_graph` once.

    Wiring only, in the order the design already commits to:
    :func:`clients.build_mcp_client` -> :func:`clients.load_featured_tools`
    -> :func:`clients.build_model` -> :func:`graph.tools_by_name` ->
    :func:`graph.build_graph` (model attached, so :func:`graph.make_grade_node`
    / :func:`graph.make_summary_node` use it instead of the deterministic
    fallback) -> one ``ainvoke`` under a file-backed
    :class:`checkpoint.AsyncBridgeSqliteSaver`.
    """
    client = client if client is not None else clients.build_mcp_client()
    tools = await clients.load_featured_tools(client)
    settings = settings or AgentSettings.load()
    model = clients.build_model(settings)
    tools_by_name = graph_module.tools_by_name(tools)

    db_path = Path(args.checkpoint_db)
    with checkpoint_module.open_checkpointer(db_path) as saver:
        compiled = graph_module.build_graph(tools_by_name, model=model, checkpointer=saver)
        config = checkpoint_module.thread_config(args.thread_id)
        result = await compiled.ainvoke(
            {
                "session_id": args.session_id,
                "tired": args.tired,
                "goal_note_path": args.goal_note,
            },
            config,
        )
    print(result.get("summary") or "(no summary produced)")
    return 0


async def _amain(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_connections:
        return await run_list_connections()
    # argparse's required mutually-exclusive group guarantees exactly one of
    # --list-connections / --goal-note was given.
    return await run_goal_note(args)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(_amain(argv))
    except ConfigError as exc:
        # ConfigError's own message only ever names an unset/blank env var
        # (see config.py's _require_env) -- never a value -- so it is safe
        # to print verbatim.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- CLI boundary: report, don't traceback-dump.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "build_parser",
    "main",
    "parse_args",
    "run_goal_note",
    "run_list_connections",
]
