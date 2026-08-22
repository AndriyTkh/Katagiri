"""Optional LangGraph Studio chat wrapper -- NOT part of the graded 005 flow.

``graph.py``'s diagnostic-branch graph deliberately has no ``messages`` state
-- the branch is picked by server data returned from ``start_session``, never
by model free-choice (FR-003, spec.md US2). LangGraph Studio only renders its
chat-bubble UI for a messages-shaped state, so that graph shows up there as a
step/state inspector, not a conversation window.

This module is a separate, ordinary tool-calling ReAct loop over the same
featured MCP tools and pinned OpenRouter model ``clients.py`` already builds
(:func:`katagiri_agent.clients.load_featured_tools`,
:func:`katagiri_agent.clients.build_model`) -- for interactive, free-form
chat against those tools in Studio. It is never imported by ``__main__.py``,
``graph.py``, or any other graded code path; ``langgraph.json`` is the only
thing that references it.
"""

from __future__ import annotations

import os

from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from katagiri_agent import clients, obsidian_bootstrap


async def make_graph() -> CompiledStateGraph:
    """Graph factory `langgraph dev` calls to build the ``chat`` graph.

    Discovery and featured-subset narrowing are unchanged from the graded
    flow (same allowlist, same two servers) -- only the control flow differs:
    here the model itself picks which tool to call, each turn, from the
    running message history.

    Two dev-convenience checks run first, both fast-fail with one clear
    line instead of a network-layer traceback surfacing minutes later:
    ``OPENROUTER_API_KEY`` presence, and (only for the ``streamable_http``
    transport) the Obsidian Local REST API endpoint actually answering --
    auto-launching Obsidian and waiting for it first, since forgetting to
    start the app is the recurring failure here, not a config mistake.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Run `python agent/scripts/setup.py` "
            "to be prompted for it (never edit agent/.env by hand)."
        )
    if os.environ.get("OBSIDIAN_TRANSPORT", "streamable_http").strip().lower() == "streamable_http":
        obsidian_bootstrap.ensure_ready(
            os.environ.get("OBSIDIAN_MCP_URL", "https://127.0.0.1:27124/mcp/")
        )

    client = clients.build_mcp_client()
    tools = await clients.load_featured_tools(client)
    model = clients.build_model()
    return create_react_agent(model, tools)


__all__ = ["make_graph"]
