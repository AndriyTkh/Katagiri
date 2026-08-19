# Katagiri — OSS component survey

Date: 2026-08-18. Scope per post-round-3 decisions: personal project, English↔Japanese,
MCP server is the build ceiling, reuse OSS everywhere. Two research passes (NLP substrate +
reader/media ecosystem), consolidated. GitHub metadata (pushed_at/license/releases) verified
2026-08-18.

## The headline verdict

**Almost everything exists. The genuinely-build list is three items:**

1. **The MCP server itself** — known_set store + append-only event log + importers + text
   scoring. Thin glue over components below.
2. **The progressive substitution engine** — no usable OSS exists (GitHub diglot-weave search:
   only 1-star toys; commercial attempts closed). Small anyway: known_set ∩ aligned tokens →
   swap, on curated pre-aligned texts.
3. **Yomitan custom-dictionary generator** (~100 lines) — known_set → a Yomitan
   "frequency"-style dict that visually marks known/unknown words in every reading surface at
   once. Huge leverage per line of code.

**And one architectural decision: Anki owns scheduling.** Anki ships FSRS-6 natively (25.x),
auto-optimizes from your own review log, handles sync/fuzz/rescheduling edge cases a
reimplementation would get wrong for months. Every capture tool below (Yomitan, asbplayer,
mpvacious, Lute) already writes to Anki — it is the de facto event bus. The MCP mirrors Anki
state into known_set on a threshold rule (e.g., interval ≥ 21d or FSRS stability ≥ X ⇒ known),
merges non-Anki evidence (Lute statuses, exposure events, jpdb/WK imports), and owns the
*definition* of "known" — not the scheduler. py-fsrs (MIT, FSRS-6) is used only as a formula —
computing stability/knownness scores from exported review logs — never as a live scheduler.

## Recommended stack (one pick per slot)

| Slot | Pick | License | Why |
|---|---|---|---|
| Tokenization | **fugashi + full unidic-py** (Python) | MIT/BSD | Only pip-installable path with pitch accent (aType) per token. Full unidic ≈770MB — fine for personal server. `unidic-lite` has NO accent fields; Sudachi/lindera dictionaries lack them too. |
| Dictionary | **jmdict-simplified** → own SQLite | CC-BY-SA data, MIT tooling | Weekly automated releases, full sense-level JSON, TS types. JMdict_e_examp variant links Tatoeba examples. |
| Pitch accent fallback | **kanjium accents.txt** (~130k entries) | claimed CC-BY-SA, provenance unverified ([issue #13](https://github.com/mifunetoshiro/kanjium/issues/13) unanswered) | De facto OSS accent DB (Yomitan uses it). Personal use fine; never redistribute commercially. |
| Scheduler | **Anki + native FSRS-6**, read via **AnkiConnect** (:8765) | GPL addon, free | See headline verdict. `getReviewsOfCards`/`cardsInfo` give full state. |
| FSRS-as-formula | **py-fsrs v6.3+** (`pip install fsrs`) | MIT | Compute probabilistic knownness from review logs. FSRS-6, same-day review support. Don't mix v5 (19-param) and v6 (21-param) sets. |
| MCP framework | **official `mcp` Python SDK v2** (built-in FastMCP decorators) | MIT | v2.0.0 Jul 2026 — beware v1-era tutorials. Standalone FastMCP 3.x only if auth/composition needed. |
| TTS | **VOICEVOX engine** self-hosted (Docker/binary, CPU fine) | LGPL engine, MIT core, per-character EULAs | REST API; AudioQuery response exposes mora-level pitch — doubles as accent cross-check. Personal use unrestricted; credit "VOICEVOX:キャラ名" if audio ever published. Cache per-word WAVs by content hash. |
| Tap-gloss surface | **Yomitan** + **yomitan-api** (local HTTP :19633) | GPL-3.0 | Works on web/ttsu/mokuro. API exposes lookup/tokenize to external programs; asbplayer already consumes it. No lookup-event export — infer from Anki adds. |
| Video surface | **asbplayer** (streaming + local) / **mpvacious** (mpv) | MIT / GPL | asbplayer already renders known-word status synced from Anki/WaniKani — free display surface for known_set, zero fork needed. |
| Manga / books | **mokuro** (OCR→JSON→reader) / **ttsu reader** (EPUB) | GPL / BSD-3 | Both Yomitan-compatible today. `.mokuro` JSON = per-volume coverage/difficulty input for MCP. ttsu is maintenance-mode, fork-friendly. |
| Long-text reading tracker (optional) | **Lute v3** | MIT | Word status 1-5 in plain SQLite (`lute.db` words table ≈ known_set feed) + CSV + AnkiConnect export. Read its DB directly; no fork needed. Only earns a slot if per-word-status-while-reading wanted beyond Anki. |
| Anki MCP bridge | vendor **ujisati/anki-mcp** (broadest AnkiConnect surface) or nailuoGG's | check per-repo | ~10 wrappers exist; none model known_set — that stays ours. |
| Subtitles source | **Jimaku** (JSON API, AniList-keyed) | site AGPL; files copyright-gray | Kitsunekko successor. Personal use tolerated; never build anything public on it. |
| Curated parallel text | **Tatoeba eng↔jpn pairs** (weekly TSV) + **Aozora Bunko** | CC-BY / public domain | Tatoeba = pre-paired sentences, free substitution-engine seed. Aozora = derivative-safe corpus (old prose — test bed, not beginner content). |
| Alignment (offline, one-off) | **awesome-align** (dormant 2022, pin env) or LLM-assisted + manual review | BSD-3 | Texts are curated → alignment is a batch job, not a runtime dependency. WSPAlign (NTT) = SOTA EN-JA if quality matters. |
| Difficulty scoring | **jreadability** (pip) + BCCWJ frequency dict + tanos JLPT lists (CC-BY) | MIT / mixed | Combine with own known_set coverage % for "difficulty for me". |
| known_set seeds | AnkiConnect live; `.apkg` via `anki` pip package (new .anki21b = zstd protobuf — legacy export or official lib); **WaniKani v2 `/assignments`** (srs_stage — `/reviews` history is dead); **jpdb Settings → "Export vocabulary reviews (.json)"** (API key does NOT expose history) | various | Import-before-export, finally honored. |

## License flags

- **JParaCrawl — BLOCKED**: research-use-only, viral onto derived data. Not needed anyway.
- **Tadoku graded readers — CC BY-NC-ND**: ND forbids derivatives → cannot run substitution on
  them or redistribute modified text. Private use tolerated; don't build the pipeline on them.
- **kanjium** — soft flag (unverified provenance, likely commercial-dictionary-derived). Personal
  use fine, no commercial redistribution.
- **jpdb frequency dicts** — scraped, gray. Private input only.
- **Jimaku subtitle files** — fan rips, gray. Private use only.
- Everything else: MIT/Apache/BSD/CC-BY(-SA) — compatible with a personal open-source project.

## Notable non-existence findings

- **No personal Japanese-learning MCP with known_set + event log exists** (searched 2026-08-18:
  only Anki-CRUD wrappers, a Yomitan-API wrapper, a Jisho wrapper). The core is genuinely
  unbuilt — and small.
- **No usable diglot-weave OSS exists.** Both funded commercial attempts (Toucan, LoomVue)
  closed-source and pivoted/died.
- **kuromoji.js unmaintained** (~2018); lindera-wasm now a crate in the lindera monorepo — the
  client-side tokenization option if ever needed, but its UniDic 2.1.2 has no accent fields.
- **WaniKani `/reviews` endpoint is deprecated/404** — seed from `srs_stage`, not history.

## Architecture consequence (v4.1 personal, final shape)

```
                    ┌─────────────────────────────┐
  Yomitan ──┐       │   Katagiri MCP (Python,     │
  asbplayer ─┼─Anki─┤   official SDK v2)          │──← ChatGPT/Claude read
  mpvacious ─┘  ▲   │                             │    known_set/stats/due
  Lute (SQLite)─┼──►│  known_set + event log      │
  jpdb/WK seed ─┘   │  (SQLite, append-only)      │──→ Yomitan custom dict
                    │                             │    (visual known-marking)
  fugashi/UniDic ──►│  coverage / difficulty /    │
  jmdict-simplified │  substitution engine        │──→ curated reader output
  kanjium accents ─►│  (Tatoeba/Aozora, offline   │    (the one novel piece)
  VOICEVOX ────────►│   pre-aligned)              │
                    └─────────────────────────────┘
```

Build order: (1) MCP skeleton + Anki mirror + importers → (2) Yomitan known-dict generator →
(3) coverage/difficulty tools → (4) substitution engine on Tatoeba sentences → (5) VOICEVOX
cache. Steps 1-3 are all glue; the first novel line of code is step 4.

## Round-4 corrections (2026-08-18, verified — supersede table rows above where they conflict)

- **MCP framework**: python-sdk **v2.0.0 renamed `FastMCP` → `MCPServer`** and snake_cased the
  API; spec 2026-07-28 is the first breaking revision. "SDK v2 with FastMCP decorators" as
  written above is impossible. Pin `mcp>=2,<3`; tools = plain Python functions + one thin
  adapter file.
- **AnkiConnect**: GitHub repo **archived 2025-11**, canonical home sourcehut
  (`~foosoft/anki-connect`) which has **no bug tracker**; ~9mo stale. Mirror reads go directly
  against `collection.anki2` (read-only; revlog/cards schema very stable). AnkiConnect only for
  optional flagged writes (`addTags`, `setDueDate`) — **`answerCards` is banned** (pollutes
  collection-wide FSRS training data irreversibly; see audit-log round 4).
- **Known-threshold rule**: FSRS stability is **not readable via AnkiConnect** — use
  `ivl ≥ 21d`; py-fsrs recompute from `getReviewsOfCards` optional. Pin `fsrs<7`.
- **New pick — AnkiMorphs** (`mortii/anki-morphs`, AGPL, active): per-morph intervals in its own
  SQLite + known-morphs CSV export = a ready-made Anki mirror; remaining work is morph→JMdict
  mapping only.
- **`unidic` pip package**: no release since 2021; runtime ~1GB download breaks recurrently
  (403s). Install full UniDic once, vendor locally. Vendor kanjium `accents.txt` (repo silent
  26 months). `jreadability` deps on `unidic-lite` — fine, it doesn't need accent.
- **SQLite FTS5**: trigram tokenizer returns 0 rows for 1-2 char Japanese words (勉強 —
  verified); unicode61 doesn't segment Japanese. Required: fugashi-tokenized space-joined shadow
  column (unicode61) + trigram for raw substring, routed by query length.
- **Repo moves**: Yomitan → `yomidevs/yomitan`; asbplayer → `asbplayer/asbplayer`;
  mokuro-reader canonical → `Gnathonic/mokuro-reader` (ZXY101 = legacy); ttsu reader officially
  maintenance-only.
- **asbplayer external API exists** (docs.asbplayer.dev/docs/reference/external-api):
  `get-bound-media` + `get-subtitles` (ms timings) via local WS server :8766; **no playhead
  read** (upstream PR opportunity, issue #1087). Netflix breakage was fixed v1.19.0.
- **mokuro-reader** fires `mokuro-reader:page.change` CustomEvent (title/volume/currentPage) on
  every page turn — a ~10-line userscript bridges it to localhost. `volume-data.json` (Local
  Folder sync) = durable progress snapshot. `.mokuro` schema frozen since 0.2.0; never compare
  its `version` field.
- **mpv**: JSON IPC = most stable integration surface in this stack; use `python-mpv-jsonipc`
  (Windows named pipes) or a ~40-line Lua pusher (mpvacious pattern).
- **Obsidian mandate** (round-4 requirement): `coddingtonbear/obsidian-local-rest-api` v5.1+
  ships a **built-in MCP endpoint** — zero code. Front read-only; HTTP :27123 (not :27124 —
  Claude Code self-signed-cert bug); never expose `command_execute`. Reject
  MarkusPfundstein/mcp-obsidian (PyPI stale, pre-5.x PATCH format). Katagiri keeps its own
  markdown search over exported files so vault search survives Obsidian being closed.
- **Yomitan custom dicts**: no in-place update — regen = manual delete+reimport with versioned
  dict names; don't automate. yomitan-api = browser-launched native-messaging host, not a
  standalone server.
- **Lute**: no public API; read `lute.db` directly but beware 4 triggers on `words` and silent
  startup migrations.
- Build order superseded by audit-log **Round 4 synthesis — v4.2** (7 steps; substitution engine
  deferred post-loop, VOICEVOX deferred; teacher loop estimate 89-170h).
