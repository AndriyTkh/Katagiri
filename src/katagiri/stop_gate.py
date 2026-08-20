"""D6: the study-consistency stop gate — counted from the event log, never judged.

What this module is for
-----------------------
Phase E code is blocked until the loop has actually been *used*: 14 study days
inside the 18-day window ending today, plus a recorded probe battery. This
module is that count, and nothing more. :func:`stop_gate` reads the event log,
reports the number, names the shortfall when there is one, and says whether a
probe battery exists at all. It does not decide what the verdict means, does not
write anything, and does not reach for a clock the caller did not ask for
(``today`` overrides it for tests).

A study day is a concrete event-type count, not an interpretation: ``study_log``
events on that day totalling at least :data:`STUDY_MINUTES_PER_DAY`, or at least
one event of an :data:`ARTIFACT_EVENT_TYPES` type. Minutes are summed in Python
rather than in SQL because the field is free-form JSON written by an importer —
"45" counts, ``null`` and "about an hour" do not, and neither aborts the check.

Declared pauses shrink the denominator, not the numerator
--------------------------------------------------------
Days covered by a ``pause_declared`` event are removed from the window, so the
18-day window walks further back in calendar time until it holds 18 *countable*
days. A declared illness or travel pause therefore costs the learner nothing,
while an undeclared one costs the full day — which is the whole point of asking
for the declaration.

Failures are values, except for caller-domain mistakes
------------------------------------------------------
A ``pause_declared`` payload that cannot be read is reported by id in
``ignored_pause_events`` rather than silently treated as "no pause": a typo in a
payload must not quietly fail the gate. A malformed ``today``, by contrast, is a
caller mistake and raises ``ValueError``, matching the rest of the codebase.

These rules are duplicated by name (not imported) in
:mod:`katagiri.sensei_letter`, which renders them into prose and must not drag
this module's dependencies in behind it. If a name changes, it changes in both
places.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any, Final

from katagiri import events

STOP_GATE_WINDOW_DAYS: Final = 18
STOP_GATE_REQUIRED_DAYS: Final = 14
STUDY_MINUTES_PER_DAY: Final = 10
MAX_PAUSE_SPAN_DAYS: Final = 365

# One of these on a day is enough on its own: it is a durable artifact of study,
# not a claim about time spent.
ARTIFACT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "mark_known",
        "mark_unknown",
        "mark_suspect",
        "review",
        "review_batch",
        "lesson_close",
        "mining",
    }
)
PAUSE_EVENT_TYPE: Final = "pause_declared"
PROBE_EVENT_TYPE: Final = "probe_battery"
_PAUSE_START_KEYS: Final = ("start_day", "from_day", "start", "from")
_PAUSE_END_KEYS: Final = ("end_day", "to_day", "end", "to")


def _parse_day(value: object) -> date | None:
    """A ``YYYY-MM-DD`` string as a date, or ``None`` if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _minutes(value: object) -> float | None:
    """Minutes from a payload field, or ``None`` if it is not a usable number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if number >= 0 else None


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _pause_span(payload: str | None) -> set[str] | None:
    """Day keys a ``pause_declared`` payload covers, or ``None`` if unreadable.

    Accepts either an explicit ``days`` list or a start/end pair. Returning
    ``None`` rather than an empty set matters: the caller reports unreadable
    pause events instead of quietly treating them as "no pause", which would let
    a typo in a payload silently fail the gate.
    """
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    listed = data.get("days")
    if isinstance(listed, list):
        parsed = {day.isoformat() for day in map(_parse_day, listed) if day}
        return parsed or None

    start = _parse_day(_first(data, _PAUSE_START_KEYS))
    if start is None:
        return None
    raw_end = _first(data, _PAUSE_END_KEYS)
    end = start if raw_end is None else _parse_day(raw_end)
    if end is None or end < start or (end - start).days > MAX_PAUSE_SPAN_DAYS:
        return None
    return {
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    }


def _pause_days(conn: sqlite3.Connection) -> tuple[set[str], list[str]]:
    """Every paused day key, and the ids of pause events that could not be read."""
    days: set[str] = set()
    ignored: list[str] = []
    for row in conn.execute(
        "SELECT id, payload FROM event WHERE type = ? ORDER BY id",
        (PAUSE_EVENT_TYPE,),
    ):
        span = _pause_span(row["payload"])
        if span is None:
            ignored.append(str(row["id"]))
            continue
        days |= span
    return days, ignored


def _study_days(conn: sqlite3.Connection, since_day: str) -> set[str]:
    """Day keys on or after ``since_day`` that count as a study day.

    Minutes are summed per day in Python rather than in SQL because the field is
    free-form JSON written by an importer: a string "45" counts, ``null`` and
    "about an hour" do not, and neither should abort the whole check.
    """
    minutes_by_day: dict[str, float] = {}
    for row in conn.execute(
        "SELECT day_key, payload FROM event WHERE type = ? AND day_key >= ?",
        (events.STUDY_LOG_TYPE, since_day),
    ):
        payload = row["payload"]
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        minutes = _minutes(data.get("minutes"))
        if minutes is None:
            continue
        key = str(row["day_key"])
        minutes_by_day[key] = minutes_by_day.get(key, 0.0) + minutes

    qualifying = {
        day
        for day, total in minutes_by_day.items()
        if total >= STUDY_MINUTES_PER_DAY
    }

    placeholders = ", ".join("?" * len(ARTIFACT_EVENT_TYPES))
    artifact_types = sorted(ARTIFACT_EVENT_TYPES)
    qualifying |= {
        str(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT day_key FROM event "
            f"WHERE type IN ({placeholders}) AND day_key >= ?",
            (*artifact_types, since_day),
        )
    }
    return qualifying


def stop_gate(
    conn: sqlite3.Connection, *, today: str | None = None
) -> dict[str, Any]:
    """Mechanical PASS/FAIL of the study-consistency gate. Reads only.

    The criterion is 14 study days inside the 18-day window ending today. Days
    covered by a ``pause_declared`` event are removed from the denominator, so the
    window walks further back in calendar time until it holds 18 countable days —
    a declared pause costs the learner nothing, and an undeclared one costs the
    full day.

    ``today`` overrides the clock for tests; the tool passes ``None``. Nothing
    here interprets the verdict: it counts, names the shortfall if there is one,
    and reports whether a probe battery exists at all.
    """
    end = date.today() if today is None else _parse_day(today)
    if end is None:
        raise ValueError(f"today must be a YYYY-MM-DD date; got {today!r}.")

    paused, ignored_pause_events = _pause_days(conn)

    # Walking back at most 18 + (number of paused days) calendar days is enough
    # to collect 18 unpaused ones, because that is every paused day there is.
    window: list[str] = []
    cursor = end
    for _ in range(STOP_GATE_WINDOW_DAYS + len(paused)):
        key = cursor.isoformat()
        if key not in paused:
            window.append(key)
            if len(window) == STOP_GATE_WINDOW_DAYS:
                break
        cursor -= timedelta(days=1)
    window.reverse()

    window_start = window[0]
    window_end = window[-1]
    span_days = (end - date.fromisoformat(window_start)).days + 1

    qualifying = _study_days(conn, window_start)
    study_day_keys = sorted(day for day in window if day in qualifying)
    study_days_in_window = len(study_day_keys)
    passed = study_days_in_window >= STOP_GATE_REQUIRED_DAYS

    failing_criterion = (
        None
        if passed
        else (
            f"study_days_in_window: {study_days_in_window} of "
            f"{STOP_GATE_REQUIRED_DAYS} required study days in the "
            f"{len(window)}-day window {window_start}..{window_end}"
        )
    )

    probe = conn.execute(
        "SELECT 1 FROM event WHERE type = ? LIMIT 1", (PROBE_EVENT_TYPE,)
    ).fetchone()

    return {
        "pass": passed,
        "failing_criterion": failing_criterion,
        "study_days_in_window": study_days_in_window,
        "window_start": window_start,
        "window_end": window_end,
        "probe_battery_recorded": probe is not None,
        "required_study_days": STOP_GATE_REQUIRED_DAYS,
        "window_length_days": len(window),
        "excluded_pause_days": span_days - len(window),
        "study_day_keys": study_day_keys,
        "ignored_pause_events": ignored_pause_events,
    }


__all__ = [
    "ARTIFACT_EVENT_TYPES",
    "MAX_PAUSE_SPAN_DAYS",
    "PAUSE_EVENT_TYPE",
    "PROBE_EVENT_TYPE",
    "STOP_GATE_REQUIRED_DAYS",
    "STOP_GATE_WINDOW_DAYS",
    "STUDY_MINUTES_PER_DAY",
    "stop_gate",
]
