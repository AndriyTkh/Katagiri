"""T005 spike: reach the EXISTING server (Obsidian Local REST API / MCP)
independently of the graph, complete discovery, and call one tool
successfully -- a read-only note read.

This exercises the Obsidian side of ``katagiri_agent.config`` in isolation
(no katagiri process, no LangGraph), the same way T004 exercised the
katagiri side. It targets whichever variant ``OBSIDIAN_TRANSPORT`` in
``agent/.env`` currently points at (see config.py's docstring and
research.md's "Open, deliberately" entry): as configured today that is the
plugin's built-in Streamable HTTP ``/mcp/`` endpoint (v5+), self-signed
cert, bearer token.

READ-ONLY, personal vault. This script:

1. Lists tools (discovery).
2. Calls exactly one read-only tool to enumerate vault files (a GET-shaped
   list), then reads exactly one note by path.

It never calls a write/PUT/POST-mutating tool (nothing that creates,
appends, patches, or deletes vault content). No token value and no note
content is ever printed -- only presence/absence, counts, and a one-line
receipt (path + byte count) for the note read.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from katagiri_agent.config import obsidian_connection  # noqa: E402

# Heuristic allowlist: tool names that are unambiguously read-only (list /
# get-shaped). Deliberately does NOT match names containing put/post/patch/
# delete/create/append/update/write/search (search can be read-only in
# principle, but is excluded here to keep the allowlist conservative -- this
# spike only needs "list" and "read one note").
# Preferred exact names first (this plugin's actual tool surface, confirmed
# by discovery: vault_list / vault_read / vault_get_document_map / tag_list /
# search_* / active_file_get_path / open_file / command_* / vault_write /
# vault_append / vault_patch / vault_move / vault_delete). Generic hints are
# a fallback in case the exact names differ on another install.
_SAFE_LIST_PREFERRED = ("vault_list",)
_SAFE_READ_PREFERRED = ("vault_read",)
_SAFE_LIST_HINTS = ("vault_list", "list", "get_active", "vault_structure")
_SAFE_READ_HINTS = ("vault_read", "get_file", "read_file", "get_note", "file_contents")
_UNSAFE_HINTS = (
    "put", "post", "patch", "delete", "create", "append", "update", "write",
    "move", "rename", "execute", "command",
)


def _is_unsafe(name: str) -> bool:
    lname = name.lower()
    return any(h in lname for h in _UNSAFE_HINTS)


def _first_structured(call_result) -> dict | list | None:
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, (dict, list)):
        return structured
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text  # plain text content block (e.g. markdown body)
    return None


def _fallback_note() -> str:
    return (
        "Documented fallback: switch OBSIDIAN_TRANSPORT to 'stdio' in "
        "agent/.env and point OBSIDIAN_STDIO_COMMAND / OBSIDIAN_STDIO_ARGS "
        "at a stdio wrapper for the Local REST API plugin (e.g. an MCP "
        "stdio<->HTTP bridge). No graph code changes either way -- "
        "config.py's obsidian_connection() is the only place that branches "
        "on transport. If the Streamable HTTP path fails on TLS, check "
        "OBSIDIAN_VERIFY_TLS (must be explicit, never silently false for a "
        "personal vault) and OBSIDIAN_CA_BUNDLE as the non-'verify=False' "
        "alternative."
    )


async def _run() -> dict:
    result: dict = {
        "verdict": None,
        "list_tools_ok": False,
        "tool_count": 0,
        "list_call_ok": False,
        "read_call_ok": False,
        "read_receipt": None,
        "error": None,
    }

    client = MultiServerMCPClient({"obsidian": obsidian_connection()})

    async with client.session("obsidian") as session:
        list_result = await session.list_tools()
        tool_names = sorted(t.name for t in list_result.tools)
        result["tool_count"] = len(tool_names)
        result["list_tools_ok"] = len(tool_names) > 0
        print(f"discovery: {len(tool_names)} tools -> {tool_names}")

        unsafe_present = [n for n in tool_names if _is_unsafe(n)]
        if unsafe_present:
            print(f"note: {len(unsafe_present)} write-shaped tool(s) present "
                  f"and will NOT be called: {unsafe_present}")

        list_tool = next(
            (n for n in tool_names if n in _SAFE_LIST_PREFERRED),
            None,
        ) or next(
            (n for n in tool_names if any(h in n.lower() for h in _SAFE_LIST_HINTS)
             and not _is_unsafe(n)),
            None,
        )
        if list_tool is None:
            raise RuntimeError(
                f"No read-only vault-listing tool found among: {tool_names}"
            )

        list_call = await session.call_tool(list_tool, {})
        files = _first_structured(list_call)
        result["list_call_ok"] = not list_call.isError and bool(files)
        print(f"list call -> tool={list_tool!r} is_error={list_call.isError} "
              f"ok={result['list_call_ok']}")

        # Normalize to a flat list of path-like strings, however the server
        # shapes its listing response.
        candidates: list[str] = []
        if isinstance(files, dict):
            for key in ("files", "notes", "items"):
                if isinstance(files.get(key), list):
                    candidates = [str(f) for f in files[key]]
                    break
        elif isinstance(files, list):
            candidates = [str(f) for f in files]

        note_path = next((p for p in candidates if p.endswith(".md")), None)
        if note_path is None and candidates:
            note_path = candidates[0]
        if note_path is None:
            raise RuntimeError("Vault listing returned no readable entries.")

        read_tool = next(
            (n for n in tool_names if n in _SAFE_READ_PREFERRED),
            None,
        ) or next(
            (n for n in tool_names if any(h in n.lower() for h in _SAFE_READ_HINTS)
             and not _is_unsafe(n)),
            None,
        )
        if read_tool is None:
            raise RuntimeError(
                f"No read-only file-read tool found among: {tool_names}"
            )

        # Try the common arg-name spellings; report which one worked.
        read_call = None
        last_exc: Exception | None = None
        for arg_name in ("filepath", "path", "file", "filename"):
            try:
                read_call = await session.call_tool(read_tool, {arg_name: note_path})
                if not read_call.isError:
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
        if read_call is None:
            raise last_exc or RuntimeError("Read call never executed.")

        content = _first_structured(read_call)
        text_len = None
        if isinstance(content, str):
            text_len = len(content.encode("utf-8"))
        elif isinstance(content, dict):
            body = content.get("content") or content.get("text") or ""
            text_len = len(str(body).encode("utf-8"))
        result["read_call_ok"] = not read_call.isError and text_len is not None
        result["read_receipt"] = f"{note_path} ({text_len} bytes)" if text_len is not None else None
        print(f"read call -> tool={read_tool!r} is_error={read_call.isError} "
              f"receipt={result['read_receipt']} ok={result['read_call_ok']}")

    result["verdict"] = "GREEN" if (
        result["list_tools_ok"] and result["list_call_ok"] and result["read_call_ok"]
    ) else "RED"
    return result


def main() -> int:
    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - diagnostic spike
        tb = traceback.format_exc()
        print("=" * 70)
        print(f"SPIKE RESULT: RED -- {type(exc).__name__}: {exc}")
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
