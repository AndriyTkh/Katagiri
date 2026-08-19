"""Morph -> lexeme normalization: the bridge between UniDic and JMdict.

Everything downstream that asks "does the learner know this word?" depends on
this module getting one thing right: the morph UniDic handed us and the JMdict
entry a human would point at are the *same word*. The two sides disagree about
orthography constantly, so the join cannot be a single equality:

* UniDic hands back a **lemma** (dictionary form, e.g. 食べ -> ``食べる``) and a
  **lemma reading** in katakana (``タベル``). JMdict stores readings in
  hiragana (``たべる``) — except for loanwords, which it stores in katakana
  (``コーヒー``). So neither side can be normalized *towards* the other; both
  are normalized to a canonical form, and readings are probed in both scripts.
* Okurigana is not fixed. UniDic's ``引っ越す`` is JMdict's ``引っ越す`` *and*
  ``引越す``; UniDic's ``分かる`` is also written ``わかる``. A headword string
  match therefore misses real words, and a reading-only match over-matches
  wildly (``生`` alone has dozens of readings across dozens of entries).
* A kana lemma may be a genuinely kana-only word (``よく``, ``もう``) or the
  kana spelling of a kanji word (``わかる`` -> ``分かる``). Those are different
  situations and are answered by different rungs at different confidence.

* UniDic's lemma is not always a bare written form. Where one spelling covers
  several lemmas it appends a disambiguator — ``私-代名詞``, ``コーヒー-coffee`` —
  which appears in no dictionary on earth. :func:`unidic_lemma_form` strips it
  before matching starts; without that step every loanword in the language
  reaches only the loosest rung.

Hence a **cascade**: rungs are tried strongest-evidence-first and the first
rung that produces a part-of-speech-plausible candidate wins. Every match
carries the name of the rung that produced it, so a wrong link is always
traceable to the rule that made it rather than to "the matcher".

The cascade
-----------
Listed here in *execution* order, with the task's rung numbers in brackets.
Execution order is by strength of evidence, which is not the same as the
numbering: agreement on both axes (rung 3) is strictly better evidence than
agreement on one (rungs 1 and 2), so it runs first.

``kanji_reading``   [rung 3]
    ``jmdict_kanji.kanji == lemma`` **and** one of the entry's readings agrees
    with the morph reading. This is the rung that resolves homographs: ``生``
    is ``せい``/``なま``/``き`` across unrelated entries and only the reading
    separates them. Requires a reading; skipped without one.

``reading_exact``   [rung 2]
    The lemma is all kana and matches ``jmdict_reading.reading`` of an entry that
    **is a kana word**: either it has no written form at all, or JMdict tags it
    ``uk`` (usually written in kana), which covers the many loanwords filed under
    a headword nobody uses — コップ under ``洋杯``, ズボン under ``洋袴``. Counting
    only headword-less entries here was a real source of wrong answers: the
    common ``uk`` entry was skipped and a rare kana-only homophone won instead,
    sending コップ to the slang for "police officer". Kana lemmas belonging to an
    ordinary kanji word are still left to the bottom rung, which is where a kana
    spelling of a kanji word belongs.

``variant_okurigana`` [rung 4a]
    Orthographic variance. The lemma's *kanji skeleton* (the lemma with all kana
    deleted, e.g. ``引っ越す`` -> ``引越``) is compared against the skeleton of
    every ``jmdict_kanji`` form of the entry, so ``引っ越す``/``引越す``/
    ``引っ超す`` collapse to one key. Reading agreement is required when a
    reading is available, because a bare skeleton is a weak key — ``行`` alone
    is shared by ``行く``, ``行う`` and ``行``.

    With a reading in hand this rung runs *before* ``kanji_exact``. Reaching it
    while an orthographic hit already exists means that hit is a homograph
    collision whose readings all contradict the morph: ``食物`` is JMdict's
    headword for ``しょくもつ``, but a morph written ``食物`` and read ``タベモノ``
    is ``食べ物``, which shares the skeleton ``食物``. Skeleton plus reading
    agreement is better evidence than orthography with a contradicted reading.

``kanji_exact``     [rung 1]
    ``jmdict_kanji.kanji == lemma``, orthography only. Reached when no reading
    was supplied, or when a reading was supplied and nothing agreed with it on
    either rung above. The latter is a weak claim — the reading is evidence
    *against* the match — and it is penalized in the confidence rather than
    suppressed, because JMdict's reading coverage is not exhaustive and a
    contradicted reading is still better than no answer.

``kana_fallback``   [rung 4b]
    Reading agreement and nothing else: the morph's reading (or its kana lemma)
    matches an entry's reading, with no orthographic agreement. This is how
    ``わかる`` reaches ``分かる`` and how a kanji lemma reaches a usually-kana
    entry. It is the loosest rung and is scored accordingly.

``pos filter``      [rung 5]
    Not a rung — a filter applied to the candidates of every rung. A coarse
    UniDic-``pos1`` -> JMdict-``pos`` compatibility map drops candidates whose
    senses cannot be the morph's part of speech, which is what stops a 助詞 from
    landing on a noun entry. It is deliberately **permissive**: it only rejects
    when the two part-of-speech systems clearly contradict each other, since a
    false rejection silently loses a real word. Symbol-ish ``pos1`` values
    (``記号``, ``補助記号``, ``空白``) reject everything — mapping punctuation to
    a dictionary entry is never right. A ``pos1`` the map has never heard of
    filters nothing, and a sense with no ``pos`` recorded is treated as
    compatible: missing data is not evidence.

Ranking and confidence
----------------------
Within the winning rung candidates are ordered by whether the lemma is the
entry's *headline* spelling rather than one of its variants, then by
``is_common``, then by whether the lemma appears among the forms at all, then by
orthographic affinity (a kanji lemma prefers an entry with many recorded
spellings; a kana lemma prefers one with none), then by ascending ``seq`` so the
answer is deterministic. :func:`_rank_key` documents why each term is where it
is, including why identity outranks frequency. ``confidence`` is the
rung's base score, reduced when the top of the ranking is a tie — an unbroken tie
means the ranking could not separate two entries and the caller should know.

Sense granularity
-----------------
``lexeme`` is keyed ``(jmdict_seq, sense_idx)`` with ids ``lx-<seq>-<sense_idx>``
and ``sense_idx`` 1-based, matching :mod:`katagiri.jmdict_import`. Matching here
is **entry**-level: a morph identifies an entry, not one of its senses, so links
target ``sense_idx = 1``. Choosing the sense a given occurrence meant needs the
surrounding sentence and is future work; the id convention already has room for
it, so a later sense disambiguator can rewrite the crosswalk without a schema
change. Callers must not read "sense 1" as a claim about meaning.

Both writers are **derived rebuilds**, single-transaction, in the house style:
:func:`populate_lexemes` replaces every ``lexeme`` row, and
:func:`map_known_morphs` replaces every ``morph_lexeme_map`` row it owns (those
whose ``item_id`` carries the ``morph:`` prefix), leaving links made by other
importers alone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Final, Iterable, Mapping, Sequence

from katagiri.logging_setup import get_logger

_logger = get_logger("normalizer")

# ---------------------------------------------------------------------------
# Identity conventions
# ---------------------------------------------------------------------------

LEXEME_ID_PREFIX: Final = "lx-"
#: Entry-level links point at the first sense; see the module docstring.
DEFAULT_SENSE_IDX: Final = 1
#: ``morph_lexeme_map.item_id`` for a link made from a bare morph rather than
#: from an ``item`` row. Keeps this module's rows identifiable so the rebuild
#: can delete exactly its own and no one else's.
MORPH_ITEM_PREFIX: Final = "morph:"

MORPHS_TABLE: Final = "ankimorphs_morphs"
MAP_TABLE: Final = "morph_lexeme_map"


def lexeme_id(seq: int, sense_idx: int = DEFAULT_SENSE_IDX) -> str:
    """``lx-<seq>-<sense_idx>``, the ``lexeme.id`` convention (sense 1-based)."""
    return f"{LEXEME_ID_PREFIX}{int(seq)}-{int(sense_idx)}"


def morph_item_id(lemma: str) -> str:
    """``morph:<lemma>`` — the synthetic ``item_id`` for a morph-only link."""
    return f"{MORPH_ITEM_PREFIX}{lemma}"


# UniDic 3.1's ``lemma`` field is not always a bare written form: when one
# orthography covers several lemmas it appends a disambiguator after an ASCII
# hyphen. The two shapes seen in practice are a part-of-speech label
# (``私-代名詞``) and, for loanwords, the source word (``コーヒー-coffee``,
# ``パソコン-personal computer``). Neither suffix exists in JMdict, so a lemma
# carrying one matches no headword at all and silently degrades to the weakest
# rung — which is precisely the kind of quiet miss this module exists to prevent.
_LEMMA_DISAMBIGUATOR: Final = "-"


def unidic_lemma_form(lemma: str) -> str:
    """The written form inside a UniDic lemma, with any disambiguator removed.

    ``コーヒー-coffee`` -> ``コーヒー``, ``私-代名詞`` -> ``私``, and a lemma with
    no suffix is returned unchanged. Splitting on the *first* hyphen is safe
    because Japanese orthography does not use the ASCII hyphen; a head that comes
    out empty means the guess was wrong, and the original is kept.
    """
    text = (lemma or "").strip()
    if _LEMMA_DISAMBIGUATOR not in text:
        return text
    head = text.split(_LEMMA_DISAMBIGUATOR, 1)[0].strip()
    return head or text


# ---------------------------------------------------------------------------
# Kana
# ---------------------------------------------------------------------------

# Katakana and hiragana are two parallel runs of code points a fixed 0x60 apart,
# but only over part of their blocks. Mapping the whole block by arithmetic is
# the classic bug here: it turns ー (U+30FC) into a hiragana code point that is
# not a long-vowel mark, and it invents hiragana for ヷヸヹヺ, which have none.
_KATA_RUN_START: Final = 0x30A1  # ァ
_KATA_RUN_END: Final = 0x30F6  # ヶ  (covers small kana ァィゥェォッャュョヮ and ヴ)
_HIRA_RUN_START: Final = 0x3041  # ぁ
_HIRA_RUN_END: Final = 0x3096  # ゖ
_KANA_DELTA: Final = _KATA_RUN_START - _HIRA_RUN_START  # 0x60

# Iteration marks pair up too, one run further along.
_KATA_ITERATION: Final = (0x30FD, 0x30FE)  # ヽヾ
_HIRA_ITERATION: Final = (0x309D, 0x309E)  # ゝゞ

#: The long-vowel mark. Script-neutral: it is written ー in both scripts and is
#: never converted. ``コーヒー`` -> ``こーひー`` keeps it, which is exactly what
#: makes the hiragana probe comparable with a hiragana JMdict reading.
LONG_VOWEL_MARK: Final = "ー"

_KATA_TO_HIRA: Final = {
    **{code: code - _KANA_DELTA for code in range(_KATA_RUN_START, _KATA_RUN_END + 1)},
    **dict(zip(_KATA_ITERATION, _HIRA_ITERATION)),
}
_HIRA_TO_KATA: Final = {
    **{code: code + _KANA_DELTA for code in range(_HIRA_RUN_START, _HIRA_RUN_END + 1)},
    **dict(zip(_HIRA_ITERATION, _KATA_ITERATION)),
}

# Kana-ness for classification: both syllabaries, the small kana, the long-vowel
# mark and the iteration marks — but not ・ (U+30FB), which is a separator.
_KATAKANA_MIDDLE_DOT: Final = 0x30FB


def katakana_to_hiragana(text: str) -> str:
    """Katakana -> hiragana, leaving ー, ヷヸヹヺ and non-kana untouched.

    Small kana convert correctly (ッ -> っ, ャ -> ゃ) because they sit inside the
    paired run. ヴ -> ゔ likewise. The four ヷヸヹヺ have no hiragana at all and
    are left as they are rather than mangled into something else.
    """
    return text.translate(_KATA_TO_HIRA) if text else text


def hiragana_to_katakana(text: str) -> str:
    """Hiragana -> katakana, the inverse of :func:`katakana_to_hiragana`."""
    return text.translate(_HIRA_TO_KATA) if text else text


def is_kana_char(char: str) -> bool:
    """One character, and it is kana (either script, marks included)."""
    code = ord(char)
    if _HIRA_RUN_START <= code <= _HIRA_ITERATION[1]:
        return True
    return _KATA_RUN_START <= code <= _KATA_ITERATION[1] and code != _KATAKANA_MIDDLE_DOT


def is_kana(text: str) -> bool:
    """Every character is kana. Empty text is not kana."""
    return bool(text) and all(is_kana_char(char) for char in text)


def canonical_reading(text: str | None) -> str | None:
    """A reading reduced to the form both sides can be compared in: hiragana.

    Whitespace is stripped; ``None``/empty come back as ``None`` so callers can
    treat "no reading" as one condition instead of three.
    """
    if text is None:
        return None
    stripped = text.strip()
    return katakana_to_hiragana(stripped) if stripped else None


def reading_probes(reading: str | None) -> tuple[str, ...]:
    """The spellings to look ``reading`` up under, deduplicated.

    Both scripts, because JMdict stores native readings in hiragana but loanword
    readings in katakana. Probing both keeps the query on
    ``jmdict_reading_reading_idx`` instead of forcing a table scan to normalize
    the stored side.
    """
    canonical = canonical_reading(reading)
    if canonical is None:
        return ()
    return tuple(dict.fromkeys((canonical, hiragana_to_katakana(canonical))))


# Han ideographs, plus the repetition mark 々 and 〆, which behave as part of a
# written form (時々, 〆切). Anything else — kana, punctuation, latin — is not
# part of the skeleton.
_HAN_RANGES: Final = (
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # compatibility ideographs
    (0x20000, 0x2EBEF),  # extensions B..F (rare, but they are real headwords)
)
_SKELETON_EXTRAS: Final = frozenset("々〆〇〻")


def is_han_char(char: str) -> bool:
    """One character, and it is a Han ideograph (or a form that acts as one)."""
    if char in _SKELETON_EXTRAS:
        return True
    code = ord(char)
    return any(low <= code <= high for low, high in _HAN_RANGES)


def kanji_skeleton(text: str) -> str:
    """The written form with all non-kanji deleted — an okurigana-blind key.

    ``引っ越す`` and ``引越す`` both reduce to ``引越``, which is what lets rung
    4a treat them as the same word. A form with no kanji reduces to ``""``; that
    is not a usable key and callers must not match on it.
    """
    return "".join(char for char in text if is_han_char(char))


# ---------------------------------------------------------------------------
# Part of speech (rung 5)
# ---------------------------------------------------------------------------

# UniDic pos1 -> (exact JMdict pos tokens, acceptable token prefixes).
#
# Coarse on purpose. The two schemes carve the language up differently (UniDic's
# 形状詞 vs JMdict's adj-na, UniDic's 助動詞 vs JMdict's cop/aux-v), and a
# fine-grained map would reject real words on a taxonomy disagreement alone. Each
# row lists everything the pos1 could *plausibly* be in JMdict's vocabulary and
# rejection only happens on a clear contradiction.
#
# An empty rule means "nothing can match": punctuation and whitespace have no
# dictionary entry, and the honest answer for them is no map at all.
_POS_RULES: Final[dict[str, tuple[frozenset[str], tuple[str, ...]]]] = {
    # Nouns are the permissive case: JMdict tags a huge share of nouns with a
    # second, non-noun part of speech (n,vs / n,adv / n,adj-no).
    "名詞": (
        frozenset(
            {
                "pn", "num", "ctr", "vs", "vs-i", "vs-s", "vs-c", "adj-na",
                "adj-no", "adj-f", "adj-t", "adv", "adv-to", "exp", "unc",
                "pref", "suf",
            }
        ),
        ("n",),
    ),
    "代名詞": (frozenset({"pn", "adj-no", "exp", "unc"}), ("n",)),
    "動詞": (frozenset({"exp", "unc", "aux", "cop"}), ("v",)),
    # UniDic 形容詞 is the i-adjective class; adj-ix covers the いい/よい split.
    "形容詞": (
        frozenset({"exp", "unc", "aux", "aux-adj", "adj-no", "adj-na", "n"}),
        ("adj-i", "adj-f"),
    ),
    # 形状詞 is UniDic's na-adjective class, which JMdict usually writes n,adj-na.
    "形状詞": (
        frozenset({"exp", "unc", "adj-na", "adj-no", "adj-t", "adj-f", "adj-ix"}),
        ("n",),
    ),
    "副詞": (
        frozenset(
            {"adv", "adv-to", "exp", "unc", "adj-no", "adj-na", "conj", "int", "prt"}
        ),
        ("n",),
    ),
    "連体詞": (frozenset({"adj-pn", "adj-f", "adj-no", "exp", "unc"}), ("n",)),
    "接続詞": (frozenset({"conj", "exp", "unc", "adv", "prt", "int"}), ("n",)),
    "感動詞": (frozenset({"int", "exp", "unc", "adv", "conj"}), ("n",)),
    # The rung-5 headline case: a particle must not land on a noun entry, so
    # there is no "n" prefix here.
    "助詞": (frozenset({"prt", "suf", "conj", "exp", "unc", "aux", "cop", "adv"}), ()),
    # です/だ are cop; た/ます are aux-v; ない is aux-adj. Deliberately *tight*:
    # neither "suf" nor the verb prefixes belong here. UniDic's ます and ず are
    # bound auxiliaries, and both have an unrelated homophone that outranks the
    # real answer on commonness alone — the ordinary verb 増す and the colloquial
    # pluralizing suffix ズ. Every UniDic 助動詞 that JMdict lists at all is filed
    # under aux/aux-v/aux-adj/cop, so nothing real is lost by excluding them.
    "助動詞": (
        frozenset({"aux", "aux-v", "aux-adj", "cop", "prt", "exp", "unc"}),
        (),
    ),
    "接頭辞": (frozenset({"pref", "n-pref", "unc", "exp"}), ("n",)),
    "接尾辞": (frozenset({"suf", "n-suf", "ctr", "unc", "exp"}), ("n",)),
    "フィラー": (frozenset({"int", "exp", "unc"}), ()),
    "記号": (frozenset(), ()),
    "補助記号": (frozenset(), ()),
    "空白": (frozenset(), ()),
}

_POS_TOKEN_JOINER: Final = ","


def _pos_tokens(packed: Iterable[str | None]) -> frozenset[str]:
    tokens: set[str] = set()
    for value in packed:
        if value:
            tokens.update(part for part in value.split(_POS_TOKEN_JOINER) if part)
    return frozenset(tokens)


def pos_compatible(pos1: str | None, pos_tokens: frozenset[str]) -> bool:
    """Could a morph tagged ``pos1`` be this entry (whose senses carry ``pos_tokens``)?

    ``True`` when no judgement can be made: no ``pos1``, a ``pos1`` outside the
    map, or an entry whose senses record no part of speech. Missing data must not
    cost a real word its match.
    """
    if not pos1:
        return True
    rule = _POS_RULES.get(pos1)
    if rule is None:
        return True
    exact, prefixes = rule
    if not exact and not prefixes:
        return False
    if not pos_tokens:
        return True
    return any(
        token in exact or (prefixes and token.startswith(prefixes))
        for token in pos_tokens
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

#: Rung names in cascade order, strongest evidence first. Also the ``method``
#: values written to ``morph_lexeme_map.method``.
METHODS: Final = (
    "kanji_reading",
    "reading_exact",
    "variant_okurigana",
    "kanji_exact",
    "kana_fallback",
)

#: Base confidence per rung. ``kanji_exact`` is listed twice in spirit: reached
#: without a reading it keeps its base score, reached *despite* a reading that
#: agreed with nothing it takes ``_READING_DISAGREEMENT_PENALTY``.
_BASE_CONFIDENCE: Final[dict[str, float]] = {
    "kanji_reading": 0.97,
    "kanji_exact": 0.90,
    "reading_exact": 0.90,
    "variant_okurigana": 0.80,
    "kana_fallback": 0.65,
}
#: An unbroken tie at the top of the ranking: the ranking could not separate two
#: entries, and saying so is more useful than pretending it could.
_TIE_PENALTY: Final = 0.15
#: A reading was supplied and no reading of the entry agreed with it.
_READING_DISAGREEMENT_PENALTY: Final = 0.15
_MIN_CONFIDENCE: Final = 0.05


@dataclass(frozen=True, slots=True)
class NormResult:
    """The outcome of normalizing one morph.

    ``matches`` is ``(seq, method, confidence)`` best-first, holding only
    candidates that survived the part-of-speech filter. ``best`` is
    ``matches[0]``'s seq, or ``None`` when nothing matched — and "nothing" is a
    real answer for punctuation and for grammar UniDic splits out that JMdict
    does not list.
    """

    matches: list[tuple[int, str, float]] = field(default_factory=list)
    best: int | None = None


@dataclass(frozen=True, slots=True)
class LexemeResult:
    """What :func:`populate_lexemes` rebuilt."""

    lexemes: int
    previous: int
    entries: int


@dataclass(frozen=True, slots=True)
class MapResult:
    """What :func:`map_known_morphs` linked."""

    mapped: int
    unmapped: int
    by_method: dict[str, int]
    previous: int = 0
    unmapped_lemmas: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# lexeme: the derived rebuild
# ---------------------------------------------------------------------------

# One row per (seq, sense_idx). headword is the entry's first written form, or
# its first reading when it has none (a kana-only word's headword *is* its kana);
# reading is the entry's first reading. "First" is insertion order, which the
# importer preserved from upstream, and upstream lists the most standard form
# first. The bare-column-with-MIN(rowid) idiom is SQLite's documented way to take
# the row of the minimum rather than an arbitrary row of the group.
_POPULATE_LEXEMES_SQL: Final = f"""
INSERT INTO lexeme
    (id, jmdict_seq, sense_idx, headword, reading, pos, gloss_en, dict_version)
SELECT '{LEXEME_ID_PREFIX}' || s.seq || '-' || s.sense_idx,
       s.seq,
       s.sense_idx,
       COALESCE(k.kanji, r.reading),
       r.reading,
       s.pos,
       s.gloss_en,
       e.dict_version
  FROM jmdict_sense s
  JOIN jmdict_entry e ON e.seq = s.seq
  LEFT JOIN (SELECT seq, kanji, MIN(rowid) FROM jmdict_kanji GROUP BY seq) k
         ON k.seq = s.seq
  LEFT JOIN (SELECT seq, reading, MIN(rowid) FROM jmdict_reading GROUP BY seq) r
         ON r.seq = s.seq
"""


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def populate_lexemes(conn: sqlite3.Connection) -> LexemeResult:
    """Rebuild ``lexeme`` from the imported ``jmdict_*`` tables.

    One row per ``(jmdict_seq, sense_idx)``. Derived, so this is DELETE + INSERT
    inside **one** transaction: a reader sees either the previous population or
    the new one, never a partial dictionary. Re-running it is a no-op beyond
    rewriting identical rows, which is what makes it safe to call after every
    JMdict import.

    Refuses to run when ``jmdict_sense`` is empty while ``lexeme`` is not —
    emptying the table because the dictionary import was skipped is a data-loss
    bug dressed up as a rebuild.
    """
    previous = _count(conn, "lexeme")
    senses = _count(conn, "jmdict_sense")
    if senses == 0 and previous > 0:
        raise ValueError(
            "jmdict_sense is empty but lexeme holds "
            f"{previous} rows; refusing to rebuild lexeme into nothing. Import "
            "JMdict first (katagiri.jmdict_import.import_jmdict)."
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM lexeme")
        conn.execute(_POPULATE_LEXEMES_SQL)
        lexemes = _count(conn, "lexeme")
        entries = _count(conn, "jmdict_entry")
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise
    _logger.info(
        "Rebuilt lexeme: %d rows from %d entries (was %d).", lexemes, entries, previous
    )
    return LexemeResult(lexemes=lexemes, previous=previous, entries=entries)


# ---------------------------------------------------------------------------
# Candidate gathering
# ---------------------------------------------------------------------------


#: JMdict's "usually written using kana alone" misc tag. An entry carrying it is
#: a kana word in practice even though it has a written form on the books: コップ
#: is filed under 洋杯 and ズボン under 洋袴, spellings no learner will ever meet.
#: Rung 2 counts these as kana entries, because otherwise a kana lemma is matched
#: against only the *truly* headword-less entries and loses to whatever rare
#: kana-only homophone happens to exist — コップ resolving to the slang "cop" and
#: ズボン to an onomatopoeic adverb were both real failures of that shape.
USUALLY_KANA_TAG: Final = "uk"


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One JMdict entry under consideration, with everything ranking needs."""

    seq: int
    is_common: bool
    kanji: tuple[str, ...]
    readings: tuple[str, ...]
    pos: frozenset[str]
    misc: frozenset[str] = frozenset()

    @property
    def usually_kana(self) -> bool:
        """JMdict says this word is normally written in kana."""
        return USUALLY_KANA_TAG in self.misc

    @property
    def written_in_kana(self) -> bool:
        """No written form at all, or one nobody uses. Rung 2's candidate test."""
        return not self.kanji or self.usually_kana

    def reading_agrees(self, canonical: str) -> bool:
        return any(canonical_reading(text) == canonical for text in self.readings)

    def has_form(self, lemma: str) -> bool:
        return lemma in self.kanji or lemma in self.readings

    @property
    def primary_form(self) -> str | None:
        """The entry's headline spelling.

        Upstream lists the most standard form first, so this is the spelling the
        entry is *about*. For a kana word it is the first reading — an entry
        JMdict tags ``uk`` is filed under a written form (コップ under ``洋杯``)
        that is not how the word is spelled in practice.
        """
        if self.written_in_kana:
            return self.readings[0] if self.readings else None
        return self.kanji[0] if self.kanji else None

    def skeletons(self) -> frozenset[str]:
        return frozenset(
            skeleton for skeleton in (kanji_skeleton(form) for form in self.kanji) if skeleton
        )


def _load_candidates(
    conn: sqlite3.Connection, seqs: Sequence[int]
) -> dict[int, _Candidate]:
    """Fetch every entry in ``seqs`` with its forms, readings and sense pos.

    One query per table over the whole seq set rather than three per seq: the
    rungs hand over up to a few hundred seqs and per-seq round trips are what
    would make the accuracy harness slow.
    """
    if not seqs:
        return {}
    unique = list(dict.fromkeys(int(seq) for seq in seqs))
    placeholders = ", ".join("?" * len(unique))

    kanji: dict[int, list[str]] = {}
    for row in conn.execute(
        f"SELECT seq, kanji FROM jmdict_kanji WHERE seq IN ({placeholders}) ORDER BY rowid",
        unique,
    ):
        kanji.setdefault(int(row["seq"]), []).append(str(row["kanji"]))

    readings: dict[int, list[str]] = {}
    for row in conn.execute(
        f"SELECT seq, reading FROM jmdict_reading WHERE seq IN ({placeholders}) ORDER BY rowid",
        unique,
    ):
        readings.setdefault(int(row["seq"]), []).append(str(row["reading"]))

    pos: dict[int, list[str | None]] = {}
    misc: dict[int, list[str | None]] = {}
    for row in conn.execute(
        f"SELECT seq, pos, misc FROM jmdict_sense WHERE seq IN ({placeholders}) "
        "ORDER BY sense_idx",
        unique,
    ):
        seq = int(row["seq"])
        pos.setdefault(seq, []).append(row["pos"])
        misc.setdefault(seq, []).append(row["misc"])

    candidates: dict[int, _Candidate] = {}
    for row in conn.execute(
        f"SELECT seq, is_common FROM jmdict_entry WHERE seq IN ({placeholders})", unique
    ):
        seq = int(row["seq"])
        candidates[seq] = _Candidate(
            seq=seq,
            is_common=bool(row["is_common"]),
            kanji=tuple(kanji.get(seq, ())),
            readings=tuple(readings.get(seq, ())),
            pos=_pos_tokens(pos.get(seq, ())),
            misc=_pos_tokens(misc.get(seq, ())),
        )
    return candidates


def _seqs_by_kanji(conn: sqlite3.Connection, form: str) -> list[int]:
    return [
        int(row[0])
        for row in conn.execute("SELECT seq FROM jmdict_kanji WHERE kanji = ?", (form,))
    ]


def _seqs_by_reading(conn: sqlite3.Connection, probes: Sequence[str]) -> list[int]:
    if not probes:
        return []
    placeholders = ", ".join("?" * len(probes))
    return [
        int(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT seq FROM jmdict_reading WHERE reading IN ({placeholders})",
            list(probes),
        )
    ]


#: Cap on the prefix sweep used by rung 4a when no reading is available. A first
#: kanji like 手 heads thousands of headwords; the sweep exists to catch okurigana
#: variance, not to enumerate the dictionary, and an unbounded sweep would make a
#: reading-less lookup quietly expensive.
_PREFIX_SWEEP_LIMIT: Final = 400


def _seqs_by_first_kanji(conn: sqlite3.Connection, first: str) -> list[int]:
    """Entries with a written form starting with ``first``.

    A prefix ``LIKE`` so SQLite can still use ``jmdict_kanji_kanji_idx``. Only
    used as the reading-less path into the okurigana rung, where the skeleton
    filter does the real work afterwards.
    """
    return [
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT seq FROM jmdict_kanji WHERE kanji LIKE ? ESCAPE '\\' LIMIT ?",
            (_like_prefix(first), _PREFIX_SWEEP_LIMIT),
        )
    ]


def _like_prefix(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _rank_key(candidate: _Candidate, lemma: str) -> tuple[int, int, int, int, int]:
    """Sort key, ascending — so "better" is encoded as a smaller number.

    1. The lemma is the entry's *headline* spelling, not merely one of its
       variants. Upstream lists the standard form first, so an entry whose first
       form is the lemma is *about* that spelling, while one that lists it fourth
       is about something else. This sits above commonness deliberately: identity
       beats frequency. The conjunctive particle ``て`` is the case that settles
       it — the quotative ``って/て`` entry is flagged common and the conjunctive
       ``て/で`` entry is not, so commonness-first hands every ``て`` in the
       language to "he said". Across the 200-row accuracy set and 651 morphs
       harvested from tokenized text, moving this term first changed exactly one
       answer, and changed it to the right one.
    2. ``is_common``. JMdict's own commonness flag is the best predictor of which
       of several equally-titled homographs a learner actually met.
    3. The lemma appears among the entry's forms at all.
    4. Orthographic affinity, and its sign depends on the lemma's script. For a
       lemma written with kanji, more recorded spellings means a better
       established entry. For a kana lemma the opposite holds: an entry carrying
       a pile of kanji spellings is a kanji word, and a kana morph is better
       explained by the entry that has none — which is what separates the
       dedicated auxiliary ``ない`` (no written form) from the adjective ``無い``
       (which also carries an ``aux-adj`` sense, so no filter can split them).
    5. ``seq`` ascending, so the answer is deterministic rather than incidental.
    """
    variants = len(candidate.kanji)
    return (
        0 if candidate.primary_form == lemma else 1,
        0 if candidate.is_common else 1,
        0 if candidate.has_form(lemma) else 1,
        variants if is_kana(lemma) else -variants,
        candidate.seq,
    )


def _score(
    candidates: Sequence[_Candidate],
    lemma: str,
    method: str,
    *,
    reading_disagreed: bool = False,
) -> list[tuple[int, str, float]]:
    """Rank one rung's survivors and attach a confidence to each."""
    ordered = sorted(candidates, key=lambda item: _rank_key(item, lemma))
    base = _BASE_CONFIDENCE[method]
    if reading_disagreed:
        base -= _READING_DISAGREEMENT_PENALTY
    # Compare the key *without* its last term. That term is the seq, which exists
    # only to make an unresolved tie deterministic — including it in the
    # comparison would mean no two candidates ever look tied and the penalty could
    # never fire, which is exactly the bug this slice guards against.
    tied = (
        len(ordered) > 1
        and _rank_key(ordered[0], lemma)[:-1] == _rank_key(ordered[1], lemma)[:-1]
    )
    if tied:
        base -= _TIE_PENALTY

    scored: list[tuple[int, str, float]] = []
    for position, candidate in enumerate(ordered):
        # Runners-up decay: they are the same evidence pointing somewhere the
        # ranking liked less, and a caller reviewing them should see that.
        confidence = max(_MIN_CONFIDENCE, round(base - 0.05 * position, 4))
        scored.append((candidate.seq, method, confidence))
    return scored


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


def normalize_morph(
    conn: sqlite3.Connection,
    lemma: str,
    reading: str | None = None,
    pos1: str | None = None,
) -> NormResult:
    """Map one UniDic morph onto JMdict entries.

    ``lemma`` is UniDic's dictionary form, ``reading`` its lemma reading in
    either script (UniDic gives katakana; hiragana is accepted too), ``pos1``
    UniDic's coarsest part of speech. Only ``lemma`` is required, but a reading
    is what makes homographs resolvable and an absent one costs accuracy on
    exactly the words that need it most.

    Rungs are tried strongest-first and the first one with a part-of-speech
    plausible candidate wins; see the module docstring for what each rung claims.
    An empty result is a legitimate answer, not a failure.
    """
    # Strip UniDic's lemma disambiguator before anything else: every rung below
    # compares against JMdict orthography, and JMdict has never heard of it.
    lemma = unidic_lemma_form(lemma)
    if not lemma:
        return NormResult()

    canonical = canonical_reading(reading)
    probes = reading_probes(reading)
    lemma_probes = reading_probes(lemma) if is_kana(lemma) else ()
    # A kana lemma is its own reading; UniDic often supplies both and they agree,
    # but the reading is optional and the lemma is not.
    effective = canonical or (canonical_reading(lemma) if is_kana(lemma) else None)

    def surviving(seqs: Sequence[int]) -> dict[int, _Candidate]:
        return {
            seq: candidate
            for seq, candidate in _load_candidates(conn, seqs).items()
            if pos_compatible(pos1, candidate.pos)
        }

    # --- rung 3: kanji form and reading agree -------------------------------
    kanji_hits = surviving(_seqs_by_kanji(conn, lemma)) if not is_kana(lemma) else {}
    if kanji_hits and effective is not None:
        agreeing = [c for c in kanji_hits.values() if c.reading_agrees(effective)]
        if agreeing:
            return _result(_score(agreeing, lemma, "kanji_reading"))

    # --- rung 2: kana lemma, kana-only entry -------------------------------
    if lemma_probes:
        kana_hits = surviving(_seqs_by_reading(conn, lemma_probes))
        kana_words = [c for c in kana_hits.values() if c.written_in_kana]
        if kana_words:
            return _result(_score(kana_words, lemma, "reading_exact"))

    skeleton = kanji_skeleton(lemma)

    # --- rung 4a, reading path: skeleton agreement plus reading agreement ---
    #
    # This runs *before* rung 1 on purpose, and only when a reading is in hand.
    # Getting here with a non-empty ``kanji_hits`` means the lemma is a written
    # form of some entry whose readings all contradict the morph — so the
    # orthographic hit is a homograph collision, not an identification. 食物 read
    # タベモノ is the case in point: 食物 is JMdict's headword for しょくもつ,
    # while the word actually spoken is 食べ物, whose skeleton is also 食物.
    # Reading agreement plus skeleton agreement beats orthography with a
    # contradicted reading, so it gets the first say.
    if skeleton and probes:
        pool = surviving(_seqs_by_reading(conn, probes))
        variants = [
            candidate for candidate in pool.values() if skeleton in candidate.skeletons()
        ]
        if variants:
            return _result(_score(variants, lemma, "variant_okurigana"))

    # --- rung 1: kanji form only -------------------------------------------
    if kanji_hits:
        return _result(
            _score(
                list(kanji_hits.values()),
                lemma,
                "kanji_exact",
                # A reading was available and agreed with nothing, here or on the
                # variant rung: still the best orthographic evidence there is,
                # but say plainly that it is shakier.
                reading_disagreed=effective is not None,
            )
        )

    # --- rung 4a, reading-less path ----------------------------------------
    # No reading to narrow with: sweep written forms sharing the first kanji and
    # let the skeleton decide. Weaker, and bounded.
    if skeleton and not probes:
        pool = surviving(_seqs_by_first_kanji(conn, skeleton[0]))
        variants = [
            candidate for candidate in pool.values() if skeleton in candidate.skeletons()
        ]
        if variants:
            return _result(_score(variants, lemma, "variant_okurigana"))

    # --- rung 4b: reading alone -------------------------------------------
    fallback_probes = probes or lemma_probes
    if fallback_probes:
        pool = surviving(_seqs_by_reading(conn, fallback_probes))
        if pool:
            return _result(_score(list(pool.values()), lemma, "kana_fallback"))

    return NormResult()


def _result(matches: list[tuple[int, str, float]]) -> NormResult:
    return NormResult(matches=matches, best=matches[0][0] if matches else None)


# ---------------------------------------------------------------------------
# morph_lexeme_map: the derived rebuild
# ---------------------------------------------------------------------------

_MAP_INSERT_SQL: Final = (
    f"INSERT OR REPLACE INTO {MAP_TABLE}"
    "(item_id, lexeme_id, surface, method, confidence) VALUES (?, ?, ?, ?, ?)"
)


def _morphs_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (MORPHS_TABLE,),
    ).fetchone()
    return row is not None


def _known_lemmas(conn: sqlite3.Connection) -> list[str]:
    """Distinct lemmas AnkiMorphs knows about.

    ``ankimorphs_morphs`` is keyed ``(lemma, inflection, source)``, so a lemma
    appears once per inflected form seen and once per source. Normalization is a
    lemma-level question, so the inflections collapse here.
    """
    return [
        str(row[0])
        for row in conn.execute(
            f"SELECT DISTINCT lemma FROM {MORPHS_TABLE} "
            "WHERE lemma IS NOT NULL AND lemma <> '' ORDER BY lemma"
        )
    ]


def map_known_morphs(
    conn: sqlite3.Connection,
    *,
    readings: Mapping[str, str] | None = None,
    pos1_by_lemma: Mapping[str, str] | None = None,
    reading_lookup: Callable[[str], str | None] | None = None,
) -> MapResult:
    """Link every AnkiMorphs lemma to a lexeme, rebuilding this module's rows.

    ``ankimorphs_morphs`` records a lemma and an inflection but **no reading**,
    which removes the cascade's best homograph discriminator: a bare ``生`` can
    only be answered by commonness. So a reading can be supplied per lemma —
    ``readings`` for a precomputed mapping, ``reading_lookup`` for a callable
    (tokenizing the lemma with UniDic is the obvious implementation, kept out of
    here so this module never needs the 500 MB dictionary loaded). ``pos1`` may
    be supplied the same way. Both are optional and absent ones simply mean a
    weaker match, never an error.

    Rows are written with ``item_id`` = ``morph:<lemma>`` and ``lexeme_id`` =
    ``lx-<seq>-1`` (entry-level; see the module docstring on sense granularity),
    ``surface`` = the lemma, ``method`` = the winning rung. Only ``best`` is
    linked — writing every candidate would turn an ambiguity into several
    confident-looking claims.

    Derived rebuild, one transaction: every ``morph:``-prefixed row is deleted
    and rewritten, and links other importers made from real ``item`` rows are
    left alone. A missing ``ankimorphs_morphs`` table is not an error — nothing
    has been ingested yet — and yields an empty result.
    """
    if not _morphs_table_exists(conn):
        _logger.info(
            "%s does not exist yet; nothing to map. Ingest AnkiMorphs first.",
            MORPHS_TABLE,
        )
        return MapResult(mapped=0, unmapped=0, by_method={})

    lemmas = _known_lemmas(conn)
    rows: list[tuple[Any, ...]] = []
    by_method: dict[str, int] = {}
    unmapped: list[str] = []

    for lemma in lemmas:
        reading = None
        if readings is not None:
            reading = readings.get(lemma)
        if reading is None and reading_lookup is not None:
            reading = reading_lookup(lemma)
        pos1 = pos1_by_lemma.get(lemma) if pos1_by_lemma is not None else None

        result = normalize_morph(conn, lemma, reading, pos1)
        if result.best is None:
            unmapped.append(lemma)
            continue
        seq, method, confidence = result.matches[0]
        rows.append(
            (morph_item_id(lemma), lexeme_id(seq), lemma, method, confidence)
        )
        by_method[method] = by_method.get(method, 0) + 1

    pattern = _like_prefix(MORPH_ITEM_PREFIX)
    previous = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {MAP_TABLE} WHERE item_id LIKE ? ESCAPE '\\'",
            (pattern,),
        ).fetchone()[0]
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            f"DELETE FROM {MAP_TABLE} WHERE item_id LIKE ? ESCAPE '\\'", (pattern,)
        )
        conn.executemany(_MAP_INSERT_SQL, rows)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise

    _logger.info(
        "Mapped %d of %d AnkiMorphs lemmas to lexemes (was %d rows); by method: %s.",
        len(rows),
        len(lemmas),
        previous,
        by_method or "{}",
    )
    return MapResult(
        mapped=len(rows),
        unmapped=len(unmapped),
        by_method=by_method,
        previous=previous,
        unmapped_lemmas=tuple(unmapped),
    )


__all__ = [
    "DEFAULT_SENSE_IDX",
    "LEXEME_ID_PREFIX",
    "LONG_VOWEL_MARK",
    "METHODS",
    "MORPH_ITEM_PREFIX",
    "LexemeResult",
    "MapResult",
    "NormResult",
    "canonical_reading",
    "hiragana_to_katakana",
    "is_han_char",
    "is_kana",
    "is_kana_char",
    "kanji_skeleton",
    "katakana_to_hiragana",
    "lexeme_id",
    "map_known_morphs",
    "morph_item_id",
    "normalize_morph",
    "populate_lexemes",
    "pos_compatible",
    "reading_probes",
    "unidic_lemma_form",
]
