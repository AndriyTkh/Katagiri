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

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from katagiri.fts_index import (
    DICT_VERSION_KEY,
    TOKENIZER_VERSION_KEY,
    TRIGRAM_MIN_CHARS,
    current_versions,
    sanitize_query,
    shadow_text,
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

#: What counts as a note. Obsidian writes ``.md`` and nothing else; a second
#: suffix here would also have to be a suffix the frontmatter parser understands.
MARKDOWN_SUFFIXES: Final = (".md",)

#: B1's export directory. Its files *are* indexed (they are part of the searched
#: corpus) but every row from it is flagged ``generated = 1`` so dashboard output
#: can be filtered out of a prose search.
GENERATED_DIR: Final = ".derived"

#: Directories never walked into. Everything else beginning with a dot is skipped
#: as well — except :data:`GENERATED_DIR`, which is exactly why that exception is
#: written out rather than left to a dot rule.
SKIP_DIRS: Final = frozenset(
    {".git", ".obsidian", ".trash", ".stfolder", ".stversions", "node_modules",
     "__pycache__"}
)

#: A frontmatter key: no colon, no leading dash, and short enough that a prose
#: line containing a colon ("Note: this is not frontmatter") does not become one.
_FM_KEY_RE: Final = re.compile(r"^(?P<key>[A-Za-z0-9_][A-Za-z0-9_. -]{0,63})\s*:(?P<rest>.*)$")

#: Frontmatter delimiters: ``---`` opens, ``---`` or ``...`` closes (both are
#: YAML document-end markers and Obsidian tolerates either).
_FM_OPEN: Final = "---"
_FM_CLOSE: Final = ("---", "...")

#: How many parse complaints one note's ``frontmatter_error`` records before it
#: says "and N more". The column is a diagnostic, not a log.
_MAX_FM_PROBLEMS: Final = 5

_logger = get_logger("md_search")


class MdSearchError(RuntimeError):
    """Base class for every failure this module raises."""


class VaultNotFoundError(MdSearchError):
    """The configured vault path does not exist, or is not a directory."""


class NoteReadError(MdSearchError):
    """One note could not be read or decoded. Never fatal to a whole run."""


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Vault walk
# ---------------------------------------------------------------------------


def vault_root(root: Path | str | None = None) -> Path:
    """The directory to index: ``root`` if given, else the configured vault.

    Reading the configuration is deferred to call time (and imported here) so
    that importing this module never touches the filesystem — the same rule
    ``fts_index`` follows for ``open_db``.
    """
    if root is None:
        from katagiri.config import get_config

        resolved = get_config().require_vault_path()
    else:
        resolved = Path(root)
    if not resolved.is_dir():
        raise VaultNotFoundError(
            f"The vault path {resolved} does not exist or is not a directory, so "
            "there is nothing to index. Set 'vault_path' in config.toml to the "
            "folder holding your markdown notes."
        )
    return resolved


def is_generated(rel_path: str) -> bool:
    """True for a vault-relative path under :data:`GENERATED_DIR`."""
    return rel_path == GENERATED_DIR or rel_path.startswith(f"{GENERATED_DIR}/")


def _skip_dir(name: str) -> bool:
    if name == GENERATED_DIR:
        return False
    return name in SKIP_DIRS or name.startswith(".")


def iter_markdown_files(root: Path) -> Iterator[Path]:
    """Every markdown file under ``root``, deepest-stable and deterministic order.

    ``os.walk`` rather than ``Path.rglob`` for one reason: pruning. Editing
    ``dirnames`` in place stops the walk from descending into ``.obsidian`` or
    ``.git`` at all, which on a real vault is most of the file count. Names are
    sorted in place so two runs over unchanged files visit them in the same
    order — a rebuild that is deterministic is one whose diffs mean something.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not _skip_dir(name))
        for name in sorted(filenames):
            if name.lower().endswith(MARKDOWN_SUFFIXES):
                yield Path(dirpath) / name


def relative_path(root: Path, path: Path) -> str:
    """``path`` as a vault-relative POSIX string — the note's logical key.

    POSIX separators on Windows too: the path is stored, compared and shown to
    the learner, and a key that changes shape with the platform is a key that
    silently duplicates rows.
    """
    return path.relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Frontmatter:
    """A note's parsed frontmatter, and whether parsing had complaints.

    ``fields`` maps a lowercased key to its values *always as a list* — a scalar
    is a one-element list — so a caller never has to branch on "is this a list
    today?". ``ok`` is False when something in the block could not be understood;
    ``error`` says what. Neither is fatal: a note with unreadable frontmatter is
    still indexed by its body (spec edge case), it is merely flagged.
    """

    fields: dict[str, list[str]]
    ok: bool
    error: str | None

    def first(self, key: str) -> str | None:
        """The first value of ``key``, or None. Convenience for scalar fields."""
        values = self.fields.get(key.lower())
        return values[0] if values else None


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _split_inline_list(value: str) -> list[str]:
    """``[a, b]`` (and bare ``a, b``) to a list of unquoted, non-empty items."""
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [item for item in (_unquote(part) for part in inner.split(",")) if item]


def _scalar_or_list(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        return _split_inline_list(text)
    unquoted = _unquote(text)
    return [unquoted] if unquoted else []


def parse_frontmatter(text: str) -> tuple[Frontmatter, str]:
    """Split ``text`` into (frontmatter, body). Never raises.

    This is a **minimal, tolerant** parser, not YAML: Katagiri depends on no YAML
    library (see ``pyproject.toml``) and this feature is not a good enough reason
    to add one. It understands what Obsidian frontmatter actually contains —
    ``key: value``, ``key: [a, b]``, and a ``-`` block list under a bare key —
    plus ``#`` comments, and it *reports* anything else instead of guessing:

    * no opening ``---`` on the first line → no frontmatter, not an error;
    * an unclosed block → the whole file is treated as body, flagged;
    * an unparseable or nested line → skipped, flagged, the rest still parsed.

    A repeated key extends its list rather than replacing it, because "tags
    appears twice" is a mistake whose halves are both worth finding.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != _FM_OPEN:
        return Frontmatter(fields={}, ok=True, error=None), text

    end = next(
        (i for i in range(1, len(lines)) if lines[i].strip() in _FM_CLOSE), None
    )
    if end is None:
        return (
            Frontmatter(
                fields={},
                ok=False,
                error=(
                    "frontmatter opens with '---' but is never closed; the whole "
                    "file was indexed as body text"
                ),
            ),
            text,
        )

    fields: dict[str, list[str]] = {}
    problems: list[str] = []
    current: str | None = None

    for offset, raw in enumerate(lines[1:end], start=2):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current is None:
                problems.append(f"line {offset}: list item before any key")
                continue
            item = _unquote(stripped[2:])
            if item:
                fields[current].append(item)
            continue
        match = _FM_KEY_RE.match(line)
        if match is None:
            problems.append(f"line {offset}: not 'key: value' and not a list item")
            continue
        if line[0].isspace():
            # A nested mapping. Understanding it would mean implementing YAML;
            # saying so is better than dropping the field without a word.
            problems.append(f"line {offset}: nested mapping is not indexed")
            continue
        key = match.group("key").strip().lower()
        current = key
        fields.setdefault(key, []).extend(_scalar_or_list(match.group("rest")))

    error = None
    if problems:
        shown = problems[:_MAX_FM_PROBLEMS]
        if len(problems) > _MAX_FM_PROBLEMS:
            shown.append(f"and {len(problems) - _MAX_FM_PROBLEMS} more")
        error = "; ".join(shown)

    body = "\n".join(lines[end + 1:])
    return Frontmatter(fields=fields, ok=not problems, error=error), body


# ---------------------------------------------------------------------------
# Reading one note
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NoteContent:
    """One markdown file, read and prepared for indexing.

    ``indexed_text`` is the title line followed by the body, and it is what
    *both* FTS indexes see (``shadow_text`` is its fugashi-segmented form). The
    title is included so a note is findable by its heading — the learner's
    "where did I write about conditional forms?" is as often a filename question
    as a body question.
    """

    path: str
    title: str
    generated: bool
    frontmatter: Frontmatter
    body: str
    indexed_text: str
    shadow_text: str
    size_bytes: int
    mtime_ns: int
    sha256: str


def _decode(data: bytes, path: Path) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NoteReadError(
            f"{path} is not valid UTF-8 ({exc.reason} at byte {exc.start}); "
            "Katagiri indexes UTF-8 markdown only."
        ) from exc
    # BOM, then CRLF: both would otherwise reach the frontmatter parser, where a
    # leading '﻿---' is not a delimiter and a trailing '\r' is not '---'.
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def read_note(root: Path, path: Path) -> NoteContent:
    """Read, parse and tokenize one note. Raises :class:`NoteReadError` only.

    Tokenization is the expensive step (fugashi over the whole body), which is
    why change detection in :func:`rebuild_md_index` decides *before* calling
    this whether the file needs re-reading at all.
    """
    try:
        stat = path.stat()
        data = path.read_bytes()
    except OSError as exc:
        raise NoteReadError(f"Could not read {path}: {exc}") from exc

    text = _decode(data, path)
    frontmatter, body = parse_frontmatter(text)
    rel = relative_path(root, path)
    title = frontmatter.first("title") or path.stem
    indexed_text = f"{title}\n{body}" if body else title

    return NoteContent(
        path=rel,
        title=title,
        generated=is_generated(rel),
        frontmatter=frontmatter,
        body=body,
        indexed_text=indexed_text,
        shadow_text=shadow_text(indexed_text),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=hashlib.sha256(data).hexdigest(),
    )


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

    ``frontmatter_errors`` and ``generated_files`` describe the *whole* corpus
    scanned, not just the part re-indexed: for a file skipped as unchanged they
    are read back from its row. Otherwise an incremental run would report zero
    flagged notes and look like the flags had cleared themselves.
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


def _indexed_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Every indexed note, keyed by path: the whole freshness picture in one read.

    One query rather than a lookup per file. A vault is thousands of notes at
    most, and the alternative — a SELECT inside the walk — turns an incremental
    run into one round trip per file whether or not anything changed.
    """
    return {
        str(row["path"]): row
        for row in conn.execute(
            f"SELECT rowid, path, generated, frontmatter_ok, size_bytes, mtime_ns, "
            f"content_sha256, index_version, dict_version, tokenizer_version "
            f"FROM {NOTE_TABLE}"
        )
    }


def _stamps_current(
    row: sqlite3.Row, *, dict_version: str, tokenizer_version: str
) -> bool:
    """True when ``row`` was built by the pipeline, dictionary and tokenizer in force.

    A stale stamp forces a re-index even for a file that has not been touched:
    ``shadow_text`` is a *function of the tokenizer*, so a dictionary upgrade
    invalidates rows whose bytes on disk are identical.
    """
    return (
        row["index_version"] == MD_INDEX_VERSION
        and row["dict_version"] == dict_version
        and row["tokenizer_version"] == tokenizer_version
    )


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def _write_note(
    conn: sqlite3.Connection,
    note: NoteContent,
    *,
    rowid: int | None,
    dict_version: str,
    tokenizer_version: str,
    now: str,
) -> int:
    """Write one note's rows, replacing any it already had. Returns its rowid.

    The rowid is reused when the note is already indexed, so a path keeps its
    identity across edits. Delete-then-insert rather than UPDATE: the note row,
    its frontmatter rows and its two index entries have to move together, and one
    delete per table is both shorter and impossible to get partially right.
    """
    if rowid is None:
        rowid = int(
            conn.execute(
                f"SELECT COALESCE(MAX(rowid), 0) + 1 FROM {NOTE_TABLE}"
            ).fetchone()[0]
        )
    else:
        _delete_note(conn, rowid)

    conn.execute(
        f"INSERT INTO {NOTE_TABLE} (rowid, path, title, generated, frontmatter, "
        "frontmatter_ok, frontmatter_error, size_bytes, mtime_ns, content_sha256, "
        "body_chars, index_version, dict_version, tokenizer_version, indexed_ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rowid,
            note.path,
            note.title,
            int(note.generated),
            json.dumps(note.frontmatter.fields, ensure_ascii=False, sort_keys=True),
            int(note.frontmatter.ok),
            note.frontmatter.error,
            note.size_bytes,
            note.mtime_ns,
            note.sha256,
            len(note.body),
            MD_INDEX_VERSION,
            dict_version,
            tokenizer_version,
            now,
        ),
    )

    conn.executemany(
        f"INSERT INTO {FRONTMATTER_TABLE} (note_rowid, key, idx, value) "
        "VALUES (?, ?, ?, ?)",
        [
            (rowid, key, idx, value)
            for key, values in note.frontmatter.fields.items()
            for idx, value in enumerate(values)
        ],
    )
    conn.execute(
        f"INSERT INTO {WORD_INDEX} (rowid, shadow_text) VALUES (?, ?)",
        (rowid, note.shadow_text),
    )
    conn.execute(
        f"INSERT INTO {TRIGRAM_INDEX} (rowid, body) VALUES (?, ?)",
        (rowid, note.indexed_text),
    )
    return rowid


def _delete_note(conn: sqlite3.Connection, rowid: int) -> None:
    """Remove every trace of one note. Deleting by rowid is why the FTS tables
    are self-contained rather than external content — see the module docstring."""
    conn.execute(f"DELETE FROM {WORD_INDEX} WHERE rowid = ?", (rowid,))
    conn.execute(f"DELETE FROM {TRIGRAM_INDEX} WHERE rowid = ?", (rowid,))
    conn.execute(
        f"DELETE FROM {FRONTMATTER_TABLE} WHERE note_rowid = ?", (rowid,)
    )
    conn.execute(f"DELETE FROM {NOTE_TABLE} WHERE rowid = ?", (rowid,))


def rebuild_md_index(
    conn: sqlite3.Connection,
    *,
    root: Path | str | None = None,
    full: bool = False,
) -> MdIndexResult:
    """Bring the markdown index in line with the vault, and report what changed.

    Incremental by default. Three questions are asked per file, cheapest first:

    1. Do the stamps still match (pipeline, dictionary, tokenizer)? A stale stamp
       re-indexes regardless of the file, because ``shadow_text`` is a function
       of the tokenizer.
    2. Do size and mtime still match? If so the file is not even opened.
    3. Does the content hash still match? A file whose mtime moved but whose
       bytes did not (a save with no edit, a sync, a ``touch``) has its freshness
       metadata updated and is counted as unchanged, not re-tokenized.

    So the cost of a run is proportional to what changed, and editing one note
    re-indexes exactly one file (SC-003 — the returned report and the stderr line
    logged from it are the evidence).

    ``full=True`` is the derived-tier drop-and-rebuild: every row is deleted up
    front and every file re-read, no questions asked.

    Notes whose file disappeared — deleted, or renamed, which is a delete plus an
    add — have every row removed, so a stale path can never produce a ghost hit.
    A note that cannot be *read* is different: it is counted in ``files_failed``,
    logged, and its existing rows are kept rather than treated as a deletion.
    """
    started = time.perf_counter()
    versions = current_versions(conn)
    dict_version = versions[DICT_VERSION_KEY]
    tokenizer_version = versions[TOKENIZER_VERSION_KEY]
    now = _utc_now()
    root_path = vault_root(root)

    scanned = indexed = removed = unchanged = failed = 0
    frontmatter_errors = generated_files = 0

    with _atomic(conn):
        known = _indexed_rows(conn)
        if full:
            _clear(conn)
        seen: set[str] = set()

        for path in iter_markdown_files(root_path):
            scanned += 1
            rel = relative_path(root_path, path)
            row = None if full else known.get(rel)

            try:
                stat = path.stat()
            except OSError as exc:
                failed += 1
                seen.add(rel)  # not a deletion: keep whatever rows it has
                _logger.warning("skipping unreadable note %s: %s", rel, exc)
                continue

            fresh = row is not None and _stamps_current(
                row, dict_version=dict_version, tokenizer_version=tokenizer_version
            )
            if (
                fresh
                and row["size_bytes"] == stat.st_size
                and row["mtime_ns"] == stat.st_mtime_ns
            ):
                seen.add(rel)
                unchanged += 1
                generated_files += int(row["generated"])
                frontmatter_errors += int(not row["frontmatter_ok"])
                continue

            try:
                note = read_note(root_path, path)
            except NoteReadError as exc:
                failed += 1
                seen.add(rel)
                _logger.warning("skipping unreadable note %s: %s", rel, exc)
                continue

            seen.add(note.path)
            generated_files += int(note.generated)
            frontmatter_errors += int(not note.frontmatter.ok)
            if not note.frontmatter.ok:
                _logger.debug(
                    "frontmatter problem in %s: %s", note.path, note.frontmatter.error
                )

            if fresh and row["content_sha256"] == note.sha256:
                # Same bytes, new mtime. Record the new metadata so the next run
                # can stop at question 2, and leave the indexed rows alone.
                conn.execute(
                    f"UPDATE {NOTE_TABLE} SET size_bytes = ?, mtime_ns = ? "
                    "WHERE rowid = ?",
                    (note.size_bytes, note.mtime_ns, row["rowid"]),
                )
                unchanged += 1
                continue

            _write_note(
                conn,
                note,
                rowid=None if row is None else int(row["rowid"]),
                dict_version=dict_version,
                tokenizer_version=tokenizer_version,
                now=now,
            )
            indexed += 1

        for rel, row in known.items():
            if rel in seen:
                continue
            if not full:  # a full run already cleared every row
                _delete_note(conn, int(row["rowid"]))
            removed += 1
            _logger.debug("removed vanished note from the index: %s", rel)

    duration = time.perf_counter() - started
    result = MdIndexResult(
        root=str(root_path),
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


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

INDEX_EMPTY_NOTE: Final = (
    "md_note holds no rows, so the markdown index is empty and no note hit is "
    "possible yet. Run rebuild_md_index() (python -m katagiri.md_search rebuild) "
    "to index the vault."
)

_LIKE_SPECIALS: Final = re.compile(r"([%_\\])")

#: ``snippet()`` counts *tokens*, and the two indexes tokenize very differently:
#: a word-index token is a morph, a trigram token is a 3-character window at
#: every offset — so N trigram tokens is roughly N characters, and asking for a
#: dozen would return a dozen letters.
_WORDS_SNIPPET_TOKENS: Final = 16
_TRIGRAM_SNIPPET_TOKENS: Final = 64


def _fts_phrase(query: str) -> str:
    """Quote ``query`` as one FTS5 phrase, exactly as the other search paths do.

    The quotes are what make the whole string data: a phrase matches as a literal
    sequence of tokens, so nothing inside it can be re-read as an operator. Any
    surviving ``"`` is doubled, FTS5's own escape.
    """
    return '"' + query.replace('"', '""') + '"'


def _like_prefix(value: str) -> str:
    """A LIKE pattern matching ``value`` as a literal prefix."""
    return _LIKE_SPECIALS.sub(r"\\\1", value) + "%"


def _as_values(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else [str(item) for item in value]


def _filters(
    tags: Sequence[str] | None,
    fields: Mapping[str, str | Sequence[str]] | None,
    path_prefix: str | None,
    include_generated: bool,
) -> tuple[list[str], list[Any]]:
    """SQL fragments and parameters for the non-text half of a query.

    Frontmatter filters are ``EXISTS`` subqueries against ``md_frontmatter``
    rather than joins: a note matches a tag once, and a join would multiply the
    row out once per matching value and quietly break ``LIMIT``.

    Semantics, stated once so a caller never has to guess: keys are ANDed, values
    within one key are ORed, and every tag in ``tags`` must be present (a tag
    filter narrows). Matching is ASCII-case-insensitive (``COLLATE NOCASE``),
    which is what ``#Grammar`` vs ``#grammar`` needs and all it needs.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if not include_generated:
        clauses.append("n.generated = 0")

    if path_prefix:
        clauses.append("n.path LIKE ? ESCAPE '\\'")
        params.append(_like_prefix(path_prefix))

    for tag in tags or ():
        clauses.append(
            f"EXISTS (SELECT 1 FROM {FRONTMATTER_TABLE} f WHERE "
            "f.note_rowid = n.rowid AND f.key = 'tags' AND f.value = ? COLLATE NOCASE)"
        )
        params.append(tag)

    for key, value in (fields or {}).items():
        values = _as_values(value)
        if not values:
            continue
        placeholders = ", ".join("?" for _ in values)
        clauses.append(
            f"EXISTS (SELECT 1 FROM {FRONTMATTER_TABLE} f WHERE "
            "f.note_rowid = n.rowid AND f.key = ? COLLATE NOCASE AND "
            f"f.value COLLATE NOCASE IN ({placeholders}))"
        )
        params.append(str(key).lower())
        params.extend(values)

    return clauses, params


def _hit(row: sqlite3.Row, *, source_index: str | None) -> dict[str, Any]:
    try:
        frontmatter = json.loads(row["frontmatter"]) if row["frontmatter"] else {}
    except json.JSONDecodeError:  # pragma: no cover - json_valid CHECK prevents it
        frontmatter = {}
    return {
        "path": row["path"],
        "title": row["title"],
        "generated": bool(row["generated"]),
        "frontmatter": frontmatter,
        "frontmatter_ok": bool(row["frontmatter_ok"]),
        "excerpt": row["excerpt"] if "excerpt" in row.keys() else None,
        "source_index": source_index,
    }


def search_notes(
    conn: sqlite3.Connection,
    query: str | None = None,
    *,
    tags: Sequence[str] | None = None,
    fields: Mapping[str, str | Sequence[str]] | None = None,
    path_prefix: str | None = None,
    include_generated: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Search indexed notes by body text, by frontmatter, or by both.

    A pure function of ``conn`` and its arguments: no config, no I/O, no vault
    access. A tool adapter is a thin wrapper that opens the database and hands
    the result through.

    Body search is length-routed exactly as ``mcp_server.search_db_query`` routes
    sentence search, and for the same reason: FTS5's trigram tokenizer indexes
    3-character windows, so a 1- or 2-character query matches *nothing* — and it
    fails silently, which is the dangerous part. Those go to the ``unicode61``
    index over the fugashi-segmented shadow text, longer ones to trigram over the
    raw text. The one difference from ``search_db``: the query is sanitized of
    FTS5 syntax *before* its length is measured, so ``評価*`` routes as the two
    characters it really is.

    ``query`` may be omitted, in which case this is a pure frontmatter query
    (``tags``/``fields``/``path_prefix``) and results come back in path order.
    ``.derived/`` notes are excluded unless ``include_generated`` is set, so
    dashboard output does not drown out prose.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1; got {limit}.")

    text = sanitize_query(query or "").strip()
    has_filters = bool(tags or fields or path_prefix)
    if not text and not has_filters:
        raise ValueError(
            "search_notes needs a query or at least one frontmatter filter; "
            f"{query!r} is empty once FTS5 syntax and bare operators are removed."
        )

    clauses, params = _filters(tags, fields, path_prefix, include_generated)

    columns = (
        "n.rowid AS rowid, n.path AS path, n.title AS title, "
        "n.generated AS generated, n.frontmatter AS frontmatter, "
        "n.frontmatter_ok AS frontmatter_ok"
    )

    if text:
        route = "words" if len(text) < TRIGRAM_MIN_CHARS else "trigram"
        index, column, window = (
            (WORD_INDEX, "shadow_text", _WORDS_SNIPPET_TOKENS)
            if route == "words"
            else (TRIGRAM_INDEX, "body", _TRIGRAM_SNIPPET_TOKENS)
        )
        route_reason = (
            f"query is {len(text)} character(s), under {TRIGRAM_MIN_CHARS}: the "
            f"trigram index cannot match it, so the unicode61 word index "
            f"({WORD_INDEX}) is used. Excerpts from it show fugashi-segmented "
            "text, because that is what it indexes"
            if route == "words"
            else f"query is {len(text)} character(s): substring search via the "
            f"trigram index ({TRIGRAM_INDEX})"
        )
        # `index`/`column` are module constants chosen by the route, never caller
        # input: an FTS5 table name cannot be a bound parameter.
        sql = (
            f"SELECT {columns}, snippet({index}, 0, '[', ']', ' … ', {window}) "
            "AS excerpt "
            f"FROM {index} JOIN {NOTE_TABLE} n ON n.rowid = {index}.rowid "
            f"WHERE {index} MATCH ?"
            + "".join(f" AND {clause}" for clause in clauses)
            + " ORDER BY rank LIMIT ?"
        )
        try:
            rows = conn.execute(sql, (_fts_phrase(text), *params, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError(
                f"SQLite rejected the full-text query {text!r} against {index} "
                f"({column}): {exc}"
            ) from exc
        source_index: str | None = index
    else:
        route = None
        route_reason = (
            "no text query: frontmatter filters only, results in path order"
        )
        where = " AND ".join(clauses) or "1"
        rows = conn.execute(
            f"SELECT {columns} FROM {NOTE_TABLE} n WHERE {where} "
            "ORDER BY n.path LIMIT ?",
            (*params, limit),
        ).fetchall()
        source_index = None

    indexed_notes = int(conn.execute(f"SELECT COUNT(*) FROM {NOTE_TABLE}").fetchone()[0])
    hits = [_hit(row, source_index=source_index) for row in rows]

    return {
        "query": text or None,
        "limit": limit,
        "route": route,
        "route_reason": route_reason,
        "filters": {
            "tags": list(tags or ()),
            "fields": {
                str(key).lower(): _as_values(value)
                for key, value in (fields or {}).items()
            },
            "path_prefix": path_prefix,
            "include_generated": include_generated,
        },
        "hits": hits,
        "hit_count": len(hits),
        "indexed_notes": indexed_notes,
        "index_empty": indexed_notes == 0,
        "note": INDEX_EMPTY_NOTE if indexed_notes == 0 else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _use_utf8_stderr() -> None:
    """Make Japanese printable on a cp1252 Windows console instead of crashing."""
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - redirected stderr
            pass


def main(argv: list[str] | None = None) -> int:
    """``python -m katagiri.md_search [rebuild|search] ...``. Output goes to stderr.

    ``rebuild`` is also how SC-003 is observed by hand: it prints the same report
    the function returns, next to the stderr line the run logged.
    """
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.md_search",
        description=(
            "Index or search the vault's markdown. 'rebuild' brings the derived "
            "index in line with the vault (incrementally unless --full); "
            "'search' queries it. Both write to stderr, like every other "
            "Katagiri diagnostic, and neither needs Obsidian running."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rebuild = sub.add_parser("rebuild", help="index the vault")
    rebuild.add_argument("--root", default=None, help="vault path (default: config)")
    rebuild.add_argument(
        "--full", action="store_true", help="drop every row and re-read every file"
    )

    search = sub.add_parser("search", help="query the index")
    search.add_argument("query", nargs="?", default=None)
    search.add_argument("--tag", action="append", dest="tags", default=None)
    search.add_argument("--path-prefix", default=None)
    search.add_argument("--include-generated", action="store_true")
    search.add_argument("--limit", type=int, default=DEFAULT_LIMIT)

    args = parser.parse_args(argv)
    _use_utf8_stderr()

    # Imported here so that importing this module does not touch the filesystem
    # or the configured database path.
    from katagiri.db import open_db
    from katagiri.logging_setup import setup_logging

    setup_logging()

    conn = open_db()
    try:
        if args.command == "rebuild":
            print(
                rebuild_md_index(conn, root=args.root, full=args.full).render(),
                file=sys.stderr,
            )
            return 0
        result = search_notes(
            conn,
            args.query,
            tags=args.tags,
            path_prefix=args.path_prefix,
            include_generated=args.include_generated,
            limit=args.limit,
        )
    except (MdSearchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"route  : {result['route']} ({result['route_reason']})", file=sys.stderr)
    print(f"hits   : {result['hit_count']} of {result['indexed_notes']} notes",
          file=sys.stderr)
    for hit in result["hits"]:
        print(f"  {hit['path']} — {hit['title']}", file=sys.stderr)
        if hit["excerpt"]:
            print(f"      {hit['excerpt']}", file=sys.stderr)
    if result["note"]:
        print(result["note"], file=sys.stderr)
    return 0


__all__ = [
    "DEFAULT_LIMIT",
    "FRONTMATTER_TABLE",
    "GENERATED_DIR",
    "INDEX_EMPTY_NOTE",
    "MARKDOWN_SUFFIXES",
    "MD_INDEX_VERSION",
    "NOTE_TABLE",
    "SKIP_DIRS",
    "TRIGRAM_INDEX",
    "TRIGRAM_MIN_CHARS",
    "WORD_INDEX",
    "Frontmatter",
    "MdIndexResult",
    "MdSearchError",
    "NoteContent",
    "NoteReadError",
    "VaultNotFoundError",
    "is_generated",
    "iter_markdown_files",
    "main",
    "parse_frontmatter",
    "read_note",
    "rebuild_md_index",
    "relative_path",
    "search_notes",
    "vault_root",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
