"""Ingest of AnkiMorphs knowledge into Katagiri's own derived morph table.

AnkiMorphs is an Anki add-on that tracks knowledge per *morph* — a UniDic-style
lemma plus the inflected surface form it appeared as — rather than per card. Two
things it produces are worth reading:

1. its own SQLite database (``ankimorphs.db``), which holds every morph the
   add-on has seen together with the highest learning interval of any card
   containing it, and
2. a *known morphs* CSV export, which is a flat list of morphs the learner has
   already declared known.

Both land in one derived table, ``ankimorphs_morphs``, keyed
``(lemma, inflection, source)``. ``source`` is part of the key on purpose: the
two inputs disagree (a CSV export is a snapshot the learner took by hand, the
database is live), and keeping both lets a later step compare them instead of
having one silently overwrite the other. Each ingest **rebuilds only its own
source's rows**, in one transaction, so re-running either one is idempotent and
neither can half-apply.

**This module is ingest-only.** Nothing here touches the ``known_set`` view or
the ``item`` table. Merging morph knowledge into the known set needs the
morph → lexeme → item chain that the A4c normalizer builds
(``morph_lexeme_map``); until it exists, an AnkiMorphs morph is a bare
lemma/inflection string with no idea which studied item it belongs to.
:func:`known_morph_count` is therefore a *reporting* helper, not a knownness
source.

The derived table is created here rather than in a migration, per
``docs/db-schema.md``: derived tables are evolved by drop-and-rebuild scripts,
so their shape must not be pinned by migration history. ``CREATE TABLE IF NOT
EXISTS`` runs inside the same transaction as the rebuild, which means a failed
first ingest leaves no table behind either.

Reading the add-on database
---------------------------
**The live ``ankimorphs.db`` is never opened** — same discipline, and for the
same reasons, as :mod:`katagiri.anki_snapshot` (see its docstring for why the
``-wal`` must be copied and recovered before ``immutable=1`` is honest). Anki
runs the add-on in-process, so while Anki is open that file has a writer, and a
mid-write read yields a torn image. Every read here happens against a throwaway
copy under ``config.scratch_root``, deleted again when the ingest finishes.

Schema variance
---------------
AnkiMorphs has renamed the columns of its ``Morphs`` table across releases
(``base``/``inflected`` in early versions, ``lemma``/``inflection`` in current
ones; one ``highest_learning_interval`` then, two separate lemma/inflection
intervals now), and MorphMan — its ancestor — used a ``morphemes`` table again.
So the layout is *detected* from ``PRAGMA table_info`` against a table of known
aliases, and anything that cannot be resolved to at least a lemma column raises
:class:`UnsupportedAnkiMorphsSchemaError` naming the table and the columns that
were actually found. Guessing which column holds a lemma would corrupt the morph
table quietly and months later.

Logging is stderr-only and carries no paths (paths come from config and are
private); exception messages do name paths, because an error the operator cannot
act on is not worth raising.
"""

from __future__ import annotations

import csv
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterable, Iterator, Sequence
from urllib.parse import quote

from katagiri.anki_snapshot import anki_is_running
from katagiri.config import get_config
from katagiri.logging_setup import get_logger

ANKIMORPHS_DB_FILE_NAME: Final = "ankimorphs.db"
MORPHS_TABLE: Final = "ankimorphs_morphs"

# Anki's own maturity threshold, and the one ``known_set`` already uses for the
# card mirror. Kept as a default rather than read from settings so this module
# has no configuration surface of its own yet.
DEFAULT_KNOWN_INTERVAL_DAYS: Final = 21

SOURCES: Final = ("db", "csv")

# Sibling files that can hold committed data the main file does not have yet.
# '-shm' is deliberately excluded: it is a derived index of the WAL and a stale
# one only misleads the recovery that rebuilds it anyway.
_JOURNAL_SUFFIXES: Final = ("-wal", "-journal")

_SCRATCH_SUBDIR: Final = "ankimorphs-ingest"

# Where the add-on database has been found to live, relative to the Anki data
# directory (the folder holding the profiles). Globs are expanded in order and
# every hit is collected, so several matches become an explicit ambiguity error
# rather than a coin toss.
_DB_SEARCH_PATTERNS: Final = (
    ANKIMORPHS_DB_FILE_NAME,
    f"*/{ANKIMORPHS_DB_FILE_NAME}",
    f"*/dbs/{ANKIMORPHS_DB_FILE_NAME}",
    f"addons21/*/{ANKIMORPHS_DB_FILE_NAME}",
    f"addons21/*/data/{ANKIMORPHS_DB_FILE_NAME}",
    f"addons21/*/user_files/{ANKIMORPHS_DB_FILE_NAME}",
)

# Candidate table names, lowercased. AnkiMorphs uses 'Morphs'; MorphMan used
# 'morphemes'. SQLite table names are case-insensitive for lookup but the real
# spelling is needed to build the SELECT, so the match is done on a lowercased
# index of sqlite_master.
_MORPH_TABLE_CANDIDATES: Final = ("morphs", "morphemes")

# Column aliases, most current first. Order matters: a database that carries
# both an old and a new spelling (mid-upgrade) is read with the new one.
_LEMMA_COLUMNS: Final = ("lemma", "base", "norm")
_INFLECTION_COLUMNS: Final = ("inflection", "inflected", "surface")
_LEMMA_INTERVAL_COLUMNS: Final = (
    "highest_lemma_learning_interval",
    "highest_learning_interval",
)
_INFLECTION_INTERVAL_COLUMNS: Final = (
    "highest_inflection_learning_interval",
    "highest_learning_interval",
)

# CSV header spellings, normalised (lowercased, non-alphanumerics stripped).
_CSV_LEMMA_HEADERS: Final = frozenset(
    {"morphlemma", "lemma", "morphbase", "base", "morph", "morphnorm", "norm"}
)
_CSV_INFLECTION_HEADERS: Final = frozenset(
    {"morphinflection", "inflection", "morphinflected", "inflected", "surface"}
)
_CSV_LEMMA_INTERVAL_HEADERS: Final = frozenset(
    {"highestlemmalearninginterval", "highestlearninginterval", "lemmainterval"}
)
_CSV_INFLECTION_INTERVAL_HEADERS: Final = frozenset(
    {"highestinflectionlearninginterval", "inflectioninterval"}
)

_logger = get_logger("ankimorphs_ingest")


class AnkiMorphsError(RuntimeError):
    """Base class for every failure this module raises."""


class AnkiMorphsDbNotFoundError(AnkiMorphsError):
    """No ``ankimorphs.db`` could be located.

    ``searched`` carries every directory pattern that was looked at, so the
    operator can see where to point the configuration instead of guessing.
    """

    def __init__(self, message: str, *, searched: tuple[Path, ...] = ()) -> None:
        super().__init__(message)
        self.searched = searched


class AnkiMorphsDbAmbiguousError(AnkiMorphsError):
    """Several ``ankimorphs.db`` files were found and none was named explicitly."""

    def __init__(self, message: str, *, candidates: tuple[Path, ...]) -> None:
        super().__init__(message)
        self.candidates = candidates


class AnkiMorphsIntegrityError(AnkiMorphsError):
    """The copy failed ``PRAGMA integrity_check`` or could not be read at all."""


class UnsupportedAnkiMorphsSchemaError(AnkiMorphsError):
    """The add-on database's layout could not be recognised.

    ``table`` is the morph table that was found (``None`` when there was none)
    and ``columns`` the column names it actually had.
    """

    def __init__(
        self,
        message: str,
        *,
        table: str | None = None,
        columns: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.table = table
        self.columns = columns


class KnownMorphsCsvError(AnkiMorphsError):
    """A known-morphs CSV could not be read, or its header was unrecognisable."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Outcome of one ingest call.

    ``morphs`` is the number of rows written for ``source`` — after
    de-duplication, so it is the number of distinct morphs, not the number of
    input lines. ``stale`` means "Anki was running while the add-on database was
    copied", so the copy may not match what the add-on eventually saves; it is a
    provenance flag, not an error, and is always ``False`` for a CSV, which is
    already a static export.
    """

    morphs: int
    source: str
    stale: bool = False


@dataclass(frozen=True, slots=True)
class _MorphsLayout:
    """Which columns of the add-on's morph table hold what."""

    table: str
    lemma: str
    inflection: str | None
    lemma_interval: str | None
    inflection_interval: str | None


# ---------------------------------------------------------------------------
# Locating the add-on database
# ---------------------------------------------------------------------------


def find_ankimorphs_db(anki_data_dir: Path | str | None = None) -> Path:
    """Locate ``ankimorphs.db`` under the configured Anki data directory.

    Searched, in order: the data directory itself, each profile directory
    beneath it (and a ``dbs`` subfolder inside one, the MorphMan-era layout),
    and each add-on directory under ``addons21`` including its ``data`` and
    ``user_files`` subfolders. Several hits raise
    :class:`AnkiMorphsDbAmbiguousError` listing them rather than picking one:
    ingesting the wrong profile's morphs looks like bad study data months later.
    """
    base = (
        Path(anki_data_dir)
        if anki_data_dir is not None
        else get_config().require_anki_data_dir()
    )
    if not base.is_dir():
        raise AnkiMorphsDbNotFoundError(
            f"The configured Anki data directory {base} does not exist or is "
            "not a directory. Point 'anki_data_dir' at the folder that holds "
            "your Anki profiles (the one containing prefs21.db).",
            searched=(base,),
        )

    found: list[Path] = []
    seen: set[str] = set()
    for pattern in _DB_SEARCH_PATTERNS:
        for candidate in sorted(base.glob(pattern)):
            if not candidate.is_file():
                continue
            # Resolve for identity only; the un-resolved path is what gets
            # reported, because that is the one the operator recognises.
            key = str(candidate.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)

    if not found:
        listing = "\n  ".join(f"{base}\\{pattern}" for pattern in _DB_SEARCH_PATTERNS)
        raise AnkiMorphsDbNotFoundError(
            f"No {ANKIMORPHS_DB_FILE_NAME} found under {base}. Searched:\n  "
            f"{listing}\nInstall/run AnkiMorphs at least once so it creates its "
            "database, or pass an explicit db_path.",
            searched=tuple(base / pattern for pattern in _DB_SEARCH_PATTERNS),
        )
    if len(found) > 1:
        listing = "\n  ".join(str(path) for path in found)
        raise AnkiMorphsDbAmbiguousError(
            f"{len(found)} AnkiMorphs databases found under {base}; refusing to "
            f"guess which one to ingest:\n  {listing}\n"
            "Pass db_path=... with the one you study from.",
            candidates=tuple(found),
        )
    return found[0]


# ---------------------------------------------------------------------------
# Copying and recovering (never the live file)
# ---------------------------------------------------------------------------


def _immutable_uri(path: Path) -> str:
    """The read URI for a copy: read-only and immutable."""
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"


def _copy_database(source: Path, scratch_dir: Path) -> tuple[Path, bool]:
    """Copy ``source``, then its journal siblings, into ``scratch_dir``.

    Returns the copy's path and whether any journal sibling had content. Main
    file first: a WAL captured no earlier than the main file either replays
    cleanly or stops at its first torn frame, whereas a WAL captured *first* can
    be replayed over a main file already checkpointed past it — the direction
    that corrupts.
    """
    destination = scratch_dir / source.name
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise AnkiMorphsError(
            f"Could not copy the AnkiMorphs database {source} to {destination}: "
            f"{exc}. The live database is never read in place, so the ingest "
            "cannot continue without a copy."
        ) from exc

    had_journal = False
    for suffix in _JOURNAL_SUFFIXES:
        sibling = Path(str(source) + suffix)
        try:
            if not sibling.is_file():
                continue
            copied = Path(str(destination) + suffix)
            shutil.copy2(sibling, copied)
            # Size of the copy, not of the source: the copy is what recovery
            # reads, and the source may have moved on since.
            had_journal = had_journal or copied.stat().st_size > 0
        except OSError as exc:
            # A vanished or locked journal is not fatal on its own; the copy is
            # then simply older. Say so rather than failing the ingest.
            _logger.warning(
                "Could not copy the '%s' journal alongside the AnkiMorphs "
                "database (%s); the ingest may miss the most recent updates.",
                suffix,
                exc,
            )
    return destination, had_journal


def _recover_journal(copy_path: Path, origin: Path) -> None:
    """Fold the copied WAL into the copy, so ``immutable=1`` sees it.

    Opens the *copy* read-write — never the live file — and switches it to
    ``journal_mode=DELETE``, which checkpoints the WAL into the main file and
    unlinks it. Without this, ``immutable=1`` would ignore the ``-wal`` and
    return the database as of its last checkpoint, and because each ingest is a
    rebuild, a stale read would not go unnoticed: it would *replace* a good
    morph table with an emptier one.

    Errors name ``origin``, the live file, not the copy: the copy is deleted
    before the exception reaches the caller, so its path is not something the
    operator could go and look at.
    """
    try:
        conn = sqlite3.connect(str(copy_path), isolation_level=None)
    except sqlite3.Error as exc:
        raise AnkiMorphsIntegrityError(
            f"The copy of the AnkiMorphs database {origin} could not be opened "
            f"to recover its journal: {exc}. The file may be damaged."
        ) from exc
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error as exc:
        raise AnkiMorphsIntegrityError(
            f"Could not recover the write-ahead log into the copy of the "
            f"AnkiMorphs database {origin}: {exc}. Close Anki and try again."
        ) from exc
    finally:
        conn.close()


def _check_integrity(conn: sqlite3.Connection, origin: Path) -> None:
    """Run ``PRAGMA integrity_check`` and fail loudly on anything but ``ok``."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        # A badly damaged file fails here rather than reporting problems:
        # SQLite cannot even walk the b-tree. Same verdict either way.
        raise AnkiMorphsIntegrityError(
            f"PRAGMA integrity_check could not run on the copy of the "
            f"AnkiMorphs database {origin}: {exc}. The file appears corrupt; "
            "let AnkiMorphs rebuild it (Recalc)."
        ) from exc
    problems = [str(row[0]) for row in rows]
    if problems != ["ok"]:
        detail = "; ".join(problems[:10])
        raise AnkiMorphsIntegrityError(
            "The copy of the AnkiMorphs database failed PRAGMA integrity_check "
            f"and was not ingested ({origin}): {detail}. Let AnkiMorphs rebuild "
            "it (Recalc)."
        )


# ---------------------------------------------------------------------------
# Schema detection
# ---------------------------------------------------------------------------


def _quote_identifier(name: str, origin: Path) -> str:
    """Quote an identifier read out of a foreign database.

    Table and column names here come from the add-on's own ``sqlite_master``,
    not from Katagiri, so they are interpolated into SQL and must be quoted. A
    name containing a double quote cannot be quoted safely without escaping
    games and has no legitimate reason to exist, so it is rejected outright.
    """
    if '"' in name:
        raise UnsupportedAnkiMorphsSchemaError(
            f"The AnkiMorphs database {origin} has an identifier containing a "
            f"double quote ({name!r}); refusing to build a query from it."
        )
    return f'"{name}"'


def _pick(available: dict[str, str], candidates: Sequence[str]) -> str | None:
    """First candidate present in ``available`` (a lowercased name -> real name)."""
    for candidate in candidates:
        real = available.get(candidate)
        if real is not None:
            return real
    return None


def _detect_layout(conn: sqlite3.Connection, origin: Path) -> _MorphsLayout:
    """Work out which table and columns hold AnkiMorphs' morph knowledge."""
    tables = {
        str(row[0]).lower(): str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    table = _pick(tables, _MORPH_TABLE_CANDIDATES)
    if table is None:
        listing = ", ".join(sorted(tables.values())) or "(none)"
        raise UnsupportedAnkiMorphsSchemaError(
            f"{origin} has no morph table (looked for "
            f"{' / '.join(_MORPH_TABLE_CANDIDATES)}), so it is not an AnkiMorphs "
            f"database. Tables present: {listing}."
        )

    columns = {
        str(row[1]).lower(): str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({_quote_identifier(table, origin)})"
        )
    }
    lemma = _pick(columns, _LEMMA_COLUMNS)
    if lemma is None:
        listing = ", ".join(sorted(columns.values())) or "(none)"
        raise UnsupportedAnkiMorphsSchemaError(
            f"The '{table}' table in {origin} has no recognisable lemma "
            f"column (looked for {', '.join(_LEMMA_COLUMNS)}). Columns present: "
            f"{listing}. Refusing to guess which column holds a lemma — a wrong "
            "guess would fill the morph table with plausible-looking nonsense. "
            "Update Katagiri for this AnkiMorphs version.",
            table=table,
            columns=tuple(sorted(columns.values())),
        )

    layout = _MorphsLayout(
        table=table,
        lemma=lemma,
        inflection=_pick(columns, _INFLECTION_COLUMNS),
        lemma_interval=_pick(columns, _LEMMA_INTERVAL_COLUMNS),
        inflection_interval=_pick(columns, _INFLECTION_INTERVAL_COLUMNS),
    )
    _logger.debug(
        "AnkiMorphs layout: table=%s lemma=%s inflection=%s intervals=%s/%s",
        layout.table,
        layout.lemma,
        layout.inflection,
        layout.lemma_interval,
        layout.inflection_interval,
    )
    if layout.lemma_interval is None and layout.inflection_interval is None:
        # Not fatal: the morphs themselves are still worth having, and the CSV
        # path has no intervals either. But knownness cannot be derived from
        # this database, so say so once, loudly enough to notice.
        _logger.warning(
            "The AnkiMorphs database has no learning-interval column, so no "
            "morph from it can be counted as known by interval."
        )
    return layout


def _select_sql(layout: _MorphsLayout, origin: Path) -> str:
    """Build the read query for a detected layout, NULL-padding missing columns."""

    def column(name: str | None) -> str:
        return "NULL" if name is None else _quote_identifier(name, origin)

    inflection = (
        "''" if layout.inflection is None
        else _quote_identifier(layout.inflection, origin)
    )
    return (
        f"SELECT {_quote_identifier(layout.lemma, origin)}, {inflection}, "
        f"{column(layout.lemma_interval)}, {column(layout.inflection_interval)} "
        f"FROM {_quote_identifier(layout.table, origin)}"
    )


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    """Coerce a source value to trimmed text. NULL and non-text both survive."""
    if value is None:
        return ""
    return str(value).strip()


def _interval(value: Any) -> int | None:
    """Coerce an interval to a non-negative int, or ``None`` when unusable.

    A negative interval is stored as-is rather than clamped: AnkiMorphs has used
    negative sentinels, and rewriting one to 0 would make it indistinguishable
    from "seen but never learned". Only genuinely unparseable values become NULL.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _deduplicate(
    rows: Iterable[tuple[str, str, Any, Any]],
) -> tuple[list[tuple[str, str, int | None, int | None]], int]:
    """Collapse repeated ``(lemma, inflection)`` pairs, keeping the best intervals.

    The derived table's primary key would otherwise reject a duplicate mid-insert
    and abort the whole rebuild. Duplicates are expected: a CSV export can list a
    morph once per note, and a mid-upgrade database can carry the same morph
    under two spellings. "Best" is the maximum interval, because knowledge is not
    lost by a second, lower-interval sighting.
    """
    merged: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    skipped = 0
    for raw_lemma, raw_inflection, raw_lemma_ivl, raw_inflection_ivl in rows:
        lemma = _text(raw_lemma)
        if not lemma:
            # A morph with no lemma is not addressable by anything downstream.
            skipped += 1
            continue
        inflection = _text(raw_inflection)
        lemma_ivl = _interval(raw_lemma_ivl)
        inflection_ivl = _interval(raw_inflection_ivl)
        key = (lemma, inflection)
        previous = merged.get(key)
        if previous is not None:
            lemma_ivl = _max_or_none(previous[0], lemma_ivl)
            inflection_ivl = _max_or_none(previous[1], inflection_ivl)
        merged[key] = (lemma_ivl, inflection_ivl)

    out = [
        (lemma, inflection, intervals[0], intervals[1])
        for (lemma, inflection), intervals in merged.items()
    ]
    return out, skipped


def _max_or_none(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


# ---------------------------------------------------------------------------
# Writing the derived table
# ---------------------------------------------------------------------------


_CREATE_TABLE_SQL: Final = f"""
CREATE TABLE IF NOT EXISTS {MORPHS_TABLE} (
    lemma          TEXT NOT NULL,
    inflection     TEXT NOT NULL DEFAULT '',
    lemma_ivl      INTEGER,
    inflection_ivl INTEGER,
    source         TEXT CHECK(source IN ('db','csv')),
    imported_ts    TEXT,
    PRIMARY KEY (lemma, inflection, source)
)
"""

_INSERT_SQL: Final = (
    f"INSERT INTO {MORPHS_TABLE}"
    "(lemma, inflection, lemma_ivl, inflection_ivl, source, imported_ts) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds — the format every timestamp here uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_table(conn: sqlite3.Connection) -> None:
    """Create ``ankimorphs_morphs`` if it is absent. Safe to call repeatedly.

    Exposed because a caller may want the table to exist (so a query does not
    fail with ``no such table``) before any ingest has run.
    """
    conn.execute(_CREATE_TABLE_SQL)


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (MORPHS_TABLE,),
    ).fetchone()
    return row is not None


def _rows_to_insert(
    rows: Sequence[tuple[str, str, int | None, int | None]],
    source: str,
    imported_ts: str,
) -> Iterator[tuple[Any, ...]]:
    for lemma, inflection, lemma_ivl, inflection_ivl in rows:
        yield (lemma, inflection, lemma_ivl, inflection_ivl, source, imported_ts)


def _rebuild(
    conn: sqlite3.Connection,
    rows: Sequence[tuple[str, str, int | None, int | None]],
    source: str,
) -> int:
    """Replace every row for ``source``, all or nothing.

    The table is derived, so this is a rebuild rather than a merge: a morph the
    add-on no longer knows must disappear. DDL, DELETE and INSERT all share one
    transaction — SQLite's DDL is transactional — so a failure anywhere leaves
    the previous contents of this source untouched, and a failure on the very
    first ingest leaves no table behind at all. The *other* source's rows are
    never touched, which is what makes the two inputs coexist.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")

    previous = 0
    if _table_exists(conn):
        previous = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {MORPHS_TABLE} WHERE source = ?", (source,)
            ).fetchone()[0]
        )
    if not rows and previous:
        _logger.warning(
            "The AnkiMorphs '%s' source yielded no morphs while %d are already "
            "stored for it; rebuilding it empty. If that is a surprise, check "
            "that the right profile or export file is being read.",
            source,
            previous,
        )

    imported_ts = _utc_now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        create_table(conn)
        conn.execute(f"DELETE FROM {MORPHS_TABLE} WHERE source = ?", (source,))
        conn.executemany(_INSERT_SQL, _rows_to_insert(rows, source, imported_ts))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise
    return len(rows)


# ---------------------------------------------------------------------------
# Public entry point: the add-on database
# ---------------------------------------------------------------------------


def ingest_ankimorphs_db(
    conn: sqlite3.Connection, db_path: Path | str | None = None
) -> IngestResult:
    """Ingest AnkiMorphs' own database into ``conn``'s ``ankimorphs_morphs``.

    ``conn`` is a migrated Katagiri database (see :func:`katagiri.db.open_db`).
    ``db_path`` overrides discovery; otherwise :func:`find_ankimorphs_db` looks
    under the configured ``anki_data_dir``.

    The live file is copied to scratch and read there, never opened in place.
    Raises :class:`AnkiMorphsError` (or a subclass) on every failure; nothing is
    written unless the whole read succeeded.
    """
    source = Path(db_path) if db_path is not None else find_ankimorphs_db()
    if not source.is_file():
        raise AnkiMorphsDbNotFoundError(
            f"The AnkiMorphs database {source} does not exist or is not a file.",
            searched=(source,),
        )

    running = anki_is_running()
    if running:
        _logger.warning(
            "Anki appears to be running. The ingest reads a copy, so nothing "
            "can be damaged, but AnkiMorphs may still be writing; the result is "
            "flagged stale."
        )

    scratch_root = get_config().scratch_root / _SCRATCH_SUBDIR
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnkiMorphsError(
            f"Could not create the scratch directory {scratch_root} for the "
            f"AnkiMorphs ingest: {exc}. The live database is never read in "
            "place, so a writable scratch area is required."
        ) from exc

    scratch_dir = Path(tempfile.mkdtemp(prefix="ankimorphs-", dir=scratch_root))
    try:
        copy_path, had_journal = _copy_database(source, scratch_dir)
        _recover_journal(copy_path, source)

        read_conn = sqlite3.connect(_immutable_uri(copy_path), uri=True)
        try:
            _check_integrity(read_conn, source)
            layout = _detect_layout(read_conn, source)
            try:
                raw = read_conn.execute(_select_sql(layout, source)).fetchall()
            except sqlite3.DatabaseError as exc:
                # integrity_check passed but a read still failed: treat it as
                # damage rather than letting a half-read table reach the mirror.
                raise AnkiMorphsIntegrityError(
                    f"Reading '{layout.table}' from the copy of the AnkiMorphs "
                    f"database {source} failed: {exc}."
                ) from exc
        finally:
            read_conn.close()
    finally:
        # The copy is a liability once read: it is a full duplicate of the
        # learner's morph knowledge sitting in scratch.
        shutil.rmtree(scratch_dir, ignore_errors=True)

    rows, skipped = _deduplicate(raw)
    if skipped:
        _logger.warning(
            "Skipped %d AnkiMorphs row(s) with an empty lemma.", skipped
        )
    count = _rebuild(conn, rows, "db")
    stale = running or had_journal
    _logger.info(
        "Ingested %d AnkiMorphs morphs from the add-on database%s.",
        count,
        " — flagged stale" if stale else "",
    )
    return IngestResult(morphs=count, source="db", stale=stale)


# ---------------------------------------------------------------------------
# Public entry point: the known-morphs CSV export
# ---------------------------------------------------------------------------


def _normalise_header(cell: str) -> str:
    """Lowercase and strip everything but letters and digits.

    ``Morph-Lemma``, ``morph lemma`` and ``Morph_Lemma`` are the same header;
    AnkiMorphs has shipped more than one of those spellings.
    """
    return "".join(char for char in cell.lower() if char.isalnum())


def _csv_column_map(header: Sequence[str], csv_path: Path) -> dict[str, int]:
    """Map role -> column index for a recognised header row.

    A header that names no lemma column is an error, not something to work
    around by assuming column 0: a two-column file could as easily be
    ``inflection,lemma``, and a silent swap would poison every downstream match.
    """
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        name = _normalise_header(cell)
        if not name:
            continue
        if "lemma" not in mapping and name in _CSV_LEMMA_HEADERS:
            mapping["lemma"] = index
        elif "inflection" not in mapping and name in _CSV_INFLECTION_HEADERS:
            mapping["inflection"] = index
        elif "lemma_ivl" not in mapping and name in _CSV_LEMMA_INTERVAL_HEADERS:
            mapping["lemma_ivl"] = index
        elif "inflection_ivl" not in mapping and name in (
            _CSV_INFLECTION_INTERVAL_HEADERS
        ):
            mapping["inflection_ivl"] = index

    if "lemma" not in mapping:
        shown = ", ".join(repr(cell) for cell in header[:10]) or "(empty row)"
        raise KnownMorphsCsvError(
            f"The first row of {csv_path} names no lemma column, so the file's "
            f"layout cannot be confirmed. Found: {shown}. Expected a header "
            "row containing one of: "
            f"{', '.join(sorted(_CSV_LEMMA_HEADERS))} (AnkiMorphs exports "
            "'Morph-Lemma'). Re-export from AnkiMorphs with headers included."
        )
    return mapping


def ingest_known_morphs_csv(
    conn: sqlite3.Connection, csv_path: Path | str
) -> IngestResult:
    """Ingest an AnkiMorphs *known morphs* CSV export into ``conn``.

    The header is sniffed rather than assumed: exports come as
    ``Morph-Lemma,Morph-Inflection``, as lemma-only, and with extra columns in
    between, and a BOM in front (``utf-8-sig`` handles both BOM and plain
    UTF-8). Unrecognised columns are ignored; a missing inflection column means
    every row is stored with an empty inflection, which is exactly the
    lemma-level knowledge such an export carries.

    Rows land with ``source = 'csv'`` and replace only the previous CSV rows.
    """
    path = Path(csv_path)
    try:
        # newline='' is csv's requirement: it must see the raw line endings to
        # handle a quoted field containing one.
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise KnownMorphsCsvError(
                    f"{path} is empty; a known-morphs export has at least a "
                    "header row."
                ) from None
            mapping = _csv_column_map(header, path)
            raw = [
                (
                    _cell(row, mapping.get("lemma")),
                    _cell(row, mapping.get("inflection")),
                    _cell(row, mapping.get("lemma_ivl")),
                    _cell(row, mapping.get("inflection_ivl")),
                )
                for row in reader
                if any(cell.strip() for cell in row)
            ]
    except OSError as exc:
        raise KnownMorphsCsvError(
            f"Could not read the known-morphs CSV {path}: {exc}."
        ) from exc
    except csv.Error as exc:
        raise KnownMorphsCsvError(
            f"The known-morphs CSV {path} is malformed: {exc}."
        ) from exc

    rows, skipped = _deduplicate(raw)
    if skipped:
        _logger.warning(
            "Skipped %d row(s) with an empty lemma while reading a known-morphs "
            "CSV.",
            skipped,
        )
    count = _rebuild(conn, rows, "csv")
    _logger.info("Ingested %d known morphs from a CSV export.", count)
    return IngestResult(morphs=count, source="csv", stale=False)


def _cell(row: Sequence[str], index: int | None) -> str:
    """A row's value at ``index``, tolerating short rows and absent roles."""
    if index is None or index >= len(row):
        return ""
    return row[index]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def known_morph_count(
    conn: sqlite3.Connection, min_ivl: int = DEFAULT_KNOWN_INTERVAL_DAYS
) -> int:
    """How many distinct morphs count as known.

    A morph is known when its lemma's highest learning interval reaches
    ``min_ivl`` days (the same maturity rule ``known_set`` applies to the card
    mirror) **or** when it came from a known-morphs CSV, which is by definition a
    list of morphs the learner has declared known.

    Distinct ``(lemma, inflection)`` pairs, not rows: the same morph can be
    present under both sources, and counting it twice would inflate every
    coverage number computed from this. Returns 0 when nothing has been ingested
    yet — the absence of the table is "no morphs", not an error.
    """
    if not _table_exists(conn):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) FROM ("
        f"  SELECT DISTINCT lemma, inflection FROM {MORPHS_TABLE}"
        f"  WHERE source = 'csv' OR (lemma_ivl IS NOT NULL AND lemma_ivl >= ?)"
        f")",
        (min_ivl,),
    ).fetchone()
    return int(row[0])


__all__ = [
    "ANKIMORPHS_DB_FILE_NAME",
    "DEFAULT_KNOWN_INTERVAL_DAYS",
    "MORPHS_TABLE",
    "SOURCES",
    "AnkiMorphsDbAmbiguousError",
    "AnkiMorphsDbNotFoundError",
    "AnkiMorphsError",
    "AnkiMorphsIntegrityError",
    "IngestResult",
    "KnownMorphsCsvError",
    "UnsupportedAnkiMorphsSchemaError",
    "create_table",
    "find_ankimorphs_db",
    "ingest_ankimorphs_db",
    "ingest_known_morphs_csv",
    "known_morph_count",
]
