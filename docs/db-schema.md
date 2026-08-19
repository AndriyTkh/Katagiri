# Database schema

One SQLite database, at `db_path` from `config.toml` (default
`%LOCALAPPDATA%\Katagiri\katagiri.db`). All DDL lives in
`src/katagiri/migrations/0001_init.sql`; the runner is `src/katagiri/db.py`.

Schema version is `PRAGMA user_version`. `db.migrate()` discovers packaged
`NNNN_*.sql` files, applies each pending one inside its own transaction, stamps
`user_version` in that same transaction, and takes a `VACUUM INTO` snapshot into
`<config dir>/backups/<db stem>.pre-migrate-<version>.bak` first whenever the
database is not brand new. Backups sit beside the *config*, not beside the
database, so a database living on a scratch or synced volume cannot take its own
safety net down with it. If a migration fails, the raised `MigrationError` names
that snapshot and carries it as `.backup`.

Migration scripts own no transaction state. `db.discover_migrations()` refuses to
load a script containing a statement-level `BEGIN`, `COMMIT`, `END`, `ROLLBACK`,
`VACUUM`, `SAVEPOINT`, `RELEASE`, `ATTACH`, or `DETACH`, or any mention of
`user_version` — a stray `COMMIT` would close the runner's transaction early, so
the rest of the script and the version stamp would land outside it and a
half-applied migration would be recorded as complete. The check is statement-level
because `BEGIN`/`END` are also legal inside trigger bodies and `CASE` expressions,
both of which this schema uses.

A migration that must rebuild a table in place cannot disable foreign keys:
`PRAGMA foreign_keys` is a **silent no-op inside a transaction**, and every
migration runs inside one. Such a script declares `PRAGMA defer_foreign_keys = ON;`
instead — settable mid-transaction, holds enforcement until COMMIT, resets itself
afterwards — so references may dangle mid-migration but a broken graph still
cannot commit.

`migrate()` also refuses to run when `user_version` exceeds the newest migration
the build ships: a database already upgraded by a newer Katagiri must not be
written to by an older one that misreads its schema.

## The two classes of table

**SOURCE-OF-TRUTH** tables hold facts only the learner can produce. They are
never regenerated. Changing their shape requires a new numbered migration, and
data must be carried across.

**DERIVED** tables hold anything reconstructible from an external source (Anki
collection, JMdict, kanjium, subtitle files) or from a fold over the
source-of-truth tables. They are evolved by **drop-and-rebuild scripts, never by
migrations** — a schema change to a derived table means dropping it and
re-importing. Their initial DDL still lives in `0001_init.sql` so that a fresh
database is complete and usable in one step.

### Rebuilding a derived table

**A rebuild must run inside a single transaction.** The `known_set` and
`lesson_outcome` views read derived tables (`anki_cards`, `anki_item_map`); a
drop-and-recreate performed in autocommit leaves a window in which any query
touching those views fails with `no such table`. One transaction makes the swap
atomic — readers see the old shape or the new one, never the gap. There are 22
derived tables and all of them are droppable with `foreign_keys = ON` (verified by
`tests/test_db.py::test_derived_tables_are_all_droppable_under_foreign_keys_on`,
which derives which FTS shadow tables to exclude from a `FTS_SHADOW_PREFIXES`
constant rather than hard-coding the sentence-search prefixes).
Drop FTS indexes before the `sentence_text` content table they read. `fts_md_words`
/ `fts_md_tri` carry no such ordering rule — they are self-contained, not
external-content, tables (see the markdown-search row below).

Consequences encoded in the DDL:

- No derived table has a foreign key pointing at another derived table.
  `foreign_keys = ON` would otherwise make `DROP TABLE` fail during a rebuild.
- Derived → source-of-truth foreign keys are fine and are used where the parent
  is stable (`sub_lines` → `media`).
- Cross-boundary links that a rebuild recomputes are *soft references*: a plain
  `TEXT` column with no FK (`item.lexeme_ref`, `morph_lexeme_map.item_id`,
  `anki_item_map.item_id`).

Two hard rules hold everywhere: **no scheduler state** (`next_due`, ease,
intervals) outside the Anki mirror — Anki owns scheduling and strength is
computed from events — and **no strength/frequency/debt columns in
source-of-truth tables**; those live in derived caches. A test enforces the first
by substring, so a column merely *hinting* at scheduling (`due`, `ease`,
`interval`, `ivl`) fails the suite outside `anki_*`.

## Timestamps

Every timestamp column is ISO-8601 UTC to whole seconds — exactly
`YYYY-MM-DDTHH:MM:SSZ`, 20 characters — enforced by a `GLOB` CHECK on all 16 of
them. The fixed width is the point: these columns are compared and sorted
lexicographically (the `lesson_outcome` window, `MAX(ts)` in `known_set`), and
mixing in fractional seconds silently breaks that ordering, because `'...:00Z'`
sorts *after* `'...:00.5Z'`. Sub-second ordering comes from the ULID primary keys
instead. Day keys (`event.day_key`, `lesson.revisit_after`) are local `YYYY-MM-DD`.

## SOURCE-OF-TRUTH

| Table | Purpose |
| --- | --- |
| `event` | Append-only log of everything that happened (reviews, sessions, marks, seeks, mining, undo). The one true history. |
| `observation` | Append-only rubric-scored performances: what was expected, what was produced, under what conditions. |
| `lesson` | One row per lesson: objective at open, next step at close, revisit date, capped free notes. |
| `lesson_unresolved` | One row per question served during a lesson but not answered on the spot; `resolved_ts` closes it. |
| `lesson_media` | Which media a lesson used (many-to-many with `media`). |
| `item` | The studied unit — word, kanji, grammar point, or sentence — with its linguistic attributes and study policy flags. |
| `item_edge` | The grammar DAG and unlock relations, as `prereq` / `unlock` edges rather than an array column. |
| `alias` | Retired id → canonical id, so a rename never breaks an old reference. |
| `manual_marks` | Learner's explicit known / unknown / suspect verdicts. Overrides the Anki mirror in the known set. |
| `media` | Things studied from: episodes, podcasts, audiobooks, with transcript path and subtitle offset. |
| `media_heartbeat` | Single row: what is on screen right now (media, position, displayed line). Liveness is derived from the row's age. |
| `settings` | Key/value configuration, keyed by scope so a topic can override `global`. |

## DERIVED

| Table | Purpose | Rebuilt from |
| --- | --- | --- |
| `metadata` | Version registry: tokenizer version, dictionary versions, protocol version, current rubric version. | The importers that write each artefact |
| `lexeme` | Dictionary-side identity at sense granularity, anchored on `jmdict_seq` + `sense_idx`. | JMdict import |
| `morph_lexeme_map` | Crosswalk from studied `item` to `lexeme`, with the surface form and method that produced the link. Filled by A4c. | Morphological analysis |
| `anki_cards` | Mirror of Anki cards: interval, due, reps, lapses, plus `queue` and `ctype` (Anki's `cards.type`). Read-only; Katagiri never schedules. | Anki collection |
| `anki_notes` | Mirror of Anki notes: model, fields JSON, tags. | Anki collection |
| `anki_item_map` | Which Katagiri `item` a mirrored note stands for; resolved at mirror time. | Anki collection + item table |
| `mirror_meta` | Single row: when the mirror was taken, the collection mtime / Anki schema version it was taken from, and `crt` — the collection's day-zero epoch second. | Anki collection |
| `jmdict_entry` | One row per JMdict `ent_seq`, with commonness and the import's dict version. | jmdict-simplified |
| `jmdict_kanji` | Kanji spellings per entry, with upstream priority tags. | jmdict-simplified |
| `jmdict_reading` | Readings per entry, with upstream priority tags. | jmdict-simplified |
| `jmdict_sense` | Senses per entry: part of speech, English gloss, usage tags. | jmdict-simplified |
| `pitch_accent` | Surface + reading → accent notation. | kanjium |
| `sentence_text` | Content table behind both sentence FTS indexes: raw Japanese, segmented shadow text, and the versions that produced them. | Sentence items |
| `fts_sentence_words` | FTS5 `unicode61` index over the space-segmented shadow text — word matching. | `sentence_text` |
| `fts_sentence_tri` | FTS5 `trigram` index over raw Japanese — substring matching. | `sentence_text` |
| `md_note` | One row per vault markdown file: path (unique, vault-relative, POSIX separators), title, `generated` flag for `.derived/` output, frontmatter JSON + ok/error, size/mtime/hash freshness triple, body char count, version stamps, `indexed_ts`. | Vault files, walked by `md_search.py` |
| `md_frontmatter` | Frontmatter exploded to rows so fields are queryable separately from body text: `note_rowid` / `key` / `idx` / `value`. Keys lowercased at index time; NOCASE index on `(key, value)`. | `md_note`'s source files |
| `fts_md_words` | FTS5 `unicode61` index over the fugashi-segmented shadow text of each note's title + body — word matching. Self-contained, not external-content (deliberate divergence from the sentence pair): the index is updated one edited file at a time and deletes by rowid instead. | Vault files |
| `fts_md_tri` | FTS5 `trigram` index over each note's raw title + body — substring matching. Also self-contained. | Vault files |
| `sub_lines` | Subtitle lines with start/end times, for "what was said around this moment" window queries. | Transcript files |
| `coverage_cache` | Known-token ratio and comprehension band per media / episode / sentence / topic. Folds over the `known_set` view plus `sub_lines` / `sentence_text` token counts, so it is stale whenever either side changes. | `known_set` + `sub_lines` |
| `item_stat_cache` | Per-item strength, comprehension debt, frequency rank, review count. | Fold over `event` / `observation` |
| `ankimorphs_morphs` | Per-morph knowledge from AnkiMorphs: lemma, inflection, and the highest learning interval of each. PK `(lemma, inflection, source)`. | AnkiMorphs add-on database + known-morphs CSV export |

`md_note`, `md_frontmatter`, `fts_md_words`, and `fts_md_tri` are the inverse
case: their DDL lives **only** in `0001_init.sql`, never re-declared elsewhere,
per D-27 — this module rebuilds rows, never schema. `md_search.rebuild_md_index()`
walks the vault and repopulates all four inside one transaction; nothing else
writes to them.

`ankimorphs_morphs` is **not** in `0001_init.sql` — it is created on first use by
`src/katagiri/ankimorphs_ingest.py` (`CREATE TABLE IF NOT EXISTS` inside the same
transaction as the rebuild), which is what the drop-and-rebuild rule above allows
for a derived table whose shape must not be pinned by migration history. `source`
is `'db'` or `'csv'` and is part of the primary key because the two inputs
disagree — a CSV is a hand-made export, the database is live — and each ingest
rebuilds only its own source's rows. It carries no foreign keys: nothing links a
morph to an `item` yet, because that needs the morph → lexeme → item chain in
`morph_lexeme_map` (A4c). Until then this table feeds reporting only, not
`known_set`. Its `lemma_ivl` / `inflection_ivl` are mirrored AnkiMorphs values,
read-only exactly as `anki_cards.ivl` is — Katagiri still schedules nothing.

`anki_cards.queue` / `anki_cards.ctype` and `mirror_meta.crt` are **wider than
what `0001_init.sql` creates**, for the same reason and by the same route: B1
needed an exact due count, and the drop-and-rebuild rule reserves numbered
migrations for source-of-truth shape changes. `anki_snapshot.ensure_mirror_shape`
drops and recreates either table when it is missing a column the writer now
fills, and the DROP and CREATE are always one transaction so the `known_set`
view never sees the gap: called from the snapshot it joins the snapshot's own
transaction, and called on its own (to make the columns exist before a query
needs them) it opens a `BEGIN IMMEDIATE` of its own and commits or rolls back.
Either way it re-reads which tables are stale *after* the write lock is held, so
a rebuild that another snapshot already finished in the meantime is not redone
over that snapshot's fresh rows. A mirror taken before those columns existed therefore has *no* due
count rather than a guessed one, and says so until the next snapshot runs —
`katagiri.today_export.anki_due_count` reports `available: false` with a reason.
`crt` is what makes the rest usable: Anki stores a review card's `due` as a day
index from the collection's day zero, and `crt` is the epoch second that index
counts from. Turning it back into a date is **not** `(now - crt) // 86400`. Anki
does not measure elapsed days from the clock time the collection happened to be
created at; it re-bases `crt` onto the rollover boundary of `crt`'s own local
calendar day (`col.conf["rollover"]`, 4 a.m. by default) and counts whole days
from there, so the day index increments at the local rollover hour rather than at
some arbitrary minute inherited from creation time. The naive division is a day
*behind* for the whole stretch between the rollover and the creation hour — a
collection made at midday hides every card that came due at 04:00 until noon,
which is the one time of day the learner most needs the count. `rollover` itself
lives in the `col.conf` JSON blob, which the snapshot does **not** mirror;
`katagiri.today_export.collection_day_index` therefore assumes Anki's default of
4 and says so, and mirroring `col.conf` is the honest fix. The per-deck daily
limits (`perDay` in a deck's configuration group) are unmirrored for the same
reason, which makes `anki_due_count` **scheduler-raw**: on a backlog it reports
every card whose scheduled day has arrived where Anki's deck list offers only that
deck's daily cap, so 300 here can be 100 there. The rendered section says so
rather than applying a limit the mirror does not carry. All of it is mirrored,
none of it is owned: Katagiri reads Anki's scheduling and still schedules nothing
itself.

### Views

| View | Purpose |
| --- | --- |
| `lesson_outcome` | A lesson's outcome, computed: observation and item counts, unassisted count, unresolved served vs still open. Outcome is never stored as prose. |
| `known_set` | `item_id`, `is_known`, `source`, `suspect`, `manual_mark`. Anki mirror rule (`ivl >= 21` days) overridden by the most recent manual mark. |

Views are derived by definition; a rebuild script may drop and recreate them —
inside one transaction, per the rule above.

`lesson_outcome` joins observations to a lesson temporally, **inclusive of
`closed_ts`**: an observation recorded in the same second the lesson closed
belongs to that lesson. The join is additionally narrowed to the lesson's
`session_id` when one was recorded.

`known_set` has three deliberate properties:

- **`suspect` never decides `is_known`.** A suspect mark is a flag for review, not
  a verdict, so the mirror still decides knownness and the flag is exposed as its
  own column. `is_known` and `source` branch on the *same* predicate
  (`mark IN ('known','unknown')`), so they can never disagree about who decided.
- **`source` is `'manual'` only for known/unknown marks**, `'anki'` otherwise —
  including when no mature card exists, since the mirror is still what decided.
- **Rows exist for mark-only ids.** The view selects from
  `item UNION manual_marks.item_id`, so marking an id that has not been imported
  yet does not silently drop the mark.

## Column rationale (non-obvious only)

Everything not listed here means what its name says.

### `event`

- **`id` is a ULID, not an integer.** Time-sortable and generated client-side,
  so a batch can be assembled offline without a round trip for ids.
- **`dedupe_key` (nullable, UNIQUE)** — a retried submit collapses onto the same
  row instead of double-counting a review. Nullable because most events have no
  natural dedupe identity.
- **`ts_device` *and* `ts_server`** — device clocks drift and get corrected;
  keeping both makes it possible to detect that rather than silently trusting
  one. `tz` records the zone in force so a past local time stays reconstructible
  after the learner travels.
- **`day_key` (local `YYYY-MM-DD`)** — stored, not computed. Streaks and daily
  rollups are local-calendar questions, and recomputing them from UTC + a
  historical zone on every query is both slow and fragile.
- **`type` has no CHECK constraint.** The vocabulary is open (`review`,
  `review_batch`, `study_session`, `mark_known`, `regen_yomitan`,
  `lesson_close`, `seek`, `mining`, `tombstone_session`, …) and a new tool must
  not require a migration to log its own events. `direction` and `grade` *are*
  constrained, because those are scoring inputs.
- **`item_id` has no foreign key.** An event may reference an id that was later
  renamed (resolve via `alias`) or an item that no longer exists. History must
  not be rewritten to satisfy a constraint.
- **`answer_given`** — deliberately not `answer`: the column holds what the
  learner produced, which is often wrong. `expected` holds the target.
- **`payload` is JSON (`json_valid` enforced)** — per-type extras without a
  column per event type. Never tokens, credentials, or secrets.
- **Undo is `type = 'tombstone_session'`, not a delete.** BEFORE UPDATE and
  BEFORE DELETE triggers `RAISE(ABORT, 'event log is append-only')`. A mistake
  is corrected by appending a correction, so the record of the mistake survives.
- **The append-only guarantee depends on `PRAGMA recursive_triggers = ON`**, which
  `db.connect()` sets. `INSERT OR REPLACE` resolves a conflict by deleting the
  existing row, and with recursive triggers off that implicit delete does *not*
  fire BEFORE DELETE triggers — so a replace on `event.id` or `event.dedupe_key`
  would quietly overwrite logged history instead of aborting. Verified for both
  `event` and `observation` in `tests/test_db.py`.

### `observation`

- **`unassisted` NOT NULL** — an assisted production is a different observation,
  not a slightly worse one, so this can never be unknown.
- **`coverage_band` NOT NULL (`>=95` / `80-95` / `<80`)** — a performance is only
  interpretable against the comprehensibility of the input it happened in.
- **`rubric_version` NOT NULL** — scores are comparable only within a rubric
  version. Without it, a rubric change silently corrupts every trend line.
- **Same-item same-day collapsing is not a constraint.** The server collapses
  repeats into one derived event at read time; the log keeps every attempt.
- Same append-only triggers as `event`.

### `lesson`

- **`objective`** is an observable can-do statement, not a subject label.
- **`next_step` is written at close**, not at open — it is a conclusion.
- **`free_notes` has `CHECK (length <= 500)`** — a hard structural cap that stops
  the notes field from becoming the place where unstructured prose accumulates
  instead of observations.
- **`session_id` is nullable** — it narrows the `lesson_outcome` join when the
  lesson ran inside one identified session, without requiring that it did.
- No stored outcome column: see the `lesson_outcome` view.

### `item`

- **`id` is TEXT with deterministic prefixes** — `w-` + sha1(kanji|reading)[:6],
  `s-` + sha1(normalized_jp)[:6], `k-` + the kanji character, `g-` + slug. The
  same word computes to the same id on any machine and in any import order.
  Grammar ids are slugs (`g-wa-topic`) so they match vault filenames directly;
  a rename is recorded in `alias` rather than by rewriting references.
- **`pitch` is the drop position** (0 = heiban), not a contour string, so
  minimal-pair drills can compare numerically. NULL means unknown.
- **`understanding` (1-5)** is the learner's self-rating and applies to grammar
  items; it is an input to selection, not a computed strength.
- **`production_eligible` DEFAULT 1** — 0 marks receptive-only material (役割語,
  archaic forms) that must never be drilled as production. Default-on so a new
  import is drillable unless explicitly held back.
- **`sealed` DEFAULT 0** — the held-out probe pool and canary exclusion. Enforced
  at query time rather than by a constraint, because "sealed" restricts which
  items may be *served*, not which rows may exist.
- **`lexeme_ref` / `jmdict_seq` are nullable soft references** into derived
  tables. An item is studiable before the dictionary side is imported or matched,
  and a JMdict re-import must not need to touch `item`.
- No frequency, comprehension-debt, or strength columns: `item_stat_cache`.

### Others

- **`item_edge.edge_type`** distinguishes `prereq` (must come before) from
  `unlock` (becomes available after); `CHECK (from_id <> to_id)` keeps the DAG
  acyclic at the trivial level.
- **`alias.alias_id` is the primary key**, which already provides the
  `alias(alias_id)` lookup index. `db.resolve_alias()` walks chains, guards
  against cycles, and returns `{id, canonical_id, redirected}` so callers can
  tell the user a redirect happened — applied on every read *and* write.
- **`manual_marks` is keyed `(item_id, ts)`**, so re-marking appends history
  rather than overwriting a verdict; the known set takes the latest `ts`.
  `item_id` is a soft reference so a mark can precede the item row.
- **`media.sub_delay_ms`** is a signed correction for subtitle files that run
  ahead of or behind the audio, applied when querying `sub_lines`.
- **`media_heartbeat.id` has `CHECK (id = 1)`** — single-row by construction, so
  there is no way to end up with two "current position" rows. Liveness is
  derived from `updated_ts` age rather than stored, so a crashed player cannot
  leave a stale `is_live = 1` behind.
- **`settings` PK is `(scope, key)`** with `scope` defaulting to `'global'`;
  per-topic overrides are rows, not a second table.
- **`sentence_text.rowid` is a declared `INTEGER PRIMARY KEY`**, with `item_id` as
  the logical key (`NOT NULL UNIQUE`). Both FTS indexes join on `content_rowid`;
  an *implicit* rowid can be renumbered by `VACUUM`, which would silently point
  every FTS hit at the wrong sentence. Declaring the column pins it.
- **`sentence_text` carries `dict_version` and `tokenizer_version`** — staleness
  is detectable without consulting the FTS indexes, which cannot store it. Both
  FTS indexes are needed: the trigram index returns nothing for queries shorter
  than three characters, and the word index cannot do substring search. They are
  external-content indexes over `sentence_text`; population (and sync strategy)
  is A3's job, not this migration's.
- **`md_note.rowid` is likewise a declared `INTEGER PRIMARY KEY`**, for the same
  VACUUM-safety reason, even though `fts_md_words` / `fts_md_tri` are
  self-contained rather than external-content and so do not join on it directly.
- **`md_note`'s freshness triple is `size_bytes` + `mtime_ns` + `content_sha256`**
  — size and mtime decide whether a file needs re-reading at all, the hash
  decides whether a re-read file actually changed. `index_version` /
  `dict_version` / `tokenizer_version` answer the other staleness question: rows
  built by an older pipeline, dictionary, or tokenizer are re-indexed even when
  the file on disk did not change.
- **`md_frontmatter` has no foreign key to `md_note`** — both are derived, and a
  rebuild must be able to drop them in either order.
- **`anki_cards.ivl` / `due` are mirrored, never written** — `ivl >= 21` days is
  the maturity rule feeding `known_set`, and `manual_marks` overrides it.
