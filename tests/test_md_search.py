"""C2/T007: the markdown prose index, on a frozen fixture vault.

What is being defended here is the half of the dual-search design that
``search_db`` cannot answer: prose. Five behaviours carry the phase's acceptance
scenarios and edge cases (spec.md FR-002/FR-003/FR-006), and each has a test
below that fails for a real reason:

* **frontmatter is queryable apart from the body** — a tag filter narrows to the
  tagged notes, and a term that lives only in frontmatter is not confused with
  one that lives in prose;
* **short and long Japanese queries both work** — 勉強 is two characters, so a
  trigram index has no window for it (the same trap ``test_fts_index.py``
  documents for the DB side); the long running-prose query must still reach the
  same notes;
* **re-indexing is incremental** — editing one note and running again re-indexes
  exactly one file, asserted on the returned report rather than on a stopwatch
  (SC-003);
* **malformed frontmatter is not fatal** — the file's body is still searchable
  and the note is flagged, not dropped;
* **deleted notes leave no ghost hits** — the next incremental run removes them.

The fixture vault is copied into ``tmp_path`` for every test, because three of
these tests mutate it. ``tests/fixtures/vault/`` itself is frozen: the counts and
the unique terms (``thunderstruck``, ``ghosthunter``, ``幽霊``) are load-bearing.

Database wiring follows the repo pattern — no ``conftest.py``, an inline fixture
that moves ``LOCALAPPDATA`` and lets the module's own ``open_db()``/``get_config()``
find the scratch database and the scratch vault (``test_mcp_tools.py``:57–72,
``test_averify.py``:320–340 for the ``vault_path`` half).

PHASE-1 NOTE — the API below is a placeholder pinned to the T003–T006 task text,
not to shipped code. Every call goes through the adapter helpers in the
"placeholder API" section so that phase 2 adapts one block, not fifty
assertions. Assertions themselves are behavioural and should survive renaming.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import tokenizer as tok
from katagiri.db import open_db

# ---------------------------------------------------------------------------
# Placeholder API — TODO confirm in phase 2 against the shipped md_search.py
# ---------------------------------------------------------------------------

from katagiri.md_search import (  # TODO confirm in phase 2
    rebuild_md_index,
    search_notes,
)

fugashi = pytest.importorskip("fugashi")


def _dicdir_available() -> bool:
    try:
        tok.dicdir_path()
    except tok.TokenizerError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _dicdir_available(),
    reason=(
        "vendored UniDic 3.1.0 is absent (vendor/unidic/unidic); see "
        "vendor/README.md"
    ),
)

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"

# Five hand-written notes plus one generated file under `.derived/`.
MARKDOWN_FILES = 6

# Unique terms, each present in exactly one fixture file.
BODY_ONLY_MALFORMED = "thunderstruck"
GHOST_EN = "ghosthunter"
GHOST_JP = "幽霊"

# 勉強 is two characters: the short-query case that a trigram index misses.
SHORT_JP = "勉強"
LONG_JP = "毎日日本語を勉強しています"


def _rebuild(conn: sqlite3.Connection, **kwargs: Any) -> Any:
    """One call site for the indexer.

    T003 specifies ``rebuild_md_index()`` returning a structured report with
    ``files_scanned`` / ``indexed`` / ``removed``; T005 says an incremental run
    returns the same shape. Whether the vault root is read from config or passed
    in is a phase-2 detail — it is decided here.
    """
    return rebuild_md_index(conn, **kwargs)  # TODO confirm in phase 2


def _search(conn: sqlite3.Connection, query: str = "", **filters: Any) -> Any:
    """One call site for the query API (T006).

    ``filters`` carries the frontmatter side (``tags=``, ``type=``, ``date=``)
    and the generated-file switch (``include_generated=``).
    """
    return search_notes(conn, query, **filters)  # TODO confirm in phase 2


def _field(row: Any, name: str) -> Any:
    """Read a field off a result row or a report, mapping or dataclass alike."""
    if isinstance(row, dict):
        return row[name]
    return getattr(row, name)


def _names(results: Any) -> set[str]:
    """File names of the hits.

    Names, not paths: every fixture file has a distinct basename, so this is
    unambiguous while staying indifferent to whether the module reports vault-
    relative or absolute paths.
    """
    return {Path(str(_field(row, "path"))).name for row in results}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A writable copy of the frozen fixture vault."""
    destination = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, destination)
    return destination


@pytest.fixture
def db(tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch):
    """A migrated database whose config also points at the scratch vault.

    The module takes no connection *and* no vault argument in the tool path, so
    the only honest way to point it at scratch data is to move the configuration
    — which exercises the real config path as a side effect.
    """
    app_data = tmp_path / "AppData"
    (app_data / "Katagiri").mkdir(parents=True)
    (app_data / "Katagiri" / "config.toml").write_text(
        f'vault_path = "{vault.as_posix()}"\n', encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(app_data))
    config_mod.reset_config_cache()
    conn = open_db()
    try:
        # The A3 precondition: a rebuild that stamps rows refuses to run without
        # dict/tokenizer versions in `metadata`.
        tok.stamp_versions(conn)
        yield conn
    finally:
        conn.close()
        config_mod.reset_config_cache()


def _touch_later(path: Path, seconds: int = 10) -> None:
    """Push a file's mtime forward so change detection cannot tie on a clock tick."""
    stamp = path.stat().st_mtime + seconds
    os.utime(path, (stamp, stamp))


# ---------------------------------------------------------------------------
# Rebuild + report shape
# ---------------------------------------------------------------------------


def test_rebuild_scans_every_markdown_file_including_derived(db) -> None:
    """A full rebuild sees all six files — the malformed one and `.derived/` too."""
    report = _rebuild(db)

    assert _field(report, "files_scanned") == MARKDOWN_FILES
    assert _field(report, "indexed") == MARKDOWN_FILES
    assert _field(report, "removed") == 0


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_tag_filter_narrows_to_tagged_notes(db) -> None:
    """`tags: [grammar, ...]` is a filter, and it excludes the untagged notes."""
    _rebuild(db)

    hits = _names(_search(db, tags="grammar"))

    assert "01-grammar-conditionals.md" in hits
    assert "03-mixed-en-jp.md" in hits
    # 02 is tagged japanese/vocab, 05 is scratch: neither carries `grammar`.
    assert "02-japanese-prose.md" not in hits
    assert "05-scratch-ghost.md" not in hits


def test_frontmatter_is_queryable_apart_from_body_text(db) -> None:
    """The two sides answer different questions about the same word.

    `conditional` is a tag on 01 *and* prose in 01 and 03. Filtering on the tag
    must not drag in the note that only mentions it in prose, which is the whole
    point of FR-002.
    """
    _rebuild(db)

    by_tag = _names(_search(db, tags="conditional"))
    by_body = _names(_search(db, "conditional"))

    assert by_tag == {"01-grammar-conditionals.md"}
    assert {"01-grammar-conditionals.md", "03-mixed-en-jp.md"} <= by_body


def test_frontmatter_scalar_fields_filter_independently(db) -> None:
    """`type` and `date` are separate fields, not one blob of frontmatter text."""
    _rebuild(db)

    dailies = _names(_search(db, type="daily"))
    on_the_18th = _names(_search(db, date="2026-08-18"))

    assert dailies == {"03-mixed-en-jp.md"}
    assert on_the_18th == {"02-japanese-prose.md"}


# ---------------------------------------------------------------------------
# Japanese: short and long queries
# ---------------------------------------------------------------------------


def test_short_japanese_query_reaches_japanese_and_mixed_notes(db) -> None:
    """勉強 — two characters, so this is the route a trigram index cannot serve."""
    _rebuild(db)

    hits = _names(_search(db, SHORT_JP))

    assert {"02-japanese-prose.md", "03-mixed-en-jp.md"} <= hits


def test_short_japanese_query_matches_a_single_word(db) -> None:
    """単語 appears in the Japanese, the mixed and the malformed note."""
    _rebuild(db)

    hits = _names(_search(db, "単語"))

    assert {
        "02-japanese-prose.md",
        "03-mixed-en-jp.md",
        "04-malformed-frontmatter.md",
    } <= hits


def test_long_japanese_query_matches_running_prose(db) -> None:
    """The same sentence sits in the Japanese note and the mixed one."""
    _rebuild(db)

    hits = _names(_search(db, LONG_JP))

    assert {"02-japanese-prose.md", "03-mixed-en-jp.md"} <= hits


# ---------------------------------------------------------------------------
# Incremental re-index (SC-003)
# ---------------------------------------------------------------------------


def test_incremental_reindex_touches_only_the_edited_file(db, vault: Path) -> None:
    """One note edited → `indexed == 1`, on the report the task makes the evidence."""
    _rebuild(db)

    note = vault / "02-japanese-prose.md"
    note.write_text(
        note.read_text(encoding="utf-8") + "\n昨日は文法の練習をしました。\n",
        encoding="utf-8",
    )
    _touch_later(note)

    report = _rebuild(db)

    assert _field(report, "indexed") == 1, "only the edited note may be re-indexed"
    assert _field(report, "removed") == 0
    # Scanning is cheap and still covers the vault; re-indexing is what must not.
    assert _field(report, "files_scanned") == MARKDOWN_FILES


def test_incremental_reindex_returns_the_new_text(db, vault: Path) -> None:
    """An edit is visible to search, and the replaced text is not."""
    _rebuild(db)
    assert _names(_search(db, "五つ")) == {"02-japanese-prose.md"}

    note = vault / "02-japanese-prose.md"
    note.write_text(
        "---\ntitle: 勉強ノート\ntags: [japanese]\n---\n\n昨日は文法の練習をしました。\n",
        encoding="utf-8",
    )
    _touch_later(note)
    _rebuild(db)

    assert _names(_search(db, "文法")) == {"02-japanese-prose.md"}
    assert _names(_search(db, "五つ")) == set(), "the replaced text must be gone"


def test_untouched_vault_reindexes_nothing(db) -> None:
    """A second run over an unchanged vault is a no-op, not a silent full rebuild."""
    _rebuild(db)

    report = _rebuild(db)

    assert _field(report, "indexed") == 0
    assert _field(report, "removed") == 0
    assert _field(report, "files_scanned") == MARKDOWN_FILES


# ---------------------------------------------------------------------------
# Malformed frontmatter
# ---------------------------------------------------------------------------


def test_malformed_frontmatter_is_not_fatal(db) -> None:
    """The rebuild completes and the other five files are indexed anyway."""
    report = _rebuild(db)

    assert _field(report, "indexed") == MARKDOWN_FILES
    assert _names(_search(db, SHORT_JP)), "a broken file must not empty the index"


def test_malformed_frontmatter_note_is_indexed_by_body(db) -> None:
    """Its body is searchable: `thunderstruck` exists nowhere else in the vault."""
    _rebuild(db)

    assert _names(_search(db, BODY_ONLY_MALFORMED)) == {
        "04-malformed-frontmatter.md"
    }


def test_malformed_frontmatter_is_flagged(db) -> None:
    """Flagged, not dropped — the operator can find it; the searcher is unaffected."""
    _rebuild(db)

    (hit,) = _search(db, BODY_ONLY_MALFORMED)

    # TODO confirm in phase 2: the flag's name (`frontmatter_ok` / `frontmatter_error`).
    assert _field(hit, "frontmatter_ok") is False


# ---------------------------------------------------------------------------
# Deletion: no ghost hits
# ---------------------------------------------------------------------------


def test_deleted_note_leaves_no_ghost_hits(db, vault: Path) -> None:
    """Delete the note, run again: the report counts the removal and search forgets it."""
    _rebuild(db)
    assert _names(_search(db, GHOST_EN)) == {"05-scratch-ghost.md"}

    (vault / "05-scratch-ghost.md").unlink()
    report = _rebuild(db)

    assert _field(report, "removed") == 1
    assert _field(report, "files_scanned") == MARKDOWN_FILES - 1
    assert _search(db, GHOST_EN) == []
    assert _search(db, GHOST_JP) == []


def test_renamed_note_is_found_only_under_its_new_name(db, vault: Path) -> None:
    """A rename is a delete plus an add; the old path must not survive as a hit."""
    _rebuild(db)

    (vault / "05-scratch-ghost.md").rename(vault / "05-renamed.md")
    _rebuild(db)

    assert _names(_search(db, GHOST_EN)) == {"05-renamed.md"}


# ---------------------------------------------------------------------------
# `.derived/` generated files
# ---------------------------------------------------------------------------


def test_derived_files_are_indexed_but_flagged_generated(db) -> None:
    """Indexed — but distinguishable, so dashboard noise can be filtered out."""
    _rebuild(db)

    hits = {Path(str(_field(row, "path"))).name: row for row in _search(db, SHORT_JP)}

    assert "today.md" in hits, "`.derived/` output is part of the corpus"
    # TODO confirm in phase 2: the flag's name (`generated` / `is_generated`).
    assert _field(hits["today.md"], "generated") is True
    assert _field(hits["02-japanese-prose.md"], "generated") is False


def test_generated_files_can_be_excluded_from_prose_results(db) -> None:
    """The filter that keeps the dashboard out of a prose answer."""
    _rebuild(db)

    hits = _names(_search(db, SHORT_JP, include_generated=False))

    assert "today.md" not in hits
    assert {"02-japanese-prose.md", "03-mixed-en-jp.md"} <= hits
