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

Hiragana is learned row by row; each row is a node so progress and reachability
can be tracked per block instead of as one all-or-nothing "hiragana" blob.
Katakana starts only once every hiragana row (plain, voiced/semi-voiced and
contracted) is in:

```yaml
id: g-hiragana-a
level: A0
---
id: g-hiragana-ka
level: A0
---
id: g-hiragana-sa
level: A0
---
id: g-hiragana-ta
level: A0
---
id: g-hiragana-na
level: A0
---
id: g-hiragana-ha
level: A0
---
id: g-hiragana-ma
level: A0
---
id: g-hiragana-ya
level: A0
---
id: g-hiragana-ra
level: A0
---
id: g-hiragana-wa
level: A0
---
id: g-hiragana-dakuten
level: A0
prereqs: [g-hiragana-ka, g-hiragana-sa, g-hiragana-ta, g-hiragana-ha]
---
id: g-hiragana-youon
level: A0
prereqs: [g-hiragana-ka, g-hiragana-sa, g-hiragana-ta, g-hiragana-na, g-hiragana-ha, g-hiragana-ma, g-hiragana-ra, g-hiragana-ya]
---
id: g-katakana-checkpoint
level: A0
prereqs: [g-hiragana-a, g-hiragana-ka, g-hiragana-sa, g-hiragana-ta, g-hiragana-na, g-hiragana-ha, g-hiragana-ma, g-hiragana-ya, g-hiragana-ra, g-hiragana-wa, g-hiragana-dakuten, g-hiragana-youon]
```

## Phase 1 — First sentences (weeks 1–6)

```
g-desu-copula ──> g-wa-topic ──> g-o-object ──> g-masu-form
                       │                            │
                       └──> g-no-possessive         └──> g-negation
                       └──> g-question-ka
```

Vocabulary: `people/family`, `food`, `numbers 1–10`, `time/days`, `transport`, `body`, core 50 verbs.

External references (T028): the most defensible mapping evident from each
node's own grammar point, not verified against the JF Standard's official
can-do numbering, Irodori's official lesson index, or Tae Kim's exact section
titles — say so plainly rather than citing a precise-looking id nobody checked.

```yaml
id: g-desu-copula
jf_can_do: A1 Interpersonal - self-introduction (name, nationality) via です/だ
irodori_lesson: Irodori I (Beginner 1), opening lessons - self-introduction
tae_kim_section: Tae Kim, Essential Grammar - だ and です
---
id: g-wa-topic
jf_can_do: A1 Interpersonal - introducing a topic (Xは...) in self-intro exchanges
irodori_lesson: Irodori I (Beginner 1), opening lessons - topic marker は
tae_kim_section: Tae Kim, Essential Grammar - Particles, は
---
id: g-o-object
jf_can_do: A1 Interpersonal - stating what one does or wants using a direct object (Xを...)
irodori_lesson: Irodori I (Beginner 1), early lessons - verbs with を objects
tae_kim_section: Tae Kim, Essential Grammar - Particles, を
---
id: g-masu-form
jf_can_do: A1 Interpersonal/Informational - describing routine actions politely (-ます form)
irodori_lesson: Irodori I (Beginner 1), daily-routine lessons - ます form
tae_kim_section: Tae Kim, Essential Grammar - Verb Basics, polite form (-ます)
---
id: g-no-possessive
jf_can_do: A1 Interpersonal - describing belonging or relations (XのY)
irodori_lesson: Irodori I (Beginner 1), early lessons - の for possession and description
tae_kim_section: Tae Kim, Essential Grammar - Particles, の
---
id: g-negation
jf_can_do: A1 Interpersonal/Informational - saying what is not the case (negative です/-ません)
irodori_lesson: Irodori I (Beginner 1) - negative forms introduced alongside affirmative ones
tae_kim_section: Tae Kim, Essential Grammar - Negative Verbs and negation
---
id: g-question-ka
jf_can_do: A1 Interpersonal - asking simple yes/no questions (...か)
irodori_lesson: Irodori I (Beginner 1), opening lessons - question marker か
tae_kim_section: Tae Kim, Essential Grammar - Question marker か
```

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
jf_can_do: A1 Interpersonal - free text, JF Standard can-do reference
irodori_lesson: free text, Irodori lesson reference
tae_kim_section: free text, Tae Kim's Guide section reference
```

`jf_can_do`, `irodori_lesson` and `tae_kim_section` (T028, D-39) are optional,
free-text external references — none required, any subset may be given. They
land on the same node id, additively: re-importing after editing a tag updates
it, and re-importing after *removing* a tag reports it as an orphan (logged,
never deleted from where it was stored) rather than silently forgetting it.
