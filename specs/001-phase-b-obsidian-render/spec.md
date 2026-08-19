# Feature Specification: Phase B — Obsidian Render

**Feature Branch**: `001-phase-b-obsidian-render`

**Created**: 2026-08-19

**Status**: Draft (mirrors beads epic `kata-ph-b`; beads is the task-tracking source of truth until switched)

**Input**: User description: "Phase B Obsidian render: Today.md aggregate exporter and GET-only Obsidian proxy" — expanded from docs/dev-plan.md v1.1 Phase B and ledger decisions D-11/D-20.

**Entry precondition**: A-verify green (met) AND ≥4 logged study days in the prior week (constitution IV).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Today.md is the morning starting point (Priority: P1)

As the learner, I open my Obsidian vault in the morning and find a single generated note (`Today.md`) that tells me exactly where I stand: Anki due count, study streak, known-count trend, weakest morphs, and resume pointers — built only from data Katagiri already has (Phase A event log, known_set, Anki mirror).

**Why this priority**: This is the first daily felt value of the vault render; every later phase (lesson memory, media resume points) extends this note rather than adding new dashboards.

**Independent Test**: Run the exporter against the fixture database; a `Today.md` appears under `.derived/` in the vault with all Phase-B sections populated and a generated-file header.

**Acceptance Scenarios**:

1. **Given** a populated event log and Anki mirror, **When** the exporter runs, **Then** `Today.md` is written under `.derived/` containing due count, streak, known-count trend, weakest morphs, and resume pointers, each rendered by a registered section renderer.
2. **Given** a file at the target path that lacks the generated-file header, **When** the exporter runs, **Then** it refuses to overwrite that file and reports the refusal.
3. **Given** a later phase registers a new section renderer, **When** the exporter runs, **Then** the new section appears without any change to existing renderers (registry is extend-only, never rewrite).

---

### User Story 2 - Agent reads my vault safely (Priority: P1)

As the learner, I ask my agent about any note in my vault ("what did I write about 敬語 last week?") and it answers by reading the vault through Katagiri's GET-only proxy tools — without the agent ever holding the Obsidian REST token or gaining write/execute access to the vault.

**Why this priority**: Vault read access is the other half of the phase's value; the proxy design (D-20) is a binding security decision, not an implementation detail.

**Independent Test**: Cold agent with only Katagiri tool descriptions reads `Today.md` and one arbitrary note through the proxy; a direct HTTP call to :27123 attempted from the agent's toolset is impossible (no such tool) and the plugin MCP endpoint is absent from the registry.

**Acceptance Scenarios**:

1. **Given** Obsidian running with obsidian-local-rest-api on :27123, **When** the agent calls a Katagiri vault-read tool, **Then** the note content is returned and the REST token never appears in tool output, errors, or the event log.
2. **Given** the Katagiri tool registry, **When** it is inspected, **Then** only GET-shaped vault tools exist — no PUT/PATCH/DELETE, no `command_execute`, and the plugin's built-in MCP endpoint is not registered with the agent.
3. **Given** Obsidian is not running, **When** a vault-read tool is called, **Then** the tool fails with a clear structured error (no hang, no plausible stub).

---

### Edge Cases

- Target `.derived/` path exists but a non-generated file sits at `Today.md` → refuse + report (never clobber prose).
- Obsidian REST API returns 401 (token rotated) → structured error, token value never echoed.
- Section renderer throws → other sections still render; failed section marked in output; exporter exit code reflects partial failure.
- Vault path contains non-ASCII (Japanese) note names → round-trips correctly (UTF-8 throughout).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an aggregate exporter structured as a **section registry**: each phase registers section renderers; later phases extend, never rewrite. *(bead kata-b1)*
- **FR-002**: The Phase-B `Today.md` MUST be defined strictly from data that exists at Phase-A close: Anki due count, streak, known-count trend, weakest morphs, resume pointers. *(kata-b1)*
- **FR-003**: Exporter writes MUST be confined to `.derived/` inside the vault, every generated file MUST carry a generated-file header, and the exporter MUST refuse to overwrite any file lacking that header. *(kata-b1)*
- **FR-004**: Katagiri MUST hold the obsidian-local-rest-api token (:27123) itself and expose **GET-shaped vault tools only** to the agent. *(kata-b2, D-20)*
- **FR-005**: The Obsidian plugin's built-in MCP endpoint MUST never be registered with the agent (it exposes PUT/PATCH/DELETE and `command_execute` behind the same token). *(kata-b2, D-20)*
- **FR-006**: Tokens MUST never appear in tool outputs, error messages, or the event log; secrets live in `%LOCALAPPDATA%`. *(constitution VI)*
- **FR-007**: New tools MUST be added to the checked-in tool registry additively (post-A6 freeze); unimplemented tools raise. *(constitution VII)*
- **FR-008**: Exporter runs and refusals MUST be observable (logged to stderr; significant actions as events where applicable).

### Key Entities

- **Section renderer**: named unit registered with the exporter; input = read-only DB views; output = one markdown section for `Today.md`.
- **Generated-file header**: marker distinguishing derived files from prose; the overwrite guard keys on it.
- **Vault-read proxy tool**: MCP tool wrapping a GET call to obsidian-local-rest-api; token held server-side.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (Milestone B): B-verify green — cold-subagent cumulative pass (scenarios A..B) on fixtures: reads `Today.md` and an arbitrary note; **direct-HTTP bypass of the proxy attempted and refused**. *(kata-bvf)*
- **SC-002** (learner metric): `Today.md` is the note actually opened on ≥5 of the last 7 days during the phase window. *(kata-bvf)*
- **SC-003**: Zero incidents of a non-generated vault file being overwritten by the exporter.
- **SC-004**: Ledger updated at phase close (decisions/coverage rows).

## Assumptions

- obsidian-local-rest-api v5.1+ installed and bound to 127.0.0.1:27123 (verified by A6 hardening checks).
- Phase A tools (`known_set`, event log, Anki mirror) are green per A-verify; fixture known_set available for tests.
- Beads `kata-b1`, `kata-b2`, `kata-bvf` remain the authoritative task states; tasks.md mirrors them.
