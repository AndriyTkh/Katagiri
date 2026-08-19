"""Today.md export tests: the section registry, due-count semantics, and writing.

Modelled on ``tests/test_sensei.py``. The repository's own vault is never
touched: every write goes to a throwaway ``tmp_path`` root.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from katagiri import ankimorphs_ingest, anki_snapshot
from katagiri import config as config_mod
from katagiri import db, events
from katagiri import today_export as te

#: The zone every day-index assertion in this file pins. Asia/Tokyo is +09:00 the
#: whole year round, so a boundary test written against it cannot be moved by a
#: daylight saving transition — the assertions are about the rollover hour and
#: nothing else. The machine's own zone is deliberately never used: the day index
#: turns over at a *local* hour, so leaving the zone unpinned makes every expected
#: index depend on where the suite happens to run. Concretely, ``NOW`` below is
#: 12:00Z, which is 02:00 local on a machine at UTC-10 — before the 04:00 rollover,
#: hence one day index lower — and the fixture's expected counts would be wrong
#: there through no fault of the code. See ``render`` and the ``zone=TOKYO``
#: arguments throughout.
TOKYO = ZoneInfo("Asia/Tokyo")

DAY = date(2026, 8, 19)
NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
NOW_EPOCH = int(NOW.timestamp())
# The collection was created exactly 100 day-boundaries ago *as measured in
# TOKYO*, so Anki's own "today" index for this fixture is 100 — but only when the
# rollover is resolved in that zone, which is why every assertion about it passes
# zone=TOKYO.
CRT = NOW_EPOCH - 100 * 86400


@pytest.fixture(autouse=True)
def _stub_setup_logging(monkeypatch):
    """Keep ``te.main`` from installing a real handler bound to captured stderr.

    Same hazard as test_sensei.py: ``setup_logging`` caches its handler on first
    call, and under pytest capture that handler would bind to a temp capture file
    and leak into later tests in the session.
    """
    monkeypatch.setattr(
        "katagiri.logging_setup.setup_logging", lambda *args, **kwargs: None
    )


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


@pytest.fixture
def vault(tmp_path):
    """A throwaway vault root. The repository's own vault is never touched."""
    root = tmp_path / "vault"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def seed_event(
    conn: sqlite3.Connection,
    *,
    day: date | str,
    type: str,
    payload: dict | None = None,
    item_id: str | None = None,
) -> None:
    """Insert one event with an exact ``day_key`` (see test_sensei.seed)."""
    key = day if isinstance(day, str) else day.isoformat()
    stamp = f"{key}T12:00:00Z"
    conn.execute(
        """
        INSERT INTO event (id, ts_device, ts_server, tz, day_key, session_id,
                           type, item_id, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            events.new_ulid(),
            stamp,
            stamp,
            "UTC",
            key,
            "seed",
            type,
            item_id,
            None if payload is None else json.dumps(payload, sort_keys=True),
        ),
    )


# card_id, note_id, deck, ivl, due, queue, ctype
DUE_CARDS = [
    (1, 1, "Core", 30, 99, 2, 2),            # review, overdue
    (2, 2, "Core", 21, 100, 2, 2),           # review, due today
    (3, 3, "Core", 40, 101, 2, 2),           # review, tomorrow
    (4, 4, "Core", 0, NOW_EPOCH - 10, 1, 1),  # learning, due now
    (5, 5, "Core", 0, NOW_EPOCH + 3600, 1, 1),  # learning, later today
    (6, 6, "Core", 1, 100, 3, 3),            # day-learning, due today
    (7, 7, "Core", 0, 5, 0, 0),              # new
    (8, 8, "Core", 25, 12, -1, 2),           # suspended
    (9, 9, "Core", 25, 12, -2, 2),           # buried
]
EXPECTED_DUE = 4


def seed_mirror(
    conn: sqlite3.Connection,
    cards=DUE_CARDS,
    *,
    crt: int | None = CRT,
    snapshot_ts: str = "2026-08-19T06:00:00Z",
) -> None:
    """Fill the Anki mirror in its current (queue-aware) shape."""
    anki_snapshot.ensure_mirror_shape(conn)
    conn.executemany(
        "INSERT INTO anki_cards"
        "(card_id, note_id, deck, ivl, due, reps, lapses, mod, queue, ctype) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?)",
        cards,
    )
    conn.execute(
        "INSERT OR REPLACE INTO mirror_meta"
        "(id, snapshot_ts, collection_mtime, anki_schema_version, crt) "
        "VALUES (1, ?, ?, ?, ?)",
        (snapshot_ts, 1700000000, 18, crt),
    )


def seed_legacy_mirror(conn: sqlite3.Connection) -> None:
    """Fill the mirror in the pre-B1 shape: no queue column, no crt."""
    conn.executemany(
        "INSERT INTO anki_cards(card_id, note_id, deck, ivl, due) "
        "VALUES (?, ?, ?, ?, ?)",
        [(row[0], row[1], row[2], row[3], row[4]) for row in DUE_CARDS],
    )
    conn.execute(
        "INSERT OR REPLACE INTO mirror_meta"
        "(id, snapshot_ts, collection_mtime, anki_schema_version) "
        "VALUES (1, ?, ?, ?)",
        ("2026-08-01T06:00:00Z", 1700000000, 18),
    )


def render(conn: sqlite3.Connection) -> str:
    """Render the page for the fixture day, with the rollover zone pinned.

    ``zone=TOKYO`` rather than the default ``None``: leaving it unset resolves
    Anki's day rollover against the machine's zone, which makes every due-count
    assertion in this file depend on where the suite runs (see ``TOKYO``).
    """
    return te.render_today(te.build_context(conn, today=DAY, now=NOW, zone=TOKYO))


def section_text(text: str, heading: str) -> str:
    """The body of one ``## heading`` block, and nothing else.

    Asserting against the whole page is how a section test passes after its
    section is deleted: "no baseline" from the known-set block or "No streak yet"
    from the streak block satisfies a loose ``"no " in text`` just as well as the
    sentence under test does. Isolating the block first makes the assertion fail
    when the section it is about stops rendering.
    """
    lines = text.splitlines()
    try:
        start = lines.index(f"## {heading}")
    except ValueError as exc:  # pragma: no cover - the assertion below is clearer
        raise AssertionError(f"no '## {heading}' section in the page") from exc
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


# ---------------------------------------------------------------------------
# The section registry
# ---------------------------------------------------------------------------


def test_registry_holds_every_phase_b_section():
    keys = [builder.section_key for builder in te.SECTIONS]
    assert keys == [
        "anki_due",
        "streak",
        "known_trend",
        "weakest_morphs",
        "resume",
    ]
    # No duplicates; the key is what a later phase looks a section up by.
    assert len(set(keys)) == len(keys)


def test_every_registered_section_reaches_the_page(conn):
    text = render(conn)
    for builder in te.SECTIONS:
        section = builder(te.build_context(conn, today=DAY, now=NOW, zone=TOKYO))
        assert section is not None, f"{builder.section_key} rendered nothing"
        assert f"## {section.heading}" in text


def test_a_later_phase_appends_a_builder_without_touching_the_renderer(
    conn, monkeypatch
):
    """The extension seam: append to SECTIONS, never edit render_today."""

    @te.section("phase_d_demo")
    def _extra(ctx):
        return te.Section(
            key="phase_d_demo", heading="Error museum", lines=("nothing yet.",)
        )

    monkeypatch.setattr(te, "SECTIONS", (*te.SECTIONS, _extra))
    text = render(conn)
    assert "## Error museum" in text
    assert "nothing yet." in text


def test_a_section_returning_none_is_skipped(conn, monkeypatch):
    @te.section("silent")
    def _silent(ctx):
        return None

    monkeypatch.setattr(te, "SECTIONS", (*te.SECTIONS, _silent))
    text = render(conn)
    assert "silent" not in text


def test_a_raising_section_is_stated_on_the_page_not_swallowed(conn, monkeypatch):
    """The backstop in ``render_sections``: a builder nobody predicted can raise,
    and a section that vanishes silently reads as "nothing to report" — which is
    the one thing the page must never imply. The failure must be visible, and the
    traceback must stay on stderr rather than being pasted into the vault."""

    @te.section("phase_d_demo")
    def _explodes(ctx):
        raise RuntimeError("morph query blew up at /home/secret/collection.anki2")

    monkeypatch.setattr(te, "SECTIONS", (*te.SECTIONS, _explodes))
    text = render(conn)

    assert "## phase_d_demo (failed)" in text
    body = section_text(text, "phase_d_demo (failed)")
    assert "could not be built" in body
    # Stated absence: unknown, explicitly not empty.
    assert "unknown, not as empty" in body

    # No traceback and no exception text on the page.
    assert "Traceback (most recent call last)" not in text
    assert "RuntimeError" not in text
    assert "morph query blew up" not in text
    assert "collection.anki2" not in text

    # The page as a whole still renders; one bad section does not sink it.
    assert "## Anki reviews" in text
    assert "## Streak" in text
    assert "## Resume" in text


def test_rendering_is_deterministic(conn):
    seed_mirror(conn)
    assert render(conn) == render(conn)


# ---------------------------------------------------------------------------
# Anki due count
# ---------------------------------------------------------------------------


def test_due_count_applies_anki_queue_semantics(conn):
    seed_mirror(conn)
    # zone=TOKYO, not the machine's: the expected index is 100 only when the
    # rollover is resolved in the zone CRT was computed against.
    result = te.anki_due_count(conn, now=NOW, zone=TOKYO)

    assert result["available"] is True
    assert result["collection_day"] == 100
    assert result["review_due"] == 2       # cards 1 and 2
    assert result["learning_due"] == 2     # cards 4 (seconds) and 6 (day-based)
    assert result["count"] == EXPECTED_DUE
    assert result["reason"] is None


def test_new_suspended_and_buried_cards_are_never_due(conn):
    seed_mirror(conn, [row for row in DUE_CARDS if row[5] <= 0])
    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is True
    assert result["count"] == 0


def test_learning_cards_use_epoch_seconds_not_day_indexes(conn):
    # queue 1 stores an absolute epoch second. A day index there (e.g. 100)
    # would look like 1970 and wrongly read as due.
    seed_mirror(conn, [(1, 1, "Core", 0, NOW_EPOCH + 60, 1, 1)])
    assert te.anki_due_count(conn, now=NOW)["count"] == 0

    conn.execute("DELETE FROM anki_cards")
    conn.executemany(
        "INSERT INTO anki_cards(card_id, note_id, deck, ivl, due, queue, ctype) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(2, 2, "Core", 0, NOW_EPOCH - 60, 1, 1)],
    )
    assert te.anki_due_count(conn, now=NOW)["count"] == 1


def test_the_day_index_turns_over_at_the_rollover_hour_not_at_midnight(conn):
    """04:00 local, not 00:00. Between midnight and the rollover Anki is still
    serving the previous day's index, so a card scheduled for the new day must
    read as not due at 01:00 and as due at 05:00 on that same calendar date."""
    crt = int(datetime(2026, 5, 11, 9, 0, tzinfo=TOKYO).timestamp())
    before_rollover = datetime(2026, 8, 19, 1, 0, tzinfo=TOKYO)
    after_rollover = datetime(2026, 8, 19, 5, 0, tzinfo=TOKYO)

    assert te.collection_day_index(crt, before_rollover, zone=TOKYO) == 99
    assert te.collection_day_index(crt, after_rollover, zone=TOKYO) == 100

    # And the count the learner actually reads moves with it.
    seed_mirror(conn, [(1, 1, "Core", 30, 100, 2, 2)], crt=crt)
    early = te.anki_due_count(conn, now=before_rollover, zone=TOKYO)
    late = te.anki_due_count(conn, now=after_rollover, zone=TOKYO)
    assert (early["available"], early["count"]) == (True, 0)
    assert (late["available"], late["count"]) == (True, 1)


def test_a_collection_created_before_its_rollover_still_starts_at_day_zero(conn):
    """Anki takes ``crt``'s local *calendar date* at the rollover hour and never
    steps back a day (rslib ``days_elapsed``, schedv2 ``_daysSinceCreation``).
    Rebasing day zero onto the *previous* rollover for a collection made at 02:00
    would put every index one ahead of Anki's, permanently — and an index one too
    high counts every card Anki has scheduled for tomorrow as due today."""
    born_before = int(datetime(2026, 8, 19, 2, 0, tzinfo=TOKYO).timestamp())
    born_after = int(datetime(2026, 8, 19, 9, 0, tzinfo=TOKYO).timestamp())
    same_evening = datetime(2026, 8, 19, 23, 0, tzinfo=TOKYO)
    next_evening = datetime(2026, 8, 20, 23, 0, tzinfo=TOKYO)

    # Both were made on the same local date, so both are on day zero — the hour
    # they were made at does not move the index.
    assert te.collection_day_index(born_before, same_evening, zone=TOKYO) == 0
    assert te.collection_day_index(born_after, same_evening, zone=TOKYO) == 0
    assert te.collection_day_index(born_before, next_evening, zone=TOKYO) == 1
    assert te.collection_day_index(born_after, next_evening, zone=TOKYO) == 1

    # The consequence the learner would feel: a card Anki has parked on day 1 is
    # not work owed on day 0.
    seed_mirror(conn, [(1, 1, "Core", 10, 1, 2, 2)], crt=born_before)
    result = te.anki_due_count(conn, now=same_evening, zone=TOKYO)
    assert result["collection_day"] == 0
    assert result["count"] == 0


def test_a_partially_queued_mirror_is_unavailable_not_a_quieter_count(conn):
    """One card whose ``queue`` is NULL is enough. ``queue = 2`` against NULL is
    NULL, which the CASE reads as false, so a half-filled mirror would answer with
    a confident count that silently omits every card whose state was not recorded.
    Missing state is not evidence that nothing is owed."""
    anki_snapshot.ensure_mirror_shape(conn)
    conn.executemany(
        "INSERT INTO anki_cards(card_id, note_id, deck, ivl, due, queue, ctype) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, 1, "Core", 30, 99, 2, 2),       # unambiguously due
            (2, 2, "Core", 21, 100, None, 2),   # state unknown
            (3, 3, "Core", 40, 98, None, 2),    # state unknown
        ],
    )
    conn.execute(
        "INSERT OR REPLACE INTO mirror_meta"
        "(id, snapshot_ts, collection_mtime, anki_schema_version, crt) "
        "VALUES (1, ?, ?, ?, ?)",
        ("2026-08-19T06:00:00Z", 1700000000, 18, CRT),
    )

    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is False
    assert result["count"] is None
    assert result["cards_mirrored"] == 3
    assert "2 of 3" in result["reason"]

    body = section_text(render(conn), "Anki reviews")
    assert "unavailable" in body.lower()
    # The one queued card must not be presented as the answer.
    assert "1 card due" not in body


def test_a_mirror_predating_the_queue_columns_reports_unavailable(conn):
    seed_legacy_mirror(conn)
    result = te.anki_due_count(conn, now=NOW)

    assert result["available"] is False
    assert result["count"] is None
    assert "snapshot" in result["reason"]


def test_the_due_section_says_so_rather_than_guessing(conn):
    seed_legacy_mirror(conn)
    text = render(conn)
    assert "unavailable" in text.lower()
    # Nine cards are in the mirror; none of their counts may be presented as due.
    assert "9 cards due" not in text


def test_a_mirror_without_crt_cannot_be_dated(conn):
    seed_mirror(conn, crt=None)
    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is False
    assert result["count"] is None
    assert result["reason"]


@pytest.mark.parametrize("crt", [-100000, -1, 0, 1000, te.MIN_PLAUSIBLE_CRT - 1])
def test_a_crt_no_collection_could_have_is_unavailable(conn, crt):
    """A ``crt`` below :data:`te.MIN_PLAUSIBLE_CRT` is corruption, not a date.

    Both directions are dangerous. A positive-but-tiny value dates day zero to
    the 1970s, which puts the day index tens of thousands of days high and reports
    every card in the collection as due — the loudest possible wrong answer. A
    non-positive one cannot even be converted to local time on Windows. Neither is
    a number to compute with, so the mirror is reported as unable to answer.
    """
    seed_mirror(conn, crt=crt)
    result = te.anki_due_count(conn, now=NOW, zone=TOKYO)

    assert result["available"] is False
    assert result["count"] is None
    assert result["collection_day"] is None
    assert "col.crt" in result["reason"]
    # The reason names the expectation, not the rejected value: a corrupt crt is
    # mirrored data, and this string is printed into the vault page.
    assert not any(char.isdigit() for char in result["reason"])


def test_a_pre_epoch_crt_is_screened_before_the_conversion_that_would_crash(conn):
    """Unscreened, a pre-epoch ``crt`` costs the learner the whole due block.

    The raiser is the *first* conversion, ``datetime.fromtimestamp(crt,
    tz=timezone.utc)``, which on Windows raises ``OSError [Errno 22]`` for a
    pre-1970 instant. That is epoch-seconds-to-UTC arithmetic, before any local
    zone is applied, so it fails identically whether ``zone`` is pinned or left to
    the platform — ``astimezone()`` is never reached and pinning a zone would not
    avoid it. Hence the zone is simply left at its default here: it is irrelevant
    to this path, not the trigger for it.

    Unscreened, that ``OSError`` escapes :func:`te.anki_due_count`, is caught by
    the ``render_sections`` backstop, and the due block degrades to a generic
    "(failed)" heading that says nothing about what is actually wrong with the
    mirror.
    """
    seed_mirror(conn, crt=-86400)

    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is False
    assert "col.crt" in result["reason"]

    text = te.render_today(te.build_context(conn, today=DAY, now=NOW))
    assert "## Anki reviews" in text
    assert "(failed)" not in text
    body = section_text(text, "Anki reviews")
    assert "unavailable" in body.lower()


@pytest.mark.parametrize(
    "crt",
    [
        CRT * 1000,          # col.crt read out of a milliseconds column
        1700000000000,       # the same mistake, spelled out
        NOW_EPOCH + 400 * 86400,  # representable, but over a year from now
        NOW_EPOCH + 2 * 86400,    # only just past the clock slack
    ],
    ids=["ms_fixture", "ms_literal", "far_future", "just_past_slack"],
)
def test_a_crt_later_than_now_is_unavailable_not_a_crash(conn, crt):
    """The upper bound. A collection cannot have been created in the future.

    The lower bound alone left two live failures. A ``crt`` stored in
    **milliseconds** is ~1000x too large and is not a representable year at all, so
    ``datetime.fromtimestamp`` raises ``OSError [Errno 22]`` on Windows — which
    escaped the counter, hit the ``render_sections`` backstop, and degraded the
    block to the generic "(failed)" heading the guard exists to prevent. A
    far-future but representable ``crt`` fails silently instead: the day index goes
    negative, nothing compares as due, and the page says the learner owes nothing
    on a collection full of overdue reviews. Both must read as "the mirror cannot
    answer".
    """
    seed_mirror(conn, crt=crt)
    result = te.anki_due_count(conn, now=NOW, zone=TOKYO)

    assert result["available"] is False
    assert result["count"] is None
    assert result["collection_day"] is None
    assert "col.crt" in result["reason"]
    # Same rule as the lower bound: the reason names the expectation, never the
    # rejected value. This string is printed into the vault page.
    assert not any(char.isdigit() for char in result["reason"])

    text = render(conn)
    assert "## Anki reviews" in text
    assert "(failed)" not in text
    body = section_text(text, "Anki reviews")
    assert "unavailable" in body.lower()
    assert "Nothing due right now" not in body


def test_a_crt_inside_the_clock_slack_is_still_believed(conn):
    """The slack is not decoration: it is what keeps a *real* collection working.

    A machine whose clock was corrected backwards after Anki stamped ``crt``, or a
    mirror carried from a machine whose clock runs slightly ahead, presents a
    genuine collection with a ``crt`` a little past now. Bounding at exactly ``now``
    would report that collection as corrupt. Inside
    :data:`te.MAX_CRT_FUTURE_SLACK_SECONDS` it must still count.
    """
    seed_mirror(conn, crt=NOW_EPOCH + 3600)
    result = te.anki_due_count(conn, now=NOW, zone=TOKYO)

    assert result["available"] is True
    # 21:00 JST on the collection's own creation date: day zero, past the rollover.
    assert result["collection_day"] == 0
    assert result["reason"] is None


def test_an_unconvertible_crt_that_slips_the_bounds_still_reads_as_unavailable(
    conn, monkeypatch
):
    """The belt-and-braces ``except`` around the conversion, on its own.

    The two bounds are what *should* stop an impossible ``crt``. This asserts the
    second line of defence behind them: with the upper bound widened so a
    milliseconds-valued ``crt`` walks straight past it,
    ``datetime.fromtimestamp(crt, tz=timezone.utc)`` raises — ``OSError [Errno 22]``
    on Windows, ``OverflowError``/``ValueError`` elsewhere for the same input — and
    the block must still degrade to a sentence about the mirror rather than to the
    generic "(failed)" heading. Widening the bound is how a future edit could
    reintroduce the crash; the ``except`` is why it would not cost the learner the
    whole section.
    """
    monkeypatch.setattr(te, "MAX_CRT_FUTURE_SLACK_SECONDS", 10**15)
    seed_mirror(conn, crt=1700000000000)

    result = te.anki_due_count(conn, now=NOW, zone=TOKYO)
    assert result["available"] is False
    assert result["count"] is None
    assert result["collection_day"] is None
    assert "col.crt" in result["reason"]
    assert not any(char.isdigit() for char in result["reason"])

    text = render(conn)
    assert "## Anki reviews" in text
    assert "(failed)" not in text
    assert "unavailable" in section_text(text, "Anki reviews").lower()


@pytest.mark.parametrize("hour", [-1, 24, 25, 100])
def test_an_impossible_rollover_hour_is_refused_without_echoing_it(hour):
    """The range guard on ``rollover_hour``, and what its message may contain.

    An hour outside 0-23 is a caller mistake, and a silent modulo or clamp would
    turn it into a day index that is quietly one off — the exact failure mode the
    rollover arithmetic exists to prevent. The refusal names the expected range
    and stops there: the rejected number is not echoed into a message that is
    printed and logged.
    """
    with pytest.raises(te.TodayExportError) as exc:
        te.collection_day_index(CRT, NOW, rollover_hour=hour, zone=TOKYO)

    message = str(exc.value)
    assert "0-23" in message
    assert str(hour) not in message


def test_anki_due_count_refuses_an_impossible_rollover_hour(conn):
    """The guard is not bypassed by going in through the public counter."""
    seed_mirror(conn)
    with pytest.raises(te.TodayExportError, match="0-23"):
        te.anki_due_count(conn, now=NOW, zone=TOKYO, rollover_hour=24)


def test_no_snapshot_at_all_is_reported_as_such(conn):
    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is False
    assert "never" in result["reason"] or "no snapshot" in result["reason"].lower()


def test_an_empty_mirror_is_never_a_confident_zero(conn):
    """A wiped ``anki_cards`` beside a crt-carrying ``mirror_meta`` is what a
    failed rebuild leaves behind, and "0 due" would send the learner away from
    Anki on a day they owe reviews. It must state unavailability instead."""
    seed_mirror(conn, [])
    result = te.anki_due_count(conn, now=NOW)
    assert result["available"] is False
    assert result["count"] is None
    assert result["cards_mirrored"] == 0
    assert "no cards" in result["reason"]

    text = render(conn)
    assert "Nothing due right now" not in text
    assert "unavailable" in text.lower()


def test_due_section_carries_snapshot_freshness(conn):
    seed_mirror(conn)
    text = render(conn)
    # The rendered phrase, not a bare "4": a loose digit match is satisfied by a
    # trend delta, a morph interval, or the collection day index.
    body = section_text(text, "Anki reviews")
    assert f"{EXPECTED_DUE} cards due." in body
    assert "2 review, 2 learning." in body
    assert "2026-08-19T06:00:00Z" in body


def test_the_due_section_says_the_count_is_scheduler_raw(conn):
    """The caveat that keeps the number honest against Anki's own deck list.

    Per-deck daily limits (``perDay``) live in the deck-config JSON the snapshot
    does not mirror, so this count is every card whose scheduled day has arrived
    while Anki may offer a capped subset today — 300 here against 100 there on a
    backlog. The page has to say which of the two it is; a learner comparing the
    two screens and finding them different would otherwise have no way to tell a
    documented difference from a broken mirror.
    """
    seed_mirror(conn)
    body = section_text(render(conn), "Anki reviews")
    assert "Scheduler-raw" in body
    assert "not mirrored" in body
    assert "deck list" in body


# ---------------------------------------------------------------------------
# Streak, known trend, morphs, resume — each must degrade gracefully
# ---------------------------------------------------------------------------


def test_streak_section_counts_consecutive_study_days(conn):
    for offset in range(5):
        seed_event(conn, day=DAY - timedelta(days=offset), type="review")

    text = render(conn)
    assert "5 day" in text


def test_streak_section_on_an_empty_log(conn):
    text = render(conn)
    assert "## Streak" in text
    assert "no streak" in text.lower()


def test_known_trend_reports_the_current_total_and_a_delta(conn):
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?,?,?)",
        ("w-1", "known", "2026-08-18T00:00:00Z"),
    )
    seed_event(
        conn, day=DAY - timedelta(days=10), type="anki_sync",
        payload={"known_total": 900},
    )
    seed_event(
        conn, day=DAY - timedelta(days=40), type="anki_sync",
        payload={"known_total": 800},
    )
    text = render(conn)
    assert "## Known set" in text
    assert "1 known of 1 tracked" in text
    # The 30-day window reaches the total logged ten days ago and names the day
    # it came from; the 7-day window reaches nothing, so it claims nothing. The
    # 40-day-old total is out of both windows and may not become a baseline.
    assert "in the last 30 days" in text
    assert "in the last 7 days" not in text
    assert f"against 900 logged {(DAY - timedelta(days=10)).isoformat()}" in text
    assert "800" not in text


def test_known_trend_without_a_baseline_says_so(conn):
    text = render(conn)
    assert "## Known set" in text
    assert "no baseline" in text.lower()


def test_weakest_morphs_tolerates_a_missing_table(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name = 'ankimorphs_morphs'"
    ).fetchone()[0] == 0
    text = render(conn)
    assert "## Weakest morphs" in text
    assert "ankimorphs" in text.lower()


def test_weakest_morphs_lists_the_lowest_intervals(conn):
    ankimorphs_ingest.create_table(conn)
    conn.executemany(
        "INSERT INTO ankimorphs_morphs"
        "(lemma, inflection, lemma_ivl, inflection_ivl, source, imported_ts) "
        "VALUES (?, ?, ?, ?, 'db', '2026-08-19T00:00:00Z')",
        [
            ("食べる", "食べて", 1, 1),
            ("走る", "走った", 60, 60),
            ("恐れる", "恐れて", 3, 3),
            ("曖昧", "曖昧", None, None),
        ],
    )
    text = render(conn)
    assert "食べる" in text
    assert "恐れる" in text
    assert "走る" not in text          # 60 days is not weak
    assert "曖昧" not in text          # no interval is not evidence of weakness


def test_resume_section_without_any_media(conn):
    text = render(conn)
    assert "## Resume" in text
    # Against the Resume block itself: "no baseline" under Known set and "No
    # streak yet" under Streak would otherwise keep this green with the whole
    # Resume section deleted.
    body = section_text(text, "Resume")
    assert "No resume pointer" in body
    assert "nothing has reported a playback position" in body


def test_resume_section_names_the_last_heartbeat(conn):
    conn.execute(
        "INSERT INTO media (id, kind, title, added_ts) VALUES (?,?,?,?)",
        ("m-1", "anime", "よつばと", "2026-08-18T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO media_heartbeat (id, media_id, anchor_ms, displayed_text, "
        "updated_ts) VALUES (1, 'm-1', 754000, ?, ?)",
        ("そこに座って", "2026-08-19T11:30:00Z"),
    )
    text = render(conn)
    assert "よつばと" in text
    assert "12:34" in text  # 754000 ms
    assert "2026-08-19T11:30:00Z" in text


def test_every_section_survives_a_dropped_mirror(conn):
    """A derived table can be absent mid-rebuild; the page must still render."""
    conn.execute("DROP TABLE anki_cards")
    conn.execute("DROP TABLE mirror_meta")
    text = render(conn)
    assert "## Anki reviews" in text
    assert "## Resume" in text


# ---------------------------------------------------------------------------
# Frontmatter and the generated-file header
# ---------------------------------------------------------------------------


def test_frontmatter_carries_the_generated_header(conn):
    text = render(conn)
    head = text.splitlines()

    assert head[0] == "---"
    block = head[: head.index("---", 1)]
    assert "schema: 2" in block
    assert "type: derived" in block
    assert "day: 2026-08-19" in block
    assert "generated: true" in block
    assert any(line.startswith("generated_at: ") for line in block)
    assert te.is_generated_note(text)


def test_generated_at_is_iso_utc_to_whole_seconds(conn):
    text = render(conn)
    stamp = next(
        line.split(": ", 1)[1]
        for line in text.splitlines()
        if line.startswith("generated_at: ")
    )
    assert stamp == "2026-08-19T12:00:00Z"
    assert datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


def test_generated_in_the_body_is_not_a_header(conn):
    body = "# Today\n\ngenerated: true\n\nMine.\n"
    assert not te.is_generated_note(body)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_write_today_writes_into_derived_and_logs(conn, vault):
    seed_mirror(conn)
    path = te.write_today(conn, vault, today=DAY, now=NOW)

    assert path == vault / ".derived" / "Today.md"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\nschema: 2\n")
    assert te.is_generated_note(body)

    logged = events.recent_events(conn, limit=5, type=te.TODAY_EVENT_TYPE)
    assert len(logged) == 1
    payload = json.loads(logged[0]["payload"])
    assert payload["day"] == "2026-08-19"
    assert payload["path"] == "Today.md"


def test_write_today_creates_the_derived_dir(conn, vault):
    assert not (vault / ".derived").exists()
    te.write_today(conn, vault, today=DAY, now=NOW)
    assert (vault / ".derived").is_dir()


def test_write_today_replaces_its_own_output(conn, vault):
    # zone=TOKYO for the same reason ``render`` pins it: the due phrase asserted
    # below is only 4 when the rollover is resolved in the fixture's own zone.
    first = te.write_today(conn, vault, today=DAY, now=NOW, zone=TOKYO)
    seed_mirror(conn)
    second = te.write_today(conn, vault, today=DAY, now=NOW, zone=TOKYO)

    assert second == first
    body = second.read_text(encoding="utf-8")
    # The seeded mirror reached the file: the due phrase itself, not any stray 4.
    assert f"{EXPECTED_DUE} cards due." in section_text(body, "Anki reviews")
    assert len(events.recent_events(conn, limit=5, type=te.TODAY_EVENT_TYPE)) == 2


def test_write_today_refuses_a_file_without_the_header(conn, vault):
    target = vault / ".derived" / "Today.md"
    target.parent.mkdir(parents=True)
    handwritten = "---\nschema: 2\ntype: derived\n---\n\nMy own notes.\n"
    target.write_text(handwritten, encoding="utf-8")

    with pytest.raises(te.TodayExportError, match="generated") as exc:
        te.write_today(conn, vault, today=DAY, now=NOW)

    # The refusal locates the file the way derived_target does: vault-relative,
    # never the resolved absolute path, which carries the 'vault_path' value.
    message = str(exc.value)
    assert ".derived/Today.md" in message
    assert str(vault) not in message and vault.as_posix() not in message

    assert target.read_text(encoding="utf-8") == handwritten
    assert events.recent_events(conn, limit=5, type=te.TODAY_EVENT_TYPE) == []


def test_write_today_refuses_an_unreadable_file(conn, vault, monkeypatch):
    target = vault / ".derived" / "Today.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\ngenerated: true\n---\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("no read for you")

    monkeypatch.setattr(te.Path, "read_text", boom)
    with pytest.raises(te.TodayExportError) as exc:
        te.write_today(conn, vault, today=DAY, now=NOW)

    message = str(exc.value)
    assert "no read for you" in message, "the reason the read failed is the point"
    assert str(vault) not in message and vault.as_posix() not in message


def test_an_os_failure_detail_never_carries_the_path_it_failed_on(vault):
    """``_os_detail``: the diagnosis without the filename the platform appends.

    A real ``PermissionError`` stringifies as ``[Errno 13] Permission denied:
    '<absolute path>'``, so interpolating ``exc`` is the other way the vault root
    reaches a message. ``strerror`` says the same thing about *why* without saying
    *where*, and an OSError raised without one has no path in it to lose.
    """
    platform_shaped = PermissionError(13, "Permission denied", str(vault / "x.md"))
    detail = te._os_detail(platform_shaped)
    assert detail == "Permission denied"
    assert str(vault) not in detail

    assert te._os_detail(OSError("no read for you")) == "no read for you"


@pytest.mark.parametrize("failing_step", ["replace", "fsync"])
def test_a_failed_write_leaves_the_previous_page_and_no_litter(
    conn, vault, monkeypatch, failing_step
):
    """The reason the write goes through a temp file plus :func:`os.replace`.

    Writing in place would truncate the old page first, so a failure anywhere
    between that truncation and the last byte leaves a half-written ``Today.md``
    — and one with no frontmatter is a file the refusal rule then reads as
    hand-written, locking the learner out of every future export until they
    delete it by hand. The safety rule must not be able to trigger on Katagiri's
    own half-finished output. So: the old page survives *byte for byte, header
    included*, and the scratch file is not left behind in ``.derived`` where the
    next run has no way to recognise it as its own.

    The second render is deliberately for a *different* day, so "unchanged" is a
    real assertion rather than one that an in-place write would satisfy by
    rewriting identical bytes.
    """
    target = te.write_today(conn, vault, today=DAY, now=NOW)
    before = target.read_text(encoding="utf-8")
    assert te.is_generated_note(before)

    def boom(*args, **kwargs):
        raise OSError("the disk gave up mid-write")

    tomorrow = DAY + timedelta(days=1)
    monkeypatch.setattr(te.os, failing_step, boom)
    with pytest.raises(te.TodayExportError) as exc:
        te.write_today(
            conn, vault, today=tomorrow, now=NOW + timedelta(days=1)
        )
    assert str(vault) not in str(exc.value), "a write failure names the key, not the root"

    after = target.read_text(encoding="utf-8")
    assert after == before
    assert tomorrow.isoformat() not in after
    # Stated as its own assertion: the header is what keeps the next export legal.
    assert te.is_generated_note(after)
    assert sorted(p.name for p in (vault / ".derived").iterdir()) == ["Today.md"]
    assert list((vault / ".derived").glob("*.tmp")) == []
    # A write that did not land must not be logged as an export.
    assert len(events.recent_events(conn, limit=5, type=te.TODAY_EVENT_TYPE)) == 1


def test_write_today_without_a_vault_needs_config(conn):
    with pytest.raises(config_mod.ConfigError, match="vault_path"):
        te.write_today(conn, today=DAY, now=NOW)


# ---------------------------------------------------------------------------
# Confinement to .derived/
# ---------------------------------------------------------------------------


def test_derived_dir_is_under_the_vault_root(vault):
    assert te.derived_dir(vault) == vault / ".derived"


@pytest.mark.parametrize(
    "name",
    ["../escape.md", "..\\escape.md", "sub/../../escape.md", "/abs.md", "C:/abs.md"],
)
def test_a_name_escaping_derived_is_refused(vault, name):
    with pytest.raises(te.TodayExportError):
        te.derived_target(name, vault)


def test_a_plain_name_is_accepted(vault):
    assert te.derived_target("Today.md", vault) == vault / ".derived" / "Today.md"


def test_the_refusal_names_the_directory_and_neither_input_nor_vault_root(vault):
    """What a refusal is allowed to contain: the check, and ``.derived``.

    Two separate things are kept out of it. The caller's string, because an error
    message is not a place to echo input back into something that gets printed and
    logged. And the vault root — because unless a ``vault_path`` was passed
    explicitly it *is* the ``vault_path`` config value, and ``katagiri.config``
    declares that config values are never logged. The message names the key
    instead, which is the actionable half anyway: the operator knows their own
    vault root and cannot act on being shown it.
    """
    with pytest.raises(te.TodayExportError) as exc:
        te.derived_target("../escape.md", vault)
    message = str(exc.value)

    assert ".derived" in message
    assert "escape.md" not in message
    assert str(vault) not in message and vault.as_posix() not in message
    assert "vault_path" in message, "the key is what the operator can act on"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_without_writing(conn, vault, capsys):
    conn.close()
    code = te.main(["--dry-run", "--vault", str(vault)])
    out = capsys.readouterr().out

    assert code == 0
    assert "generated: true" in out
    assert not (vault / ".derived").exists()


def test_cli_writes_into_the_given_vault(conn, vault, capsys):
    conn.close()
    code = te.main(["--vault", str(vault)])

    assert code == 0
    assert "wrote " in capsys.readouterr().out
    assert (vault / ".derived" / "Today.md").is_file()


def test_cli_refuses_a_bad_day_without_echoing_what_was_typed(
    local_app_data, vault, capsys
):
    """``--day`` is refused by naming the expected shape, not the rejected value.

    ``strptime``'s own ``ValueError`` quotes the argument verbatim, and that string
    is printed, written to logs, and pasted into bug reports. The reader already
    has what they typed on the line above; a filename, a path or a pasted token
    that landed in the wrong argument must not be copied into Katagiri's output.
    """
    code = te.main(["--day", "garbage", "--vault", str(vault)])
    out = capsys.readouterr().out

    assert code == 2
    assert "error:" in out
    assert "YYYY-MM-DD" in out
    assert "garbage" not in out
    assert not (vault / ".derived").exists()


def test_cli_reports_a_refused_overwrite(conn, vault, capsys):
    target = vault / ".derived" / "Today.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nschema: 2\n---\n\nmine\n", encoding="utf-8")
    conn.close()

    assert te.main(["--vault", str(vault)]) == 1
    assert "error:" in capsys.readouterr().out
