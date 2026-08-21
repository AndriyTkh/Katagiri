---
schema: 1
type: drill
id: d-0002
title: Assessment cadence
skill: [listening, prosody, production]
minutes: 10
frequency: weekly (dictation, pitch-marking) + monthly (monologue)
---

# Assessment cadence

The practical companion to the study skill's Core behavior 5. That page states the policy
(what counts as an error, which `log_error` pattern it feeds, why no synthesis anywhere in
the pitch exercise); this page states the exact steps.

Three checks, on two clocks. The weekly two are independent of each other — run whichever is
still outstanding for the current ISO week, skip whichever already ran. The monthly one is
its own clock, checked once per calendar month.

## Weekly mora-count dictation

**Prerequisite**: a real Irodori audio line under `vendor/irodori/` (hand-acquired, see
`vendor/README.md`). No line yet → say so, skip the week, do **not** invent a substitute line
by speaking or typing one — the whole point is a source recording with a known-correct mora
sequence to score against.

1. Pick one unheard line whose vocabulary sits at or near the learner's current i+1 coverage
   band — not the hardest available line, not a trivial one.
2. Play the line **once**. A second play is allowed if genuinely inaudible (say why); more
   than two means the exercise is not measuring dictation anymore and should be deferred.
3. Learner writes the kana transcription. No dictionary, no pause-and-look-up, no replaying
   word-by-word — this is a listening measurement, same discipline as WATCH mode's first pass.
4. **Score mora-by-mora against the source line's kana**, not word-by-word:
   - **Mora-length error**: the transcription's mora count at a given position disagrees with
     the source — a long vowel (あー/おー-type) written as short or the reverse, a geminate っ
     dropped or inserted, ん counted as part of the preceding mora instead of its own. Each
     instance → `log_error(said=<what they wrote>, correct=<source segment>,
     pattern="mora-length", severity=...)`.
   - **Devoiced-vowel error**: a vowel that devoices in the source's phonetic environment
     (word-final す/つ, です/ます-class endings — see `35-phonology/l1-profile.md` §5) is
     written as a fully voiced kana, or a vowel that should stay voiced is written as if
     devoiced. Each instance → `log_error(..., pattern="devoiced-vowels", ...)`.
   - A transcription can carry zero, one, or several of each in the same line — log every
     instance separately, do not collapse them into one call.
5. Close the whole exercise with **one** `log_observations` call: `unassisted=true` unless the
   line was replayed more than once or the learner asked for its meaning before writing,
   `coverage_band` from the i+1 estimate used in step 1, `rubric_version` as usual.
6. Close the lesson with `topic: "weekly-mora-dictation"` (reserved slug, verbatim, every
   time) so a later gate can count dictation weeks the same mechanical way KANA's
   `phase0-kana-dictation` slug already lets it count Phase-0 days.

## Weekly five-word pitch-pattern marking

Text-only, start to finish. No audio, no TTS, no synthesis call anywhere in this exercise —
that is the point, not an incidental limitation (this is the perception-training precursor to
the eventual **F-02** VOICEVOX trigger; see `spec.md`'s deferred-triggers table for the exact
firing condition).

1. Pick five words: a mix of already-known vocabulary and the current week's mined items.
   Present each in **kana only** — no accent number, no kanji reading hint.
2. Learner marks each word's pitch pattern using the same notation as
   `35-phonology/pitch-accent.md`: `[0]` for heiban (no drop), `[n]` for a drop after mora *n*.
   The marking surface is plain text — a table the learner fills in, e.g.:

   | Word (kana) | Learner's mark | Kanjium number | Match? |
   |---|---|---|---|
   | はし | [ ] | | |
   | あめ | [ ] | | |
   | かみ | [ ] | | |
   | いま | [ ] | | |
   | (5th word) | [ ] | | |

3. For each word, call `lookup` and read its `pitch` field — this is sourced from
   `vendor/kanjium/accents.txt` (the kanjium row in `vendor/README.md`), never recalled from
   memory or guessed. Fill in the "Kanjium number" column and mark "Match?" yes/no.
4. **Score the mismatches**, not the matches:
   - A mismatch where the learner's own description (or the agent's observation of how they
     produced it, if said aloud) shows the l1-profile's predicted length-creep tell — marking
     the accented mora louder *and longer* rather than only lower — reuses the existing
     `mora-length` pattern: `log_error(said=<their mark>, correct=<kanjium number>,
     pattern="mora-length", severity=...)`.
   - A mismatch that is purely pitch-height (no length signature) has **no existing pattern to
     reuse honestly**. Do not force it into `mora-length` or invent a new pattern name for it.
     Instead, note it in the closing observation (step 5) — the mismatch is still counted, just
     as structured note text rather than a `log_error` pattern.
5. Close the whole 5-word set with **one** `log_observations` call: `unassisted=true` (this is
   marking, not hint-escalated elicitation) unless the `[0]`/`[n]` notation itself had to be
   re-taught this session, `coverage_band` (usually `">=95"` since the words are chosen from
   known vocabulary plus already-mined items), `rubric_version`, and the pitch-height-only
   mismatch count from step 4 in the note.
6. Close the lesson with `topic: "weekly-pitch-marking"` (reserved slug, verbatim).

## Monthly 60-second monologue

1. Pick a prompt matching the learner's current modality-ladder rung (SKILL.md §KANA step 7):
   script visible for A0→A1, script hidden for A1+. Zero free conversation is still the floor
   at A0 — do not run this exercise at all for a learner who has not cleared A0.
2. Learner records themselves speaking for roughly 60 seconds (a few seconds either side is
   fine; do not pad or cut to hit an exact count).
3. Save the file as `.mp3` or `.wav` under the vault at
   `80-progress/monologues/<YYYY-MM>-monologue.mp3` (or `.wav`) — never under `local/` or
   `.derived/`, both of which the vault snapshot always excludes regardless of extension.
   `VAULT_SNAPSHOT_EXTENSIONS` already includes both formats (FR-007, widened in TG0/T005),
   so the file is captured by the next snapshot with no extra step.
4. **Trend, concretely**: note the recording's length in seconds, and count disfluencies —
   false starts, self-corrections, and unfilled pauses longer than about a second — across the
   60 seconds. Compare both numbers against the prior month's monologue for the same slot (not
   against an abstract target). Falling disfluency count and/or growing length at steady
   disfluency density is the trend signal; log both numbers in the closing `log_observations`
   note.
5. Close the lesson with `topic: "monthly-monologue"` (reserved slug, verbatim).

## What this page does not claim

No real weekly dictation, weekly pitch-marking, or monthly monologue record exists in the
event log yet. Whether these three are actually measurable through existing tools alone — no
new tool, no new table — is verified by fixtures in a separate task (006 T036), not asserted
here.
