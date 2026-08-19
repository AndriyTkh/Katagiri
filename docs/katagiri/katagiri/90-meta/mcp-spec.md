---
schema: 1
type: meta
---

# katagiri-mcp — tool surface

Design notes:
- Every tool is a thin wrapper over one core-library function.
- Read tools are cheap and pure. Write tools always append or create; they never silently overwrite.
- Anything that mutates the vault returns the path it touched so Claude can show you the diff.
- `as_of` defaults to now, but exists on every knowledge query so you can ask counterfactuals ("what did I know in June?").

Status legend: **v0** = build first, **v1** = second pass, **v2** = later.

---

## Knowledge model

| Tool | Args | Returns | Status |
|---|---|---|---|
| `known_set` | `as_of?`, `kinds[]` (word/kanji/grammar), `min_strength?` | Set of item IDs with strength scores. **The core primitive — everything depends on it.** | v0 |
| `item_get` | `id \| lemma` | Full item record + backlinks | v0 |
| `resolve_lemma` | `surface` | Canonical lemma + reading + existing ID (dedupe guard before any add) | v0 |
| `stats` | `window?` | Counts, input/output clocks, retention, streak-free progress summary | v0 |
| `weakest` | `n`, `kind?`, `direction?` | Lowest-strength items, for drill generation | v0 |
| `graph_neighbors` | `id`, `depth?` | Related items: co-occurring words, shared grammar, same media | v1 |
| `confusion_pairs` | `min_count?` | Your specific confusions, mined from wrong answers | v1 |

## Search & selection

| Tool | Args | Returns | Status |
|---|---|---|---|
| `search_vocab` | `query?`, `topics[]?`, `pos?`, `register?`, `strength_lt?`, `limit` | Word list | v0 |
| `topic_get` | `topic \| path` | Parsed topic file: words, sentences, notes, mastery | v0 |
| `word_dossier` | `word`, `write?` | The full network for one word — every sentence, media scene, mistake, related grammar. Renders to `.derived/words/` if `write` (gitignored, for Obsidian graph browsing). **This is how you get backlinks without 3,000 committed files.** | v1 |
| `search_grammar` | `query?`, `level?`, `understanding_lt?`, `prereq_of?` | Grammar notes | v0 |
| `search_sentences` | `contains[]?`, `grammar?`, `max_unknown?`, `register?`, `has_audio?` | Sentences | v0 |
| `find_i_plus_one` | `n`, `focus?`, `source?` (bank/media/generated) | Sentences with exactly one unknown item. **§3.2** | v0 |
| `next_reachable` | `n` | Curriculum-DAG nodes whose prerequisites you've met | v1 |

## Review

| Tool | Args | Returns | Status |
|---|---|---|---|
| `get_due` | `limit`, `directions[]?`, `deck?`, `include_new?` | Due cards with prompts + audio refs | v0 |
| `submit_review` | `item`, `direction`, `grade`, `latency_ms`, `answer_given?`, `audio_ref?` | Appends to `reviews.jsonl`. **`answer_given` is what powers §3.6 — always send it.** | v0 |
| `schedule_preview` | `item`, `grade` | What each grade would do to the interval | v1 |
| `refit_scheduler` | — | Refits FSRS params against your own log, reports change | v2 |

## Authoring

| Tool | Args | Returns | Status |
|---|---|---|---|
| `add_vocab` | `topic`, `word`, `reading`, `meaning`, `pos`, `pitch?`, `also[]?`, `note?` | Appends a row to the topic file (creating it if needed) + returns the diff | v0 |
| `add_sentence` | `jp`, `en`, `home` (topic/grammar/media), `register`, `audio?` | Appends to the home note's Sentences section | v0 |
| `add_grammar` | `title`, `summary`, `prereqs[]`, `level`, `examples[]` | Path | v0 |
| `log_error` | `said`, `correct`, `pattern`, `context`, `severity` | Appends to error museum | v0 |
| `triage_inbox` | `dry_run?` | Proposals for filing `00-inbox/` items; applies on confirm | v1 |
| `fmt` | `paths[]?` | Normalize topic files: align tables, sort rows, fill derived columns (mastery, rōmaji), apply `script_mode`, refresh generated indexes. Deterministic and idempotent — the thing that makes hand-edited files safely machine-readable. | v0 |
| `get_settings` / `set_setting` | `key`, `value` | Read/write [[90-meta/settings]]. Changing `kanji_enabled` or `script_mode` triggers `fmt`, never a data migration. | v0 |
| `validate_vault` | `fix?` | Schema lint: missing fields, bad IDs, broken links, orphan items | v0 |

## Media pipeline

| Tool | Args | Returns | Status |
|---|---|---|---|
| `coverage` | `text \| url \| media_id` | % known, unknown list ranked by in-work frequency, comprehensibility estimate, **"learn these 15 to reach 92%"**. §3.4 | v0 |
| `recommend_media` | `kind`, `minutes?`, `target_coverage?`, `interests[]?` | Ranked candidates with coverage estimates | v1 |
| `ingest_media` | `url \| path`, `kind` | Transcript (timestamped), tokenized, media note created, clips staged in `local/` | v1 |
| `explain_passage` | `media_id`, `start`, `end` | Line-by-line breakdown: tokens, grammar, register, cultural notes, what to notice | v1 |
| `mine_clips` | `media_id`, `items[]?`, `max?` | Audio+screenshot cards for target words in context | v1 |
| `annotate_watch` | `media_id`, `note`, `timestamp` | Live note-taking while watching | v1 |
| `tasks_from_media` | `media_id`, `types[]` | Generated exercises: dictation, cloze, dub, comprehension Qs | v1 |

## Exercises & drills

| Tool | Args | Returns | Status |
|---|---|---|---|
| `gen_exercise` | `type`, `focus?`, `n`, `constraints?` | Exercise set. Types: `cloze`, `translate_en_jp`, `conjugate`, `particle_pick`, `counter`, `minimal_pair`, `dictation`, `register_fix`, `word_order` | v0 |
| `gen_adversarial` | `n`, `from?` (confusion graph / error museum) | Drills targeting your specific failure patterns. §3.6 | v1 |
| `conjugate` | `lemma`, `forms[]?` | Full paradigm from verb class — no stored conjugations | v0 |
| `build_sentences` | `items[]`, `n`, `register`, `max_unknown` | Sentences putting target words in usable context | v0 |
| `gen_ambient_audio` | `minutes`, `focus?` | Prompt/pause/answer audio file from weak items. §3.7 | v2 |

## Speech

| Tool | Args | Returns | Status |
|---|---|---|---|
| `tts` | `text`, `voice?`, `speed?`, `style?` | Audio ref (cached by content hash) | v0 |
| `asr` | `audio` | Transcript + confidence + timing | v1 |
| `score_pronunciation` | `audio`, `target` | Accuracy (phoneme/mora diff), timing alignment, pitch-contour comparison, per-mora feedback | v2 |
| `pitch_accent` | `word \| phrase` | Drop position, contour, minimal-pair partners | v1 |
| `start_conversation` | `scenario?`, `register`, `max_new_words`, `vocab_ceiling` | Session handle for constrained speaking. §3.9 | v1 |
| `end_conversation` | `session` | Transcript + new words + errors → written to `00-inbox/` | v1 |

## Reflection

| Tool | Args | Returns | Status |
|---|---|---|---|
| `weekly_letter` | `week?` | Drafts the sensei letter into `80-progress/`. §3.8 | v1 |
| `export` | `format` (anki/csv/json), `scope?` | Export file. **Never be locked in.** | v1 |
| `set_sensei_language` | `level` (en / simple-jp / jp-only) | Gradual L1 removal. §3.10 | v2 |

---

## Division of labour with obsidian-mcp

`obsidian-mcp` handles: free-text search, opening notes, reading arbitrary files, following links, your own ad-hoc browsing.

`katagiri-mcp` is the **only** thing that writes to topic files, so table structure can't drift.

`katagiri-mcp` handles: anything that needs the *knowledge model* — coverage, i+1, scheduling, generation, validation.

Rule of thumb: if the answer depends on **what you know**, it's katagiri. If it depends on **what's written**, it's obsidian.
