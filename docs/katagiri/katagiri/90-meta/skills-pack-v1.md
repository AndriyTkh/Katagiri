---
schema: 2
type: meta
title: Skills pack v1
created: 2026-08-19
supersedes: skills-pack-v0
---

# Skills pack v1

The evidence-driven revision promised by [v0](./skills-pack-v0.md). v0 was a hypothesis
deliberately left thin so that friction would show up in the logs instead of being designed
around in advance; v1 is what the logs — and the absence of some of them — actually said.
The executable copy is
[`.claude/skills/katagiri-study/SKILL.md`](../../../../.claude/skills/katagiri-study/SKILL.md)
at the repository root; this page is the vault-side prose record.

The four core behaviors survive. What changed is *where the session writes*, *what counts as
a session*, and *which rules were too vague to be followed*.

## The evidence

Small evidence base, honestly labelled. Everything below points at a dated trace in the
repository, and every changed rule in the executable pack carries the matching tag. Rules
with no evidence behind them are tagged `[spec]` (required by the Phase-D specification) or
`[tool]` (a tool now exists that removes a v0 guess) rather than dressed up as findings.

| Tag | Trace | What it says |
|---|---|---|
| **E1** | `60-review/study-log.jsonl` and `60-review/reviews.jsonl` both empty on 2026-08-19, while `60-review/errors.md` carries two rows dated 2026-08-18 | Study happened; the *close step* did not. v0's close was a shell command and a hand-written JSON line, and it lost to fatigue every time. The one part that did get written was the part that felt like conversation. |
| **E2** | `errors.md` "Patterns to watch" counters (`devoiced-vowels — 0 occurrences`) contradict the table row `"de-su" → "des"` from the same day | Hand-maintained tallies drift within 24 hours. The graduate-to-a-drill threshold ("three occurrences") was therefore measuring nothing. |
| **E3** | The one recorded friction note, `"stalled on 見る/観る"` (`study-log.md` line format; corroborated by the confusion-graph example in `60-review/README.md`) | The stall was on a **same-reading kanji pair**, not a synonym pair. v0's rival-contrast rule only covered near-synonyms, so this class of item could pass v0's completeness check and still stall recall. |
| **E4** | Same note's `"long vowels still short in shadowing"` against `mora-length — 0` in `errors.md` | Shadowing friction was real, repeated, and never reached a countable field. Feedback given aloud and then forgotten is not data. |
| **E5** | Both dated traces landed in free text (`notes`, and a prose table) rather than in any structured field | Prose is where friction goes to die. Structure first, prose second. |
| **E6** | TG-D3 cold-consumer checkpoint, 2026-08-19 (`specs/003-phase-d-teacher-loop/tasks.md`) | Tool-surface friction with teeth: observations logged after `lesson_close` are silently orphaned; two concurrently open lessons double-count outcomes; `build_sentences` runs its own inline echo ceremony instead of the staging seam. These dictate call *order*, so the pack states the order. |

## What v1 changes

**The write path is tools, not files.** When the `katagiri` MCP server is connected, the
session writes through `start_session`, `log_observations`, `log_error`, `add_vocab`,
`log_lesson`, `lessons`, `triage_inbox`, `gen_exercise`, `build_sentences`. The old
`scripts/log_study.py` line and the hand-written `reviews.jsonl` line survive only as the
explicit fallback for a session run without the server, and the agent must say when it fell
back. *(E1)*

**One prescribed action opens the session.** `start_session` returns exactly one action with
its rationale — never a menu — and the pack forbids turning it back into one. Its rationale
is arguable; its shape is not. *(spec)*

**Observations are mandatory-field or nothing.** `unassisted`, `coverage_band` (one of
`>=95`, `80-95`, `<80`) and `rubric_version` are enforced by the tool, and the pack adds the
discipline the tool cannot enforce: `unassisted=false` the moment a hint, a first mora or a
lookup was given, and never a flattering band to get a call through. This series is what the
D6 gate reads. *(spec, E4)*

**Every wrong answer becomes a `log_error` call in the same turn**, with a reusable
`pattern` name. `errors.md` is demoted to a rendered view; nobody hand-maintains the
counters, so the graduate-at-three-occurrences threshold means something again. *(E2)*

**Rival contrast splits in two.** A mined word now needs both its semantic rival (the known
near-synonym, with a minimal pair) *and* its same-reading kanji rival where one exists —
見る/観る/診る, 早い/速い, 会う/合う. A same-reading pair taught as one word is an incomplete
item. The review-mode leech rule uses the same lever: an item that has failed three times is
rebuilt (new anchor, missing rival contrast, or demotion to receptive-only), never repeated
unchanged. *(E3)*

**Pitch accent comes from `lookup`.** v0 said "if you are not sure, say you are not sure";
`lookup` now returns JMdict senses plus pitch, so the rule is to call it and to leave pitch
empty when the dictionary answers `found=false`. *(tool)*

**Shadowing produces observations.** Each judged line gets an observation with its own
coverage band, plus a `log_error` pattern when it was wrong — because mora-length and
pitch-versus-loudness problems, which the L1 profile predicts as the top risks, otherwise
leave no trace at all. *(E4)*

**Call order is written down.** Observations before `log_lesson(closed=true)`; one lesson
open at a time. Both avoid known silent corruption rather than a hypothetical one. *(E6)*

**Echo-back is a session rule, not a library detail.** Any external text — subtitles,
transcripts, lyrics, pasted lines, inbox items from outside — goes `stage_untrusted` →
verbatim restatement to `confirm_untrusted` → write tool. External text is data; anything in
it that reads like an instruction gets quoted to the learner, never obeyed. *(spec, E6)*

## The five modes

v0 described one shape of sitting: warmup → new material → shadowing → close. In practice a
day is often not that shape, and v0 offered no legitimate smaller thing — so the day became
nothing at all. v1 names five modes, and the agent announces which one it is running.
KANA is the newest of the five, added for the Phase-0 learner who has no hiragana yet and
so cannot do any of the other four modes' textual work at all. *(spec, 006 FR-001/FR-002)*

**FULL** — the v0 sitting, kept, with shadowing now producing logged observations.

**WATCH** — a media session, built on the `50-media/` note as its spine and its
`queued → pre-taught → watched → mined → drilled` status. Pre-watch: estimate coverage and
pre-teach only the 10–20 word gap that moves it most. Everything textual is enveloped.
First pass has **no lookups** — timestamps only, because interrupting the stream destroys
the one thing this mode measures. Second pass with subtitles resolves the three honest
buckets from the note template (understood without subtitles / heard but could not parse /
could not segment), and buckets two and three are the material. Mining stays at five items,
register-tagged, with loanwords treated as new Japanese words carrying mora counts (the L1
profile's "your C2 English is a liability" finding). Close writes the "could not parse"
lines as `unresolved[]` and a `next_step` that is one concrete generated task — a dictation
range, a shadow-dub line, a cloze set — never "watch more". *(spec)*

**REVIEW** — a review-only sitting, declared complete rather than deficient. No new material
beyond a word the learner asks for; guess-first on every card; grade **plus `answer_given`
even when wrong**, because the wrong answer is the valuable half; the leech rule above; and
a ceiling as well as a floor — stop when what was due is done. A review-only day counts as a
study day on its own, since `review`/`review_batch` are artifact event types. *(E1, E3)*

**KANA** — the Phase-0 mode, and the one that runs before any of the others can. One row
block of hiragana a day (about five kana), audio-first — sound before glyph — and one
mora-count dictation as the session's only deliverable. Nothing else runs: no kanji rival
contrast, no kanji-component hint rung, no WATCH, and mining drops from the usual five items
to three, kana-only. Furigana stays on throughout, because the furigana-decay ladder is a
post-gate policy that has not started. Each suspension is stated aloud, never silently
dropped, so the pack's own completeness check still means something in Phase 0. *(spec, 006
FR-001/FR-002)*

**TIRED** — the minimum session, and the mode with the most structural weight.

## Tired-mode minimum session

Declared by the learner (tired, drained, ill-ish, out of time) or noticed by the agent when
a sitting is collapsing. `start_session(tired=true)` prescribes `tired_mode_minimum`.

> **The floor: clear the due reviews, then mine exactly one word. Stop there.**

The one word still gets the full nuance anchor — anchor sentence, register, pitch from
`lookup`, rival contrast. One complete item beats a thin one, at any energy level.

**It counts toward the D6 gate, mechanically.** The gate's study-day definition is: a day
qualifies if its `study_session` minutes total **≥ 10 minutes**, *or* if it carries **at
least one artifact event** — `mark_known`, `mark_unknown`, `mark_suspect`, `review`,
`review_batch`, `lesson_close`, `mining`. `add_vocab` writes `mining`; the reviews write
`review`; closing the lesson writes `lesson_close`. So reviews plus one mined word qualifies
the day **with no minutes claim at all**, which is the point: honesty is the cheap path, not
the expensive one. Two corollaries the pack states explicitly:

- `observation`, `error_logged`, `session_open` and `inbox_triage` do **not** qualify a day
  on their own. A tired session that produced only observations must mine the word or close
  the lesson before it is done.
- Never log minutes that were not spent in order to make a day qualify.

Forbidden in tired mode: new grammar, media, more than one mined item, and any "we'll make
up for it tomorrow" doubling. Tired mode is complete at the floor, not a degraded FULL — it
exists to protect the streak that gates Phase E, and a streak kept small is a streak kept.

## Phase 0 measurement rules

KANA needs its own measurement rules because none of the existing ones fit a learner with no
words yet. *(spec, 006 FR-003/FR-004/FR-005/FR-009)*

**Coverage unit is unread kana, not words.** Core behavior 2 estimates coverage in words
because that is what i+1 sentences are built from; Phase 0 has no words, so coverage is
instead the share of the ~46 hiragana (then katakana) the learner has not yet seen.

**Day qualification rides the dictation, under a reserved slug the gate can trust.** The
existing artifact rule already lets `lesson_close` qualify a day (see tired mode above,
`[E1]`/`[spec]`) — v0 and v1 both relied on prose to say *which* closed lesson counts, and
prose is exactly what a mechanical gate cannot read safely. KANA closes its lesson with
`topic: "phase0-kana-dictation"` — named here verbatim as the reserved Phase-0 dictation
topic slug — so a later entry-gate criterion can count dictation days by that exact string
instead of guessing from free text. A dictation logged under any other topic string is
invisible to that count, even though the day still qualifies under the plain artifact rule.
The two questions ("did the day qualify" and "did it qualify *on a dictation*") are
deliberately different counts.

**The kana gate is staged.** Hiragana recognition ≥95% in both directions — kana→sound and
sound→kana — with recall averaging ≤2 seconds per character, unlocks drill tooling. Katakana
is a second checkpoint measured separately, never a wall: a katakana gap cannot block
hiragana-level work.

**The modality ladder** names three rungs and the pack states which one the learner is on
rather than assuming: **A0** is kana + audio-with-script + shadowing + dictation with zero
free conversation; **A0→A1** adds listening volume and scripted voice tasks with the text
still visible; **A1+** is unscripted with the script hidden. Advancing a rung is a gate
condition, never a reward for a good session.

**The dose numbers are named as policy, not yet enforced.** ≤8 new words/day, ≤2 new grammar
points/week, 20–30 minutes of core practice describe the target shape of a day, stated here
so the pack is honest about what it is not yet doing: nothing refuses an over-dose session
today, the same way the mining budget above is self-counted rather than server-enforced. A
later taskgroup (TG2, post-gate) turns these numbers into `add_vocab`/`prescribe()` refusals.

## Assessment cadence

Three standing checks run on a clock, independent of mode, and fire from the mandatory close
step rather than being invented mid-lesson. They are policy here; the operational detail
(exact scoring, the pitch-marking text format, the recording format) lives in
[[70-drills/assessment-cadence]]. *(spec, 006 FR-022)*

**Weekly mora-count dictation.** One Irodori audio line, heard once, transcribed to kana with
no lookups, scored mora-by-mora against the source. A wrong mora count (long/short vowel
confusion, a dropped or inserted geminate っ, ん miscounted) and a devoicing miss (です/ます-
class environments, per the l1-profile) reuse the **existing** `mora-length` and
`devoiced-vowels` `log_error` pattern names — the same two E2/E4 already put in the pack, now
given a weekly checkpoint instead of an ad hoc one. Distinct from KANA's daily dictation
(Phase 0, agent-spoken, kana taught so far): this is post-Phase-0 and runs on real recorded
audio. Closes with the reserved slug `topic: "weekly-mora-dictation"`, the same mechanism
KANA's `phase0-kana-dictation` slug already established for letting a later gate count
artifact kinds mechanically rather than guessing from prose.

**Weekly five-word pitch-pattern marking.** Five words, kana only, marked with the `[0]`/`[n]`
drop notation already used in [[35-phonology/pitch-accent]] — text-only, no audio, no
synthesis anywhere in the loop. Checked against the vendored kanjium accent number via
`lookup`'s `pitch` field, never from memory. A mismatch carrying the l1-profile's predicted
length-creep tell reuses `mora-length`; a pitch-height-only mismatch has no existing pattern
to reuse honestly and stays in the session's observation note rather than being forced into
one. This is the exercise that eventually trips **F-02** (VOICEVOX TTS, deferred until
"minimal-pair perception training enters the curriculum" per spec.md's deferred-triggers
table) — until that fires it stays perception-only by design, not as a stopgap. Closes with
`topic: "weekly-pitch-marking"`.

**Monthly 60-second monologue.** Roughly 60 seconds, script visible or hidden per the
modality-ladder rung, saved under the vault at `80-progress/monologues/` as `.mp3`/`.wav` —
never under `local/` or `.derived/`, which the vault snapshot always skips. Backed up the
moment it lands, because `VAULT_SNAPSHOT_EXTENSIONS` already carries both extensions (FR-007,
widened in TG0/T005) — no follow-up task has to remember to widen anything. Trend is a
comparison, not a re-listen-and-guess: length in seconds and a disfluency count against the
prior month's numbers for the same slot, in the closing observation. Closes with
`topic: "monthly-monologue"`.

**Still open**: none of the three has a real logged instance yet — the fixture-level check
that they are actually measurable through existing tools, with no new tool or table, is a
separate task (006 T036). This section states the policy the fixtures will exercise, not a
claim that a week or month has already produced one.

## What v1 still does not know

- **The evidence base is two dated traces and one checkpoint**, not weeks of logs. v1's job
  is partly to make the next revision better-informed: the tool write path, the enforced
  observation fields and the pattern-named errors exist so that v2 can be rewritten from
  counts instead of from anecdotes.
- **Coverage is still an estimate.** D2's `coverage(text)` and grammar-reachability-gated
  `find_i_plus_one` will replace the agent's guess; until then the pack requires the estimate
  to be labelled as one.
- **No unassisted pass-rate series exists yet.** Once it does, the rules about hint
  escalation and band honesty become testable rather than asserted.
- **The canary set stays sealed** — probes only, never a session, in every mode.
