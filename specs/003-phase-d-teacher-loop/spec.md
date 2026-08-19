# Feature Specification: Phase D — Teacher Loop

**Feature Branch**: `003-phase-d-teacher-loop`

**Created**: 2026-08-19

**Status**: Active — **tasks.md is the task-tracking source of truth** (switched from beads 2026-08-19; former epic `kata-ph-d` retired, IDs kept as `[was: kata-*]`)

**Input**: User description: "Phase D teacher loop: authoring and session tools, skills pack v1, sensei letter, vocab+grammar intelligence, D6 stop-gate" — expanded from docs/dev-plan.md v1.1 Phase D. Execution order inside the phase is felt-value-first: **D3 → D4 → D5 → D2**, ending at the D6 stop-gate. (D1 moved to A8 in Phase A.)

**Entry precondition**: C-verify green AND ≥4 logged study days in the prior week (constitution IV).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One prescribed action per session (Priority: P1)

As the learner, I open a study session and the agent calls `start_session`, which returns **exactly one prescribed action** (not a dashboard). During the lesson I mine words (`add_vocab`), log mistakes (`log_error`), get generated exercises (`gen_exercise`, `build_sentences`), and the agent records structured observations (`log_observations`) and a lesson record (`log_lesson`). Everything lands in the event log.

**Why this priority**: D3 is the core of the teacher loop — every other Phase-D item consumes its tools; it is first in the felt-value order.

**Independent Test**: One full scripted lesson loop on fixtures: `start_session` → exercise → `log_error` → mine → `log_lesson`; artifacts appear in vault and event log.

**Acceptance Scenarios**:

1. **Given** an open session, **When** `start_session` is called, **Then** the response contains exactly one prescribed action with its rationale, never a menu.
2. **Given** an observation being logged, **When** `log_observations` is called, **Then** the mandatory fields `unassisted` flag, coverage band, and `rubric_version` are required (call fails without them) — this is the unassisted pass-rate source.
3. **Given** lesson content derived from media text, **When** any write tool receives it, **Then** the content arrives in the untrusted-data envelope and the write requires echo-back confirmation before committing. *(constitution VI)*
4. **Given** `lessons(topic?, unresolved_only)` is queried, **Then** past lesson records return filtered accordingly.

---

### User Story 2 - The system remembers my lessons (Priority: P1)

As the learner, my next session picks up where the last one left off: unresolved threads (`unresolved[]`), a `next_step` written at close and read at open, and `revisit_after` topic-level spacing (Anki schedules items; this schedules topics). These surface in `Today.md` via the Phase-B section registry. The skills pack is revised to v1 from weeks of logged friction, and a **tired-mode minimum session** (reviews + one mined word) is defined and counts toward the gate.

**Why this priority**: Lesson memory converts isolated sessions into a continuing curriculum; tired-mode protects the study streak that gates everything else.

**Independent Test**: Close a lesson with `next_step` + `unresolved[]`; open a new session and observe both surfaced; `Today.md` shows the lesson-memory section.

**Acceptance Scenarios**:

1. **Given** a lesson closed with `next_step`, **When** the next session opens, **Then** `start_session`'s prescribed action reflects that `next_step`.
2. **Given** a topic with `revisit_after` due, **When** `Today.md` renders, **Then** the topic appears in the lesson-memory section (via B1 registry — extension, not rewrite).
3. **Given** a tired-mode session (reviews + one mined word), **When** the day is evaluated, **Then** it counts as a study day for gate purposes.

---

### User Story 3 - Weekly sensei letter with substance (Priority: P2)

As the learner, I receive the full sensei letter: A9's streak/review/new-known content extended with my errors, unresolved threads, and probe results.

**Why this priority**: Retrospective value on top of the loop; extends existing A9 rather than new surface.

**Independent Test**: Generate a letter from a fixture event log containing errors, unresolved threads, and probe results; all three sections render.

**Acceptance Scenarios**:

1. **Given** logged `log_error` events and unresolved lesson threads, **When** the letter generates, **Then** both appear alongside the A9 baseline stats.

---

### User Story 4 - i+1 material that is actually reachable (Priority: P2)

As the learner, when I ask for next material, `find_i_plus_one` only proposes sentences whose grammar is reachable in my curriculum DAG **and** whose vocabulary coverage is adequate — never on vocabulary alone (D-28). I can see real `coverage(text)` numbers, comprehension-debt ranking, and difficulty-for-me scoring.

**Why this priority**: D2 is intelligence on top of the loop; ordered last (felt-value-first) but required before D-verify.

**Independent Test**: Fixture with a grammar point not yet reachable: sentence containing it is excluded from `find_i_plus_one` output even at 100% vocab coverage.

**Acceptance Scenarios**:

1. **Given** curriculum.md with `prereqs`/`unlocks`, **When** imported, **Then** grammar-DAG rows exist as `item` rows with dependency edges.
2. **Given** a candidate sentence with unreachable grammar, **When** `find_i_plus_one` runs, **Then** the sentence is gated out regardless of vocab coverage.
3. **Given** a text, **When** `coverage(text)` is called, **Then** the known-word coverage percentage is computed from the real known_set.
4. **Given** ranking requests, **Then** comprehension-debt and difficulty-for-me (jreadability + BCCWJ + JLPT + coverage %) scores are returned.

---

### User Story 5 - The gate to Phase E is mechanical (Priority: P1)

As the learner-developer, Phase E code is blocked until `stop_gate_status` says PASS: **14 study days within an 18-day window** (study day = ≥10 min or ≥1 logged artifact — concrete event-type count, not reinterpretable), plus one canary probe battery run with unassisted pass-rate recorded across ≥2 coverage bands. Declared illness/travel pauses allowed. If unmet twice → explicit re-plan.

**Why this priority**: D6 is the structural mitigation for the project's #1 risk (building past the gates).

**Independent Test**: Fixture event logs for pass and fail cases; `stop_gate_status` prints PASS or FAIL + the failing criterion, deterministically.

**Acceptance Scenarios**:

1. **Given** 13 study days in 18, **When** `stop_gate_status` runs, **Then** FAIL with the day-count criterion named.
2. **Given** 14 study days but no probe battery recorded, **Then** FAIL naming the probe criterion.
3. **Given** a declared pause window, **Then** paused days extend the window instead of failing it.
4. **Exception**: the write-only mpv seek logger (kata-e6s, already shipped) is exempt from the gate.

---

### Edge Cases

- `log_observations` without `rubric_version` → rejected, not defaulted (silent defaults would corrupt the pass-rate series).
- Canary set sentences (sealed, A0b) referenced by any drill → validator screams; probes only.
- Media-derived text attempting instruction injection → envelope + echo-back stops the write (rehearsed in E-verify, contract set here).
- Two consecutive gate failures → re-plan event, not silent limbo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide authoring/session tools: `add_vocab`, `log_error`, `triage_inbox`, `gen_exercise`, `build_sentences`, `log_observations`, `log_lesson`, `lessons(topic?, unresolved_only)`, `start_session`. All writes flow through the event log. *(bead kata-d3)*
- **FR-002**: `log_observations` MUST enforce mandatory fields: `unassisted` flag, coverage band, `rubric_version`. *(kata-d3)*
- **FR-003**: `start_session` MUST return exactly one prescribed action. *(kata-d3)*
- **FR-004**: Media-derived text MUST arrive in the untrusted-data envelope; write tools MUST require echo-back confirmation on such content. *(kata-d3, D-22)*
- **FR-005**: Skills pack v1 MUST be an evidence-driven revision of A0c's v0 (from logged friction), including WATCH/REVIEW mode content. *(kata-d4)*
- **FR-006**: Lesson memory MUST persist `unresolved[]`, `next_step` (written at close, read at open), and `revisit_after` topic spacing; surfaced in Today.md via B1's registry. *(kata-d4)*
- **FR-007**: Tired-mode minimum session (reviews + one mined word) MUST be defined and count toward the gate. *(kata-d4)*
- **FR-008**: Sensei letter MUST extend A9 with errors, unresolved threads, probe results. *(kata-d5)*
- **FR-009**: System MUST compute real `coverage(text)`; import the grammar DAG from curriculum.md (`prereqs`/`unlocks`) into `item` rows; gate `find_i_plus_one` on grammar reachability AND coverage; provide comprehension-debt ranking and difficulty-for-me scoring. *(kata-d2, D-28)*
- **FR-010**: `stop_gate_status` (shipped in A6) MUST evaluate the D6 criteria mechanically: 14/18 study days + probe battery with unassisted pass-rate across ≥2 coverage bands; declared pauses; re-plan trigger on two misses. *(kata-d6, D-19)*
- **FR-011**: All new tools registered additively; unimplemented tools raise. *(constitution VII)*

### Key Entities

- **Observation**: event with `unassisted`, coverage band, `rubric_version` — the unassisted pass-rate series.
- **Lesson**: record with topic, `unresolved[]`, `next_step`, `revisit_after`.
- **Grammar-DAG item**: `item` row imported from curriculum.md with prereq/unlock edges.
- **Probe battery result**: canary-set probe outcomes recorded per coverage band.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (D-verify): cumulative cold-subagent pass (A..D): one full lesson loop (i+1 pick → exercise → `log_error` → mine) lands artifacts in vault + event log. *(kata-dvf)*
- **SC-002** (Milestone D): loop used daily for two weeks per `stop_gate_status`; probe battery recorded; skills pack revised from logged friction.
- **SC-003** (learner metric): ≥5 of last 7 days show events from Phase-D tools.
- **SC-004**: D6 gate evaluated only mechanically — zero self-assessed passes.

## Assumptions

- Canary set (A0b) remains sealed; probes may read it, drills never.
- curriculum.md with `prereqs`/`unlocks` exists or is authored during D2 (content task, learner-owned).
- Task state lives in tasks.md (beads retired for this phase 2026-08-19; `kata-d2/d3/d4/d5/d6/dvf` are historical refs).
