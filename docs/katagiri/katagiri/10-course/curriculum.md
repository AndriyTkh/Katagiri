---
schema: 1
type: index
title: Curriculum DAG
---

# Curriculum — a graph, not a course

Concepts declare prerequisites. The system asks *"what is reachable given what I know"* rather than *"what is lesson 14."* This is what lets the vault grow for two years without ever being restructured — you add nodes, not chapters. It also lets media unlock things out of order, which is how immersion actually works.

## Phase 0 — Ears and mouth (weeks 1–3)

Runs in parallel with everything. No prerequisites.

- [[35-phonology/mora-timing]]
- [[35-phonology/pitch-accent]]
- [[35-phonology/minimal-pairs]]
- **Hiragana** — yes, immediately. Not because reading matters yet, but because you cannot do dictation, use a dictionary, or read a subtitle without it. 46 characters, ~10 hours. Then katakana.
- [[70-drills/shadowing]] daily from day 1

## Phase 1 — First sentences (weeks 1–6)

```
g-desu-copula ──> g-wa-topic ──> g-o-object ──> g-masu-form
                       │                            │
                       └──> g-no-possessive         └──> g-negation
                       └──> g-question-ka
```

Vocabulary: `people/family`, `food`, `numbers 1–10`, `time/days`, `transport`, `body`, core 50 verbs.

## Phase 2 — Media entry (weeks 4–12)

Target: a 3–5 minute YouTube clip at 80%+ coverage, pre-taught.

- g-te-form, g-adjectives-i, g-adjectives-na, g-past-tense
- counters subsystem
- register awareness (before any anime — see §2.6 in [[ARCHITECTURE]])

## Phase 3 — Comprehension engine (month 3+)

- が vs は revisit at understanding 2–3
- transitivity pairs, potential form, conditionals
- First full anime episode, mined
- Constrained conversation practice with Codex

## Phase 4 — Kanji (month 4–6, deliberately deferred)

By then the vault already knows every kanji in your spoken vocabulary. You start with a personalized, component-ordered list of characters you *already say every day* — which is a completely different experience from opening a JLPT N5 deck.

## Node format

```yaml
id: g-0003
prereqs: [g-0001, g-0002]
level: A0
unlocks: [g-0004]
```
