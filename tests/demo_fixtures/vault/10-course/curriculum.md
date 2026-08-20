---
schema: 1
type: index
title: Curriculum DAG (demo fixture)
---

# Curriculum — demo fixture

Small synthetic grammar DAG for the specs/005-mcp-assignment demo profile.
Structurally the same shape as the real vault's `10-course/curriculum.md`
(parsed by `katagiri.intelligence.parse_curriculum` /
`import_curriculum`), trimmed to a handful of nodes so the fixture DB
build (`scripts/build_demo_db.py`) has real grammar rows to seed
reachability from — this is what lets `find_i_plus_one`'s grammar-DAG gate
(D-28) behave like a real vault instead of an empty one.

## Phase 1 — First sentences (demo subset)

```
g-desu-copula ──> g-wa-topic ──> g-o-object
                       │
                       └──> g-question-ka
```

```yaml
id: g-desu-copula
level: A0
---
id: g-wa-topic
level: A0
prereqs: [g-desu-copula]
---
id: g-o-object
level: A1
prereqs: [g-wa-topic]
---
id: g-question-ka
level: A0
prereqs: [g-desu-copula]
```

Vocabulary topics used by the fixture DB's seeded items: `food`,
`transport` — matching the two demo goal-note variants' `goal_theme`
values (`tests/demo_fixtures/vault/00-goals/`), so a `topic=` filter
passed through from the goal note's frontmatter actually narrows the
seeded pool instead of returning nothing.

(This is a synthetic demo fixture — not the learner's real curriculum.
The real vault's curriculum lives at `docs/katagiri/katagiri/10-course/
curriculum.md` and is never read by the demo profile.)
