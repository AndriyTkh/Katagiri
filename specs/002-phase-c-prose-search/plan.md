# Implementation Plan: Phase C — Prose Search

**Branch**: `002-phase-c-prose-search` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-phase-c-prose-search/spec.md`

**Task tracking**: tasks.md (authoritative; switched from beads 2026-08-19 — former epic `kata-ph-c`, task `kata-c2`, gate `kata-cvf`). C1 was folded into A6 (`search_db` shipped) — this phase is C2 only.

## Summary

Katagiri's own markdown search over the vault export + hand-written notes: frontmatter-aware, incrementally re-indexed, served as MCP tools, fully independent of Obsidian running. Completes the dual-search design: `search_db` answers from state, markdown search answers from prose. Gate: C-verify — same question via both paths, markdown path with Obsidian closed.

## Technical Context

**Language/Version**: Python 3.12 (pinned)

**Primary Dependencies**: existing fugashi/UniDic tokenizer substrate (A2) and SQLite FTS5 machinery (A3 patterns); stdlib frontmatter parsing or minimal YAML handling

**Storage**: derived index tables in the existing SQLite DB (drop-and-rebuild script, never a migration; version-stamped rows) — reuses A3 conventions

**Testing**: pytest + cumulative cold-subagent scenarios (A..C)

**Target Platform**: Windows 11, stdio MCP, stderr logging, `PYTHONUTF8=1`

**Project Type**: single project

**Performance Goals**: incremental re-index cost ∝ changed files; queries interactive (<1s on personal vault)

**Constraints**: no dependency on Obsidian/:27123; `search_db` untouched (definitive DB search, D-24); explicit size estimate 8–15h to re-baseline bottom-up before build (D-29)

**Scale/Scope**: one vault, 1 build task + 1 gate

**Estimate re-baseline** (2026-08-19, T002, bottom-up per D-29): T003 schema+rebuild 2.5h, T004 walk+frontmatter+tokenize 2.5h, T005 incremental+cleanup 2.5h, T006 query API 2h, T007 fixture vault+tests 3h, T008 MCP registration 1h — total ≈13.5h, inside the original 8–15h envelope. No single task exceeds 8h, so no further split of tasks.md required.

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I MCP ceiling | PASS | MCP tools only |
| II OSS-first | PASS | reuses vendored tokenizer + SQLite FTS5; the indexer is on the genuinely-build path (part of the MCP server) |
| III Event log sacred | PASS | index = derived tier: drop-and-rebuild, version-stamped |
| IV Study-first | PASS | entry: B-verify green + ≥4 study days prior week |
| V Two-gate verification | PASS | C-verify (`kata-cvf`) blocking; learner metric per defaults |
| VI Security | PASS | pure local reads; no new network surface |
| VII Tool-contract stability | PASS | new tools additive; `search_db` not rewritten |

No violations.

## Project Structure

### Documentation (this feature)

```text
specs/002-phase-c-prose-search/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── checklists/requirements.md
└── tasks.md             # mirrors beads kata-c2/cvf
```

Data model: [docs/db-schema.md](../../docs/db-schema.md) + derived-index tables defined in the rebuild script. Contracts: [src/katagiri/tool_registry.py](../../src/katagiri/tool_registry.py), additive.

### Source Code (repository root)

```text
src/katagiri/
├── md_search.py         # NEW — frontmatter-aware indexer + incremental change detection + query API
├── mcp_server.py        # register markdown-search tool(s) additively
└── tool_registry.py     # additive entries (e.g. search_notes; name finalized at build)

tests/
├── test_md_search.py    # NEW — frontmatter, incremental, JP queries, malformed frontmatter, ghost-hit removal
└── test_cverify.py      # NEW — cumulative scenarios A..C incl. Obsidian-closed path
```

**Structure Decision**: one new module following the A3 FTS conventions (routing short/long queries consistently with `search_db` behavior); index storage colocated in the existing DB as derived tables.

## Complexity Tracking

None.
