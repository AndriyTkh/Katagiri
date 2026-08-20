# Specification Quality Checklist: 005 — MCP Assignment

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-20
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details beyond binding decisions (the v3 council settled framework, transport, checkpointer and topology; those are recorded as constraints, not re-derived)
- [x] Focused on user value and business needs — here the "user" is dual: the learner-owner (whose data must stay private) and the grader (who must be able to reproduce and assess)
- [x] Written for non-technical stakeholders where the subject permits; rubric-facing requirements name rubric sections so a reader can check them against the assignment
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — the one genuine unknown (which Obsidian MCP variant the course tested) is a **named decision point** with a task that closes it and a documented non-blocking path, not a clarification hole
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable (SC-001..SC-009, each with an on-screen or command-line check)
- [x] Success criteria technology-agnostic where possible — SC-005/SC-009 deliberately are not, because they encode the scope claim and only make sense in terms of specific files
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified, including the two lane hazards that have already caused incidents in this repository
- [x] Scope is clearly bounded — an explicit binding scope claim plus an "Out of scope" section
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows (existing-server steering, custom-server branching, isolation, failure, documentation) plus the rehearsal gate
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation leakage beyond binding decisions

## Assignment-specific coverage (this checklist's real job)

- [x] Part A — configure, discover, call, **incorporate into a flow**, explain contract, demo a realistic failure → US1, US4, FR-001/002/014, T005/T015/T022/T029
- [x] Part B — separate process, independently startable, ≥3 substantive tools, explicit schemas, primary-data-source tool, distinguishable errors → US2, FR-006/015, T012/T017/T021
- [x] Part C — 8-row contracts for every custom tool + one existing-server tool → FR-012/014, T018/T020/T022
- [x] Part D — no secrets, env vars, local dataset as deterministic demo input, reproducible repository → FR-009/020, T003/T008/T025
- [x] Defence — 9 steps, 5-segment timing, independent operation → FR-017, quickstart.md, T024/T028
- [x] Minimum-condition rule addressed head-on: ≥3 qualifying tools (T021), qualifying data-source tool (`lookup`), both connections callable (T004/T005/T017), **both servers incorporated into agent flows** (T014 — the decorative-read fix, the single highest-risk item in the feature)

## Governance coverage

- [x] Constitution VI conflict identified, not glossed: ledger row → constitution amendment + version bump → wiring, ordered as T006 before all of TG-C
- [x] Constitution V honored in substance (frozen fixtures, never live personal data) via the demo profile; the non-author README dry-run stands in for this feature's cold-consumer test
- [x] Constitution VII untouched — zero registry edits; narrowing is client-side
- [x] D-29 respected — bottom-up estimates are T002, before build

## Notes

- 005 is **not** a Katagiri phase: no phase-entry study-day requirement, no D6 stop-gate interaction, no learner metric (it adds no study surface). This is stated in spec.md so a later reader does not "fix" the missing gate.
- No beads history exists for this feature, so no `[was: kata-*]` refs appear anywhere in these artifacts.
- Two items are irreducibly **user-side** and cannot be delegated to an agent: the instructor question (T001) and the OpenRouter top-up (T027). Both are marked in tasks.md.
