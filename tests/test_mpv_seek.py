"""mpv seek logger: protocol, detection, batching, reconnect, status.

Nothing here needs mpv installed. The whole module talks to the outside world
through one seam — :class:`katagiri.mpv_seek_logger.Transport` — so these tests
inject a scripted in-memory mpv instead: it parses real JSON IPC requests off
the wire, advances a timeline of frames, interleaves ``seek`` notifications
before the reply they precede, and reports EOF when its script runs out. That
last part is what makes the reconnect path testable at all.

The privacy assertion (full paths never reach the log) is checked against every
column of every row rather than against the payload alone, because "we only put
the basename in the payload" is a claim about one call site, and the thing worth
guaranteeing is a property of the database.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from katagiri import db, events
from katagiri import config as config_mod
from katagiri import mpv_seek_logger as msl

# A deliberately private-looking location: the directory is what must never land.
MEDIA_DIR = r"D:\Users\andrii\Videos\Japanese\Shirokuma Cafe S01"
MEDIA_PATH = rf"{MEDIA_DIR}\ep03.mkv"
MEDIA_FILE = "ep03.mkv"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_setup_logging(monkeypatch):
    """Never let ``msl.main`` install a real handler bound to captured stderr.

    ``setup_logging`` is idempotent and caches its handler on first call; under
    pytest capture that handler would bind to a temp capture file and leak into
    later tests in the session (see test_mcp_tools.py's identical hazard note).
    """
    monkeypatch.setattr(msl, "setup_logging", lambda *args, **kwargs: None)


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Isolate config, database and backups under a tmp %LOCALAPPDATA%."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def migrated_db(local_app_data):
    """Bring the schema up at the configured path and hand back a reader."""
    connection = db.open_db()
    try:
        yield connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Scripted mpv
# ---------------------------------------------------------------------------


def frame(
    time_pos: float | None,
    *,
    path: str | None = MEDIA_PATH,
    title: str | None = "Shirokuma Cafe",
    events_out: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One poll's worth of mpv state.

    ``events_out`` names notifications mpv emits *before* answering this frame's
    ``time-pos`` request — i.e. they happened before the position being read.
    """
    return {
        "time-pos": time_pos,
        "path": path,
        "media-title": title,
        "events": events_out,
    }


class FakeMpv:
    """In-memory duplex stand-in for the mpv JSON IPC pipe.

    Advances one frame per ``time-pos`` request, which is the first request the
    logger issues each tick. Once the frames are exhausted every read returns
    empty bytes, exactly as a closed pipe does.
    """

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = [dict(item) for item in frames]
        self.index = -1
        self.out: deque[dict[str, Any]] = deque()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    # -- Transport ---------------------------------------------------------

    def send(self, line: bytes) -> None:
        assert line.endswith(b"\n"), "IPC requests must be newline-terminated"
        message = json.loads(line.decode("utf-8"))
        self.sent.append(message)
        command = message["command"]
        assert command[0] == "get_property", f"unexpected command {command!r}"
        name = command[1]

        if name == "time-pos":
            self.index += 1
            current = self._frame()
            if current is not None:
                for event in current.get("events", ()):
                    self.out.append({"event": event})

        current = self._frame()
        if current is None:
            return  # script exhausted; readline() now reports EOF
        value = current.get(name)
        if value is None:
            self.out.append(
                {"request_id": message["request_id"], "error": "property unavailable"}
            )
        else:
            self.out.append(
                {
                    "request_id": message["request_id"],
                    "error": "success",
                    "data": value,
                }
            )

    def readline(self) -> bytes:
        if not self.out:
            return b""
        return json.dumps(self.out.popleft()).encode("utf-8") + b"\n"

    def close(self) -> None:
        self.closed = True

    # -- helpers -----------------------------------------------------------

    def _frame(self) -> dict[str, Any] | None:
        if 0 <= self.index < len(self.frames):
            return self.frames[self.index]
        return None

    def push_event(self, name: str) -> None:
        """Queue a notification to be read before the next reply."""
        self.out.append({"event": name})


def drive(frames: list[dict[str, Any]], **kwargs: Any) -> list[msl.SeekRecord]:
    """Poll a tracker over ``frames`` through the real client and return records."""
    client = msl.MpvClient(FakeMpv(frames))
    tracker = msl.SeekTracker(**kwargs)
    records: list[msl.SeekRecord] = []
    for _ in frames:
        records.extend(tracker.poll(client))
    return records


def ticking(step: float = 1.0):
    """A fake monotonic clock advancing ``step`` seconds per call."""
    return itertools.count(0.0, step).__next__


def run(frames: list[dict[str, Any]], **kwargs: Any) -> int:
    """Run the daemon loop over one scripted mpv, one tick per frame.

    Uses the module's real default database seam (the tmp %LOCALAPPDATA%), so
    these tests exercise the startup migrate and the per-flush connection the
    daemon actually uses.
    """
    fake = FakeMpv(frames)
    kwargs.setdefault("max_ticks", len(frames))
    return msl.run_logger(
        connect=lambda: fake,
        sleep=lambda _seconds: None,
        monotonic=ticking(),
        **kwargs,
    )


def seek_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM event WHERE type = ? ORDER BY id", (msl.EVENT_TYPE,)
    ).fetchall()
    return [dict(row) for row in rows]


def payloads(conn) -> list[dict[str, Any]]:
    return [json.loads(row["payload"]) for row in seek_rows(conn)]


# ---------------------------------------------------------------------------
# Documentation is part of the deliverable
# ---------------------------------------------------------------------------


def test_the_exact_mpv_conf_line_is_documented():
    # The user has to paste this into mpv.conf; if it drifts from the pipe the
    # logger actually opens, the feature silently records nothing.
    assert msl.MPV_CONF_LINE == r"input-ipc-server=\\.\pipe\mpv-katagiri"
    assert msl.MPV_CONF_LINE in msl.__doc__
    assert msl.PIPE_PATH in msl.MPV_CONF_LINE
    # And the optional at-logon registration is documented but never performed.
    assert "schtasks /Create" in msl.__doc__


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------


def test_client_demultiplexes_events_from_replies():
    fake = FakeMpv([frame(12.0, events_out=("seek", "playback-restart"))])
    client = msl.MpvClient(fake)

    assert client.get_property("time-pos") == 12.0
    assert client.get_property("path") == MEDIA_PATH
    names = [message["event"] for message in client.take_events()]
    assert names == ["seek", "playback-restart"]
    # Drained, not re-delivered.
    assert client.take_events() == []
    assert [message["command"] for message in fake.sent] == [
        ["get_property", "time-pos"],
        ["get_property", "path"],
    ]


def test_unavailable_property_is_none_not_an_exception():
    client = msl.MpvClient(FakeMpv([frame(None)]))
    assert client.get_property("time-pos") is None


def test_end_of_stream_raises_mpv_disconnected():
    client = msl.MpvClient(FakeMpv([]))
    with pytest.raises(msl.MpvDisconnected):
        client.get_property("time-pos")


def test_garbage_line_is_skipped_rather_than_fatal():
    fake = FakeMpv([frame(5.0)])
    original_readline = fake.readline
    served = {"done": False}

    def readline() -> bytes:
        if not served["done"]:
            served["done"] = True
            return b"not json at all\n"
        return original_readline()

    fake.readline = readline  # type: ignore[method-assign]
    client = msl.MpvClient(fake)
    assert client.get_property("time-pos") == 5.0


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_backward_jump_past_the_threshold_is_a_seek_back():
    records = drive(
        [
            frame(30.0),
            frame(31.0),
            frame(24.0, events_out=("seek",)),
        ]
    )
    assert len(records) == 1
    record = records[0]
    assert record.direction == "back"
    assert record.from_s == 31.0
    assert record.to_s == 24.0
    assert record.delta_s == -7.0
    assert record.file == MEDIA_FILE


def test_a_jump_of_exactly_two_seconds_counts():
    # The threshold is inclusive: "at least 2s back" is the documented rule.
    records = drive([frame(10.0), frame(8.0, events_out=("seek",))])
    assert [(r.direction, r.delta_s) for r in records] == [("back", -2.0)]


def test_sub_threshold_jump_is_ignored_even_with_a_seek_event():
    records = drive(
        [
            frame(10.0),
            frame(9.2, events_out=("seek",)),
            frame(10.2),
            frame(11.2),
        ]
    )
    assert records == []


def test_forward_seek_is_classified_forward():
    records = drive([frame(10.0), frame(95.0, events_out=("seek",))])
    assert [(r.direction, r.delta_s) for r in records] == [("forward", 85.0)]


def test_plain_playback_never_looks_like_a_seek():
    # Without a seek notification, a moving position is just the film playing —
    # including a suspiciously large gap after a stall.
    records = drive([frame(1.0), frame(2.0), frame(3.0), frame(40.0)])
    assert records == []


def test_a_reply_that_raced_the_seek_is_still_caught_one_tick_later():
    # mpv can emit `seek` just before answering with the *pre*-seek position.
    # The grace tick exists for exactly this, and must not lose the jump nor
    # misreport where it came from.
    records = drive(
        [
            frame(50.0),
            frame(50.4, events_out=("seek",)),  # notification beat the position
            frame(41.0),  # position has now settled
        ]
    )
    assert len(records) == 1
    assert records[0].from_s == 50.0
    assert records[0].to_s == 41.0
    assert records[0].direction == "back"


def test_switching_file_is_not_a_seek():
    records = drive(
        [
            frame(600.0),
            frame(2.0, path=rf"{MEDIA_DIR}\ep04.mkv", events_out=("seek",)),
            frame(3.0, path=rf"{MEDIA_DIR}\ep04.mkv"),
        ]
    )
    assert records == []


def test_idle_mpv_produces_nothing():
    records = drive([frame(None, path=None), frame(None, path=None)])
    assert records == []


def test_seek_before_any_position_is_known_is_dropped_not_guessed():
    # The first frame of a file is a new timeline, never a seek within the old.
    records = drive([frame(0.0, events_out=("seek",)), frame(0.5)])
    assert records == []


def test_seek_out_of_an_unknown_position_is_dropped():
    # A gap where mpv had no position (loading, idle) leaves no honest `from_s`,
    # so the jump after it is dropped rather than measured from a stale sample.
    records = drive(
        [
            frame(300.0),
            frame(None),
            frame(12.0, events_out=("seek",)),
            frame(13.0),
        ]
    )
    assert records == []


def test_title_that_looks_like_a_path_is_reduced_to_a_basename():
    # mpv falls back to the filename for media-title; a fallback must not be a
    # way for the directory to get in through the back door.
    records = drive(
        [
            frame(20.0, title=MEDIA_PATH),
            frame(10.0, title=MEDIA_PATH, events_out=("seek",)),
        ]
    )
    assert records[0].title == MEDIA_FILE


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (MEDIA_PATH, MEDIA_FILE),
        ("/home/me/media/ep03.mkv", "ep03.mkv"),
        ("https://host/path/clip.mkv?token=secret#t=30", "clip.mkv"),
        ("ep03.mkv", "ep03.mkv"),
        ("", None),
        (None, None),
    ],
)
def test_basename_strips_every_directory_form(raw, expected):
    assert msl.basename(raw) == expected


def test_threshold_must_be_positive():
    with pytest.raises(ValueError):
        msl.SeekTracker(threshold_s=0)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_events_land_with_the_expected_payload(migrated_db):
    rc = run([frame(30.0), frame(22.0, events_out=("seek",))])
    assert rc == 0

    rows = seek_rows(migrated_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["type"] == "seek"
    assert row["session_id"].startswith(msl.SESSION_PREFIX)
    assert row["media_ref"] is None
    # 'back' lives in the payload, not in the `direction` column: that column's
    # CHECK constraint enumerates study directions only.
    assert row["direction"] is None

    payload = json.loads(row["payload"])
    assert payload == {
        "file": MEDIA_FILE,
        "title": "Shirokuma Cafe",
        "from_s": 30.0,
        "to_s": 22.0,
        "delta_s": -8.0,
        "direction": "back",
    }


def test_no_full_path_ever_reaches_the_database(migrated_db):
    run([frame(30.0), frame(22.0, events_out=("seek",))])

    rows = seek_rows(migrated_db)
    assert rows
    haystack = " ".join(
        str(value) for row in rows for value in row.values() if value is not None
    )
    assert MEDIA_FILE in haystack
    for fragment in (MEDIA_DIR, "D:", "andrii", "Videos", "Japanese"):
        assert fragment not in haystack, f"{fragment!r} leaked into the event log"


def test_repeated_identical_rewinds_are_two_events(migrated_db):
    # The dedupe decision, asserted: rewinding to the same second twice is the
    # signal, not a duplicate. Any dedupe_key would have collapsed these.
    run(
        [
            frame(30.0),
            frame(22.0, events_out=("seek",)),
            frame(30.0, events_out=("seek",)),
            frame(22.0, events_out=("seek",)),
        ]
    )

    rows = seek_rows(migrated_db)
    directions = [json.loads(row["payload"])["direction"] for row in rows]
    assert directions == ["back", "forward", "back"]
    assert all(row["dedupe_key"] is None for row in rows)

    identical = [
        json.loads(row["payload"]) for row in rows if row["payload"] is not None
    ]
    repeats = [item for item in identical if item["from_s"] == 30.0]
    assert len(repeats) == 2
    assert repeats[0] == repeats[1]
    assert len({row["id"] for row in rows}) == len(rows)


def test_forward_seeks_are_recorded_on_the_same_channel(migrated_db):
    run([frame(10.0), frame(200.0, events_out=("seek",))])
    assert [item["direction"] for item in payloads(migrated_db)] == ["forward"]


def test_records_are_batched_until_the_flush_interval(migrated_db):
    # The fake clock advances one second per tick, so a 10s flush interval must
    # produce exactly one mid-run flush over 12 ticks, plus the exit flush.
    opened = []

    def open_conn():
        connection = db.connect()
        opened.append(connection)
        return connection

    frames = [frame(30.0), frame(20.0, events_out=("seek",))]
    frames += [frame(20.0 + index) for index in range(1, 9)]
    frames += [frame(100.0, events_out=("seek",)), frame(101.0)]
    assert len(frames) == 12

    msl.run_logger(
        connect=lambda: FakeMpv(frames),
        open_conn=open_conn,
        sleep=lambda _s: None,
        monotonic=ticking(),
        flush_interval_s=10.0,
        max_ticks=12,
    )

    # Two flushes: one when the interval elapsed, one on the way out. A rewind
    # is therefore never held longer than the interval, and the DB is touched
    # twice in twelve seconds rather than twelve times.
    assert len(opened) == 2
    assert [item["direction"] for item in payloads(migrated_db)] == ["back", "forward"]


def test_nothing_is_written_when_there_is_nothing_to_write(migrated_db):
    opened = []

    def open_conn():
        connection = db.connect()
        opened.append(connection)
        return connection

    msl.run_logger(
        connect=lambda: FakeMpv([frame(1.0), frame(2.0), frame(3.0)]),
        open_conn=open_conn,
        sleep=lambda _s: None,
        monotonic=ticking(100.0),
        max_ticks=3,
    )
    # A quiet film costs the event DB nothing: no connection is even opened.
    assert opened == []
    assert seek_rows(migrated_db) == []


def test_flush_holds_records_when_the_database_refuses(migrated_db):
    attempts = {"count": 0}

    def open_conn():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return db.connect()

    msl.run_logger(
        connect=lambda: FakeMpv(
            [frame(30.0), frame(20.0, events_out=("seek",)), frame(21.0), frame(22.0)]
        ),
        open_conn=open_conn,
        sleep=lambda _s: None,
        monotonic=ticking(100.0),
        max_ticks=4,
    )
    # First flush failed; the record survived to the next one rather than being
    # lost or retried into a crash.
    assert attempts["count"] == 2
    assert [item["direction"] for item in payloads(migrated_db)] == ["back"]


def test_pending_backlog_is_capped(monkeypatch):
    monkeypatch.setattr(msl, "MAX_PENDING", 3)
    pending = [
        msl.SeekRecord(
            file="a.mkv",
            title=None,
            from_s=float(index),
            to_s=0.0,
            delta_s=-float(index),
            direction="back",
            ts=events.utc_now_stamp(),
            day="2026-08-19",
        )
        for index in range(6)
    ]
    msl._trim_pending(pending)
    assert len(pending) == 3
    # The oldest go, so the most recent rewinds are the ones kept.
    assert [record.from_s for record in pending] == [3.0, 4.0, 5.0]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_reconnects_after_mpv_closes_and_keeps_recording(migrated_db):
    first = FakeMpv([frame(30.0), frame(20.0, events_out=("seek",))])
    second = FakeMpv(
        [
            frame(500.0, path=rf"{MEDIA_DIR}\ep09.mkv"),
            frame(480.0, path=rf"{MEDIA_DIR}\ep09.mkv", events_out=("seek",)),
        ]
    )
    supply = iter([first, second])
    connects = {"count": 0}

    def connect():
        connects["count"] += 1
        return next(supply)

    rc = msl.run_logger(
        connect=connect,
        sleep=lambda _s: None,
        monotonic=ticking(100.0),
        # Two frames, then the tick that hits EOF and reconnects, then the second
        # instance's two frames.
        max_ticks=5,
    )

    assert rc == 0
    assert connects["count"] == 2
    assert first.closed is True
    records = payloads(migrated_db)
    assert [item["file"] for item in records] == [MEDIA_FILE, "ep09.mkv"]
    assert all(item["direction"] == "back" for item in records)


def test_missing_pipe_is_retried_rather_than_fatal(migrated_db):
    attempts = {"count": 0}
    fake = FakeMpv([frame(30.0), frame(20.0, events_out=("seek",))])

    def connect():
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return fake

    rc = msl.run_logger(
        connect=connect,
        sleep=lambda _s: None,
        monotonic=ticking(100.0),
        max_ticks=4,
    )
    assert rc == 0
    assert attempts["count"] == 3
    assert [item["direction"] for item in payloads(migrated_db)] == ["back"]


def test_ctrl_c_flushes_and_exits_clean(migrated_db):
    fake = FakeMpv([frame(30.0), frame(20.0, events_out=("seek",)), frame(21.0)])
    calls = {"count": 0}

    def sleep(_seconds: float) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt

    rc = msl.run_logger(
        connect=lambda: fake,
        sleep=sleep,
        # A long interval so nothing has flushed yet: the exit path is what is
        # under test.
        monotonic=ticking(0.1),
        max_ticks=None,
        flush_interval_s=1000.0,
    )

    assert rc == 0
    assert fake.closed is True
    assert [item["direction"] for item in payloads(migrated_db)] == ["back"]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def _append_seek(conn, *, direction, delta, file="ep03.mkv", ts=None, day="2026-08-19"):
    events.append_event(
        conn,
        type=msl.EVENT_TYPE,
        session_id=f"{msl.SESSION_PREFIX}{day}",
        ts_device=ts or events.utc_now_stamp(),
        payload={
            "file": file,
            "title": None,
            "from_s": 10.0,
            "to_s": 10.0 + delta,
            "delta_s": delta,
            "direction": direction,
        },
    )


def test_status_counts_the_last_24_hours(migrated_db):
    _append_seek(migrated_db, direction="back", delta=-6.0)
    _append_seek(migrated_db, direction="back", delta=-4.5)
    _append_seek(migrated_db, direction="back", delta=-3.0, file="ep04.mkv")
    _append_seek(migrated_db, direction="forward", delta=90.0)

    summary = msl.status(migrated_db)
    assert summary["hours"] == 24
    assert summary["total"] == 4
    assert summary["back"] == 3
    assert summary["forward"] == 1
    assert summary["unclassified"] == 0
    assert summary["seconds_rewound"] == pytest.approx(13.5)
    assert summary["top_files_by_rewind"] == [
        {"file": "ep03.mkv", "back": 2},
        {"file": "ep04.mkv", "back": 1},
    ]


def test_status_window_excludes_older_events(migrated_db):
    # Relative to the real clock, so the test does not rot on a fixed date.
    old = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(events.TS_FORMAT)
    _append_seek(migrated_db, direction="back", delta=-9.0, ts=old)
    _append_seek(migrated_db, direction="back", delta=-2.0)

    summary = msl.status(migrated_db)
    assert summary["total"] == 1
    assert summary["seconds_rewound"] == pytest.approx(2.0)
    # Widen the window and the old one reappears — it was filtered, not missing.
    assert msl.status(migrated_db, hours=24 * 365)["total"] == 2


def test_status_on_an_empty_log_is_zeroes_not_an_error(migrated_db):
    summary = msl.status(migrated_db)
    assert summary["total"] == 0
    assert summary["back"] == 0
    assert "nothing recorded yet" in msl.format_status(summary)


def test_status_rejects_a_nonsense_window(migrated_db):
    with pytest.raises(ValueError):
        msl.status(migrated_db, hours=0)


def test_format_status_names_the_most_rewound_file(migrated_db):
    _append_seek(migrated_db, direction="back", delta=-6.0)
    text = msl.format_status(msl.status(migrated_db))
    assert "rewinds" in text
    assert "ep03.mkv" in text


def test_status_cli_prints_json(migrated_db, capsys):
    _append_seek(migrated_db, direction="back", delta=-5.0)
    rc = msl.main(["status", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["back"] == 1
    # Diagnostics never share the report's stream.
    assert captured.out.lstrip().startswith("{")


def test_run_subcommand_parses_its_knobs():
    args = msl.build_parser().parse_args(["run", "--threshold", "3.5"])
    assert args.command == "run"
    assert args.threshold == 3.5
    assert args.pipe == msl.PIPE_PATH
