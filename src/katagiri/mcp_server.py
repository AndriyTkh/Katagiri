"""Katagiri MCP server — stdio transport only.

There is no network listener: this process is spawned by an MCP client, speaks
JSON-RPC over stdin/stdout, and exits with it. stdout belongs to the protocol;
all diagnostics go to stderr (see :mod:`katagiri.logging_setup`).

Two layers live in this file, and the boundary between them is load-bearing:

*Logic* — :func:`search_db_query`, :func:`stop_gate`, :func:`security_scan` and
their helpers are plain functions. They take a connection (or nothing), return
plain dicts, raise real exceptions, and know nothing about MCP.

*Adapter* — the ``@server.tool`` functions at the bottom are deliberately thin:
open a connection, call one logic function, hand the result through
:func:`katagiri.tool_registry.redact`, return. No branching, no formatting, no
business rules. Anything worth testing is testable without a server.

Every registered tool has a :class:`~katagiri.tool_registry.ToolSpec`, and that
registry is the contract. Tools whose data does not exist yet are registered as
raising: an unimplemented tool must never return a plausible-looking stub, because
a wrong answer that looks right is the one failure mode a study tool cannot
tolerate.

SECRETS: tool results are shown to a model and often quoted back to the learner,
and event payloads are appended to a log that cannot be edited afterwards. Both
paths go through :func:`redact`, and no exception message here interpolates a
value that could carry a credential.
"""

from __future__ import annotations

import contextlib
import json
import logging
import platform
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, Final

from mcp.server import MCPServer

from katagiri import __version__, events, jmdict_import, known
from katagiri.db import open_db, resolve_alias
from katagiri.logging_setup import get_logger, setup_logging
from katagiri.tool_registry import redact

logger = get_logger("mcp_server")

server: MCPServer[Any] = MCPServer(
    name="katagiri",
    version=__version__,
    instructions=(
        "Personal English<->Japanese study tools over a local SQLite database. "
        "Read-only in this build: nothing here writes to the event log. "
        "'search_db' is the definitive local search — prefer it over guessing "
        "whether an item exists. 'lookup' returns JMdict senses plus pitch "
        "accent; if JMdict has not been imported yet it answers "
        "found=false with a note, never a plausible-looking guess."
    ),
)


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------


def _redact_event_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Scrub secret-named keys *inside* an event row's JSON ``payload`` string.

    :func:`redact` walks mapping keys, and ``payload`` reaches this layer as text,
    so a credential that someone appended in violation of the event-log rules
    would otherwise sail straight through into a tool result. The payload is only
    re-encoded when something was actually redacted; otherwise the caller keeps
    the stored bytes exactly, which is what makes the log auditable.

    This is a read-path guard, not permission to log secrets: the event log is
    append-only, so the row itself stays dirty forever.
    """
    payload = row.get("payload")
    if not isinstance(payload, str) or not payload:
        return row
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return row
    cleaned = redact(data)
    if cleaned == data:
        return row
    return {
        **row,
        "payload": json.dumps(cleaned, ensure_ascii=False, sort_keys=True),
    }


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Open the configured database for one tool call, and close it after.

    A tool call is short and a client may keep the server alive for hours, so a
    per-call connection is cheaper to reason about than a long-lived one: no
    stale WAL snapshot, no connection wedged open across a backup.
    """
    conn = open_db()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Logic: search
# ---------------------------------------------------------------------------

TRIGRAM_MIN_CHARS: Final = 3
WORD_INDEX: Final = "fts_sentence_words"
TRIGRAM_INDEX: Final = "fts_sentence_tri"
INDEX_EMPTY_NOTE: Final = (
    "sentence_text holds no rows, so both FTS5 sentence indexes are empty and no "
    "sentence hit is possible yet; A3 populates them. Item and alias matching is "
    "unaffected."
)
_LIKE_SPECIALS: Final = re.compile(r"([%_\\])")


def _fts_phrase(query: str) -> str:
    """Quote ``query`` as a single FTS5 phrase.

    Everything the learner types is data, not query syntax: unquoted, a stray
    ``*``, ``:`` or ``OR`` would be parsed as an operator, and a bare ``"`` is a
    syntax error. Doubling internal quotes inside one phrase makes the whole
    string literal.
    """
    return '"' + query.replace('"', '""') + '"'


def _like_prefix(query: str) -> str:
    """A LIKE pattern matching ``query`` as a literal prefix."""
    return _LIKE_SPECIALS.sub(r"\\\1", query) + "%"


def _sentence_hits(
    conn: sqlite3.Connection, index: str, column: str, query: str, limit: int
) -> list[sqlite3.Row]:
    # `index` and `column` are module constants, never caller input; FTS5 table
    # names cannot be bound as parameters.
    sql = (
        f"SELECT sentence_text.item_id AS item_id, sentence_text.jp AS jp "
        f"FROM {index} "
        f"JOIN sentence_text ON sentence_text.rowid = {index}.rowid "
        f"WHERE {index} MATCH ? ORDER BY rank LIMIT ?"
    )
    try:
        return conn.execute(sql, (_fts_phrase(query), limit)).fetchall()
    except sqlite3.OperationalError as exc:
        raise ValueError(
            f"SQLite rejected the {column} full-text query for this search "
            f"string ({index}): {exc}"
        ) from exc


def search_db_query(
    conn: sqlite3.Connection, query: str, limit: int = 20
) -> dict[str, Any]:
    """Search items, aliases and sentence text for ``query``.

    Sentence search is length-routed. FTS5's trigram tokenizer indexes 3-character
    windows, so a 1- or 2-character query matches *nothing* — silently, which is
    the dangerous part. Those go to the unicode61 index over ``shadow_text``,
    whose inserted spaces make it a real word index. Longer queries go to trigram
    over the raw text, where substring matching is what you want.

    Item exact match, prefix match and alias resolution run for every query
    length. Hits are deduplicated by ``item_id``, strongest provenance first:
    exact, alias, prefix, then sentence text. ``source_index`` on each hit names
    where it came from, so a caller can tell an exact surface match from a
    substring coincidence.
    """
    text = query.strip()
    if not text:
        raise ValueError("search_db needs a non-empty query.")
    if limit < 1:
        raise ValueError(f"limit must be at least 1; got {limit}.")

    route = "words" if len(text) < TRIGRAM_MIN_CHARS else "trigram"
    route_reason = (
        f"query is {len(text)} character(s), under {TRIGRAM_MIN_CHARS}: the "
        f"trigram index cannot match it, so the unicode61 word index "
        f"({WORD_INDEX}) is used"
        if route == "words"
        else f"query is {len(text)} character(s): substring search via the "
        f"trigram index ({TRIGRAM_INDEX})"
    )

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item_id: str | None, hit_text: str | None, kind: str | None, source: str) -> None:
        if len(hits) >= limit:
            return
        if item_id is not None:
            if item_id in seen:
                return
            seen.add(item_id)
        hits.append(
            {
                "item_id": item_id,
                "text": hit_text,
                "kind": kind,
                "source_index": source,
            }
        )

    for row in conn.execute(
        "SELECT id, kind, kanji, reading FROM item "
        "WHERE kanji = ? OR reading = ? ORDER BY id",
        (text, text),
    ):
        add(row["id"], row["kanji"] or row["reading"], row["kind"], "item_exact")

    resolved = resolve_alias(conn, text)
    if resolved["redirected"]:
        canonical = resolved["canonical_id"]
        row = conn.execute(
            "SELECT id, kind, kanji, reading FROM item WHERE id = ?", (canonical,)
        ).fetchone()
        add(
            canonical,
            (row["kanji"] or row["reading"]) if row is not None else None,
            row["kind"] if row is not None else None,
            "alias",
        )

    for row in conn.execute(
        "SELECT id, kind, kanji, reading FROM item "
        "WHERE (kanji LIKE ? ESCAPE '\\' OR reading LIKE ? ESCAPE '\\') "
        "ORDER BY id LIMIT ?",
        (_like_prefix(text), _like_prefix(text), limit),
    ):
        add(row["id"], row["kanji"] or row["reading"], row["kind"], "item_prefix")

    if route == "words":
        sentence_rows = _sentence_hits(conn, WORD_INDEX, "shadow_text", text, limit)
        source = WORD_INDEX
    else:
        sentence_rows = _sentence_hits(conn, TRIGRAM_INDEX, "jp", text, limit)
        source = TRIGRAM_INDEX
    for row in sentence_rows:
        add(row["item_id"], row["jp"], "sentence", source)

    total_sentences = int(
        conn.execute("SELECT COUNT(*) FROM sentence_text").fetchone()[0]
    )
    index_empty = total_sentences == 0

    return {
        "query": text,
        "limit": limit,
        "route": route,
        "route_reason": route_reason,
        "hits": hits,
        "hit_count": len(hits),
        "sentence_rows": total_sentences,
        "index_empty": index_empty,
        "note": INDEX_EMPTY_NOTE if index_empty else None,
    }


# ---------------------------------------------------------------------------
# Logic: dictionary lookup
# ---------------------------------------------------------------------------

JMDICT_NOT_IMPORTED_NOTE: Final = (
    "JMdict is not imported yet: jmdict_entry has no rows (or the table is "
    "absent). This is 'not imported', not 'no such word' — run "
    "python -m katagiri.jmdict_import to populate it."
)


def dictionary_lookup(conn: sqlite3.Connection, surface: str) -> dict[str, Any]:
    """JMdict senses plus pitch accent for ``surface``. Never raises for empty data.

    A wrong "no such word" is worse than an honest "not imported yet", so this
    checks ``jmdict_entry`` for rows before delegating to
    :func:`katagiri.jmdict_import.lookup_word`. An absent table (caught as
    ``sqlite3.OperationalError``) is treated the same as an empty one — both
    mean the derived tables have never been built.
    """
    try:
        imported = bool(
            conn.execute("SELECT 1 FROM jmdict_entry LIMIT 1").fetchone()
        )
    except sqlite3.OperationalError:
        imported = False

    if not imported:
        return {
            "surface": surface,
            "found": False,
            "entries": [],
            "note": JMDICT_NOT_IMPORTED_NOTE,
        }

    entries = jmdict_import.lookup_word(conn, surface)
    return {
        "surface": surface,
        "found": bool(entries),
        "entries": entries,
        "note": None,
    }


# ---------------------------------------------------------------------------
# Logic: stop gate
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Logic: local-exposure check
# ---------------------------------------------------------------------------

HARDENED_PORTS: Final[tuple[int, ...]] = (27123, 8766, 19633, 8765)
FIREWALL_COMMAND: Final = (
    'netsh advfirewall firewall add rule name="Katagiri deny inbound" dir=in '
    "action=block protocol=TCP localport=27123,8766,19633,8765"
)
SECURITY_NOTE: Final = (
    "Read-only check. Katagiri never edits firewall rules; run firewall_command "
    "yourself in an elevated prompt to add the inbound deny. A port with "
    "loopback_only false is reachable from the local network."
)
_NETSTAT_TIMEOUT_S: Final = 30
# Windows netstat: "  TCP    127.0.0.1:8765   0.0.0.0:0   LISTENING   1234".
# The state word is localised on non-English Windows, so a listening socket is
# recognised by its wildcard foreign address instead of by that word.
_NETSTAT_LINE: Final = re.compile(
    r"^\s*TCP\s+(?P<local>\S+)\s+(?P<foreign>\S+)\s+(?P<state>\S+)", re.IGNORECASE
)
_LOOPBACK_HOSTS: Final = frozenset({"::1", "0:0:0:0:0:0:0:1", "localhost"})


def _split_address(address: str) -> tuple[str, int] | None:
    """``host, port`` from a netstat address, or ``None`` if unparseable."""
    if address.startswith("["):
        host, bracket, port = address.rpartition("]:")
        if not bracket:
            return None
        host = host[1:]
    else:
        host, colon, port = address.rpartition(":")
        if not colon:
            return None
    try:
        return host, int(port)
    except ValueError:
        return None


def _is_loopback(host: str) -> bool:
    return host.startswith("127.") or host.lower() in _LOOPBACK_HOSTS


def _is_listening(foreign: str, state: str) -> bool:
    if state.upper().startswith("LISTEN"):
        return True
    parsed = _split_address(foreign)
    return parsed is not None and parsed[1] == 0


def parse_netstat(text: str, ports: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    """Per-port listening state for ``ports``, from netstat's output.

    ``loopback_only`` is ``None`` when nothing is listening: there is no binding
    to vouch for, and reporting ``True`` would read as "checked and safe".
    """
    found: dict[int, set[str]] = {port: set() for port in ports}
    for line in text.splitlines():
        match = _NETSTAT_LINE.match(line)
        if match is None:
            continue
        if not _is_listening(match.group("foreign"), match.group("state")):
            continue
        parsed = _split_address(match.group("local"))
        if parsed is None:
            continue
        host, port = parsed
        if port in found:
            found[port].add(host)

    report: dict[str, dict[str, Any]] = {}
    for port in ports:
        hosts = sorted(found[port])
        report[str(port)] = {
            "listening": bool(hosts),
            "loopback_only": (
                all(_is_loopback(host) for host in hosts) if hosts else None
            ),
            "bound_addresses": hosts,
        }
    return report


def netstat_text() -> str:
    """Raw ``netstat -ano -p TCP`` output.

    Raises rather than degrading: a hardening check that cannot see the listening
    sockets has to say so, because "no exposed port found" and "could not look"
    are not the same answer.
    """
    if sys.platform != "win32":
        raise RuntimeError(
            "security_status reads Windows netstat output; this process is on "
            f"{sys.platform}. Check listening sockets with the platform's own "
            "tool instead."
        )
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_NETSTAT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not run netstat: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            f"netstat exited {completed.returncode}: "
            f"{(completed.stderr or '').strip()[:200]}"
        )
    return completed.stdout


def security_scan(ports: tuple[int, ...] = HARDENED_PORTS) -> dict[str, Any]:
    """Are the local helper ports bound to loopback only? Read-only."""
    report = parse_netstat(netstat_text(), ports)
    exposed = sorted(
        int(port)
        for port, state in report.items()
        if state["listening"] and not state["loopback_only"]
    )
    return {
        "checked_ports": list(ports),
        "ports": report,
        "exposed_ports": exposed,
        "all_loopback_only": not exposed,
        "changed_anything": False,
        "firewall_command": FIREWALL_COMMAND,
        "note": SECURITY_NOTE,
    }


# ---------------------------------------------------------------------------
# MCP adapter — thin wrappers only, one logic call each
# ---------------------------------------------------------------------------


@server.tool(
    name="ping",
    title="Ping Katagiri",
    description="Liveness check: returns server status, Katagiri version and Python version.",
)
def ping() -> dict[str, str]:
    """Return a small status dict. No side effects, no I/O."""
    logger.debug("ping called")
    return {
        "status": "ok",
        "katagiri_version": __version__,
        "python": platform.python_version(),
    }


@server.tool(
    name="known_word",
    title="Known word",
    description=(
        "Is an item id or surface form in the known set? Returns the verdict with "
        "its source, or candidates when a surface form is ambiguous. 'found' "
        "false means nothing matched, which is different from is_known false."
    ),
)
def known_word(query: str) -> dict[str, Any]:
    logger.debug("known_word called")
    with _db() as conn:
        return redact(known.known_word(conn, query))


@server.tool(
    name="known_set_stats",
    title="Known set stats",
    description=(
        "Totals for the known set, split by item kind and by source. Marks on "
        "ids with no item row are counted under kind 'unlinked'."
    ),
)
def known_set_stats() -> dict[str, Any]:
    logger.debug("known_set_stats called")
    with _db() as conn:
        return redact(known.known_set_stats(conn))


@server.tool(
    name="recent_events",
    title="Recent events",
    description=(
        "Most recent rows from the append-only event log, newest first. "
        "Optionally filtered to one event type and to a YYYY-MM-DD day_key floor."
    ),
)
def recent_events(
    limit: int = 50, type: str | None = None, since_day: str | None = None
) -> list[dict[str, Any]]:
    logger.debug("recent_events called")
    with _db() as conn:
        rows = events.recent_events(conn, limit, type, since_day)
    return [redact(_redact_event_payload(row)) for row in rows]


@server.tool(
    name="search_db",
    title="Search the database",
    description=(
        "Definitive local search over item surfaces, aliases and sentence text. "
        "Queries under 3 characters use the unicode61 word index; longer queries "
        "use the trigram index. Each hit names the index it came from. The "
        "sentence indexes are empty until A3 populates them, and the result says "
        "so explicitly."
    ),
)
def search_db(query: str, limit: int = 20) -> dict[str, Any]:
    logger.debug("search_db called")
    with _db() as conn:
        return redact(search_db_query(conn, query, limit))


@server.tool(
    name="lookup",
    title="Dictionary lookup",
    description=(
        "JMdict senses plus pitch accent for a surface form (headword or "
        "reading). Returns found=false with a note instead of raising when "
        "JMdict has not been imported yet."
    ),
)
def lookup(surface: str) -> dict[str, Any]:
    logger.debug("lookup called")
    with _db() as conn:
        return redact(dictionary_lookup(conn, surface))


@server.tool(
    name="stop_gate_status",
    title="Stop gate status",
    description=(
        "Mechanical PASS/FAIL: 14 study days inside the 18-day window ending "
        "today, where a study day means study_session events totalling at least "
        "10 minutes or at least one artifact event, and declared pauses drop "
        "days from the window's denominator. Reports the count and the shortfall; "
        "it does not interpret them."
    ),
)
def stop_gate_status() -> dict[str, Any]:
    logger.debug("stop_gate_status called")
    with _db() as conn:
        return redact(stop_gate(conn))


@server.tool(
    name="security_status",
    title="Security status",
    description=(
        "Read-only hardening check: are the local helper ports (27123, 8766, "
        "19633, 8765) bound to 127.0.0.1 rather than 0.0.0.0? Reports per-port "
        "state and the exact netsh command for the operator to run. Changes "
        "nothing."
    ),
)
def security_status() -> dict[str, Any]:
    logger.debug("security_status called")
    return redact(security_scan())


def main() -> None:
    """Entry point: stderr logging, then serve MCP over stdio."""
    setup_logging(logging.INFO)
    logger.info(
        "starting katagiri %s (python %s) on stdio",
        __version__,
        platform.python_version(),
    )
    if sys.stdout is None:  # pragma: no cover - defensive
        raise RuntimeError("stdout is unavailable; the MCP stdio transport needs it.")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
