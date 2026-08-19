---
schema: 2
type: meta
---

# Moonshots

Things that have not been done, as far as I know, and that are only possible because you will own a complete, queryable, plain-text record of your own learning — plus an LLM in the loop and audio on both ends.

Ordered roughly by ratio of insight to effort.

---

## 1. A held-out test set for your own brain

Every learning app measures you on the material it taught you. That measures memorization, not Japanese.

So: at the start, take 200 sentences at graded difficulty and **lock them away**. They are never studied, never reviewed, never shown. Once a quarter, you sit a blind test on a sample of them — listening comprehension, answered aloud.

That number is an unbiased measure of **transfer**: your actual Japanese, not your SRS retention rate. Nothing you do can game it, because you can't study the test.

Nobody does this. Not because it's hard — because it requires an entity that holds data back from you on purpose. A commercial app has no incentive to build the one metric that could show its own product isn't working.

Extension: keep the canary set in a separate committed file with a `sealed: true` flag and have `validate` scream if it's ever referenced by a drill. Sealed by tooling, not willpower.

## 2. A formal grammar of *your* Japanese, including its wrong rules

Second-language acquisition has a concept called **interlanguage**: the learner's speech isn't broken Japanese, it's a coherent internal system with its own rules, some of which are wrong. Fossilization is when a wrong rule stops being revised.

The theory is 50 years old and has never been operationalized, because it would require every utterance a learner ever produced. You will have exactly that: every spoken answer, every dictation, every conversation transcript, every `answer_given` field.

So the model reads the whole corpus and writes a **descriptive grammar of your interlanguage**:

> *You have internalized: を marks any noun immediately preceding a verb. This is correct in 87% of your usages and wrong in the 13% involving intransitive verbs, where you produce 「ドアを開いた」for 「ドアが開いた」.*

Then the vicious part: it **generates sentences that are correct only under your wrong rule**, shows you them, and lets you feel the rule break. You cannot revise a rule you don't know you have. This turns fossilization from something you discover in year five into something you get a report on in month three.

## 3. A personal audiogram for Japanese

Audiologists find your hearing threshold with an adaptive staircase — present a tone, louder if missed, quieter if caught, converge on the threshold. Same method works for phoneme discrimination.

From your dictation diffs and minimal-pair drills, build a **confusion matrix over Japanese phonology**: which contrasts you can actually perceive, and at what difficulty. Then synthesize audio sitting exactly at your threshold — the right speaker, the right speed, the right coarticulation — and walk the threshold outward.

Output is a chart: *you discriminate き/ぎ at 95%, こ/ご at 71%, and single/geminate consonants at 58% above 1.4× speed.* Now you know your bottleneck is a **perceptual** one, not vocabulary, which is the diagnosis nearly every stalled learner gets wrong. Vocabulary is what people study when their real problem is that they can't hear the difference.

## 4. Rewind telemetry — free comprehension labels

Every time you rewind while watching, you have generated a **timestamped, labeled comprehension failure**. Every pause. Every subtitle toggle.

That's the most valuable training signal in language learning and everyone throws it away. Capture it — a browser extension, a wrapped player, or just Claude in Chrome watching the player state — map the timestamp to the tokens in that window, and feed it into comprehension debt automatically.

Concrete path (2026-08): the mpv Lua pusher built for the media context channel already streams playhead position. Seek-backward events are a property change away — logging them into the event log is nearly free once that channel exists. asbplayer (streaming) exposes no playhead, so telemetry is mpv/local-files only at first.

You get an ever-sharpening model of what actually blocks you, from doing the thing you were going to do anyway. Zero added effort. This is the single highest ratio of value to work on the whole list.

## 5. The show that is written for exactly one viewer

Stop *selecting* comprehensible input. **Commission it.**

A serialized audio drama, released two episodes a week, generated with a hard constraint: only words in your known set, plus 2–3 new ones per episode, plus recurring characters, an ongoing plot, and complexity that rises exactly as fast as you do. Real voices. Cliffhangers.

Every existing approach fails one half of the problem: textbook audio is comprehensible and boring, real anime is interesting and incomprehensible. Nobody has been able to do both, because writing bespoke serialized fiction against a per-learner vocabulary constraint required a writer per learner. Now it doesn't.

The affective hook does the work that discipline was doing. You'll want to know what happens, which means you'll listen four times, which is spaced repetition you didn't have to schedule.

## 6. Hearing yourself speak fluent Japanese

Clone your voice. Generate your target sentences — with correct pitch accent, correct mora timing, native prosody — **in your own voice.**

Speech therapy has known for decades that self-modeling (hearing/seeing yourself performing correctly) beats hearing a model you don't identify with. Nobody has applied it to L2 prosody because voice cloning wasn't available and pitch-accent annotation wasn't automatable. Both now are.

Slightly uncanny. Probably the most effective single item here, because the thing standing between you and native prosody is a belief that your mouth doesn't do that.

## 7. Semantic gap analysis — find the holes, not the gaps

Embed every word you know. Cluster. Now overlay the same embedding space for a native-level vocabulary and look for **regions where Japanese is dense and you are empty**:

> *You have solid vocabulary for anger and for happiness. You have nothing for the embarrassment/awkwardness cluster, where Japanese has at least six distinguishable words — 恥ずかしい、気まずい、ばつが悪い… You are currently unable to describe the most common social emotion in Japanese life.*

Frequency lists can't find this. Frequency is one-dimensional and semantics isn't. Only possible with embeddings plus a complete record of your vocabulary.

## 8. Anomalous decay means bad encoding, not insufficient review

Every SRS responds to forgetting by scheduling more repetitions. That's the wrong treatment for a large class of failures.

Fit a forgetting curve **per item**. Some items decay far faster than your personal baseline predicts. Those aren't under-reviewed — they're **badly encoded**: no hook, no context, no image, learned from a list. Repetition doesn't fix bad encoding, it just costs you more.

So: detect anomalous decay statistically, and **prescribe re-encoding instead of re-review** — go mine a real scene containing that word, build a mnemonic, use it in a conversation tonight. Then watch whether the decay constant changes. That's a closed-loop diagnostic, and you can only run it with a per-item longitudinal record.

## 9. Your own N-of-1 randomized trials

Once FSRS is fitted to your log, you have a **predictive model of your memory**. That means you can simulate. Run twelve virtual weeks of "30 min speaking vs 30 min dictation" and compare projected retention before living either one.

Better: actually run it. Alternate two protocols by week, measure with the canary set (§1), and get a real effect size for *you*. Self-experimentation with an actual control condition. Everyone in the language-learning community argues about methods on the basis of anecdote. You'd have data on the only subject you care about.

## 10. The register ladder

Take one mined scene. Have the model produce the same content at five politeness levels — rough, plain, polite, humble, keigo — same meaning, register as the only variable.

You cannot buy this. It requires generating parallel text on demand. It's a **controlled experiment in social language**, and it does in ten minutes what usually takes years of embarrassing yourself: it isolates register as a perceivable dimension. This is also the specific antidote to learning from anime.

## 11. An L1 interference profile

Your first language determines which Japanese contrasts are hard, and it's predictable from phonology. English speakers wreck ら because English has no flap in that position — but a Polish or Spanish speaker's tapped r is already nearly correct, and their problem is vowel length instead, since their language doesn't distinguish it. Mandarin speakers arrive with pitch already active but the wrong system for it.

So the phonology drills shouldn't be generic. Tell me your L1 (and any other languages) and the drill set gets built against your specific predicted interference — plus, more usefully, the list of things that are *easy for you* and that generic courses will waste your time on.

## 12. The vault that stops speaking English

A `sensei_language` ladder — `en` → `simple-jp` → `jp-only` — with a scheduled, tracked transition. Then the final step: rewrite your own month-2 notes in Japanese at month 8, in a `shadow/` view.

Your study materials become your study materials. And rereading your own early notes rewritten by a later you is a more honest progress measure than any number.

---

## What all of these have in common

None require a model breakthrough. Every one requires **owning a complete, structured, longitudinal record of one person's learning** — which is precisely what no commercial app will ever give you, because that record is the product they're selling access to.

That's the actual reason to build this yourself. Not that the apps are bad. That the interesting things are only possible on the other side of the data.
