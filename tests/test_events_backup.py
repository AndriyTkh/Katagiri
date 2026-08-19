"""Event log, known-set access, and backup/restore.

The restore drill at the bottom is the point of the whole file: a backup nobody
has ever restored is a hope, not a backup.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zipfile

import pytest

from katagiri import backup, db, events, known
from katagiri import config as config_mod


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir so config, db and backups are isolated."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def conn(local_app_data):
    """A migrated database at the configured path."""
    connection = db.open_db()
    try:
        yield connection
    finally:
        connection.close()


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# ---------------------------------------------------------------------------
# ULID
# ---------------------------------------------------------------------------


def test_ulid_is_26_crockford_characters():
    ulid = events.new_ulid()
    assert len(ulid) == events.ULID_LENGTH == 26
    assert set(ulid) <= set(events.CROCKFORD_ALPHABET)
    # Crockford excludes these four so a hand-copied id cannot be misread.
    assert not set("ILOU") & set(ulid)


def test_ulid_is_strictly_monotonic_within_the_process():
    # A tight loop puts many of these inside one millisecond, which is exactly
    # the case the random-increment path exists for.
    ulids = [events.new_ulid() for _ in range(2000)]
    assert ulids == sorted(ulids)
    assert len(set(ulids)) == len(ulids)


def test_ulid_stays_monotonic_when_the_clock_goes_backwards():
    first = events.new_ulid()
    # 1970: a clock that stepped back decades must still not produce a smaller
    # id, or the event log's ordering silently breaks.
    second = events.new_ulid(ts_ms=1000)
    third = events.new_ulid()
    assert first < second < third


def test_ulid_carries_its_own_timestamp():
    before = int(time.time() * 1000)
    encoded = events.ulid_time_ms(events.new_ulid())
    after = int(time.time() * 1000)
    assert before - 1000 <= encoded <= after + 1000


def test_ulid_rejects_a_timestamp_beyond_48_bits():
    with pytest.raises(ValueError, match="48-bit"):
        events.new_ulid(ts_ms=1 << 48)


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


def test_append_event_stamps_the_strict_timestamp_format(conn):
    event_id = events.append_event(conn, type="review", session_id="s1")
    row = conn.execute("SELECT * FROM event WHERE id = ?", (event_id,)).fetchone()
    assert len(row["ts_server"]) == 20 and row["ts_server"].endswith("Z")
    assert row["ts_device"] == row["ts_server"]
    assert len(row["day_key"]) == 10
    assert row["session_id"] == "s1"


def test_append_event_accepts_an_explicit_strict_timestamp(conn):
    events.append_event(
        conn,
        type="review",
        session_id="s1",
        ts_device="2026-08-19T09:00:00Z",
        tz="Asia/Tokyo",
    )
    row = conn.execute("SELECT * FROM event").fetchone()
    assert row["ts_device"] == "2026-08-19T09:00:00Z"
    assert row["tz"] == "Asia/Tokyo"


def test_append_event_does_not_launder_a_fractional_timestamp(conn):
    # The schema's GLOB CHECK is the enforcer; append_event must pass a
    # caller-supplied stamp through verbatim so a broken device clock surfaces
    # instead of being quietly rewritten.
    with pytest.raises(sqlite3.IntegrityError):
        events.append_event(
            conn,
            type="review",
            session_id="s1",
            ts_device="2026-08-19T09:00:00.123Z",
        )
    assert _count(conn, "event") == 0


def test_append_event_dedupe_key_is_idempotent(conn):
    first = events.append_event(
        conn, type="review", session_id="s1", dedupe_key="retry-1", grade=3
    )
    second = events.append_event(
        conn, type="review", session_id="s1", dedupe_key="retry-1", grade=4
    )
    assert first == second
    assert _count(conn, "event") == 1
    # The absorbed retry must not have rewritten the original either.
    assert conn.execute("SELECT grade FROM event").fetchone()[0] == 3


def test_dedupe_does_not_swallow_a_real_constraint_violation(conn):
    # ON CONFLICT is scoped to dedupe_key alone: INSERT OR IGNORE would have
    # hidden this CHECK failure.
    with pytest.raises(sqlite3.IntegrityError):
        events.append_event(
            conn, type="review", session_id="s1", grade=9, dedupe_key="k"
        )
    assert _count(conn, "event") == 0


def test_append_event_encodes_payload_as_json(conn):
    events.append_event(
        conn,
        type="mining",
        session_id="s1",
        payload={"items": ["猫", "犬"], "count": 2},
    )
    payload = json.loads(conn.execute("SELECT payload FROM event").fetchone()[0])
    assert payload == {"count": 2, "items": ["猫", "犬"]}


def test_event_log_refuses_updates_and_deletes(conn):
    event_id = events.append_event(conn, type="review", session_id="s1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE event SET grade = 1 WHERE id = ?", (event_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM event WHERE id = ?", (event_id,))


# ---------------------------------------------------------------------------
# mark_item
# ---------------------------------------------------------------------------


def test_mark_item_writes_the_mark_and_its_event(conn):
    result = events.mark_item(conn, "w-neko", "known", note="from episode 3")
    assert result["mark"] == "known"

    mark = conn.execute("SELECT * FROM manual_marks").fetchone()
    assert (mark["item_id"], mark["mark"], mark["note"]) == (
        "w-neko",
        "known",
        "from episode 3",
    )
    event = conn.execute("SELECT * FROM event").fetchone()
    assert event["type"] == "mark_known"
    assert event["item_id"] == "w-neko"
    assert event["id"] == result["event_id"]


def test_mark_item_resolves_an_alias_before_writing(conn):
    conn.execute(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) "
        "VALUES ('w-old', 'w-new', '2026-08-19T09:00:00Z')"
    )
    result = events.mark_item(conn, "w-old", "known")
    assert result["item_id"] == "w-new"
    assert result["redirected"] is True
    assert conn.execute("SELECT item_id FROM manual_marks").fetchone()[0] == "w-new"


def test_mark_item_rejects_an_unknown_mark(conn):
    with pytest.raises(ValueError, match="mark must be one of"):
        events.mark_item(conn, "w-neko", "maybe")
    assert _count(conn, "manual_marks") == 0
    assert _count(conn, "event") == 0


def test_mark_item_writes_neither_row_when_the_mark_insert_fails(conn, monkeypatch):
    # A mark value that passes the Python guard but fails the schema CHECK.
    monkeypatch.setitem(events.MARK_EVENT_TYPES, "bogus", "mark_bogus")
    with pytest.raises(sqlite3.IntegrityError):
        events.mark_item(conn, "w-neko", "bogus")
    assert _count(conn, "manual_marks") == 0
    assert _count(conn, "event") == 0


def test_mark_item_writes_neither_row_when_the_event_insert_fails(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("event log unavailable")

    monkeypatch.setattr(events, "append_event", boom)
    with pytest.raises(RuntimeError, match="event log unavailable"):
        events.mark_item(conn, "w-neko", "known")
    # The mark had already been inserted inside the transaction; the rollback is
    # what makes "every mutation an event" true rather than aspirational.
    assert _count(conn, "manual_marks") == 0
    assert _count(conn, "event") == 0


# ---------------------------------------------------------------------------
# recent_events
# ---------------------------------------------------------------------------


def test_recent_events_filters_and_orders_newest_first(conn):
    events.append_event(conn, type="review", session_id="s1")
    events.append_event(conn, type="mining", session_id="s1")
    third = events.append_event(conn, type="review", session_id="s1")

    recent = events.recent_events(conn)
    assert [row["id"] for row in recent][0] == third
    assert len(recent) == 3

    only_reviews = events.recent_events(conn, type="review")
    assert {row["type"] for row in only_reviews} == {"review"}
    assert len(events.recent_events(conn, limit=1)) == 1


def test_recent_events_since_day_excludes_earlier_days(conn):
    events.append_event(
        conn, type="review", session_id="s1", ts_device="2020-01-01T00:00:00Z"
    )
    events.append_event(conn, type="review", session_id="s1")
    assert len(events.recent_events(conn, since_day="2021-01-01")) == 1


# ---------------------------------------------------------------------------
# import_study_log
# ---------------------------------------------------------------------------


def _write_study_log(path, records):
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8"
    )
    return path


def test_import_study_log_is_idempotent_across_runs(conn, tmp_path):
    log = _write_study_log(
        tmp_path / "study-log.jsonl",
        [
            {
                "ts": "2026-08-17T20:00:00Z",
                "type": "study_session",
                "minutes": 35,
                "activities": ["anki", "immersion"],
                "items_mined": 4,
                "notes": "episode 3",
            },
            {
                "ts": "2026-08-18T20:30:00Z",
                "type": "study_session",
                "minutes": 20,
                "activities": ["shadowing"],
                "items_mined": 0,
                "notes": None,
            },
            {"ts": "2026-08-18T21:00:00Z", "type": "something_else"},
        ],
    )

    first = events.import_study_log(conn, log)
    assert (first["imported"], first["duplicate"], first["skipped"]) == (2, 0, 1)

    second = events.import_study_log(conn, log)
    assert (second["imported"], second["duplicate"], second["skipped"]) == (0, 2, 1)

    assert _count(conn, "event") == 2
    rows = events.recent_events(conn, type=events.STUDY_LOG_TYPE)
    payload = json.loads(rows[0]["payload"])
    assert payload["minutes"] in (20, 35)
    assert rows[0]["dedupe_key"].startswith("study:")


def test_import_study_log_normalizes_foreign_timestamps(conn, tmp_path):
    # Foreign data *is* normalised — unlike a caller-supplied ts_device — because
    # accepting what the outside world wrote is the whole job here.
    log = _write_study_log(
        tmp_path / "s.jsonl",
        [{"ts": "2026-08-17T20:00:00.500+00:00", "type": "study_session", "minutes": 5}],
    )
    events.import_study_log(conn, log)
    row = conn.execute("SELECT ts_device, dedupe_key FROM event").fetchone()
    assert row["ts_device"] == "2026-08-17T20:00:00Z"
    assert row["dedupe_key"] == "study:2026-08-17T20:00:00Z"


def test_import_study_log_handles_an_empty_file(conn, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert events.import_study_log(conn, empty)["records"] == 0
    assert _count(conn, "event") == 0


def test_import_study_log_refuses_a_corrupt_line(conn, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"ts": "2026-08-17T20:00:00Z"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        events.import_study_log(conn, bad)


def test_import_study_log_reports_a_missing_file(conn, tmp_path):
    with pytest.raises(FileNotFoundError):
        events.import_study_log(conn, tmp_path / "nope.jsonl")


# ---------------------------------------------------------------------------
# known set
# ---------------------------------------------------------------------------


def _add_item(connection, item_id, kind="word", kanji=None, reading=None):
    connection.execute(
        "INSERT INTO item (id, kind, kanji, reading, created_ts) "
        "VALUES (?, ?, ?, ?, '2026-08-19T09:00:00Z')",
        (item_id, kind, kanji, reading),
    )


def test_known_word_follows_an_alias_to_the_canonical_item(conn):
    _add_item(conn, "w-new", kanji="猫", reading="ねこ")
    events.mark_item(conn, "w-new", "known")
    conn.execute(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) "
        "VALUES ('w-old', 'w-new', '2026-08-19T09:00:00Z')"
    )

    result = known.known_word(conn, "w-old")
    assert result["item_id"] == "w-new"
    assert result["is_known"] is True
    assert result["redirected"] is True
    assert result["matched_by"] == "alias"
    assert result["source"] == "manual"


def test_known_word_matches_a_surface_form(conn):
    _add_item(conn, "w-neko", kanji="猫", reading="ねこ")
    events.mark_item(conn, "w-neko", "known")
    result = known.known_word(conn, "ねこ")
    assert (result["item_id"], result["matched_by"], result["is_known"]) == (
        "w-neko",
        "surface",
        True,
    )


def test_known_word_returns_candidates_instead_of_guessing(conn):
    _add_item(conn, "w-ashita", kanji="明日", reading="あした")
    _add_item(conn, "w-myounichi", kanji="明日", reading="みょうにち")
    result = known.known_word(conn, "明日")
    assert result["ambiguous"] is True
    assert result["is_known"] is None
    assert {c["item_id"] for c in result["candidates"]} == {
        "w-ashita",
        "w-myounichi",
    }


def test_known_word_distinguishes_not_found_from_not_known(conn):
    _add_item(conn, "w-known-item")
    absent = known.known_word(conn, "w-never-heard-of")
    assert absent["found"] is False and absent["is_known"] is None

    present = known.known_word(conn, "w-known-item")
    assert present["found"] is True and present["is_known"] is False


def test_known_word_reports_suspect_separately_from_the_verdict(conn):
    _add_item(conn, "w-sus")
    events.mark_item(conn, "w-sus", "suspect")
    result = known.known_word(conn, "w-sus")
    # 'suspect' is a flag for review, not a verdict: the mirror still decides.
    assert result["suspect"] is True
    assert result["is_known"] is False
    assert result["source"] == "anki"


def test_known_word_rejects_an_empty_query(conn):
    with pytest.raises(ValueError):
        known.known_word(conn, "   ")


def test_known_set_stats_counts_by_kind_and_source(conn):
    _add_item(conn, "w-a")
    _add_item(conn, "k-b", kind="kanji")
    _add_item(conn, "g-c", kind="grammar")
    events.mark_item(conn, "w-a", "known")
    events.mark_item(conn, "k-b", "unknown")
    # A mark on an id with no item row must stay visible.
    events.mark_item(conn, "w-not-imported", "known")

    stats = known.known_set_stats(conn)
    assert stats["total"] == 4
    assert stats["known"] == 2
    assert stats["unknown"] == 2
    assert stats["by_kind"]["word"]["total"] == 1
    assert stats["by_kind"]["unlinked"]["total"] == 1
    assert stats["by_source"]["manual"]["total"] == 3
    assert stats["latest_marks_by_value"] == {"known": 2, "unknown": 1}


# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------


def test_create_backup_produces_an_openable_database(conn, tmp_path):
    events.append_event(conn, type="review", session_id="s1")
    path = backup.create_backup(conn, tmp_path / "backups")

    report = backup.verify_backup(path)
    assert report["ok"] and report["integrity"] == "ok"
    assert report["user_version"] == db.latest_version()
    assert report["event_count"] == 1


def test_create_backup_defaults_to_the_config_backups_dir(conn, local_app_data):
    path = backup.create_backup(conn)
    assert path.parent == backup.default_backup_dir()
    assert path.parent.name == "backups"


def test_create_backup_prunes_to_the_newest_keep(conn, tmp_path):
    dest = tmp_path / "backups"
    made = [backup.create_backup(conn, dest, keep=5) for _ in range(5)]
    assert len(backup.list_backups(dest)) == 5

    backup.create_backup(conn, dest, keep=2)
    remaining = backup.list_backups(dest)
    assert len(remaining) == 2
    # Oldest go first, and filename order is chronological order.
    assert made[0] not in remaining
    assert remaining == sorted(remaining)


def test_prune_leaves_pre_migration_snapshots_alone(conn, tmp_path):
    dest = tmp_path / "backups"
    backup.create_backup(conn, dest, keep=1)
    sacred = dest / "katagiri.pre-migrate-0.bak"
    sacred.write_bytes(b"not really a database")

    backup.prune_backups(dest, keep=1)
    assert sacred.exists()


def test_create_backup_rejects_a_nonsense_keep(conn, tmp_path):
    with pytest.raises(ValueError, match="keep must be at least 1"):
        backup.create_backup(conn, tmp_path, keep=0)


def test_verify_backup_rejects_a_file_that_is_not_a_database(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"x" * 4096)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(junk)


def test_copy_vault_snapshot_captures_text_and_skips_local_and_derived(tmp_path):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "local").mkdir()
    (vault / ".derived").mkdir()
    (vault / "notes" / "keep.md").write_text("kept", encoding="utf-8")
    (vault / "notes" / "log.jsonl").write_text("{}\n", encoding="utf-8")
    (vault / "notes" / "audio.mp3").write_bytes(b"binary")
    (vault / "local" / "machine.md").write_text("skip", encoding="utf-8")
    (vault / ".derived" / "cache.jsonl").write_text("skip", encoding="utf-8")

    archive = backup.copy_vault_snapshot(vault, tmp_path / "backups")
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert names == {"notes/keep.md", "notes/log.jsonl"}


def test_copy_vault_snapshot_rejects_a_missing_vault(tmp_path):
    with pytest.raises(backup.BackupError, match="not a directory"):
        backup.copy_vault_snapshot(tmp_path / "nowhere")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_refuses_an_existing_target_then_honours_force(conn, tmp_path):
    events.append_event(conn, type="review", session_id="s1")
    snapshot = backup.create_backup(conn, tmp_path / "backups")

    target = tmp_path / "restored.db"
    target.write_bytes(b"precious existing file")

    with pytest.raises(backup.BackupError, match="already exists"):
        backup.restore_backup(snapshot, target)
    assert target.read_bytes() == b"precious existing file"

    result = backup.restore_backup(snapshot, target, force=True)
    assert result["overwrote"] is True
    assert result["integrity"] == "ok"
    assert result["user_version"] == db.latest_version()


def test_restore_creates_a_target_that_does_not_exist_yet(conn, tmp_path):
    snapshot = backup.create_backup(conn, tmp_path / "backups")
    target = tmp_path / "new" / "katagiri.db"
    result = backup.restore_backup(snapshot, target)
    assert result["overwrote"] is False
    assert target.is_file()


def test_restore_refuses_a_corrupt_snapshot_before_touching_the_target(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"SQLite format 3\x00" + b"\x00" * 2048)
    target = tmp_path / "live.db"
    target.write_bytes(b"still here")

    with pytest.raises(backup.BackupError):
        backup.restore_backup(corrupt, target, force=True)
    # Restoring a broken backup over a working database would turn a recoverable
    # situation into an unrecoverable one.
    assert target.read_bytes() == b"still here"


def test_restore_clears_a_stale_wal_sidecar(conn, tmp_path):
    snapshot = backup.create_backup(conn, tmp_path / "backups")
    target = tmp_path / "target.db"
    target.write_bytes(b"old database")
    stale_wal = tmp_path / "target.db-wal"
    stale_wal.write_bytes(b"old write-ahead log")

    backup.restore_backup(snapshot, target, force=True)
    # A WAL belonging to the old database replayed against the restored one is
    # silent corruption.
    assert not stale_wal.exists()


# ---------------------------------------------------------------------------
# The drill
# ---------------------------------------------------------------------------


def _remove_database(path):
    for suffix in ("", "-wal", "-shm"):
        candidate = path.with_name(path.name + suffix)
        candidate.unlink(missing_ok=True)


def test_rehearsed_restore_drill_after_the_database_is_deleted(local_app_data, tmp_path):
    """Populate, back up, destroy, restore, and prove the events survived."""
    db_path = tmp_path / "drill.db"
    backups = tmp_path / "backups"

    conn = db.open_db(db_path)
    try:
        _add_item(conn, "w-neko", kanji="猫", reading="ねこ")
        events.mark_item(conn, "w-neko", "known", note="mined from episode 3")
        expected_ids = [
            events.append_event(
                conn, type="review", session_id="drill", item_id="w-neko", grade=3
            ),
            events.append_event(
                conn, type="mining", session_id="drill", payload={"items_mined": 2}
            ),
        ]
        log = _write_study_log(
            tmp_path / "study-log.jsonl",
            [{"ts": "2026-08-18T20:00:00Z", "type": "study_session", "minutes": 30}],
        )
        events.import_study_log(conn, log)

        before = events.recent_events(conn)
        before_stats = known.known_set_stats(conn)
        snapshot = backup.create_backup(conn, backups)
    finally:
        conn.close()

    assert backup.verify_backup(snapshot)["ok"]

    # Disaster.
    _remove_database(db_path)
    assert not db_path.exists()

    result = backup.restore_backup(snapshot, db_path)
    assert result["integrity"] == "ok"
    assert result["user_version"] == db.latest_version()

    restored = db.open_db(db_path)
    try:
        after = events.recent_events(restored)
        assert [row["id"] for row in after] == [row["id"] for row in before]
        assert set(expected_ids) <= {row["id"] for row in after}
        # The full record survived, not just the row count.
        review = next(row for row in after if row["type"] == "review")
        assert (review["item_id"], review["grade"]) == ("w-neko", 3)
        assert known.known_set_stats(restored) == before_stats
        assert known.known_word(restored, "猫")["is_known"] is True

        # And the restored database is a live database, not a museum piece.
        events.append_event(restored, type="review", session_id="post-restore")
        assert len(events.recent_events(restored)) == len(before) + 1
    finally:
        restored.close()


def test_rehearsed_restore_drill_over_a_corrupted_original(local_app_data, tmp_path):
    """The likelier disaster: the file is still there, but it is rubbish."""
    db_path = tmp_path / "corrupt-drill.db"
    conn = db.open_db(db_path)
    try:
        events.append_event(conn, type="review", session_id="drill", grade=4)
        snapshot = backup.create_backup(conn, tmp_path / "backups")
        expected = events.recent_events(conn)
    finally:
        conn.close()

    db_path.write_bytes(b"\x00" * 8192)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(db_path)

    backup.restore_backup(snapshot, db_path, force=True)

    restored = db.open_db(db_path)
    try:
        assert events.recent_events(restored) == expected
    finally:
        restored.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_create_then_verify_then_restore(local_app_data, tmp_path, capsys):
    db_path = tmp_path / "cli.db"
    conn = db.open_db(db_path)
    try:
        events.append_event(conn, type="review", session_id="cli")
    finally:
        conn.close()

    dest = tmp_path / "backups"
    assert backup.main(["create", "--db", str(db_path), "--dest", str(dest)]) == 0
    capsys.readouterr()

    snapshot = backup.list_backups(dest, stem="cli")[-1]
    assert backup.main(["verify", str(snapshot)]) == 0
    assert "integrity    : ok" in capsys.readouterr().out

    target = tmp_path / "cli-restored.db"
    assert backup.main(["restore", str(snapshot), "--target", str(target)]) == 0
    out = capsys.readouterr().out
    assert "offline only" in out
    assert target.is_file()


def test_cli_reports_an_error_without_a_traceback(tmp_path, capsys):
    assert backup.main(["verify", str(tmp_path / "missing.db")]) == 2
    assert "error:" in capsys.readouterr().out
