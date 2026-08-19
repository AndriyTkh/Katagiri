# Quickstart: Phase C validation (C-verify)

Mirrors gate bead `kata-cvf`. Fixtures only.

## Prerequisites

- B-verify green.
- Fixture vault (markdown notes with frontmatter, Japanese + mixed content) in `tests/fixtures/`.
- **Obsidian closed** for the markdown-path scenario.

## Steps

```bash
uv run pytest tests/test_md_search.py -ra
```

```bash
uv run pytest tests/test_cverify.py -ra
```

## Expected outcomes

1. Same fixture question answered via `search_db` (state view) **and** via markdown search (prose view).
2. Markdown path succeeds with Obsidian fully closed.
3. Editing one fixture note → incremental run re-indexes only that file (log assertion); deleted note produces no ghost hits.
4. Frontmatter fields queryable separately; malformed frontmatter non-fatal.
5. Cumulative: scenarios A..B still green.

## Learner metric (manual, from event log)

≥4 study days/week during the phase; ≥5 of last 7 days show events from this phase's tools once shipped.
