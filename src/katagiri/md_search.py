"""Markdown note search: Katagiri's own index over the vault's prose.

Why this exists alongside ``search_db``
---------------------------------------
``search_db`` answers from *state* — items, aliases, sentence text the learner
studied. This module answers from *prose*: the markdown the learner wrote, plus
whatever ``.derived/`` generated for them. The two are complementary views of the
same question, and this one must keep working with Obsidian closed, so it reads
the vault directly from disk rather than through the ``:27123`` REST bridge.

Derived tier, and stamped
-------------------------
Everything here is derived (D-27): the source of truth is the vault's files. All
DDL lives in ``0001_init.sql`` — ``md_note``, ``md_frontmatter`` and the two FTS5
indexes — and this module only ever rebuilds *rows*, never schema. Every row
carries three stamps: :data:`MD_INDEX_VERSION` (this pipeline), and the
``dict_version``/``tokenizer_version`` that were current in ``metadata`` when the
row was built. A stamp that no longer matches is a re-index trigger even when the
file on disk did not change, which is what keeps a tokenizer upgrade from leaving
silently wrong ``shadow_text`` behind.

The version stamps come from :func:`katagiri.fts_index.current_versions`, which
*refuses* rather than stamping NULLs — see its docstring for why.

Two indexes, one routing rule
-----------------------------
Japanese has no spaces, so the same split the sentence index uses applies here:

* ``fts_md_words`` indexes the fugashi-segmented shadow text with ``unicode61``,
  and is the only index that can match a 1- or 2-character word.
* ``fts_md_tri`` indexes the raw text with ``trigram`` for substring search, and
  silently matches *nothing* below :data:`TRIGRAM_MIN_CHARS` characters.

:func:`search_notes` routes on query length exactly as ``search_db_query`` does,
so a learner asking the same question of both paths gets the same routing
decision.

Unlike the sentence indexes, these two are **self-contained** FTS5 tables rather
than external-content ones. External content is the right trade when the whole
index is rebuilt at once (``'delete-all'`` then repopulate); it has no correct
single-row delete without handing FTS5 the exact previously-indexed value back.
This index is updated one edited file at a time, so it deletes by rowid instead
and cannot drift out of agreement with ``md_note``.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from katagiri.fts_index import (
    DICT_VERSION_KEY,
    TOKENIZER_VERSION_KEY,
    TRIGRAM_MIN_CHARS,
    current_versions,
)
from katagiri.logging_setup import get_logger

#: Bumped whenever this module changes how a note is turned into rows (what gets
#: indexed, how frontmatter is parsed, how the body is tokenized). Rows stamped
#: with an older value are re-indexed on the next run even if the file is
#: untouched.
MD_INDEX_VERSION: Final = 1

NOTE_TABLE: Final = "md_note"
FRONTMATTER_TABLE: Final = "md_frontmatter"
WORD_INDEX: Final = "fts_md_words"
TRIGRAM_INDEX: Final = "fts_md_tri"

DEFAULT_LIMIT: Final = 20

_logger = get_logger("md_search")


class MdSearchError(RuntimeError):
    """Base class for every failure this module raises."""


class VaultNotFoundError(MdSearchError):
    """The configured vault path does not exist, or is not a directory."""


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MdIndexResult:
    """What one :func:`rebuild_md_index` call did.

    This object *is* the SC-003 evidence mechanism, together with the stderr line
    :func:`rebuild_md_index` logs from it: after editing a single note, an
    incremental run must report ``files_indexed == 1`` with every other file
    counted as unchanged. Full and incremental runs return the same shape on
    purpose — otherwise "only the edited file was re-indexed" would not be a
    comparable claim.

    ``files_scanned`` counts markdown files found on disk; ``files_indexed``
    those whose rows were (re)written; ``files_removed`` rows deleted because
    their file is gone; ``files_unchanged`` files skipped as already current;
    ``files_failed`` files that could not be read (their existing rows are kept,
    never silently dropped).
    """

    root: str
    full: bool
    files_scanned: int
    files_indexed: int
    files_removed: int
    files_unchanged: int
    files_failed: int
    frontmatter_errors: int
    generated_files: int
    index_version: int
    dict_version: str
    tokenizer_version: str
    duration_s: float

    def as_dict(self) -> dict[str, Any]:
        """The report as plain data, for a tool response or an assertion."""
        return {
            "root": self.root,
            "full": self.full,
            "files_scanned": self.files_scanned,
            "files_indexed": self.files_indexed,
            "files_removed": self.files_removed,
            "files_unchanged": self.files_unchanged,
            "files_failed": self.files_failed,
            "frontmatter_errors": self.frontmatter_errors,
            "generated_files": self.generated_files,
            "index_version": self.index_version,
            "dict_version": self.dict_version,
            "tokenizer_version": self.tokenizer_version,
            "duration_s": self.duration_s,
        }

    def render(self) -> str:
        return "\n".join(
            [
                f"root      : {self.root}",
                f"mode      : {'full rebuild' if self.full else 'incremental'}",
                f"scanned   : {self.files_scanned}",
                f"indexed   : {self.files_indexed}",
                f"removed   : {self.files_removed}",
                f"unchanged : {self.files_unchanged}",
                f"failed    : {self.files_failed} (unreadable; existing rows kept)",
                f"fm errors : {self.frontmatter_errors} (indexed by body anyway)",
                f"generated : {self.generated_files} (.derived/ output)",
                f"index ver : {self.index_version}",
                f"dict      : {self.dict_version}",
                f"tokenizer : {self.tokenizer_version}",
                f"duration  : {self.duration_s:.3f}s",
            ]
        )


# ---------------------------------------------------------------------------
# Transactions and clearing
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _atomic(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the block as one all-or-nothing unit, owned or nested.

    Same shape (and same reasoning) as ``fts_index._atomic``: a full rebuild
    *destroys* rows before it writes new ones, so a failure halfway must not be
    able to leave the database with a half-index. When the caller already holds a
    transaction the work becomes a SAVEPOINT, so a failure rolls back this
    rebuild without committing — or discarding — whatever else it was doing.
    """
    if conn.in_transaction:
        conn.execute("SAVEPOINT katagiri_md_rebuild")
        try:
            yield
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK TO katagiri_md_rebuild")
                conn.execute("RELEASE katagiri_md_rebuild")
            raise
        conn.execute("RELEASE katagiri_md_rebuild")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _clear(conn: sqlite3.Connection) -> None:
    """Empty every markdown-index table. Rows only — the schema is the A1 migration's.

    The FTS tables are self-contained, so a plain ``DELETE`` unindexes correctly
    (there is no content table whose agreement could be assumed).
    """
    for table in (WORD_INDEX, TRIGRAM_INDEX, FRONTMATTER_TABLE, NOTE_TABLE):
        conn.execute(f"DELETE FROM {table}")


def _indexed_paths(conn: sqlite3.Connection) -> set[str]:
    """Every note path currently in the index."""
    return {
        str(row[0])
        for row in conn.execute(f"SELECT path FROM {NOTE_TABLE}")
    }


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def _iter_notes(root: Any) -> Iterator[Any]:
    """Walk ``root`` and yield one parsed note per markdown file.

    Not implemented yet: the vault walk, frontmatter parsing and body
    tokenization are T004. It raises rather than returning an empty iterator on
    purpose — an empty walk would make :func:`rebuild_md_index` report a
    plausible-looking "0 files scanned" for a vault full of notes (D-24: stubs
    raise instead of returning plausible values).
    """
    raise NotImplementedError(
        "The vault walk lands in T004 (specs/002-phase-c-prose-search/tasks.md). "
        "T003 ships the derived-index schema, the version stamping and the "
        "structured report only."
    )


def rebuild_md_index(
    conn: sqlite3.Connection,
    *,
    root: Any | None = None,
    full: bool = False,
) -> MdIndexResult:
    """Bring the markdown index in line with the vault, and report what changed.

    ``full=True`` is the derived-tier drop-and-rebuild: every row is deleted and
    every file re-read and re-tokenized. The default is the incremental path —
    files whose size, mtime and stamps all still match are skipped without being
    read, so the cost is proportional to what changed rather than to vault size.

    Both modes return the same :class:`MdIndexResult`, and both log one stderr
    line summarising it; that pair is what makes "editing one note re-indexed one
    file" checkable (SC-003).
    """
    started = time.perf_counter()
    versions = current_versions(conn)
    dict_version = versions[DICT_VERSION_KEY]
    tokenizer_version = versions[TOKENIZER_VERSION_KEY]

    scanned = indexed = removed = unchanged = failed = 0
    frontmatter_errors = generated_files = 0

    with _atomic(conn):
        known = _indexed_paths(conn)
        if full:
            _clear(conn)
        for _note in _iter_notes(root):  # pragma: no cover - T004
            scanned += 1

    duration = time.perf_counter() - started
    result = MdIndexResult(
        root=str(root),
        full=full,
        files_scanned=scanned,
        files_indexed=indexed,
        files_removed=removed,
        files_unchanged=unchanged,
        files_failed=failed,
        frontmatter_errors=frontmatter_errors,
        generated_files=generated_files,
        index_version=MD_INDEX_VERSION,
        dict_version=dict_version,
        tokenizer_version=tokenizer_version,
        duration_s=duration,
    )
    _logger.info(
        "md index %s: scanned=%d indexed=%d removed=%d unchanged=%d failed=%d "
        "in %.3fs (was %d rows)",
        "full rebuild" if full else "incremental",
        scanned,
        indexed,
        removed,
        unchanged,
        failed,
        duration,
        len(known),
    )
    return result


__all__ = [
    "DEFAULT_LIMIT",
    "FRONTMATTER_TABLE",
    "MD_INDEX_VERSION",
    "NOTE_TABLE",
    "TRIGRAM_INDEX",
    "TRIGRAM_MIN_CHARS",
    "WORD_INDEX",
    "MdIndexResult",
    "MdSearchError",
    "VaultNotFoundError",
    "rebuild_md_index",
]
