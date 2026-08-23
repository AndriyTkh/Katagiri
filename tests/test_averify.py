r"""kata-avf: the Phase A gate, verified end to end on frozen fixtures.

This file is a *cold* verification harness, not a unit-test suite. Nothing here
mocks a Katagiri module: every step runs the real code against one temporary
database that is built once per module —

    lay down the real JMdict (the session template, imported once from the
    vendored checksum-verified zip) -> migrate
            -> snapshot a fabricated Anki collection
            -> sync its revlog into review_batch events
            -> mark one word known through the event path
            -> rebuild the derived lexeme table
            -> load the frozen vault sentences as item rows
            -> rebuild both FTS5 sentence indexes

and then the four learner queries the gate exists to protect are asserted
against arithmetic this file states out loud (see :data:`EXPECTED_*`). Anything
that reads a number out of the database and compares it to another number read
out of the database would pass on an empty pipeline; the constants here are
written by hand from the fixture, so they cannot.

The Anki collection is fabricated from Anki's own DDL, trimmed to the columns
``anki_snapshot``/``anki_sync`` actually read. **No real collection is ever
opened**, not even read-only: the fixture is written into ``tmp_path`` from the
constants below.

``revlog`` ids are epoch milliseconds and both ``anki_sync._local_day`` and
``events._day_key`` derive their day from *system local* time, so the review
timestamps are built relative to the real local clock (midday, to stay clear of
any DST or offset edge) rather than to a frozen date. That keeps "yesterday's
reviews" meaningful on the machine the gate runs on.

The MCP check spawns the server as a real subprocess and speaks JSON-RPC over
its stdin/stdout, because that is the only transport the server offers and the
existing A6 tests call the tool functions in-process. Two things are being
proved there that an in-process call cannot: the initialize handshake completes,
and stdout carries protocol frames *only* — the logging setup that ``main()``
installs must land on stderr.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from katagiri import (
    anki_snapshot,
    anki_sync,
    backup,
    events,
    fts_index,
    jmdict_import,
    known,
    mcp_server,
    normalizer,
    tokenizer,
)
from katagiri import config as config_mod
from katagiri.db import open_db

# The MCP checks below spawn the real server as a subprocess, so the whole
# module belongs to the 'mcp' group and runs after the cheap unit groups.
#
# The module-scoped `mcp_client` fixture below hands out an *un-initialized*
# client, and test_initialize_handshake_and_tools_list is the one test that
# performs the `initialize` handshake on it; every other test in this module
# reuses that same already-initialized client. Under `-n` with per-test
# scheduling, xdist can send that one test to a different worker than the
# rest — each worker builds its own module-scoped fixture instance (a
# separate server subprocess), so a worker that never ran the handshake test
# serves every other test an un-initialized client, which answers with
# JSON-RPC -32602. Pinning the whole module to one xdist group keeps the
# fixture instance (and the subprocess behind it) single so the handshake is
# always seen before it's relied on.
pytestmark = [pytest.mark.mcp, pytest.mark.xdist_group("averify-mcp")]

FIXTURES = Path(__file__).parent / "fixtures" / "averify"
VAULT = FIXTURES / "vault"

# ---------------------------------------------------------------------------
# The fabricated Anki collection
# ---------------------------------------------------------------------------

# Anki's real DDL for the tables the readers touch, verbatim in column order.
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
_REVLOG_DDL = """
CREATE TABLE revlog (
    id integer primary key, cid integer not null, usn integer not null,
    ease integer not null, ivl integer not null, lastIvl integer not null,
    factor integer not null, time integer not null, type integer not null
);
"""

ANKI_SCHEMA_VERSION = 11
DECK_DEFAULT = 1
DECK_CORE = 1600000000000
MODEL_BASIC = 1500000000000

# note_id -> (field values, tags). 0x1f is applied by the builder so the data
# stays readable here.
FIXTURE_NOTES: dict[int, tuple[list[str], str]] = {
    1: (["食べる", "to eat"], " core verb "),
    2: (["勉強", "study"], " core noun "),
    3: (["猫", "cat"], " animals "),
    4: (["図書館", "library"], " places "),
    5: (["三時間", "three hours"], ""),
}
# card_id -> (note_id, ivl, due, reps, lapses)
FIXTURE_CARDS: dict[int, tuple[int, int, int, int, int]] = {
    101: (1, 30, 500, 12, 1),
    102: (2, 5, 20, 3, 0),
    103: (3, 21, 600, 20, 2),
    104: (4, 0, 1, 0, 0),
    105: (5, 7, 44, 4, 1),
}

# Reviews seeded for "yesterday": (card_id, ease). Ease 1-4 is a real answer.
YESTERDAY_REVIEWS: tuple[tuple[int, int], ...] = (
    (101, 3),
    (102, 3),
    (103, 2),
    (104, 4),
    (101, 1),
)
# Anki's manual "set due date" bookkeeping. Excluded from the counts, but it
# still moves the cursor past itself.
YESTERDAY_RESCHEDULES: tuple[tuple[int, int], ...] = ((105, 0), (105, 0))
# Reviews seeded three days back, so the sync produces two distinct day batches.
OLDER_REVIEWS: tuple[tuple[int, int], ...] = ((101, 3), (102, 3), (103, 3))
OLDER_DAYS_BACK = 3

# --- the arithmetic this file asserts, written by hand from the fixture -------

EXPECTED_CARDS = len(FIXTURE_CARDS)                     # 5
EXPECTED_NOTES = len(FIXTURE_NOTES)                     # 5
EXPECTED_YESTERDAY_REVIEWS = len(YESTERDAY_REVIEWS)     # 5
EXPECTED_YESTERDAY_CARDS = len({cid for cid, _ in YESTERDAY_REVIEWS})  # 4
EXPECTED_OLDER_REVIEWS = len(OLDER_REVIEWS)             # 3
EXPECTED_SKIPPED = len(YESTERDAY_RESCHEDULES)           # 2
EXPECTED_BATCHES = 2
#: Every ease 1-4 always carries a key, so "no 4s" reads as 0 rather than absent.
EXPECTED_YESTERDAY_EASE_HIST = {
    ease: sum(1 for _, e in YESTERDAY_REVIEWS if e == ease) for ease in (1, 2, 3, 4)
}                                                       # {1:1, 2:1, 3:2, 4:1}

#: Study days the stop gate can see after the pipeline: the two review_batch days
#: plus today, which the manual 'known' mark makes an artifact day on its own.
EXPECTED_STUDY_DAYS = EXPECTED_BATCHES + 1              # 3

#: Word items the harness inserts. The first is the one that gets marked known.
KNOWN_WORD_ID = "w-avf-taberu"
KNOWN_WORD_SURFACE = "食べる"
KNOWN_WORD_READING = "たべる"
#: 食べる's JMdict sequence number, from the vendored dictionary.
TABERU_SEQ = 1358280

FIXTURE_WORDS: tuple[tuple[str, str, str], ...] = (
    (KNOWN_WORD_ID, KNOWN_WORD_SURFACE, KNOWN_WORD_READING),
    ("w-avf-benkyou", "勉強", "べんきょう"),
    ("w-avf-neko", "猫", "ねこ"),
)

EXPECTED_SENTENCES = 4          # `- JP:` lines across the three frozen vault files
EXPECTED_WORDS = len(FIXTURE_WORDS)                          # 3
EXPECTED_KNOWN_SET_TOTAL = EXPECTED_WORDS + EXPECTED_SENTENCES  # 7
EXPECTED_KNOWN = 1              # exactly one manual 'known' mark
EXPECTED_UNKNOWN = EXPECTED_KNOWN_SET_TOTAL - EXPECTED_KNOWN    # 6

#: item ids of the two frozen sentences containing 勉強, in rebuild order.
BENKYOU_SENTENCE_IDS = ("s-avf-01", "s-avf-03")
NEKO_SENTENCE_ID = "s-avf-04"

_JP_LINE = re.compile(r"^-\s*JP:\s*(?P<jp>\S.*?)\s*$")
_TS = "T00:00:00Z"


def _local_midday(days_back: int) -> datetime:
    """Local midday, ``days_back`` days ago — a timestamp whose local date is
    unambiguous whatever the host's zone or DST state."""
    now = datetime.now().astimezone()
    return (now - timedelta(days=days_back)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )


def _epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def build_collection(path: Path) -> Path:
    """Write a minimal, structurally real Anki schema-11 ``collection.anki2``.

    Fabricated from the constants above; no real collection is read.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.executescript(_COL_DDL + _NOTES_DDL + _CARDS_DDL + _REVLOG_DDL)
        decks_json = json.dumps(
            {
                str(DECK_DEFAULT): {"id": DECK_DEFAULT, "name": "Default"},
                str(DECK_CORE): {"id": DECK_CORE, "name": "Japanese::Core"},
            }
        )
        models_json = json.dumps(
            {str(MODEL_BASIC): {"id": MODEL_BASIC, "name": "Basic"}}
        )
        conn.execute(
            "INSERT INTO col VALUES (1, 1400000000, 1700000000, 1700000000, ?, 0, "
            "-1, 0, '{}', ?, ?, '{}', '{}')",
            (ANKI_SCHEMA_VERSION, models_json, decks_json),
        )
        conn.executemany(
            "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    nid,
                    f"guid{nid}",
                    MODEL_BASIC,
                    1700000000,
                    -1,
                    tags,
                    "\x1f".join(fields),
                    fields[0],
                    0,
                    0,
                    "",
                )
                for nid, (fields, tags) in FIXTURE_NOTES.items()
            ],
        )
        conn.executemany(
            "INSERT INTO cards VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    cid,
                    nid,
                    DECK_CORE,
                    0,
                    1700000000,
                    -1,
                    2,
                    2,
                    due,
                    ivl,
                    2500,
                    reps,
                    lapses,
                    0,
                    0,
                    0,
                    0,
                    "",
                )
                for cid, (nid, ivl, due, reps, lapses) in FIXTURE_CARDS.items()
            ],
        )

        # revlog.id is the review's epoch-millisecond timestamp *and* the primary
        # key, so every row gets its own millisecond.
        rows: list[tuple[Any, ...]] = []
        for offset, (cid, ease) in enumerate(
            (*YESTERDAY_REVIEWS, *YESTERDAY_RESCHEDULES)
        ):
            rows.append(
                (_epoch_ms(_local_midday(1)) + offset * 1000, cid, -1, ease,
                 10, 5, 2500, 4000, 1)
            )
        for offset, (cid, ease) in enumerate(OLDER_REVIEWS):
            rows.append(
                (_epoch_ms(_local_midday(OLDER_DAYS_BACK)) + offset * 1000, cid,
                 -1, ease, 10, 5, 2500, 4000, 1)
            )
        conn.executemany(
            "INSERT INTO revlog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    finally:
        conn.close()
    return path


def vault_sentences() -> list[str]:
    """The frozen vault's Japanese sentences, in file-then-line order."""
    found: list[str] = []
    for markdown in sorted(VAULT.glob("*.md")):
        for line in markdown.read_text(encoding="utf-8").splitlines():
            match = _JP_LINE.match(line.strip())
            if match is not None:
                found.append(match.group("jp"))
    return found


# ---------------------------------------------------------------------------
# The one pipeline, built once
# ---------------------------------------------------------------------------


def dictionary_state(conn: sqlite3.Connection) -> jmdict_import.ImportResult:
    """The dictionary this database holds, in :class:`ImportResult` shape.

    The pipeline does not run the importer any more — the session-scoped
    template did, once — so the row counts are read back out of the tables and
    the provenance out of ``metadata``, where ``import_jmdict`` stamps it. Both
    survive the file copy, so the fields mean what the importer's own result
    meant.
    """
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    provenance = {
        row["key"]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM metadata "
            "WHERE key IN ('jmdict_version', 'jmdict_dict_date')"
        )
    }
    return jmdict_import.ImportResult(
        entries=count("jmdict_entry"),
        kanji_rows=count("jmdict_kanji"),
        reading_rows=count("jmdict_reading"),
        sense_rows=count("jmdict_sense"),
        version=provenance.get("jmdict_version"),
        dict_date=provenance.get("jmdict_dict_date"),
    )


@pytest.fixture(scope="module")
def pipeline(tmp_path_factory, real_jmdict_template) -> dict[str, Any]:
    """Run the whole Phase A pipeline once and hand back its artefacts.

    Module-scoped because every assertion below reads the same populated
    database. The dictionary arrives as a file copy of the session template
    (``conftest.real_jmdict_template``) rather than a fresh ~21s import; under
    ``--public-build`` that template is itself imported from ground zero this
    run. The database is closed again at teardown so the restore drill can copy
    the file.
    """
    root = tmp_path_factory.mktemp("averify")
    app_data = root / "AppData"
    (app_data / "Katagiri").mkdir(parents=True)
    db_path = root / "katagiri.db"
    (app_data / "Katagiri" / "config.toml").write_text(
        f'db_path = "{db_path.as_posix()}"\n'
        f'scratch_root = "{(root / "scratch").as_posix()}"\n'
        f'vault_path = "{VAULT.as_posix()}"\n',
        encoding="utf-8",
    )
    previous_local_app_data = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = str(app_data)
    config_mod.reset_config_cache()

    collection = build_collection(root / "anki" / "User 1" / "collection.anki2")
    sentences = vault_sentences()

    # 1. JMdict, from the real vendored zip — imported once into the session
    #    template and laid down here as a file copy. The template is already a
    #    migrated Katagiri database at the current schema version, so the
    #    open_db() below finds nothing pending and migrates nothing (see
    #    db.migrate: no pending migrations returns before any backup or DDL).
    real_jmdict_template.materialize(db_path)

    conn = open_db()
    try:
        jmdict = dictionary_state(conn)
        # 2. Provenance, without which the FTS rebuild rightly refuses to run.
        stamps = tokenizer.stamp_versions(conn)
        # 3. Anki mirror + review history.
        sync = anki_sync.sync_anki(conn, collection_path=collection)
        # 4. Word items, then one manual 'known' mark through the event path.
        for item_id, kanji, reading in FIXTURE_WORDS:
            conn.execute(
                "INSERT INTO item (id, kind, kanji, reading, created_ts) "
                "VALUES (?, 'word', ?, ?, ?)",
                (item_id, kanji, reading, f"2026-01-01{_TS}"),
            )
        mark = events.mark_item(conn, KNOWN_WORD_ID, "known", note="averify")
        # 5. Derived lexeme table, and the morph crosswalk.
        lexemes = normalizer.populate_lexemes(conn)
        morph_map = normalizer.map_known_morphs(conn)
        # 6. Vault sentences as sentence items, then the index over them.
        for index, jp in enumerate(sentences, start=1):
            conn.execute(
                "INSERT INTO item (id, kind, kanji, created_ts) "
                "VALUES (?, 'sentence', ?, ?)",
                (f"s-avf-{index:02d}", jp, f"2026-01-01{_TS}"),
            )
        index_result = fts_index.rebuild_index(conn)

        yield {
            "root": root,
            "app_data": app_data,
            "db_path": db_path,
            "collection": collection,
            "sentences": sentences,
            "conn": conn,
            "jmdict": jmdict,
            "stamps": stamps,
            "sync": sync,
            "mark": mark,
            "lexemes": lexemes,
            "morph_map": morph_map,
            "index": index_result,
        }
    finally:
        conn.close()
        config_mod.reset_config_cache()
        if previous_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_local_app_data


@pytest.fixture
def conn(pipeline) -> sqlite3.Connection:
    return pipeline["conn"]


# ---------------------------------------------------------------------------
# The pipeline itself landed
# ---------------------------------------------------------------------------


def test_the_vault_fixture_is_the_shape_the_arithmetic_assumes(pipeline):
    """Guard the guard: every count below is derived from these sentences."""
    assert len(pipeline["sentences"]) == EXPECTED_SENTENCES
    assert pipeline["sentences"] == [
        "毎日日本語を勉強します。",
        "ご飯を食べる。",
        "図書館で三時間も勉強した。",
        "猫が窓の近くで寝ている。",
    ]


def test_jmdict_imported_from_the_checksum_verified_vendor_zip(pipeline, conn):
    result = pipeline["jmdict"]
    assert result.entries > 100_000, "the real dictionary, not a stub"
    # The importer ran once, into the session template, and this database is a
    # copy of that file — so the counts are read back rather than reported, and
    # what has to be proved here is that the copy carries a *whole* import:
    # every detail table populated, and not one detail row orphaned from its
    # entry. A truncated or half-written template fails this.
    assert result.kanji_rows > 0, "a dictionary with no written forms is not one"
    assert result.reading_rows > result.entries, "entries carry ≥1 reading each"
    assert result.sense_rows > result.entries, "entries carry ≥1 sense each"
    for table in ("jmdict_kanji", "jmdict_reading", "jmdict_sense"):
        orphans = conn.execute(
            f"SELECT COUNT(*) FROM {table} AS d WHERE NOT EXISTS "
            "(SELECT 1 FROM jmdict_entry AS e WHERE e.seq = d.seq)"
        ).fetchone()[0]
        assert orphans == 0, f"{table} rows without an entry"
    # The zip is the frozen fixture, and its provenance reached the rows: the
    # version stamped into metadata is the one every entry row declares.
    versions = [
        row[0]
        for row in conn.execute("SELECT DISTINCT dict_version FROM jmdict_entry")
    ]
    assert versions == [result.version], "one import, one declared dict_version"
    assert result.version, "an import with unknown provenance is not worth keeping"


def test_the_anki_mirror_and_the_review_batches_landed(pipeline, conn):
    sync = pipeline["sync"]
    assert sync.mirror.schema_version == ANKI_SCHEMA_VERSION
    assert sync.mirror.cards == EXPECTED_CARDS
    assert sync.mirror.notes == EXPECTED_NOTES
    assert sync.batches == EXPECTED_BATCHES
    assert sync.reviews == EXPECTED_YESTERDAY_REVIEWS + EXPECTED_OLDER_REVIEWS
    assert sync.skipped == EXPECTED_SKIPPED, "ease-0 reschedules are not studying"

    # The mirror is a real mirror: fields round-trip as a JSON array in notetype
    # field order.
    fields = conn.execute(
        "SELECT fields FROM anki_notes WHERE note_id = 1"
    ).fetchone()[0]
    assert json.loads(fields) == ["食べる", "to eat"]


def test_the_lexeme_table_and_the_morph_crosswalk(pipeline, conn):
    lexemes = pipeline["lexemes"]
    assert lexemes.lexemes > lexemes.entries, "one lexeme row per sense, not per entry"
    assert conn.execute("SELECT COUNT(*) FROM lexeme").fetchone()[0] == lexemes.lexemes
    assert conn.execute(
        "SELECT id FROM lexeme WHERE jmdict_seq = ? AND sense_idx = 1", (TABERU_SEQ,)
    ).fetchone()[0] == f"lx-{TABERU_SEQ}-1", "sense_idx is 1-based"

    # No AnkiMorphs ingest happened, so the mapper must say "nothing" rather
    # than raise: an absent source table is not an error.
    assert pipeline["morph_map"].mapped == 0

    # The rung logic itself still works against the real dictionary.
    assert normalizer.normalize_morph(conn, "勉強", "ベンキョウ", "名詞").best is not None
    assert normalizer.morph_item_id("勉強") == "morph:勉強"


def test_the_sentence_index_was_rebuilt_deterministically(pipeline, conn):
    result = pipeline["index"]
    assert result.rows == EXPECTED_SENTENCES
    assert result.skipped == 0
    assert result.dict_version == pipeline["stamps"]["dict_version"]
    assert result.tokenizer_version == pipeline["stamps"]["tokenizer_version"]
    # Rowids are handed out from 1 in item-id order, which is what makes a
    # rebuild comparable to the one before it.
    assert [
        (row[0], row[1])
        for row in conn.execute("SELECT rowid, item_id FROM sentence_text ORDER BY rowid")
    ] == [(n, f"s-avf-{n:02d}") for n in range(1, EXPECTED_SENTENCES + 1)]
    assert fts_index.index_staleness(conn).stale_rows == 0


# ---------------------------------------------------------------------------
# Learner query 1: known-word
# ---------------------------------------------------------------------------


def test_known_word_answers_marked_and_unmarked_and_absent(conn):
    marked = known.known_word(conn, KNOWN_WORD_ID)
    assert marked["found"] is True
    assert marked["is_known"] is True
    assert marked["source"] == "manual"
    assert marked["manual_mark"] == "known"
    assert marked["suspect"] is False

    # The same verdict through the surface form and through the reading.
    for surface in (KNOWN_WORD_SURFACE, KNOWN_WORD_READING):
        by_surface = known.known_word(conn, surface)
        assert by_surface["item_id"] == KNOWN_WORD_ID, surface
        assert by_surface["is_known"] is True, surface
        assert by_surface["matched_by"] == "surface", surface

    unmarked = known.known_word(conn, "勉強")
    assert unmarked["found"] is True
    assert unmarked["is_known"] is False, "an unmarked item is a real 'not known'"
    assert unmarked["manual_mark"] is None

    absent = known.known_word(conn, "w-avf-nothing")
    assert absent["found"] is False
    assert absent["is_known"] is None, "'never heard of it' must not read as 'not known'"


def test_the_mark_landed_in_the_append_only_log_too(pipeline, conn):
    mark = pipeline["mark"]
    assert mark["item_id"] == KNOWN_WORD_ID
    row = conn.execute(
        "SELECT type, item_id, payload FROM event WHERE id = ?", (mark["event_id"],)
    ).fetchone()
    assert row["type"] == "mark_known"
    assert row["item_id"] == KNOWN_WORD_ID
    assert json.loads(row["payload"])["mark"] == "known"

    # Append-only is not decoration: the log refuses to be edited.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE event SET type = 'x' WHERE id = ?", (mark["event_id"],))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM event WHERE id = ?", (mark["event_id"],))


# ---------------------------------------------------------------------------
# Learner query 2: known-count
# ---------------------------------------------------------------------------


def test_known_set_stats_match_the_fixture_arithmetic_exactly(conn):
    stats = known.known_set_stats(conn)

    assert stats["total"] == EXPECTED_KNOWN_SET_TOTAL
    assert stats["known"] == EXPECTED_KNOWN
    assert stats["unknown"] == EXPECTED_UNKNOWN
    assert stats["suspect"] == 0
    assert stats["latest_marks_by_value"] == {"known": EXPECTED_KNOWN}

    # One manual mark; everything else falls through to the (empty) Anki rule.
    assert stats["by_source"] == {
        "anki": {"total": EXPECTED_KNOWN_SET_TOTAL - 1, "known": 0},
        "manual": {"total": 1, "known": 1},
    }
    assert stats["by_kind"] == {
        "sentence": {"total": EXPECTED_SENTENCES, "known": 0},
        "word": {"total": EXPECTED_WORDS, "known": EXPECTED_KNOWN},
    }
    # anki_item_map is untouched by the snapshot, so no card's ivl>=21 can make
    # anything known: every 'known' here is the one mark.
    assert conn.execute("SELECT COUNT(*) FROM anki_item_map").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Learner query 3: yesterday's reviews
# ---------------------------------------------------------------------------


def test_yesterdays_review_batch_carries_the_seeded_count(pipeline, conn):
    yesterday = _local_midday(1).strftime("%Y-%m-%d")
    older = _local_midday(OLDER_DAYS_BACK).strftime("%Y-%m-%d")

    by_day = {batch.day_key: batch for batch in pipeline["sync"].days}
    assert sorted(by_day) == sorted({older, yesterday})

    batch = by_day[yesterday]
    assert batch.reviews == EXPECTED_YESTERDAY_REVIEWS
    assert batch.cards == EXPECTED_YESTERDAY_CARDS
    assert batch.ease_hist == EXPECTED_YESTERDAY_EASE_HIST, (
        "ease-0 reschedules must be outside the histogram"
    )
    assert sum(batch.ease_hist.values()) == EXPECTED_YESTERDAY_REVIEWS
    assert by_day[older].reviews == EXPECTED_OLDER_REVIEWS

    # The same numbers, read back out of the event log rather than the result.
    rows = conn.execute(
        "SELECT day_key, dedupe_key, payload FROM event WHERE type = ? "
        "AND day_key = ?",
        (anki_sync.REVIEW_BATCH_TYPE, yesterday),
    ).fetchall()
    assert len(rows) == 1, "one batch per day for a single sync run"
    payload = json.loads(rows[0]["payload"])
    assert payload["reviews"] == EXPECTED_YESTERDAY_REVIEWS
    assert payload["cards"] == EXPECTED_YESTERDAY_CARDS
    assert payload["source"] == "anki_revlog"
    assert rows[0]["dedupe_key"] == (
        f"{anki_sync.DEDUPE_PREFIX}:{yesterday}:{payload['last_id']}"
    )


def test_a_second_sync_appends_nothing(pipeline, conn):
    """Idempotency, on the same collection the first run read."""
    before = conn.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    cursor_before = anki_sync.read_cursor(conn)

    again = anki_sync.sync_anki(conn, collection_path=pipeline["collection"])

    assert again.batches == 0
    assert again.reviews == 0
    assert again.cursor == cursor_before
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == before
    # The mirror is still rebuilt, and still says the same thing.
    assert again.mirror.cards == EXPECTED_CARDS


# ---------------------------------------------------------------------------
# Learner query 4: the 勉 / 勉強 substring searches
# ---------------------------------------------------------------------------


def test_two_character_query_finds_the_sentence_through_the_word_index(conn):
    """勉強 is the docstring's own example of what trigram cannot do."""
    hits = fts_index.search_words(conn, "勉強")
    assert [hit["item_id"] for hit in hits] == sorted(BENKYOU_SENTENCE_IDS)
    assert {hit["jp"] for hit in hits} == {
        "毎日日本語を勉強します。",
        "図書館で三時間も勉強した。",
    }
    # Why the routing exists: the trigram index is silent, not wrong.
    assert fts_index.search_trigram(conn, "勉強") == []


def test_one_character_query_finds_a_sentence_when_it_is_its_own_morph(conn):
    hits = fts_index.search_words(conn, "猫")
    assert [hit["item_id"] for hit in hits] == [NEKO_SENTENCE_ID]
    assert fts_index.search_trigram(conn, "猫") == []


def test_a_three_character_query_reaches_the_trigram_index(conn):
    hits = fts_index.search_trigram(conn, "で三時間")
    assert [hit["item_id"] for hit in hits] == ["s-avf-03"]


def test_search_db_routes_short_queries_and_finds_the_benkyou_sentences(conn):
    result = mcp_server.search_db_query(conn, "勉強")

    assert result["route"] == "words"
    assert "trigram index cannot match it" in result["route_reason"]
    assert result["index_empty"] is False
    assert result["sentence_rows"] == EXPECTED_SENTENCES
    assert result["note"] is None

    by_source = {hit["item_id"]: hit["source_index"] for hit in result["hits"]}
    # The word item matches exactly, and both sentences come from the word index.
    assert by_source["w-avf-benkyou"] == "item_exact"
    for sentence_id in BENKYOU_SENTENCE_IDS:
        assert by_source[sentence_id] == mcp_server.WORD_INDEX


def test_a_single_kanji_of_a_compound_reaches_the_item_but_not_the_sentence(conn):
    """A documented Phase A limit, asserted rather than assumed.

    ``勉`` routes to the word index (1 character < 3), and that index matches on
    morph boundaries — ``勉強`` is one morph, so ``勉`` cannot match it. The
    trigram index, which *would* match a substring, needs 3 characters. So the
    only 勉 hit is the item-prefix match on the word item. This is exactly what
    both modules document; it is recorded here so that a later change to the
    routing is a visible change, not a silent one.
    """
    result = mcp_server.search_db_query(conn, "勉")

    assert result["route"] == "words"
    sources = {hit["item_id"]: hit["source_index"] for hit in result["hits"]}
    assert sources == {"w-avf-benkyou": "item_prefix"}
    assert not set(BENKYOU_SENTENCE_IDS) & set(sources)
    # Neither index can serve it, which is the reason above and not an accident.
    assert fts_index.search_words(conn, "勉") == []
    assert fts_index.search_trigram(conn, "勉") == []


# ---------------------------------------------------------------------------
# MCP protocol, over a real stdio subprocess
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2026-07-28"
CONTRACT_TOOLS = frozenset(
    {
        "ping",
        "known_word",
        "known_set_stats",
        "recent_events",
        "search_db",
        "lookup",
        "stop_gate_status",
        "security_status",
        "vault_file",
        "vault_list",
        "obsidian_active_note",
        # Phase C (additive): markdown search over the local derived index.
        "search_notes",
        # Phase D (additive): the US1, US2 and US4 tool batches.
        "stage_untrusted",
        "confirm_untrusted",
        "start_session",
        "log_lesson",
        "lessons",
        "log_observations",
        "log_error",
        "add_vocab",
        "triage_inbox",
        "gen_exercise",
        "build_sentences",
        "lesson_memory",
        "coverage",
        "find_i_plus_one",
        # Phase E (additive): the media overlay, lyrics, screenshots, Anki
        # launch, and the D-44 curriculum outlook.
        "media_now",
        "media_context",
        "lyrics_now",
        "lyrics_context",
        "screenshot_capture",
        "screenshot_read",
        "open_anki",
        "study_plan",
    }
)


class _StdioClient:
    """The smallest honest MCP client: newline-delimited JSON-RPC over a pipe."""

    def __init__(self, app_data: Path) -> None:
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(app_data)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "katagiri.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        self._next_id = 0
        self.stdout_lines: list[bytes] = []

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self.process.stdin.flush()

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise AssertionError(
                    "the MCP server closed stdout before answering; stderr was:\n"
                    + self._drain_stderr()
                )
            self.stdout_lines.append(line)
            return json.loads(line.decode("utf-8"))

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": {} if params is None else params,
            }
        )
        response = self._read()
        assert response["jsonrpc"] == "2.0", response
        assert response["id"] == self._next_id, response
        return response

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _drain_stderr(self) -> str:
        assert self.process.stderr is not None
        return self.process.stderr.read().decode("utf-8", "replace")

    def close(self) -> str:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged server
            self.process.kill()
            self.process.wait(timeout=15)
        stderr = self._drain_stderr()
        assert self.process.stdout is not None
        self.process.stdout.close()
        self.process.stderr.close()
        return stderr


def _tool_payload(response: dict[str, Any]) -> Any:
    """The structured result of a ``tools/call``, whichever field carries it."""
    assert "error" not in response, response
    result = response["result"]
    assert result.get("isError") is not True, result
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    blocks = [
        block["text"] for block in result.get("content", []) if block.get("type") == "text"
    ]
    assert blocks, f"no readable content in {result}"
    return json.loads(blocks[0])


@pytest.fixture(scope="module")
def mcp_client(pipeline):
    client = _StdioClient(pipeline["app_data"])
    try:
        yield client
    finally:
        stderr = client.close()
        # The server's own startup line must be here and nowhere else.
        assert "starting katagiri" in stderr, stderr[-2000:]


def test_initialize_handshake_and_tools_list(mcp_client):
    initialized = mcp_client.call(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kata-avf", "version": "1"},
        },
    )
    assert "error" not in initialized, initialized
    result = initialized["result"]
    assert result["serverInfo"]["name"] == "katagiri"
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]

    mcp_client.notify("notifications/initialized")

    listed = mcp_client.call("tools/list")
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == CONTRACT_TOOLS, "the A6 contract is additive-only"


def test_lookup_over_the_wire_returns_the_real_jmdict_entry(mcp_client):
    payload = _tool_payload(
        mcp_client.call(
            "tools/call", {"name": "lookup", "arguments": {"surface": "食べる"}}
        )
    )
    assert payload["surface"] == "食べる"
    assert payload["found"] is True
    assert payload["note"] is None
    seqs = [entry["seq"] for entry in payload["entries"]]
    assert TABERU_SEQ in seqs, seqs
    entry = next(e for e in payload["entries"] if e["seq"] == TABERU_SEQ)
    assert any("食べる" == form["text"] for form in entry["kanji"])
    assert any(
        "eat" in (sense["gloss"] or "") for sense in entry["senses"]
    ), entry["senses"]


def test_stop_gate_status_over_the_wire_counts_rather_than_judges(mcp_client):
    payload = _tool_payload(
        mcp_client.call("tools/call", {"name": "stop_gate_status", "arguments": {}})
    )
    assert payload["required_study_days"] == 14
    assert payload["window_length_days"] == 18
    # Three artifact days exist (two review_batch days plus today's mark_known),
    # and 3 of 14 must fail rather than be rounded up to a pass.
    assert payload["study_days_in_window"] == EXPECTED_STUDY_DAYS
    assert payload["pass"] is False
    assert f"{EXPECTED_STUDY_DAYS} of 14" in payload["failing_criterion"]
    assert payload["probe_battery_recorded"] is False


def test_known_word_and_stats_agree_over_the_wire(mcp_client):
    verdict = _tool_payload(
        mcp_client.call(
            "tools/call",
            {"name": "known_word", "arguments": {"query": KNOWN_WORD_SURFACE}},
        )
    )
    assert verdict["is_known"] is True
    assert verdict["item_id"] == KNOWN_WORD_ID

    stats = _tool_payload(
        mcp_client.call("tools/call", {"name": "known_set_stats", "arguments": {}})
    )
    assert stats["total"] == EXPECTED_KNOWN_SET_TOTAL
    assert stats["known"] == EXPECTED_KNOWN


def test_stdout_carried_protocol_frames_and_nothing_else(mcp_client):
    assert mcp_client.stdout_lines, "nothing was read from stdout yet"
    for line in mcp_client.stdout_lines:
        text = line.decode("utf-8")
        message = json.loads(text)  # a log line would fail right here
        assert message.get("jsonrpc") == "2.0", text


# ---------------------------------------------------------------------------
# Restore drill
# ---------------------------------------------------------------------------


@pytest.mark.compile
def test_backup_verify_damage_restore_round_trip(pipeline, conn, tmp_path):
    """Snapshot the populated database, damage a copy of it, restore, recount.

    A ground-zero drill: it VACUUMs, copies and rewrites the whole ~200MB
    database, so it runs only under ``--public-build`` (see ``conftest``).

    The snapshot is taken with ``create_backup`` (``VACUUM INTO``) straight from
    the live connection, and *that* is what becomes the drill's starting file. A
    plain ``shutil.copy2`` of ``katagiri.db`` is not an alternative: the database
    runs in WAL mode, so a file-level copy without the ``-wal`` sidecar is a torn
    snapshot missing committed rows — measured here first, and it is the reason
    ``backup.py`` uses ``VACUUM INTO`` at all.
    """
    torn = tmp_path / "torn.db"
    shutil.copy2(pipeline["db_path"], torn)
    torn_conn = sqlite3.connect(f"file:{torn.as_posix()}?mode=ro", uri=True)
    try:
        torn_total = torn_conn.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    finally:
        torn_conn.close()
    # Not asserted as "short": whether the WAL happens to have been checkpointed
    # is SQLite's business, and a drill that depends on that timing would be
    # flaky rather than informative. What is asserted is the *snapshot*, below —
    # it must be complete no matter where the committed rows were living.
    assert torn_total <= EXPECTED_KNOWN_SET_TOTAL

    snapshot = backup.create_backup(conn, tmp_path / "backups")
    live = tmp_path / "drill.db"
    shutil.copy2(snapshot, live)

    drill_conn = open_db(live)
    try:
        before = known.known_set_stats(drill_conn)
        assert before["total"] == EXPECTED_KNOWN_SET_TOTAL, (
            "VACUUM INTO must capture every committed row"
        )
    finally:
        drill_conn.close()

    report = backup.verify_backup(snapshot)
    assert report["ok"] is True
    assert report["integrity"] == "ok"
    assert report["event_count"] > 0
    assert report["user_version"] >= 1

    # Damage the original: overwrite the middle of the file with junk, well past
    # the header, so SQLite finds a corrupt page rather than a foreign file.
    size = live.stat().st_size
    assert size > 64 * 1024, "the fixture database must span enough pages to damage"
    with live.open("r+b") as handle:
        handle.seek(size // 2)
        handle.write(b"\xde\xad\xbe\xef" * 4096)
    for sidecar in ("-wal", "-shm"):
        Path(str(live) + sidecar).unlink(missing_ok=True)

    damaged = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
    try:
        problems = [str(row[0]) for row in damaged.execute("PRAGMA integrity_check")]
    except sqlite3.DatabaseError as exc:  # pragma: no cover - depends where it bit
        problems = [str(exc)]
    finally:
        damaged.close()
    assert problems != ["ok"], "the damage did not take; the drill would prove nothing"

    result = backup.restore_backup(snapshot, live, force=True)
    assert result["overwrote"] is True
    assert result["integrity"] == "ok"

    restored = open_db(live)
    try:
        after = known.known_set_stats(restored)
    finally:
        restored.close()
    assert after == before, "the restored known set must be the pre-damage one"
    assert after["total"] == EXPECTED_KNOWN_SET_TOTAL
    assert after["known"] == EXPECTED_KNOWN


def test_restore_refuses_a_corrupt_snapshot(tmp_path):
    """A backup that fails its own integrity check must never be restored."""
    fake = tmp_path / "not-a-database.db"
    fake.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4096)
    with pytest.raises(backup.BackupError):
        backup.restore_backup(fake, tmp_path / "target.db")
    assert not (tmp_path / "target.db").exists()


# ---------------------------------------------------------------------------
# The snapshot reader's central promise
# ---------------------------------------------------------------------------


def test_the_live_collection_was_never_opened_read_write(pipeline):
    """The fixture collection must still be byte-identical and journal-free."""
    collection = pipeline["collection"]
    assert collection.is_file()
    for sidecar in ("-wal", "-shm", "-journal"):
        assert not Path(str(collection) + sidecar).exists(), sidecar
    # It was opened by the builder in the default rollback journal mode and never
    # touched since; a reader that wrote to it would have left WAL or a journal.
    probe = sqlite3.connect(f"file:{collection.as_posix()}?mode=ro", uri=True)
    try:
        assert probe.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert probe.execute("SELECT ver FROM col").fetchone()[0] == (
            ANKI_SCHEMA_VERSION
        )
    finally:
        probe.close()


def test_an_unsupported_schema_version_is_refused_loudly(pipeline, tmp_path):
    """Fail loud, not quiet: a schema this reader has not been written against
    must not be guessed at."""
    stray = build_collection(tmp_path / "stray" / "collection.anki2")
    bad = sqlite3.connect(str(stray), isolation_level=None)
    try:
        bad.execute("UPDATE col SET ver = 17")
    finally:
        bad.close()

    with pytest.raises(anki_snapshot.UnsupportedAnkiSchemaError) as exc:
        anki_snapshot.snapshot_anki(pipeline["conn"], collection_path=stray)
    assert exc.value.schema_version == 17
    # The previous mirror survived the refusal.
    assert pipeline["conn"].execute(
        "SELECT COUNT(*) FROM anki_cards"
    ).fetchone()[0] == EXPECTED_CARDS
