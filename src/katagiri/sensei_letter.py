"""Weekly "sensei letter": a short progress note derived from the event log.

The letter is prose first and numbers second, on purpose. A dashboard makes you
compliant; a teacher noticing what changed makes you a learner. So this module
reads one ISO week out of the ``event`` table, reduces it to a small
:class:`WeekStats`, and renders a warm two-or-three paragraph note plus one
table. Nothing here judges, and nothing here invents: every figure is a count of
something that was actually appended to the log.

**The log is the only source.** No Anki mirror, no vault scraping, no
recomputation of the known set. That keeps the letter reproducible — the same
week always renders the same bytes — and keeps it honest about what it does not
know (a quiet week reads as a quiet week, not as failure).

Phase D (D5) enriches this: error-museum highlights, media notes, input/output
clocks. The two extension seams meant for that are
:meth:`WeekStats.table_rows` and :data:`BODY_SECTIONS` — add a row builder or a
paragraph builder there rather than rewriting :func:`render_letter`.

The study-day rule (``study_session`` minutes summing to >= 10, or any durable
study artifact on that day) is deliberately the *same* rule the stop gate in
:mod:`katagiri.mcp_server` applies, and is replicated here rather than imported:
that module imports the MCP server SDK, and a letter renderer must not drag a
transport dependency in behind it. If the rule changes, it changes in both
places — the constants below name their twin so the grep finds them.

SECRETS: the letter is written into the vault and its path is appended to the
event log. Only aggregate counts and the ISO week reach either, never payload
text, note bodies, or file contents.
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


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


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

    @property
    def label(self) -> str:
        return week_label(self.iso_year, self.iso_week)

    @property
    def is_quiet(self) -> bool:
        """True when the log has nothing to say about this week."""
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

    for row in rows:
        kind = str(row["type"])
        payload = _payload(row["payload"])
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
#: Extension seam for D5: append a builder, do not edit :func:`render_letter`.
BODY_SECTIONS: Final[tuple[Callable[[WeekStats], str | None], ...]] = (
    _opening,
    _middle,
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
        "Written from the event log only.*"
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
    "LETTER_EVENT_TYPE",
    "PROGRESS_DIR_NAME",
    "STUDY_MINUTES_PER_DAY",
    "SenseiLetterError",
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
