# Tasks: Phase B — Obsidian Render

**Input**: Design documents from `/specs/001-phase-b-obsidian-render/`

**Prerequisites**: plan.md, spec.md

**Tests**: Included — constitution V mandates the cumulative cold-subagent pass; unit tests follow existing repo practice (pytest, 621 tests green at Phase A close).

**⚠️ SOURCE OF TRUTH**: Beads. Every task cites its bead ID; update bead status (`bd update --claim` / `bd close`) as the primary act, tick here as mirror. On conflict, beads wins.

> **Reconciled 2026-08-21 (beads retirement)**: checkboxes flipped to match final bd state — kata-b1, kata-b2, kata-bvf, kata-ph-b all closed. Technical gates (T010) went green 2026-08-19 (tests/test_bverify.py 10 passed incl. live-bypass + token-canary; suite 795 green). T011's learner metric (Today.md ≥5-of-7 days) was **waived per D-35** (user decision 2026-08-21), not met. Beads DB is now a read-only archive; spec-kit is the sole tracker.

**Organization**: US1 = Today.md exporter (kata-b1), US2 = Obsidian proxy (kata-b2). Stories are independent after Foundational; gate tasks close the phase (kata-bvf).

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

*(none — project skeleton exists from Phase A; entry precondition ≥4 study days verified via `stop_gate_status`-adjacent event queries, manual check)*

- [x] T001 Confirm phase entry: A-verify green + ≥4 logged study days in prior week (event-log query; record in weekly status log in docs/dev-plan.md) [Bead: kata-ph-b entry]

## Phase 2: Foundational

- [x] T002 Bottom-up estimates onto kata-b1/kata-b2/kata-bvf; split any >8h task into sub-beads with own definition of done (D-29) [Bead: kata-ph-b]

## Phase 3: User Story 1 — Today.md exporter (P1) 🎯 MVP

**Goal**: `Today.md` rendered into `.derived/` from existing data via section registry. **[Bead: kata-b1]**

**Independent Test**: exporter run on fixture DB produces complete `Today.md`; headerless-file overwrite refused.

- [x] T003 [P] [US1] Section-registry core (register/render/extend API, generated-file header, overwrite-refusal guard) in src/katagiri/exporter.py [Bead: kata-b1]
- [x] T004 [US1] Phase-B section renderers (Anki due count, streak, known-count trend, weakest morphs, resume pointers) in src/katagiri/exporter.py [Bead: kata-b1]
- [x] T005 [US1] `.derived/` write confinement + stderr logging of runs/refusals in src/katagiri/exporter.py [Bead: kata-b1]
- [x] T006 [P] [US1] Unit tests: registry extension, each section on fixtures, header-guard refusal in tests/test_exporter.py [Bead: kata-b1]

**Checkpoint**: `bd close kata-b1` when green.

## Phase 4: User Story 2 — Obsidian GET-only proxy (P1)

**Goal**: agent reads vault through Katagiri; token server-side; plugin MCP never exposed. **[Bead: kata-b2]**

**Independent Test**: cold agent reads a note via proxy tool; registry shows GET-shaped tools only; token absent from all outputs.

- [x] T007 [P] [US2] Proxy module: token load from %LOCALAPPDATA%, GET calls to :27123, structured errors (Obsidian down / 401 without token echo) in src/katagiri/obsidian_proxy.py [Bead: kata-b2]
- [x] T008 [US2] Register GET-shaped vault tools additively in src/katagiri/tool_registry.py + src/katagiri/mcp_server.py; assert no write-shaped tool exists [Bead: kata-b2]
- [x] T009 [P] [US2] Unit tests: GET-only surface, token-leak scan of outputs/errors/event log, down/401 paths in tests/test_obsidian_proxy.py [Bead: kata-b2]

**Checkpoint**: `bd close kata-b2` when green.

## Phase 5: Gate — B-verify (P0, blocks Phase C)

**[Bead: kata-bvf]** Blocking bead; max two fail→fix→rerun cycles, residual → backlog beads.

- [x] T010 [Gate] Cumulative cold-subagent scenarios A..B on fixtures (read Today.md, read arbitrary note, direct-HTTP bypass refused) in tests/test_bverify.py [Bead: kata-bvf]
- [x] T011 [Gate] Learner metric check from event log: Today.md opened ≥5 of last 7 days [Bead: kata-bvf]
- [x] T012 [Gate] Ledger + coverage table updated; weekly status line appended to docs/dev-plan.md; `bd close kata-bvf` [Bead: kata-bvf]

## Dependencies & Execution Order

- T001–T002 first. US1 (T003–T006) and US2 (T007–T009) are **parallel lanes** (bd confirms: kata-b1 and kata-b2 have no edge between them).
- Gate (T010–T012) requires both stories closed. kata-bvf blocks kata-c2 (Phase C).

## Implementation Strategy

MVP = US1 (daily felt value). US2 can land in the same window from the second lane. Phase closes only on the gate + learner metric, per constitution V.
