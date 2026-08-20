# Specification Quality Checklist: 006 — Teaching Method

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-20
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond binding upstream decisions (tool names, `caps` key names, the migration number and the reserved dictation slug are the checked-in contract surface and are kept deliberately)
- [x] Focused on learner value: what is taught, in what order, at what dose, on what evidence
- [x] Written so a non-implementing reader can tell what changes for the learner
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous (each FR maps to at least one quickstart expected outcome)
- [x] Success criteria are measurable, including the mechanical entry-gate counts
- [x] Success criteria technology-agnostic where possible (SC-008 is deliberately not — contract discipline is the criterion)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified, including the two that bite first (dictation logged under a free-text topic; reps double-counted against the study-log importer)
- [x] Scope is clearly bounded: one ungated prose/data taskgroup, one gate, six post-gate taskgroups, one close
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows (Phase 0, dose, input, production, materials, assessment, kanji, worksheets)
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation leakage beyond binding decisions

## Feature-specific gates (why this checklist has extra rows)

- [x] **TG0 is genuinely ungated**: nothing in FR-001…FR-009 requires a contract change, a schema change, or a gate evaluation. The only code in TG0 is the snapshot-extension constant and its test.
- [x] **Entry gate is additive**: FR-011 states the 14-in-18 count and probe battery stay necessary, and the quickstart asserts the pre-existing verdict is unchanged on the pre-existing fixtures.
- [x] **Governance precedes code**: every contract- or schema-touching taskgroup in tasks.md opens with a ledger/constitution task, and the dependency list names that ordering as load-bearing (T008→T009, T012→T013, T018→T019, T022→T023, T027→T028, T037→T038).
- [x] **Zero new ToolSpecs** is stated in the spec (FR-025), the plan's constraints, the conflict map's hot-file rule, and SC-008 — and is asserted by a registry smoke test in each registration task.
- [x] **Cut means cut**: `next_topic`, `plan_revision`, `mark_topic_progress`, `run_drill`, `check_answer` appear once, in FR-014, as cut — not in the deferral table.
- [x] **Deferrals carry triggers**: F-02 (revised), F-03, STT, restore-CLI each have a stated firing condition and are listed in both spec.md and tasks.md.
- [x] **No TTS/STT/voice anywhere** in the requirements, including the pitch-marking exercise, which is explicitly text-only.

## Notes

- Authoritative input is council plan v3 §"Feature 006" + §"Governing principles" (with plan v1 §"Project context", decision #5 and #6 as background). This spec re-decides nothing; disagreement during implementation is filed as a ledger row, not improvised.
- Two constitution amendments are filed *by* this feature: principle IV gains the entry gate (→ 1.1.0, TG1) and the whole-schema-in-one-migration constraint gains a stated exception for migration 0002 (→ 1.2.0, TG4). Both are filed before the code they authorise.
- Open-by-design items are listed at the end of research.md (hiragana latency bound, per-node Irodori lesson assignment, pitch-marking set source). None blocks planning.
- `/speckit-analyze` will flag `[Gate]` and `[Ops]` task labels as unmapped to a user story — expected noise, per specs/README.md §"Task-label legend".
