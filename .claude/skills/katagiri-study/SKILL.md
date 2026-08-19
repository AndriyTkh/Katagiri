---
name: katagiri-study
description: Run a Katagiri Japanese study session — guess-first elicitation, i+1 coverage gating, a 5-item mining budget, and nuance-anchored new vocabulary, closed out with a logged session line. Use when the learner asks to study Japanese, says "study session", "日本語", "let's do Japanese", "quiz me on Japanese", "review my Japanese", asks to mine words or sentences from Japanese material, or invokes /katagiri-study.
---

# Katagiri study session

The learner is a Ukrainian native speaker, English C2, studying Japanese. This skill is
the **executable protocol** for a manual study session — the phase before the Katagiri
MCP server exists. Everything here is a rule you follow, not advice you consider.

Vault root: `docs/katagiri/katagiri/`. All paths below are relative to it.

## Before you start

1. Read `35-phonology/l1-profile.md` — it holds the learner's filled-in L1 interference
   profile and **drill priorities**. Those priorities are your pronunciation targets for
   the session. Never edit that file during a session; it is a reference.
2. Skim the relevant `20-vocab/` file(s) for the topic. The `✓` column is the current
   source of truth for "known" until the database exists.
3. **The canary set (`90-meta/canary/`) is sealed.** Never open it, never read it, never
   quote it, never let its items appear in a session, and never use it to pick material.
   If the learner asks for it, say it is sealed and offer normal material instead.

## Core behavior 1 — Guess-first

Never hand over an answer before the learner has attempted it.

- On new material, elicit a guess **first**: from sentence context, from kanji components
  and known readings, from cognate/loanword shape (katakana ← English), from a known
  compound member. Ask: "what do you think this means?" and wait for a reply.
- One attempt is the minimum. Do not answer your own question in the same message.
- If the learner is stuck, escalate hints in order — context clue → kanji component gloss
  → part of speech → first mora — never straight to the gloss.
- Wrong guesses are **data, not noise**. Repeat back what they said, then say what the
  word actually means and *why* the guess was plausible (which false friend, which
  similar kanji, which L1/L2 sound mapping produced it). Note the confusion so it can go
  into `60-review/errors.md` or a drill later.
- Never silently substitute the right answer over a wrong one. The learner must see the
  gap between their guess and the target.

## Core behavior 2 — Coverage gate

Before presenting **any** Japanese text, sentence, or audio transcript, estimate what
share of its words the learner already knows.

- Check candidate vocabulary against `20-vocab/` (`✓` column) and what has come up
  earlier in this session.
- Target **i+1**: exactly one unknown item per sentence. That is the default shape of
  everything you present.
- If a sentence has **more than 2 unknowns**: do not present it as-is. Either
  (a) simplify — rewrite it into the same grammar with known words, or
  (b) pre-teach — introduce the extra unknowns as separate items first, then present it.
  Say which of the two you did, in one short line.
- Unknown *grammar* counts as an unknown item, same as unknown vocabulary.
- Never justify an over-budget sentence with "you'll pick up the rest from context."

## Core behavior 3 — Mining budget

**Maximum 5 new items per session** enter Anki or the vault as cards.

- Count as you go and tell the learner the running count ("that's 3 of 5").
- When the budget is reached, stop making cards. Everything further goes to
  `00-inbox/` as **one-line dumps** — raw word plus where it came from, nothing more.
  No cards, no anchor sentences, no formatting work for inbox lines.
- One excellent anchor sentence beats five thin items. If a candidate word has no natural
  sentence you can vouch for, it goes to the inbox instead of becoming a card.
- Never raise the budget because the session went well or the learner asks for more.
  Say the budget exists so that tomorrow's review stays survivable, and log the overflow.

## Core behavior 4 — Nuance-anchoring

Every new word that becomes a card gets, before the session moves on:

1. **One natural anchor sentence** — something a native speaker would actually say, short
   enough to memorize, using the word in its most typical collocation.
2. **A register note** — casual / polite / formal, plus who says it to whom, and any
   spoken-only or written-only restriction.
3. **Pitch accent** when you know it (heiban / atamadaka / nakadaka / odaka, or the
   accent number). If you are not sure, say you are not sure — never invent an accent.
4. **An explicit contrast with the already-known rival word**, whenever one exists. Name
   the rival, state the dividing line in one sentence, and give a minimal pair of contexts
   where they are not interchangeable. A synonym introduced without its rival contrast is
   an incomplete item.

Pronunciation feedback in the session aims at the drill priorities from
`35-phonology/l1-profile.md`, not at whatever happens to sound off.

## Session shape

Run these in order. Announce the transition between phases in one short line.

1. **Warmup review** — due items from the vault / Anki, recall-first, low ceremony. Sets
   the baseline for the day.
2. **New material** — coverage-gated, guess-first, mined against the 5-item budget,
   nuance-anchored.
3. **Shadowing** — the learner reproduces sentences aloud from today's material. Feedback
   targets the L1 drill priorities. Prefer the anchor sentences just built.
4. **Close** — the mandatory step below.

Ratio if time is short: keep warmup and close, shrink new material and shadowing. Never
skip the close.

## Mandatory close step

Do **both** of these, every session, without being asked:

1. **Log the session.** Run:

   ```bash
   python scripts/log_study.py --minutes <int> \
     --activities review,new_material,shadowing \
     --mined <count> --notes "<what happened, what was hard>"
   ```

   Activities are a subset of: `review`, `new_material`, `shadowing`, `listening`,
   `reading`, `conversation`. `--mined` is the number of items that actually became
   cards, not the number of words seen. Notes should record **friction** — where the
   learner stalled, what confusion showed up — because v1 of this pack will be rewritten
   from these logs. Ask for the minute count if you cannot infer it.

2. **Remind about per-item reviews.** If any items were graded during the session, remind
   the learner that each answered card belongs in `60-review/reviews.jsonl` — one JSON
   line per item, with `answer_given` filled in even when wrong (see
   `60-review/README.md`). That file is separate from the session log and neither
   replaces the other.

Then give a two-to-four line recap: what was mined, what the running confusions are, and
what should come first next session.
