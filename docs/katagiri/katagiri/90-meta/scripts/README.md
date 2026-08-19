---
schema: 1
type: meta
---

# Scripts / CLI

The CLI and the MCP server are two faces of the same core library (`katagiri-core`). Never duplicate logic between them.

## Planned commands

```bash
katagiri build                  # rebuild .cache/katagiri.db from markdown + reviews.jsonl
katagiri validate [--fix]       # schema lint — run before every commit
katagiri rebuild-indexes        # regenerate category MOCs and tables
katagiri drill [--categories] [--direction]
katagiri due
katagiri coverage <url|file>
katagiri add-vocab <lemma>
katagiri export --format anki
katagiri stats
```

## Build order (do them in this order)

1. **Parser + schema validation.** Markdown → typed objects. Nothing works without this.
2. **`known_set`.** The core primitive; everything personalized depends on it.
3. **Review log reader + FSRS scheduler.**
4. **Tokenizer** (fugashi + UniDic) → enables coverage, i+1, media ingest.
5. **`coverage`.** The highest-leverage single feature in the project.
6. **Index generation.**
7. **MCP server** wrapping 1–6.
8. Everything else.

## Dependency notes

- fugashi + unidic-lite for tokenization (`pip install fugashi unidic-lite`)
- py-fsrs for scheduling
- JMdict / KANJIDIC2 / KRADFILE downloaded into `.cache/dict/` (gitignored — they're large and freely re-fetchable)
- yt-dlp, ffmpeg, faster-whisper for the media pipeline
- Keep dictionary data out of git. It's not yours and it's big.

## Pre-commit hook

```bash
katagiri validate || exit 1
```
