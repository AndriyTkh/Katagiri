# Implementation Plan: 006 — Teaching Method

**Branch**: `006-teaching-method` | **Date**: 2026-08-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-teaching-method/spec.md`; authoritative upstream = council plan v3 §"Feature 006 — teaching-method" + §"Governing principles".

**Task tracking**: tasks.md (authoritative; spec-kit, no beads history for this feature).

## Summary

The teaching method itself: what is taught, in what order, at what dose, and on what evidence. It splits hard along one line.

**TG0 is prose and data only and lands immediately** — a KANA mode in the study skill, Phase-0 suspensions, kana curriculum rows through the existing importer, and the ops prerequisites (backup task installed and verified, vault snapshot widened to `.mp3`/`.wav` before the first audio artifact exists). No contract changes, so nothing gates it and the learner can study the day it merges.

**Everything else is gated** on an evidence-quality entry gate — ≥10 study days, ≥6 with a scored observation, ≥3 with a dictation artifact — because every remaining decision (dose caps, input metrics, audio anchoring, kanji budget) is a calibration against evidence that does not exist yet. Post-gate work is a sequence of additive contract changes: a curriculum rung and a caps block inside the existing `prescribe()`, a cap refusal on `add_vocab`, listening reps into the existing `study_session` series, migration 0002 for audio anchors, curriculum material tags in the existing parser, construction trajectories derived from existing observations, plus the prose-side cadence, kanji and worksheet policies. **Zero new ToolSpecs, additive-only, ledger row before code, every time.**

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`), uv-managed

**Primary Dependencies**: none new. `mcp>=2,<3`; existing tokenizer / known_set / event-log substrate; existing vendored kanjium accents (pitch-marking check); vendored Irodori (never committed) + Tae Kim extracts (committable, CC BY-NC-SA) added under the existing `vendor/` + `CHECKSUMS.sha256` policy (D-10)

**Storage**: existing SQLite. One migration this feature — `0002_*.sql`, additive audio-anchor references, discovered by the existing `NNNN_name.sql` runner (backup-before-migrate already in place; the runner refuses a migration that sets `user_version` itself)

**Testing**: pytest; group placement per `tests/conftest.py` (`compile` → general → `mcp`); cold-subagent 006-verify scenario at the close

**Target Platform**: Windows 11, stdio MCP, stderr logging, `PYTHONUTF8=1`

**Project Type**: single project

**Constraints**:
- **Zero new ToolSpecs.** Contract changes are additive arguments/output keys on `start_session`, `add_vocab`, `stop_gate_status` only; each carries a D-24 contract-diff justification filed *before* the code task.
- **Stop-gate changes additive only.** The 14-in-18 count and probe battery stay necessary (D-19); the 006 entry gate adds criteria and changes no existing verdict.
- **Single prescriber.** Topic selection is a rung in `prescribe()`, not a second planner; the five cut tools (`next_topic`, `plan_revision`, `mark_topic_progress`, `run_drill`, `check_answer`) are cut, not deferred.
- **No new tables.** Construction state is derived from `observation` rows; curriculum material refs are node attributes on existing `item` rows.
- **No TTS, no STT, no voice** anywhere in this feature (F-02 revised / F-03 / new STT deferral).
- **Agent never writes vaults.** Worksheets go through `today_export`'s `.derived/` confinement + `generated: true` guard; read-back is the GET-only proxy, arriving as untrusted data.
- **Prose is prose.** TG0 changes no behaviour in code beyond the two ops items; a rule that must bind mechanically is a post-gate task, not a stronger sentence.

**Scale/Scope**: 9 taskgroups, 42 tasks, one of which (TG0) is releasable on day one and one of which (the entry gate) is calendar-bound. Bottom-up estimates and >8h splits mandatory before build (D-29).

## Constitution Check

Against `.specify/memory/constitution.md` v1.0.0, and stating the two amendments this feature itself files.

| Principle | Status | Note |
|---|---|---|
| I Personal tool, MCP ceiling | PASS | no new surface at all; the worksheet loop deliberately adds no MCP tool, and the frontend stays Claude Code + skill over stdio |
| II OSS-first | PASS | Anki still owns item scheduling; construction trajectories are derived reads, not a scheduler; Irodori/Tae Kim/kanjium are integrated data, not reimplemented content |
| III Event log sacred | PASS | input logging appends to the existing `study_session` series with a dedupe key; no edit path; migration 0002 is additive and runs behind backup-before-migrate |
| IV Study-first, gated progression | **AMENDS** | the 006 entry gate adds criteria to the D-19 machinery. Ledger row + **MINOR bump to 1.1.0** filed in TG1 *before* the gate code (T008 → T009) |
| V Two-gate verification | PASS | 006-verify (cold subagent) + the learner metric read from the real event log at the close |
| VI Security hardening | PASS | worksheet read-back is untrusted data through the GET-only proxy; writes confined to `.derived/` with server-named files; no new listener, no network fetch |
| VII Tool-contract stability | PASS (with filings) | additive-only; zero new ToolSpecs; every diff has a contract-diff justification row before its code task |
| Tech constraint: whole schema in one migration (D-12/D-27) | **EXCEPTION** | migration `0002` is required by FR-018. Stated exception + ledger row + **MINOR bump to 1.2.0** filed in TG4 *before* the migration (T022 → T023) |

Two amendments, both filed before the code they authorise. No unfiled violation.

## Project Structure

### Documentation (this feature)

```text
specs/006-teaching-method/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── checklists/requirements.md
└── tasks.md
```

Data model: [docs/db-schema.md](../../docs/db-schema.md) — extended once, by migration 0002 (audio-anchor refs) and by the `gate_evaluation`-adjacent documentation of the entry-gate criteria. Contracts: [src/katagiri/tool_registry.py](../../src/katagiri/tool_registry.py), additive only, no new specs.

### Source Code (repository root)

```text
.claude/skills/katagiri-study/SKILL.md   # TG0 KANA mode, Phase-0 suspensions, modality ladder; TG6 cadence/kanji policy
docs/katagiri/katagiri/90-meta/skills-pack-v1.md   # prose mirror of the above (kept in sync)
docs/katagiri/katagiri/10-course/curriculum.md     # TG0 kana node blocks; TG5 material tags
docs/katagiri/katagiri/70-drills/                  # TG6 dictation / pitch-marking / monologue cadence notes

src/katagiri/
├── backup.py            # TG0 — VAULT_SNAPSHOT_EXTENSIONS += .mp3/.wav
├── stop_gate.py         # TG1 — additive 006 entry-gate criteria beside the 14/18 mechanics
├── session_tools.py     # TG2 — prescribe() curriculum rung + caps block; add_vocab cap refusal
│                        # TG3 — listening-rep logging into the study_session series
├── migrations/0002_*.sql # TG4 — additive audio-anchor references
├── intelligence.py      # TG4 A0 production pool restriction; TG5 curriculum tag parsing + construction trajectory
├── today_export.py       # TG7 — worksheet writer on the existing .derived/ pattern
└── tool_registry.py     # additive output/arg strings only (serial-on-master tasks)

vendor/                  # TG5 — Irodori (never committed) + Tae Kim extracts, download scripts + CHECKSUMS.sha256

tests/
├── test_events_backup.py      # existing home for backup/snapshot tests — extension case
├── test_stop_gate_d6.py       # extended with the 006 entry-gate cases
├── test_session_tools.py      # caps + refusal + rung cases
├── test_intelligence.py       # tags, orphan semantics, construction trajectory, anchored pool
├── test_today.py              # worksheet confinement cases
└── test_006verify.py          # NEW — cold-subagent close
```

**Structure Decision**: no new modules. Every change lands in the module that already owns the concern, which is what keeps "zero new ToolSpecs" honest — a new module tends to grow a tool. The cost is that `session_tools.py` and `intelligence.py` are single-lane files for the whole feature; the conflict map in tasks.md enforces that.

## Execution model

- **Taskgroup = merge unit** (specs/README.md): every task in a group done, tests green, merged before the next group starts.
- **TG0 merges alone and first**, on the current integration branch, and is not blocked by anything in this feature.
- **TG1 is calendar-bound.** Its final task cannot be checked off until the log really shows the days; the wall-clock wait overlaps feature 005 work rather than idling.
- **TG2–TG7 are strictly after TG1's PASS.** Each opens with a governance task (ledger row / contract diff / constitution bump), then lane work, then one serial registration task on master for the hot files.
- **Hot files** (`tool_registry.py`, `mcp_server.py`) are only ever edited in the serial registration tasks.

## Complexity Tracking

| Item | Why it is not simpler | Mitigation |
|---|---|---|
| Migration 0002 breaks the one-migration rule | audio anchoring cannot be derived; production honesty at A0 needs a stored reference | stated constitution exception + ledger row before the migration; additive columns only; existing backup-before-migrate |
| Two gates now govern one code path (D6 stop gate + 006 entry gate) | they answer different questions (readiness for Phase E vs. evidence quality for teaching-method design) | one module, additive criteria, both reported through the same existing tool; tests assert the old verdict is untouched |
| Prose and code both express the same dose rules | prose ships today and binds nothing; code binds but is gated | TG0 prose states the caps as policy and says they are not yet enforced; TG2 makes them refusals and updates the prose to say so |
