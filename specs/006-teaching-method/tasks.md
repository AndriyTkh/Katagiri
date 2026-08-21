# Tasks: 006 — Teaching Method

**Input**: Design documents from `/specs/006-teaching-method/`

**Prerequisites**: plan.md, spec.md, research.md. Phase D merged to the current integration branch (session tools, lesson memory, intelligence, stop gate).

**Tests**: Included (constitution V).

**✅ SOURCE OF TRUTH**: this tasks.md (spec-kit). No beads history for this feature — there are no `[was: kata-*]` refs to carry.

**Taskgroup rule**: each `## Taskgroup` is the merge unit — all tasks in it fully done, tests green, merged before the next group starts. No task spans groups. Completed boxes: `[x]`.

**Integration branch**: TG0 lands on whichever branch Phase D is integrating into at the time (`phase-d` while it is open, `master` after). Record the actual branch in T001; read "master" in lane instructions below as that branch.

**The one structural rule of this feature**:

> **TG0 is UNGATED and prose/data only** — it changes no tool contract and lands immediately, before the learner's first session.
> **TG1 is the entry gate.** **TG2–TG8 may not start until TG1's real-log evaluation reads PASS.**
> Every contract-touching taskgroup opens with a governance task (ledger row / contract-diff justification / constitution bump) that **precedes** its code task, and closes with one serial registration task. **Zero new ToolSpecs in the entire feature.**

**Organization**: US1 = Phase 0 kana + modality ladder (TG0, TG6 for the ladder's later sections), US2 = dose contract (TG2), US3 = input strand (TG3), US4 = audio anchors (TG4), US5 = curriculum material refs + construction state (TG5), US6 = assessment cadence (TG6), US7 = kanji policy (TG6), US8 = worksheet loop (TG7), `[Gate]` = entry gate (TG1) and 006-verify (TG8), `[Ops]` = the TG0 operational prerequisites.

## Workfile & conflict map

| Taskgroup | Worktree? | New files (lane-owned) | Shared/hot files touched |
|---|---|---|---|
| TG0 prose lane | yes: `wt/006-kana-prose` | — | .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md (only this lane, whole feature until TG6) |
| TG0 data lane | yes: `wt/006-kana-data` | — | docs/katagiri/katagiri/10-course/curriculum.md (only this lane until TG5) |
| TG0 ops lane | yes: `wt/006-ops` | — | src/katagiri/backup.py, tests/test_events_backup.py |
| TG0 ops (host steps) | no (integration branch) | — | docs/dev-plan.md |
| TG1 governance | no (master, serial) | — | docs/decisions-ledger.md, docs/audit-log.md, .specify/memory/constitution.md |
| TG1 gate code | no (master, serial) | — | src/katagiri/stop_gate.py, tests/test_stop_gate_d6.py, docs/db-schema.md |
| TG1 registration | no (master, serial) | — | src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py |
| TG2 governance | no (master, serial) | — | docs/decisions-ledger.md, docs/audit-log.md |
| TG2 dose lane | yes: `wt/006-dose` | — | src/katagiri/session_tools.py (only this lane until TG3), tests/test_session_tools.py |
| TG2 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG3 input lane | yes: `wt/006-input` | — | src/katagiri/session_tools.py (only this lane), tests/test_session_tools.py |
| TG3 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG4 migration lane | yes: `wt/006-anchors` | src/katagiri/migrations/0002_audio_anchors.sql | docs/db-schema.md, src/katagiri/intelligence.py (only this lane until TG5), tests/test_intelligence.py |
| TG4 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG5 curriculum lane | yes: `wt/006-curriculum` | — | src/katagiri/intelligence.py (only this lane), docs/katagiri/katagiri/10-course/curriculum.md, tests/test_intelligence.py |
| TG5 vendor lane | yes: `wt/006-vendor` | scripts/fetch_irodori.py, scripts/fetch_taekim.py | vendor/README.md, vendor/CHECKSUMS.sha256, .gitignore |
| TG6 prose lane | yes: `wt/006-cadence` | docs/katagiri/katagiri/70-drills/ additions | .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md (only this lane) |
| TG7 worksheet lane | yes: `wt/006-worksheet` | — | src/katagiri/today_export.py, tests/test_today.py |
| TG8 close | no (master, serial) | tests/test_006verify.py | docs/decisions-ledger.md, docs/dev-plan.md, this tasks.md |

**Hot-file rule**: `src/katagiri/tool_registry.py` and `src/katagiri/mcp_server.py` are edited **only** in the serial-on-master registration tasks (T010, T017, T021, T026) — never inside a worktree. `session_tools.py` is owned by one lane at a time (TG2 then TG3); `intelligence.py` likewise (TG4 then TG5); the skill prose pair is owned by TG0's prose lane and then by TG6's. Lanes inside a taskgroup own disjoint files, so merges are conflict-free by construction.

## Format: `[ID] [P?] [Story] Description` — each task lists **Write** and **Read** (the complete context a fresh agent needs)

---

## Taskgroup 0: Phase 0 kana — UNGATED, prose + data + ops 🎯 lands first, blocks nothing

**Goal**: the learner can run a KANA session **today**, and the first audio artifact it produces is backed up.

**Independent Test**: one real KANA session end to end; a dictation-carrying lesson close in the event log; a vault snapshot containing the `.mp3`/`.wav` it produced.

**No contract changes in this taskgroup.** Two code lines change (`backup.py` constants and their test); everything else is prose or curriculum data.

- [x] T001 Bottom-up estimates for every task below; split any >8h task into sub-tasks in this file (D-29); confirm the lane plan against the conflict map above; record the actual integration branch. Write: this tasks.md. Read: plan.md, research.md, specs/003-phase-d-teacher-loop/tasks.md §Estimates (the calibration anchor and the actual-vs-estimate note).
### Estimates (T001, 2026-08-20; human-hour scale, Phase C/D anchors; expected agent wall-clock ≈0.1×)

T002 3 · T003 3 · T004 3 · T005 1.5 · T006 1 · T007 1 · T008 2.5 · T009 5 · T010 2 · T011 0.5 (effort-only; calendar-bound ≥10-day window) · T012 1.5 · T013 3 · T014 3.5 · T015 2 · T016 3.5 · T017 2 · T018 1.5 · T019 3 · T020 2.5 · T021 1.5 · T022 2 · T023 3 · T024 3.5 · T025 3 · T026 1.5 · T027 1.5 · T028 4.5 · T029 4 · T030 3.5 · T031 3.5 · T032 1.5 · T033 3.5 · T034 2.5 · T035 2 · T036 3.5 · T037 1.5 · T038 4 · T039 2.5 · T040 1 · T041 6 · T042 2. **Total ≈107h** (≈10h wall-clock at the specs/003 ratio ≈0.09). No task >8h — no splits (D-29). Lane plan confirmed against the conflict map: TG0's three lanes are disjoint (skill-prose pair / curriculum.md / backup.py+test); session_tools.py single-owner TG2→TG3, intelligence.py TG4→TG5, prose pair TG0→TG6 — unchanged. **Integration branch: `master`** — Phase D closed and `phase-d` merged+deleted 2026-08-20; read "integration branch" below as `master`.

- [x] T002 [US1] **KANA mode** in the study skill: a peer of FULL/WATCH/REVIEW/TIRED — hiragana in row blocks of ~5 kana/day, audio-first, daily artifact = mora-count dictation, nothing else runs. Add the Phase-0 suspensions in the same edit: kanji-rival rule off, kanji-component hint ladder off, WATCH off, mining capped at ≤3 kana-only items, furigana always on. State each suspension as suspended (the agent says so out loud), never silently drop it. Lane `wt/006-kana-prose`. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md (keep the mirror in sync). Read: .claude/skills/katagiri-study/SKILL.md (whole, 260 ln — the mode section shape, the `[E*]`/`[spec]`/`[tool]` evidence-tag convention), docs/katagiri/katagiri/90-meta/skills-pack-v1.md, spec.md FR-001/FR-002, docs/katagiri/katagiri/10-course/curriculum.md §"Phase 0 — Ears and mouth".
- [x] T003 [US1] Phase-0 measurement rules in the same prose, serial after T002 (same files): (a) coverage unit = **unread kana**, not words; (b) day qualification = the **dictation artifact**, carried on the closed lesson under a **reserved Phase-0 dictation topic slug** — name the slug here, verbatim, because TG1's gate code will count it and must never guess from prose; (c) **staged kana gates** — hiragana recognition ≥95% in *both* directions (kana→sound, sound→kana) with a stated latency bound unlocks drill tooling, katakana is a second checkpoint and never a wall; (d) the **modality ladder** (A0 = kana + audio-with-script + shadowing + dictation, zero free conversation; A0→A1 = listening volume + scripted voice tasks with visible text; A1+ = unscripted, script hidden); (e) the dose numbers as *policy not yet enforced* (≤8 new words/day, ≤2 new grammar/week, 20–30 min core) with a line saying TG2 turns them into refusals. Lane `wt/006-kana-prose`. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md. Read: T002 output, spec.md FR-003/FR-004/FR-005/FR-009, src/katagiri/stop_gate.py:92–128 (`ARTIFACT_EVENT_REASONS` / `NON_ARTIFACT_EVENT_TYPES` — why `lesson_close` is the artifact the dictation must ride and why `observation` alone cannot qualify a day), .claude/skills/katagiri-study/SKILL.md §TIRED (the existing, correct statement of the study-day rule to stay consistent with).
- [x] T004 [P] [US1] **Kana curriculum data** through the existing ingest: author hiragana row-block nodes and the katakana checkpoint as node blocks (`id:` plus optional `prereqs:`/`level:`) in the Phase-0 section of curriculum.md, then run the importer and verify `item`/`item_edge` rows, idempotence on re-run, and that nothing was authored under the "Node format" heading (which the parser deliberately skips and reports). No parser change. Lane `wt/006-kana-data`, fully parallel with T002/T003. Write: docs/katagiri/katagiri/10-course/curriculum.md. Read: docs/katagiri/katagiri/10-course/curriculum.md (whole — Phase-0 section and the "Node format" block), src/katagiri/intelligence.py:66–114 (module docs: "How curriculum.md maps to rows", `FORMAT_ONLY_HEADINGS`, edge direction, idempotency and the additive/orphan rule), src/katagiri/intelligence.py:1163–1260 (`parse_curriculum`, `curriculum_path`) and :1385–1520 (`import_curriculum` result shape).
- [x] T005 [P] [Ops] Widen `VAULT_SNAPSHOT_EXTENSIONS` to include `.mp3` and `.wav` — **before** any audio artifact can land in the vault — and extend the snapshot tests with an audio-file case that also asserts `.derived/`/`local/` stay excluded. Lane `wt/006-ops`, parallel with the prose and data lanes. Write: src/katagiri/backup.py, tests/test_events_backup.py. Read: src/katagiri/backup.py:56–64 (the constants and the exclusion rationale) and :200–215 (the snapshot filter), tests/test_events_backup.py (existing snapshot-test pattern), spec.md FR-007.
- [x] T006 [Ops] Install the daily backup scheduled task **and verify one real run** (a dated snapshot on disk, not merely a registered task); record the command used and the verification result. Host-side task on the integration branch. Write: docs/dev-plan.md (ops line: task name, schedule, verified run, snapshot path). Read: src/katagiri/backup.py:26–37 (the exact `schtasks /Create` line and the query/delete commands), src/katagiri/installer.py:456–530 (`schtasks_backup_command`, `SCHTASK_BUILDERS`, `probe_schtasks`) and :848 (`step_schtasks`), quickstart.md §1.
- [x] T007 [Ops] **Single-writer discipline** documented: one authoritative frontend process per day, one DB writer at a time, and what to do when two are open. Write: docs/dev-plan.md (ops section). Read: research.md §Phase 0 (single-writer decision), src/katagiri/db.py (connection/transaction behaviour), plan-level note in specs/README.md §"Worktree bootstrap" (why a second checkout can quietly become a second writer).

**Checkpoint TG0**: full suite green; the three lanes merge in any order (disjoint files); T006's verified snapshot exists. **Then the learner studies** — TG0's value is not a passing test, it is a session that happens today. The entry-gate clock starts at the first KANA session; note the date here when it happens.

> **Checkpoint result (2026-08-20)**: TG0 complete. All three lanes merged to master (prose 810cb6f, data 60ee5fc, ops 5f9d0a0); reserved dictation slug = `phase0-kana-dictation` (T003 — TG1's gate code counts this verbatim). Full suite 1620 pass / 5 ground-zero skips (two pre-existing real-curriculum shape tests updated for the 13 new kana nodes: 20 ids / 30 edges / node-block source). T006: "Katagiri Daily Backup" registered (daily 21:00, direct .venv interpreter action) and a real 78 MB snapshot verified — **with a caveat**: schtasks-*triggered* runs twice produced wrong-DB snapshots (quarantined); incident + manual-snapshot guidance in docs/dev-plan.md §Ops, follow-up fix filed. **First KANA session: pending — learner has not studied yet; record the date here at first session; the entry-gate clock has NOT started.**

---

## Taskgroup 1: Entry gate — governance, then code, then the calendar (P0, blocks TG2–TG8)

**Goal**: contract-touching work is impossible until the evidence is real: **≥10 study days, ≥6 with a scored observation, ≥3 with a dictation artifact**.

**Independent Test**: four fixtures (9 days / 10-with-5-scored / 10-with-6-scored-2-dictations / pass) each report the right verdict, and in all four the pre-existing 14-in-18 and probe-battery verdicts are unchanged.

- [x] T008 [Gate] **Governance first, code second.** File the ledger rows and bump the constitution *before* T009 starts: (a) a row for the Phase-0 teaching rules TG0 established (KANA mode, coverage unit, dictation-artifact day qualification via the reserved slug, staged kana gates); (b) a row for the **006 entry gate** stating the three criteria and, explicitly, that they are **additive** to D-19's 14-in-18 count and probe battery — which stay necessary; (c) reasoning detail in docs/audit-log.md; (d) constitution principle IV amended with the entry gate, **MINOR bump to 1.1.0** plus the Sync Impact Report comment at the top. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md, .specify/memory/constitution.md. Read: docs/decisions-ledger.md (whole — next free D-number after D-31, the coverage table, the deferral table), .specify/memory/constitution.md (whole — principle IV, the Governance section's amendment procedure and versioning rules), spec.md §Entry Gate + FR-010…FR-013, research.md §Entry gate.
- [x] T009 [Gate] Entry-gate criteria **in code**, additive beside the existing mechanics: count qualifying study days, days carrying a scored observation (the `unassisted`/`coverage_band`/`rubric_version` series `log_observations` enforces), and days carrying a dictation artifact (a `lesson_close` whose payload topic is the reserved Phase-0 slug from T003). Report each criterion by name on failure. **Do not touch the existing pass/fail computation**: the 14-in-18 day count and the probe-battery criteria stay exactly as they are, and a test asserts the old verdict is unchanged on the old fixtures. Document the criteria in docs/db-schema.md next to the `gate_evaluation` event type. Serial on master (single-owner file). Write: src/katagiri/stop_gate.py, tests/test_stop_gate_d6.py, docs/db-schema.md. Read: src/katagiri/stop_gate.py (whole, 558 ln — the module docstring's criterion-by-criterion reasoning, :85–144 constants incl. `ARTIFACT_EVENT_REASONS`/`NON_ARTIFACT_EVENT_TYPES`/`PROBE_MIN_COVERAGE_BANDS`, :398–540 `stop_gate` and its result dict), tests/test_stop_gate_d6.py (fixture recipe), src/katagiri/session_tools.py `log_observations` (the mandatory-field names) and `log_lesson` (the payload topic field the slug rides), T008's ledger rows.
- [x] T010 [Gate] Registration (serial, master): surface the entry-gate criteria through `stop_gate_status` as **additive output keys** and update its ToolSpec `output`/`summary` strings accordingly. **Zero new ToolSpecs** — assert this in the registry smoke test. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: src/katagiri/tool_registry.py:1–105 (the additive-only rule and the per-phase fragment seam) and :210–240 (the `stop_gate_status` spec, incl. the study-day sentence that must stay true), src/katagiri/mcp_server.py (the `stop_gate_status` adapter block), T009 output.
- [x] T011 [Gate] **Live the gate** (calendar-bound; clock started at the TG0 checkpoint): run the evaluation against the real event log, record the verdict and the three counts, and either open TG2 or state which criterion is short and by how much. Two misses → explicit re-plan, not a quiet retry. Write: docs/dev-plan.md, this tasks.md. Read: the `stop_gate_status` output, quickstart.md §2. **Result (2026-08-21): blocking effect waived by user — D-35.** Baseline evaluation recorded FAIL 1/10 · 0/6 · 0/3 (expected, pre-study). The user waived the gate's blocking effect on TG2–TG8 so the teaching method is finished before learning starts; the criteria stay computed as informational `stop_gate_status` keys (no code change). Constitution amended 1.2.0→1.3.0; reasoning in docs/audit-log.md "Gate waivers — pre-study build-out (2026-08-21)". TG2–TG8 open.
  > **Evaluation #1 (2026-08-20, day of TG1 code landing — baseline, not a miss)**: FAIL. Counts: study days 1/10, scored-observation days 0/6, dictation days 0/3. Short by 9 study days, 6 scored days, 3 dictation days. Expected — the learner's first KANA session has not happened yet; the entry-gate clock has not started. This baseline read verifies the wiring against the real log; the two-miss counter starts with the first *post-study* evaluation.

**Checkpoint TG1**: suite green; ledger + constitution committed **before** T009's commit (check the dates); real-log verdict recorded. **TG2–TG8 stay closed until this reads PASS.**

> **Checkpoint result (2026-08-20)**: TG1 code complete — T008 (governance f397663) landed before T009 (code 82cd936) before T010 (registration 39970bc); order verified. tests/test_mcp_tools.py + test_stop_gate_d6.py: 183 pass. Baseline real-log evaluation recorded under T011 (FAIL 1/10 · 0/6 · 0/3 — expected pre-study). **TG1 stays open on T011 only; TG2–TG8 closed until the real-log read is PASS.** *(Superseded 2026-08-21: T011's blocking effect waived per D-35 — TG1 complete, TG2–TG8 open.)*

---

## Taskgroup 2: US2 — Dose contract in code (P1, post-gate)

**Goal**: the caps stop being self-counted. Topic selection becomes one more rung in the one prescriber.

**Independent Test**: eight mining events today → the ninth `add_vocab` is refused with a structured error naming the cap; `caps.new_words_left == 0`.

- [x] T012 [Gate] **Contract-diff justification** *(done 2026-08-21, D-36, commit 6bb9490)* (precedes all TG2 code): ledger row under D-24 naming exactly what changes — an additive `caps` block in `start_session`'s action payload, and a new structured refusal path on `add_vocab` — with the argument that both are additive (no removed or renamed key, no optional→required change, no meaning change) and that the five cut tools (`next_topic`, `plan_revision`, `mark_topic_progress`, `run_drill`, `check_answer`) are **cut, not deferred**. Reasoning detail in docs/audit-log.md. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md. Read: src/katagiri/tool_registry.py:1–35 (the additive-only contract rule in the module docstring), docs/decisions-ledger.md (D-24 row, next free number), spec.md FR-014…FR-016 + FR-025, research.md §Post-gate decisions.
- [x] T013 [US2] **Curriculum rung** in `prescribe()`: a new rung reading curriculum reachability, placed **above** the generic "open a lesson" fallback and below the existing next-step / revisit / unresolved rungs, returning a topic action with its rationale. The fallback stays as the last resort for an unavailable or empty curriculum. Still exactly one action, still never a menu — the single-prescriber property is the acceptance criterion. Lane `wt/006-dose`. Write: src/katagiri/session_tools.py. Read: src/katagiri/session_tools.py:1–40 (module docstring on why the answer is one action) and :954–1060 (`prescribe` ladder with its order rationale, `_action`, `start_session`), src/katagiri/intelligence.py:115–157 (what "reachable" means: `prereq`-only walk, mastery via known_set or `understanding`, sealed items never offered) and :1618–1690 (the reachability graph builder), spec.md FR-014.
- [x] T014 [US2] **Caps block**: count today's mining events and this week's grammar introductions from the event log and return an additive `caps{new_words_left, grammar_left, listening_reps_left}` on every action payload. Caps: ≤8 new words/day, ≤2 new grammar/week, 20–30 min core/day, review queue hard-capped with the overflow reported as **deferral** rather than a longer session. Constants named and commented with their source (research.md §Post-gate). Serial after T013, same file/lane. Write: src/katagiri/session_tools.py. Read: T013 output, src/katagiri/events.py (`append_event`, the query helpers, the `mining` event written by `add_vocab`), src/katagiri/stop_gate.py:92–128 (the event-type vocabulary these counts read), spec.md FR-015.
- [x] T015 [US2] **`add_vocab` cap refusal**: past the daily cap the call refuses with the module's existing structured refusal shape, naming the cap and the count it read, and pointing at the inbox as the overflow route. Never a silent success. Serial after T014, same file/lane. Write: src/katagiri/session_tools.py. Read: T014 output, src/katagiri/session_tools.py `add_vocab` + `_refused`/`_base` (the refusal shape to reuse), src/katagiri/tool_registry.py:726–780 (`add_vocab` spec — what its output already promises), spec.md FR-016.
- [x] T016 [P] [US2] Tests: rung placement and single-action shape, caps arithmetic at boundaries (0/1/8 words; 1/2 grammar), refusal path and its message fields, and a case proving the caps block is *additive* (existing keys and values unchanged). Written against T015's API; `[P]` once T014 lands. Write: tests/test_session_tools.py. Read: tests/test_session_tools.py (fixture recipe), session_tools.py API after T015, quickstart.md §3 outcomes 1–4.
- [x] T017 [US2] Registration (serial, master, after the lane merges): update the `start_session` and `add_vocab` ToolSpec `output`/`summary` strings for the additive keys and the refusal; congruence and smoke tests; assert **zero new ToolSpecs**. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: src/katagiri/tool_registry.py:500–536 (`start_session` spec) and :726–790 (`add_vocab` spec), the phase-fragment seam docs at :87–105, T015 output.

**Checkpoint TG2**: full suite green; a cap refusal observed in a real session; the skill prose line from T003 ("not yet enforced") updated by TG6 — note the debt here so TG6 closes it.
> Checkpoint result 2026-08-21: full suite 1672 passed / 5 skipped (one dverify test updated: the empty-log single-action test now expects the curriculum rung, which correctly outranks `open_first_lesson` when the fixture curriculum is imported; bare fallback covered in test_session_tools). Cap refusal exercised over real MCP stdio by `test_add_vocab_refuses_past_the_daily_new_word_cap_over_mcp` — a live-session observation still pending until the user starts learning (pre-study build-out, D-35). TG6 debt stands: T003 "not yet enforced" prose line. Extra fix landed b49fd1d: add_vocab cap check now reads the caller clock, matching prescribe/_caps_block.

---

## Taskgroup 3: US3 — Input strand (P2, post-gate)

**Goal**: narrow-listening reps are counted as reps, in the one event series, without double-counting.

**Independent Test**: the same listening block logged twice → one event; `import_study_log` over the same day adds nothing.

- [x] T018 [Gate] Ledger row + contract-diff justification for input logging: it appends to the **existing** `study_session` series (no second unread channel), takes its own deterministic dedupe-key namespace so it cannot collide with the importer's `study:<normalised ts>` keys, and reports **reps of known audio** rather than minutes — with the explicit note that a reps-only log claims no minutes and therefore changes no day-qualification arithmetic. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md. Read: src/katagiri/events.py:428–505 (`import_study_log`, `STUDY_LOG_TYPE`, the `study:` dedupe key), spec.md FR-017, research.md §Post-gate decisions (input strand).
- [x] T019 [US3] Input logging: write listening blocks into the `study_session` series with the new dedupe namespace, carrying `listening_reps` and the identity of the known recording (source + which anchor, once TG4 exists — until then the source string). Minutes are omitted, never zero-filled to look measured. Lane `wt/006-input`. Write: src/katagiri/session_tools.py. Read: T018's ledger row, src/katagiri/events.py:428–505 and `append_event`, src/katagiri/session_tools.py (the write-tool pattern and refusal shape), spec.md FR-017.
- [x] T020 [P] [US3] Tests: idempotent re-log, no collision with `import_study_log` over the same day (both paths exercised), reps read back as reps, and an assertion that day-qualification counts are identical before and after a reps-only log. Write: tests/test_session_tools.py. Read: tests/test_events_backup.py (the study-log import test pattern), T019 API, quickstart.md §3 outcome 5.
- [x] T021 [US3] Registration (serial, master): additive argument/output strings on the tool that carries the listening log — **no new ToolSpec**; if no existing tool can carry it additively, stop and file a ledger row rather than adding a spec. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: tool_registry.py:1–35 + the relevant existing spec, T019 output.

**Checkpoint TG3**: full suite green; one real week of both logging paths running with zero duplicates.
> Checkpoint result 2026-08-21: full suite 1685 passed / 5 skipped. Carrier tool is `log_lesson` (3 optional listening args, additive; 26 ToolSpecs unchanged, contracts regenerated in f47e32e). The "one real week of both logging paths, zero duplicates" criterion cannot be observed pre-study (D-35 build-out) — collision-freedom is proven by test (`test_log_listening_and_the_importer_do_not_collide_on_the_same_day`); the live-week observation transfers to the first real study week.

---

## Taskgroup 4: US4 — Audio anchors, migration 0002 (P1, post-gate)

**Goal**: production is restricted to what has been heard.

**Independent Test**: an unanchored item is withheld from an A0 production drill with `text-only-not-for-A0-production` as the stated reason.

- [x] T022 [Gate] **Constitution exception, filed before the migration exists**: the whole-schema-in-one-migration rule (D-12/D-27, constitution §Technology Constraints) does not survive this feature. File the ledger row stating the exception and its scope (additive columns only, no rename, no drop, no derived-table rebuild), the reasoning in docs/audit-log.md, and bump the constitution — **MINOR to the next free version** (1.4.0 if 1.3.0 is current — the "1.2.0" originally written here went stale when D-34 and D-35 landed first) with its Sync Impact Report entry. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md, .specify/memory/constitution.md. Read: .specify/memory/constitution.md §Technology Constraints + §Governance, docs/decisions-ledger.md (D-12/D-27 rows), spec.md FR-018, src/katagiri/db.py:42–60 + :175–260 (the migration runner: `NNNN_name.sql` discovery, one transaction per migration, refusal of any migration that touches `user_version`, backup-before-migrate).
- [x] T023 [US4] **Migration 0002**: additive audio-anchor reference on items/sentences — source plus timestamp (Irodori MP3 refs) — and a way to mark an item text-only-not-for-A0-production. Additive DDL only; no `user_version` statement (the runner stamps it); document the columns in docs/db-schema.md beside the A1 tables. Lane `wt/006-anchors`. Write: NEW src/katagiri/migrations/0002_audio_anchors.sql, docs/db-schema.md. Read: T022's ledger row, src/katagiri/migrations/0001_init.sql (item/sentence DDL, the append-only triggers, naming conventions), src/katagiri/db.py:175–260 (runner constraints), docs/db-schema.md (source-of-truth vs derived classification).
- [x] T024 [US4] A0 **production pool restriction**: production drills draw only from audio-anchored items; unanchored items are withheld with the reason named in the result, never substituted and never synthesised (no TTS in this feature — F-02 stays deferred). Reuse the existing selection plumbing; add no table and no tool. Serial after T023, lane `wt/006-anchors`. Write: src/katagiri/intelligence.py. Read: T023 output, src/katagiri/intelligence.py:115–157 (the i+1 gate's documented treatment of `sealed` and `production_eligible` — this task is what finally consumes the production distinction) plus the selection function it names, spec.md FR-018.
- [x] T025 [P] [US4] Tests: migration applies additively and idempotently on a scratch DB, backup-before-migrate observed, anchored/unanchored pool split, and the withheld-reason string. Write: tests/test_intelligence.py, tests/test_db.py. Read: tests/test_db.py (migration-runner test pattern), tests/test_intelligence.py fixture recipe, quickstart.md §3 outcomes 6–7.
- [x] T026 [US4] Registration (serial, master): additive output keys where the anchored/withheld distinction surfaces; **zero new ToolSpecs**. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: tool_registry.py:1–35 and the affected specs, T024 output.

**Checkpoint TG4**: full suite green; migration applied to the real DB behind a verified backup; first anchored item drilled as production.

Checkpoint result 2026-08-21: full suite 1694 passed / 5 skipped. Migration 0002 (audio_source, audio_offset_ms, text_only on `item`) additive, idempotent, backup-before-migrate covered by test_db.py (39db48c). `find_i_plus_one(..., production=True)` withholds unanchored/text-only candidates with reason `text-only-not-for-A0-production`, never substitutes (10f2949, tested 39db48c). Registered on `find_i_plus_one` additively — zero new ToolSpecs, 26 tools unchanged (6a967cf). "First anchored item drilled as production" against the real DB defers to the user's first real study week (D-35 pattern, same as TG2/TG3 checkpoints) — no anchored items exist pre-study.

---

## Taskgroup 5: US5 — Curriculum material refs + construction state + vendored materials (P2, post-gate)

**Goal**: nodes point at real materials; grammar progress is a trajectory read from evidence already logged.

**Independent Test**: a tag removed from curriculum.md is reported as an orphan and deletes nothing; a U-shaped dip appears in a construction trajectory and lowers no gate.

- [x] T027 [Gate] Ledger rows: (a) curriculum node attributes carry JF can-do id / Irodori lesson / Tae Kim section, with **no new table and no new tool**, and removal semantics = orphan-reported-never-deleted (the additive rule for source-of-truth tables); (b) construction state is **derived** from observation events — accuracy over attempts, no new table, no terminal state, U-dips logged and never penalised, reachability gating **output** tasks only. Reasoning in docs/audit-log.md. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md. Read: spec.md FR-019/FR-021, research.md §Post-gate decisions, src/katagiri/intelligence.py:95–114 (the existing additive/orphan doctrine this extends).
- [x] T028 [US5] **Parser extension**: node attributes for JF can-do id, Irodori lesson, Tae Kim section, landing on existing `item` rows, each carrying the source line that produced it; removal/orphan semantics implemented and reported in the same change. Author the first real tags for the Phase-1 nodes. Lane `wt/006-curriculum`. Write: src/katagiri/intelligence.py, docs/katagiri/katagiri/10-course/curriculum.md. Read: T027 ledger rows, src/katagiri/intelligence.py:66–114 (mapping doc, `FORMAT_ONLY_HEADINGS`, idempotency, orphan reporting) and :1163–1260 (`parse_curriculum`) and :1385–1520 (`import_curriculum` result shape), docs/katagiri/katagiri/10-course/curriculum.md (whole), spec.md FR-019.
- [x] T029 [US5] **Construction trajectory** derived from observation events: accuracy over attempts per grammar construction, computed on read, with no new table, no terminal state, and dips visible rather than punished; reachability continues to gate output tasks only. Serial after T028, same lane. Write: src/katagiri/intelligence.py. Read: T028 output, src/katagiri/intelligence.py:158–200 (the comprehension-debt fold — the existing pattern for deriving state from `observation` rows and the reason `event` rows are not folded in), docs/db-schema.md (observation columns), spec.md FR-021.
- [x] T030 [P] [US5] Tests: all three tag kinds import; tag removal reports an orphan and deletes nothing; re-import restores; trajectory arithmetic incl. a U-dip case; sealed items still never offered. Write: tests/test_intelligence.py. Read: tests/test_intelligence.py fixture recipe, T029 API, quickstart.md §3 outcomes 8–9.
- [x] T031 [P] [US5] **Vendored materials**: download scripts plus checksum entries for Irodori (PDF/MP3 — acquired by hand, **never committed**; custom Japan Foundation terms, illustrations untouchable, no redistribution) and Tae Kim (extracts **committable** under CC BY-NC-SA **with attribution**); `.gitignore` negation patterns and vendor/README rows following the existing policy; no runtime downloads anywhere. Lane `wt/006-vendor`, fully parallel with the curriculum lane. Write: NEW scripts/fetch_irodori.py, NEW scripts/fetch_taekim.py, vendor/README.md, vendor/CHECKSUMS.sha256, .gitignore. Read: vendor/README.md (whole — the four hard rules and the expected-contents table), .gitignore (the `vendor/*` negation pattern), research.md §Post-gate (licensing), spec.md FR-020, docs/decisions-ledger.md D-10.
- [x] T032 [US5] Registration (serial, master): additive output keys where tags or trajectories surface; if nothing surfaces, record "no contract change" explicitly rather than leaving the question open. Write: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py, tests/test_mcp_tools.py. Read: tool_registry.py:1–35 and the affected specs, T029 output.

**Checkpoint TG5**: full suite green; Irodori acquired locally and uncommitted (verify with `git status`); the first node pointing at a real recording.

Checkpoint result 2026-08-21: full suite **1706 passed / 5 skipped** (up from TG4's 1694; net +12 across T028/T029/T030/T032). Curriculum node attributes (jf_can_do/irodori_lesson/tae_kim_section) additive via the `settings` table, orphan-report-never-delete mirroring the existing edge doctrine (D-39, 296ecfd); 7 real Phase-1 nodes tagged in curriculum.md, flagged honestly as unverified against official JF/Irodori/Tae Kim numbering. Construction trajectory derived purely on read from `observation` rows (D-40, ff5d3c9) — confirmed never wired into any reachability/gate path; tests assert the U-dip shape directly and byte-for-byte gate equality before/after (37378ae). Registration (T032, 110b895) added both capabilities as additive optional args on `find_i_plus_one` (`include_curriculum_tags`, `include_trajectory`/`trajectory_window`) after ruling out other existing tools as a fit — 26-tool invariant held, tool-contracts.md regenerated. "Irodori acquired locally" and "first node pointing at a real recording" both deferred, same D-35 pattern as TG2–TG4: no Irodori PDF/MP3 exists in this environment (by design — hand-acquisition only, `fetch_irodori.py` correctly refuses to guess a source URL or fabricate a checksum); the 7 tagged nodes reference lesson/section identifiers, not recordings, until the user acquires the real materials.

---

## Taskgroup 6: US6 + US7 — Assessment cadence, kanji policy, prose reconciliation (P1, post-gate, prose + data)

**Goal**: weekly and monthly evidence that cannot be talked around, and a kanji policy that stays recognition-only.

**Independent Test**: one week of fixtures yields one dictation record and one pitch-marking record; one month yields one monologue artifact.

- [x] T033 [US6] **Assessment cadence** in the study skill and the drills notes: weekly mora-count dictation (hear an Irodori line → write kana; mora-length and devoiced-vowel errors are objective and are logged as `log_error` patterns that already exist in the pack's vocabulary), weekly five-word **pitch-pattern marking** checked against the vendored kanjium accent numbers (text-only perception training — no synthesis anywhere; this is the exercise that will eventually trigger F-02), monthly 60-second self-recorded **monologue** artifact (length and fluency trend, stored in the vault and therefore backed up by TG0's widened snapshot). Lane `wt/006-cadence`. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md, NEW docs/katagiri/katagiri/70-drills/ cadence notes. Read: .claude/skills/katagiri-study/SKILL.md (whole — existing `log_error` pattern names such as `devoiced-vowels`, `mora-length`; the close ritual), docs/katagiri/katagiri/35-phonology/l1-profile.md (the drill priorities the feedback must target), vendor/README.md (kanjium accents row), spec.md FR-022.
- [x] T034 [US7] **Kanji policy**: recognition-only at A0–A1, per-topic budget tied to the learner's known **spoken** words (never JLPT list order), production refused as out of policy, and per-item **furigana decay** always → first occurrence → off, with the stage derived from the item's existing known/understanding state — no new column. Serial after T033, same lane/files. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md. Read: T033 output, docs/katagiri/katagiri/10-course/curriculum.md §"Phase 4 — Kanji", src/katagiri/known.py (the known-state source the budget reads), spec.md FR-023.
- [x] T035 [US1] Prose reconciliation, closing TG2's noted debt: the dose lines that T003 wrote as "policy, not yet enforced" now say **enforced by refusal**, with the caps block and the `add_vocab` error named so the agent stops self-counting; the modality ladder gains its A0→A1 and A1+ rungs' concrete task types. Serial after T034, same lane/files. Write: .claude/skills/katagiri-study/SKILL.md, docs/katagiri/katagiri/90-meta/skills-pack-v1.md. Read: T034 output, T003's prose, T015/T017 output (the real refusal message and `caps` key names), spec.md FR-009/FR-015.
- [x] T036 [P] [US6] Verify the cadence is *measurable* rather than merely written: a fixture week/month produces the three record kinds through existing tools only, and each error path lands on an existing `log_error` pattern. No new tool, no new table — if either seems needed, stop and file a ledger row. Write: tests/test_006verify.py (cadence cases; the file is created here and extended in T041). Read: tests/test_dverify.py (cold-scenario test pattern), T033 output, quickstart.md §4.

**Checkpoint TG6**: full suite green; one real weekly dictation and one pitch-marking record in the log; the prose no longer claims the caps are self-counted.

> **Checkpoint result (2026-08-21)**: TG6 complete. wt/006-cadence merged to master (T035 913a271, T036 48580ff; merge commit on master). T035: SKILL.md/skills-pack-v1.md dose lines now state the new-word cap is enforced by refusal — names `add_vocab`'s real `new_word_cap_reached` error and the real `caps.new_words_left`/`grammar_left`/`listening_reps_left` keys; honestly notes only the new-word cap has an enforcing refusal wired (grammar/listening caps are reported, not yet refused — matches spec FR-016's scope). Modality ladder A0→A1/A1+ rungs filled with concrete task types. T036: new tests/test_006verify.py (4 tests) proves the three cadence record kinds (weekly mora-dictation, weekly pitch-marking, monthly monologue) are measurable through existing tools only — no new tool, no new table; the monologue kind correctly has no log_error path by design (trend-only, per assessment-cadence.md's own recipe). Full suite passing on master (see dev-plan.md/session note for the post-merge run). **TG6 checkpoint condition on a real weekly dictation + pitch-marking log record stays open until the learner actually studies** — pending, same as TG0/TG1's "learner has not studied yet" state; the fixture-proven measurability above satisfies the code-verification half of this checkpoint.

---

## Taskgroup 7: US8 — Worksheet loop (P2, post-gate)

**Goal**: worksheets round-trip through the vault without any agent ever writing to it.

**Independent Test**: worksheet written to `.derived/`, hand-edited, read back through the GET-only proxy as untrusted data; a write outside `.derived/` or over a file lacking `generated: true` is refused.

- [x] T037 [Gate] Ledger row: the worksheet loop reuses the `today_export` `.derived/` writer (confined path, `generated: true` guard, server-named file) and the existing GET-only vault read path; **the agent never writes vaults** (D-20), and **no MCP tool surface is added** — the write happens on the exporter path, the read on existing vault tools. Serial on master. Write: docs/decisions-ledger.md, docs/audit-log.md. Read: src/katagiri/today_export.py:14–60 (confinement and `generated: true` doctrine) and :984–1060 (the frontmatter guard and refusal messages), docs/decisions-ledger.md D-11/D-20/D-22, spec.md FR-024.
- [x] T038 [US8] Worksheet writer on the existing pattern: render the worksheet (furigana per T034's decay stage, cloze/scramble/table shapes) into `<vault>/.derived/` with `generated: true` frontmatter and a server-generated filename, reusing the existing path-resolution and overwrite guards rather than re-implementing them. Lane `wt/006-worksheet`. Write: src/katagiri/today_export.py. Read: T037 ledger row, src/katagiri/today_export.py:984–1140 (write path, `.tmp` handling, refusal messages) and :1140–1160 (the CLI surface this rides), spec.md FR-024.
- [x] T039 [P] [US8] Tests: confinement refusal (path escape), `generated: true` refusal, round-trip read-back, and an assertion that the read-back path treats content as data (instruction-shaped text is returned, never acted on). Write: tests/test_today.py. Read: tests/test_today.py (section/write test patterns), T038 API, quickstart.md §3 outcome 10.
- [x] T040 [US8] Read-back wiring check (serial, master): confirm the filled-in worksheet is retrieved through the existing GET-only vault tools with **no contract change at all**; record "no contract change" in the registration note if that holds. Write: tests/test_mcp_tools.py (if any assertion is added), docs/dev-plan.md (one line). Read: src/katagiri/obsidian_proxy.py (the GET-only surface), src/katagiri/tool_registry.py (vault tool specs), T038 output. **Result (2026-08-21): no contract change.** `mcp_server.vault_file(path)` is `return redact(obsidian_proxy.read_vault_file(path))` — the identical one-arg call T039's `test_write_worksheet_then_read_it_back_through_the_vault_proxy` already exercises on `obsidian_proxy.read_vault_file` directly; `tool_registry.py` gained no `worksheet` ToolSpec (grep confirms) and the frozen `A6_CONTRACT`/`test_a6_contract_is_additive_only` entries for `vault_file`/`vault_list`/`obsidian_active_note` are untouched. No test added: T039 plus the existing contract-lock tests (`test_a6_contract_is_additive_only`, `test_specs_agree_with_the_generated_json_schemas`, `test_every_registered_tool_has_a_spec_and_vice_versa`) already cover both the round trip and the no-new-tool guarantee; a worksheet-specific dispatch test would only re-exercise the generic `redact()` wrapper, which has its own tests.

**Checkpoint TG7**: full suite green; one worksheet filled in by hand in Obsidian and read back.

---

## Taskgroup 8: Gate — 006-verify + close (P0)

- [x] T041 [Gate] **006-verify**: cold-subagent scenarios against frozen fixtures — KANA session closing with a dictation artifact under the reserved slug; the daily new-word cap refusing and being *reported* rather than worked around; an A0 production drill offering only anchored items; a worksheet round-trip treating read-back as data; cumulative A..D still green. Max two fail→fix→rerun cycles (D-23); residual findings go to the ledger. Write: tests/test_006verify.py. Read: quickstart.md §4, tests/test_dverify.py (the cumulative cold-scenario pattern), tests/conftest.py (test-group placement: `compile` → general → `mcp`).
- [x] T042 [Gate] Close: learner metric read from the real event log (dictation and pitch-marking records per week, monologue per month, zero days over the new-word cap — an honest zero is reported as a zero); **deferral register updated** — F-02 trigger revised to "minimal-pair perception training enters the curriculum" with its two prerequisites noted, F-03 restated as Realtime-API voice over our own agent, new rows for STT (kotoba-whisper, pinned checkpoint → unscripted production assessment) and the restore-CLI process-list nicety (→ backlog); review-coverage row appended; weekly status line. Write: docs/decisions-ledger.md, docs/dev-plan.md, this tasks.md. Read: the real event log, docs/decisions-ledger.md §Deferred options + §Review coverage, spec.md §Deferred + quickstart.md §Learner metric.

**Checkpoint TG8**: 006-verify green, learner metric recorded (pass or honest fail), deferral register and coverage table updated.

> **Checkpoint result (2026-08-21)**: TG8 complete — 006 fully closed, every checkbox in this
> file checked. T041 (006-verify) green on frozen fixtures; cumulative A..D green modulo the
> two pre-existing Phase-E-caused failures filed under D-43 (out of 006's scope). T042 learner
> metric read read-only from the real event log (`recent_events`, 15 total events since the
> 2026-08-20 reset): dictation records this week 0, pitch-marking records this week 0,
> monologue records this month 0, days over the new-word cap 0 (out of 1 day with any mining
> activity — genuinely under cap, not merely unmeasured). Honest zero across the board — no
> lesson has ever been closed, so the reserved `phase0-kana-dictation` slug has never fired;
> matches `stop_gate_status`'s entry-gate sub-dict (1/10 study days) and every prior TG
> checkpoint's "learner has not studied yet" note. Deferral register revised in
> decisions-ledger.md (F-02/F-03 restated with prerequisites, F-11/F-12 added for STT and the
> restore-CLI nicety) and a review-coverage row appended for the whole 006 scope. Weekly status
> line appended to dev-plan.md.

---

## Deferred — with explicit triggers (do not pull forward)

| Item | Row | Fires when | Prerequisites at fire time |
|---|---|---|---|
| VOICEVOX TTS | **F-02, revised** | "minimal-pair perception training enters the curriculum" | backup allowlist already widened (T005); an injectable-transport test seam designed **before** the first test |
| Voice interaction, option A: ChatGPT desktop Voice → Codex → katagiri (local stdio) | **F-03, revised** | empirical test PASSED 2026-08-20; remaining gate: Phase 0 kana complete | zero build; covered by Plus subscription |
| Voice interaction, option B: OpenAI Realtime API over our own agent | **F-03 fallback** | option A test fails, or OpenAI restricts arbitrary MCP servers in voice-directed Codex | metered ~$0.05–0.20/min; needs API credits |
| STT (kotoba-whisper, pinned checkpoint) | new row (T042) | unscripted production assessment needs it — the monologue stops being hand-scoreable | pinned checkpoint + checksum under the vendor policy |
| Restore-CLI process-list nicety | backlog (T042) | never blocking; operator comfort only | — |

Each row needs its trigger stated as **met** in a ledger row before any task is written against it.

## Dependencies & Execution Order

- **TG0 → TG1 → (TG2 → TG3 → TG4 → TG5 → TG6 → TG7) → TG8.** TG0 is ungated and merges first; TG1 blocks everything after it; TG8 needs all of TG2–TG7.
- Inside TG0: prose lane (T002 → T003) ∥ data lane (T004) ∥ ops lane (T005), then host steps T006, T007. Max parallel width 3.
- Inside TG1: **T008 (governance) strictly before T009 (code)** — this ordering is the point of the taskgroup, not a formality. Then T010, then the calendar-bound T011.
- Inside TG2: T012 → (T013 → T014 → T015, serial in one file) with T016 `[P]` after T014, then T017.
- Inside TG3: T018 → T019 → T020 `[P]`, then T021.
- Inside TG4: T022 → T023 → T024, T025 `[P]`, then T026.
- Inside TG5: T027 → (T028 → T029) ∥ T031, T030 `[P]`, then T032.
- Inside TG6: T033 → T034 → T035, T036 `[P]`.
- Inside TG7: T037 → T038 → T039 `[P]` → T040.
- T011 is wall-clock: it overlaps other work (feature 005) in real time but is *checked off* only in TG1, and nothing in TG2–TG8 may start before it does. *(Resolved 2026-08-21: checked off via the D-35 waiver, not a PASS read — see T011's result note.)*

## Implementation Strategy

The feature is deliberately lopsided. **TG0 is the whole point of the first day**: it is prose and two constants, it lands immediately, and it is what makes every later taskgroup possible — the entry gate reads evidence that only real KANA sessions produce. Everything after TG0 is small, additive, and preceded by its own governance filing, because the alternative (design the whole teaching method now, from zero evidence) is the failure this split exists to prevent.

Post-gate taskgroups are ordered by how much later work depends on them: the dose contract first (it changes every session), then the input metric, then the schema change (once, deliberately, behind a filed exception), then the material joins, then prose policy, then the worksheet loop. Each lane's Read list is its complete context — an executing agent never needs another lane's files, the council plan, or another feature's artifacts.
