"""Client + model layer for the katagiri-agent (T012).

This module is the one place that:

1. Builds the single :class:`MultiServerMCPClient` wiring both MCP servers
   (``katagiri`` over stdio, ``obsidian`` over whichever transport T001/T005
   settled) from the swappable connection config in
   :mod:`katagiri_agent.config`.
2. Discovers each server's full tool surface, then applies a **client-side
   allowlist** -- the *featured subset* -- before anything is bound to the
   model. Discovery itself is never narrowed: ``session.list_tools()``
   against the raw katagiri connection still returns all 26 registered
   tools (see ``src/katagiri/tool_registry.py``, read-only from this
   project), and no edit lands in that registry or in ``mcp_server.py``.
   Narrowing happens only in :func:`load_featured_tools`, after discovery,
   which is what research.md's "client-side allowlist... no registry edit
   and no server-side profile" decision means in code.
3. Builds the one pinned :class:`~langchain_openai.ChatOpenAI` model, talking
   to OpenRouter's OpenAI-compatible endpoint, and binds the featured tools
   to it.

**Featured-subset membership is fixed here** (research.md's "Open,
deliberately" list names this as T012's job) and is mirrored, not
re-decided, into T021's ``docs/assignment/tool-triage.md``.

**Pinned model**: see ``OPENROUTER_MODEL`` in ``agent/.env.example`` and the
"Client + model layer (T012)" section of ``agent/README.md`` for the exact
id and the reasoning. Nothing in this module makes a network call at import
time or at object-construction time: building the client and the model only
assembles configuration (connection dicts, an API key string, a model id
string). The first real network round trip happens when a caller actually
invokes a tool or the model -- exercised for real only by T017's smoke test
and T027's (user-side, funded-account) verification, never by importing
this module.
"""

from __future__ import annotations

from typing import Final

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

from katagiri_agent.config import AgentSettings, mcp_connections

# ---------------------------------------------------------------------------
# Featured subset -- fixed at T012, mirrored (not re-decided) at T021.
# ---------------------------------------------------------------------------

KATAGIRI_FEATURED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        # research.md's "substantive five" (the triage table's >=2-beyond-
        # retrieval bar): coverage, find_i_plus_one, gen_exercise,
        # build_sentences, triage_inbox.
        "coverage",
        "find_i_plus_one",
        "gen_exercise",
        "build_sentences",
        "triage_inbox",
        # research.md's primary-data-source tool over vendored JMdict.
        "lookup",
        # Session/logging tools the T013 diagnostic-branch graph actually
        # calls: read goal note -> start_session -> branch on action.kind ->
        # exercise / review / triage path -> grade -> log_lesson /
        # log_observations -> summary.
        "start_session",
        "log_lesson",
        "log_observations",
        # triage_inbox's note_envelope_id argument is untrusted-only
        # (session_tools' envelope rule: an inbox note is copied off a web
        # page or subtitle). The triage path cannot call triage_inbox at all
        # without staging and confirming that text first.
        "stage_untrusted",
        "confirm_untrusted",
    }
)
"""katagiri tools bound to the model: 11 of the 26 registered in
``tool_registry.TOOL_SPECS``. The other 15 (``ping``, ``known_word``,
``known_set_stats``, ``recent_events``, ``search_db``, ``stop_gate_status``,
``security_status``, ``vault_file``, ``vault_list``, ``obsidian_active_note``,
``search_notes``, ``lessons``, ``lesson_memory``, ``log_error``,
``add_vocab``) stay discoverable at the protocol level -- katagiri's own
Obsidian proxy tools among them, distinct from the *existing server*
connection below -- but are never handed to the model. T021's triage table
explains why each one is a helper rather than a second omission.
"""

OBSIDIAN_FEATURED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "vault_list",
        "vault_read",
    }
)
"""Read-only subset of the existing server's tool surface (16 tools on the
Streamable HTTP variant per T005's spike) bound to the model: exactly the
pair needed to find and read the demo vault's goal note (list, then read one
file by path) -- the same pair T005's ``spike_existing.py`` exercised. The
plugin's write-shaped tools (``vault_write``, ``vault_append``,
``vault_patch``, ``vault_move``, ``vault_delete``, ``vault_copy``,
``command_execute``, ...) are never bound to the model through this client,
independently of and in addition to the katagiri allowlist above -- the
existing server carries a write surface behind the same bearer token, and
the model has no legitimate reason to touch it for this flow.
"""


def build_mcp_client() -> MultiServerMCPClient:
    """The one ``MultiServerMCPClient`` wiring both MCP servers.

    Connections come from :func:`katagiri_agent.config.mcp_connections`,
    which is where the katagiri-stdio-only-vs-obsidian-swappable-transport
    decision lives. This function never branches on transport itself --
    that stays config.py's job so a transport swap changes configuration
    only.
    """
    return MultiServerMCPClient(mcp_connections())


async def load_featured_tools(client: MultiServerMCPClient) -> list[BaseTool]:
    """Discover both servers, then keep only the featured subset.

    Discovery is unrestricted: ``client.get_tools(server_name=...)`` calls
    each server's real ``list_tools()`` and sees its whole registered
    surface (26 for katagiri, 16 for obsidian on the Streamable HTTP
    variant). Filtering to :data:`KATAGIRI_FEATURED_TOOLS` and
    :data:`OBSIDIAN_FEATURED_TOOLS` happens only after that, client-side --
    this function is the one place that narrowing happens, so a reviewer
    checking "is this a server-side profile" only has to read here.

    Raises ``RuntimeError`` if a named tool goes missing from a server's
    discovery (a version drift on either server, or a typo in the allowlist
    above) rather than silently binding a smaller-than-intended subset to
    the model.
    """
    katagiri_tools = await client.get_tools(server_name="katagiri")
    obsidian_tools = await client.get_tools(server_name="obsidian")

    featured = [tool for tool in katagiri_tools if tool.name in KATAGIRI_FEATURED_TOOLS]
    featured += [tool for tool in obsidian_tools if tool.name in OBSIDIAN_FEATURED_TOOLS]

    found = {tool.name for tool in featured}
    missing_katagiri = KATAGIRI_FEATURED_TOOLS - found
    missing_obsidian = OBSIDIAN_FEATURED_TOOLS - found
    if missing_katagiri or missing_obsidian:
        raise RuntimeError(
            "Featured-subset tool(s) not found in server discovery -- "
            f"katagiri missing {sorted(missing_katagiri)}, "
            f"obsidian missing {sorted(missing_obsidian)}. Check the "
            "connected server versions against the featured-subset "
            "membership fixed in katagiri_agent.clients."
        )
    return featured


def build_model(settings: AgentSettings | None = None) -> BaseChatModel:
    """The one pinned OpenRouter chat model.

    Building this object makes no network call -- ``ChatOpenAI.__init__``
    only stores the base URL, the API key, and the model id string. Whether
    ``OPENROUTER_MODEL`` is actually reachable on the account behind
    ``OPENROUTER_API_KEY`` is verified for real only by T027 (user-side,
    funded-account check), never here.
    """
    settings = settings or AgentSettings.load()
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
    )


async def build_bound_model(
    client: MultiServerMCPClient | None = None,
    settings: AgentSettings | None = None,
) -> tuple[BaseChatModel, list[BaseTool]]:
    """Convenience: assemble both layers and bind the featured tools.

    Returns ``(bound_model, featured_tools)`` so a caller (T013's graph)
    can dispatch tool calls against the exact tool objects the model was
    bound to, rather than re-deriving them.
    """
    client = client or build_mcp_client()
    tools = await load_featured_tools(client)
    model = build_model(settings)
    return model.bind_tools(tools), tools


__all__ = [
    "KATAGIRI_FEATURED_TOOLS",
    "OBSIDIAN_FEATURED_TOOLS",
    "build_bound_model",
    "build_mcp_client",
    "build_model",
    "load_featured_tools",
]
