# katagiri-agent

LangGraph homework agent for the 005 MCP assignment. This is a **separate uv
project** from the root `katagiri` package on purpose: it keeps katagiri's tiny
dependency set free of the LangChain/LangGraph tree, and makes the "the custom
server is process-separated, unmodified" claim mechanically checkable
(`git diff --stat src/katagiri/` stays empty except the one additive config
override).

It talks to two MCP servers:

- **katagiri**, over stdio — the custom server built for this course, unchanged.
- **Obsidian Local REST API** (`coddingtonbear/obsidian-local-rest-api`), over
  whichever transport the course's install uses — kept swappable (see
  `src/katagiri_agent/config.py`).

## Setup

```powershell
cd agent
uv sync                 # creates agent/.venv, installs deps (Python 3.12)
copy .env.example .env  # fill in real values; .env is gitignored
```

`PYTHONUTF8=1` must be set for every subprocess this project spawns (Windows
console codepage is cp1252 by default, and both MCP servers speak Japanese
text). It is set two places: in `.env` (loaded by `python-dotenv` for the
agent process itself) and in the `env` dict handed to katagiri's stdio
`StdioConnection` in `config.py` (the subprocess katagiri spawns does not
inherit `.env` unless it is passed through explicitly).

## Connection configuration

`src/katagiri_agent/config.py` builds the `MultiServerMCPClient` connection
dict for both servers. The katagiri connection is fixed at stdio (that is the
only transport katagiri's server supports). The Obsidian connection is
**swappable between stdio and Streamable HTTP** via one env var
(`OBSIDIAN_TRANSPORT`), because which variant the course's install actually
exposes is still an open question (see `specs/005-mcp-assignment/research.md`,
"Open, deliberately"). Swapping it is a config change only — no graph code
references transport details.

For the Streamable HTTP case, the Obsidian Local REST API plugin serves a
**self-signed certificate**. `config.py` exposes an `httpx_client_factory`
(passed through to `langchain_mcp_adapters`'s `StreamableHttpConnection`) that
builds an `httpx.AsyncClient` with `verify=False` when
`OBSIDIAN_VERIFY_TLS=false` — never as a silent default.

## Dependency intersection verdict (T003)

Recorded from the `uv sync` resolution in `agent/` (its own venv, separate
from katagiri's), run 2026-08-20 on Python 3.12.13, `uv 0.11.21`:

| Package | Resolved version |
|---|---|
| `mcp` | `1.29.0` |
| `langchain-mcp-adapters` | `0.3.2` |
| `langgraph` | `1.2.11` |
| `langgraph-checkpoint-sqlite` | `3.1.1` |
| `langchain-openai` | `1.6.0` |

**Verdict: RED at the PyPI-metadata level — no version intersection exists.**

- katagiri (primary checkout venv) requires `mcp>=2,<3` and has `mcp==2.0.0`
  installed.
- `langchain-mcp-adapters` 0.3.2 (the latest release on PyPI as of
  2026-08-20) declares `Requires-Dist: mcp<2.0.0,>=1.24.0` — it resolves to
  `mcp==1.29.0` and **explicitly excludes** `mcp>=2.0.0`.
- These ranges (`[1.24.0, 2.0.0)` vs `[2.0.0, 3.0.0)`) are disjoint. No
  released version of `langchain-mcp-adapters` supports `mcp>=2`: checking
  PyPI history, `0.3.0` and earlier declared `mcp>=1.9.2` with **no** upper
  bound (so an old `langchain-mcp-adapters` would happily let `uv` resolve
  `mcp` up to `2.0.0` today), but `0.3.1`/`0.3.2` pin a floor of `1.24.0`, and
  `0.3.2` (released 2026-08-06, **after** `mcp==2.0.0` shipped on
  2026-07-28) added the `<2.0.0` ceiling on top of that floor — i.e. the
  maintainers deliberately excluded `mcp` 2.x once it existed, not by
  oversight.
- Per instructions, katagiri's `mcp>=2,<3` pin is **not** touched to chase
  this — the two projects are separate uv subprojects with separate venvs
  specifically so this kind of conflict cannot force a compromise on
  katagiri's side.

**This is reported, not silently worked around**, per T003's instruction.
Because `agent/` and katagiri run in separate processes and separate
venvs, a disjoint *Python package* version range does not by itself prove
the *wire protocol* (JSON-RPC over stdio) is incompatible between an
`mcp==1.29.0` client and an `mcp==2.0.0` server — that is exactly what
T004's spike (below) exists to test empirically, before any graph code is
written. See the spike result for whether this package-level mismatch
manifests as an actual failure.

## Day-1 spike (T004)

`scripts/spike_stdio_call.py` spawns the katagiri server from the **primary
checkout's venv** (`C:\ProjectsC\RandomPr\Katagiri\.venv\Scripts\python.exe -m
katagiri.mcp_server`) via `MultiServerMCPClient`, lists tools, then makes
**two** real tool **calls** — `ping` (no args) and `lookup` (a real surface
form, `"猫"`) — not just `list_tools()`. This probes the known Windows
failure surface (`NotImplementedError` / `SelectorEventLoop` on the
subprocess transport, "Connection closed" on call) before any graph code is
written. Both calls are read-only against the real local DB, which is
acceptable for this spike; no write tool is ever invoked.

Run it with:

```powershell
$env:PYTHONUTF8 = "1"
cd agent
uv run python scripts/spike_stdio_call.py
```

**Result: GREEN.** Run 2026-08-20, Windows 11, Python 3.12.13 (agent venv,
`mcp==1.29.0` via `langchain-mcp-adapters==0.3.2`) spawning katagiri's
Python 3.12.13 / `mcp==2.0.0` server process:

- `list_tools()` returned all 26 registered tools, including `ping` and
  `lookup`.
- `call_tool("ping", {})` round-tripped `{"status": "ok", "katagiri_version":
  "0.1.0", "python": "3.12.13"}`.
- `call_tool("lookup", {"surface": "猫"})` round-tripped a well-shaped
  dictionary-lookup response (`found` key present, populated senses) —
  confirming Japanese text survives the stdio JSON-RPC round trip under
  `PYTHONUTF8=1`.
- Neither known failure symptom (`NotImplementedError` / `SelectorEventLoop`,
  "Connection closed") appeared.

**This means the T003 package-level version mismatch (client `mcp==1.29.0`
via `langchain-mcp-adapters`, server `mcp==2.0.0`) does not manifest as a
wire-protocol failure in practice** — at least not for `list_tools` +
these two simple, argument-light calls on Windows stdio. The JSON-RPC/MCP
protocol layer both versions speak is evidently compatible enough for this
workload. This is *not* a blanket guarantee for every tool or every payload
shape in the 26-tool surface; TG-C's integration smoke test (T017) and the
graph's own tool calls remain the ongoing check. No fallback (attach to a
manually started katagiri process) is needed at this time; the fallback
recipe is kept in the script (`_fallback_note()`) and below in case a later
task's call pattern regresses this.

**Fallback, if a later call regresses this (documented, not currently
needed):** start katagiri by hand in one terminal —

```powershell
$env:PYTHONUTF8 = "1"
C:\ProjectsC\RandomPr\Katagiri\.venv\Scripts\python.exe -m katagiri.mcp_server
```

— then connect the agent to that already-running process instead of letting
`MultiServerMCPClient` spawn it. `langchain-mcp-adapters`'s stdio transport
only supports spawn-and-own, not attach-to-existing, so this fallback would
need either (a) a raw `mcp.client.stdio` session opened against a
pipe/socket bridge to the running process, or (b) katagiri's own launch
being driven directly by the graph's own subprocess call outside
`MultiServerMCPClient`'s pool management. Record whichever is chosen in
TG-C's transport plan if this is ever exercised for real.
