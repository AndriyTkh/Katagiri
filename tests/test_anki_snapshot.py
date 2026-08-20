"""Anki snapshot tests: copy-then-read protocol, schema gating, mirror rebuild.

The fixtures here build real ``collection.anki2`` files with Anki's own table
layout (schema 11 and schema 18) rather than mocking sqlite, because the whole
point of the module under test is how it *reads a file*: copy order, WAL
recovery, ``immutable=1``, integrity checking. A mock would assert the
implementation back to itself.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

import pytest

from katagiri import anki_snapshot
from katagiri import config as config_mod
from katagiri import db
from katagiri.anki_snapshot import (
    AnkiCollectionAmbiguousError,
    AnkiCollectionNotFoundError,
    AnkiIntegrityError,
    UnsupportedAnkiSchemaError,
    snapshot_anki,
)

# ---------------------------------------------------------------------------
# Fixture collection construction (Anki's real DDL, trimmed to what we read)
# ---------------------------------------------------------------------------

_COL_DDL = """
CREATE TABLE col (
    id integer primary key, crt integer not null, mod integer not null,
    scm integer not null, ver integer not null, dty integer not null,
    usn integer not null, ls integer not null, conf text not null,
    models text not null, decks text not null, dconf text not null,
    tags text not null
);
"""

_NOTES_DDL = """
CREATE TABLE notes (
    id integer primary key, guid text not null, mid integer not null,
    mod integer not null, usn integer not null, tags text not null,
    flds text not null, sfld integer not null, csum integer not null,
    flags integer not null, data text not null
);
"""

_CARDS_DDL = """
CREATE TABLE cards (
    id integer primary key, nid integer not null, did integer not null,
    ord integer not null, mod integer not null, usn integer not null,
    type integer not null, queue integer not null, due integer not null,
    ivl integer not null, factor integer not null, reps integer not null,
    lapses integer not null, left integer not null, odue integer not null,
    odid integer not null, flags integer not null, data text not null
);
"""

# Schema 18 moved decks and notetypes out of col's JSON blobs into real tables.
# 'collate unicase' is kept verbatim from Anki's own DDL: unicase is a custom
# collation Anki registers on its own connection, so no other process has it.
# The builder registers a stand-in just to be able to CREATE these tables; the
# reader under test never registers one, which is exactly the point — it must
# read decks and notetypes without provoking the collation (no ORDER BY or
# comparison on those columns), or it would fail on every real collection.
_DECKS_DDL = """
CREATE TABLE decks (
    id integer primary key, name text not null collate unicase,
    mtime_secs integer not null, usn integer not null, common blob not null,
    kind blob not null
);
"""

_NOTETYPES_DDL = """
CREATE TABLE notetypes (
    id integer primary key, name text not null collate unicase,
    mtime_secs integer not null, usn integer not null, config blob not null
);
"""

DECK_DEFAULT = 1
DECK_CORE = 1600000000000
DECK_FILTERED = 1699999999999  # a filtered deck: named nowhere, odid points home
MODEL_BASIC = 1500000000000
MODEL_CLOZE = 1500000000001

# note_id -> (model_id, field values, tags). \x1f is Anki's field separator; it
# is applied by the builder so the test data stays readable.
NOTES = {
    1: (MODEL_BASIC, ["日本語", "Japanese language", "audio[sound:a.mp3]"], " core vocab "),
    2: (MODEL_BASIC, ["猫", "cat"], " animals "),
    3: (MODEL_CLOZE, ["{{c1::森}}が好き", "", "note with \"quotes\" & emoji 🐟"], ""),
}

# card_id -> (note_id, did, odid, ivl, due, reps, lapses, mod)
CARDS = {
    101: (1, DECK_CORE, 0, 30, 500, 12, 1, 1700000001),
    102: (2, DECK_CORE, 0, 5, 20, 3, 0, 1700000002),
    103: (3, DECK_FILTERED, DECK_CORE, 21, 600, 20, 2, 1700000003),
    104: (3, DECK_DEFAULT, 0, 0, 1, 0, 0, 1700000004),
}

MATURE_NOTE_IDS = {1, 3}  # ivl >= 21 on at least one card

# col.crt in the fixture: the collection's day-zero epoch second.
COLLECTION_CRT = 1400000000

# card_id -> (queue, type), for the cards that are not plain reviews. Kept apart
# from CARDS so the tuple shape the rest of this file unpacks stays as it was.
CARD_QUEUES = {
    102: (0, 0),    # new
    104: (-1, 2),   # suspended
}
DEFAULT_QUEUE_AND_TYPE = (2, 2)


def _unicase(left: str, right: str) -> int:
    """Stand-in for Anki's custom collation; only the builder needs one."""
    lowered = (left.lower(), right.lower())
    return (lowered[0] > lowered[1]) - (lowered[0] < lowered[1])


def build_collection(
    path: Path, *, ver: int = 11, wal: bool = False, filler: int = 0
) -> Path:
    """Write a minimal but structurally real ``collection.anki2`` at ``path``.

    ``wal=True`` leaves the file in WAL mode (the caller is responsible for
    whether a ``-wal`` survives). ``filler`` adds extra notes/cards so the file
    spans enough pages for corruption tests to bite.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.create_collation("unicase", _unicase)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_COL_DDL + _NOTES_DDL + _CARDS_DDL)

        decks_json, models_json = "", ""
        # Only 18 gets the table layout: versions above it are fixtures for the
        # "unsupported version" path and never get as far as a deck lookup.
        if ver == 18:
            conn.executescript(_DECKS_DDL + _NOTETYPES_DDL)
            conn.executemany(
                "INSERT INTO decks VALUES (?, ?, 0, 0, x'', x'')",
                [
                    (DECK_DEFAULT, "Default"),
                    # Schema 18 separates nested deck components with \x1f.
                    (DECK_CORE, "Japanese\x1fCore"),
                ],
            )
            conn.executemany(
                "INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')",
                [(MODEL_BASIC, "Basic"), (MODEL_CLOZE, "Cloze")],
            )
        else:
            decks_json = json.dumps(
                {
                    str(DECK_DEFAULT): {"id": DECK_DEFAULT, "name": "Default"},
                    str(DECK_CORE): {"id": DECK_CORE, "name": "Japanese::Core"},
                }
            )
            models_json = json.dumps(
                {
                    str(MODEL_BASIC): {"id": MODEL_BASIC, "name": "Basic"},
                    str(MODEL_CLOZE): {"id": MODEL_CLOZE, "name": "Cloze"},
                }
            )

        conn.execute(
            "INSERT INTO col VALUES (1, 1400000000, 1700000000, 1700000000, ?, 0, "
            "-1, 0, '{}', ?, ?, '{}', '{}')",
            (ver, models_json, decks_json),
        )

        note_rows = [
            (nid, f"guid{nid}", mid, 1700000000, -1, tags, "\x1f".join(fields),
             fields[0], 0, 0, "")
            for nid, (mid, fields, tags) in NOTES.items()
        ]
        card_rows = [
            (cid, nid, did, 0, mod, -1,
             CARD_QUEUES.get(cid, DEFAULT_QUEUE_AND_TYPE)[1],
             CARD_QUEUES.get(cid, DEFAULT_QUEUE_AND_TYPE)[0],
             due, ivl, 2500, reps, lapses, 0, 0, odid, 0, "")
            for cid, (nid, did, odid, ivl, due, reps, lapses, mod) in CARDS.items()
        ]
        for index in range(filler):
            nid = 10_000 + index
            note_rows.append(
                (nid, f"g{nid}", MODEL_BASIC, 1700000000, -1, " filler ",
                 f"表現{index}\x1ffiller gloss {index} " + "x" * 120, f"表現{index}",
                 0, 0, "")
            )
            card_rows.append(
                (20_000 + index, nid, DECK_CORE, 0, 1700000000, -1, 2, 2, index,
                 index % 40, 2500, 1, 0, 0, 0, 0, 0, "")
            )

        conn.executemany(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", note_rows
        )
        conn.executemany(
            "INSERT INTO cards VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            card_rows,
        )
    finally:
        conn.close()
    return path


# ---------------------------------------------------------------------------
# Environment fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir so config, db and scratch are isolated."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def anki_dir(local_app_data):
    """The configured Anki data directory (profiles live one level down)."""
    path = local_app_data / "anki-data"
    path.mkdir(parents=True, exist_ok=True)
    config_path = config_mod.config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f'anki_data_dir = "{path.as_posix()}"\n'
        f'scratch_root = "{(local_app_data / "scratch").as_posix()}"\n',
        encoding="utf-8",
    )
    config_mod.reset_config_cache()
    return path


@pytest.fixture
def collection(anki_dir):
    """A schema-11 fixture collection inside a single profile directory."""
    return build_collection(anki_dir / "User 1" / "collection.anki2")


@pytest.fixture
def conn(anki_dir, monkeypatch):
    """A migrated Katagiri database, with the Anki process check pinned off.

    Pinned because the real check shells out to ``tasklist``: a developer with
    Anki open would otherwise see ``stale`` flip under them.
    """
    monkeypatch.setattr(anki_snapshot, "anki_is_running", lambda: False)
    connection = db.open_db()
    try:
        yield connection
    finally:
        connection.close()


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def log_records():
    """Collect this module's log records, regardless of propagation.

    A handler on the logger itself rather than ``caplog``: ``setup_logging()``
    sets ``propagate = False`` on the ``katagiri`` logger for the whole process,
    so once any test in the session has called it, records would never reach
    caplog's root handler. This keeps the assertion independent of test order.
    """
    logger = logging.getLogger("katagiri.anki_snapshot")
    recorder = _Recorder()
    previous_level = logger.level
    logger.addHandler(recorder)
    logger.setLevel(logging.DEBUG)
    try:
        yield recorder.records
    finally:
        logger.removeHandler(recorder)
        logger.setLevel(previous_level)


def scratch_dir() -> Path:
    return config_mod.get_config().scratch_root / "anki-snapshot"


def install_connect_spy(monkeypatch) -> list[str]:
    """Record every ``sqlite3.connect`` target from here on, delegating through."""
    calls: list[str] = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):
        calls.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    return calls


# ---------------------------------------------------------------------------
# The mirror itself
# ---------------------------------------------------------------------------


def test_snapshot_populates_the_mirror_tables(conn, collection):
    result = snapshot_anki(conn, collection_path=collection)

    assert (result.cards, result.notes) == (len(CARDS), len(NOTES))
    assert result.schema_version == 11
    assert result.stale is False

    cards = {
        row["card_id"]: row
        for row in conn.execute("SELECT * FROM anki_cards")
    }
    assert set(cards) == set(CARDS)
    assert cards[101]["note_id"] == 1
    assert cards[101]["deck"] == "Japanese::Core"
    assert (cards[101]["ivl"], cards[101]["due"]) == (30, 500)
    assert (cards[101]["reps"], cards[101]["lapses"]) == (12, 1)
    assert cards[101]["mod"] == 1700000001
    # Anki's scheduling state, mirrored not owned: without queue there is no way
    # to tell a review card waiting for you from a suspended one.
    assert (cards[101]["queue"], cards[101]["ctype"]) == (2, 2)
    assert cards[104]["deck"] == "Default"

    notes = {row["note_id"]: row for row in conn.execute("SELECT * FROM anki_notes")}
    assert set(notes) == set(NOTES)
    assert notes[1]["model"] == "Basic"
    assert notes[3]["model"] == "Cloze"


def test_a_filtered_cards_deck_is_reported_as_its_home_deck(conn, collection):
    """A card parked in a filtered deck still belongs to the deck it came from."""
    snapshot_anki(conn, collection_path=collection)
    deck = conn.execute(
        "SELECT deck FROM anki_cards WHERE card_id = 103"
    ).fetchone()[0]
    assert deck == "Japanese::Core"


def test_fields_round_trip_as_a_json_array(conn, collection):
    snapshot_anki(conn, collection_path=collection)

    for note_id, (_, fields, _) in NOTES.items():
        stored = conn.execute(
            "SELECT fields FROM anki_notes WHERE note_id = ?", (note_id,)
        ).fetchone()[0]
        assert json.loads(stored) == fields, note_id
        # The separator must not survive into the mirror as text.
        assert "\x1f" not in stored

    # Empty fields keep their position, and SQLite agrees the value is JSON.
    assert json.loads(
        conn.execute("SELECT fields FROM anki_notes WHERE note_id = 3").fetchone()[0]
    )[1] == ""
    assert conn.execute(
        "SELECT COUNT(*) FROM anki_notes WHERE json_valid(fields)"
    ).fetchone()[0] == len(NOTES)


def test_tags_are_stored_without_ankis_padding(conn, collection):
    snapshot_anki(conn, collection_path=collection)
    tags = dict(conn.execute("SELECT note_id, tags FROM anki_notes"))
    assert tags[1] == "core vocab"
    assert tags[3] == ""


def test_mirror_meta_is_stamped(conn, collection):
    before = int(collection.stat().st_mtime)
    result = snapshot_anki(conn, collection_path=collection)

    row = conn.execute("SELECT * FROM mirror_meta").fetchone()
    assert row["id"] == 1
    assert row["anki_schema_version"] == result.schema_version == 11
    assert row["collection_mtime"] == before
    # The CHECK constraint would have rejected a malformed timestamp, so simply
    # reaching this point proves the format; assert the shape anyway.
    assert len(row["snapshot_ts"]) == 20 and row["snapshot_ts"].endswith("Z")
    # col.crt: the collection's day-zero. Without it a card's day-indexed due
    # cannot be turned back into a date, so it is mirrored with the rest.
    assert row["crt"] == COLLECTION_CRT
    assert conn.execute("SELECT COUNT(*) FROM mirror_meta").fetchone()[0] == 1


def test_card_queues_and_types_round_trip(conn, collection):
    """Every queue value is mirrored verbatim; none is normalised away."""
    snapshot_anki(conn, collection_path=collection)
    mirrored = {
        row["card_id"]: (row["queue"], row["ctype"])
        for row in conn.execute("SELECT card_id, queue, ctype FROM anki_cards")
    }
    assert mirrored[101] == (2, 2)
    assert mirrored[102] == (0, 0), "a new card must not read as a review"
    assert mirrored[104] == (-1, 2), "a suspended card must stay negative"


def test_a_mirror_in_the_old_shape_is_rebuilt_by_the_next_snapshot(conn, collection):
    """Derived tables are evolved by drop-and-rebuild, not by migration.

    A database whose mirror was written before the queue columns existed carries
    the narrow table; the next snapshot replaces the table wholesale. Nothing is
    carried across because nothing needs to be — the snapshot is the source.
    """
    conn.executescript(
        "DROP TABLE anki_cards;"
        "CREATE TABLE anki_cards ("
        " card_id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL, deck TEXT,"
        " ivl INTEGER, due INTEGER, reps INTEGER, lapses INTEGER, mod INTEGER);"
        "DROP TABLE mirror_meta;"
        "CREATE TABLE mirror_meta ("
        " id INTEGER PRIMARY KEY, snapshot_ts TEXT NOT NULL,"
        " collection_mtime INTEGER, anki_schema_version INTEGER, CHECK (id = 1));"
    )
    conn.execute("INSERT INTO anki_cards(card_id, note_id, ivl) VALUES (999, 1, 5)")

    snapshot_anki(conn, collection_path=collection)

    columns = {row[1] for row in conn.execute('PRAGMA table_info("anki_cards")')}
    assert {"queue", "ctype"} <= columns
    assert "crt" in {row[1] for row in conn.execute('PRAGMA table_info("mirror_meta")')}
    assert conn.execute(
        "SELECT COUNT(*) FROM anki_cards WHERE card_id = 999"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT crt FROM mirror_meta").fetchone()[0] == COLLECTION_CRT


def test_ensure_mirror_shape_is_idempotent(conn):
    """A mirror already at the current shape is left alone, rows and all."""
    assert anki_snapshot.ensure_mirror_shape(conn) == ("anki_cards", "mirror_meta")
    conn.execute(
        "INSERT INTO anki_cards(card_id, note_id, ivl, queue, ctype) "
        "VALUES (7, 7, 30, 2, 2)"
    )
    assert anki_snapshot.ensure_mirror_shape(conn) == ()
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 1


def test_a_standalone_rebuild_is_atomic(conn, monkeypatch):
    """A failure between the DROP and the CREATE must not lose ``anki_cards``.

    Called on its own there is no caller transaction to fall back on, so if the
    rebuild ran in autocommit the DROP would already be durable when the CREATE
    failed: the mirror's *rows* are expendable, but ``known_set`` reads that
    table, so every query through the view would fail until another snapshot
    happened to run. The rebuild therefore has to carry its own transaction.

    The failure is injected as invalid DDL, which is what a process dying
    between the two statements looks like from the database's side.
    """
    conn.execute("INSERT INTO anki_cards(card_id, note_id, ivl) VALUES (5, 5, 30)")
    broken = dict(anki_snapshot._REQUIRED_SHAPE)
    broken["anki_cards"] = (
        ("queue", "ctype"),
        ("CREATE TABLE anki_cards (this is not valid DDL",),
    )
    monkeypatch.setattr(anki_snapshot, "_REQUIRED_SHAPE", broken)

    with pytest.raises(sqlite3.DatabaseError):
        anki_snapshot.ensure_mirror_shape(conn)

    assert conn.in_transaction is False, "the failed rebuild left a transaction open"
    assert conn.execute(
        "SELECT COUNT(*) FROM anki_cards WHERE card_id = 5"
    ).fetchone()[0] == 1
    # The view is only queryable while its base table exists; this is the
    # failure the transaction is really there to prevent.
    assert conn.execute("SELECT COUNT(*) FROM known_set").fetchone()[0] == 0


def test_a_standalone_rebuild_commits(conn):
    """Standing alone the rebuild must land, not sit in an open transaction."""
    assert anki_snapshot.ensure_mirror_shape(conn) == ("anki_cards", "mirror_meta")
    assert conn.in_transaction is False

    other = db.connect()
    try:
        columns = {
            row[1] for row in other.execute('PRAGMA table_info("anki_cards")')
        }
    finally:
        other.close()
    assert {"queue", "ctype"} <= columns


def test_a_standalone_rebuild_rechecks_staleness_under_the_lock(conn, monkeypatch):
    """The unlocked staleness read is a hint; only the locked one may be acted on.

    The pre-check runs before ``BEGIN IMMEDIATE``, so a concurrent snapshot can
    rebuild *and refill* the mirror in that gap. Acting on the stale answer would
    then drop an already-current table and throw away the rows the other snapshot
    just committed.

    The race is injected by flipping the staleness answer between the two calls,
    which is what the losing side of it actually observes — no threads needed.
    """
    anki_snapshot.ensure_mirror_shape(conn)
    conn.execute(
        "INSERT INTO anki_cards(card_id, note_id, ivl, queue, ctype) "
        "VALUES (11, 11, 21, 2, 2)"
    )
    truth = anki_snapshot._stale_tables
    calls: list[int] = []

    def flipping(connection):
        calls.append(1)
        if len(calls) == 1:
            # Unlocked pre-check: the mirror still looks like the old shape.
            return [("anki_cards", anki_snapshot._REQUIRED_SHAPE["anki_cards"][1])]
        # Under the write lock, where the other snapshot's work is visible.
        return truth(connection)

    monkeypatch.setattr(anki_snapshot, "_stale_tables", flipping)

    assert anki_snapshot.ensure_mirror_shape(conn) == ()
    assert len(calls) == 2, "staleness was not re-checked under the write lock"
    assert conn.in_transaction is False, "the early return left a transaction open"
    assert conn.execute(
        "SELECT COUNT(*) FROM anki_cards WHERE card_id = 11"
    ).fetchone()[0] == 1, "a redundant rebuild dropped the concurrent snapshot's rows"


def test_a_callers_transaction_still_owns_the_rebuild(conn):
    """Inside a caller's transaction the rebuild must not commit on its own.

    ``_write_mirror`` relies on this: the drop-and-recreate and the reinsert are
    one unit, so a COMMIT in the middle would publish an empty mirror.
    """
    conn.execute("BEGIN IMMEDIATE")
    assert anki_snapshot.ensure_mirror_shape(conn) == ("anki_cards", "mirror_meta")
    assert conn.in_transaction is True, "the rebuild committed the caller's work"

    conn.execute("ROLLBACK")
    columns = {row[1] for row in conn.execute('PRAGMA table_info("anki_cards")')}
    assert "queue" not in columns, "the caller's rollback did not undo the rebuild"


# ---------------------------------------------------------------------------
# The rebuild DDL against the migration that first created these tables
# ---------------------------------------------------------------------------

# What the rebuild DDL is *allowed* to add on top of 0001_init.sql. Everything
# else must match, in both directions.
_ADDED_COLUMNS = {"anki_cards": {"queue", "ctype"}, "mirror_meta": {"crt"}}
_ADDED_INDEXES = {"anki_cards": {"anki_cards_queue_idx"}, "mirror_meta": set()}


def _column_spec(connection, table, ignore):
    """Ordered (name, type, notnull, default, pk) for a table's columns."""
    return [
        (row[1], str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
        for row in connection.execute(f'PRAGMA table_info("{table}")')
        if row[1] not in ignore
    ]


def _index_spec(connection, table, ignore):
    """name -> (unique, partial, indexed columns), skipping implicit indexes."""
    spec = {}
    for row in connection.execute(f'PRAGMA index_list("{table}")'):
        name = str(row[1])
        if name in ignore or name.startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            str(info[2]) for info in connection.execute(f'PRAGMA index_info("{name}")')
        )
        spec[name] = (int(row[2]), int(row[4]), columns)
    return spec


def _check_constraints(connection, table):
    """Every CHECK clause of a table, whitespace-normalised and sorted.

    PRAGMA does not expose CHECKs, so they come out of ``sqlite_master`` by
    scanning for balanced parentheses after each CHECK keyword — a plain regex
    would stop at the first ')' inside a nested expression.
    """
    sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()[0]
    sql = re.sub(r"--[^\n]*", " ", sql)
    found = []
    for match in re.finditer(r"\bCHECK\b\s*\(", sql, re.IGNORECASE):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(sql)):
            if sql[index] == "(":
                depth += 1
            elif sql[index] == ")":
                depth -= 1
                if depth == 0:
                    found.append(" ".join(sql[start : index + 1].split()))
                    break
    return sorted(found)


@pytest.fixture
def migrated_schema(tmp_path):
    """A database holding only what 0001_init.sql creates."""
    connection = db.open_db(tmp_path / "migration-shape.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def rebuilt_schema(tmp_path):
    """A database holding only what the rebuild DDL creates."""
    connection = sqlite3.connect(str(tmp_path / "rebuild-shape.db"))
    try:
        for statement in (
            *anki_snapshot._ANKI_CARDS_DDL,
            *anki_snapshot._MIRROR_META_DDL,
        ):
            connection.execute(statement)
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize("table", sorted(_ADDED_COLUMNS))
def test_the_rebuild_ddl_matches_the_migration(
    table, migrated_schema, rebuilt_schema
):
    """The rebuild DDL duplicates 0001_init.sql; nothing else pins them together.

    ``_ANKI_CARDS_DDL`` / ``_MIRROR_META_DDL`` restate tables the migration also
    creates, so a column type, a NOT NULL, an index or a CHECK edited in one
    place and not the other would produce two different ``anki_cards`` depending
    on whether a rebuild had ever run — and no existing test would notice. The
    columns B1 deliberately added are the only permitted difference; anything
    else, in either direction, fails here and has to be reconciled (or, if the
    divergence is intended, moved into the allow-lists above with a reason).
    """
    added_columns = _ADDED_COLUMNS[table]
    assert _column_spec(rebuilt_schema, table, frozenset()) != _column_spec(
        migrated_schema, table, frozenset()
    ), "the added columns are missing from the rebuild DDL"

    assert _column_spec(rebuilt_schema, table, added_columns) == _column_spec(
        migrated_schema, table, frozenset()
    )
    assert set(added_columns) <= {
        name for name, *_ in _column_spec(rebuilt_schema, table, frozenset())
    }
    assert _index_spec(rebuilt_schema, table, _ADDED_INDEXES[table]) == _index_spec(
        migrated_schema, table, frozenset()
    )
    assert _check_constraints(rebuilt_schema, table) == _check_constraints(
        migrated_schema, table
    )


def test_the_known_set_view_still_reads_a_rebuilt_mirror(conn, collection):
    """The rebuild must not leave the view pointing at a table that is gone."""
    snapshot_anki(conn, collection_path=collection)
    conn.execute(
        "INSERT INTO anki_item_map (note_id, item_id, method) VALUES (1, 'w-1', 't')"
    )
    conn.execute(
        "INSERT INTO item (id, kind, kanji, created_ts) "
        "VALUES ('w-1', 'word', '日本語', '2026-08-01T00:00:00Z')"
    )
    row = conn.execute(
        "SELECT is_known FROM known_set WHERE item_id = 'w-1'"
    ).fetchone()
    assert row is not None and row["is_known"] == 1


def test_schema_18_collections_read_decks_and_notetypes_from_tables(conn, anki_dir):
    path = build_collection(anki_dir / "User 1" / "collection.anki2", ver=18)
    result = snapshot_anki(conn, collection_path=path)

    assert result.schema_version == 18
    assert (result.cards, result.notes) == (len(CARDS), len(NOTES))
    decks = dict(conn.execute("SELECT card_id, deck FROM anki_cards"))
    # Schema 18's \x1f deck-name separator is normalised to '::'.
    assert decks[101] == "Japanese::Core"
    assert decks[104] == "Default"
    models = dict(conn.execute("SELECT note_id, model FROM anki_notes"))
    assert models[3] == "Cloze"


# ---------------------------------------------------------------------------
# Rebuild semantics
# ---------------------------------------------------------------------------


def test_rerunning_is_idempotent(conn, collection):
    first = snapshot_anki(conn, collection_path=collection)
    snapshot_anki(conn, collection_path=collection)
    second = snapshot_anki(conn, collection_path=collection)

    assert (second.cards, second.notes) == (first.cards, first.notes)
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == len(CARDS)
    assert conn.execute("SELECT COUNT(*) FROM anki_notes").fetchone()[0] == len(NOTES)
    assert conn.execute("SELECT COUNT(*) FROM mirror_meta").fetchone()[0] == 1


def test_rebuild_drops_rows_that_anki_no_longer_has(conn, collection):
    """Derived tables are rebuilt, not merged: deletions must propagate."""
    snapshot_anki(conn, collection_path=collection)
    conn.execute(
        "INSERT INTO anki_cards(card_id, note_id, ivl) VALUES (999, 1, 999)"
    )
    conn.execute("INSERT INTO anki_notes(note_id, model) VALUES (998, 'ghost')")

    snapshot_anki(conn, collection_path=collection)

    assert conn.execute(
        "SELECT COUNT(*) FROM anki_cards WHERE card_id = 999"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM anki_notes WHERE note_id = 998"
    ).fetchone()[0] == 0


def test_snapshot_leaves_the_note_to_item_crosswalk_alone(conn, collection):
    """``anki_item_map`` belongs to the mapping step, not to the snapshot."""
    conn.execute(
        "INSERT INTO anki_item_map(note_id, item_id, method) VALUES (1, 'w-1', 'test')"
    )
    snapshot_anki(conn, collection_path=collection)
    assert conn.execute("SELECT COUNT(*) FROM anki_item_map").fetchone()[0] == 1


def test_a_failed_snapshot_leaves_the_previous_mirror_intact(conn, collection):
    good = snapshot_anki(conn, collection_path=collection)
    stamp = conn.execute("SELECT snapshot_ts FROM mirror_meta").fetchone()[0]

    broken = build_collection(
        collection.parent.parent / "Broken" / "collection.anki2", ver=17
    )
    with pytest.raises(UnsupportedAnkiSchemaError):
        snapshot_anki(conn, collection_path=broken)

    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == good.cards
    assert conn.execute("SELECT snapshot_ts FROM mirror_meta").fetchone()[0] == stamp


# ---------------------------------------------------------------------------
# The live file is never opened
# ---------------------------------------------------------------------------


def test_only_a_scratch_copy_is_opened_and_only_as_immutable(
    conn, collection, monkeypatch
):
    calls = install_connect_spy(monkeypatch)
    snapshot_anki(conn, collection_path=collection)

    assert calls, "the snapshot opened no database at all"
    # Nothing under the Anki profile directory was ever handed to sqlite.
    assert not any(str(collection) in call for call in calls)
    assert not any(collection.parent.as_posix() in call for call in calls)
    # Every open targeted the scratch copy.
    marker = scratch_dir().as_posix()
    assert all(marker in Path(call.split("?")[0].removeprefix("file:")).as_posix()
               for call in calls)
    # The connection that reads the data is read-only and immutable.
    read_uris = [call for call in calls if "immutable=1" in call]
    assert len(read_uris) == 1
    assert "mode=ro" in read_uris[0]


def test_the_live_collection_is_not_modified(conn, collection):
    before = collection.stat()
    before_bytes = collection.read_bytes()
    siblings_before = sorted(p.name for p in collection.parent.iterdir())

    snapshot_anki(conn, collection_path=collection)

    after = collection.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert collection.read_bytes() == before_bytes
    # No -wal/-shm left behind next to the learner's collection.
    assert sorted(p.name for p in collection.parent.iterdir()) == siblings_before


def test_the_scratch_copy_is_removed_afterwards(conn, collection):
    snapshot_anki(conn, collection_path=collection)
    leftovers = list(scratch_dir().rglob("*.anki2"))
    assert leftovers == []


def test_the_scratch_copy_is_removed_even_when_the_snapshot_fails(conn, anki_dir):
    path = build_collection(anki_dir / "User 1" / "collection.anki2", ver=99)
    with pytest.raises(UnsupportedAnkiSchemaError):
        snapshot_anki(conn, collection_path=path)
    assert list(scratch_dir().rglob("*.anki2")) == []


# ---------------------------------------------------------------------------
# WAL handling
# ---------------------------------------------------------------------------


@pytest.fixture
def wal_bound_collection(anki_dir):
    """A WAL-mode collection with one note+card reachable only via the ``-wal``.

    Recreates what a running Anki looks like from outside: a reader holds a
    snapshot open, so the writer's newest commits cannot be checkpointed into
    the main file and live only in the ``-wal``.
    """
    path = build_collection(anki_dir / "User 1" / "collection.anki2", wal=True)
    reader = sqlite3.connect(str(path))
    writer = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM cards").fetchone()
    writer.execute(
        "INSERT INTO notes VALUES (4, 'guid4', ?, 1700000009, -1, ' wal ', "
        "'掛かる\x1fto take (time)', '掛かる', 0, 0, '')",
        (MODEL_BASIC,),
    )
    writer.execute(
        "INSERT INTO cards VALUES (105, 4, ?, 0, 1700000009, -1, 2, 2, 700, 99, "
        "2500, 4, 0, 0, 0, 0, 0, '')",
        (DECK_CORE,),
    )
    wal = Path(str(path) + "-wal")
    assert wal.is_file() and wal.stat().st_size > 0, "fixture left no WAL to recover"
    try:
        yield path
    finally:
        writer.close()
        reader.close()


def test_the_wal_only_row_is_invisible_without_recovery(wal_bound_collection, tmp_path):
    """Pins the hazard the recovery step exists for.

    ``immutable=1`` ignores the ``-wal`` outright, so a copy of the main file
    alone reads the collection as of its last checkpoint. That is why this module
    recovers the journal before reading: rebuilding the mirror by DELETE+INSERT
    from a stale read would not merely miss a row, it would *replace* a good
    mirror with an emptier one.
    """
    naive = tmp_path / "naive.anki2"
    naive.write_bytes(wal_bound_collection.read_bytes())  # main file only
    read = sqlite3.connect(anki_snapshot._immutable_uri(naive), uri=True)
    try:
        assert read.execute("SELECT COUNT(*) FROM cards WHERE id = 105").fetchone()[
            0
        ] == 0
    finally:
        read.close()


def test_data_living_only_in_an_uncheckpointed_wal_is_still_mirrored(
    conn, wal_bound_collection
):
    result = snapshot_anki(conn, collection_path=wal_bound_collection)

    assert (result.cards, result.notes) == (len(CARDS) + 1, len(NOTES) + 1)
    row = conn.execute("SELECT * FROM anki_cards WHERE card_id = 105").fetchone()
    assert row is not None, "the WAL-only card never reached the mirror"
    assert (row["note_id"], row["ivl"]) == (4, 99)
    assert json.loads(
        conn.execute("SELECT fields FROM anki_notes WHERE note_id = 4").fetchone()[0]
    ) == ["掛かる", "to take (time)"]
    # A live journal is provenance, not an error: the mirror is complete but the
    # collection was moving while it was taken.
    assert result.stale is True


def test_a_closed_collection_is_not_flagged_stale(conn, collection):
    assert snapshot_anki(conn, collection_path=collection).stale is False


def test_a_running_anki_warns_and_flags_the_snapshot_stale(
    conn, collection, monkeypatch, log_records
):
    monkeypatch.setattr(anki_snapshot, "anki_is_running", lambda: True)
    result = snapshot_anki(conn, collection_path=collection)

    assert result.stale is True
    assert result.cards == len(CARDS)  # warned, but proceeded on the copy
    warnings = [
        record.getMessage()
        for record in log_records
        if record.levelno >= logging.WARNING
    ]
    assert any("running" in message.lower() for message in warnings), warnings


# ---------------------------------------------------------------------------
# Integrity and schema gating
# ---------------------------------------------------------------------------


def _truncate(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.truncate(path.stat().st_size // 2)


def _rot_every_page_but_the_first(path: Path) -> None:
    """Leave page 1 (header + schema) intact, garble the b-trees behind it.

    Deterministic where picking one page number is not: the file opens, the
    schema still parses, and the damage is only discoverable by walking the
    tables — which is precisely what integrity_check does.
    """
    page_size = 4096
    with path.open("r+b") as handle:
        size = path.stat().st_size
        handle.seek(page_size)
        handle.write(b"\xde\xad\xbe\xef" * ((size - page_size) // 4))


def _blank_the_header(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"NOT A DATABASE")


@pytest.mark.parametrize(
    "damage", [_truncate, _rot_every_page_but_the_first, _blank_the_header],
    ids=["truncated", "rotted-pages", "no-header"],
)
def test_a_corrupt_collection_fails_loudly_and_writes_nothing(conn, anki_dir, damage):
    path = build_collection(
        anki_dir / "User 1" / "collection.anki2", filler=400
    )
    damage(path)

    with pytest.raises(AnkiIntegrityError) as excinfo:
        snapshot_anki(conn, collection_path=path)

    assert str(path.name) in str(excinfo.value) or "collection" in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM mirror_meta").fetchone()[0] == 0


def add_unicase_name_index(path: Path) -> Path:
    """Add a real index collated with Anki's own 'unicase' sequence.

    ``_DECKS_DDL``/``_NOTETYPES_DDL`` already declare ``name`` as
    ``collate unicase`` (see the comment above them), but a column-level
    collation alone does not make ``PRAGMA integrity_check`` resolve it — only
    an *index* ordered by that collation does, because integrity_check walks
    index b-trees and must compare keys to verify they are sorted. Real Anki
    creates exactly such a unique index over ``decks.name`` (to enforce
    case-insensitive uniqueness); this reproduces that with the smallest
    possible addition to the existing fixture.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.create_collation("unicase", _unicase)
    try:
        conn.execute("CREATE UNIQUE INDEX idx_decks_name ON decks (name)")
    finally:
        conn.close()
    return path


def test_a_unicase_collated_index_is_not_misdiagnosed_as_corrupt(conn, anki_dir):
    """Modern (Rust-backend) Anki indexes with its own 'unicase' collation.

    Python's sqlite3 has no native definition for it, so without a registered
    fallback even ``PRAGMA integrity_check`` on a perfectly healthy schema-18
    collection fails with "no such collation sequence: unicase" — which is not
    evidence of a corrupt collection, and the snapshot must not misreport it
    as one.
    """
    path = add_unicase_name_index(
        build_collection(anki_dir / "User 1" / "collection.anki2", ver=18)
    )

    result = snapshot_anki(conn, collection_path=path)

    assert result.schema_version == 18
    assert (result.cards, result.notes) == (len(CARDS), len(NOTES))


def test_a_genuinely_unresolvable_collation_still_says_collation_not_corruption(
    conn, anki_dir, monkeypatch
):
    """If some other, unregistered collation still slips through, the error
    must name the real cause rather than repeating the generic corruption
    verdict this same PRAGMA gives for actual damage."""
    path = add_unicase_name_index(
        build_collection(anki_dir / "User 1" / "collection.anki2", ver=18)
    )
    monkeypatch.setattr(
        anki_snapshot, "_register_fallback_collations", lambda conn: None
    )

    with pytest.raises(AnkiIntegrityError, match="collation") as excinfo:
        snapshot_anki(conn, collection_path=path)

    # The misdiagnosis this fix removes: real damage says "appears corrupt"
    # and sends the learner to Anki's Check Database, which would find
    # nothing wrong here.
    assert "appears corrupt" not in str(excinfo.value)
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 0


@pytest.mark.parametrize("ver", [0, 3, 12, 17, 19, 99])
def test_an_unsupported_schema_version_is_refused(conn, anki_dir, ver):
    path = build_collection(anki_dir / "User 1" / "collection.anki2", ver=ver)

    with pytest.raises(UnsupportedAnkiSchemaError) as excinfo:
        snapshot_anki(conn, collection_path=path)

    assert excinfo.value.schema_version == ver
    message = str(excinfo.value)
    assert str(ver) in message and "11" in message and "18" in message
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 0


def test_supported_versions_are_exactly_the_documented_pair():
    assert anki_snapshot.SUPPORTED_SCHEMA_VERSIONS == frozenset({11, 18})


def test_a_file_that_is_not_a_collection_is_refused(conn, anki_dir):
    path = anki_dir / "User 1" / "collection.anki2"
    path.parent.mkdir(parents=True, exist_ok=True)
    other = sqlite3.connect(str(path))
    other.execute("CREATE TABLE unrelated (x)")
    other.commit()
    other.close()

    with pytest.raises(UnsupportedAnkiSchemaError, match="col"):
        snapshot_anki(conn, collection_path=path)


def test_a_collection_missing_its_cards_table_is_refused(conn, anki_dir):
    path = build_collection(anki_dir / "User 1" / "collection.anki2")
    scratch = sqlite3.connect(str(path), isolation_level=None)
    scratch.execute("DROP TABLE cards")
    scratch.close()

    with pytest.raises(UnsupportedAnkiSchemaError, match="cards"):
        snapshot_anki(conn, collection_path=path)


# ---------------------------------------------------------------------------
# Locating the collection
# ---------------------------------------------------------------------------


def test_the_collection_is_found_under_a_profile_directory(collection, anki_dir):
    assert anki_snapshot.find_collection() == collection


def test_a_data_dir_that_is_itself_a_profile_is_accepted(anki_dir):
    path = build_collection(anki_dir / "collection.anki2")
    assert anki_snapshot.find_collection() == path


def test_several_profiles_raise_and_list_every_candidate(anki_dir):
    first = build_collection(anki_dir / "User 1" / "collection.anki2")
    second = build_collection(anki_dir / "Testing" / "collection.anki2")

    with pytest.raises(AnkiCollectionAmbiguousError) as excinfo:
        anki_snapshot.find_collection()

    assert set(excinfo.value.candidates) == {first, second}
    message = str(excinfo.value)
    assert str(first) in message and str(second) in message
    assert "collection_path" in message


def test_no_collection_anywhere_raises(anki_dir):
    (anki_dir / "Empty Profile").mkdir()
    with pytest.raises(AnkiCollectionNotFoundError, match="collection.anki2"):
        anki_snapshot.find_collection()


def test_a_missing_data_dir_raises(local_app_data):
    missing = local_app_data / "nope"
    with pytest.raises(AnkiCollectionNotFoundError, match="does not exist"):
        anki_snapshot.find_collection(missing)


def test_an_explicit_missing_collection_path_raises(conn, anki_dir):
    with pytest.raises(AnkiCollectionNotFoundError):
        snapshot_anki(conn, collection_path=anki_dir / "ghost.anki2")


# ---------------------------------------------------------------------------
# Process detection
# ---------------------------------------------------------------------------


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("anki.exe                      12345 Console       1    412,320 K\n", True),
        ("ANKI.EXE                      12345 Console       1    412,320 K\n", True),
        ("INFO: No tasks are running which match the specified criteria.\n", False),
        ("", False),
    ],
)
def test_anki_is_running_reads_tasklist_output(monkeypatch, stdout, expected):
    monkeypatch.setattr(anki_snapshot, "_on_windows", lambda: True)
    monkeypatch.setattr(
        anki_snapshot.subprocess, "run", lambda *a, **kw: _Completed(stdout)
    )
    assert anki_snapshot.anki_is_running() is expected


@pytest.mark.parametrize("failure", [OSError("no tasklist"), TimeoutError("slow")])
def test_anki_is_running_never_raises(monkeypatch, failure):
    """A failed check costs the warning, never the snapshot."""
    monkeypatch.setattr(anki_snapshot, "_on_windows", lambda: True)

    def boom(*args, **kwargs):
        raise failure

    monkeypatch.setattr(anki_snapshot.subprocess, "run", boom)
    assert anki_snapshot.anki_is_running() is False


def test_anki_is_running_is_false_off_windows(monkeypatch):
    monkeypatch.setattr(anki_snapshot, "_on_windows", lambda: False)
    monkeypatch.setattr(
        anki_snapshot.subprocess, "run", lambda *a, **kw: pytest.fail("shelled out")
    )
    assert anki_snapshot.anki_is_running() is False


def test_the_process_check_is_a_fixed_argv_with_a_timeout(monkeypatch):
    """No shell, no interpolated input, and it can never hang a snapshot."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(anki_snapshot, "_on_windows", lambda: True)

    def record(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return _Completed("")

    monkeypatch.setattr(anki_snapshot.subprocess, "run", record)
    anki_snapshot.anki_is_running()

    assert isinstance(seen["argv"], list) and seen["argv"][0] == "tasklist"
    assert seen.get("shell") in (None, False)
    assert isinstance(seen.get("timeout"), (int, float)) and seen["timeout"] > 0


# ---------------------------------------------------------------------------
# The known_set view over mirrored data
# ---------------------------------------------------------------------------


def _add_item(connection: sqlite3.Connection, item_id: str) -> None:
    connection.execute(
        "INSERT INTO item(id, kind, created_ts) VALUES (?, 'word', ?)",
        (item_id, "2026-01-01T00:00:00Z"),
    )


def test_known_set_follows_the_mirror_once_a_snapshot_has_run(conn, collection):
    for note_id in NOTES:
        item_id = f"w-{note_id}"
        _add_item(conn, item_id)
        conn.execute(
            "INSERT INTO anki_item_map(note_id, item_id, method) "
            "VALUES (?, ?, 'test')",
            (note_id, item_id),
        )

    # Before the snapshot the mirror is empty, so nothing is known.
    assert dict(conn.execute("SELECT item_id, is_known FROM known_set")) == {
        "w-1": 0, "w-2": 0, "w-3": 0
    }

    snapshot_anki(conn, collection_path=collection)

    known = dict(conn.execute("SELECT item_id, is_known FROM known_set"))
    assert known == {
        "w-1": 1,  # ivl 30
        "w-2": 0,  # ivl 5, still young
        "w-3": 1,  # ivl exactly 21, the maturity boundary
    }
    assert {int(item_id.split("-")[1]) for item_id, is_known in known.items()
            if is_known} == MATURE_NOTE_IDS
    assert {row["source"] for row in conn.execute("SELECT source FROM known_set")} == {
        "anki"
    }


def test_a_manual_mark_still_overrides_the_freshly_mirrored_anki_state(
    conn, collection
):
    _add_item(conn, "w-1")
    conn.execute(
        "INSERT INTO anki_item_map(note_id, item_id) VALUES (1, 'w-1')"
    )
    conn.execute(
        "INSERT INTO manual_marks(item_id, mark, ts) "
        "VALUES ('w-1', 'unknown', '2026-02-02T00:00:00Z')"
    )

    snapshot_anki(conn, collection_path=collection)

    row = conn.execute("SELECT * FROM known_set WHERE item_id = 'w-1'").fetchone()
    assert (row["is_known"], row["source"]) == (0, "manual")


def test_the_snapshot_timestamp_moves_forward_on_a_later_run(conn, collection):
    snapshot_anki(conn, collection_path=collection)
    first = conn.execute("SELECT snapshot_ts FROM mirror_meta").fetchone()[0]
    time.sleep(1.05)  # whole-second resolution: the schema forbids fractions
    snapshot_anki(conn, collection_path=collection)
    second = conn.execute("SELECT snapshot_ts FROM mirror_meta").fetchone()[0]
    assert second > first
