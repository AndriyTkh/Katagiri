# Feature Specification: Phase C — Prose Search

**Feature Branch**: `002-phase-c-prose-search`

**Created**: 2026-08-19

**Status**: Active — **tasks.md is the task-tracking source of truth** (switched from beads 2026-08-19; former epic `kata-ph-c` retired, IDs kept as `[was: kata-*]`)

**Input**: User description: "Phase C prose search: Katagiri markdown search independent of Obsidian" — expanded from docs/dev-plan.md v1.1 Phase C. Note: C1 (DB search) was folded into A6 per Round 5 (`search_db` is the definitive search, already shipped); Phase C is C2 only.

**Entry precondition**: B-verify green AND ≥4 logged study days in the prior week (constitution IV).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Search my prose without Obsidian running (Priority: P1)

As the learner, I ask my agent "where did I write about conditional forms?" and it searches my vault export and hand-written notes through Katagiri's own markdown search — even when Obsidian is closed. The same question is answerable through both `search_db` (state) and markdown search (prose), and the two views complement each other.

**Why this priority**: It is the only work item of the phase; it removes the Obsidian-running dependency from prose recall and completes the dual-search design (D-09/D-11).

**Independent Test**: With Obsidian closed, a cold agent answers a fixture question via the markdown search tool; the same query through `search_db` returns the state-side view.

**Acceptance Scenarios**:

1. **Given** a vault with markdown notes containing frontmatter, **When** the indexer runs, **Then** notes are indexed with frontmatter fields queryable separately from body text.
2. **Given** one note edited since the last index run, **When** the indexer runs again, **Then** only changed files are re-indexed (incremental), and results reflect the edit.
3. **Given** Obsidian fully closed, **When** the agent calls the markdown search tool, **Then** results return normally (no dependency on :27123).
4. **Given** a Japanese-language query (e.g. 勉強), **When** searched, **Then** matches are found in both Japanese and mixed-language notes.

---

### Edge Cases

- Malformed frontmatter (unclosed `---`, invalid YAML) → note still indexed by body; frontmatter flagged, not fatal.
- Deleted/renamed notes → removed from the index on next incremental run (no ghost hits).
- `.derived/` generated files → indexed but distinguishable (generated flag), so prose results can be filtered from dashboard noise.
- Very large vault → incremental run cost proportional to changed files, not vault size.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide its own markdown search over the vault export plus hand-written notes, exposed as MCP tool(s), fully independent of Obsidian running. *(bead kata-c2)*
- **FR-002**: The indexer MUST be frontmatter-aware: frontmatter fields are parsed and queryable distinctly from body content. *(kata-c2)*
- **FR-003**: Re-indexing MUST be incremental (change detection; only modified files re-processed). *(kata-c2)*
- **FR-004**: Search tools MUST be registered additively in the checked-in tool registry; `search_db` remains the definitive DB-side search — this feature does not rewrite it. *(constitution VII, D-24)*
- **FR-005**: Index storage lives in the derived tier: drop-and-rebuild script, never a migration; indexed rows carry an index/version stamp so staleness is detectable. *(constitution III)*
- **FR-006**: Japanese text MUST be searchable consistently with the DB-side approach (short-query and long-query behavior both covered).

### Key Entities

- **Markdown index**: derived store mapping note path → frontmatter fields + tokenized body, with per-file freshness metadata.
- **Search result**: note path, matched excerpt, frontmatter context, generated-file flag.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (Milestone C): C-verify green — cumulative cold-subagent pass (scenarios A..C): the **same question answered via `search_db` and via markdown search, the latter with Obsidian closed**. *(kata-cvf)*
- **SC-002** (learner metric): ≥4 study days/week sustained during the phase; ≥5 of last 7 days show events from this phase's tools once shipped.
- **SC-003**: Incremental re-index after editing one note touches only that note (verifiable from logs).

## Assumptions

- Explicit size estimate 8–15h (Round 5 developer figure) to be re-baselined bottom-up before implementation starts (D-29).
- B1's `.derived/` output is part of the searched corpus.
- Task state lives in tasks.md (beads retired for this phase 2026-08-19; `kata-c2`/`kata-cvf` are historical refs).
