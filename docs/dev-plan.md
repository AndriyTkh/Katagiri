# Katagiri — Development Plan v1.1

Date: 2026-08-19. v1 approved by user, then revised per the 7-role review panel
(audit-log **Round 5** — all 17 consolidated findings accepted; two sub-items deferred).
Supersedes audit-log Round 4 synthesis as the execution sequence; inherits its technical
decisions. Phase order fixed by user: core+read-MCP → Obsidian render → search → learning
tools → overlay.

Status: **v1.1 — revised post-review. Next step: bottom-up estimates + beads.**

Companion: [decisions-ledger.md](decisions-ledger.md) — decisions, deferrals, excluded
moonshots, and the review-coverage table (reviews are incremental; never re-review settled
scope).

## Standing constraints (non-negotiable)

- Personal tool, English↔Japanese, MCP server is the build ceiling. No app, no public service.
- OSS-first. Anki owns scheduling; `answerCards` banned; Anki writes = `addTags`/`setDueDate`
  only, `exportPackage` verified before any batch, hard-fail closed if unavailable.
- Vault is prose; DB is state. Event log is the single non-reconstructible asset.
- **Study-first rule (Round 5)**: ~20–30 min study before any build session; no building on a
  zero-review day. Study + event logging start Phase A day one, not Phase D.
- Timeline is counted in build-only hours; study time is not billable to the build.

## Verification protocol (every phase)

Two gates per phase, both required:

1. **Cold-subagent pass** — a fresh agent with no context beyond tool descriptions runs the
   phase's scripted scenarios **against a frozen fixture set** (mini `collection.anki2`, mini
   vault, JMdict subset — not live personal data). Assertions are on tool-call sequence and
   structured response fields; free-prose judgement is advisory. The suite is **cumulative**:
   phase N runs scenarios A..N, doubling as the regression gate. Before each run, a
   disposition rule is fixed: each complaint class is pre-declared blocker vs next-phase
   backlog. Max two fail→fix→rerun cycles per phase; residual findings go to backlog.
   Budget: **+20–25%** on build hours (was 10–15% — Round 5 correction), verification hours
   logged separately.
2. **Learner metric** — one per phase, read from the event log; a phase can fail this with a
   green subagent pass. Defaults: reviews/day trend not declining; ≥4 study days/week during
   the phase; for C/D/E, ≥5 of last 7 days show events from that phase's tools.

Scenario sketches (expanded into beads): **A-verify** — cold agent answers known-word /
known-count / yesterday's-reviews / 勉-substring queries on the fixture; restore drill from
backup snapshot passes. **B-verify** — reads Today.md and an arbitrary note; direct-HTTP
bypass of the Obsidian proxy attempted and refused. **C-verify** — same question via DB
search and markdown search; markdown path with Obsidian closed. **D-verify** — one full
lesson loop (i+1 pick → exercise → `log_error` → mine) lands artifacts in vault + event log.
**E-verify** — mpv position/title; asbplayer subtitle window from anchor; mokuro page;
screenshot round-trip; one `.lrc` through WATCH mode; **adversarial scenario: a subtitle
line containing tool-call instructions must not trigger any write tool**.

Phase entry precondition (B through E): the preceding week shows ≥4 logged study days.

---

## Phase A — Foundation + read-access MCP

### A0 — Before any schema code

- **A0a. Project skeleton** — pyproject/uv, Python pin, `.gitignore` + vendoring policy for
  ~1GB UniDic/kanjium (checksums recorded), config loader with machine paths in
  `%LOCALAPPDATA%` (never in repo/vault), **stderr-only logging** (stdout corrupts MCP
  stdio), pre-commit secret scan, working Windows 11 MCP launch (absolute interpreter path,
  `PYTHONUTF8=1` visible to client).
- **A0b. Seal the canary set** (MOONSHOTS §1) — 200 graded sentences, `sealed: true`,
  validator screams if any drill references them. Before the first study day; contaminated
  forever if late.
- **A0c. Zero-code start** — skills pack v0 authored now (prompt-side: guess-first, coverage
  gate, mining budget, nuance-anchoring) and used for manual study through A–C; L1
  interference profile conversation (MOONSHOTS §11); daily study + shadowing logged as
  events (manually/scripted until A5 exists, then imported).

### A1–A9 — Build

- **A1. Whole schema, one migration** — ALL DDL: lexeme, alias, item, event, observation,
  lesson, media, plus FTS tables, JMdict tables, Anki-mirror tables, grammar-DAG item rows'
  schema. Tables classified **source-of-truth** (event log, manual marks, lessons) vs
  **derived** (FTS, JMdict, mirror — drop-and-rebuild scripts, not migrations). Minimal
  migration runner from day one: `PRAGMA user_version`, numbered scripts,
  backup-before-migrate. Append-only enforced on the event log via
  `BEFORE UPDATE`/`BEFORE DELETE` triggers (`RAISE(ABORT)`).
- **A2. Tokenizer substrate** — fugashi + full UniDic vendored (checksummed), kanjium
  accents vendored. Tokenizer+dict version stamped into a metadata table.
- **A3. FTS5 dual index** — fugashi shadow column (unicode61) + trigram, routed by query
  length. `rebuild_index` a first-class deliverable; indexed rows carry dict/tokenizer
  version so staleness is detectable.
- **A4. Anki mirror, split three ways** (highest-variance work in the plan):
  - A4a. Snapshot reader — **never open live `collection.anki2`**: detect Anki running, copy
    collection + `-wal`/`-journal` to scratch, open `mode=ro&immutable=1`, `integrity_check`,
    fail loud on unknown schema version. AnkiMorphs CSV export = degraded fallback.
  - A4b. AnkiMorphs ingest (its SQLite + known-morphs CSV).
  - A4c. **Morph→lexeme normalizer** — UniDic lemma/inflection → JMdict entries across
    kana/kanji/okurigana variants. Accuracy target vs ~200 hand-labelled morphs. This is the
    hidden hard piece; `known_word()` correctness depends on it.
  - A4d. **Recurring sync job** — idempotent incremental Anki→event-log sync (records dated
    `review_batch` events), Windows Task Scheduler + on-launch catch-up. Feeds streaks,
    Today.md, and the D6 gate.
- **A5. known_set + event log** — merge of mirror + AnkiMorphs + manual marks (`ivl ≥ 21d`);
  every mutation an event. **Backup task**: scheduled `VACUUM INTO` snapshots + vault copy +
  one rehearsed restore (drill repeats in A-verify).
- **A6. MCP server + tool registry + hardening** — `mcp>=2,<3`, plain functions + thin
  adapter, **stdio transport only** (no network listener). Checked-in **tool registry**
  (name, args, output shape, stability tier); post-A6 changes additive only; unimplemented
  tools raise, never return plausible stubs. Read tools: `known_word`, `known_set_stats`,
  `lookup` (JMdict+pitch), `search_db` (the definitive search — no later "proper" rewrite),
  `recent_events`, `stop_gate_status` (mechanical PASS/FAIL + failing criterion).
  Hardening: verify third-party ports (:27123, :8766, :19633, :8765) bound to 127.0.0.1,
  Windows Firewall inbound deny; tokens never in tool outputs, errors, or the event log.
- **A7. JMdict** — jmdict-simplified → derived tables, sense-level.
- **A8. Yomitan known-dict generator** (pulled forward from Phase D — first felt daily
  value: every reading surface colors known/unknown). Regen **drift-triggered** (known_set
  Δ > 150 words), prints a 60-second numbered reimport checklist, each regen/skip logged.
- **A9. Minimal sensei letter** — streak, review counts, new known words, from the event
  log. Enriched later in D5.

**Thin vertical slice checkpoint (week 2–3)**: A1 + minimal A2 + A6 with a hand-seeded
fixture known_set answers a real query end-to-end. The fixture known_set also unblocks
B/C/D work whenever A4 stalls.

**Milestone A (binary checklist)**: A-verify green on fixtures (incl. restore drill) ·
Yomitan dict imported and coloring real pages · sync job produced ≥7 daily `review_batch`
events · learner metric met · ledger updated.

## Phase B — Obsidian render

- **B1. Aggregate exporter as section registry** — each phase registers a section renderer;
  later phases extend, never rewrite. Phase-B `Today.md` defined strictly from data that
  exists now: Anki due count, streak, known-count trend, weakest morphs, resume pointers.
  Writes confined to `.derived/` with a generated-file header; refuses to overwrite any
  file lacking the header.
- **B2. Obsidian access, proxied** (amends D-11) — Katagiri holds the
  obsidian-local-rest-api token (:27123) and exposes GET-shaped vault tools only. The
  plugin's built-in MCP endpoint is **never registered with the agent** (it exposes
  PUT/PATCH/DELETE and `command_execute` behind the same token).

**Milestone B**: B-verify green (incl. direct-HTTP bypass refused) · Today.md is the note
actually opened ≥5 of last 7 days · ledger updated.

## Phase C — Prose search

C1 (DB search) was folded into A6 (Round 5 — tool-contract stability). Phase C is:

- **C2. Katagiri markdown search** — own indexer over vault export + hand-written notes
  (frontmatter-aware, incremental re-index), served as MCP tools; independent of Obsidian
  running. Sized explicitly (est. 8–15h — Round 5 developer figure), not inherited.

**Milestone C**: C-verify green — same question answered via `search_db` and markdown
search, the latter with Obsidian closed · learner metric met.

## Phase D — Teacher loop

Execution order inside the phase is felt-value-first: D3 → D4 → D5 → D2 (IDs kept stable;
D1 moved to A8).

- **D2. Vocabulary + grammar intelligence** — real `coverage(text)`; **grammar DAG imported
  from curriculum.md (`prereqs`/`unlocks`) into `item` rows**; `find_i_plus_one` gated on
  grammar reachability AND known-word coverage (not vocabulary alone — Round 5 teacher
  HIGH); comprehension-debt ranking; difficulty-for-me (jreadability + BCCWJ + JLPT +
  coverage %).
- **D3. Authoring + session tools** — `add_vocab`, `log_error`, `triage_inbox`,
  `gen_exercise`, `build_sentences`, **`log_observations`** (mandatory fields: `unassisted`
  flag, coverage band, `rubric_version` — the unassisted pass-rate source), **`log_lesson`
  / `lessons(topic?, unresolved_only)`**, **`start_session`** (returns exactly one
  prescribed action, not a dashboard). All writes through the event log; media-derived text
  arrives in an untrusted-data envelope and write tools require echo-back confirmation on
  such content.
- **D4. Skills pack v1 + lesson memory** — evidence-driven revision of A0c's v0 after weeks
  of real use. Lesson memory spelled out: `unresolved[]`, `next_step` (written at close,
  read at open), `revisit_after` (topic-level spacing — Anki schedules items, this
  schedules topics); surfaced in Today.md via B1's registry. **Tired-mode minimum session**
  defined (reviews + one mined word) — counts toward the gate. WATCH/REVIEW mode content.
- **D5. Sensei letter, full** — extends A9 with errors, unresolved threads, probe results.
- **D6. ⛔ STOP-GATE (restated per Round 5)** — **14 study days within an 18-day window**
  before Phase E code. Study day = ≥10 min or ≥1 logged artifact (concrete event-type
  count, not reinterpretable); declared illness/travel pause allowed; `stop_gate_status`
  (A6) prints PASS/FAIL + failing criterion; plus one outcome criterion: one canary probe
  battery run with unassisted pass-rate recorded across ≥2 coverage bands. If unmet twice →
  explicit re-plan, not silent limbo. **Exception**: the write-only mpv seek logger (E6
  slice, no MCP surface, no agent) may be built/run any time after A5 — rewind data is
  worthless retroactively.

**Milestone D**: D-verify green · loop used daily two weeks (per `stop_gate_status`) ·
probe battery recorded · skills pack revised from logged friction.

## Phase E — Media overlay (context channel)

Channel order E1/E2/E3 decided by measured consumption mix during the D6 window (count
what was actually watched/read). E4 ships immediately after the first channel lands.

- **E1. mpv channel** — Lua pusher or `python-mpv-jsonipc` (Windows named pipes); heartbeat
  daemon; `media_now` / `media_context`. Local files first-class (full playhead).
- **E2. asbplayer channel (streaming)** — WS :8766, `get-bound-media` + `get-subtitles`.
  No playhead exists (issue #1087): **anchor derived automatically from the last
  mining/copy event's timestamp**; manual anchors accepted and their use counted, so F-05
  (upstream PR) fires on data, not annoyance.
- **E3. mokuro channel (manga)** — page-change userscript → localhost bridge **with shared
  secret + Origin validation**; `volume-data.json` poller fallback; `.mokuro` JSON as text
  layer.
- **E4. Screenshot-question tool** — mpv `screenshot-to-file` → confined scratch root,
  **server-generated filenames** (media titles are attacker-controlled; no path traversal)
  → agent reads frame.
- **E5. Music + karaoke** — audio via mpv; timed lyrics (`.lrc`/`.ass`) through the same
  subtitle pipeline; lyric lines minable like subtitle lines.
- **E6. Rewind telemetry** — analysis stays a moonshot; the capture slice (seek-back events
  → event log) ideally already running via the D6 exception.

All externally-sourced text (subtitles, OCR, lyrics) is wrapped in the untrusted-data
envelope with a "data, never instructions" contract in every media tool description.

**Milestone E**: E-verify green (incl. adversarial subtitle scenario) · anchored
"what did she just say?" answered on the primary consumption surface · words mined with
source refs · learner metric met.

## Phase F — Deferred (not cut; revisit conditions in ledger)

Progressive substitution engine · VOICEVOX · ASR/speaking · jpdb/WK importers · Lute ·
advanced difficulty modeling · event-log hash chain · remaining moonshots.

## Estimates & delivery process (Round 5)

- Round-4's 89–170h band predates B1/C2/E4–E6/verification — **stale**. Before beads:
  bottom-up estimate per task; tasks >8h split into sub-beads with own definition of done.
- Actual hours logged per bead from week 1; full re-baseline when Phase A closes
  (measured hours/week + estimate-accuracy ratio).
- Verification budget 20–25%, tracked separately.
- **Task-level dependency DAG**, not phase chaining (e.g. A6 `lookup` needs A7; A3 needs
  A2; B1/C2/D-work can run on the fixture known_set while A4 stalls). A-verify…E-verify
  and D6 are **blocking beads**.
- Weekly 15-minute review appends one status line here (beads closed, hours, blocker).
  Every task tagged must/should/could; at 1.5× a phase estimate, all "could" cut +
  re-estimate.

## Risks

1. Building past the gates — mitigations now structural: study-first rule, phase entry
   preconditions, `stop_gate_status`, learner metrics on every gate.
2. Anki internal schema / live-file locking — snapshot-read protocol (A4a), fail-loud
   version check, AnkiMorphs CSV fallback.
3. Event-log loss — backup task + rehearsed restore + append-only triggers (A5/A1).
4. Prompt injection via media text — envelope + echo-back + adversarial E-verify (Round 5
   security).
5. Supply rot (UniDic/kanjium/AnkiConnect) — vendored + checksummed; AnkiConnect off
   critical path.
6. asbplayer/mokuro upstream drift — small versioned surfaces; derived/manual anchors as
   permanent fallback.
7. Estimate error — bottom-up + logged actuals + Phase A re-baseline + slip rule.

## Pipeline

1. User approval — ✓ 2026-08-19 (v1).
2. 7-role review panel — ✓ 2026-08-19, Round 5 in audit-log; all findings merged (this
   document, v1.1).
3. Revision — ✓ this document.
4. Beads — ✓ 2026-08-19. 5 phase epics + 33 task beads (`kata-*` prefix, `bd ready` /
   `bd dep tree kata-evf`), task-level blocks-DAG wired, verify passes + D6 as blocking
   beads, must/should/could labels, priorities set. Ready set = A0a/A0b/A0c only, as
   designed.
5. **Per-phase, at implementation time**: bottom-up estimates (minutes) onto that phase's
   beads, split >8h tasks into sub-beads with own definition of done, write dedicated
   workfiles, and compute the parallel lanes from `bd ready` within the phase.

## Ops

- **Daily backup scheduled task (specs/006 TG0 T006)** — installed 2026-08-20:
  `schtasks /Create /TN "Katagiri Daily Backup" /SC DAILY /ST 21:00 /F /TR "cmd /c cd /d \"C:\ProjectsC\RandomPr\Katagiri\" && uv run python -m katagiri.backup create"`
  (the exact line `installer.schtasks_backup_command()` / the `backup.py` module docstring
  document). Task did not previously exist; `schtasks /Create` returned SUCCESS. Verified run:
  `schtasks /Run /TN "Katagiri Daily Backup"` returned Last Result `2` — in this dev session
  `uv run`'s package-sync step collided with a currently-running `katagiri-mcp.exe` holding its
  own console-script file open (file lock), not a fault in the task registration itself.
  Bypassing `uv run`'s sync step with a direct interpreter call —
  `.venv\Scripts\python.exe -m katagiri.backup create` — produced a real dated snapshot:
  `C:\Users\andri\AppData\Local\Katagiri\backups\katagiri.20260820T185300.db` (78,065,664 bytes,
  written 2026-08-20 21:53:02 local). Snapshot mechanics (VACUUM INTO, naming, retention) are
  unchanged code; only the trigger path differed from the schtasks-run path this once. Query
  and delete for reference: `schtasks /Query /TN "Katagiri Daily Backup"`,
  `schtasks /Delete /TN "Katagiri Daily Backup" /F`.

- **Single-writer discipline (specs/006 TG0 T007)** — one authoritative frontend process (MCP
  server or CLI) touches the learner DB per day; SQLite WAL plus `db.connect()`'s
  `isolation_level=None` + explicit `BEGIN IMMEDIATE` writes (`src/katagiri/db.py`) make a
  *single* concurrent writer safe and readers non-blocking, but they do not make two writer
  processes cooperative — a second writer contends for the same file lock, and a long-held
  transaction from one process can push the other into (or past) `BUSY_TIMEOUT_MS` and fail its
  write. The most likely accidental second writer is a second checkout: a git worktree opened
  for parallel lane work can quietly point at the same real DB/config and start its own MCP
  server or CLI session against it (`specs/README.md` "Worktree bootstrap" flags exactly this
  class of hazard for `.venv`/hooks; the same reasoning applies to the DB). Rule: before
  starting a second frontend process against the real (non-fixture) database, confirm no other
  Katagiri MCP server or CLI write session is already open against it. If two are found open
  at once, stop one immediately (the newer one, unless the older is clearly the stray) rather
  than letting both keep writing — do not attempt to merge divergent writes by hand.

## Weekly status log

(appended by the weekly review)

- 2026-08-19 — Phase C entry (specs/002 T001): entry criteria NOT met — kata-bvf open
  (technical checks green, 795 tests; Today.md adoption metric pending a week of real usage)
  and 0 logged study days in prior week (event log empty). User explicitly overrode the gate
  and ordered Phase C start; waiver recorded in decisions-ledger. kata-bvf close + adoption
  metric remain a parallel user-side item blocking nothing in C but required before D entry
  review.
- 2026-08-19 — Phase C complete (specs/002): md_search module (4 derived tables, incremental,
  frontmatter-aware, Obsidian-independent) + search_notes MCP tool. C-verify green (17/17 cold
  gate; full suite 847). SC-002 learner metric unmet (0 logged study days) — carried as
  user-side item with kata-bvf metric; both gate Phase D entry. Incident: vendor/unidic
  destroyed by junction-following worktree removal, restored same day from pinned-checksum
  re-download (638718c4…, bit-identical).
- 2026-08-19 — Phase D entry (specs/003): C-verify green — specs/002 TG-C4 complete (T009,
  T010 checked). Study-day prerequisite (≥4 logged study days prior week) WAIVED by user
  directive 2026-08-19 — agent-side testing done where possible; real-usage testing deferred.
  Mitigation: all Phase D work lands on integration branch `phase-d`, merged to `master` only
  after sufficient real testing.
- 2026-08-19 — Phase D TG-D3 checkpoint: US1 loop usable (11 MCP tools registered, suite 1141
  green, cold-subagent scripted lesson loop lands all six US1 event types in the event log).
  **D6 calendar clock (T022) starts today**: 14 study days in an 18-day window counted from
  2026-08-19. Vault half of the loop (Today.md lesson-memory section) is T010/TG-D4.
- 2026-08-20 — Phase D TG-D4 checkpoint: US2 (lesson memory + skills pack v1 + Today.md
  section), US3 (sensei letter errors/threads/probes paragraphs), US4 (vocab/grammar
  intelligence: coverage, DAG-gated i+1, comprehension debt, difficulty-for-me) all merged.
  14 new tools registered (`lesson_memory`, `coverage`, `find_i_plus_one`); suite 1461 green.
  Incident: intel worktree's vendored difficulty datasets (jreadability/BCCWJ/JLPT, gitignored)
  destroyed by `git worktree remove --force` before re-fetch; recovered same-session,
  bit-identical to pinned checksums — no data-integrity loss, but a reminder that
  `--force` on a worktree holding real (non-junction) untracked vendor data needs an
  explicit re-vendor step first, not after. tanos JLPT source is unversioned (checksum-only
  pin); BCCWJ is research/education-only licensed (correctly never committed).
- 2026-08-20 — Phase D D-verify gate PASS (specs/003 TG-D5, T018+T019): tests/test_dverify.py
  cold cumulative gate green on all 7 quickstart "Expected outcomes (D-verify)" — full fixture
  lesson loop lands artifacts in vault + event log; `log_observations` without `rubric_version`
  rejected; `start_session` returns exactly one prescribed action reflecting the prior
  `next_step`; unreachable-grammar sentence excluded from `find_i_plus_one` at 100% vocab
  coverage; media-derived write without echo-back refused; canary-referencing drill trips the
  validator; scenarios A..C still green. Full suite 1480 tests. Learner metric (D-23, required
  on every gate) read read-only from the real event log
  (`%LOCALAPPDATA%\Katagiri\katagiri.db`, via `events.recent_events`): **0 events total, 0
  Phase-D tool events since 2026-08-19, 0 distinct day_keys** — honest zero, not a shortfall
  in the code: every Phase D exercise so far has been agent/fixture-only, so no real-usage
  data exists yet. Same gap as the C-verify SC-002 finding, still covered by the 2026-08-19
  Phase D entry waiver. TG-D5 complete; TG-D6 (T020–T022) is next, and T022's 14-study-days-in-
  18-days window (clock from 2026-08-19) cannot advance until real study days are logged.
- 2026-08-20 — **Phase D CLOSED (user waiver on T022)**: user directed phase close at 005/006
  launch; `phase-d` branch fast-forward merged to `master` (8051309 + close commit). T022's
  live-gate criteria were NOT met — learner DB reset 2026-08-20 leaves 0 real study days and
  the 2026-08-19 clock unsatisfiable. Waiver closes the phase-D ledger only; the mechanical
  stop gate (`stop_gate_status`, T020/T021) still blocks all Phase E (specs/004) code until
  real 14/18-day evidence plus a recorded probe battery exist. Consumption-mix record (F-10)
  deferred to Phase E entry for the same reason. Next work: specs/006 TG0 + specs/005 TG-A.
