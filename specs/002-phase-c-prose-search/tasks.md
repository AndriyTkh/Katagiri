# Tasks: Phase C — Prose Search

**Input**: Design documents from `/specs/002-phase-c-prose-search/`

**Prerequisites**: plan.md, spec.md

**Tests**: Included (constitution V).

**✅ SOURCE OF TRUTH**: this tasks.md (spec-kit), effective at Phase C entry (switch decision 2026-08-19). Beads retired for phases C–E; historical bead IDs kept as `[was: kata-*]` for traceability into the closed-beads record.

**Taskgroup rule**: each `## Taskgroup` below is the merge unit. Every task in a group is *fully* done (code + tests green + merged to `master`) before the next group starts. No task spans groups. Completed boxes: `[x]`.

**Organization**: single user story (US1 = markdown search, was kata-c2) + gate (was kata-cvf).

## Workfile & conflict map

| Taskgroup | Worktree? | New files (lane-owned) | Shared/hot files touched |
|---|---|---|---|
| TG-C1 Setup | no (master) | — | docs/dev-plan.md |
| TG-C2 Indexer core | yes: `wt/c-indexer` | src/katagiri/md_search.py, tests/test_md_search.py, tests/fixtures/vault/ | src/katagiri/migrations/0001_init.sql (derived-table DDL, D-27), tests/test_db.py (DERIVED_TABLES tuple + count) |
| TG-C3 Registration | no (master, serial) | — | src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py |
| TG-C4 Gate | no (master) | tests/test_cverify.py | docs/decisions-ledger.md, docs/dev-plan.md |

Phase C is single-lane: worktree isolation buys context isolation, not parallelism (real parallel lanes start in Phase D). The migration file + test_db.py are shared repo-wide, but no other lane runs during Phase C, so editing them inside `wt/c-indexer` is safe *this phase only*. Hot files (`tool_registry.py`, `mcp_server.py`) are touched **only** in TG-C3, directly on master — never inside a worktree.

## Format: `[ID] [P?] [Story] Description` — each task lists **Write** (files created/edited) and **Read** (complete context a fresh agent needs).

## Taskgroup C1: Setup (serial, master)

- [x] T001 Confirm phase entry (recorded as user-waived, see D-30): Phase B complete (specs/001 tasks all checked, B-verify green) + ≥4 logged study days prior week; record entry line in docs/dev-plan.md. Write: docs/dev-plan.md. Read: specs/001-phase-b-obsidian-render/tasks.md, docs/dev-plan.md (tail). [was: kata-ph-c entry]
- [x] T002 Re-baseline the 8–15h estimate bottom-up onto T003–T008; if any single task still lands >8h, split it further *in this file* (D-29) before starting TG-C2; note the estimate in plan.md Technical Context. Write: this tasks.md, plan.md. Read: plan.md, research.md. [was: kata-c2]

## Taskgroup C2: User Story 1 — Indexer core (P1) 🎯 MVP — worktree `wt/c-indexer`

**Goal**: frontmatter-aware, incremental, Obsidian-independent vault search engine (module only; MCP exposure is TG-C3). **[was: kata-c2]**

**Independent Test**: fixture question answered via module API with Obsidian closed; one-note edit re-indexes exactly one file (asserted on the returned index report).

- [ ] T003 [US1] Derived-index schema + rebuild: add md-search derived tables to the migration DDL (all DDL lives in the A1 migration per D-27; drop-and-rebuild repopulates *rows*, never schema), version-stamped rows, `rebuild_md_index()` entry point returning a structured report (files_scanned/indexed/removed) — this report + stderr log lines are the SC-003 evidence mechanism. Write: src/katagiri/migrations/0001_init.sql, tests/test_db.py (extend `DERIVED_TABLES` tuple + count assertion), NEW src/katagiri/md_search.py (schema + rebuild skeleton). Read: docs/db-schema.md (derived-tier conventions, D-27), tests/test_db.py:1–60 + DERIVED_TABLES block, src/katagiri/fts_index.py (external-content-table + delete-all reindex mechanics to reuse). [was: kata-c2]
- [ ] T004 [US1] Vault walk + frontmatter parse (malformed non-fatal) + body tokenization consistent with A3 short/long routing. Write: src/katagiri/md_search.py (serial after T003, same file). Read: src/katagiri/tokenizer.py (fugashi substrate), src/katagiri/fts_index.py (A3 routing/tokenization parity), src/katagiri/config.py (vault paths). [was: kata-c2]
- [ ] T005 [US1] Incremental change detection (mtime/hash), deletion/rename cleanup (no ghost hits), generated-file flag for .derived/ content; incremental runs return the same structured report (proves one-note edit → one file re-indexed). Write: src/katagiri/md_search.py (serial after T004). Read: T003/T004 output. [was: kata-c2]
- [ ] T006 [US1] Query API: frontmatter filters + body FTS, short/long JP routing parity with `search_db` behavior; pure function surface ready for a thin MCP adapter. Write: src/katagiri/md_search.py (serial after T005). Read: src/katagiri/fts_index.py query-routing functions, T004 output. [was: kata-c2]
- [ ] T007 [P] [US1] Fixture vault + unit tests: create tests/fixtures/vault/ (markdown notes with frontmatter, Japanese + mixed content, one malformed-frontmatter file, one .derived/ file — per quickstart.md); tests for frontmatter queries, JP short/long queries, incremental re-index (report assertion), malformed frontmatter, deletion ghost-hit removal. Inline db fixtures per repo pattern (no conftest.py — copy the `monkeypatch LOCALAPPDATA → reset_config_cache → open_db` recipe from tests/test_mcp_tools.py:57–72). Write: NEW tests/test_md_search.py, NEW tests/fixtures/vault/*. Read: quickstart.md, tests/test_mcp_tools.py:1–80, md_search.py API. May start [P] against T003's API surface while T004–T006 land. [was: kata-c2]

**Checkpoint**: lane green (`pytest tests/test_md_search.py tests/test_db.py` + full suite), merge `wt/c-indexer` → master, delete worktree.

## Taskgroup C3: Registration (serial, master — hot files)

- [ ] T008 [US1] Additive MCP exposure: append ToolSpec entries (e.g. `search_notes`; name finalized here) to TOOL_SPECS in src/katagiri/tool_registry.py; add thin `@server.tool` adapter(s) + module import in src/katagiri/mcp_server.py; extend registry smoke tests. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: tool_registry.py TOOL_SPECS (line 87 + last entry), mcp_server.py adapter block (694–861), md_search.py query API. [was: kata-c2]

**Checkpoint**: full suite green; US1 done — mark T003–T008 checked.

## Taskgroup C4: Gate — C-verify (P0, blocks Phase D)

**[was: kata-cvf]** Blocking; max two rerun cycles.

- [ ] T009 [Gate] Cumulative cold-subagent scenarios A..C: same question answered via `search_db` and via markdown search, the latter with Obsidian closed. Write: NEW tests/test_cverify.py. Read: quickstart.md (runbook), tests/test_md_search.py fixtures. [was: kata-cvf]
- [ ] T010 [Gate] Learner metric from event log; ledger/coverage update; weekly status line; mark phase complete in this file. Write: docs/decisions-ledger.md, docs/dev-plan.md, this tasks.md. Read: event log via `recent_events`, docs/decisions-ledger.md coverage table. [was: kata-cvf]

## Dependencies & Execution Order

- Strictly sequential taskgroups: C1 → C2 → C3 → C4. Inside C2: T003 → T004 → T005 → T006; T007 [P] alongside T004–T006.
- C4 (C-verify) blocks Phase D entry (specs/003 T001).

## Implementation Strategy

Single-story phase; MVP = the whole story. Smallest phase — use it to calibrate estimate accuracy (D-29 re-baseline data feeds Phase D's T002). Context efficiency: an agent executing any TG-C2 task needs only that task's Read list — no beads, no phase-D/E artifacts.
