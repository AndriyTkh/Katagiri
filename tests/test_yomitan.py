"""Yomitan export tests: zip validity, who gets coloured, and drift gating.

The fixtures seed the real ``known_set`` inputs — ``item`` rows, ``anki_cards`` /
``anki_item_map`` mirror rows, and ``manual_marks`` — rather than stubbing the
view, because the questions under test are all questions about the view's own
semantics: does an Anki-mature card colour, does a manual ``unknown`` override it,
does a ``suspect`` flag stop it, and does a mark on an id that has no item row
survive as a *counted* skip instead of vanishing.

The zip is opened and parsed rather than compared to a golden file. What matters
is that Yomitan can read it — a byte comparison would fail on every harmless
formatting change and still tell us nothing about validity.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile

import pytest

from katagiri import config as config_mod
from katagiri import db
from katagiri import yomitan_export
from katagiri.events import append_event, mark_item, recent_events
from katagiri.yomitan_export import (
    DICT_FORMAT,
    DICT_TITLE,
    DRIFT_THRESHOLD,
    INDEX_MEMBER,
    KNOWN_TAG,
    REGEN_EVENT_TYPE,
    SKIP_EVENT_TYPE,
    TAG_BANK_MEMBER,
    TERM_BANK_MEMBER,
    check_drift,
    generate_dict,
    known_terms,
    main,
    maybe_regen,
    plausible_surface,
    reimport_checklist,
)

CREATED_TS = "2026-01-01T00:00:00Z"
MARK_TS = "2026-01-02T00:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Point %LOCALAPPDATA% at a tmp dir so config, db and scratch are isolated."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def conn(local_app_data):
    connection = db.open_db()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def out_dir(tmp_path):
    return tmp_path / "dicts"


def add_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kanji: str | None = None,
    reading: str | None = None,
    kind: str = "word",
) -> str:
    conn.execute(
        """
        INSERT INTO item (id, kind, kanji, reading, created_ts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (item_id, kind, kanji, reading, CREATED_TS),
    )
    return item_id


def add_mark(
    conn: sqlite3.Connection, item_id: str, mark: str, ts: str = MARK_TS
) -> None:
    """Write a mark row directly — bulk seeding, no event log involved."""
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?, ?, ?)",
        (item_id, mark, ts),
    )


def add_anki_known(
    conn: sqlite3.Connection, item_id: str, *, note_id: int, ivl: int = 30
) -> None:
    """Mirror a mature card onto ``item_id`` (the view's ``ivl >= 21`` rule)."""
    conn.execute(
        "INSERT INTO anki_cards (card_id, note_id, ivl) VALUES (?, ?, ?)",
        (note_id * 10, note_id, ivl),
    )
    conn.execute(
        "INSERT INTO anki_item_map (note_id, item_id, method) VALUES (?, ?, ?)",
        (note_id, item_id, "test"),
    )


def read_zip(path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        payloads = {
            name: json.loads(archive.read(name).decode("utf-8")) for name in names
        }
    return {"names": names, **payloads}


def expressions(terms) -> set[str]:
    return {term.expression for term in terms}


@pytest.fixture
def seeded(conn):
    """A small known set exercising every path into (and out of) the export.

    Known words: 食べる (Anki-mature), 犬 (manual known), ねこ (kana only, manual).
    Excluded: 走る (mirror mature but manually marked unknown), 難しい (suspect),
    未来 (no mark, no mature card), 水 (kanji item), 〜ている (grammar item),
    and the mark-only id ``w-orphan``.
    """
    add_item(conn, "w-taberu", kanji="食べる", reading="たべる")
    add_anki_known(conn, "w-taberu", note_id=1)

    add_item(conn, "w-inu", kanji="犬", reading="いぬ")
    add_mark(conn, "w-inu", "known")

    add_item(conn, "w-neko", kanji=None, reading="ねこ")
    add_mark(conn, "w-neko", "known")

    # Mature in Anki, but the learner says otherwise: the manual mark wins.
    add_item(conn, "w-hashiru", kanji="走る", reading="はしる")
    add_anki_known(conn, "w-hashiru", note_id=2)
    add_mark(conn, "w-hashiru", "unknown")

    # Mature in Anki and flagged suspect: known, but not trustworthy enough to
    # colour. See the module docstring of yomitan_export.
    add_item(conn, "w-muzukashii", kanji="難しい", reading="むずかしい")
    add_anki_known(conn, "w-muzukashii", note_id=3)
    add_mark(conn, "w-muzukashii", "suspect")

    add_item(conn, "w-mirai", kanji="未来", reading="みらい")

    add_item(conn, "k-mizu", kanji="水", reading="みず", kind="kanji")
    add_mark(conn, "k-mizu", "known")

    add_item(conn, "g-teiru", kanji="〜ている", reading="ている", kind="grammar")
    add_mark(conn, "g-teiru", "known")

    # A mark on an id that was never imported: no item row, so no surface.
    add_mark(conn, "w-orphan", "known")
    return conn


# ---------------------------------------------------------------------------
# Surface plausibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "surface",
    ["食べる", "たべる", "ネコ", "日本語", "Tシャツ"],
)
def test_plausible_surface_accepts_words(surface):
    assert plausible_surface(surface) is True


@pytest.mark.parametrize(
    "surface",
    [
        None,
        "",
        "   ",
        "hello",
        "w-taberu",
        "〜ている",  # slot marker: never appears verbatim in running text
        "~ている",
        "食べ る",  # whitespace: not one page token
        "あ" * 30,  # a sentence that landed in a word field
    ],
)
def test_plausible_surface_rejects_non_words(surface):
    assert plausible_surface(surface) is False


# ---------------------------------------------------------------------------
# known_terms
# ---------------------------------------------------------------------------


def test_known_terms_includes_only_trustworthy_known_words(seeded):
    result = known_terms(seeded)

    assert expressions(result.terms) == {"食べる", "犬", "ねこ"}


def test_known_terms_excludes_manual_unknown_over_mature_card(seeded):
    assert "走る" not in expressions(known_terms(seeded).terms)


def test_known_terms_excludes_suspect(seeded):
    """Documented decision: a suspected word must not colour as known.

    The item is genuinely ``is_known = 1`` in the view — the mature Anki card
    still decides — so this is the export filtering it out, not the view.
    """
    row = seeded.execute(
        "SELECT is_known, suspect FROM known_set WHERE item_id = 'w-muzukashii'"
    ).fetchone()
    assert (row["is_known"], row["suspect"]) == (1, 1)

    assert "難しい" not in expressions(known_terms(seeded).terms)


def test_known_terms_excludes_non_word_kinds(seeded):
    surfaces = expressions(known_terms(seeded).terms)
    assert "水" not in surfaces
    assert "〜ている" not in surfaces


def test_known_terms_counts_mark_only_ids_instead_of_dropping_them(seeded):
    result = known_terms(seeded)

    assert result.skipped_no_item == 1
    assert result.skip_counts()["skipped_no_item"] == 1


def test_known_terms_reading_is_blank_when_it_repeats_the_expression(seeded):
    by_expression = {term.expression: term.reading for term in known_terms(seeded).terms}

    assert by_expression["食べる"] == "たべる"
    # Kana-only headword: Yomitan's convention is an empty reading, not the same
    # kana twice.
    assert by_expression["ねこ"] == ""


def test_known_terms_collapses_duplicate_surfaces(conn):
    add_item(conn, "w-a", kanji="犬", reading="いぬ")
    add_item(conn, "w-b", kanji="犬", reading="いぬ")
    add_mark(conn, "w-a", "known")
    add_mark(conn, "w-b", "known")

    result = known_terms(conn)

    assert result.count == 1
    assert result.duplicates == 1


def test_known_terms_uses_the_marking_api_end_to_end(conn):
    """The real write path (mark_item) lands in the export, not just raw SQL."""
    add_item(conn, "w-yama", kanji="山", reading="やま")
    mark_item(conn, "w-yama", "known")

    assert expressions(known_terms(conn).terms) == {"山"}


def test_known_terms_empty_known_set_is_not_an_error(conn):
    result = known_terms(conn)
    assert result.count == 0
    assert result.terms == ()


# ---------------------------------------------------------------------------
# generate_dict / zip structure
# ---------------------------------------------------------------------------


def test_generate_dict_writes_a_valid_yomitan_zip(seeded, out_dir):
    result = generate_dict(seeded, out_dir)

    assert result.path.parent == out_dir
    assert result.path.suffix == ".zip"
    assert result.terms == 3
    assert result.revision.endswith("-3")
    assert zipfile.is_zipfile(result.path)

    content = read_zip(result.path)
    assert content["names"] == {INDEX_MEMBER, TERM_BANK_MEMBER, TAG_BANK_MEMBER}

    index = content[INDEX_MEMBER]
    assert index["title"] == DICT_TITLE
    assert index["format"] == DICT_FORMAT
    assert index["revision"] == result.revision

    tag_bank = content[TAG_BANK_MEMBER]
    assert tag_bank[0][0] == KNOWN_TAG
    assert len(tag_bank[0]) == 5

    bank = content[TERM_BANK_MEMBER]
    assert len(bank) == 3
    for entry in bank:
        assert isinstance(entry, list)
        assert len(entry) == 8
        expression, reading, definition_tags, rules, score, definitions, seq, term_tags = entry
        assert isinstance(expression, str) and expression
        assert isinstance(reading, str)
        assert definition_tags == KNOWN_TAG
        assert term_tags == KNOWN_TAG
        assert rules == ""
        assert score == 0
        assert definitions == [KNOWN_TAG]
        assert seq == 0

    assert {entry[0] for entry in bank} == {"食べる", "犬", "ねこ"}


def test_generate_dict_term_bank_is_sorted_and_reproducible(seeded, out_dir):
    first = generate_dict(seeded, out_dir)
    first_bytes = first.path.read_bytes()

    second = generate_dict(seeded, out_dir)

    assert second.path == first.path
    assert second.path.read_bytes() == first_bytes

    bank = read_zip(first.path)[TERM_BANK_MEMBER]
    assert bank == sorted(bank, key=lambda entry: (entry[0], entry[1]))


def test_generate_dict_defaults_to_the_configured_scratch_directory(seeded):
    result = generate_dict(seeded)

    expected = config_mod.get_config().scratch_root / yomitan_export.OUTPUT_SUBDIR
    assert result.path.parent == expected
    assert result.path.is_file()


def test_generate_dict_leaves_no_partial_file_behind(seeded, out_dir):
    result = generate_dict(seeded, out_dir)
    assert list(out_dir.glob("*.part")) == []
    assert [path.name for path in out_dir.iterdir()] == [result.path.name]


def test_generate_dict_logs_the_regen_with_counts_and_basename_only(seeded, out_dir):
    result = generate_dict(seeded, out_dir)

    events = recent_events(seeded, type=REGEN_EVENT_TYPE)
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])

    assert payload["terms"] == result.terms == 3
    assert payload["revision"] == result.revision
    assert payload["path"] == result.path.name
    # The event log is durable and backed up; a machine-local absolute path has
    # no business being permanent in it.
    assert "/" not in payload["path"] and "\\" not in payload["path"]
    assert payload["skipped_no_item"] == 1


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def seed_prior_regen(conn: sqlite3.Connection, terms: int) -> None:
    append_event(
        conn,
        type=REGEN_EVENT_TYPE,
        session_id=yomitan_export.SESSION_ID,
        payload={"terms": terms, "revision": f"2026-01-01-{terms}", "path": "old.zip"},
    )


def test_check_drift_first_run_has_nothing_to_compare_against(seeded):
    drift = check_drift(seeded)

    assert drift.first_run is True
    assert drift.last_regen_terms is None
    assert drift.delta == 3
    assert drift.threshold == DRIFT_THRESHOLD
    assert drift.should_regen is True


def test_check_drift_small_change_is_not_worth_a_reimport(seeded):
    seed_prior_regen(seeded, 3)

    drift = check_drift(seeded)

    assert drift.last_regen_terms == 3
    assert drift.delta == 0
    assert drift.should_regen is False


def test_check_drift_at_the_threshold_still_skips(seeded):
    # Exactly 150 terms of drift: the rule is "more than", not "at least".
    seed_prior_regen(seeded, 3 + DRIFT_THRESHOLD)

    drift = check_drift(seeded)

    assert drift.delta == -DRIFT_THRESHOLD
    assert drift.should_regen is False


def test_check_drift_one_past_the_threshold_regenerates(seeded):
    seed_prior_regen(seeded, 3 + DRIFT_THRESHOLD + 1)

    drift = check_drift(seeded)

    assert drift.delta == -(DRIFT_THRESHOLD + 1)
    # Signed, but the trigger is magnitude: a known set that shrank leaves the
    # overlay lying green, which is the worse direction to be stale in.
    assert drift.should_regen is True


def test_check_drift_uses_the_most_recent_regen_event(seeded):
    seed_prior_regen(seeded, 1000)
    seed_prior_regen(seeded, 3)

    assert check_drift(seeded).delta == 0


def test_check_drift_growth_past_the_threshold_regenerates(conn):
    seed_prior_regen(conn, 0)
    for index in range(DRIFT_THRESHOLD + 1):
        item_id = f"w-bulk-{index:04d}"
        add_item(conn, item_id, kanji=None, reading=f"ばるく{index:04d}")
        add_mark(conn, item_id, "known")

    drift = check_drift(conn)

    assert drift.last_regen_terms == 0
    assert drift.delta == DRIFT_THRESHOLD + 1
    assert drift.should_regen is True


def test_check_drift_rejects_a_negative_threshold(conn):
    with pytest.raises(ValueError, match="threshold"):
        check_drift(conn, -1)


# ---------------------------------------------------------------------------
# maybe_regen
# ---------------------------------------------------------------------------


def test_maybe_regen_first_run_writes_the_dictionary(seeded, out_dir):
    outcome = maybe_regen(seeded, out_dir)

    assert outcome.regenerated is True
    assert outcome.generated is not None
    assert outcome.generated.path.is_file()
    assert outcome.drift.first_run is True

    assert len(recent_events(seeded, type=REGEN_EVENT_TYPE)) == 1
    assert recent_events(seeded, type=SKIP_EVENT_TYPE) == []


def test_maybe_regen_skip_is_logged_with_the_numbers_behind_it(seeded, out_dir):
    seed_prior_regen(seeded, 3)

    outcome = maybe_regen(seeded, out_dir)

    assert outcome.regenerated is False
    assert outcome.generated is None
    assert not out_dir.exists() or list(out_dir.glob("*.zip")) == []

    skips = recent_events(seeded, type=SKIP_EVENT_TYPE)
    assert len(skips) == 1
    payload = json.loads(skips[0]["payload"])
    assert payload == {"delta": 0, "threshold": DRIFT_THRESHOLD}
    assert skips[0]["id"] == outcome.event_id
    # A skip must not be mistaken for a regen by the next drift check.
    assert len(recent_events(seeded, type=REGEN_EVENT_TYPE)) == 1


def test_maybe_regen_second_run_after_a_real_regen_skips(seeded, out_dir):
    first = maybe_regen(seeded, out_dir)
    second = maybe_regen(seeded, out_dir)

    assert first.regenerated is True
    assert second.regenerated is False
    assert second.drift.last_regen_terms == first.generated.terms


def test_maybe_regen_large_drift_writes_again(seeded, out_dir):
    seed_prior_regen(seeded, 3 + DRIFT_THRESHOLD + 1)

    outcome = maybe_regen(seeded, out_dir)

    assert outcome.regenerated is True
    assert outcome.generated.terms == 3
    assert recent_events(seeded, type=SKIP_EVENT_TYPE) == []


def test_maybe_regen_honours_an_explicit_threshold(seeded, out_dir):
    seed_prior_regen(seeded, 5)

    outcome = maybe_regen(seeded, out_dir, threshold=1)

    assert outcome.drift.delta == -2
    assert outcome.regenerated is True


# ---------------------------------------------------------------------------
# Checklist and CLI
# ---------------------------------------------------------------------------


def test_reimport_checklist_is_numbered_and_covers_the_manual_steps():
    steps = reimport_checklist()

    assert len(steps) == 6
    for number, step in enumerate(steps, start=1):
        assert step.startswith(f"{number}. ")

    joined = " ".join(steps).lower()
    for expected in ("dictionaries", "remove", "import", "count", "hover"):
        assert expected in joined


def test_cli_gen_writes_a_dictionary_and_puts_the_checklist_on_stderr(
    seeded, out_dir, capsys
):
    code = main(["gen", "--db", str(db.database_path()), "--out", str(out_dir)])
    captured = capsys.readouterr()

    assert code == 0
    assert list(out_dir.glob("*.zip"))
    # stdout carries the machine-readable result; the human checklist goes to
    # stderr, because stdout is the JSON-RPC wire everywhere else in Katagiri.
    assert "dictionary :" in captured.out
    assert "terms      : 3" in captured.out
    assert "Re-import in Yomitan" in captured.err
    assert "1. Open Yomitan" in captured.err
    assert "Re-import in Yomitan" not in captured.out


def test_cli_check_reports_drift_without_writing(seeded, out_dir, capsys):
    code = main(["check", "--db", str(db.database_path())])
    captured = capsys.readouterr()

    assert code == 0
    assert "regen due  : yes" in captured.out
    assert not out_dir.exists()
    assert recent_events(seeded, type=REGEN_EVENT_TYPE) == []


def test_cli_auto_skips_quietly_when_the_dictionary_is_current(
    seeded, out_dir, capsys
):
    seed_prior_regen(seeded, 3)

    code = main(["auto", "--db", str(db.database_path()), "--out", str(out_dir)])
    captured = capsys.readouterr()

    assert code == 0
    assert "up to date" in captured.out
    assert "Re-import in Yomitan" not in captured.err
    assert not out_dir.exists() or list(out_dir.glob("*.zip")) == []
