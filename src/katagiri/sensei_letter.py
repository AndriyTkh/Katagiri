"""Weekly "sensei letter": a short progress note derived from the event log.

The letter is prose first and numbers second, on purpose. A dashboard makes you
compliant; a teacher noticing what changed makes you a learner. So this module
reads one ISO week out of the ``event`` table, reduces it to a small
:class:`WeekStats`, and renders a warm two-or-three paragraph note plus one
table. Nothing here judges, and nothing here invents: every figure is a count of
something that was actually appended to the log.

**Only append-only sources.** The ``event`` log, and — since D5 — the two other
source-of-truth logs the teacher loop writes: ``observation`` (rubric-scored
performances) and ``lesson_unresolved`` (questions served and not answered). No
Anki mirror, no vault scraping, no derived cache, no recomputation of the known
set. That keeps the letter reproducible — the same week always renders the same
bytes — and keeps it honest about what it does not know (a quiet week reads as a
quiet week, not as failure).

Phase D (D5) enriches this: the error museum, open threads, and the probe /
observation record are three more paragraphs. The two extension seams meant for
that are :meth:`WeekStats.table_rows` and :data:`BODY_SECTIONS` — add a row
builder or a paragraph builder there rather than rewriting
:func:`render_letter`.

The study-day rule (``study_session`` minutes summing to >= 10, or any durable
study artifact on that day) is deliberately the *same* rule the stop gate in
:mod:`katagiri.mcp_server` applies, and is replicated here rather than imported:
that module imports the MCP server SDK, and a letter renderer must not drag a
transport dependency in behind it. If the rule changes, it changes in both
places — the constants below name their twin so the grep finds them.

SECRETS: the letter is written into the vault and its path is appended to the
event log. Only aggregate counts and the ISO week reach the *event log*, never
payload text, note bodies, or file contents.

The letter body itself quotes exactly three kinds of short label, and nothing
else: an error ``pattern`` whose own event payload records **no** untrusted
provenance for that field, an observation's ``task_type``, and the enum-valued
``coverage_band`` / ``rubric_version``. Media-derived text is counted and named
as counted, never repeated — a generated vault note is a bad place to replay
text a subtitle file wrote. Unresolved-thread text is never quoted at all: the
``lesson`` tables carry no provenance column, so the module cannot prove who
wrote a thread, and ``lessons(unresolved_only=True)`` is the honest place to read
them. Every quoted label is whitespace-collapsed and length-capped
(:data:`MAX_LABEL_CHARS`) so one 2 000-character "pattern" cannot become the
letter.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

from katagiri import events
from katagiri.config import get_config
from katagiri.logging_setup import get_logger

logger = get_logger("sensei_letter")

# --- Rules shared with mcp_server.stop_gate (keep in step; see module docstring)
STUDY_MINUTES_PER_DAY: Final = 10
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

LETTER_EVENT_TYPE: Final = "sensei_letter"
PROGRESS_DIR_NAME: Final = "80-progress"
FRONTMATTER_SCHEMA: Final = 2
FRONTMATTER_TYPE: Final = "progress"
LETTER_SUFFIX: Final = "-sensei-letter.md"

# A streak is history, not a week: it may reach back past the week start. Bounded
# so a corrupt day_key cannot turn the walk into an unbounded scan.
MAX_STREAK_LOOKBACK_DAYS: Final = 400

REVIEW_BATCH_TYPE: Final = "review_batch"
REVIEW_EVENT_TYPE: Final = "review"
MARK_KNOWN_TYPE: Final = "mark_known"
MINING_TYPE: Final = "mining"

# --- D5 sources. Names kept in step with their writers rather than imported:
# session_tools imports the envelope machinery and mcp_server imports the MCP
# SDK, and a letter renderer must not drag either in behind it (same rule as
# STUDY_MINUTES_PER_DAY above). If a name changes, it changes in both places.
ERROR_EVENT_TYPE: Final = "error_logged"  # session_tools.ERROR_EVENT
PROBE_BATTERY_TYPE: Final = "probe_battery"  # mcp_server.PROBE_EVENT_TYPE

#: Severities, worst first — the order they are reported in. session_tools.SEVERITIES.
ERROR_SEVERITIES: Final[tuple[str, ...]] = ("high", "medium", "low")

#: Coverage bands, best first. Schema enum (docs/db-schema.md) and
#: session_tools.COVERAGE_BANDS. Fixed order, so a letter never reorders on
#: dict-iteration luck.
COVERAGE_BAND_ORDER: Final[tuple[str, ...]] = (">=95", "80-95", "<80")

#: How many repeat patterns / task types the letter names. A museum tour, not an
#: inventory: past three, prose stops teaching and starts listing.
MAX_ERROR_PATTERNS: Final = 3
MAX_TASK_TYPES: Final = 3

#: Cap on any label quoted into the letter. Upstream caps ``pattern`` at 2 000
#: characters, which is a paragraph, not a label.
MAX_LABEL_CHARS: Final = 60

#: Payload key under which the write tools record which fields arrived enveloped.
UNTRUSTED_KEY: Final = "untrusted"

# Payload keys an event may carry a running known-set total under. None of the
# read-only tools write one today; when something does, the delta is used as a
# cross-check on the mark_known count instead of being ignored.
_KNOWN_TOTAL_KEYS: Final = ("known_total", "known_count", "known_words_total")

_WEEK_PATTERN: Final = re.compile(r"^(?P<year>\d{4})-[Ww](?P<week>\d{1,2})$")
_GENERATED_PATTERN: Final = re.compile(
    r"^generated\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE
)
_TRUTHY: Final = frozenset({"true", "yes", "on", "1"})


class SenseiLetterError(RuntimeError):
    """Raised when a letter cannot be computed or written."""


# ---------------------------------------------------------------------------
# Week arithmetic
# ---------------------------------------------------------------------------


def parse_week_label(label: str) -> tuple[int, int]:
    """``"2026-W34"`` -> ``(2026, 34)``. Raises on anything else."""
    match = _WEEK_PATTERN.match(label.strip())
    if match is None:
        raise SenseiLetterError(
            f"{label!r} is not an ISO week label; expected e.g. '2026-W34'."
        )
    year = int(match["year"])
    week = int(match["week"])
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise SenseiLetterError(
            f"{label!r} is not a week that exists: {exc}."
        ) from exc
    return year, week


def week_label(iso_year: int, iso_week: int) -> str:
    """``(2026, 34)`` -> ``"2026-W34"`` (week always two digits)."""
    return f"{iso_year:04d}-W{iso_week:02d}"


def week_bounds(iso_year: int, iso_week: int) -> tuple[date, date]:
    """Monday and Sunday of an ISO week."""
    try:
        monday = date.fromisocalendar(iso_year, iso_week, 1)
    except ValueError as exc:
        raise SenseiLetterError(
            f"{week_label(iso_year, iso_week)} is not a week that exists: {exc}."
        ) from exc
    return monday, monday + timedelta(days=6)


def current_week(today: date | None = None) -> tuple[int, int]:
    """ISO year and week containing ``today`` (defaults to the local date)."""
    day = date.today() if today is None else today
    calendar = day.isocalendar()
    return calendar.year, calendar.week


# ---------------------------------------------------------------------------
# Payload coercion
# ---------------------------------------------------------------------------


def _payload(raw: object) -> dict[str, Any]:
    """Decode an event payload, or ``{}``.

    A payload that is not readable JSON is not an error here: the event still
    happened, and a letter that refuses to render because one row is malformed
    would be worse than a letter that counts that row as "no extras".
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, (str, bytes, bytearray)):
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _number(value: object) -> float | None:
    """A non-negative number from a free-form payload field, else ``None``.

    Matches ``mcp_server._minutes``: ``True`` is not 1, ``"45"`` is 45, "about an
    hour" is unusable, and a negative figure is refused rather than subtracted.
    """
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


def _count_from(payload: dict[str, Any], keys: Iterable[str]) -> int | None:
    """First usable count under ``keys``; a list counts by its length."""
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, (list, tuple)):
            return len(value)
        number = _number(value)
        if number is not None:
            return int(number)
    return None


def _clean_label(value: object, *, limit: int = MAX_LABEL_CHARS) -> str | None:
    """A short single-line label from the log, or ``None`` if there isn't one.

    Whitespace is collapsed because a label carrying a newline would break the
    paragraph it lands in, and the result is truncated because upstream caps
    these fields at paragraph length, not label length.
    """
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_untrusted(payload: dict[str, Any], field_name: str) -> bool:
    """Did ``field_name`` arrive through the untrusted-data envelope?

    The write tools record provenance per field under ``payload['untrusted']``.
    An unreadable or absent record is read as *untrusted* rather than trusted:
    the letter quotes a label only when the log positively says nobody else
    wrote it, so a payload shape this module does not recognise costs a quote,
    never a leak.
    """
    provenance = payload.get(UNTRUSTED_KEY)
    if provenance is None:
        return False
    if not isinstance(provenance, dict):
        return True
    return field_name in provenance


def _percent(part: int, whole: int) -> int:
    """``part`` as a whole-number percentage of ``whole`` (0 when ``whole`` is 0)."""
    return 0 if whole <= 0 else int(round(100 * part / whole))


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErrorMuseum:
    """The week's ``error_logged`` events, reduced to what prose can stand on.

    ``patterns`` holds the quotable repeat offenders, worst first, already
    capped at :data:`MAX_ERROR_PATTERNS`. ``unquotable`` counts mistakes whose
    pattern came in enveloped (media-derived) — they are real mistakes and are
    counted, but their text is not repeated into the vault. ``patternless``
    counts mistakes logged with no usable pattern at all: an anecdote rather
    than a lesson, and worth saying so.
    """

    total: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    patterns: tuple[tuple[str, int], ...] = ()
    unquotable: int = 0
    patternless: int = 0

    @property
    def severity_label(self) -> str:
        """``"1 high, 4 medium"`` over the severities actually recorded."""
        return ", ".join(
            f"{self.by_severity[name]} {name}"
            for name in ERROR_SEVERITIES
            if self.by_severity.get(name)
        )


@dataclass(frozen=True, slots=True)
class ThreadState:
    """Unresolved lesson threads as they stood at the end of the week.

    "Open" is evaluated **as of the week's end**, not as of now: a thread
    answered three weeks later must not silently rewrite this week's letter.
    ``oldest_open_day`` is the local day part of the oldest still-open thread's
    ``created_ts``.
    """

    opened: int = 0
    resolved: int = 0
    still_open: int = 0
    oldest_open_day: date | None = None
    lessons_with_open: int = 0

    @property
    def eventful(self) -> bool:
        """Did anything happen to a thread *this week*?

        Deliberately excludes ``still_open``: an old thread rotting through a
        week with no study in it does not make that week busy, and the quiet
        opening already says the useful thing.
        """
        return bool(self.opened or self.resolved)


@dataclass(frozen=True, slots=True)
class BandResult:
    """One coverage band's slice of the week's observations."""

    band: str
    total: int
    unassisted: int

    @property
    def label(self) -> str:
        return f"{self.band} — {self.unassisted} of {self.total} unassisted"


@dataclass(frozen=True, slots=True)
class ProbeResults:
    """The week's ``observation`` rows: the unassisted pass-rate series.

    ``battery_logged`` records whether a ``probe_battery`` event landed in the
    same week, because that is the specific thing the D6 gate looks for — a
    week of ordinary observations is not a probe battery.
    """

    total: int = 0
    unassisted: int = 0
    bands: tuple[BandResult, ...] = ()
    task_types: tuple[str, ...] = ()
    rubric_versions: tuple[str, ...] = ()
    battery_logged: bool = False

    @property
    def unassisted_percent(self) -> int:
        return _percent(self.unassisted, self.total)


@dataclass(frozen=True, slots=True)
class WeekStats:
    """One ISO week reduced to the figures a letter can stand on.

    ``streak`` counts consecutive study days ending at ``streak_through`` — the
    most recent study day at or before the week's end. A quiet Sunday therefore
    does not zero a six-day streak; it just dates it.

    ``known_delta`` is the change in a logged known-set total across the week, or
    ``None`` when nothing in the log carries one. It is a cross-check on
    ``new_known_words``, not a replacement: whichever is larger wins there, so a
    week whose marks were made outside Katagiri is not reported as zero.
    """

    iso_year: int
    iso_week: int
    start_day: date
    end_day: date
    study_days: int
    streak: int
    streak_through: date | None
    reviews_total: int
    reviews_batched: int
    reviews_individual: int
    new_known_words: int
    known_delta: int | None
    minutes_total: int
    items_mined: int
    event_count: int
    extras: dict[str, Any] = field(default_factory=dict)
    # D5 additions. Defaulted, so a caller that builds a WeekStats by hand from
    # the A9 figures alone still gets a letter — one that simply has nothing to
    # say about errors, threads or probes.
    errors: ErrorMuseum = field(default_factory=ErrorMuseum)
    threads: ThreadState = field(default_factory=ThreadState)
    probes: ProbeResults = field(default_factory=ProbeResults)

    @property
    def label(self) -> str:
        return week_label(self.iso_year, self.iso_week)

    @property
    def is_quiet(self) -> bool:
        """True when the logs have nothing to say about this week.

        A mistake logged or a performance scored is study, so either one is
        enough to make the week loud — checked before the ``event_count`` test,
        because those two live in their own tables and a hand-seeded fixture (or
        a future writer) must not be able to produce a week that has
        observations *and* reads as empty.
        """
        if self.errors.total or self.probes.total or self.threads.eventful:
            return False
        return self.event_count == 0 or (
            self.study_days == 0
            and self.reviews_total == 0
            and self.new_known_words == 0
            and self.minutes_total == 0
            and self.items_mined == 0
        )

    @property
    def hours_label(self) -> str:
        hours, minutes = divmod(self.minutes_total, 60)
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"

    def table_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for the stats table.

        Extension seam: D5 appends rows here (clocks, coverage, media) instead of
        reshaping the renderer.
        """
        streak = "—" if self.streak == 0 else f"{self.streak} day(s)"
        if self.streak and self.streak_through is not None:
            streak = f"{streak}, through {self.streak_through.isoformat()}"
        rows = [
            ("Study days", f"{self.study_days} of 7"),
            ("Streak", streak),
            ("Reviews", str(self.reviews_total)),
            ("New known words", str(self.new_known_words)),
            ("Time studied", self.hours_label),
            ("Items mined", str(self.items_mined)),
        ]
        if self.known_delta is not None:
            rows.append(("Known-set change", f"{self.known_delta:+d}"))
        return rows


def _study_day_minutes(
    conn: sqlite3.Connection, first_day: str, last_day: str
) -> dict[str, float]:
    """Minutes per ``day_key`` from ``study_session`` events in the range."""
    minutes_by_day: dict[str, float] = {}
    for row in conn.execute(
        "SELECT day_key, payload FROM event "
        "WHERE type = ? AND day_key BETWEEN ? AND ?",
        (events.STUDY_LOG_TYPE, first_day, last_day),
    ):
        minutes = _number(_payload(row["payload"]).get("minutes"))
        if minutes is None:
            continue
        key = str(row["day_key"])
        minutes_by_day[key] = minutes_by_day.get(key, 0.0) + minutes
    return minutes_by_day


def _qualifying_days(
    conn: sqlite3.Connection, first_day: str, last_day: str
) -> set[str]:
    """Day keys in the range that count as study days (stop-gate definition)."""
    qualifying = {
        day
        for day, total in _study_day_minutes(conn, first_day, last_day).items()
        if total >= STUDY_MINUTES_PER_DAY
    }
    artifact_types = sorted(ARTIFACT_EVENT_TYPES)
    placeholders = ", ".join("?" * len(artifact_types))
    qualifying |= {
        str(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT day_key FROM event "
            f"WHERE type IN ({placeholders}) AND day_key BETWEEN ? AND ?",
            (*artifact_types, first_day, last_day),
        )
    }
    return qualifying


def _streak(conn: sqlite3.Connection, end: date) -> tuple[int, date | None]:
    """Consecutive study days ending at the last study day on or before ``end``.

    The lookback covers the streak window plus the week itself, and is bounded by
    :data:`MAX_STREAK_LOOKBACK_DAYS` so this can never turn into a full scan.
    """
    first = end - timedelta(days=MAX_STREAK_LOOKBACK_DAYS)
    qualifying = _qualifying_days(conn, first.isoformat(), end.isoformat())
    if not qualifying:
        return 0, None

    # Find where the streak ends: the newest study day at or before ``end``.
    anchor: date | None = None
    cursor = end
    while cursor >= first:
        if cursor.isoformat() in qualifying:
            anchor = cursor
            break
        cursor -= timedelta(days=1)
    if anchor is None:
        return 0, None

    length = 0
    cursor = anchor
    while cursor >= first and cursor.isoformat() in qualifying:
        length += 1
        cursor -= timedelta(days=1)
    return length, anchor


def _known_total_at(
    conn: sqlite3.Connection, *, before_day: str | None, within: tuple[str, str] | None
) -> int | None:
    """Newest logged known-set total in a window, or ``None`` if none exists."""
    if within is not None:
        sql = "SELECT payload FROM event WHERE day_key BETWEEN ? AND ? ORDER BY id DESC"
        params: tuple[Any, ...] = within
    else:
        sql = "SELECT payload FROM event WHERE day_key < ? ORDER BY id DESC"
        params = (before_day,)
    for row in conn.execute(sql, params):
        total = _count_from(_payload(row["payload"]), _KNOWN_TOTAL_KEYS)
        if total is not None:
            return total
    return None


def _error_museum(payloads: Iterable[dict[str, Any]]) -> ErrorMuseum:
    """Fold this week's ``error_logged`` payloads into an :class:`ErrorMuseum`.

    Takes payloads rather than a connection because the week's events are
    already in hand: the museum is a second reading of the same rows, not a
    second query.
    """
    total = 0
    by_severity: dict[str, int] = {}
    counts: dict[str, int] = {}
    unquotable = 0
    patternless = 0

    for payload in payloads:
        total += 1
        severity = _clean_label(payload.get("severity"), limit=16)
        if severity in ERROR_SEVERITIES:
            # ``severity`` is a closed enum upstream; anything else is not
            # counted under a name it does not have.
            by_severity[str(severity)] = by_severity.get(str(severity), 0) + 1
        pattern = _clean_label(payload.get("pattern"))
        if pattern is None:
            patternless += 1
        elif _is_untrusted(payload, "pattern"):
            unquotable += 1
        else:
            counts[pattern] = counts.get(pattern, 0) + 1

    # Repeats first, then alphabetical: two patterns seen twice must not swap
    # places between renders of the same week.
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return ErrorMuseum(
        total=total,
        by_severity=by_severity,
        patterns=tuple(ranked[:MAX_ERROR_PATTERNS]),
        unquotable=unquotable,
        patternless=patternless,
    )


def _day_part(stamp: object) -> str | None:
    """The ``YYYY-MM-DD`` head of an ISO-UTC timestamp, or ``None``.

    ``observation.ts`` and ``lesson_unresolved.created_ts`` are UTC to the
    second; ``event.day_key`` is a *local* day. Aligning the two means taking
    the UTC date, so a thread created just before local midnight can land in the
    neighbouring week. Accepted rather than papered over: the alternative is
    guessing a zone that was never recorded on those rows, and a letter that
    guesses is worse than one that is a few hours coarse at the boundary.
    """
    if not isinstance(stamp, str) or len(stamp) < 10:
        return None
    head = stamp[:10]
    try:
        date.fromisoformat(head)
    except ValueError:
        return None
    return head


def _thread_state(
    conn: sqlite3.Connection, first_day: str, last_day: str
) -> ThreadState:
    """Unresolved lesson threads as of ``last_day``.

    Only threads created on or before the week's end are considered, and a
    thread resolved *after* the week's end still counts as open here — that is
    what makes a past week's letter reproducible.
    """
    opened = 0
    resolved = 0
    still_open = 0
    oldest: str | None = None
    lessons: set[str] = set()

    for row in conn.execute(
        "SELECT lesson_id, created_ts, resolved_ts FROM lesson_unresolved"
    ):
        created = _day_part(row["created_ts"])
        if created is None or created > last_day:
            continue
        closed = _day_part(row["resolved_ts"])
        if first_day <= created <= last_day:
            opened += 1
        if closed is not None and first_day <= closed <= last_day:
            resolved += 1
        if closed is None or closed > last_day:
            still_open += 1
            lessons.add(str(row["lesson_id"]))
            if oldest is None or created < oldest:
                oldest = created

    return ThreadState(
        opened=opened,
        resolved=resolved,
        still_open=still_open,
        oldest_open_day=None if oldest is None else date.fromisoformat(oldest),
        lessons_with_open=len(lessons),
    )


def _probe_results(
    conn: sqlite3.Connection,
    first_day: str,
    last_day: str,
    *,
    battery_logged: bool,
) -> ProbeResults:
    """The week's ``observation`` rows, banded.

    ``unassisted`` is the pass-rate numerator the D6 gate reads (spec US5): an
    assisted production is a different observation, not a slightly worse one, so
    it is counted separately rather than discounted.
    """
    total = 0
    unassisted_total = 0
    per_band: dict[str, list[int]] = {}
    task_counts: dict[str, int] = {}
    versions: set[str] = set()

    for row in conn.execute(
        "SELECT task_type, unassisted, coverage_band, rubric_version "
        "FROM observation WHERE substr(ts, 1, 10) BETWEEN ? AND ?",
        (first_day, last_day),
    ):
        total += 1
        unassisted = 1 if row["unassisted"] else 0
        unassisted_total += unassisted
        band = _clean_label(row["coverage_band"], limit=8)
        if band is not None:
            slot = per_band.setdefault(band, [0, 0])
            slot[0] += 1
            slot[1] += unassisted
        task = _clean_label(row["task_type"])
        if task is not None:
            task_counts[task] = task_counts.get(task, 0) + 1
        version = _clean_label(row["rubric_version"], limit=24)
        if version is not None:
            versions.add(version)

    # Schema order first (best band first), then anything the enum does not
    # cover, alphabetically — a band the CHECK constraint forbids can only get
    # here through a hand-written row, and dropping it silently would hide it.
    ordered = [band for band in COVERAGE_BAND_ORDER if band in per_band]
    ordered += sorted(band for band in per_band if band not in COVERAGE_BAND_ORDER)
    ranked_tasks = sorted(task_counts.items(), key=lambda pair: (-pair[1], pair[0]))

    return ProbeResults(
        total=total,
        unassisted=unassisted_total,
        bands=tuple(
            BandResult(band=band, total=per_band[band][0], unassisted=per_band[band][1])
            for band in ordered
        ),
        task_types=tuple(name for name, _ in ranked_tasks[:MAX_TASK_TYPES]),
        rubric_versions=tuple(sorted(versions)),
        battery_logged=battery_logged,
    )


def compute_week_stats(
    conn: sqlite3.Connection,
    iso_year: int | None = None,
    iso_week: int | None = None,
    *,
    today: date | None = None,
) -> WeekStats:
    """Reduce one ISO week of the event log to a :class:`WeekStats`.

    With no week given, the week containing ``today`` (default: the local date)
    is used. Reads only — computing a letter never writes anything.

    Counting rules, all of them deliberate:

    * **study_days** — a day with ``study_session`` minutes summing to >= 10, or
      any durable study artifact (a review, a mark, a mining or lesson-close
      event). Same rule as the stop gate.
    * **reviews_total** — each ``review_batch`` contributes its
      ``payload.reviews`` count (a list counts by length); a batch whose count is
      unreadable still contributes 1, because the batch happened. Each individual
      ``review`` event contributes 1.
    * **new_known_words** — distinct items marked known this week, plus any
      unattributed ``mark_known`` events; raised to ``known_delta`` when a logged
      known-set total grew by more than that.
    * **minutes_total / items_mined** — summed from usable payload fields only.
      ``mining`` events without a count contribute 1 item each.
    * **errors** — every ``error_logged`` event in the week, grouped by pattern
      and severity (D5).
    * **threads / probes** — the other two append-only logs, read for the same
      week: open lesson threads as of the week's end, and the week's
      rubric-scored observations (D5).
    """
    if (iso_year is None) != (iso_week is None):
        raise SenseiLetterError(
            "compute_week_stats needs both iso_year and iso_week, or neither."
        )
    if iso_year is None or iso_week is None:
        iso_year, iso_week = current_week(today)

    start, end = week_bounds(iso_year, iso_week)
    first_day, last_day = start.isoformat(), end.isoformat()

    rows = conn.execute(
        "SELECT type, item_id, payload FROM event "
        "WHERE day_key BETWEEN ? AND ? ORDER BY id",
        (first_day, last_day),
    ).fetchall()

    reviews_batched = 0
    reviews_individual = 0
    minutes_total = 0.0
    items_mined = 0
    known_items: set[str] = set()
    known_unattributed = 0
    error_payloads: list[dict[str, Any]] = []
    battery_logged = False

    for row in rows:
        kind = str(row["type"])
        payload = _payload(row["payload"])
        if kind == ERROR_EVENT_TYPE:
            error_payloads.append(payload)
        elif kind == PROBE_BATTERY_TYPE:
            battery_logged = True
        if kind == REVIEW_BATCH_TYPE:
            count = _count_from(payload, ("reviews", "count", "n"))
            reviews_batched += 1 if count is None else count
        elif kind == REVIEW_EVENT_TYPE:
            reviews_individual += 1
        elif kind == MARK_KNOWN_TYPE:
            item_id = row["item_id"]
            if item_id:
                known_items.add(str(item_id))
            else:
                known_unattributed += 1
        if kind == events.STUDY_LOG_TYPE:
            minutes = _number(payload.get("minutes"))
            if minutes is not None:
                minutes_total += minutes
            mined = _count_from(payload, ("items_mined",))
            items_mined += mined or 0
        elif kind == MINING_TYPE:
            mined = _count_from(payload, ("items_mined", "count", "items"))
            items_mined += 1 if mined is None else mined

    qualifying = _qualifying_days(conn, first_day, last_day)
    study_days = sum(
        1
        for offset in range(7)
        if (start + timedelta(days=offset)).isoformat() in qualifying
    )
    streak, streak_through = _streak(conn, end)

    marks = len(known_items) + known_unattributed
    before = _known_total_at(conn, before_day=first_day, within=None)
    inside = _known_total_at(conn, before_day=None, within=(first_day, last_day))
    known_delta = (
        inside - before if before is not None and inside is not None else None
    )
    new_known_words = max(marks, known_delta) if known_delta is not None else marks

    return WeekStats(
        iso_year=iso_year,
        iso_week=iso_week,
        start_day=start,
        end_day=end,
        study_days=study_days,
        streak=streak,
        streak_through=streak_through,
        reviews_total=reviews_batched + reviews_individual,
        reviews_batched=reviews_batched,
        reviews_individual=reviews_individual,
        new_known_words=new_known_words,
        known_delta=known_delta,
        minutes_total=int(round(minutes_total)),
        items_mined=items_mined,
        event_count=len(rows),
        errors=_error_museum(error_payloads),
        threads=_thread_state(conn, first_day, last_day),
        probes=_probe_results(conn, first_day, last_day, battery_logged=battery_logged),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _opening(stats: WeekStats) -> str:
    if stats.is_quiet:
        return (
            f"A quiet week — the log shows nothing for "
            f"{stats.start_day.isoformat()} through {stats.end_day.isoformat()}. "
            "That is information, not a verdict: quiet weeks happen to everyone "
            "who studies for years rather than for a month. お帰りなさい — pick "
            "one small thing (ten minutes, one deck, one episode) and the "
            "next letter will have something to count."
        )
    if stats.study_days >= 6:
        opening = "Almost every day this week. That is the rare kind of week."
    elif stats.study_days >= 4:
        opening = "A solid week — most days had study in them."
    elif stats.study_days >= 2:
        opening = "A lighter week, and still not an empty one."
    else:
        opening = "A thin week. One day of contact is more than none."
    return (
        f"{opening} You studied on {stats.study_days} of 7 days "
        f"({stats.hours_label} logged), お疲れさま."
    )


def _middle(stats: WeekStats) -> str | None:
    if stats.is_quiet:
        return None
    parts: list[str] = []
    if stats.reviews_total:
        detail = ""
        if stats.reviews_batched and stats.reviews_individual:
            detail = (
                f" ({stats.reviews_batched} in batches, "
                f"{stats.reviews_individual} one at a time)"
            )
        parts.append(f"{stats.reviews_total} reviews{detail}")
    if stats.new_known_words:
        parts.append(f"{stats.new_known_words} words crossed into 分かる")
    if stats.items_mined:
        parts.append(f"{stats.items_mined} items mined from real material")
    if not parts:
        return (
            "No reviews, no new words, no mining — but the days were logged, so "
            "something was happening. Next week, let one of those days leave a "
            "trace: even five cards is a trace."
        )
    body = parts[0] if len(parts) == 1 else f"{'; '.join(parts[:-1])}; and {parts[-1]}"
    return f"What the log actually holds: {body}."


def _errors(stats: WeekStats) -> str | None:
    """The error museum: what the week's mistakes had in common.

    Empty state is a nudge rather than silence, on the same reasoning as
    :func:`_middle`'s "no reviews" branch: on a week that had study in it, an
    empty museum usually means the tool went unused, and that is the fixable
    thing. A quiet week skips the paragraph entirely — nagging an empty week is
    how a teacher loses a learner.
    """
    if stats.is_quiet:
        return None
    museum = stats.errors
    if museum.total == 0:
        return (
            "Nothing in the error museum this week. That is not the same as no "
            "mistakes — an unlogged one is simply one you get to make again, so "
            "let the next one get written down while it is still warm."
        )

    severities = museum.severity_label
    lead = f"{museum.total} mistake(s) logged"
    lead += f" ({severities})." if severities else "."

    parts = [lead]
    if museum.patterns:
        named = "; ".join(
            f"{pattern} ×{count}" if count > 1 else pattern
            for pattern, count in museum.patterns
        )
        repeats = any(count > 1 for _, count in museum.patterns)
        parts.append(
            f"The museum's current exhibits: {named}."
            + (
                " A pattern that shows up twice is not bad luck; it is the next "
                "drill, already written."
                if repeats
                else ""
            )
        )
    if museum.unquotable:
        parts.append(
            f"{museum.unquotable} came in from media-derived text and are "
            "counted here, not quoted — this letter does not repeat text it "
            "cannot vouch for."
        )
    if museum.patternless:
        parts.append(
            f"{museum.patternless} arrived without a pattern, which leaves it "
            "an anecdote; the pattern is the part a later drill can group by."
        )
    return " ".join(parts)


def _unresolved(stats: WeekStats) -> str | None:
    """Open lesson threads — the "what you're avoiding" paragraph.

    Counts only, never the thread text: see the module docstring's SECRETS note.
    Nothing at all to report means no paragraph, because an empty thread list is
    a good and unremarkable state, not a finding.
    """
    if stats.is_quiet:
        return None
    threads = stats.threads
    if not (threads.opened or threads.resolved or threads.still_open):
        return None

    parts: list[str] = []
    if threads.opened or threads.resolved:
        movement: list[str] = []
        if threads.opened:
            movement.append(f"{threads.opened} new question(s) went unanswered")
        if threads.resolved:
            movement.append(f"{threads.resolved} older one(s) got answered")
        parts.append(f"Threads this week: {', and '.join(movement)}.")

    if threads.still_open:
        where = (
            f" across {threads.lessons_with_open} lesson(s)"
            if threads.lessons_with_open > 1
            else ""
        )
        line = (
            f"{threads.still_open} question(s) are still open{where} as of "
            f"{stats.end_day.isoformat()}"
        )
        if threads.oldest_open_day is not None:
            age = (stats.end_day - threads.oldest_open_day).days
            line += (
                f", the oldest since {threads.oldest_open_day.isoformat()} "
                f"({age} day(s))"
            )
        parts.append(
            line + ". `lessons(unresolved_only=True)` has the actual questions; "
            "an unanswered one compounds, an answered one becomes a lesson."
        )
    else:
        parts.append("Nothing is left open. That is a clean desk, and it is rare.")
    return " ".join(parts)


def _probes(stats: WeekStats) -> str | None:
    """Probe and observation results: the unassisted pass-rate series.

    Named as a gap rather than skipped when the week scored nothing, because the
    D6 gate reads this series and a silent gap in it is exactly the failure mode
    the gate exists to catch.
    """
    if stats.is_quiet:
        return None
    probes = stats.probes
    if probes.total == 0:
        return (
            "No rubric-scored observations this week, so the unassisted "
            "pass-rate series has a gap here. One probe battery says more than a "
            "week of feeling like it went well — and the gate reads the series, "
            "not the feeling."
        )

    parts = [
        f"Scored performances: {probes.total}, "
        f"{probes.unassisted} of them unassisted "
        f"({probes.unassisted_percent}%)."
    ]
    if probes.bands:
        parts.append(
            "By coverage band: "
            + "; ".join(band.label for band in probes.bands)
            + "."
        )
    if len(probes.bands) < 2:
        parts.append(
            "All of it inside one coverage band — a pass-rate needs at least "
            "two before it means anything about difficulty."
        )
    if probes.task_types:
        parts.append(f"Tasks: {', '.join(probes.task_types)}.")
    if probes.rubric_versions:
        versions = ", ".join(probes.rubric_versions)
        parts.append(f"Scored against rubric {versions}.")
    parts.append(
        "A probe battery is on the log for this week."
        if probes.battery_logged
        else "No probe battery this week — these were ordinary observations."
    )
    return " ".join(parts)


def _closing(stats: WeekStats) -> str:
    if stats.is_quiet:
        return "Until next week — また来週."
    if stats.streak >= 7:
        streak_line = (
            f"A {stats.streak}-day streak stands behind this week. Protect it by "
            "letting a bad day be a ten-minute day, not a zero."
        )
    elif stats.streak >= 3:
        streak_line = (
            f"{stats.streak} days in a row as of "
            f"{stats.streak_through.isoformat() if stats.streak_through else 'week end'}"
            ". Streaks are scaffolding, not the building — but keep this one."
        )
    elif stats.streak:
        streak_line = (
            "The streak is short right now. Short is where every long one "
            "started."
        )
    else:
        streak_line = "No streak yet this week. Tomorrow starts one."
    return f"{streak_line} また来週 — see you next week. 先生"


#: Paragraph builders, in order. Each returns a paragraph or ``None`` to skip.
#: Extension seam: append a builder, do not edit :func:`render_letter`.
#:
#: The D5 three sit between what the week held and the sign-off, worst news
#: first: mistakes, then the questions still open, then the scored evidence.
#: That is the order a teacher would use — the museum is the most actionable, the
#: probe record is the most abstract, and neither belongs after "また来週".
BODY_SECTIONS: Final[tuple[Callable[[WeekStats], str | None], ...]] = (
    _opening,
    _middle,
    _errors,
    _unresolved,
    _probes,
    _closing,
)


def _frontmatter(stats: WeekStats) -> list[str]:
    return [
        "---",
        f"schema: {FRONTMATTER_SCHEMA}",
        f"type: {FRONTMATTER_TYPE}",
        f'title: "Week {stats.iso_week}, {stats.iso_year} — sensei letter"',
        f"week: {stats.label}",
        "generated: true",
        "---",
    ]


def render_letter(stats: WeekStats) -> str:
    """Render the letter as markdown. Deterministic: same stats, same bytes.

    The ``generated: true`` key in the frontmatter is load-bearing, not
    decoration: :func:`write_letter` refuses to overwrite any letter that lacks
    it, which is what keeps a hand-written note in ``80-progress/`` safe.
    """
    lines = _frontmatter(stats)
    lines.append("")
    lines.append(f"# Week {stats.iso_week}, {stats.iso_year} — sensei letter")
    lines.append("")
    lines.append(
        f"*{stats.start_day.isoformat()} – {stats.end_day.isoformat()}. "
        "Written from the logs only — events, observations, open threads.*"
    )
    lines.append("")

    for section in BODY_SECTIONS:
        paragraph = section(stats)
        if paragraph:
            lines.append(paragraph)
            lines.append("")

    lines.append("## Numbers")
    lines.append("")
    lines.append("| Metric | This week |")
    lines.append("| --- | --- |")
    for label, value in stats.table_rows():
        lines.append(f"| {label} | {value} |")
    lines.append("")
    lines.append(
        f"*Generated by Katagiri from {stats.event_count} logged event(s). "
        "Edits here are not read back; correct the log, not the letter.*"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def letter_filename(stats_or_label: WeekStats | str) -> str:
    """``2026-W34-sensei-letter.md`` for a week label or a stats object."""
    label = (
        stats_or_label.label
        if isinstance(stats_or_label, WeekStats)
        else stats_or_label
    )
    return f"{label}{LETTER_SUFFIX}"


def is_generated_letter(text: str) -> bool:
    """Does this markdown carry ``generated: true`` in its frontmatter?

    Only the frontmatter block counts. A body line saying ``generated: true``
    proves nothing about who wrote the file, and treating it as permission would
    be a way to talk Katagiri into clobbering a hand-written note.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return False
        match = _GENERATED_PATTERN.match(stripped)
        if match is not None:
            value = match["value"].strip().strip("\"'").lower()
            return value in _TRUTHY
    return False


def progress_dir(vault_path: Path | str | None = None) -> Path:
    """``<vault>/80-progress``. The vault comes from config when not given."""
    base = (
        Path(vault_path)
        if vault_path is not None
        else get_config().require_vault_path()
    )
    return base / PROGRESS_DIR_NAME


def write_letter(
    conn: sqlite3.Connection,
    vault_path: Path | str | None = None,
    *,
    iso_year: int | None = None,
    iso_week: int | None = None,
    today: date | None = None,
    tz: str | None = None,
) -> Path:
    """Compute, render and write the week's letter; return its path.

    Refuses to overwrite an existing file unless that file's own frontmatter says
    ``generated: true``. A hand-written progress note is the learner's, and no
    regeneration is worth losing one — the refusal names the file and what to do.

    On success a ``sensei_letter`` event is appended carrying only the week label
    and the file's basename.
    """
    stats = compute_week_stats(conn, iso_year, iso_week, today=today)
    directory = progress_dir(vault_path)
    target = directory / letter_filename(stats)

    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise SenseiLetterError(
                f"{target} exists but could not be read, so Katagiri cannot tell "
                f"whether it was generated: {exc}. Move or delete it by hand."
            ) from exc
        if not is_generated_letter(existing):
            raise SenseiLetterError(
                f"{target} already exists and does not carry 'generated: true' in "
                "its frontmatter, so it is treated as hand-written and left "
                "alone. Rename or move it if you want a fresh letter here."
            )

    body = render_letter(stats)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise SenseiLetterError(f"Could not write the letter to {target}: {exc}") from exc

    events.append_event(
        conn,
        type=LETTER_EVENT_TYPE,
        session_id=f"sensei:{stats.label}",
        tz=tz,
        payload={"week": stats.label, "path": target.name},
    )
    logger.info("Wrote sensei letter for %s.", stats.label)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.sensei_letter",
        description="Write the weekly sensei letter from the event log.",
    )
    parser.add_argument(
        "when",
        nargs="?",
        default="current",
        choices=["current"],
        help="which week to render (only 'current'; use --week for another).",
    )
    parser.add_argument(
        "--week",
        metavar="ISOWEEK",
        help="ISO week to render, e.g. 2026-W34. Overrides 'current'.",
    )
    parser.add_argument(
        "--vault",
        metavar="PATH",
        help="vault root to write into (default: vault_path from config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the letter to stdout; write nothing and log nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.sensei_letter``."""
    from katagiri.db import open_db
    from katagiri.logging_setup import setup_logging

    args = _build_parser().parse_args(argv)
    setup_logging()

    # The letter contains Japanese, and a legacy Windows console is cp1252: without
    # this, printing it raises UnicodeEncodeError instead of showing the letter.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:  # pragma: no branch - always present on 3.12
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")

    year: int | None = None
    week: int | None = None
    try:
        if args.week is not None:
            year, week = parse_week_label(args.week)
    except SenseiLetterError as exc:
        print(f"error: {exc}")
        return 2

    conn = open_db()
    try:
        if args.dry_run:
            stats = compute_week_stats(conn, year, week)
            print(render_letter(stats), end="")
            return 0
        path = write_letter(conn, args.vault, iso_year=year, iso_week=week)
    except SenseiLetterError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        conn.close()

    print(f"wrote {path}")
    return 0


__all__ = [
    "ARTIFACT_EVENT_TYPES",
    "BODY_SECTIONS",
    "COVERAGE_BAND_ORDER",
    "ERROR_EVENT_TYPE",
    "ERROR_SEVERITIES",
    "LETTER_EVENT_TYPE",
    "PROBE_BATTERY_TYPE",
    "PROGRESS_DIR_NAME",
    "STUDY_MINUTES_PER_DAY",
    "BandResult",
    "ErrorMuseum",
    "ProbeResults",
    "SenseiLetterError",
    "ThreadState",
    "WeekStats",
    "compute_week_stats",
    "current_week",
    "is_generated_letter",
    "letter_filename",
    "main",
    "parse_week_label",
    "progress_dir",
    "render_letter",
    "week_bounds",
    "week_label",
    "write_letter",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
