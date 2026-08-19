# Quickstart: Phase D validation (D-verify + D6)

Mirrors gate beads `kata-dvf` and `kata-d6`. Fixtures only.

## Prerequisites

- C-verify green; ≥4 study days prior week.
- Fixture curriculum.md with `prereqs`/`unlocks`; fixture event logs for gate pass/fail cases; sealed canary fixtures.

## Steps

```bash
uv run pytest tests/test_session_tools.py tests/test_envelope.py tests/test_lesson_memory.py tests/test_intelligence.py -ra
```

```bash
uv run pytest tests/test_dverify.py tests/test_stop_gate_d6.py -ra
```

## Expected outcomes (D-verify)

1. Full lesson loop on fixtures: i+1 pick → exercise → `log_error` → mine — artifacts land in vault AND event log.
2. `log_observations` without `rubric_version` → rejected.
3. `start_session` returns exactly one prescribed action; reflects prior `next_step`.
4. Unreachable-grammar sentence excluded from `find_i_plus_one` at 100% vocab coverage.
5. Media-derived write without echo-back confirmation → refused.
6. Canary sentence referenced by a drill → validator screams.
7. Cumulative: scenarios A..C still green.

## Expected outcomes (D6 stop-gate)

- 13/18 days → FAIL naming day-count; 14/18 without probe battery → FAIL naming probe; declared pause extends window; pass case → PASS.

## Milestone D (manual)

- Loop used daily two weeks per `stop_gate_status`; probe battery recorded across ≥2 coverage bands; skills pack v1 revised from logged friction; ≥5 of last 7 days show Phase-D tool events.
