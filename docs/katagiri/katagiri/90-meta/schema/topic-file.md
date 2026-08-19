---
schema: 2
type: meta
---

# Schema: topic file

The topic file is the **only** thing you hand-write and the **only** committed source of truth for vocabulary and sentences. One file per topic. Words, sentences, and notes together, in one place, readable top to bottom.

## Shape

```markdown
---
schema: 2
type: topic
topic: food/fruit-vegetables      # required, hierarchical, unique
title: Fruit & Vegetables         # required, human title
tags: [week-01]
---

# Fruit & Vegetables

Optional intro prose. Anything you like.

## Words

| Word | Reading | Pitch | Meaning | POS | Also | ✓ |
|---|---|---|---|---|---|---|
| 林檎 | りんご | [0] | apple | n | food | ●●●○○ |
| 水 | みず | [0] | water (cold) | n·〜杯 | drink | ●●○○○ |

## Sentences

- これは 林檎です。 — This is an apple. `polite` →[[g-desu-copula]]

## Notes

### 林檎（りんご）
Usually written in kana...
```

## Column rules

**You write these:**

| Column | Required | Notes |
|---|---|---|
| Word | yes | Kanji if it exists, otherwise same as Reading |
| Reading | yes | Kana. This is the identity anchor. |
| Pitch | yes | `[0]`, `[2]`, or `[?]` if you haven't verified it. Never guess. |
| Meaning | yes | Short gloss. Long explanations go in Notes. |
| POS | yes | Compact codes, see [[90-meta/tags]]. `n`, `v1·vt`, `v5·vi`, `adj-i`, `adj-na`, `exp`. Append `·〜つ` for a counter. |
| Also | no | Extra topics this word belongs to. Cross-listing is generated from this. |

**`katagiri fmt` writes these** — treat the topic file like gofmt'd code: edit freely, run the formatter, it aligns pipes and fills derived columns:

| Column | Source |
|---|---|
| Rōmaji | generated from Reading, only if `script_mode` includes rōmaji. Never stored. |
| ✓ | mastery, computed from `reviews.jsonl` |
| Kanji column hidden | if `kanji_enabled: false`, `fmt` drops the Word column and keeps Reading |

`fmt` is idempotent and deterministic. Running it never changes meaning, only layout.

## Identity — no ID columns

IDs are **derived from content**, not allocated:

```
word_id     = "w-" + sha1("林檎|りんご")[:6]
sentence_id = "s-" + sha1(normalized_japanese)[:6]
```

- Deterministic across machines. No counters, no allocation, no collisions in practice.
- **No `w-0142` clutter in your tables.** The thing you objected to about per-word files applies to ID columns too.
- Editing a sentence produces a new ID, which is correct — it's a different sentence.
- Fixing a typo in a *reading* would orphan review history, so `validate` catches unmatched rows and asks: rename, or new word? Confirmed renames are appended to `90-meta/aliases.tsv` (flat, sorted, greppable, committed).

That reconciliation step is the honest cost of not having visible IDs. It's worth it.

## Sentences

`Japanese — English  `register`  →[[grammar-link]]`

Sentences live where they belong: topic sentences here, grammar sentences in the grammar note, mined sentences in the media note. There is no central sentence file — `40-sentences/_index.md` is a **generated** aggregate view across all of them.

## Notes section

Detail blocks keyed by `### Word（Reading）`. Contain prose and any field too rare to deserve a column (register warnings, near-synonym contrasts, provenance). **Never repeat what's already in the table** — the parser attaches the block to the row, so duplication means two writers and drift.

## Size guidance

Comfortable up to ~150 words per file. Beyond that, split by subtopic (`food/fruit` and `food/vegetable` become separate files) rather than letting one file become unscannable. The whole point of this format is that you can read it.
