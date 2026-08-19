"""D4: lesson memory — what the last lesson left behind, read back at the next one.

What this module is for
-----------------------
:mod:`katagiri.session_tools` *writes* the three things a lesson leaves behind:
``unresolved[]`` threads, a ``next_step`` recorded at close, and a
``revisit_after`` day key that schedules the **topic**. This module is the read
side of the same loop: it aggregates those three into the answer to "where was I,
and what is owed?", and renders them as the ``lesson_memory`` section of
``Today.md`` through the Phase-B :data:`katagiri.today_export.SECTIONS` registry
(FR-006). Nothing here writes anything — no rows, no events, no files.

Katagiri schedules topics; Anki schedules items
-----------------------------------------------
``revisit_after`` is the only scheduling this project does, and it is deliberately
coarse: a *topic* comes back, not a card. Anki already owns item-level spacing and
does it better than anything written here would; a second scheduler competing for
the same reviews would be both redundant and wrong. So a due revisit means "re-test
this objective cold", never "answer these six cards".

One attribution rule, not two
-----------------------------
A lesson's *outcome* — how many observations happened inside it, how many were
unassisted — is computed by the ``lesson_outcome`` view, which attributes an
observation to a lesson by session id plus timestamp window. That rule has two
sharp edges, both found in the TG-D3 cold run: an observation logged **after**
``lesson_close`` falls outside every window and is attributed to no lesson, and two
lessons open at once both claim the observations in their overlap.

This module does not fix that by inventing a second rule, and it does not compute
its own attribution at all. When lesson aggregates are wanted, they come from
:func:`katagiri.session_tools.lessons`, which reads ``lesson_outcome``. What this
module does instead is **name the condition on the page**: an open lesson is
reported as open, and two open at once are reported as double-counting, because a
learner who can see the overlap can close a lesson, while a silently wrong count
cannot be argued with.

Ladder parity with ``start_session``
------------------------------------
The "next session opens with" line is :func:`katagiri.session_tools.prescribe`
itself, called read-only — not a reimplementation of its ladder. That function
exists separately from ``start_session`` precisely so the choice can be inspected
without appending an event, and using it here is what guarantees the page and the
session cannot disagree about what comes next. The same-window rule holds for
pending next steps: :func:`pending_next_steps` scans the identical lookback and
applies the identical "has it been prescribed once already" test, so a next step
this page calls pending is a next step the next open will actually prescribe.

SECRETS: every string this module returns is learner-authored lesson text (or
externally-sourced text that already passed the envelope's echo-back at write
time), and :mod:`katagiri.today_export` writes it into the vault. Nothing here
reads credentials, tokens or files. Thread and objective text is flattened to a
single line and truncated before it is rendered — see :func:`_flatten` — so no
stored text can forge a markdown heading or a frontmatter fence in a page whose
``generated: true`` key is load-bearing.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from katagiri import session_tools
from katagiri.logging_setup import get_logger

_log = get_logger("lesson_memory")

#: Section identity in the ``Today.md`` registry. Declared here rather than in
#: :mod:`katagiri.today_export` so the renderer and the tests name the same
#: constant, and the key can be looked up without importing the page module.
SECTION_KEY: Final = "lesson_memory"
SECTION_HEADING: Final = "Lesson memory"

#: How many of each kind reach the page. Totals are reported alongside, so a
#: truncated list reads as "3 of 11" rather than as "3".
DEFAULT_THREAD_LIMIT: Final = 5
DEFAULT_REVISIT_LIMIT: Final = 5
DEFAULT_NEXT_STEP_LIMIT: Final = 3
DEFAULT_OPEN_LESSON_LIMIT: Final = 5

#: Thread and instruction text is capped at render width. ``next_step`` and
#: unresolved threads may be up to ``session_tools.MAX_TEXT_CHARS`` (2000)
#: characters, and a page made of four 2000-character bullets is a page nobody
#: reads.
TEXT_MAX: Final = 160

_WHITESPACE: Final = re.compile(r"\s+")
_DAY_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _day(value: date | str | None) -> date:
    """The local day this read is *about*.

    A :class:`~datetime.date` (what a ``TodayContext`` carries), a ``YYYY-MM-DD``
    day key (what a tool call carries), or ``None`` for today. ``ValueError`` on
    anything else, matching :func:`katagiri.session_tools.lessons` — a malformed
    date is a caller-domain mistake, not a refusal to render.
    """
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(
            f"today must be a YYYY-MM-DD date or a date object; got {value!r}."
        ) from None


def _check_limit(name: str, value: int) -> int:
    if value < 1:
        raise ValueError(f"{name} must be at least 1; got {value}.")
    return value


def _flatten(text: object, limit: int = TEXT_MAX) -> str:
    """Stored text as one safe, bounded line.

    Two jobs, both load-bearing. Whitespace is collapsed, so a multi-line thread
    cannot become several markdown lines — a stored ``---`` or ``## `` at the
    start of its own line would otherwise render as a frontmatter fence or a
    heading inside a page whose frontmatter decides whether it may be overwritten
    at all. And the result is truncated, because these columns hold up to 2000
    characters.
    """
    flat = _WHITESPACE.sub(" ", str(text)).strip()
    if not flat:
        return "(blank)"
    if len(flat) > limit:
        return flat[: limit - 1].rstrip() + "…"
    return flat


def _days_since(stamp: object, today: date) -> int | None:
    """Whole days from a timestamp's day part to ``today``. ``None`` if unusable.

    Only the day part is read: these are local day questions ("open three days"),
    and the schema's fixed-width stamps make the slice exact. Unparseable input
    yields ``None`` rather than a zero, so "age unknown" never renders as "today".
    """
    if not isinstance(stamp, str) or not _DAY_RE.match(stamp):
        return None
    try:
        return (today - date.fromisoformat(stamp[:10])).days
    except ValueError:
        return None


def _age(days: int | None, *, since: str, verb: str) -> str:
    """``"open 3 days (since 2026-08-16)"`` — or just the date, if age is unknown."""
    if days is None:
        return f"{verb} {since}"
    if days <= 0:
        return f"{verb} today"
    unit = "day" if days == 1 else "days"
    return f"{verb} {days} {unit} ({since})"


def _count(shown: int, total: int) -> str:
    """``"(3 of 11)"`` when the list was cut, ``"(3)"`` when it was not."""
    return f"({shown} of {total})" if total > shown else f"({shown})"


# ---------------------------------------------------------------------------
# The three things a lesson leaves behind
# ---------------------------------------------------------------------------


def open_threads(
    conn: sqlite3.Connection,
    *,
    topic: str | None = None,
    today: date | str | None = None,
    limit: int = DEFAULT_THREAD_LIMIT,
) -> list[dict[str, Any]]:
    """Questions served in a lesson and never answered. Oldest first.

    Oldest first, and not newest first, because an unanswered question compounds:
    the one that has been open longest is the one that has been blocking the
    longest. Same ordering as ``session_tools``' unresolved-thread action, so the
    head of this list is the thread the next session would prescribe.

    ``topic`` matches exactly — topics are names the learner chose, and a fuzzy
    match would quietly merge two of them.
    """
    _check_limit("limit", limit)
    day = _day(today)
    where = ["u.resolved_ts IS NULL"]
    params: list[Any] = []
    if topic is not None:
        where.append("l.topic = ?")
        params.append(topic)
    clause = " AND ".join(where)

    rows = conn.execute(
        f"""
        SELECT u.id AS unresolved_id, u.text AS text, u.created_ts AS created_ts,
               l.id AS lesson_id, l.topic AS topic, l.closed_ts AS lesson_closed_ts
          FROM lesson_unresolved u
          JOIN lesson l ON l.id = u.lesson_id
         WHERE {clause}
         ORDER BY u.created_ts ASC, u.id ASC
         LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    return [
        {
            "unresolved_id": int(row["unresolved_id"]),
            "lesson_id": str(row["lesson_id"]),
            "topic": str(row["topic"]),
            "text": row["text"],
            "created_ts": row["created_ts"],
            "days_open": _days_since(row["created_ts"], day),
            "lesson_closed": row["lesson_closed_ts"] is not None,
        }
        for row in rows
    ]


def count_open_threads(
    conn: sqlite3.Connection, *, topic: str | None = None
) -> int:
    """How many threads are open in total, independent of any display limit."""
    where = ["u.resolved_ts IS NULL"]
    params: list[Any] = []
    if topic is not None:
        where.append("l.topic = ?")
        params.append(topic)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM lesson_unresolved u
          JOIN lesson l ON l.id = u.lesson_id
         WHERE {' AND '.join(where)}
        """,
        tuple(params),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def pending_next_steps(
    conn: sqlite3.Connection,
    *,
    today: date | str | None = None,
    limit: int = DEFAULT_NEXT_STEP_LIMIT,
    lookback: int = session_tools.NEXT_STEP_LOOKBACK,
) -> list[dict[str, Any]]:
    """Next steps written at close that no session has been given yet.

    "Pending" means exactly what ``start_session`` means by it, and is computed
    the same way: the newest ``lookback`` closed lessons that carry a non-empty
    ``next_step``, minus any whose ``next_step`` was already handed to a session
    (a ``session_open`` event whose action names that lesson). A next step is
    prescribed **once** — re-offering it forever would stall the loop on the first
    thing the learner avoided — so a step that has been prescribed is no longer
    pending here either, even if it was never done. It remains visible through
    ``lessons()``, which is the record; this list is the queue.

    Because the window and the test are the same, the head of this list is the
    next step the next non-tired ``start_session`` will prescribe.
    """
    _check_limit("limit", limit)
    _check_limit("lookback", lookback)
    day = _day(today)

    rows = conn.execute(
        """
        SELECT id, topic, objective, next_step, closed_ts
          FROM lesson
         WHERE closed_ts IS NOT NULL
           AND next_step IS NOT NULL
           AND trim(next_step) <> ''
         ORDER BY closed_ts DESC, id DESC
         LIMIT ?
        """,
        (lookback,),
    ).fetchall()

    pending: list[dict[str, Any]] = []
    for row in rows:
        prescribed = conn.execute(
            """
            SELECT 1 FROM event
             WHERE type = ?
               AND json_extract(payload, '$.action.kind') = ?
               AND json_extract(payload, '$.action.lesson_id') = ?
             LIMIT 1
            """,
            (
                session_tools.SESSION_OPEN_EVENT,
                session_tools.ACTION_NEXT_STEP,
                row["id"],
            ),
        ).fetchone()
        if prescribed is not None:
            continue
        pending.append(
            {
                "lesson_id": str(row["id"]),
                "topic": str(row["topic"]),
                "objective": row["objective"],
                "next_step": row["next_step"],
                "closed_ts": row["closed_ts"],
                "days_since_close": _days_since(row["closed_ts"], day),
            }
        )
        if len(pending) >= limit:
            break
    return pending


def due_revisits(
    conn: sqlite3.Connection,
    *,
    today: date | str | None = None,
    limit: int = DEFAULT_REVISIT_LIMIT,
) -> list[dict[str, Any]]:
    """Topics whose ``revisit_after`` day has arrived. Most overdue first.

    A topic stops being due once *another* lesson on it was opened after the
    revisit date: that lesson **is** the revisit, so nothing has to mark it done.
    The scheduling lesson itself is excluded from that test, or a lesson that
    backdated its own revisit date would cancel it on the spot. Both rules are
    ``session_tools``' — same SQL shape, widened from one row to a list.

    **One entry per topic.** The supersession test above is per *row*: it only
    clears a lesson whose topic was re-opened after *that row's own* revisit date,
    so two lessons scheduling the same topic can both survive it — an older one
    due long ago and a newer one due last week are both un-superseded, because
    neither was followed by a lesson late enough to cancel it. Rendering that as
    two bullets would contradict this module's whole contract (it schedules
    topics, never rows) and would make one owed revisit read as two. So the rows
    are collapsed to the most overdue per topic — earliest ``revisit_after``,
    ties broken by the lowest lesson id, which is exactly the row
    ``session_tools._revisit_action`` takes with its ``LIMIT 1``. The head of this
    list is therefore still the topic the next session would prescribe.

    ``limit`` counts topics, not rows, so a page that shows five shows five
    distinct topics.

    The comparison is lexicographic, which is exact because the schema pins both
    columns to fixed widths.
    """
    _check_limit("limit", limit)
    day = _day(today)
    # No SQL ``LIMIT``: the limit is a count of topics, and rows are only known
    # to be duplicates after the collapse below. Ordering matches
    # ``session_tools._revisit_action`` so the first surviving row of each topic
    # is that topic's most overdue one.
    rows = conn.execute(
        """
        SELECT l.id, l.topic, l.objective, l.revisit_after
          FROM lesson l
         WHERE l.revisit_after IS NOT NULL
           AND l.revisit_after <= ?
           AND NOT EXISTS (
               SELECT 1 FROM lesson newer
                WHERE newer.topic = l.topic
                  AND newer.id <> l.id
                  AND newer.opened_ts > l.revisit_after || 'T00:00:00Z'
           )
         ORDER BY l.revisit_after ASC, l.id ASC
        """,
        (day.isoformat(),),
    ).fetchall()

    due: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        topic = str(row["topic"])
        if topic in seen:
            continue
        seen.add(topic)
        due.append(
            {
                "lesson_id": str(row["id"]),
                "topic": topic,
                "objective": row["objective"],
                "revisit_after": str(row["revisit_after"]),
                "days_overdue": _days_since(str(row["revisit_after"]), day),
            }
        )
        if len(due) >= limit:
            break
    return due


def count_due_revisits(
    conn: sqlite3.Connection, *, today: date | str | None = None
) -> int:
    """How many topics are due in total, independent of any display limit.

    ``COUNT(DISTINCT l.topic)`` and not ``COUNT(*)``: two lessons can each hold an
    un-superseded ``revisit_after`` for the same topic (see
    :func:`due_revisits`), and counting rows would report two owed revisits where
    one topic is owed. The total this returns is the total the list it accompanies
    would reach with no limit.
    """
    day = _day(today)
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT l.topic) AS n
          FROM lesson l
         WHERE l.revisit_after IS NOT NULL
           AND l.revisit_after <= ?
           AND NOT EXISTS (
               SELECT 1 FROM lesson newer
                WHERE newer.topic = l.topic
                  AND newer.id <> l.id
                  AND newer.opened_ts > l.revisit_after || 'T00:00:00Z'
           )
        """,
        (day.isoformat(),),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def next_revisit(
    conn: sqlite3.Connection, *, today: date | str | None = None
) -> dict[str, Any] | None:
    """The soonest topic revisit that has **not** come due yet, if any.

    Only used to make the empty state say something: "nothing due, and the next
    one is on Tuesday" is information, while "nothing due" alone is
    indistinguishable from "nothing is ever scheduled". No supersession test is
    needed — a date that has not arrived cannot have been overtaken by a lesson.
    """
    day = _day(today)
    row = conn.execute(
        """
        SELECT l.id, l.topic, l.revisit_after
          FROM lesson l
         WHERE l.revisit_after IS NOT NULL
           AND l.revisit_after > ?
         ORDER BY l.revisit_after ASC, l.id ASC
         LIMIT 1
        """,
        (day.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    days = _days_since(str(row["revisit_after"]), day)
    return {
        "lesson_id": str(row["id"]),
        "topic": str(row["topic"]),
        "revisit_after": str(row["revisit_after"]),
        "days_until": None if days is None else -days,
    }


def open_lessons(
    conn: sqlite3.Connection,
    *,
    today: date | str | None = None,
    limit: int = DEFAULT_OPEN_LESSON_LIMIT,
) -> list[dict[str, Any]]:
    """Lessons with no ``closed_ts``. Newest first.

    Surfaced because an open lesson is where the ``lesson_outcome`` attribution
    window is still growing: observations keep landing in it, and once two are
    open at the same time the overlap is counted by both. This module reports the
    condition; it does not re-attribute anything (see the module docstring).
    """
    _check_limit("limit", limit)
    day = _day(today)
    rows = conn.execute(
        """
        SELECT id, topic, objective, opened_ts, session_id
          FROM lesson
         WHERE closed_ts IS NULL
         ORDER BY opened_ts DESC, id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "lesson_id": str(row["id"]),
            "topic": str(row["topic"]),
            "objective": row["objective"],
            "opened_ts": row["opened_ts"],
            "session_id": row["session_id"],
            "days_open": _days_since(row["opened_ts"], day),
        }
        for row in rows
    ]


def count_open_lessons(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM lesson WHERE closed_ts IS NULL"
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def count_lessons(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM lesson").fetchone()
    return int(row["n"]) if row is not None else 0


# ---------------------------------------------------------------------------
# The aggregate
# ---------------------------------------------------------------------------


def snapshot(
    conn: sqlite3.Connection,
    *,
    today: date | str | None = None,
    thread_limit: int = DEFAULT_THREAD_LIMIT,
    revisit_limit: int = DEFAULT_REVISIT_LIMIT,
    next_step_limit: int = DEFAULT_NEXT_STEP_LIMIT,
    open_lesson_limit: int = DEFAULT_OPEN_LESSON_LIMIT,
) -> dict[str, Any]:
    """Everything lesson memory holds, in one read-only JSON-safe dict.

    This is the shape a tool registration (T017) exposes and the shape
    :func:`section_lines` renders. ``next_action`` is
    :func:`katagiri.session_tools.prescribe` verbatim — the same single action the
    next ``start_session`` would return for a non-tired session, computed without
    appending anything. Totals accompany every truncated list so the page can say
    how much it is not showing.
    """
    day = _day(today)
    key = day.isoformat()
    return {
        "day": key,
        "lessons_total": count_lessons(conn),
        "next_action": session_tools.prescribe(conn, today=key),
        "open_threads": open_threads(conn, today=day, limit=thread_limit),
        "open_threads_total": count_open_threads(conn),
        "pending_next_steps": pending_next_steps(
            conn, today=day, limit=next_step_limit
        ),
        "due_revisits": due_revisits(conn, today=day, limit=revisit_limit),
        "due_revisits_total": count_due_revisits(conn, today=day),
        "next_revisit": next_revisit(conn, today=day),
        "open_lessons": open_lessons(conn, today=day, limit=open_lesson_limit),
        "open_lessons_total": count_open_lessons(conn),
    }


# ---------------------------------------------------------------------------
# The Today.md section
# ---------------------------------------------------------------------------


def render_lines(memory: Mapping[str, Any]) -> tuple[str, ...]:
    """A snapshot as the lines of the ``Today.md`` lesson-memory section.

    Pure formatting: no database, no clock. Order on the page is the loop's own
    order — the one prescribed action first, then the state that produced it
    (threads, next steps, topic revisits), then the one warning worth printing.

    Never empty. A database with no lessons in it still gets the prescribed
    opener and a sentence saying the memory is empty, because a section that
    silently shrinks to nothing reads as "nothing owed" when it may mean
    "nothing recorded".
    """
    lines: list[str] = []

    action = memory.get("next_action") or {}
    instruction = _flatten(action.get("instruction") or "open a lesson", limit=200)
    lines.append(f"Next session opens with: {instruction}")
    rationale = action.get("rationale")
    if rationale:
        lines.append(f"Why: {_flatten(rationale, limit=200)}")

    threads: Sequence[Mapping[str, Any]] = memory.get("open_threads") or ()
    if threads:
        total = int(memory.get("open_threads_total") or len(threads))
        lines.append("")
        lines.append(f"Open threads {_count(len(threads), total)}, oldest first:")
        for thread in threads:
            age = _age(
                thread.get("days_open"),
                since=str(thread.get("created_ts") or "an unknown time")[:10],
                verb="open",
            )
            lines.append(
                f"- {_flatten(thread.get('topic'), limit=40)} — "
                f"{_flatten(thread.get('text'))} ({age})"
            )

    # The head of this list is what ``next_action`` already says, when the ladder
    # reached the next step at all; printing it twice would make one item look
    # like two.
    prescribed_lesson = None
    if action.get("kind") == session_tools.ACTION_NEXT_STEP:
        prescribed_lesson = action.get("lesson_id")
    steps = [
        step
        for step in (memory.get("pending_next_steps") or ())
        if step.get("lesson_id") != prescribed_lesson
    ]
    if steps:
        lines.append("")
        lines.append("Next steps still waiting, newest close first:")
        for step in steps:
            age = _age(
                step.get("days_since_close"),
                since=str(step.get("closed_ts") or "an unknown time")[:10],
                verb="closed",
            )
            lines.append(
                f"- {_flatten(step.get('topic'), limit=40)} — "
                f"{_flatten(step.get('next_step'))} ({age})"
            )

    revisits: Sequence[Mapping[str, Any]] = memory.get("due_revisits") or ()
    lines.append("")
    if revisits:
        total = int(memory.get("due_revisits_total") or len(revisits))
        lines.append(
            f"Topics due for revisit {_count(len(revisits), total)} "
            "— Katagiri schedules topics; Anki schedules items:"
        )
        for topic in revisits:
            overdue = topic.get("days_overdue")
            when = f"due {topic.get('revisit_after')}"
            if isinstance(overdue, int) and overdue > 0:
                unit = "day" if overdue == 1 else "days"
                when = f"{when}, {overdue} {unit} overdue"
            lines.append(
                f"- {_flatten(topic.get('topic'), limit=40)} — {when} "
                f"(objective: {_flatten(topic.get('objective'), limit=80)})"
            )
    else:
        upcoming = memory.get("next_revisit")
        if upcoming:
            lines.append(
                "No topic is due for revisit today — the next is "
                f"{_flatten(upcoming.get('topic'), limit=40)} on "
                f"{upcoming.get('revisit_after')}."
            )
        else:
            lines.append(
                "No topic revisits are scheduled. Close a lesson with "
                "revisit_after to put its topic on the calendar; item-level "
                "spacing stays Anki's job."
            )

    open_total = int(memory.get("open_lessons_total") or 0)
    still_open: Sequence[Mapping[str, Any]] = memory.get("open_lessons") or ()
    if still_open:
        listed = ", ".join(
            "{} ({})".format(
                _flatten(lesson.get("topic"), limit=40),
                _age(
                    lesson.get("days_open"),
                    since=str(lesson.get("opened_ts") or "")[:10],
                    verb="open",
                ),
            )
            for lesson in still_open
        )
        # The list is capped; the number is not. Say so rather than letting a
        # count of seven sit in front of five names.
        hidden = open_total - len(still_open)
        if hidden > 0:
            listed = f"{listed}, and {hidden} more"
        lines.append("")
        if open_total > 1:
            lines.append(
                f"{open_total} lessons are open at once: {listed}. Observations "
                "recorded now are attributed to every open lesson whose window "
                "they fall in, so these counts overlap until they are closed."
            )
        else:
            lines.append(
                f"One lesson is still open: {listed}. Observations logged after a "
                "lesson closes belong to no lesson at all, so close it when it is "
                "actually over — not before the last observation."
            )

    if not memory.get("lessons_total"):
        lines.append("")
        lines.append(
            "No lessons have been recorded yet, so there is no memory to read "
            "back — this section fills itself in from the first lesson you close."
        )

    return tuple(lines)


def section_lines(
    conn: sqlite3.Connection, *, today: date | str | None = None
) -> tuple[str, ...]:
    """The section's lines, with the database failure case handled.

    Split from :func:`render_lines` so the page module stays one small builder,
    and so the "could not be read" sentence is this module's wording rather than
    the registry's generic backstop. ``lesson`` and ``lesson_unresolved`` are
    created by the initial migration, so their absence means the database is not
    a Katagiri database — worth saying, not worth guessing past.
    """
    try:
        memory = snapshot(conn, today=today)
    except sqlite3.Error:
        _log.exception("Lesson memory could not be read.")
        return (
            "Lesson memory could not be read right now. Treat this as unknown, "
            "not as an empty backlog: open threads, next steps and due revisits "
            "may all still be waiting.",
        )
    _log.info(
        "lesson memory read: threads=%d next_steps=%d due=%d open_lessons=%d",
        memory["open_threads_total"],
        len(memory["pending_next_steps"]),
        memory["due_revisits_total"],
        memory["open_lessons_total"],
    )
    return render_lines(memory)
