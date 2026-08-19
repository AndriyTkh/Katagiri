# Katagiri v4 mockup — substrate + progressive-translation overlay

Date: 2026-08-18. Round 3 input. Successor to v3 (agent-native, killed by round-2 panel).

## Thesis change from v3

v3 said: "the agent is the product; Katagiri hosts it." Round 2 panel converged: the agent layer
is commoditizing for free; the substrate is the only defensible asset. User accepted this and
went further:

1. **Katagiri does not host a tutor.** Speaking practice, comprehension lessons, grammar
   explanation/grading in context — all deferred to external general agents (ChatGPT, Claude,
   whatever the user already pays for) which connect to Katagiri's **MCP server** as their
   tool/data layer. MCP exists "only to keep track of everything."
2. **The product surface is a progressive-translation media overlay.** Texts and subtitles start
   in the user's L1 and progressively swap in Japanese words as the user's known_set grows, until
   the passage is fully Japanese. Framed by the user as "our main selling point as an app."
3. **Word voiceovers stay in scope** — per-lexeme TTS reference audio (VOICEVOX, accent override
   at single-lexeme granularity only, cached by content hash).
4. Everything else (grammar authoring, drill templates, tutor UX, conversation engine) is out.

## Components

### A. Substrate (the base everything builds off)

- **Identity layer:** lexeme IDs anchored to JMdict entry+sense with an alias/redirect table
  (v1's `aliases.tsv` pattern, kept per round-1 tech finding #3). UniDic for tokenization and
  accent fields.
- **known_set:** per-user item→strength map, split receptive/productive per round-2 pedagogy
  finding. Written only by app-owned events, never by agent prose.
- **Event log:** append-only `review_events` + new `exposure_events` (word shown in overlay,
  word tapped, word heard). ULID, dual timestamps, tz — round-1 tech fix kept.
- **Scheduler:** FSRS over recognition; overlay exposures feed a separate strength signal
  (implicit, low-weight) distinct from explicit review grades.
- **Voiceover cache:** per-lexeme VOICEVOX audio, content-hash keyed, license-vetted voices only.

### B. Overlay (the product)

- **Reading surface:** browser extension + reader app. Takes any text (web page, pasted text,
  ebook, article). Renders it in L1 with Japanese words substituted inline for items the user
  knows or is learning. Substitution ratio is driven by known_set + scheduler (due/new items get
  priority placement). Tap a word → gloss, reading, voiceover, pitch; tap-to-add logs an
  exposure/learn event. Over months the same text drifts from ~95% L1 to 100% Japanese.
- **Subtitle surface:** same mechanic on subtitles (asbplayer/Language Reactor-style client-side
  overlay for streaming video + local files). L1 subtitle line with Japanese words woven in,
  ratio keyed to known_set; full-JP + furigana at the top of the ladder.
- **Placement rules:** substitution happens at content-word level (nouns, verb stems, adjectives,
  set phrases). Particles/grammar are NOT substituted piecewise — grammar arrives in staged
  "sentence-frame flips" where a whole clause flips to Japanese word order once its items are
  known. (Open design question — this is the hard part.)
- **All client-side.** No server-side ingestion of copyrighted media (round-2 tech finding kept).
  Server sees token IDs and events, not the text itself (privacy + copyright posture).

### C. MCP server (the bookkeeper)

Tool surface, deliberately small (~10 tools, down from v3's ~20):

- Read: `known_set`, `item_get`, `resolve_lemma`, `stats`, `weakest`, `confusion_pairs`,
  `coverage(text)` (pasted text only), `get_due`.
- Write: `log_exposure`, `log_error`, `add_item` (dedupe-guarded via `resolve_lemma`),
  `submit_review` — **but see open question below: round-2 agent-reliability finding #1 said
  agent-called submit_review is unenforceable; if external agents are the tutor, someone has to
  write grades. Options: (a) accept agent-written grades from external tutors as a separate
  lower-trust event class; (b) require review to happen in Katagiri's own UI and give agents
  read-only; (c) signed grading sessions with app-rendered cards inside the agent chat.**
- External agents (ChatGPT/Claude apps with MCP connector support) do: conversation practice,
  grammar explanation and in-context grading, lesson planning, error interpretation. Katagiri
  ships prompt templates/instructions ("connect your tutor"), not a tutor.

### D. Explicitly out of scope

Tutor UX, conversation engine, grammar_constructions table, drill template engine beyond what
the overlay needs, perception-gating, learner_model belief store, weekly letters, automated
pronunciation scoring, server-side media ingestion.

## Known unresolved questions (carry-ins from round 2)

1. External-agent-written events: the guardrail problem (fact linter unworkable, self-confirming
   beliefs, missing submit_review calls) transfers to third-party agents — worse, since Katagiri
   doesn't control their prompts at all.
2. Does progressive word-substitution actually teach? (Diglot weave method — needs evidence
   check.) Japanese word order/particles don't slot into L1 sentences the way European-language
   words do; a Japanese noun in a Ukrainian sentence teaches the noun but possibly wrong
   collocation and zero grammar. The "sentence-frame flip" idea is unvalidated hand-waving.
3. Competitive: Language Reactor, asbplayer, Migaku, jpdb do adjacent things; Toucan did exactly
   L1-page word-substitution as a business. What happened to it and why?
4. Monetization unclear: extension + MCP server + free tutor agents the user already pays
   someone else for = where does money enter?
5. GDPR / event-log erasure conflict from round 2 still unresolved.
