---
schema: 1
type: meta
---

# Conventions

Read this once. It's the contract between you, Claude, and the app.

## Script policy — configurable

All of this is set in [[90-meta/settings]], not hardcoded. Defaults:

- Kanji recorded always, **displayed per `kanji_enabled`** (currently `false`).
- Reading (kana) is always present. It's the identity anchor and can't be turned off in the data.
- Rōmaji is **generated from kana, never stored**. `fmt` adds or drops the column per `script_mode`.
- Furigana per `furigana_mode` — `unknown_only` glosses just the kanji you haven't learned, so it shrinks by itself.
- `reading_as_goal: false` means reading cards exist but sit far behind listening and speaking in the queue.

One teacher's note, stated once: reading is fine to defer *as a goal*, but kana literacy is a week-one **prerequisite** — dictation, dictionary lookup and subtitles all require it, and it's 46 characters. There is a `script_mode: romaji` and an `audio_only` for people who disagree, and they work properly.

## Notes vs data

Everything hand-written is Markdown. Everything machine-scale is in the DB. The test is: **would a human ever read this file?**

## Frontmatter

Every note has `schema:` and `type:`. `type` is one of:
`topic | grammar | media | lesson | drill | phonology | progress | index | settings | meta`

IDs never appear in topic files — they're derived from content (see [[ARCHITECTURE]] §1.3).

## Pitch accent notation

`pitch: [0]` = heiban, no drop. `pitch: [2]` = drop after the 2nd mora.
Count **moras**, not characters: きって = ki-t-te = 3, とうきょう = to-o-kyo-o = 4, ん is its own mora.
If unverified, write `pitch: "[?]"` — an honest unknown beats a confident guess.

## Register tags

`polite` `plain` `casual` `rough` `humble` `honorific` `feminine` `masculine` `archaic` `anime-only` `written-only`

Everything spoken gets one. This is what stops anime from teaching you to talk like a delinquent without knowing it.

## Topics

Hierarchical, slash-separated: `food/fruit`, `people/family`, `transport`, `time/days`, `verbs/motion`.
A word has one **home topic** (the file it lives in) plus optional `Also` entries for cross-listing. Add a topic by creating the file — nothing needs registering.

Split a file once it passes ~150 words. Scannability is the whole point of this format.

## Grading scale (used in `reviews.jsonl`)

| Grade | Meaning |
|---|---|
| 1 | No idea |
| 2 | Wrong, but recognised it after seeing the answer |
| 3 | Right, with effort or hesitation |
| 4 | Right, instant and automatic |

For speaking cards, **hesitation is a 3 even if the answer was perfect.** Fluency is speed.

## Never do these

- Never edit a line in `reviews.jsonl`. Append a correction instead.
- Never put copyrighted media in the repo. `local/` only.
- Never hand-edit anything under `.cache/`, `.derived/`, or a file marked `<!-- GENERATED -->`.
- Never repeat a table field inside a Notes block. The parser attaches the block to the row; duplication means drift.
- Never add a second authoritative store for content. One writer for Markdown, one for the event log.
- Never store a conjugated form as its own vocab entry. Store the dictionary form + verb class.
- Never add a word without a reading and a category.
