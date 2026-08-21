"""``Today.md``: one derived page, assembled from a registry of section builders.

The point of this module is the *registry*, not the page. Phase B knows about
five things — Anki's due count, the study streak, the known-set trend, the
weakest morphs, and where you left off — and each of those is one function that
returns a :class:`Section` or ``None``. Later phases append a builder to
:data:`SECTIONS`; :func:`render_today` never has to be edited again. That is the
same extension seam :mod:`katagiri.sensei_letter` uses for its paragraphs, kept
deliberately identical so there is one pattern to learn rather than two.

**Every section states absence rather than inventing a number.** An empty event
log, an Anki mirror taken before the queue columns existed, an ``ankimorphs``
table that no ingest has created yet — each of those renders a sentence saying
so. A study tool that guesses is worse than one that admits it does not know,
because the learner cannot tell the two apart from the page.

**Writes are confined to ``<vault>/.derived/``** and refuse to overwrite any file
whose frontmatter does not say ``generated: true``. ``.derived`` is already
excluded from vault backups (:mod:`katagiri.backup`), which is the other half of
the same statement: nothing here is a source of truth, and nothing here is
allowed to destroy one.

SECRETS: the page is written into the vault and its path is appended to the event
log. Only aggregate counts, day keys, and study material the learner already has
reach either — never credentials, never file contents from outside the database.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Final

from katagiri import events, known, lesson_memory, sensei_letter
from katagiri.config import get_config
from katagiri.db import resolve_alias
from katagiri.intelligence import DEFAULT_MIN_UNDERSTANDING
from katagiri.logging_setup import get_logger

logger = get_logger("today_export")

DERIVED_DIR_NAME: Final = ".derived"
TODAY_FILENAME: Final = "Today.md"
TODAY_EVENT_TYPE: Final = "today_export"
FRONTMATTER_SCHEMA: Final = 2
FRONTMATTER_TYPE: Final = "derived"
GENERATOR: Final = "katagiri.today_export"

TS_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
DAY_FORMAT: Final = "%Y-%m-%d"

#: Anki queue values. A card is only "due" out of these three; 0 is new and
#: anything negative is suspended or buried, and neither is work the learner owes
#: today. Queue 4 (preview, in a filtered deck) is deliberately not counted: it
#: is a temporary state Anki puts a card in, not a review that is waiting.
QUEUE_REVIEW: Final = 2
QUEUE_LEARNING_SECONDS: Final = 1
QUEUE_LEARNING_DAYS: Final = 3

#: Local hour at which Anki starts a new day. **Assumed, not mirrored.** Anki
#: keeps it in ``col.conf["rollover"]``, a JSON blob :mod:`katagiri.anki_snapshot`
#: does not copy, and 4 is Anki's own default for every collection that has not
#: been reconfigured. An operator who moved their rollover will see a due count
#: that is right except during the hours between their rollover and this one; the
#: honest fix is to mirror ``col.conf``, which is a separate change.
DEFAULT_ROLLOVER_HOUR: Final = 4

#: The earliest ``col.crt`` treated as a real collection start date:
#: ``2006-01-01T00:00:00Z``. Anki did not exist before 2006, so nothing at or
#: below this is a collection that was actually created — it is a corrupt or
#: half-written mirror row. Two separate reasons to screen it rather than compute
#: with it. First, a garbage-but-positive ``crt`` (a truncated value, a
#: milliseconds/seconds mix-up read the wrong way) yields a day index tens of
#: thousands of days high, and every card in the collection then reads as due:
#: the loudest possible wrong answer. Second, a non-positive ``crt`` cannot even
#: be converted — ``datetime.fromtimestamp(crt, tz=timezone.utc)`` raises
#: ``OSError [Errno 22]`` on Windows for a pre-1970 instant, *before* any zone is
#: applied, which would surface as the generic "(failed)" backstop instead of a
#: sentence naming what is wrong.
MIN_PLAUSIBLE_CRT: Final = 1136073600

#: How far past ``now`` a ``col.crt`` may sit and still be believed: one day.
#: A collection cannot have been created in the future, so in principle the bound
#: is ``now``. The slack exists only for the two ways a *real* collection can look
#: slightly ahead of this process's clock — the machine's clock being corrected
#: backwards after Anki stamped ``crt``, and a mirror carried between machines
#: whose clocks disagree. A day is far more than either needs and far less than
#: any corruption produces, so nothing genuine is rejected and nothing corrupt is
#: computed with. The failure this closes is the mirror image of
#: :data:`MIN_PLAUSIBLE_CRT`'s: a ``crt`` stored in **milliseconds** (Anki keeps
#: ``col.crt`` in seconds, but neighbouring Anki columns are milliseconds, so the
#: mix-up is one wrong join away) is ~1000x too large. That is not merely a wrong
#: index — ``datetime.fromtimestamp`` cannot represent the year at all and raises
#: ``OSError [Errno 22]`` on Windows, i.e. it lands in exactly the generic
#: "(failed)" backstop the lower bound was added to prevent. A far-future but
#: representable ``crt`` fails differently and just as badly: the day index goes
#: negative, and the page then reports zero cards due for a collection full of
#: overdue reviews.
MAX_CRT_FUTURE_SLACK_SECONDS: Final = 86_400

#: How far back the known-set trend looks. Two windows, because one alone is
#: either too noisy (7) or too slow to move (30) to mean anything on its own.
TREND_WINDOWS_DAYS: Final = (7, 30)

#: Morphs shown in the weakest-morphs section.
WEAKEST_MORPHS_LIMIT: Final = 8
#: An interval at or above this many days is not "weak" — it is the same
#: threshold ``known_set`` uses to call an Anki card mature.
WEAK_INTERVAL_MAX_DAYS: Final = 21

MORPHS_TABLE: Final = "ankimorphs_morphs"

#: Truncation for the one line of subtitle text a resume pointer carries.
DISPLAYED_TEXT_MAX: Final = 60


class TodayExportError(RuntimeError):
    """Raised when the page cannot be built or written."""


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Section:
    """One rendered block of the page.

    ``key`` is stable and machine-readable; ``heading`` is what the learner sees.
    Keeping them separate means a heading can be reworded without breaking a
    later phase that looks a section up by name.
    """

    key: str
    heading: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TodayContext:
    """Everything a section builder is allowed to read.

    ``day`` is the local calendar day the page is about (day keys in the event
    log are local); ``now`` is the UTC instant it was generated at. Both are
    injected rather than read from the clock inside a builder, so no section
    reaches for the clock — which is what keeps the render independent of *when*
    it runs, and the tests honest.

    ``zone`` is the local zone Anki's day rollover is measured in, ``None``
    meaning the machine's. It is a field rather than a lookup so that "what does
    local mean here" is answerable from the context alone, and so a test can pin
    it instead of pinning the machine.

    One honest caveat on determinism: a context is a *complete* description of the
    render only when ``zone`` is set. Leaving it ``None`` means the day rollover is
    resolved against the machine's zone at render time, so the same context and
    the same database can yield a different Anki due count on a differently
    configured machine. That is the intended default for a personal tool — the
    learner's own zone is the right one — but a test that cares about the boundary
    must pin ``zone`` rather than trusting the field's absence.
    """

    conn: sqlite3.Connection
    day: date
    now: datetime
    zone: tzinfo | None = None

    @property
    def day_key(self) -> str:
        return self.day.strftime(DAY_FORMAT)

    @property
    def stamp(self) -> str:
        return self.now.strftime(TS_FORMAT)


SectionBuilder = Callable[[TodayContext], Section | None]


def section(key: str) -> Callable[[SectionBuilder], SectionBuilder]:
    """Tag a builder with its section key.

    The key lives on the function so :data:`SECTIONS` can be introspected — "what
    does this page contain?" is answerable without calling anything, which is
    what a later phase needs before it decides where to insert itself.
    """

    def decorate(func: SectionBuilder) -> SectionBuilder:
        func.section_key = key  # type: ignore[attr-defined]
        return func

    return decorate


def _missing_table(exc: sqlite3.Error) -> bool:
    """Is this the "the derived table is not there" error, rather than damage?"""
    return isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc)


# ---------------------------------------------------------------------------
# Anki due count
# ---------------------------------------------------------------------------


def _mirror_meta(conn: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        return conn.execute("SELECT * FROM mirror_meta WHERE id = 1").fetchone()
    except sqlite3.Error:
        return None


def _has_queue_columns(conn: sqlite3.Connection) -> bool:
    try:
        columns = {
            str(row[1]) for row in conn.execute('PRAGMA table_info("anki_cards")')
        }
    except sqlite3.Error:  # pragma: no cover - PRAGMA on a live conn does not fail
        return False
    return {"queue", "ctype"} <= columns


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "available": False,
        "count": None,
        "review_due": None,
        "learning_due": None,
        "collection_day": None,
        "snapshot_ts": None,
        "cards_mirrored": None,
        "reason": reason,
        **extra,
    }


def _as_local(moment: datetime, zone: tzinfo | None) -> datetime:
    """``moment`` in local time, at the offset that applied to *that* instant.

    ``astimezone()`` with no argument asks the platform what local time was at
    the instant being converted, so two values on opposite sides of a daylight
    saving transition each get the offset that actually applied to them. Reading
    one ``tzinfo`` up front instead — ``datetime.now().astimezone().tzinfo``, a
    *fixed* offset frozen at call time — pins both values to whichever offset
    happened to be in force when the code ran, and quietly makes the caller's
    result depend on the wall clock as well as on its arguments.
    """
    return moment.astimezone(zone) if zone is not None else moment.astimezone()


def collection_day_index(
    crt: int,
    now: datetime,
    *,
    rollover_hour: int = DEFAULT_ROLLOVER_HOUR,
    zone: tzinfo | None = None,
) -> int:
    """Anki's own "today" for a collection created at ``crt``.

    **Not** ``(now - crt) // 86400``. Anki does not measure elapsed days from the
    clock time the collection happened to be created at. Day zero is ``crt``'s
    *local calendar date*, and the index is the number of local calendar days
    since that date, minus one for as long as ``now`` has not yet reached the
    rollover hour. So the index increments at the rollover hour in local time —
    04:00 by default — not at midnight and not at some arbitrary minute inherited
    from when the collection was made.

    Getting the boundary wrong is not cosmetic. A collection created at midday
    reads one day *behind* under the naive formula for the whole stretch between
    the rollover and that creation hour, which means every card that came due at
    04:00 stays invisible until noon: the learner is told there is nothing to do
    on a morning when there is.

    ``crt``'s own clock time is then discarded, and that is the half which is
    easy to get wrong in the other direction. A collection created at 02:00 — so
    *before* that date's rollover — still belongs to day zero; Anki does not walk
    day zero back to the previous rollover for it (rslib ``days_elapsed``,
    schedv2 ``_daysSinceCreation``). A Katagiri that did would report an index one
    higher than Anki's forever, for every collection made in the small hours,
    which means every card Anki has scheduled for tomorrow is counted as due
    today — the learner is handed work that is not theirs yet, every single day.

    The arithmetic runs on calendar dates rather than on an elapsed duration on
    purpose: a daylight saving transition between day zero and ``now`` moves the
    duration by an hour, which would move a floored day count by a whole day for
    anyone reading the page during the hour after their rollover.

    ``zone`` is the local zone the rollover is expressed in, defaulting to the
    machine's. It is injectable so this is testable without moving the clock.
    Unlike Anki this does not clamp at zero: a mirror whose ``crt`` is in the
    future yields a negative index, which counts nothing as due rather than
    presenting day-zero cards as owed.

    ``crt`` must be a plausible collection start date — see
    :data:`MIN_PLAUSIBLE_CRT` and :data:`MAX_CRT_FUTURE_SLACK_SECONDS`, which
    :func:`anki_due_count` screens against before calling here. A ``crt`` outside
    those bounds is not returned as a number. The first step below,
    ``datetime.fromtimestamp(int(crt), tz=timezone.utc)``, is what raises for such
    a value — ``OSError [Errno 22]`` on Windows for a pre-1970 instant, and the
    same for a value too large to be a year (a ``crt`` stored in milliseconds).
    Note *where* that happens: the conversion from epoch seconds fails before any
    zone is involved, so it raises identically whether ``zone`` is pinned or left
    to the platform, and ``astimezone()`` — which the failure is easy to blame — is
    never reached. That is deliberately left to propagate rather than being turned
    into an index nobody can justify; :func:`anki_due_count` catches it and says
    the mirror cannot answer.
    """
    if not 0 <= rollover_hour <= 23:
        raise TodayExportError(
            "The Anki rollover hour must be an hour of the day (0-23)."
        )
    day_zero = _as_local(
        datetime.fromtimestamp(int(crt), tz=timezone.utc), zone
    ).date()
    now_local = _as_local(now, zone)
    elapsed = (now_local.date() - day_zero).days
    if now_local.hour < rollover_hour:
        # Before today's rollover Anki is still serving yesterday's day index.
        elapsed -= 1
    return elapsed


def anki_due_count(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    zone: tzinfo | None = None,
    rollover_hour: int = DEFAULT_ROLLOVER_HOUR,
) -> dict[str, Any]:
    """How many Anki cards are due, by Anki's own rules, from the mirror.

    Returns ``{available, count, review_due, learning_due, collection_day,
    snapshot_ts, cards_mirrored, reason}``. ``available`` is false — with ``count``
    ``None`` and a ``reason`` — whenever the mirror cannot answer the question:

    * no snapshot has ever been taken;
    * the snapshot predates the ``queue`` / ``ctype`` columns;
    * the mirror carries no ``crt``, or one no real collection could have — too
      early to be Anki (:data:`MIN_PLAUSIBLE_CRT`), later than now
      (:data:`MAX_CRT_FUTURE_SLACK_SECONDS`), or a value that will not convert to a
      date at all — so a day index cannot be dated;
    * *any* mirrored card is missing its ``queue`` — a card whose state is unknown
      is not evidence that it is not due, and SQL would quietly count it as such;
    * the mirror holds no cards at all, which is either a collection that has not
      been snapshotted properly or a rebuild that did not finish, and is
      indistinguishable from both.

    **There is no fallback estimate.** A plausible-looking due count that is
    actually a card count — or a confident ``0`` from an empty table — would send
    the learner to Anki expecting the wrong day's work, or away from it entirely.

    The arithmetic is Anki's: the collection's "today" comes from
    :func:`collection_day_index`, a review card (queue 2) or a day-scheduled
    learning card (queue 3) is due when its ``due`` day index is at or before
    that, and a seconds-scheduled learning card (queue 1) is due when its ``due``
    *epoch second* is at or before now. New (0), suspended (-1), buried (-2, -3)
    and preview (4) cards are not due.

    **The count is scheduler-raw, and on a backlog it exceeds what Anki shows.**
    Anki's deck list caps each deck at its configuration group's daily limits
    (``perDay``), and those live in the deck-config JSON that
    :mod:`katagiri.anki_snapshot` does not mirror — the same unmirrored blob
    :data:`DEFAULT_ROLLOVER_HOUR` is assumed from. So a learner sitting on 300
    matured reviews gets 300 here while Anki offers them 100 today. Neither number
    is wrong: this one is "reviews whose scheduled day has arrived", Anki's is
    "reviews it will hand you today". Applying a limit that is not in the mirror
    would be a guess, so the rendered section names which of the two it is instead
    of showing the larger one as though it were Anki's.
    """
    moment = now or datetime.now(timezone.utc)
    now_epoch = int(moment.timestamp())

    meta = _mirror_meta(conn)
    if meta is None:
        return _unavailable(
            "the Anki mirror has never been snapshotted, so no due count exists "
            "yet; run a snapshot first."
        )

    keys = meta.keys()
    crt = meta["crt"] if "crt" in keys else None
    snapshot_ts = meta["snapshot_ts"] if "snapshot_ts" in keys else None

    if not _has_queue_columns(conn):
        return _unavailable(
            "this mirror was taken before Katagiri recorded card queues, so an "
            "exact due count is unavailable pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
        )
    if crt is None:
        return _unavailable(
            "this mirror carries no collection start date (col.crt), so a due "
            "day index cannot be dated; unavailable pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
        )
    try:
        crt_seconds = int(crt)
    except (TypeError, ValueError):
        crt_seconds = -1
    if crt_seconds < MIN_PLAUSIBLE_CRT:
        # Screened here rather than computed with. A negative crt cannot even be
        # converted to local time on Windows (OSError [Errno 22]), which would
        # reach the render_sections backstop and print a generic "(failed)"
        # heading; and a positive-but-tiny one dates day zero to the 1970s, which
        # makes the day index tens of thousands of days high and reports every
        # card in the collection as due. Both are corruption, and neither is a
        # number worth showing the learner.
        return _unavailable(
            "this mirror's collection start date (col.crt) predates Anki itself, "
            "so it cannot be a real collection start and a due day index cannot "
            "be dated from it; unavailable pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
        )
    if crt_seconds > now_epoch + MAX_CRT_FUTURE_SLACK_SECONDS:
        # The other end of the same guard, and the reason the lower bound alone was
        # not enough. A collection cannot have been created after the moment being
        # asked about, so anything past now (plus the day of clock slack described
        # at MAX_CRT_FUTURE_SLACK_SECONDS) is corruption as surely as a pre-Anki
        # date is. Both of its shapes were reaching the generic "(failed)" backstop
        # or a silent wrong answer: a crt stored in milliseconds is ~1000x too
        # large and is not a representable year at all, so
        # datetime.fromtimestamp raises OSError [Errno 22] on Windows; a
        # far-future but representable crt converts fine and drives the day index
        # negative, so the page reports nothing due for a collection full of
        # overdue reviews.
        return _unavailable(
            "this mirror's collection start date (col.crt) is in the future, so it "
            "cannot be a real collection start and a due day index cannot be dated "
            "from it; unavailable pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
        )

    try:
        collection_day = collection_day_index(
            crt_seconds, moment, rollover_hour=rollover_hour, zone=zone
        )
    except (OSError, OverflowError, ValueError) as exc:
        # Belt and braces behind the two bounds above. Those bounds are what
        # *should* stop an impossible crt; this is here so that if one of them is
        # ever loosened, narrowed, or simply outflanked by a value nobody thought
        # of, the block still degrades to a sentence about the mirror rather than
        # to the render_sections backstop's generic "(failed)" heading, which tells
        # the learner nothing. The raiser is datetime.fromtimestamp, not
        # astimezone: OSError [Errno 22] on Windows, OverflowError or ValueError on
        # other platforms for the same input, so all three name one fault. An
        # impossible rollover_hour raises TodayExportError, which is a caller
        # mistake and deliberately not caught here.
        logger.warning(
            "col.crt could not be converted to a collection day (%s).",
            type(exc).__name__,
        )
        return _unavailable(
            "this mirror's collection start date (col.crt) could not be read as a "
            "date at all, so a due day index cannot be dated from it; unavailable "
            "pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
        )

    try:
        row = conn.execute(
            """
            SELECT
              SUM(CASE WHEN queue = ? AND due <= ? THEN 1 ELSE 0 END) AS reviews,
              SUM(CASE WHEN queue = ? AND due <= ? THEN 1 ELSE 0 END) AS day_learn,
              SUM(CASE WHEN queue = ? AND due <= ? THEN 1 ELSE 0 END) AS sec_learn,
              COUNT(*)                                                AS total,
              SUM(CASE WHEN queue IS NULL THEN 1 ELSE 0 END)          AS unqueued
              FROM anki_cards
            """,
            (
                QUEUE_REVIEW, collection_day,
                QUEUE_LEARNING_DAYS, collection_day,
                QUEUE_LEARNING_SECONDS, now_epoch,
            ),
        ).fetchone()
    except sqlite3.Error as exc:
        return _unavailable(
            "the Anki mirror table could not be read, so no due count exists "
            f"yet ({type(exc).__name__}); run a snapshot first.",
            snapshot_ts=snapshot_ts,
        )

    total = int(row["total"] or 0)
    unqueued = int(row["unqueued"] or 0)
    if unqueued:
        # Not "all of them": one card whose queue is NULL is enough. The SQL
        # above compares NULL = 2, which is NULL, which the CASE reads as false —
        # so a partially-filled mirror would silently report those cards as not
        # due. Missing state is not the same as "nothing owed".
        return _unavailable(
            f"{unqueued} of {total} mirrored card(s) are missing their queue, so "
            "an exact due count is unavailable pending a fresh snapshot.",
            snapshot_ts=snapshot_ts,
            cards_mirrored=total,
        )
    if total == 0:
        # An empty mirror alongside a crt-carrying mirror_meta is exactly what a
        # rebuild that wiped anki_cards and then failed leaves behind, and it is
        # indistinguishable from a collection that genuinely has no cards. "0
        # due" is the more dangerous of the two readings, because it tells the
        # learner to skip Anki. Refuse to pick.
        return _unavailable(
            "the Anki mirror holds no cards, so there is nothing to count from; "
            "run a snapshot and this becomes a real number.",
            snapshot_ts=snapshot_ts,
            cards_mirrored=0,
        )

    review_due = int(row["reviews"] or 0)
    learning_due = int(row["day_learn"] or 0) + int(row["sec_learn"] or 0)
    return {
        "available": True,
        "count": review_due + learning_due,
        "review_due": review_due,
        "learning_due": learning_due,
        "collection_day": collection_day,
        "snapshot_ts": snapshot_ts,
        "reason": None,
        "cards_mirrored": total,
    }


@section("anki_due")
def _due_section(ctx: TodayContext) -> Section:
    result = anki_due_count(ctx.conn, now=ctx.now, zone=ctx.zone)
    if not result["available"]:
        return Section(
            key="anki_due",
            heading="Anki reviews",
            lines=(f"Due count unavailable — {result['reason']}",),
        )

    count = result["count"]
    if count == 0:
        head = "Nothing due right now."
    elif count == 1:
        head = "1 card due."
    else:
        head = f"{count} cards due."
    detail = (
        f"{result['review_due']} review, {result['learning_due']} learning."
    )
    return Section(
        key="anki_due",
        heading="Anki reviews",
        lines=(
            f"{head} {detail}",
            f"*Mirror taken {result['snapshot_ts']}; "
            f"collection day {result['collection_day']}. Scheduler-raw count — "
            "per-deck daily limits are not mirrored, so Anki's own deck list may "
            "offer fewer than this today.*",
        ),
    )


# ---------------------------------------------------------------------------
# Streak
# ---------------------------------------------------------------------------


@section("streak")
def _streak_section(ctx: TodayContext) -> Section:
    """Consecutive study days, by the same rule the stop gate applies.

    The rule itself is not restated here: it is imported from
    :mod:`katagiri.sensei_letter`, which is where it already lives. Two copies of
    "what counts as a study day" would be one copy too many — the letter and this
    page must never disagree about whether yesterday counted.
    """
    try:
        length, through = sensei_letter._streak(ctx.conn, ctx.day)
    except sqlite3.Error:
        return Section(
            key="streak",
            heading="Streak",
            lines=("The event log could not be read, so no streak is claimed.",),
        )

    if length == 0 or through is None:
        return Section(
            key="streak",
            heading="Streak",
            lines=(
                "No streak yet — nothing in the log counts as a study day. "
                "Ten minutes today starts one.",
            ),
        )
    freshness = (
        "through today."
        if through == ctx.day
        else f"through {through.isoformat()} — today is still open."
    )
    return Section(
        key="streak",
        heading="Streak",
        lines=(f"{length} day(s) in a row, {freshness}",),
    )


# ---------------------------------------------------------------------------
# Known-set trend
# ---------------------------------------------------------------------------


def _oldest_logged_total(
    conn: sqlite3.Connection, *, since_day: str, until_day: str
) -> tuple[int, str] | None:
    """The earliest logged known-set total inside ``[since_day, until_day]``.

    Returns the total and the ``day_key`` it was logged on, so the caller can say
    what its baseline actually is. Earliest by *day key* rather than by insertion
    order, so an event backfilled later cannot displace the day it belongs to.
    Bounded on both ends deliberately:
    :func:`sensei_letter._known_total_at` with ``before_day`` walks the log all the
    way back, so a total logged two years ago would be presented as this week's
    starting point. A window with nothing in it yields ``None`` and the section
    says it has no baseline, which is true.

    Which payload keys carry a total is not restated here — it comes from
    :mod:`katagiri.sensei_letter`, so the letter and this page cannot disagree
    about what counts as a logged known-set total.
    """
    for row in conn.execute(
        "SELECT day_key, payload FROM event WHERE day_key BETWEEN ? AND ? "
        "ORDER BY day_key ASC, id ASC",
        (since_day, until_day),
    ):
        total = sensei_letter._count_from(
            sensei_letter._payload(row["payload"]), sensei_letter._KNOWN_TOTAL_KEYS
        )
        if total is not None:
            return total, str(row["day_key"])
    return None


@section("known_trend")
def _known_trend_section(ctx: TodayContext) -> Section:
    """Where the known set stands, and which way it has moved.

    Current size comes from the ``known_set`` view; the baselines come from
    totals other tools have logged. Those are different measurements, so the
    deltas are labelled as coming from the log rather than presented as an exact
    change in the view — and each one names the day its baseline was logged on,
    because "+40 over 30 days" measured against a total from eleven months ago is
    a lie told with a true subtraction.
    """
    try:
        stats = known.known_set_stats(ctx.conn)
    except sqlite3.Error:
        return Section(
            key="known_trend",
            heading="Known set",
            lines=("The known set could not be read right now.",),
        )

    lines = [
        f"{stats['known']} known of {stats['total']} tracked "
        f"({stats['suspect']} flagged suspect)."
    ]

    deltas: list[str] = []
    for window in TREND_WINDOWS_DAYS:
        since = (ctx.day - timedelta(days=window)).strftime(DAY_FORMAT)
        try:
            baseline = _oldest_logged_total(
                ctx.conn, since_day=since, until_day=ctx.day_key
            )
        except sqlite3.Error:
            baseline = None
        if baseline is not None:
            total, logged_on = baseline
            deltas.append(
                f"{stats['known'] - total:+d} in the last {window} days "
                f"(against {total} logged {logged_on})"
            )
    if deltas:
        lines.append(f"Trend against logged totals: {'; '.join(deltas)}.")
    else:
        lines.append(
            "No baseline in the log yet, so no trend is claimed — the first "
            "logged known-set total becomes the reference point."
        )
    return Section(key="known_trend", heading="Known set", lines=tuple(lines))


# ---------------------------------------------------------------------------
# Weakest morphs
# ---------------------------------------------------------------------------


@section("weakest_morphs")
def _weakest_morphs_section(ctx: TodayContext) -> Section:
    """The morphs AnkiMorphs holds at the shortest intervals.

    ``ankimorphs_morphs`` is created on first ingest, not by a migration, so its
    absence is the normal state of a fresh database and is reported as "not
    imported" rather than as an error. A morph with no interval at all is left
    out entirely: not knowing how well something is known is not evidence that it
    is known badly.
    """
    heading = "Weakest morphs"
    try:
        rows = ctx.conn.execute(
            f"""
            SELECT lemma, MIN(lemma_ivl) AS ivl
              FROM {MORPHS_TABLE}
             WHERE lemma_ivl IS NOT NULL AND lemma_ivl < ?
             GROUP BY lemma
             ORDER BY ivl ASC, lemma ASC
             LIMIT ?
            """,
            (WEAK_INTERVAL_MAX_DAYS, WEAKEST_MORPHS_LIMIT),
        ).fetchall()
    except sqlite3.Error as exc:
        if _missing_table(exc):
            return Section(
                key="weakest_morphs",
                heading=heading,
                lines=(
                    "No ankimorphs data yet — run an AnkiMorphs ingest and this "
                    "section fills itself in.",
                ),
            )
        return Section(
            key="weakest_morphs",
            heading=heading,
            lines=("The ankimorphs table could not be read right now.",),
        )

    if not rows:
        return Section(
            key="weakest_morphs",
            heading=heading,
            lines=(
                f"Nothing below {WEAK_INTERVAL_MAX_DAYS} days — either the "
                "ingest is empty or every morph is mature.",
            ),
        )
    listed = ", ".join(f"{row['lemma']} ({row['ivl']}d)" for row in rows)
    return Section(
        key="weakest_morphs",
        heading=heading,
        lines=(f"Shortest intervals first: {listed}.",),
    )


# ---------------------------------------------------------------------------
# Resume pointers
# ---------------------------------------------------------------------------


def _clock(anchor_ms: object) -> str | None:
    """``754000`` -> ``"12:34"``. ``None`` for anything unusable."""
    if not isinstance(anchor_ms, (int, float)) or isinstance(anchor_ms, bool):
        return None
    if anchor_ms < 0:
        return None
    total = int(anchor_ms) // 1000
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


@section("resume")
def _resume_section(ctx: TodayContext) -> Section:
    """Where the last watching session stopped, if anything said so.

    ``media_heartbeat`` is a single live pointer, not a history: it says what was
    on screen, not what is on screen now. The row's own timestamp is printed
    beside it so a three-week-old pointer reads as three weeks old rather than as
    an invitation to press play.
    """
    heading = "Resume"
    try:
        row = ctx.conn.execute(
            """
            SELECT h.media_id, h.anchor_ms, h.displayed_text, h.updated_ts,
                   m.title, m.kind
              FROM media_heartbeat h
              LEFT JOIN media m ON m.id = h.media_id
             WHERE h.id = 1
            """
        ).fetchone()
    except sqlite3.Error:
        row = None

    if row is None:
        return Section(
            key="resume",
            heading=heading,
            lines=(
                "No resume pointer — nothing has reported a playback position "
                "yet.",
            ),
        )

    label = row["title"] or row["media_id"] or "an untitled source"
    parts = [str(label)]
    clock = _clock(row["anchor_ms"])
    if clock:
        parts.append(f"at {clock}")
    lines = [f"Last seen: {' '.join(parts)} (as of {row['updated_ts']})."]

    text = row["displayed_text"]
    if isinstance(text, str) and text.strip():
        trimmed = text.strip()
        if len(trimmed) > DISPLAYED_TEXT_MAX:
            trimmed = trimmed[:DISPLAYED_TEXT_MAX] + "…"
        lines.append(f"On screen: 「{trimmed}」")
    return Section(key="resume", heading=heading, lines=tuple(lines))


# ---------------------------------------------------------------------------
# Lesson memory
# ---------------------------------------------------------------------------


@section(lesson_memory.SECTION_KEY)
def _lesson_memory_section(ctx: TodayContext) -> Section:
    """What the last lesson left behind: open threads, next step, due revisits.

    Phase D's half of the loop, appended at the seam rather than folded into an
    existing section: everything about *which* rows matter and *how* they read
    lives in :mod:`katagiri.lesson_memory`, so this builder is the plug and
    nothing more. It never returns ``None`` — a database with no lessons in it
    still says so, because a vanished section reads as "nothing owed".
    """
    return Section(
        key=lesson_memory.SECTION_KEY,
        heading=lesson_memory.SECTION_HEADING,
        lines=lesson_memory.section_lines(ctx.conn, today=ctx.day),
    )


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

#: Section builders, in page order. **This is the extension seam.** A later
#: phase appends a builder here (decorated with :func:`section`) and the page
#: grows; :func:`render_today` is not touched, and no existing section changes
#: shape. Removing or reordering one is a different kind of change and should be
#: argued for, not slipped in.
SECTIONS: Final[tuple[SectionBuilder, ...]] = (
    _due_section,
    _streak_section,
    _known_trend_section,
    _weakest_morphs_section,
    _resume_section,
    _lesson_memory_section,
)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def build_context(
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
    now: datetime | None = None,
    zone: tzinfo | None = None,
) -> TodayContext:
    """Freeze the clock for one render.

    ``today`` defaults to the local date (day keys in the event log are local);
    ``now`` to the current UTC instant, truncated to whole seconds because that is
    the only timestamp precision anything in this project stores. ``zone`` is the
    local zone Anki's rollover is measured in, defaulting to the machine's.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    moment = moment.replace(microsecond=0)
    return TodayContext(
        conn=conn, day=today or date.today(), now=moment, zone=zone
    )


def _frontmatter(ctx: TodayContext) -> list[str]:
    return [
        "---",
        f"schema: {FRONTMATTER_SCHEMA}",
        f"type: {FRONTMATTER_TYPE}",
        f'title: "Today — {ctx.day_key}"',
        f"day: {ctx.day_key}",
        # Load-bearing, and first among the 'generated*' keys on purpose: this is
        # what write_today checks before it is willing to overwrite anything.
        "generated: true",
        f"generated_at: {ctx.stamp}",
        f"generator: {GENERATOR}",
        "---",
    ]


def render_sections(ctx: TodayContext) -> list[Section]:
    """Run every registered builder. A builder that returns ``None`` is skipped.

    A builder that *raises* still gets a section — one that says it failed. The
    failure is logged to stderr with its traceback, but the page must say so too:
    a section that vanishes silently is indistinguishable from a section that had
    nothing to report, and the learner reads "no weakest morphs" as good news
    when it actually means the query blew up. Stated absence, never silence.

    Builders are expected to handle their own absent-data cases and return a
    sentence saying so; this is the backstop for the case nobody predicted.
    """
    built: list[Section] = []
    for builder in SECTIONS:
        key = getattr(builder, "section_key", getattr(builder, "__name__", "?"))
        try:
            result = builder(ctx)
        except Exception:  # noqa: BLE001 - one bad section must not sink the page
            logger.exception("Section %s failed to build.", key)
            built.append(
                Section(
                    key=key,
                    heading=f"{key} (failed)",
                    lines=(
                        f"This section could not be built. Nothing here is a "
                        f"claim about your study data — treat '{key}' as "
                        "unknown, not as empty. The traceback is on stderr.",
                    ),
                )
            )
            continue
        if result is not None:
            built.append(result)
    return built


def render_today(
    ctx: TodayContext, sections: Sequence[Section] | None = None
) -> str:
    """Render the page as markdown.

    Deterministic for a fixed context *whose* ``zone`` *is set* — nothing here
    reads the clock. With ``ctx.zone`` left ``None`` the Anki day rollover is
    resolved against the machine's zone while the due section is being built, so
    the same context and the same database can render a different due count on a
    differently configured machine; :class:`TodayContext` states the caveat in
    full. Pin ``zone`` when byte-for-byte identical output matters.

    ``sections`` lets a caller that has already run the registry (as
    :func:`write_today` has, to count them for the event log) pass the result in
    rather than building every section a second time.

    The ``generated: true`` key in the frontmatter is not decoration:
    :func:`write_today` refuses to overwrite any file that lacks it.
    """
    built = render_sections(ctx) if sections is None else sections
    lines = _frontmatter(ctx)
    lines.append("")
    lines.append(f"# Today — {ctx.day_key}")
    lines.append("")
    lines.append(
        "*Generated by Katagiri from its own database. Edits here are not read "
        "back; this file is rewritten every time it is exported.*"
    )
    lines.append("")

    for item in built:
        lines.append(f"## {item.heading}")
        lines.append("")
        lines.extend(item.lines)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

#: Reused verbatim from the sensei letter rather than reimplemented: the rule
#: ("only the frontmatter block counts; a body line saying ``generated: true``
#: proves nothing") is the same rule, and a second implementation would be a
#: second thing to get subtly wrong. Aliased under a name that does not say
#: "letter", since this module writes a page.
is_generated_note = sensei_letter.is_generated_letter


def derived_dir(vault_path: Path | str | None = None) -> Path:
    """``<vault>/.derived``. The vault comes from config when not given."""
    base = (
        Path(vault_path)
        if vault_path is not None
        else get_config().require_vault_path()
    )
    return base / DERIVED_DIR_NAME


def derived_target(name: str, vault_path: Path | str | None = None) -> Path:
    """Resolve ``name`` inside ``.derived``, refusing anything that escapes it.

    Phase B only ever writes one filename, so this looks like ceremony today. It
    is not: the registry exists so later phases add outputs, and the moment a
    section name or a media id reaches a path is the moment "confined to
    ``.derived``" stops being obvious. Checked on the resolved path, so ``..``
    segments, absolute paths and drive-relative paths all fail the same way.

    The refusal names neither the caller's string nor the vault root. The string is
    caller input, and an error message is not a place to echo input back. The root
    is — unless an explicit ``vault_path`` was passed — the ``vault_path`` config
    value, and :mod:`katagiri.config` states that config values are never logged;
    a ``TodayExportError`` is printed by the CLI, logged, and pasted into bug
    reports, so it is exactly the crack such a value escapes through. What the
    message does carry is which check failed and the one directory writes are
    confined to, ``.derived`` — a constant of this module, not a configured value.
    """
    directory = derived_dir(vault_path)
    candidate = (directory / name).resolve()
    root = directory.resolve()
    if candidate == root or root not in candidate.parents:
        raise TodayExportError(
            f"Refusing to write outside the vault's {DERIVED_DIR_NAME} directory: "
            "the requested name resolves elsewhere. Katagiri only writes generated "
            "files into .derived, under the configured 'vault_path'."
        )
    return candidate


def _os_detail(exc: OSError) -> str:
    """Why an OS call failed, without the path it failed on.

    A platform ``OSError`` stringifies as ``"[Errno 13] Permission denied:
    'C:\\\\Users\\\\me\\\\Vault\\\\.derived\\\\Today.md'"`` — the message carries
    the filename, which is how the vault root would reach an error string the rest
    of this module goes out of its way not to build (see :func:`derived_target`).
    ``strerror`` is the same diagnosis with the path left off. An ``OSError`` raised
    without one — a wrapper's, or a test's — has no path in it either, so falling
    back to its ``str`` leaks nothing.
    """
    return exc.strerror or str(exc) or type(exc).__name__


def write_today(
    conn: sqlite3.Connection,
    vault_path: Path | str | None = None,
    *,
    today: date | None = None,
    now: datetime | None = None,
    zone: tzinfo | None = None,
    tz: str | None = None,
) -> Path:
    """Render and write ``<vault>/.derived/Today.md``; return its path.

    Refuses to overwrite an existing file unless that file's own frontmatter says
    ``generated: true``, and refuses just as firmly when the file cannot be read
    at all — an unreadable file is one Katagiri cannot vouch for, and "I could not
    check" is not a reason to overwrite.

    The write itself goes to a temporary file in the same directory and is moved
    into place with :func:`os.replace`, which is atomic on every platform this
    runs on. Writing in place would truncate first, so a crash mid-write would
    leave a file with no frontmatter — which the refusal rule above would then
    read as hand-written, locking the learner out of every future export until
    they deleted it by hand. The safety rule must not be able to trigger on
    Katagiri's own half-finished output.

    On success a ``today_export`` event is appended carrying the day key, the
    file's basename, and how many sections rendered. No section content.

    Every refusal below names the file the way :func:`derived_target` does — the
    vault-relative ``.derived/Today.md``, never the resolved absolute path, which
    would carry the ``vault_path`` config value into a message that is printed and
    logged. The operator knows their own vault root; the key is named instead.
    """
    ctx = build_context(conn, today=today, now=now, zone=zone)
    target = derived_target(TODAY_FILENAME, vault_path)
    where = f"{DERIVED_DIR_NAME}/{target.name}"

    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise TodayExportError(
                f"{where} (under the configured 'vault_path') exists but could not "
                "be read, so Katagiri cannot tell whether it was generated "
                f"({_os_detail(exc)}). Move or delete it by hand."
            ) from exc
        if not is_generated_note(existing):
            raise TodayExportError(
                f"{where} (under the configured 'vault_path') already exists and "
                "does not carry 'generated: true' in its frontmatter, so it is "
                "treated as hand-written and left alone. Rename or move it if you "
                "want a fresh export here."
            )

    built = render_sections(ctx)
    body = render_today(ctx, built)
    scratch: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, scratch_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        scratch = Path(scratch_name)
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic on POSIX and on Windows (ReplaceFile semantics): either the old
        # page or the whole new one is on disk, never a truncated hybrid.
        os.replace(scratch, target)
        scratch = None
    except OSError as exc:
        raise TodayExportError(
            f"Could not write the Today page to {where}, under the configured "
            f"'vault_path' ({_os_detail(exc)})."
        ) from exc
    finally:
        if scratch is not None:
            # The move never happened; leaving a .tmp behind in .derived would be
            # litter the next run has no way to recognise as its own.
            with contextlib.suppress(OSError):
                scratch.unlink()

    events.append_event(
        conn,
        type=TODAY_EVENT_TYPE,
        session_id=f"today:{ctx.day_key}",
        tz=tz,
        payload={
            "day": ctx.day_key,
            "path": target.name,
            "sections": len(built),
        },
    )
    logger.info("Wrote the Today page for %s (%d sections).", ctx.day_key, len(built))
    return target


# ---------------------------------------------------------------------------
# Worksheets
# ---------------------------------------------------------------------------
#
# A worksheet is a sibling of the Today page and the sensei letter, not a third
# kind of writer: it rides the same confined path (:func:`derived_target`, into
# ``.derived`` rather than ``80-progress`` — a worksheet is disposable practice
# material, not a weekly record) and the same ``generated: true`` guard
# (:data:`is_generated_note`), and it writes through the identical
# tempfile-then-``os.replace`` sequence :func:`write_today` uses, for the same
# reason: a crash mid-write must never leave a frontmatter-less file that the
# guard would then mistake for something hand-written.
#
# **Item selection is out of scope for this writer.** Nothing in this module or
# in :mod:`katagiri.session_tools` yet assembles "today's items" as a set — the
# nearest things (`_weakest_morphs_section`'s AnkiMorphs lemmas, the single
# next-step action in ``session_tools``) are shaped for a sentence, not a batch
# of items to drill. So :func:`worksheet_items` takes the simplest input that
# composes with what this module already reads: a caller-supplied sequence of
# item ids (or a surface form ``known_word`` can resolve). A future item-picker
# (``find_i_plus_one``, a curated list, an operator's own choice) plugs in ahead
# of this function without it changing.


#: The three shapes FR-024 names. A worksheet may render any subset, in any
#: order; :func:`render_worksheet` renders exactly the shapes it is given, in
#: the order given.
WORKSHEET_SHAPES: Final = ("cloze", "scramble", "table")

WORKSHEET_FILENAME_TEMPLATE: Final = "Worksheet-{day}.md"
WORKSHEET_EVENT_TYPE: Final = "worksheet_export"
WORKSHEET_FRONTMATTER_TYPE: Final = "worksheet"

#: The three furigana-decay stages (skills-pack-v1.md, "Furigana decays per
#: item"; T034, prose-only). Named exactly as that table names them so a reader
#: moving between the doc and this code is looking at the same three words.
FURIGANA_ALWAYS: Final = "always"
FURIGANA_FIRST_OCCURRENCE: Final = "first_occurrence"
FURIGANA_OFF: Final = "off"


def worksheet_filename(day_key: str) -> str:
    """``Worksheet-2026-08-21.md``. One worksheet file per day, like Today.md."""
    return WORKSHEET_FILENAME_TEMPLATE.format(day=day_key)


@dataclass(frozen=True, slots=True)
class WorksheetItem:
    """One item as every shape on the page will render it.

    Resolved once per render (:func:`worksheet_items`) so a word that appears in
    more than one shape gets the same surface, meaning and furigana stage
    everywhere on the page — never recomputed per shape, which is what keeps the
    stages from disagreeing with themselves within one export.
    """

    item_id: str  # canonical, after alias resolution
    kind: str | None
    kanji: str | None
    reading: str | None
    meaning: str | None
    furigana_stage: str


def _item_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT kind, kanji, reading, understanding, lexeme_ref "
        "FROM item WHERE id = ?",
        (item_id,),
    ).fetchone()


def _item_meaning(conn: sqlite3.Connection, lexeme_ref: str | None) -> str | None:
    """``item.lexeme_ref`` is a soft reference (no FK, per the schema) — a stale
    one after a re-import is a missing meaning, not an error."""
    if not lexeme_ref:
        return None
    row = conn.execute(
        "SELECT gloss_en FROM lexeme WHERE id = ?", (lexeme_ref,)
    ).fetchone()
    return None if row is None else row["gloss_en"]


def furigana_stage(
    conn: sqlite3.Connection, item_id: str, row: sqlite3.Row | None = None
) -> str:
    """Which of the three decay stages this item is at right now.

    Reads exactly the fields skills-pack-v1.md's table names and nothing else:
    :func:`katagiri.known.known_word` for a word or kanji item (``found``,
    ``is_known``, ``ambiguous``, ``source``, ``suspect``), or ``item.understanding``
    against :data:`katagiri.intelligence.DEFAULT_MIN_UNDERSTANDING` for a grammar
    item — the same column :class:`katagiri.intelligence.MasteryLookup` reads,
    queried the same way, so this is not a second notion of "known" grown next to
    the first one. There is no stored stage; calling this twice after a mark
    changes the answer, on purpose.

    ``row`` lets a caller that already has the ``item`` row (as
    :func:`worksheet_items` does) skip a second query; when omitted this queries
    for it.
    """
    if row is None:
        row = _item_row(conn, item_id)
    kind = None if row is None else row["kind"]
    understanding = None if row is None else row["understanding"]

    if kind == "grammar":
        if understanding is None or understanding < DEFAULT_MIN_UNDERSTANDING:
            return FURIGANA_ALWAYS
        if understanding >= 5:
            return FURIGANA_OFF
        return FURIGANA_FIRST_OCCURRENCE

    answer = known.known_word(conn, item_id)
    if not answer.get("found") or answer.get("ambiguous") or not answer.get(
        "is_known"
    ):
        return FURIGANA_ALWAYS
    if answer.get("suspect") or answer.get("source") == "manual":
        return FURIGANA_FIRST_OCCURRENCE
    return FURIGANA_OFF


def worksheet_items(
    conn: sqlite3.Connection, item_ids: Sequence[str]
) -> list[WorksheetItem]:
    """Resolve each id into the shape every worksheet renderer reads from.

    Each id is alias-resolved first (:func:`katagiri.db.resolve_alias`), exactly
    as :class:`katagiri.intelligence.MasteryLookup` resolves one, so a renamed
    item keeps answering to its old id here too. An id with no ``item`` row is a
    caller mistake, not a state this module can render around, so it raises
    rather than silently dropping the item from the page.
    """
    resolved: list[WorksheetItem] = []
    for raw_id in item_ids:
        canonical = resolve_alias(conn, raw_id)["canonical_id"]
        row = _item_row(conn, canonical)
        if row is None:
            raise TodayExportError(
                f"Worksheet item {raw_id!r} has no matching item row; it cannot "
                "be rendered."
            )
        resolved.append(
            WorksheetItem(
                item_id=canonical,
                kind=row["kind"],
                kanji=row["kanji"],
                reading=row["reading"],
                meaning=_item_meaning(conn, row["lexeme_ref"]),
                furigana_stage=furigana_stage(conn, canonical, row=row),
            )
        )
    return resolved


def _render_surface(item: WorksheetItem, seen: set[str]) -> str:
    """The item's surface, with furigana rendered per its decay stage.

    ``seen`` is shared across every shape on one page: a ``first_occurrence``
    item shows its ruby the first time any shape reaches it and plain kanji on
    every later occurrence, on this page, in this render — the ladder rung
    acting on the render, never a second field recording that it fired.
    """
    surface = item.kanji or item.reading or item.item_id
    already_seen = item.item_id in seen
    seen.add(item.item_id)
    if not item.kanji or not item.reading or item.kanji == item.reading:
        # Nothing to gloss: a kana-only surface, or no distinct reading on file.
        return surface
    show = item.furigana_stage == FURIGANA_ALWAYS or (
        item.furigana_stage == FURIGANA_FIRST_OCCURRENCE and not already_seen
    )
    if show:
        return f"<ruby>{item.kanji}<rt>{item.reading}</rt></ruby>"
    return item.kanji


def _scrambled(reading: str) -> str:
    """A deterministic scramble: the reading's characters in reverse order.

    Not random — :func:`sensei_letter.render_letter`'s "same stats, same bytes"
    applies here too, so the same item scrambles the same way on every render.
    Too short to scramble (0 or 1 characters) is returned unchanged.
    """
    chars = list(reading)
    return reading if len(chars) < 2 else "".join(reversed(chars))


def _render_cloze(items: Sequence[WorksheetItem], seen: set[str]) -> list[str]:
    lines = ["Fill in the missing word from its meaning.", ""]
    if not items:
        lines.append("No items were supplied for this worksheet.")
        return lines
    for n, item in enumerate(items, start=1):
        prompt = item.meaning or f"(no recorded meaning for {item.item_id})"
        lines.append(f"{n}. {prompt} — ______")
    lines.append("")
    lines.append("<details><summary>Answers</summary>")
    lines.append("")
    for n, item in enumerate(items, start=1):
        lines.append(f"{n}. {_render_surface(item, seen)}")
    lines.append("")
    lines.append("</details>")
    return lines


def _render_scramble(items: Sequence[WorksheetItem], seen: set[str]) -> list[str]:
    lines = ["Unscramble each reading, then check it against the surface.", ""]
    if not items:
        lines.append("No items were supplied for this worksheet.")
        return lines
    for n, item in enumerate(items, start=1):
        if not item.reading:
            lines.append(f"{n}. (no reading recorded for {item.item_id})")
            continue
        lines.append(f"{n}. {_scrambled(item.reading)}")
    lines.append("")
    lines.append("<details><summary>Answers</summary>")
    lines.append("")
    for n, item in enumerate(items, start=1):
        if not item.reading:
            lines.append(f"{n}. —")
            continue
        lines.append(f"{n}. {_render_surface(item, seen)} ({item.reading})")
    lines.append("")
    lines.append("</details>")
    return lines


def _render_table(items: Sequence[WorksheetItem], seen: set[str]) -> list[str]:
    if not items:
        return ["No items were supplied for this worksheet."]
    lines = ["| # | Item | Meaning |", "| --- | --- | --- |"]
    for n, item in enumerate(items, start=1):
        meaning = item.meaning or "(no recorded meaning)"
        lines.append(f"| {n} | {_render_surface(item, seen)} | {meaning} |")
    return lines


#: Shape name -> (heading, renderer). The extension seam for a later shape,
#: mirrored on :data:`SECTIONS`: append here, :func:`render_worksheet` does not
#: change.
_SHAPE_RENDERERS: Final[
    dict[str, tuple[str, Callable[[Sequence[WorksheetItem], set[str]], list[str]]]]
] = {
    "cloze": ("Cloze", _render_cloze),
    "scramble": ("Scramble", _render_scramble),
    "table": ("Table", _render_table),
}


def _worksheet_frontmatter(
    ctx: TodayContext, items: Sequence[WorksheetItem], shapes: Sequence[str]
) -> list[str]:
    return [
        "---",
        f"schema: {FRONTMATTER_SCHEMA}",
        f"type: {WORKSHEET_FRONTMATTER_TYPE}",
        f'title: "Worksheet — {ctx.day_key}"',
        f"day: {ctx.day_key}",
        f"items: {len(items)}",
        f"shapes: {', '.join(shapes)}",
        # Load-bearing, exactly as in _frontmatter: this is what write_worksheet
        # checks before it is willing to overwrite anything.
        "generated: true",
        f"generated_at: {ctx.stamp}",
        f"generator: {GENERATOR}",
        "---",
    ]


def render_worksheet(
    ctx: TodayContext,
    items: Sequence[WorksheetItem],
    shapes: Sequence[str] = WORKSHEET_SHAPES,
) -> str:
    """Render one worksheet page: frontmatter, then each shape as its own section.

    ``seen`` (furigana occurrence tracking) is threaded across every shape, in
    the order given, so "first occurrence" means the first line on the whole
    page, not the first line of each shape taken separately.
    """
    unknown = sorted(set(shapes) - set(_SHAPE_RENDERERS))
    if unknown:
        raise TodayExportError(
            f"Unknown worksheet shape(s): {', '.join(unknown)}. Choose from "
            f"{', '.join(WORKSHEET_SHAPES)}."
        )

    lines = _worksheet_frontmatter(ctx, items, shapes)
    lines.append("")
    lines.append(f"# Worksheet — {ctx.day_key}")
    lines.append("")
    lines.append(
        "*Generated by Katagiri from its own database. Edits here are not read "
        "back; this file is rewritten every time it is exported.*"
    )
    lines.append("")

    seen: set[str] = set()
    for shape in shapes:
        heading, renderer = _SHAPE_RENDERERS[shape]
        lines.append(f"## {heading}")
        lines.append("")
        lines.extend(renderer(items, seen))
        lines.append("")

    return "\n".join(lines)


def write_worksheet(
    conn: sqlite3.Connection,
    item_ids: Sequence[str],
    vault_path: Path | str | None = None,
    *,
    shapes: Sequence[str] = WORKSHEET_SHAPES,
    today: date | None = None,
    now: datetime | None = None,
    zone: tzinfo | None = None,
    tz: str | None = None,
) -> Path:
    """Resolve, render and write one day's worksheet; return its path.

    Every safety property is inherited, not reimplemented: the target path comes
    from :func:`derived_target`, so it is confined to ``.derived`` the same way
    ``Today.md`` is; an existing file is refused unless :func:`is_generated_note`
    says it carries ``generated: true``; and the write itself goes to a
    ``tempfile.mkstemp`` scratch file in the same directory, moved into place
    with :func:`os.replace`, for the identical crash-safety reason
    :func:`write_today` documents — a truncated in-place write would leave a
    frontmatter-less file the guard above would then treat as hand-written.

    On success a ``worksheet_export`` event is appended carrying the day key, the
    file's basename, the item count and the shapes rendered. No item content, no
    meanings, no furigana stages.
    """
    ctx = build_context(conn, today=today, now=now, zone=zone)
    resolved_items = worksheet_items(conn, item_ids)
    target = derived_target(worksheet_filename(ctx.day_key), vault_path)
    where = f"{DERIVED_DIR_NAME}/{target.name}"

    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise TodayExportError(
                f"{where} (under the configured 'vault_path') exists but could "
                "not be read, so Katagiri cannot tell whether it was generated "
                f"({_os_detail(exc)}). Move or delete it by hand."
            ) from exc
        if not is_generated_note(existing):
            raise TodayExportError(
                f"{where} (under the configured 'vault_path') already exists "
                "and does not carry 'generated: true' in its frontmatter, so it "
                "is treated as hand-written and left alone. Rename or move it "
                "if you want a fresh worksheet here."
            )

    body = render_worksheet(ctx, resolved_items, shapes)
    scratch: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, scratch_name = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        scratch = Path(scratch_name)
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, target)
        scratch = None
    except OSError as exc:
        raise TodayExportError(
            f"Could not write the worksheet to {where}, under the configured "
            f"'vault_path' ({_os_detail(exc)})."
        ) from exc
    finally:
        if scratch is not None:
            with contextlib.suppress(OSError):
                scratch.unlink()

    events.append_event(
        conn,
        type=WORKSHEET_EVENT_TYPE,
        session_id=f"worksheet:{ctx.day_key}",
        tz=tz,
        payload={
            "day": ctx.day_key,
            "path": target.name,
            "items": len(resolved_items),
            "shapes": list(shapes),
        },
    )
    logger.info(
        "Wrote the worksheet for %s (%d items, shapes: %s).",
        ctx.day_key,
        len(resolved_items),
        ", ".join(shapes),
    )
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.today_export",
        description="Write <vault>/.derived/Today.md from Katagiri's database.",
    )
    parser.add_argument(
        "--vault",
        metavar="PATH",
        help="vault root to write into (default: vault_path from config).",
    )
    parser.add_argument(
        "--day",
        metavar="YYYY-MM-DD",
        help="the local day the page is about (default: today).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the page to stdout; write nothing and log nothing.",
    )
    return parser


def _parse_day(raw: str | None) -> date | None:
    """Parse ``--day``, naming the expectation rather than the rejected value.

    The message says what a day looks like and stops there. Echoing the argument
    back — or the ``ValueError``, which quotes it verbatim — puts caller-supplied
    text into an error string that is printed, logged, and read aloud in bug
    reports; the reader already has what they typed on the line above.
    """
    if raw is None:
        return None
    try:
        return datetime.strptime(raw.strip(), DAY_FORMAT).date()
    except ValueError as exc:
        raise TodayExportError(
            "--day must be one calendar day in YYYY-MM-DD form, for example "
            "2026-08-19."
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.today_export``."""
    from katagiri.db import open_db
    from katagiri.logging_setup import setup_logging

    args = _build_parser().parse_args(argv)
    setup_logging()

    # The page contains Japanese, and a legacy Windows console is cp1252: without
    # this, printing it raises UnicodeEncodeError instead of showing the page.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:  # pragma: no branch - always present on 3.12
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")

    try:
        day = _parse_day(args.day)
    except TodayExportError as exc:
        print(f"error: {exc}")
        return 2

    conn = open_db()
    try:
        if args.dry_run:
            print(render_today(build_context(conn, today=day)), end="")
            return 0
        path = write_today(conn, args.vault, today=day)
    except TodayExportError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        conn.close()

    print(f"wrote {path}")
    return 0


__all__ = [
    "DERIVED_DIR_NAME",
    "FURIGANA_ALWAYS",
    "FURIGANA_FIRST_OCCURRENCE",
    "FURIGANA_OFF",
    "SECTIONS",
    "TODAY_EVENT_TYPE",
    "TODAY_FILENAME",
    "WORKSHEET_EVENT_TYPE",
    "WORKSHEET_FILENAME_TEMPLATE",
    "WORKSHEET_SHAPES",
    "Section",
    "SectionBuilder",
    "TodayContext",
    "TodayExportError",
    "WorksheetItem",
    "anki_due_count",
    "build_context",
    "derived_dir",
    "derived_target",
    "furigana_stage",
    "is_generated_note",
    "main",
    "render_sections",
    "render_today",
    "render_worksheet",
    "section",
    "worksheet_filename",
    "worksheet_items",
    "write_today",
    "write_worksheet",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
