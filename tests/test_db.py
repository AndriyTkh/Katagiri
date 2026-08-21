"""Schema migration tests: versioning, append-only logs, backups, FK enforcement."""

from __future__ import annotations

import sqlite3

import pytest

from katagiri import config as config_mod
from katagiri import db

# Every object 0001_init.sql is expected to create. FTS5 shadow tables
# (fts_*_data, _idx, _docsize, _config) are deliberately not listed: they are
# SQLite's internals, not our schema.
SOURCE_OF_TRUTH_TABLES = {
    "event",
    "observation",
    "lesson",
    "lesson_unresolved",
    "lesson_media",
    "item",
    "item_edge",
    "alias",
    "manual_marks",
    "media",
    "media_heartbeat",
    "settings",
}

# Dropped and rebuilt by import/refresh scripts, never migrated. Order matters
# for the drop test: FTS indexes before the content table they read.
DERIVED_TABLES = (
    "fts_sentence_words",
    "fts_sentence_tri",
    "sentence_text",
    "metadata",
    "lexeme",
    "morph_lexeme_map",
    "anki_cards",
    "anki_notes",
    "anki_item_map",
    "mirror_meta",
    "jmdict_entry",
    "jmdict_kanji",
    "jmdict_reading",
    "jmdict_sense",
    "pitch_accent",
    "fts_md_words",
    "fts_md_tri",
    "md_note",
    "md_frontmatter",
    "sub_lines",
    "coverage_cache",
    "item_stat_cache",
)

# FTS5 keeps its own shadow tables (<name>_data, _idx, _content, _docsize,
# _config) beside every fts_ table listed above. Derived from DERIVED_TABLES
# rather than hard-coded so a new FTS index cannot be mistaken for an
# undocumented table by the fresh-migration test.
FTS_SHADOW_PREFIXES = tuple(
    f"{name}_" for name in DERIVED_TABLES if name.startswith("fts_")
)

EXPECTED_TABLES = SOURCE_OF_TRUTH_TABLES | set(DERIVED_TABLES)

EXPECTED_VIEWS = {"lesson_outcome", "known_set"}

EXPECTED_TRIGGERS = {
    "event_no_update",
    "event_no_delete",
    "observation_no_update",
    "observation_no_delete",
}

_EVENT_INSERT = (
    "INSERT INTO event"
    "(id, ts_device, ts_server, tz, day_key, session_id, type, item_id) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_EVENT_ROW = (
    "01J000000000000000000EVENT",
    "2026-08-19T09:00:00Z",
    "2026-08-19T09:00:01Z",
    "Europe/Kyiv",
    "2026-08-19",
    "sess-1",
    "review",
    "w-abc123",
)

_OBSERVATION_INSERT = (
    "INSERT INTO observation"
    "(id, ts, session_id, item_id, task_type, unassisted, coverage_band, "
    " rubric_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_OBSERVATION_ROW = (
    "01J0000000000000000000OBS",
    "2026-08-19T09:05:00Z",
    "sess-1",
    "w-abc123",
    "shadow",
    1,
    ">=95",
    "r1",
)


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir so config (and backups/) are isolated."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def conn(local_app_data):
    """A connection to a fresh, unmigrated database at the configured path."""
    connection = db.connect()
    try:
        yield connection
    finally:
        connection.close()


def _names(connection: sqlite3.Connection, kind: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? "
            "AND name NOT LIKE 'sqlite_%'",
            (kind,),
        )
    }


def _write_migrations(directory, *files: tuple[str, str]):
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in files:
        (directory / name).write_text(body, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# connection setup
# ---------------------------------------------------------------------------


def test_connect_uses_configured_path_and_required_pragmas(conn, local_app_data):
    assert db.database_path() == local_app_data / "Katagiri" / "katagiri.db"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA encoding").fetchone()[0].upper() == "UTF-8"
    # Without this, INSERT OR REPLACE silently skips the append-only triggers.
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    # One busy timeout, set one way.
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS


def test_connect_creates_the_parent_directory(local_app_data):
    target = local_app_data / "nested" / "deeper" / "k.db"
    connection = db.connect(target)
    try:
        assert target.exists()
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# fresh migration
# ---------------------------------------------------------------------------


def test_fresh_migrate_stamps_version_and_creates_every_object(conn):
    assert db.user_version(conn) == 0

    result = db.migrate(conn)

    assert result.from_version == 0
    assert result.to_version == 2
    assert result.applied == (1, 2)
    assert db.user_version(conn) == 2
    assert db.user_version(conn) == db.latest_version()

    tables = _names(conn, "table")
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {sorted(missing)}"
    unexpected = {
        name
        for name in tables - EXPECTED_TABLES
        if not name.startswith(FTS_SHADOW_PREFIXES)
    }
    assert not unexpected, f"undocumented tables: {sorted(unexpected)}"

    assert EXPECTED_VIEWS <= _names(conn, "view")
    assert EXPECTED_TRIGGERS <= _names(conn, "trigger")


def test_fresh_migrate_writes_no_backup(conn, local_app_data):
    result = db.migrate(conn)
    assert result.backup is None
    assert not (local_app_data / "Katagiri" / "backups").exists()


def test_both_fts_indexes_are_queryable(conn):
    db.migrate(conn)
    # Trigram and unicode61 indexes exist and accept MATCH; population is A3.
    assert conn.execute(
        "SELECT COUNT(*) FROM fts_sentence_tri WHERE fts_sentence_tri MATCH ?",
        ("こと",),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM fts_sentence_words WHERE fts_sentence_words MATCH ?",
        ("neko",),
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# append-only logs
# ---------------------------------------------------------------------------


def test_event_update_and_delete_both_abort(conn):
    db.migrate(conn)
    conn.execute(_EVENT_INSERT, _EVENT_ROW)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE event SET grade = 4 WHERE id = ?", (_EVENT_ROW[0],))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM event WHERE id = ?", (_EVENT_ROW[0],))

    # The row is untouched by both attempts.
    row = conn.execute(
        "SELECT grade FROM event WHERE id = ?", (_EVENT_ROW[0],)
    ).fetchone()
    assert row is not None and row[0] is None


def test_observation_update_and_delete_both_abort(conn):
    db.migrate(conn)
    conn.execute(_OBSERVATION_INSERT, _OBSERVATION_ROW)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE observation SET unassisted = 0 WHERE id = ?",
            (_OBSERVATION_ROW[0],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "DELETE FROM observation WHERE id = ?", (_OBSERVATION_ROW[0],)
        )
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 1


def test_insert_or_replace_into_event_aborts(conn):
    """REPLACE deletes the conflicting row; that delete must hit the trigger."""
    db.migrate(conn)
    conn.execute(_EVENT_INSERT, _EVENT_ROW)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT OR REPLACE INTO event"
            "(id, ts_device, ts_server, tz, day_key, session_id, type, item_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_EVENT_ROW[0], *_EVENT_ROW[1:6], "mark_known", "w-overwritten"),
        )

    row = conn.execute(
        "SELECT type, item_id FROM event WHERE id = ?", (_EVENT_ROW[0],)
    ).fetchone()
    assert (row["type"], row["item_id"]) == ("review", "w-abc123")
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 1


def test_insert_or_replace_into_observation_aborts(conn):
    db.migrate(conn)
    conn.execute(_OBSERVATION_INSERT, _OBSERVATION_ROW)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "INSERT OR REPLACE INTO observation"
            "(id, ts, session_id, item_id, task_type, unassisted, coverage_band,"
            " rubric_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*_OBSERVATION_ROW[:5], 0, "<80", "r2"),
        )

    row = conn.execute(
        "SELECT unassisted, rubric_version FROM observation WHERE id = ?",
        (_OBSERVATION_ROW[0],),
    ).fetchone()
    assert (row["unassisted"], row["rubric_version"]) == (1, "r1")


def test_insert_or_replace_on_a_derived_table_still_works(conn):
    """The append-only rule is for logs; rebuild scripts must stay unaffected."""
    db.migrate(conn)
    for value in ("1.0.0", "1.1.0"):
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value, updated_ts) "
            "VALUES ('tokenizer_version', ?, '2026-08-19T09:00:00Z')",
            (value,),
        )
    assert conn.execute(
        "SELECT value FROM metadata WHERE key = 'tokenizer_version'"
    ).fetchone()[0] == "1.1.0"


def test_undo_is_an_event_not_a_delete(conn):
    """A tombstone is appended; nothing is ever removed from the log."""
    db.migrate(conn)
    conn.execute(_EVENT_INSERT, _EVENT_ROW)
    conn.execute(
        _EVENT_INSERT,
        ("01J00000000000000000TOMBS", *_EVENT_ROW[1:6], "tombstone_session", None),
    )
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# idempotency and backups
# ---------------------------------------------------------------------------


def test_second_migrate_run_is_a_noop(conn, local_app_data):
    first = db.migrate(conn)
    assert first.applied == (1, 2)

    second = db.migrate(conn)

    assert second.applied == ()
    assert second.from_version == second.to_version == first.to_version
    assert second.backup is None
    assert not second.changed
    assert db.user_version(conn) == 2
    # A no-op must not snapshot either.
    assert not (local_app_data / "Katagiri" / "backups").exists()


def test_backup_written_before_migrating_a_non_fresh_db(conn, local_app_data, tmp_path):
    db.migrate(conn)
    conn.execute(_EVENT_INSERT, _EVENT_ROW)

    # `directory=` replaces the packaged migration set entirely rather than
    # extending it, so this only needs one migration versioned past the
    # packaged baseline (currently 2) to exercise "migrate a non-fresh DB".
    pending = _write_migrations(
        tmp_path / "migs",
        ("0003_later.sql", "CREATE TABLE later_addition (a TEXT);\n"),
    )
    result = db.migrate(conn, directory=pending)

    assert result.applied == (3,)
    assert db.user_version(conn) == 3
    assert result.backup is not None
    # Named for the version we migrated *away* from, kept beside the config.
    assert result.backup.name == "katagiri.pre-migrate-2.bak"
    assert result.backup.parent == local_app_data / "Katagiri" / "backups"
    assert result.backup.is_file() and result.backup.stat().st_size > 0

    # The snapshot is a real database holding the pre-migration state.
    snapshot = sqlite3.connect(str(result.backup))
    try:
        assert snapshot.execute("PRAGMA user_version").fetchone()[0] == 2
        assert snapshot.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            snapshot.execute("SELECT * FROM later_addition")
    finally:
        snapshot.close()


def test_repeated_backup_does_not_overwrite_an_existing_snapshot(
    conn, local_app_data, tmp_path
):
    db.migrate(conn)
    existing = local_app_data / "Katagiri" / "backups" / "katagiri.pre-migrate-2.bak"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"older snapshot")

    pending = _write_migrations(
        tmp_path / "migs",
        ("0003_later.sql", "CREATE TABLE later_addition (a TEXT);\n"),
    )
    result = db.migrate(conn, directory=pending)

    assert result.backup != existing
    assert existing.read_bytes() == b"older snapshot"
    assert result.backup.is_file()


# ---------------------------------------------------------------------------
# atomicity
# ---------------------------------------------------------------------------


def test_failed_migration_rolls_back_and_leaves_version_alone(conn, tmp_path):
    broken = _write_migrations(
        tmp_path / "broken",
        (
            "0001_broken.sql",
            "CREATE TABLE half_done (a TEXT);\nCREATE TABLE half_done (a TEXT);\n",
        ),
    )
    with pytest.raises(db.MigrationError, match="0001_broken.sql") as exc:
        db.migrate(conn, directory=broken)

    assert db.user_version(conn) == 0
    assert "half_done" not in _names(conn, "table")
    # Nothing to restore from, and the message says so rather than implying one.
    assert exc.value.backup is None
    assert "still at version 0" in str(exc.value)


def test_failed_migration_names_the_backup_to_restore_from(conn, tmp_path):
    db.migrate(conn)
    broken = _write_migrations(
        tmp_path / "broken2",
        ("0003_broken.sql", "CREATE TABLE dup (a TEXT);\nCREATE TABLE dup (a TEXT);\n"),
    )
    with pytest.raises(db.MigrationError) as exc:
        db.migrate(conn, directory=broken)

    backup = exc.value.backup
    assert backup is not None and backup.is_file()
    assert str(backup) in str(exc.value)
    assert "still at version 2" in str(exc.value)
    assert db.user_version(conn) == 2


def test_migrate_refuses_to_downgrade(conn, tmp_path):
    """A database upgraded by a newer build is not ours to write to."""
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 99")

    with pytest.raises(db.MigrationError, match="version 99"):
        db.migrate(conn)

    assert db.user_version(conn) == 99


def test_discover_migrations_rejects_a_stray_commit(tmp_path):
    """A COMMIT mid-script would close the runner's transaction early."""
    directory = _write_migrations(
        tmp_path / "stray",
        (
            "0001_stray_commit.sql",
            "CREATE TABLE a (x TEXT);\nCOMMIT;\nCREATE TABLE b (x TEXT);\n",
        ),
    )
    with pytest.raises(db.MigrationError, match="COMMIT"):
        db.discover_migrations(directory)


@pytest.mark.parametrize(
    "body",
    [
        "BEGIN;\nCREATE TABLE a (x TEXT);\n",
        "CREATE TABLE a (x TEXT);\nVACUUM;\n",
        "CREATE TABLE a (x TEXT);\nPRAGMA user_version = 9;\n",
        "CREATE TABLE a (x TEXT);\nROLLBACK;\n",
    ],
)
def test_discover_migrations_rejects_transaction_control(tmp_path, body):
    directory = _write_migrations(tmp_path / "bad_tx", ("0001_bad.sql", body))
    with pytest.raises(db.MigrationError):
        db.discover_migrations(directory)


def test_validation_allows_trigger_bodies_and_case_expressions(tmp_path):
    """BEGIN/END inside a trigger body, and END inside CASE, are legal SQL."""
    directory = _write_migrations(
        tmp_path / "ok",
        (
            "0001_triggers.sql",
            "CREATE TABLE a (x TEXT);\n"
            "CREATE TRIGGER a_no_delete BEFORE DELETE ON a\n"
            "BEGIN\n"
            "    SELECT RAISE(ABORT, 'nope');\n"
            "END;\n"
            "CREATE VIEW v AS SELECT CASE WHEN x = 'y' THEN 1 ELSE 0 END AS f FROM a;\n",
        ),
    )
    assert [m.version for m in db.discover_migrations(directory)] == [1]


def test_discover_migrations_rejects_an_unnumbered_file(tmp_path):
    bad = _write_migrations(tmp_path / "bad", ("init.sql", "SELECT 1;\n"))
    with pytest.raises(db.MigrationError, match="init.sql"):
        db.discover_migrations(bad)


def test_discover_migrations_orders_by_version(tmp_path):
    directory = _write_migrations(
        tmp_path / "many",
        ("0010_ten.sql", "SELECT 1;\n"),
        ("0002_two.sql", "SELECT 1;\n"),
        ("notes.md", "ignored\n"),
    )
    assert [m.version for m in db.discover_migrations(directory)] == [2, 10]


def test_packaged_migrations_are_discoverable():
    packaged = db.discover_migrations()
    assert [m.version for m in packaged] == [1, 2]
    assert packaged[0].name == "init"
    assert packaged[1].name == "audio_anchors"


# ---------------------------------------------------------------------------
# foreign keys
# ---------------------------------------------------------------------------


def test_foreign_keys_are_enforced(conn):
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO item_edge (from_id, to_id, edge_type) "
            "VALUES ('g-wa-topic', 'g-does-not-exist', 'prereq')"
        )


def test_foreign_keys_cascade_lesson_children(conn):
    db.migrate(conn)
    conn.execute(
        "INSERT INTO lesson (id, opened_ts, topic, objective) "
        "VALUES ('l1', '2026-08-19T09:00:00Z', 'topics', 'can mark a topic')"
    )
    conn.execute(
        "INSERT INTO lesson_unresolved (lesson_id, text, created_ts) "
        "VALUES ('l1', 'why wa and not ga?', '2026-08-19T09:10:00Z')"
    )
    conn.execute("DELETE FROM lesson WHERE id = 'l1'")
    assert conn.execute("SELECT COUNT(*) FROM lesson_unresolved").fetchone()[0] == 0


def test_soft_references_are_not_foreign_keys(conn):
    """event.item_id / manual_marks.item_id accept ids with no item row yet."""
    db.migrate(conn)
    conn.execute(_EVENT_INSERT, _EVENT_ROW)
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) "
        "VALUES ('w-nosuch', 'known', '2026-08-19T09:00:00Z')"
    )
    assert conn.execute("SELECT COUNT(*) FROM manual_marks").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# alias resolution
# ---------------------------------------------------------------------------


def test_resolve_alias_reports_redirects_and_follows_chains(conn):
    db.migrate(conn)
    conn.executemany(
        "INSERT INTO alias (alias_id, canonical_id, reason, created_ts) "
        "VALUES (?, ?, ?, '2026-08-19T09:00:00Z')",
        [
            ("g-wa", "g-wa-particle", "rename"),
            ("g-wa-particle", "g-wa-topic", "rename again"),
        ],
    )

    assert db.resolve_alias(conn, "g-wa") == {
        "id": "g-wa",
        "canonical_id": "g-wa-topic",
        "redirected": True,
    }
    assert db.resolve_alias(conn, "g-wa-topic") == {
        "id": "g-wa-topic",
        "canonical_id": "g-wa-topic",
        "redirected": False,
    }


def test_resolve_alias_allows_a_chain_of_exactly_the_hop_limit(conn):
    db.migrate(conn)
    links = [
        (f"g-link-{n:02d}", f"g-link-{n + 1:02d}")
        for n in range(db._MAX_ALIAS_HOPS)
    ]
    assert len(links) == 16
    conn.executemany(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) "
        "VALUES (?, ?, '2026-08-19T09:00:00Z')",
        links,
    )

    resolved = db.resolve_alias(conn, "g-link-00")
    assert resolved["canonical_id"] == "g-link-16"
    assert resolved["redirected"] is True


def test_resolve_alias_rejects_a_chain_past_the_hop_limit(conn):
    db.migrate(conn)
    conn.executemany(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) "
        "VALUES (?, ?, '2026-08-19T09:00:00Z')",
        [
            (f"g-link-{n:02d}", f"g-link-{n + 1:02d}")
            for n in range(db._MAX_ALIAS_HOPS + 1)
        ],
    )
    with pytest.raises(db.DatabaseError, match="hops"):
        db.resolve_alias(conn, "g-link-00")


def test_resolve_alias_detects_a_cycle(conn):
    db.migrate(conn)
    conn.executemany(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) "
        "VALUES (?, ?, '2026-08-19T09:00:00Z')",
        [("g-a", "g-b"), ("g-b", "g-a")],
    )
    with pytest.raises(db.DatabaseError, match="cycle"):
        db.resolve_alias(conn, "g-a")


# ---------------------------------------------------------------------------
# derived views
# ---------------------------------------------------------------------------


def test_lesson_outcome_view_summarises_observations(conn):
    db.migrate(conn)
    conn.execute(
        "INSERT INTO lesson (id, opened_ts, closed_ts, session_id, topic, objective) "
        "VALUES ('l1', '2026-08-19T09:00:00Z', '2026-08-19T10:00:00Z', 'sess-1', "
        "'weather', 'can describe today''s weather')"
    )
    conn.execute(_OBSERVATION_INSERT, _OBSERVATION_ROW)
    conn.execute(
        "INSERT INTO lesson_unresolved (lesson_id, text, created_ts) "
        "VALUES ('l1', 'unanswered', '2026-08-19T09:30:00Z')"
    )

    row = conn.execute("SELECT * FROM lesson_outcome WHERE lesson_id = 'l1'").fetchone()
    assert row["observation_count"] == 1
    assert row["unassisted_count"] == 1
    assert row["unresolved_served"] == 1
    assert row["unresolved_open"] == 1


def test_known_set_view_lets_manual_marks_override_the_anki_mirror(conn):
    db.migrate(conn)
    conn.execute(
        "INSERT INTO item (id, kind, created_ts) "
        "VALUES ('w-mature', 'word', '2026-08-19T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO item (id, kind, created_ts) "
        "VALUES ('w-shaky', 'word', '2026-08-19T09:00:00Z')"
    )
    conn.executemany(
        "INSERT INTO anki_notes (note_id, model) VALUES (?, 'basic')", [(1,), (2,)]
    )
    conn.executemany(
        "INSERT INTO anki_cards (card_id, note_id, ivl) VALUES (?, ?, ?)",
        [(11, 1, 40), (12, 2, 40)],
    )
    conn.executemany(
        "INSERT INTO anki_item_map (note_id, item_id) VALUES (?, ?)",
        [(1, "w-mature"), (2, "w-shaky")],
    )
    # Mirror says both are known (ivl >= 21); a manual mark says otherwise.
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) "
        "VALUES ('w-shaky', 'unknown', '2026-08-19T09:30:00Z')"
    )

    known = {
        row["item_id"]: (row["is_known"], row["source"])
        for row in conn.execute("SELECT * FROM known_set")
    }
    assert known["w-mature"] == (1, "anki")
    assert known["w-shaky"] == (0, "manual")


def test_known_set_latest_mark_wins(conn):
    db.migrate(conn)
    conn.execute(
        "INSERT INTO item (id, kind, created_ts) "
        "VALUES ('w-flip', 'word', '2026-08-19T09:00:00Z')"
    )
    conn.executemany(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES ('w-flip', ?, ?)",
        [
            ("known", "2026-08-19T09:00:00Z"),
            ("unknown", "2026-08-19T10:00:00Z"),
            ("known", "2026-08-19T11:00:00Z"),
        ],
    )
    row = conn.execute(
        "SELECT * FROM known_set WHERE item_id = 'w-flip'"
    ).fetchone()
    assert (row["is_known"], row["source"], row["manual_mark"]) == (1, "manual", "known")


def test_known_set_suspect_flags_without_deciding(conn):
    """'suspect' is a review flag, not a verdict: the mirror still decides."""
    db.migrate(conn)
    conn.executemany(
        "INSERT INTO item (id, kind, created_ts) "
        "VALUES (?, 'word', '2026-08-19T09:00:00Z')",
        [("w-suspect-mature",), ("w-suspect-new",)],
    )
    conn.execute("INSERT INTO anki_notes (note_id, model) VALUES (1, 'basic')")
    conn.execute(
        "INSERT INTO anki_cards (card_id, note_id, ivl) VALUES (11, 1, 40)"
    )
    conn.execute(
        "INSERT INTO anki_item_map (note_id, item_id) VALUES (1, 'w-suspect-mature')"
    )
    conn.executemany(
        "INSERT INTO manual_marks (item_id, mark, ts) "
        "VALUES (?, 'suspect', '2026-08-19T09:30:00Z')",
        [("w-suspect-mature",), ("w-suspect-new",)],
    )

    rows = {
        row["item_id"]: (row["is_known"], row["source"], row["suspect"])
        for row in conn.execute("SELECT * FROM known_set")
    }
    # Mature card + suspect flag: still known, still flagged, mirror decided.
    assert rows["w-suspect-mature"] == (1, "anki", 1)
    assert rows["w-suspect-new"] == (0, "anki", 1)


def test_known_set_includes_items_that_only_have_a_mark(conn):
    """A mark on a not-yet-imported id must not vanish from the known set."""
    db.migrate(conn)
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) "
        "VALUES ('w-not-imported', 'known', '2026-08-19T09:00:00Z')"
    )
    row = conn.execute(
        "SELECT * FROM known_set WHERE item_id = 'w-not-imported'"
    ).fetchone()
    assert row is not None
    assert (row["is_known"], row["source"]) == (1, "manual")


# ---------------------------------------------------------------------------
# no scheduler state anywhere in the source of truth
# ---------------------------------------------------------------------------


def test_no_scheduler_state_columns_outside_the_anki_mirror(conn):
    """Substring match: any column merely *hinting* at scheduling is a finding."""
    db.migrate(conn)
    forbidden = ("due", "ease", "interval", "ivl")
    offenders = []
    for table in sorted(EXPECTED_TABLES):
        if table.startswith("anki_"):
            continue
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            column = row[1].lower()
            if any(needle in column for needle in forbidden):
                offenders.append(f"{table}.{row[1]}")
    assert not offenders, f"scheduler state outside the mirror: {offenders}"


def test_derived_tables_are_all_droppable_under_foreign_keys_on(conn):
    """Rebuild scripts must be able to drop every derived table, FKs enabled."""
    db.migrate(conn)
    assert len(DERIVED_TABLES) == 22
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # One transaction, exactly as a rebuild script must do it.
    conn.execute("BEGIN IMMEDIATE")
    for table in DERIVED_TABLES:
        conn.execute(f'DROP TABLE "{table}"')
    conn.execute("COMMIT")

    remaining = _names(conn, "table")
    assert not (set(DERIVED_TABLES) & remaining)
    # Nothing source-of-truth was collateral damage.
    assert SOURCE_OF_TRUTH_TABLES <= remaining


def test_sentence_text_rowid_is_a_real_column(conn):
    """FTS content_rowid must ride a declared INTEGER PRIMARY KEY, not an
    implicit rowid that VACUUM is free to renumber."""
    db.migrate(conn)
    columns = {
        row[1]: (row[2], row[5])
        for row in conn.execute("PRAGMA table_info(sentence_text)")
    }
    assert columns["rowid"] == ("INTEGER", 1)  # declared type, pk position 1

    conn.execute(
        "INSERT INTO sentence_text (rowid, item_id, jp) VALUES (7, 's-abc123', 'ねこ')"
    )
    conn.execute("VACUUM")
    row = conn.execute(
        "SELECT rowid, item_id FROM sentence_text WHERE item_id = 's-abc123'"
    ).fetchone()
    assert row["rowid"] == 7


def test_item_id_is_unique_in_sentence_text(conn):
    db.migrate(conn)
    conn.execute(
        "INSERT INTO sentence_text (item_id, jp) VALUES ('s-abc123', 'ねこ')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO sentence_text (item_id, jp) VALUES ('s-abc123', 'いぬ')"
        )


# ---------------------------------------------------------------------------
# value constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_ts",
    [
        "2026-08-19 09:00:00",       # space instead of T, no zone
        "2026-08-19T09:00:00+02:00",  # offset instead of Z
        "2026-08-19T09:00:00.123Z",   # fractional seconds break lexical ordering
        "2026-08-19",
        "",
    ],
)
def test_timestamp_columns_reject_non_iso_utc(conn, bad_ts):
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            _OBSERVATION_INSERT,
            (_OBSERVATION_ROW[0], bad_ts, *_OBSERVATION_ROW[2:]),
        )


def test_timestamp_columns_are_checked_on_every_table_that_has_one(conn):
    """No timestamp column may be left unconstrained."""
    db.migrate(conn)
    unchecked = []
    for table in sorted(EXPECTED_TABLES):
        if table.startswith("fts_sentence_"):
            continue
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()[0]
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            column = row[1]
            if not (column.endswith("_ts") or column == "ts"):
                continue
            if f"{column} GLOB" not in sql:
                unchecked.append(f"{table}.{column}")
    assert not unchecked, f"timestamp columns without a format CHECK: {unchecked}"


def test_free_notes_cap_is_exactly_500(conn):
    db.migrate(conn)
    insert = (
        "INSERT INTO lesson (id, opened_ts, topic, objective, free_notes) "
        "VALUES (?, '2026-08-19T09:00:00Z', 't', 'can do', ?)"
    )
    conn.execute(insert, ("l-ok", "x" * 500))
    assert conn.execute(
        "SELECT length(free_notes) FROM lesson WHERE id = 'l-ok'"
    ).fetchone()[0] == 500

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(insert, ("l-too-long", "x" * 501))


def test_lesson_close_cannot_precede_open(conn):
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO lesson (id, opened_ts, closed_ts, topic, objective) "
            "VALUES ('l-bad', '2026-08-19T10:00:00Z', '2026-08-19T09:00:00Z', "
            "'t', 'can do')"
        )
