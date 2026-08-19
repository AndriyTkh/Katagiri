---
schema: 2
type: index
---

# Vocabulary

One file per topic. Words, sentences, and notes together — this is the "what do I know, sorted by topic" view, and it's the format you actually read and edit.

Format spec: [[90-meta/schema/topic-file]]

## Topics

| Topic | Words | Learned | Avg mastery |
|---|---|---|---|
| [[food-fruit-vegetables]] | 2 | 2 | ●●○○○ |
| [[people-family]] | 1 | 1 | ●○○○○ |

<!-- rows below the header are refreshed by: katagiri fmt -->

## Planned topics

`people/family` · `food/fruit` · `food/vegetable` · `food/drink` · `numbers` · `counters` · `time/days` · `time/clock` · `transport` · `body` · `place/city` · `place/home` · `weather` · `clothing` · `emotion` · `verbs/motion` · `verbs/daily` · `adjectives/basic` · `greetings` · `school` · `work`

Add a topic by creating the file. Nothing needs registering.

## Cross-topic views

A word's `Also` column cross-lists it elsewhere. Those views are generated into `.derived/` (gitignored) — so 林檎 appears under both `food/fruit` and `food`, without existing twice in git.
