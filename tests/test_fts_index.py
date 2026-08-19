"""Sentence FTS index tests, run against the **real vendored UniDic 3.1.0**.

No mock tagger, for the same reason ``test_tokenizer.py`` has none: the thing
under test is whether real fugashi segmentation makes ``unicode61`` able to match
a real Japanese word. A fake tokenizer that inserted spaces where the test wanted
them would prove nothing about the index. The dictionary is gitignored, so the
whole module skips when it is absent.

The load-bearing assertion in here is the 勉強 pair: two characters, found by the
word index and *missed* by the trigram index. That is not a curiosity, it is the
reason the schema carries two indexes and the reason
``mcp_server.search_db_query`` routes on query length — so the miss is asserted
explicitly, and a future "simplify to one index" change fails here.

FTS5 external-content correctness is checked with SQLite's own
``'integrity-check'`` command rather than by counting rows: ``SELECT COUNT(*)``
on an external-content FTS table reads the *content* table, so it would report
success for a completely empty index.
"""

from __future__ import annotations

import sqlite3

import pytest

from katagiri import db
from katagiri import fts_index
from katagiri import tokenizer as tok

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

TS = "2026-01-02T03:04:05Z"

# 勉強 is the whole point: exactly two characters, so no trigram window covers it.
S_BENKYOU = "毎日日本語を勉強しています"
# 評価 is the FTS-operator-injection subject: `評価 OR x` must not behave like OR.
S_HYOUKA = "この評価は正しいと思う"
# Kana-only, and stored in item.reading with item.kanji NULL: proves the fallback.
S_KANA = "ありがとうございます"


def _add_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kind: str = "sentence",
    kanji: str | None = None,
    reading: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO item(id, kind, kanji, reading, created_ts) VALUES (?, ?, ?, ?, ?)",
        (item_id, kind, kanji, reading, TS),
    )


def _seed(conn: sqlite3.Connection) -> None:
    """Three indexable sentences, one unindexable one, and a decoy word item."""
    _add_item(conn, "s-benkyou", kanji=S_BENKYOU)
    _add_item(conn, "s-hyouka", kanji=S_HYOUKA)
    _add_item(conn, "s-kana", reading=S_KANA)
    # No kanji and no reading: nothing to index, and sentence_text.jp is NOT NULL.
    _add_item(conn, "s-empty")
    # A word item whose surface is the search term. If this ever shows up in
    # sentence_text, the source query stopped filtering on kind.
    _add_item(conn, "w-benkyou", kind="word", kanji="勉強", reading="べんきょう")


INDEXED_IDS = ("s-benkyou", "s-hyouka", "s-kana")


@pytest.fixture
def conn(tmp_path):
    """A migrated database with versions stamped and sentences seeded."""
    connection = db.open_db(tmp_path / "katagiri.db")
    tok.stamp_versions(connection)
    _seed(connection)
    yield connection
    connection.close()


@pytest.fixture
def bare_conn(tmp_path):
    """A migrated database with **no** version stamp (and no sentences)."""
    connection = db.open_db(tmp_path / "bare.db")
    yield connection
    connection.close()


def _integrity_ok(conn: sqlite3.Connection, index: str) -> bool:
    """True when SQLite says ``index`` agrees with its content table, row for row.

    ``rank`` is what makes this worth asserting. Plain ``'integrity-check'``
    validates only the index's *internal* structure — measured against this SQLite
    (3.53), it passes an index that is entirely empty, one whose terms disagree
    with the content table, and one carrying entries for rows that do not exist.
    ``'integrity-check', 1`` is the variant that cross-checks index against
    content, so it fails on all three.
    """
    try:
        conn.execute(
            f"INSERT INTO {index}({index}, rank) VALUES ('integrity-check', 1)"
        )
    except sqlite3.DatabaseError:
        return False
    return True


def _content_dump(conn: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT rowid, item_id, jp, shadow_text, dict_version, tokenizer_version "
            "FROM sentence_text ORDER BY rowid"
        )
    ]


def _ids(hits: list[dict]) -> list[str]:
    return [hit["item_id"] for hit in hits]


# ---------------------------------------------------------------------------
# shadow_text
# ---------------------------------------------------------------------------


def test_shadow_text_segments_without_losing_characters(conn):
    shadow = fts_index.shadow_text(S_BENKYOU)
    parts = shadow.split(" ")

    assert len(parts) > 1, "a whole sentence collapsed into one token"
    # Segmentation only inserts spaces; it must not add, drop or reorder anything.
    assert "".join(parts) == S_BENKYOU
    # The word the routing test depends on has to be a standalone token, not a
    # substring of a longer one — that is what makes unicode61 able to match it.
    assert "勉強" in parts
    # UniDic's own segmentation is the contract, and it splits 日本語 into
    # 日本 + 語. The word index therefore indexes those two tokens, which is
    # precisely why a 日本語 query has to reach the trigram index instead (see
    # test_three_character_query_hits_the_trigram_index).
    assert "日本" in parts and "語" in parts


def test_shadow_text_of_blank_is_blank():
    assert fts_index.shadow_text("") == ""
    assert fts_index.shadow_text("   ") == ""


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------


def test_rebuild_populates_content_table_and_both_indexes(conn):
    result = fts_index.rebuild_index(conn)

    assert result.rows == 3
    assert result.skipped == 1, "the item with neither kanji nor reading"
    assert result.by_source == ((fts_index.SOURCE_SENTENCE_ITEMS, 3),)
    assert result.duration_s >= 0.0

    rows = _content_dump(conn)
    assert [row[1] for row in rows] == list(INDEXED_IDS)
    # Rowids are handed out from 1 in source order, so the FTS join key is
    # predictable rather than whatever the insert order happened to produce.
    assert [row[0] for row in rows] == [1, 2, 3]
    # item.kanji is the surface column; item.reading is the kana-only fallback.
    assert rows[0][2] == S_BENKYOU
    assert rows[2][2] == S_KANA

    # The real proof that the external-content sync pattern was followed: SQLite
    # itself confirms each index matches the content table row for row.
    assert _integrity_ok(conn, fts_index.WORD_INDEX)
    assert _integrity_ok(conn, fts_index.TRIGRAM_INDEX)


def test_rebuild_indexes_only_sentence_items(conn):
    fts_index.rebuild_index(conn)

    indexed = {row[1] for row in _content_dump(conn)}
    assert "w-benkyou" not in indexed, "a kind='word' item reached the sentence index"
    assert indexed == set(INDEXED_IDS)


def test_external_content_indexes_are_not_self_maintaining(conn):
    """Pins *why* rebuild_index writes the FTS rows by hand.

    The migration declares ``content='sentence_text'`` and creates no triggers, so
    a write to the content table alone leaves the indexes behind. If SQLite (or a
    later migration) ever started maintaining them, this test fails and the manual
    mirroring in :func:`rebuild_index` becomes double-indexing.
    """
    fts_index.rebuild_index(conn)
    conn.execute(
        "INSERT INTO sentence_text(rowid, item_id, jp, shadow_text) "
        "VALUES (99, 's-orphan', ?, ?)",
        (S_BENKYOU, fts_index.shadow_text(S_BENKYOU)),
    )

    # SQLite now reports the index as out of step with its content table...
    assert not _integrity_ok(conn, fts_index.WORD_INDEX)
    assert not _integrity_ok(conn, fts_index.TRIGRAM_INDEX)
    # ...and, the part that actually bites: the unindexed sentence is simply
    # invisible to search. No error, just a sentence that is in the database and
    # cannot be found.
    assert "s-orphan" not in _ids(fts_index.search_words(conn, "勉強"))
    assert "s-orphan" not in _ids(fts_index.search_trigram(conn, "日本語"))


def test_rebuild_is_idempotent(conn):
    first = fts_index.rebuild_index(conn)
    before = _content_dump(conn)

    second = fts_index.rebuild_index(conn)
    after = _content_dump(conn)

    assert second.rows == first.rows
    # Deterministic rowids mean an unchanged source rebuilds to identical bytes,
    # so "did anything change?" is answerable by comparison.
    assert after == before
    assert _integrity_ok(conn, fts_index.WORD_INDEX)
    assert _integrity_ok(conn, fts_index.TRIGRAM_INDEX)
    assert _ids(fts_index.search_words(conn, "勉強")) == ["s-benkyou"]


def test_rebuild_refuses_without_stamped_versions(bare_conn):
    with pytest.raises(fts_index.VersionsNotStampedError, match="stamp_versions"):
        fts_index.rebuild_index(bare_conn)

    # And it refused *before* destroying anything.
    remaining = bare_conn.execute("SELECT COUNT(*) FROM sentence_text").fetchone()[0]
    assert int(remaining) == 0


def test_rebuild_scope_selects_sources(conn):
    assert fts_index.resolve_scope(fts_index.SCOPE_ALL) == (
        fts_index.SOURCE_SENTENCE_ITEMS,
    )

    scoped = fts_index.rebuild_index(conn, scope=fts_index.SOURCE_SENTENCE_ITEMS)
    assert scoped.rows == 3

    with pytest.raises(fts_index.FtsIndexError, match="Unknown scope"):
        fts_index.rebuild_index(conn, scope="media_sub_lines")


def test_rebuild_failure_leaves_the_old_index_intact(conn, monkeypatch):
    fts_index.rebuild_index(conn)
    before = _content_dump(conn)
    assert before

    calls = {"n": 0}
    real = fts_index.shadow_text

    # Fail on the second row: the clear has already happened and one row is
    # already accumulated, which is exactly the window where a non-transactional
    # rebuild would leave the database with a partial index.

    def boom(text: str) -> str:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("tokenizer died mid-rebuild")
        return real(text)

    monkeypatch.setattr(fts_index, "shadow_text", boom)

    with pytest.raises(RuntimeError, match="died mid-rebuild"):
        fts_index.rebuild_index(conn)

    monkeypatch.undo()
    assert _content_dump(conn) == before
    assert _integrity_ok(conn, fts_index.WORD_INDEX)
    assert _integrity_ok(conn, fts_index.TRIGRAM_INDEX)
    assert _ids(fts_index.search_words(conn, "勉強")) == ["s-benkyou"]
    assert not conn.in_transaction, "the failed rebuild left a transaction open"


# ---------------------------------------------------------------------------
# Routing: the 勉強 case
# ---------------------------------------------------------------------------


def test_two_character_word_needs_the_words_index(conn):
    """勉強 is why there are two indexes and why search routes on length."""
    fts_index.rebuild_index(conn)

    assert len("勉強") < fts_index.TRIGRAM_MIN_CHARS
    assert _ids(fts_index.search_words(conn, "勉強")) == ["s-benkyou"]
    # The miss is the rationale, so it is asserted rather than assumed: trigram
    # indexes 3-character windows, has none for a 2-character query, and returns
    # nothing *silently* — no error to warn a caller who routed wrongly.
    assert fts_index.search_trigram(conn, "勉強") == []


def test_three_character_query_hits_the_trigram_index(conn):
    fts_index.rebuild_index(conn)

    assert _ids(fts_index.search_trigram(conn, "日本語")) == ["s-benkyou"]
    # Substring search is what trigram buys. Both of these cross a morph
    # boundary, so the word index — which matches whole UniDic tokens — cannot
    # see them: 日本語 was segmented as 日本 + 語, and 語を勉 spans three morphs.
    assert fts_index.search_words(conn, "日本語") == []
    assert _ids(fts_index.search_trigram(conn, "語を勉")) == ["s-benkyou"]
    assert fts_index.search_words(conn, "語を勉") == []


def test_kana_only_sentence_is_searchable(conn):
    fts_index.rebuild_index(conn)

    assert _ids(fts_index.search_trigram(conn, "ありがとう")) == ["s-kana"]


def test_search_limit_and_empty_query_are_validated(conn):
    fts_index.rebuild_index(conn)

    assert len(fts_index.search_trigram(conn, "い", limit=1)) <= 1
    with pytest.raises(ValueError, match="limit must be at least 1"):
        fts_index.search_words(conn, "勉強", limit=0)
    with pytest.raises(ValueError, match="non-empty"):
        fts_index.search_words(conn, "   ")


# ---------------------------------------------------------------------------
# Query sanitisation / FTS operator injection
# ---------------------------------------------------------------------------


def test_fts_operators_in_user_input_are_neutralized(conn):
    fts_index.rebuild_index(conn)

    # Baseline: the literal word is found.
    assert _ids(fts_index.search_words(conn, "評価")) == ["s-hyouka"]

    # `評価 OR x` must not be read as a disjunction. Treated literally it is a
    # two-token phrase that appears in no sentence, so the honest answer is zero
    # hits — not "everything containing 評価".
    assert fts_index.search_words(conn, "評価 OR x") == []
    assert fts_index.search_words(conn, "評価 NOT 正しい") == []


def test_sanitize_query_strips_syntax_and_bare_operators():
    assert fts_index.sanitize_query('"評価" OR x*') == "評価 x"
    assert fts_index.sanitize_query("勉強^ AND (日本語)") == "勉強 日本語"
    # Lowercase `or` is not an FTS5 operator, so it is ordinary text and is kept.
    assert fts_index.sanitize_query("評価 or x") == "評価 or x"
    assert fts_index.sanitize_query('"*"') == ""


def test_syntax_characters_do_not_reach_sqlite_as_syntax(conn):
    fts_index.rebuild_index(conn)

    # A stray quote would be an FTS5 syntax error and a stray `*` a prefix
    # search; both are stripped, so these behave like the plain query.
    assert _ids(fts_index.search_trigram(conn, '日本語"')) == ["s-benkyou"]
    assert _ids(fts_index.search_trigram(conn, "日本語*")) == ["s-benkyou"]
    with pytest.raises(ValueError, match="non-empty"):
        fts_index.search_trigram(conn, '"^*:()"')


# ---------------------------------------------------------------------------
# Version stamping and staleness
# ---------------------------------------------------------------------------


def test_rows_are_stamped_with_the_current_versions(conn):
    result = fts_index.rebuild_index(conn)
    current = fts_index.current_versions(conn)

    assert result.dict_version == current[fts_index.DICT_VERSION_KEY]
    assert result.tokenizer_version == current[fts_index.TOKENIZER_VERSION_KEY]
    assert result.dict_version == tok.DICT_VERSION
    for row in _content_dump(conn):
        assert row[4] == result.dict_version
        assert row[5] == result.tokenizer_version

    fresh = fts_index.index_staleness(conn)
    assert fresh.total_rows == 3
    assert fresh.stale_rows == 0
    assert fresh.stale is False
    assert len(fresh.row_versions) == 1


def test_tampered_metadata_makes_every_row_stale(conn):
    fts_index.rebuild_index(conn)

    # Stand-in for a dictionary upgrade: metadata moves on, the rows do not.
    conn.execute(
        "UPDATE metadata SET value = ?, updated_ts = ? WHERE key = ?",
        ("9.9.9", TS, fts_index.DICT_VERSION_KEY),
    )

    stale = fts_index.index_staleness(conn)
    assert stale.stale is True
    assert stale.stale_rows == 3
    assert stale.current_versions[fts_index.DICT_VERSION_KEY] == "9.9.9"
    assert [group.dict_version for group in stale.row_versions] == [tok.DICT_VERSION]
    assert stale.row_versions[0].stale is True
    assert "STALE" in stale.render()

    # Rebuilding under the new versions clears the staleness.
    fts_index.rebuild_index(conn)
    assert fts_index.index_staleness(conn).stale_rows == 0


def test_tokenizer_version_change_alone_is_stale(conn):
    fts_index.rebuild_index(conn)
    conn.execute(
        "UPDATE metadata SET value = ?, updated_ts = ? WHERE key = ?",
        ("fugashi 0.0.0", TS, fts_index.TOKENIZER_VERSION_KEY),
    )

    assert fts_index.index_staleness(conn).stale_rows == 3


def test_rows_written_without_a_stamp_are_stale(conn):
    """A NULL stamp counts as stale; `<> NULL` would have quietly said no."""
    fts_index.rebuild_index(conn)
    conn.execute(
        "INSERT INTO sentence_text(rowid, item_id, jp, shadow_text) "
        "VALUES (98, 's-unstamped', ?, ?)",
        (S_HYOUKA, fts_index.shadow_text(S_HYOUKA)),
    )

    stale = fts_index.index_staleness(conn)
    assert stale.total_rows == 4
    assert stale.stale_rows == 1
    assert any(group.dict_version is None for group in stale.row_versions)


def test_staleness_requires_stamped_versions(bare_conn):
    with pytest.raises(fts_index.VersionsNotStampedError, match="stamp_versions"):
        fts_index.index_staleness(bare_conn)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """A seeded database that ``main`` will open, without touching the config."""
    path = tmp_path / "cli.db"
    setup = db.open_db(path)
    tok.stamp_versions(setup)
    _seed(setup)
    setup.close()

    real_open_db = db.open_db
    monkeypatch.setattr(db, "open_db", lambda *args, **kwargs: real_open_db(path))
    return path


def test_cli_rebuild_then_status(cli_db, capsys):
    assert fts_index.main(["rebuild"]) == 0
    rebuild_output = capsys.readouterr().err
    assert "rows      : 3" in rebuild_output
    assert "skipped   : 1" in rebuild_output

    # status exits 0 while the index matches the current versions...
    assert fts_index.main(["status"]) == 0
    assert "stale     : 0" in capsys.readouterr().err

    conn = db.open_db(cli_db)
    conn.execute(
        "UPDATE metadata SET value = ?, updated_ts = ? WHERE key = ?",
        ("9.9.9", TS, fts_index.DICT_VERSION_KEY),
    )
    conn.close()

    # ...and non-zero once it does not, so a shell can gate a rebuild on it.
    assert fts_index.main(["status"]) == 1
    assert "stale     : 3" in capsys.readouterr().err


def test_cli_reports_missing_versions_plainly(tmp_path, monkeypatch, capsys):
    path = tmp_path / "unstamped.db"
    db.open_db(path).close()
    real_open_db = db.open_db
    monkeypatch.setattr(db, "open_db", lambda *args, **kwargs: real_open_db(path))

    assert fts_index.main(["rebuild"]) == 2
    assert "stamp_versions" in capsys.readouterr().err
