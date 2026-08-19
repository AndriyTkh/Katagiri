---
schema: 1
type: meta
---

# 桐 Katagiri

A Japanese-learning system built as an **Obsidian vault first, app second**.

Priorities, in order:
1. **Speech and listening.** Reading is a support skill, not the goal.
2. **Media immersion** (YouTube, anime, manga, podcasts) over textbooks, as early as possible.
3. **Human-readable plain files.** Every fact about your Japanese lives in Markdown you can read, edit, and grep. The app is a *view* over these files, never the owner of them.

## The one rule that keeps this from breaking

> **Markdown is the source of truth. Everything else is derived and disposable.**

- Hand-curated content (topic files, grammar, media notes) → Markdown, committed to git.
- Review history → append-only event log (`60-review/reviews.jsonl`). Never edited, never rewritten.
- Machine-scale data (tokenized transcripts, dictionaries, frequency tables) and everything derived (scheduling state, indexes, coverage, cross-topic views) → `.cache/` and `.derived/`, gitignored, rebuildable with one command.

The boundary between the two is a single question: **would a human ever read this file?**

If you ever want to change SRS algorithm, redesign the app, or move to a different tool, you replay the event log. You lose nothing.

## Where things live

| Folder | What |
|---|---|
| `00-inbox/` | Raw dumps. Screenshots, half-remembered words, "what was that phrase". Zero structure required. Gets triaged. |
| `10-course/` | The curriculum DAG and dated lesson notes. |
| `20-vocab/` | **One file per topic.** Words, sentences and notes together — the readable "what do I know" view. |
| `30-grammar/` | One note per grammar concept, with an *understanding level* that goes up over time. |
| `35-phonology/` | Pitch accent, mora timing, minimal pairs, the sounds that don't exist in your L1. |
| `40-sentences/` | Generated cross-topic sentence index. Sentences themselves live in their home topic/grammar/media note. |
| `50-media/` | One note per episode / video / chapter, with timestamps. |
| `60-review/` | Append-only review log + your personal error museum. |
| `70-drills/` | Drill definitions: shadowing, dictation, dubbing, roleplay scenarios. |
| `80-progress/` | Weekly letters from your teacher. Read these, not streak counters. |
| `90-meta/` | Schemas, tag registry, MCP spec, scripts. The rules of the vault. |
| `local/` | Gitignored. Audio clips, subtitle files, media you don't own the rights to. |

## Start here

- [[CONVENTIONS]] — how to write a note so the tooling can read it
- [[ARCHITECTURE]] — why it's built this way, and the design decisions you're locked into
- [[ROADMAP]] — what gets built when
- [[MOONSHOTS]] — the things that are only possible because you own the data
- [[90-meta/decisions/0001-db-vs-markdown]] — why the DB isn't the source of truth
- [[10-course/curriculum]] — what to learn next
- [[90-meta/settings]] — script mode, kanji on/off, reading-optional
- [[90-meta/schema/topic-file]] — the one format you'll hand-write
- [[90-meta/mcp-spec]] — the API surface Claude Code will drive
