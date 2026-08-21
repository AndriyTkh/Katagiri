"""D2 intelligence tests: coverage, the curriculum DAG, i+1 selection, difficulty.

What is actually being defended here
------------------------------------
The headline is D-28: **vocabulary coverage may never answer the grammar
question**. So the load-bearing test in this file is
:func:`test_full_coverage_with_unreachable_grammar_is_still_gated` — a candidate
at 100% vocabulary coverage whose grammar sits behind an unmastered prerequisite
must come back gated, named ``unreachable_grammar``, and it must stay gated even
with ``require_grammar=False`` and every other gate opened wide. Everything else
in the module can be right and that one being wrong would make the feature a
sentence-difficulty toy that occasionally serves walls.

Around it: the coverage basis (what is in the denominator and what is not), the
``KnownLookup`` cache (a per-candidate query count is what makes batch selection
unusable), curriculum parsing and its idempotent, COALESCE-preserving import,
reachability over ``prereq`` edges only, the debt fold and its cache
double-counting rule, and difficulty's graceful degradation when a vendored
dataset is absent or its digest is wrong.

Fixtures follow ``tests/test_exercises.py``: ``LOCALAPPDATA`` is redirected to a
tmp directory so the real ``open_db``/config path is exercised, and rows are
seeded with direct INSERTs so timestamps are exactly what a test says they are.
Morph-level tests build :class:`Morph` values directly — the point there is the
counting rule, not UniDic — while the selection and difficulty tests run the real
vendored tokenizer, because that is the thing they are claims about.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import intelligence
from katagiri.db import open_db
from katagiri.intelligence import (
    BAD_GATE,
    BAD_LIMIT,
    BAD_TOP_UNKNOWN,
    BAD_WEIGHTS,
    COMPONENT_COVERAGE,
    COMPONENT_FREQUENCY,
    COMPONENT_JLPT,
    COMPONENT_READABILITY,
    CURRICULUM_CYCLE,
    CURRICULUM_EMPTY,
    CURRICULUM_UNAVAILABLE,
    DATASET_CHECKSUM,
    DATASET_MISSING,
    DEBT_FROM_CACHE,
    DEBT_FROM_CACHE_TAIL,
    DEBT_FROM_NOTHING,
    DEBT_FROM_OBSERVATIONS,
    DEBT_HALF_LIFE_DAYS,
    DEFAULT_MIN_UNDERSTANDING,
    DIFFICULTY_WEIGHTS,
    EDGE_PREREQ,
    EDGE_UNLOCK,
    EMPTY_TEXT,
    GATE_COVERAGE_TOO_LOW,
    GATE_GRAMMAR_UNKNOWN,
    GATE_NOT_AUDIO_ANCHORED,
    GATE_SEALED,
    GATE_TOO_MANY_UNKNOWN,
    GATE_TOO_MUCH_NEW_GRAMMAR,
    GATE_UNREACHABLE_GRAMMAR,
    GRAMMAR_DAG_CYCLE,
    GRAMMAR_FROM_EDGES,
    GRAMMAR_FROM_EXPLICIT,
    GRAMMAR_FROM_HOME_TOPIC,
    GRAMMAR_FROM_NOTHING,
    KEY_LEMMA,
    KEY_READING,
    KEY_SURFACE,
    MASTERY_KNOWN_SET,
    MASTERY_UNDERSTANDING,
    MAX_CANDIDATES,
    MAX_COVERAGE_CHARS,
    MAX_TOP_UNKNOWN,
    NO_CANDIDATES,
    NO_COMPONENTS,
    RANKED_BY_DEBT,
    SOURCE_DIAGRAM,
    SOURCE_NODE_BLOCK,
    STATE_AMBIGUOUS,
    STATE_UNKNOWN,
    STATE_UNSEEN,
    TEXT_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    Candidate,
    DatasetStatus,
    FrequencyList,
    GrammarDag,
    JlptLevels,
    KnownLookup,
    MasteryLookup,
    ReadabilityModel,
    as_candidate,
    candidates_from_items,
    combine_difficulty,
    comprehension_debt,
    coverage,
    coverage_band,
    coverage_from_morphs,
    difficulty_for_me,
    find_cycle,
    find_i_plus_one,
    grammar_reachability,
    import_curriculum,
    is_content_morph,
    is_function_morph,
    is_ignored,
    load_grammar_dag,
    parse_curriculum,
    type_key,
)
from katagiri.jmdict_import import ChecksumError
from katagiri.tokenizer import Morph

TS = "2026-08-01T00:00:00Z"
NOW = "2026-08-20T00:00:00Z"
NOW_DT = datetime(2026, 8, 20, tzinfo=timezone.utc)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CURRICULUM = REPO_ROOT / "docs" / "katagiri" / "katagiri" / "10-course" / "curriculum.md"


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A migrated database reached through the real config path, as in A6's tests."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    conn = open_db()
    try:
        yield conn
    finally:
        conn.close()
        config_mod.reset_config_cache()


def morph(
    surface: str,
    lemma: str | None = None,
    *,
    pos1: str | None = "名詞",
    pos2: str | None = None,
    reading: str | None = None,
) -> Morph:
    """One synthetic morph. Only the fields the coverage basis reads matter."""
    return Morph(
        surface=surface,
        lemma=surface if lemma is None else lemma,
        lemma_reading=reading,
        pos1=pos1,
        pos2=pos2,
        pos3=None,
        pos4=None,
        infl_type=None,
        infl_form=None,
        is_unknown=False,
    )


def seed_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kind: str = "word",
    kanji: str | None = None,
    reading: str | None = None,
    level: str | None = None,
    understanding: int | None = None,
    sealed: int = 0,
    home_topic: str | None = None,
    ts: str = TS,
) -> None:
    conn.execute(
        """
        INSERT INTO item (id, kind, home_topic, kanji, reading, level,
                          understanding, sealed, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item_id, kind, home_topic, kanji, reading, level, understanding, sealed, ts),
    )


def mark(
    conn: sqlite3.Connection, item_id: str, value: str = "known", ts: str = TS
) -> None:
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?, ?, ?)",
        (item_id, value, ts),
    )


def seed_word(
    conn: sqlite3.Connection,
    item_id: str,
    kanji: str,
    *,
    reading: str | None = None,
    known: bool = False,
) -> None:
    seed_item(conn, item_id, kind="word", kanji=kanji, reading=reading)
    if known:
        mark(conn, item_id)


def seed_edge(
    conn: sqlite3.Connection, from_id: str, to_id: str, edge_type: str = EDGE_PREREQ
) -> None:
    conn.execute(
        "INSERT INTO item_edge (from_id, to_id, edge_type) VALUES (?, ?, ?)",
        (from_id, to_id, edge_type),
    )


def seed_sentence(
    conn: sqlite3.Connection,
    item_id: str,
    jp: str,
    *,
    sealed: int = 0,
    home_topic: str | None = None,
) -> None:
    seed_item(
        conn, item_id, kind="sentence", sealed=sealed, home_topic=home_topic
    )
    conn.execute(
        "INSERT INTO sentence_text (item_id, jp) VALUES (?, ?)", (item_id, jp)
    )


_OBS_COUNTER = {"n": 0}


def seed_observation(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    ts: str,
    unassisted: int = 0,
    band: str = ">=95",
    expected: str | None = None,
    produced: str | None = None,
) -> str:
    _OBS_COUNTER["n"] += 1
    obs_id = f"obs-{_OBS_COUNTER['n']:05d}"
    conn.execute(
        """
        INSERT INTO observation (id, ts, session_id, item_id, task_type, expected,
                                 produced, unassisted, coverage_band, rubric_version)
        VALUES (?, ?, 'sess-1', ?, 'read_to_meaning', ?, ?, ?, ?, 'v1')
        """,
        (obs_id, ts, item_id, expected, produced, unassisted, band),
    )
    return obs_id


def seed_stat_cache(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    debt: float,
    computed_ts: str,
    strength: float | None = None,
    review_count: int | None = None,
    last_event_ts: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO item_stat_cache (item_id, strength, comprehension_debt,
                                     review_count, last_event_ts, computed_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, strength, debt, review_count, last_event_ts, computed_ts),
    )


def days_ago(days: float) -> str:
    return (NOW_DT - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot(conn: sqlite3.Connection) -> tuple[list[Any], list[Any]]:
    items = [
        tuple(row)
        for row in conn.execute(
            "SELECT id, kind, level, understanding, created_ts FROM item ORDER BY id"
        )
    ]
    edges = [
        tuple(row)
        for row in conn.execute(
            "SELECT from_id, to_id, edge_type FROM item_edge ORDER BY 1, 2, 3"
        )
    ]
    return items, edges


def unavailable_datasets() -> dict[str, Any]:
    """Stub loaders for the three vendored datasets, all reporting absent.

    Lets a difficulty test say "no dataset answered" without touching (or
    invalidating) the real vendored copies and their 185k-row parse.
    """
    return {
        "model": ReadabilityModel(
            coefficients={},
            intercept=0.0,
            status=DatasetStatus(
                name="jreadability", available=False, error=DATASET_MISSING,
                note="stub",
            ),
        ),
        "frequency": FrequencyList(
            entries=(),
            by_lemma_pos={},
            by_lemma={},
            status=DatasetStatus(
                name="bccwj", available=False, error=DATASET_MISSING, note="stub"
            ),
        ),
        "levels": JlptLevels(
            by_word={},
            counts={},
            status=DatasetStatus(
                name="jlpt", available=False, error=DATASET_MISSING, note="stub"
            ),
        ),
    }


# ===========================================================================
# 1. The coverage basis
# ===========================================================================


def test_ignored_function_and_content_classification():
    assert is_ignored(morph("。", pos1="補助記号"))
    assert is_ignored(morph("　", pos1="空白"))
    assert is_ignored(morph(" ", pos1="名詞"))          # blank surface, whatever the POS
    assert is_function_morph(morph("は", pos1="助詞"))
    assert is_function_morph(morph("です", pos1="助動詞"))
    assert not is_content_morph(morph("は", pos1="助詞"))
    assert not is_content_morph(morph("。", pos1="補助記号"))
    # Bound affixes stay in the denominator: 食べ方 is a word the learner needs.
    assert is_content_morph(morph("お", pos1="接頭辞"))
    assert is_content_morph(morph("方", pos1="接尾辞"))


def test_type_key_separates_same_lemma_by_pos():
    assert type_key(morph("明日", pos1="名詞")) != type_key(morph("明日", pos1="副詞"))


def test_coverage_counts_known_unknown_and_unseen(db):
    seed_word(db, "w-neko", "猫", known=True)
    seed_word(db, "w-inu", "犬")                       # item row, not known
    result = coverage_from_morphs(
        db,
        [morph("猫"), morph("犬"), morph("鳥"), morph("。", pos1="補助記号")],
    )
    assert result["ok"] is True
    assert result["counts"]["counted_tokens"] == 3
    assert result["counts"]["known_tokens"] == 1
    assert result["counts"]["ignored_morphs"] == 1
    assert result["counts"]["by_state"] == {STATE_UNKNOWN: 1, STATE_UNSEEN: 1}
    states = {entry["lemma"]: entry["state"] for entry in result["unknown"]}
    assert states == {"犬": STATE_UNKNOWN, "鳥": STATE_UNSEEN}
    assert result["known_pct"] == pytest.approx(33.33, abs=0.01)


def test_ambiguous_surface_counts_as_not_known(db):
    """Two items spelled 明日: a verdict would be a guess, so it is its own state."""
    seed_item(db, "w-ashita", kind="word", kanji="明日", reading="あした")
    seed_item(db, "w-asu", kind="word", kanji="明日", reading="あす")
    mark(db, "w-ashita")
    result = coverage_from_morphs(db, [morph("明日")])
    assert result["known_pct"] == 0.0
    assert result["counts"]["by_state"] == {STATE_AMBIGUOUS: 1}
    assert result["unknown"][0]["state"] == STATE_AMBIGUOUS
    assert result["unknown"][0]["item_id"] is None


def test_function_morphs_are_counted_but_kept_out_of_the_ratio(db):
    seed_word(db, "w-neko", "猫", known=True)
    result = coverage_from_morphs(
        db,
        [
            morph("猫"),
            morph("は", pos1="助詞"),
            morph("です", pos1="助動詞"),
            morph("。", pos1="補助記号"),
        ],
    )
    assert result["counts"]["function_tokens"] == 2
    assert result["counts"]["counted_tokens"] == 1
    assert result["known_pct"] == 100.0          # not 25%, not 50%
    assert result["band"] == ">=95"


def test_zero_content_tokens_gives_no_percentage(db):
    result = coverage_from_morphs(
        db, [morph("は", pos1="助詞"), morph("。", pos1="補助記号")]
    )
    assert result["ok"] is True
    assert result["known_pct"] is None
    assert result["known_ratio"] is None
    assert result["band"] is None
    assert "no countable content token" in result["note"]


@pytest.mark.parametrize(
    ("pct", "band"),
    [
        (100.0, ">=95"),
        (95.0, ">=95"),
        (94.996, "80-95"),        # unrounded: 94.996 rounds to 95.0 but is not >=95
        (94.99, "80-95"),
        (80.0, "80-95"),
        (79.99, "<80"),
        (0.0, "<80"),
        (None, None),
    ],
)
def test_coverage_band_thresholds(pct, band):
    assert coverage_band(pct) == band


def test_band_comes_from_the_unrounded_ratio(db):
    """19 of 20 is exactly 95%; the band must not be decided on a rounded copy."""
    seed_word(db, "w-neko", "猫", known=True)
    morphs = [morph("猫") for _ in range(19)] + [morph("鳥")]
    result = coverage_from_morphs(db, morphs)
    assert result["known_pct"] == 95.0
    assert result["band"] == ">=95"
    assert coverage_band(94.996) == "80-95"


def test_unknown_list_is_ranked_and_carries_cumulative_pct(db):
    seed_word(db, "w-neko", "猫", known=True)
    morphs = (
        [morph("猫")] * 6 + [morph("犬")] * 3 + [morph("鳥")] * 1
    )
    result = coverage_from_morphs(db, morphs, top_unknown=5)
    assert [entry["lemma"] for entry in result["unknown"]] == ["犬", "鳥"]
    assert [entry["occurrences"] for entry in result["unknown"]] == [3, 1]
    # 6 known of 10, then +3 = 90%, then +1 = 100%.
    assert [entry["cumulative_pct"] for entry in result["unknown"]] == [90.0, 100.0]
    assert result["types"] == {"counted": 3, "known": 1, "unknown": 2}


def test_unknown_list_order_is_stable_for_equal_counts(db):
    result = coverage_from_morphs(db, [morph("鳥"), morph("犬")])
    assert [entry["lemma"] for entry in result["unknown"]] == ["犬", "鳥"]


def test_top_unknown_truncates_without_changing_the_ratio(db):
    seed_word(db, "w-neko", "猫", known=True)
    morphs = [morph("猫"), morph("犬"), morph("鳥"), morph("魚")]
    full = coverage_from_morphs(db, morphs, top_unknown=15)
    short = coverage_from_morphs(db, morphs, top_unknown=1)
    assert len(full["unknown"]) == 3
    assert len(short["unknown"]) == 1
    assert short["unknown_types"] == 3
    assert short["known_pct"] == full["known_pct"]


@pytest.mark.parametrize("value", [-1, MAX_TOP_UNKNOWN + 1])
def test_bad_top_unknown_is_a_value(db, value):
    result = coverage_from_morphs(db, [morph("猫")], top_unknown=value)
    assert result == {
        "ok": False,
        "error": BAD_TOP_UNKNOWN,
        "note": result["note"],
    }


@pytest.mark.parametrize("value", ["15", 1.0, True])
def test_top_unknown_type_misuse_raises(db, value):
    with pytest.raises(TypeError):
        coverage_from_morphs(db, [morph("猫")], top_unknown=value)


def test_coverage_refuses_empty_and_oversized_text(db):
    assert coverage(db, "   ")["error"] == EMPTY_TEXT
    big = coverage(db, "猫" * (MAX_COVERAGE_CHARS + 1))
    assert big["error"] == TEXT_TOO_LARGE
    with pytest.raises(TypeError):
        coverage(db, 123)  # type: ignore[arg-type]


def test_coverage_tokenizes_real_text(db):
    """The whole path, on the real vendored UniDic: 猫です。is one content token."""
    seed_word(db, "w-neko", "猫", known=True)
    result = coverage(db, "猫です。")
    assert result["ok"] is True
    assert result["known_pct"] == 100.0
    assert result["counts"]["counted_tokens"] == 1
    assert result["counts"]["function_tokens"] == 1
    assert result["chars"] == 4


# ===========================================================================
# 2. KnownLookup: the keys tried, and the cache
# ===========================================================================


def test_lookup_keys_dedupe_and_label_by_value():
    keys = KnownLookup.lookup_keys(morph("猫", "猫"))
    assert keys == ((KEY_LEMMA, "猫"),)          # surface == lemma, one key
    keys = KnownLookup.lookup_keys(morph("読み", "読む"))
    assert keys == ((KEY_LEMMA, "読む"), (KEY_SURFACE, "読み"))


def test_reading_key_is_offered_only_for_a_kana_lemma():
    kana = KnownLookup.lookup_keys(morph("テレビ", "テレビ", reading="テレビ"))
    assert kana == ((KEY_LEMMA, "テレビ"), (KEY_READING, "てれび"))
    kanji = KnownLookup.lookup_keys(morph("雨", "雨", reading="アメ"))
    assert all(which != KEY_READING for which, _ in kanji)


def test_a_kanji_word_is_never_resolved_by_a_homophone_reading(db):
    """飴 known must not make 雨 known: that is how coverage inflates silently."""
    seed_word(db, "w-ame-candy", "飴", reading="あめ", known=True)
    result = coverage_from_morphs(db, [morph("雨", reading="アメ")])
    assert result["known_pct"] == 0.0
    assert result["unknown"][0]["state"] == STATE_UNSEEN


def test_kana_lemma_resolves_by_reading(db):
    """テレビ is not in the vault as テレビ, but its hiragana reading is."""
    seed_item(db, "w-terebi", kind="word", kanji=None, reading="てれび")
    mark(db, "w-terebi")
    verdict = KnownLookup(db).verdict(morph("テレビ", "テレビ", reading="テレビ"))
    assert verdict.is_known is True
    assert verdict.matched_by == KEY_READING
    assert verdict.item_id == "w-terebi"


def test_surface_key_reports_itself_as_the_hit(db):
    seed_word(db, "w-yomi", "読み", known=True)
    verdict = KnownLookup(db).verdict(morph("読み", "読む"))
    assert verdict.is_known is True
    assert verdict.matched_by == KEY_SURFACE
    assert verdict.item_id == "w-yomi"


def test_known_lookup_caches_by_key_tuple(db):
    seed_word(db, "w-neko", "猫", known=True)
    lookup = KnownLookup(db)
    for _ in range(50):
        lookup.verdict(morph("猫"))
    assert lookup.queries == 1


def test_query_count_does_not_scale_with_repeated_passes(db):
    seed_word(db, "w-neko", "猫", known=True)
    morphs = [morph("猫"), morph("犬"), morph("鳥"), morph("は", pos1="助詞")]
    lookup = KnownLookup(db)
    first = coverage_from_morphs(db, morphs, lookup=lookup)
    after_one = lookup.queries
    for _ in range(20):
        coverage_from_morphs(db, morphs, lookup=lookup)
    assert lookup.queries == after_one
    assert first["known_queries"] == after_one


def test_query_count_does_not_scale_with_candidates(db):
    """20 candidates over one word type must cost one known_word call, not 20."""
    seed_word(db, "w-neko", "猫", known=True)
    seed_item(db, "g-desu-copula", kind="grammar", understanding=5)
    candidates = [{"text": "猫です。", "grammar_ids": ["g-desu-copula"]}] * 20
    result = find_i_plus_one(db, candidates, score_difficulty=False)
    assert result["ok"] is True
    assert result["known_queries"] == 1


# ===========================================================================
# 3. Curriculum parsing
# ===========================================================================

CURRICULUM_MD = """---
schema: 1
type: index
---

# Test curriculum

## Phase 1

```yaml
id: g-root
level: A0
unlocks: [g-extra]
---
id: g-mid
prereqs: [g-root]
level: A1
---
id: g-leaf
prereqs: [g-mid]
```

## Phase 2

```
g-leaf ──> g-top
             │
             └──> g-branch
```

## Node format

```yaml
id: g-0003
prereqs: [g-0001, g-0002]
level: A0
```
"""


def test_parse_node_blocks_and_diagram_together():
    parsed = parse_curriculum(CURRICULUM_MD)
    assert [node.id for node in parsed.nodes] == ["g-root", "g-mid", "g-leaf"]
    assert parsed.levels == {"g-root": "A0", "g-mid": "A1"}
    assert {(edge.from_id, edge.to_id, edge.edge_type, edge.source) for edge in parsed.edges} == {
        ("g-root", "g-extra", EDGE_UNLOCK, SOURCE_NODE_BLOCK),
        ("g-root", "g-mid", EDGE_PREREQ, SOURCE_NODE_BLOCK),
        ("g-mid", "g-leaf", EDGE_PREREQ, SOURCE_NODE_BLOCK),
        ("g-leaf", "g-top", EDGE_PREREQ, SOURCE_DIAGRAM),
        ("g-top", "g-branch", EDGE_PREREQ, SOURCE_DIAGRAM),
    }
    assert set(parsed.ids) == {
        "g-root", "g-mid", "g-leaf", "g-extra", "g-top", "g-branch"
    }


def test_prereqs_and_unlocks_both_point_earlier_to_later():
    parsed = parse_curriculum(CURRICULUM_MD)
    by_type = {
        edge.edge_type: (edge.from_id, edge.to_id)
        for edge in parsed.edges
        if edge.source == SOURCE_NODE_BLOCK and edge.to_id in {"g-mid", "g-extra"}
    }
    # prereqs: [g-root] on g-mid  ->  g-root -> g-mid
    assert by_type[EDGE_PREREQ] == ("g-root", "g-mid")
    # unlocks: [g-extra] on g-root ->  g-root -> g-extra
    assert by_type[EDGE_UNLOCK] == ("g-root", "g-extra")


def test_node_format_section_is_skipped_and_says_so():
    parsed = parse_curriculum(CURRICULUM_MD)
    assert all(not node.id.startswith("g-000") for node in parsed.nodes)
    assert any("Node format" in note for note in parsed.skipped)


def test_non_slug_ids_are_skipped_and_reported():
    text = "```yaml\nid: wa-topic\nprereqs: [g-root]\n```\n"
    parsed = parse_curriculum(text)
    assert parsed.nodes == ()
    assert any("not a grammar slug" in note for note in parsed.skipped)

    text = "```yaml\nid: g-mid\nprereqs: [g-root, nonsense]\n```\n"
    parsed = parse_curriculum(text)
    assert parsed.nodes[0].prereqs == ("g-root",)
    assert any("'nonsense'" in note for note in parsed.skipped)


def test_self_edge_is_refused_and_reported():
    parsed = parse_curriculum("```yaml\nid: g-loop\nprereqs: [g-loop]\n```\n")
    assert parsed.edges == ()
    assert any("its own prereq" in note for note in parsed.skipped)


def test_branch_with_no_parent_in_its_column_is_skipped_not_guessed():
    parsed = parse_curriculum("```\n   └──> g-orphan\n```\n")
    assert parsed.edges == ()
    assert any("no node above it in that column" in note for note in parsed.skipped)


def test_two_branches_on_one_line_do_not_link_to_each_other():
    text = (
        "```\n"
        "g-a ──> g-b\n"
        "  │       │\n"
        "  └──> g-c└──> g-d\n"
        "```\n"
    )
    parsed = parse_curriculum(text)
    pairs = {(edge.from_id, edge.to_id) for edge in parsed.edges}
    assert ("g-c", "g-d") not in pairs
    assert ("g-a", "g-b") in pairs


def test_unclosed_fence_is_parsed_and_reported():
    parsed = parse_curriculum("```yaml\nid: g-root\n")
    assert [node.id for node in parsed.nodes] == ["g-root"]
    assert any("never closed" in note for note in parsed.skipped)


def test_the_real_curriculum_parses_into_the_phase_one_diagram():
    """Read the learner's own file: Phase-1 arrow diagram plus Phase-0 kana node blocks."""
    parsed = parse_curriculum(REAL_CURRICULUM.read_text(encoding="utf-8"))
    diagram_pairs = {
        (edge.from_id, edge.to_id)
        for edge in parsed.edges
        if edge.source == SOURCE_DIAGRAM
    }
    assert diagram_pairs == {
        ("g-desu-copula", "g-wa-topic"),
        ("g-wa-topic", "g-o-object"),
        ("g-o-object", "g-masu-form"),
        ("g-wa-topic", "g-no-possessive"),
        ("g-masu-form", "g-negation"),
        ("g-wa-topic", "g-question-ka"),
    }
    assert all(edge.edge_type == EDGE_PREREQ for edge in parsed.edges)
    # The Phase-0 kana rows (006 T004) declare node blocks with prereq edges.
    node_block_edges = [e for e in parsed.edges if e.source == SOURCE_NODE_BLOCK]
    assert len(node_block_edges) == 24
    assert len(parsed.nodes) == 13
    assert all(
        node.id.startswith(("g-hiragana-", "g-katakana-")) for node in parsed.nodes
    )
    # The documented g-0003 example is under "Node format" and must not import.
    assert any("Node format" in note for note in parsed.skipped)


# ===========================================================================
# 4. find_cycle
# ===========================================================================


def test_find_cycle_names_the_cycle():
    cycle = find_cycle([("a", "b"), ("b", "c"), ("c", "a")])
    assert cycle is not None
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b", "c"}


def test_find_cycle_returns_none_for_a_dag():
    assert find_cycle([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]) is None
    assert find_cycle([]) is None


# ===========================================================================
# 5. import_curriculum
# ===========================================================================


def write_curriculum(tmp_path: Path, text: str, name: str = "curriculum.md") -> Path:
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


def test_import_creates_items_and_edges(db, tmp_path):
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path)
    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["nodes"] == {
        "ids": 6,
        "declared": 3,
        "items_created": 6,
        "stubs_created": 3,          # g-extra, g-top, g-branch
        "levels_filled": 0,
        "unchanged": 0,
    }
    assert result["edges"]["parsed"] == 5
    assert result["edges"]["created"] == 5
    assert result["edges"]["by_type"] == {EDGE_PREREQ: 4, EDGE_UNLOCK: 1}
    assert result["edges"]["by_source"] == {SOURCE_NODE_BLOCK: 3, SOURCE_DIAGRAM: 2}

    kinds = {
        row["id"]: row["kind"] for row in db.execute("SELECT id, kind FROM item")
    }
    assert set(kinds) == set(parse_curriculum(CURRICULUM_MD).ids)
    assert set(kinds.values()) == {"grammar"}
    levels = dict(db.execute("SELECT id, level FROM item WHERE level IS NOT NULL"))
    assert levels == {"g-root": "A0", "g-mid": "A1"}


def test_import_is_idempotent_and_the_second_run_writes_nothing(db, tmp_path):
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    import_curriculum(db, path=path)
    before = snapshot(db)
    again = import_curriculum(db, path=path)
    assert again["ok"] is True
    assert again["nodes"]["items_created"] == 0
    assert again["nodes"]["stubs_created"] == 0
    assert again["nodes"]["levels_filled"] == 0
    assert again["nodes"]["unchanged"] == 6
    assert again["edges"]["created"] == 0
    assert again["edges"]["already_present"] == 5
    assert snapshot(db) == before


def test_dry_run_reports_everything_and_writes_nothing(db, tmp_path):
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["nodes"]["items_created"] == 6
    assert result["edges"]["created"] == 5
    assert snapshot(db) == ([], [])


def test_upsert_coalesce_preserves_curated_level_and_understanding(db, tmp_path):
    """A curated level or self-rating is never overwritten by a stub import."""
    seed_item(db, "g-root", kind="grammar", level="B2", understanding=4)
    seed_item(db, "g-leaf", kind="grammar")           # no level yet
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path)
    assert result["ok"] is True
    row = db.execute(
        "SELECT level, understanding FROM item WHERE id = 'g-root'"
    ).fetchone()
    assert row["level"] == "B2"            # not A0
    assert row["understanding"] == 4       # never touched by the import at all
    # g-mid declares A1 and had no row: the level is filled in.
    assert db.execute(
        "SELECT level FROM item WHERE id = 'g-mid'"
    ).fetchone()["level"] == "A1"


def test_levels_filled_counts_a_blank_level_being_completed(db, tmp_path):
    seed_item(db, "g-mid", kind="grammar", level=None)
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path)
    assert result["nodes"]["levels_filled"] == 1
    assert db.execute(
        "SELECT level FROM item WHERE id = 'g-mid'"
    ).fetchone()["level"] == "A1"


def test_cycle_in_the_file_is_refused_whole(db, tmp_path):
    text = (
        "```yaml\nid: g-a\nprereqs: [g-b]\n---\nid: g-b\nprereqs: [g-a]\n```\n"
    )
    path = write_curriculum(tmp_path, text)
    result = import_curriculum(db, path=path)
    assert result["ok"] is False
    assert result["error"] == CURRICULUM_CYCLE
    assert result["cycle"][0] == result["cycle"][-1]
    assert snapshot(db) == ([], [])


def test_cycle_closed_against_stored_edges_is_refused(db, tmp_path):
    """A file edge that only becomes a cycle with rows already present."""
    seed_item(db, "g-a", kind="grammar")
    seed_item(db, "g-b", kind="grammar")
    seed_edge(db, "g-b", "g-a")
    before = snapshot(db)
    path = write_curriculum(tmp_path, "```yaml\nid: g-b\nprereqs: [g-a]\n```\n")
    result = import_curriculum(db, path=path)
    assert result["error"] == CURRICULUM_CYCLE
    assert set(result["cycle"]) == {"g-a", "g-b"}
    assert snapshot(db) == before


def test_missing_file_is_a_value(db, tmp_path):
    result = import_curriculum(db, path=tmp_path / "nope.md")
    assert result["ok"] is False
    assert result["error"] == CURRICULUM_UNAVAILABLE
    assert "Could not read the curriculum" in result["note"]


def test_a_file_with_no_nodes_is_a_value(db, tmp_path):
    path = write_curriculum(tmp_path, "# Nothing here\n\nJust prose.\n")
    result = import_curriculum(db, path=path)
    assert result["ok"] is False
    assert result["error"] == CURRICULUM_EMPTY


def test_orphan_edges_are_reported_never_deleted(db, tmp_path):
    seed_item(db, "g-old", kind="grammar")
    seed_item(db, "g-leaf", kind="grammar")
    seed_edge(db, "g-old", "g-leaf")
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path)
    assert {"from_id": "g-old", "to_id": "g-leaf", "edge_type": EDGE_PREREQ} in (
        result["orphan_edges"]
    )
    assert "never deleted" in result["note"]
    assert db.execute(
        "SELECT COUNT(*) FROM item_edge WHERE from_id = 'g-old'"
    ).fetchone()[0] == 1


def test_an_id_that_is_already_a_word_item_is_left_alone(db, tmp_path):
    seed_item(db, "g-mid", kind="word", kanji="なにか")
    path = write_curriculum(tmp_path, CURRICULUM_MD)
    result = import_curriculum(db, path=path)
    assert result["ok"] is True
    assert any("already exists as a word item" in note for note in result["skipped"])
    assert db.execute(
        "SELECT kind FROM item WHERE id = 'g-mid'"
    ).fetchone()["kind"] == "word"
    edges = {
        (row["from_id"], row["to_id"])
        for row in db.execute("SELECT from_id, to_id FROM item_edge")
    }
    assert not any("g-mid" in pair for pair in edges)


def test_import_of_the_real_curriculum(db):
    result = import_curriculum(db, path=REAL_CURRICULUM)
    assert result["ok"] is True
    assert result["nodes"]["ids"] == 20
    assert result["edges"]["created"] == 30
    assert result["edges"]["by_source"] == {SOURCE_NODE_BLOCK: 24, SOURCE_DIAGRAM: 6}
    # Idempotent on the learner's own file too.
    before = snapshot(db)
    import_curriculum(db, path=REAL_CURRICULUM)
    assert snapshot(db) == before


# ===========================================================================
# 6. GrammarDag and reachability
# ===========================================================================


CHAIN = ("g-desu-copula", "g-wa-topic", "g-o-object", "g-masu-form")


def seed_chain(conn: sqlite3.Connection) -> None:
    """The real phase-1 chain: copula -> wa -> o -> masu, as prereq edges."""
    for node in CHAIN:
        seed_item(conn, node, kind="grammar")
    for earlier, later in zip(CHAIN, CHAIN[1:]):
        seed_edge(conn, earlier, later)


def test_prereq_closure_is_transitive(db):
    seed_chain(db)
    dag = load_grammar_dag(db)
    assert dag.prereq_closure("g-masu-form") == (
        "g-desu-copula", "g-o-object", "g-wa-topic"
    )
    assert dag.prereq_closure("g-desu-copula") == ()


def test_prereq_closure_terminates_on_a_cyclic_graph():
    dag = GrammarDag(
        prereqs={"a": ("b",), "b": ("a",)}, unlocked_by={}, cycle=("a", "b", "a")
    )
    assert dag.prereq_closure("a") == ("b",)


def test_load_grammar_dag_separates_the_edge_types_and_finds_cycles(db):
    seed_chain(db)
    seed_item(db, "g-extra", kind="grammar")
    seed_edge(db, "g-wa-topic", "g-extra", EDGE_UNLOCK)
    dag = load_grammar_dag(db)
    assert dag.cycle is None
    assert dag.prereqs["g-masu-form"] == ("g-o-object",)
    assert dag.unlocked_by == {"g-extra": ("g-wa-topic",)}
    assert "g-extra" not in dag.prereqs


def test_a_node_is_reachable_when_every_prereq_is_mastered(db):
    seed_chain(db)
    for node in CHAIN[:-1]:
        mark(db, node)
    out = grammar_reachability(db, ["g-masu-form"])["g-masu-form"]
    assert out["reachable"] is True
    assert out["is_new"] is True                 # the node itself is the +1
    assert out["mastered"] is False
    assert out["closure_size"] == 3
    assert out["missing_prereqs"] == []


def test_one_unmastered_prerequisite_however_deep_makes_it_unreachable(db):
    seed_chain(db)
    mark(db, "g-wa-topic")
    mark(db, "g-o-object")
    # g-desu-copula, the root two hops away, is not mastered.
    out = grammar_reachability(db, ["g-masu-form"])["g-masu-form"]
    assert out["reachable"] is False
    assert [entry["id"] for entry in out["missing_prereqs"]] == ["g-desu-copula"]


def test_mastery_via_known_set_and_via_understanding(db):
    seed_chain(db)
    mark(db, "g-desu-copula")
    db.execute(
        "UPDATE item SET understanding = ? WHERE id = 'g-wa-topic'",
        (DEFAULT_MIN_UNDERSTANDING,),
    )
    out = grammar_reachability(db, ["g-desu-copula", "g-wa-topic", "g-o-object"])
    assert out["g-desu-copula"]["mastered_via"] == MASTERY_KNOWN_SET
    assert out["g-wa-topic"]["mastered_via"] == MASTERY_UNDERSTANDING
    assert out["g-o-object"]["mastered"] is False
    assert out["g-o-object"]["mastered_via"] is None


def test_understanding_below_the_threshold_is_not_mastery(db):
    seed_chain(db)
    db.execute(
        "UPDATE item SET understanding = ? WHERE id = 'g-desu-copula'",
        (DEFAULT_MIN_UNDERSTANDING - 1,),
    )
    out = grammar_reachability(db, ["g-wa-topic"])["g-wa-topic"]
    assert out["reachable"] is False
    assert out["missing_prereqs"][0]["understanding"] == DEFAULT_MIN_UNDERSTANDING - 1
    # ...and a lower bar makes the same graph reachable.
    lower = grammar_reachability(
        db, ["g-wa-topic"], min_understanding=DEFAULT_MIN_UNDERSTANDING - 1
    )["g-wa-topic"]
    assert lower["reachable"] is True


def test_unlock_edges_are_reported_but_never_walked(db):
    """Availability is not reachability: an unlock door is never a requirement."""
    seed_item(db, "g-source", kind="grammar")
    seed_item(db, "g-target", kind="grammar")
    seed_edge(db, "g-source", "g-target", EDGE_UNLOCK)
    out = grammar_reachability(db, ["g-target"])["g-target"]
    assert out["reachable"] is True              # g-source is NOT mastered
    assert out["prereqs"] == []
    assert out["unlocked_by"] == ["g-source"]
    assert out["unlock_ready"] == []
    mark(db, "g-source")
    ready = grammar_reachability(db, ["g-target"])["g-target"]
    assert ready["unlock_ready"] == ["g-source"]
    assert ready["reachable"] is True


def test_a_node_with_no_item_row_is_reported_as_nonexistent(db):
    out = grammar_reachability(db, ["g-ghost"])["g-ghost"]
    assert out["exists"] is False
    assert out["kind"] is None
    assert out["mastered"] is False


def test_reachability_follows_an_alias_redirect(db):
    seed_item(db, "g-new", kind="grammar")
    db.execute(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) VALUES (?, ?, ?)",
        ("g-old", "g-new", TS),
    )
    mark(db, "g-new")
    out = grammar_reachability(db, ["g-old"])["g-old"]
    assert out["canonical_id"] == "g-new"
    assert out["redirected"] is True
    assert out["mastered"] is True


def test_mastery_lookup_caches_per_node(db):
    seed_chain(db)
    lookup = MasteryLookup(db)
    ids = ["g-masu-form", "g-o-object", "g-wa-topic", "g-desu-copula"]
    grammar_reachability(db, ids, mastery=lookup)
    first = lookup.queries
    grammar_reachability(db, ids, mastery=lookup)
    assert lookup.queries == first == 4


def test_mastery_lookup_rejects_a_non_int_threshold(db):
    with pytest.raises(TypeError):
        MasteryLookup(db, min_understanding="3")  # type: ignore[arg-type]


# ===========================================================================
# 7. find_i_plus_one — the D-28 gate
# ===========================================================================


@pytest.fixture
def gate_world(db):
    """猫 known, the phase-1 chain stored, copula and wa mastered.

    g-o-object is deliberately left unmastered: that is the wall the
    independent test proves coverage cannot see.
    """
    seed_word(db, "w-neko", "猫", known=True)
    seed_chain(db)
    mark(db, "g-desu-copula")
    mark(db, "g-wa-topic")
    return db


def test_full_coverage_with_unreachable_grammar_is_still_gated(gate_world):
    """D-28, in one test.

    「猫です。」 is 100% vocabulary coverage — every content token is in the real
    known set. Its grammar point g-masu-form sits behind g-o-object, which the
    learner has not mastered. Coverage says "serve it"; the grammar half must
    override that, name ``unreachable_grammar``, and name the missing node.
    """
    candidate = {"text": "猫です。", "grammar_ids": ["g-masu-form"]}
    result = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    assert result["ok"] is True

    entry = result["gated"][0]
    assert entry["coverage"]["known_pct"] == 100.0     # the tempting number
    assert entry["coverage"]["unknown_types"] == 0
    assert entry["accepted"] is False
    assert entry["gated_by"] == [GATE_UNREACHABLE_GRAMMAR]
    assert entry["grammar"]["reachable"] is False
    assert entry["grammar"]["unreachable"] == [
        {"id": "g-masu-form", "missing_prereqs": ["g-o-object"]}
    ]
    assert result["candidates"] == []
    assert result["counts"] == {
        "offered": 1,
        "accepted": 0,
        "returned": 0,
        "gated": 1,
        "by_reason": {GATE_UNREACHABLE_GRAMMAR: 1},
        "unannotated": 0,
    }
    assert result["gates"]["reachability_edge_type"] == EDGE_PREREQ


def test_unreachable_grammar_survives_every_relaxation(gate_world):
    """There is no flag that turns the wall into comprehensible input."""
    candidate = {"text": "猫です。", "grammar_ids": ["g-masu-form"]}
    result = find_i_plus_one(
        gate_world,
        [candidate],
        require_grammar=False,          # opts out of "unknown grammar", not of this
        min_coverage_pct=0.0,
        max_unknown_types=None,
        max_new_grammar=None,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["candidates"] == []
    assert result["gated"][0]["gated_by"] == [GATE_UNREACHABLE_GRAMMAR]


def test_mastering_the_missing_prerequisite_opens_the_same_candidate(gate_world):
    """The mirror image: the only thing that changed is the grammar half."""
    candidate = {"text": "猫です。", "grammar_ids": ["g-masu-form"]}
    mark(gate_world, "g-o-object")
    result = find_i_plus_one(gate_world, [candidate], score_difficulty=False)
    assert [entry["text"] for entry in result["candidates"]] == ["猫です。"]
    entry = result["candidates"][0]
    assert entry["accepted"] is True
    assert entry["gated_by"] == []
    assert entry["order"] == 1
    assert entry["grammar"]["new"] == ["g-masu-form"]
    assert entry["grammar"]["resolved_from"] == GRAMMAR_FROM_EXPLICIT


def test_a_reachable_root_grammar_point_is_accepted(gate_world):
    candidate = {"text": "猫です。", "grammar_ids": ["g-wa-topic"]}
    result = find_i_plus_one(gate_world, [candidate], score_difficulty=False)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["grammar"]["reachable"] is True
    assert result["candidates"][0]["grammar"]["new"] == []   # already mastered


def test_two_new_grammar_points_is_i_plus_two(gate_world):
    seed_item(gate_world, "g-alpha", kind="grammar")
    seed_item(gate_world, "g-beta", kind="grammar")
    candidate = {"text": "猫です。", "grammar_ids": ["g-alpha", "g-beta"]}
    result = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    assert result["gated"][0]["gated_by"] == [GATE_TOO_MUCH_NEW_GRAMMAR]
    # Both are roots, so neither is unreachable — only the budget was blown.
    assert result["gated"][0]["grammar"]["reachable"] is True
    relaxed = find_i_plus_one(
        gate_world, [candidate], max_new_grammar=2, score_difficulty=False
    )
    assert len(relaxed["candidates"]) == 1
    unlimited = find_i_plus_one(
        gate_world, [candidate], max_new_grammar=None, score_difficulty=False
    )
    assert len(unlimited["candidates"]) == 1


def test_coverage_too_low_gate(gate_world):
    candidate = {"text": "犬です。", "grammar_ids": ["g-wa-topic"]}
    result = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    entry = result["gated"][0]
    assert entry["coverage"]["known_pct"] == 0.0
    assert GATE_COVERAGE_TOO_LOW in entry["gated_by"]
    assert entry["grammar"]["reachable"] is True


def test_too_many_unknown_types_gate(gate_world):
    candidate = {"text": "犬です。", "grammar_ids": ["g-wa-topic"]}
    result = find_i_plus_one(
        gate_world,
        [candidate],
        min_coverage_pct=0.0,
        max_unknown_types=0,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["gated"][0]["gated_by"] == [GATE_TOO_MANY_UNKNOWN]
    lenient = find_i_plus_one(
        gate_world,
        [candidate],
        min_coverage_pct=0.0,
        max_unknown_types=None,
        score_difficulty=False,
    )
    assert len(lenient["candidates"]) == 1


def test_no_grammar_information_is_gated_by_default(gate_world):
    result = find_i_plus_one(
        gate_world, ["猫です。"], include_gated=True, score_difficulty=False
    )
    entry = result["gated"][0]
    assert entry["gated_by"] == [GATE_GRAMMAR_UNKNOWN]
    assert entry["grammar"]["resolved_from"] == GRAMMAR_FROM_NOTHING
    assert result["counts"]["unannotated"] == 1


@pytest.mark.parametrize("annotation", ["not-a-slug", "g-ghost", "w-neko"])
def test_an_id_that_is_no_grammar_point_is_grammar_unknown(gate_world, annotation):
    """Unparseable, absent, and wrong-kind all mean 'no grammar information'."""
    candidate = {"text": "猫です。", "grammar_ids": [annotation]}
    result = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    assert GATE_GRAMMAR_UNKNOWN in result["gated"][0]["gated_by"]


def test_require_grammar_false_bypasses_only_the_unknown_gate(gate_world):
    result = find_i_plus_one(
        gate_world, ["猫です。"], require_grammar=False, score_difficulty=False
    )
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["gated_by"] == []
    assert result["gates"]["require_grammar"] is False
    assert "require_grammar=False" in result["note"]
    assert "D-28 forbids by default" in result["note"]


def test_sealed_items_are_never_offered_and_there_is_no_override(gate_world):
    seed_sentence(gate_world, "s-sealed", "猫です。", sealed=1)
    candidate = {"id": "s-sealed", "text": "猫です。", "grammar_ids": ["g-wa-topic"]}
    result = find_i_plus_one(
        gate_world,
        [candidate],
        min_coverage_pct=0.0,
        max_unknown_types=None,
        max_new_grammar=None,
        require_grammar=False,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["candidates"] == []
    assert result["gated"][0]["gated_by"] == [GATE_SEALED]
    # ...and the built-in loader never even offers it.
    assert candidates_from_items(gate_world) == []


def test_every_failing_reason_is_reported_not_just_the_first(gate_world):
    seed_sentence(gate_world, "s-bad", "犬と鳥です。", sealed=1)
    candidate = {
        "id": "s-bad",
        "text": "犬と鳥です。",
        "grammar_ids": ["g-o-object", "g-masu-form"],
    }
    result = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    reasons = set(result["gated"][0]["gated_by"])
    assert reasons == {
        GATE_SEALED,
        GATE_UNREACHABLE_GRAMMAR,
        GATE_TOO_MUCH_NEW_GRAMMAR,
        GATE_COVERAGE_TOO_LOW,
        GATE_TOO_MANY_UNKNOWN,
    }
    assert result["counts"]["by_reason"][GATE_SEALED] == 1


def test_grammar_is_read_from_prereq_edges_pointing_at_the_item(gate_world):
    seed_sentence(gate_world, "s-one", "猫です。")
    seed_edge(gate_world, "g-wa-topic", "s-one")
    result = find_i_plus_one(
        gate_world, [{"id": "s-one", "text": "猫です。"}], score_difficulty=False
    )
    entry = result["candidates"][0]
    assert entry["grammar"]["resolved_from"] == GRAMMAR_FROM_EDGES
    assert entry["grammar"]["ids"] == ["g-wa-topic"]


def test_grammar_falls_back_to_a_grammar_slug_home_topic(gate_world):
    seed_sentence(gate_world, "s-two", "猫です。", home_topic="g-wa-topic")
    result = find_i_plus_one(
        gate_world, [{"id": "s-two", "text": "猫です。"}], score_difficulty=False
    )
    assert result["candidates"][0]["grammar"]["resolved_from"] == (
        GRAMMAR_FROM_HOME_TOPIC
    )


def test_an_explicit_annotation_beats_a_stored_edge(gate_world):
    seed_sentence(gate_world, "s-three", "猫です。")
    seed_edge(gate_world, "g-masu-form", "s-three")
    result = find_i_plus_one(
        gate_world,
        [{"id": "s-three", "text": "猫です。", "grammar_ids": ["g-wa-topic"]}],
        score_difficulty=False,
    )
    entry = result["candidates"][0]
    assert entry["grammar"]["resolved_from"] == GRAMMAR_FROM_EXPLICIT
    assert entry["grammar"]["ids"] == ["g-wa-topic"]


def test_accepted_candidates_are_ranked_by_comprehension_debt(gate_world):
    seed_word(gate_world, "w-inu", "犬", known=True)
    for _ in range(3):
        seed_observation(gate_world, "w-inu", ts=days_ago(1), unassisted=0)
    result = find_i_plus_one(
        gate_world,
        [
            {"text": "猫です。", "grammar_ids": ["g-wa-topic"]},
            {"text": "犬です。", "grammar_ids": ["g-wa-topic"]},
        ],
        now=NOW,
        score_difficulty=False,
    )
    assert result["ranked_by"] == RANKED_BY_DEBT
    assert [entry["text"] for entry in result["candidates"]] == ["犬です。", "猫です。"]
    assert result["candidates"][0]["debt"]["total"] > 0
    assert result["candidates"][0]["debt"]["vocab"] > 0
    assert result["candidates"][1]["debt"]["total"] == 0.0
    assert result["candidates"][0]["order"] == 1
    assert result["as_of"] == NOW


def test_top_truncates_the_accepted_list_without_hiding_the_count(gate_world):
    seed_word(gate_world, "w-inu", "犬", known=True)
    candidates = [
        {"text": "猫です。", "grammar_ids": ["g-wa-topic"]},
        {"text": "犬です。", "grammar_ids": ["g-wa-topic"]},
    ]
    result = find_i_plus_one(gate_world, candidates, top=1, score_difficulty=False)
    assert result["counts"]["accepted"] == 2
    assert result["counts"]["returned"] == 1
    assert len(result["candidates"]) == 1


def test_gated_candidates_are_only_returned_when_asked_for(gate_world):
    candidate = {"text": "猫です。", "grammar_ids": ["g-masu-form"]}
    quiet = find_i_plus_one(gate_world, [candidate], score_difficulty=False)
    assert quiet["gated"] == []
    assert quiet["counts"]["gated"] == 1
    assert "every candidate was gated out" in quiet["note"]
    loud = find_i_plus_one(
        gate_world, [candidate], include_gated=True, score_difficulty=False
    )
    assert len(loud["gated"]) == 1


def test_the_builtin_loader_reads_stored_sentence_items(gate_world):
    seed_sentence(gate_world, "s-a", "猫です。", home_topic="g-wa-topic")
    seed_sentence(gate_world, "s-b", "猫です。", sealed=1, home_topic="g-wa-topic")
    loaded = candidates_from_items(gate_world)
    assert [candidate.id for candidate in loaded] == ["s-a"]
    result = find_i_plus_one(gate_world, score_difficulty=False)
    assert [entry["id"] for entry in result["candidates"]] == ["s-a"]


# ---------------------------------------------------------------------------
# A0 production pool (D-38 / FR-018): the audio-anchor gate
# ---------------------------------------------------------------------------
#
# This worktree has no vendored UniDic dictionary (vendor/unidic/unidic is a
# 775MB gitignored directory not copied into a git worktree), so the real
# tokenizer raises TokenizerError unconditionally here — a environment gap,
# not a claim about this feature's code (confirmed by `git stash`: the same
# tokenizer failures exist before and after the audio-anchor commits). These
# tests are about the audio-anchor gate in find_i_plus_one, not about
# tokenization, so `intelligence.get_tagger`/`intelligence.tokenize` are
# monkeypatched to a tiny deterministic stand-in that isolates the gate logic
# from the vendored dictionary entirely.


@pytest.fixture
def stub_tokenizer(monkeypatch):
    """Replace the real UniDic tokenizer with a caller-fed text -> Morph map.

    ``get_tagger`` is patched so it never raises TOKENIZER_UNAVAILABLE, and
    ``tokenize`` is patched to look a candidate's exact text up in the returned
    dict. Each test seeds one entry per candidate text; a single content
    :func:`morph` whose lemma *is* the seeded item's id is enough to make
    ``KnownLookup`` resolve it directly by item id (see ``known.known_word``),
    with no dependency on real tokenization or on kanji/reading columns.
    """
    morph_map: dict[str, list[Any]] = {}
    monkeypatch.setattr(intelligence, "get_tagger", lambda: object())
    monkeypatch.setattr(
        intelligence,
        "tokenize",
        lambda text, *, tagger=None: morph_map.get(text, [morph(text, text)]),
    )
    return morph_map


def _set_audio_anchor(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    audio_source: str | None = None,
    audio_offset_ms: int | None = None,
    text_only: int = 0,
) -> None:
    """Fill in migration 0002's item columns on an already-seeded item.

    A separate helper rather than new ``seed_sentence``/``seed_item`` keyword
    arguments: this keeps every pre-existing seeding call in this file exactly
    as it was, and makes the three audio-anchor columns opt-in only where a
    test is actually about them.
    """
    conn.execute(
        "UPDATE item SET audio_source = ?, audio_offset_ms = ?, text_only = ? "
        "WHERE id = ?",
        (audio_source, audio_offset_ms, text_only, item_id),
    )


def test_production_pool_accepts_an_audio_anchored_item(db, stub_tokenizer):
    seed_word(db, "w-neko", "cat-word", known=True)
    seed_sentence(db, "s-anchored", "TEXT-ANCHORED")
    _set_audio_anchor(
        db, "s-anchored", audio_source="irodori-u1.mp3", audio_offset_ms=1200
    )
    stub_tokenizer["TEXT-ANCHORED"] = [morph("w-neko", "w-neko")]

    result = find_i_plus_one(
        db,
        [{"id": "s-anchored", "text": "TEXT-ANCHORED", "grammar_ids": []}],
        production=True,
        require_grammar=False,
        min_coverage_pct=0.0,
        score_difficulty=False,
    )
    assert result["ok"] is True
    assert result["gates"]["production"] is True
    assert [entry["id"] for entry in result["candidates"]] == ["s-anchored"]
    assert result["candidates"][0]["gated_by"] == []
    assert result["counts"] == {
        "offered": 1,
        "accepted": 1,
        "returned": 1,
        "gated": 0,
        "by_reason": {},
        "unannotated": 1,
    }


def test_production_pool_withholds_an_unanchored_item(db, stub_tokenizer):
    seed_word(db, "w-neko", "cat-word", known=True)
    seed_sentence(db, "s-unanchored", "TEXT-UNANCHORED")
    # audio_source stays NULL — never anchored at all.
    stub_tokenizer["TEXT-UNANCHORED"] = [morph("w-neko", "w-neko")]

    result = find_i_plus_one(
        db,
        [{"id": "s-unanchored", "text": "TEXT-UNANCHORED", "grammar_ids": []}],
        production=True,
        require_grammar=False,
        min_coverage_pct=0.0,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["candidates"] == []
    assert result["gated"][0]["gated_by"] == [GATE_NOT_AUDIO_ANCHORED]
    assert result["counts"]["by_reason"][GATE_NOT_AUDIO_ANCHORED] == 1


def test_production_pool_withholds_a_text_only_item_even_when_anchored(
    db, stub_tokenizer
):
    """'was recorded' and 'is fit to produce from' are different claims."""
    seed_word(db, "w-neko", "cat-word", known=True)
    seed_sentence(db, "s-textonly", "TEXT-TEXTONLY")
    _set_audio_anchor(db, "s-textonly", audio_source="irodori-u1.mp3", text_only=1)
    stub_tokenizer["TEXT-TEXTONLY"] = [morph("w-neko", "w-neko")]

    result = find_i_plus_one(
        db,
        [{"id": "s-textonly", "text": "TEXT-TEXTONLY", "grammar_ids": []}],
        production=True,
        require_grammar=False,
        min_coverage_pct=0.0,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["candidates"] == []
    assert result["gated"][0]["gated_by"] == [GATE_NOT_AUDIO_ANCHORED]


def test_gate_not_audio_anchored_is_the_spec_wording(db, stub_tokenizer):
    """The reason string is spec.md FR-018's own wording, kept verbatim.

    Also covers the "no stored item row" case: an ad hoc candidate offered with
    no ``id`` has no anchor metadata to check at all, and is withheld the same
    way as a genuinely unanchored one — absence of anchoring information is not
    evidence of anchoring.
    """
    assert GATE_NOT_AUDIO_ANCHORED == "text-only-not-for-A0-production"
    seed_word(db, "w-neko", "cat-word", known=True)
    stub_tokenizer["AD-HOC-TEXT"] = [morph("w-neko", "w-neko")]

    result = find_i_plus_one(
        db,
        [{"text": "AD-HOC-TEXT", "grammar_ids": []}],  # no id -> no stored row
        production=True,
        require_grammar=False,
        min_coverage_pct=0.0,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["gated"][0]["gated_by"] == ["text-only-not-for-A0-production"]
    assert result["counts"]["by_reason"] == {"text-only-not-for-A0-production": 1}


def test_default_production_false_ignores_audio_anchor_columns(db, stub_tokenizer):
    """production defaults to False: reading selection is unaffected, exactly
    as before migration 0002 added the audio-anchor columns."""
    seed_word(db, "w-neko", "cat-word", known=True)
    seed_sentence(db, "s-unanchored", "TEXT-UNANCHORED")
    stub_tokenizer["TEXT-UNANCHORED"] = [morph("w-neko", "w-neko")]

    result = find_i_plus_one(
        db,
        [{"id": "s-unanchored", "text": "TEXT-UNANCHORED", "grammar_ids": []}],
        require_grammar=False,
        min_coverage_pct=0.0,
        score_difficulty=False,
    )
    assert result["gates"]["production"] is False
    assert [entry["id"] for entry in result["candidates"]] == ["s-unanchored"]
    assert result["candidates"][0]["gated_by"] == []


def test_production_pool_withheld_items_are_never_substituted_or_reappear(
    db, stub_tokenizer
):
    """No substitution and no synthesis: a withheld candidate never reappears
    as a different accepted one, and 'accepted' counts only true anchors."""
    seed_word(db, "w-neko", "cat-word", known=True)

    seed_sentence(db, "s-anchor-a", "TEXT-A")
    _set_audio_anchor(db, "s-anchor-a", audio_source="irodori-u1.mp3")
    stub_tokenizer["TEXT-A"] = [morph("w-neko", "w-neko")]

    seed_sentence(db, "s-unanchored-b", "TEXT-B")
    stub_tokenizer["TEXT-B"] = [morph("w-neko", "w-neko")]

    seed_sentence(db, "s-textonly-c", "TEXT-C")
    _set_audio_anchor(db, "s-textonly-c", audio_source="irodori-u2.mp3", text_only=1)
    stub_tokenizer["TEXT-C"] = [morph("w-neko", "w-neko")]

    candidates = [
        {"id": "s-anchor-a", "text": "TEXT-A", "grammar_ids": []},
        {"id": "s-unanchored-b", "text": "TEXT-B", "grammar_ids": []},
        {"id": "s-textonly-c", "text": "TEXT-C", "grammar_ids": []},
    ]
    result = find_i_plus_one(
        db,
        candidates,
        production=True,
        require_grammar=False,
        min_coverage_pct=0.0,
        include_gated=True,
        score_difficulty=False,
    )
    assert result["counts"]["accepted"] == 1
    assert [entry["id"] for entry in result["candidates"]] == ["s-anchor-a"]
    accepted_texts = {entry["text"] for entry in result["candidates"]}
    assert accepted_texts == {"TEXT-A"}

    gated_ids = {entry["id"] for entry in result["gated"]}
    assert gated_ids == {"s-unanchored-b", "s-textonly-c"}
    for entry in result["gated"]:
        assert entry["gated_by"] == [GATE_NOT_AUDIO_ANCHORED]
    assert result["counts"]["by_reason"][GATE_NOT_AUDIO_ANCHORED] == 2


def test_selection_writes_nothing(gate_world):
    before = snapshot(gate_world)
    events_before = gate_world.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    find_i_plus_one(
        gate_world,
        [{"text": "猫です。", "grammar_ids": ["g-wa-topic"]}],
        score_difficulty=False,
    )
    assert snapshot(gate_world) == before
    assert gate_world.execute("SELECT COUNT(*) FROM event").fetchone()[0] == (
        events_before
    )


# --- failure-as-value paths -------------------------------------------------


@pytest.mark.parametrize("top", [0, -1, 201])
def test_bad_limit_is_a_value(gate_world, top):
    result = find_i_plus_one(gate_world, ["猫です。"], top=top)
    assert result["ok"] is False
    assert result["error"] == BAD_LIMIT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_coverage_pct": -1.0},
        {"min_coverage_pct": 100.5},
        {"max_unknown_types": -1},
        {"max_new_grammar": -1},
        {"min_understanding": 0},
        {"min_understanding": 6},
    ],
)
def test_bad_gate_is_a_value(gate_world, kwargs):
    result = find_i_plus_one(gate_world, ["猫です。"], **kwargs)
    assert result["ok"] is False
    assert result["error"] == BAD_GATE


def test_bad_top_unknown_in_selection_is_a_value(gate_world):
    result = find_i_plus_one(gate_world, ["猫です。"], top_unknown=MAX_TOP_UNKNOWN + 1)
    assert result["error"] == BAD_TOP_UNKNOWN


def test_bad_weights_in_selection_is_a_value(gate_world):
    result = find_i_plus_one(gate_world, ["猫です。"], weights={"nonsense": 1.0})
    assert result["ok"] is False
    assert result["error"] == BAD_WEIGHTS
    assert "nonsense" in result["note"]


def test_no_candidates_is_a_value(db):
    assert find_i_plus_one(db, [])["error"] == NO_CANDIDATES
    assert find_i_plus_one(db)["error"] == NO_CANDIDATES     # empty database


def test_too_many_candidates_is_a_value(gate_world):
    result = find_i_plus_one(gate_world, ["猫です。"] * (MAX_CANDIDATES + 1))
    assert result["ok"] is False
    assert result["error"] == TOO_MANY_CANDIDATES


def test_a_stored_cycle_refuses_selection(gate_world):
    seed_item(gate_world, "g-x", kind="grammar")
    seed_item(gate_world, "g-y", kind="grammar")
    seed_edge(gate_world, "g-x", "g-y")
    seed_edge(gate_world, "g-y", "g-x")
    result = find_i_plus_one(gate_world, ["猫です。"], score_difficulty=False)
    assert result["ok"] is False
    assert result["error"] == GRAMMAR_DAG_CYCLE
    assert set(result["cycle"]) == {"g-x", "g-y"}
    assert "added by hand" in result["note"]


def test_empty_and_oversized_candidates_are_gated_not_crashed(gate_world):
    result = find_i_plus_one(
        gate_world,
        [
            {"text": "   ", "grammar_ids": ["g-wa-topic"]},
            {"text": "猫" * 2001, "grammar_ids": ["g-wa-topic"]},
        ],
        include_gated=True,
        score_difficulty=False,
    )
    assert result["ok"] is True
    reasons = [entry["gated_by"] for entry in result["gated"]]
    assert EMPTY_TEXT in reasons[0]
    assert TEXT_TOO_LARGE in reasons[1]


@pytest.mark.parametrize(
    ("value", "match"),
    [
        (123, "must be a Candidate"),
        ({"id": "s-1"}, "needs 'text'"),
        ({"text": 5}, "needs 'text'"),
        ({"text": "x", "id": 5}, "id must be a str"),
        ({"text": "x", "grammar_ids": "g-a"}, "not a single string"),
    ],
)
def test_malformed_candidate_input_raises(value, match):
    with pytest.raises(TypeError, match=match):
        as_candidate(value)  # type: ignore[arg-type]


def test_as_candidate_accepts_the_shapes_it_documents():
    assert as_candidate("猫です。") == Candidate(text="猫です。")
    assert as_candidate(Candidate(text="x")).text == "x"
    built = as_candidate(
        {"jp": "猫です。", "item_id": " s-1 ", "grammar": [" g-a ", ""], "source": "vault"}
    )
    assert built == Candidate(
        text="猫です。", id="s-1", grammar_ids=("g-a",), source="vault"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"top": "5"},
        {"top": True},
        {"min_coverage_pct": "80"},
        {"min_understanding": "3"},
        {"top_unknown": "5"},
        {"max_unknown_types": "1"},
        {"weights": {"coverage": "heavy"}},
    ],
)
def test_selection_type_misuse_raises(gate_world, kwargs):
    with pytest.raises(TypeError):
        find_i_plus_one(gate_world, ["猫です。"], **kwargs)


# ===========================================================================
# 8. Comprehension debt
# ===========================================================================


def test_an_item_with_no_evidence_is_reported_at_zero(db):
    out = comprehension_debt(db, ["w-neko"], now=NOW)
    assert out["w-neko"]["debt"] == 0.0
    assert out["w-neko"]["source"] == DEBT_FROM_NOTHING
    assert out["w-neko"]["cache"] is None
    assert comprehension_debt(db, [], now=NOW) == {}


def test_assisted_attempt_is_full_debt_at_high_coverage(db):
    seed_observation(db, "w-x", ts=NOW, unassisted=0, band=">=95")
    out = comprehension_debt(db, ["w-x"], now=NOW)["w-x"]
    assert out["debt"] == 1.0                    # 1.0 signal x 1.0 attribution x 1.0
    assert out["assisted"] == 1
    assert out["source"] == DEBT_FROM_OBSERVATIONS
    assert out["last_observation_ts"] == NOW


def test_attribution_discounts_debt_earned_in_hard_input(db):
    seed_observation(db, "w-hard", ts=NOW, unassisted=0, band="<80")
    seed_observation(db, "w-mid", ts=NOW, unassisted=0, band="80-95")
    out = comprehension_debt(db, ["w-hard", "w-mid"], now=NOW)
    assert out["w-hard"]["debt"] == 0.5          # mostly the input's fault
    assert out["w-mid"]["debt"] == 0.75


def test_a_mismatch_on_an_unassisted_attempt_is_weighted_below_assisted(db):
    seed_observation(
        db, "w-miss", ts=NOW, unassisted=1, band=">=95", expected="cat", produced="dog"
    )
    out = comprehension_debt(db, ["w-miss"], now=NOW)["w-miss"]
    assert out["debt"] == 0.75
    assert out["misses"] == 1
    assert out["assisted"] == 0


def test_a_shallow_string_match_is_not_a_miss(db):
    seed_observation(
        db, "w-ok", ts=NOW, unassisted=1, band=">=95",
        expected=" Cat  ", produced="cat",
    )
    out = comprehension_debt(db, ["w-ok"], now=NOW)["w-ok"]
    assert out["misses"] == 0
    assert out["clean"] == 1


def test_an_unassisted_attempt_with_nothing_to_contradict_it_repays(db):
    seed_observation(db, "w-clean", ts=NOW, unassisted=1, band=">=95")
    seed_observation(db, "w-clean", ts=NOW, unassisted=0, band=">=95")
    out = comprehension_debt(db, ["w-clean"], now=NOW)["w-clean"]
    # 1.0 assisted, then -0.5 x 0.5 credit attribution at >=95.
    assert out["debt"] == 0.75
    assert out["clean"] == 1
    assert out["assisted"] == 1


def test_credit_is_stronger_evidence_inside_hard_input(db):
    seed_observation(db, "w-a", ts=NOW, unassisted=1, band=">=95")
    seed_observation(db, "w-b", ts=NOW, unassisted=1, band="<80")
    out = comprehension_debt(db, ["w-a", "w-b"], now=NOW)
    # Both clamp to zero, so the difference is visible against a standing debt.
    assert out["w-a"]["debt"] == 0.0
    assert out["w-b"]["debt"] == 0.0
    seed_observation(db, "w-a", ts=NOW, unassisted=0, band=">=95")
    seed_observation(db, "w-b", ts=NOW, unassisted=0, band=">=95")
    out = comprehension_debt(db, ["w-a", "w-b"], now=NOW)
    assert out["w-a"]["debt"] == 0.75            # 1.0 - 0.5 x 0.5
    assert out["w-b"]["debt"] == 0.5             # 1.0 - 0.5 x 1.0


def test_debt_is_clamped_at_zero(db):
    for _ in range(10):
        seed_observation(db, "w-good", ts=NOW, unassisted=1, band="<80")
    out = comprehension_debt(db, ["w-good"], now=NOW)["w-good"]
    assert out["debt"] == 0.0
    assert out["clean"] == 10


def test_recency_decays_with_the_half_life(db):
    seed_observation(db, "w-old", ts=days_ago(DEBT_HALF_LIFE_DAYS), unassisted=0)
    seed_observation(db, "w-older", ts=days_ago(2 * DEBT_HALF_LIFE_DAYS), unassisted=0)
    out = comprehension_debt(db, ["w-old", "w-older"], now=NOW)
    assert out["w-old"]["debt"] == pytest.approx(0.5)
    assert out["w-older"]["debt"] == pytest.approx(0.25)
    # A shorter half-life ages the same row faster.
    faster = comprehension_debt(db, ["w-old"], now=NOW, half_life_days=15.0)
    assert faster["w-old"]["debt"] == pytest.approx(0.25)


def test_a_timestamp_the_schema_would_not_accept_is_skipped_not_guessed(db):
    """The column's GLOB CHECK passes digit shapes that are not real instants.

    ``2026-13-45T99:99:99Z`` satisfies the schema's pattern and is still not a
    date, so it is counted as skipped rather than folded at a guessed age.
    """
    seed_observation(db, "w-x", ts=NOW, unassisted=0)
    seed_observation(db, "w-x", ts="2026-13-45T99:99:99Z", unassisted=0)
    out = comprehension_debt(db, ["w-x"], now=NOW)["w-x"]
    assert out["skipped_observations"] == 1
    assert out["debt"] == 1.0


def test_a_fresh_cache_is_used_as_is(db):
    seed_observation(db, "w-c", ts=days_ago(10), unassisted=0)
    seed_stat_cache(db, "w-c", debt=4.0, computed_ts=days_ago(5))
    out = comprehension_debt(db, ["w-c"], now=NOW)["w-c"]
    assert out["source"] == DEBT_FROM_CACHE
    assert out["folded_observations"] == 0
    assert out["observations"] == 1
    # 4.0 decayed over 5 days at a 30-day half-life; the observation is not
    # counted a second time.
    assert out["debt"] == pytest.approx(4.0 * 0.5 ** (5 / 30), abs=1e-4)


def test_a_stale_cache_is_a_prior_plus_only_the_observations_after_it(db):
    seed_observation(db, "w-d", ts=days_ago(20), unassisted=0)     # before the cache
    seed_observation(db, "w-d", ts=NOW, unassisted=0, band=">=95")  # after it
    seed_stat_cache(db, "w-d", debt=2.0, computed_ts=days_ago(10))
    out = comprehension_debt(db, ["w-d"], now=NOW)["w-d"]
    assert out["source"] == DEBT_FROM_CACHE_TAIL
    assert out["observations"] == 2
    assert out["folded_observations"] == 1
    assert out["debt"] == pytest.approx(2.0 * 0.5 ** (10 / 30) + 1.0, abs=1e-4)


def test_a_cache_computed_at_the_newest_observation_counts_as_fresh(db):
    seed_observation(db, "w-e", ts=NOW, unassisted=0)
    seed_stat_cache(db, "w-e", debt=1.0, computed_ts=NOW)
    out = comprehension_debt(db, ["w-e"], now=NOW)["w-e"]
    assert out["source"] == DEBT_FROM_CACHE
    assert out["debt"] == 1.0


def test_strength_and_review_count_are_reported_never_folded(db):
    seed_stat_cache(
        db, "w-f", debt=1.0, computed_ts=NOW, strength=99.0, review_count=42,
        last_event_ts=NOW,
    )
    out = comprehension_debt(db, ["w-f"], now=NOW)["w-f"]
    assert out["debt"] == 1.0                    # not 1.0 + 99 + 42
    assert out["cache"] == {
        "comprehension_debt": 1.0,
        "strength": 99.0,
        "review_count": 42,
        "computed_ts": NOW,
        "last_event_ts": NOW,
    }


def test_duplicate_and_blank_ids_are_collapsed(db):
    out = comprehension_debt(db, ["w-a", " w-a ", "", "w-b"], now=NOW)
    assert set(out) == {"w-a", "w-b"}


@pytest.mark.parametrize("bad", ["30", None, True])
def test_half_life_type_misuse_raises(db, bad):
    with pytest.raises(TypeError):
        comprehension_debt(db, ["w-a"], half_life_days=bad)


@pytest.mark.parametrize("bad", [0, -1.0])
def test_non_positive_half_life_raises(db, bad):
    with pytest.raises(ValueError):
        comprehension_debt(db, ["w-a"], half_life_days=bad)


def test_a_now_that_is_not_the_schema_stamp_raises(db):
    with pytest.raises(ValueError):
        comprehension_debt(db, ["w-a"], now="2026-08-20")
    with pytest.raises(TypeError):
        comprehension_debt(db, ["w-a"], now=123)  # type: ignore[arg-type]


# ===========================================================================
# 9. difficulty_for_me
# ===========================================================================


def test_combine_difficulty_is_a_renormalised_weighted_mean():
    components = {
        COMPONENT_COVERAGE: {"available": True, "difficulty": 50.0},
        COMPONENT_READABILITY: {"available": True, "difficulty": 100.0},
        COMPONENT_FREQUENCY: {"available": False, "difficulty": None},
        COMPONENT_JLPT: {"available": False, "difficulty": None},
    }
    out = combine_difficulty(components)
    weight = DIFFICULTY_WEIGHTS[COMPONENT_COVERAGE] + DIFFICULTY_WEIGHTS[
        COMPONENT_READABILITY
    ]
    expected = (
        DIFFICULTY_WEIGHTS[COMPONENT_COVERAGE] * 50.0
        + DIFFICULTY_WEIGHTS[COMPONENT_READABILITY] * 100.0
    ) / weight
    assert out["difficulty"] == pytest.approx(round(expected, 2))
    assert out["weight_used"] == pytest.approx(weight)
    assert out["components_used"] == [COMPONENT_COVERAGE, COMPONENT_READABILITY]
    assert set(out["components_missing"]) == {COMPONENT_FREQUENCY, COMPONENT_JLPT}


def test_combine_difficulty_reports_nothing_when_nothing_answered():
    out = combine_difficulty({name: {"available": False} for name in DIFFICULTY_WEIGHTS})
    assert out["difficulty"] is None
    assert out["weight_used"] == 0.0


def test_combine_difficulty_honours_a_custom_weighting():
    components = {
        COMPONENT_COVERAGE: {"available": True, "difficulty": 0.0},
        COMPONENT_READABILITY: {"available": True, "difficulty": 100.0},
    }
    out = combine_difficulty(components, {COMPONENT_COVERAGE: 0.0})
    assert out["difficulty"] == 100.0            # coverage weighted out entirely
    assert out["components_used"] == [COMPONENT_READABILITY]


def test_unknown_or_non_numeric_weights_are_refused():
    with pytest.raises(ValueError):
        combine_difficulty({}, {"nonsense": 1.0})
    with pytest.raises(TypeError):
        combine_difficulty({}, {COMPONENT_COVERAGE: "heavy"})
    with pytest.raises(TypeError):
        combine_difficulty({}, weights=[1, 2])  # type: ignore[arg-type]


def test_difficulty_degrades_to_coverage_alone_when_no_dataset_is_vendored(db):
    seed_word(db, "w-neko", "猫", known=True)
    result = difficulty_for_me(db, "猫です。", **unavailable_datasets())
    assert result["ok"] is True
    assert result["components_used"] == [COMPONENT_COVERAGE]
    assert set(result["components_missing"]) == {
        COMPONENT_READABILITY, COMPONENT_FREQUENCY, COMPONENT_JLPT
    }
    assert result["weight_used"] == pytest.approx(DIFFICULTY_WEIGHTS[COMPONENT_COVERAGE])
    assert result["difficulty"] == 0.0           # 100% known -> 0 difficulty
    assert "missing" in result["note"]
    for name in (COMPONENT_READABILITY, COMPONENT_FREQUENCY, COMPONENT_JLPT):
        assert result["components"][name]["error"] == DATASET_MISSING
    for dataset in ("jreadability", "bccwj", "jlpt"):
        assert result["datasets"][dataset]["available"] is False


def test_a_checksum_mismatch_degrades_the_score_instead_of_raising(db, monkeypatch):
    """vendor/README.md rule 3 is honoured by the loader; the study loop survives."""
    def boom(**_kwargs):
        raise ChecksumError("bad bytes", expected="a" * 64, actual="b" * 64)

    monkeypatch.setattr(intelligence, "load_readability_model", boom)
    seed_word(db, "w-neko", "猫", known=True)
    stubs = unavailable_datasets()
    stubs.pop("model")                            # let the real loader path run
    result = difficulty_for_me(db, "猫です。", **stubs)
    assert result["ok"] is True
    assert result["components"][COMPONENT_READABILITY]["error"] == DATASET_CHECKSUM
    assert "bad bytes" in result["components"][COMPONENT_READABILITY]["note"]
    assert COMPONENT_READABILITY in result["components_missing"]


def test_no_component_at_all_is_a_value(db):
    result = difficulty_for_me(db, "。。。", **unavailable_datasets())
    assert result["ok"] is False
    assert result["error"] == NO_COMPONENTS
    assert result["components"][COMPONENT_COVERAGE]["available"] is False


def test_difficulty_refuses_bad_weights_and_bad_text(db):
    bad = difficulty_for_me(db, "猫です。", weights={"nonsense": 1.0})
    assert bad["error"] == BAD_WEIGHTS
    assert difficulty_for_me(db, "   ")["error"] == EMPTY_TEXT
    assert difficulty_for_me(db, "猫" * (MAX_COVERAGE_CHARS + 1))["error"] == (
        TEXT_TOO_LARGE
    )
    with pytest.raises(TypeError):
        difficulty_for_me(db)
    with pytest.raises(TypeError):
        difficulty_for_me(db, 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        difficulty_for_me(db, "猫です。", weights={COMPONENT_COVERAGE: "heavy"})


def test_coverage_carries_the_heaviest_weight(db):
    """The learner-level component outweighs any single corpus-level one."""
    assert DIFFICULTY_WEIGHTS[COMPONENT_COVERAGE] == max(DIFFICULTY_WEIGHTS.values())
    seed_word(db, "w-neko", "猫", known=True)
    known_text = difficulty_for_me(db, "猫です。")
    unknown_text = difficulty_for_me(db, "犬です。")
    assert known_text["ok"] and unknown_text["ok"]
    assert unknown_text["difficulty"] > known_text["difficulty"]


def test_difficulty_on_the_real_vendored_datasets(db):
    seed_word(db, "w-neko", "猫", known=True)
    result = difficulty_for_me(db, "猫です。")
    assert result["ok"] is True
    assert result["higher_is_harder"] is True
    assert result["components_used"] == list(DIFFICULTY_WEIGHTS)
    assert result["weight_used"] == 1.0
    assert result["components_missing"] == []
    assert result["note"] is None
    assert 0.0 <= result["difficulty"] <= 100.0
    assert result["components"][COMPONENT_READABILITY]["band"]
    assert result["components"][COMPONENT_JLPT]["hardest_level"] == "N5"
    assert result["chars"] == 4


# ===========================================================================
# 10. difficulty wiring inside find_i_plus_one
# ===========================================================================


def test_score_difficulty_off_reports_nothing_and_costs_no_dataset(gate_world):
    result = find_i_plus_one(
        gate_world,
        [{"text": "猫です。", "grammar_ids": ["g-wa-topic"]}],
        score_difficulty=False,
    )
    assert result["scored_difficulty"] is False
    assert result["difficulty_datasets"] == {}
    assert result["candidates"][0]["difficulty"] is None
    assert result["ranked_by"] == RANKED_BY_DEBT


def test_score_difficulty_on_decorates_without_reordering(gate_world):
    """Difficulty is reported; the ranking stays comprehension debt."""
    seed_word(gate_world, "w-inu", "犬", known=True)
    for _ in range(3):
        seed_observation(gate_world, "w-inu", ts=days_ago(1), unassisted=0)
    candidates = [
        {"text": "猫です。", "grammar_ids": ["g-wa-topic"]},
        {"text": "犬です。", "grammar_ids": ["g-wa-topic"]},
    ]
    plain = find_i_plus_one(
        gate_world, candidates, now=NOW, score_difficulty=False
    )
    scored = find_i_plus_one(gate_world, candidates, now=NOW, score_difficulty=True)
    assert scored["scored_difficulty"] is True
    assert scored["ranked_by"] == RANKED_BY_DEBT
    assert [entry["text"] for entry in scored["candidates"]] == [
        entry["text"] for entry in plain["candidates"]
    ]
    summary = scored["candidates"][0]["difficulty"]
    assert summary["ok"] is True
    assert summary["higher_is_harder"] is True
    assert set(summary["by_component"]) == set(DIFFICULTY_WEIGHTS)
    assert scored["difficulty_datasets"]["bccwj"]["available"] is True


def test_a_missing_dataset_changes_the_note_but_not_the_verdict(
    gate_world, monkeypatch
):
    stubs = unavailable_datasets()
    monkeypatch.setattr(
        intelligence, "_safe_readability_model", lambda: stubs["model"]
    )
    monkeypatch.setattr(
        intelligence, "_safe_frequency_list", lambda: stubs["frequency"]
    )
    monkeypatch.setattr(intelligence, "_safe_jlpt_levels", lambda: stubs["levels"])
    candidates = [{"text": "猫です。", "grammar_ids": ["g-wa-topic"]}]
    degraded = find_i_plus_one(gate_world, candidates, score_difficulty=True)
    assert degraded["ok"] is True
    assert [entry["text"] for entry in degraded["candidates"]] == ["猫です。"]
    assert "difficulty is scored without" in degraded["note"]
    assert "Gating and ranking are unaffected." in degraded["note"]
    summary = degraded["candidates"][0]["difficulty"]
    assert summary["weight_used"] == pytest.approx(
        DIFFICULTY_WEIGHTS[COMPONENT_COVERAGE]
    )
    assert summary["components_used"] == [COMPONENT_COVERAGE]
