"""D6: the study-consistency stop gate — counted from the event log, never judged.

What this module is for
-----------------------
Phase E code is blocked until the loop has actually been *used*: 14 study days
inside the 18-day window ending today, plus a probe battery whose unassisted
pass-rate is recorded across at least two coverage bands. This module is that
count, and nothing more. :func:`stop_gate` reads the event log and the
observation log, reports the numbers, and names every criterion that fell short.
It does not decide what the verdict means and does not reach for a clock the
caller did not ask for (``today`` overrides it for tests).

A study day is a concrete event-type count, not an interpretation: ``study_log``
events on that day totalling at least :data:`STUDY_MINUTES_PER_DAY`, or at least
one event of an :data:`ARTIFACT_EVENT_TYPES` type. That set is spelled out with a
reason per type in :data:`ARTIFACT_EVENT_REASONS`, and the types that look like
artifacts but deliberately are *not* one are spelled out too, in
:data:`NON_ARTIFACT_EVENT_TYPES` — "≥ 1 logged artifact" has to be a list of
strings a test can pin, or it becomes whatever the reader wants it to be.
Minutes are summed in Python rather than in SQL because the field is free-form
JSON written by an importer — "45" counts, ``null`` and "about an hour" do not,
and neither aborts the check.

The probe criterion gates the verdict
-------------------------------------
The probe battery is not a footnote on the day count: a fortnight of logged
minutes with no scored performance behind it says nothing about whether the loop
works. So the gate fails unless a ``probe_battery`` event exists **and** the
``observation`` table holds an unassisted pass-rate spread over at least
:data:`PROBE_MIN_COVERAGE_BANDS` coverage bands, with at least
:data:`PROBE_MIN_UNASSISTED_OBSERVATIONS` unassisted performance somewhere in it.

Note what is *not* being asked: nothing here compares the pass-rate against a
number. A proficiency bar would block Phase E for being bad at Japanese, which is
not what this gate is for. The requirement is that the rate exists, in more than
one band, so a later reading can tell performance apart from comprehensibility of
input. The bands and the ``unassisted`` flag come from
``session_tools.log_observations``, which refuses an observation missing either.

The whole observation log is read, not a window: a probe battery is a one-off
run, and the append-only log is the record that it happened.

Two consecutive misses trigger an explicit re-plan
--------------------------------------------------
Each evaluation appends a :data:`GATE_EVENT_TYPE` event carrying the verdict and
the criterion that failed, so the gate has a history instead of an opinion. When
that history shows :data:`RE_PLAN_AFTER_FAILURES` consecutive failures — the
evaluation being made now included — ``re_plan_triggered`` is true: an unmet gate
twice over is a planning problem, not silent limbo. This is the one thing in the
module that writes, and it is deliberately inside :func:`stop_gate` rather than
in a wrapper, so no caller can evaluate the gate without the evaluation being
recorded. ``record=False`` exists for a caller that genuinely only wants to look.

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
payload must not quietly fail the gate. A ``gate_evaluation`` payload that cannot
be read is reported the same way in ``ignored_gate_events`` and breaks the
failure run rather than being counted as a miss. A malformed ``today``, by
contrast, is a caller mistake and raises ``ValueError``, matching the rest of the
codebase.

These rules are duplicated by name (not imported) in
:mod:`katagiri.sensei_letter`, which renders them into prose and must not drag
this module's dependencies in behind it. If a name changes, it changes in both
places.

The 006 entry gate is additive, and a separate verdict
----------------------------------------------------------
D-33 adds a second, unrelated question on top of the D6 mechanics above: not
"has the loop been used consistently these last 18 days" but "does the log
hold enough of the *right kind* of evidence to calibrate a teaching method
against" — ten arbitrary or TIRED-only days say nothing about that. Three
counts, each over the *whole* event log rather than the 18-day window: every
qualifying study day the log has ever recorded (:data:`ENTRY_GATE_MIN_STUDY_DAYS`),
every day carrying a scored observation — an :data:`ENTRY_GATE_OBSERVATION_EVENT_TYPE`
event whose payload has every field ``session_tools.log_observations`` enforces
before it writes one (:data:`ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS`), and every
day carrying a dictation artifact — a :data:`ENTRY_GATE_DICTATION_EVENT_TYPE`
event whose payload ``topic`` is the reserved Phase-0 slug
:data:`ENTRY_GATE_DICTATION_TOPIC` (:data:`ENTRY_GATE_MIN_DICTATION_DAYS`).

This lives in :func:`stop_gate`'s result as the additive ``entry_gate`` key and
never changes the meaning of the pre-existing ``pass`` boolean: the 14-in-18
day count and the probe battery remain necessary on their own, exactly as
before T009. ``entry_gate`` is not written into the persisted
``gate_evaluation`` payload; the registration task that surfaces it through
the tool layer decides that.
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

#: One of these on a day is enough on its own: it is a durable artifact of study,
#: not a claim about time spent. Written out with the reason per type because
#: "at least one logged artifact" is a criterion, and a criterion that is a bare
#: set literal is one refactor away from meaning something else. The frozenset
#: below is derived from this mapping, so the two can never disagree.
ARTIFACT_EVENT_REASONS: Final[dict[str, str]] = {
    "mark_known": "a known/unknown/suspect verdict on an item is dated and durable",
    "mark_unknown": "same verdict, other direction — still a decision that was made",
    "mark_suspect": "flagging an item as shaky is a judgement about it, logged",
    "review": "one answered review is a scored attempt that happened",
    "review_batch": "a batch of reviews, collapsed by the writer into one event",
    "lesson_close": "a lesson with an objective and a next step, closed",
    "mining": "material extracted from real input and added to the study set",
}

#: Exactly the day-qualifying artifact types, derived from the reasons above.
ARTIFACT_EVENT_TYPES: Final[frozenset[str]] = frozenset(ARTIFACT_EVENT_REASONS)

#: Types that are *deliberately* not artifacts, listed so the exclusion is a
#: decision on the record rather than an oversight. Opening a session or a lesson
#: is an intention; seeking in a video, regenerating a dictionary export, writing
#: a letter, declaring a pause, or evaluating this gate are not study. None of
#: them may buy a study day. Not exhaustive — the log's vocabulary is open — but
#: every one of these has been asked about at least once.
NON_ARTIFACT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session_open",
        "lesson_open",
        "seek",
        "regen_yomitan",
        "sensei_letter",
        "tombstone_session",
        "pause_declared",
        "probe_battery",
        "gate_evaluation",
    }
)

PAUSE_EVENT_TYPE: Final = "pause_declared"
PROBE_EVENT_TYPE: Final = "probe_battery"

#: The gate's own verdict history. Appended by :func:`stop_gate` on every
#: evaluation; documented in docs/db-schema.md alongside the other event types
#: this module parses rather than merely counts.
GATE_EVENT_TYPE: Final = "gate_evaluation"
GATE_EVENT_SESSION_ID: Final = "stop-gate"

#: A pass-rate in one band cannot be told apart from the comprehensibility of the
#: input it was measured in; that is the whole reason ``coverage_band`` is a
#: required field. Two bands is the smallest number that carries the comparison
#: (spec FR-010: "unassisted pass-rate across >= 2 coverage bands").
PROBE_MIN_COVERAGE_BANDS: Final = 2

#: An all-assisted log has no *unassisted* pass-rate to record, however many rows
#: it holds. One is the floor, not a proficiency bar: the gate asks whether the
#: measurement exists, never whether the learner scored well.
PROBE_MIN_UNASSISTED_OBSERVATIONS: Final = 1

#: Exactly the ``observation.coverage_band`` CHECK, best band first, matching
#: ``session_tools.COVERAGE_BANDS`` and ``sensei_letter.COVERAGE_BAND_ORDER``.
#: Duplicated rather than imported for the same reason as the rules above.
COVERAGE_BANDS: Final[tuple[str, ...]] = (">=95", "80-95", "<80")

#: "If unmet twice -> explicit re-plan" (spec US5). Two, not three: the point is
#: to notice the second miss, while the plan can still change.
RE_PLAN_AFTER_FAILURES: Final = 2

#: How far back the failure run is walked. The trigger only ever needs two
#: evaluations, so a longer run is reported up to this cap and no further — a log
#: with thousands of failed evaluations must not turn a status call into a scan.
MAX_GATE_HISTORY_SCAN: Final = 64

_PAUSE_START_KEYS: Final = ("start_day", "from_day", "start", "from")
_PAUSE_END_KEYS: Final = ("end_day", "to_day", "end", "to")

# ---------------------------------------------------------------------------
# The 006 entry gate (D-33) — additive, and counted over the whole log rather
# than the 18-day D6 window. See the module docstring's "006 entry gate"
# section for the reasoning; docs/db-schema.md documents these next to
# `gate_evaluation`.
# ---------------------------------------------------------------------------

#: "≥10 study days" (D-33, spec FR-010). Cumulative: the whole event log, not
#: a window — the question is whether ten such days have ever happened.
ENTRY_GATE_MIN_STUDY_DAYS: Final = 10

#: "≥6 with a scored observation" (D-33, spec FR-010).
ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS: Final = 6

#: "≥3 with a dictation artifact" (D-33, spec FR-010).
ENTRY_GATE_MIN_DICTATION_DAYS: Final = 3

#: The event type ``session_tools.log_observations`` appends one of per
#: scored performance. Duplicated by name rather than imported, matching this
#: module's existing convention (see the module docstring) of not dragging
#: ``session_tools`` in as a dependency.
ENTRY_GATE_OBSERVATION_EVENT_TYPE: Final = "observation"

#: The mandatory fields ``session_tools.log_observations`` enforces before it
#: will write an observation. A day only counts as "scored" if its event's
#: payload actually carries all three — a hand-written or malformed
#: ``observation`` event must not buy the day for free.
ENTRY_GATE_SCORED_OBSERVATION_KEYS: Final = (
    "unassisted",
    "coverage_band",
    "rubric_version",
)

#: The event type a dictation artifact rides (D-32): already one of
#: :data:`ARTIFACT_EVENT_TYPES`, named again here because the entry gate reads
#: it for a different question — which topic, not merely that it happened.
ENTRY_GATE_DICTATION_EVENT_TYPE: Final = "lesson_close"

#: The reserved Phase-0 dictation topic slug (D-32) a qualifying
#: ``lesson_close`` payload's ``topic`` field carries — the same field
#: ``session_tools.log_lesson`` writes.
ENTRY_GATE_DICTATION_TOPIC: Final = "phase0-kana-dictation"


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


def _payload_dict(payload: str | None) -> dict[str, Any] | None:
    """A payload column as a dict, or ``None`` if it is not readable as one."""
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _pause_span(payload: str | None) -> set[str] | None:
    """Day keys a ``pause_declared`` payload covers, or ``None`` if unreadable.

    Accepts either an explicit ``days`` list or a start/end pair. Returning
    ``None`` rather than an empty set matters: the caller reports unreadable
    pause events instead of quietly treating them as "no pause", which would let
    a typo in a payload silently fail the gate.
    """
    data = _payload_dict(payload)
    if data is None:
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
        data = _payload_dict(row["payload"])
        if data is None:
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


def _rate(part: int, whole: int) -> float | None:
    """``part / whole`` to four places, or ``None`` when there is no denominator."""
    if whole <= 0:
        return None
    return round(part / whole, 4)


def _probe_battery(conn: sqlite3.Connection) -> dict[str, Any]:
    """The recorded probe battery, banded: is there an unassisted pass-rate?

    Counts the whole ``observation`` table rather than the study window. A probe
    battery is a one-off run and the observation log is append-only, so "was one
    recorded" is a question about the log, not about the last 18 days.

    ``unassisted`` is the pass-rate numerator (the same reading
    :mod:`katagiri.sensei_letter` takes): an assisted production is a different
    observation, not a slightly worse one, so it is counted separately rather
    than discounted.
    """
    recorded = (
        conn.execute(
            "SELECT 1 FROM event WHERE type = ? LIMIT 1", (PROBE_EVENT_TYPE,)
        ).fetchone()
        is not None
    )

    counts: dict[str, list[int]] = {}
    for row in conn.execute(
        "SELECT coverage_band, COUNT(*) AS total, "
        "SUM(CASE WHEN unassisted THEN 1 ELSE 0 END) AS unassisted "
        "FROM observation GROUP BY coverage_band"
    ):
        band = str(row["coverage_band"])
        counts[band] = [int(row["total"] or 0), int(row["unassisted"] or 0)]

    # Schema order first (best band first), then anything the enum does not
    # cover, alphabetically. The CHECK constraint makes the second list empty in
    # practice; dropping such a row silently would hide a hand-written one.
    ordered = [band for band in COVERAGE_BANDS if band in counts]
    ordered += sorted(band for band in counts if band not in COVERAGE_BANDS)

    total = sum(counts[band][0] for band in ordered)
    unassisted = sum(counts[band][1] for band in ordered)

    return {
        "recorded": recorded,
        "observations": total,
        "unassisted": unassisted,
        "unassisted_rate": _rate(unassisted, total),
        "coverage_bands": ordered,
        "bands": [
            {
                "band": band,
                "observations": counts[band][0],
                "unassisted": counts[band][1],
                "unassisted_rate": _rate(counts[band][1], counts[band][0]),
            }
            for band in ordered
        ],
    }


def _gate_verdict(payload: str | None) -> bool | None:
    """The ``pass`` flag of a ``gate_evaluation`` payload, or ``None``.

    ``None`` means "cannot tell", which is not the same as "failed": an
    unreadable verdict must not be counted toward the re-plan trigger.
    """
    data = _payload_dict(payload)
    if data is None:
        return None
    verdict = data.get("pass")
    return verdict if isinstance(verdict, bool) else None


def _prior_gate_failures(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """Length of the failure run ending at the newest recorded evaluation.

    Walks ``gate_evaluation`` events newest first and stops at the first one that
    passed — or at the first one whose verdict cannot be read, whose id is
    returned so an unreadable payload is visible rather than being counted as
    either outcome.
    """
    failures = 0
    ignored: list[str] = []
    rows = conn.execute(
        "SELECT id, payload FROM event WHERE type = ? ORDER BY id DESC LIMIT ?",
        (GATE_EVENT_TYPE, MAX_GATE_HISTORY_SCAN),
    ).fetchall()
    for row in rows:
        verdict = _gate_verdict(row["payload"])
        if verdict is None:
            ignored.append(str(row["id"]))
            break
        if verdict:
            break
        failures += 1
    return failures, ignored


def _entry_gate(conn: sqlite3.Connection) -> dict[str, Any]:
    """The 006 entry gate (D-33): additive to the D6 mechanics, and separate.

    Three counts, each over the *whole* event log rather than the 18-day D6
    window — the question is whether the evidence has ever accumulated, not
    whether it accumulated recently:

    * qualifying study days: the same rule :func:`_study_days` applies to the
      D6 window, applied here with no lower bound on ``day_key``.
    * days carrying a scored observation: an
      :data:`ENTRY_GATE_OBSERVATION_EVENT_TYPE` event whose payload has every
      key in :data:`ENTRY_GATE_SCORED_OBSERVATION_KEYS`.
    * days carrying a dictation artifact: an
      :data:`ENTRY_GATE_DICTATION_EVENT_TYPE` event whose payload ``topic``
      equals :data:`ENTRY_GATE_DICTATION_TOPIC`.

    Reported the same way the D6 criteria are: every shortfall named by a
    stable string a test can pin, nothing interpreted. This dict becomes the
    ``entry_gate`` key in :func:`stop_gate`'s result; it does not read or
    change the pre-existing ``pass`` verdict.
    """
    study_days_total = len(_study_days(conn, ""))

    scored_days: set[str] = set()
    for row in conn.execute(
        "SELECT day_key, payload FROM event WHERE type = ?",
        (ENTRY_GATE_OBSERVATION_EVENT_TYPE,),
    ):
        data = _payload_dict(row["payload"])
        if data is not None and all(
            key in data for key in ENTRY_GATE_SCORED_OBSERVATION_KEYS
        ):
            scored_days.add(str(row["day_key"]))

    dictation_days: set[str] = set()
    for row in conn.execute(
        "SELECT day_key, payload FROM event WHERE type = ?",
        (ENTRY_GATE_DICTATION_EVENT_TYPE,),
    ):
        data = _payload_dict(row["payload"])
        if data is not None and data.get("topic") == ENTRY_GATE_DICTATION_TOPIC:
            dictation_days.add(str(row["day_key"]))

    scored_count = len(scored_days)
    dictation_count = len(dictation_days)

    checks = (
        (
            "entry_gate_study_days",
            study_days_total,
            ENTRY_GATE_MIN_STUDY_DAYS,
            "required qualifying study days",
        ),
        (
            "entry_gate_scored_observation_days",
            scored_count,
            ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS,
            "required days with a scored observation",
        ),
        (
            "entry_gate_dictation_days",
            dictation_count,
            ENTRY_GATE_MIN_DICTATION_DAYS,
            "required days with a dictation artifact",
        ),
    )

    failing_criteria = [
        f"{name}: {count} of {required} {description}"
        for name, count, required, description in checks
        if count < required
    ]

    return {
        "pass": not failing_criteria,
        "failing_criterion": failing_criteria[0] if failing_criteria else None,
        "failing_criteria": failing_criteria,
        "study_days": study_days_total,
        "required_study_days": ENTRY_GATE_MIN_STUDY_DAYS,
        "study_days_pass": study_days_total >= ENTRY_GATE_MIN_STUDY_DAYS,
        "scored_observation_days": scored_count,
        "required_scored_observation_days": ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS,
        "scored_observation_days_pass": (
            scored_count >= ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS
        ),
        "dictation_days": dictation_count,
        "required_dictation_days": ENTRY_GATE_MIN_DICTATION_DAYS,
        "dictation_days_pass": dictation_count >= ENTRY_GATE_MIN_DICTATION_DAYS,
    }


def stop_gate(
    conn: sqlite3.Connection, *, today: str | None = None, record: bool = True
) -> dict[str, Any]:
    """Mechanical PASS/FAIL of the study-consistency gate, and record it.

    Two criteria, both counted:

    * 14 study days inside the 18-day window ending today. Days covered by a
      ``pause_declared`` event are removed from the denominator, so the window
      walks further back in calendar time until it holds 18 countable days — a
      declared pause costs the learner nothing, and an undeclared one costs the
      full day.
    * a recorded probe battery whose unassisted pass-rate spans at least
      :data:`PROBE_MIN_COVERAGE_BANDS` coverage bands and contains at least one
      unassisted performance. Recorded, not good: no threshold is applied to the
      rate itself.

    Every evaluation is appended to the log as a :data:`GATE_EVENT_TYPE` event,
    which is what makes ``consecutive_failures`` and ``re_plan_triggered``
    answerable at all; the count includes the evaluation being made now. Pass
    ``record=False`` for a look that must not be part of that history — the tool
    surface never does.

    ``today`` overrides the clock for tests; the tool passes ``None``. Nothing
    here interprets the verdict: it counts and names every shortfall.

    The result also carries an additive ``entry_gate`` key (D-33, the 006
    entry gate — see :func:`_entry_gate`): a separate verdict over the whole
    log, computed alongside this one and never altering the meaning of
    ``pass`` above.
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

    probe = _probe_battery(conn)
    probe_bands = probe["coverage_bands"]

    failing_criteria: list[str] = []
    if study_days_in_window < STOP_GATE_REQUIRED_DAYS:
        failing_criteria.append(
            f"study_days_in_window: {study_days_in_window} of "
            f"{STOP_GATE_REQUIRED_DAYS} required study days in the "
            f"{len(window)}-day window {window_start}..{window_end}"
        )

    if not probe["recorded"]:
        failing_criteria.append(
            f"probe_battery_recorded: no {PROBE_EVENT_TYPE} event in the log; "
            "the gate needs one recorded probe battery run"
        )
    elif len(probe_bands) < PROBE_MIN_COVERAGE_BANDS:
        failing_criteria.append(
            f"probe_battery_coverage_bands: unassisted pass-rate recorded in "
            f"{len(probe_bands)} of {PROBE_MIN_COVERAGE_BANDS} required coverage "
            f"bands ({', '.join(probe_bands) or 'none'})"
        )
    elif probe["unassisted"] < PROBE_MIN_UNASSISTED_OBSERVATIONS:
        failing_criteria.append(
            f"probe_battery_unassisted_rate: {probe['observations']} observations "
            f"across {len(probe_bands)} coverage bands, none of them unassisted; "
            f"an unassisted pass-rate needs at least "
            f"{PROBE_MIN_UNASSISTED_OBSERVATIONS} unassisted performance"
        )

    passed = not failing_criteria
    failing_criterion = None if passed else failing_criteria[0]

    prior_failures, ignored_gate_events = _prior_gate_failures(conn)
    consecutive_failures = 0 if passed else prior_failures + 1
    re_plan_triggered = consecutive_failures >= RE_PLAN_AFTER_FAILURES

    result: dict[str, Any] = {
        "pass": passed,
        "failing_criterion": failing_criterion,
        "failing_criteria": failing_criteria,
        "study_days_in_window": study_days_in_window,
        "window_start": window_start,
        "window_end": window_end,
        "probe_battery_recorded": probe["recorded"],
        "probe_coverage_bands": probe_bands,
        "probe_observations": probe["observations"],
        "probe_unassisted": probe["unassisted"],
        "probe_unassisted_rate": probe["unassisted_rate"],
        "probe_bands": probe["bands"],
        "required_coverage_bands": PROBE_MIN_COVERAGE_BANDS,
        "required_study_days": STOP_GATE_REQUIRED_DAYS,
        "window_length_days": len(window),
        "excluded_pause_days": span_days - len(window),
        "study_day_keys": study_day_keys,
        "consecutive_failures": consecutive_failures,
        "re_plan_triggered": re_plan_triggered,
        "re_plan_after_failures": RE_PLAN_AFTER_FAILURES,
        "ignored_pause_events": ignored_pause_events,
        "ignored_gate_events": ignored_gate_events,
        "entry_gate": _entry_gate(conn),
    }

    event_id = None
    if record:
        # Appended after the verdict is computed, so the run this evaluation
        # belongs to is the prior run plus one — never a read of its own row.
        event_id = events.append_event(
            conn,
            type=GATE_EVENT_TYPE,
            session_id=GATE_EVENT_SESSION_ID,
            payload={
                "pass": passed,
                "failing_criterion": failing_criterion,
                "failing_criteria": failing_criteria,
                "consecutive_failures": consecutive_failures,
                "re_plan_triggered": re_plan_triggered,
                "study_days_in_window": study_days_in_window,
                "required_study_days": STOP_GATE_REQUIRED_DAYS,
                "window_start": window_start,
                "window_end": window_end,
                "probe_battery_recorded": probe["recorded"],
                "probe_coverage_bands": probe_bands,
                "probe_unassisted_rate": probe["unassisted_rate"],
            },
        )
    result["gate_evaluation_event_id"] = event_id
    return result


__all__ = [
    "ARTIFACT_EVENT_REASONS",
    "ARTIFACT_EVENT_TYPES",
    "COVERAGE_BANDS",
    "ENTRY_GATE_DICTATION_EVENT_TYPE",
    "ENTRY_GATE_DICTATION_TOPIC",
    "ENTRY_GATE_MIN_DICTATION_DAYS",
    "ENTRY_GATE_MIN_SCORED_OBSERVATION_DAYS",
    "ENTRY_GATE_MIN_STUDY_DAYS",
    "ENTRY_GATE_OBSERVATION_EVENT_TYPE",
    "ENTRY_GATE_SCORED_OBSERVATION_KEYS",
    "GATE_EVENT_SESSION_ID",
    "GATE_EVENT_TYPE",
    "MAX_GATE_HISTORY_SCAN",
    "MAX_PAUSE_SPAN_DAYS",
    "NON_ARTIFACT_EVENT_TYPES",
    "PAUSE_EVENT_TYPE",
    "PROBE_EVENT_TYPE",
    "PROBE_MIN_COVERAGE_BANDS",
    "PROBE_MIN_UNASSISTED_OBSERVATIONS",
    "RE_PLAN_AFTER_FAILURES",
    "STOP_GATE_REQUIRED_DAYS",
    "STOP_GATE_WINDOW_DAYS",
    "STUDY_MINUTES_PER_DAY",
    "stop_gate",
]
