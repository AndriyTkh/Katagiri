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
  documents for the DB side); it must route to the word index, while running
  prose routes to trigram and reaches the same notes;
* **re-indexing is incremental** — editing one note and running again re-indexes
  exactly one file, asserted on the returned report rather than on a stopwatch
  (SC-003); a file whose mtime moved but whose bytes did not is *not* re-indexed;
* **malformed frontmatter is not fatal** — the file's body is still searchable
  and the note is flagged, not dropped;
* **deleted notes leave no ghost hits** — the next incremental run removes them.

The fixture vault is copied into ``tmp_path`` for every test, because four of
these tests mutate it. ``tests/fixtures/vault/`` itself is frozen: the file count
and the unique terms (``thunderstruck``, ``ghosthunter``, ``窓の近く``, ``幽霊``)
are load-bearing.

Database wiring follows the repo pattern — no ``conftest.py``, an inline fixture
that moves ``LOCALAPPDATA`` and lets ``open_db()``/``get_config()`` find the
scratch database and the scratch vault (``test_mcp_tools.py``:57–72,
``test_averify.py``:320–340 for the ``vault_path`` half). The vault root is
passed to :func:`rebuild_md_index` explicitly everywhere except
:func:`test_rebuild_falls_back_to_the_configured_vault`, which is the one test
that exists to prove the config fallback the MCP adapter will rely on.

Japanese queries are chosen to be tokenizer-robust: the multi-character ones go
through trigram, which is substring search over the raw text and therefore
independent of how fugashi segments; only the deliberate short-query cases
(勉強, 単語) depend on morph boundaries, which is exactly what they are testing.
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
from katagiri.md_search import rebuild_md_index, search_notes

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
ONLY_IN_JP_NOTE = "窓の近く"
GHOST_EN = "ghosthunter"
GHOST_JP = "幽霊"

# 勉強 is two characters: the short-query case a trigram index misses silently.
SHORT_JP = "勉強"
LONG_JP = "毎日日本語を勉強しています"


def _hits(result: dict[str, Any]) -> list[dict[str, Any]]:
    return result["hits"]


def _names(result: dict[str, Any]) -> set[str]:
    """File names of the hits.

    Names, not the vault-relative paths the module returns: every fixture file
    has a distinct basename, so this is unambiguous and it keeps the `.derived/`
    prefix out of assertions that are not about generated files.
    """
    return {Path(hit["path"]).name for hit in _hits(result)}


def _by_name(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {Path(hit["path"]).name: hit for hit in _hits(result)}


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
    """A migrated database, version-stamped, with the config pointing at the vault.

    Moving ``LOCALAPPDATA`` is the only honest way to give ``open_db()`` a
    scratch database, and it exercises the real config path as a side effect.
    The version stamps are not optional: a rebuild stamps every row it writes and
    refuses to run without them, because ``shadow_text`` is a function of the
    tokenizer and unstamped rows could not be invalidated later.
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


def test_rebuild_scans_every_markdown_file_including_derived(
    db: sqlite3.Connection, vault: Path
) -> None:
    """A first rebuild sees all six files — the malformed one and `.derived/` too."""
    report = rebuild_md_index(db, root=vault)

    assert report.files_scanned == MARKDOWN_FILES
    assert report.files_indexed == MARKDOWN_FILES
    assert report.files_removed == 0
    assert report.files_unchanged == 0
    assert report.files_failed == 0
    assert report.generated_files == 1, "`.derived/today.md`, and only it"
    assert report.frontmatter_errors == 1, "the malformed note, and only it"


def test_rebuild_falls_back_to_the_configured_vault(
    db: sqlite3.Connection, vault: Path
) -> None:
    """No ``root``: the vault comes from config, which is the tool path."""
    report = rebuild_md_index(db)

    assert Path(report.root) == vault
    assert report.files_indexed == MARKDOWN_FILES


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_frontmatter_tag_filter_narrows_to_tagged_notes(
    db: sqlite3.Connection, vault: Path
) -> None:
    """`tags: [grammar, ...]` is a filter, and it excludes the untagged notes."""
    rebuild_md_index(db, root=vault)

    hits = _names(search_notes(db, tags=["grammar"]))

    assert "01-grammar-conditionals.md" in hits
    assert "03-mixed-en-jp.md" in hits
    # 02 is tagged japanese/vocab, 05 is scratch: neither carries `grammar`.
    assert "02-japanese-prose.md" not in hits
    assert "05-scratch-ghost.md" not in hits


def test_frontmatter_is_queryable_apart_from_body_text(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The two sides answer different questions about the same word.

    `conditional` is a tag on 01 *and* prose in 01 and 03. Filtering on the tag
    must not drag in the note that only mentions it in prose, which is the whole
    point of FR-002.
    """
    rebuild_md_index(db, root=vault)

    by_tag = _names(search_notes(db, tags=["conditional"]))
    by_body = _names(search_notes(db, "conditional"))

    assert by_tag == {"01-grammar-conditionals.md"}
    assert {"01-grammar-conditionals.md", "03-mixed-en-jp.md"} <= by_body


def test_frontmatter_scalar_fields_filter_independently(
    db: sqlite3.Connection, vault: Path
) -> None:
    """`type` and `date` are separate fields, not one blob of frontmatter text."""
    rebuild_md_index(db, root=vault)

    dailies = _names(search_notes(db, fields={"type": "daily"}))
    on_the_18th = _names(search_notes(db, fields={"date": "2026-08-18"}))

    assert dailies == {"03-mixed-en-jp.md"}
    assert on_the_18th == {"02-japanese-prose.md"}


def test_frontmatter_filters_and_body_text_compose(
    db: sqlite3.Connection, vault: Path
) -> None:
    """Both notes carry the sentence; only one of them is tagged `vocab`."""
    rebuild_md_index(db, root=vault)

    assert _names(search_notes(db, LONG_JP)) == {
        "02-japanese-prose.md",
        "03-mixed-en-jp.md",
    }
    assert _names(search_notes(db, LONG_JP, tags=["vocab"])) == {
        "02-japanese-prose.md"
    }


# ---------------------------------------------------------------------------
# Japanese: short and long queries
# ---------------------------------------------------------------------------


def test_short_japanese_query_routes_to_the_word_index(
    db: sqlite3.Connection, vault: Path
) -> None:
    """勉強 — two characters, so this is the route a trigram index cannot serve."""
    rebuild_md_index(db, root=vault)

    result = search_notes(db, SHORT_JP)

    assert result["route"] == "words"
    assert _names(result) == {"02-japanese-prose.md", "03-mixed-en-jp.md"}


def test_short_japanese_query_matches_a_single_word(
    db: sqlite3.Connection, vault: Path
) -> None:
    """単語 sits in the Japanese, the mixed and the malformed note."""
    rebuild_md_index(db, root=vault)

    assert _names(search_notes(db, "単語")) == {
        "02-japanese-prose.md",
        "03-mixed-en-jp.md",
        "04-malformed-frontmatter.md",
    }


def test_long_japanese_query_matches_running_prose(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The same sentence sits in the Japanese note and the mixed one."""
    rebuild_md_index(db, root=vault)

    result = search_notes(db, LONG_JP)

    assert result["route"] == "trigram"
    assert _names(result) == {"02-japanese-prose.md", "03-mixed-en-jp.md"}


# ---------------------------------------------------------------------------
# Incremental re-index (SC-003)
# ---------------------------------------------------------------------------


def test_incremental_reindex_touches_only_the_edited_file(
    db: sqlite3.Connection, vault: Path
) -> None:
    """One note edited → `files_indexed == 1`, the report the task makes evidence."""
    rebuild_md_index(db, root=vault)

    note = vault / "02-japanese-prose.md"
    note.write_text(
        note.read_text(encoding="utf-8") + "\n昨日は文法の練習をしました。\n",
        encoding="utf-8",
    )
    _touch_later(note)

    report = rebuild_md_index(db, root=vault)

    assert report.files_indexed == 1, "only the edited note may be re-indexed"
    assert report.files_unchanged == MARKDOWN_FILES - 1
    assert report.files_removed == 0
    # Scanning is cheap and still covers the vault; re-indexing is what must not.
    assert report.files_scanned == MARKDOWN_FILES


def test_incremental_reindex_returns_the_new_text(
    db: sqlite3.Connection, vault: Path
) -> None:
    """An edit is visible to search, and the replaced text is not."""
    rebuild_md_index(db, root=vault)
    assert _names(search_notes(db, ONLY_IN_JP_NOTE)) == {"02-japanese-prose.md"}

    note = vault / "02-japanese-prose.md"
    note.write_text(
        "---\ntitle: 勉強ノート\ntags: [japanese]\n---\n\n昨日は文法の練習をしました。\n",
        encoding="utf-8",
    )
    _touch_later(note)
    rebuild_md_index(db, root=vault)

    assert _names(search_notes(db, "昨日は文法")) == {"02-japanese-prose.md"}
    assert _hits(search_notes(db, ONLY_IN_JP_NOTE)) == [], "replaced text must be gone"


def test_untouched_vault_reindexes_nothing(
    db: sqlite3.Connection, vault: Path
) -> None:
    """A second run over an unchanged vault is a no-op, not a silent full rebuild."""
    rebuild_md_index(db, root=vault)

    report = rebuild_md_index(db, root=vault)

    assert report.files_indexed == 0
    assert report.files_removed == 0
    assert report.files_unchanged == MARKDOWN_FILES
    assert report.files_scanned == MARKDOWN_FILES


def test_touched_but_unedited_file_is_not_reindexed(
    db: sqlite3.Connection, vault: Path
) -> None:
    """A save with no edit moves the mtime; the content hash says nothing changed."""
    rebuild_md_index(db, root=vault)

    _touch_later(vault / "02-japanese-prose.md")
    report = rebuild_md_index(db, root=vault)

    assert report.files_indexed == 0, "same bytes, so no re-tokenization"
    assert report.files_unchanged == MARKDOWN_FILES


def test_full_rebuild_reindexes_everything(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The derived-tier drop-and-rebuild: no questions asked, and no ghosts left."""
    rebuild_md_index(db, root=vault)

    report = rebuild_md_index(db, root=vault, full=True)

    assert report.full is True
    assert report.files_indexed == MARKDOWN_FILES
    assert report.files_unchanged == 0
    assert _names(search_notes(db, SHORT_JP)) == {
        "02-japanese-prose.md",
        "03-mixed-en-jp.md",
    }


# ---------------------------------------------------------------------------
# Malformed frontmatter
# ---------------------------------------------------------------------------


def test_malformed_frontmatter_is_not_fatal(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The rebuild completes, counts the problem, and indexes every file anyway."""
    report = rebuild_md_index(db, root=vault)

    assert report.files_indexed == MARKDOWN_FILES
    assert report.files_failed == 0, "unparseable frontmatter is not an unreadable file"
    assert report.frontmatter_errors == 1
    assert _hits(search_notes(db, SHORT_JP)), "a broken file must not empty the index"


def test_malformed_frontmatter_note_is_indexed_by_body(
    db: sqlite3.Connection, vault: Path
) -> None:
    """Its body is searchable: `thunderstruck` exists nowhere else in the vault."""
    rebuild_md_index(db, root=vault)

    assert _names(search_notes(db, BODY_ONLY_MALFORMED)) == {
        "04-malformed-frontmatter.md"
    }


def test_malformed_frontmatter_is_flagged(
    db: sqlite3.Connection, vault: Path
) -> None:
    """Flagged, not dropped — the operator can find it; the searcher is unaffected."""
    rebuild_md_index(db, root=vault)

    (broken,) = _hits(search_notes(db, BODY_ONLY_MALFORMED))

    assert broken["frontmatter_ok"] is False
    assert broken["frontmatter"] == {}, "an unclosed block yields no fields at all"
    assert _hits(search_notes(db, BODY_ONLY_MALFORMED, tags=["grammar"])) == [], (
        "so its half-written `tags: [grammar` is not a tag either"
    )


def test_intact_frontmatter_is_not_flagged(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The flag has to discriminate, or it says nothing about the broken note."""
    rebuild_md_index(db, root=vault)

    (hit,) = _hits(search_notes(db, "conditional", tags=["conditional"]))

    assert hit["frontmatter_ok"] is True
    assert hit["frontmatter"]["tags"] == ["grammar", "conditional"]


# ---------------------------------------------------------------------------
# Deletion: no ghost hits
# ---------------------------------------------------------------------------


def test_deleted_note_leaves_no_ghost_hits(
    db: sqlite3.Connection, vault: Path
) -> None:
    """Delete the note, run again: the report counts it and search forgets it."""
    rebuild_md_index(db, root=vault)
    assert _names(search_notes(db, GHOST_EN)) == {"05-scratch-ghost.md"}

    (vault / "05-scratch-ghost.md").unlink()
    report = rebuild_md_index(db, root=vault)

    assert report.files_removed == 1
    assert report.files_scanned == MARKDOWN_FILES - 1
    assert _hits(search_notes(db, GHOST_EN)) == []
    assert _hits(search_notes(db, GHOST_JP)) == []
    assert search_notes(db, GHOST_EN)["indexed_notes"] == MARKDOWN_FILES - 1


def test_renamed_note_is_found_only_under_its_new_name(
    db: sqlite3.Connection, vault: Path
) -> None:
    """A rename is a delete plus an add; the old path must not survive as a hit."""
    rebuild_md_index(db, root=vault)

    (vault / "05-scratch-ghost.md").rename(vault / "05-renamed.md")
    report = rebuild_md_index(db, root=vault)

    assert report.files_removed == 1
    assert report.files_indexed == 1
    assert _names(search_notes(db, GHOST_EN)) == {"05-renamed.md"}


# ---------------------------------------------------------------------------
# `.derived/` generated files
# ---------------------------------------------------------------------------


def test_derived_files_are_indexed_but_flagged_generated(
    db: sqlite3.Connection, vault: Path
) -> None:
    """Indexed — but distinguishable, so dashboard noise stays separable."""
    rebuild_md_index(db, root=vault)

    hits = _by_name(search_notes(db, SHORT_JP, include_generated=True))

    assert "today.md" in hits, "`.derived/` output is part of the corpus"
    assert hits["today.md"]["path"] == ".derived/today.md"
    assert hits["today.md"]["generated"] is True
    assert hits["02-japanese-prose.md"]["generated"] is False


def test_generated_files_are_excluded_from_prose_results_by_default(
    db: sqlite3.Connection, vault: Path
) -> None:
    """The default that keeps the dashboard out of a prose answer."""
    rebuild_md_index(db, root=vault)

    hits = _names(search_notes(db, SHORT_JP))

    assert "today.md" not in hits
    assert hits == {"02-japanese-prose.md", "03-mixed-en-jp.md"}
