"""Read-only snapshot of an Anki collection into Katagiri's mirror tables.

**The live ``collection.anki2`` is never opened.** Not read-only, not with
``immutable=1``, not "just for a moment". Anki assumes it is the only writer; a
second process holding locks (or merely creating a ``-shm``/``-wal``) on the
live file can make Anki fail to save, and a mid-write read yields a torn image.
Every read here happens against a throwaway copy under ``config.scratch_root``,
which is deleted again when the snapshot finishes.

The protocol, in order:

1. Look for a running Anki (``tasklist``, stdlib only). A hit is a *warning*,
   not an error: the copy may not match what Anki finally saves.
2. ``stat`` the live file (never open it) for its mtime, then copy it to a
   scratch directory, followed by its ``-wal`` / ``-journal`` siblings.
3. Recover the copied WAL into the copy — see `Why the WAL matters` below.
4. Open the copy with ``mode=ro&immutable=1`` and run ``PRAGMA
   integrity_check``. Anything other than ``ok`` aborts.
5. Read ``col.ver``, the Anki schema version, and abort on anything this module
   has not been written against (see :data:`SUPPORTED_SCHEMA_VERSIONS`).
6. Rebuild ``anki_cards`` and ``anki_notes`` (DELETE + INSERT in one
   transaction) and stamp ``mirror_meta``. A mirror still carrying an older
   shape is dropped and recreated in that same transaction — see
   :func:`ensure_mirror_shape`.

No AnkiConnect, no network, no writes of any kind to anything Anki owns.

Why the WAL matters
-------------------
``immutable=1`` promises SQLite the file cannot change, so SQLite skips locking
*and* ignores the ``-wal`` entirely. Anki runs its collection in WAL mode, so
while Anki is open (or after it crashed) committed data can live only in the
``-wal`` — and an ``immutable=1`` read of the main file alone silently returns
the collection as of the last checkpoint. That is measurably dangerous here:
this module rebuilds derived tables by DELETE + INSERT, so a stale read does not
merely go unnoticed, it *replaces* a good mirror with an emptier one.

So the ``-wal`` is copied alongside the collection and then recovered into the
copy: :func:`_recover_journal` opens the *copy* read-write once and sets
``journal_mode=DELETE``, which checkpoints the WAL into the main file and
removes it. Only then is the copy read with ``mode=ro&immutable=1``, and by then
that flag is honest — the file really is finished changing. Writing to the copy
is safe by construction; it is ours and it is deleted afterwards.

The copy order is collection first, then siblings. Neither order is atomic
against a live writer, but this direction fails safely: a WAL is a log of pages
that *supersede* the main file, so a WAL captured no earlier than the main file
either replays cleanly or stops at the first torn frame (SQLite validates frame
checksums during recovery). Copying the WAL first risks the opposite pairing — a
stale WAL replayed over a main file that has already been checkpointed past it,
which is the direction that corrupts. The ``-shm`` is deliberately *not* copied:
it is a derived index of the WAL, and a stale one only misleads recovery, which
rebuilds it from the WAL anyway.

Logging is stderr-only via :mod:`katagiri.logging_setup`, and deliberately
carries no paths: filesystem locations come from config and are treated as
private (see :mod:`katagiri.config`). Exception messages *do* name paths,
because an error the operator cannot act on is not worth raising.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from katagiri.config import get_config
from katagiri.logging_setup import get_logger

COLLECTION_FILE_NAME: Final = "collection.anki2"

# Sibling files that can hold committed data the main file does not have yet.
# '-shm' is excluded on purpose: see the module docstring.
_JOURNAL_SUFFIXES: Final = ("-wal", "-journal")

# Anki schema versions (``col.ver``) this module has been written against:
#
#   11  the long-lived Anki 2.1.x schema, used by every 2.1 release before the
#       Rust backend's schema upgrade, and still what a collection downgraded
#       for sync ("Downgrade & Quit") reports. Covers both the v1 and v2
#       schedulers: the scheduler choice lives in the ``conf`` JSON blob and
#       changes *scheduling semantics*, not table layout, so it does not affect
#       what is mirrored here.
#   18  the current schema, from Anki 2.1.28-ish onward (Rust backend), which
#       moved notetypes/decks/tags/config out of ``col``'s JSON blobs into real
#       tables. Also what the v3 scheduler runs on.
#
# 12-17 were transitional steps inside the Rust rewrite that no released Anki
# leaves a collection sitting on, so they are rejected rather than guessed at.
#
# Both accepted versions keep ``cards`` and ``notes`` byte-identical in the
# columns mirrored here, which is why one reader serves both; only deck and
# notetype *name* lookup differs (JSON blob vs. table).
SUPPORTED_SCHEMA_VERSIONS: Final = frozenset({11, 18})

_MIRROR_TABLES: Final = ("anki_cards", "anki_notes")

# The mirror's *current* shape, which is wider than what 0001_init.sql created:
# B1 added ``anki_cards.queue`` / ``anki_cards.ctype`` and ``mirror_meta.crt`` so
# an exact due count can be computed without inventing a scheduler.
#
# These are DERIVED tables, so the shape change is made by drop-and-rebuild here
# rather than by a numbered migration — the rule in ``docs/db-schema.md`` ("They
# are evolved by drop-and-rebuild scripts, never by migrations"), and the same
# route ``ankimorphs_ingest`` already takes for ``ankimorphs_morphs``. A rebuild
# wipes every row anyway, so there is nothing a migration would have carried
# across; a snapshot is the only thing that can refill the table.
#
# ``ctype`` mirrors Anki's ``cards.type``. It is renamed because ``type`` is
# already the event log's own column name and reading ``anki_cards.type`` next
# to ``event.type`` invites exactly the wrong assumption: one is Anki's card
# state, the other is Katagiri's event kind.
#
# The DDL is kept as a tuple of statements rather than one script because
# ``executescript`` implicitly COMMITs whatever transaction is open, and this
# runs inside the snapshot's transaction.
_ANKI_CARDS_DDL: Final = (
    """
CREATE TABLE anki_cards (
    card_id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL,
    deck    TEXT,
    ivl     INTEGER,                       -- days; ivl >= 21 feeds the known set
    due     INTEGER,                       -- Anki's own scheduling, mirrored not owned
    reps    INTEGER,
    lapses  INTEGER,
    mod     INTEGER,
    queue   INTEGER,                       -- Anki queue: 2 review, 1/3 learning,
                                           -- 0 new, negative suspended/buried
    ctype   INTEGER                        -- Anki cards.type; see note above
)
""",
    "CREATE INDEX anki_cards_note_idx ON anki_cards(note_id)",
    "CREATE INDEX anki_cards_ivl_idx  ON anki_cards(ivl)",
    "CREATE INDEX anki_cards_queue_idx ON anki_cards(queue, due)",
)

_MIRROR_META_DDL: Final = (
    """
CREATE TABLE mirror_meta (
    id                  INTEGER PRIMARY KEY,
    snapshot_ts         TEXT NOT NULL,
    collection_mtime    INTEGER,           -- staleness check without reopening Anki
    anki_schema_version INTEGER,
    crt                 INTEGER,           -- col.crt: the collection's day-zero
                                           -- epoch second, which is what turns a
                                           -- card's day-indexed due into a date

    CHECK (id = 1),
    CHECK (snapshot_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
)
""",
)

# table name -> (columns it must have, statements that recreate it)
_REQUIRED_SHAPE: Final = {
    "anki_cards": (("queue", "ctype"), _ANKI_CARDS_DDL),
    "mirror_meta": (("crt",), _MIRROR_META_DDL),
}

# Anki separates a note's field values with 0x1f (unit separator).
_FIELD_SEPARATOR: Final = "\x1f"

# Schema 18 stores nested deck names with 0x1f between components; schema 11's
# JSON uses '::'. Both are normalised to '::'.
_DECK_COMPONENT_SEPARATOR: Final = "\x1f"
_DECK_NAME_JOINER: Final = "::"

_PROCESS_NAME: Final = "anki.exe"
_PROCESS_QUERY_TIMEOUT_S: Final = 10

_logger = get_logger("anki_snapshot")


class AnkiSnapshotError(RuntimeError):
    """Base class for every failure this module raises."""


class AnkiCollectionNotFoundError(AnkiSnapshotError):
    """No ``collection.anki2`` could be located."""


class AnkiCollectionAmbiguousError(AnkiSnapshotError):
    """Several profiles hold a collection and none was named explicitly.

    ``candidates`` carries the paths found, so a caller can present the choice
    rather than making one.
    """

    def __init__(self, message: str, *, candidates: tuple[Path, ...]) -> None:
        super().__init__(message)
        self.candidates = candidates


class AnkiIntegrityError(AnkiSnapshotError):
    """The copy failed ``PRAGMA integrity_check`` or could not be read at all."""


class UnsupportedAnkiSchemaError(AnkiSnapshotError):
    """``col.ver`` is missing, unreadable, or a version this module rejects.

    ``schema_version`` is the value read, or ``None`` when it could not be read.
    """

    def __init__(self, message: str, *, schema_version: int | None = None) -> None:
        super().__init__(message)
        self.schema_version = schema_version


@dataclass(frozen=True, slots=True)
class MirrorResult:
    """Outcome of one :func:`snapshot_anki` call.

    ``stale`` means "this snapshot may not match what Anki eventually saves":
    Anki was running while the copy was taken, or the collection had a non-empty
    journal (a live session, or the leftovers of a crashed one). It is a
    *provenance* flag, not an error — the mirror is internally consistent either
    way, because the copy was recovered to a committed point in time before
    being read.
    """

    cards: int
    notes: int
    schema_version: int
    stale: bool


# ---------------------------------------------------------------------------
# Locating the collection
# ---------------------------------------------------------------------------


def find_collection(anki_data_dir: Path | str | None = None) -> Path:
    """Locate ``collection.anki2`` under the configured Anki data directory.

    ``anki_data_dir`` is normally the folder holding Anki's *profiles*, so the
    collection sits one level down (``<data dir>/<profile>/collection.anki2``).
    A directory that is itself a profile is accepted too. Several profiles is
    not an error to resolve by picking one — it raises
    :class:`AnkiCollectionAmbiguousError` listing every candidate, because
    quietly mirroring the wrong profile is the kind of bug that looks like bad
    study data months later.
    """
    base = (
        Path(anki_data_dir)
        if anki_data_dir is not None
        else get_config().require_anki_data_dir()
    )
    if not base.is_dir():
        raise AnkiCollectionNotFoundError(
            f"The configured Anki data directory {base} does not exist or is "
            "not a directory. Point 'anki_data_dir' at the folder that holds "
            "your Anki profiles (the one containing prefs21.db)."
        )

    direct = base / COLLECTION_FILE_NAME
    if direct.is_file():
        return direct

    candidates = sorted(
        child / COLLECTION_FILE_NAME
        for child in base.iterdir()
        if child.is_dir() and (child / COLLECTION_FILE_NAME).is_file()
    )
    if not candidates:
        raise AnkiCollectionNotFoundError(
            f"No {COLLECTION_FILE_NAME} found in {base} or in any profile "
            "directory directly beneath it. Check 'anki_data_dir', or pass an "
            "explicit collection_path."
        )
    if len(candidates) > 1:
        listing = "\n  ".join(str(path) for path in candidates)
        raise AnkiCollectionAmbiguousError(
            f"{len(candidates)} Anki collections found under {base}; refusing "
            f"to guess which profile to mirror:\n  {listing}\n"
            "Pass collection_path=... with the one you study from.",
            candidates=tuple(candidates),
        )
    return candidates[0]


# ---------------------------------------------------------------------------
# Is Anki running?
# ---------------------------------------------------------------------------


def _on_windows() -> bool:
    """Whether the process-listing path below is available.

    A function rather than an inline ``os.name`` test so that a test can select
    the branch without reassigning ``os.name`` itself — which is global state
    that pathlib, tempfile and subprocess all consult.
    """
    return os.name == "nt"


def anki_is_running() -> bool:
    """Best-effort check for a running Anki, using ``tasklist`` (stdlib only).

    A wrong answer costs nothing but the warning: correctness rests on always
    reading a copy, never on Anki being closed. So every failure to determine
    this — no ``tasklist``, a timeout, a non-Windows host — returns ``False``
    rather than raising.
    """
    if not _on_windows():
        _logger.debug("Not on Windows; skipping the Anki process check.")
        return False
    # No shell, fixed argv, hard timeout: this must never be able to hang a
    # snapshot, and there is no user-supplied text anywhere in the command.
    kwargs: dict[str, Any] = {}
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", None)
    if no_window is not None:
        kwargs["creationflags"] = no_window
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_PROCESS_NAME}", "/NH"],
            capture_output=True,
            text=True,
            timeout=_PROCESS_QUERY_TIMEOUT_S,
            check=False,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _logger.debug("Could not run tasklist (%s); assuming Anki is closed.", exc)
        return False
    # tasklist prints an "INFO: No tasks are running..." line rather than
    # failing when the filter matches nothing, so match on the image name.
    return _PROCESS_NAME in (completed.stdout or "").lower()


# ---------------------------------------------------------------------------
# Copying and recovering
# ---------------------------------------------------------------------------


def _immutable_uri(path: Path) -> str:
    """The read URI for a copy: read-only and immutable."""
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"


def _copy_collection(source: Path, scratch_dir: Path) -> tuple[Path, bool]:
    """Copy ``source`` (then its journal siblings) into ``scratch_dir``.

    Returns the copy's path and whether any journal sibling had content. Order
    and the ``-shm`` omission are explained in the module docstring.
    """
    destination = scratch_dir / source.name
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise AnkiSnapshotError(
            f"Could not copy the Anki collection {source} to {destination}: "
            f"{exc}. The live collection is never read in place, so the "
            "snapshot cannot continue without a copy."
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
            # actually reads, and the source may have moved on since.
            had_journal = had_journal or copied.stat().st_size > 0
        except OSError as exc:
            # A vanished or locked journal is not fatal on its own; the copy is
            # then simply older. Say so instead of failing the snapshot.
            _logger.warning(
                "Could not copy the '%s' journal alongside the collection (%s); "
                "the snapshot may miss the most recent reviews.",
                suffix,
                exc,
            )
    return destination, had_journal


def _recover_journal(copy_path: Path) -> None:
    """Fold the copied WAL/journal into the copy, so ``immutable=1`` sees it.

    Opens the *copy* read-write — never the live collection — and switches it to
    ``journal_mode=DELETE``, which checkpoints the WAL into the main file and
    unlinks it. A torn final frame is handled by SQLite's own recovery (frame
    checksums), leaving the copy at the last fully committed transaction.
    """
    try:
        conn = sqlite3.connect(str(copy_path), isolation_level=None)
    except sqlite3.Error as exc:
        raise AnkiIntegrityError(
            f"The copied Anki collection at {copy_path} could not be opened to "
            f"recover its journal: {exc}. The collection file may be damaged."
        ) from exc
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error as exc:
        raise AnkiIntegrityError(
            f"Could not recover the write-ahead log into the copied collection "
            f"at {copy_path}: {exc}. Close Anki and try again."
        ) from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reading the copy
# ---------------------------------------------------------------------------


def _check_integrity(conn: sqlite3.Connection, copy_path: Path) -> None:
    """Run ``PRAGMA integrity_check`` and fail loudly on anything but ``ok``."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        # Badly damaged files fail here rather than reporting problems: SQLite
        # cannot even walk the b-tree. Same verdict either way.
        raise AnkiIntegrityError(
            f"PRAGMA integrity_check could not run on the copy of the Anki "
            f"collection ({copy_path}): {exc}. The collection appears "
            "corrupt; run Tools > Check Database in Anki."
        ) from exc
    problems = [str(row[0]) for row in rows]
    if problems != ["ok"]:
        detail = "; ".join(problems[:10])
        raise AnkiIntegrityError(
            "The copy of the Anki collection failed PRAGMA integrity_check and "
            f"was not mirrored ({copy_path}): {detail}. Run Tools > Check "
            "Database in Anki."
        )


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _read_schema_version(conn: sqlite3.Connection, copy_path: Path) -> int:
    """Read and validate ``col.ver``.

    ``col.scm`` is deliberately not used as the version: it is a schema
    *modification time* used for sync conflict detection, not a version number.
    """
    if "col" not in _table_names(conn):
        raise UnsupportedAnkiSchemaError(
            f"{copy_path} has no 'col' table, so it is not an Anki collection "
            "(or is far older than anything Katagiri supports). Expected an "
            f"Anki 2.1 {COLLECTION_FILE_NAME}."
        )
    try:
        row = conn.execute("SELECT ver FROM col ORDER BY id LIMIT 1").fetchone()
    except sqlite3.DatabaseError as exc:
        raise UnsupportedAnkiSchemaError(
            f"Could not read the Anki schema version from col.ver in "
            f"{copy_path}: {exc}."
        ) from exc
    if row is None or row[0] is None:
        raise UnsupportedAnkiSchemaError(
            f"The 'col' table in {copy_path} has no usable row, so the Anki "
            "schema version is unknown. Refusing to mirror a collection whose "
            "layout cannot be confirmed."
        )
    version = int(row[0])
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(v) for v in sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise UnsupportedAnkiSchemaError(
            f"Anki collection schema version {version} is not supported "
            f"(supported: {supported}). Katagiri reads the cards and notes "
            "tables directly, so an unrecognised schema could be misread "
            "silently — refusing rather than guessing. Upgrade or downgrade "
            f"the collection with Anki itself, or update Katagiri. ({copy_path})",
            schema_version=version,
        )
    return version


def _deck_names(conn: sqlite3.Connection, version: int) -> dict[int, str]:
    """Map deck id -> display name, from wherever this schema keeps them."""
    names: dict[int, str] = {}
    tables = _table_names(conn)
    if version >= 18 and "decks" in tables:
        try:
            for deck_id, name in conn.execute("SELECT id, name FROM decks"):
                if name is not None:
                    names[int(deck_id)] = str(name).replace(
                        _DECK_COMPONENT_SEPARATOR, _DECK_NAME_JOINER
                    )
        except sqlite3.DatabaseError as exc:
            _logger.warning("Could not read the decks table (%s).", exc)
        return names
    for deck_id, name in _from_col_json(conn, "decks").items():
        names[deck_id] = name
    return names


def _notetype_names(conn: sqlite3.Connection, version: int) -> dict[int, str]:
    """Map notetype id -> name, from wherever this schema keeps them."""
    names: dict[int, str] = {}
    tables = _table_names(conn)
    if version >= 18 and "notetypes" in tables:
        try:
            for model_id, name in conn.execute("SELECT id, name FROM notetypes"):
                if name is not None:
                    names[int(model_id)] = str(name)
        except sqlite3.DatabaseError as exc:
            _logger.warning("Could not read the notetypes table (%s).", exc)
        return names
    return _from_col_json(conn, "models")


def _from_col_json(conn: sqlite3.Connection, column: str) -> dict[int, str]:
    """Read ``col.<column>``, a JSON object of id -> {"name": ...} (schema 11).

    Unreadable or absent blobs degrade to an empty map: a missing deck name
    costs a label, and is not worth refusing an otherwise good snapshot over.
    """
    if column not in {
        str(row[1]) for row in conn.execute("PRAGMA table_info(col)")
    }:
        return {}
    try:
        row = conn.execute(f"SELECT {column} FROM col ORDER BY id LIMIT 1").fetchone()
    except sqlite3.DatabaseError as exc:
        _logger.warning("Could not read col.%s (%s).", column, exc)
        return {}
    if row is None or not row[0]:
        return {}
    try:
        blob = json.loads(row[0])
    except (TypeError, ValueError) as exc:
        _logger.warning("col.%s is not valid JSON (%s).", column, exc)
        return {}
    if not isinstance(blob, dict):
        return {}
    names: dict[int, str] = {}
    for key, value in blob.items():
        try:
            ident = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict) and value.get("name") is not None:
            names[ident] = str(value["name"])
    return names


def _normalise_tags(raw: Any) -> str | None:
    """Anki pads its tag string with spaces; store it collapsed."""
    if raw is None:
        return None
    return " ".join(str(raw).split())


def _fields_json(raw: Any) -> str | None:
    """Turn Anki's 0x1f-separated field string into a JSON array.

    An array, not an object keyed by field name: field *names* live in the
    notetype, are renameable, and are not unique in practice, whereas a note's
    identity with its notetype is entirely positional. Keeping the ordinals is
    what makes the mirror re-interpretable after a notetype rename.
    """
    if raw is None:
        return None
    return json.dumps(str(raw).split(_FIELD_SEPARATOR), ensure_ascii=False)


def _extract(
    conn: sqlite3.Connection, version: int, copy_path: Path
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], int | None]:
    """Pull the mirrored columns out of the copy's ``cards`` and ``notes``.

    Returns the card rows, the note rows, and ``col.crt``.
    """
    tables = _table_names(conn)
    missing = sorted({"cards", "notes"} - tables)
    if missing:
        raise UnsupportedAnkiSchemaError(
            f"The Anki collection at {copy_path} reports schema version "
            f"{version} but is missing the {', '.join(missing)} table(s). "
            "Refusing to mirror a collection that does not match its own "
            "declared schema.",
            schema_version=version,
        )

    decks = _deck_names(conn, version)
    notetypes = _notetype_names(conn, version)

    try:
        # odid is the *original* deck of a card sitting in a filtered deck; the
        # deck the learner actually organised it under is the one worth
        # mirroring, so odid wins when it is set.
        card_rows = [
            (
                int(card_id),
                int(note_id),
                decks.get(int(odid) or int(did)),
                None if ivl is None else int(ivl),
                None if due is None else int(due),
                None if reps is None else int(reps),
                None if lapses is None else int(lapses),
                None if mod is None else int(mod),
                None if queue is None else int(queue),
                None if ctype is None else int(ctype),
            )
            for (
                card_id, note_id, did, odid, ivl, due, reps, lapses, mod, queue,
                ctype,
            ) in conn.execute(
                "SELECT id, nid, did, odid, ivl, due, reps, lapses, mod, queue, "
                "type FROM cards"
            )
        ]
        note_rows = [
            (
                int(note_id),
                notetypes.get(int(mid)) if mid is not None else None,
                _fields_json(flds),
                _normalise_tags(tags),
                None if mod is None else int(mod),
            )
            for note_id, mid, flds, tags, mod in conn.execute(
                "SELECT id, mid, flds, tags, mod FROM notes"
            )
        ]
    except sqlite3.DatabaseError as exc:
        # integrity_check passed but a read still failed: treat it as damage
        # rather than letting a half-read collection reach the mirror.
        raise AnkiIntegrityError(
            f"Reading cards/notes from the copied Anki collection "
            f"({copy_path}) failed: {exc}."
        ) from exc
    return card_rows, note_rows, _read_crt(conn)


# ---------------------------------------------------------------------------
# Writing the mirror
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _stale_tables(
    conn: sqlite3.Connection,
) -> list[tuple[str, tuple[str, ...]]]:
    """The mirror tables missing a column this module now writes, with their DDL.

    An absent table gives an empty column set, so "missing a column" and
    "missing the table" answer the same way, which is what the caller wants.
    """
    return [
        (table, statements)
        for table, (required, statements) in _REQUIRED_SHAPE.items()
        if not _columns(conn, table).issuperset(required)
    ]


def ensure_mirror_shape(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Bring the mirror tables up to their current shape; return what changed.

    A table missing any column this module now writes is dropped and recreated
    rather than altered: these are derived tables, a snapshot rebuilds every row
    anyway, and ``docs/db-schema.md`` reserves numbered migrations for
    source-of-truth shape changes.

    The DROP and the CREATE are only safe as one unit: a failure between them
    leaves the database with ``anki_cards`` *gone* — the mirror's rows are
    expendable, but the ``known_set`` view reads that table, so every query
    through it would fail until another snapshot ran. Callers already inside a
    transaction (as :func:`_write_mirror` is) supply that atomicity; called on
    its own, this opens a ``BEGIN IMMEDIATE`` of its own and commits or rolls
    back, so it can also be used to make the columns exist before a query needs
    them without ever running DDL in autocommit.

    ``BEGIN IMMEDIATE`` rather than the deferred default: the write lock is
    taken up front, so a concurrent writer loses at BEGIN (where retrying is
    free) instead of at the first DROP.

    Staleness is decided **twice** when this owns the transaction: once
    optimistically, so the common no-op opens no transaction at all, and again
    once the write lock is held. Only the second answer is acted on. The first
    read happens with no lock, so a concurrent snapshot can rebuild *and refill*
    the mirror in the gap before ``BEGIN IMMEDIATE`` returns — acting on the
    stale answer would then drop a table that is already current and throw away
    the rows that other snapshot just committed. A caller who brought its own
    transaction already held the lock for both reads, so for it the second read
    only confirms the first.
    """
    # Optimistic, unlocked, and therefore only a hint: re-read under the lock
    # below before touching anything.
    if not _stale_tables(conn):
        return ()

    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        # The answer that counts: taken with the write lock held, so no other
        # writer can have changed the shape since.
        stale = _stale_tables(conn)
        if not stale:
            # Someone else got there first, rows included. Nothing to do, and
            # nothing was written, so this COMMIT only releases the lock.
            if owns_transaction:
                conn.execute("COMMIT")
            return ()
        for table, statements in stale:
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            for statement in statements:
                conn.execute(statement)
        if owns_transaction:
            conn.execute("COMMIT")
    except BaseException:
        # BaseException, not Exception: an interrupt between the DROP and the
        # CREATE is exactly the case this transaction exists to survive.
        if owns_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
                pass
        raise

    rebuilt = tuple(table for table, _ in stale)
    _logger.info(
        "Rebuilt Anki mirror table(s) %s to the current shape; they are "
        "empty until this snapshot fills them.",
        ", ".join(rebuilt),
    )
    return rebuilt


def _read_crt(conn: sqlite3.Connection) -> int | None:
    """``col.crt`` — the collection's day-zero epoch second.

    Anki's own "today" is the number of whole days from the rollover boundary of
    ``crt``'s *local calendar day* (``col.conf["rollover"]``, 4 a.m. by default)
    to the same boundary today — *not* ``(now - crt) // 86400``, which measures
    from whatever clock time the collection happened to be created at and so runs
    a day behind between the rollover and that creation hour. See
    :func:`katagiri.today_export.collection_day_index` for the calculation.
    Without ``crt`` a mirrored ``due`` day index cannot be turned back into a
    date at all, so a collection that will not give it up degrades to ``None``
    and the reader says the count is unavailable rather than assuming an epoch.
    """
    try:
        row = conn.execute("SELECT crt FROM col ORDER BY id LIMIT 1").fetchone()
    except sqlite3.DatabaseError as exc:
        _logger.warning("Could not read col.crt (%s); due dates will be unavailable.", exc)
        return None
    if row is None or row[0] is None:
        _logger.warning(
            "The Anki collection reports no col.crt; due dates will be "
            "unavailable until a snapshot supplies one."
        )
        return None
    return int(row[0])


def _write_mirror(
    conn: sqlite3.Connection,
    *,
    card_rows: list[tuple[Any, ...]],
    note_rows: list[tuple[Any, ...]],
    version: int,
    collection_mtime: int,
    crt: int | None,
) -> None:
    """Replace the mirror tables and stamp ``mirror_meta``, all or nothing.

    ``anki_cards``/``anki_notes`` are derived, so this is a rebuild rather than
    a merge: rows Anki has deleted must disappear, and DELETE + INSERT says that
    in one step. ``anki_item_map`` is deliberately left alone — it is the
    note-to-item crosswalk owned by the mapping step, not part of this snapshot.
    """
    try:
        previous = conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0]
    except sqlite3.DatabaseError:
        # The table is absent (never created, or dropped by a rebuild that did
        # not finish). There is no previous mirror to be surprised about.
        previous = 0
    if not card_rows and not note_rows and previous:
        _logger.warning(
            "The Anki collection yielded no cards or notes while the existing "
            "mirror holds %d cards; rebuilding it empty. If that is a surprise, "
            "check that the right profile is configured.",
            previous,
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Inside the transaction on purpose: the known_set view reads
        # anki_cards, so a drop-and-recreate performed in autocommit would leave
        # a window in which any query touching that view fails.
        ensure_mirror_shape(conn)
        for table in _MIRROR_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            "INSERT INTO anki_cards"
            "(card_id, note_id, deck, ivl, due, reps, lapses, mod, queue, ctype) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            card_rows,
        )
        conn.executemany(
            "INSERT INTO anki_notes(note_id, model, fields, tags, mod) "
            "VALUES (?, ?, ?, ?, ?)",
            note_rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO mirror_meta"
            "(id, snapshot_ts, collection_mtime, anki_schema_version, crt) "
            "VALUES (1, ?, ?, ?, ?)",
            (_utc_now(), collection_mtime, version, crt),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def snapshot_anki(
    conn: sqlite3.Connection, *, collection_path: Path | str | None = None
) -> MirrorResult:
    """Mirror the Anki collection into ``conn``'s ``anki_*`` tables.

    ``conn`` is a migrated Katagiri database (see :func:`katagiri.db.open_db`).
    ``collection_path`` overrides discovery; otherwise the collection is found
    under the configured ``anki_data_dir``.

    Raises :class:`AnkiSnapshotError` (or a subclass) on every failure: a
    missing or ambiguous collection, a corrupt file, an unsupported schema.
    Nothing is written unless the whole snapshot succeeded.
    """
    source = Path(collection_path) if collection_path is not None else find_collection()
    if not source.is_file():
        raise AnkiCollectionNotFoundError(
            f"The Anki collection {source} does not exist or is not a file."
        )

    running = anki_is_running()
    if running:
        _logger.warning(
            "Anki appears to be running. The snapshot is taken from a copy, so "
            "nothing can be damaged, but it may not match what Anki finally "
            "saves; the result is flagged stale."
        )

    # stat, not open: the mtime is recorded from the live file, which is the
    # only thing this module ever does to it.
    try:
        collection_mtime = int(source.stat().st_mtime)
    except OSError as exc:
        raise AnkiSnapshotError(
            f"Could not stat the Anki collection {source}: {exc}."
        ) from exc

    scratch_root = get_config().scratch_root / "anki-snapshot"
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnkiSnapshotError(
            f"Could not create the scratch directory {scratch_root} for the "
            f"Anki snapshot: {exc}. The live collection is never read in "
            "place, so a writable scratch area is required."
        ) from exc

    scratch_dir = Path(tempfile.mkdtemp(prefix="snapshot-", dir=scratch_root))
    try:
        copy_path, had_journal = _copy_collection(source, scratch_dir)
        _recover_journal(copy_path)

        read_conn = sqlite3.connect(_immutable_uri(copy_path), uri=True)
        try:
            _check_integrity(read_conn, copy_path)
            version = _read_schema_version(read_conn, copy_path)
            card_rows, note_rows, crt = _extract(read_conn, version, copy_path)
        finally:
            read_conn.close()
    finally:
        # The copy is a liability once read: it is a full duplicate of the
        # learner's collection sitting in scratch.
        shutil.rmtree(scratch_dir, ignore_errors=True)

    _write_mirror(
        conn,
        card_rows=card_rows,
        note_rows=note_rows,
        version=version,
        collection_mtime=collection_mtime,
        crt=crt,
    )

    stale = running or had_journal
    _logger.info(
        "Mirrored %d Anki cards and %d notes (schema version %d)%s.",
        len(card_rows),
        len(note_rows),
        version,
        " — flagged stale" if stale else "",
    )
    return MirrorResult(
        cards=len(card_rows),
        notes=len(note_rows),
        schema_version=version,
        stale=stale,
    )


__all__ = [
    "COLLECTION_FILE_NAME",
    "SUPPORTED_SCHEMA_VERSIONS",
    "AnkiCollectionAmbiguousError",
    "AnkiCollectionNotFoundError",
    "AnkiIntegrityError",
    "AnkiSnapshotError",
    "MirrorResult",
    "UnsupportedAnkiSchemaError",
    "anki_is_running",
    "ensure_mirror_shape",
    "find_collection",
    "snapshot_anki",
]
