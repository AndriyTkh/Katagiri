---
schema: 1
type: meta
---

# Architecture & Design Rationale

This document exists so that in eight months, when you want to add a feature, you know which decisions are load-bearing and which are free to change.

---

## 1. Decisions that are load-bearing (change these and things break)

### 1.1 Markdown is the source of truth; the database is a cache

Every alternative fails the same way. If SRS state lives in SQLite and content lives in Markdown, you get two writers and no merge strategy. First time you edit a word in Obsidian while the app has it open, you lose data or you build a sync layer, and the sync layer becomes the project.

So: **content in Markdown, review events in an append-only log, everything else derived.**

```
20-vocab/items/*.md   ─┐
30-grammar/*.md        ├─→  build  ─→  .cache/katagiri.db  ─→  app / MCP queries
40-sentences/*.md      │              (rebuildable, gitignored)
60-review/reviews.jsonl┘
```

`katagiri build` is idempotent. Delete `.cache/` at any time.

### 1.2 Review history is event-sourced, never mutated

`60-review/reviews.jsonl` is append-only. One JSON object per line, one line per answered card:

```json
{"ts":"2026-08-18T07:41:02Z","item":"w-01f3","direction":"listen_to_meaning","grade":3,"latency_ms":2840,"answer":"apple","expected":"apple","session":"s-0114","source":"app"}
```

Why this and not a `next_due` field on the note:

- **You can change scheduling algorithms retroactively.** Swap SM-2 for FSRS, retune parameters, and recompute your entire history. Your data is not hostage to your first guess at an algorithm.
- **Git diffs stay sane.** Append-only means every commit is additive. No churn, no conflicts.
- **It records what you *answered*, not just pass/fail.** This is the single most underrated design choice in this whole system — see §3.6.
- **Latency is captured.** Recall speed is a better fluency signal than accuracy, and for speech it's the only signal that matters.

Rule: nothing ever rewrites a line in this file. Corrections are new events.

### 1.3 Content-derived IDs, invisible in the files

Review history needs stable identity. Visible `w-0142` columns would clutter the tables you actually read, so IDs are **derived, not allocated**:

```
word_id     = "w-" + sha1("林檎|りんご")[:6]
sentence_id = "s-" + sha1(normalized_japanese)[:6]
```

Deterministic across machines, no counters, no allocation, nothing to see in the Markdown. Editing a sentence yields a new ID, which is correct — it's a different sentence. Fixing a typo in a *reading* would orphan history, so `validate` catches unmatched rows and asks "rename, or new word?"; confirmed renames append to `90-meta/aliases.tsv` (flat, sorted, greppable, committed).

That reconciliation prompt is the honest cost of keeping IDs out of the tables. Worth it.

### 1.4 Topic files are the unit — reversed decision

**Superseded v1, which used one note per word. That was wrong.** It optimized for machine-side elegance and produced thousands of files nobody would ever browse. The stated goal is *"a human looks at what they know, sorted by topic"* — and a per-word vault cannot serve that at all.

The rule now:

> **One Markdown file per topic. Words, sentences and notes together in it. Nothing smaller than a topic is ever a file.**

Format: [[90-meta/schema/topic-file]]. Words in a scannable table, sentences below, prose notes for the entries that need them. You read and edit exactly this and nothing else.

The concerns that made me argue for per-word notes are handled without files:

| Concern | Resolution |
|---|---|
| A word belongs to several topics | It has one **home topic**; the `Also` column cross-lists it. Cross-topic views are generated into `.derived/` (gitignored), so 林檎 appears in two views while existing once in git. |
| Backlinks / provenance / the "network" | Generated **word dossiers** on demand — every sentence, media scene, and logged mistake for one word, rendered into `.derived/words/` for Obsidian graph browsing. Regenerable, never committed. You get the network when you want it, not 3,000 files when you don't. |
| Stable identity for review history | Content-derived IDs, §1.3. No visible ID columns. |

### 1.4b Where the second store belongs

Your instinct that some things shouldn't be Markdown was right, but the boundary isn't words-vs-topics. It's this:

> **Would a human ever read this file? If yes, Markdown. If no, database.**

| Hand-curated → Markdown, committed | Machine-scale → DB, gitignored |
|---|---|
| Topic files (words, sentences, notes) | Tokenized media transcripts |
| Grammar notes | Dictionary data (JMdict, KANJIDIC2, pitch DB) |
| Lessons, drills, media notes | Frequency tables, coverage matrices |
| Error museum, weekly letters | Untriaged candidate sentences |
| `reviews.jsonl` (append-only events) | Scheduler state, search indexes, cross-topic views, word dossiers |

Note what is *not* on either list: a second **authoritative** store for content. There is exactly one writer for hand-curated content (you, in Markdown) and exactly one for events (the app, appending to JSONL). Everything in the right column is derived and deletable. Introduce a second authoritative store and you inherit a sync layer, and the sync layer becomes the project.

### 1.4c `katagiri fmt` — the topic file is gofmt'd Markdown

This is what makes a hand-edited file safely machine-readable. You type sloppily; `fmt` aligns the pipes, sorts rows, fills the derived columns (mastery, rōmaji), refreshes generated tables, and validates the schema. Deterministic and idempotent. Run it on save.

Because `fmt` owns layout and you own content, there's no ambiguity about who wrote what — which is the thing that normally kills hybrid human/machine formats.

### 1.5 Schema versioning from day one

Every note carries `schema: 1`. When the schema changes, migration scripts live in `90-meta/scripts/migrations/` and bump the number. This is ten minutes of work now and saves a weekend later.

### 1.6 The curriculum is a DAG, not a linear course

`10-course/curriculum.md` declares concepts and their prerequisites. The app asks "what is reachable given what I know" instead of "what is lesson 14". This is what lets the system grow with you without ever needing to be restructured — you add nodes, not chapters. It also means media can unlock grammar out of order, which is how immersion actually works.

### 1.7 Copyright hygiene

Subtitle files, ripped audio, manga pages: **never committed**. They live in `local/`, which is gitignored. Media notes store a reference (URL, episode, timestamp) plus your own notes and short quoted lines. If this repo ever goes public, that distinction matters.

### 1.8 Display is a setting; data is not

Every script preference — kanji on/off, kana-only, rōmaji, audio-only, furigana on unknown kanji only — is a **rendering setting** in [[90-meta/settings]]. None of them affect what gets stored.

Topic files always record kanji, kana reading, pitch, mora count and register, even for a learner who has switched all of it off. `fmt` decides what to show. Consequences:

- Turning kanji on in month four is a config flip, not a migration, and the system already knows which kanji make up *your* spoken vocabulary.
- Rōmaji is **generated from kana, never stored**, so changing your mind costs nothing.
- `audio_only` mode is properly supported rather than bolted on, because audio refs and pitch are first-class fields.

The rule: **a display preference must never be able to cause data loss.**

---

## 2. What you're missing as a learner

You designed a good vocabulary-and-grammar system. Here's what a teacher would add, roughly in order of how much regret it prevents.

### 2.1 Pitch accent — from day one, as data

Japanese distinguishes words by pitch. 箸 hashi [1] (chopsticks), 橋 hashi [2] (bridge), 端 hashi [0] (edge). 雨 ame [1] (rain) vs 飴 ame [0] (candy). Nobody tells beginners this because "it's advanced." It isn't advanced — it's *cheap now and expensive later*. Learning a word without its accent means learning it wrong and re-learning it in year three, with a fossilized habit fighting you.

Every vocab note has a `pitch:` field using standard drop notation ([0] = heiban/no drop, [n] = drop after mora n). It costs you nothing to store it and it makes you sound like someone who has ears.

### 2.2 Mora timing, not syllable timing

Japanese is mora-timed. きって has three beats (ki-t-te), とうきょう has four (to-o-kyo-o), and ん is a full beat. English speakers compress these and become permanently hard to understand. This is a *rhythm* skill, trained by shadowing with a metronome-like awareness, not by reading.

### 2.3 The specific sounds that will betray you

ら-row (a flap, neither L nor R), ふ (bilabial, no teeth), つ, devoiced vowels (です → "des", 好き → "ski"), the nasal が in mid-word, ん assimilating (こんばんは vs こんにちは). These need targeted minimal-pair drills in `35-phonology/`, not general practice.

### 2.4 Dictation is the highest-yield listening drill and nobody does it

Recognition is not comprehension. Write down what you hear, character by character, then diff against the transcript. The diff *is* the lesson: it shows you exactly which sounds and which grammatical particles you are not perceiving. Your ear improves faster from ten minutes of this than an hour of passive watching.

### 2.5 Directions of recall are separate skills, scheduled separately

One word produces at least five different cards, and they are not interchangeable:

| Direction | Trains | Prompt → Answer |
|---|---|---|
| `listen_to_meaning` | listening | audio → meaning |
| `meaning_to_speech` | **speaking** | English/image → you say it aloud |
| `read_to_meaning` | reading | kanji+kana → meaning |
| `cloze_production` | grammar in use | sentence with gap → fill it |
| `shadow` | prosody | audio → repeat, scored on timing+pitch |

Given your priorities, `listen_to_meaning` and `meaning_to_speech` are your primary decks. Reading cards exist but are scheduled lazily. **Speaking cards must be answered out loud and scored by ASR — never by typing.** If you can type it you cannot necessarily say it, and the whole point of this project is the saying.

### 2.6 Register and politeness — the anime trap

You want to learn from anime. Anime is full of speech that will get you treated as a child, a thug, or a woman from 1955. だぜ, てめえ, plain-form commands, 〜わよ, 〜かしら, おれ vs ぼく vs わたし, casual copula dropping.

Every phrase and sentence in this vault carries a `register:` tag (`polite`, `plain`, `casual`, `rough`, `humble`, `honorific`, `feminine`, `masculine`, `archaic`, `anime-only`). The system can then **lint your output**: if you're practising a shop-counter roleplay and you say a 俺様 line, it tells you. This turns your biggest risk into your biggest advantage — you get anime's stickiness *and* social awareness.

### 2.7 Conjugation as a rule engine, not flashcards

Do not memorize 200 conjugated forms. Store verbs with their class (godan / ichidan / irregular) and generate every form programmatically. Then drill the *transformation*: "食べる → past negative polite" → you say 食べませんでした. Infinite exercises from a small rule set. Same for adjectives (i/na).

### 2.8 Counters and numbers deserve their own subsystem

一つ二つ、一人二人、一本、一匹、一枚、一杯 — irregular, high-frequency, and impossible to avoid. They need dedicated drills, ideally spoken-aloud and timed, because in real speech you have no time to compute them.

### 2.9 Grammar concepts get *understanding levels*, and get revisited

は vs が is not a lesson, it's a two-year relationship. Each grammar note has `understanding: 1-5` and a `revisit:` log. You come back to it at level 2 with sentences you've now met in the wild, and it means something different. A linear course cannot do this; a DAG with levels can.

### 2.10 An error log is worth more than any textbook

`60-review/errors.md` records *your* mistakes: what you said, what was right, what pattern it belongs to. After two months this file is a personalized syllabus that no course could produce. It's also the input to §3.6.

### 2.11 Two clocks, tracked separately

- **Input clock:** hours of Japanese into your ears.
- **Output clock:** minutes of Japanese out of your mouth.

Nearly every self-taught learner has a 50:1 ratio and doesn't notice until they freeze in a real conversation. Track both. Target something like 4:1 to 6:1 early, tightening over time.

### 2.12 Kanji: don't study it yet, but *instrument* it now

You're right to defer kanji. But every word note stores its kanji and their components from the start. So on the day you begin, the system already knows which 300 kanji make up your existing spoken vocabulary and can order them by *your* usefulness rather than by JLPT level. You start kanji with a personalized, pre-populated, already-half-familiar list. This costs nothing today.

### 2.13 Handwriting (optional, but honest)

Writing kanji by hand roughly doubles retention for most learners and is the fastest way to stop confusing 待/持/特. If you ever plateau on kanji recognition, this is the lever.

---

## 3. The ambitious part

Ideas that are worth building because they're only possible when your entire learning history is queryable plain text.

### 3.1 `known_set` is the core primitive

One function — "what does this learner know, and how well, as of a given date" — makes every other feature personalized. Sentence selection, media recommendation, exercise generation, conversation constraint, coverage analysis: all of them are just `known_set` plus a filter. Build this well and build it first.

### 3.2 The i+1 router

The strongest known selection heuristic for immersion: study sentences containing **exactly one** unknown item. `find_sentences(max_unknown=1)` across your entire sentence bank, mined media, and generated candidates. This turns a chaotic media diet into an ordered curriculum without you ever planning one.

### 3.3 Comprehension debt: your own frequency list

JLPT and textbook frequency lists are built from newspapers. You are watching anime and YouTube. So don't use theirs — build yours: every time a word blocks your comprehension in real media, it scores a point. Rank by **cost to you**, not corpus frequency. Within a month your top-200 list is dramatically better targeted than any published list, because it's derived from the actual Japanese you actually encounter.

### 3.4 "Am I ready for this?" — coverage gating

Point the tool at any video, article, or manga chapter. It returns: % of tokens you know, the unknown list ranked by frequency-within-this-work, an estimate of comprehensibility, and *the 15 words that would take you from 78% to 92%*. Pre-teach those fifteen words, then watch. This is the single highest-leverage workflow in the whole system and it's the reason media immersion works or fails.

### 3.5 Shadow-dubbing

Take a mined clip. Mute the line. You dub it. The system compares your recording to the original on **timing (mora alignment)** and **pitch contour**, and shows the two curves overlaid. This is the closest thing to a private accent coach, it is genuinely fun, and it trains §2.2 and §2.1 simultaneously.

### 3.6 The confusion graph

Because the review log stores *what you answered*, not just whether you were right, the system can build a graph of your specific confusions: you mix up 見る/観る, you drop が in questions, you hear こ as ご. Then it **generates adversarial drills** targeting exactly those pairs — minimal-pair audio, cloze traps, sentences designed to re-catch that specific error.

No commercial app does this, because no commercial app keeps your wrong answers. It's the most valuable data you will produce and it costs one extra JSON field.

### 3.7 Ambient audio drills, generated from your own weak items

Every morning, generate a 15-minute audio file — Pimsleur-style, prompt/pause/answer — from your currently-weakest items, with real TTS voices. You listen while commuting or walking. Zero screen. This is where a huge amount of your speaking practice will actually come from, and it's only possible because your weak-item list is a queryable file.

### 3.8 The weekly sensei letter

Every Sunday, Claude reads the week's event log, error museum, and media notes, and writes you a letter in `80-progress/`: what improved, what's fossilizing, what you're avoiding, what to do next week, and one thing to be pleased about. Prose, not dashboards. Streaks make you compliant; a teacher noticing your progress makes you a learner. Keep them all — reading last quarter's letters is its own motivation.

### 3.9 Constrained-vocabulary conversation (this is what Codex is for — see §4)

A speaking partner that is *forbidden* from using words you don't know, plus at most 2–3 new ones per conversation, all of which get logged into your vault afterwards. Immersion without incomprehension. The constraint is the feature.

### 3.10 Gradual L1 removal

A `sensei_language` setting that walks from "explain in English" → "explain in simple Japanese, English on request" → "Japanese only, English available by tagging in a translator persona." Scheduled, deliberate, tracked. Most people never make this transition because there's no mechanism forcing it.

### 3.11 The vault becomes Japanese

Out-of-the-box: a `shadow/` view where you rewrite your own older lesson notes in Japanese as your level rises. Your study materials become your study materials. Rereading your month-2 notes rewritten by month-8 you is a startlingly good progress measure.

### 3.12 Karaoke as pitch and mora training

Unexpected but real: Japanese songs enforce mora timing (each mora gets a note) and give you enormous repetition with strong affective hooks. Karaoke tracks have timed lyrics for free. A song is a shadowing exercise you *want* to repeat forty times.

### 3.13 Never lock yourself in

Export to Anki (`.apkg`) and plain CSV from day one. If this project dies, your data walks out intact. This constraint also keeps the schema honest.

---

## 4. Codex as pair speaker

Codex has strong voice generation and low-latency conversation. Use it for everything spoken; use Claude for everything analytical. Split by strength, not by loyalty.

**Codex owns:**
- Live spoken conversation practice, constrained by `known_set` (§3.9)
- Reading aloud: example sentences, drills, dictation source audio
- Shadowing target audio at variable speed
- Roleplay scenarios from `70-drills/roleplay/` (konbini, ordering, train delay, doctor, self-introduction)
- Generating the ambient audio drills (§3.7)

**Claude owns:**
- Vault authoring and schema discipline
- Media analysis, transcription cleanup, coverage reports
- Exercise generation, curriculum planning, the weekly letter
- MCP orchestration in Claude Code

**Contract between them:** every Codex session ends by writing a session note into `00-inbox/` — transcript, new words used, your errors, a self-assessed fluency note. Claude triages that inbox into the vault. This is the loop that makes speaking practice *accumulate* instead of evaporate, and it's the piece almost everyone gets wrong: conversation practice that leaves no trace teaches you the least per minute of anything you do.

Fallback if you'd rather not depend on a single vendor for voice: **VOICEVOX** is free, local, Japanese-native, and better at Japanese prosody than most general TTS. Good for bulk audio generation (drills, sentence banks) where you want determinism and no API cost.

---

## 5. Tools you'll need that you didn't list

| Need | Tool | Why |
|---|---|---|
| Tokenization + lemma + readings | **fugashi/MeCab + UniDic** | Japanese has no spaces. Everything downstream depends on this. Non-negotiable. |
| Dictionary data | **JMdict** (JMdict_e) | Free, comprehensive, structured. |
| Kanji data | **KANJIDIC2** + **KRADFILE** | Readings, meanings, stroke counts, and components for §2.12. |
| Pitch accent data | **Kanjium** pitch DB / OJAD | Fills the `pitch:` field automatically instead of by hand. |
| Timestamped transcripts | **WhisperX** / faster-whisper | Forced alignment gives word-level timestamps → clip extraction, dictation, dubbing. |
| Video + subtitle acquisition | **yt-dlp** | Also grabs existing Japanese subs, which beat ASR when available. |
| Audio slicing | **ffmpeg** | Clip extraction for cards and dubbing. |
| Scheduling | **FSRS** (py-fsrs) | Modern, well-validated, and parameter-fittable *to your own log*. |
| Pronunciation scoring | ASR + **Parselmouth/librosa** for F0 | Transcript match for accuracy, pitch contour for prosody. |
| Frequency lists | anime/drama/subtitle-derived lists | Matches your actual diet far better than newspaper corpora. |
| Grammar reference | Tae Kim, Imabi, DoJG index | For citation, not for study order. |

---

## 6. The layer boundaries (so future features don't tangle)

```
┌─────────────────────────────────────────────────────────┐
│  Interfaces:  Obsidian · app UI · Claude Code · Codex    │
├─────────────────────────────────────────────────────────┤
│  MCP servers:  obsidian-mcp  +  katagiri-mcp             │  ← 90-meta/mcp-spec.md
├─────────────────────────────────────────────────────────┤
│  Core library (katagiri-core, one package):              │
│    known_set · scheduler · tokenizer · coverage ·        │
│    conjugator · generator · media pipeline · audio       │
├─────────────────────────────────────────────────────────┤
│  Derived cache: .cache/katagiri.db  (rebuildable)        │
├─────────────────────────────────────────────────────────┤
│  Source of truth: Markdown notes + reviews.jsonl (git)   │
└─────────────────────────────────────────────────────────┘
```

**Rules:**
1. Only the core library writes to the vault. MCP and the app call the core; they never parse Markdown themselves.
2. The core never writes to `.cache/` except via `build`.
3. Every MCP tool is a thin wrapper over one core function. If an MCP tool has logic in it, that logic belongs in the core.
4. `katagiri validate` must pass before every commit. Schema drift is the thing that kills vaults.
