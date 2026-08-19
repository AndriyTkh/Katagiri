---
schema: 1
type: meta
---

# Review log

## `reviews.jsonl` — append-only. Never edit a line.

One JSON object per answered card:

```json
{"ts":"2026-08-18T07:41:02Z","item":"w-0001","direction":"listen_to_meaning","grade":3,"latency_ms":2840,"answer_given":"apple","expected":"apple","session":"s-0001","source":"app"}
```

| Field | Notes |
|---|---|
| `ts` | ISO 8601 UTC |
| `item` | permanent item ID |
| `direction` | `listen_to_meaning` \| `meaning_to_speech` \| `read_to_meaning` \| `cloze_production` \| `shadow` |
| `grade` | 1–4, see [[CONVENTIONS]] |
| `latency_ms` | recall speed — the real fluency signal for speech |
| `answer_given` | **what you actually answered.** Always record it. |
| `audio_ref` | for spoken answers, path in `local/recordings/` |
| `session` | groups a sitting |

### Why `answer_given` matters more than it looks

Pass/fail tells you *that* you failed. Your wrong answer tells you *why*. Accumulated, wrong answers form a confusion graph — you mix up 見る/観る, you hear こ as ご, you drop が in questions — and the system generates drills aimed precisely at those pairs. No commercial SRS app keeps this. It's one extra field and it's the most valuable data you will produce.

### Why append-only

Scheduling state is **computed** from this log, never stored in it. Which means you can change SRS algorithm, refit FSRS parameters to your own history, or rebuild the entire app, and recompute your full history from scratch. Your data is never hostage to your first guess at an algorithm.

## `errors.md` — the error museum

Curated, human-written, and worth more than any textbook you'll buy.
