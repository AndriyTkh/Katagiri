"""T021: the D6 stop gate's criteria — the three things it now actually gates on.

`tests/test_mcp_tools.py` already defends the day-count arithmetic and the
declared-pause exclusion through the tool surface. What is defended here is what
T021 added on top, and each of the three is a thing that used to be *reported*
rather than *enforced*:

1. the probe battery gates ``pass``. A fortnight of logged minutes with no scored
   performance behind it used to pass the gate while cheerfully reporting
   ``probe_battery_recorded: false``.
2. the artifact event-type set is a closed, named list rather than whatever a set
   literal happens to hold, so "at least one logged artifact" cannot quietly
   change meaning. This file is where that list is pinned; `docs/db-schema.md`
   documents the same list in prose, and the two are meant to be read together.
3. every evaluation is persisted, and two consecutive persisted failures trigger
   the re-plan. Before T021 there was no verdict history for a trigger to read.

Event rows are seeded with direct INSERTs (the log's triggers block UPDATE and
DELETE, not INSERT) so that ``day_key`` is exactly what each test says it is.
Going through :func:`katagiri.events.append_event` would derive ``day_key`` from
the machine's own time zone, which would make the window arithmetic depend on
where the test runs. The gate's *own* events are the exception: those are written
by the code under test, on purpose.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import db as db_mod
from katagiri import events
from katagiri import stop_gate as sg

TODAY = "2026-08-19"
TS = "T12:00:00Z"

#: The documented artifact set (docs/db-schema.md, "Event types the D6 stop gate
#: counts"). Spelled out rather than derived from the module so that a change to
#: the module has to be a change here too — which is the only way a criterion
#: stays a criterion.
DOCUMENTED_ARTIFACT_TYPES = (
    "lesson_close",
    "mark_known",
    "mark_suspect",
    "mark_unknown",
    "mining",
    "review",
    "review_batch",
)


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A migrated database at a configured throwaway path."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    connection = db_mod.open_db()
    try:
        yield connection
    finally:
        connection.close()
        config_mod.reset_config_cache()


def seed_event(
    connection: sqlite3.Connection,
    *,
    day: str,
    type: str,
    payload: Any = None,
    item_id: str | None = None,
    session_id: str = "test-session",
) -> str:
    event_id = events.new_ulid()
    connection.execute(
        """
        INSERT INTO event (id, ts_device, ts_server, tz, day_key, session_id,
                           type, item_id, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            f"{day}{TS}",
            f"{day}{TS}",
            "UTC",
            day,
            session_id,
            type,
            item_id,
            None if payload is None else json.dumps(payload, ensure_ascii=False),
        ),
    )
    return event_id


def seed_observation(
    connection: sqlite3.Connection,
    *,
    band: str,
    unassisted: bool,
    day: str = TODAY,
    task_type: str = "produce",
    rubric_version: str = "r1",
) -> str:
    observation_id = events.new_ulid()
    connection.execute(
        """
        INSERT INTO observation (id, ts, session_id, item_id, task_type,
                                 expected, produced, unassisted, coverage_band,
                                 rubric_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            f"{day}{TS}",
            "probe-session",
            "w-1",
            task_type,
            "食べる",
            "食べる",
            1 if unassisted else 0,
            band,
            rubric_version,
        ),
    )
    return observation_id


def days_back(count: int, *, end: str = TODAY, skip: set[str] | None = None) -> list[str]:
    """``count`` consecutive calendar days ending at ``end``, newest last."""
    last = date.fromisoformat(end)
    out: list[str] = []
    offset = 0
    while len(out) < count:
        day = (last - timedelta(days=offset)).isoformat()
        offset += 1
        if skip and day in skip:
            continue
        out.append(day)
    return sorted(out)


def study_days(connection: sqlite3.Connection, days: list[str], minutes: int = 30) -> None:
    for day in days:
        seed_event(
            connection, day=day, type=events.STUDY_LOG_TYPE, payload={"minutes": minutes}
        )


def seed_probe_battery(
    connection: sqlite3.Connection,
    *,
    bands: tuple[str, ...] = (">=95", "80-95"),
    unassisted: bool = True,
    day: str = "2026-08-12",
) -> None:
    """A probe battery event plus one observation in each of ``bands``."""
    seed_event(connection, day=day, type=sg.PROBE_EVENT_TYPE)
    for band in bands:
        seed_observation(connection, band=band, unassisted=unassisted, day=day)


def seed_passing_gate(connection: sqlite3.Connection) -> None:
    """Everything the gate asks for: 14 study days and a banded probe battery."""
    study_days(connection, days_back(14))
    seed_probe_battery(connection)


def gate_events(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every persisted gate evaluation, oldest first, payload decoded."""
    rows = connection.execute(
        "SELECT id, session_id, day_key, payload FROM event WHERE type = ? "
        "ORDER BY id",
        (sg.GATE_EVENT_TYPE,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry["payload"] = json.loads(row["payload"])
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# (b) The artifact event-type set is a closed, documented list
# ---------------------------------------------------------------------------


def test_the_artifact_event_type_set_is_exactly_what_is_documented():
    assert sorted(sg.ARTIFACT_EVENT_TYPES) == list(DOCUMENTED_ARTIFACT_TYPES)


def test_every_artifact_type_carries_the_reason_it_counts():
    assert sorted(sg.ARTIFACT_EVENT_REASONS) == list(DOCUMENTED_ARTIFACT_TYPES)
    assert sg.ARTIFACT_EVENT_TYPES == frozenset(sg.ARTIFACT_EVENT_REASONS), (
        "the set must be derived from the reasons, or the two can disagree"
    )
    for type_name, reason in sg.ARTIFACT_EVENT_REASONS.items():
        assert reason.strip(), f"{type_name} counts for no stated reason"


def test_the_types_that_deliberately_do_not_count_are_named_and_disjoint():
    assert not sg.ARTIFACT_EVENT_TYPES & sg.NON_ARTIFACT_EVENT_TYPES
    # The three the gate reads for other purposes must never buy a study day,
    # least of all the gate's own event.
    for type_name in (
        sg.PAUSE_EVENT_TYPE,
        sg.PROBE_EVENT_TYPE,
        sg.GATE_EVENT_TYPE,
        "session_open",
        "lesson_open",
    ):
        assert type_name in sg.NON_ARTIFACT_EVENT_TYPES


@pytest.mark.parametrize("artifact_type", DOCUMENTED_ARTIFACT_TYPES)
def test_one_documented_artifact_event_makes_a_study_day(conn, artifact_type):
    study_days(conn, days_back(13))
    seed_probe_battery(conn)
    seed_event(conn, day="2026-08-04", type=artifact_type, item_id="w-1")

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["study_days_in_window"] == 14
    assert "2026-08-04" in gate["study_day_keys"]
    assert gate["pass"] is True


@pytest.mark.parametrize("other_type", sorted(sg.NON_ARTIFACT_EVENT_TYPES))
def test_a_non_artifact_event_does_not_make_a_study_day(conn, other_type):
    study_days(conn, days_back(13))
    seed_event(conn, day="2026-08-04", type=other_type, payload={"note": "not study"})

    gate = sg.stop_gate(conn, today=TODAY, record=False)

    assert gate["study_days_in_window"] == 13
    assert "2026-08-04" not in gate["study_day_keys"]


# ---------------------------------------------------------------------------
# (a) The probe battery gates the verdict
# ---------------------------------------------------------------------------


def test_thirteen_days_fails_naming_the_day_count(conn):
    """quickstart.md D6: 13/18 days -> FAIL naming day-count."""
    study_days(conn, days_back(13))
    seed_probe_battery(conn)

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["pass"] is False
    assert gate["study_days_in_window"] == 13
    assert "study_days_in_window" in gate["failing_criterion"]
    assert "13" in gate["failing_criterion"]
    assert "14" in gate["failing_criterion"]


def test_fourteen_days_without_a_probe_battery_fails_naming_the_probe(conn):
    """quickstart.md D6: 14/18 without probe battery -> FAIL naming probe."""
    study_days(conn, days_back(14))

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["study_days_in_window"] == 14
    assert gate["pass"] is False, (
        "the day count alone must not pass the gate any more"
    )
    assert gate["probe_battery_recorded"] is False
    assert "probe" in gate["failing_criterion"]
    assert gate["failing_criteria"] == [gate["failing_criterion"]]


def test_a_probe_battery_in_one_band_does_not_pass(conn):
    """< 2 coverage bands -> FAIL, and the band shortfall is the name given."""
    study_days(conn, days_back(14))
    seed_probe_battery(conn, bands=(">=95",))

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["probe_battery_recorded"] is True
    assert gate["probe_coverage_bands"] == [">=95"]
    assert gate["pass"] is False
    assert "probe_battery_coverage_bands" in gate["failing_criterion"]
    assert f"1 of {sg.PROBE_MIN_COVERAGE_BANDS}" in gate["failing_criterion"]


def test_a_probe_event_with_no_observations_at_all_does_not_pass(conn):
    """The event on its own is a claim; the pass-rate is the evidence."""
    study_days(conn, days_back(14))
    seed_event(conn, day="2026-08-12", type=sg.PROBE_EVENT_TYPE)

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["probe_battery_recorded"] is True
    assert gate["probe_observations"] == 0
    assert gate["probe_unassisted_rate"] is None
    assert gate["pass"] is False
    assert "probe_battery_coverage_bands" in gate["failing_criterion"]


def test_two_bands_with_an_unassisted_performance_passes(conn):
    seed_passing_gate(conn)

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["pass"] is True
    assert gate["failing_criterion"] is None
    assert gate["failing_criteria"] == []
    assert gate["probe_coverage_bands"] == [">=95", "80-95"]
    assert gate["required_coverage_bands"] == 2
    assert gate["probe_unassisted_rate"] == 1.0


def test_two_bands_with_no_unassisted_performance_fails_on_the_rate(conn):
    """The threshold is on whether an unassisted rate exists, not on its value."""
    study_days(conn, days_back(14))
    seed_probe_battery(conn, unassisted=False)

    gate = sg.stop_gate(conn, today=TODAY)

    assert len(gate["probe_coverage_bands"]) == 2
    assert gate["probe_observations"] == 2
    assert gate["probe_unassisted"] == 0
    assert gate["probe_unassisted_rate"] == 0.0
    assert gate["pass"] is False
    assert "probe_battery_unassisted_rate" in gate["failing_criterion"]

    # One unassisted performance anywhere in the series is the whole threshold:
    # no proficiency bar is applied to the rate itself.
    seed_observation(conn, band="<80", unassisted=True, day="2026-08-12")
    better = sg.stop_gate(conn, today=TODAY)
    assert better["probe_unassisted"] == sg.PROBE_MIN_UNASSISTED_OBSERVATIONS
    assert better["probe_unassisted_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert better["pass"] is True, "a low rate is still a recorded rate"


def test_the_pass_rate_is_reported_per_band_in_schema_order(conn):
    study_days(conn, days_back(14))
    seed_event(conn, day="2026-08-12", type=sg.PROBE_EVENT_TYPE)
    seed_observation(conn, band="<80", unassisted=False)
    seed_observation(conn, band=">=95", unassisted=True)
    seed_observation(conn, band=">=95", unassisted=False)

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["probe_coverage_bands"] == [">=95", "<80"], "best band first"
    assert gate["probe_bands"] == [
        {
            "band": ">=95",
            "observations": 2,
            "unassisted": 1,
            "unassisted_rate": 0.5,
        },
        {
            "band": "<80",
            "observations": 1,
            "unassisted": 0,
            "unassisted_rate": 0.0,
        },
    ]
    assert gate["pass"] is True


def test_both_criteria_are_named_when_both_fall_short(conn):
    study_days(conn, days_back(13))

    gate = sg.stop_gate(conn, today=TODAY)

    assert len(gate["failing_criteria"]) == 2
    assert "study_days_in_window" in gate["failing_criteria"][0]
    assert "probe_battery_recorded" in gate["failing_criteria"][1]
    assert gate["failing_criterion"] == gate["failing_criteria"][0], (
        "the day count is named first: it is the one the learner can act on"
    )


# ---------------------------------------------------------------------------
# (c) Every evaluation is persisted
# ---------------------------------------------------------------------------


def test_every_evaluation_appends_a_gate_evaluation_event(conn):
    study_days(conn, days_back(14))

    first = sg.stop_gate(conn, today=TODAY)
    second = sg.stop_gate(conn, today=TODAY)

    recorded = gate_events(conn)
    assert len(recorded) == 2
    assert [entry["id"] for entry in recorded] == [
        first["gate_evaluation_event_id"],
        second["gate_evaluation_event_id"],
    ]
    assert recorded[0]["session_id"] == sg.GATE_EVENT_SESSION_ID

    payload = recorded[0]["payload"]
    assert payload["pass"] is False
    assert payload["failing_criterion"] == first["failing_criterion"]
    assert payload["window_start"] == first["window_start"]
    assert payload["window_end"] == TODAY
    assert payload["study_days_in_window"] == 14
    assert payload["required_study_days"] == sg.STOP_GATE_REQUIRED_DAYS
    assert payload["probe_battery_recorded"] is False
    assert payload["probe_coverage_bands"] == []
    assert payload["consecutive_failures"] == 1


def test_a_passing_evaluation_is_persisted_too(conn):
    seed_passing_gate(conn)

    gate = sg.stop_gate(conn, today=TODAY)

    (recorded,) = gate_events(conn)
    assert recorded["payload"]["pass"] is True
    assert recorded["payload"]["failing_criterion"] is None
    assert recorded["payload"]["failing_criteria"] == []
    assert recorded["payload"]["consecutive_failures"] == 0
    assert recorded["payload"]["re_plan_triggered"] is False
    assert gate["gate_evaluation_event_id"] == recorded["id"]


def test_record_false_evaluates_without_joining_the_history(conn):
    study_days(conn, days_back(13))

    gate = sg.stop_gate(conn, today=TODAY, record=False)

    assert gate["pass"] is False
    assert gate["gate_evaluation_event_id"] is None
    assert gate_events(conn) == []
    assert gate["consecutive_failures"] == 1, (
        "the verdict still knows where it would land in the run"
    )


def test_a_gate_evaluation_event_is_not_a_study_day(conn):
    """The gate's own bookkeeping must never move the number it is counting."""
    study_days(conn, days_back(13))

    first = sg.stop_gate(conn, today=TODAY)
    second = sg.stop_gate(conn, today=TODAY)

    assert first["study_days_in_window"] == 13
    assert second["study_days_in_window"] == 13


# ---------------------------------------------------------------------------
# (c) Two consecutive misses trigger the re-plan
# ---------------------------------------------------------------------------


def test_one_failure_does_not_trigger_the_re_plan(conn):
    study_days(conn, days_back(13))

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["pass"] is False
    assert gate["consecutive_failures"] == 1
    assert gate["re_plan_triggered"] is False
    assert gate["re_plan_after_failures"] == sg.RE_PLAN_AFTER_FAILURES


def test_two_consecutive_failures_trigger_the_re_plan(conn):
    study_days(conn, days_back(13))

    first = sg.stop_gate(conn, today=TODAY)
    second = sg.stop_gate(conn, today=TODAY)
    third = sg.stop_gate(conn, today=TODAY)

    assert (first["consecutive_failures"], first["re_plan_triggered"]) == (1, False)
    assert (second["consecutive_failures"], second["re_plan_triggered"]) == (2, True)
    assert (third["consecutive_failures"], third["re_plan_triggered"]) == (3, True), (
        "a third miss is still an unmet gate, not a fresh start"
    )
    assert [entry["payload"]["re_plan_triggered"] for entry in gate_events(conn)] == [
        False,
        True,
        True,
    ]


def test_a_pass_between_failures_resets_the_run(conn):
    study_days(conn, days_back(13))
    assert sg.stop_gate(conn, today=TODAY)["consecutive_failures"] == 1
    assert sg.stop_gate(conn, today=TODAY)["re_plan_triggered"] is True

    # The fourteenth day arrives, and a probe battery with it.
    study_days(conn, ["2026-08-04"])
    seed_probe_battery(conn)
    passing = sg.stop_gate(conn, today=TODAY)
    assert passing["pass"] is True
    assert passing["consecutive_failures"] == 0
    assert passing["re_plan_triggered"] is False

    # A later miss starts counting from one again, not from three.
    later = sg.stop_gate(conn, today="2026-09-30")
    assert later["pass"] is False
    assert later["consecutive_failures"] == 1
    assert later["re_plan_triggered"] is False


def test_an_unreadable_gate_payload_is_reported_not_counted_as_a_miss(conn):
    study_days(conn, days_back(13))
    sg.stop_gate(conn, today=TODAY)  # one real failure, on the record
    bad = seed_event(
        conn, day=TODAY, type=sg.GATE_EVENT_TYPE, payload={"note": "hand-written"}
    )

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["ignored_gate_events"] == [bad]
    assert gate["consecutive_failures"] == 1, (
        "an unreadable verdict ends the run: 'cannot tell' is not 'failed'"
    )
    assert gate["re_plan_triggered"] is False


# ---------------------------------------------------------------------------
# Regression: the T020 declared-pause exclusion, through the new criteria
# ---------------------------------------------------------------------------


def test_declared_pause_still_shrinks_the_denominator(conn):
    paused = {f"2026-08-{day:02d}" for day in range(10, 15)}
    seed_event(
        conn,
        day="2026-08-09",
        type=sg.PAUSE_EVENT_TYPE,
        payload={"start_day": "2026-08-10", "end_day": "2026-08-14"},
    )
    study_days(conn, days_back(14, skip=paused))
    seed_probe_battery(conn, day="2026-08-19")

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["window_length_days"] == 18
    assert gate["excluded_pause_days"] == 5
    assert gate["window_start"] == "2026-07-28"
    assert gate["study_days_in_window"] == 14
    assert not paused & set(gate["study_day_keys"])
    assert gate["pass"] is True


def test_study_on_a_paused_day_is_still_not_counted(conn):
    seed_event(
        conn,
        day="2026-08-09",
        type=sg.PAUSE_EVENT_TYPE,
        payload={"days": ["2026-08-10"]},
    )
    study_days(conn, ["2026-08-10"])

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["study_day_keys"] == []
    assert gate["excluded_pause_days"] == 1


def test_an_unreadable_pause_payload_is_still_reported_not_assumed(conn):
    bad = seed_event(
        conn,
        day="2026-08-09",
        type=sg.PAUSE_EVENT_TYPE,
        payload={"note": "away for a bit"},
    )

    gate = sg.stop_gate(conn, today=TODAY)

    assert gate["ignored_pause_events"] == [bad]
    assert gate["excluded_pause_days"] == 0


def test_a_malformed_today_is_still_a_caller_error(conn):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        sg.stop_gate(conn, today="19/08/2026")
    assert gate_events(conn) == [], "a refused call records no verdict"
