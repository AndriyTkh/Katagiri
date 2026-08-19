# Katagiri — Decisions & Deferrals Ledger

Running record: decisions made, options deferred (revisit conditions noted), and moonshots
excluded from the current dev plan. Update whenever a decision lands or scope moves.
Detailed reasoning lives in [audit-log.md](audit-log.md); this file is the index.

Last updated: 2026-08-19.

## Review coverage (what has already been audited — don't re-review)

| Scope | Reviewed by | Round / date | Status |
|---|---|---|---|
| Project classification, scope options A/B/C | multi-agent council | rounds 1–3, 2026-08 | settled — do not reopen |
| OSS component picks, licenses, integration facts | research passes + council | rounds 3–4 + oss-components.md, verified 2026-08-18 | settled — trust oss-components.md incl. round-4 corrections |
| Dependencies / build-order feasibility (v4.2) | council round 4 | 2026-08-18 | settled |
| dev-plan v1 (structure, process, security, pedagogy) | 7-role panel | round 5, 2026-08-19 | done — findings merged into v1.1 |
| dev-plan v1.1 revisions themselves | nobody | — | unreviewed delta; spot-check only the *changes* if ever re-reviewed |
| Beads DAG (38 beads, created 2026-08-19) | nobody | — | unreviewed; per-phase estimates/workfiles/parallel lanes computed at implementation time |

Rule: a new review round scopes only the delta since the last one. Re-reviewing a settled row
is waste unless new external facts invalidate it (note the invalidation here first).

## Decisions (binding)

| # | Date | Decision | Where argued |
|---|---|---|---|
| D-01 | 2026-08 | Personal tool, English↔Japanese, option C scope | audit rounds 1–3 |
| D-02 | 2026-08 | MCP server is the build ceiling — no app, no public service | audit round 3 |
| D-03 | 2026-08 | OSS-first; genuinely-build list = MCP server, substitution engine, Yomitan dict generator | oss-components.md |
| D-04 | 2026-08 | Anki owns scheduling; FSRS never reimplemented as live scheduler | oss-components.md |
| D-05 | 2026-08-18 | `answerCards` banned; Anki writes = `addTags`/`setDueDate` only, `exportPackage` before batches | audit round 4 |
| D-06 | 2026-08-18 | Anki mirror reads `collection.anki2` directly; AnkiConnect off critical path | audit round 4 |
| D-07 | 2026-08-18 | Known threshold = `ivl ≥ 21d` (FSRS stability unreadable via AnkiConnect); py-fsrs formula-only, pin `fsrs<7` | audit round 4 |
| D-08 | 2026-08-18 | MCP SDK pinned `mcp>=2,<3`; plain functions + thin adapter | audit round 4 |
| D-09 | 2026-08-18 | FTS5 = fugashi shadow column (unicode61) + trigram, routed by query length | audit round 4 |
| D-10 | 2026-08-18 | Vendor full UniDic + kanjium accents.txt locally; no runtime downloads | audit round 4 |
| D-11 | 2026-08-18 | Obsidian via local-rest-api v5.1+, :27123; own markdown search kept independent. **Amended by D-20**: plugin's MCP endpoint never registered with agent; Katagiri proxies GET-only | audit round 4 → round 5 |
| D-12 | 2026-08-18 | Whole schema in one migration (lexeme, alias, item, event, observation, lesson, media) | audit round 4 |
| D-13 | 2026-08-19 | **No own media player.** Player = mpv + asbplayer + mokuro + MCP context channel. Own player rejected (DRM wall identical, months of cost, violates D-02/D-03) | dev-plan v1 |
| D-14 | 2026-08-19 | Execution order: core+read-MCP → Obsidian render → dual search → learning tools → overlay last (user priority) | dev-plan v1 |
| D-15 | 2026-08-19 | Every phase gated by cold-subagent MCP verification pass, in addition to unit tests | dev-plan v1 (user amendment) |
| D-16 | 2026-08-19 | Stop-gate D6: 14 consecutive study days before Phase E code; mechanical exit criteria | audit round 4 + dev-plan |
| D-17 | 2026-08-19 | Screenshot-question tool + music/karaoke added to scope (Phase E) | dev-plan v1 |
| D-18 | 2026-08-19 | Study-first rule: study before build sessions, no building on zero-review days; study + event logging from Phase A day one; phase entry needs ≥4 study days in prior week | round 5, cluster 1 |
| D-19 | 2026-08-19 | Stop-gate = 14 study days in 18-day window, concrete event-count definition, `stop_gate_status` tool, declared pauses, re-plan trigger; mpv seek logger exempt | round 5, clusters 1/15 |
| D-20 | 2026-08-19 | Obsidian proxied: Katagiri holds REST token, GET-only tools, plugin MCP endpoint never exposed to agent | round 5, cluster 9 |
| D-21 | 2026-08-19 | Never open live collection.anki2 — snapshot copy → ro+immutable → integrity_check; detect-Anki-running precondition | round 5, cluster 5 |
| D-22 | 2026-08-19 | Security workstream: stdio-only MCP, 127.0.0.1 + firewall verification, untrusted-data envelope on media text, echo-back before writes on media content, secrets in %LOCALAPPDATA%, confined write roots | round 5, cluster 9 |
| D-23 | 2026-08-19 | Verification = fixture-based, assertion-driven, cumulative (phase N runs A..N); 20–25% budget; max 2 reruns; learner metric on every gate | round 5, clusters 7/16 |
| D-24 | 2026-08-19 | Tool registry checked in; C1 folded into A6; contracts additive after freeze; stubs raise instead of returning plausible values | round 5, cluster 6 |
| D-25 | 2026-08-19 | Yomitan known-dict pulled forward to A8; regen drift-triggered (Δ>150 known words), not weekly | round 5, clusters 2/15 |
| D-26 | 2026-08-19 | Canary set sealed as A0b, before first study day, validator-enforced | round 5, cluster 3 |
| D-27 | 2026-08-19 | All DDL in the A1 migration; derived vs source-of-truth classification; migration runner + backup-before-migrate from day one; event-log append-only via triggers | round 5, clusters 4/8 |
| D-28 | 2026-08-19 | i+1 gated on curriculum grammar-DAG reachability, not vocabulary coverage alone | round 5, cluster 10 |
| D-29 | 2026-08-19 | Estimates bottom-up per bead before creation; actuals logged; re-baseline after Phase A; task-level DAG; weekly status line; 1.5× slip rule | round 5, cluster 13 |
| D-30 | 2026-08-19 | Phase C entry gate waived by user — specs/002 T001 entry criteria (Phase B complete + B-verify green + ≥4 study days prior week) NOT satisfied: kata-bvf still open pending user-side Today.md adoption metric, event log shows 0 study days. User explicitly chose to override and start Phase C on 2026-08-19. Consequence: Phase C proceeds; kata-bvf stays open and must close before Phase D entry (specs/003 T001) unless separately waived | user override, 2026-08-19 |

## Deferred options (not cut — revisit condition attached)

| # | Option | Deferred to | Revisit when |
|---|---|---|---|
| F-01 | Progressive substitution engine (the novel piece) | post-loop | Usage data exists; prerequisites (aligned text + known_set) done by Phases A–D |
| F-02 | VOICEVOX TTS + per-word WAV cache | post-loop | Yomitan word audio stops being enough (sentence TTS needed) |
| F-03 | ASR / speaking scored aloud; conversation with vocab ceiling | post-loop | Teacher loop stable; speaking becomes the bottleneck |
| F-04 | jpdb / WaniKani seed importers | post-loop | Actually holding jpdb/WK history worth importing |
| F-05 | asbplayer playhead upstream PR (issue #1087) | opportunistic | Manual anchors prove annoying in practice |
| F-06 | Advanced difficulty modeling beyond jreadability+coverage | post-loop | D2 baseline proves too coarse |
| F-07 | Lute v3 integration (per-word reading status) | undecided | Long-text reading tracking wanted beyond Anki/Yomitan |
| F-08 | Overlay-on-Google/browser-wide OCR (original "option 1" idea) | superseded | Only if a surface appears that asbplayer+mokuro+Yomitan can't cover |
| F-09 | Event-log per-event hash chain (tamper evidence) | post-loop | Append-only triggers prove insufficient (round 5: overkill for personal tool today) |
| F-10 | Phase E channel order (E1/E2/E3) | decision-at-gate | Fixed by measured consumption mix during the D6 window |

## Moonshots — status vs current plan

| § | Moonshot | In plan? |
|---|---|---|
| 1 | Canary set (held-out test) | **Partially** — ROADMAP says seal in Phase 0 (cheap, time-critical); not a dev-plan task yet |
| 2 | Interlanguage grammar report | No — needs utterance corpus (post-ASR, F-03) |
| 3 | Personal audiogram | No — needs dictation/minimal-pair history at scale |
| 4 | Rewind telemetry | **Capture slice only** (E6: log seek-backs from mpv channel). Analysis/auto-debt stays out |
| 5 | Bespoke serialized audio drama | No — needs known_set + TTS (F-02) |
| 6 | Voice-clone self-modeling | No |
| 7 | Semantic gap analysis (embeddings) | No |
| 8 | Decay anomaly → re-encoding prescription | No — needs months of review log |
| 9 | N-of-1 randomized trials | No — needs canary set + fitted FSRS |
| 10 | Register ladder | No — cheap prompt-side; candidate for skills pack v1 (D4 follow-up) |
| 11 | L1 interference profile | **Partially** — ROADMAP: one conversation, do early; content exists in 35-phonology/l1-profile.md |
| 12 | Vault stops speaking English (`sensei_language` ladder) | No — month-scale maturity needed |

## Rejected (not deferred — decided against)

| Option | Why | Where |
|---|---|---|
| Build own media player | See D-13 | dev-plan v1 |
| JParaCrawl corpus | Research-only license, viral | oss-components.md |
| Tadoku graded readers in pipeline | CC BY-NC-ND, no derivatives | oss-components.md |
| AnkiConnect `answerCards` | Pollutes FSRS training data irreversibly | audit round 4 |
| MarkusPfundstein/mcp-obsidian | Stale, pre-5.x PATCH format | audit round 4 |
| kuromoji.js / client-side tokenization | Unmaintained; no accent fields | oss-components.md |
| In-Katagiri scheduler (FSRS reimplementation) | See D-04 | oss-components.md |
| Agent interface inside player | Default agent app + MCP instead | dev-plan v1 |
