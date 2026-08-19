---
schema: 1
type: phonology
id: p-0001
title: Pitch accent
tags: [core, day-one]
---

# Pitch accent

Japanese is a **pitch-accent** language. Words are distinguished by where the pitch *drops*, not by stress.

You are being taught this on day one on purpose. Every course defers it as "advanced." It is not advanced — it is *cheap now and expensive later*. A word learned without its accent is a word learned wrong, and you'll be fighting a habit in year three instead of storing one field today.

## Notation

`[0]` — **heiban**, no drop. Pitch rises after mora 1 and stays up, including onto the following particle.
`[n]` — drop occurs after mora *n*.

Count moras, not characters. ん is a mora. Long vowels are two moras. The small っ is a mora.

## The pairs that prove it

| Word | Accent | Meaning |
|---|---|---|
| はし | [1] | 箸 chopsticks |
| はし | [2] | 橋 bridge |
| はし | [0] | 端 edge |
| あめ | [1] | 雨 rain |
| あめ | [0] | 飴 candy |
| かみ | [1] | 神 god |
| かみ | [2] | 紙 paper |
| いま | [1] | 今 now |
| いま | [0] | 居間 living room |

Context usually saves you. Usually. But accent is also what makes you *comprehensible* — listeners parse word boundaries partly from pitch, so wrong accent makes you harder to follow even when every sound is right.

## Practice

- Every vocab note has a `pitch:` field. Learn it with the word, not later.
- Shadow with pitch in mind, not just sounds → [[70-drills/shadowing]]
- Later: `score_pronunciation` overlays your pitch contour on a native one. §3.5 in [[ARCHITECTURE]].

## Honest caveat

Accent varies by region (Kansai is often near-inverted from Tokyo) and some words have multiple accepted accents. Learn standard Tokyo, hold it loosely, and mark uncertain entries `[?]` rather than guessing.
