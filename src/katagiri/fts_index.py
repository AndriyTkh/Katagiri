"""Sentence search index: the ``sentence_text`` content table and its two FTS5 views.

What the schema commits us to
-----------------------------
``0001_init.sql`` declares ``sentence_text`` as a plain table with an explicit
``rowid INTEGER PRIMARY KEY``, and both FTS5 tables as **external content**
tables over it::

    CREATE VIRTUAL TABLE fts_sentence_words USING fts5(
        shadow_text, content='sentence_text', content_rowid='rowid',
        tokenize='unicode61');
    CREATE VIRTUAL TABLE fts_sentence_tri USING fts5(
        jp, content='sentence_text', content_rowid='rowid',
        tokenize='trigram');

External content means SQLite stores **only the index**, reads column values back
from ``sentence_text`` when it needs them (``snippet()``, ``highlight()``), and —
crucially — *does not maintain itself*. There are no triggers in the migration, so
writing a row into ``sentence_text`` does not index it. Every write must be
mirrored explicitly with a matching rowid:

* ``INSERT INTO fts_sentence_words(rowid, shadow_text) VALUES (?, ?)``
* ``INSERT INTO fts_sentence_tri(rowid, jp) VALUES (?, ?)``

and the index is emptied with FTS5's ``'delete-all'`` command (available exactly
because these tables are external-content; a plain ``DELETE FROM <fts>`` would
have to read the *old* values back out of the content table to unindex them, so it
is only correct while content and index still agree). That is why
:func:`rebuild_index` clears the indexes **before** clearing the content table.

Why two indexes
---------------
Japanese has no spaces, so ``unicode61`` over raw text would index a whole
sentence as one enormous token. Two complementary indexes solve this:

* ``fts_sentence_words`` indexes :func:`shadow_text` — the fugashi morph surfaces
  joined by spaces — so ``unicode61`` sees real word boundaries. This is the only
  index that can match a 1- or 2-character word.
* ``fts_sentence_tri`` indexes the raw ``jp`` for substring search, but FTS5's
  trigram tokenizer indexes 3-character windows and therefore matches *nothing*
  for a query shorter than 3 characters — silently.

``mcp_server.search_db_query`` routes on query length for exactly that reason
(``< 3`` characters → words index, otherwise trigram). This module owns
population and the query primitives; the routing policy stays there.

Derived, and stamped
--------------------
Everything here is derived: source of truth is ``item`` (rows with
``kind='sentence'``), and the sentence's Japanese surface lives in
``item.kanji`` — the column is named for word items but is the surface field for
every kind, which is how ``search_db_query`` reads it too (``kanji or reading``).
``item.reading`` is used as the fallback so a kana-only sentence is not dropped.

Each row is stamped with the ``dict_version``/``tokenizer_version`` that were
current in ``metadata`` when it was built, so :func:`index_staleness` can say
which rows a dictionary or tokenizer upgrade invalidated without re-tokenizing
anything. Those keys are written by :func:`katagiri.tokenizer.stamp_versions`; if
they are missing, a rebuild refuses rather than stamping rows with NULL and
losing the ability to tell fresh rows from stale ones.

``sub_lines`` (subtitle text) will become a second source. The extension point is
the ``scope`` argument and the :data:`SOURCES` registry, not a second function:
rowids are handed out by one counter across all selected sources so that the two
FTS indexes and the content table can never disagree about who owns a rowid.
"""

from __future__ import annotations

import argparse
import contextlib
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Final

from katagiri.logging_setup import get_logger
from katagiri.tokenizer import tokenize

CONTENT_TABLE: Final = "sentence_text"
WORD_INDEX: Final = "fts_sentence_words"
TRIGRAM_INDEX: Final = "fts_sentence_tri"

#: ``metadata`` keys that stamp every indexed row. Written by
#: :func:`katagiri.tokenizer.stamp_versions`.
DICT_VERSION_KEY: Final = "dict_version"
TOKENIZER_VERSION_KEY: Final = "tokenizer_version"

#: The one source implemented today. ``media_sub_lines`` is the reserved name for
#: the subtitle source; adding it means adding a reader to :data:`SOURCES`.
SOURCE_SENTENCE_ITEMS: Final = "sentence_items"

SCOPE_ALL: Final = "all"

#: Trigram windows are 3 characters wide, so a shorter query cannot match. Kept
#: here as the *reason* rows are also indexed word-wise; the routing decision
#: itself lives in ``mcp_server``.
TRIGRAM_MIN_CHARS: Final = 3

DEFAULT_LIMIT: Final = 20

# Rows are inserted in batches so a large rebuild does not hold every row in
# memory at once. Purely a memory bound; the transaction still spans everything.
_BATCH_ROWS: Final = 500

_logger = get_logger("fts_index")


class FtsIndexError(RuntimeError):
    """Base class for every failure this module raises."""


class VersionsNotStampedError(FtsIndexError):
    """``metadata`` carries no current dictionary/tokenizer version."""


class DuplicateItemError(FtsIndexError):
    """Two source rows claimed the same ``item_id`` (which is UNIQUE)."""


# ---------------------------------------------------------------------------
# Shadow text
# ---------------------------------------------------------------------------


def shadow_text(text: str) -> str:
    """``text`` re-rendered with spaces between fugashi morph surfaces.

    This is what ``fts_sentence_words`` indexes: ``unicode61`` splits on the
    inserted spaces, turning a space-free Japanese sentence into real word
    tokens. Morphs whose surface is empty or whitespace-only are dropped — they
    would collapse into a double space and index nothing.

    Empty or whitespace-only input yields ``""`` rather than an error, matching
    :func:`katagiri.tokenizer.tokenize`.
    """
    return " ".join(
        morph.surface for morph in tokenize(text) if morph.surface.strip()
    )


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def _metadata_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    value = row[0]
    if value is None or not str(value).strip():
        return None
    return str(value)


def current_versions(conn: sqlite3.Connection) -> dict[str, str]:
    """The dictionary and tokenizer versions rows must be stamped with.

    Raises :class:`VersionsNotStampedError` when either is absent. Refusing here
    is the point: an index whose rows carry NULL versions can never be shown to
    be stale, so it would have to be rebuilt on every upgrade "just in case".
    """
    versions = {
        DICT_VERSION_KEY: _metadata_value(conn, DICT_VERSION_KEY),
        TOKENIZER_VERSION_KEY: _metadata_value(conn, TOKENIZER_VERSION_KEY),
    }
    missing = sorted(key for key, value in versions.items() if value is None)
    if missing:
        raise VersionsNotStampedError(
            f"metadata has no value for {', '.join(missing)}, so indexed rows "
            "cannot be stamped with the versions that produced them. Run "
            "katagiri.tokenizer.stamp_versions(conn) first — it records the "
            "tokenizer stack and the vendored UniDic version that this index "
            "would be built from."
        )
    return {key: str(value) for key, value in versions.items()}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One indexable unit of Japanese text, before it is tokenized or numbered."""

    item_id: str
    jp: str


def _sentence_item_rows(conn: sqlite3.Connection) -> Iterator[SourceRow]:
    """``item`` rows with ``kind='sentence'``, ordered by id.

    ``item.kanji`` is the surface field (its name is word-flavoured but every
    kind stores its surface there); ``item.reading`` is the fallback for a
    kana-only sentence. A row with neither yields an empty ``jp``, which
    :func:`rebuild_index` counts as skipped — every "nothing to index" decision
    is made in one place there, so the tally cannot disagree with reality.
    """
    for row in conn.execute(
        "SELECT id, kanji, reading FROM item WHERE kind = 'sentence' ORDER BY id"
    ):
        jp = row["kanji"] or row["reading"] or ""
        yield SourceRow(item_id=str(row["id"]), jp=str(jp).strip())


#: Registry of population sources. A new source (``media_sub_lines``) is a new
#: entry here plus a name in a ``scope``; nothing else in this module changes.
SOURCES: Final[dict[str, Callable[[sqlite3.Connection], Iterator[SourceRow]]]] = {
    SOURCE_SENTENCE_ITEMS: _sentence_item_rows,
}


def resolve_scope(scope: str) -> tuple[str, ...]:
    """The source names a ``scope`` selects, in a fixed (rowid-stable) order."""
    if scope == SCOPE_ALL:
        return tuple(SOURCES)
    if scope in SOURCES:
        return (scope,)
    known = ", ".join((SCOPE_ALL, *SOURCES))
    raise FtsIndexError(f"Unknown scope {scope!r}; expected one of {known}.")


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IndexResult:
    """What one :func:`rebuild_index` call produced."""

    rows: int
    dict_version: str
    tokenizer_version: str
    duration_s: float
    scope: str
    by_source: tuple[tuple[str, int], ...]
    skipped: int

    def render(self) -> str:
        lines = [
            f"scope     : {self.scope}",
            f"rows      : {self.rows}",
            f"skipped   : {self.skipped} (source rows with no indexable text)",
            f"dict      : {self.dict_version}",
            f"tokenizer : {self.tokenizer_version}",
            f"duration  : {self.duration_s:.3f}s",
        ]
        lines.extend(f"source    : {name} -> {count} rows"
                     for name, count in self.by_source)
        return "\n".join(lines)


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the block as one all-or-nothing unit, owned or nested.

    A rebuild first *destroys* the existing index, so a failure halfway through
    must not be able to leave the database with no index at all. When the caller
    already holds a transaction the work is wrapped in a SAVEPOINT instead, so a
    failure rolls back this rebuild without silently committing — or discarding —
    whatever else the caller was doing.
    """
    if conn.in_transaction:
        conn.execute("SAVEPOINT katagiri_fts_rebuild")
        try:
            yield
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK TO katagiri_fts_rebuild")
                conn.execute("RELEASE katagiri_fts_rebuild")
            raise
        conn.execute("RELEASE katagiri_fts_rebuild")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _clear(conn: sqlite3.Connection) -> None:
    """Empty both FTS indexes, then the content table they read from.

    Order matters. ``'delete-all'`` is the external-content way to empty an FTS5
    index without consulting the content table, but doing it *after* the content
    rows were gone would still be relying on an index whose backing rows have
    vanished — the honest sequence is index first, content second.
    """
    for index in (WORD_INDEX, TRIGRAM_INDEX):
        conn.execute(f"INSERT INTO {index}({index}) VALUES ('delete-all')")
    conn.execute(f"DELETE FROM {CONTENT_TABLE}")


def _flush(
    conn: sqlite3.Connection,
    content: list[tuple[int, str, str, str, str, str]],
    words: list[tuple[int, str]],
    trigrams: list[tuple[int, str]],
) -> None:
    conn.executemany(
        f"INSERT INTO {CONTENT_TABLE}"
        "(rowid, item_id, jp, shadow_text, dict_version, tokenizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        content,
    )
    # External content: these two writes are the *only* thing that indexes a row.
    conn.executemany(
        f"INSERT INTO {WORD_INDEX}(rowid, shadow_text) VALUES (?, ?)", words
    )
    conn.executemany(f"INSERT INTO {TRIGRAM_INDEX}(rowid, jp) VALUES (?, ?)", trigrams)
    content.clear()
    words.clear()
    trigrams.clear()


def rebuild_index(conn: sqlite3.Connection, *, scope: str = SCOPE_ALL) -> IndexResult:
    """Rebuild ``sentence_text`` and both FTS indexes from source, atomically.

    Derived-data rebuild, not an incremental update: the old rows are dropped and
    every selected source is re-read and re-tokenized. Rowids are assigned
    sequentially from 1 in source order (sources in :data:`SOURCES` order, rows
    ordered by ``item_id``), which makes a rebuild deterministic — two rebuilds of
    unchanged data produce byte-identical rows, so "did anything change?" is a
    real question with a real answer.

    Everything happens inside one transaction (see :func:`_atomic`): a failure
    mid-rebuild leaves the previous index exactly as it was.
    """
    started = time.perf_counter()
    versions = current_versions(conn)
    dict_version = versions[DICT_VERSION_KEY]
    tokenizer_version = versions[TOKENIZER_VERSION_KEY]
    source_names = resolve_scope(scope)

    content: list[tuple[int, str, str, str, str, str]] = []
    words: list[tuple[int, str]] = []
    trigrams: list[tuple[int, str]] = []
    counts: dict[str, int] = {name: 0 for name in source_names}
    seen: dict[str, str] = {}
    rowid = 0
    skipped = 0

    with _atomic(conn):
        before = int(
            conn.execute(f"SELECT COUNT(*) FROM {CONTENT_TABLE}").fetchone()[0]
        )
        _clear(conn)
        for name in source_names:
            for source_row in SOURCES[name](conn):
                if source_row.item_id in seen:
                    raise DuplicateItemError(
                        f"item_id {source_row.item_id!r} was produced by both "
                        f"source {seen[source_row.item_id]!r} and source "
                        f"{name!r}; sentence_text.item_id is UNIQUE, so one of "
                        "them would silently win."
                    )
                # Nothing indexable: either the source row carries no Japanese at
                # all, or tokenizing produced no surfaces (text that is only
                # punctuation). Either way the row would be unreachable by every
                # query, and sentence_text.jp is NOT NULL.
                shadow = shadow_text(source_row.jp) if source_row.jp else ""
                if not source_row.jp or not shadow:
                    _logger.debug(
                        "skipping %s from %s: no indexable text",
                        source_row.item_id,
                        name,
                    )
                    skipped += 1
                    continue
                seen[source_row.item_id] = name
                rowid += 1
                counts[name] += 1
                content.append(
                    (
                        rowid,
                        source_row.item_id,
                        source_row.jp,
                        shadow,
                        dict_version,
                        tokenizer_version,
                    )
                )
                words.append((rowid, shadow))
                trigrams.append((rowid, source_row.jp))
                if len(content) >= _BATCH_ROWS:
                    _flush(conn, content, words, trigrams)
        if content:
            _flush(conn, content, words, trigrams)

    duration = time.perf_counter() - started
    _logger.info(
        "rebuilt sentence index: scope=%s rows=%d (was %d) skipped=%d in %.3fs",
        scope,
        rowid,
        before,
        skipped,
        duration,
    )
    return IndexResult(
        rows=rowid,
        dict_version=dict_version,
        tokenizer_version=tokenizer_version,
        duration_s=duration,
        scope=scope,
        by_source=tuple((name, counts[name]) for name in source_names),
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowVersions:
    """A distinct stamped version pair present in the index, and its row count."""

    dict_version: str | None
    tokenizer_version: str | None
    rows: int
    stale: bool


@dataclass(frozen=True, slots=True)
class StalenessResult:
    """How much of the index was built by something other than what is current."""

    stale_rows: int
    total_rows: int
    current_versions: dict[str, str]
    row_versions: tuple[RowVersions, ...]

    @property
    def stale(self) -> bool:
        return self.stale_rows > 0

    def render(self) -> str:
        lines = [
            f"rows      : {self.total_rows}",
            f"stale     : {self.stale_rows}",
            f"dict      : {self.current_versions[DICT_VERSION_KEY]} (current)",
            f"tokenizer : {self.current_versions[TOKENIZER_VERSION_KEY]} (current)",
        ]
        lines.extend(
            f"stamped   : dict={group.dict_version} "
            f"tokenizer={group.tokenizer_version} rows={group.rows} "
            f"{'STALE' if group.stale else 'ok'}"
            for group in self.row_versions
        )
        return "\n".join(lines)


def index_staleness(conn: sqlite3.Connection) -> StalenessResult:
    """Compare each row's stamped versions against the current ones.

    Cheap by construction: this is a ``GROUP BY`` over two stamped columns, not a
    re-tokenization. A non-zero ``stale_rows`` means those rows' ``shadow_text``
    was produced by a dictionary or tokenizer that is no longer the one in force,
    so word-index hits for them are answers from a previous build.
    """
    versions = current_versions(conn)
    dict_version = versions[DICT_VERSION_KEY]
    tokenizer_version = versions[TOKENIZER_VERSION_KEY]

    groups: list[RowVersions] = []
    total = 0
    stale_rows = 0
    # `IS NOT` rather than `<>`: a NULL stamp (a row written by something that
    # bypassed rebuild_index) is stale, and `<>` would evaluate to NULL there.
    for row in conn.execute(
        # `row_count`, not `rows`: ROWS is an SQL keyword (window frames) and a
        # keyword alias is a needless bet on the parser's mood.
        f"SELECT dict_version, tokenizer_version, COUNT(*) AS row_count, "
        f"       (dict_version IS NOT ? OR tokenizer_version IS NOT ?) AS stale "
        f"FROM {CONTENT_TABLE} "
        f"GROUP BY dict_version, tokenizer_version "
        f"ORDER BY stale DESC, row_count DESC, dict_version, tokenizer_version",
        (dict_version, tokenizer_version),
    ):
        count = int(row["row_count"])
        is_stale = bool(row["stale"])
        total += count
        if is_stale:
            stale_rows += count
        groups.append(
            RowVersions(
                dict_version=row["dict_version"],
                tokenizer_version=row["tokenizer_version"],
                rows=count,
                stale=is_stale,
            )
        )

    return StalenessResult(
        stale_rows=stale_rows,
        total_rows=total,
        current_versions=versions,
        row_versions=tuple(groups),
    )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

# FTS5 operators and syntax characters. Inside a quoted phrase these are already
# inert (`_fts_phrase` is the actual defence), but they are stripped as well so
# that what reaches SQLite contains no operator token at all — one fewer thing to
# be wrong about across SQLite versions, and it keeps a query like `評価 OR x`
# from *looking* like it was honoured.
_FTS_SYNTAX_CHARS: Final = '"*^:()[]{}~,'
_FTS_OPERATORS: Final = frozenset({"AND", "OR", "NOT", "NEAR"})


def sanitize_query(query: str) -> str:
    """Reduce a user query to literal text: no FTS5 syntax, no bare operators.

    Two steps, in this order:

    1. Drop FTS5 syntax characters. ``"`` would otherwise end the phrase this
       gets wrapped in, and ``*``/``^``/``:`` are prefix, initial-token and
       column filters.
    2. Drop bare-word operators (``AND``/``OR``/``NOT``/``NEAR``), which FTS5
       only recognises uppercase and unquoted.

    Japanese text contains none of these, so a real query loses nothing; a query
    that *was* trying to smuggle syntax loses exactly the smuggled part.
    """
    cleaned = "".join(" " if char in _FTS_SYNTAX_CHARS else char for char in query)
    kept = [word for word in cleaned.split() if word not in _FTS_OPERATORS]
    return " ".join(kept)


def _fts_phrase(query: str) -> str:
    """Wrap a sanitized query as one FTS5 phrase.

    The quotes are what make the whole string data: a phrase is matched as a
    literal sequence of tokens, so nothing inside it can be re-read as an
    operator. Any surviving ``"`` is doubled, which is FTS5's own escape for a
    literal quote inside a phrase.
    """
    return '"' + query.replace('"', '""') + '"'


def _search(
    conn: sqlite3.Connection, index: str, query: str, limit: int
) -> list[dict[str, Any]]:
    text = sanitize_query(query).strip()
    if not text:
        raise ValueError(
            f"Search needs a non-empty query; {query!r} is empty once FTS5 "
            "syntax and bare operators are removed."
        )
    if limit < 1:
        raise ValueError(f"limit must be at least 1; got {limit}.")
    # `index` is a module constant, never caller input: an FTS5 table name
    # cannot be a bound parameter.
    sql = (
        f"SELECT {CONTENT_TABLE}.rowid AS rowid, {CONTENT_TABLE}.item_id AS item_id, "
        f"       {CONTENT_TABLE}.jp AS jp "
        f"FROM {index} "
        f"JOIN {CONTENT_TABLE} ON {CONTENT_TABLE}.rowid = {index}.rowid "
        f"WHERE {index} MATCH ? ORDER BY rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (_fts_phrase(text), limit)).fetchall()
    except sqlite3.OperationalError as exc:
        raise ValueError(
            f"SQLite rejected the full-text query {text!r} against {index}: {exc}"
        ) from exc
    return [
        {"rowid": int(row["rowid"]), "item_id": row["item_id"], "jp": row["jp"]}
        for row in rows
    ]


def search_words(
    conn: sqlite3.Connection, query: str, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Word search over ``shadow_text`` via the ``unicode61`` index.

    The index that can match a short word: ``勉強`` is two characters, so trigram
    has no window for it, but the shadow text has it as a standalone token.
    Matching is on morph boundaries, so this does *not* find a query that only
    appears mid-token.
    """
    return _search(conn, WORD_INDEX, query, limit)


def search_trigram(
    conn: sqlite3.Connection, query: str, limit: int = DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Substring search over the raw ``jp`` via the trigram index.

    Returns nothing at all for a query shorter than :data:`TRIGRAM_MIN_CHARS` —
    not an error, just silence, which is why the caller must route short queries
    to :func:`search_words`.
    """
    return _search(conn, TRIGRAM_INDEX, query, limit)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _use_utf8_stderr() -> None:
    """Make Japanese printable on a cp1252 Windows console instead of crashing."""
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - redirected stderr
            pass


def main(argv: list[str] | None = None) -> int:
    """``python -m katagiri.fts_index [rebuild|status]``. Output goes to stderr."""
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.fts_index",
        description=(
            "Populate or inspect the sentence search index. 'rebuild' re-derives "
            "sentence_text and both FTS5 indexes from source rows; 'status' "
            "reports row counts and which rows were stamped by a version that is "
            "no longer current. Both write to stderr, like every other Katagiri "
            "diagnostic."
        ),
    )
    parser.add_argument("command", choices=("rebuild", "status"))
    parser.add_argument(
        "--scope",
        default=SCOPE_ALL,
        choices=(SCOPE_ALL, *SOURCES),
        help="which sources to rebuild from (default: all)",
    )
    args = parser.parse_args(argv)

    _use_utf8_stderr()

    # Imported here so that importing this module does not touch the filesystem
    # or the configured database path.
    from katagiri.db import open_db

    conn = open_db()
    try:
        if args.command == "rebuild":
            report: Any = rebuild_index(conn, scope=args.scope)
        else:
            report = index_staleness(conn)
    except FtsIndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(report.render(), file=sys.stderr)
    if args.command == "status" and report.stale:
        return 1
    return 0


__all__ = [
    "CONTENT_TABLE",
    "DEFAULT_LIMIT",
    "DICT_VERSION_KEY",
    "SCOPE_ALL",
    "SOURCES",
    "SOURCE_SENTENCE_ITEMS",
    "TOKENIZER_VERSION_KEY",
    "TRIGRAM_INDEX",
    "TRIGRAM_MIN_CHARS",
    "WORD_INDEX",
    "DuplicateItemError",
    "FtsIndexError",
    "IndexResult",
    "RowVersions",
    "SourceRow",
    "StalenessResult",
    "VersionsNotStampedError",
    "current_versions",
    "index_staleness",
    "main",
    "rebuild_index",
    "resolve_scope",
    "sanitize_query",
    "search_trigram",
    "search_words",
    "shadow_text",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
