"""Tests for :mod:`katagiri.normalizer`.

Two halves, deliberately different in kind.

The **unit half** runs against a hand-built mini-JMdict — the tiny-fixture
approach :mod:`tests.test_jmdict_import` uses, a few dozen entries in a real zip
with a real checksum manifest, imported through the real importer. Every entry in
it exists to pin one rung of the cascade or one shape that has actually produced a
wrong answer: a homograph that only the reading separates, a usually-kana entry
competing with a rare kana-only homophone, an okurigana-reduced spelling, and the
``食物``/``食べ物`` collision where an exact orthographic hit belongs to a
different word. Fixture entries carry the real ``seq`` numbers of the words they
stand for so the fixture and the accuracy set never disagree about identity.

The **accuracy half** runs against the real vendored JMdict — all 218k entries —
and scores the cascade over the 200 labelled morphs in
``src/katagiri/data/morph_labels_200.tsv``. That is the test that actually
protects ``known_word()``: the unit tests say each rung does what it claims, and
only the labelled set says the cascade as a whole gets real words right. It is
**not** skipped by default, because an accuracy target nobody runs is not a
target; it reads a copy of the session-wide JMdict template (``tests/conftest.py``)
rather than reimporting, so the dictionary costs a file copy here. It skips only
when the vendored dictionary is absent, which is the one situation where the test
could not be meaningful. The one ground-zero-scale rebuild in this half —
``populate_lexemes`` over all 218k entries — is marked ``compile`` and runs under
``--public-build`` only; nothing else in the module reads the ``lexeme`` table
(``normalize_morph`` queries the ``jmdict_*`` tables directly).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from katagiri import config as config_mod
from katagiri import db
from katagiri import jmdict_import as jm
from katagiri import normalizer as nz

# ---------------------------------------------------------------------------
# The mini-JMdict fixture
# ---------------------------------------------------------------------------

VERSION = "3.6.2-test"
DICT_DATE = "2026-08-01"


def _kanji(text, *, common=False, tags=None):
    return {"text": text, "common": common, "tags": tags or []}


def _kana(text, *, common=False, tags=None, applies=None):
    return {
        "text": text,
        "common": common,
        "tags": tags or [],
        "appliesToKanji": applies or ["*"],
    }


def _sense(pos, gloss, *, misc=None):
    return {
        "partOfSpeech": list(pos),
        "gloss": [{"lang": "eng", "text": gloss}],
        "misc": list(misc or []),
        "appliesToKanji": ["*"],
        "appliesToKana": ["*"],
    }


def _word(seq, kanji, kana, senses):
    return {"id": str(seq), "kanji": kanji, "kana": kana, "sense": senses}


# Real seq numbers throughout, so a fixture assertion and an accuracy-set label
# can never claim different identities for the same word.
WORDS = [
    # --- plain kanji verb: rungs 3 and 1 ---------------------------------
    _word(
        1358280,
        [_kanji("食べる", common=True), _kanji("喰べる")],
        [_kana("たべる", common=True)],
        [_sense(["v1", "vt"], "to eat")],
    ),
    # --- homograph only the reading separates ----------------------------
    _word(
        1378450,
        [_kanji("生", common=True)],
        [_kana("なま", common=True)],
        [_sense(["adj-no", "n"], "raw; uncooked; fresh")],
    ),
    _word(
        2088240,
        [_kanji("生")],
        [_kana("せい"), _kana("しょう")],
        [_sense(["n"], "life; living")],
    ),
    # --- kana-only adverb: rung 2 ----------------------------------------
    _word(
        1012480,
        [],
        [_kana("もう", common=True), _kana("もー")],
        [_sense(["adv"], "already; yet; by now")],
    ),
    # --- usually-kana entry vs a rare kana-only homophone ----------------
    # The uk entry is filed under a written form nobody uses; counting only
    # headword-less entries as kana words sent コップ to the slang for "cop".
    _word(
        1050390,
        [_kanji("洋杯", common=True), _kanji("洋盃"), _kanji("骨杯")],
        [_kana("コップ", common=True), _kana("コツフ")],
        [_sense(["n"], "glass (drinking vessel); tumbler", misc=["uk"])],
    ),
    _word(
        2846389,
        [],
        [_kana("コップ")],
        [_sense(["n"], "cop; police officer", misc=["sl", "rare"])],
    ),
    # --- okurigana variance: rung 4a needs 書込む -> 書き込む -------------
    _word(
        1343730,
        [_kanji("書き込む", common=True), _kanji("書きこむ")],
        [_kana("かきこむ", common=True)],
        [_sense(["v5m", "vt"], "to fill in (a field, entry, etc.)")],
    ),
    # --- the collision: 食物 is a headword of a *different* word ---------
    _word(
        1358340,
        [_kanji("食べ物", common=True), _kanji("食べもの")],
        [_kana("たべもの", common=True)],
        [_sense(["n"], "food")],
    ),
    _word(
        1358620,
        [_kanji("食物", common=True)],
        [_kana("しょくもつ", common=True)],
        [_sense(["n"], "food; foodstuff")],
    ),
    # --- kana lemma belonging to a kanji word: rung 4b -------------------
    _word(
        1606560,
        [_kanji("分かる", common=True), _kanji("解る"), _kanji("判る")],
        [_kana("わかる", common=True)],
        [_sense(["v5r", "vi"], "to understand; to comprehend")],
    ),
    # --- particle, and the noun the pos filter must keep it away from ----
    _word(
        2028920,
        [],
        [_kana("は", common=True)],
        [_sense(["prt"], "indicates sentence topic")],
    ),
    _word(
        1470400,
        [_kanji("葉", common=True)],
        [_kana("は", common=True)],
        [_sense(["n"], "leaf; blade (of grass); frond")],
    ),
    # --- auxiliary vs adjective-with-an-auxiliary-sense ------------------
    # No pos filter can split these; the orthographic-affinity term does.
    _word(
        2257550,
        [],
        [_kana("ない", common=True)],
        [_sense(["aux-adj", "suf"], "not")],
    ),
    _word(
        1529520,
        [_kanji("無い", common=True)],
        [_kana("ない", common=True)],
        [
            _sense(["adj-i"], "nonexistent; not being (there)", misc=["uk"]),
            _sense(["adj-i", "aux-adj"], "not"),
        ],
    ),
    # --- loanword, for the lemma disambiguator ---------------------------
    _word(
        1049180,
        [_kanji("珈琲")],
        [_kana("コーヒー", common=True)],
        [_sense(["n"], "coffee", misc=["uk"])],
    ),
    # --- an entry with several senses, for populate_lexemes --------------
    _word(
        1516925,
        [_kanji("方", common=True)],
        [_kana("かた", common=True)],
        [
            _sense(["n"], "direction; way"),
            _sense(["n"], "person; lady; gentleman", misc=["hon"]),
            _sense(["n-suf"], "method of; manner of"),
        ],
    ),
    # --- a sense with no English gloss and no pos, for the missing-data path
    _word(
        9999001,
        [_kanji("架空語")],
        [_kana("かくうご")],
        [{"partOfSpeech": [], "gloss": [], "misc": []}],
    ),
    # --- two entries the ranking cannot possibly separate ------------------
    # Invented, and invented on purpose: every ranking term is identical, so only
    # the seq tie-break is left. Real JMdict does not hand us a perfect tie, but
    # the *tie state* is a branch of the confidence logic and needs a fixture that
    # reaches it. These two seqs exist in no dictionary.
    _word(
        9999002,
        [_kanji("同綴", common=True)],
        [_kana("どうてつ", common=True)],
        [_sense(["n"], "contrived homograph A")],
    ),
    _word(
        9999003,
        [_kanji("同綴", common=True)],
        [_kana("どうてつ", common=True)],
        [_sense(["n"], "contrived homograph B")],
    ),
]


def _payload(words):
    return {
        "version": VERSION,
        "languages": ["eng"],
        "commonOnly": False,
        "dictDate": DICT_DATE,
        "dictRevisions": ["1.09"],
        "tags": {"uk": "usually kana", "v1": "Ichidan verb"},
        "words": words,
    }


class Vendor:
    """A tmp checkout-shaped vendor tree: one data file and a manifest."""

    def __init__(self, root):
        self.root = root
        self.jmdict = root / "vendor" / "jmdict" / "jmdict-eng-test.json.zip"
        self.manifest = root / "vendor" / "CHECKSUMS.sha256"
        self.jmdict.parent.mkdir(parents=True, exist_ok=True)

    def write(self, words=None):
        payload = _payload(WORDS if words is None else words)
        with zipfile.ZipFile(self.jmdict, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "jmdict-eng-test.json", json.dumps(payload, ensure_ascii=False)
            )
        digest = hashlib.sha256(self.jmdict.read_bytes()).hexdigest()
        relative = self.jmdict.relative_to(self.root).as_posix()
        self.manifest.write_text(
            f"# test manifest\n{digest}  {relative}\n", encoding="utf-8"
        )


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Keep config, backups and the default db path inside tmp."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def conn(local_app_data):
    connection = db.open_db(local_app_data / "katagiri.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def mini(conn, tmp_path):
    """A migrated database with the mini-JMdict imported."""
    vendor = Vendor(tmp_path / "checkout")
    vendor.write()
    jm.import_jmdict(conn, vendor.jmdict, manifest_path=vendor.manifest)
    return conn


def _best(conn, lemma, reading=None, pos1=None):
    return nz.normalize_morph(conn, lemma, reading, pos1)


# ---------------------------------------------------------------------------
# Kana handling
# ---------------------------------------------------------------------------


def test_katakana_becomes_hiragana():
    assert nz.katakana_to_hiragana("タベル") == "たべる"
    assert nz.katakana_to_hiragana("ニッポン") == "にっぽん"


def test_small_kana_convert_because_they_sit_inside_the_paired_run():
    assert nz.katakana_to_hiragana("キャッキャ") == "きゃっきゃ"
    assert nz.katakana_to_hiragana("ァィゥェォッャュョヮ") == "ぁぃぅぇぉっゃゅょゎ"
    assert nz.katakana_to_hiragana("ヴ") == "ゔ"


def test_the_long_vowel_mark_is_never_converted():
    """ー is written the same in both scripts; arithmetic on it invents a vowel."""
    assert nz.katakana_to_hiragana("コーヒー") == "こーひー"
    assert nz.LONG_VOWEL_MARK in nz.katakana_to_hiragana("ラーメン")
    assert nz.hiragana_to_katakana("こーひー") == "コーヒー"


def test_kana_without_a_hiragana_counterpart_is_left_alone():
    """ヷヸヹヺ have no hiragana; mangling them would be worse than keeping them."""
    assert nz.katakana_to_hiragana("ヷヸヹヺ") == "ヷヸヹヺ"


def test_iteration_marks_convert():
    assert nz.katakana_to_hiragana("ヽヾ") == "ゝゞ"
    assert nz.hiragana_to_katakana("ゝゞ") == "ヽヾ"


def test_hiragana_to_katakana_round_trips():
    for text in ("たべる", "こーひー", "きゃっきゃ", "ぁぃぅ"):
        assert nz.katakana_to_hiragana(nz.hiragana_to_katakana(text)) == text


def test_is_kana_accepts_both_scripts_the_long_mark_and_small_kana():
    for text in ("たべる", "タベル", "コーヒー", "こーひー", "ちょっと", "ヽ"):
        assert nz.is_kana(text), text


def test_is_kana_rejects_kanji_latin_punctuation_and_empty():
    for text in ("食べる", "コーヒーcoffee", "。", "ABC", "コーヒー・ミル", ""):
        assert not nz.is_kana(text), text


def test_canonical_reading_normalizes_strips_and_reports_absence():
    assert nz.canonical_reading("タベル") == "たべる"
    assert nz.canonical_reading("  タベル  ") == "たべる"
    assert nz.canonical_reading(None) is None
    assert nz.canonical_reading("   ") is None


def test_reading_probes_cover_both_scripts_and_deduplicate():
    """JMdict keeps native readings in hiragana and loanwords in katakana."""
    assert nz.reading_probes("タベル") == ("たべる", "タベル")
    assert nz.reading_probes("コーヒー") == ("こーひー", "コーヒー")
    assert nz.reading_probes(None) == ()


def test_kanji_skeleton_deletes_okurigana():
    assert nz.kanji_skeleton("引っ越す") == "引越"
    assert nz.kanji_skeleton("引越す") == "引越"
    assert nz.kanji_skeleton("食べ物") == "食物"
    assert nz.kanji_skeleton("時々") == "時々"
    assert nz.kanji_skeleton("わかる") == ""


def test_unidic_lemma_disambiguators_are_stripped():
    assert nz.unidic_lemma_form("コーヒー-coffee") == "コーヒー"
    assert nz.unidic_lemma_form("私-代名詞") == "私"
    assert nz.unidic_lemma_form("パソコン-personal computer") == "パソコン"
    assert nz.unidic_lemma_form("円-助数詞") == "円"
    assert nz.unidic_lemma_form("食べる") == "食べる"
    assert nz.unidic_lemma_form("  食べる  ") == "食べる"


def test_a_lemma_that_is_only_a_disambiguator_keeps_its_original():
    """An empty head means the guess was wrong; do not return nothing."""
    assert nz.unidic_lemma_form("-代名詞") == "-代名詞"
    assert nz.unidic_lemma_form("") == ""


def test_lexeme_and_morph_id_conventions():
    assert nz.lexeme_id(1358280) == "lx-1358280-1"
    assert nz.lexeme_id(1358280, 3) == "lx-1358280-3"
    assert nz.morph_item_id("食べる") == "morph:食べる"


# ---------------------------------------------------------------------------
# The part-of-speech filter (rung 5)
# ---------------------------------------------------------------------------


def test_a_particle_is_not_allowed_to_be_a_noun():
    assert not nz.pos_compatible("助詞", frozenset({"n"}))
    assert not nz.pos_compatible("助詞", frozenset({"n", "adj-no"}))
    assert nz.pos_compatible("助詞", frozenset({"prt"}))
    assert nz.pos_compatible("助詞", frozenset({"prt", "conj"}))


def test_verbs_and_nouns_accept_their_own_families():
    assert nz.pos_compatible("動詞", frozenset({"v5s", "vt"}))
    assert nz.pos_compatible("動詞", frozenset({"v1", "vi"}))
    assert nz.pos_compatible("名詞", frozenset({"n", "vs"}))
    assert nz.pos_compatible("名詞", frozenset({"n-suf"}))
    assert not nz.pos_compatible("動詞", frozenset({"n"}))


def test_auxiliaries_reject_ordinary_verbs_and_suffixes():
    """ます must not reach the verb 増す, nor ず the pluralizing suffix ズ."""
    assert not nz.pos_compatible("助動詞", frozenset({"v5s", "vi", "vt"}))
    assert not nz.pos_compatible("助動詞", frozenset({"suf"}))
    assert nz.pos_compatible("助動詞", frozenset({"aux-v"}))
    assert nz.pos_compatible("助動詞", frozenset({"cop", "aux-v"}))


def test_symbols_are_compatible_with_nothing():
    for pos1 in ("記号", "補助記号", "空白"):
        assert not nz.pos_compatible(pos1, frozenset({"n"}))
        assert not nz.pos_compatible(pos1, frozenset())


def test_missing_data_never_costs_a_word_its_match():
    assert nz.pos_compatible(None, frozenset({"n"}))
    assert nz.pos_compatible("名詞", frozenset())
    assert nz.pos_compatible("未知の品詞", frozenset({"n"}))


# ---------------------------------------------------------------------------
# populate_lexemes
# ---------------------------------------------------------------------------


def _lexeme(conn, lexeme_id):
    return conn.execute("SELECT * FROM lexeme WHERE id = ?", (lexeme_id,)).fetchone()


def test_populate_lexemes_writes_one_row_per_sense(mini):
    result = nz.populate_lexemes(mini)

    senses = mini.execute("SELECT COUNT(*) FROM jmdict_sense").fetchone()[0]
    assert result.lexemes == senses
    assert result.previous == 0
    assert result.entries == len(WORDS)
    # 方 has three senses, so it contributes three lexemes and no more.
    assert [
        row[0]
        for row in mini.execute(
            "SELECT id FROM lexeme WHERE jmdict_seq = 1516925 ORDER BY sense_idx"
        )
    ] == ["lx-1516925-1", "lx-1516925-2", "lx-1516925-3"]


def test_lexeme_rows_carry_headword_reading_pos_gloss_and_version(mini):
    nz.populate_lexemes(mini)

    row = _lexeme(mini, "lx-1358280-1")
    assert row["jmdict_seq"] == 1358280
    assert row["sense_idx"] == 1
    assert row["headword"] == "食べる"
    assert row["reading"] == "たべる"
    assert row["pos"] == "v1,vt"
    assert row["gloss_en"] == "to eat"
    assert row["dict_version"] == VERSION


def test_a_kana_only_entry_uses_its_reading_as_the_headword(mini):
    """もう has no written form; its kana *is* its headword."""
    nz.populate_lexemes(mini)

    row = _lexeme(mini, "lx-1012480-1")
    assert row["headword"] == "もう"
    assert row["reading"] == "もう"


def test_the_headword_is_the_first_written_form_upstream_listed(mini):
    nz.populate_lexemes(mini)

    assert _lexeme(mini, "lx-1606560-1")["headword"] == "分かる"
    assert _lexeme(mini, "lx-1050390-1")["headword"] == "洋杯"


def test_a_sense_with_no_pos_or_gloss_still_gets_its_lexeme(mini):
    nz.populate_lexemes(mini)

    row = _lexeme(mini, "lx-9999001-1")
    assert row is not None
    assert row["pos"] is None
    assert row["gloss_en"] is None


def test_populate_lexemes_is_idempotent(mini):
    first = nz.populate_lexemes(mini)
    second = nz.populate_lexemes(mini)

    assert second.lexemes == first.lexemes
    assert second.previous == first.lexemes


def test_populate_lexemes_drops_rows_a_reimport_removed(mini, tmp_path):
    nz.populate_lexemes(mini)
    assert _lexeme(mini, "lx-1358280-1") is not None

    smaller = Vendor(tmp_path / "smaller")
    smaller.write([WORDS[3]])  # もう only
    jm.import_jmdict(mini, smaller.jmdict, manifest_path=smaller.manifest)
    result = nz.populate_lexemes(mini)

    assert result.lexemes == 1
    assert _lexeme(mini, "lx-1358280-1") is None
    assert _lexeme(mini, "lx-1012480-1") is not None


def test_populate_lexemes_refuses_to_empty_itself_when_jmdict_is_empty(mini):
    """An unimported dictionary must not silently erase the lexeme table."""
    nz.populate_lexemes(mini)
    before = mini.execute("SELECT COUNT(*) FROM lexeme").fetchone()[0]
    for table in jm.JMDICT_TABLES:
        mini.execute(f"DELETE FROM {table}")

    with pytest.raises(ValueError, match="refusing to rebuild"):
        nz.populate_lexemes(mini)

    assert mini.execute("SELECT COUNT(*) FROM lexeme").fetchone()[0] == before


def test_populate_lexemes_on_an_empty_database_is_allowed(conn):
    """Nothing to lose, so an empty rebuild is not a data-loss risk."""
    result = nz.populate_lexemes(conn)

    assert result.lexemes == 0
    assert result.previous == 0


def test_a_failure_mid_rebuild_leaves_the_previous_population_intact(mini):
    """DELETE and INSERT share one transaction, so a failed rebuild loses nothing.

    A trigger that aborts every insert is the cleanest way to fail the rebuild at
    exactly the interesting moment: the old rows are already deleted and the new
    ones are going in.
    """
    nz.populate_lexemes(mini)
    before = [row[0] for row in mini.execute("SELECT id FROM lexeme ORDER BY id")]
    assert before

    mini.execute(
        "CREATE TRIGGER refuse_lexeme BEFORE INSERT ON lexeme "
        "BEGIN SELECT RAISE(ABORT, 'no inserts today'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            nz.populate_lexemes(mini)
    finally:
        mini.execute("DROP TRIGGER refuse_lexeme")

    assert [row[0] for row in mini.execute("SELECT id FROM lexeme ORDER BY id")] == before


# ---------------------------------------------------------------------------
# The cascade, rung by rung
# ---------------------------------------------------------------------------


def test_rung3_kanji_and_reading_agree(mini):
    result = _best(mini, "食べる", "タベル", "動詞")

    assert result.best == 1358280
    assert result.matches[0][1] == "kanji_reading"
    assert result.matches[0][2] == pytest.approx(0.97)


def test_rung3_separates_a_homograph_by_its_reading(mini):
    """生 is なま and せい in unrelated entries; only the reading tells them apart."""
    assert _best(mini, "生", "ナマ", "名詞").best == 1378450
    assert _best(mini, "生", "セイ", "名詞").best == 2088240


def test_a_reading_in_either_script_works(mini):
    """UniDic gives katakana; a caller passing hiragana must not be punished."""
    assert _best(mini, "食べる", "たべる", "動詞").best == 1358280
    assert _best(mini, "食べる", "タベル", "動詞").best == 1358280


def test_rung1_kanji_only_when_no_reading_is_supplied(mini):
    result = _best(mini, "食べる", None, "動詞")

    assert result.best == 1358280
    assert result.matches[0][1] == "kanji_exact"
    assert result.matches[0][2] == pytest.approx(0.90)


def test_rung1_is_penalized_when_the_reading_agreed_with_nothing(mini):
    """A contradicted reading is evidence against the match, so say so."""
    result = _best(mini, "食べる", "ゼンゼンチガウ", "動詞")

    assert result.best == 1358280
    assert result.matches[0][1] == "kanji_exact"
    assert result.matches[0][2] < 0.90


def test_rung2_a_kana_lemma_reaches_a_kana_only_entry(mini):
    result = _best(mini, "もう", "モウ", "副詞")

    assert result.best == 1012480
    assert result.matches[0][1] == "reading_exact"
    assert result.matches[0][2] == pytest.approx(0.90)


def test_rung2_counts_a_usually_kana_entry_as_a_kana_word(mini):
    """コップ is filed under 洋杯. Skipping uk entries here picked "cop" instead."""
    result = _best(mini, "コップ-kop", "コップ", "名詞")

    assert result.best == 1050390
    assert result.matches[0][1] == "reading_exact"
    assert 2846389 in [seq for seq, _, _ in result.matches]


def test_rung4a_matches_an_okurigana_reduced_spelling(mini):
    """書込む is real orthography that JMdict does not list; the skeleton finds it."""
    result = _best(mini, "書込む", "カキコム", "動詞")

    assert result.best == 1343730
    assert result.matches[0][1] == "variant_okurigana"
    assert result.matches[0][2] == pytest.approx(0.80)


def test_rung4a_beats_an_exact_orthographic_hit_the_reading_contradicts(mini):
    """食物 is JMdict's headword for しょくもつ. Read タベモノ it is 食べ物."""
    result = _best(mini, "食物", "タベモノ", "名詞")

    assert result.best == 1358340
    assert result.matches[0][1] == "variant_okurigana"


def test_an_exact_orthographic_hit_still_wins_when_the_reading_agrees(mini):
    assert _best(mini, "食物", "ショクモツ", "名詞").best == 1358620


def test_rung4a_works_without_a_reading_via_the_prefix_sweep(mini):
    result = _best(mini, "書込む", None, "動詞")

    assert result.best == 1343730
    assert result.matches[0][1] == "variant_okurigana"


def test_rung4b_a_kana_lemma_reaches_a_kanji_word(mini):
    """わかる is how 分かる is often written; nothing orthographic agrees."""
    result = _best(mini, "わかる", "ワカル", "動詞")

    assert result.best == 1606560
    assert result.matches[0][1] == "kana_fallback"
    assert result.matches[0][2] == pytest.approx(0.65)


def test_the_lemma_disambiguator_is_stripped_before_matching(mini):
    """Without stripping, コーヒー-coffee matches no headword in any dictionary."""
    result = _best(mini, "コーヒー-coffee", "コーヒー", "名詞")

    assert result.best == 1049180
    assert result.matches[0][1] == "reading_exact"


# ---------------------------------------------------------------------------
# The cascade: filtering, ranking and honest emptiness
# ---------------------------------------------------------------------------


def test_the_pos_filter_keeps_a_particle_off_a_noun_entry(mini):
    """は is both the topic particle and 葉 "leaf"; pos1 decides."""
    assert _best(mini, "は", "ハ", "助詞").best == 2028920
    assert _best(mini, "葉", "ハ", "名詞").best == 1470400


def test_pos1_steers_the_cascade_for_one_and_the_same_lemma(mini):
    """The kana lemma は is the particle or 葉 "leaf"; only pos1 can say which."""
    as_particle = _best(mini, "は", "ハ", "助詞")
    as_noun = _best(mini, "は", "ハ", "名詞")

    assert as_particle.best == 2028920
    assert as_particle.matches[0][1] == "reading_exact"
    assert as_noun.best == 1470400
    assert as_noun.matches[0][1] == "kana_fallback"


def test_without_a_pos1_a_kana_lemma_still_resolves(mini):
    """No pos1 filters nothing, so the kana word wins on being a kana word."""
    result = _best(mini, "は", "ハ", None)

    assert result.best == 2028920


def test_punctuation_maps_to_nothing_and_that_is_the_right_answer(mini):
    result = _best(mini, "。", None, "補助記号")

    assert result.best is None
    assert result.matches == []


def test_an_unknown_word_and_an_empty_lemma_both_yield_nothing(mini):
    assert _best(mini, "存在しない単語", "ソンザイシナイタンゴ", "名詞").best is None
    assert _best(mini, "", "タベル", "動詞").best is None
    assert _best(mini, "   ", None, None).matches == []


def test_the_headline_spelling_outranks_commonness(mini):
    """Identity beats frequency; 無い is common but ない is the auxiliary's own entry."""
    result = _best(mini, "ない", "ナイ", "助動詞")

    assert result.best == 2257550


def test_matches_are_ordered_best_first_with_decaying_confidence(mini):
    result = _best(mini, "コップ", "コップ", "名詞")

    assert len(result.matches) > 1
    assert result.best == result.matches[0][0]
    confidences = [confidence for _, _, confidence in result.matches]
    assert confidences == sorted(confidences, reverse=True)


def test_every_match_in_a_result_shares_the_rung_that_produced_it(mini):
    result = _best(mini, "コップ", "コップ", "名詞")

    methods = {method for _, method, _ in result.matches}
    assert len(methods) == 1
    assert methods <= set(nz.METHODS)


def test_confidence_is_reduced_when_the_ranking_cannot_separate_two_entries(mini):
    """A tie means the ranking failed, and the caller deserves to know that."""
    untied = _best(mini, "食べる", "タベル", "動詞")
    tied = _best(mini, "同綴", "ドウテツ", "名詞")

    assert untied.matches[0][1] == tied.matches[0][1] == "kanji_reading"
    assert len(tied.matches) == 2
    assert tied.matches[0][2] < untied.matches[0][2]


def test_a_tie_is_still_broken_deterministically_by_the_lower_seq(mini):
    """Reduced confidence is not an excuse for a non-reproducible answer."""
    first = _best(mini, "同綴", "ドウテツ", "名詞")
    again = _best(mini, "同綴", "ドウテツ", "名詞")

    assert first.best == 9999002
    assert first.matches == again.matches


# ---------------------------------------------------------------------------
# map_known_morphs
# ---------------------------------------------------------------------------


def _create_morphs_table(conn, rows):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ankimorphs_morphs (
            lemma          TEXT NOT NULL,
            inflection     TEXT NOT NULL DEFAULT '',
            lemma_ivl      INTEGER,
            inflection_ivl INTEGER,
            source         TEXT CHECK(source IN ('db','csv')),
            imported_ts    TEXT,
            PRIMARY KEY (lemma, inflection, source)
        )
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO ankimorphs_morphs"
        "(lemma, inflection, lemma_ivl, inflection_ivl, source, imported_ts) "
        "VALUES (?, ?, ?, ?, 'db', '2026-08-19T00:00:00Z')",
        rows,
    )


def _map_rows(conn):
    return {
        row["item_id"]: dict(row)
        for row in conn.execute("SELECT * FROM morph_lexeme_map")
    }


def test_map_known_morphs_without_the_table_is_empty_not_an_error(mini):
    """Nothing has been ingested yet. That is a state, not a failure."""
    result = nz.map_known_morphs(mini)

    assert result.mapped == 0
    assert result.unmapped == 0
    assert result.by_method == {}


def test_map_known_morphs_writes_the_crosswalk(mini):
    _create_morphs_table(
        mini, [("食べる", "食べ", 30, 30), ("もう", "もう", 40, 40)]
    )

    result = nz.map_known_morphs(mini)

    assert result.mapped == 2
    rows = _map_rows(mini)
    assert rows["morph:食べる"]["lexeme_id"] == "lx-1358280-1"
    assert rows["morph:食べる"]["surface"] == "食べる"
    assert rows["morph:食べる"]["method"] == "kanji_exact"
    assert 0 < rows["morph:食べる"]["confidence"] <= 1
    assert rows["morph:もう"]["lexeme_id"] == "lx-1012480-1"


def test_entry_level_links_target_sense_one(mini):
    """A morph identifies an entry, not a sense; sense 1 is the documented stand-in."""
    _create_morphs_table(mini, [("方", "方", 30, 30)])

    nz.map_known_morphs(mini)

    assert _map_rows(mini)["morph:方"]["lexeme_id"] == "lx-1516925-1"


def test_the_same_lemma_under_several_inflections_is_mapped_once(mini):
    _create_morphs_table(
        mini,
        [("食べる", "食べ", 30, 30), ("食べる", "食べた", 30, 25), ("食べる", "", 30, None)],
    )

    result = nz.map_known_morphs(mini)

    assert result.mapped == 1
    assert result.by_method == {"kanji_exact": 1}


def test_supplied_readings_sharpen_a_homograph(mini):
    """AnkiMorphs records no reading, which is exactly what 生 needs."""
    _create_morphs_table(mini, [("生", "生", 30, 30)])

    with_reading = nz.map_known_morphs(mini, readings={"生": "セイ"})

    assert with_reading.mapped == 1
    assert _map_rows(mini)["morph:生"]["lexeme_id"] == "lx-2088240-1"
    assert _map_rows(mini)["morph:生"]["method"] == "kanji_reading"


def test_a_reading_lookup_callable_is_consulted_when_no_mapping_has_the_lemma(mini):
    _create_morphs_table(mini, [("生", "生", 30, 30)])

    nz.map_known_morphs(mini, reading_lookup=lambda lemma: "ナマ")

    assert _map_rows(mini)["morph:生"]["lexeme_id"] == "lx-1378450-1"


def test_a_supplied_pos1_filters_the_candidates(mini):
    _create_morphs_table(mini, [("は", "は", 30, 30)])

    nz.map_known_morphs(mini, readings={"は": "ハ"}, pos1_by_lemma={"は": "助詞"})

    assert _map_rows(mini)["morph:は"]["lexeme_id"] == "lx-2028920-1"


def test_unmappable_lemmas_are_reported_not_hidden(mini):
    _create_morphs_table(
        mini, [("食べる", "食べ", 30, 30), ("存在しない単語", "", 30, None)]
    )

    result = nz.map_known_morphs(mini)

    assert result.mapped == 1
    assert result.unmapped == 1
    assert result.unmapped_lemmas == ("存在しない単語",)
    assert "morph:存在しない単語" not in _map_rows(mini)


def test_the_rebuild_replaces_this_modules_rows_and_no_others(mini):
    """A link another importer made from a real item must survive the rebuild."""
    mini.execute(
        "INSERT INTO morph_lexeme_map(item_id, lexeme_id, surface, method, confidence)"
        " VALUES ('it-0001', 'lx-1358280-1', '食べた', 'anki_field', 0.5)"
    )
    _create_morphs_table(mini, [("食べる", "食べ", 30, 30), ("もう", "", 40, None)])
    nz.map_known_morphs(mini)

    mini.execute("DELETE FROM ankimorphs_morphs WHERE lemma = 'もう'")
    result = nz.map_known_morphs(mini)

    rows = _map_rows(mini)
    assert result.previous == 2
    assert result.mapped == 1
    assert "morph:もう" not in rows
    assert rows["it-0001"]["method"] == "anki_field"


def test_map_known_morphs_counts_the_rungs_it_used(mini):
    _create_morphs_table(
        mini,
        [("食べる", "", 30, None), ("もう", "", 30, None), ("わかる", "", 30, None)],
    )

    result = nz.map_known_morphs(mini)

    assert result.mapped == 3
    assert sum(result.by_method.values()) == 3
    assert set(result.by_method) <= set(nz.METHODS)


# ---------------------------------------------------------------------------
# The accuracy set, against the real vendored JMdict
# ---------------------------------------------------------------------------

LABELS_PATH = Path(nz.__file__).with_name("data") / "morph_labels_200.tsv"
ACCURACY_TARGET = 0.90


def _load_labels():
    """Parse the accuracy set: (lemma, reading, pos1, expected_seq, category)."""
    rows = []
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or line.startswith("lemma\t"):
            continue
        lemma, reading, pos1, seq, note = line.split("\t")
        rows.append(
            (
                lemma,
                reading or None,
                pos1 or None,
                int(seq) if seq else None,
                note.split(":", 1)[0],
            )
        )
    return rows


def test_the_accuracy_set_is_well_formed_and_the_expected_size():
    rows = _load_labels()

    assert len(rows) == 200
    assert len({(lemma, reading, pos1) for lemma, reading, pos1, _, _ in rows}) == 200
    # Every category the task asked for is represented.
    categories = {category for *_, category in rows}
    assert categories == {
        "noun",
        "verb",
        "kana",
        "okurigana",
        "homograph",
        "suru",
        "particle",
        "tricky",
    }
    # Rows whose correct answer is "no map" are a deliberate part of the set.
    assert any(seq is None for *_, seq, _ in ((*r[:3], r[3], r[4]) for r in rows))


@pytest.fixture(scope="session")
def real_jmdict(real_jmdict_template, tmp_path_factory):
    """The real vendored JMdict in a scratch database, shared by this module.

    A copy of the session template (see ``tests/conftest.py``), so the 218k-entry
    import is not repaid here: normal runs copy the cached file, and only
    ``--public-build`` imports from ground zero. Writes made by tests below land
    in this module's copy and never touch the template. Skips, via the template
    fixture, when the vendored dictionary is absent.
    """
    path = real_jmdict_template.materialize(
        tmp_path_factory.mktemp("real_jmdict") / "jmdict.db"
    )
    connection = db.open_db(path)
    try:
        yield connection
    finally:
        connection.close()


def test_the_real_import_is_the_dictionary_we_think_it_is(real_jmdict):
    """The dictionary under the accuracy half is present and the size we expect."""
    entries = real_jmdict.execute("SELECT COUNT(*) FROM jmdict_entry").fetchone()[0]

    assert entries > 200_000


@pytest.mark.compile
def test_populate_lexemes_scales_to_the_whole_dictionary(real_jmdict):
    """A ~10s full rebuild; nothing else here reads ``lexeme``, so it is compile-only."""
    result = nz.populate_lexemes(real_jmdict)

    senses = real_jmdict.execute("SELECT COUNT(*) FROM jmdict_sense").fetchone()[0]
    assert result.lexemes == senses
    assert result.lexemes > 200_000
    # The id convention holds for every row, not just the ones tested by hand.
    mismatched = real_jmdict.execute(
        "SELECT COUNT(*) FROM lexeme "
        "WHERE id <> 'lx-' || jmdict_seq || '-' || sense_idx"
    ).fetchone()[0]
    assert mismatched == 0


def test_accuracy_target(real_jmdict):
    """Score the whole cascade over 200 labelled morphs from the real dictionary.

    This is the test that protects ``known_word()``. The unit tests above say each
    rung does what it claims; only this one says the cascade gets real words right.
    """
    rows = _load_labels()
    totals: dict[str, int] = {}
    correct: dict[str, int] = {}
    failures = []

    for lemma, reading, pos1, expected, category in rows:
        result = nz.normalize_morph(real_jmdict, lemma, reading, pos1)
        totals[category] = totals.get(category, 0) + 1
        if result.best == expected:
            correct[category] = correct.get(category, 0) + 1
        else:
            method = result.matches[0][1] if result.matches else "no-match"
            failures.append(
                f"    [{category}] {lemma} / {reading or '-'} / {pos1 or '-'}: "
                f"expected {expected}, got {result.best} via {method}"
            )

    scored = sum(totals.values())
    hits = sum(correct.values())
    accuracy = hits / scored

    breakdown = "\n".join(
        f"    {category:10s} {correct.get(category, 0):3d}/{totals[category]:3d} "
        f"= {correct.get(category, 0) / totals[category]:.3f}"
        for category in sorted(totals)
    )
    message = (
        f"morph->lexeme accuracy {hits}/{scored} = {accuracy:.4f}, "
        f"below the {ACCURACY_TARGET:.2f} target.\n"
        f"  per category:\n{breakdown}\n"
        f"  failures:\n" + "\n".join(failures)
    )
    assert accuracy >= ACCURACY_TARGET, message


@pytest.mark.parametrize(
    ("lemma", "reading", "pos1", "expected", "why"),
    [
        ("ます", "マス", "助動詞", 2210290, "must not reach the ordinary verb 増す"),
        ("ず", "ズ", "助動詞", 2829645, "must not reach the pluralizing suffix ズ"),
        ("食物", "タベモノ", "名詞", 1358340, "orthographic hit belongs to しょくもつ"),
        ("コップ-kop", "コップ", "名詞", 1050390, "uk entry, not the slang for 'cop'"),
        ("ズボン-jupon", "ズボン", "名詞", 1074260, "uk entry, not the adverb"),
        ("ない", "ナイ", "助動詞", 2257550, "the auxiliary, not the adjective 無い"),
        ("て", "テ", "助詞", 2654270, "conjunctive て, not the common quotative って"),
        ("生", "ナマ", "名詞", 1378450, "homograph resolved by reading"),
        ("生", "セイ", "名詞", 2088240, "same kanji, different entry"),
        ("私-代名詞", "ワタクシ", "代名詞", 2842390, "わたくし, not the common わたし"),
    ],
)
def test_known_hard_cases_stay_fixed(real_jmdict, lemma, reading, pos1, expected, why):
    """Each of these was a real wrong answer during development. Regression guards."""
    result = nz.normalize_morph(real_jmdict, lemma, reading, pos1)

    assert result.best == expected, f"{lemma}/{reading}: {why} (got {result.best})"


def test_every_expected_seq_in_the_accuracy_set_exists_upstream(real_jmdict):
    """No invented seq numbers: the whole point of building the set against real data."""
    missing = [
        (lemma, seq)
        for lemma, _, _, seq, _ in _load_labels()
        if seq is not None
        and real_jmdict.execute(
            "SELECT 1 FROM jmdict_entry WHERE seq = ?", (seq,)
        ).fetchone()
        is None
    ]

    assert missing == []
