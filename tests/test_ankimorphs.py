"""AnkiMorphs ingest tests: schema variance, copy-then-read, per-source rebuild.

The fixtures build real ``ankimorphs.db`` files — current layout, the older
``base``/``inflected`` one, a lemma-only one, and MorphMan's ``morphemes`` table
— rather than mocking sqlite, because what is under test is how the module
*reads a file it did not write*: which columns it recognises, whether it ever
touches the live file, and whether a rebuild is atomic. A mock would assert the
implementation back to itself.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from katagiri import ankimorphs_ingest
from katagiri import config as config_mod
from katagiri import db
from katagiri.ankimorphs_ingest import (
    MORPHS_TABLE,
    AnkiMorphsDbAmbiguousError,
    AnkiMorphsDbNotFoundError,
    AnkiMorphsIntegrityError,
    KnownMorphsCsvError,
    UnsupportedAnkiMorphsSchemaError,
    find_ankimorphs_db,
    ingest_ankimorphs_db,
    ingest_known_morphs_csv,
    known_morph_count,
)

# ---------------------------------------------------------------------------
# Fixture add-on databases (AnkiMorphs' own DDL, trimmed to what is read)
# ---------------------------------------------------------------------------

# Current AnkiMorphs: knowledge is tracked twice, once per lemma and once per
# inflection, so a lemma can be mature while one of its inflected forms is new.
_CURRENT_DDL = """
CREATE TABLE Morphs (
    lemma TEXT NOT NULL,
    inflection TEXT NOT NULL,
    highest_lemma_learning_interval INTEGER,
    highest_inflection_learning_interval INTEGER,
    PRIMARY KEY (lemma, inflection)
);
CREATE TABLE Cards (
    card_id INTEGER PRIMARY KEY ASC, note_id INTEGER, note_type_id INTEGER,
    card_type INTEGER, tags TEXT
);
CREATE TABLE Card_Morph_Map (
    card_id INTEGER, morph_lemma TEXT, morph_inflection TEXT,
    PRIMARY KEY (card_id, morph_lemma, morph_inflection)
);
CREATE TABLE Seen_Morphs (
    lemma TEXT, inflection TEXT, PRIMARY KEY (lemma, inflection)
);
"""

# The older layout: one interval for both granularities, and the lemma column is
# called 'base' with a separate normalised form in 'norm'.
_LEGACY_DDL = """
CREATE TABLE Morphs (
    norm TEXT NOT NULL,
    base TEXT NOT NULL,
    inflected TEXT NOT NULL,
    is_base INTEGER NOT NULL,
    highest_learning_interval INTEGER,
    PRIMARY KEY (norm, base, inflected)
);
"""

# A build that tracks lemmas only — no inflection column at all.
_LEMMA_ONLY_DDL = """
CREATE TABLE Morphs (
    lemma TEXT NOT NULL PRIMARY KEY,
    highest_lemma_learning_interval INTEGER
);
"""

# MorphMan, the ancestor: different table name, no interval column anywhere.
_MORPHMAN_DDL = """
CREATE TABLE morphemes (
    norm TEXT NOT NULL, base TEXT NOT NULL, inflected TEXT NOT NULL,
    read TEXT, pos TEXT, subPos TEXT,
    PRIMARY KEY (norm, base, inflected)
);
"""

# (lemma, inflection, highest_lemma_ivl, highest_inflection_ivl)
CURRENT_MORPHS = [
    ("読む", "読む", 120, 120),
    ("読む", "読んだ", 120, 30),
    ("読む", "読まない", 120, 4),
    ("猫", "猫", 365, 365),
    ("掛かる", "掛かった", 21, 21),  # exactly at the maturity threshold
    ("難しい", "難しかった", 20, 20),  # one day short of it
    ("食べる", "食べる", 0, 0),
    ("森", "森", None, None),  # seen, never scheduled
]

# lemma_ivl >= 21: every 読む row (lemma knowledge is per-lemma), 猫, 掛かった.
KNOWN_AT_21 = 5


def build_morphs_db(path: Path, *, layout: str = "current", wal: bool = False) -> Path:
    """Write a structurally real ``ankimorphs.db`` at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode = WAL")
        if layout == "current":
            conn.executescript(_CURRENT_DDL)
            conn.executemany(
                "INSERT INTO Morphs VALUES (?, ?, ?, ?)", CURRENT_MORPHS
            )
            conn.executemany(
                "INSERT INTO Cards VALUES (?, ?, ?, ?, ?)",
                [(1, 10, 100, 0, "japanese"), (2, 11, 100, 0, "")],
            )
            conn.executemany(
                "INSERT INTO Card_Morph_Map VALUES (?, ?, ?)",
                [(1, "読む", "読んだ"), (2, "猫", "猫")],
            )
        elif layout == "legacy":
            conn.executescript(_LEGACY_DDL)
            conn.executemany(
                "INSERT INTO Morphs VALUES (?, ?, ?, ?, ?)",
                [
                    ("読む", "読む", "読んだ", 0, 45),
                    ("猫", "猫", "猫", 1, 365),
                    ("森", "森", "森", 1, 3),
                ],
            )
        elif layout == "lemma_only":
            conn.executescript(_LEMMA_ONLY_DDL)
            conn.executemany(
                "INSERT INTO Morphs VALUES (?, ?)",
                [("読む", 45), ("猫", 365), ("森", 3)],
            )
        elif layout == "morphman":
            conn.executescript(_MORPHMAN_DDL)
            conn.executemany(
                "INSERT INTO morphemes VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("読む", "読む", "読んだ", "ヨンダ", "動詞", "自立"),
                    ("猫", "猫", "猫", "ネコ", "名詞", "一般"),
                ],
            )
        elif layout == "no_morph_table":
            conn.executescript(
                "CREATE TABLE Frobs (id INTEGER PRIMARY KEY, whatever TEXT);"
            )
        elif layout == "no_lemma_column":
            conn.executescript(
                "CREATE TABLE Morphs (id INTEGER PRIMARY KEY, occurrences INTEGER);"
            )
            conn.execute("INSERT INTO Morphs VALUES (1, 7)")
        else:  # pragma: no cover - fixture misuse
            raise AssertionError(f"unknown layout {layout!r}")
    finally:
        conn.close()
    return path


def write_csv(path: Path, text: str, *, bom: bool = False) -> Path:
    """Write a known-morphs CSV, optionally with a UTF-8 BOM in front."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8", newline="")
    return path


BOTH_COLUMNS_CSV = "Morph-Lemma,Morph-Inflection\n読む,読んだ\n猫,猫\n掛かる,掛かった\n"
LEMMA_ONLY_CSV = "Morph-Lemma\n読む\n猫\n食べる\n"


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
def morphs_db(anki_dir):
    """A current-layout AnkiMorphs database inside a single profile directory."""
    return build_morphs_db(anki_dir / "User 1" / "ankimorphs.db")


@pytest.fixture
def conn(anki_dir, monkeypatch):
    """A migrated Katagiri database, with the Anki process check pinned off.

    Pinned because the real check shells out to ``tasklist``: a developer with
    Anki open would otherwise see ``stale`` flip under them.
    """
    monkeypatch.setattr(ankimorphs_ingest, "anki_is_running", lambda: False)
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
    caplog's root handler.
    """
    logger = logging.getLogger("katagiri.ankimorphs_ingest")
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
    return config_mod.get_config().scratch_root / "ankimorphs-ingest"


def install_connect_spy(monkeypatch) -> list[str]:
    """Record every ``sqlite3.connect`` target from here on, delegating through."""
    calls: list[str] = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):
        calls.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    return calls


def stored(conn: sqlite3.Connection, source: str | None = None) -> dict:
    """``(lemma, inflection, source)`` -> row, for the whole derived table."""
    sql = f"SELECT * FROM {MORPHS_TABLE}"
    params: tuple = ()
    if source is not None:
        sql += " WHERE source = ?"
        params = (source,)
    return {
        (row["lemma"], row["inflection"], row["source"]): row
        for row in conn.execute(sql, params)
    }


# ---------------------------------------------------------------------------
# Ingesting the add-on database
# ---------------------------------------------------------------------------


def test_ingest_populates_the_derived_table(conn, morphs_db):
    result = ingest_ankimorphs_db(conn, morphs_db)

    assert (result.morphs, result.source, result.stale) == (
        len(CURRENT_MORPHS),
        "db",
        False,
    )
    rows = stored(conn)
    assert len(rows) == len(CURRENT_MORPHS)
    assert set(rows) == {(lemma, infl, "db") for lemma, infl, _, _ in CURRENT_MORPHS}

    read = rows[("読む", "読んだ", "db")]
    assert (read["lemma_ivl"], read["inflection_ivl"]) == (120, 30)
    seen = rows[("森", "森", "db")]
    assert (seen["lemma_ivl"], seen["inflection_ivl"]) == (None, None)
    # Same 20-character ISO-8601 UTC shape as every timestamp in the schema.
    assert len(read["imported_ts"]) == 20 and read["imported_ts"].endswith("Z")


def test_the_table_is_created_on_first_ingest_not_by_a_migration(conn, morphs_db):
    """The derived table is this module's to create; no migration ships it."""
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MORPHS_TABLE,)
    ).fetchone()[0] == 0

    ingest_ankimorphs_db(conn, morphs_db)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MORPHS_TABLE,)
    ).fetchone()[0] == 1


def test_legacy_column_names_are_detected(conn, anki_dir):
    """``base``/``inflected`` and a single shared interval column."""
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db", layout="legacy")
    result = ingest_ankimorphs_db(conn, path)

    assert result.morphs == 3
    rows = stored(conn)
    assert ("読む", "読んだ", "db") in rows
    read = rows[("読む", "読んだ", "db")]
    # One source column feeds both granularities when that is all there is.
    assert (read["lemma_ivl"], read["inflection_ivl"]) == (45, 45)
    # 'norm' is not mistaken for the lemma when 'base' is present.
    assert ("読む", "読む", "db") not in rows


def test_a_missing_inflection_column_stores_an_empty_inflection(conn, anki_dir):
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db", layout="lemma_only")
    result = ingest_ankimorphs_db(conn, path)

    assert result.morphs == 3
    assert set(stored(conn)) == {
        ("読む", "", "db"),
        ("猫", "", "db"),
        ("森", "", "db"),
    }


def test_the_morphman_era_table_is_read_without_intervals(
    conn, anki_dir, log_records
):
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db", layout="morphman")
    result = ingest_ankimorphs_db(conn, path)

    assert result.morphs == 2
    rows = stored(conn)
    assert rows[("読む", "読んだ", "db")]["lemma_ivl"] is None
    # No interval column means nothing from this database can be known by
    # interval, and that must be said out loud rather than read as "all new".
    assert known_morph_count(conn) == 0
    warnings = [
        record.getMessage()
        for record in log_records
        if record.levelno >= logging.WARNING
    ]
    assert any("learning-interval" in message for message in warnings), warnings


@pytest.mark.parametrize("layout", ["no_morph_table", "no_lemma_column"])
def test_an_unrecognised_layout_raises_instead_of_guessing(conn, anki_dir, layout):
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db", layout=layout)

    with pytest.raises(UnsupportedAnkiMorphsSchemaError) as excinfo:
        ingest_ankimorphs_db(conn, path)

    message = str(excinfo.value)
    # The error must be actionable: it names what was found, not just "bad file".
    assert str(path) in message
    if layout == "no_morph_table":
        assert "Frobs" in message
        assert excinfo.value.table is None
    else:
        assert excinfo.value.table == "Morphs"
        assert excinfo.value.columns == ("id", "occurrences")
        assert "occurrences" in message

    # A failed ingest writes nothing at all — not even the empty table.
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MORPHS_TABLE,)
    ).fetchone()[0] == 0


def test_a_corrupt_database_is_refused(conn, anki_dir):
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db")
    raw = bytearray(path.read_bytes())
    # Scribble over everything after the first page, so the file still declares
    # itself a database (header and page count intact) but its b-trees are gone.
    assert len(raw) > 4096, "fixture database is a single page; nothing to corrupt"
    raw[4096:] = b"\xa5" * (len(raw) - 4096)
    path.write_bytes(bytes(raw))

    with pytest.raises(AnkiMorphsIntegrityError):
        ingest_ankimorphs_db(conn, path)


def test_a_missing_database_names_every_place_it_looked(anki_dir):
    with pytest.raises(AnkiMorphsDbNotFoundError) as excinfo:
        find_ankimorphs_db(anki_dir)

    message = str(excinfo.value)
    assert "addons21" in message and "user_files" in message
    assert str(anki_dir) in message
    assert excinfo.value.searched, "the error carries no searched paths"


def test_an_explicit_path_that_does_not_exist_is_reported(conn, tmp_path):
    with pytest.raises(AnkiMorphsDbNotFoundError):
        ingest_ankimorphs_db(conn, tmp_path / "nope" / "ankimorphs.db")


@pytest.mark.parametrize(
    "relative",
    [
        "ankimorphs.db",
        "User 1/ankimorphs.db",
        "User 1/dbs/ankimorphs.db",
        "addons21/1974309724/ankimorphs.db",
        "addons21/1974309724/data/ankimorphs.db",
        "addons21/1974309724/user_files/ankimorphs.db",
    ],
)
def test_discovery_finds_the_database_wherever_the_addon_put_it(anki_dir, relative):
    path = build_morphs_db(anki_dir / relative)
    assert find_ankimorphs_db(anki_dir) == path


def test_two_databases_are_an_ambiguity_not_a_coin_toss(anki_dir):
    build_morphs_db(anki_dir / "User 1" / "ankimorphs.db")
    build_morphs_db(anki_dir / "Japanese" / "ankimorphs.db")

    with pytest.raises(AnkiMorphsDbAmbiguousError) as excinfo:
        find_ankimorphs_db(anki_dir)
    assert len(excinfo.value.candidates) == 2
    assert "db_path" in str(excinfo.value)


def test_discovery_is_used_when_no_path_is_given(conn, morphs_db):
    """No db_path: the configured anki_data_dir is searched."""
    assert ingest_ankimorphs_db(conn).morphs == len(CURRENT_MORPHS)


# ---------------------------------------------------------------------------
# The live file is never opened
# ---------------------------------------------------------------------------


def test_only_a_scratch_copy_is_opened_and_only_as_immutable(
    conn, morphs_db, monkeypatch
):
    calls = install_connect_spy(monkeypatch)
    ingest_ankimorphs_db(conn, morphs_db)

    assert calls, "the ingest opened no database at all"
    # Nothing under the Anki profile directory was ever handed to sqlite.
    assert not any(str(morphs_db) in call for call in calls)
    assert not any(morphs_db.parent.as_posix() in call for call in calls)
    # Every open targeted the scratch copy.
    marker = scratch_dir().as_posix()
    assert all(
        marker in Path(call.split("?")[0].removeprefix("file:")).as_posix()
        for call in calls
    )
    # The connection that reads the morphs is read-only and immutable.
    read_uris = [call for call in calls if "immutable=1" in call]
    assert len(read_uris) == 1
    assert "mode=ro" in read_uris[0]


def test_the_live_database_is_not_modified(conn, morphs_db):
    before = morphs_db.stat()
    before_bytes = morphs_db.read_bytes()
    siblings_before = sorted(p.name for p in morphs_db.parent.iterdir())

    ingest_ankimorphs_db(conn, morphs_db)

    after = morphs_db.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    assert morphs_db.read_bytes() == before_bytes
    # No -wal/-shm left behind next to the add-on's database.
    assert sorted(p.name for p in morphs_db.parent.iterdir()) == siblings_before


def test_the_scratch_copy_is_removed_afterwards(conn, morphs_db):
    ingest_ankimorphs_db(conn, morphs_db)
    assert list(scratch_dir().rglob("*.db")) == []


def test_the_scratch_copy_is_removed_even_when_the_ingest_fails(conn, anki_dir):
    path = build_morphs_db(
        anki_dir / "User 1" / "ankimorphs.db", layout="no_morph_table"
    )
    with pytest.raises(UnsupportedAnkiMorphsSchemaError):
        ingest_ankimorphs_db(conn, path)
    assert list(scratch_dir().rglob("*.db")) == []


@pytest.fixture
def wal_bound_db(anki_dir):
    """A WAL-mode add-on database whose newest morph lives only in the ``-wal``.

    Recreates what a running Anki looks like from outside: a reader holds a
    snapshot open, so the writer's newest commits cannot be checkpointed into the
    main file.
    """
    path = build_morphs_db(anki_dir / "User 1" / "ankimorphs.db", wal=True)
    reader = sqlite3.connect(str(path))
    writer = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM Morphs").fetchone()
    writer.execute("INSERT INTO Morphs VALUES ('走る', '走った', 99, 60)")
    wal = Path(str(path) + "-wal")
    assert wal.is_file() and wal.stat().st_size > 0, "fixture left no WAL to recover"
    try:
        yield path
    finally:
        writer.close()
        reader.close()


def test_a_morph_living_only_in_an_uncheckpointed_wal_is_still_ingested(
    conn, wal_bound_db
):
    """Pins the hazard the recovery step exists for.

    ``immutable=1`` ignores the ``-wal``, so a copy of the main file alone reads
    the database as of its last checkpoint — and since each ingest is a rebuild,
    a stale read would *replace* good rows rather than merely miss one.
    """
    naive = sqlite3.connect(
        ankimorphs_ingest._immutable_uri(wal_bound_db), uri=True
    )
    try:
        assert naive.execute(
            "SELECT COUNT(*) FROM Morphs WHERE lemma = '走る'"
        ).fetchone()[0] == 0
    finally:
        naive.close()

    result = ingest_ankimorphs_db(conn, wal_bound_db)

    assert result.morphs == len(CURRENT_MORPHS) + 1
    assert ("走る", "走った", "db") in stored(conn)
    # A live journal is provenance, not an error.
    assert result.stale is True


def test_a_running_anki_warns_and_flags_the_result_stale(
    conn, morphs_db, monkeypatch, log_records
):
    monkeypatch.setattr(ankimorphs_ingest, "anki_is_running", lambda: True)
    result = ingest_ankimorphs_db(conn, morphs_db)

    assert result.stale is True
    assert result.morphs == len(CURRENT_MORPHS)  # warned, but proceeded on the copy
    assert any(
        "running" in record.getMessage()
        for record in log_records
        if record.levelno >= logging.WARNING
    )


# ---------------------------------------------------------------------------
# Rebuild semantics
# ---------------------------------------------------------------------------


def test_ingesting_the_same_database_twice_is_idempotent(conn, morphs_db):
    first = ingest_ankimorphs_db(conn, morphs_db)
    before = {key: dict(row) for key, row in stored(conn).items()}

    second = ingest_ankimorphs_db(conn, morphs_db)
    after = {key: dict(row) for key, row in stored(conn).items()}

    assert (first.morphs, second.morphs) == (len(CURRENT_MORPHS),) * 2
    assert set(before) == set(after)
    for key, row in after.items():
        # Everything but the import stamp is identical; no duplicate rows appeared.
        assert {k: v for k, v in row.items() if k != "imported_ts"} == {
            k: v for k, v in before[key].items() if k != "imported_ts"
        }


def test_a_rebuild_drops_morphs_the_addon_no_longer_has(conn, morphs_db):
    ingest_ankimorphs_db(conn, morphs_db)
    assert ("猫", "猫", "db") in stored(conn)

    live = sqlite3.connect(str(morphs_db), isolation_level=None)
    try:
        live.execute("DELETE FROM Morphs WHERE lemma = '猫'")
    finally:
        live.close()

    result = ingest_ankimorphs_db(conn, morphs_db)
    assert result.morphs == len(CURRENT_MORPHS) - 1
    assert ("猫", "猫", "db") not in stored(conn)


def test_duplicate_source_rows_are_merged_keeping_the_longest_interval(
    conn, tmp_path
):
    """A duplicated morph must not abort the rebuild on the primary key."""
    path = write_csv(
        tmp_path / "dupes.csv",
        "Morph-Lemma,Morph-Inflection,Highest-Lemma-Learning-Interval\n"
        "読む,読んだ,5\n"
        "読む,読んだ,90\n"
        "読む,読んだ,\n"
        "猫,猫,30\n",
    )
    result = ingest_known_morphs_csv(conn, path)

    assert result.morphs == 2
    rows = stored(conn)
    assert rows[("読む", "読んだ", "csv")]["lemma_ivl"] == 90


def test_the_two_sources_coexist_and_rebuild_independently(conn, morphs_db, tmp_path):
    """The primary key includes ``source``, so neither input can erase the other."""
    ingest_ankimorphs_db(conn, morphs_db)
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "known.csv", BOTH_COLUMNS_CSV))

    by_source = dict(
        conn.execute(f"SELECT source, COUNT(*) FROM {MORPHS_TABLE} GROUP BY source")
    )
    assert by_source == {"db": len(CURRENT_MORPHS), "csv": 3}
    # The same morph is present once per source, not collapsed into one row.
    assert ("読む", "読んだ", "db") in stored(conn)
    assert ("読む", "読んだ", "csv") in stored(conn)

    # Re-ingesting a *smaller* CSV rebuilds only the CSV rows.
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "small.csv", "Morph-Lemma\n猫\n"))
    by_source = dict(
        conn.execute(f"SELECT source, COUNT(*) FROM {MORPHS_TABLE} GROUP BY source")
    )
    assert by_source == {"db": len(CURRENT_MORPHS), "csv": 1}


def test_an_emptied_source_warns_before_rebuilding_empty(conn, tmp_path, log_records):
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "a.csv", BOTH_COLUMNS_CSV))
    log_records.clear()
    result = ingest_known_morphs_csv(
        conn, write_csv(tmp_path / "b.csv", "Morph-Lemma\n")
    )

    assert result.morphs == 0
    assert stored(conn, "csv") == {}
    assert any(
        "rebuilding it empty" in record.getMessage()
        for record in log_records
        if record.levelno >= logging.WARNING
    )


def test_a_failure_mid_rebuild_leaves_the_previous_rows_untouched(
    conn, morphs_db, tmp_path
):
    """Atomicity: DELETE + INSERT share one transaction, so a mid-insert abort
    restores the source's previous contents rather than truncating them."""
    ingest_ankimorphs_db(conn, morphs_db)
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "known.csv", BOTH_COLUMNS_CSV))
    before = {key: dict(row) for key, row in stored(conn).items()}

    # A real constraint failure part-way through the INSERT stream, rather than a
    # patched-out function: whatever aborts the statement, the rebuild must not
    # be observable half-done.
    conn.execute(
        f"CREATE TEMP TRIGGER boom AFTER INSERT ON main.{MORPHS_TABLE} "
        "WHEN NEW.lemma = 'BOOM' BEGIN SELECT RAISE(ABORT, 'boom'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            ingest_known_morphs_csv(
                conn,
                write_csv(tmp_path / "bad.csv", "Morph-Lemma\n犬\nBOOM\n"),
            )
    finally:
        conn.execute("DROP TRIGGER temp.boom")

    after = {key: dict(row) for key, row in stored(conn).items()}
    assert after == before
    assert not any(key[0] in {"犬", "BOOM"} for key in after)
    # The db-source rows were never in scope in the first place.
    assert len(stored(conn, "db")) == len(CURRENT_MORPHS)


# ---------------------------------------------------------------------------
# The known-morphs CSV
# ---------------------------------------------------------------------------


def test_a_lemma_and_inflection_csv_is_ingested(conn, tmp_path):
    result = ingest_known_morphs_csv(
        conn, write_csv(tmp_path / "known.csv", BOTH_COLUMNS_CSV)
    )

    assert (result.morphs, result.source, result.stale) == (3, "csv", False)
    assert set(stored(conn)) == {
        ("読む", "読んだ", "csv"),
        ("猫", "猫", "csv"),
        ("掛かる", "掛かった", "csv"),
    }
    # No interval column in the export: knownness comes from the source itself.
    assert stored(conn)[("猫", "猫", "csv")]["lemma_ivl"] is None


def test_a_lemma_only_csv_is_ingested_with_empty_inflections(conn, tmp_path):
    result = ingest_known_morphs_csv(
        conn, write_csv(tmp_path / "known.csv", LEMMA_ONLY_CSV)
    )

    assert result.morphs == 3
    assert set(stored(conn)) == {
        ("読む", "", "csv"),
        ("猫", "", "csv"),
        ("食べる", "", "csv"),
    }


@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize(
    "header",
    ["Morph-Lemma,Morph-Inflection", "morph_lemma,morph_inflection", "lemma,inflection"],
)
def test_header_sniffing_survives_spelling_and_a_bom(conn, tmp_path, header, bom):
    path = write_csv(
        tmp_path / "known.csv", f"{header}\n読む,読んだ\n猫,猫\n", bom=bom
    )
    result = ingest_known_morphs_csv(conn, path)

    assert result.morphs == 2
    # A BOM must not end up glued to the first lemma or the first header cell.
    assert set(stored(conn)) == {("読む", "読んだ", "csv"), ("猫", "猫", "csv")}


def test_columns_in_an_unexpected_order_follow_the_header(conn, tmp_path):
    """The header decides which column is which; position never does."""
    path = write_csv(
        tmp_path / "known.csv",
        "Occurrence,Morph-Inflection,Morph-Lemma\n12,読んだ,読む\n",
    )
    ingest_known_morphs_csv(conn, path)
    assert set(stored(conn)) == {("読む", "読んだ", "csv")}


def test_extra_columns_are_ignored_and_intervals_kept_when_present(conn, tmp_path):
    path = write_csv(
        tmp_path / "known.csv",
        "Morph-Lemma,Morph-Inflection,Occurrence,"
        "Highest-Lemma-Learning-Interval,Highest-Inflection-Learning-Interval\n"
        "読む,読んだ,12,120,30\n",
    )
    ingest_known_morphs_csv(conn, path)
    row = stored(conn)[("読む", "読んだ", "csv")]
    assert (row["lemma_ivl"], row["inflection_ivl"]) == (120, 30)


def test_blank_lines_short_rows_and_padding_are_tolerated(conn, tmp_path):
    path = write_csv(
        tmp_path / "known.csv",
        "Morph-Lemma,Morph-Inflection\n"
        "  読む  ,  読んだ  \n"
        "\n"
        "猫\n"  # short row: no inflection cell at all
        ",\n"  # nothing but separators
        ',"食べる, 食う",食べた\n',  # a quoted field containing a comma
    )
    result = ingest_known_morphs_csv(conn, path)

    # The empty-lemma row is dropped, not stored as a nameless morph.
    assert set(stored(conn)) == {("読む", "読んだ", "csv"), ("猫", "", "csv")}
    assert result.morphs == 2


def test_an_unrecognisable_header_is_refused(conn, tmp_path):
    path = write_csv(tmp_path / "known.csv", "column a,column b\n読む,読んだ\n")

    with pytest.raises(KnownMorphsCsvError) as excinfo:
        ingest_known_morphs_csv(conn, path)
    message = str(excinfo.value)
    assert "Morph-Lemma" in message and str(path) in message
    # Nothing was written, and the table was not created on a doomed ingest.
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MORPHS_TABLE,)
    ).fetchone()[0] == 0


def test_an_empty_csv_is_refused(conn, tmp_path):
    with pytest.raises(KnownMorphsCsvError):
        ingest_known_morphs_csv(conn, write_csv(tmp_path / "empty.csv", ""))


def test_a_missing_csv_is_refused(conn, tmp_path):
    with pytest.raises(KnownMorphsCsvError):
        ingest_known_morphs_csv(conn, tmp_path / "nope.csv")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_known_morph_count_is_zero_before_anything_is_ingested(conn):
    """No table yet means no morphs — not ``no such table``."""
    assert known_morph_count(conn) == 0


def test_known_morph_count_applies_the_maturity_threshold(conn, morphs_db):
    ingest_ankimorphs_db(conn, morphs_db)

    assert known_morph_count(conn) == KNOWN_AT_21
    # 難しかった sits at 20 days, so lowering the bar by one day admits it.
    assert known_morph_count(conn, min_ivl=20) == KNOWN_AT_21 + 1
    assert known_morph_count(conn, min_ivl=366) == 0
    # A morph with no interval at all is never known by interval.
    assert known_morph_count(conn, min_ivl=0) == len(CURRENT_MORPHS) - 1


def test_csv_morphs_are_known_by_definition(conn, tmp_path):
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "known.csv", BOTH_COLUMNS_CSV))
    # No intervals in the export, yet every row counts: the learner exported it
    # as a list of morphs they know.
    assert known_morph_count(conn) == 3
    assert known_morph_count(conn, min_ivl=10_000) == 3


def test_a_morph_present_in_both_sources_is_counted_once(conn, morphs_db, tmp_path):
    ingest_ankimorphs_db(conn, morphs_db)
    ingest_known_morphs_csv(conn, write_csv(tmp_path / "known.csv", BOTH_COLUMNS_CSV))

    # 読む/読んだ and 猫/猫 are mature in the database and present in the CSV;
    # 掛かる/掛かった is in both too. So the CSV adds nothing new.
    assert known_morph_count(conn) == KNOWN_AT_21
    assert conn.execute(f"SELECT COUNT(*) FROM {MORPHS_TABLE}").fetchone()[0] == (
        len(CURRENT_MORPHS) + 3
    )
