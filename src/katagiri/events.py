"""Event-log write API: the only sanctioned way to mutate Katagiri's history.

The ``event`` table is append-only (BEFORE UPDATE / BEFORE DELETE triggers abort),
so this module never offers an update or a delete. Every state change the learner
or the server makes is expressed as an appended row; corrections are new events,
not edits.

Identifiers are ULIDs: a 48-bit millisecond timestamp followed by 80 random bits,
rendered in Crockford base32 as 26 characters. That makes the primary key both
lexicographically time-sortable and collision-free without a coordinator, which
is what lets the schema keep whole-second timestamps (see the migration header)
while still ordering two events recorded in the same second. :func:`new_ulid` is
monotonic within the process: inside one millisecond it increments the random
component instead of drawing a fresh one, and a clock that steps backwards is
pinned to the last millisecond already issued.

Timestamps are written in the schema's exact format, ``YYYY-MM-DDTHH:MM:SSZ``
(20 characters, no fractional seconds), enforced by a GLOB CHECK. A
caller-supplied ``ts_device`` is passed through **verbatim** rather than
normalised, so a malformed timestamp is rejected by the database instead of being
quietly rewritten here. Foreign data (see :func:`import_study_log`) is normalised
on the way in, because there the whole point is to accept what the outside world
wrote.

``day_key`` is the *local* calendar date of the event, used for streaks and daily
rollups. It is derived from ``ts_device`` in the zone named by ``tz``.

SECRETS: ``payload`` (and ``answer_given`` / ``expected`` / the ``note`` on a
mark) land in a durable, append-only log that is backed up and cannot be edited
or deleted afterwards. Never put credentials, API keys, tokens, cookies, session
secrets, or file contents from outside the vault into them. There is no redaction
path: an appended secret stays appended.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from katagiri.db import resolve_alias

try:  # pragma: no cover - depends on whether tzdata is available locally
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.12
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]

_log = logging.getLogger("katagiri.events")

# Crockford base32: no I, L, O or U, so a hand-copied id cannot be misread.
CROCKFORD_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH: Final = 26
TS_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
STUDY_LOG_TYPE: Final = "study_session"
STUDY_LOG_SESSION_ID: Final = "study-log-import"

MARK_EVENT_TYPES: Final = {
    "known": "mark_known",
    "unknown": "mark_unknown",
    "suspect": "mark_suspect",
}

_MAX_MS: Final = (1 << 48) - 1
_MAX_RANDOM: Final = (1 << 80) - 1

_ulid_lock = threading.Lock()
_last_ms: int = -1
_last_random: int = 0


# ---------------------------------------------------------------------------
# ULID
# ---------------------------------------------------------------------------


def new_ulid(ts_ms: int | None = None) -> str:
    """Return a fresh ULID, monotonically increasing within this process.

    ``ts_ms`` overrides the clock (tests); it is still subject to the monotonic
    rule, so passing an older millisecond does not produce a smaller id.
    """
    global _last_ms, _last_random

    with _ulid_lock:
        now_ms = int(time.time() * 1000) if ts_ms is None else int(ts_ms)
        if now_ms < 0 or now_ms > _MAX_MS:
            raise ValueError(
                f"ULID timestamp {now_ms} ms is outside the 48-bit range a ULID "
                "can carry (year 1970 through 10889)."
            )

        if now_ms > _last_ms:
            _last_ms = now_ms
            _last_random = secrets.randbits(80)
        elif _last_random < _MAX_RANDOM:
            # Same millisecond, or a clock that went backwards. Stay inside the
            # last millisecond issued and step the random component so the id
            # still sorts after its predecessor.
            _last_random += 1
        else:
            # Exhausted the 80-bit space inside one millisecond (2^80 ids). Roll
            # forward rather than repeat an id.
            _last_ms += 1
            _last_random = secrets.randbits(80)

        value = (_last_ms << 80) | _last_random

    return _encode_crockford(value)


def _encode_crockford(value: int) -> str:
    """Render a 128-bit integer as 26 Crockford base32 characters."""
    chars = ["0"] * ULID_LENGTH
    for index in range(ULID_LENGTH - 1, -1, -1):
        chars[index] = CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def ulid_time_ms(ulid: str) -> int:
    """Millisecond timestamp encoded in ``ulid``. Inverse of the prefix."""
    if len(ulid) != ULID_LENGTH:
        raise ValueError(f"A ULID is {ULID_LENGTH} characters; got {len(ulid)}.")
    value = 0
    for char in ulid:
        try:
            value = (value << 5) | CROCKFORD_ALPHABET.index(char.upper())
        except ValueError as exc:
            raise ValueError(
                f"{char!r} is not a Crockford base32 character."
            ) from exc
    return value >> 80


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def utc_now_stamp() -> str:
    """Current UTC instant in the schema's exact 20-character format."""
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def normalize_stamp(value: str) -> str:
    """Coerce an ISO-8601-ish string into the schema's exact format.

    Used for data arriving from outside Katagiri. Sub-second precision is
    truncated, not rounded: an event's ordering comes from its ULID, so the
    timestamp only has to name the second it happened in.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"{value!r} is not an ISO-8601 timestamp Katagiri can read."
        ) from exc
    if parsed.tzinfo is None:
        # No offset given: the only honest reading of a naive stamp in a local
        # study log is local time.
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc).replace(microsecond=0).strftime(TS_FORMAT)


def local_tz_name() -> str:
    """Best-effort name of the zone this machine is in.

    Windows has no IANA zone database, so without the optional ``tzdata``
    package the honest answer is the platform's own abbreviation ("W. Europe
    Summer Time") or a fixed offset. The column is documentation, not a key —
    nothing joins on it — so a non-IANA value is recorded rather than invented.
    Callers that know the real IANA name should pass ``tz=`` explicitly.
    """
    local = datetime.now().astimezone()
    name = local.tzname()
    if name:
        return name
    return f"UTC{local.strftime('%z') or '+0000'}"


def _zone(tz: str | None) -> Any:
    """Resolve ``tz`` to a tzinfo, or ``None`` meaning "use system local"."""
    if tz is None or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # A zone we cannot resolve (typically no tzdata on Windows) must not
        # cost us the event. Fall back to system local for the day boundary and
        # keep the caller's string in the tz column.
        _log.warning(
            "Time zone %r could not be resolved; day_key computed from system "
            "local time instead.",
            tz,
        )
        return None


def _day_key(stamp: str, tz: str | None) -> str | None:
    """Local calendar date of ``stamp``, or ``None`` if it cannot be parsed."""
    try:
        moment = normalize_stamp(stamp)
    except ValueError:
        return None
    parsed = datetime.strptime(moment, TS_FORMAT).replace(tzinfo=timezone.utc)
    zone = _zone(tz)
    local = parsed.astimezone(zone) if zone is not None else parsed.astimezone()
    return local.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _encode_payload(payload: Any) -> str | None:
    """JSON-encode ``payload``. A string is assumed to be JSON already.

    Never pass secrets: see the module docstring.
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        # Validated by the schema's json_valid CHECK rather than re-encoded, so
        # a caller holding canonical JSON keeps it byte for byte.
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def append_event(
    conn: sqlite3.Connection,
    *,
    type: str,
    session_id: str,
    item_id: str | None = None,
    direction: str | None = None,
    grade: int | None = None,
    latency_ms: int | None = None,
    answer_given: str | None = None,
    expected: str | None = None,
    audio_ref: str | None = None,
    media_ref: str | None = None,
    payload: Any = None,
    dedupe_key: str | None = None,
    ts_device: str | None = None,
    tz: str | None = None,
) -> str:
    """Append one event and return its id.

    ``ts_server`` is stamped now (UTC, whole seconds). ``ts_device`` defaults to
    it and is otherwise stored verbatim, so a badly formatted device clock is
    refused by the schema rather than silently rewritten. ``day_key`` is the
    local date of ``ts_device`` in ``tz``.

    When ``dedupe_key`` is given and already present, the existing event's id is
    returned and nothing is written — a retried tool call is not a second event.
    Only that one UNIQUE constraint is absorbed; every CHECK the schema imposes
    still raises.

    Does not manage a transaction: call it inside one when the event has to land
    together with something else (as :func:`mark_item` does).
    """
    ts_server = utc_now_stamp()
    device = ts_server if ts_device is None else ts_device
    zone_name = tz if tz is not None else local_tz_name()
    day_key = _day_key(device, tz) or _day_key(ts_server, tz)
    event_id = new_ulid()

    conn.execute(
        """
        INSERT INTO event (
            id, dedupe_key, ts_device, ts_server, tz, day_key, session_id,
            type, item_id, direction, grade, latency_ms, answer_given,
            expected, audio_ref, media_ref, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_key) DO NOTHING
        """,
        (
            event_id,
            dedupe_key,
            device,
            ts_server,
            zone_name,
            day_key,
            session_id,
            type,
            item_id,
            direction,
            grade,
            latency_ms,
            answer_given,
            expected,
            audio_ref,
            media_ref,
            _encode_payload(payload),
        ),
    )

    if dedupe_key is None:
        return event_id

    # Either the row just written or the one that was already there; reading it
    # back is cheaper than reasoning about rowcount across sqlite3 versions.
    row = conn.execute(
        "SELECT id FROM event WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()
    return event_id if row is None else str(row[0])


def mark_item(
    conn: sqlite3.Connection,
    item_id: str,
    mark: str,
    note: str | None = None,
    *,
    tz: str | None = None,
) -> dict[str, Any]:
    """Record a manual known/unknown/suspect mark, and the event for it.

    The mark row and its event are written in one transaction: a mark that is not
    in the log did not happen, and an event with no mark behind it is a lie. Both
    or neither.

    ``item_id`` is resolved through the alias table first, so a mark applied to a
    retired id lands on the item it actually refers to.

    The mark's ``ts`` is part of ``manual_marks``' primary key, at whole-second
    resolution. Re-marking the same item inside the same second therefore
    overwrites that second's row (the event log still gets both events, which is
    where the history lives).
    """
    if mark not in MARK_EVENT_TYPES:
        raise ValueError(
            f"mark must be one of {sorted(MARK_EVENT_TYPES)}; got {mark!r}."
        )

    resolved = resolve_alias(conn, item_id)
    canonical = resolved["canonical_id"]
    ts = utc_now_stamp()

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO manual_marks (item_id, mark, ts, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_id, ts) DO UPDATE SET
                mark = excluded.mark,
                note = excluded.note
            """,
            (canonical, mark, ts, note),
        )
        event_id = append_event(
            conn,
            type=MARK_EVENT_TYPES[mark],
            session_id=f"mark:{ts}",
            item_id=canonical,
            ts_device=ts,
            tz=tz,
            payload={
                "mark": mark,
                "note": note,
                "requested_item_id": item_id,
                "redirected": resolved["redirected"],
            },
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    return {
        "item_id": canonical,
        "requested_item_id": item_id,
        "redirected": resolved["redirected"],
        "mark": mark,
        "ts": ts,
        "event_id": event_id,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def recent_events(
    conn: sqlite3.Connection,
    limit: int = 50,
    type: str | None = None,
    since_day: str | None = None,
) -> list[dict[str, Any]]:
    """Most recent events first.

    Ordered by ``id``: a ULID sorts by time, so this stays correct for two events
    recorded in the same second where ``ts_server`` alone would tie.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1; got {limit}.")

    clauses: list[str] = []
    params: list[Any] = []
    if type is not None:
        clauses.append("type = ?")
        params.append(type)
    if since_day is not None:
        clauses.append("day_key >= ?")
        params.append(since_day)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"SELECT * FROM event {where} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Study-log import
# ---------------------------------------------------------------------------


def import_study_log(
    conn: sqlite3.Connection, jsonl_path: Path | str
) -> dict[str, Any]:
    """Import the scripts study-log JSONL into the event log. Idempotent.

    Each record is ``{"ts", "type": "study_session", "minutes", "activities",
    "items_mined", "notes"}``. The dedupe key is ``study:<normalised ts>``, so
    re-importing the same file appends nothing: only genuinely new lines land.

    Records of any other ``type`` are counted and skipped rather than guessed at.
    An unreadable line fails the import loudly — a study log with a corrupt line
    is a problem to look at, not to skip past.
    """
    path = Path(jsonl_path)
    if not path.is_file():
        raise FileNotFoundError(f"Study log {path} does not exist.")

    imported = 0
    duplicate = 0
    skipped = 0
    total = 0

    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            total += 1
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{number} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"{path}:{number} is not a JSON object."
                )
            if record.get("type") != STUDY_LOG_TYPE:
                skipped += 1
                continue

            raw_ts = record.get("ts")
            if not isinstance(raw_ts, str) or not raw_ts.strip():
                raise ValueError(
                    f"{path}:{number} has no usable 'ts' field; it is the dedupe "
                    "key, so the line cannot be imported without one."
                )
            stamp = normalize_stamp(raw_ts)
            dedupe_key = f"study:{stamp}"

            before = conn.execute(
                "SELECT 1 FROM event WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()

            append_event(
                conn,
                type=STUDY_LOG_TYPE,
                session_id=STUDY_LOG_SESSION_ID,
                ts_device=stamp,
                dedupe_key=dedupe_key,
                payload={
                    "minutes": record.get("minutes"),
                    "activities": record.get("activities"),
                    "items_mined": record.get("items_mined"),
                    "notes": record.get("notes"),
                    "source": path.name,
                },
            )
            if before is None:
                imported += 1
            else:
                duplicate += 1

    return {
        "path": str(path),
        "records": total,
        "imported": imported,
        "duplicate": duplicate,
        "skipped": skipped,
    }


__all__ = [
    "CROCKFORD_ALPHABET",
    "MARK_EVENT_TYPES",
    "STUDY_LOG_SESSION_ID",
    "STUDY_LOG_TYPE",
    "TS_FORMAT",
    "ULID_LENGTH",
    "append_event",
    "import_study_log",
    "local_tz_name",
    "mark_item",
    "new_ulid",
    "normalize_stamp",
    "recent_events",
    "ulid_time_ms",
    "utc_now_stamp",
]
