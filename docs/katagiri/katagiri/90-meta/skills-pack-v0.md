---
schema: 2
type: meta
title: Skills pack v0
created: 2026-08-19
---

# Skills pack v0

The zero-code start. Before the Katagiri MCP server exists, study happens manually with
Claude, and the *method* lives on the prompt side as a skills pack. This page is the
vault-side record of what v0 says; the executable copy is
[`.claude/skills/katagiri-study/SKILL.md`](../../../../.claude/skills/katagiri-study/SKILL.md)
at the repository root.

## The four behaviors

1. **Guess-first** — no answer before an attempt. New material is approached through
   context, kanji components, and cognate shape; hints escalate rather than resolve. A
   wrong guess is logged material that gets explained, never silently corrected away.
2. **Coverage gate** — before presenting any text, estimate the known-word share and aim
   for i+1 (one unknown per sentence). More than two unknowns means simplify or pre-teach.
   Learner state lives in `20-vocab/` (the `✓` column) until the database exists.
3. **Mining budget** — at most 5 new items per session become cards. Overflow goes to
   `00-inbox/` as one-line dumps. One trustworthy anchor sentence outranks five thin
   items.
4. **Nuance-anchoring** — every new card carries a natural anchor sentence, a register
   note (casual/polite/formal), pitch accent when known, and an explicit contrast against
   the already-known rival word.

Session shape is warmup review → new material → shadowing → close. Pronunciation targets
come from the drill priorities in [`35-phonology/l1-profile.md`](../35-phonology/l1-profile.md).
The canary set in `90-meta/canary/` is sealed and never enters a session.

The close step is mandatory: append a session line via `scripts/log_study.py` (schema in
[`60-review/study-log.md`](../60-review/study-log.md)) and, when items were graded, record
per-item lines in `60-review/reviews.jsonl`.

## Why v0 is deliberately thin

v0 is a hypothesis, not a curriculum. It encodes only the four behaviors that are already
known to matter and leaves the rest unspecified so that friction shows up in the logs
instead of being designed around in advance.

**v1 (Phase D4)** will be an evidence-driven revision: rewritten from the accumulated
`60-review/study-log.jsonl` notes, `reviews.jsonl` answer history, and `errors.md`, so
each changed rule points at a recorded failure rather than an intuition.
