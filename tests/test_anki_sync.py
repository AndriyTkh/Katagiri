"""Anki review-history sync: daily batching, idempotency, catch-up, atomicity.

The collection fixture is a real ``collection.anki2``, built by
``tests/test_anki_snapshot.py``'s :func:`build_collection` (imported, never run —
that module's tests are its own) and then given the ``revlog`` table Anki writes
its review history into. Reading a real file is the point: the module under test
copies, WAL-recovers and integrity-checks one, and a mock would assert the
implementation back to itself.

Days are pinned to ``Asia/Tokyo`` rather than the host's zone so the fixture's
late-evening reviews land on a known local date; one row sits at 00:30 Tokyo,
which is the *previous* day in UTC, so grouping by UTC would visibly fail.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from katagiri import anki_snapshot, anki_sync
from katagiri import config as config_mod
from katagiri import db
from katagiri.anki_snapshot import AnkiCollectionNotFoundError
from katagiri.anki_sync import CURSOR_KEY, REVIEW_BATCH_TYPE, read_cursor, sync_anki

# Imported for its fixture builder only; importing a test module does not run it.
from test_anki_snapshot import CARDS, NOTES, build_collection

TZ = "Asia/Tokyo"

# Anki's own revlog DDL, trimmed to nothing (every column is kept: the reader
# selects three of them by name, and a narrower table would not prove that).
_REVLOG_DDL = """
CREATE TABLE revlog (
    id integer primary key, cid integer not null, usn integer not null,
    ease integer not null, ivl integer not null, lastIvl integer not null,
    factor integer not null, time integer not null, type integer not null
);
CREATE INDEX ix_revlog_cid ON revlog (cid);
CREATE INDEX ix_revlog_usn ON revlog (usn);
"""


def at(day: str, clock: str) -> int:
    """Epoch milliseconds for a wall-clock time in :data:`TZ` — a revlog id."""
    moment = datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=ZoneInfo(TZ))
    return int(moment.timestamp() * 1000)


D1, D2, D3 = "2026-03-01", "2026-03-02", "2026-03-03"

# (revlog id, card id, ease). Ease 0 is Anki's manual reschedule bookkeeping.
REVIEWS: list[tuple[int, int, int]] = [
    (at(D1, "09:00"), 101, 3),
    (at(D1, "09:01"), 102, 1),
    (at(D1, "09:02"), 102, 3),
    (at(D2, "23:30"), 103, 2),
    (at(D2, "23:45"), 104, 4),
    # 00:30 in Tokyo is 15:30 the previous day in UTC: this row belongs to D3.
    (at(D3, "00:30"), 101, 3),
    (at(D3, "08:00"), 102, 3),
    (at(D3, "09:00"), 103, 0),  # manual reschedule, not a review
]

EXPECTED = {
    D1: {"reviews": 3, "cards": 2, "ease": {"1": 1, "2": 0, "3": 2, "4": 0}},
    D2: {"reviews": 2, "cards": 2, "ease": {"1": 0, "2": 1, "3": 0, "4": 1}},
    D3: {"reviews": 2, "cards": 2, "ease": {"1": 0, "2": 0, "3": 2, "4": 0}},
}
TOTAL_REVIEWS = sum(day["reviews"] for day in EXPECTED.values())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def add_revlog(path: Path, rows: list[tuple[int, int, int]]) -> Path:
    """Create ``revlog`` if absent and append ``(id, cid, ease)`` rows."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "revlog" not in existing:
            conn.executescript(_REVLOG_DDL)
        conn.executemany(
            "INSERT INTO revlog VALUES (?, ?, -1, ?, 10, 5, 2500, 4200, 1)",
            rows,
        )
    finally:
        conn.close()
    return path


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir so config, db and scratch are isolated."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


def _write_config(local_app_data, anki_data_dir: Path | None) -> None:
    config_path = config_mod.config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'scratch_root = "{(local_app_data / "scratch").as_posix()}"']
    if anki_data_dir is not None:
        lines.insert(0, f'anki_data_dir = "{anki_data_dir.as_posix()}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    config_mod.reset_config_cache()


@pytest.fixture
def anki_dir(local_app_data):
    """The configured Anki data directory (profiles live one level down)."""
    path = local_app_data / "anki-data"
    path.mkdir(parents=True, exist_ok=True)
    _write_config(local_app_data, path)
    return path


@pytest.fixture
def collection(anki_dir):
    """A schema-11 collection with the fixture review history."""
    path = build_collection(anki_dir / "User 1" / "collection.anki2")
    return add_revlog(path, REVIEWS)


@pytest.fixture
def conn(anki_dir, monkeypatch):
    """A migrated Katagiri database, with the Anki process check pinned off."""
    monkeypatch.setattr(anki_snapshot, "anki_is_running", lambda: False)
    connection = db.open_db()
    try:
        yield connection
    finally:
        connection.close()


def batch_events(connection: sqlite3.Connection) -> list[dict]:
    """Every ``review_batch`` event, oldest first, payload decoded."""
    rows = connection.execute(
        "SELECT * FROM event WHERE type = ? ORDER BY id", (REVIEW_BATCH_TYPE,)
    ).fetchall()
    out = []
    for row in rows:
        record = dict(row)
        record["payload"] = json.loads(record["payload"])
        out.append(record)
    return out


def scratch_dir() -> Path:
    return config_mod.get_config().scratch_root / "anki-sync"


# ---------------------------------------------------------------------------
# One batch per local day
# ---------------------------------------------------------------------------


def test_each_local_day_becomes_one_review_batch_event(conn, collection):
    result = sync_anki(conn, collection_path=collection, tz=TZ)

    assert (result.batches, result.reviews) == (3, TOTAL_REVIEWS)
    assert (result.first_day, result.last_day) == (D1, D3)
    assert result.skipped == 1  # the ease-0 reschedule

    events = batch_events(conn)
    assert [event["day_key"] for event in events] == [D1, D2, D3]
    for event in events:
        expected = EXPECTED[event["day_key"]]
        payload = event["payload"]
        assert payload["reviews"] == expected["reviews"], event["day_key"]
        assert payload["cards"] == expected["cards"], event["day_key"]
        assert payload["ease_hist"] == expected["ease"], event["day_key"]
        assert payload["first_id"] <= payload["last_id"]
        assert event["session_id"] == anki_sync.SYNC_SESSION_ID
        assert event["type"] == REVIEW_BATCH_TYPE
        assert event["dedupe_key"] == (
            f"anki_revlog:{event['day_key']}:{payload['last_id']}"
        )
        # The event's own day_key is derived by append_event from ts_device, so
        # this agreeing with the grouping is the invariant that keeps a batch
        # from being filed under a different day than it counted.
        assert event["tz"] == TZ


def test_the_day_boundary_is_local_not_utc(conn, collection):
    """The 00:30 Tokyo row is the previous day in UTC; it must land on D3."""
    sync_anki(conn, collection_path=collection, tz=TZ)
    by_day = {event["day_key"]: event["payload"] for event in batch_events(conn)}

    assert by_day[D3]["reviews"] == 2
    assert by_day[D3]["first_id"] == at(D3, "00:30")
    assert by_day[D2]["reviews"] == 2
    assert by_day[D2]["last_id"] == at(D2, "23:45")


def test_the_manual_reschedule_is_not_counted_but_is_passed(conn, collection):
    """Ease 0 is bookkeeping: no review, yet the cursor moves past it."""
    result = sync_anki(conn, collection_path=collection, tz=TZ)

    assert result.reviews == TOTAL_REVIEWS  # the ease-0 row is not in here
    assert result.cursor == at(D3, "09:00")  # ...but the cursor cleared it
    assert read_cursor(conn) == at(D3, "09:00")


def test_a_day_of_only_reschedules_produces_no_event(conn, anki_dir):
    path = add_revlog(
        build_collection(anki_dir / "User 1" / "collection.anki2"),
        [(at(D1, "10:00"), 101, 0), (at(D1, "10:05"), 102, 0)],
    )
    result = sync_anki(conn, collection_path=path, tz=TZ)

    assert (result.batches, result.reviews, result.skipped) == (0, 0, 2)
    assert batch_events(conn) == []
    assert read_cursor(conn) == at(D1, "10:05")


def test_the_cursor_holds_the_newest_synced_revlog_id(conn, collection):
    result = sync_anki(conn, collection_path=collection, tz=TZ)
    stored = conn.execute(
        "SELECT value FROM metadata WHERE key = ?", (CURSOR_KEY,)
    ).fetchone()
    assert int(stored[0]) == result.cursor == max(row[0] for row in REVIEWS)


def test_an_empty_revlog_syncs_nothing_and_leaves_the_cursor_at_zero(conn, anki_dir):
    path = add_revlog(build_collection(anki_dir / "User 1" / "collection.anki2"), [])
    result = sync_anki(conn, collection_path=path, tz=TZ)

    assert (result.batches, result.reviews, result.cursor) == (0, 0, 0)
    assert (result.first_day, result.last_day) == (None, None)
    assert read_cursor(conn) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM metadata WHERE key = ?", (CURSOR_KEY,)
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Idempotency and catch-up
# ---------------------------------------------------------------------------


def test_rerunning_with_no_new_reviews_appends_nothing(conn, collection):
    first = sync_anki(conn, collection_path=collection, tz=TZ)
    ids_after_first = [event["id"] for event in batch_events(conn)]

    second = sync_anki(conn, collection_path=collection, tz=TZ)
    third = sync_anki(conn, collection_path=collection, tz=TZ)

    assert (second.batches, second.reviews) == (0, 0)
    assert (third.batches, third.reviews) == (0, 0)
    assert second.cursor == third.cursor == first.cursor
    # Not merely "the same count": the same rows.
    assert [event["id"] for event in batch_events(conn)] == ids_after_first


def test_reviews_arriving_after_a_day_was_synced_append_a_new_batch(conn, collection):
    """Catch-up: the same day, later rows, and no lost or overwritten history."""
    first = sync_anki(conn, collection_path=collection, tz=TZ)
    before = batch_events(conn)

    late = [(at(D3, "21:00"), 103, 4), (at(D3, "21:01"), 105, 2)]
    add_revlog(collection, late)
    second = sync_anki(conn, collection_path=collection, tz=TZ)

    assert (second.batches, second.reviews) == (1, 2)
    assert second.days[0].day_key == D3
    assert second.cursor == at(D3, "21:01") > first.cursor

    after = batch_events(conn)
    assert len(after) == len(before) + 1
    # Nothing already logged was rewritten: the earlier events survive verbatim.
    assert after[: len(before)] == before

    # Two batches for D3, distinct dedupe keys, and their counts are additive.
    d3 = [event for event in after if event["day_key"] == D3]
    assert len(d3) == 2
    assert len({event["dedupe_key"] for event in d3}) == 2
    assert sum(event["payload"]["reviews"] for event in d3) == EXPECTED[D3][
        "reviews"
    ] + 2
    assert d3[1]["payload"]["ease_hist"] == {"1": 0, "2": 1, "3": 0, "4": 1}
    # Every dedupe key in the log is still unique (the UNIQUE index says so, but
    # a collision would have been *absorbed*, not raised — hence the count).
    keys = [event["dedupe_key"] for event in after]
    assert len(set(keys)) == len(keys)


def test_a_retried_batch_collapses_onto_the_one_already_logged(conn, collection):
    """Same batch, same key: the second append returns the first event's id."""
    sync_anki(conn, collection_path=collection, tz=TZ)
    events = batch_events(conn)
    target = events[-1]

    conn.execute("BEGIN IMMEDIATE")
    returned = anki_sync.append_event(
        conn,
        type=REVIEW_BATCH_TYPE,
        session_id=anki_sync.SYNC_SESSION_ID,
        dedupe_key=target["dedupe_key"],
        payload={"reviews": 999},
    )
    conn.execute("COMMIT")

    assert returned == target["id"]
    assert len(batch_events(conn)) == len(events)
    assert batch_events(conn)[-1]["payload"]["reviews"] == target["payload"]["reviews"]


def test_a_corrupt_cursor_is_refused_rather_than_reset(conn, collection):
    conn.execute(
        "INSERT INTO metadata(key, value, updated_ts) VALUES (?, 'yesterday', ?)",
        (CURSOR_KEY, "2026-03-04T00:00:00Z"),
    )
    with pytest.raises(anki_sync.AnkiSyncError, match=CURSOR_KEY):
        sync_anki(conn, collection_path=collection, tz=TZ)
    assert batch_events(conn) == []


# ---------------------------------------------------------------------------
# The mirror comes along
# ---------------------------------------------------------------------------


def test_the_card_and_note_mirror_is_refreshed(conn, collection):
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 0

    result = sync_anki(conn, collection_path=collection, tz=TZ)

    assert (result.mirror.cards, result.mirror.notes) == (len(CARDS), len(NOTES))
    assert result.mirror.schema_version == 11
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == len(CARDS)
    assert conn.execute("SELECT COUNT(*) FROM anki_notes").fetchone()[0] == len(NOTES)
    assert conn.execute("SELECT COUNT(*) FROM mirror_meta").fetchone()[0] == 1


def test_a_no_op_sync_still_refreshes_the_mirror(conn, collection):
    sync_anki(conn, collection_path=collection, tz=TZ)
    conn.execute("DELETE FROM anki_cards")

    result = sync_anki(conn, collection_path=collection, tz=TZ)

    assert result.batches == 0
    assert result.mirror.cards == len(CARDS)
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == len(CARDS)


def test_the_live_collection_is_never_opened_and_the_copy_is_removed(
    conn, collection, monkeypatch
):
    calls: list[str] = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):
        calls.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    sync_anki(conn, collection_path=collection, tz=TZ)

    assert calls, "the sync opened no database at all"
    assert not any(str(collection) in call for call in calls)
    assert not any(collection.parent.as_posix() in call for call in calls)
    # The revlog read is read-only and immutable, like the mirror's.
    immutable = [call for call in calls if "immutable=1" in call]
    assert len(immutable) == 2 and all("mode=ro" in call for call in immutable)
    assert list(scratch_dir().rglob("*.anki2")) == []


def test_reviews_living_only_in_an_uncheckpointed_wal_are_still_synced(conn, anki_dir):
    """A sync right after a study session must see what Anki has not flushed."""
    path = build_collection(anki_dir / "User 1" / "collection.anki2", wal=True)
    add_revlog(path, [(at(D1, "09:00"), 101, 3)])

    reader = sqlite3.connect(str(path))
    writer = sqlite3.connect(str(path), isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM revlog").fetchone()
    writer.execute(
        "INSERT INTO revlog VALUES (?, 102, -1, 4, 10, 5, 2500, 4200, 1)",
        (at(D1, "09:30"),),
    )
    wal = Path(str(path) + "-wal")
    assert wal.is_file() and wal.stat().st_size > 0, "fixture left no WAL to recover"
    try:
        result = sync_anki(conn, collection_path=path, tz=TZ)
    finally:
        writer.close()
        reader.close()

    assert (result.batches, result.reviews) == (1, 2)
    assert result.cursor == at(D1, "09:30")
    assert result.mirror.stale is True  # a live journal is provenance, not an error


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_a_failure_mid_append_leaves_no_events_and_an_unmoved_cursor(
    conn, collection, monkeypatch
):
    real_append = anki_sync.append_event
    calls: list[int] = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(anki_sync, "append_event", flaky)

    with pytest.raises(sqlite3.OperationalError):
        sync_anki(conn, collection_path=collection, tz=TZ)

    assert len(calls) == 2, "the failure did not land mid-way through the batches"
    assert batch_events(conn) == []
    assert read_cursor(conn) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM metadata WHERE key = ?", (CURSOR_KEY,)
    ).fetchone()[0] == 0

    # And the next run, unpatched, replays everything the failed one dropped.
    monkeypatch.setattr(anki_sync, "append_event", real_append)
    result = sync_anki(conn, collection_path=collection, tz=TZ)
    assert (result.batches, result.reviews) == (3, TOTAL_REVIEWS)


def test_a_failure_writing_the_cursor_takes_the_events_with_it(
    conn, collection, monkeypatch
):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(anki_sync, "_write_cursor", boom)

    with pytest.raises(sqlite3.OperationalError):
        sync_anki(conn, collection_path=collection, tz=TZ)

    assert batch_events(conn) == []
    assert read_cursor(conn) == 0


# ---------------------------------------------------------------------------
# A collection that is not there
# ---------------------------------------------------------------------------


def test_an_explicit_missing_collection_raises_and_writes_nothing(conn, anki_dir):
    with pytest.raises(AnkiCollectionNotFoundError):
        sync_anki(conn, collection_path=anki_dir / "ghost.anki2", tz=TZ)

    assert batch_events(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM anki_cards").fetchone()[0] == 0
    assert read_cursor(conn) == 0


def test_a_collection_without_a_revlog_table_is_refused(conn, anki_dir):
    path = build_collection(anki_dir / "User 1" / "collection.anki2")  # no revlog

    with pytest.raises(anki_sync.AnkiSyncError, match="revlog"):
        sync_anki(conn, collection_path=path, tz=TZ)

    assert batch_events(conn) == []
    assert read_cursor(conn) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_run_syncs_and_reports(conn, collection, capsys):
    code = anki_sync.main(["run", "--collection", str(collection), "--tz", TZ])
    output = capsys.readouterr().out

    assert code == 0
    assert f"reviews synced : {TOTAL_REVIEWS}" in output
    assert f"{D1} .. {D3}" in output
    # Written through main's own connection; visible on this one too.
    assert len(batch_events(conn)) == 3


def test_cli_status_prints_the_cursor_and_the_last_batch(conn, collection, capsys):
    sync_anki(conn, collection_path=collection, tz=TZ)
    capsys.readouterr()

    assert anki_sync.main(["status"]) == 0
    output = capsys.readouterr().out
    assert str(max(row[0] for row in REVIEWS)) in output
    assert D3 in output
    assert f"reviews      : {EXPECTED[D3]['reviews']}" in output


def test_cli_status_before_any_sync_says_so(anki_dir, capsys):
    assert anki_sync.main(["status"]) == 0
    output = capsys.readouterr().out
    assert "revlog cursor  : 0" in output
    assert "none appended yet" in output


def test_cli_run_exits_2_when_the_collection_is_missing(anki_dir, capsys):
    ghost = anki_dir / "User 1" / "collection.anki2"

    code = anki_sync.main(["run", "--collection", str(ghost), "--tz", TZ])

    output = capsys.readouterr().out
    assert code == 2
    assert output.startswith("error:")
    assert "collection" in output

    # Nothing was written: no events, no cursor, no mirror.
    connection = db.open_db()
    try:
        assert batch_events(connection) == []
        assert read_cursor(connection) == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM anki_cards"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_cli_run_exits_2_when_anki_is_not_configured(local_app_data, capsys):
    """Anki not installed, or never pointed at: a message, not a traceback."""
    _write_config(local_app_data, None)

    code = anki_sync.main(["run", "--tz", TZ])

    output = capsys.readouterr().out
    assert code == 2
    assert output.startswith("error:")
    assert "anki_data_dir" in output


def test_cli_run_exits_2_when_no_profile_holds_a_collection(anki_dir, capsys):
    (anki_dir / "Empty Profile").mkdir()

    assert anki_sync.main(["run", "--tz", TZ]) == 2
    assert "collection.anki2" in capsys.readouterr().out


def test_the_scheduled_task_line_is_documented_not_registered():
    """The module tells the operator how to schedule it; it never does it."""
    doc = anki_sync.__doc__ or ""
    assert "schtasks /Create" in doc and "python -m katagiri.anki_sync run" in doc
    source = Path(anki_sync.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    assert "schtasks" not in body, "the module must not create a scheduled task"
