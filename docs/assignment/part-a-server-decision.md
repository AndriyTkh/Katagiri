# Part A — existing-server decision record

## Decision (settled, from research.md — do not re-litigate here)

Existing server = **Obsidian Local REST API MCP**
(`coddingtonbear/obsidian-local-rest-api`). See
`specs/005-mcp-assignment/research.md` for the full rationale (one coherent
narrative with the study vault, Part C, and the failure demo).

## Question that must be sent to the instructor — NOT YET SENT (user-side)

**Status: not yet sent — user-side.** This is T001 and is not this agent's
job to send; it is recorded here so T005's swappable-config choice has a
traceable open question attached to it.

> Which exact Obsidian MCP server / version / commit did the course test
> against — the Local REST API plugin's **built-in `/mcp/` endpoint**
> (Streamable HTTP, bearer token, self-signed cert, shipped from plugin
> v5.0 as "Local REST API **with MCP**"), or a separate **stdio wrapper**
> process in front of the REST API?

Why it matters: `langchain-mcp-adapters` supports both transports today
(`StreamableHttpConnection` with `httpx_client_factory` for the self-signed
cert; `StdioConnection` for a wrapper), so nothing blocks development either
way — but the grading environment's actual variant determines which one the
demo recording must target. `katagiri_agent/config.py`'s
`obsidian_connection()` keeps this a **config-only** choice (`OBSIDIAN_TRANSPORT`
env var); no graph code branches on transport. Closed by T001's answer, not
before.

## OpenWeather contingency (documented, not built)

Kept as a **documented contingency only** — no parallel implementation
exists and none should be started unless the instructor rejects Obsidian.

- **Trigger**: instructor rejects the Obsidian Local REST API server as the
  "existing server" for this assignment.
- **Transport**: stdio (OpenWeather has no natural HTTP-streamable MCP
  server on the approved list; a stdio wrapper is the fallback shape).
- **Credential**: `OWM_API_KEY` (OpenWeatherMap API key), read the same way
  `agent/.env` reads every other secret — never hardcoded, never logged.
- **Build**: a Go MCP server (per the assignment's suggested-stack note for
  this contingency).
- **Requirement before it counts**: the weather result must chain into
  **≥2 dependent decisions** downstream in the graph (e.g. theme selection
  *and* review-priority evidence) — a single weather→theme hop is exactly
  the "isolated showcase" pattern rubric §4 marks Developing (5–9/14) for.
- **Current state**: documented only. Nothing under this contingency is
  built, and nothing should be until the trigger fires.

## T005 — observed-variant note (this task)

**Variant reached**: the plugin's **built-in `/mcp/` endpoint**
(Streamable HTTP), **not** a stdio wrapper — confirmed against the real
personal vault (`docs/katagiri/katagiri`, the only vault this checkout has
configured), independent of the katagiri server and independent of any
LangGraph code, per `agent/scripts/spike_existing.py`.

- **Plugin**: `obsidian-local-rest-api`, manifest id
  `"Local REST API with MCP"`, **version 5.1.0** (satisfies D-11's "v5.1+"
  floor).
- **Endpoint**: `https://127.0.0.1:27124/mcp/`. The plugin's insecure HTTP
  port (27123, `enableInsecureServer`) is **disabled** on this install —
  confirmed both from the plugin's own `data.json` and empirically
  (`netstat` showed 27124 `LISTENING`, nothing on 27123 while the spike
  ran). So on this install there is no HTTP fallback; HTTPS + self-signed
  cert is the only reachable path.
- **Auth**: bearer token, read from the plugin's own `data.json` (the
  vault's locally-generated API key) for this one-off spike and placed in
  the agent's own gitignored `agent/.env` (`OBSIDIAN_API_TOKEN`) —
  presence-only, never printed, never committed (`agent/.env` is
  git-ignored; verified via `git check-ignore`).
- **Cert handling**: self-signed-cert path was exercised for real —
  `OBSIDIAN_VERIFY_TLS=false` in `agent/.env` (the documented escape hatch
  in `config.py`'s `_httpx_client_factory`), and the TLS handshake +
  request succeeded. `.env.example` correctly warns this flag should never
  be silently `false` for a personal-vault connection in the *production*
  graph config — for a one-off diagnostic spike against the personal vault
  it is the documented, visible way to reach an HTTPS endpoint whose CA is
  not otherwise trusted by this machine. (`OBSIDIAN_CA_BUNDLE`, pointing at
  an exported copy of the plugin's self-signed cert, is the non-`verify=False`
  alternative and remains available in `config.py` for whenever a
  demo-vault cert is exported and checked into the demo profile.)
- **Discovery**: `list_tools()` returned **16 tools**:
  `active_file_get_path`, `command_execute`, `command_list`, `open_file`,
  `search_query`, `search_simple`, `tag_list`, `vault_append`,
  `vault_copy`, `vault_delete`, `vault_get_document_map`, `vault_list`,
  `vault_move`, `vault_patch`, `vault_read`, `vault_write`.
- **One successful call**: `vault_list` (enumerate vault files) followed by
  `vault_read` on the first `.md` entry returned — **read receipt:
  `ARCHITECTURE.md` (23172 bytes)**. No note content is reproduced here or
  anywhere in the spike's output; only the path and byte count. No
  write-shaped tool (`vault_append`/`vault_copy`/`vault_delete`/
  `vault_move`/`vault_patch`/`vault_write`/`command_execute`) was ever
  called.

**How to switch to the other variant** (stdio wrapper), if T001's answer
requires it: set `OBSIDIAN_TRANSPORT=stdio` in `agent/.env` and point
`OBSIDIAN_STDIO_COMMAND` / `OBSIDIAN_STDIO_ARGS` at the wrapper process.
`katagiri_agent/config.py`'s `obsidian_connection()` is the only place that
branches on transport — no graph code changes either way. This is exactly
the swappability `config.py`'s module docstring commits to, and this spike
is the empirical proof that the `streamable_http` side of that
swappability is real today, on this machine, against the real plugin.

**Environment note for whoever reruns this spike**: the plugin's REST
server only listens while the Obsidian desktop app is running with this
vault open. In this sandboxed dev environment, a GUI process started as a
plain background job gets torn down when its owning job object closes —
launch Obsidian with the harness's own long-lived background-process
mechanism (not `&`/`disown` inside a ephemeral shell), then poll
`netstat -ano | findstr 2712` for `LISTENING` before running the spike.
