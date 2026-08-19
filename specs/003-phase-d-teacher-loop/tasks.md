# Tasks: Phase D — Teacher Loop

**Input**: Design documents from `/specs/003-phase-d-teacher-loop/`

**Prerequisites**: plan.md, spec.md; Phase C complete (specs/002 TG-C4 checked).

**Tests**: Included (constitution V).

**✅ SOURCE OF TRUTH**: this tasks.md (spec-kit). Beads retired for phases C–E; historical bead IDs kept as `[was: kata-*]`.

**Taskgroup rule**: each `## Taskgroup` is the merge unit — all tasks in it fully done, tests green, merged to `master` before the next group starts. No task spans groups. Completed boxes: `[x]`.

**Organization**: US1 = session tools (was kata-d3), US2 = skills pack + lesson memory (was kata-d4), US3 = sensei letter (was kata-d5), US4 = intelligence (was kata-d2), gates = D-verify (was kata-dvf) + D6 stop-gate (was kata-d6). Felt-value order US1 → US2 → US3 → US4 preserved for *merge order*; US2/US3/US4 build in parallel worktrees after US1.

## Workfile & conflict map

| Taskgroup | Worktree? | New files (lane-owned) | Shared/hot files touched |
|---|---|---|---|
| TG-D1 Setup | no (master) | — | docs/dev-plan.md, this file |
| TG-D2 Foundational: seam | no (master, serial) | — | src/katagiri/tool_registry.py, src/katagiri/mcp_server.py |
| TG-D2 Foundational: envelope | yes: `wt/d-envelope` | src/katagiri/envelope.py, tests/test_envelope.py | none |
| TG-D3 US1 lane A | yes: `wt/d-session` | src/katagiri/session_tools.py, tests/test_session_tools.py | none |
| TG-D3 US1 lane B | yes: `wt/d-exercises` | src/katagiri/exercises.py, tests/test_exercises.py | none |
| TG-D3 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG-D4 lane US2 | yes: `wt/d-memory` | src/katagiri/lesson_memory.py, tests/test_lesson_memory.py | src/katagiri/today_export.py (SECTIONS seam — only this lane), .claude/skills/katagiri-study/SKILL.md + docs skills-pack mirror (only this lane) |
| TG-D4 lane US3 | yes: `wt/d-letter` | tests/test_sensei_full.py | src/katagiri/sensei_letter.py (only this lane) |
| TG-D4 lane US4 | yes: `wt/d-intel` | src/katagiri/intelligence.py, tests/test_intelligence.py | none |
| TG-D4 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG-D5 Gate D-verify | no (master) | tests/test_dverify.py | docs/decisions-ledger.md, docs/dev-plan.md |
| TG-D6 Gate D6 | no (master, serial) | src/katagiri/stop_gate.py (T020 extraction), tests/test_stop_gate_d6.py | src/katagiri/mcp_server.py (T020), docs/db-schema.md (T021 gate-evaluation event type) |

**Hot-file rule**: `tool_registry.py` and `mcp_server.py` are edited only in serial-on-master tasks (T003, T009, T017, T020), never inside a worktree. Every worktree lane owns disjoint files → merges are conflict-free by construction. `today_export.py` and `sensei_letter.py` are single-lane files this phase.

## Format: `[ID] [P?] [Story] Description` — each task lists **Write** and **Read** (full context needed by a fresh agent).

## Taskgroup D1: Setup (serial, master)

- [x] T001 Confirm phase entry: C-verify green (specs/002 TG-C4 checked) + ≥4 logged study days prior week; record entry line. Write: docs/dev-plan.md. Read: specs/002 tasks.md, docs/dev-plan.md (tail). [was: kata-ph-d entry]
- [x] T002 Bottom-up estimates for every task below; split any >8h task into sub-tasks in this file (D-29); confirm the lane plan against the conflict map. Write: this tasks.md. Read: plan.md, research.md, Phase C actual-vs-estimate note from specs/002 T002. [was: kata-ph-d]

### Estimates (T002, 2026-08-19; human-hour scale, Phase C anchors; expected agent wall-clock ≈0.1×)

T003 2.5 · T004 4 · T005 3 · T006 4 · T007 4 · T008 5.5 · T009 2.5 · T010 3.5 · T011 3 · T012 2.5 · T013 4 · T014 4 · T015a 4 · T015b 5 · T016 3 · T017 2 · T018 5 · T019 1.5 · T020 1.5 · T021 5 · T022 0.5 (effort-only; calendar-bound 18-day window). **Total ≈70h** (≈7h wall-clock at Phase C ratio ≈0.09), under the 89–170h top-down band (docs/oss-components.md:140). T015 (9h) split → T015a/T015b per D-29; lane plan confirmed against conflict map — T015a/b stay serial inside `wt/d-intel`, disjointness unchanged. Note: specs/002 T002 never recorded actuals; calibration recovered from git timestamps (Phase C 18:28→19:43, 2026-08-19).

**Branch note (2026-08-19)**: Phase D integration branch is `phase-d` (user directive — real-usage testing deferred). All lanes branch from and merge to `phase-d`, not `master`; `phase-d` → `master` merge gated on sufficient real testing. Read `master` in lane instructions below as `phase-d` for this phase.

## Taskgroup D2: Foundational (seam serial; envelope in parallel worktree)

- [x] T003 Registration seam: split `TOOL_SPECS` into per-phase tuple fragments concatenated into the final tuple, and group `@server.tool` adapters in mcp_server.py into clearly-delimited per-module blocks with one import line each. Honest rationale: lanes never touch these files either way — the seam keeps every later *serial registration diff* one-liner-sized/reviewable and advances the kata-ph-a.1 extraction goal. Pure restructure — tool contracts, names, behavior identical; existing test suite is the proof (no test edits). Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py. Read: tool_registry.py (whole, 429 ln), mcp_server.py:690–877 (adapter region, from the `# MCP adapter` comment). [enabler; was implicit in kata-ph-a.1]
- [x] T004 [P] Untrusted-data envelope + echo-back confirmation protocol (blocks all media-content writes; contract adversarially tested in E-verify). Pure new module, no MCP registration yet. Worktree `wt/d-envelope`. Write: NEW src/katagiri/envelope.py, NEW tests/test_envelope.py. Read: spec.md envelope requirements, docs/decisions-ledger.md security decisions (envelope/echo-back entries), tests/test_mcp_tools.py:1–80 (fixture recipe). [was: kata-d3]

**Checkpoint**: suite green after T003 merge; T004 lane merges independently (no shared files).

## Taskgroup D3: User Story 1 — Authoring + session tools (P1) 🎯 MVP

**Goal**: full write loop through the event log. **[was: kata-d3]**

**Independent Test**: scripted lesson loop lands artifacts in vault + event log.

Two parallel worktree lanes, then one serial registration task.

- [x] T005 [US1] start_session (returns exactly one prescribed action), log_lesson, lessons(topic?, unresolved_only). Lane `wt/d-session`. Write: NEW src/katagiri/session_tools.py. Read: docs/db-schema.md (event/observation/lesson DDL), src/katagiri/events.py (`append_event` — the event-write path), envelope.py API (from T004). [was: kata-d3]
- [x] T006 [US1] log_observations with enforced mandatory fields (unassisted, coverage band, rubric_version), log_error, add_vocab, triage_inbox. Write: src/katagiri/session_tools.py (serial after T005, same file). Read: T005 output, docs/db-schema.md. [was: kata-d3]
- [x] T007 [P] [US1] Unit tests for session tools incl. mandatory-field rejection paths. Write: NEW tests/test_session_tools.py. Read: tests/test_mcp_tools.py fixture recipe, session_tools.py API. [P] against T006 once T005 lands. [was: kata-d3]
- [x] T008 [P] [US1] gen_exercise + build_sentences with canary-set validator hook (canary sealed — drills never touch it). Lane `wt/d-exercises`, fully parallel with T005–T007. Write: NEW src/katagiri/exercises.py, NEW tests/test_exercises.py. Read: docs/katagiri/katagiri/90-meta/canary/canary-set.md, docs/db-schema.md, envelope.py API. [was: kata-d3]
- [x] T009 [US1] Registration (serial, master, after both lanes merge): additive ToolSpec batch for all US1 tools into the phase-D fragment (T003 seam) + per-module adapter blocks + envelope-gated write paths wired + registry smoke tests. Write: tool_registry.py, mcp_server.py, tests/test_mcp_tools.py. Read: seam layout from T003, session_tools.py/exercises.py APIs. [was: kata-d3]

**Checkpoint**: full suite green; scripted lesson loop lands artifacts in vault + event log. Merging this unblocks US2/US3/US4 lanes. **Start the D6 calendar clock here (T022) — study days accumulate from the first day the loop is usable.**

> **Checkpoint result (2026-08-19)**: suite 1141 green; cold-subagent loop PASS for the event-log half (all six US1 event types + read-back). Vault half deferred to TG-D4 by construction — Today.md lesson-memory section is T010; treat "vault" in this checkpoint as satisfied at the TG-D4 checkpoint. D6 clock started 2026-08-19 (dev-plan entry). Cold-consumer friction to address in TG-D4/T018: (1) `build_sentences` runs its own inline echo ceremony and ignores the stage/confirm staging seam — unify or document at the tool surface (consider in T017); (2) its refusal nests `challenge_id` under `challenge` while the description says otherwise — fix description or flatten; (3) observations logged after `lesson_close` are silently orphaned (lesson_outcome attributes by session+timestamp window) and concurrent open lessons double-count — T010/T018 should warn, document, or gate call order; minor: `lessons()` returns `id` for what `log_lesson` calls `lesson_id`; drill vs sentence key naming drift (`material`/`direction` vs `text`/`origin`); no row-level read tool for observations/errors beyond `recent_events`.

## Taskgroup D4: US2 + US3 + US4 — three parallel lanes

Merge order (felt-value): US2 → US3 → US4. Registration for all three lands as one serial task (T016) after lane merges.

### Lane US2 — Skills pack v1 + lesson memory (P1), worktree `wt/d-memory`

**Independent Test**: next_step written at close surfaces at next open; lesson-memory section appears in Today.md.

- [ ] T010 [in-progress: lane-d-memory] [US2] Lesson memory (unresolved[], next_step, revisit_after — schedules topics, never items) + Today.md section renderer plugged into the `SECTIONS` seam. Write: NEW src/katagiri/lesson_memory.py, src/katagiri/today_export.py (append one `@section` builder + one tuple entry only). Read: today_export.py:700–720 (SECTIONS seam docs), docs/db-schema.md, session_tools.py API. [was: kata-d4]
- [ ] T011 [P] [in-progress: lane-d-memory-skills] [US2] Skills pack v1: evidence-driven revision of the v0 pack from logged friction; WATCH/REVIEW mode content; tired-mode minimum session (reviews + one mined word, counts toward gate). The executable pack lives at .claude/skills/katagiri-study/SKILL.md; docs/katagiri/katagiri/90-meta/skills-pack-v0.md is the prose mirror — update both. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v0.md (or a new skills-pack-v1.md beside it). Read: both current files, event log friction evidence, docs/katagiri/katagiri/60-review/study-log.md. [was: kata-d4]
- [ ] T012 [P] [US2] Unit tests for lesson memory incl. Today.md section rendering. Write: NEW tests/test_lesson_memory.py. Read: fixture recipe, lesson_memory.py API, tests/test_today.py (section-test pattern). [was: kata-d4]

### Lane US3 — Sensei letter, full (P2), worktree `wt/d-letter`

- [x] T013 [P] [US3] Extend sensei_letter.py with errors, unresolved-threads, probe-results paragraphs (extend the module's existing `BODY_SECTIONS` registry, sensei_letter.py:557) + tests. Write: src/katagiri/sensei_letter.py (only lane touching it), NEW tests/test_sensei_full.py. Read: sensei_letter.py (whole), docs/katagiri/katagiri/80-progress/2026-W34-sensei-letter.md (target shape), docs/db-schema.md. [was: kata-d5]

### Lane US4 — Vocab + grammar intelligence (P1), worktree `wt/d-intel`

**Independent Test**: unreachable-grammar sentence gated out at 100% vocab coverage.

- [ ] T014 [in-progress: lane-d-intel] [US4] coverage(text) from real known_set + grammar-DAG import from curriculum.md (prereqs/unlocks → item rows). Write: NEW src/katagiri/intelligence.py. Read: docs/katagiri/katagiri/10-course/curriculum.md, docs/db-schema.md (item DAG rows), src/katagiri/known.py (the known-set access module). [was: kata-d2]
- [ ] T015a [US4] find_i_plus_one gated on DAG reachability AND coverage + comprehension-debt ranking (folded from observation/item_stat_cache). No external data. Write: src/katagiri/intelligence.py (serial after T014). Read: T014 output, docs/db-schema.md. [was: kata-d2; split from T015 per D-29]
- [ ] T015b [US4] difficulty-for-me scoring + vendored difficulty data: vendor jreadability + BCCWJ frequency + JLPT lists under vendor/ with CHECKSUMS.sha256 entries per D-10, loader/parse layer, combined score (readability + frequency + JLPT + coverage %). Write: src/katagiri/intelligence.py (serial after T015a), vendor/ additions. Read: research.md sourcing note, vendor/README.md + CHECKSUMS.sha256 pattern. [was: kata-d2; split from T015 per D-29]
- [ ] T016 [P] [US4] Unit tests incl. the unreachable-grammar gating case. Written against T015b's API; [P] only after T015a lands. Write: NEW tests/test_intelligence.py. Read: fixture recipe, intelligence.py API. [was: kata-d2]

### Serial close of TG-D4

- [ ] T017 Registration (serial, master, after US2/US3/US4 merges): additive ToolSpec batch (lesson-memory tools, intelligence tools; letter has no new tools unless spec says otherwise) + adapter blocks + smoke tests. Write: tool_registry.py, mcp_server.py, tests/test_mcp_tools.py. Read: seam layout, new module APIs. [was: kata-d4/d2]

**Checkpoint**: full suite green; US2/US3/US4 done.

## Taskgroup D5: Gate — D-verify (P0)

**[was: kata-dvf]** Needs US1+US2+US4 merged. Max two rerun cycles.

- [ ] T018 [Gate] Cumulative cold-subagent scenarios A..D: full lesson loop lands artifacts in vault + event log. Write: NEW tests/test_dverify.py. Read: quickstart.md (runbook), prior *verify tests. [was: kata-dvf]
- [ ] T019 [Gate] Learner metric + ledger/coverage update + weekly status line; mark gate complete here. Write: docs/decisions-ledger.md, docs/dev-plan.md, this tasks.md. Read: event log, coverage table. [was: kata-dvf]

## Taskgroup D6: Gate — D6 STOP-GATE (P0, blocks ALL Phase E code)

**[was: kata-d6]** Mechanical evaluation only; needs US1+US2+D-verify.

- [ ] T020 [Gate] Mechanical extraction only: move existing stop_gate logic from mcp_server.py into NEW src/katagiri/stop_gate.py; adapter delegates. Behavior unchanged — the existing declared-pause exclusion (`_pause_days`, mcp_server.py:499) is *preserved through the move, not rebuilt*. Existing suite green is the proof (no new tests here). Serial on master (touches mcp_server.py; completes that slice of kata-ph-a.1). Write: NEW src/katagiri/stop_gate.py, src/katagiri/mcp_server.py (delegate + import). Read: mcp_server.py:480–549 (current stop_gate incl. `_pause_days`) + :787–801 (stop_gate_status adapter). [was: kata-d6]
- [ ] T021 [Gate] Extend gate criteria in stop_gate.py: (a) probe-battery criterion actually gates `passed` — today `probe_battery_recorded` is computed but never factors into the pass/fail boolean (mcp_server.py:521 pre-move); pass requires unassisted pass-rate across ≥2 coverage bands (fields enforced by T006's log_observations); (b) concrete event-type counts behind the study-day definition (≥10 min or ≥1 logged artifact) for the 14/18-day window; (c) two-miss re-plan trigger backed by persisted gate-evaluation events (no such history exists yet — define the event type in docs/db-schema.md, write via events.append_event). Write: src/katagiri/stop_gate.py, NEW tests/test_stop_gate_d6.py, docs/db-schema.md (gate-evaluation event type). Read: T020 output, spec.md D6 criteria, session_tools.py log_observations field names, src/katagiri/events.py. [was: kata-d6]
- [ ] T022 [Gate] Live the gate (calendar-bound; clock started at TG-D3 checkpoint): 14 study days in 18-day window + one recorded probe battery; `stop_gate_status` PASS; record consumption mix (fixes E1/E2/E3 order per F-10); mark phase complete. Write: docs/dev-plan.md, this tasks.md. Read: `stop_gate_status` output, event log. [was: kata-d6]

## Dependencies & Execution Order

- TG-D1 → TG-D2 → TG-D3 → TG-D4 → TG-D5 → TG-D6. Within TG-D2: T003 (serial) ∥ T004 (worktree). Within TG-D3: lane A (T005→T006, T007[P]) ∥ lane B (T008), then T009. Within TG-D4: three lanes fully parallel, then T017.
- Within TG-D6: T020 → T021 → T022. T022 is wall-clock: it overlaps TG-D4/TG-D5 work in real time but is *checked off* only in TG-D6. D6 blocks every Phase E task.

## Implementation Strategy

MVP = US1 (the loop itself); the loop must be pleasant before it gets smart — hence merge order US2 → US3 → US4. Max parallel width is 3 worktrees (TG-D4); each lane's Read list is its complete context — an executing agent never needs beads, other lanes' files, or phase C/E artifacts.
