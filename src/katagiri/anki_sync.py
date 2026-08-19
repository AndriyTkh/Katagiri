r"""Incremental sync of Anki's review history into Katagiri's event log.

Anki records every answered card in ``revlog`` (one row per review, ``id`` = the
review's epoch-millisecond timestamp and primary key). That table is *not*
mirrored — a mirror of it would be a second, ever-growing copy of data Katagiri
only ever reads in daily aggregate. Instead each local calendar day of reviews
becomes one appended ``review_batch`` event, which is what streaks, ``Today.md``
and the stop-gate actually consume.

The live ``collection.anki2`` is never opened; see :mod:`katagiri.anki_snapshot`
for why. This module follows the same copy-then-read discipline for ``revlog``
that that module does for ``cards``/``notes``, and for the same reasons: copy the
collection (main file first, then its journal siblings), fold the copied WAL into
the copy, read it with ``mode=ro&immutable=1``, ``PRAGMA integrity_check``, then
delete the copy. The discipline is repeated here rather than shared because each
foreign-database reader in Katagiri owns its own copy (so does
:mod:`katagiri.ankimorphs_ingest`); nothing in ``anki_snapshot`` is edited by
this module.

One :func:`sync_anki` call therefore takes two copies: ``snapshot_anki`` takes
one to refresh the card/note mirror, and this module takes one for ``revlog``.
Two copies of a collection is cheap next to the alternative — reaching into
another module's private helpers, or holding one copy across two public APIs that
do not share a contract about it.

The incremental cursor
----------------------
No derived cursor table: the high-water mark is a single ``metadata`` row,
``anki_sync_last_revlog_id`` — the largest ``revlog.id`` already accounted for.
Each run reads only ``id > cursor``, so the cost of a run is proportional to new
reviews, not to collection age.

Idempotency
-----------
The obvious dedupe key, ``anki_revlog:<day>``, is wrong: reviews for a day arrive
*after* that day has been synced whenever a sync runs mid-session, and a second
key collision would silently drop them (``append_event`` absorbs a duplicate
key). The key therefore names the batch, not the day:

    dedupe_key = f"anki_revlog:{day_key}:{last_id}"

``last_id`` is the largest ``revlog.id`` in that batch, so:

* re-running with no new reviews reads no rows and appends nothing;
* a catch-up run over the same day appends a *new* batch (new ``last_id``) rather
  than colliding with, or overwriting, the batch already logged;
* a batch retried after a crash mid-transaction produces the identical key and
  collapses into the row already there.

A day can therefore hold several ``review_batch`` events, and their payloads are
*additive*: each counts only the reviews in its own batch. Consumers sum the
batches sharing a ``day_key``; they must not treat one batch as the day's total.
As a belt-and-braces guard, a day whose newest id is not past the cursor is
skipped even if a row for it somehow reached the grouping step.

The events and the cursor advance in one transaction. A failure anywhere in the
append loop leaves the cursor where it was, so the next run re-derives exactly
the same batches. (The card/note mirror is rebuilt in its own transaction by
``snapshot_anki``; it is derived state that every run replaces wholesale, so it
has nothing to be atomic with.)

Manual rescheduling is not studying: ``revlog`` rows whose ``ease`` is outside
1-4 are Anki's "set due date"/reschedule bookkeeping entries, and counting them
would inflate streaks and quietly satisfy the stop-gate. They are excluded from
the counts but still move the cursor past themselves.

Scheduling: Katagiri does not create scheduled tasks — a personal tool should not
install background jobs behind its owner's back. To catch up once a day at 22:30,
run this yourself (one line, from the repository root):

    schtasks /Create /TN "Katagiri Anki Sync" /SC DAILY /ST 22:30 /F /TR "cmd /c cd /d C:\ProjectsC\RandomPr\Katagiri && uv run python -m katagiri.anki_sync run"

Check it with ``schtasks /Query /TN "Katagiri Anki Sync"`` and remove it with
``schtasks /Delete /TN "Katagiri Anki Sync" /F``.

CLI::

    python -m katagiri.anki_sync run    [--collection PATH] [--tz ZONE] [--db PATH]
    python -m katagiri.anki_sync status [--db PATH]

Exit codes: 0 on success, 2 when Anki, the collection, or the configuration is
not there (nothing is written in that case).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from katagiri import db
from katagiri.anki_snapshot import (
    AnkiCollectionNotFoundError,
    AnkiSnapshotError,
    MirrorResult,
    find_collection,
    snapshot_anki,
)
from katagiri.config import ConfigError, get_config
from katagiri.events import TS_FORMAT, append_event
from katagiri.logging_setup import get_logger

try:  # pragma: no cover - depends on whether tzdata is available locally
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - zoneinfo is stdlib on 3.12
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]

#: ``metadata`` key holding the largest ``revlog.id`` already synced.
CURSOR_KEY: Final = "anki_sync_last_revlog_id"

REVIEW_BATCH_TYPE: Final = "review_batch"
SYNC_SESSION_ID: Final = "anki-sync"
DEDUPE_PREFIX: Final = "anki_revlog"

#: The grades Anki records for an actual answer; anything else is bookkeeping.
EASE_VALUES: Final = (1, 2, 3, 4)

_SCRATCH_SUBDIR: Final = "anki-sync"
# '-shm' is deliberately absent: it is a derived index of the WAL, and a stale
# one only misleads the recovery that rebuilds it anyway.
_JOURNAL_SUFFIXES: Final = ("-wal", "-journal")

_logger = get_logger("anki_sync")


class AnkiSyncError(RuntimeError):
    """Raised when the review history cannot be read or replayed.

    Failures that belong to the collection itself (missing, corrupt,
    unsupported schema) surface as :class:`katagiri.anki_snapshot.AnkiSnapshotError`
    subclasses instead — this module does not restate another module's verdicts.
    """


@dataclass(frozen=True, slots=True)
class DayBatch:
    """One appended ``review_batch`` event.

    ``ease_hist`` is keyed by the integer ease 1-4 and always carries all four
    keys, so a consumer never has to distinguish "no lapses" from "field
    missing". JSON renders those keys as strings, which is what lands in the
    event payload.
    """

    day_key: str
    reviews: int
    cards: int
    ease_hist: dict[int, int]
    first_id: int
    last_id: int
    event_id: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Outcome of one :func:`sync_anki` call.

    ``batches`` counts the ``review_batch`` events appended (0 for a no-op
    re-run) and ``reviews`` the reviews they cover; ``days`` carries the detail.
    ``skipped`` counts ``revlog`` rows read but not counted as reviews — manual
    reschedules, and any row whose timestamp is unusable.
    """

    batches: int
    reviews: int
    first_day: str | None
    last_day: str | None
    mirror: MirrorResult
    cursor: int
    cursor_before: int
    skipped: int = 0
    days: tuple[DayBatch, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class _Group:
    """Reviews for one local day, accumulated while scanning ``revlog``."""

    day_key: str
    first_id: int
    last_id: int
    last_ms: int
    reviews: int = 0
    cards: set[int] = field(default_factory=set)
    ease_hist: dict[int, int] = field(default_factory=lambda: {e: 0 for e in EASE_VALUES})


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def _zone(tz: str | None) -> Any:
    """Resolve ``tz`` to a tzinfo, or ``None`` meaning "use system local".

    Mirrors :mod:`katagiri.events` deliberately: the day a review is grouped
    under here must be the ``day_key`` ``append_event`` derives from the same
    timestamp, including the fallback when a zone cannot be resolved (typically
    a Windows host without ``tzdata``).
    """
    if tz is None or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        _logger.warning(
            "Time zone %r could not be resolved; review days were grouped by "
            "system local time instead.",
            tz,
        )
        return None


def _instant(ms: int) -> datetime | None:
    """UTC instant of a ``revlog.id``, or ``None`` if it is not a usable one."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _utc_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(TS_FORMAT)


def _local_day(moment: datetime, zone: Any) -> str:
    local = moment.astimezone(zone) if zone is not None else moment.astimezone()
    return local.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# The cursor
# ---------------------------------------------------------------------------


def read_cursor(conn: sqlite3.Connection) -> int:
    """Largest ``revlog.id`` already synced; 0 when nothing has been.

    A value that is not an integer is an error, not something to reset: a run
    that silently restarts from zero re-derives every historical batch, and
    where a day was logged in several batches the re-derived one would
    double-count that day.
    """
    row = conn.execute(
        "SELECT value FROM metadata WHERE key = ?", (CURSOR_KEY,)
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    try:
        return int(str(row[0]))
    except ValueError as exc:
        raise AnkiSyncError(
            f"The Anki sync cursor in metadata['{CURSOR_KEY}'] is "
            f"{row[0]!r}, which is not a revlog id. Refusing to guess: fix or "
            "delete that row (deleting it re-reads the whole review history)."
        ) from exc


def _write_cursor(conn: sqlite3.Connection, value: int) -> None:
    """Stamp the cursor (inside the caller's transaction)."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value, updated_ts) VALUES (?, ?, ?)",
        (CURSOR_KEY, str(value), _utc_now()),
    )


def last_batch(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The most recently appended ``review_batch`` event, or ``None``.

    Ordered by ``id``: a ULID sorts by time, so this stays correct for two
    batches appended inside the same second.
    """
    row = conn.execute(
        "SELECT id, day_key, ts_device, payload FROM event "
        "WHERE type = ? ORDER BY id DESC LIMIT 1",
        (REVIEW_BATCH_TYPE,),
    ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):  # pragma: no cover - json_valid CHECK guards this
        payload = {}
    return {
        "event_id": row["id"],
        "day_key": row["day_key"],
        "ts_device": row["ts_device"],
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# Reading revlog from a copy
# ---------------------------------------------------------------------------


def _immutable_uri(path: Path) -> str:
    """The read URI for a copy: read-only and immutable."""
    return f"file:{quote(path.as_posix(), safe='/:')}?mode=ro&immutable=1"


def _copy_collection(source: Path, scratch_dir: Path) -> tuple[Path, bool]:
    """Copy ``source``, then its journal siblings, into ``scratch_dir``.

    Returns the copy's path and whether any journal sibling had content. Main
    file first: a WAL captured no earlier than the main file either replays
    cleanly or stops at its first torn frame, whereas a WAL captured *first* can
    be replayed over a main file already checkpointed past it — the direction
    that corrupts.
    """
    destination = scratch_dir / source.name
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        raise AnkiSyncError(
            f"Could not copy the Anki collection {source} to {destination}: "
            f"{exc}. The live collection is never read in place, so the sync "
            "cannot continue without a copy."
        ) from exc

    had_journal = False
    for suffix in _JOURNAL_SUFFIXES:
        sibling = Path(str(source) + suffix)
        try:
            if not sibling.is_file():
                continue
            copied = Path(str(destination) + suffix)
            shutil.copy2(sibling, copied)
            # Size of the copy, not of the source: the copy is what recovery
            # reads, and the source may have moved on since.
            had_journal = had_journal or copied.stat().st_size > 0
        except OSError as exc:
            # A vanished or locked journal is not fatal on its own; the copy is
            # then simply older, and the reviews it misses arrive next run —
            # which is exactly what the cursor is for.
            _logger.warning(
                "Could not copy the '%s' journal alongside the collection (%s); "
                "this sync may miss the most recent reviews.",
                suffix,
                exc,
            )
    return destination, had_journal


def _recover_journal(copy_path: Path, origin: Path) -> None:
    """Fold the copied WAL into the copy, so ``immutable=1`` sees it.

    Opens the *copy* read-write — never the live collection — and switches it to
    ``journal_mode=DELETE``, which checkpoints the WAL into the main file and
    unlinks it. Without this, ``immutable=1`` would ignore the ``-wal`` and
    return the collection as of its last checkpoint, so a sync run right after a
    study session would read none of it: exactly the reviews it exists to pick
    up live in that WAL while Anki is open.

    Errors name ``origin``, the live file, not the copy: the copy is deleted
    before the exception reaches the caller.
    """
    try:
        conn = sqlite3.connect(str(copy_path), isolation_level=None)
    except sqlite3.Error as exc:
        raise AnkiSyncError(
            f"The copy of the Anki collection {origin} could not be opened to "
            f"recover its journal: {exc}. The file may be damaged."
        ) from exc
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error as exc:
        raise AnkiSyncError(
            f"Could not recover the write-ahead log into the copy of the Anki "
            f"collection {origin}: {exc}. Close Anki and try again."
        ) from exc
    finally:
        conn.close()


def _check_integrity(conn: sqlite3.Connection, origin: Path) -> None:
    """Run ``PRAGMA integrity_check`` and fail loudly on anything but ``ok``."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        # A badly damaged file fails here rather than reporting problems: SQLite
        # cannot even walk the b-tree. Same verdict either way.
        raise AnkiSyncError(
            f"PRAGMA integrity_check could not run on the copy of the Anki "
            f"collection {origin}: {exc}. The collection appears corrupt; run "
            "Tools > Check Database in Anki."
        ) from exc
    problems = [str(row[0]) for row in rows]
    if problems != ["ok"]:
        detail = "; ".join(problems[:10])
        raise AnkiSyncError(
            "The copy of the Anki collection failed PRAGMA integrity_check, so "
            f"no reviews were synced ({origin}): {detail}. Run Tools > Check "
            "Database in Anki."
        )


def read_revlog(source: Path | str, after_id: int = 0) -> list[tuple[int, int, int]]:
    """Reviews newer than ``after_id`` as ``(id, cid, ease)``, oldest first.

    Takes its own scratch copy of the collection and deletes it afterwards; the
    live file is never opened. ``after_id`` is applied in SQL, so an unchanged
    collection costs one index seek rather than a full scan.
    """
    origin = Path(source)
    scratch_root = get_config().scratch_root / _SCRATCH_SUBDIR
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AnkiSyncError(
            f"Could not create the scratch directory {scratch_root} for the "
            f"Anki review sync: {exc}. The live collection is never read in "
            "place, so a writable scratch area is required."
        ) from exc

    scratch_dir = Path(tempfile.mkdtemp(prefix="revlog-", dir=scratch_root))
    try:
        copy_path, _ = _copy_collection(origin, scratch_dir)
        _recover_journal(copy_path, origin)

        read_conn = sqlite3.connect(_immutable_uri(copy_path), uri=True)
        try:
            _check_integrity(read_conn, origin)
            names = {
                str(row[0])
                for row in read_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "revlog" not in names:
                raise AnkiSyncError(
                    f"The Anki collection {origin} has no 'revlog' table, so it "
                    "holds no review history to sync. Every Anki 2.1 collection "
                    "has one; check that this file is really a collection."
                )
            try:
                return [
                    (int(review_id), int(card_id), int(ease))
                    for review_id, card_id, ease in read_conn.execute(
                        "SELECT id, cid, ease FROM revlog WHERE id > ? ORDER BY id",
                        (int(after_id),),
                    )
                ]
            except sqlite3.DatabaseError as exc:
                # integrity_check passed but a read still failed: treat it as
                # damage rather than syncing a half-read history.
                raise AnkiSyncError(
                    f"Reading revlog from the copy of the Anki collection "
                    f"{origin} failed: {exc}."
                ) from exc
        finally:
            read_conn.close()
    finally:
        # The copy is a liability once read: a full duplicate of the learner's
        # collection sitting in scratch.
        shutil.rmtree(scratch_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_by_day(
    rows: list[tuple[int, int, int]], *, tz: str | None, cursor: int
) -> tuple[list[_Group], int, int]:
    """Fold ``revlog`` rows into one group per local day.

    Returns the groups (oldest day first), the number of rows not counted as
    reviews, and the new cursor. Rows that are skipped still move the cursor:
    they have been seen, and re-reading them every run forever would be a
    growing tax for no new information.

    A group whose newest id is not past ``cursor`` is dropped: such a batch is
    already in the log, and appending it again under the same dedupe key would
    be absorbed silently rather than noticed.
    """
    zone = _zone(tz)
    groups: dict[str, _Group] = {}
    skipped = 0
    new_cursor = cursor

    for review_id, card_id, ease in rows:
        new_cursor = max(new_cursor, review_id)
        moment = _instant(review_id)
        if moment is None:
            # A revlog id outside the range a timestamp can hold is not a review
            # anyone can date; say so once and move on rather than failing the
            # whole sync over one unusable row.
            _logger.warning(
                "Skipped a revlog row whose id (%d) is not a usable timestamp.",
                review_id,
            )
            skipped += 1
            continue
        if ease not in EASE_VALUES:
            # Anki's manual "set due date"/reschedule bookkeeping. Not studying.
            skipped += 1
            continue

        day_key = _local_day(moment, zone)
        group = groups.get(day_key)
        if group is None:
            group = _Group(
                day_key=day_key,
                first_id=review_id,
                last_id=review_id,
                last_ms=review_id,
            )
            groups[day_key] = group
        else:
            group.first_id = min(group.first_id, review_id)
            if review_id >= group.last_id:
                group.last_id = review_id
                group.last_ms = review_id
        group.reviews += 1
        group.cards.add(card_id)
        group.ease_hist[ease] += 1

    ordered = [
        group for _, group in sorted(groups.items()) if group.last_id > cursor
    ]
    return ordered, skipped, new_cursor


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _append_batches(
    conn: sqlite3.Connection,
    groups: list[_Group],
    *,
    new_cursor: int,
    tz: str | None,
) -> tuple[DayBatch, ...]:
    """Append one event per day and advance the cursor, all or nothing.

    The cursor and the events it accounts for must land together: a cursor that
    moved past events that were never appended is a silently lost study day, and
    no later run would notice, because the rows behind it are never read again.
    """
    batches: list[DayBatch] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for group in groups:
            moment = _instant(group.last_ms)
            assert moment is not None  # every grouped row had a usable instant
            event_id = append_event(
                conn,
                type=REVIEW_BATCH_TYPE,
                session_id=SYNC_SESSION_ID,
                # The batch's newest review: its local date in ``tz`` is the
                # group's day_key by construction, so the event's day_key
                # (which append_event derives) agrees with the grouping.
                ts_device=_utc_stamp(moment),
                tz=tz,
                dedupe_key=f"{DEDUPE_PREFIX}:{group.day_key}:{group.last_id}",
                payload={
                    "reviews": group.reviews,
                    "cards": len(group.cards),
                    "ease_hist": {str(e): group.ease_hist[e] for e in EASE_VALUES},
                    "first_id": group.first_id,
                    "last_id": group.last_id,
                    "source": "anki_revlog",
                },
            )
            batches.append(
                DayBatch(
                    day_key=group.day_key,
                    reviews=group.reviews,
                    cards=len(group.cards),
                    ease_hist=dict(group.ease_hist),
                    first_id=group.first_id,
                    last_id=group.last_id,
                    event_id=event_id,
                )
            )
        _write_cursor(conn, new_cursor)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise
    return tuple(batches)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def sync_anki(
    conn: sqlite3.Connection,
    *,
    collection_path: Path | str | None = None,
    tz: str | None = None,
) -> SyncResult:
    """Refresh the Anki mirror, then append a ``review_batch`` per new day.

    ``conn`` is a migrated Katagiri database (see :func:`katagiri.db.open_db`).
    ``collection_path`` overrides discovery; ``tz`` names the zone the day
    boundary is drawn in (default: this machine's local time, as
    :mod:`katagiri.events` reads it).

    Idempotent: a run with no new reviews refreshes the mirror and appends
    nothing. Raises :class:`katagiri.anki_snapshot.AnkiSnapshotError` (missing,
    corrupt or unsupported collection) or :class:`AnkiSyncError`; the event log
    and the cursor are untouched unless the whole replay succeeded.
    """
    source = Path(collection_path) if collection_path is not None else find_collection()
    if not source.is_file():
        # Checked before anything is written, so an absent collection costs a
        # message and nothing else.
        raise AnkiCollectionNotFoundError(
            f"The Anki collection {source} does not exist or is not a file, so "
            "there is no review history to sync."
        )

    # The cursor is read before the mirror is rebuilt so that a failure in the
    # rebuild cannot be mistaken for "no new reviews".
    cursor = read_cursor(conn)
    mirror = snapshot_anki(conn, collection_path=source)

    rows = read_revlog(source, cursor)
    groups, skipped, new_cursor = _group_by_day(rows, tz=tz, cursor=cursor)

    if not groups and new_cursor == cursor:
        _logger.info("No new Anki reviews since revlog id %d.", cursor)
        return SyncResult(
            batches=0,
            reviews=0,
            first_day=None,
            last_day=None,
            mirror=mirror,
            cursor=cursor,
            cursor_before=cursor,
            skipped=skipped,
        )

    batches = _append_batches(conn, groups, new_cursor=new_cursor, tz=tz)
    reviews = sum(batch.reviews for batch in batches)
    _logger.info(
        "Synced %d Anki review(s) into %d daily batch(es)%s; cursor %d -> %d.",
        reviews,
        len(batches),
        f", skipping {skipped} non-review row(s)" if skipped else "",
        cursor,
        new_cursor,
    )
    return SyncResult(
        batches=len(batches),
        reviews=reviews,
        first_day=batches[0].day_key if batches else None,
        last_day=batches[-1].day_key if batches else None,
        mirror=mirror,
        cursor=new_cursor,
        cursor_before=cursor,
        skipped=skipped,
        days=batches,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.anki_sync",
        description="Sync Anki's review history into the Katagiri event log.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="sync new reviews and refresh the mirror")
    run.add_argument("--db", type=Path, default=None, help="database to write")
    run.add_argument(
        "--collection", type=Path, default=None, help="collection.anki2 to read"
    )
    run.add_argument(
        "--tz", default=None, help="IANA zone for the day boundary (default: local)"
    )

    status = sub.add_parser("status", help="print the cursor and the last batch")
    status.add_argument("--db", type=Path, default=None, help="database to read")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.anki_sync``."""
    args = _build_parser().parse_args(argv)

    try:
        conn = db.open_db(args.db)
        try:
            if args.command == "run":
                result = sync_anki(
                    conn, collection_path=args.collection, tz=args.tz
                )
                print(
                    f"reviews synced : {result.reviews}\n"
                    f"batches         : {result.batches}\n"
                    f"days            : "
                    f"{result.first_day or '-'} .. {result.last_day or '-'}\n"
                    f"revlog cursor   : {result.cursor_before} -> {result.cursor}\n"
                    f"mirror          : {result.mirror.cards} cards, "
                    f"{result.mirror.notes} notes"
                    f"{' (stale)' if result.mirror.stale else ''}"
                )
                return 0

            if args.command == "status":
                cursor = read_cursor(conn)
                latest = last_batch(conn)
                print(f"revlog cursor  : {cursor}")
                if latest is None:
                    print("last batch     : none appended yet")
                else:
                    payload = latest["payload"]
                    print(
                        f"last batch day : {latest['day_key']}\n"
                        f"  reviews      : {payload.get('reviews')}\n"
                        f"  cards        : {payload.get('cards')}\n"
                        f"  event        : {latest['event_id']}"
                    )
                return 0
        finally:
            conn.close()
    except (
        AnkiSyncError,
        AnkiSnapshotError,
        ConfigError,
        db.DatabaseError,
        OSError,
        sqlite3.Error,
    ) as exc:
        print(f"error: {exc}")
        return 2

    return 2  # pragma: no cover - argparse rejects unknown commands first


__all__ = [
    "CURSOR_KEY",
    "DEDUPE_PREFIX",
    "EASE_VALUES",
    "REVIEW_BATCH_TYPE",
    "SYNC_SESSION_ID",
    "AnkiSyncError",
    "DayBatch",
    "SyncResult",
    "last_batch",
    "main",
    "read_cursor",
    "read_revlog",
    "sync_anki",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
