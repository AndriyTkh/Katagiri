# Katagiri — idea-stage audit log

Consolidated record of a multi-round audit of the Katagiri project (Japanese-learning system).
Original vault docs unzipped into `docs/katagiri/katagiri/`. This file preserves findings that
otherwise only existed in a chat session (scratchpad mockups are session-temp and gone).

Session date: 2026-08-18.

---

## Round 0 — v1 audit (personal Obsidian-vault-first design)

Source: original docs in `docs/katagiri/katagiri/` (README, ARCHITECTURE, ROADMAP, MOONSHOTS,
CONVENTIONS, ADR 0001, mcp-spec, schema/topic-file, settings, l1-profile).

**What's sound:** Markdown source of truth + append-only `reviews.jsonl` event log + derived
DB is a good pattern for one user with git. ADR-0001's core argument — git as audit layer for a
fallible LLM co-author — is the strongest idea in the corpus. Tool picks (fugashi/UniDic, JMdict,
FSRS, yt-dlp, WhisperX, VOICEVOX) are all real and free. Pedagogy basics (pitch from day 1,
register tags, dictation, directional cards, error museum) are defensible.

**Design bugs found:**
- `fmt` dropping the kanji column when `kanji_enabled: false` deletes data from the source of
  truth, violating the project's own "display preference must never cause data loss" rule. Fix:
  columns always present in source; display filtering only in generated/derived views.
- `fmt` is itself a hidden bidirectional sync layer — the thing ADR-0001 warned becomes "the
  project." Budget 3-4x instinct for it.
- Content-hash IDs (`sha1(normalized_japanese)`) need a precisely defined normalization or they
  silently diverge across authors/machines.
- No usage gate between roadmap phases — nothing stops feature-building from replacing actual
  studying. Vault at time of audit: ~60KB of architecture prose, ~3KB of actual Japanese,
  `reviews.jsonl` was 0 bytes.

**Claims debunked (prose outran evidence):**
- Rewind telemetry ("highest value-to-effort") — actually high effort (browser extension, DRM
  hostile platforms) for noisy signal. Replace with a one-tap "mark this moment" hotkey.
- Automated pronunciation/prosody *scoring* — research-grade, not solo-buildable. Use overlay
  visualization instead of a score.
- ASR-scored isolated-word speaking cards — Whisper hallucinates on short non-native utterances.
- Constrained-vocabulary conversation ("forbidden from unknown words") — LLMs can't hold a hard
  vocabulary ceiling; expect leakage, don't call it forbidden.
- N-of-1 randomized trials — statistically void at n=1, week-level alternation, ~20-sentence
  quarterly canary samples. Keep canary set as a trend line only.
- Per-item decay anomaly detection — too few data points per item once FSRS intervals grow. Use
  leech detection (Anki-style N-lapses flag) instead.
- Personal audiogram via adaptive-staircase synthesized stimuli — research project. Build a
  confusion matrix from existing dictation diffs instead.
- Voice-clone self-modeling as "probably most effective" — unsupported claim, cheap to try, don't
  oversell it.

**Structure proposed:** Base (vault format w/ fixes, parser+validate, FSRS+reviews.jsonl,
known_set, minimal fmt, Anki/CSV export, coverage+tokenizer pulled into base, canary set sealed
immediately) / Future (MCP server, weekly letter, dictation, TTS, media pipeline) / Discovery
(constrained conversation, pitch overlay, voice clone, semantic gap report) / Disregard
(automated scoring, rewind telemetry, N-of-1 framing, per-item decay modeling, dynamic column
mutation, standalone app).

---

## Round 1 — pivot to public multi-user app (v2)

User clarified: this is meant to be a **public, generalized app**, not a personal vault. Raised
concrns that killed Markdown-as-source-of-truth for that context:
- Content-hash IDs break on in-session edits (hash changes mid-session orphans in-flight
  references) — tolerable for one user with git, fatal for a public app.
- Metadata-per-word (8-12 fields: kanji, reading, pitch, POS, register, topic, mastery, audio ref,
  provenance, frequency) is too heavy for a readable Markdown table.
- Obsidian can hide frontmatter but not derived table columns cleanly — hacky, breaks on updates.
- l1-interference profile becomes a per-user onboarding feature feeding a teacher agent, not a
  hand-written doc, in a generalized product.

**v2 mockup (`katagiri-v2-mockup.md`, not preserved verbatim — summary below):**
Postgres source of truth. Global dictionary-anchored lexicon (JMdict + UniDic) with per-user
overlay (`user_items`). Append-only `review_events` (kept from v1 — best idea in it). Derived/
rebuildable cache tier (known_set, FSRS state, coverage). Client-side SQLite mirror + offline
queue. Pillars: (1) known_set core primitive, (2) coverage/i+1 media-gating tool, (3) speech-first
SRS w/ spoken answers, (4) LLM teacher agent (onboarding L1 profile, weekly letters, confusion-
graph drills), (5) full data export day one.

### Debunker panel round 1 (4 agents, Opus): design, tech, market, pedagogy

**Design findings (12, top 4 fatal):**
1. No import path — only users the product would impress (intermediates w/ existing Anki/jpdb
   decks) get told "you know nothing" on first use. Import must ship before export.
2. Nobody fills the vocab — no seeded curriculum/decks, user-authored topics don't scale.
3. "ASR fuzzy check + self-grade" is two chores stacked → silently degrades to tap-to-reveal,
   poisoning `review_events` with grades for utterances that never happened.
4. No product, only a toolbox — no default "what do I do for 10 minutes" home screen.
   (Also: onboarding-as-LLM-interview before first card; no pronunciation feedback despite high
   friction of recording; offline only works for cards you don't care about; coverage tool's
   YouTube-paste UX is fragile and in the wrong place; settings surface is power-user complexity;
   "view your knowledge" undefined and worse than jpdb/WaniKani; weekly letters are founder taste;
   export-on-day-one is a churn ramp.)

**Tech findings (12, top 4 fatal):**
1. JMdict entry-level identity can't represent the confusion-graph headline example (見る/観る are
   spellings of ONE entry) and inflates known_set across senses/spellings.
2. LLM as primary content author with v1's audit layer (git diff review) deleted and the author
   scaled to every user — no bisect, no reviewer.
3. No alias/redirect table — JMdict version churn and custom-word promotion both orphan history;
   v1 had `aliases.tsv` for this, v2 dropped it.
4. "Append-only merges trivially offline" is false: needs event ULID+dedupe, dual timestamps
   (device+server), timezone/day_key, and a sibling-burial policy for same-day interference.
   (Also: server-side YouTube ingestion is ToS-risk + IP-blocked; Kanjium pitch DB license is
   murky, JMdict is CC BY-SA (attribution obligations on exports); VOICEVOX per-character license
   terms vary and homograph/compound-accent errors are product poison; FSRS per-card model doesn't
   fit multi-direction same-item cards; UniDic↔JMdict has no official crosswalk; stack should be
   one Postgres + one monolith + one worker, not 6 services; build order should front-load the
   differentiator (speech) not the commoditized part (coverage).)

**Market findings (verdict-level):**
jpdb.io already ships pillars 1+2 (known_set + coverage/i+1 + 21k prebuilt decks) for **free**.
Speech pillar without scoring = Anki-with-a-mic, already exists. Cold-start content moat: v2 has
none, competitors have years of curated decks. Realistic ceiling for solo entrant: low four
figures of subscribers. Unit economics survivable (~$2-4/user/mo variable) but demand is the
real problem, not cost. **Verdict: narrowest viable positioning = a speaking layer, not a full
app** — import known_set from jpdb/Anki, spoken-production drills against the real gap list,
L1-specific interference targeting (Slavic-L1 Japanese has zero competitors), honest pronunciation
feedback instead of refusing to score. ~$5/mo web-billed.

**Pedagogy findings (12, top 3 fatal):**
1. Speech-first ships with no feedback loop; self-graded prosody is circular (learner is
   perceptually deaf to the exact errors they'd grade — Flege/Best). Fix: perception (minimal-pair
   AXB, dictation) before production; gate production behind perception accuracy.
2. Word-level SRS as the spine measures the thinnest aspect of word knowledge (Nation's
   aspects-of-word-knowledge framework); isolated-word cards don't train connected-speech
   recognition. Fix: word as scheduling index, sentence as the reviewed stimulus.
3. Coverage math itself is wrong: adequate comprehension needs ~95-98% token coverage
   (Hu & Nation; Van Zeeland & Schmitt), not the 90-92% in the mockup, and tokenization inflates
   further (grammar constructions counted as "known" morphemes).
   (Also: i+1 at word level ignores grammar entirely — v2 dropped v1's grammar DAG; no
   comprehensible-input library; no interaction/negotiation of meaning/writing — "SRS with
   extras"; LLM-generated L1-interference profiles will confidently misteach for L1s with thin
   contrastive literature, and v1's own hedging/confidence discipline doesn't survive automation;
   FSRS fit on a self-graded scale that folds latency into grade, double-counting; pitch accent is
   phrase-level, not lexeme-level, so day-1 isolated-word drilling teaches something that doesn't
   survive contact with a sentence; weekly LLM letters are the worst cost/effect ratio in the
   stack; register tags `feminine`/`masculine` encode an outdated sociolinguistic model.)

### Round 1 synthesis (delivered, not yet rebuilt into a new mockup at that point)
All four panels converged: coverage/i+1 (the planned wedge) is the weakest pillar; speech pillar
as specified is empty; grammar has no representation; LLM must never author language facts;
identity layer under-designed; import must precede export; stack over-built.

---

## Round 2 — agent-native pivot (v3)

User's stance: "the whole system should be based on what an agent would develop while working
with the user... yes results may be inconsistent, but they will be personalized." Give the agent
tools, accept inconsistency for personalization.

**v3 mockup (`katagiri-v3-mockup.md`, summary):**
Load-bearing rule: **"The agent owns judgment. The system owns facts and state."** Inconsistency
in pedagogy = feature; inconsistency in facts or state = poison.
- **Layer 1 (substrate, deterministic, LLM never writes):** lexicon (UniDic accent fields, not
  Kanjium), `grammar_constructions` (restored from v1, writer listed as "import + agent (level
  only)" — this contradiction was later found fatal), append-only `review_events` w/ ULID + dual
  timestamps + tz, derived FSRS state, `session_transcripts`.
- **Layer 2 (agent memory, LLM-authored, second-class):** `learner_model` (beliefs w/ confidence/
  evidence/written_at/superseded_by), `teaching_notes`, `plans` (user-editable prose), `hypotheses`
  (testable claims). Two safety rules: (a) a hypothesis may reorder the drill queue, never remove
  from it; (b) Layer 2 fully user-visible/editable — "the user is the diff review that v1 got from
  git."
- **Layer 3 (tool belt — "this is the product"):** ~20 tools across knowledge (lookup, known_set,
  grammar_state, history), practice (due, submit_review, drill templates, conjugate), speech
  (speak/VOICEVOX w/ accent override, transcribe as binary check, perception_probe AXB gating
  production, pitch_contour visualization only), memory (remember/recall/log_error/note/
  propose_plan), content (coverage on pasted text only, curated library, import).
- **Layer 4 (guardrails, "~a week"):** regex fact linter on agent output vs lexicon; agent
  read-only for facts; injection containment via pre-summarized structured input; deterministic
  floor (due cards) enforced by app not prompt; per-user cost ceiling degrading to template drills.

### Debunker panel round 2 (5 agents, Opus): design, agent-reliability, tech, pedagogy, market

**Design findings (12, top 4 fatal):**
1. "User is the diff review" is a non-mechanism — beginner can't audit unfalsifiable claims about
   their own interlanguage in a language they don't know (v1's own example: `~~ら flap~~` struck
   by an LLM, verifiable only by someone who can already hear the flap). Fix: audit by scheduled
   experiment (Layer 1 test + auto-expiry), not by user reading.
2. LLM latency inside the highest-frequency interaction (card answering, 60-200x/session) —
   conversational wrapper turns a 6-min session into 14 with a spinner per card. Fix: agent
   composes before/after the drill block, never in the request path of a card.
3. Agent-decided session length can't honor a daily commitment (10-20min claim has ~100% variance;
   due-count swings 12→180 after a lapse). Fix: user picks a time budget, app enforces as wall
   clock/truncates queue, agent has no say in ending session.
4. No path/ladder/visible position — frequency-rank spine moves invisibly per session; no answer
   to "how far along am I." Fix: deterministic band ladder (top-2000 in 8 bands) computed by app,
   agent can't move it.
(Also: blank-page problem — chat surface with nothing to type, esp. for beginners who need a
teacher precisely because they can't self-direct; editable prose plan has no confirmation loop —
"slot machine" feel; onboarding contradiction — agent needs a model, interviews rejected, 60s-to-
first-review all simultaneously claimed; mic/headphone-dependent sessions fail in the dominant
study context (commute, open office); no repair UX for agent mistakes (repeats, forgets,
contradicts, wrongly praises); no shareable/growth-loop artifact, and personalization deletes
the shared-coordinate substrate that gives WaniKani/Duolingo community; English-only agent prose
contradicts the L1-interference feature's own target users. **Key reframe: "the moat is the floor,
not the ceiling" — what stops a user going to ChatGPT is the deterministic scheduler + ladder +
bounded session, not the agent.**)

**Agent-reliability findings (12, top 5 fatal):**
1. Grading via agent-called `submit_review` makes the "app-enforced floor" unenforceable — at 1-2%
   per-call omission rate over ~40 graded exchanges/session, 33-55% chance of a silently missing
   event per session; scheduler drifts with no artifact to debug. Fix: card exchange is an
   app-owned UI component; agent injects/reads callback; delete `submit_review` from the tool belt.
2. Fact linter checks notation; dominant error class isn't notation — wrong semantic explanations
   ("死んでいる means is dying," mapping ている onto Slavic aspect) contain zero readings/accent
   marks, pass clean, get written to teaching_notes forever. Also creates false positives that
   suppress natural spoken register (してる, 分かんない) — undermining the speech-first
   differentiator. Fix: agent emits typed fact-references (`{{lex:id.field}}`), app renders from
   lexicon; any bare factual string outside a reference = rejected turn.
3. Agent-authored grammar explanations have zero guardrail (only cheap surfaces were hardened).
   Fix: human-authored canonical semantics + gold contrast pairs per construction; agent
   personalizes framing only; LLM-judge checks against gold text. Real content-authoring cost.
4. "Reorder never remove" fails at the definition boundary — with an unbounded queue and bounded
   session, reorder to rank 47 IS remove. Fix: per-item staleness SLA force-injected by code,
   can't be reordered out; agent gets a "tray" not the queue; add a starvation monitor as a
   product metric.
5. Layer 2 evidence can cite the agent's own prior transcript utterances — literal self-
   confirmation loop, confidence self-graded by the same model, compounds over months into a
   functional "fact" that outranks contradicting event data. Fix: evidence typed to only
   event/probe/error rows (never transcript/agent-authored); confidence decays unless refreshed
   by new event evidence; cap "anecdote" evidence at low confidence.
(Also: no replay/versioning record for agent turns — the nondeterministic half is exactly the
half left unreproducible, backwards from the FSRS versioning discipline; no retrieval policy for
Layer 2 — >80% of accumulated context must be dropped every session with the selection mechanism
unspecified, and natural truncation order discards exactly the slow long-tail signal fossilizing
errors are made of; `learner_model` unbounded free-form store makes contradictions undetectable —
split into enumerated single-writer slots + capped FIFO scratch, cut the unbounded belief graph;
`grammar_constructions` writer "import + agent (level only)" already violates Layer 1's own "LLM
never writes facts" rule; core thesis is statistically unfalsifiable at achievable scale (needs
~350 adherent users for a powered A/B, realistic yield 60-100) — build invariant tests + a golden
violation corpus + simulated-persona runs instead of a live experiment; cost ceiling as a
mid-session cliff punishes the most-engaged paying cohort — fix via pre-session budgeting +
frozen/append-only context for cache efficiency + agent working one block ahead of the card
being answered.)

**Tech findings (12, top 4 fatal, converges independently with agent-reliability on #1/#2):**
1. Fact linter is unenforceable by regex — mention vs. assertion undecidable by pattern (every
   negative teaching example, "don't say X," is a false positive); accent claims in natural prose
   have no notation to extract; conjugation well-formedness ≠ appropriateness (passive-for-
   causative is well-formed AND wrong, passes clean); "block and regenerate" has no bounded
   convergence. Same fix as agent-reliability: typed fact-references rendered server-side.
2. `drill(type,...)` deterministic for only 1 of 7 claimed types (`conjugate`). `particle_pick` is
   *unsound* — は/が, に/へ, で/に are often both grammatical with different meaning, so a template
   marks correct Japanese wrong precisely where learners are most confused. `register_fix` has no
   deterministic ground truth. And `drill` is the stated fallback when the cost ceiling trips —
   the "safe cheap path" is the least-built, most-wrong subsystem.
3. `grammar_constructions` writer = "import" but nothing importable exists (no licensed
   machine-readable JP construction inventory w/ prereq edges) — means hand-authoring 200-800
   constructions is a hidden 4-8 week content project, and the resulting table IS a fixed
   curriculum sitting in Layer 1 — contradicting the thesis's own claim that "nobody ships a fixed
   curriculum." Detection (which constructions has the user been exposed to) also has no
   substrate: UniDic doesn't annotate lexical-aspect class needed to distinguish progressive vs.
   resultative ている.
4. Perception probes are unsynthesizable as specified — VOICEVOX is Japanese-phonology-only TTS,
   structurally cannot produce the wrong side of an L1 contrast (no labiodental [f] for ふ, no
   English-R for ら), so most of the useful AXB contrasts can't be generated; single-voice
   synthetic stimuli also let learners pass by detecting synthesis artifacts rather than the
   contrast; probe gates production, so a broken probe hard-blocks the core loop.
(Also: injection containment via "pre-summarized structured input" just moves the injected
component upstream to the summarizer — needs real privilege separation (untrusted-context agent
returns only fixed-schema struct, never free text, no write tools); VOICEVOX accent-override
pipeline breaks on segmentation not accent — engine phrases multi-word utterances by its own
rules, no well-defined override target for compounds, and per-character commercial license terms
vary (same landmine class as Kanjium); import resolution from Anki .apkg or jpdb only reaches
headword granularity, not the sense-level identity Layer 1 requires — jpdb has no public API,
prefer WaniKani; Layer 2 becomes authoritative via unaudited embedding-search retrieval since
beliefs are prose not keyed to lexeme IDs; append-only `review_events` "never deleted" conflicts
with GDPR Art.17 erasure rights, and conversational transcripts are inherently special-category
data; ~20 tools is a 4-8 week API-product effort minimum, and `lookup` has no disambiguation
protocol for homographs (生, はし) despite every downstream write keying off its result; honest
build estimate 14-20 calendar months solo as specified, with Layer 4 "~a week" off by 6-10x and
wrong in kind, not just under-scoped.)

**Pedagogy findings (12, top 3 fatal, converges with tech/agent-reliability independently):**
1. The complexity budget is spent where evidence is thinnest — the only enforced floor (FSRS on
   recognition cards) is what jpdb/Anki already do for free; the effects with real evidence
   (distributed practice, retrieval practice, interleaving, input volume, corrective feedback) are
   all schedule/volume properties deliverable by a static syllabus, needing no LLM; the effects
   that need per-learner adaptation (aptitude-treatment interaction) are small and narrow in the
   literature. Fix: four app-enforced weekly budgets (retrieval, input minutes, production turns,
   phonological trials); agent allocates within, can't zero any.
2. Nothing in v3 measures learning — FSRS retention is circular (a fit statistic for the
   scheduler's own items, not proficiency); "you know 1,240 lemmas" is a rendering of card state.
   Without a syllabus there's no sampling frame, so no construct-validity argument is even
   possible. Fix (highest-value item in the whole review per this panel): a held-out probe bank,
   stratified by frequency/construction, never taught/scheduled, monthly battery (yes/no vocab w/
   pseudoword correction, elicited imitation, timed+untimed grammaticality judgment).
3. `learner_model` is unfalsifiable by construction — evidence refs point at instruments (chat
   quotes, recognition-card grades) that cannot confirm the constructs being claimed (interlanguage
   transfer hypotheses); v1's own l1-profile.md is the specimen (theme-rheme framing claim already
   silently rewrote a curriculum decision with no field where it can ever be shown false). Fix:
   a belief is inert unless it names an operationalizable test + pass threshold + expiry; auto-
   demote to `unsupported` if unconfirmed.
(Also: agent-decided sequencing fights developmental readiness — Processability Theory/Teachability
Hypothesis says instruction above a learner's processing stage produces no durable gain; add
`processability_stage` computed at import, not agent-writable, ready-set computed by app; LLM's
comparative advantage produces declarative not procedural knowledge — nothing in v3 has a fluency
dimension despite `latency_ms` already being logged unused — gate "known" on accuracy + RT band +
falling coefficient-of-variation of RT; per-session greedy agent decisions systematically prefer
the *worse* schedule because blocked/massed practice feels like more progress to both learner and
a preference-tuned model (Bjork's desirable-difficulties / metacognitive-illusion research) — make
interleaving of confusable pairs an app-enforced floor property; recognition-fit FSRS state sold
as "knowing" hides the large receptive/productive gap, and one scalar `understanding` level
collapses dimensions that dissociate in the literature — split into receptive/productive-untimed/
productive-timed, each written only by drill outcome, agent proposes target not level; perception-
gates-production is invalid twice over (perception/production dissociate; AXB passable via
acoustic memory without a real category) — cut the gate, use AXB pre/post as measurement only, use
multi-talker natural recordings for training (the literature's actual active ingredient) not
single-voice synthetic; after self-graded prosody was killed, NOTHING gives spoken production
feedback — a fossilization engine from the other direction; still "an SRS with a chatbot on top" —
no reading-at-volume engine despite accepting 95-98% coverage thresholds (which are reading-at-
volume thresholds) in round 1, no interaction/negotiation of meaning/output-under-pressure/writing;
"agent decides everything" removes autonomy (the motivational lever the product can least afford
to lose per Self-Determination Theory) without replacing it with anything, and relatedness has zero
support anywhere; `log_error` has no corrective-feedback policy, so a preference-tuned model
defaults to recasts — the CF type research shows produces the least learner repair. **Structural
reframe, independently converging with design/agent-reliability: the defensible architecture is
not "deterministic floor, agentic ceiling" but a deterministic *skeleton* (stage-ordered ready set,
enforced practice distribution, enforced volume budgets, held-out assessment) with agentic *flesh*
only on the genuinely per-learner parts: explanation framing, example choice, mnemonic hooks,
motivational rationale, error interpretation.**)

**Market findings (verdict-level, worse than round 1):**
- Cost model negative at every consumer price point for a daily-active user: ~20k tokens resent
  per invocation × 35-45 invocations/session → frontier-model cost ≈$79/user/month; mid-tier
  ≈$4.50-9 COGS vs ≈$7 net after App Store 30% cut. Break-even needs ~$20-25/mo web-billed —
  ChatGPT Plus price, for one language. **The business is only solvent because of churn; improving
  retention makes the unit economics worse.**
- The exact "LLM tutor + FSRS + tool belt" pattern already ships free: a public Anki-forum web app
  (.apkg upload → voice-tutor review → FSRS-preserved download) and OpenTutor (open source,
  BYO-key, "FSRS + knowledge-graph-aware prioritization"). Layer 3 — called "what we actually
  build; it IS the product" in the mockup — is the exact layer Anki MCP servers commoditized in
  2025.
- Bundled first-party substitution now live: ChatGPT Study Mode (all plans, globally, voice+
  memory), Google Guided Learning/LearnLM, Claude Projects — all inside the $20/mo the target user
  already pays.
- Langua ($12.50/mo, 20+ languages) already ships the v3 session loop: voice conversation, SRS
  auto-mined from what was said, saved vocab woven back into future chats.
- AI-category apps retain measurably worse than non-AI apps (RevenueCat 2026: 6.1% vs 9.5%
  monthly, 21.1% vs 30.7% annual) — direct empirical answer to the open question of whether
  agent-led retains better than a gamified fixed path: no.
- Peer-reviewed AI-app review analysis shows self-contradiction/hallucination as a dominant
  complaint category, and AI-tutor reviews specifically cite "stays at beginner level no matter
  how you push," "forgets," "repeats itself" — exactly the failure modes "inconsistency is the
  feature" produces, and the fact linter (as designed) can't catch any of them since they're not
  notation errors.
- EU AI Act Annex III classifies AI that evaluates learning outcomes/determines level as
  high-risk; v3's core loop (agent-set curriculum, perception-gated production, per-user
  understanding levels) is exactly that; standalone obligation date (2 Dec 2027) falls inside the
  product's plausible lifetime.
- **Verdict: no candidate positioning survives as a venture** — B2B is the worst fit for
  nondeterminism + Annex III; premium/fewer-users loses to Langua at $12.50 and ChatGPT at $20;
  BYO-API-key/self-hosted is exactly what OpenTutor already gives away free. **The one asset with
  no competitor and no LLM-feature equivalent is static, deterministic, authored Slavic-L1
  contrastive Japanese content** (Ukrainian/Russian perfective→た interference, palatalization,
  ら-flap, aspect mapping) as decks/readers/course — the literal opposite of the agent-native
  pivot. The pivot does not survive as a business; keep it as a personal tool / open-source repo.**

### Round 2 synthesis
Independent, unprompted convergence across all 5 panels on one reframe: **"the moat is the floor,
not the ceiling"** — the agent layer is exactly what's commoditizing for free (jpdb, OpenTutor,
ChatGPT Study Mode, Langua); the deterministic substrate (identity layer, event log, drill
templates, held-out probe bank, replay/eval harness) is the only defensible asset. Recommended
structure: deterministic *skeleton* (stage-ordered ready set, enforced practice-distribution/
volume budgets, held-out assessment) + agentic *flesh* only on framing/examples/mnemonics/
motivation/error-interpretation — roughly the 15% of v3's scope that was ever safely agent-owned.

Three options given: (A) build v3.1 (skeleton+flesh) as a product — technically fixable but
business case still fails on cost + free incumbents + bundled substitution; (B) content play
(Slavic-L1 contrastive Japanese, static, per market's suggestion) — not what the user wants to
build; (C) **recommended** — build v3.1 for personal use / open-source the substrate, decide on a
product in 6 months with real n=1 data from the held-out probe bank, which sidesteps the cost
model, Annex III exposure, App Store cut, and churn math entirely.

---

## Round 3 — v4 (MCP substrate + progressive-translation overlay), debunked

User's direction (as given before the earlier session save):

1. **Defer speaking + "understanding" lessons to external general agents** (ChatGPT/Claude acting
   as tutors) that use Katagiri's MCP server as their tool/data layer — i.e. Katagiri stops trying
   to build its own tutor-agent UX and instead becomes **the substrate other people's agents plug
   into**. This directly matches Round 2's converged conclusion that the substrate (not the agent)
   is the defensible asset — MCP existing "only to keep track of everything" is a much smaller,
   more honest scope than v3's Layer 3.
2. **Voiceovers for specific words still matter** — TTS/pronunciation reference stays in scope,
   presumably still via VOICEVOX w/ accent override (per Round 2 tech findings: apply override
   only at single-lexeme granularity, cache by content hash, watch per-character license terms).
3. **Grammar grading deferred to the agent, based on context/topic** — i.e. explicit
   `grammar_constructions` authoring/detection (Round 2 tech finding #3, fatal) may no longer be
   Katagiri's problem to solve at all if an external agent judges grammar in situ.
4. **New stated core selling point, not previously mocked or debunked:** a **simple, native-feeling
   media-consumption overlay** for text/subtitles that **progressively replaces translation with
   Japanese** as the user's known_set grows — i.e. a text/subtitle overlay that starts mostly-L1
   and mixes in more Japanese words over time until the passage is fully Japanese. Explicitly
   framed by the user as: "that's actually our main selling point... but that 'other stuff' is the
   base everything builds off." So: known_set + identity layer + event log (the substrate) stays
   as foundational infrastructure; the progressive-translation media overlay is the actual product
   surface / wedge feature to differentiate on, not coverage-as-a-standalone-tool and not the
   agent-tutor UX.

This direction was not yet turned into a mockup or run through a debunker panel before the session
save request interrupted the round. Notable adjacent facts already established that bear on it:
- Round 1 market panel flagged Language Reactor / asbplayer / Migaku as already doing
  client-side-in-the-player overlay-style tooling — worth explicitly comparing the progressive-
  translation overlay against these on next pass (none of them do *progressive* replacement keyed
  to a personal known_set, as far as established in this session — that may be the actual
  differentiator, unverified).
- Round 2 tech finding #5: server-side ingestion of arbitrary video/subtitle text remains a ToS/
  legal problem regardless of which layer "does the teaching" — the overlay itself would need to
  run client-side (browser extension / on-device), consistent with the fix already recommended for
  `coverage`.
- If Katagiri's MCP is now primarily a tracking/state layer for third-party agents to call, the
  Round 2 guardrail findings (fact linter unworkable, agent-authored grammar semantics unguarded,
  `submit_review` needing to be app-owned not agent-called, Layer 2 belief-evidence contamination)
  arguably transfer almost unchanged to "how does an external ChatGPT/Claude session calling this
  MCP get graded/logged safely" — the guardrail work doesn't go away just because Katagiri isn't
  hosting the tutor itself.

### v4 mockup

Written to `docs/katagiri-v4-mockup.md` (persisted in repo this time). Components: (A) substrate
(identity layer w/ alias table, receptive/productive known_set, append-only review+exposure
events, FSRS, voiceover cache), (B) overlay product (browser extension + reader; L1 text with
Japanese words substituted per known_set, ratio grows to 100% JP; subtitle variant; content-word
substitution + staged "sentence-frame flips" for grammar; all client-side), (C) MCP server
(~10 tools, external ChatGPT/Claude agents as tutors, three options a/b/c for the submit_review
trust problem), (D) explicit non-goals (no hosted tutor, no grammar table, no learner_model).

### Debunker panel round 3 (5 agents): design, tech, pedagogy, market, agent-interop

**The Toucan precedent (all five panels, independently verified):** Toucan was exactly this
mechanic — browser extension substituting L2 words into L1 web pages, Japanese included. Raised
~$30M (~$4.5M seed 2021 + $20M Series A), 300k+ Chrome installs, 4.7 rating, "clearly wasn't able
to become a profitable business," wound down early 2023; Babbel bought the technology only (no
founders/staff, Sept 2023) and moved all paid features to the free tier — the acquirer's own
pricing verdict that the mechanic is a free engagement feature, not a product. Babbel doesn't
even sell Japanese. The only other funded dynamic-substitution attempt (LoomVue, IES-grant-funded
"dynamic diglot weave") pivoted off the extension surface entirely (citing real-time translation
cost, install friction, site fragility) and never supported Japanese.

**Design findings (12, top 4 fatal):**
1. Day-1 invisibility — substitution ratio driven by known_set, new user's known_set is empty, so
   the product renders nothing on first run; v4 also silently dropped round-1's "import before
   export" fix (no Anki/jpdb/WaniKani import anywhere). Fix: import + 2-min self-placement seeds
   known_set; force-substitute a starter band so the product visibly exists in 60 seconds.
2. Tap-to-gloss at subtitle speed physically impossible — line lives 1-4s, interaction loop is
   4-8s, no hover on touch, click collides with player pause. Competitors are usable only via
   auto-pause/sidebar, none of which v4 specifies. Fix: auto-pause on lines w/ target items +
   post-episode encounter-review queue as the primary interaction.
3. Extension can't reach where the behavior happens — anime is watched in Netflix/Crunchyroll
   mobile/TV apps, manga on paper/apps; Chrome Android has no extensions. "Media overlay" as
   specced = desktop-power-user tooling, the niche Language Reactor/asbplayer already own free.
   Fix: honest wedge = desktop browser + first-party mobile reader app (surface Katagiri
   controls), or lead with reading and cut subtitles.
4. "Connect your own tutor" is a developer task (ChatGPT: developer mode + paid tier + public
   HTTPS URL + injection warnings) — the un-connected majority gets a product with no teaching in
   it. Fix: product must be complete with zero agents connected; tutor = power-user bonus.
   (Also: re-breaks round-2 session/ladder/repair fixes by omission — no due surface, no "did I
   study today," no demote-back-to-L1 control; mixed-script typography unexamined — ransom-note
   lines, reflow on every known_set update, no rule for which orthographic form appears; value
   curve is inverted-U with churn at both ends — invisible at ratio 0, taxes the user's actual
   goal in the middle, graduates best users into churning at the top; tap gesture conflates
   lookup/learn/curiosity into one polluted signal; "server sees only token IDs" is still a
   de-facto watch-history fingerprint — privacy claim overreaches.)

**Tech findings (12, top 4 fatal):**
1. The alignment layer — the load-bearing component — is unspecified and research-grade. Per-
   sentence bilingual word alignment En↔Ja runs ~35-47% AER on the best published aligners (one
   of the worst measured pairs; En-Fr is ~15-17%); Uk↔Ja has no models, no gold data, would pivot
   through English compounding error; many Japanese tokens have NO L1 counterpart span at all
   (dropped subjects, particles, light-verb constructions). 10-20% realistic per-substitution
   error rate = several confidently-wrong sense mappings per page, each writing a poisoned
   exposure event. Fix: cut arbitrary-page substitution; pre-align a curated licensed library
   offline w/ human review; arbitrary pages get tap-gloss only (Yomitan territory).
2. "All client-side" and the pipeline are mutually exclusive — client JP tokenization is fine
   (lindera-wasm/kuromoji.js; Yomitan proves JMdict-in-extension), but that's the easy 10%: no
   on-device MT exists for Ja at all in Bergamot/Firefox Translations, Uk is dev-tier, Uk↔Ja
   on-device does not exist anywhere; full UniDic w/ accent fields is too big for an extension.
   Cloud MT violates v4's own privacy posture and attaches per-pageview COGS to a free surface.
   Same fix: server-side offline preprocessing of curated content; client does display + events.
3. Sentence-frame flips have no possible deterministic implementation — it's rule-based MT
   (abandoned by the field), requiring particle generation that round 2 already established can't
   even be deterministically *graded*; every error is presented as ground truth to a learner who
   can't detect it. Cut; if graded grammar exposure matters, hand-author parallel ladder tiers.
4. Aligned L1+JP subtitle pairs mostly don't exist — JP subs live on JP-catalog titles, Uk subs
   on Ukraine-licensed titles, near-empty intersection, and even En+JP pairs aren't line-aligned
   (JP subs condense/re-segment); Language Reactor's answer is MT-ing one track, so the mechanic
   becomes substitution into an MT line — MT error × alignment error in real time. Fix: local
   files + user-supplied subs only (asbplayer model); streaming = best-effort, expect breakage.
   (Also: all three submit_review options fail as specced — (a) FSRS has no provenance dimension
   so lower-trust grades either pollute or are theater, (b) is coherent but falsifies "grading
   deferred to agents," (c) depends on per-host app platforms; pick (b), MCP is read-mostly +
   low-stakes writes. MCP server = multi-tenant SaaS (OAuth 2.1 + DCR + rate limits + abuse
   handling) for a product with no revenue. Exposure→strength loop is self-inflating — overlay
   shows word because scheduler thinks it's known, sight of it then raises strength: monotonic
   ratchet on zero evidence; exposures must never raise strength. Sense-level identity re-broken
   by arbitrary-text substitution (WSD unspecified) — round-1 finding #1 returns. MV3/store-
   takedown risk documented (LLN rename precedent). coverage(text) via MCP means the server DOES
   see raw text — falsifies the privacy claim as written. VOICEVOX carry-ins kept but need a
   per-voice license matrix + credit strings as an artifact. Honest estimate: 6-9 months solo for
   the defensible subset; 24+ months for the mockup as written, which still ships a
   probabilistically wrong core mechanic.)

**Pedagogy findings (12, top 4 fatal):**
1. The evidence base shows flashcard-parity, receptive vocab only — Burling 1968 is a course
   report with no outcome measures that mainstream practice ignored for 55 years; the best
   controlled study (Christensen, Merrill & Yanchar 2007) found the diglot reader "equally as
   effective as" drill-and-practice — i.e., parity with the flashcards Katagiri already has;
   nothing tests grammar, listening, production, retention, or Japanese. "50% better retention"
   claims trace to vendor marketing (Prismatext). What it does deliver: receptive form-meaning
   links + a real, replicated motivation/adherence benefit. Fix: reposition overlay internally
   as a spaced re-exposure + motivation layer for items FIRST LEARNED ELSEWHERE — never as the
   mechanism of first learning or grammar.
2. v4's real weekly practice mix re-breaks round-2 pedagogy fatals #1/#2 — for the modal
   (agent-less) user: FSRS recognition cards + reading L1 text with JP nouns + tapping glosses.
   No production, no feedback, no grammar, no listening-as-listening; held-out probe bank
   (round 2's highest-value item) silently gone; four enforced budgets gone. Ceiling: passes
   yes/no vocab tests, cannot read/parse/understand/produce. Fix: probe battery + budget floors
   are substrate, not tutor UX — they stay in Katagiri's scope regardless of who tutors.
3. Exposure→strength is circular and corrupts known_set — the one asset the pivot exists to
   protect. Incidental-learning research: ~8-20 *attended* encounters for durable gains; eye-
   tracking shows context-predictable items get skipped — the embedded word whose meaning rides
   on the L1 frame is exactly the skippable one. "Same text becomes 100% Japanese" then measures
   the inflation loop, not the learner. Fix: exposures may decay-refresh items already proven by
   graded retrieval, never raise strength or admit to known_set; sampled one-tap "did you know
   this?" micro-probes calibrate exposure weight and double as missing measurement.
4. The subtitle surface trains the empirically harmful condition — L1 subtitles harm foreign
   speech perception (Mitterer & McQueen 2009), L2/same-language captions help (Birulés-Muntané &
   Soto-Faraco 2016; Montero Perez et al. meta-analysis); keyword/partial-captioning studies
   found full captions beat keyword captions, learners rate partial captions *distracting*;
   subtitle reading is automatic and attention-dominant (d'Ydewalle) — the learner reads L1, not
   listens to Japanese. The woven JP word sits at its L1-syntax position while the audio delivers
   it verb-final — the imagined read/heard binding mostly can't occur. Fix: cut the woven-L1
   ladder as a listening feature; honest ladder = L1 subs → full-JP subs + furigana + tap-gloss →
   none, with known_set personalizing *when to promote* and *what to gloss* (the defensible
   novelty).
   (Also: frame-flip carries the whole grammar story and is unbuilt — below it the learner
   acquires zero syntax, and Burling's original gradation was hand-authored; at the threshold
   hand off to real graded-reader sentences at 98%+ known coverage instead. A JP word in an L1
   frame is learned as an L1 lemma with JP phonology (Jiang 2000 lexical mediation) — no
   particles, wrong collocates ("drink" medicine), bare verb stems incoherent for an agglutinative
   language; substitute chunks (薬を飲む) and always gloss inside one real JP example sentence w/
   audio. Kanji arrive with readings behind an optional tap → private logography that supports
   neither listening nor reading aloud; force furigana/auto-audio for first N encounters, gate
   known_set admission on reading-recall not meaning-recognition. Mixed-script hybrid reading is
   plausibly a pseudo-skill — real Japanese is unsegmented, head-final; overlay pre-solves
   segmentation via L1 spacing; no transfer research exists (risk, not license); start full-JP
   graded sentences early + timed real-JP transfer probes. Tap conflates "I didn't recognize
   this" (lapse signal) with add/curiosity — separate event semantics. Per-lexeme citation-form
   single-voice TTS is near-zero as a phonology strand (keep as reference only). Where it IS
   strong: sidesteps the 95-98% coverage lockout for beginners, delivers incidental retrieval
   practice + spacing on known items, real adherence benefit, cheap kanji desensitization — as a
   retention/motivation surface it is "the strongest single feature this project has produced
   across four versions.")

**Agent-interop findings (12, top 4 fatal/severe):**
1. Option (a) "lower-trust event class" is incoherent — FSRS has no provenance dimension; grades
   either feed state (one bad ChatGPT session grading "Easy" on 40 untested items inflates
   stabilities, retention collapses weeks later with no visible cause) or don't (tutor grading is
   theater, product promise false). Kill (a); demote agent outcomes to the implicit exposure
   class.
2. "Prompt templates, not a tutor" is a scope illusion — no delivery channel for templates
   (permanent version skew: users run v1 templates against a v4 server forever), so every
   guarantee must move into the write pipeline: server-side lemma resolution, validation, dedupe,
   quarantine, reconciliation UI, per-source anomaly detection. The MCP write surface IS a
   validation product, same order of work as v3's Layer 4 after the 6-10x correction.
3. Cross-agent grade-semantics drift + in-conversation flooding breaks FSRS core assumptions even
   with honest events — three tutors map "correct" differently; a 20-min conversation touches
   食べる five times and a dutiful agent logs five reviews. Fix: NEVER accept a grade — accept a
   structured observation {item_id, task_type, expected, produced, unassisted}; server derives
   the grade under one rubric (realistically binary pass/fail → Good/Again); server rule
   collapses same-item same-day observations to one event.
4. Option (c) is now genuinely implementable (post-mockup fact): MCP Apps extension (SEP-1865,
   Jan 2026 spec) renders server-supplied sandboxed UI in both Claude and ChatGPT, and tool-result
   `_meta` reaches the component but NOT the model — so Katagiri can mint single-use grading
   nonces in `_meta`, render its own grade buttons, and accept submit_review only with a valid
   nonce (model can't fabricate what it never sees). Caveats: optional extension (local models
   and most hosts don't render it — fallback mandatory), reported host bugs dropping custom
   `_meta`, nonce must never live in a readable resource, elicitation still unsupported in
   claude.ai/ChatGPT.
   (Also: nothing forces resolve_lemma-first and MCP has no call-ordering semantics — writes must
   accept only server-issued session-scoped IDs, reject unserved IDs with corrective errors
   (agents follow tool errors, not descriptions), single-use review tokens per (item, session),
   two-phase add_item; missing logs are undetectable in-band — surface "served but never
   resolved" as a reconciliation queue. Write-capable MCP is a prompt-injection sink and consent
   UX belongs to the host — read-only default OAuth scope, write scope as second consent, burst
   quarantine (human tutoring ≈1-3 writes/min), free-text fields treated as untrusted. The
   "local model" persona — most aligned with the privacy posture — is exactly the client that
   can't do OAuth or MCP Apps and has the worst reliability: tier capabilities by client. Failure
   UX has no owner ("Katagiri says 500 words, ChatGPT says 100") — per-event provenance view +
   one-click bulk tombstone of "everything agent X wrote in session Y" (append-only-compatible,
   partially serves GDPR). Two concurrent tutors double-grade the same due items — server-side
   item leases. coverage(text)/log_error free text re-break the privacy posture — ephemeral
   tokenize-and-discard or client-side only. SOUND: the read-only tool set (known_set, stats,
   weakest, confusion_pairs, get_due) is genuinely safe to expose to arbitrary agents with just
   auth + rate limits — external tutors READING state has no serious failure mode; the append-
   only event spine is exactly what makes quarantine/tombstone/provenance implementable.)

**Market findings (verdict-level):**
- Money enters v4 nowhere — tutor value accrues to OpenAI/Anthropic, overlay competes with free
  (Toucan-by-Babbel, Vocabo, asbplayer), MCP server is pure cost with no gateable surface. v3's
  problem was COGS > price; v4's problem is no price. "Substrate other agents plug into" has zero
  capture mechanism — it's unpaid infrastructure making ChatGPT/Claude subscriptions more
  valuable, and platforms can internalize "remember which words the user knows" natively.
- Target user is a near-empty intersection with opposed halves: JP learners × desktop-browser
  watchers × will-configure-MCP. The power users who CAN configure connectors (Anki/jpdb/Refold
  culture) are ideologically L2-first and despise L1 scaffolding; the beginners who'd want L1
  scaffolding can't configure connectors. Plausibly four figures of humans worldwide.
- Platform risk fired twice against the comparables in the last 6 months: Language Reactor's
  Chrome listing "currently unavailable" (Feb 2026); asbplayer Netflix detection fully broken by
  Chrome 149 (June 2026). Solo product with extension-only surface dies on any one of: policy
  turn, store review, player refactor.
- GENUINE GAP CONFIRMED: nobody ships known_set/FSRS-keyed progressive substitution for Japanese
  (verified against Toucan/Babbel, LoomVue, Prismatext, Migaku, Vocabo, Language Reactor,
  asbplayer, Lingopie, jpdb, LingQ, Readlang, Satori, Manabi). But the gap is graveyard-shaped:
  both funded dynamic-substitution attempts avoided Japanese and failed commercially, and
  Japanese is the structurally worst-fit language for the mechanic (SOV, particles, no clean
  word-slot mapping). The gap exists because Japanese is where diglot weave works worst, not
  because nobody looked.
- WTP ceiling in the niche: $5-10/mo, and every paid comparator bundles a full system at that
  price (Language Reactor $5.95, Migaku ~$9-11, Lingopie $5.99, Satori ~$9, jpdb ~$5 Patreon).
- "Bring your own tutor via MCP" has no consumer-product precedent; the one real distribution
  channel (ChatGPT Apps directory) means OpenAI review, OpenAI owning the user relationship, and
  sherlock risk.
- Verdict: no venture-viable positioning; narrowest commercial shape is a $5/mo niche
  reader/subtitle tool sold on the progression ladder with app-owned reviews — a Migaku
  competitor fighting a decade head start with Toucan's corpse as the free alternative. Round-2
  option C (personal tool / open-source substrate) stands unoverturned.

### Round 3 synthesis

Five panels converged, again independently, on one architecture and one classification:

**The mechanic survives only inside a boundary.** As the *first-learning* mechanism, grammar
vehicle, or listening feature, the overlay is refuted (flashcard-parity evidence, harmful-
condition subtitle literature, unimplementable alignment/flip engines, Toucan's $30M natural
experiment). As a *spaced re-exposure + motivation layer for items first learned elsewhere*,
running on curated pre-aligned content with exposure events that never raise strength — it is
genuinely novel, genuinely undone by anyone, and pedagogy's verdict: the strongest single feature
across four versions. "Build it as the porch, not the house."

**The convergent respec (v4.1):**
1. Spine: app-owned daily review session (10-min wall-clock, app-enforced), import-seeded
   (Anki/jpdb/WaniKani), band ladder, held-out monthly probe battery — the round-2 skeleton,
   still non-negotiable.
2. Overlay: curated, offline-pre-aligned, human-reviewed reading library (first-party reader app
   + extension for the curated set), chunk-level substitution w/ furigana/audio-forcing rules,
   demote-control on every word (repair UX + negative signal). Arbitrary web pages: tap-gloss
   only. No frame flips — hand off to full-JP graded sentences at 98% coverage.
3. Subtitles: local files/user-supplied subs (asbplayer model); ladder = L1 → full-JP+gloss →
   none; known_set decides promotion timing and gloss selection. No woven-L1 listening claims.
4. MCP: read-only + log_exposure/log_error as low-trust classes for all clients (option b floor);
   signed grading cards via MCP Apps `_meta` nonces where hosts support it (option c ceiling);
   grades never accepted — structured observations, server-derived grades, item leases,
   provenance + bulk tombstone. Budget the write pipeline as a real validation product.
5. Voiceovers: keep as reference audio only (license matrix artifact required); not a phonology
   strand.

**Classification unchanged:** round-2 option C stands — personal tool / open-source substrate,
with the curated-reader overlay as its flagship surface. The business case did not improve; the
product idea did. If a commercial shape is ever attempted, it is a $5/mo Japanese reader on the
progression-ladder pitch, and it fights Migaku with Toucan's corpse given away free next door.

### Post-round-3 scope decisions (user, 2026-08-18)

1. **English↔Japanese only.** Ukrainian↔Japanese dropped (poor translation/model support — the
   round-3 tech finding on missing Uk↔Ja alignment/MT models accepted).
2. **Personal project, accepted.** Option C taken. No app, no product. Building the MCP server
   is the workload ceiling.
3. **OSS-first.** Reuse existing open-source projects/components wherever one exists; build only
   what genuinely has no OSS equivalent. Component research commissioned (NLP substrate stack +
   reader/media ecosystem) — results recorded in `docs/oss-components.md`.

### Vision reconciliation (user statement, 2026-08-18)

User restated the vision as a final check: agent-as-teacher is the center (lessons on request,
"continue where lacking", conversation partner, grading help), served by MCP servers + skills;
an Obsidian vault as the user-facing view (progress, vocab, topic-grouped notes) backed by the
original DB; a media layer (texts/anime/manga) that is transcribed/extracted for reference,
struggle-analysis, and source citations on words/lessons. Compared against v4.1 + oss-components:

**Divergences found (spec gaps, all fixable):**

1. **Obsidian vault missing from spec.** v1 had it as source of truth; audits killed that for
   multi-user sync reasons that no longer apply — but v4.1/oss-components dropped the vault
   entirely instead of demoting it. Reconciliation: vault returns as a **one-way generated view**
   — MCP renders word/progress/lesson notes as markdown into a vault folder; user's own
   topic/grouping notes live alongside and link into them; generated files are never hand-edited
   (regenerated on change). SQLite stays source of truth. No OSS question — it's an exporter.
2. **No lesson entity.** The stated core workflow ("make a lesson on topic X", "continue where
   I'm weak") needs curriculum memory: what was taught, when, from which media, with what
   outcome. Spec has item strengths + events but no lesson/session grouping or topic tags.
   `weakest`/`confusion_pairs` answer "what's weak", nothing answers "what was already taught."
   Add: lesson entity (topic, items, media refs, outcome, agent notes) + `log_lesson` +
   `lesson_history` tools; lessons also render into the vault.
3. **No media-source schema.** User wants transcripts kept + source references on words/lessons
   ("this word came from episode X"). oss-components has the extraction tools (mokuro JSON,
   Jimaku subs, asbplayer) but the spec has no media table and no `media_ref` on items/events —
   a leftover of the commercial copyright posture ("server never sees text"), which a personal
   local tool doesn't need. Add: media table (source, transcript path, per-source
   coverage/difficulty) + media_ref on items, events, lessons. v1's 50-media note pattern
   returns, DB-backed.
4. **Skills demoted too far.** Audit killed "prompt templates" because third-party distribution
   has no delivery channel — true for a product, false for a personal repo where the user
   maintains agent and server together. Skills (teacher role, lesson format, grading rubric,
   conversation-partner mode, media-mining workflow) are a **first-class deliverable** next to
   the MCP server; they are where "agent makes key decisions" lives.
5. **Agent trust model over-hardened.** Rounds 2-3 hardened writes against arbitrary untrusted
   third-party agents. The actual client is ONE agent running user-authored skills. Keep the
   structured-observation shape ({item, task_type, expected, produced} → server derives grade)
   because it's cheap hygiene and keeps grade semantics in one rubric; keep same-day dedupe.
   Drop: lower-trust event classes, nonce-signed grading cards, leases, burst quarantine,
   provenance tombstone UI — paranoia priced for a threat model that no longer exists. Derived
   grades may feed Anki via AnkiConnect `answerCards` (the agent-led rehearsal loop closes).
6. **Overlay silently absent from the vision statement.** Previously "the main selling point";
   v4.1 demoted it to re-exposure/motivation layer; the restated vision doesn't mention it at
   all. Consistent with the demotion — but the substitution engine was build-item #2 in
   oss-components' novel-code list. Flagged: if the vision statement is authoritative, the
   substitution engine drops below lesson/media/vault schema in build priority (possibly to
   "later/maybe").

**Aligned (no change needed):** agent-as-teacher via external agent + MCP context (v4.1 core);
conversation partner (read tools + observation writes); grading via structured observations
(user's "agent grades based on context" = agent judges, server normalizes — same thing);
tracking/rehearsal tools; Anki-owns-scheduling unaffected.

**Net effect on build order:** (1) MCP skeleton + Anki mirror + importers → (2) lesson + media
schema + vault exporter → (3) skills pack (teacher/grader/conversation) → (4) Yomitan known-dict
generator → (5) coverage/difficulty over media transcripts → (6) substitution engine (demoted,
pending user confirmation) → (7) VOICEVOX cache.

## Round 4 — dual search (Obsidian MCP mandate) + media viewer as agent-context channel

### Round 4 input (user corrections to vision reconciliation, 2026-08-18)

1. **Obsidian vault MCP is mandated** (external requirement — non-negotiable). Design response:
   **dual search surface.** The agent gets two parallel search paths: (a) an Obsidian MCP
   searching the vault (generated notes + user's own topic notes, backlinks, user-curated
   groupings), and (b) Katagiri's DB MCP searching SQLite (lexemes, known_set, events, stats).
   Vault stays a generated view + user-note layer; SQLite stays source of truth for facts.
2. **Media overlay/viewer confirmed IN scope** — vision-reconciliation demotion reversed.
   Rationale: Japanese specifically is learned heavily through media consumption. **New primary
   role: agent context channel** — the viewer knows what the user is currently watching/reading
   (anime episode, manga volume/page) and the agent can pull the surrounding transcript/context
   to explain proper translation meaning in situ. Secondary role: known-word marking /
   re-exposure (v4.1 boundary intact — exposures never raise strength). Form factor (full app
   vs plugin on existing tools) explicitly deferred to implementation.
3. Carried from vision reconciliation: single-trusted-agent write model (structured observations
   + dedupe kept; nonces/leases/quarantine dropped), lesson entity, media table, vault exporter,
   skills pack as first-class deliverable.

### Debunker panel round 4 (5 agents + 4 deep-research children): design, tech, pedagogy, agent-interop, workload/feasibility

**Fatal convergence #1 — `answerCards` must never be called (tech + interop, independently
verified against Anki source).** Agent-derived grades written via AnkiConnect `answerCards`
produce revlog rows indistinguishable from human button presses (`type 0-2`, `ease 1-4`,
`time≈0`); FSRS-6 trains on them — including its two same-day-review parameters — so a
systematically laxer agent threshold inflates optimized stability and lengthens intervals for
EVERY card in the preset. Irreversible: `update_memory_state` replays the whole revlog on every
optimize; revlog deletion is unsupported because it cannot sync; `UNDO_LIMIT=30`. Also silently
un-suspends/un-buries and ignores daily limits. Fix: derived grades live only in Katagiri's own
append-only log. Anki channels: `addTags`/`removeTags` for signal (zero scheduling effect,
reversible), `setDueDate` for nudges (writes `type 4`/`ease 0` — excluded from FSRS training,
preserves memory_state). If agent-graded production must ever be scheduled: own note type, own
deck, own preset (FSRS params are per-preset). Bonus: **FSRS stability is not readable via
AnkiConnect at all** — the oss-components threshold rule "stability ≥ X ⇒ known" is
unimplementable as written; use `ivl ≥ 21d`, optionally py-fsrs recompute from
`getReviewsOfCards`. Also: AnkiConnect itself is **archived** (GitHub 2025-11, sourcehut has no
bug tracker, ~9mo stale) — the Anki mirror should read `collection.anki2` directly (revlog/cards
schema = most stable in ecosystem); **AnkiMorphs** (active, 2026-08) already computes per-morph
intervals + known-CSV export and can BE the mirror, leaving only morph→JMdict mapping (~3-6h).

**Fatal convergence #2 — dual search makes the vault a stale second oracle (design + interop +
tech, same finding three ways).** Agent's natural query is prose; vault search matches prose in
one call; DB needs a resolve hop — the model takes the cheaper path and returns last export's
numbers with full confidence. Obsidian search is also substring-only on unsegmented Japanese
(食べる misses 食べた), so vault negatives are meaningless. Structural fix (all three panels
converged): **generated notes contain NO volatile state, ever** — no strength/due/interval/
known-flag; stable content only (senses, readings, citations, lesson prose, links) + frontmatter
`generated: true / generated_at / db_rev`. Wrong answer becomes unavailable rather than
discouraged. Precedence rule goes in the MCP server `instructions` field (host-injected), not
just tool descriptions. Physically partition: `katagiri/generated/**` (exporter-owned,
Sync-excluded, watcher-ignored, hand-edits quarantined to `.conflicts/` never merged) vs
`katagiri/notes/**` (human+agent, Katagiri read-only). Vault under git. User topic notes are
retrieval cues + evidence about learner beliefs, never fact sources — mismatch vs DB = teaching
opportunity, not correction to absorb (round-2 self-confirmation loop otherwise returns via
filesystem hop).

**Fatal convergence #3 — desktop/runtime reality.** Obsidian Local REST API plugin is
`isDesktopOnly` and requires the app running; Claude cloud connectors cannot reach localhost. So
vault search via Obsidian MCP is offline whenever Obsidian is closed. Fix: Katagiri MCP ships its
own always-available markdown search (rg/FTS5 over the files it exports — it wrote them); the
Obsidian MCP satisfies the mandate as the secondary, never load-bearing, path. Cheapest mandate
implementation: the plugin now ships a built-in MCP endpoint (`/mcp/`, v5.1+) — zero code; front
it read-only (cyanheads wrapper `OBSIDIAN_READ_ONLY=true` or allowlist to 4 read tools); use HTTP
:27123 not HTTPS :27124 (live Claude Code self-signed-cert bug). Never expose `command_execute`.
MarkusPfundstein/mcp-obsidian rejected (PyPI stale since 2025-04, speaks pre-5.x PATCH format).

**Fatal convergence #4 — mid-playback explanation is self-defeating (design + pedagogy).**
Synchronous explain-loop = 30-60s interruption per question; L1 explanation text delivered
during L2 audio is the empirically harmful condition; every pause converts a parsing-under-time-
pressure trial (the one strand media uniquely provides) into an offline reading trial. Fix: two
hard modes. **WATCH** — no agent; one-tap/hotkey "didn't get that" mark (timestamp + displayed
line), zero feedback. **REVIEW** — post-episode/chapter, agent builds one lesson from the marks +
transcript context. In-situ answers capped at ≤1 line; anything longer routes to REVIEW. "In
situ" = anchored to a timestamp, not concurrent with playback. Works on non-instrumented devices
too (phone shortcut writes a timestamp).

**Position plumbing — verified per surface (tech + 3 research children):**
- **mpv**: only real live-position source. JSON IPC over Windows named pipe (`python-mpv-jsonipc`
  1.3.0); `time-pos`, `path`, `sub-text`, `sub-start/end`. mpvacious precedent; ~40-line Lua
  pusher POSTs on subtitle change (avoids Windows IPC concurrency pain). IPC protocol = most
  stable surface in the whole stack.
- **mokuro-reader** (canonical = Gnathonic fork, active): fires `mokuro-reader:page.change`
  CustomEvent with title/volume/currentPage on every page turn — unconsumed by anyone; ~10-line
  userscript POSTs it to localhost. Durable fallback: Local Folder sync provider writes
  `volume-data.json` (progress per volume_uuid). Page text read straight from `.mokuro` JSON
  (schema frozen since 0.2.0; never compare the `version` field — it's the package version).
- **asbplayer** (moved to own org; official external API): you run the WS server, asbplayer
  connects; `get-bound-media` + `get-subtitles` (full script, ms timings) exist as of
  v1.19/v1.20; **no playhead read** (seek is write-only; ~30-line upstream PR opportunity,
  maintainer receptive per #1087). Mining events via AnkiConnect-proxy carry exact
  `file (HH:MM:SS.S)` source strings — event-driven anchors for free. Netflix breakage was
  Netflix-side, fixed in v1.19.0 — not a permanent wound.
- Architecture: heartbeats **pushed** to an always-on local daemon (or one SQLite row); MCP
  (spawned per session, stdio) only reads. `media_now()` returns `{media_id, anchor, age_seconds,
  is_live}`; `is_live:false` past 90s — agent must ask, never guess. Heartbeat carries the
  **displayed text**, not just timestamp (Jimaku subs drift vs release; text-keyed lookup,
  timestamp as tiebreak; store learned per-file `sub_delay_ms`). `media_context` returns ID'd
  text lines, mandatory window (default ±3, cap ~40 lines), never raw mokuro JSON. No RAG — an
  episode is 8-15k tokens whole; `sub_lines` table + window query is the entire pipeline. Port
  mpvacious's dedupe (`is_same_event`, 0.05s tolerance) rather than inventing one.

**Pedagogy findings (beyond convergence #4):**
- **Guess-first protocol** — single highest-value rule in the delta: agent's first turn on any
  tap/question elicits the learner's parse ("what do you think it means?"), then confirms/
  corrects. One rule simultaneously creates a retrieval trial (beats contextual inference —
  van den Broek 2022), a self-explanation prompt (g=.55, Bisra 2018), the round-3 micro-probe
  for exposure calibration, and the system's only continuous unassisted-comprehension measure.
  Logged as the settled observation shape verbatim. (Debunked in passing: "lookup harms
  inferencing" is weakly grounded — Mondria; the real loss from explain-first is the skipped
  retrieval.)
- **Nuance claims are unaudited content** — round-3 boundaries protect state, not beliefs. Every
  nuance claim ships as a minimal contrast (actual line vs one altered variant) + an anchor
  (JMdict sense id or ≥2 corpus occurrences) or is written tagged `unverified` with confidence;
  `superseded_by` for later corrections. Lesson note = the audit artifact.
- **Coverage gate as behavior branch**: ≥95% known → in-situ nuance is genuine i+1; 80-95% →
  agent refuses in-situ, pre-teaches top blockers, user then watches uninterrupted; <80% →
  decline + offer lower-coverage item. Prevents "line-by-line translation mislabeled as
  immersion" (agent + user both preference-biased toward it).
- **Mining budget**: ≤~10 proposed adds/episode, frequency/JLPT-filtered, chunk-level with
  furigana+audio; `register_profile` on media rows; anime role-language (役割語) and archaic
  forms receptive-only, never production/conversation targets. known_set admission still
  requires graded retrieval.
- **Held-out probe pool reinstated** (dropped two rounds running): N items per frequency band
  excluded from Anki/overlay/mining AND from the agent-readable surface (enforced by flag at
  query time); 10-min monthly battery. Plus the guess-first `unassisted` pass-rate per coverage
  band over time = the continuous measure. Without these the system reports progress
  monotonically regardless of learning.
- **Lesson entity respec**: `objective` as observable can-do; per-item `task_type`/direction;
  **`unresolved[]`** (deferred failures — this list IS "where lacking"); `next_step` (written at
  close, read at next open); `revisit_after` (topic-level spacing — Anki schedules items, nothing
  else schedules topics). `outcome` = derived view over that lesson's observations, never fresh
  prose. Free prose capped ~500 chars. Query tool: `lessons(topic?, unresolved_only)` returning
  capped summaries + a server-side rollup, so history is never bulk-read.

**Tool surface (interop):** realistic delta count was 26-77 (degradation band starts ~30).
Consolidated to ~15: 11 `katagiri_*` tools (resolve / item / query_items / stats / coverage /
log_observations / add_items / log_lesson / lessons / media_now / media_context), 4 allowlisted
Obsidian read tools, **0 agent-facing Anki tools** (Katagiri calls AnkiConnect internally, if at
all). Never name a tool `search`/`get`/`list` (top MCP collision names); `katagiri_` prefix in
the name itself. Alias/redirect resolution on every read AND write, returning
`{id, canonical_id, redirected}` — stale vault IDs are the dominant honest-mistake error source.
Kept from round-3 hardening (needed even for trusted agent): resolve-first server-issued IDs +
corrective errors, same-day observation collapse, "served but never resolved" counter,
`session_id` on every row + `tombstone_session()` one-liner (undo, not attribution — 1-2h, 1% of
the round-3 provenance UI).

**Skills reality (interop):** SKILL.md is Claude-native; no ChatGPT equivalent loads a repo
folder. State the constraint: Claude-only teacher. Behavior contract (rubric, observation schema,
protocol) lives server-side — MCP `instructions` field + a `katagiri_protocol` resource; skill is
a thin loader. Server exposes `protocol_version`; observations carry `rubric_version`; skew
warning attached to every tool result on mismatch (agents read tool results, not descriptions).

**Vault export mechanics (tech + design + Obsidian research child):**
- **Aggregate notes, not per-lexeme**: one note per lesson/topic/media-source/frequency-band with
  word tables; per-lexeme only for touched items, cap ≤2k files (10k+ files = documented Obsidian
  degradation; 20k = only tested figure).
- Filenames: ASCII `{romaji-slug}-{jmdict_seq}`, Japanese form in `aliases:` frontmatter
  (**NFC/NFD is a hard blocker for Japanese filenames in synced vaults** — live Obsidian 1.13
  regression drops NFD-named files from index). Immutable at creation; renames orphan user links.
- Diff-before-write (mtime alone triggers reindex; touching all N files reindexes all N);
  hash-gated atomic writes (temp + `os.replace`); regenerate once at lesson end via explicit
  `export_vault` tool, not per-observation; never write a file open in an editor tab (2s editor
  debounce clobbers it); quote wikilinks in frontmatter; exclude `workspace.json` from any sync.
- Never put live SQLite in the vault (corruption + second writer); `VACUUM INTO` snapshot if
  browsing wanted.
- **First deliverable: generated `Today.md`** — due count, 3 weakest, last lesson + unresolved
  thread, resume points from last heartbeat, open mark-queue, streak from event log. Answers the
  recurring "no daily entry point" fatal (rounds 1, 2, now 4). Success metric: "is Today.md the
  note I open," not "can the agent search."
- Canonical "did I study today" computed from Katagiri's event log; Anki streak display-only.

**Stack corrections (feasibility + research children):**
- **MCP SDK: oss-components line is wrong.** Spec 2026-07-28 = first breaking revision;
  python-sdk v2.0.0 renamed `FastMCP`→`MCPServer`, snake_cased everything. "SDK v2 with FastMCP
  decorators" is an impossible combination. Pin `mcp>=2,<3`; every tool = plain Python function;
  single ~50-line MCP adapter file. Budget 4-12h/yr spec churn.
- `unidic` pip package = most fragile link (no release since 2021, runtime ~1GB download with
  recurring 403 breakage). Keep full UniDic for accent fields but install once and vendor
  locally; kanjium `accents.txt` vendored (repo silent 26 months — freeze the 3.1MB file).
  Pin `fsrs<7`.
- SQLite FTS5: trigram returns **0 rows for 2-char words** (勉強 — verified live); unicode61
  doesn't segment Japanese. Fix verified: fugashi-tokenized space-joined shadow column under
  unicode61 + trigram for raw substring, route by query length. `PYTHONUTF8=1` everywhere.
- Repo moves for the OSS doc: yomitan → `yomidevs/`; anki-connect → sourcehut (archived on
  GitHub); asbplayer → `asbplayer/`; mokuro-reader → `Gnathonic/`; ttsu officially
  maintenance-only; Lute has no public API (read its SQLite, beware 4 triggers on `words` +
  silent startup migrations); Yomitan custom dicts have **no in-place update** — weekly regen is
  a manual delete+reimport ritual with versioned dict names, don't automate.
- Yomitan-api = native-messaging host launched by the browser, not a standalone HTTP server;
  port default moved once already; every path requires a running browser.

**Effort (feasibility):** full delta'd system 250-500h. Shippable teacher loop **89-170h
(~11-21 spare-time weeks @ ~8h/wk)**. Delta alone ≈30-55 dev-days fork-free; any fork/own-viewer
= +3-5 months + recurring breakage — forbidden. Recurring maintenance budget: 15-35h/yr, largest
line = skills tuning 12-24h/yr (which IS the product). One-off scripts (JMdict import, seeds,
Yomitan dict gen, alignment batches) excluded from maintenance.

### Round 4 synthesis — v4.2

1. **Schema lands whole in one migration** (lexeme, alias, item, event, observation, lesson,
   media — splitting it across build steps was self-inflicted rework). MCP skeleton: plain
   functions + thin v2 adapter, ~15 tools total, fugashi shadow-FTS, `PYTHONUTF8=1`.
2. **Anki mirror without AnkiConnect on the critical path**: read-only `collection.anki2` +
   AnkiMorphs ingest + `ivl≥21d` threshold. `answerCards` banned. Optional flagged writes =
   `addTags`/`setDueDate` only, `exportPackage` before any batch.
3. **Skills pack v0 + lesson memory**, then a mandatory stop-gate: **two weeks of actual study
   before more code** (round-0's 60KB-prose/0-byte-reviews finding, enforced this time).
   Guess-first, coverage gate, mining budget, nuance-anchoring all live here.
4. **Context channel**: mpv Lua pusher + mokuro userscript/`volume-data.json` poller + asbplayer
   WS transcripts; heartbeat daemon; `media_now`/`media_context`; WATCH/REVIEW modes. Manga/EPUB
   manual anchors ("vol 3 p42") accepted from day one — typing the anchor is 80% of the value at
   2% of the cost.
5. **Vault**: aggregate exporter (Today.md first), plugin's built-in MCP read-only-fronted,
   Katagiri's own markdown search as the always-available path. Mandate satisfied at ~1-3h + the
   exporter.
6. **Visible feedback**: Yomitan known-dict (weekly, versioned names) + `coverage(media_id)`.
7. **Deferred, not cut** (user's explicit correction stands: overlay/viewer wanted): progressive
   substitution engine — post-loop decision with usage data; its prerequisite (curated aligned
   text + known_set) is built by steps 1-6 anyway. VOICEVOX deferred (Yomitan supplies word
   audio meanwhile). ASR, jpdb/WK importers, difficulty modeling: post-loop.

**Classification unchanged**: personal tool, option C. Round 4 changed the *how* everywhere but
the *what* survived: agent-teacher on an MCP substrate, dual search (with the vault demoted to
prose-only), media context channel (with playback and explanation separated), and a build order
that reaches a usable teacher loop in ~11-21 spare-time weeks.

---

## Round 5 — dev-plan review panel (2026-08-19)

Subject: dev-plan.md v1 (user-approved draft). Seven-role subagent panel, each reviewing
independently against dev-plan + decisions-ledger + oss-components + Round 4: **teacher**,
**developer**, **tech lead**, **product manager**, **production manager**, **security**,
**learner-advocate**. ~50 raw findings, consolidated below into 17 clusters. Verdicts in one
line each:

- Teacher: sequencing coherent, but ~100h of infrastructure before any teaching; i+1 modeled
  on vocabulary only; held-out measurement absent.
- Developer: component list, not an executable breakdown — scaffolding layer missing, hardest
  piece (morph→lexeme) hidden, verification gate not implementable as written.
- Tech lead: phase order sound; defects are intra-phase — unstable tool contract, schema claim
  contradicted, non-cumulative verification, no durability plan for the event log.
- Product: A–C = 6–14 weeks delivering nothing felt; pull value into Phase A, trim C.
- Production manager: weak as a delivery instrument — one mechanically checkable gate,
  top-down estimates, no slip policy, the sole gate self-audited.
- Security: no security workstream at all — untrusted media text, five unauthenticated local
  listeners, live collection.anki2 reads, secrets, backups each unaddressed.
- Learner-advocate: engineered for the system, not the person — every gate measures software,
  none measures learning.

### Consolidated findings and dispositions (all → dev-plan v1.1)

1. **Study starts too late / stop-gate brittle** (6 of 7 reviewers, HIGH) — study + event
   logging from Phase A day one; entry precondition for Phases B–D (no phase starts in a week
   with <4 logged study days); D6 restated as **14 study days within an 18-day window**,
   study day = concrete event count (≥10 min or ≥1 artifact), declared illness/travel pause,
   `stop_gate_status` MCP tool computes PASS/FAIL from the event log, re-plan trigger if
   unmet twice. ACCEPTED.
2. **Value pulled forward** (product, learner, prod-mgr HIGH) — Yomitan known-dict moves
   D1→A8; `log_error` thin slice early; minimal sensei letter as soon as event log exists;
   thin vertical slice (A1 + minimal A2 + A6, hand-seeded known_set) answering a real query
   by week 2–3. ACCEPTED.
3. **Missing Phase A0** (developer HIGH; teacher + learner HIGH on canary) — A0a project
   skeleton (pyproject/uv, .gitignore vs ~1GB vendored assets, config outside repo,
   stderr-only logging — stdout corrupts MCP stdio — Windows launch); A0b **canary set sealed
   before first study day** (200 graded sentences, `sealed: true`, validator-enforced); A0c
   zero-code start: skills pack v0 authored now, L1 profile conversation, daily study logging
   begins. ACCEPTED.
4. **Schema integrity** (tech lead, developer HIGH) — all DDL (FTS, JMdict, mirror tables)
   into the A1 migration; explicit derived-vs-source-of-truth classification; minimal
   migration runner (`PRAGMA user_version`, numbered scripts, backup-before-migrate) from day
   one. ACCEPTED.
5. **Anki read safety** (security, tech lead, developer) — never open live collection.anki2:
   snapshot copy (incl. -wal/-journal) → open `mode=ro&immutable=1` → `integrity_check`;
   detect-Anki-running precondition; fail loud on unknown schema version; AnkiMorphs CSV as
   degraded fallback; new risk row. ACCEPTED.
6. **Tool contract stability** (tech lead HIGH; developer, product) — checked-in tool
   registry (name/args/output/stability); C1 folded into A6 (Phase C = markdown search only);
   unimplemented tools raise, never return plausible stubs; post-freeze changes additive.
   ACCEPTED.
7. **Verification protocol repair** (developer, tech lead, prod-mgr HIGH) — frozen fixture
   set (mini collection.anki2, mini vault, JMdict subset); assertions on structured fields,
   free prose advisory; **cumulative** suite (phase N runs A..N); blocker/backlog disposition
   rule fixed before each run; budget 20–25% not 10–15%; max two reruns per phase.
   ACCEPTED.
8. **Event-log durability** (tech lead HIGH, security MED) — Phase A backup task
   (`VACUUM INTO` snapshots + vault copy + one rehearsed restore); restore drill inside
   A-verify; append-only enforced by `BEFORE UPDATE/DELETE RAISE(ABORT)` triggers.
   ACCEPTED. (Per-event hash chain: DEFERRED — overkill for a personal tool today.)
9. **Security workstream** (security HIGH×4) — (a) hardening task: Katagiri MCP stdio-only,
   third-party ports verified 127.0.0.1 + firewall deny, mokuro bridge shared secret +
   Origin check; (b) **Obsidian proxied**: Katagiri holds the REST token, exposes GET-shaped
   tools only, plugin's own MCP endpoint never registered with the agent (amends D-11);
   B-verify attempts the direct-HTTP bypass; (c) **prompt-injection defenses**: untrusted-data
   envelope on all media-derived text, write tools refuse to fire on media content without
   echo-back confirmation, adversarial subtitle scenario in E-verify; (d) secrets in
   %LOCALAPPDATA%, pre-commit secret scan, tokens never in outputs/logs; (e) confined write
   roots — exporter writes only `.derived/` files bearing a generated-file header,
   screenshots get server-generated names. ACCEPTED.
10. **Pedagogy content gaps** (teacher HIGH×2, MED×3) — curriculum grammar DAG imported into
    `item` table and `find_i_plus_one` gated on grammar reachability, not vocabulary alone;
    D3 names `log_observations` (unassisted flag, coverage band, rubric_version) +
    `log_lesson`/`lessons()`; lesson memory spelled out (`unresolved[]`, `next_step`,
    `revisit_after`) and surfaced in Today.md; shadowing logged as events from Phase A.
    ACCEPTED.
11. **Recurring sync job** (developer HIGH) — idempotent incremental Anki→event-log sync
    with Windows scheduling; D6 and streaks depend on it existing. ACCEPTED.
12. **Morph→lexeme normalizer surfaced** (developer HIGH) — A4 split into reader / ingest /
    normalizer; normalizer gets an accuracy target vs ~200 hand-labelled morphs. ACCEPTED.
13. **Delivery process** (prod-mgr) — bottom-up estimates per bead before creation; actual
    hours logged; re-baseline after Phase A; tasks >8h split; task-level dependency DAG (not
    phase chaining); fixture known_set unblocks downstream while A4 is in flight; weekly
    15-minute status line appended to dev-plan; must/should/could tags; at 1.5× phase
    estimate all "could" items cut + re-estimate. ACCEPTED.
14. **Exporter architecture** (tech lead, product MED) — B1 built as section registry (each
    phase registers a renderer); Phase-B Today.md defined strictly from data existing at
    Phase B. ACCEPTED.
15. **Phase E adjustments** (product, learner, prod-mgr, tech lead) — channel order (E1/E2/E3)
    decided by measured consumption mix during the D6 window; E4 ships immediately after the
    first channel; **write-only mpv seek logger exempted from the stop-gate** (capture is
    worthless retroactively); Yomitan regen drift-triggered (known_set Δ>150) with printed
    checklist, regens logged; asbplayer anchor derived from the mining/copy event, manual
    anchor use counted so F-05 fires on data. ACCEPTED.
16. **Learner metrics on gates** (learner HIGH, product MED) — each phase gate adds one
    learner metric read from the event log (reviews/day trend, days-studied/14, adoption of
    that phase's tools); a phase can fail on it with the subagent pass green. ACCEPTED.
17. **Session UX + honest timeline** (learner MED) — `start_session` returns exactly one
    prescribed action; tired-mode minimum session (reviews + one mined word) counts toward
    the gate; standing rule: study first (~20–30 min) before any build session, no building
    on a zero-review day; timeline recomputed as build-only hours. ACCEPTED.

**Net effect**: no change to the *what* or the user-fixed phase order; Phase A grows (A0,
splits, security, backup), Phase C shrinks to markdown search, D1 moves into A, verification
becomes fixture-based and cumulative, and the gate system now measures the learner as well as
the software. Plan revised to v1.1; beads to be created from the revised task list with
task-level dependencies.

---

## 006 TG0/TG1 — Phase-0 teaching rules and entry-gate governance (2026-08-20)

Session date: 2026-08-20. Filed per spec/006-teaching-method T008 ("governance first, code
second"): the ledger rows and constitution bump below are committed *before* T009 (the
gate-criteria code) starts, per FR-012.

**Context**: the learner's DB was reset 2026-08-20 and no first lesson has been logged yet.
006's entire risk is designing a teaching method calibrated on evidence that does not exist.
TG0 (prose + two data constants: `VAULT_SNAPSHOT_EXTENSIONS`, the backup task) is deliberately
split from every later taskgroup so it can ship *before* any of that evidence, and the entry
gate (TG1) is the structural device that stops US2–US8 (dose contract, input strand, audio
anchors, curriculum refs, assessment cadence, kanji policy, worksheet loop) from being built
on a foundation of zero real sessions.

1. **Phase-0 teaching rules (KANA mode, coverage unit, dictation slug, staged kana gates)**
   (ledger D-32). KANA is modeled as a peer session mode rather than a variant of FULL because
   its constraints are categorical, not parametric: zero free conversation, a single fixed daily
   artifact (mora-count dictation), and a suspended feature set (kanji-rival rule,
   kanji-component hints, WATCH, mining capped at ≤3 kana-only items) — trying to express that
   as FULL-with-flags would hide the suspensions instead of stating them. Coverage unit =
   unread kana rather than words, because word-based coverage has no meaning before the writing
   system itself is legible. Day qualification rides the **existing** `lesson_close` artifact
   event type under a reserved topic slug, `phase0-kana-dictation`, instead of a new event type
   or table: TG0 is scoped as prose/data-only (no stop-gate code change), so the slug is what
   lets TG1's gate code count real dictation days mechanically later without the code having to
   guess a naming convention from prose. The kana gate is staged (hiragana ≥95% both directions
   unlocks drill tooling; katakana is a second checkpoint, never a wall) so that katakana, which
   the learner will need less urgently, cannot stall hiragana-level progress. ACCEPTED —
   plan v3 TG0 (teacher R2 F2/Q3); source spec.md FR-001…FR-005/FR-009, research.md TG0 notes.

2. **006 entry gate: ≥10 study days / ≥6 scored observation / ≥3 dictation artifact, additive
   to D-19** (ledger D-33). A plain day count is gameable by exactly the sessions that teach the
   least — ten TIRED-mode or arbitrary days would pass a count-only gate while producing none of
   the scored-observation or dictation evidence the post-gate designs (dose caps, audio anchors,
   curriculum reachability) are calibrated against. The three-part criterion is therefore about
   evidence *quality*: a scored observation confirms the agent is actually assessing the learner
   (not just logging attendance), and a dictation artifact confirms Phase-0 KANA work is real and
   recorded, not assumed. The gate is explicitly **additive** to the D-19 stop-gate mechanics (14
   study days in an 18-day window, plus the canary probe battery) rather than a replacement,
   because D-19 already blocks Phase E code and a 006-specific gate that quietly superseded it
   would loosen that blocking without anyone deciding to. Both gates must independently pass;
   006's criteria never lower or satisfy D-19's, and D-19's count never satisfies 006's evidence
   requirement. Mechanical evaluation only (no self-assessment), surfaced as additive keys on the
   existing `stop_gate_status` tool so no new ToolSpec is needed, consistent with constitution
   principle V (verification is assertion-driven, never self-assessed) and principle VII
   (contract changes are additive after freeze). ACCEPTED — plan v3 §Entry gate (architect
   MAJOR 4 + teacher F2 re-cut, architect F5); source spec.md §Entry Gate, FR-010…FR-013,
   research.md §Entry gate.

**Net effect**: no code changes from this filing — TG0's prose/data already shipped (merged
2026-08-20: prose 810cb6f, data 60ee5fc, ops 5f9d0a0) and this entry documents the reasoning
behind it plus the entry-gate criteria that TG1's code (T009, next) will implement against
`stop_gate_status`. Constitution principle IV amended to state the entry gate; version bumped
1.0.0 → 1.1.0 (MINOR: new gate criteria layered onto an existing principle, no principle
redefined or removed).

## Gate waivers — pre-study build-out (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-35, constitution bumped 1.2.0 → 1.3.0.

**Context**: every usage gate in the plan (Phase B's Today.md adoption metric, the 006 entry
gate's ≥10/≥6/≥3 study-day criteria, T011 "live the gate") was written on the assumption that
the learner studies *while* the system is built, so real usage evidence accumulates alongside
the code. The user's actual constraint is the inverse: a tight schedule where learning cannot
start until the teaching method is finished — the agent needs a complete program to follow
from day one. Under that ordering the gates are not merely unmet, they are unsatisfiable:
each blocks the very work that would make satisfying it worthwhile. The user stated the
blocks were "conflicting and impossible to follow" in this setting and explicitly approved
gate removal and continued implementation.

1. **Phase B learner metric waived** (D-35a). kata-bvf's technical checks went green
   2026-08-19 (tests/test_bverify.py 10 passed, incl. the live bypass 401/200 check and the
   token-canary boundary proof; suite 795 green at the time). Only the user-side ≥5-of-7-days
   Today.md adoption metric held the bead open. Precedent: D-30 (Phase C entry gate waived by
   user 2026-08-19) and D-31 (Phase C closed with its learner metric recorded as NOT met under
   the D-30 waiver context). Same shape here: the code is done and verified; the usage
   evidence is deferred, not the engineering. Phase B closes; kata-bvf and kata-ph-b close in
   beads with the waiver as the recorded reason.

2. **006 entry gate's blocking effect waived** (D-35b). D-33's criteria and T009/T010's
   mechanical evaluation are *kept*: `stop_gate_status` continues to compute and surface the
   three counts as informational keys, so the moment real study starts the same instrument
   reports honestly against the same bar. What is waived is solely the gate's blocking effect
   on TG2–TG8 — the dose contract, input strand, audio anchors, curriculum refs, cadence,
   worksheet loop now proceed pre-study. This is deliberately narrower than deleting the gate:
   the constitution amendment records the waiver on the addendum rather than removing it.

3. **What is NOT waived**. The D6 stop-gate for Phase E (D-19: 14-in-18 study days + probe
   battery, evaluated by `stop_gate_status`) remains fully binding — 004 media overlay is
   separately deferred to dead-last by user instruction and its gate is untouched. The
   ~20–30 min study-first norm in Principle IV stays as stated intent for once learning
   begins; it was never a build blocker enforced in code.

ACCEPTED — user override 2026-08-21; source D-35, constitution Sync Impact Report 1.3.0.

## 006 TG2 — dose contract-diff justification (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-36, per spec.md FR-025's governance-first rule and
`tool_registry.py`'s additive-only contract (D-24): a contract-touching taskgroup gets its
ledger row and reasoning committed *before* the code task that implements it — here, T012 lands
before T013–T017. This filing sits inside the D-35 waiver context: D-35b lifted the 006 entry
gate's *blocking effect* on TG2–TG8 so implementation can proceed pre-study, but it left FR-025's
governance-first requirement untouched — the waiver relaxes when contract work may start, not
whether it still needs a diff justification first. The two rules are independent: one is about
evidence timing, the other is about review discipline, and only the first was ever in question.

**Why additive-on-existing-tools beats new tools here.** TG2's job is to make the dose — 20–30
min core/day, ≤8 new words/day, ≤2 new grammar/week — a property of the server rather than a
prose promise the learner self-counts (research.md §Post-gate decisions: "prose caps are
self-counted and the count is the first thing a good session breaks"). That job needs exactly
two things read out to a caller: how much room is left, and a hard stop when a specific cap is
crossed. Both already have a natural home. `start_session` is the single entry point every
frontend already calls to learn what happens next (spec.md §Governing principles: "`start_session`
/`prescribe()` owns what happens next"), so room-left is an additive field on the payload it
already returns, not a second call a caller could forget to make. `add_vocab` is the one write
path that adds a new word, so the refusal that stops the ninth word belongs at the point of the
write, not in a side-channel a caller could bypass by calling `add_vocab` directly and skipping a
check. Standing up new tools for either — say, a `get_caps` or `check_cap` — would create exactly
the second-planner problem FR-014 explicitly rules out for topic selection: a second place that
decides something, callable out of order, driftable from the first. Reusing the two existing
touchpoints means the cap cannot be circumvented by a caller that only knows the old contract,
because the old contract still works exactly as before and the new behavior rides along.

**The additive argument, spelled out per FR-016/D-24:**
- `caps{new_words_left, grammar_left, listening_reps_left}` is a **new optional key** appended to
  an existing payload. No existing key is removed, renamed, or reinterpreted; a caller reading
  only the old keys sees no change. Because it is new, no caller can have depended on its
  *absence* in a way this breaks — the additive-only rule's actual guarantee (old callers keep
  working) holds by construction.
- `add_vocab`'s cap refusal is a **new outcome on an existing call**, not a new argument or a new
  required field. It reuses the module's existing refusal shape (structured error naming what was
  refused and why — the same shape `tool_registry.py`'s stub/refusal conventions already use
  elsewhere), so this is not a new error dialect a caller has to learn; it is the same shape
  firing for a new reason. The call's success path, arguments, and successful-add payload are all
  unchanged.
- Neither change touches an argument's optionality (nothing goes optional→required), and neither
  changes what an existing key *means* — `new_words_left` etc. are net-new names, not repurposed
  ones. Net ToolSpec count: **zero** — both changes land inside the two `ToolSpec` entries that
  already exist for `start_session` and `add_vocab`.

**Why each cut tool is cut for cause, not deferred:**
- `next_topic`, `plan_revision`, `mark_topic_progress` — these would each be a second place that
  decides what the learner does next, alongside `prescribe()`. Spec.md's single-prescriber
  property (§Governing principles, FR-014) is stated as the thing that keeps `start_session` from
  becoming a dashboard; research.md's own rationale for cutting them is direct: "five tools that
  each decide something are five prescribers." Topic selection instead becomes one more rung
  *inside* `prescribe()`'s existing ladder, reading curriculum reachability and sitting above the
  generic "open a lesson" fallback (FR-014) — the decision moves into the one function that
  already owns deciding, rather than gaining siblings.
- `run_drill`, `check_answer` — the post-gate design's drill and grading flow rides the session
  tools that already exist (`start_session`'s prescribed action, `log_observations`,
  `log_lesson`) rather than a parallel drill-and-check API. Standing these up would duplicate
  machinery the session tools already provide (an action to perform, a place to record what
  happened) behind a second, narrower vocabulary — the same "second planner" problem in miniature,
  scoped to one exercise instead of one session.
- All five are stated as **CUT** in spec.md FR-014 itself, not left open with a revisit trigger
  the way `docs/decisions-ledger.md`'s "Deferred options" table works for genuinely-later work —
  there is no condition under which they come back; the ladder-rung and existing-tool designs are
  the permanent replacement, not a placeholder for them.

**Net effect**: no code changes from this filing. This entry and ledger row D-36 are the
governance step FR-025 requires before T013 (the `prescribe()` topic rung + caps block) and the
later T014–T017 tasks touch `start_session`/`add_vocab`; `tests/test_mcp_tools.py`'s congruence
check stays the enforcement backstop that the additive claims above are not just argued but true
once the code lands.

## 006 TG3 — input-strand logging justification (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-37, per spec.md FR-025's governance-first rule: T018
(this filing) lands before T019–T021 (the code that writes and reads listening-block entries).
FR-017 sets the requirement directly: input logging must write into the same `study_session`
event series with a deterministic dedupe key in its own namespace — no second unread channel, no
double-count against `events.import_study_log`'s `study:<ts>` keys — and the metric is
narrow-listening reps of known audio, not raw minutes.

**Why the existing `study_session` series, not a second channel.** `events.py:428-505`
(`import_study_log`) already owns one event series for study activity: every record it appends
carries `type=STUDY_LOG_TYPE` (`"study_session"`), and every downstream reader of study
history — day-qualification counts, `stop_gate_status`'s D-19/D-33 tallies, any future dashboard —
reads that one series. Standing up a second `type` (e.g. `"listening_block"`) for input-strand
data would mean every one of those readers now has two places to check, and a reader that only
knows the old contract silently under-counts a day that had real input work but no import-log
line. Appending to `study_session` means the existing readers already see the new writes without
being touched — the additive-only property research.md's rationale (`events.import_study_log`
for the existing key shape) leans on for the whole 006 post-gate strand.

**The dedupe-key namespace: `listen:<normalised ts>`.** The importer's convention
(`events.py:481-482`) is `stamp = normalize_stamp(raw_ts); dedupe_key = f"study:{stamp}"` —
deterministic per logical record (the same input timestamp always normalizes to the same stamp
and therefore the same key), checked against `event.dedupe_key` by exact string match
(`events.py:484-486`) before the row is appended, so a re-run only ever adds genuinely-new lines.
The input strand reuses the same `normalize_stamp` shape but a different literal prefix —
`listen:<normalised ts>` — keyed off the listening block's own timestamp (block start, or
whatever timestamp T019/T020 treat as that block's identity) rather than the study-log line's
`ts`. Determinism carries over unchanged: the same logical listening block, logged twice, collapses
to one row, same as the importer's own idempotency guarantee.

**Collision analysis against `import_study_log` re-run over the same day.** The check at
`event.dedupe_key = ?` (events.py:485) is an exact-string lookup, not a prefix-insensitive or
per-day scan — so collision is possible only if a `listen:X` value is byte-identical to a
`study:X` value for some `X`, which cannot happen: the two prefixes differ (`"listen:"` vs.
`"study:"`) before the shared `<normalised ts>` suffix even enters the comparison, so no
listening-block key can ever equal an importer key regardless of what timestamp either carries.
Re-running `import_study_log` over a JSONL file that spans a day already carrying listening-block
rows therefore imports exactly the `study_session` lines it always would have — new `study:` keys
land, already-seen `study:` keys dedupe against themselves — and touches zero `listen:` rows,
because the importer only ever constructs `study:`-prefixed keys and only ever selects on the key
it just built. There is no shared counter, index, or lookup between the two namespaces for a
re-run to disturb.

**Reps, not minutes — and why that changes no arithmetic.** The listening-block payload carries a
reps count (e.g. "10 replays of one 40-second Irodori dialogue," research.md §Post-gate decisions)
against a known audio-anchored item; it does not carry a `minutes` field, and T019–T021 must not
synthesize one (no estimated-minutes-from-reps conversion, no zero-fill of an absent field) —
absence of a minutes claim is the whole point, not an accident to paper over. Every
day-qualification and dose computation that reads `study_session` for minutes (the D6 stop-gate's
14-in-18 count under D-19, the D-33 entry-gate criteria surfaced on `stop_gate_status`, TG2's
`caps` block under D-36) reads the `minutes` key specifically; a listening-block row that never
populates `minutes` is invisible to that arithmetic by construction, not filtered out by a special
case. The row still counts wherever a reader means "did a `study_session` event happen this day"
(if any such reader exists) but contributes zero minutes and zero new-word/grammar cap consumption
to anything that sums `minutes` or the caps fields — the reps number lives in the payload for
input-strand-specific readers (future listening-volume tracking, F-10's channel-mix decision) to
pick up on their own terms, not to be silently coerced into the minutes arithmetic other strands
already own.

**ToolSpec expectation.** No new tool is implied by any of the above: the write path is
`append_event` against the existing `study_session` series (already used by
`import_study_log`), and read-side exposure (if any is needed for T021) is expected to ride an
existing tool's payload additively, the same pattern D-36 used for `start_session`'s `caps`
block. If T021 finds no existing tool can carry the reps field additively without redefining an
existing key's meaning, the correct move is to stop and file a ledger row before inventing a new
ToolSpec — not to add one under time pressure. Zero new ToolSpecs are expected from T019–T021.

**Net effect**: no code changes from this filing. This entry and ledger row D-37 are the
governance step FR-025 requires before T019–T021 (the listening-block writer and any read-side
exposure) land.

## 006 TG4 — migration 0002 constitution exception (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-38, per spec.md FR-018 ("The migration requires a
stated constitution exception plus a ledger row, filed first") and FR-025's governance-first
rule: this entry and D-38 land before T023 (the migration file itself) is written. T023 does not
exist yet — this is the exception that lets it exist.

**Why the whole-schema-in-one-migration rule cannot survive this feature.** D-12 ("whole schema in
one migration — lexeme, alias, item, event, observation, lesson, media") and D-27 ("all DDL in the
A1 migration") were decided in round 4/round 5, before any database existed. At that point "one
migration" was a coherent, satisfiable rule: the schema had no history yet, so writing it once, in
one file, was strictly better than staging it across several — no runner had to reconcile
versions, no live data existed to protect. That precondition no longer holds. Phases A through D
shipped, and the event log — Principle III, "the single non-reconstructible asset" — now holds
real (if still agent/fixture-only per the Phase D coverage note) history stamped at whatever
`user_version` migration 0001 left it at. FR-018 needs an audio-anchor reference (source +
timestamp; Irodori MP3 refs) added to `item`/`sentence`, plus a text-only-not-for-A0-production
marker. There is no way to add a column to a table that already exists in an already-migrated
database except a **new** migration — going back and rewriting migration 0001 in place is not an
option: `db.py`'s own runner (`discover_migrations`, `db.py:178-214`) discovers and orders
migration files by their `NNNN_name.sql` stamp and refuses duplicate version numbers; rewriting
0001 after it has already run against someone's database would not touch that database's
recorded `user_version` at all, and the runner's `migrate()` explicitly refuses to let an older
build's migration set act on a database a newer build already stamped past it (`db.py:284-293`).
The "one migration" instruction, taken literally, is now unsatisfiable without either destroying
existing history or leaving the runner's own consistency guarantees. Something has to give: either
the rule, or the guarantees it was meant to protect. The exception gives ground on the rule's
letter while holding its intent.

**Why additive-only + the runner's existing guarantees preserve the rule's intent.** D-12/D-27
were never really about the number "one" — reading them together with Principle III's derived vs.
source-of-truth classification, the actual concern is that schema history stay append-only and
restorable, the same property the event log itself is held to. Migration 0002 preserves that
property by construction, not by promise:
- **One transaction per migration, still.** `_apply` (`db.py:317-357`) wraps migration 0002's
  script in exactly one `BEGIN IMMEDIATE ... COMMIT` the same as migration 0001 did; a mid-script
  failure rolls back to the pre-0002 state, reported with the exact pre-migration version. Two
  migration files does not mean two half-applied states are now possible — each file is still
  atomic on its own.
- **The runner alone owns the version stamp.** `_validate_script` (`db.py:217-254`) rejects any
  migration script that itself sets or reads `user_version`, and rejects `BEGIN`/`COMMIT`/`VACUUM`/
  transaction-control statements in the script text — migration 0002's `.sql` file gets the same
  validation migration 0001's did. The scope fence below adds "no `user_version` statement" as an
  explicit, redundant-on-purpose restatement of a check the runner already enforces mechanically.
- **Backup-before-migrate is unconditional.** `migrate()` (`db.py:273-314`) snapshots via
  `VACUUM INTO` before applying anything whenever the database is non-empty (`db.py:301-302`,
  `_backup` at `db.py:381-413`) — a real learner's database, non-empty by definition, always gets
  a pre-0002 snapshot before the new columns land. Restorability — the property Principle III's
  "rehearsed restore drill" cares about — is not weakened by there being two migration files
  instead of one; the snapshot happens before *each* pending migration is applied, not once at
  the start of time.
- **Additive-only means nothing already-appended is disturbed.** The scope fence — new nullable
  columns on `item`/`sentence` only; no rename, no drop, no derived-table rebuild — means existing
  rows, existing column meanings, and every query written against the pre-0002 schema keep working
  unchanged. This is the same append-only spirit the event log's `BEFORE UPDATE`/`BEFORE DELETE`
  triggers enforce in-schema: migration 0002 extends the schema forward, it does not rewrite what
  came before.

**The scope fence, explicitly.** The exception covers migration 0002 and only migration 0002:
additive columns for the audio-anchor reference (source + timestamp, Irodori MP3 refs) and a
text-only-not-for-A0-production marker on `item`/`sentence`. It does not cover, now or by
precedent: renaming a column or table, dropping a column or table, or rebuilding any derived table
(FTS, JMdict mirror, Anki mirror — D-27's derived classification already treats those as
drop-and-rebuild-never-migrated, untouched by this row). A future migration proposing any of those
still needs its own argued exception and its own ledger row, filed before its code task, exactly as
this one was. D-12/D-27's text is not edited to read differently for the general case — the
constitution amendment adds a scoped paragraph recording this exception without touching the
original rule's wording, so the rule stands, unweakened, for every migration that is not 0002.

**Net effect**: no code changes from this filing (migration 0002 / T023 is a separate task, gated
open by this entry and ledger row D-38). This entry, D-38, and the constitution's Technology
Constraints amendment (1.3.0 → 1.4.0) are the governance step FR-018 and FR-025 require before
T023 is written.

## 006 TG5 — curriculum attributes + construction-state derivation (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-39 and D-40, per spec.md FR-025's governance-first
rule: T027 (this filing) is a gate that lands before T028/T029 (the code that actually parses the
new curriculum tags and derives construction state) are written. This is a ledger/audit-log-only
task — no code file is touched here; T028/T029 implement what this entry authorizes.

**D-39 — why curriculum node attributes are additive metadata, not a new table.** FR-019 requires
`curriculum.md` node attributes to be extended to carry a JF can-do id, an Irodori lesson ref, and
a Tae Kim section ref, "with removal/orphan semantics defined in the same change. No new table, no
new tool." The existing importer already has exactly the mechanism this needs, documented at
`intelligence.py:104-107`: `item` and `item_edge` are **source-of-truth** tables, so the import is
additive — item rows upsert with `COALESCE(existing, new)` so a curated field is never clobbered
by a stub, edges insert `ON CONFLICT DO NOTHING`, and "`item` and `item_edge` are source-of-truth
tables, so this import is additive: an edge deleted from `curriculum.md` is *reported* as an orphan
and left alone. Drop-and-rebuild is the derived-table rule and applying it here would delete the
learner's hand-made edges along with the file's." The three new attribute tags are columns/fields
on the same `item` node rows that edges already live beside — they carry exactly the same
provenance risk an edge does (a human can delete the line in `curriculum.md` without meaning to
delete the underlying fact), so they get exactly the same treatment: additive on import, and if a
tag disappears from a re-parsed `curriculum.md`, the importer reports it as an **orphan** (logged,
surfaced to the learner/operator) and the row's existing tag value is **left alone**, never
deleted. No new table is needed because the tags are attributes of a node that already has a row;
no new tool is needed because the existing curriculum-import path is what walks `curriculum.md` in
the first place. This is not a new doctrine — it is the same doctrine at `intelligence.py:95-114`
applied to a second kind of node-level fact (an attribute tag) instead of the first kind
(an edge), for the same reason: source-of-truth data must never be destroyed by an edit to the
document that merely *describes* it.

**D-40 — why construction state is derived on read, never stored.** FR-021 separates two different
kinds of progress: vocabulary keeps Anki's SRS decay (Anki owns scheduling, untouched by this
row), but grammar **constructions** must be "an accuracy-over-attempts trajectory derived from
existing observation events — no new table, no terminal state, U-shaped dips logged and never
penalised. Reachability gates output tasks only." A construction is not an item with a due date; it
is a claim about a learner's trajectory over time, and that trajectory already exists, in full, as
the sequence of observation events already logged for every attempt at that construction. Storing
a second, separately-updated "mastery" or "progress" value for the same construction would create
exactly the failure mode `intelligence.py:104-107`'s additive/no-derived-rebuild split was written
to prevent for edges: two places claiming to know the same fact, with no mechanical guarantee they
agree, and no way to tell which one is right when they drift. Deriving accuracy-over-attempts on
read from the event log keeps the event log the **only** ledger of what happened — the same
principle the event log's append-only triggers already enforce structurally (Principle III, cited
in D-38). Concretely: (a) no new table — a construction's state is a query over existing
observation-event rows, not a row of its own; (b) no terminal state — there is no "mastered" or
"complete" value ever written anywhere, because a stored terminal state is exactly the kind of
self-assessment shortcut the project's doctrine bans elsewhere (learner-graded mastery is never
trusted over mechanically observed behavior); (c) a U-shaped dip — a temporary drop in accuracy
that is a well-documented interlanguage phenomenon, not regression — is visible in the derived
trajectory and may be logged/surfaced, but **never** lowers or penalises any gate, because
penalising a dip would penalise the exact process (restructuring an interim grammar) that produces
real acquisition; and (d) reachability — the existing i+1 grammar-DAG gate from D-28, walked over
`prereq` edges only — keeps gating **output/production** tasks only, exactly as it already does,
and this row does not extend it to comprehension/input tasks: an unreached construction can still
be heard and read, it simply cannot yet be asked for in a production drill.

**Net effect**: no code changes from this filing. This entry and ledger rows D-39/D-40 are the
governance step FR-025 requires before T028/T029 (the curriculum-attribute parser and the
construction-state read-path) are written.

## 006 TG7 — worksheet loop reuse justification (2026-08-21)

Session date: 2026-08-21. Filed as ledger D-41, per spec.md FR-025's governance-first rule: T037
(this filing) is the gate that lands before T038 (the code that renders the worksheet into
`.derived/`) is written. This is a ledger/audit-log-only task — no code file is touched here;
T038 implements what this entry authorizes.

**Why 100% reuse, not a new writer.** FR-024 states the requirement plainly: "The worksheet loop
MUST write through the existing `today_export` `.derived/` pattern (`generated: true` frontmatter
check, confined path, server-named file) and read back through the existing GET-only vault proxy.
The agent never writes vaults. No new MCP tool surface." The `today_export` module already
carries the exact doctrine a worksheet writer would otherwise have to reinvent, documented at its
own module header (today_export.py:14-21): "Writes are confined to `<vault>/.derived/` and refuse
to overwrite any file whose frontmatter does not say `generated: true`." Two mechanisms make that
true today, and the worksheet loop calls them rather than restating them: `derived_target`
(today_export.py:1001-1028) resolves any requested name against the resolved `.derived` root and
refuses anything whose resolved path is not a strict descendant of that root — checked on the
resolved path, so `..` segments, absolute paths and drive-relative paths all fail the same way,
regardless of what the worksheet's requested name looked like before resolution; and
`is_generated_note` (today_export.py:984-988, aliased from `sensei_letter.is_generated_letter` so
the frontmatter-parsing rule exists in exactly one place, not two subtly different ones) gates
every overwrite on the target file's own frontmatter already declaring `generated: true` — an
unreadable file is refused exactly as firmly as a hand-authored one, because "I could not check"
is never treated as permission to overwrite. Layered on top, the worksheet's filename is chosen by
the server, never accepted from a caller argument, which is what keeps the confinement check
meaningful rather than a check a caller could route around by naming its own path. None of this is
new code: T038 calls the existing `derived_target`/`is_generated_note`/write machinery with a
worksheet-specific name and body, the same way `write_today` already does for `Today.md`.

**What would have been at risk if the worksheet loop had instead opened a new write path or a new
tool.** A second writer means a second implementation of "confined to `.derived`" and a second
implementation of the `generated: true` guard — exactly the failure mode the project's other
additive doctrines (D-24 for tool contracts; `intelligence.py:104-107`'s additive-import rule cited
in D-39/D-40) all exist to prevent: two places that each believe they enforce the same invariant,
with no mechanical guarantee they agree, and a real chance one of the two is subtly wrong in a way
the other's tests never catch. A new MCP tool for either the write or the read would additionally
blow FR-025's "zero new ToolSpecs across the whole feature" budget, and would need its own security
review pass — confinement, overwrite guard, envelope handling for anything read back as untrusted
content — that the existing tools have already had under D-22. Reusing both paths verbatim means
the worksheet loop inherits every property those two mechanisms already have, including the ones
nobody has to re-argue here, rather than opening a new surface that starts from zero accumulated
trust.

**Why this is not a new instance of D-20, just its record.** D-20 already states, as a general
rule, that the agent never writes vaults — Katagiri holds the vault REST token, exposes only
GET-only tools to the agent, and the Obsidian plugin's own MCP write endpoint is never registered
with it. The worksheet loop does not need a vault *write* tool at all, because the write is not a
call through any vault API in the first place: it is a local filesystem write inside the vault
directory, performed by Katagiri's own process under the `derived_target`/`generated: true`
guards above, exactly as `write_today` already performs one for `Today.md`. The *read* of the
filled-in worksheet, once the learner has hand-edited it in Obsidian, goes through the same
GET-only proxy every other read (`vault_file`/`vault_list`/`obsidian_active_note`) already uses,
and its content is treated as untrusted data on the way back in, same as any other vault read.
Nothing about this taskgroup asks D-20 to bend or grow an exception; this row exists so the
worksheet-specific instance of that compliance is on the record, rather than merely implied by
"D-20 already covers it."

**Net effect**: no code changes from this filing. This entry and ledger row D-41 are the
governance step FR-025 requires before T038 (the worksheet writer) and T039/T040 (the confinement/
overwrite/round-trip tests and the read-back wiring check) are written.

## `study_plan` tool + `import_curriculum` operator CLI (2026-08-23)

Session date: 2026-08-23. Filed as ledger rows D-44 and D-45, two additive changes with no
overlap between them beyond both touching the curriculum system.

**D-44 — new MCP tool `study_plan`, experimental, read-only.** It returns the curriculum
outlook: every curriculum node's mastered/reachable/blocked status, computed by the existing
`load_grammar_dag`/`grammar_reachability` pair (`intelligence.py:1917,2031`), plus node counts and
the existing caps block already surfaced elsewhere under D-36. Additive under D-24: it is a new
ToolSpec, and no existing tool's contract changes. Why a new tool rather than riding an existing
one: `lessons` (`mcp_server.py:995`, `session_tools.py:1519`) returns a bare list today, so there
is no additive slot to hang a whole-DAG-status payload off of without changing what callers of
`lessons` currently get back; `start_session` (`mcp_server.py:917`, `session_tools.py:1188`)
already carries the D-36 caps block and stays the single prescriber of what to do next — that
property (one action, never a menu) is exactly what `study_plan` must not disturb, since a full
node-by-node outlook read alongside a single-action prescription is not itself a competing
prescription. `study_plan`'s output is therefore explicitly informational: it names no next
action and offers no menu to choose from, so it does not create a second "what do I do now"
answer next to `start_session`'s. This is the first MCP-visible view of the whole study program —
until now, curriculum state was visible only one prescribed action at a time, through whatever
`start_session` chose to hand back.

**D-45 — operator CLI: `scripts/import_curriculum.py` gains a `--curriculum-md` mode.** It
invokes the existing `intelligence.import_curriculum` (`intelligence.py:1605`), including a
`--dry-run` pass-through. The gap being filled: no invocation path existed at all for that
importer outside test code (`tests/test_intelligence.py`, `tests/test_dverify.py`) — an operator
who needed to rebuild the grammar DAG from an edited `curriculum.md` had no script to run. The
deliberate MCP-side exclusion stays untouched: `test_the_curriculum_import_is_not_a_tool`
(`tests/test_mcp_tools.py:1253`) asserts `"import_curriculum" not in registered_tools()`, on the
reasoning that rewriting the grammar DAG that gates the whole i+1 reachability system (D-28) is an
operator's job, never something an agent session should be able to trigger over MCP. Adding a CLI
mode does not reach into that boundary — it is a second, human-operated entry point into the same
already-tested importer function, run from a terminal, not from the agent's tool surface; nothing
about it registers a tool or changes what `registered_tools()` returns.

**Net effect**: no contract change on either side. `study_plan` is a new, additive, informational
ToolSpec; the CLI mode is a new operator-only invocation path for code that already existed and
was already tested, with the MCP exclusion for that same code left exactly as it was.

---

## 007 TG1 — connection_status + holdout governance (2026-08-24)

Session date: 2026-08-24. Filed as ledger row D-46, before implementation, covering three
points from the 007 setup-observability plan's TG1 gate.

**Point (a) — `connection_status`, additive tool, 26 → 27.** Contract lives at
`specs/007-setup-observability/contracts/connection_status.md`. It takes no arguments and returns
process identity (version, pid, cwd, entry point, transport), resolved paths (data home, config,
DB, log file — reflecting the *answering* process's actual env-override resolution, not a
hardcoded default), client identity when available, and a secrets **presence** map keyed by
secret name with values restricted to the literal strings `"set"`/`"unset"` — never the secret
itself. `changed_anything` is hardcoded `false`; the behavioral guarantees section requires it
never raise on a missing config file, a locked/unreachable DB, or absent client identity — those
degrade to `config_exists: false` / `db_available: false` / an `"unknown"` client rather than an
exception. Judged against D-24 (the standing additive-tool-change test): no existing ToolSpec's
argument or return contract is touched, so this is a pure addition, not a breaking or ambiguous
change to anything already shipped. Judged against constitution VII: the tool is read-only,
mutates nothing, and the contract is explicit that it is "stable once merged" (additive-only
afterwards) — so it is VII-compliant on arrival and needs no separate constitution bump to land.

**Point (b) — held-out-suite governance, made explicit rather than assumed.** The 007 held-out
module (`specs/007-setup-observability/holdout/`) was authored in two pre-tasks commits
(77792d9 "test(007): author held-out stability suite", 4cedd6e "test(007): author held-out instances
stability module") *before* `/speckit-tasks` generated `tasks.md` — by design, so no
implementation task's shape could leak into the validation data meant to check it. Per
`plan.md`'s "Held-out suite protocol" section, the directory sits outside `tests/`, so ordinary
`uv run pytest` never collects it; it runs only from the dedicated gate task
(`uv run pytest specs/007-setup-observability/holdout -q`), and every other implementation task's
Read list and dispatch prompt explicitly excludes `holdout/` paths, this task's own dispatch
included. The governance rule this row records for the record: a held-out test failing means the
*implementation* is wrong and gets fixed — the held-out test itself is never edited to make it
pass; changing a held-out test requires an explicit user decision, recorded in the gate task text,
not a unilateral call by whichever session is running the gate.

**Point (c) — side-by-side instances, `KATAGIRI_DATA_HOME`.** Two installed copies of Katagiri on
one machine currently collide on `%LOCALAPPDATA%\Katagiri` (config, DB, logs) — there is no way to
point a second instance elsewhere. The fix mirrors a precedent already on the books rather than
inventing a new mechanism: D-22 established `KATAGIRI_CONFIG` as an environment override for
`config_path()` (`config.py:185-199`) that is used verbatim when set and leaves the default
`%LOCALAPPDATA%\Katagiri\config.toml` path untouched when unset — "does not add a second secret
source or move anything out of `%LOCALAPPDATA%`," per that function's own docstring
(`config.py:188-194`). `KATAGIRI_DATA_HOME` extends the identical argument one level up, to
`config_dir()` itself, so an override there relocates config, DB, *and* logs together as one
instance root instead of overriding them independently. The installer's new `--data-home` flag is
the operator-facing way to set that override durably: it persists the chosen path into
`agent/.env`, the same file the agent process already reads its environment from, rather than
requiring the operator to set the environment variable by hand every launch. `connection_status`
(point a) is what makes the result checkable: its `data_home`/`data_home_source` fields
(`"default"` vs `"env"`) let two side-by-side instances be told apart by which data home each one
actually resolved to. None of this touches Constitution I — multiple installed copies are still
multiple copies of a single-user tool, not a multi-tenant server; nothing here adds per-request
tenant isolation, auth, or a shared-state assumption that Constitution I would forbid.

**Judgment call — no constitution bump.** `connection_status` is additive, read-only, and already
matches constitution VII's additive-only-after-merge stability rule on day one; the
`KATAGIRI_DATA_HOME`/`--data-home` change is additive and default-preserving under the same D-22
reasoning already accepted for `KATAGIRI_CONFIG`; the held-out-suite rule formalizes a process
already in effect (commits already made, plan section already written) rather than changing any
shipped behavior. None of the three points removes a guarantee, widens the threat model, or
changes what "one user" means — so this row is filed as a plan-level decision, not a constitution
amendment.

## 007 TG2-close — HTTP-client allowlist exemption for pre-007 setup fetchers (2026-08-24)

`tests/test_bverify.py::test_only_the_obsidian_proxy_is_an_http_client` and
`tests/test_cverify.py::test_the_obsidian_proxy_is_still_the_only_http_client` failed at 007's
TG2-close full-suite run because `irodori_import.py` and `vendor_fetch.py` (both pre-007,
commits 5002422 and e0f5b64, 2026-08-23) use `urllib.request` and were never routed through the
allowlisted-exception process Phase E's asbplayer integration already set up for this invariant.
Read both modules to characterize them honestly rather than guess: `irodori_import.py` fetches
one file, the freely-published JF Irodori Starter TOC PDF, from `irodori.jpf.go.jp`;
`vendor_fetch.py` fetches the `vendor/` reference artifacts (UniDic, JMdict, kanjium accents,
BCCWJ, JLPT decks, jreadability) from their official hosts. Both are consent-gated, setup-time-only,
never reachable from MCP request-time code. User decision 2026-08-24: extend
`HTTP_CLIENT_ALLOWLIST` in both test files with `irodori_import.py` and `vendor_fetch.py` rather
than routing either module through the Obsidian proxy or building a second allowlist mechanism.
Recorded as D-47.

## 007 T021 — held-out pid asserts, venv launcher-shim probe (2026-08-24)

Two held-out stability asserts (`test_stability_connection.py`'s `payload["pid"] ==
client.process.pid`, `test_stability_logging.py`'s `str(pid) in text`) failed against a correct
server implementation. Isolated the cause with a Popen probe outside any Katagiri code:

```python
import subprocess, sys
p = subprocess.Popen([sys.executable, "-c", "import os; print(os.getpid())"],
                     stdout=subprocess.PIPE, text=True)
out, _ = p.communicate()
print("Popen.pid:", p.pid, "child os.getpid():", out.strip())
```

On this checkout's `.venv\Scripts\python.exe`, `Popen.pid` and the child's own `os.getpid()`
are two different numbers every run — the launcher shim under `.venv\Scripts\` exec's a real
interpreter as a child process rather than replacing its own image, so `Popen.pid` names the
shim and the truthful `os.getpid()` the server reports (via `connection_status` and its own log
line) belongs to the shim's direct child. This reproduces with an arbitrary throwaway script,
nothing katagiri-specific, and is a property of this Windows venv layout, not of
`mcp_server.py` or any 007 code. Confirmed the child relationship holds via
`Get-CimInstance Win32_Process -Filter "ParentProcessId=<Popen.pid>"` while the process is
alive, which is what the new `direct_child_pids()` holdout helper (`holdout/conftest.py`)
automates. User decision to amend the two asserts (accept `Popen.pid` or one of its direct
children) is recorded in `specs/007-setup-observability/tasks.md` T021 and filed as D-48 in
`docs/decisions-ledger.md`. No other held-out assertion was touched.

## 008 TG1 — browser companion detection boundaries (2026-08-24)

Filed before any 008 code task, mirroring 007's T001 pattern (ledger-first discipline). Read
research.md R3/R4/R6/R7 and plan.md's Constitution Check and Key design decisions to confirm
these are binding decisions, not implementation notes:

- **Zero MCP contract growth.** 008 registers no ToolSpec and changes none — plan.md's
  Constitution Check marks Principle VII PASS on exactly this basis, calling it "the strongest
  form of additive only." There is nothing for a future feature to migrate or preserve here.
- **No silent install, ever.** Chrome's only programmatic-install paths are enterprise policy
  (`ExtensionInstallForcelist`, a machine security-policy write) and unpacked side-loading.
  Both are rejected on principle, not as a stopgap until something better ships — recorded as a
  platform fact the design works around permanently, via detect → store-URL handoff →
  post-wizard re-check (research.md R5, R6).
- **Browser profiles are read-only to Katagiri.** The detector in `companions.py` reads
  `Extensions\<id>\` presence and `Preferences` JSON only; no write, rename, delete, or
  extension file manipulation under any browser data directory, at any point in the design.
- **Absent ⇒ `MANUAL STEP`, never `MISSING`.** `doctor_exit_code()` (installer.py:795) returns 1
  iff any row is `MISSING`; making an absent companion `MANUAL STEP` leaves that function
  untouched and keeps SC-004 (installer exits 0 with everything optional missing) true. This is
  the same in-line argument `probe_irodori` and `probe_schtasks` already carry — 008 extends an
  existing severity convention rather than inventing one (research.md R6, plan.md decision 3).
- **The mokuro row is configuration readiness, not liveness.** `MokuroBridgeServer` is Katagiri's
  own server (not the learner's browser), is never started by the MCP server at request time,
  and the userscript that would drive it is not shipped in this repository (research.md R3). So
  the row can only honestly report `mokuro_shared_secret` set/unset and pinned-port
  free/occupied — never "the learner installed the userscript" — with "free" worded as expected
  outside a live session, per spec FR-010.
- **No `HTTP_CLIENT_ALLOWLIST` change.** The port probe is a bare
  `socket.create_connection(("127.0.0.1", 8767), timeout≈0.2)`. Checked against
  `tests/test_bverify.py`'s `HTTP_CLIENT_PATTERNS` and `HTTP_SERVER_PATTERNS`: it matches
  neither, so no allowlist entry is needed. This is the explicit contrast with D-47, which *did*
  need an allowlist exemption because `irodori_import.py`/`vendor_fetch.py` use
  `urllib.request`. Plan.md treats this as a hard constraint, not a preference: if an
  implementer ever needs an HTTP client for this probe, that forces a stop-and-escalate to a new
  user decision and ledger row, never a silent allowlist edit (research.md R3, plan.md
  decision 5).
- **The 008/009 boundary.** 008 detects only the asbplayer *browser extension* (one of three
  distinct "asbplayer" things per research.md R4: extension, web app, WebSocket bridge). It adds
  no bridge health row and touches none of `asbplayer_launch.py`, `media_asbplayer.py`, or
  `config.asbplayer_bridge_dir` — so `009-asbplayer-bridge-in-process`, which is expected to
  replace the Go bridge with an in-process Python WS server, inherits no doctor-row contract it
  has to preserve or migrate.

**Judgment call — no constitution bump.** Plan.md's Constitution Check evaluates all seven
principles as PASS against v1.4.0 with no adaptation needed beyond V's existing cold-subagent
gate posture (already established by 005/007), and Complexity Tracking is empty. None of the
above narrows or widens any existing principle: VII already required additive-only tool
changes and this row makes zero tool changes; VI's read-only/no-network posture is unchanged
by a bounded loopback connect; nothing here touches the event log, phase gating, or the
single-user posture that Principle I protects. Filed as a plan-level decisions-ledger row
(D-49), not a constitution amendment.

## 009 TG1 — in-process asbplayer bridge: cutover, protocol freeze, and the listener exemption (2026-08-24)

Filed before any 009 code task, per research.md R7's instruction that the allowlist question
is a user decision, not an implementation choice, and mirroring 007's/008's ledger-first
pattern. Read plan.md's Constitution Check and Key design decisions, and research.md R4, R6,
R7, R8 plus all six open items, to confirm these are binding decisions rather than
implementation notes, and recorded the reasoning behind each of the four escalation answers
here rather than only in the ledger row.

- **Why a clean cutover instead of a config-gated fallback.** Plan.md decision 4 rejects
  keeping the Go spawn path behind a flag: maintaining two implementations of one protocol
  means every future bug report starts with "which bridge was running?" — a permanent tax
  with no offsetting benefit once the in-process replacement exists. The rollback safety net
  that actually matters is kept anyway: `bridge_port_is_occupied()` (`asbplayer_launch.py`)
  already makes Katagiri stand down for whatever already holds port 8766, so a learner who
  starts the old Go bridge by hand is unaffected. `asbplayer_bridge_dir` is left in the config
  schema as dead weight rather than removed, purely because `tests/test_skeleton.py:115,146`
  pin it as a known key — deleting it would be a test-driven false economy for a key that
  costs nothing to leave inert (FR-014, plan.md decision 5).
- **Why the protocol is treated as frozen, not as a target to improve toward.** research.md R1
  and R3 transcribe the Go bridge's routes, fields, and the `addNote` intercept branch-by-branch
  with `main.go` line citations against the asbplayer checkout at local commit `37495e22`
  (based on upstream tag `1.20.2`, `570f441d`). The reasoning for treating that transcription
  as the contract, rather than any implementer's sense of "what a bridge should do": the real
  client is asbplayer's own extension, which was never written against a spec, only against
  this exact Go behavior — deviating from the transcription risks the extension silently
  failing to interoperate in ways no Katagiri-side test would catch (research.md O-1 is exactly
  this risk, closed only by TG3's mandatory real-machine differential run). Upstream issue
  #1087 is open, unmerged, and has no overlap with `37495e22` today (research.md R8); the task
  text is explicit that a merged protocol change upstream is a stop-and-replan trigger for the
  dedicated re-check task, not something later work should try to absorb quietly.
- **Why the six G-1..G-6 divergences are documented as deliberate rather than silently fixed.**
  research.md R4 catalogs six Go-source behaviors as bugs, not contract: a double-slashed
  proxy URL on `GET /` (G-1), an index panic on a missing `Content-Type` header (G-2), an
  unchecked type assertion on the `addNote` note body that panics instead of forwarding a
  malformed note (G-3), a single shared response channel that lets one waiter eat another
  request's reply (G-4, fixed by a per-`messageId` registry — a strict improvement given
  `media_asbplayer.py` already only ever has one request in flight), request-body logging to
  stdout that would corrupt MCP stdio and leak note contents (G-5, contradicting FR-017), and
  no timeout on the outbound AnkiConnect leg (G-6). Recording all six by name means a future
  reviewer diffing the new module against `main.go` reads each difference as an intentional,
  reasoned fix rather than a reimplementation error.
- **Why `aiohttp`, and why not stdlib.** research.md R6's decision table rejected stdlib
  (`http.server` plus a hand-written RFC 6455 handshake and frame codec) on a constitutional
  ground, not a convenience one: the real client is a genuine browser WebSocket — masked
  frames, fragmentation, control frames, a close handshake, and base64 payloads past 64 KiB for
  `load-subtitles` — and hand-writing that framing against it is reimplementing OSS in the
  sense Principle II forbids outright, for the single highest technical risk in the whole
  feature. `websockets` alone was rejected because its server hook cannot see the three POST
  request bodies the HTTP half needs; `websockets` sans-io over hand-rolled HTTP/1.1 was kept
  on record as the fallback if `aiohttp` ever becomes unavailable, since it trades one hand-
  rolled protocol for another rather than eliminating the risk. The AnkiConnect outbound leg
  deliberately stays on `http.client` rather than `aiohttp`'s own client, because FR-007 wants
  byte-exact header and body passthrough and an async client that manages `Host`,
  `Content-Length`, and hop-by-hop headers on your behalf is the wrong instrument for that —
  the cost of this choice is that the new module needs **both** allowlist entries, not just the
  server one.
- **Why both allowlist questions went to the user rather than being pre-approved.** research.md
  R7 is explicit that `media_mokuro.py` is the only standing `HTTP_SERVER_ALLOWLIST` exemption
  and that D-47 is the process precedent for a client-side exemption going to the user with its
  own ledger row. The task text treats these as two separate questions with two separate
  answers precisely because they gate different risk: the server exemption is "should Katagiri
  ever listen on a socket for this purpose at all" (the answer determines whether 009 can exist
  in this form, or has to be re-planned entirely), while the client exemption is "which
  fidelity/exemption trade-off for one outbound leg." Both were answered "yes" by the user on
  2026-08-24, recorded verbatim in the ledger row (D-50) with the exact escalation text and the
  user's exact reply, matching D-47's and D-48's precedent of quoting the user's decision rather
  than paraphrasing it.
- **Why `aiohttp`'s transitive tree needed its own escalation.** Katagiri has carried exactly
  four runtime dependencies (`fugashi`, `mcp`, `pypdf`, `tzdata`) since inception, and D-10 set a
  vendored-and-checksummed posture for data specifically because unaudited runtime downloads
  were treated as a risk worth avoiding. `aiohttp` pulls in `multidict`, `yarl`, `frozenlist`,
  `aiosignal`, `propcache`, and `attrs`, none of which were audited before this row (research.md
  O-6) — a materially different kind of addition from a data file, so it went to the user as
  its own question rather than being bundled into the WebSocket-hosting decision.
- **Why zero MCP contract growth was confirmed rather than assumed.** Plan.md decision 10
  scopes the doctor row as the smallest useful addition and explicitly calls a bridge-status
  MCP tool a different, larger feature. Because 008 deliberately left this exact door open
  (008 research.md R4, restated in D-49(g)) precisely so 009 could choose either way, the
  choice itself — doctor row only, no tool — needed the user's explicit confirmation rather
  than treating 008's silence as tacit permission.

**Judgment call — no constitution amendment.** Plan.md's Constitution Check and research.md R7
agree that Principle VI's "stdio-only MCP transport, no network listener" describes the MCP
transport itself and already coexists with `media_mokuro.py`'s bridge; this feature extends
that same coexistence to a second module (`asbplayer_bridge.py`) rather than narrowing or
widening the principle's text, so it is recorded as a plan-level decisions-ledger row (D-50),
not a constitution amendment — matching how D-49 treated 008's Principle VII question. If the
user's escalation-1 answer is later read as wanting Principle VI to name this second exception
explicitly in the constitution text, that is a separate amendment filed the normal way
(ledger row first), not implied by this row or by D-50.

## 2026-08-24 — 009 T008 upstream re-check (second pass)

No stop-and-replan: v1.20.2 still latest, `main.go`/web-socket-client unchanged upstream since
`570f441d`, issue #1087's subscribe/capability-handshake half still open and unmerged (its
media-targeting half was already merged pre-`570f441d` and already reflected in research.md
R1.3); local asbplayer checkout HEAD unmoved at `37495e22` but working tree is dirty with
unrelated, unmerged local `request-playback-state` exploration. Full findings in
specs/009-asbplayer-bridge-in-process/research.md ("2026-08-24 — Upstream re-check, second
pass (T008)").
