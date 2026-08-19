"""Sensei-letter tests: week stats, rendering, and the no-clobber write rule."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from katagiri import config as config_mod
from katagiri import db, events
from katagiri import sensei_letter as sl

WEEK = (2026, 34)


@pytest.fixture(autouse=True)
def _stub_setup_logging(monkeypatch):
    """Never let ``sl.main`` install a real handler bound to captured stderr.

    ``sensei_letter.main`` imports ``setup_logging`` locally from
    ``katagiri.logging_setup`` on every call, so the stub must be applied at
    that source rather than on the ``sl`` module. ``setup_logging`` is
    idempotent and caches its handler on first call; under pytest capture that
    handler would bind to a temp capture file and leak into later tests in the
    session (see test_mcp_tools.py's identical hazard note).
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


def seed(
    conn: sqlite3.Connection,
    *,
    day: date | str,
    type: str,
    payload: dict | None = None,
    item_id: str | None = None,
    session: str = "seed",
) -> None:
    """Insert one event with an exact ``day_key``.

    Written straight into the table rather than through :func:`append_event`
    because ``day_key`` there is derived from the machine's local zone, and these
    assertions are about specific calendar days. INSERT is allowed by the
    append-only triggers; only UPDATE and DELETE abort.
    """
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
            session,
            type,
            item_id,
            None if payload is None else json.dumps(payload, sort_keys=True),
        ),
    )


def week_days() -> list[date]:
    start, _ = sl.week_bounds(*WEEK)
    return [start + timedelta(days=offset) for offset in range(7)]


def stats_for(conn: sqlite3.Connection) -> sl.WeekStats:
    return sl.compute_week_stats(conn, *WEEK)


# ---------------------------------------------------------------------------
# Week arithmetic
# ---------------------------------------------------------------------------


def test_week_bounds_are_monday_to_sunday():
    start, end = sl.week_bounds(*WEEK)
    assert start.isoweekday() == 1
    assert end.isoweekday() == 7
    assert (end - start).days == 6


def test_week_label_and_parse_round_trip():
    assert sl.week_label(2026, 34) == "2026-W34"
    assert sl.week_label(2026, 4) == "2026-W04"
    assert sl.parse_week_label("2026-W34") == WEEK
    assert sl.parse_week_label("2026-w04") == (2026, 4)


@pytest.mark.parametrize("bad", ["2026-34", "W34", "2026-W99", "", "last week"])
def test_parse_week_label_rejects_nonsense(bad):
    with pytest.raises(sl.SenseiLetterError):
        sl.parse_week_label(bad)


def test_current_week_follows_the_given_date():
    assert sl.current_week(date(2026, 8, 19)) == WEEK


def test_half_a_week_is_refused(conn):
    with pytest.raises(sl.SenseiLetterError):
        sl.compute_week_stats(conn, 2026, None)


def test_no_week_given_uses_the_date(conn):
    stats = sl.compute_week_stats(conn, today=date(2026, 8, 19))
    assert (stats.iso_year, stats.iso_week) == WEEK
    assert stats.label == "2026-W34"


# ---------------------------------------------------------------------------
# study_days: the ten-minute threshold and the artifact escape hatch
# ---------------------------------------------------------------------------


def test_study_days_apply_the_ten_minute_rule(conn):
    days = week_days()
    seed(conn, day=days[0], type="study_session", payload={"minutes": 9})
    seed(conn, day=days[1], type="study_session", payload={"minutes": 10})
    seed(conn, day=days[2], type="study_session", payload={"minutes": 5})
    seed(conn, day=days[2], type="study_session", payload={"minutes": 6})
    seed(conn, day=days[3], type="study_session", payload={"minutes": "45"})
    seed(conn, day=days[4], type="study_session", payload={"minutes": "an hour"})

    stats = stats_for(conn)
    # Mon 9m below the gate, Fri unreadable; Tue, Wed (5+6) and Thu count.
    assert stats.study_days == 3
    assert stats.minutes_total == 75


def test_an_artifact_alone_makes_a_study_day(conn):
    days = week_days()
    seed(conn, day=days[0], type="mark_known", item_id="w-1")
    seed(conn, day=days[1], type="review")
    seed(conn, day=days[2], type="lesson_close")
    seed(conn, day=days[3], type="regen_yomitan")  # not a study artifact

    stats = stats_for(conn)
    assert stats.study_days == 3
    assert stats.minutes_total == 0


def test_days_outside_the_week_do_not_count(conn):
    start, end = sl.week_bounds(*WEEK)
    seed(conn, day=start - timedelta(days=1), type="review")
    seed(conn, day=end + timedelta(days=1), type="review")
    seed(conn, day=start, type="review")

    stats = stats_for(conn)
    assert stats.study_days == 1
    assert stats.reviews_total == 1


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


def test_batched_and_individual_reviews_sum(conn):
    days = week_days()
    seed(conn, day=days[0], type="review_batch", payload={"reviews": 12})
    seed(conn, day=days[1], type="review_batch", payload={"reviews": "8"})
    seed(conn, day=days[2], type="review_batch", payload={"reviews": ["a", "b", "c"]})
    seed(conn, day=days[3], type="review")
    seed(conn, day=days[3], type="review")

    stats = stats_for(conn)
    assert stats.reviews_batched == 23
    assert stats.reviews_individual == 2
    assert stats.reviews_total == 25


def test_a_batch_with_an_unreadable_count_still_counts_once(conn):
    days = week_days()
    seed(conn, day=days[0], type="review_batch", payload={"reviews": "lots"})
    seed(conn, day=days[1], type="review_batch", payload=None)
    seed(conn, day=days[2], type="review_batch", payload={"reviews": -3})

    assert stats_for(conn).reviews_total == 3


# ---------------------------------------------------------------------------
# New known words, mining, minutes
# ---------------------------------------------------------------------------


def test_new_known_words_counts_distinct_items(conn):
    days = week_days()
    seed(conn, day=days[0], type="mark_known", item_id="w-1")
    seed(conn, day=days[1], type="mark_known", item_id="w-2")
    seed(conn, day=days[2], type="mark_known", item_id="w-2")  # same word again
    seed(conn, day=days[3], type="mark_known")  # no item id
    seed(conn, day=days[4], type="mark_unknown", item_id="w-9")

    stats = stats_for(conn)
    assert stats.new_known_words == 3
    assert stats.known_delta is None


def test_a_logged_known_total_delta_raises_the_count(conn):
    start = week_days()[0]
    seed(conn, day=start - timedelta(days=3), type="anki_sync", payload={"known_total": 100})
    seed(conn, day=start, type="mark_known", item_id="w-1")
    seed(conn, day=start + timedelta(days=4), type="anki_sync", payload={"known_total": 112})

    stats = stats_for(conn)
    assert stats.known_delta == 12
    assert stats.new_known_words == 12  # marks alone would have said 1


def test_items_mined_from_sessions_and_mining_events(conn):
    days = week_days()
    seed(
        conn,
        day=days[0],
        type="study_session",
        payload={"minutes": 30, "items_mined": 4, "activities": ["anime"]},
    )
    seed(conn, day=days[1], type="study_session", payload={"minutes": 20, "items_mined": None})
    seed(conn, day=days[2], type="mining", payload={"items_mined": 3})
    seed(conn, day=days[3], type="mining", payload=None)  # happened, count unknown

    stats = stats_for(conn)
    assert stats.items_mined == 8
    assert stats.minutes_total == 50
    assert stats.hours_label == "50m"


def test_hours_label_splits_at_an_hour(conn):
    seed(conn, day=week_days()[0], type="study_session", payload={"minutes": 125})
    assert stats_for(conn).hours_label == "2h 05m"


# ---------------------------------------------------------------------------
# Streak boundaries
# ---------------------------------------------------------------------------


def test_streak_ends_at_the_last_study_day_of_the_week(conn):
    days = week_days()
    for day in days[:6]:  # Mon..Sat, quiet Sunday
        seed(conn, day=day, type="review")

    stats = stats_for(conn)
    assert stats.study_days == 6
    assert stats.streak == 6
    assert stats.streak_through == days[5]


def test_streak_reaches_back_before_the_week(conn):
    days = week_days()
    for offset in (2, 1):
        seed(conn, day=days[0] - timedelta(days=offset), type="review")
    for day in days:
        seed(conn, day=day, type="review")

    stats = stats_for(conn)
    assert stats.study_days == 7
    assert stats.streak == 9
    assert stats.streak_through == days[6]


def test_a_gap_breaks_the_streak_but_not_the_week(conn):
    days = week_days()
    for day in (days[0], days[1], days[3], days[4], days[5], days[6]):
        seed(conn, day=day, type="review")

    stats = stats_for(conn)
    assert stats.study_days == 6
    assert stats.streak == 4  # Thu..Sun only
    assert stats.streak_through == days[6]


def test_an_empty_log_has_no_streak(conn):
    stats = stats_for(conn)
    assert stats.streak == 0
    assert stats.streak_through is None
    assert stats.is_quiet


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_letter_frontmatter_matches_the_vault_contract(conn):
    seed(conn, day=week_days()[0], type="review")
    text = sl.render_letter(stats_for(conn))
    head = text.splitlines()

    assert head[0] == "---"
    assert "schema: 2" in head[:7]
    assert "type: progress" in head[:7]
    assert "week: 2026-W34" in head[:7]
    assert "generated: true" in head[:7]
    assert head[6] == "---"
    assert 'title: "Week 34, 2026 — sensei letter"' in head[:7]
    assert sl.is_generated_letter(text)


def test_letter_carries_the_numbers(conn):
    days = week_days()
    for day in days[:5]:
        seed(conn, day=day, type="study_session", payload={"minutes": 30, "items_mined": 2})
    seed(conn, day=days[0], type="review_batch", payload={"reviews": 40})
    seed(conn, day=days[1], type="review")
    seed(conn, day=days[2], type="mark_known", item_id="w-1")

    stats = stats_for(conn)
    text = sl.render_letter(stats)

    assert "| Study days | 5 of 7 |" in text
    assert "| Reviews | 41 |" in text
    assert "| New known words | 1 |" in text
    assert "| Items mined | 10 |" in text
    assert "| Time studied | 2h 30m |" in text
    assert "| Streak | 5 day(s), through " in text
    assert "# Week 34, 2026 — sensei letter" in text
    assert "None" not in text


def test_rendering_is_deterministic(conn):
    days = week_days()
    seed(conn, day=days[0], type="review_batch", payload={"reviews": 7})
    seed(conn, day=days[1], type="study_session", payload={"minutes": 20})

    first = sl.render_letter(stats_for(conn))
    second = sl.render_letter(sl.compute_week_stats(conn, *WEEK))
    assert first == second


def test_a_quiet_week_still_renders(conn):
    stats = stats_for(conn)
    text = sl.render_letter(stats)

    assert stats.is_quiet
    assert "quiet week" in text
    assert "| Study days | 0 of 7 |" in text
    assert "| Streak | — |" in text
    assert sl.is_generated_letter(text)


def test_known_delta_row_appears_only_when_computable(conn):
    start = week_days()[0]
    seed(conn, day=start - timedelta(days=1), type="anki_sync", payload={"known_total": 50})
    seed(conn, day=start, type="anki_sync", payload={"known_total": 57})

    text = sl.render_letter(stats_for(conn))
    assert "| Known-set change | +7 |" in text


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_write_letter_writes_and_logs(conn, vault):
    seed(conn, day=week_days()[0], type="review_batch", payload={"reviews": 11})

    path = sl.write_letter(conn, vault, iso_year=2026, iso_week=34)

    assert path == vault / "80-progress" / "2026-W34-sensei-letter.md"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\nschema: 2\n")
    assert "| Reviews | 11 |" in body

    logged = events.recent_events(conn, limit=5, type=sl.LETTER_EVENT_TYPE)
    assert len(logged) == 1
    payload = json.loads(logged[0]["payload"])
    assert payload == {"week": "2026-W34", "path": "2026-W34-sensei-letter.md"}


def test_write_letter_creates_the_progress_dir(conn, vault):
    assert not (vault / "80-progress").exists()
    sl.write_letter(conn, vault, iso_year=2026, iso_week=34)
    assert (vault / "80-progress").is_dir()


def test_write_letter_refuses_a_hand_written_note(conn, vault):
    target = vault / "80-progress" / "2026-W34-sensei-letter.md"
    target.parent.mkdir(parents=True)
    handwritten = "---\nschema: 2\ntype: progress\nweek: 2026-W34\n---\n\nMy own notes.\n"
    target.write_text(handwritten, encoding="utf-8")

    with pytest.raises(sl.SenseiLetterError, match="hand-written"):
        sl.write_letter(conn, vault, iso_year=2026, iso_week=34)

    assert target.read_text(encoding="utf-8") == handwritten
    assert events.recent_events(conn, limit=5, type=sl.LETTER_EVENT_TYPE) == []


def test_generated_in_the_body_is_not_permission(conn, vault):
    target = vault / "80-progress" / "2026-W34-sensei-letter.md"
    target.parent.mkdir(parents=True)
    sneaky = (
        "---\nschema: 2\ntype: progress\n---\n\n"
        "generated: true\n\nStill hand-written.\n"
    )
    target.write_text(sneaky, encoding="utf-8")

    assert not sl.is_generated_letter(sneaky)
    with pytest.raises(sl.SenseiLetterError):
        sl.write_letter(conn, vault, iso_year=2026, iso_week=34)
    assert target.read_text(encoding="utf-8") == sneaky


def test_write_letter_replaces_its_own_output(conn, vault):
    first = sl.write_letter(conn, vault, iso_year=2026, iso_week=34)
    assert "| Reviews | 0 |" in first.read_text(encoding="utf-8")

    seed(conn, day=week_days()[0], type="review_batch", payload={"reviews": 9})
    second = sl.write_letter(conn, vault, iso_year=2026, iso_week=34)

    assert second == first
    assert "| Reviews | 9 |" in second.read_text(encoding="utf-8")
    assert len(events.recent_events(conn, limit=5, type=sl.LETTER_EVENT_TYPE)) == 2


def test_write_letter_without_a_vault_needs_config(conn):
    with pytest.raises(config_mod.ConfigError, match="vault_path"):
        sl.write_letter(conn, iso_year=2026, iso_week=34)


def test_letter_filename_accepts_label_or_stats(conn):
    assert sl.letter_filename("2026-W34") == "2026-W34-sensei-letter.md"
    assert sl.letter_filename(stats_for(conn)) == "2026-W34-sensei-letter.md"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_without_writing(conn, vault, capsys):
    seed(conn, day=week_days()[0], type="review_batch", payload={"reviews": 5})
    conn.close()

    code = sl.main(["--week", "2026-W34", "--dry-run"])
    out = capsys.readouterr().out

    assert code == 0
    assert "week: 2026-W34" in out
    assert "| Reviews | 5 |" in out
    assert not (vault / "80-progress").exists()


def test_cli_writes_into_the_given_vault(conn, vault, capsys):
    conn.close()
    code = sl.main(["--week", "2026-W34", "--vault", str(vault)])
    out = capsys.readouterr().out

    assert code == 0
    assert "wrote " in out
    assert (vault / "80-progress" / "2026-W34-sensei-letter.md").is_file()


def test_cli_current_is_the_default_week(conn, vault, capsys):
    conn.close()
    assert sl.main(["current", "--vault", str(vault)]) == 0
    year, week = sl.current_week()
    expected = vault / "80-progress" / sl.letter_filename(sl.week_label(year, week))
    assert expected.is_file()
    assert "wrote " in capsys.readouterr().out


def test_cli_rejects_a_bad_week(conn, vault, capsys):
    conn.close()
    assert sl.main(["--week", "not-a-week", "--vault", str(vault)]) == 2
    assert "error:" in capsys.readouterr().out


def test_cli_reports_a_refused_overwrite(conn, vault, capsys):
    target = vault / "80-progress" / "2026-W34-sensei-letter.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nschema: 2\ntype: progress\n---\n\nmine\n", encoding="utf-8")
    conn.close()

    assert sl.main(["--week", "2026-W34", "--vault", str(vault)]) == 1
    assert "error:" in capsys.readouterr().out
