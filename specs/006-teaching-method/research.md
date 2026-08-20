# Research: 006 — Teaching Method

Settled decisions. Source of record: council plan v3 (5 roles × 2 adversarial rounds), with plan v1 §"Project context", curriculum-stack decision #5 and pedagogy sources #6 as background. **Nothing here is open for re-decision during implementation** — an implementing agent that disagrees files a ledger row, it does not improvise.

## Frontend and policy location

- **Decision**: policy lives in the katagiri server; every frontend is a thin, disposable caller. `start_session`/`prescribe()` decides what happens next, `log_*` records evidence, `stop_gate_status` judges readiness.
  **Rationale**: two frontends with divergent pedagogy was the failure mode the council found first; a rule that lives in a prompt dies with the prompt.
  **Alternatives**: teaching policy in the skill prose only (rejected — prose has already failed to hold the mining budget).
  **Source**: plan v3 §Governing principles.

- **Decision**: the consumer-voice frontend is **deferred behind an empirical test**, not dead (re-verified 2026-08-20; supersedes the council-R1 "dead" verdict, whose evidence predated 2026-07-23).
  **Rationale**: ordinary ChatGPT voice mode still cannot call custom MCP connectors (OpenAI help docs, Aug 2026), and connectors still cannot reach localhost — that half of the original verdict stands. But ChatGPT desktop Voice (GPT-Live, shipped 2026-07-23) drives Codex by speech, and Codex is a real MCP client for **local stdio** servers (`codex mcp add katagiri -- <command>`); tool work runs in an async background agent spawned by the voice turn. Local stdio = same security posture as Claude Code today — no tunnel, no listener, constitution VI and D-22 intact. Formerly-unconfirmed link now CONFIRMED 2026-08-20: the learner's live test showed voice-directed Codex invoking katagiri's user-added local stdio tools ("works almost too well"). F-03 option A's only remaining gate is Phase 0 kana. Subscription route preferred over Realtime API: zero build, zero marginal cost (Plus already paid), no API-credit barrier for future OSS users.
  **Alternatives**: tunnel + auth (rejected on constitution), remote rehost (rejected on D-01/D-02).
  **Source**: plan v3 §Governing principles; risk list in plan v1 §"Known open risks".

- **Decision**: daily tutor frontend stays Claude Code/Desktop + `katagiri-study` over stdio, unchanged by this feature.
  **Source**: plan v3 §Governing principles.

## Phase 0 (the ungated part)

- **Decision**: KANA mode is a peer session mode, not a variant of FULL: hiragana in row blocks of ~5 kana/day, audio-first, daily artifact = mora-count dictation, nothing else running.
  **Rationale**: the learner is an absolute beginner with a reset DB; kana is the precondition for dictation, dictionary use and subtitles (curriculum.md Phase 0). A mode that shares FULL's machinery inherits FULL's rules, which are wrong at this level.
  **Source**: plan v3 TG0 (teacher R2 F2 + Q3).

- **Decision**: Phase-0 suspensions — kanji-rival rule, kanji-component hints, WATCH mode all off; mining ≤3 kana-only items; furigana always.
  **Rationale**: every one of those rules assumes a reader. Applying them to a non-reader produces observations that measure the rule, not the learner.
  **Source**: plan v3 TG0.

- **Decision**: Phase-0 coverage unit = **unread kana**, not words; day qualification = **dictation artifact**.
  **Rationale**: word coverage is undefined for someone who cannot read the words; and the honest gate at this stage is "did a dictation happen", which is a durable artifact rather than a minutes claim.
  **Implementation note (not a re-decision)**: TG0 is prose/data only, so the dictation qualifies a day by riding the **existing** `lesson_close` artifact event type under a reserved Phase-0 dictation topic slug. The slug is what later makes the entry gate's dictation count mechanical; no `stop_gate.py` change happens in TG0.
  **Source**: plan v3 TG0; `stop_gate.ARTIFACT_EVENT_REASONS` for the existing artifact set.

- **Decision**: the kana gate is **staged** — hiragana recognition ≥95% in *both* directions with a latency bound unlocks drill tooling; katakana is a second checkpoint, not a wall.
  **Rationale**: one-direction recognition is the classic false pass; katakana-as-wall stalls otherwise-ready work for a script the learner meets mostly in loanwords.
  **Source**: plan v3 TG0.

- **Decision**: kana curriculum items are authored as **data** through the existing `curriculum.md` ingest.
  **Rationale**: the importer already exists, is idempotent, upserts with `COALESCE` so curated values survive, and reports skips/orphans. A second authoring path would be a second source of truth.
  **Source**: plan v3 TG0; `katagiri.intelligence` module docs ("How curriculum.md maps to rows", "Idempotency, and what the import will not do").

- **Decision**: ops prerequisites are bundled into TG0 — install the daily backup scheduled task and verify one run; widen `VAULT_SNAPSHOT_EXTENSIONS` to `.mp3`/`.wav` **before** the first audio artifact lands.
  **Rationale**: an audio artifact that the snapshot silently skips is an unrecoverable loss of the only non-reconstructible asset (constitution III). Ordering matters more than the size of the change.
  **Source**: plan v3 TG0 (operator R2 #3).

- **Decision**: single-writer discipline documented — one authoritative frontend process per day; one DB writer.
  **Source**: plan v3 §Single-writer discipline (operator MAJOR 5).

## Entry gate

- **Decision**: contract-touching taskgroups require **≥10 study days, ≥6 with a scored observation, ≥3 with a dictation artifact**.
  **Rationale**: 10 arbitrary or TIRED days would satisfy a day count while producing none of the evidence the post-gate designs are calibrated against. The gate is about evidence *quality*.
  **Alternatives**: plain day count (rejected — gameable by exactly the sessions that teach the least).
  **Source**: plan v3 §Entry gate (architect MAJOR 4 + teacher F2 re-cut).

- **Decision**: the additions are **additive to the existing 14/18 stop-gate mechanics**; ledger row + constitution bump filed before the gate-criteria code.
  **Rationale**: D-19's count remains necessary; a gate that replaced it would quietly loosen Phase-E blocking. Governance-before-code is the standing rule (constitution Governance).
  **Source**: plan v3 §Entry gate (architect F5).

## Post-gate decisions

- **Decision**: `next_topic`, `plan_revision`, `mark_topic_progress`, `run_drill`, `check_answer` are **CUT**. Topic selection becomes a new rung inside the existing `prescribe()` ladder, reading curriculum reachability.
  **Rationale**: the single-prescriber property is the thing that keeps `start_session` from becoming a dashboard; five tools that each decide something are five prescribers.
  **Source**: plan v3 §Post-gate TGs.

- **Decision**: the dose lives in code — `prescribe()` counts today's mining events and the week's grammar introductions and returns an additive `caps` block (`new_words_left`, `grammar_left`, `listening_reps_left`); `add_vocab` refuses past the daily cap with a structured error. Caps: 20–30 min core/day, ≤8 new words/day, ≤2 new grammar/week, review queue hard-capped with deferral.
  **Rationale**: prose caps are self-counted and the count is the first thing a good session breaks.
  **Source**: plan v3 §Post-gate TGs (teacher F3).

- **Decision**: input logging writes the **same** `study_session` event series with a deterministic dedupe key; the A0 strand metric is **narrow-listening reps of known audio** (e.g. 10 replays of one 40-second Irodori dialogue), not raw minutes.
  **Rationale**: a second unread channel is a second truth; and minutes of unknown audio measure endurance, not intake. The importer already owns the `study:<normalised ts>` dedupe namespace (`events.import_study_log`), so the new writes take their own namespace and cannot double-count.
  **Source**: plan v3 §Post-gate TGs (teacher F4); `events.import_study_log` for the existing key shape.

- **Decision**: additive item/sentence **audio-anchor** ref (source + timestamp; Irodori MP3 refs) via **migration 0002**; A0 production drills restricted to the anchored pool; unanchored items marked text-only-not-for-A0-production. Constitution exception + ledger row first.
  **Rationale**: producing a word never heard installs an error that later work must unlearn; the anchor is the cheapest possible check and cannot be derived.
  **Source**: plan v3 §Post-gate TGs (teacher F1).

- **Decision**: curriculum material refs are **node attributes** in `curriculum.md` (JF can-do id, Irodori lesson, Tae Kim section) parsed by the existing importer, with removal/orphan semantics defined in the same change. **No new table, no new tool.** Grammar DAG order from Tae Kim/Genki sequencing; Irodori = audio + situational input; JF can-dos = secondary goal axis.
  **Rationale**: the DAG is already the reachability source and `item`/`item_edge` are source-of-truth tables whose importer is additive by design — removal must therefore be *reported*, never applied.
  **Source**: plan v3 §Post-gate TGs (architect F4); curriculum-stack licensing verified in plan v1 decision #5.

- **Decision**: Irodori/Tae Kim materials arrive through `vendor/` download scripts + checksums. **Irodori is never committed** (custom Japan Foundation terms: non-commercial text extraction acceptable, illustrations untouchable, redistribution forbidden); Tae Kim extracts are committable under CC BY-NC-SA with attribution. Marugoto (paywalled) and NHK Easy (no license) stay rejected.
  **Source**: plan v1 decision #5 (licensing verified); vendor/README hard rules; D-10.

- **Decision**: vocabulary keeps SRS decay (Anki owns items); grammar **constructions** are an accuracy-over-attempts trajectory **derived from existing observation events** — no new table, no terminal state, U-dips logged and not penalised. Reachability gates **output** tasks only.
  **Rationale**: a construction is not an item and does not have a due date; a terminal "mastered" state invites the exact self-assessment the project bans. U-shaped dips are a known interlanguage phenomenon, so penalising them would penalise progress.
  **Source**: plan v3 §Post-gate TGs (teacher R1-5, architect F4).

- **Decision**: assessment cadence — **weekly** mora-count dictation (Irodori line → kana; mora-length and devoiced-vowel errors are objective and feed existing `log_error` patterns), **weekly** five-word pitch-pattern marking against vendored kanjium accent numbers (text-only perception training, builds toward the F-02 trigger), **monthly** 60-second self-recorded monologue (length/fluency trend).
  **Rationale**: each is non-gameable and cheap; together they cover perception, transcription and production without needing TTS or STT.
  **Source**: plan v3 §Post-gate TGs (teacher F5).

- **Decision**: kanji is **recognition-only**, per-topic budget tied to known spoken words, furigana decaying per item (always → first occurrence → off).
  **Rationale**: curriculum.md already defers kanji to month 4–6 on purpose, ordered by the words the learner already says.
  **Source**: plan v3 §Post-gate TGs; curriculum.md Phase 4.

- **Decision**: worksheets are written through the existing `today_export` `.derived/` pattern (`generated: true`, confined path, server-named); the learner fills them in inside Obsidian; katagiri reads them back through the GET-only proxy. **The agent never writes vaults.**
  **Rationale**: plan v1's original idea (agent writes worksheets via the Obsidian MCP `vault_patch`) is dead under D-20 — the plugin's MCP endpoint is never registered with an agent. The `.derived/` writer already has the confinement and the `generated: true` guard, so reusing it is both cheaper and safer.
  **Source**: plan v3 §Post-gate TGs; D-11/D-20; `today_export` module docs.

- **Decision**: modality ladder — A0 = kana + audio-with-script + shadowing + dictation, **zero free conversation**; A0→A1 = listening volume + scripted voice tasks with visible text; A1+ = unscripted, script hidden.
  **Rationale**: it answers plan v1's open pedagogy risk ("is voice-first chat right for a beginner who must learn kana?") with a staged answer rather than a preference.
  **Source**: plan v3 §Post-gate TGs.

## Pedagogy sources behind the above (background, not re-derived)

Distilled from free sources in plan v1 §6: Ellis's ten principles; Nation's four strands (the session time budget); Krashen comprehensible input; TPRS circling (yes/no → either/or → short answer → open); Lyster & Ranta's correction taxonomy (recasts for beginners and off-target errors, elicitation for the current target, max 1–2 corrections per turn); AutoTutor dialogue moves (pump → hint → prompt → assert → summary); Dunlosky/Bjork retrieval + spacing; JF can-do milestones; Genki's one-point-at-a-time convention; the session-close recap ritual. These inform the dose numbers and the correction rules; they are not re-argued in this feature.

## Deferred, with triggers (do not pull forward)

- **VOICEVOX TTS → F-02, revised trigger**: "minimal-pair perception training enters the curriculum." Prereqs at fire time: backup allowlist already widened (done in TG0); an injectable-transport test seam designed *before* the first test.
- **Voice interaction → F-03, revised**: option A = ChatGPT desktop Voice → Codex → katagiri over local stdio, when the empirical MCP-invocation test passes and Phase 0 kana is complete. Option B (fallback) = Realtime API over our own agent, only if the test fails or OpenAI restricts arbitrary MCP servers in voice-directed Codex.
- **STT (kotoba-whisper, pinned checkpoint) → new deferral row**: when unscripted production assessment actually needs it. Local STT for mixed-language free talk was measured against production stacks and found disappointing (plan v1 #11) — so it waits for a need, not for curiosity.
- **Restore-CLI process-list nicety → backlog.**

## Open (deliberately, not clarification-blockers)

- Exact latency bound for the hiragana gate (a number the learner's first week supplies; authored in TG0 prose as a stated value and revisable by ledger row).
- Which Irodori lesson supplies each Phase-1 node's audio (content authoring, TG5, learner-owned).
- Whether the weekly pitch-marking set draws from mined items or from a fixed list (decided in TG6 from the first weeks' data).
