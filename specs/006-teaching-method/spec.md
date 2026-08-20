# Feature Specification: 006 — Teaching Method

**Feature Branch**: `006-teaching-method`

**Created**: 2026-08-20

**Status**: Active — **tasks.md is the task-tracking source of truth** (spec-kit; no beads history for this feature)

**Input**: Council plan v3 (5 roles × 2 adversarial rounds), section "Feature 006 — teaching-method" plus "Governing principles"; background in plan v1 §"Project context", curriculum-stack decision #5 and pedagogy sources #6. Where v3 contradicts v1/v2, **v3 wins** — this spec re-decides nothing.

**Shape of the feature**: one ungated prose/data taskgroup that lands *before the learner's first session*, then an evidence-quality **entry gate**, then contract-touching taskgroups that may not start until that gate reads PASS.

## Governing principles inherited (not re-argued here)

- **Policy lives in the katagiri server; every frontend is a thin disposable caller.** `start_session`/`prescribe()` owns what happens next, `log_*` records evidence, `stop_gate_status` judges readiness. No teaching policy is added to a frontend that the server does not own.
- **The consumer-voice frontend is deferred, not dead** (re-verified 2026-08-20): ordinary ChatGPT voice mode still cannot call custom MCP connectors, and connectors still cannot reach localhost — but ChatGPT desktop Voice (GPT-Live, shipped 2026-07-23) can drive Codex by speech, and Codex supports **local stdio** MCP servers (same posture as Claude Code today; no tunnel, no listener, constitution VI and D-22 intact). Whether voice-directed Codex invokes *arbitrary user-added* MCP servers is unconfirmed — an empirical test gates F-03. Voice returns via that path first (zero build, covered by existing Plus subscription); a Realtime-API layer over our own agent is the fallback only.
- **Daily tutor frontend stays** Claude Code/Desktop + the `katagiri-study` skill over stdio, unchanged.
- **Governance first.** Any stop-gate, schema, or contract touch gets a decisions-ledger row — and a constitution bump where the constitution speaks — **filed before the code task**. Stop-gate changes are additive only: the 14-day-in-18 count from D-19 stays necessary; new criteria only ever add.
- **Zero new ToolSpecs.** Every contract change in this feature is an additive argument or output key on an existing tool, each carrying a D-24 contract-diff justification. A tool whose contract would genuinely break is out of scope, not renamed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Phase 0: kana before anything else (Priority: P1, UNGATED)

As an absolute-beginner learner with a fresh database and no first lesson yet, I open a study session **today** and get a KANA session: one row block of hiragana (~5 kana), audio-first, and one dictation artifact at the end. Nothing else runs — no kanji rivals, no kanji-component hints, no media watching, no five-item mining budget. The day counts as a study day because the dictation exists, not because I claimed minutes.

**Why this priority**: the learner cannot do dictation, use a dictionary, or read a subtitle without kana (curriculum.md, Phase 0). Every later taskgroup in this feature consumes evidence that only real Phase-0 sessions can produce, so this is both first in value and the source of the entry gate's data. It is deliberately **prose and data only** — no contract changes — so it can land the same day it is written.

**Independent Test**: run one KANA session end to end against the real server: `start_session` → row block → dictation → close. A dictation-carrying lesson close exists in the event log and the day qualifies mechanically under the *existing* study-day rule.

**Acceptance Scenarios**:

1. **Given** the `katagiri-study` skill and a learner who says "let's start Japanese", **When** the mode is chosen, **Then** KANA runs as a peer of FULL/WATCH/REVIEW/TIRED and the agent says which mode it is in.
2. **Given** a KANA session, **When** new material is introduced, **Then** it is one hiragana row block of about five kana, audio-first, and mining is capped at ≤3 kana-only items.
3. **Given** Phase 0, **When** any rule from the kanji-rival set, the kanji-component hint ladder, or WATCH mode would fire, **Then** it is suspended and the suspension is stated, not silently skipped; furigana is always on.
4. **Given** a Phase-0 session, **When** coverage is discussed, **Then** the coverage unit is **unread kana**, not words.
5. **Given** the session close, **When** the day is evaluated, **Then** the qualifying artifact is the **dictation** — carried on the closed lesson under the reserved Phase-0 dictation topic slug — and never a minutes claim.
6. **Given** the audio the learner produces or is given, **When** it lands in the vault, **Then** the vault snapshot already includes `.mp3`/`.wav`, so the first audio artifact is backed up rather than silently dropped.

---

### User Story 2 - The dose is enforced by the server, not by my willpower (Priority: P1, post-gate)

As the learner, `start_session` tells me how much room is left today — new words, new grammar this week, listening reps — and `add_vocab` **refuses** past the daily cap with a structured error instead of politely accepting the ninth word. Topic selection is one more rung in the same prescriber; there is no second planner.

**Why this priority**: the dose limits are the one part of the pedagogy that a motivated learner reliably breaks, and prose has already failed at holding them (the v1 pack's mining budget is prose and self-counted). Putting the count in the server is the difference between a rule and a wish.

**Independent Test**: fixture log with eight mining events today → `add_vocab` refuses the ninth with a structured error naming the cap; `start_session` returns `caps.new_words_left = 0`.

**Acceptance Scenarios**:

1. **Given** curriculum reachability data, **When** no next step, revisit, or open thread applies, **Then** `prescribe()` returns a **topic** rung read from curriculum reachability, above the generic "open a lesson" fallback — still exactly one action, still one prescriber.
2. **Given** any prescribed action, **When** it is returned, **Then** the payload carries an additive `caps` block: `new_words_left`, `grammar_left`, `listening_reps_left`.
3. **Given** the daily new-word cap is spent, **When** `add_vocab` is called again, **Then** it refuses with a structured error naming the cap and the count it read — never a silent success, never a raise-free "ok: true".
4. **Given** two new grammar points already introduced this week, **When** `caps` is computed, **Then** `grammar_left` is 0.
5. **Given** a review queue past its hard cap, **When** the caps block is computed, **Then** the overflow is reported as deferral, not as a longer session.

---

### User Story 3 - Listening reps counted as reps (Priority: P2, post-gate)

As the learner, my narrow-listening work — ten replays of one 40-second dialogue — is logged as **reps of known audio**, in the same `study_session` event series everything else uses, with a deterministic dedupe key so nothing double-counts against the study-log importer.

**Why this priority**: input volume is the A0 strand that most needs measuring and is easiest to fake with a minutes claim. Reps of a *known* recording is the honest unit.

**Independent Test**: log the same listening block twice → one event; run `import_study_log` over a study-log file covering the same day → still no double count; the reps metric reads 10, and the day does not become a study day on reps alone unless the existing artifact rule already says so.

**Acceptance Scenarios**:

1. **Given** a narrow-listening block, **When** it is logged, **Then** it appends to the `study_session` series with a dedupe key in its own namespace (never colliding with the importer's `study:<ts>` keys).
2. **Given** the same block logged twice, **When** the second write runs, **Then** the dedupe key suppresses it.
3. **Given** a reps-only log, **When** minutes are absent, **Then** no minutes are invented and the day-qualification rule is left exactly as it was.

---

### User Story 4 - I only produce what I have heard (Priority: P1, post-gate)

As the learner, an item may be drilled **as production** only when it carries an audio anchor — a source plus a timestamp pointing at native audio (Irodori MP3s). Items without an anchor are marked text-only-not-for-A0-production and are still perfectly good reading material.

**Why this priority**: at A0, producing a word whose sound you have never heard installs an error that later work has to unlearn. This is the one place in the feature where a schema change is unavoidable.

**Independent Test**: fixture pool with one anchored and one unanchored item → the A0 production drill offers only the anchored one and says why the other was withheld.

**Acceptance Scenarios**:

1. **Given** migration 0002, **When** it runs, **Then** items/sentences carry an additive audio-anchor reference (source + timestamp) and nothing existing is renamed or dropped.
2. **Given** an unanchored item, **When** an A0 production drill is built, **Then** the item is excluded and reported as text-only-not-for-A0-production.
3. **Given** the migration, **When** it is applied, **Then** the constitution exception (whole-schema-in-one-migration) and its ledger row already exist.

---

### User Story 5 - Curriculum nodes point at real materials (Priority: P2, post-gate)

As the learner, a curriculum node can say which JF can-do it serves, which Irodori lesson supplies its audio, and which Tae Kim section explains it — as node attributes in `curriculum.md`, read by the existing importer. No new table, no new tool.

**Why this priority**: the grammar DAG is already the reachability source; joining it to materials is what turns "reachable" into "here is the recording and the explanation". Doing it as attributes keeps the single-file authoring story intact.

**Independent Test**: fixture curriculum with all three tag kinds imports; removing a tag reports an orphan rather than deleting learner-made rows.

**Acceptance Scenarios**:

1. **Given** a node block carrying JF can-do / Irodori lesson / Tae Kim section tags, **When** the curriculum is imported, **Then** the tags land on the existing `item` rows and every one names the source line that produced it.
2. **Given** a tag removed from the file, **When** the import re-runs, **Then** the removal is **reported as an orphan** and left alone — the additive rule for source-of-truth tables.
3. **Given** grammar sequencing, **When** the DAG order is authored, **Then** it follows Tae Kim/Genki sequencing, Irodori supplies audio and situational input, and JF can-dos are a secondary goal axis — never the primary order.
4. **Given** the materials themselves, **When** they are acquired, **Then** Irodori arrives through a `vendor/` download script and is **never committed**, while Tae Kim extracts are committable with CC BY-NC-SA attribution; both carry checksums.

---

### User Story 6 - Weekly and monthly evidence I cannot game (Priority: P1, post-gate)

As the learner, my progress is measured by artifacts whose quality I cannot talk my way around: a **weekly mora-count dictation** (hear a line, write kana — mora-length and devoiced-vowel errors are objectively right or wrong), a **weekly five-word pitch-pattern marking** checked against the vendored kanjium accent numbers, and a **monthly 60-second self-recorded monologue** whose length and fluency trend is visible.

**Why this priority**: it is the outcome instrument for the whole feature, and the pitch-marking exercise is the perception training that will later trigger F-02.

**Independent Test**: a week of fixtures produces one dictation record and one pitch-marking record; a month produces one monologue artifact; each errors path feeds existing `log_error` patterns.

**Acceptance Scenarios**:

1. **Given** a weekly dictation, **When** errors are scored, **Then** mora-length and devoiced-vowel mistakes are logged as `log_error` patterns that already exist in the pack's vocabulary.
2. **Given** a weekly pitch-marking set, **When** it is checked, **Then** it is checked against the vendored kanjium accent numbers, text-only, with no audio synthesis anywhere in the loop.
3. **Given** a monthly monologue, **When** it is recorded, **Then** the artifact is stored (backed up — see US1 scenario 6) and its length/fluency trend is readable across months.

---

### User Story 7 - Kanji stays recognition-only, and furigana fades (Priority: P2, post-gate)

As the learner, kanji is **recognition only** at A0–A1, budgeted per topic against the words I already say, and furigana decays per item: always → first occurrence only → off.

**Independent Test**: an item at each decay stage renders with the expected furigana treatment; the stage is derived from the item's existing known/understanding state, not from a new column.

**Acceptance Scenarios**:

1. **Given** a topic, **When** its kanji budget is computed, **Then** it is tied to the learner's known *spoken* words, never to a JLPT list order.
2. **Given** an item whose state has matured, **When** material renders, **Then** its furigana steps down one stage — and never up as a reward.
3. **Given** any A0/A1 material, **When** kanji production is requested, **Then** it is refused as out of policy.

---

### User Story 8 - Worksheets round-trip through the vault (Priority: P2, post-gate)

As the learner, katagiri writes a worksheet into the vault's `.derived/` directory using the existing `today_export` pattern (`generated: true` frontmatter, confined path, server-named file); I fill it in inside Obsidian; katagiri reads it back through the GET-only proxy. No agent ever writes to a vault.

**Independent Test**: worksheet written to `.derived/`, hand-edited, read back via the existing vault read path; a write attempt outside `.derived/` or against a file lacking `generated: true` is refused.

**Acceptance Scenarios**:

1. **Given** a worksheet write, **When** the path resolves outside `<vault>/.derived/`, **Then** it is refused (the existing confinement, reused, not re-implemented).
2. **Given** a target file without `generated: true` in its frontmatter, **When** a write is attempted, **Then** it is refused.
3. **Given** a filled-in worksheet, **When** it is read back, **Then** it arrives through the GET-only proxy as **untrusted data** and any instruction-shaped text in it is quoted to the learner, never acted on.
4. **Given** the whole loop, **When** it runs, **Then** no new MCP tool surface exists: the write happens on the exporter path and the read on the existing vault read tools.

---

### Entry Gate - contract-touching work is blocked until the evidence is real (Priority: P0)

As the learner-developer, none of US2–US8 may be built until the event log shows **≥10 study days, of which ≥6 carry a scored observation and ≥3 carry a dictation artifact**. Ten arbitrary or TIRED days do not open this gate; the criterion is about evidence *quality*, because every post-gate design decision is calibrated against evidence that does not exist yet.

**Why this priority**: this feature's whole risk is designing a teaching method for a learner who has not studied. The 006 entry gate is the structural mitigation, and it is deliberately layered on the D6 machinery rather than replacing it.

**Independent Test**: fixture logs for each failure shape (9 days; 10 days with 5 scored observations; 10 days with 2 dictation artifacts) → each reports FAIL naming its own criterion; the pass fixture reports PASS. In every fixture the existing 14/18 verdict is unchanged.

**Acceptance Scenarios**:

1. **Given** 9 qualifying study days, **When** the entry gate is evaluated, **Then** FAIL naming the day-count criterion.
2. **Given** 10 study days but 5 with scored observations, **Then** FAIL naming the observation criterion.
3. **Given** 10 study days, 6 scored, 2 dictation artifacts, **Then** FAIL naming the dictation criterion.
4. **Given** any evaluation, **When** the result is produced, **Then** the existing 14-in-18 day count and probe-battery criteria are still evaluated and still necessary — the new criteria only add.
5. **Given** the code that evaluates these criteria, **When** it is written, **Then** its ledger row and constitution bump are already committed.

### Edge Cases

- A Phase-0 day with observations but no dictation → not a qualifying day for the *entry gate's* dictation count, even if the existing artifact rule already counted the day. The two counts are different questions and are reported separately.
- A dictation artifact logged under a free-text topic instead of the reserved Phase-0 slug → invisible to the gate. The skill prose owns the slug; the gate never guesses from prose.
- `add_vocab` refused by the cap while the learner insists → the refusal is structured and final; the overflow route is the inbox, which already exists.
- Listening reps logged both by hand and by the study-log importer for the same day → dedupe namespace prevents the double count; a collision would be a test failure, not a rounding error.
- Curriculum tag removed then re-added → orphan reported on removal, re-import restores the tag; no row is ever deleted by the importer.
- Migration 0002 applied to a database that predates it → backup-before-migrate runs first (existing runner behaviour), and the migration must not touch `user_version` itself (the runner refuses it).
- Unanchored item requested for A0 production → withheld with a reason, never substituted with a synthesised reading (no TTS in this feature; VOICEVOX stays deferred).
- Worksheet read-back containing instruction-shaped text → quoted, never executed (constitution VI).

## Requirements *(mandatory)*

### Functional Requirements — Phase 0 (ungated, prose/data only)

- **FR-001**: The `katagiri-study` skill (`.claude/skills/katagiri-study/SKILL.md`, with its prose mirror `docs/katagiri/katagiri/90-meta/skills-pack-v1.md`) MUST define a **KANA mode** as a peer of FULL/WATCH/REVIEW/TIRED: hiragana in row blocks of about five kana per day, audio-first, daily artifact = mora-count dictation, nothing else running.
- **FR-002**: Phase-0 suspensions MUST be stated in the same prose: kanji-rival rule suspended, kanji-component hint ladder suspended, WATCH mode suspended, mining capped at ≤3 kana-only items, furigana always on.
- **FR-003**: In Phase 0 the coverage unit MUST be **unread kana**, not words.
- **FR-004**: A Phase-0 study day MUST qualify on its **dictation artifact**, carried by the session's closed lesson under a reserved Phase-0 dictation topic slug — reusing the existing `lesson_close` artifact event type. No stop-gate code changes in this taskgroup.
- **FR-005**: The kana gate MUST be staged: **hiragana recognition ≥95% in both directions (kana→sound and sound→kana) with a stated latency bound** unlocks drill tooling; **katakana is a second checkpoint, not a wall** — katakana gaps never block hiragana-level work.
- **FR-006**: Kana curriculum items MUST be authored as data through the **existing** `curriculum.md` ingest (node blocks with `id`/`prereqs`/`level`, parsed by `katagiri.intelligence.parse_curriculum` / `import_curriculum`). No parser change, no new table.
- **FR-007**: `VAULT_SNAPSHOT_EXTENSIONS` MUST include `.mp3` and `.wav` **before** any audio artifact lands in the vault.
- **FR-008**: The daily backup scheduled task MUST be installed and **one run verified** as part of this taskgroup; the single-writer rule (one authoritative frontend process per day; one DB writer) MUST be documented in the ops docs.
- **FR-009**: The **modality ladder** MUST be stated in the skill prose: A0 = kana + audio-with-script + shadowing + dictation, **zero free conversation**; A0→A1 = listening volume + scripted voice tasks with visible text; A1+ = unscripted, script hidden.

### Functional Requirements — Entry gate

- **FR-010**: An evidence-quality entry gate MUST be evaluated mechanically: **≥10 study days, ≥6 with a scored observation, ≥3 with a dictation artifact**. Arbitrary or TIRED-only days do not satisfy it.
- **FR-011**: The gate additions MUST be **additive** to the existing D-19 mechanics: 14 study days in an 18-day window and the probe battery remain necessary conditions; the new criteria only add, and the existing verdict is unchanged by their introduction.
- **FR-012**: The ledger row(s) and constitution version bump MUST be committed **before** the gate-criteria code task starts.
- **FR-013**: The gate result MUST be reachable through the existing `stop_gate_status` surface as additive output keys. **No new ToolSpec.**

### Functional Requirements — post-gate (contract-touching)

- **FR-014**: Topic selection MUST be a new rung inside the existing `prescribe()` ladder, reading curriculum reachability, placed above the generic "open a lesson" fallback. The single-prescriber property is preserved: still exactly one action, still no menu. `next_topic`, `plan_revision`, `mark_topic_progress`, `run_drill` and `check_answer` are **CUT** — they are not deferred, they are not built.
- **FR-015**: `prescribe()` MUST count today's mining events and this week's grammar introductions and return an additive `caps` block in the action payload: `new_words_left`, `grammar_left`, `listening_reps_left`. Dose: 20–30 min core per day, ≤8 new words/day, ≤2 new grammar per week, review queue hard-capped with explicit deferral.
- **FR-016**: `add_vocab` MUST refuse past the daily cap with a structured error (the module's existing refusal shape), naming the cap and the count read.
- **FR-017**: Input logging MUST write into the **same** `study_session` event series with a deterministic dedupe key in its own namespace — no second unread channel, no double-count against `events.import_study_log` (`study:<ts>` keys). The A0 strand metric is **narrow-listening reps of known audio**, not raw minutes.
- **FR-018**: An additive item/sentence **audio-anchor** reference (source + timestamp; Irodori MP3 refs) MUST be introduced by **migration 0002**; A0 production drills MUST be restricted to the audio-anchored pool; unanchored items MUST be marked text-only-not-for-A0-production. The migration requires a stated constitution exception plus a ledger row, filed first.
- **FR-019**: `curriculum.md` node attributes MUST be extended to carry JF can-do id, Irodori lesson, and Tae Kim section tags, with **removal/orphan semantics defined in the same change**. No new table, no new tool. Grammar DAG order follows Tae Kim/Genki sequencing; Irodori is the audio and situational-input source; JF can-dos are a secondary goal axis.
- **FR-020**: Irodori and Tae Kim materials MUST arrive through `vendor/` download scripts with checksums: **Irodori never committed**; Tae Kim extracts committable under CC BY-NC-SA with attribution.
- **FR-021**: Vocabulary keeps its SRS decay (Anki owns scheduling). Grammar **constructions** MUST be an accuracy-over-attempts trajectory **derived from existing observation events** — no new table, no terminal state, U-shaped dips logged and never penalised. Reachability gates **output** tasks only.
- **FR-022**: Assessment cadence MUST be: **weekly** mora-count dictation (hear an Irodori line → write kana; mora-length and devoiced-vowel errors feed existing `log_error` patterns), **weekly** five-word pitch-pattern marking checked against vendored kanjium accent numbers (text-only perception training, building toward the F-02 trigger), **monthly** 60-second self-recorded monologue artifact (length/fluency trend).
- **FR-023**: Kanji policy MUST be recognition-only, with a per-topic budget tied to known **spoken** words, and per-item furigana decay (always → first occurrence → off) derived from existing item state.
- **FR-024**: The worksheet loop MUST write through the existing `today_export` `.derived/` pattern (`generated: true` frontmatter check, confined path, server-named file) and read back through the existing GET-only vault proxy. The agent never writes vaults. No new MCP tool surface.
- **FR-025**: Every contract change in this feature MUST be additive-only under D-24 and MUST carry a contract-diff justification (ledger row + reasoning in `docs/audit-log.md`) filed before the code task. **Zero new ToolSpecs** across the whole feature.

### Key Entities

- **KANA session**: a session mode; row block of ~5 hiragana, audio-first, one dictation artifact, ≤3 kana-only mined items.
- **Dictation artifact**: a mora-count dictation carried on a closed lesson under the reserved Phase-0 topic slug — the honest day-qualifying evidence and the entry gate's third criterion.
- **Caps block**: additive action-payload field `{new_words_left, grammar_left, listening_reps_left}` computed from today's mining events and the week's grammar introductions.
- **Listening rep**: one replay of a known recording, logged into the `study_session` series under its own dedupe namespace.
- **Audio anchor**: source + timestamp reference on an item/sentence; the admission ticket for the A0 production pool.
- **Curriculum material tags**: JF can-do id / Irodori lesson / Tae Kim section, as node attributes on existing `item` rows.
- **Construction trajectory**: accuracy over attempts per grammar construction, derived from observation events; no row of its own.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (Phase 0, immediate): the learner runs a KANA session on the day TG0 merges — one dictation artifact in the event log, backed up, with the audio extensions already widened.
- **SC-002** (staged kana gate): hiragana recognition ≥95% in both directions within the stated latency bound before any drill tooling is unlocked; katakana measured separately and never blocking.
- **SC-003** (entry gate): ≥10 study days / ≥6 scored observations / ≥3 dictation artifacts, evaluated mechanically, with the 14/18 + probe criteria still evaluated and unchanged.
- **SC-004** (dose): across a post-gate week, zero days exceed 8 new words and zero weeks exceed 2 new grammar points — enforced by refusal, not by self-report.
- **SC-005** (input strand): narrow-listening reps recorded with zero double-counts against the study-log importer over a full week of both paths running.
- **SC-006** (production honesty): 100% of A0 production drills draw from audio-anchored items; every withheld item reports why.
- **SC-007** (evidence quality): one dictation and one pitch-marking record per week, one monologue per month, for the first post-gate month.
- **SC-008** (contract discipline): zero new ToolSpecs; every contract diff has a ledger row dated **before** its code commit; `tests/test_mcp_tools.py` congruence green throughout.
- **SC-009** (006-verify): cold-subagent scenario passes — KANA session, cap refusal, anchored-only production pool, worksheet round-trip.

## Deferred, with explicit triggers

| Item | Deferred to | Fires when |
|---|---|---|
| VOICEVOX TTS (per-word/sentence audio) | **F-02, revised** | "minimal-pair perception training enters the curriculum". Prerequisites when it fires: backup allowlist already widened (FR-007, done in TG0); an injectable-transport test seam designed **before** the first test is written. |
| Voice interaction — option A: ChatGPT desktop Voice → Codex → katagiri over local stdio (zero build, Plus subscription) | **F-03, revised** | empirical test **PASSED 2026-08-20** (learner's live test: voice-directed Codex called katagiri stdio tools). Remaining gate: Phase 0 kana complete (modality ladder: zero free conversation at A0). |
| Voice interaction — option B: OpenAI Realtime API over our own agent (fallback; metered ~$0.05–0.20/min, needs API credits) | **F-03 fallback** | option A's empirical test fails, or OpenAI restricts arbitrary MCP servers in voice-directed Codex. |
| STT (kotoba-whisper, pinned checkpoint) | new deferral row | unscripted production assessment actually needs it — i.e. the monologue artifact stops being scoreable by hand. |
| Restore-CLI process-list nicety | backlog | never a blocker; pure operator comfort. |

Nothing in this table may be pulled forward by convenience; each needs its trigger stated as met in a ledger row.

## Assumptions

- The learner has not yet had a first lesson; the DB was reset 2026-08-20. Every post-gate design decision is therefore calibrated on evidence that TG0 produces.
- `docs/katagiri/katagiri/10-course/curriculum.md` exists with a Phase-0 section and a documented "Node format" block; the importer already skips the format-documenting section and reports the skip.
- The existing study-day rule (`study_session` minutes ≥10 **or** one artifact event) is unchanged by this feature; the entry gate adds questions rather than editing that answer.
- Irodori PDFs/MP3s are acquired by the operator by hand through the download script; nothing in this feature fetches at runtime (D-10).
- The integration branch for this feature is whichever branch Phase D is integrating into at the time TG0 lands (`phase-d` while it is open, `master` after) — recorded in tasks.md, not assumed.
