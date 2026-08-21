# Existing-server contract — `vault_read` (Obsidian Local REST API MCP)

This is the 8-row contract for **one tool of the existing server** required
by Part C (spec.md FR-014). It documents `vault_read` as it is actually
reached by this project's agent, on this project's configuration — the
**demo profile** (docs/assignment/demo-setup.md), not the personal vault —
not transcribed from upstream. Upstream reference for detail beyond this
doc's scope: `coddingtonbear/obsidian-local-rest-api`.

## Why `vault_read`, not another tool

The server exposes 16 tools (T005 spike, `docs/assignment/part-a-server-decision.md`).
Of those, exactly two are bound into the agent's client-side allowlist —
`OBSIDIAN_FEATURED_TOOLS = {"vault_list", "vault_read"}`
(`agent/src/katagiri_agent/clients.py`) — and `vault_read` is the one the
US1 goal-note path actually calls: `katagiri_agent.graph`'s `read_goal_note`
node calls `vault_read` on the demo vault's goal note, and
`katagiri_agent.goal_note.parse_goal_note` consumes exactly its return value
(`source_tool="vault_read"` is hardcoded as the default in
`build_provenance_entry`). `vault_list` only enumerates paths (used once, at
spike time, to discover the note's path); it never touches note content and
was not chosen.

## This project's configuration (not upstream's defaults)

- **Which server variant**: the plugin's built-in `/mcp/` endpoint,
  Streamable HTTP — not a stdio wrapper. This is the variant T005 actually
  reached and the working assumption `part-a-server-decision.md` records;
  **T001's instructor confirmation is still pending (user-side)**, so this
  framing is provisional, not settled.
- **Which instance**: the **demo** Local REST API instance (`docs/assignment/demo-setup.md`),
  not the personal one. Non-default port — HTTPS **`27224`** (insecure HTTP
  `27223` left disabled, mirroring the personal instance's own
  insecure-port-disabled state) — and a **distinct demo token**, generated
  once for this instance and never shared with the personal
  `%LOCALAPPDATA%\Katagiri\config.toml`. The personal instance's `27123`/`27124`
  play no part in this contract: katagiri's own proxy tools are hardcoded to
  `27123` and structurally cannot reach the demo instance (D-34); this
  contract is only about the homework agent's own direct connection.
- **Cert handling**: self-signed cert on `27224`, same shape as the T005
  spike observed on `27124`. `OBSIDIAN_VERIFY_TLS=false` (rehearsal-fast) or
  `OBSIDIAN_CA_BUNDLE` pointed at the demo instance's exported cert
  (recording-clean) — both read by `agent/src/katagiri_agent/config.py`'s
  `_httpx_client_factory`. Never bypass verification against the personal
  instance; only ever against this demo one.
- **What it's called on**: `tests/demo_fixtures/vault/00-goals/goal-note.md`
  (or `goal-note.variant-b.md`, swapped in for the changed-valid-input
  demo) — synthetic frontmatter (`goal_theme: food` / `transport`), never
  personal vault content.

## The 8 rows

### 1. Name

`vault_read`

### 2. Model-facing description (as this project frames it to the agent)

Read the full content of one note in the connected Obsidian vault, given
its vault-relative path. In this project it is called on exactly one path
per run — the demo goal note — to fetch frontmatter that steers the
katagiri-side call downstream. It is never used to browse or search; the
agent already knows the path it wants before calling it.

### 3. Input schema

| Field | Type | Required | Constraints / notes |
|---|---|---|---|
| path (arg name observed as `filepath` in this plugin's schema; `path`/`file`/`filename` also probed defensively by the T005 spike) | string | yes | Vault-relative path, e.g. `00-goals/goal-note.md`. No leading slash; must resolve inside the vault root the plugin instance is bound to (the demo vault copy, for this project's demo instance). |

No other arguments are passed by this project — the tool exists to be
called with a path and nothing else.

### 4. Output schema

The T005 spike (`agent/scripts/spike_existing.py`, `_first_structured`)
found the Streamable HTTP endpoint answers a successful call with **either**:

- a bare string — the raw markdown body, including any leading
  `---`-delimited frontmatter block, **or**
- a dict carrying the same text under one of `content` / `text` / `body`.

`katagiri_agent.goal_note._extract_raw_text` tolerates both shapes (and a
list/tuple of content blocks, in case `langchain_mcp_adapters` wraps the
result), because this project observed no guarantee of which shape a given
call returns. Downstream, only the leading frontmatter block is parsed
(`goal_theme` specifically); the markdown body past the closing `---` is
never inspected in this project's path.

### 5. Purpose (in this project's flow)

`vault_read` is the **read-only goal-note source**: the one point where the
existing server's data enters the graph. Its return value is parsed for the
`goal_theme` frontmatter field, and that value is passed as a **literal
argument** (never as an instruction) into the katagiri-side call —
`find_i_plus_one`'s theme filter / `gen_exercise`'s topic argument
(`katagiri_agent.goal_note.build_provenance_entry`). The note's content,
including its markdown body, is treated as **untrusted data** throughout —
it is read as an opaque string and only ever placed into a keyword
argument, never executed, formatted-as-code, or interpreted as a directive
(constitution VI). Provenance is recorded explicitly (note path →
`vault_read` → `goal_theme` → katagiri tool + argument → output field) so
the value's path through the run is a lookup, not a reconstruction.

### 6. Error conditions (as this project's call site handles them)

Two layers apply: what the transport/session call itself can fail with
(`katagiri_agent.resilience`), and what a *successful* call can still hand
back that isn't usable content (`katagiri_agent.goal_note`).

| Condition | Distinguished as | Behavior |
|---|---|---|
| Plugin/instance unreachable (demo Obsidian window closed, network refused) | `TransportError` | Retried with bounded backoff and session re-establishment; if retries exhaust, the flow degrades to a **katagiri-only path** that completes and states its degradation rather than hiding it (T015). |
| Invalid/rejected demo API key | `AuthError` | **Never retried** — a rejected key is not a transient condition backoff can fix; surfaced immediately, distinct from `TransportError`. |
| Call succeeds but note is missing / vault_read returns nothing readable | `EmptyResult` via `goal_note.STATUS_NO_CONTENT` | **Not an error** — a type-distinct, successful-empty outcome. Reported explicitly, never silently defaulted (a silent default here is exactly the "decorative read" failure mode this project's US1 exists to avoid). |
| Note content present but not a well-formed `---`/`---` frontmatter block | `goal_note.STATUS_MALFORMED_FRONTMATTER` | Explicit, reported condition; no attempt to guess a value from malformed text. |
| Frontmatter present but has no non-empty `goal_theme` key | `goal_note.STATUS_MISSING_FIELD` | Explicit, reported condition, names which keys *were* present. |

**The three realistic failures this project demonstrates** (T015's scripted
injections, mapped onto this tool's call site): **plugin stopped**
(transport loss → retry/backoff → degraded katagiri-only mode),
**invalid API key** (auth failure, surfaced immediately, no retry), and
**missing note** (a successful empty result — explicitly not an error,
kept type-distinct from the two failures above).

### 7. Side effects

None on the vault. `vault_read` is a GET-shaped read; this project never
calls any write-shaped tool on this server (`vault_append`, `vault_write`,
`vault_patch`, `vault_move`, `vault_delete`, `command_execute` are all
present on the server but excluded from `OBSIDIAN_FEATURED_TOOLS` and never
called). The only local side effect is in-memory: the parsed value and its
provenance entry are added to the graph's checkpointed state
(`langgraph-checkpoint-sqlite`), so a resumed run can recover it without
re-reading the vault.

### 8. Example input/output (this project's fixture, not upstream's)

Input (called by `read_goal_note` against the demo instance):

```json
{"filepath": "00-goals/goal-note.md"}
```

Output (raw return value, demo vault variant A — abbreviated; the real
call returns the full note body, not just the frontmatter shown here):

```
---
type: goal
goal_theme: food
focus: restaurant-ordering
updated: 2026-08-01
---

# Current goal
...
```

Parsed result (`katagiri_agent.goal_note.parse_goal_note`):

```json
{
  "status": "ok",
  "value": "food",
  "detail": "'00-goals/goal-note.md': goal_theme='food'."
}
```

Provenance entry recorded downstream (illustrative shape,
`ProvenanceEntry.as_dict()`):

```json
{
  "note_path": "00-goals/goal-note.md",
  "source_tool": "vault_read",
  "source_field": "goal_theme",
  "value": "food",
  "katagiri_tool": "gen_exercise",
  "katagiri_argument": "topic",
  "output_field": "exercise.topic"
}
```

Swapping in `goal-note.variant-b.md` (`goal_theme: transport`) is the
changed-valid-input demo: same tool, same argument shape, a different
literal value flowing through to a different katagiri argument.
