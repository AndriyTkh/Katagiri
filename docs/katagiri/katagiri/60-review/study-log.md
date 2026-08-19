---
schema: 2
type: meta
title: Study session log
created: 2026-08-19
---

# Study session log

`60-review/study-log.jsonl` — **append-only**, one JSON object per line, UTF-8, `\n`
terminated. Never edit or reorder a line.

This is the **session** log: one line per sitting, coarse-grained, about time and shape.
It is distinct from [`reviews.jsonl`](./README.md), which records one line per *answered
item* with grade, latency, and `answer_given`. A session produces one study-log line and
zero-to-many reviews lines. Neither substitutes for the other.

## Line format

```json
{"ts":"2026-08-19T18:20:11Z","type":"study_session","minutes":35,"activities":["review","new_material","shadowing"],"items_mined":4,"notes":"stalled on 見る/観る; long vowels still short in shadowing"}
```

| Field | Type | Notes |
|---|---|---|
| `ts` | string | ISO 8601 UTC, `Z`-suffixed. Session end. |
| `type` | string | Always `"study_session"` — the log is single-purpose today, and the tag keeps it mergeable into the unified event log later. |
| `minutes` | int | Wall-clock minutes actually spent. |
| `activities` | array | Subset of `review`, `new_material`, `shadowing`, `listening`, `reading`, `conversation`. Order is not significant; no duplicates. |
| `items_mined` | int | Items that actually became cards. Inbox one-liners do **not** count. Capped in practice by the 5-item mining budget. |
| `notes` | string | Free text. Record **friction**: where recall stalled, which confusions surfaced, what felt unsustainable. This field is the raw material for the v1 skills-pack revision. |

## How lines get written

Manual or scripted, no service involved:

```bash
python scripts/log_study.py --minutes 35 \
  --activities review,new_material,shadowing \
  --mined 4 --notes "stalled on 見る/観る"
```

The script validates the activity names, stamps `ts` in UTC (overridable with `--date`),
appends exactly one line, and echoes it to stderr. Hand-appending a line is equally valid
as long as the shape matches.

## Lifecycle

Manual/scripted until **A5**, which imports this file into the unified event log. At that
point the file becomes an *importable source*, not the primary store — but it stays
append-only and stays readable, so the whole study history survives any rebuild of the
app. Nothing downstream should ever rewrite it in place.
