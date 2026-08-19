---
schema: 2
type: meta
adr: 0001
status: decided
date: 2026-08-18
---

# ADR 0001 — DB as source of truth, Markdown as generated view

**Proposal:** SQLite authoritative for consistent IDs and cross-topic relations. Human reads generated Markdown in Obsidian. Model reads/writes via MCP, vault as fallback.

**Decision:** Rejected for hand-curated content. Adopted for machine-scale data (already the case) and adopted in one respect: **single-writer discipline via MCP.**

---

## Where it's right

1. **Single writer through MCP is correct** and I'm adopting it. Two writers on text files (you in Obsidian, the model via file edits) is a real drift risk. From now on: the model writes *only* through `katagiri-mcp`, never by editing files directly. It formats, validates, and commits atomically. Direct file writes by the model are a bug.
2. **Relations across topics are genuinely awkward in flat text.** `Also` columns, grammar prereqs, media provenance, sentence→word edges. Real problem, needs a real answer (below).
3. **Renames and refactors are painful in text.** Also real.
4. For transcripts, dictionaries, frequency tables, coverage matrices — DB authority is already the design. No human reads those.

## Why it fails as the authority for content

### 1. Git stops working, and git is your review layer for an LLM

SQLite in git is a binary blob. You lose `git diff`, `git blame`, line-level history, merges, and — critically — **the ability to review what the model wrote.**

An LLM is going to be the primary author of your vault. Thousands of edits over years. Some of them will be wrong: a mis-transcribed pitch accent, a hallucinated nuance, a register tag that's subtly off. With Markdown you review a diff before committing and catch it in seconds. With a DB you get "the database changed" and a trust-me. Six months in, a wrong pitch accent you never noticed is a fossilized error and you cannot bisect to find when it entered.

This is the argument that actually decides it. Not purity — auditability. Plain text is how you keep a fallible writer accountable.

### 2. "Human only reads it" is false, and every generated-view system dies here

You will annotate. You'll be looking at 水 and type *"the one from that ramen video"* in the margin. That is the single most valuable content in the whole vault — it's the personal hook that makes the word stick — and in your design it gets destroyed on next regen.

So you'd add edit-detection and round-tripping. That's a bidirectional sync layer, and it becomes the project. Every system that has tried "generated file that humans also touch" has landed in one of two places: a read-only view that frustrates people into working around it, or a sync layer that consumes all the engineering budget. There's no third outcome.

### 3. Referential integrity is *wrong* for a learning vault

This is the part that looks like a DB win and isn't. You want FK constraints to guarantee that every sentence references real words.

But a learner's most valuable notes are dangling references. A word you heard in episode 3 and can't identify yet. A grammar pattern you've noticed but not learned. A sentence with a `[?]` pitch accent. A question with no answer. Strict integrity forbids exactly the half-formed state that *is* learning. You'd spend your time satisfying the schema instead of recording what happened to you.

Text plus `validate` gives the right guarantee: integrity checked at commit time, warnings not errors, and unresolved things allowed to sit unresolved. That's not a weaker version of FK constraints, it's a more appropriate one.

### 4. The model gains nothing from DB authority

There is no query you can run against an authoritative DB that you can't run against a derived one built from text. Zero. The model's structured-query needs are fully served either way — and LLMs read Markdown natively, so the fallback path is *better* with text, not worse.

DB authority buys the model nothing and costs you §1–3.

### 5. Durability

Five years from now, a folder of Markdown opens in anything. A SQLite file whose schema lived in a dead app is an archaeology project. This is your language-learning history — the one thing here that's actually irreplaceable.

## The actual answer to "consistent IDs and relations"

Both problems are solvable without moving authority.

**Consistent IDs** — already solved by content-hashing (ADR: ARCHITECTURE §1.3). `w-` + `sha1("林檎|りんご")[:6]`. Consistent across machines, no allocator, no coordination, no DB. A DB gives you *sequential* IDs, which are not more consistent, just more opaque.

**Relations** — the fix is to stop treating the derived DB as a search index and start treating it as a **materialized relation graph**. Every edge worth querying gets extracted at build time:

```
word ──appears_in──> sentence ──demonstrates──> grammar
  │                     │
  │                     └──mined_from──> media@timestamp
  ├──also_in──> topic
  ├──confused_with──> word        (from reviews.jsonl answer_given)
  ├──contains──> kanji ──shares_component──> kanji
  └──errored_in──> error_entry
```

Query that graph freely. It has every relational property you wanted — joins, transitive closure, integrity reports — and it's rebuilt from text in seconds. The only thing it lacks is authority, which is the thing you don't want it to have.

## Reframe: git already *is* the database

Content-addressed storage, append-only history, atomic transactions (commits), branching, merge conflict detection, and full audit trail. It is a database. It's a worse one for querying and a much better one for a corpus authored jointly by a human and a fallible model over several years.

So: **git for authority and history, SQLite for queries, Markdown for humans.** Each doing the thing it's actually good at.

## Consequences

- Model writes only via `katagiri-mcp`. Direct file edits by the model are a bug.
- Every MCP write returns a diff. You review before it commits.
- `.derived/relations.db` becomes a first-class artifact with its own schema doc, not an implementation detail.
- `validate` reports integrity as warnings, never blocks on unresolved items.
- Revisit this ADR if the vault exceeds ~20k words or if a real multi-user case appears.
