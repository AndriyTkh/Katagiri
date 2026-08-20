# Quickstart: 006 validation (TG0 smoke → entry gate → 006-verify)

Three checks, in the order the feature ships them. The first runs on the day TG0 merges; the second is calendar-bound; the third closes the feature.

## Prerequisites

- Phase D merged to the current integration branch (session tools, lesson memory, intelligence, stop gate).
- `.claude/skills/katagiri-study/SKILL.md` present (the executable pack) plus its prose mirror `docs/katagiri/katagiri/90-meta/skills-pack-v1.md`.
- `docs/katagiri/katagiri/10-course/curriculum.md` present with its Phase-0 section and "Node format" block.
- Fixture event logs for each entry-gate shape (9 days; 10 days / 5 scored; 10 days / 6 scored / 2 dictations; the pass case). Fixtures only — never live personal data (constitution V).
- For TG4+: a scratch database to apply migration 0002 against, and the backup-before-migrate path exercised.

## 1. TG0 smoke (ungated, day one)

```bash
uv run pytest tests/test_events_backup.py tests/test_intelligence.py -k "snapshot or curriculum" -ra
```

```bash
uv run python -m katagiri.backup create --vault
schtasks /Query /TN "Katagiri Daily Backup"
```

### Expected outcomes (TG0)

1. `VAULT_SNAPSHOT_EXTENSIONS` contains `.md`, `.jsonl`, `.mp3`, `.wav`; a fixture vault containing one `.mp3` and one `.wav` snapshots them, and `.derived/` plus `local/` stay excluded.
2. The daily backup scheduled task exists **and one real run is verified** (a dated snapshot file on disk, not just a registered task).
3. Kana node blocks import from `curriculum.md`: `item` rows exist for the authored kana ids, edges are reported with their source line, and a re-run writes nothing (idempotent).
4. The skill prose defines KANA mode, the Phase-0 suspensions, coverage-unit = unread kana, the reserved Phase-0 dictation topic slug, the staged kana gates, and the modality ladder. Checked by reading the file — TG0 asserts no new code behaviour beyond items 1–2.
5. One real KANA session runs end to end and closes with a dictation-carrying lesson; the day qualifies under the **existing** study-day rule (artifact route, zero minutes claimed).

## 2. Entry gate (calendar-bound)

```bash
uv run pytest tests/test_stop_gate_d6.py -ra
```

Then, against the real log:

```bash
uv run python -c "from katagiri import db, stop_gate; conn=db.connect(); print(stop_gate.stop_gate(conn))"
```

### Expected outcomes (entry gate)

- 9 qualifying days → FAIL naming the **day-count** criterion.
- 10 days, 5 with scored observations → FAIL naming the **observation** criterion.
- 10 days, 6 scored, 2 dictation artifacts → FAIL naming the **dictation** criterion.
- Pass fixture → PASS.
- In **every** fixture, the pre-existing 14-in-18 verdict and probe-battery criteria are evaluated and byte-identical to their pre-change values. A changed existing verdict is a failure, not an improvement.
- The ledger row and the constitution bump to 1.1.0 are already committed when these tests first run.

**Contract-touching taskgroups (TG2–TG7) do not start until the real-log run above reads PASS.**

## 3. Post-gate checks, per taskgroup

```bash
uv run pytest tests/test_session_tools.py tests/test_intelligence.py tests/test_today.py -ra
```

### Expected outcomes

1. `prescribe()` returns the curriculum-reachability **topic** rung when no next step, revisit or open thread applies — and still returns exactly one action, never a list.
2. Every action payload carries `caps{new_words_left, grammar_left, listening_reps_left}`.
3. Eight mining events today → `add_vocab` refuses the ninth with a structured error naming the cap and the count read.
4. Two grammar introductions this week → `grammar_left == 0`.
5. A listening block logged twice → one event; `import_study_log` over the same day adds no duplicate; reps read back as reps, minutes absent.
6. Migration 0002 applies additively, is idempotent, does not set `user_version` itself, and runs behind backup-before-migrate.
7. Unanchored item requested for an A0 production drill → withheld, with `text-only-not-for-A0-production` as the stated reason.
8. Curriculum node with JF can-do / Irodori / Tae Kim tags imports; removing a tag from the file reports an **orphan** and deletes nothing.
9. Construction trajectory is computed from `observation` rows only; a U-shaped dip appears in the trajectory and lowers no gate.
10. Worksheet write outside `<vault>/.derived/` → refused; write over a file lacking `generated: true` → refused; read-back arrives as untrusted data.
11. `tests/test_mcp_tools.py` congruence green with **zero new ToolSpecs** across the whole feature.

## 4. 006-verify (cold subagent, feature close)

```bash
uv run pytest tests/test_006verify.py -ra
```

A fresh agent with no context beyond tool descriptions, against frozen fixtures:

1. Runs a KANA session and closes it with a dictation artifact under the reserved slug.
2. Hits the daily new-word cap and reports the refusal rather than working around it.
3. Builds an A0 production drill and offers only audio-anchored items.
4. Writes a worksheet to `.derived/` and reads a filled-in one back, treating its content as data.
5. Cumulative: scenarios A..D still green.

Max two fail→fix→rerun cycles (D-23); residual findings go to the ledger, not into a third cycle.

## Learner metric (manual, feature close)

Read from the real event log: dictation artifacts per week ≥1 and pitch-marking records per week ≥1 for the first post-gate month, one monologue per month, and zero days over the new-word cap. A green 006-verify with a failing learner metric is a **failed** close (constitution V).
