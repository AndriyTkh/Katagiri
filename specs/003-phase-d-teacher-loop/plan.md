# Implementation Plan: Phase D — Teacher Loop

**Branch**: `003-phase-d-teacher-loop` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-phase-d-teacher-loop/spec.md`

**Task tracking**: tasks.md (authoritative; switched from beads 2026-08-19 — former epic `kata-ph-d`: tasks `kata-d3/d4/d5/d2`, gates `kata-dvf/d6`). Execution order felt-value-first: **D3 → D4 → D5 → D2**, phase ends at the D6 stop-gate.

## Summary

The teaching loop: write-capable authoring/session tools (D3), evidence-revised skills pack v1 + lesson memory + tired-mode (D4), full sensei letter (D5), and vocab+grammar intelligence with DAG-gated i+1 (D2). Closes on D-verify (full lesson loop lands artifacts in vault + event log) and the D6 stop-gate (14/18 study days + probe battery), which blocks all Phase-E code.

## Technical Context

**Language/Version**: Python 3.12 (pinned)

**Primary Dependencies**: `mcp>=2,<3`; existing tokenizer/known_set/event-log substrate; jreadability + BCCWJ frequency + JLPT lists for difficulty-for-me (D2; vendored/checksummed per D-10 policy); py-fsrs formula-only pinned `fsrs<7` if stability math needed (D-07)

**Storage**: existing SQLite — first phase writing heavily to source-of-truth tables (event, observation, lesson, item DAG rows); all mutations through the event log

**Testing**: pytest + cumulative cold-subagent scenarios (A..D) + mechanical `stop_gate_status` fixtures

**Target Platform**: Windows 11, stdio MCP, stderr logging

**Project Type**: single project

**Performance Goals**: interactive tool latency; `find_i_plus_one` acceptable at personal-corpus scale

**Constraints**: `log_observations` mandatory fields enforced (unassisted, coverage band, rubric_version); `start_session` returns exactly one action; untrusted-envelope + echo-back on media-derived writes; canary set sealed (validator-enforced, drills never touch it); D6 evaluated mechanically only

**Scale/Scope**: 4 build tasks + 2 gates — the largest phase; bottom-up estimates + >8h splits mandatory before build (D-29)

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I MCP ceiling | PASS | tools only; skills pack is prompt-side content |
| II OSS-first | PASS | jreadability/BCCWJ/JLPT data reused; Anki still owns scheduling — `revisit_after` schedules topics, never items |
| III Event log sacred | PASS | all D3 writes are events; observation/lesson are source-of-truth tables from the A1 DDL |
| IV Study-first | PASS | entry: C-verify + ≥4 study days; D6 stop-gate is this principle's enforcement point |
| V Two-gate verification | PASS | D-verify + learner metric; probe battery adds the outcome criterion |
| VI Security | PASS | first phase accepting media-derived text into write tools → envelope + echo-back implemented here (contract), adversarially tested in E-verify |
| VII Tool-contract stability | PASS | large additive tool batch; registry updated; unimplemented raise |

No violations.

## Project Structure

### Documentation (this feature)

```text
specs/003-phase-d-teacher-loop/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── checklists/requirements.md
└── tasks.md             # mirrors beads kata-d2/d3/d4/d5/d6/dvf
```

Data model: [docs/db-schema.md](../../docs/db-schema.md) — observation/lesson/item tables shipped in the A1 migration; this phase populates them. Contracts: [src/katagiri/tool_registry.py](../../src/katagiri/tool_registry.py), additive.

### Source Code (repository root)

```text
src/katagiri/
├── session_tools.py     # NEW (D3) — start_session, log_observations, log_lesson, lessons, log_error, add_vocab, triage_inbox
├── exercises.py         # NEW (D3) — gen_exercise, build_sentences (canary-validator hooked)
├── envelope.py          # NEW (D3) — untrusted-data envelope + echo-back confirmation protocol
├── lesson_memory.py     # NEW (D4) — unresolved[]/next_step/revisit_after + Today.md section renderer (plugs into exporter registry)
├── sensei_letter.py     # EXTEND (D5) — errors, unresolved threads, probe results
├── intelligence.py      # NEW (D2) — coverage(text), grammar-DAG import from curriculum.md, find_i_plus_one gate, debt ranking, difficulty-for-me
├── stop_gate.py         # EXTEND (D6) — 14/18-window logic, pauses, probe-battery criterion (module extraction tracked by kata-ph-a.1)
└── tool_registry.py     # additive batch

skills/                  # skills pack v1 (D4) — evidence-driven revision of A0c v0, WATCH/REVIEW modes, tired-mode definition

tests/
├── test_session_tools.py, test_envelope.py, test_lesson_memory.py,
├── test_intelligence.py, test_stop_gate_d6.py, test_sensei_full.py
└── test_dverify.py      # cumulative A..D lesson-loop scenario
```

**Structure Decision**: per-concern modules from the start (avoids repeating the A6 file-cap squeeze that spawned kata-ph-a.1). Lesson memory renders into Today.md strictly through B1's section registry — no exporter edits.

## Complexity Tracking

None — scope is large but principle-clean; size is managed by D-29 splits, not by constitution exceptions.
