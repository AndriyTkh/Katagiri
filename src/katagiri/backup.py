r"""Backup and restore for the Katagiri database and vault.

Database snapshots are taken with ``VACUUM INTO``, which asks SQLite itself to
write a consistent, defragmented copy while the database stays open. That is the
only safe way to copy a live WAL database: a file-level copy of ``katagiri.db``
without its ``-wal`` sidecar is a torn snapshot that may be missing committed
transactions. Snapshots land under ``<config dir>/backups`` — beside the
configuration rather than beside the database, so a database on a synced or
scratch volume cannot take its own safety net down with it. That is the same
directory :mod:`katagiri.db` writes pre-migration snapshots into; the two naming
schemes are disjoint (``.pre-migrate-N.bak`` versus ``.<stamp>.db``) so pruning
routine snapshots never deletes a pre-migration one.

Vault snapshots are a plain zip of the markdown and JSONL under the vault,
excluding ``local/`` (machine-specific, not portable) and ``.derived/``
(rebuildable by definition — backing it up would preserve staleness).

RESTORE IS OFFLINE ONLY. :func:`restore_backup` copies a file over the database
path; doing that while the MCP server holds the database open corrupts both the
running connection's view and, via a stale ``-wal``, the file that lands. Stop
the Katagiri MCP server first, restore, then start it again. The function refuses
an existing target unless ``force=True``, and when forcing it removes the
target's ``-wal``/``-shm`` sidecars, because a leftover WAL from the old database
replayed against a restored one is silent corruption.

Scheduling: Katagiri does not create scheduled tasks — a personal tool should not
install background jobs behind its owner's back. To run a daily snapshot at
21:00, run this yourself (one line, from the repository root):

    schtasks /Create /TN "Katagiri Daily Backup" /SC DAILY /ST 21:00 /F /TR "cmd /c cd /d C:\ProjectsC\RandomPr\Katagiri && uv run python -m katagiri.backup create"

Check it with ``schtasks /Query /TN "Katagiri Daily Backup"`` and remove it with
``schtasks /Delete /TN "Katagiri Daily Backup" /F``.

CLI::

    python -m katagiri.backup create  [--dest DIR] [--keep N] [--vault]
    python -m katagiri.backup verify  PATH
    python -m katagiri.backup restore PATH [--target DB] [--force]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from katagiri import db
from katagiri.config import get_config

_log = logging.getLogger("katagiri.backup")

DEFAULT_KEEP: Final = 14
BACKUP_SUFFIX: Final = ".db"
VAULT_SNAPSHOT_EXTENSIONS: Final = frozenset({".md", ".jsonl"})
# Excluded from vault snapshots: machine-local scratch, and anything derived is
# rebuildable from sources that *are* backed up.
VAULT_EXCLUDED_DIRS: Final = frozenset({"local", ".derived"})
STAMP_FORMAT: Final = "%Y%m%dT%H%M%S"


class BackupError(RuntimeError):
    """Raised when a backup or restore cannot be completed."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime(STAMP_FORMAT)


def default_backup_dir() -> Path:
    """``<config dir>/backups`` — shared with the migration runner."""
    return db.backup_dir_default()


def _snapshot_pattern(stem: str) -> str:
    """Glob matching only routine snapshots of ``stem``, not pre-migrate ones."""
    return f"{stem}.[0-9]" + "[0-9]" * 7 + f"T[0-9][0-9][0-9][0-9][0-9][0-9]*{BACKUP_SUFFIX}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_backup(
    conn: sqlite3.Connection,
    dest_dir: Path | str | None = None,
    *,
    keep: int = DEFAULT_KEEP,
) -> Path:
    """Write a ``VACUUM INTO`` snapshot and prune to the newest ``keep``.

    ``VACUUM INTO`` cannot run inside a transaction; :func:`katagiri.db.connect`
    opens connections in autocommit mode, so this works on a connection that is
    not mid-transaction. It also refuses to overwrite, so a second snapshot in
    the same second gets a counter suffix rather than clobbering the first.
    """
    if keep < 1:
        raise ValueError(f"keep must be at least 1; got {keep}.")

    target_dir = Path(dest_dir) if dest_dir is not None else default_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    source = _main_database_file(conn)
    stem = source.stem if source is not None else "katagiri"
    stamp = _stamp()

    # A second snapshot inside the same second is disambiguated with '~NN'.
    # The separator matters: '~' sorts *after* '.', so "…T101112.db" <
    # "…T101112~01.db" and the filename order stays chronological — which is what
    # prune_backups relies on to decide what is oldest. A '-' would invert it.
    dest = target_dir / f"{stem}.{stamp}{BACKUP_SUFFIX}"
    counter = 1
    while dest.exists():
        dest = target_dir / f"{stem}.{stamp}~{counter:02d}{BACKUP_SUFFIX}"
        counter += 1

    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    except sqlite3.Error as exc:
        raise BackupError(f"Could not write the backup {dest}: {exc}") from exc

    prune_backups(target_dir, keep=keep, stem=stem)
    return dest


def _main_database_file(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return Path(row[2]) if row[2] else None
    return None


def list_backups(
    dest_dir: Path | str | None = None, *, stem: str = "katagiri"
) -> list[Path]:
    """Routine snapshots of ``stem``, oldest first.

    Sorted by filename, which is a UTC timestamp in a fixed-width format, so
    lexicographic order is chronological order (see the '~NN' note in
    :func:`create_backup`). File mtimes are not consulted: copying a backups
    directory rewrites them.
    """
    target_dir = Path(dest_dir) if dest_dir is not None else default_backup_dir()
    if not target_dir.is_dir():
        return []
    return sorted(target_dir.glob(_snapshot_pattern(stem)))


def prune_backups(
    dest_dir: Path | str | None = None,
    *,
    keep: int = DEFAULT_KEEP,
    stem: str = "katagiri",
) -> list[Path]:
    """Delete all but the newest ``keep`` routine snapshots. Returns what went."""
    if keep < 1:
        raise ValueError(f"keep must be at least 1; got {keep}.")
    existing = list_backups(dest_dir, stem=stem)
    doomed = existing[: max(0, len(existing) - keep)]
    removed: list[Path] = []
    for path in doomed:
        try:
            path.unlink()
        except OSError as exc:
            # A snapshot we cannot delete is a tidiness problem, not a reason to
            # fail the backup that just succeeded.
            _log.warning("Could not prune old backup %s: %s", path, exc)
            continue
        removed.append(path)
    return removed


def copy_vault_snapshot(
    vault_path: Path | str, dest_dir: Path | str | None = None
) -> Path:
    """Zip the vault's markdown and JSONL into a timestamped archive.

    Only ``.md`` and ``.jsonl`` are captured: the vault's value is its text, and
    pulling in media would turn a fast daily snapshot into a slow one nobody
    runs. ``local/`` and ``.derived/`` are skipped at any depth.
    """
    source = Path(vault_path)
    if not source.is_dir():
        raise BackupError(f"Vault path {source} is not a directory.")

    target_dir = Path(dest_dir) if dest_dir is not None else default_backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = _stamp()
    dest = target_dir / f"vault.{stamp}.zip"
    counter = 1
    while dest.exists():
        dest = target_dir / f"vault.{stamp}-{counter}.zip"
        counter += 1

    written = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in VAULT_SNAPSHOT_EXTENSIONS:
                continue
            relative = path.relative_to(source)
            if any(part.lower() in VAULT_EXCLUDED_DIRS for part in relative.parts[:-1]):
                continue
            archive.write(path, arcname=relative.as_posix())
            written += 1

    _log.info("Vault snapshot %s holds %d file(s).", dest.name, written)
    return dest


# ---------------------------------------------------------------------------
# Verify and restore
# ---------------------------------------------------------------------------


def verify_backup(backup_path: Path | str) -> dict[str, Any]:
    """Integrity-check a snapshot without modifying it.

    Opened read-only through a URI so that checking a backup cannot create,
    upgrade, or WAL-ify it. Returns ``{"path", "integrity", "user_version",
    "size_bytes", "event_count"}``; ``event_count`` is ``None`` when the file has
    no ``event`` table.
    """
    path = Path(backup_path)
    if not path.is_file():
        raise BackupError(f"Backup {path} does not exist.")

    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"Could not open backup {path}: {exc}") from exc
    try:
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise BackupError(
                f"Backup {path} is not a readable SQLite database: {exc}"
            ) from exc
        try:
            events = int(conn.execute("SELECT COUNT(*) FROM event").fetchone()[0])
        except sqlite3.Error:
            events = None
    finally:
        conn.close()

    return {
        "path": str(path),
        "integrity": integrity,
        "ok": integrity == "ok",
        "user_version": version,
        "size_bytes": path.stat().st_size,
        "event_count": events,
    }


def restore_backup(
    backup_path: Path | str,
    target_db_path: Path | str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Restore a snapshot over ``target_db_path``. **Offline only.**

    Stop the Katagiri MCP server before calling this: overwriting the file a live
    connection has open corrupts it. Nothing here can detect a running server
    reliably on Windows, so this is a rule, not a guard.

    The snapshot is integrity-checked *before* anything is written — restoring a
    corrupt backup over a working database would turn a recoverable situation
    into an unrecoverable one. An existing target is refused unless ``force``;
    when forcing, the target's ``-wal``/``-shm`` sidecars are removed, since a
    WAL belonging to the old database replayed against the restored one is silent
    corruption.
    """
    source = Path(backup_path)
    report = verify_backup(source)
    if not report["ok"]:
        raise BackupError(
            f"Backup {source} fails integrity_check ({report['integrity']}); "
            "refusing to restore it over anything."
        )

    target = (
        Path(target_db_path) if target_db_path is not None else get_config().db_path
    )
    overwrote = target.exists()
    if overwrote and source.resolve() == target.resolve():
        raise BackupError(
            f"The backup and the restore target are the same file ({target})."
        )

    if overwrote and not force:
        raise BackupError(
            f"{target} already exists. Restoring would destroy it. Move it aside, "
            "or pass force=True (CLI: --force) if that is what you want."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise BackupError(f"Could not restore {source} to {target}: {exc}") from exc

    for sidecar in (
        target.with_name(target.name + "-wal"),
        target.with_name(target.name + "-shm"),
    ):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - locked sidecar means a live server
            raise BackupError(
                f"Restored {target} but could not remove the stale sidecar "
                f"{sidecar}: {exc}. A Katagiri process is probably still "
                "running; stop it and remove the file before opening the "
                "database."
            ) from exc

    after = verify_backup(target)
    return {
        "restored_from": str(source),
        "restored_to": str(target),
        "overwrote": overwrote,
        "integrity": after["integrity"],
        "user_version": after["user_version"],
        "event_count": after["event_count"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.backup",
        description="Snapshot, verify, and restore the Katagiri database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="write a snapshot and prune old ones")
    create.add_argument("--db", type=Path, default=None, help="database to snapshot")
    create.add_argument("--dest", type=Path, default=None, help="backups directory")
    create.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP, help=f"keep newest N (default {DEFAULT_KEEP})"
    )
    create.add_argument(
        "--vault", action="store_true", help="also zip the vault's .md/.jsonl"
    )

    verify = sub.add_parser("verify", help="integrity-check a snapshot")
    verify.add_argument("path", type=Path)

    restore = sub.add_parser(
        "restore", help="restore a snapshot (stop the MCP server first)"
    )
    restore.add_argument("path", type=Path)
    restore.add_argument("--target", type=Path, default=None, help="database to write")
    restore.add_argument(
        "--force", action="store_true", help="overwrite an existing target"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.backup``."""
    args = _build_parser().parse_args(argv)

    try:
        if args.command == "create":
            # connect(), not open_db(): a backup must never be the thing that
            # triggers a schema migration.
            conn = db.connect(args.db)
            try:
                dest = create_backup(conn, args.dest, keep=args.keep)
            finally:
                conn.close()
            print(f"database snapshot: {dest}")
            if args.vault:
                vault = get_config().require_vault_path()
                print(f"vault snapshot:    {copy_vault_snapshot(vault, args.dest)}")
            return 0

        if args.command == "verify":
            report = verify_backup(args.path)
            print(
                f"{report['path']}\n"
                f"  integrity    : {report['integrity']}\n"
                f"  user_version : {report['user_version']}\n"
                f"  events       : {report['event_count']}\n"
                f"  size         : {report['size_bytes']} bytes"
            )
            return 0 if report["ok"] else 1

        if args.command == "restore":
            print(
                "Restore is offline only. Stop the Katagiri MCP server before "
                "continuing."
            )
            result = restore_backup(args.path, args.target, force=args.force)
            print(
                f"restored {result['restored_from']} -> {result['restored_to']}\n"
                f"  overwrote    : {result['overwrote']}\n"
                f"  integrity    : {result['integrity']}\n"
                f"  user_version : {result['user_version']}"
            )
            return 0
    except (BackupError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 2

    return 2  # pragma: no cover - argparse rejects unknown commands first


__all__ = [
    "BackupError",
    "DEFAULT_KEEP",
    "VAULT_EXCLUDED_DIRS",
    "VAULT_SNAPSHOT_EXTENSIONS",
    "copy_vault_snapshot",
    "create_backup",
    "default_backup_dir",
    "list_backups",
    "main",
    "prune_backups",
    "restore_backup",
    "verify_backup",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
