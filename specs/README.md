# Spec-Kit Tracking (authoritative for Phases C–E)

**Status (2026-08-19): tracking switched to spec-kit for Phases C, D, E.** The per-feature
`tasks.md` files are the source of truth for phase C+ work — checkboxes there are live state.
**Phases A+B remain beads-run until closed** (`kata-ph-b` epic + `kata-bvf` gate); do not drive
Phase B from `specs/001-*/tasks.md`. An agent may still be running beads on A/B — treat bead
state as live for those phases only. Once `kata-bvf` closes, beads becomes a read-only archive.

## Feature map

| Spec-kit feature | Tracking | Former beads epic |
|---|---|---|
| `001-phase-b-obsidian-render` | **beads (until kata-bvf closes)** | kata-ph-b |
| `002-phase-c-prose-search` | **spec-kit tasks.md** | kata-ph-c |
| `003-phase-d-teacher-loop` | **spec-kit tasks.md** | kata-ph-d |
| `004-phase-e-media-overlay` | **spec-kit tasks.md** | kata-ph-e |
| `005-mcp-assignment` | **spec-kit tasks.md** | — (no beads history) |
| `006-teaching-method` | **spec-kit tasks.md** | — (no beads history) |
| `007-setup-observability` | **spec-kit tasks.md** | — (no beads history) |
| `008-browser-companion-check` | **spec-kit tasks.md** | — (no beads history) |
| `009-asbplayer-bridge-in-process` | **spec-kit tasks.md** | — (no beads history) |

`008-browser-companion-check` — installer doctor detection of learner-installed browser
companions (Yomitan/asbplayer extensions, mokuro bridge readiness) with a detect +
guide-to-install flow. Chrome forbids silent extension installs, so scope is presence
check + Web Store handoff + re-check; no MCP tool, no schema change. Specified
2026-08-24 (spec/plan/research/quickstart/tasks; no data-model, no holdout — plan.md
§Deliberate omissions records why).

`009-asbplayer-bridge-in-process` — retire the external Go WebSocket bridge
(`asbplayer_launch.py` runs `go run main.go` from a configured checkout and needs Go on
PATH) and host `ws://127.0.0.1:8766/ws` inside Katagiri instead. Clean cutover: one new
aiohttp-backed module reimplements the bridge's protocol — six server→client JSON
commands correlated by `messageId` (5 s deadline), five relay endpoints, text
`PING`/`PONG`, plus the AnkiConnect proxy on `POST /` with addNote-intercept mining and
byte-exact passthrough. Protocol frozen at asbplayer 1.20.2 + local commit `37495e22`
(F-05 playback-state + HOST bind), transcribed with `main.go` line citations in
research.md; upstream issue #1087 is open/unmerged and re-checked before shipping.
Zero MCP contract growth, `media_asbplayer.py` untouched. An in-process listener needs
`HTTP_SERVER_ALLOWLIST` (and likely `HTTP_CLIENT_ALLOWLIST`) entries and a new runtime
dependency — both escalated to the user under D-47 precedent, not pre-decided. Specified
2026-08-24 (spec/plan/research/quickstart/tasks; no data-model, no contracts, no holdout —
plan.md §Deliberate omissions records why, and what differential gate replaces it).

Phase A closed pre-migration; record in docs/dev-plan.md, docs/audit-log.md, closed beads.
Historical bead IDs survive in tasks as `[was: kata-*]` for traceability.

## Recommended spec-kit workflow (per feature)

1. Point at the feature: set `$env:SPECIFY_FEATURE_DIRECTORY = "specs/00N-..."` (note: the env
   var is `SPECIFY_FEATURE_DIRECTORY`, not `SPECIFY_FEATURE`) or edit `.specify/feature.json`.
   `setup_tasks.py --json` was observed to crash with UnicodeEncodeError on this machine's
   cp1252 console (2026-08-19) — set `PYTHONUTF8=1` when running the .specify scripts.
2. `/speckit-analyze` after any spec/plan/tasks edit — consistency check, non-destructive.
   Caveat: it is single-feature; cross-feature references (phase-entry checks) are reviewed by
   hand, the tool never sees them.
3. **`/speckit-implement` is NOT the execution engine for the lane model.** It executes all of
   tasks.md itself, sequentially, in one session, and knows nothing about worktrees or
   checkpoints. Use it only scoped to a single taskgroup at a time (stop at each checkpoint),
   or treat tasks.md purely as the ledger that the orchestrator (dispatching per-task
   subagents) updates. Completed boxes: `[x]` (accept `[X]` — speckit-implement writes
   uppercase).
4. `/speckit-converge` at phase end if drift is suspected between code and artifacts.
5. New decisions still land in docs/decisions-ledger.md first; constitution and specs follow.

## Execution model (applies to 002/003/004 tasks.md)

- **Taskgroup = merge unit.** Every task in a group fully done + tests green + merged before
  the next group starts. No task spans groups. Checkbox flips happen in the same session as
  the completing merge.
- **Worktree lanes.** Tasks marked with a `wt/...` lane run in a git worktree branched from
  the default branch — **`main`** since 2026-08 (older feature docs may still say `master`) —
  at their taskgroup start. A lane edits only its
  lane-owned files (listed in each feature's Workfile & conflict map). Lanes are conflict-free
  by construction.
- **Worktree bootstrap (Windows, this repo).** (a) `.venv/` is gitignored — a fresh worktree
  has no venv; run tests/scripts via the primary checkout's `.venv` (absolute path) or
  provision one. (b) `git config core.hooksPath` points absolutely at `.beads/hooks`, so beads
  pre-commit/post-checkout hooks fire in every worktree; the lane has no beads DB, expect
  "database not initialized — skipping" noise (needs `bd` on PATH), or unset `core.hooksPath`
  per worktree. (c) `.claude/settings.json` is tracked — sessions opened inside a lane also get
  the `bd prime` hook; its beads guidance does not apply to phase C+ work.
- **Hot files** (`src/katagiri/tool_registry.py`, `src/katagiri/mcp_server.py`; plus
  `media_channel.py` after its Phase-E freeze) are edited only in serial-on-master tasks, never
  in a worktree. specs/003 T003 installs the per-phase-fragment / per-module-block seam that
  keeps these serial diffs one-liner-sized.
- **Context efficiency.** Every task carries a Write list and a Read list; the Read list is the
  complete context a fresh agent needs. Dispatch one subagent per task (or per lane), give it
  only the task text + Read list.
- **Gates are serial.** *-verify and stop-gate tasks run on master after all lanes merge.
- **Task-label legend.** `[was: kata-*]` (002/003/004) = retired historical bead ref;
  `[Bead: kata-*]` (001 only) = still-live bead. `[Gate]` in the story slot is a local
  extension of the template's `[USn]` labels — `/speckit-analyze` may flag gate tasks as
  unmapped to a story; that noise is expected.

## Layer roles (unchanged)

- **Vision / constraints**: `.specify/memory/constitution.md` — distilled from docs/dev-plan.md
  v1.1 + docs/decisions-ledger.md. Ledger + audit-log stay the authoritative reasoning record.
- **What/why per phase**: `specs/NNN-*/spec.md`
- **How per phase**: `specs/NNN-*/plan.md` (+ research.md, quickstart.md = the *-verify runbook)
- **Work items**: `specs/NNN-*/tasks.md` (authoritative for C–E)

Deliberately not duplicated into spec-kit: docs/db-schema.md (data model),
src/katagiri/tool_registry.py (contracts), docs/audit-log.md (research detail).

## Beads retirement checklist (run when kata-bvf closes) — EXECUTED 2026-08-21

> Done at Phase B close (D-35 waiver): 001 tasks.md reconciled, CLAUDE.md + AGENTS.md
> beads rules replaced with spec-kit-only wording, `bd prime` SessionStart hook removed,
> kata-duw closed, and ALL remaining beads closed (stale C/D/E duplicates + archive
> residents) at user request. `.beads/` stays as a read-only archive for `[was:]` refs.

1. Reconcile specs/001 tasks.md checkboxes against final `bd list` state (archive accuracy).
2. Update CLAUDE.md + AGENTS.md: drop the now-dead phase-A/B beads rule (the phase-scoped
   split was already applied 2026-08-19); keep both files in sync (they are independent files).
3. Remove the `bd prime` SessionStart hook from .claude/settings.json.
4. Close/supersede kata-duw; keep the beads DB as a read-only archive (no export needed —
   `[was:]` refs point into it).

## Beads without a spec-kit home (stay in beads archive)

- `kata-mz2` — Milestone A user-side manual steps. Outside agent authority; not a feature.
- `kata-ph-a.1` — module extraction from mcp_server.py; partially delivered by specs/003
  T003 (registration seam) and T020 (stop_gate extraction); remainder (search/security) stays
  optional P3.
- `kata-duw` — the migration itself; close at retirement step 4.
