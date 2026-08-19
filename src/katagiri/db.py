"""SQLite connection handling and schema migration for Katagiri.

The database lives wherever :mod:`katagiri.config` points ``db_path`` — by
default ``%LOCALAPPDATA%\\Katagiri\\katagiri.db``, i.e. outside the repository.

Schema versioning uses SQLite's own ``PRAGMA user_version``; there is no
migrations bookkeeping table. Migration files ship inside the package as
``migrations/NNNN_name.sql`` and are applied in numeric order, each inside its
own transaction, with ``user_version`` stamped in that same transaction. A
migration therefore either lands whole or not at all.

Before touching a database that already holds objects, the runner takes a
``VACUUM INTO`` snapshot under ``<config dir>/backups/``. Backups sit next to
the configuration rather than next to the database so that a database on a
synced or scratch volume cannot quietly delete its own safety net.

Migration scripts must not manage transactions or the version stamp themselves;
:func:`discover_migrations` rejects a script that tries to. A migration that
needs to rebuild a table in place (the twelve-step ``ALTER TABLE`` dance) cannot
turn foreign keys off, because ``PRAGMA foreign_keys`` is a silent no-op inside a
transaction and everything here runs inside one. Such a script declares
``PRAGMA defer_foreign_keys = ON;`` instead: that *is* settable mid-transaction,
holds enforcement until COMMIT, and resets itself afterwards, so the migration
can leave references temporarily dangling but still cannot commit a broken graph.

Derived tables (FTS indexes, JMdict import, Anki mirror, caches) are created by
the initial migration but are meant to be evolved by drop-and-rebuild scripts,
never by later migrations. See ``docs/db-schema.md``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from katagiri.config import config_dir, get_config

MIGRATIONS_DIR_NAME: Final = "migrations"
BACKUP_DIR_NAME: Final = "backups"
_MIGRATION_NAME_RE: Final = re.compile(r"^(\d{4})_([A-Za-z0-9][A-Za-z0-9_-]*)\.sql$")
_MAX_ALIAS_HOPS: Final = 16
BUSY_TIMEOUT_MS: Final = 5000

# Statement-level transaction control a migration script must not contain: the
# runner owns the transaction and the version stamp.
_FORBIDDEN_LEADING_KEYWORDS: Final = frozenset(
    {"BEGIN", "COMMIT", "END", "ROLLBACK", "VACUUM", "SAVEPOINT", "RELEASE", "ATTACH",
     "DETACH"}
)
_BLOCK_COMMENT_RE: Final = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE: Final = re.compile(r"--[^\n]*")
_STRING_LITERAL_RE: Final = re.compile(r"'(?:[^']|'')*'")
# Trigger bodies legitimately contain BEGIN ... END, so they are removed before
# statement-level scanning. (A trigger body containing its own CASE ... END would
# defeat this; none does, and the scan errs toward rejecting rather than missing.)
_TRIGGER_BODY_RE: Final = re.compile(
    r"\bCREATE\s+(?:TEMP\s+|TEMPORARY\s+)?TRIGGER\b.*?\bEND\b\s*;",
    re.DOTALL | re.IGNORECASE,
)
_LEADING_WORD_RE: Final = re.compile(r"[A-Za-z_]+")


class DatabaseError(RuntimeError):
    """Raised for Katagiri-level database problems (not raw sqlite3 errors)."""


class MigrationError(DatabaseError):
    """Raised when migrations are malformed or fail to apply.

    ``backup`` carries the pre-migration snapshot path when one was taken, so a
    caller handling the failure can name the file to restore from.
    """

    def __init__(self, message: str, *, backup: Path | None = None) -> None:
        super().__init__(message)
        self.backup = backup


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def database_path() -> Path:
    """The configured database path."""
    return get_config().db_path


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with Katagiri's required pragmas.

    ``foreign_keys`` is ON (SQLite defaults it OFF, per-connection), the journal
    is WAL, and the text encoding is UTF-8. Autocommit mode is used
    (``isolation_level=None``) because this module drives transactions
    explicitly and because ``VACUUM INTO`` cannot run inside one.

    ``recursive_triggers`` is ON because the append-only guarantee depends on it:
    ``INSERT OR REPLACE`` resolves a conflict by deleting the existing row, and
    with recursive triggers off that implicit delete does **not** fire BEFORE
    DELETE triggers — so an ``INSERT OR REPLACE`` into ``event`` would quietly
    overwrite logged history instead of aborting.

    The busy timeout is set once, via sqlite3's ``timeout`` argument (which is
    exactly ``sqlite3_busy_timeout``). Issuing ``PRAGMA busy_timeout`` as well
    would just be a second, contradictory place to change the same value.
    """
    path = Path(db_path) if db_path is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000
    )
    conn.row_factory = sqlite3.Row
    conn.text_factory = str
    try:
        # Encoding only takes effect on a not-yet-created database; harmless
        # otherwise, and makes the intent explicit rather than relying on the
        # library default.
        conn.execute("PRAGMA encoding = 'UTF-8'")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def open_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Connect and bring the schema up to date. The normal entry point."""
    conn = connect(db_path)
    try:
        migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


# ---------------------------------------------------------------------------
# Migration discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Migration:
    """One discovered migration file."""

    version: int
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What a :func:`migrate` call did."""

    from_version: int
    to_version: int
    applied: tuple[int, ...]
    backup: Path | None

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def migrations_dir() -> Path:
    """The packaged migrations directory."""
    return Path(__file__).resolve().parent / MIGRATIONS_DIR_NAME


def discover_migrations(directory: Path | str | None = None) -> tuple[Migration, ...]:
    """Return the migrations in ``directory`` (default: packaged), ordered.

    Any ``.sql`` file that does not match ``NNNN_name.sql`` is an error rather
    than a silent skip: a typo'd filename must not mean a skipped migration.
    """
    base = Path(directory) if directory is not None else migrations_dir()
    if not base.is_dir():
        raise MigrationError(f"Migrations directory {base} does not exist.")

    found: list[Migration] = []
    for path in sorted(base.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".sql":
            continue
        match = _MIGRATION_NAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"Migration file {path.name} in {base} is not named "
                "NNNN_description.sql (four digits, underscore, description)."
            )
        version = int(match.group(1))
        if version < 1:
            raise MigrationError(
                f"Migration {path.name} has version 0; versions start at 1 "
                "because user_version 0 means 'empty database'."
            )
        _validate_script(path)
        found.append(Migration(version=version, name=match.group(2), path=path))

    found.sort(key=lambda mig: mig.version)
    for earlier, later in zip(found, found[1:]):
        if earlier.version == later.version:
            raise MigrationError(
                f"Duplicate migration version {earlier.version}: "
                f"{earlier.path.name} and {later.path.name}."
            )
    return tuple(found)


def _validate_script(path: Path) -> None:
    """Reject a migration that manages transactions or the version stamp itself.

    The runner wraps every script in exactly one transaction and stamps
    ``user_version`` inside it. A stray ``COMMIT`` would end that transaction
    early, so the remainder of the script — and the version stamp — would land
    outside it: a half-applied migration recorded as complete. Cheaper to catch
    at load time than to debug afterwards.

    Detection is statement-level, not a bare keyword search, because ``BEGIN`` and
    ``END`` are also legal SQL *inside* trigger bodies and ``CASE`` expressions.
    """
    text = path.read_text(encoding="utf-8")
    stripped = _BLOCK_COMMENT_RE.sub(" ", text)
    stripped = _LINE_COMMENT_RE.sub(" ", stripped)
    stripped = _STRING_LITERAL_RE.sub("''", stripped)
    stripped = _TRIGGER_BODY_RE.sub(" ", stripped)

    for statement in stripped.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        if "user_version" in statement.lower():
            raise MigrationError(
                f"Migration {path.name} sets or reads user_version. The runner "
                "owns the version stamp; remove it from the script."
            )
        leading = _LEADING_WORD_RE.match(statement)
        if leading is None:
            continue
        keyword = leading.group(0).upper()
        if keyword in _FORBIDDEN_LEADING_KEYWORDS:
            raise MigrationError(
                f"Migration {path.name} contains a '{keyword}' statement. The "
                "runner wraps each migration in exactly one transaction; a "
                "script must not manage transactions, VACUUM, or attach "
                "databases itself."
            )


def latest_version(directory: Path | str | None = None) -> int:
    """Highest available migration version, or 0 if there are none."""
    available = discover_migrations(directory)
    return available[-1].version if available else 0


# ---------------------------------------------------------------------------
# Migration application
# ---------------------------------------------------------------------------


def user_version(conn: sqlite3.Connection) -> int:
    """Current schema version stamped in the database header."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(
    conn: sqlite3.Connection,
    *,
    directory: Path | str | None = None,
    backup_dir: Path | str | None = None,
) -> MigrationResult:
    """Apply every pending migration in order. A no-op when already current."""
    available = discover_migrations(directory)
    start = user_version(conn)
    newest = available[-1].version if available else 0

    if start > newest:
        # An older build must not touch a database a newer build has upgraded:
        # its DDL expectations are wrong and it would happily write to a schema
        # it does not understand.
        raise MigrationError(
            f"Database is at schema version {start} but this build only ships "
            f"migrations up to {newest}. Refusing to run: a newer Katagiri "
            "already upgraded this database. Update Katagiri, or restore an "
            "older snapshot from the backups directory."
        )

    pending = [mig for mig in available if mig.version > start]
    if not pending:
        return MigrationResult(start, start, (), None)

    # A fresh database has nothing worth snapshotting. Anything else does —
    # including a version-0 file that somehow already holds objects.
    if start > 0 or _has_user_objects(conn):
        backup = _backup(conn, start, backup_dir)
    else:
        backup = None

    for mig in pending:
        _apply(conn, mig, backup=backup)

    return MigrationResult(
        from_version=start,
        to_version=pending[-1].version,
        applied=tuple(mig.version for mig in pending),
        backup=backup,
    )


def _apply(
    conn: sqlite3.Connection, mig: Migration, *, backup: Path | None = None
) -> None:
    # Captured before the attempt: after a failure the rollback may itself have
    # failed, and reporting a version read from a broken connection would be a
    # guess dressed up as a fact.
    before = user_version(conn)
    sql = mig.path.read_text(encoding="utf-8")
    # BEGIN/COMMIT are added here rather than kept in the .sql files so that a
    # migration author cannot forget them, and so the version stamp is inside
    # the same transaction as the DDL. executescript() implicitly commits any
    # open transaction first, hence the BEGIN must live in the script text.
    script = (
        "BEGIN IMMEDIATE;\n"
        f"{sql}\n"
        f"PRAGMA user_version = {mig.version};\n"
        "COMMIT;\n"
    )
    try:
        conn.executescript(script)
    except sqlite3.Error as exc:
        rolled_back = True
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            rolled_back = False
        detail = (
            f"was rolled back; the database is still at version {before}"
            if rolled_back
            else f"failed AND the rollback failed; the database was at version "
            f"{before} before the attempt and may now be inconsistent"
        )
        snapshot = (
            f" Pre-migration snapshot: {backup}."
            if backup is not None
            else " No snapshot was taken (the database was empty)."
        )
        raise MigrationError(
            f"Migration {mig.path.name} {detail}: {exc}.{snapshot}",
            backup=backup,
        ) from exc


def _has_user_objects(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row[0]) > 0


def _main_database_file(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            filename = row[2]
            return Path(filename) if filename else None
    return None


def backup_dir_default() -> Path:
    """Where snapshots go: ``<config dir>/backups``."""
    return config_dir() / BACKUP_DIR_NAME


def _backup(
    conn: sqlite3.Connection,
    version: int,
    backup_dir: Path | str | None = None,
) -> Path:
    """Snapshot the database before migrating away from ``version``."""
    target_dir = Path(backup_dir) if backup_dir is not None else backup_dir_default()
    target_dir.mkdir(parents=True, exist_ok=True)

    db_file = _main_database_file(conn)
    stem = db_file.stem if db_file is not None else "katagiri"
    dest = target_dir / f"{stem}.pre-migrate-{version}.bak"
    if dest.exists():
        # VACUUM INTO refuses to overwrite, and an older snapshot is not ours
        # to destroy. Disambiguate instead of failing the migration.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        candidate = target_dir / f"{stem}.pre-migrate-{version}.{stamp}.bak"
        counter = 1
        while candidate.exists():
            candidate = (
                target_dir / f"{stem}.pre-migrate-{version}.{stamp}-{counter}.bak"
            )
            counter += 1
        dest = candidate

    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    except sqlite3.Error as exc:
        raise MigrationError(
            f"Could not write the pre-migration backup {dest}; refusing to "
            f"migrate without one: {exc}"
        ) from exc
    return dest


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


def resolve_alias(conn: sqlite3.Connection, item_id: str) -> dict[str, Any]:
    """Resolve ``item_id`` through the alias table.

    Returns ``{"id", "canonical_id", "redirected"}``. Callers apply this on
    every read *and* write so that a renamed item keeps working and the caller
    can tell the user a redirect happened. Unknown ids resolve to themselves.
    """
    seen = {item_id}
    current = item_id
    # One extra iteration beyond the hop limit: the last lookup is the one that
    # finds no further alias and breaks. Without it a chain of exactly
    # _MAX_ALIAS_HOPS links would be rejected as too long.
    for _ in range(_MAX_ALIAS_HOPS + 1):
        row = conn.execute(
            "SELECT canonical_id FROM alias WHERE alias_id = ?", (current,)
        ).fetchone()
        if row is None:
            break
        nxt = row[0]
        if nxt in seen:
            raise DatabaseError(
                f"Alias cycle detected while resolving {item_id!r} "
                f"(revisited {nxt!r}); fix the alias table."
            )
        seen.add(nxt)
        current = nxt
    else:
        raise DatabaseError(
            f"Alias chain for {item_id!r} is longer than {_MAX_ALIAS_HOPS} "
            "hops; fix the alias table."
        )

    return {
        "id": item_id,
        "canonical_id": current,
        "redirected": current != item_id,
    }


__all__ = [
    "BACKUP_DIR_NAME",
    "BUSY_TIMEOUT_MS",
    "DatabaseError",
    "Migration",
    "MigrationError",
    "MigrationResult",
    "backup_dir_default",
    "connect",
    "database_path",
    "discover_migrations",
    "latest_version",
    "migrate",
    "migrations_dir",
    "open_db",
    "resolve_alias",
    "user_version",
]
