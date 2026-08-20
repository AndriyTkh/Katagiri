"""T004 day-1 spike: a REAL tool CALL over stdio on Windows, through
``MultiServerMCPClient``, against the katagiri server.

This is deliberately more than ``list_tools()``. Open bug reports exist for
exactly this path on Windows:

- ``NotImplementedError`` from asyncio's subprocess machinery when the event
  loop policy resolves to ``SelectorEventLoop`` instead of
  ``ProactorEventLoop`` (only Proactor supports subprocess pipes on Windows).
- "Connection closed" surfacing on the *call*, after ``list_tools()`` already
  succeeded -- i.e. discovery working is not proof the call path works.

The spike:

1. Spawns katagiri over stdio, using the connection config from
   ``katagiri_agent.config`` (same config the graph will use later).
2. Lists tools and asserts the two read-only tools this spike calls
   (``ping``, ``lookup``) are present.
3. Calls ``ping`` (no args) and asserts the round-tripped ``status: ok``.
4. Calls ``lookup`` with a real surface form and asserts a well-shaped
   response (``found`` key present) -- still read-only, still safe against
   the real local DB.

Nothing here calls a write tool (``start_session``, ``log_*``, ``add_vocab``,
``stage_untrusted``, etc.) -- never do that from this script.

No secret value is ever printed: only presence/absence and shapes.

Run with ``PYTHONUTF8=1`` set (Windows console default codepage corrupts
Japanese text otherwise):

    $env:PYTHONUTF8 = "1"
    uv run python scripts/spike_stdio_call.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

# Make the package importable when run as a plain script (uv run python
# scripts/spike_stdio_call.py), without requiring an editable install step.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from katagiri_agent.config import katagiri_connection  # noqa: E402

KNOWN_FAILURE_HINTS = (
    "NotImplementedError",
    "SelectorEventLoop",
    "Connection closed",
)


def _fallback_note() -> str:
    return (
        "Documented fallback (research.md 'Day-1 spike'): attach to a "
        "manually started katagiri process instead of letting "
        "MultiServerMCPClient spawn it. Start katagiri by hand in one "
        "terminal:\n"
        "    $env:PYTHONUTF8 = \"1\"\n"
        "    C:\\ProjectsC\\RandomPr\\Katagiri\\.venv\\Scripts\\python.exe "
        "-m katagiri.mcp_server\n"
        "then connect the agent to that already-running process instead of "
        "spawning it as a subprocess. langchain-mcp-adapters' stdio "
        "transport only supports spawn-and-own, not attach-to-existing, so "
        "this fallback needs either (a) a raw mcp.client.stdio session "
        "opened against a pipe/socket bridge to the running process, or "
        "(b) switching katagiri's own launch to be driven directly by the "
        "graph's own subprocess call outside MultiServerMCPClient's pool "
        "management. Record whichever is chosen in TG-C's transport plan."
    )


async def _run() -> dict:
    result: dict = {
        "verdict": None,
        "list_tools_ok": False,
        "ping_ok": False,
        "lookup_ok": False,
        "tool_names": [],
        "error": None,
        "error_type": None,
    }

    client = MultiServerMCPClient({"katagiri": katagiri_connection()})

    async with client.session("katagiri") as session:
        list_result = await session.list_tools()
        tool_names = sorted(t.name for t in list_result.tools)
        result["tool_names"] = tool_names
        result["list_tools_ok"] = "ping" in tool_names and "lookup" in tool_names
        print(f"list_tools: {len(tool_names)} tools, ping present, lookup present "
              f"-> {result['list_tools_ok']}")

        ping_result = await session.call_tool("ping", {})
        ping_payload = _first_structured(ping_result)
        result["ping_ok"] = bool(ping_payload) and ping_payload.get("status") == "ok"
        print(f"ping call -> is_error={ping_result.isError} "
              f"status={ping_payload.get('status') if ping_payload else '?'} "
              f"katagiri_version={ping_payload.get('katagiri_version') if ping_payload else '?'} "
              f"ok={result['ping_ok']}")

        lookup_result = await session.call_tool("lookup", {"surface": "猫"})
        lookup_payload = _first_structured(lookup_result)
        result["lookup_ok"] = bool(lookup_payload) and "found" in lookup_payload
        print(f"lookup call -> is_error={lookup_result.isError} "
              f"found={lookup_payload.get('found') if lookup_payload else '?'} "
              f"ok={result['lookup_ok']}")

    result["verdict"] = "GREEN" if (
        result["list_tools_ok"] and result["ping_ok"] and result["lookup_ok"]
    ) else "RED"
    return result


def _first_structured(call_result) -> dict | None:
    """Pull a plain dict out of a CallToolResult, structured or not."""
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


def main() -> int:
    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic spike
        tb = traceback.format_exc()
        hit = next((h for h in KNOWN_FAILURE_HINTS if h in tb), None)
        print("=" * 70)
        print(f"SPIKE RESULT: RED -- {type(exc).__name__}: {exc}")
        if hit:
            print(f"Matches known Windows failure surface: {hit!r}")
        print("-" * 70)
        print(tb)
        print("-" * 70)
        print(_fallback_note())
        print("=" * 70)
        return 1

    print("=" * 70)
    print(f"SPIKE RESULT: {result['verdict']}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["verdict"] != "GREEN":
        print(_fallback_note())
    print("=" * 70)
    return 0 if result["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
