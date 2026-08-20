"""Build (or rebuild) the demo fixture SQLite database from scratch.

This is the grader-facing fixture-DB builder for the 005 assignment's demo
profile (US3, FR-009). It runs three steps, always in this order, against a
single SQLite file:

1. **Migrate** — bring the schema up to date via :func:`katagiri.db.migrate`
   (the same migration runner the real app uses).
2. **Import** — a scripted, vendored, checksummed JMdict (and kanjium
   pitch-accent) import (D-10: zero runtime downloads). The JMdict step's
   wall-clock time is printed and written into a JSON receipt next to the
   database, because the grader needs to know up front how long a
   from-scratch rebuild takes.
3. **Seed** — write the minimal study state the demo needs. Seed *content* is
   finalized in task T016; the section below is a deliberately thin,
   idempotent placeholder marked for that extension.

Safety
------
This script refuses to write anywhere outside the demo profile directory. It
never touches the real ``%LOCALAPPDATA%\\Katagiri`` profile: the resolved
profile directory is checked against the real one (via
``katagiri.config.config_dir()``) before anything is created, and every path
this script writes (database, WAL/SHM siblings, backups, the timing receipt)
is derived from that one directory rather than from the ambient config.

Idempotence
-----------
Re-running this script is safe and cheap to reason about:

* the migration runner is already a no-op once the schema is current;
* ``import_jmdict``/``import_kanjium`` are DELETE + INSERT in one transaction
  (importing again just reimports the same vendored bytes); and
* the seed step stamps a ``metadata`` marker and only (re)inserts rows that
  do not already exist, so running it twice does not duplicate seed data.

Usage
-----
    python scripts/build_demo_db.py
    python scripts/build_demo_db.py --profile-dir D:\\demo\\Katagiri-demo
    python scripts/build_demo_db.py --skip-kanjium --no-seed
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# Allow running this script directly (``python scripts/build_demo_db.py``)
# without the package having been installed: add the lane's ``src`` to
# sys.path when it is not already importable.
try:
    from katagiri import db, jmdict_import as jm
    from katagiri.config import config_dir as real_config_dir
except ImportError:  # pragma: no cover - convenience for direct invocation
    _SRC = Path(__file__).resolve().parent.parent / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from katagiri import db, jmdict_import as jm
    from katagiri.config import config_dir as real_config_dir

DEFAULT_PROFILE_DIR_NAME = "Katagiri-demo"
DB_FILE_NAME = "katagiri.db"
BACKUP_DIR_NAME = "backups"
TIMING_FILE_NAME = "build_demo_db.timing.json"

# Bumped whenever the placeholder seed section below changes shape. T016 will
# take this over and grow it into the real seed content (>=2 prescribe()
# rungs, >=2 coverage outcomes per spec.md). Until then this stays a no-op on
# rerun once stamped.
SEED_PLACEHOLDER_VERSION = "0-placeholder"


class DemoProfileError(RuntimeError):
    """Raised when the resolved target would write outside the demo profile."""


@dataclass(frozen=True, slots=True)
class StepTiming:
    """Wall-clock time of one build step, in seconds."""

    name: str
    seconds: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_profile_dir() -> Path:
    """``%LOCALAPPDATA%\\Katagiri-demo`` — never the real ``Katagiri`` dir."""
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        raise DemoProfileError(
            "LOCALAPPDATA is not set, so the default demo profile directory "
            "cannot be computed. Pass --profile-dir explicitly."
        )
    return Path(raw) / DEFAULT_PROFILE_DIR_NAME


def _ensure_demo_profile_dir(candidate: Path) -> Path:
    """Resolve ``candidate`` and refuse it if it is (or contains) the real profile.

    Two checks, both fatal:

    1. The resolved directory must not *be* the real personal profile
       directory (``katagiri.config.config_dir()``, normally
       ``%LOCALAPPDATA%\\Katagiri``).
    2. The resolved directory must not be an *ancestor* of the real profile
       directory (which would make the real profile a subdirectory of "the
       demo profile" and defeat the point of the check).

    Everything this script subsequently writes is derived from the returned
    path, so passing this check is what "refuses to write anywhere outside
    the demo profile directory" means in practice.
    """
    resolved = candidate.expanduser().resolve()

    try:
        real_dir = real_config_dir().resolve()
    except Exception:
        # LOCALAPPDATA unset or similar: nothing to compare against, so there
        # is no personal profile this run could collide with.
        real_dir = None

    if real_dir is not None:
        if resolved == real_dir:
            raise DemoProfileError(
                f"Refusing to build into {resolved}: it is the real personal "
                f"profile directory ({real_dir}). Pass --profile-dir with a "
                "path outside it (default: %LOCALAPPDATA%\\Katagiri-demo)."
            )
        try:
            real_dir.relative_to(resolved)
        except ValueError:
            pass
        else:
            raise DemoProfileError(
                f"Refusing to build into {resolved}: the real personal profile "
                f"directory ({real_dir}) sits inside it. Pass --profile-dir "
                "with a path that does not contain the real profile."
            )

    return resolved


def _run_migration(conn: sqlite3.Connection, profile_dir: Path) -> None:
    """Run pending migrations, with backups pinned under the demo profile.

    ``db.migrate``'s default backup location comes from the *ambient* config
    (``katagiri.config.config_dir()``), which would silently point at the
    real personal profile. Passing ``backup_dir`` explicitly keeps every
    written byte under ``profile_dir``.
    """
    result = db.migrate(conn, backup_dir=profile_dir / BACKUP_DIR_NAME)
    if result.changed:
        print(
            f"migrated schema {result.from_version} -> {result.to_version} "
            f"(applied {list(result.applied)})"
        )
    else:
        print(f"schema already current (version {result.from_version})")


def _run_jmdict_import(
    conn: sqlite3.Connection, jmdict_zip: Path | None
) -> tuple[jm.ImportResult, StepTiming]:
    start = time.perf_counter()
    result = jm.import_jmdict(conn, jmdict_zip)
    elapsed = time.perf_counter() - start
    print(
        f"jmdict {result.version} ({result.dict_date}): {result.entries} entries, "
        f"{result.kanji_rows} kanji, {result.reading_rows} readings, "
        f"{result.sense_rows} senses -- {elapsed:.2f}s"
    )
    return result, StepTiming(name="jmdict_import", seconds=elapsed)


def _run_kanjium_import(
    conn: sqlite3.Connection, kanjium_path: Path | None
) -> tuple[jm.PitchResult, StepTiming]:
    start = time.perf_counter()
    result = jm.import_kanjium(conn, kanjium_path)
    elapsed = time.perf_counter() - start
    print(
        f"kanjium {result.source_version}: {result.rows} accent rows from "
        f"{result.lines} lines -- {elapsed:.2f}s"
    )
    return result, StepTiming(name="kanjium_import", seconds=elapsed)


# ---------------------------------------------------------------------------
# Seed step -- MINIMAL PLACEHOLDER, see T016
# ---------------------------------------------------------------------------
#
# T016 (tasks.md) finalizes the actual seed content: enough `item` rows,
# `event`/`observation` history, and coverage state that the demo reaches
# >=2 distinct prescribe() rungs and >=2 distinct coverage outcomes
# (spec.md "Fresh/empty demo DB" pitfall). Until that lands, this function is
# intentionally thin: it only stamps a metadata marker so the step is
# idempotent, and does not touch `item`/`event`/`coverage_cache` at all.
#
# T016: replace the body of this function (keep the signature and the
# idempotence contract -- safe to call on every run, no duplicate rows).


def _seed_demo_state(conn: sqlite3.Connection) -> StepTiming:
    """Placeholder for the demo study-state seed. Extend in T016."""
    start = time.perf_counter()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value, updated_ts) "
            "VALUES ('demo_seed_version', ?, ?)",
            (SEED_PLACEHOLDER_VERSION, _utc_now()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    elapsed = time.perf_counter() - start
    print(
        f"seed: placeholder marker only (version {SEED_PLACEHOLDER_VERSION!r}); "
        "real seed content lands in T016 -- {:.3f}s".format(elapsed)
    )
    return StepTiming(name="seed_demo_state", seconds=elapsed)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_demo_db.py",
        description=(
            "Build (or rebuild) the demo fixture SQLite database from scratch: "
            "migrate, import the vendored/checksummed JMdict + kanjium data, "
            "then seed minimal study state. Idempotent and re-runnable; "
            "refuses to write outside the demo profile directory."
        ),
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help=(
            "demo profile directory to build into (default: "
            "%%LOCALAPPDATA%%\\Katagiri-demo). Must not be the real personal "
            "profile directory."
        ),
    )
    parser.add_argument(
        "--jmdict-zip",
        type=Path,
        default=None,
        help="override the vendored JMdict archive path (default: auto-detect)",
    )
    parser.add_argument(
        "--kanjium",
        type=Path,
        default=None,
        help="override the vendored kanjium accents.txt path (default: auto-detect)",
    )
    parser.add_argument(
        "--skip-kanjium",
        action="store_true",
        help="skip the kanjium pitch-accent import",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="skip the study-state seed step",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    candidate = args.profile_dir if args.profile_dir is not None else default_profile_dir()
    try:
        profile_dir = _ensure_demo_profile_dir(candidate)
    except DemoProfileError as exc:
        print(f"error: {exc}")
        return 2

    profile_dir.mkdir(parents=True, exist_ok=True)
    db_path = profile_dir / DB_FILE_NAME
    print(f"demo profile directory: {profile_dir}")
    print(f"demo database: {db_path}")

    timings: list[StepTiming] = []
    conn = db.connect(db_path)
    try:
        _run_migration(conn, profile_dir)

        try:
            _, jmdict_timing = _run_jmdict_import(conn, args.jmdict_zip)
        except jm.JmdictImportError as exc:
            print(f"error: JMdict import failed: {exc}")
            return 2
        timings.append(jmdict_timing)

        if not args.skip_kanjium:
            try:
                _, kanjium_timing = _run_kanjium_import(conn, args.kanjium)
            except jm.JmdictImportError as exc:
                print(f"error: kanjium import failed: {exc}")
                return 2
            timings.append(kanjium_timing)

        if not args.no_seed:
            timings.append(_seed_demo_state(conn))
    finally:
        conn.close()

    receipt = {
        "built_ts": _utc_now(),
        "profile_dir": str(profile_dir),
        "db_path": str(db_path),
        "steps": [asdict(t) for t in timings],
    }
    receipt_path = profile_dir / TIMING_FILE_NAME
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"timing receipt: {receipt_path}")

    jmdict_seconds = next(
        (t.seconds for t in timings if t.name == "jmdict_import"), None
    )
    if jmdict_seconds is not None:
        print(f"JMdict step wall-clock: {jmdict_seconds:.2f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
