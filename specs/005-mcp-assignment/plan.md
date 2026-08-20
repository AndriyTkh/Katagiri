# Implementation Plan: 005 — MCP Assignment (agent + defence package)

**Branch**: `005-mcp-assignment` | **Date**: 2026-08-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-mcp-assignment/spec.md`

**Task tracking**: tasks.md (authoritative, spec-kit). No beads history — this feature was never in beads, so no `[was: kata-*]` refs exist. Execution order is **risk-first**: unblock/spike → demo isolation → agent → docs/defence → rehearsal.

## Summary

A university MCP lab deliverable built *on top of* katagiri without touching its contract. A new `agent/` uv subproject runs a LangGraph flow over two MCP connections — katagiri over stdio (the custom server, already 26 tools) and the approved Obsidian Local REST API MCP server (the existing server). The flow reads a learner-authored goal note from a **dedicated demo vault**, passes a frontmatter field through as a literal argument into katagiri's branch call, routes on `action.kind` from `start_session`, grades, and logs. A committed demo profile (fixture DB + demo vault + demo token, selected by one additive `KATAGIRI_CONFIG` env override) guarantees the graded demo never touches personal study data. Deliverables close with generated-plus-hand-written contract docs, a design rationale, a defence script mapped onto the assignment's 9-step checklist, and a rehearsal gate.

## Technical Context

**Language/Version**: Python 3.12 (pinned, both projects)

**Primary Dependencies**:

- katagiri (unchanged): `mcp>=2,<3`, stdio transport, 26 tools in `src/katagiri/tool_registry.py`
- `agent/` (new, separate dependency tree): `langgraph`, `langchain-mcp-adapters` (`MultiServerMCPClient`), `langchain-openai` (`ChatOpenAI(base_url="https://openrouter.ai/api/v1")`), `langgraph-checkpoint-sqlite>=3.0.1` (CVE-2025-67644 floor), `python-dotenv`
- Version intersection `langchain-mcp-adapters` ↔ `mcp>=2,<3` verified on day 1 (T003); a conflict here is a day-1 discovery, not a week-3 surprise

**Storage**: demo run reads a **fixture** SQLite DB built by script (scripted JMdict import from the existing vendored data); checkpointer writes its own SQLite file under the agent's scratch. Katagiri's own storage layer is untouched.

**Testing**: `agent/tests/` run by the agent subproject (agent deps are not in katagiri's venv); katagiri-side `tests/` gains only the config-override tests, the isolation guard, and the contract-doc drift check. `uv run pytest` and `uv run pytest --public-build` both stay green. No CI exists — pre-commit + pytest are the only runners.

**Target Platform**: Windows 11, stdio MCP, stderr logging, `PYTHONUTF8=1` in both launch paths

**Project Type**: single repository, two uv projects (root = katagiri, `agent/` = the homework agent)

**Performance Goals**: none numeric. The binding constraint is the **defence clock**: 10–15 minutes total, per the 5-segment table. Startup must be fast enough that segment 1 fits in 2 minutes.

**Constraints**:

- Scope claim: no katagiri contract changes; the *only* `src/katagiri/` edit is the additive `KATAGIRI_CONFIG` override
- Client-side allowlist only — no server-side tool profile
- Demo never touches personal DB / vault / token
- Governance order for the D-20 scoping: ledger row → constitution amendment + bump → code
- `mcp>=2,<3`, stdio only; no network listener added anywhere
- Model pinned; free-tier request ceiling must not be discovered during a recording

**Scale/Scope**: 5 taskgroups, 30 tasks. Deadline-bound external deliverable — bottom-up estimates and >8h splits still mandatory (D-29).

## Constitution Check

Constitution version at planning time: **1.0.0**. This feature *requires* an amendment to Principle VI before any wiring (see FR-011); the row below records both the pre- and post-amendment position.

| Principle | Status | Note |
|---|---|---|
| I Personal Tool, MCP Ceiling | PASS | No app, no GUI, no service, no new media player. The agent is a **disposable thin caller** of katagiri, built for one graded demo; policy stays in the server. It raises no ceiling because it adds no user-facing surface to katagiri. |
| II OSS-First | PASS | LangGraph / `langchain-mcp-adapters` / existing Obsidian plugin integrated, nothing reimplemented. Anki untouched. |
| III Event Log Is Sacred | PASS | The flow writes through existing `log_lesson` / `log_observations` tools into the **fixture** DB's event log. No schema change, no new write path, no direct DB writes from the agent. |
| IV Study-First, Gated Progression | PASS (not a phase) | 005 is an external deliverable, not a phase: it adds no study surface, does not consume the D6 stop-gate, and does not gate Phase E. Study time is still not billable to this build (workflow rule). |
| V Two-Gate Verification | PASS (adapted) | The cold-subagent gate's *substance* — frozen fixtures, never live personal data — is honored by the demo profile (US3) and by the rehearsal gate; the non-author README dry-run is this feature's cold-consumer test. There is no learner metric because there is no new study surface to measure. |
| VI Security Hardening by Default | **AMENDMENT REQUIRED** | Principle VI currently reads "the plugin's own MCP endpoint is never registered with the agent" (D-20). This feature registers a plugin MCP endpoint — but of a **dedicated demo vault**, own port, own token, synthetic content, with katagiri's personal-vault proxy untouched. Ledger row lands first, then VI is amended and the constitution version bumped, then wiring starts (T006 precedes T012). Everything else in VI is upheld: stdio-only katagiri, secrets outside the repo, `.env.example` before first commit, no personal token reachable from the agent env. |
| VII Tool-Contract Stability | PASS | Zero registry edits. The featured-subset narrowing is **client-side**; all 26 tools stay discoverable. The contract doc is *generated from* the registry, which strengthens rather than relaxes this principle. |

**Violations requiring tracking**: one — Principle VI scope (handled by governance, not by exception; see Complexity Tracking).

## Project Structure

### Documentation (this feature)

```text
specs/005-mcp-assignment/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md          # IS the defence runbook (the gate executes it)
├── checklists/requirements.md
└── tasks.md
```

Deliverable docs (the graded artifacts) live outside the spec dir, in `docs/assignment/`, because the grader reads the repository, not `specs/`.

### Source Code (repository root)

```text
agent/                            # NEW uv subproject — the entire agent lives here
├── pyproject.toml                # own dependency tree; keeps katagiri's dep purity
├── .env.example                  # committed; real .env gitignored (lands BEFORE first commit)
├── README.md                     # agent-local start commands (README.md at root carries the grader section)
├── src/katagiri_agent/
│   ├── config.py                 # env loading, connection config kept SWAPPABLE (stdio ⇄ streamable-http)
│   ├── clients.py                # MultiServerMCPClient wiring, allowlist, ChatOpenAI(pinned model)
│   ├── graph.py                  # diagnostic-branch graph: goal note → start_session → branch on action.kind
│   ├── goal_note.py              # frontmatter parse → literal-arg passthrough + provenance record
│   ├── resilience.py             # detect loss → backoff retry → session re-establish → degraded path
│   └── checkpoint.py             # sync SqliteSaver
├── scripts/spike_stdio_call.py   # day-1: real tool CALL over stdio on Windows
├── scripts/spike_existing.py     # day-1: existing-server discover + call
└── tests/                        # agent-side tests (need agent deps)

scripts/                          # katagiri-side scripts (import katagiri.*, run in katagiri's venv)
├── build_demo_db.py              # NEW — seeded fixture DB via scripted JMdict import; timed
├── gen_tool_contracts.py        # NEW — 8-row tables, generated rows from tool_registry.py
└── preflight_demo.py            # NEW — ports, processes, keys, one real tool-call round-trip

docs/assignment/                  # NEW — the graded deliverables
├── part-a-server-decision.md     # instructor question + answer + swappability note + OpenWeather contingency
├── demo-setup.md                 # demo vault/port/token/netstat runbook; personal-Obsidian decision
├── tool-contracts.md             # GENERATED rows + hand-written prose, all 26 tools
├── existing-server-contract.md   # one Obsidian tool, 8 rows, in this project's context
├── tool-triage.md                # substantive ≥3 vs helpers; lookup = primary data source
├── design-rationale.md           # boundary justification, trade-offs, reserved-topic differentiation
└── defence-script.md             # 9 steps verbatim + 5-segment timing table + cut list

tests/                            # katagiri-side additions only
├── test_config_override.py       # KATAGIRI_CONFIG additive behavior
├── test_demo_isolation.py        # personal DB/vault/token unreachable under demo profile
└── test_contract_docs.py         # drift check (also wired into pre-commit)

src/katagiri/config.py            # THE ONLY src/katagiri EDIT (additive env override)
.pre-commit-config.yaml           # one hook entry (drift check)
README.md                         # grader-environment section
docs/decisions-ledger.md          # D-32 row (D-20 scoping)
.specify/memory/constitution.md   # Principle VI amendment + version bump
```

**Structure Decision**: the agent is a **separate uv project**, not a package inside `src/katagiri/`. Three reasons, in order of weight: (1) it keeps katagiri's tiny dependency set uncontaminated by the LangChain tree, which is also what makes the "process-separated custom server" claim self-evidently true to a grader; (2) it makes the scope claim mechanically checkable (`git diff --stat src/katagiri/` must list exactly one file); (3) it lets the agent's tests carry agent dependencies without touching katagiri's `tests/` collection or `conftest.py` groups (`compile` / general / `mcp`).

**Deliverable-location decision**: contract docs are *generated* into `docs/assignment/tool-contracts.md` by a script under katagiri's `scripts/` — not under `agent/` — because the generator imports `katagiri.tool_registry` and must run in katagiri's venv. The hand-written prose lives in the same file, in stable hand-authored blocks the generator does not overwrite.

## Complexity Tracking

| Item | Why it is needed | Simpler alternative rejected because |
|---|---|---|
| Constitution VI amendment (D-20 scoping) | The assignment requires the agent to talk to the existing server directly; VI as written forbids registering *any* plugin MCP endpoint with an agent. | Proxying the demo vault through katagiri would mean adding tools (contract change) and would make the existing-server connection indirect — which the rubric explicitly rejects ("configured but never successfully called" / "disconnected demonstration call"). Governance handles this properly: ledger row, then amendment, then code. |
| One `src/katagiri/` edit (`KATAGIRI_CONFIG`) | Without it, the only way to point the demo at fixtures is editing the real `%LOCALAPPDATA%` config before recording and remembering to restore it — a manual step whose failure mode is broadcasting personal data. | Copying katagiri into a demo checkout duplicates the thing being graded and breaks "independently startable, the same server". A CLI flag would be a contract change to the server's startup surface; an env var is additive and inert when unset. |
| Second Obsidian vault + second plugin port | The assignment demands a dedicated demonstration vault; personal notes must not appear on screen. | Reusing the personal vault with "careful navigation" is exactly the failure this project's constitution V exists to prevent. |
| Two spikes before any graph code | Known open bug reports on the Windows `MultiServerMCPClient` + stdio **call** path (`NotImplementedError` / `SelectorEventLoop`, "Connection closed"). | Building the graph first risks discovering an unfixable transport problem after the topology is committed. The spike's fallback (attach to a manually started katagiri process) must be known before, not during, rehearsal. |

No constitution *exception* is claimed — the single conflict is resolved by amendment through the governance path.
