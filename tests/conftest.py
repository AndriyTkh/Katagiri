"""Shared suite wiring: test groups, run order, and the JMdict template cache.

Groups
------
``compile``
    Ground-zero rebuilds: real JMdict reimports, corruption/restore drills.
    These only run when ``--public-build`` is given, and they run FIRST.
``mcp``
    Tests that spawn the real MCP server over stdio (marked per module with
    ``pytestmark``). They run LAST.
Everything else is the general group, in the middle.

Runs
----
Normal (``uv run pytest``)
    Skips ``compile``. The real dictionary is not reimported: the template
    fixture below hands out copies of a cached import and merely verifies the
    vendored zip is present and matches its checksum manifest.
Public build (``uv run pytest --public-build``)
    Runs everything. The template cache is ignored: the dictionary is imported
    from ground zero, and every ``compile`` drill runs.

JMdict template
---------------
``real_jmdict_template`` imports the vendored JMdict once into a SQLite file
cached under ``tests/.cache`` keyed by the zip's sha256. Modules that need a
populated dictionary copy that file (~1s) instead of paying the ~21s import
each. ``import_jmdict`` writes only into the database (the manifest is read,
never written), so a file copy carries the import completely.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from katagiri import db as db_mod
from katagiri import jmdict_import as jm

_CACHE_DIR = Path(__file__).resolve().parent / ".cache"

# Modules that spawn the real MCP server; ordered last so the cheap unit
# groups fail fast before any subprocess cold-start is paid.
_MCP_ORDER_BAND = {"mcp": 2, "compile": 0}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--public-build",
        action="store_true",
        default=False,
        help=(
            "run the 'compile' group: ground-zero reimports and corruption "
            "drills, with the JMdict template cache ignored"
        ),
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not config.getoption("--public-build"):
        skip_compile = pytest.mark.skip(
            reason="ground-zero 'compile' test; run with --public-build"
        )
        for item in items:
            if "compile" in item.keywords:
                item.add_marker(skip_compile)

    def band(item: pytest.Item) -> int:
        if "compile" in item.keywords:
            return _MCP_ORDER_BAND["compile"]
        if "mcp" in item.keywords:
            return _MCP_ORDER_BAND["mcp"]
        return 1

    # Stable sort keeps file and definition order within each band.
    items.sort(key=band)


@dataclass(frozen=True)
class JmdictTemplate:
    """A populated dictionary database, ready to be copied into module scratch."""

    path: Path
    ground_zero: bool  # True when --public-build imported it fresh this run

    def materialize(self, dest: Path) -> Path:
        """Copy the template file to ``dest`` and hand the path back."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, dest)
        return dest

    def copy_into(self, conn: sqlite3.Connection) -> None:
        """Clone the template's contents into an already-open database."""
        src = sqlite3.connect(str(self.path))
        try:
            src.backup(conn)
        finally:
            src.close()


@pytest.fixture(scope="session")
def real_jmdict_template(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> JmdictTemplate:
    try:
        zip_path = jm.default_jmdict_zip()
    except jm.JmdictImportError as exc:  # pragma: no cover - unvendored checkout
        pytest.skip(f"The vendored JMdict is not available: {exc}")

    public_build = request.config.getoption("--public-build")

    if public_build:
        # Ground zero: fresh import this run, no cache read or write.
        path = tmp_path_factory.mktemp("jmdict_template") / "jmdict-template.db"
        _import_into(path, zip_path)
        return JmdictTemplate(path=path, ground_zero=True)

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    cached = _CACHE_DIR / f"jmdict-{digest[:16]}.db"
    if not cached.exists():
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Build under a per-process name, then rename: a crashed import must not
        # leave a half-written file that later runs mistake for the template, and
        # two pytest processes racing to build must not write into the same file.
        building = cached.with_suffix(f".building-{os.getpid()}")
        _import_into(building, zip_path)
        try:
            building.replace(cached)
        except OSError:
            # A concurrent run won the rename while ours was open/locked; if the
            # final file is there, its import is as good as ours.
            if not cached.exists():
                raise
            building.unlink(missing_ok=True)
    return JmdictTemplate(path=cached, ground_zero=False)


def _import_into(path: Path, zip_path: Path) -> None:
    connection = db_mod.open_db(path)
    try:
        jm.import_jmdict(connection, zip_path)
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
