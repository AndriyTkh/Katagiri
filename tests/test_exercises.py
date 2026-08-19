"""D3: drill generation, sentence building, and the sealed-canary validator hook.

What is being defended here
---------------------------
The generators are the easy part. What these tests actually pin down is the
*validator*, because contamination of the sealed canary set is the one failure in
this project that cannot be undone: a canary sentence used once as a drill is
spent, silently, and no later measurement can tell which numbers were still
honest. So there are tests for every route by which a canary sentence could reach
a drill — whole, as a fragment, as a cloze with a hole in it, spelled in katakana,
spelled as its kana reading, referenced by id, or reached through a carrier
sentence that leaked into ``item`` unsealed — and a test that the generators
refuse to run at all when the sealed file cannot be loaded.

**No real canary sentence appears in this file.** Every screening test builds a
*synthetic* sealed set out of invented sentences whose ids are computed with
``canary_sentence_id``, which is also how the parser's own integrity check is
exercised. The real sealed file is touched exactly once, by
:func:`test_real_sealed_set_loads`, which asserts counts and band shape and never
looks at a sentence. Copying sealed text into a test file to test the thing that
exists to stop sealed text from being copied would be its own contamination.

Fixtures follow ``tests/test_mcp_tools.py``: ``LOCALAPPDATA`` is moved to a tmp
dir so the module's own ``open_db``/config path is exercised, and rows are seeded
with direct INSERTs so ordering inputs (``event.ts_server``) are exactly what each
test says they are rather than whatever the machine's clock and zone produce.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import events, exercises
from katagiri.db import open_db
from katagiri.envelope import (
    ECHO_MISMATCH,
    SOURCE_MEDIA,
    EchoGate,
    reset_default_gate,
    wrap,
)
from katagiri.exercises import (
    BAD_COUNT,
    CANARY_ECHO,
    CANARY_SET_TAMPERED,
    CANARY_SET_UNAVAILABLE,
    CANARY_VAULT_PATH,
    CANARY_VIOLATION,
    CLOZE_BLANK,
    CLOZE_PRODUCTION,
    ECHO_BACK_REQUIRED,
    FLAG,
    LISTEN_TO_MEANING,
    MAX_SOURCE_CHARS,
    MEANING_TO_SPEECH,
    NO_CANDIDATES,
    NO_SUCH_ITEM,
    READ_TO_MEANING,
    SEALED_ITEM,
    SHADOW,
    SKIP_NO_TEMPLATE,
    SKIP_RECEPTIVE_ONLY,
    SOURCE_EXTERNAL,
    SOURCE_TEMPLATE,
    SOURCE_TOO_LARGE,
    UNENVELOPED_SOURCE,
    UNKNOWN_DIRECTION,
    VERIFY_AUTOMATIC,
    VERIFY_OBSERVATION,
    CanaryGuard,
    CanarySentence,
    CanarySetTampered,
    CanarySetUnavailable,
    build_sentences,
    canary_sentence_id,
    canary_set_path,
    coarse_pos,
    gen_exercise,
    load_canary_guard,
    normalize_for_screening,
    refuses,
    reset_canary_cache,
    worst_code,
)

TS = "2026-08-01T00:00:00Z"

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_VAULT = REPO_ROOT / "docs" / "katagiri" / "katagiri"


# ---------------------------------------------------------------------------
# A synthetic sealed set: invented sentences, real id scheme
# ---------------------------------------------------------------------------

#: Invented sentences standing in for the sealed set. Chosen for what each one
#: lets a test prove:
#:  * BIKE    — content-bearing, long enough to slice into fragments and clozes,
#:              and carries a kana reading so the reading column is exercised.
#:  * MUST    — its only long shared run with ordinary Japanese is the kana
#:              ending 〜なければなりません, i.e. shared *grammar*, which must not
#:              be reported as contamination.
#:  * KATA    — katakana, so the kana-folding half of normalisation is exercised.
FAKE_BIKE = "赤い自転車が倉庫の前に止まっています。"
FAKE_BIKE_READING = "あかいじてんしゃがそうこのまえにとまっています。"
FAKE_MUST = "毎朝の体操を続けなければなりません。"
FAKE_KATA = "テレビのニュースで新しい制度の説明を聞きました。"


def fake_rows() -> list[tuple[str, str, str, str]]:
    return [
        (canary_sentence_id(FAKE_BIKE), "b1", FAKE_BIKE, FAKE_BIKE_READING),
        (canary_sentence_id(FAKE_MUST), "b3", FAKE_MUST, ""),
        (canary_sentence_id(FAKE_KATA), "b4", FAKE_KATA, ""),
    ]


def fake_markdown(*, sealed: str = "true") -> str:
    lines = [
        "---",
        "schema: 2",
        "type: meta",
        f"sealed: {sealed}",
        "---",
        "",
        "# Synthetic set",
        "",
        "| id | band | japanese | reading (kana) | english |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cid, band, japanese, reading in fake_rows():
        lines.append(f"| {cid} | {band} | {japanese} | {reading} | (invented) |")
    return "\n".join(lines) + "\n"


@pytest.fixture
def guard() -> CanaryGuard:
    return CanaryGuard.from_rows(fake_rows())


@pytest.fixture
def fake_vault(tmp_path: Path) -> Path:
    """A vault directory holding a synthetic sealed set at the real relative path."""
    target = tmp_path / "vault"
    path = target.joinpath(*CANARY_VAULT_PATH.split("/"))
    path.parent.mkdir(parents=True)
    path.write_text(fake_markdown(), encoding="utf-8")
    return target


@pytest.fixture(autouse=True)
def _clean_module_state():
    reset_canary_cache()
    reset_default_gate()
    yield
    reset_canary_cache()
    reset_default_gate()


# ---------------------------------------------------------------------------
# Database fixture and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A migrated database found through the real config path, as in A6's tests."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    conn = open_db()
    try:
        yield conn
    finally:
        conn.close()
        config_mod.reset_config_cache()


def seed_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kind: str = "word",
    kanji: str | None = None,
    reading: str | None = None,
    pos: str | None = None,
    home_topic: str | None = None,
    jlpt: str | None = None,
    level: str | None = None,
    understanding: int | None = None,
    production_eligible: int = 1,
    sealed: int = 0,
    created_ts: str = TS,
) -> str:
    conn.execute(
        """
        INSERT INTO item (id, kind, home_topic, kanji, reading, pos, jlpt, level,
                          understanding, production_eligible, sealed, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            kind,
            home_topic,
            kanji,
            reading,
            pos,
            jlpt,
            level,
            understanding,
            production_eligible,
            sealed,
            created_ts,
        ),
    )
    conn.commit()
    return item_id


def seed_sentence_text(conn: sqlite3.Connection, item_id: str, jp: str) -> None:
    conn.execute(
        "INSERT INTO sentence_text (item_id, jp) VALUES (?, ?)", (item_id, jp)
    )
    conn.commit()


def seed_drill_event(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    ts: str,
    direction: str = READ_TO_MEANING,
) -> None:
    """A past drill, so ``last_drilled`` ordering has something to order by."""
    conn.execute(
        """
        INSERT INTO event (id, ts_device, ts_server, tz, day_key, session_id,
                           type, item_id, direction)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            events.new_ulid(),
            ts,
            ts,
            "Europe/Kyiv",
            ts[:10],
            "test-session",
            "review",
            item_id,
            direction,
        ),
    )
    conn.commit()


def ids(result: dict[str, Any], key: str = "exercises") -> list[str]:
    return [row["item_id"] for row in result[key]]


# ---------------------------------------------------------------------------
# Loading and integrity of the sealed set
# ---------------------------------------------------------------------------


def test_real_sealed_set_loads():
    """The hook reads the actual sealed file: 200 sentences, five bands of 40.

    Counts and shape only. Nothing in this test looks at a sentence, and nothing
    it can fail on requires quoting one.
    """
    real = CanaryGuard.from_file(REAL_VAULT.joinpath(*CANARY_VAULT_PATH.split("/")))
    assert len(real) == 200
    assert real.bands() == {"b1": 40, "b2": 40, "b3": 40, "b4": 40, "b5": 40}
    assert all(cid.startswith("s-") for cid in real.ids())


def test_canary_set_path_is_vault_relative(tmp_path):
    assert canary_set_path(tmp_path) == tmp_path.joinpath(
        *CANARY_VAULT_PATH.split("/")
    )


def test_load_canary_guard_caches_until_the_file_changes(fake_vault):
    first = load_canary_guard(vault_path=fake_vault)
    second = load_canary_guard(vault_path=fake_vault)
    assert first is second

    path = fake_vault.joinpath(*CANARY_VAULT_PATH.split("/"))
    rewritten = fake_markdown().replace(
        "| (invented) |", "| (invented, edited comment) |"
    )
    path.write_text(rewritten, encoding="utf-8")
    third = load_canary_guard(vault_path=fake_vault)
    assert third is not first
    assert len(third) == len(first)


def test_load_canary_guard_refuses_a_missing_file(tmp_path):
    with pytest.raises(CanarySetUnavailable) as excinfo:
        load_canary_guard(vault_path=tmp_path / "nowhere")
    assert excinfo.value.code == CANARY_SET_UNAVAILABLE


def test_load_canary_guard_refuses_without_a_configured_vault(db):
    """No vault configured is a refusal, not a fallback to an empty guard."""
    with pytest.raises(CanarySetUnavailable) as excinfo:
        load_canary_guard()
    assert excinfo.value.code == CANARY_SET_UNAVAILABLE


def test_edited_sentence_fails_its_own_id_check():
    rows = fake_rows()
    cid, band, japanese, reading = rows[0]
    rows[0] = (cid, band, japanese + "ね", reading)
    with pytest.raises(CanarySetTampered) as excinfo:
        CanaryGuard.from_rows(rows)
    assert excinfo.value.code == CANARY_SET_TAMPERED
    # The refusal must not quote the sealed sentence it was checking.
    assert japanese not in str(excinfo.value)


def test_unsealed_frontmatter_is_tampering():
    with pytest.raises(CanarySetTampered):
        CanaryGuard.from_markdown(fake_markdown(sealed="false"))


def test_empty_set_is_refused_not_treated_as_clean():
    empty = "---\nsealed: true\n---\n\n# nothing here\n"
    with pytest.raises(CanarySetUnavailable) as excinfo:
        CanaryGuard.from_markdown(empty)
    assert excinfo.value.code == CANARY_SET_UNAVAILABLE


def test_malformed_row_id_is_tampering():
    text = fake_markdown().replace(canary_sentence_id(FAKE_BIKE), "s-XYZ")
    with pytest.raises(CanarySetTampered):
        CanaryGuard.from_markdown(text)


def test_guard_repr_and_sentence_repr_do_not_leak_text(guard):
    sentence = CanarySentence(
        id=canary_sentence_id(FAKE_BIKE), band="b1", japanese=FAKE_BIKE
    )
    assert FAKE_BIKE not in repr(sentence)
    assert "redacted" in repr(sentence)
    assert FAKE_BIKE not in repr(guard)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_normalisation_folds_kana_and_drops_punctuation():
    assert normalize_for_screening("テレビ、です。") == normalize_for_screening("てれびです")
    assert normalize_for_screening(" 今日は　暑い！ ") == "今日は暑い"


def test_normalisation_keeps_the_long_vowel_mark():
    """ー distinguishes words; dropping it would collapse コート onto コト."""
    assert "ー" in normalize_for_screening("コート")


# ---------------------------------------------------------------------------
# The validator hook, tier by tier
# ---------------------------------------------------------------------------


def test_exact_canary_sentence_is_refused(guard):
    findings = guard.screen(FAKE_BIKE, where="material")
    assert refuses(findings)
    assert worst_code(findings) == CANARY_VIOLATION
    assert findings[0].canary_id == canary_sentence_id(FAKE_BIKE)
    assert findings[0].band == "b1"
    assert findings[0].where == "material"


def test_katakana_respelling_does_not_evade_the_guard(guard):
    """Same sentence, hiragana swapped for katakana: still the same sentence."""
    respelled = FAKE_KATA.replace("ニュース", "にゅーす").replace("テレビ", "てれび")
    assert refuses(guard.screen(respelled))


def test_canary_sentence_embedded_in_a_longer_drill_is_refused(guard):
    findings = guard.screen(f"次の文を音読してください: {FAKE_BIKE} 意味も言ってください。")
    assert worst_code(findings) == CANARY_VIOLATION


def test_a_fragment_of_a_canary_sentence_is_refused(guard):
    """The realistic leak: not a whole sentence pasted in, part of one drilled."""
    fragment = FAKE_BIKE[4:14]
    assert len(fragment) >= exercises.MIN_CONTAINMENT_CHARS
    assert worst_code(guard.screen(fragment)) == CANARY_VIOLATION


def test_a_short_fragment_is_not_treated_as_overlap(guard):
    """自転車 is a word, not a canary sentence. A guard that refuses it is off."""
    assert guard.screen("自転車") == ()
    assert guard.screen("倉庫") == ()


def test_cloze_built_from_a_canary_sentence_is_refused(guard):
    """A cloze contains no canary sentence and is contained in none — tier 4's job."""
    cloze = FAKE_BIKE.replace("倉庫", CLOZE_BLANK)
    assert FAKE_BIKE not in cloze
    assert cloze not in FAKE_BIKE
    assert worst_code(guard.screen(cloze)) == CANARY_VIOLATION


def test_kana_reading_of_a_canary_sentence_is_refused(guard):
    findings = guard.screen(FAKE_BIKE_READING)
    assert worst_code(findings) == CANARY_VIOLATION
    assert findings[0].matched == "reading"


def test_canary_id_reference_is_refused(guard):
    findings = guard.screen(f"probe item {canary_sentence_id(FAKE_MUST)} follow-up")
    assert worst_code(findings) == CANARY_VIOLATION
    assert findings[0].matched == "id"


def test_shared_content_is_flagged_not_refused(guard):
    """Same content words, different sentence: worth surfacing, not a violation."""
    echo = "自転車が倉庫の前で見つかった。"
    findings = guard.screen(echo)
    assert findings
    assert not refuses(findings)
    assert worst_code(findings) == CANARY_ECHO
    assert findings[0].severity == FLAG
    assert findings[0].canary_id == canary_sentence_id(FAKE_BIKE)


def test_shared_grammar_alone_is_clean(guard):
    """〜なければなりません is shared by every sentence using it. Not contamination."""
    assert guard.screen("宿題を今日中に終わらせなければなりません。") == ()


def test_unrelated_sentence_is_clean(guard):
    assert guard.screen("週末は図書館で歴史の本を借りました。") == ()


def test_findings_never_carry_sealed_text(guard):
    findings = guard.screen(FAKE_BIKE)
    dumped = json.dumps([f.as_dict() for f in findings], ensure_ascii=False)
    assert FAKE_BIKE not in dumped
    assert FAKE_BIKE[4:14] not in dumped
    assert findings[0].matched_chars > 0


def test_screen_all_reports_every_part(guard):
    findings = guard.screen_all(
        (("material", FAKE_BIKE), ("expected", "自転車"), ("prompt", FAKE_MUST))
    )
    wheres = {f.where for f in findings}
    assert wheres == {"material", "prompt"}


def test_empty_text_screens_clean(guard):
    assert guard.screen("") == ()
    assert refuses(()) is False
    assert worst_code(()) == ""


# ---------------------------------------------------------------------------
# gen_exercise — happy paths
# ---------------------------------------------------------------------------


def test_gen_exercise_word_read_to_meaning(db, guard):
    seed_item(db, "w-jiten1", kanji="自転車", reading="じてんしゃ", pos="名詞", jlpt="N4")
    result = gen_exercise(db, guard=guard)
    assert result["ok"] is True
    drill = result["exercises"][0]
    assert drill["item_id"] == "w-jiten1"
    assert drill["direction"] == READ_TO_MEANING
    assert drill["material"] == "自転車"
    assert "自転車" in drill["prompt"]
    # `item` has no gloss column, so the meaning side is graded by observation
    # rather than compared against a target this module made up.
    assert drill["expected"] is None
    assert drill["verify"] == VERIFY_OBSERVATION
    assert drill["canary_screened"] is True
    assert result["canary_sentences_screened_against"] == len(guard)


def test_gen_exercise_kana_only_word_uses_its_reading(db, guard):
    seed_item(db, "w-kana01", kanji=None, reading="ばんそうこう", pos="名詞")
    result = gen_exercise(db, guard=guard)
    assert result["exercises"][0]["material"] == "ばんそうこう"
    assert result["exercises"][0]["cue"] == "reading"


def test_gen_exercise_sentence_prefers_derived_sentence_text(db, guard):
    seed_item(db, "s-abc123", kind="sentence", kanji="古い方の文", reading="ふるいほうのぶん")
    seed_sentence_text(db, "s-abc123", "週末は図書館で歴史の本を借りました。")
    result = gen_exercise(db, guard=guard)
    assert result["exercises"][0]["material"] == "週末は図書館で歴史の本を借りました。"


def test_gen_exercise_listen_hides_the_written_form(db, guard):
    seed_item(
        db,
        "s-abc124",
        kind="sentence",
        kanji="週末は図書館で歴史の本を借りました。",
        reading="しゅうまつはとしょかんでれきしのほんをかりました。",
    )
    result = gen_exercise(db, guard=guard, direction=LISTEN_TO_MEANING)
    drill = result["exercises"][0]
    assert drill["material"] == "しゅうまつはとしょかんでれきしのほんをかりました。"
    assert drill["hidden_material"] == "週末は図書館で歴史の本を借りました。"


def test_gen_exercise_shadow_is_verified_by_observation(db, guard):
    seed_item(db, "s-abc125", kind="sentence", kanji="週末は図書館で歴史の本を借りました。")
    result = gen_exercise(db, guard=guard, direction=SHADOW)
    drill = result["exercises"][0]
    assert drill["direction"] == SHADOW
    assert drill["expected"] == drill["material"]
    assert drill["verify"] == VERIFY_OBSERVATION


def test_gen_exercise_cloze_cuts_the_blank_out_of_a_real_sentence(db, guard):
    seed_item(db, "w-hon001", kanji="歴史", reading="れきし", pos="名詞")
    seed_item(db, "s-abc126", kind="sentence", kanji="週末は歴史の本を借りました。")
    result = gen_exercise(db, guard=guard, direction=CLOZE_PRODUCTION)
    drill = result["exercises"][0]
    assert drill["direction"] == CLOZE_PRODUCTION
    assert CLOZE_BLANK in drill["material"]
    assert "歴史" not in drill["material"]
    assert drill["expected"] == "歴史"
    assert drill["verify"] == VERIFY_AUTOMATIC
    assert drill["carrier_item_id"] == "s-abc126"


def test_gen_exercise_cloze_needs_a_carrier(db, guard):
    """No sentence contains the word, so no cloze is invented for it."""
    seed_item(db, "w-hon002", kanji="歴史", reading="れきし", pos="名詞")
    result = gen_exercise(db, guard=guard, direction=CLOZE_PRODUCTION)
    assert result["ok"] is False
    assert result["error"] == NO_CANDIDATES


def test_gen_exercise_grammar_item_produces_speech(db, guard):
    seed_item(db, "g-noni", kind="grammar", kanji="のに", understanding=3)
    result = gen_exercise(db, guard=guard)
    drill = result["exercises"][0]
    assert drill["direction"] == MEANING_TO_SPEECH
    assert drill["understanding"] == 3


def test_gen_exercise_orders_never_drilled_first_then_oldest(db, guard):
    for item_id in ("w-aaa001", "w-bbb002", "w-ccc003"):
        seed_item(db, item_id, kanji="自転車", reading="じてんしゃ", pos="名詞")
    seed_drill_event(db, "w-aaa001", ts="2026-08-10T09:00:00Z")
    seed_drill_event(db, "w-bbb002", ts="2026-08-02T09:00:00Z")
    result = gen_exercise(db, guard=guard, count=3)
    assert ids(result) == ["w-ccc003", "w-bbb002", "w-aaa001"]


def test_gen_exercise_is_deterministic(db, guard):
    for item_id in ("w-aaa001", "w-bbb002", "w-ccc003"):
        seed_item(db, item_id, kanji="自転車", pos="名詞")
    first = gen_exercise(db, guard=guard, count=2)
    second = gen_exercise(db, guard=guard, count=2)
    assert ids(first) == ids(second)


def test_gen_exercise_honours_count_and_topic(db, guard):
    for index in range(4):
        seed_item(db, f"w-t{index:05d}", kanji="自転車", pos="名詞", home_topic="transport")
    seed_item(db, "w-other1", kanji="歴史", pos="名詞", home_topic="history")
    result = gen_exercise(db, guard=guard, topic="transport", count=2)
    assert result["returned"] == 2
    assert all(row["home_topic"] == "transport" for row in result["exercises"])


def test_gen_exercise_resolves_a_renamed_id(db, guard):
    seed_item(db, "w-new001", kanji="自転車", pos="名詞")
    db.execute(
        "INSERT INTO alias (alias_id, canonical_id, reason, created_ts) "
        "VALUES (?, ?, ?, ?)",
        ("w-old001", "w-new001", "rename", TS),
    )
    db.commit()
    result = gen_exercise(db, guard=guard, item_ids=["w-old001"])
    assert result["ok"] is True
    assert ids(result) == ["w-new001"]
    assert result["redirects"] == [{"from": "w-old001", "to": "w-new001"}]


# ---------------------------------------------------------------------------
# gen_exercise — the canary gate
# ---------------------------------------------------------------------------


def test_canary_material_in_the_pool_is_screened_out(db, guard):
    """A canary sentence that leaked into `item` unsealed still never drills."""
    seed_item(db, "s-leak01", kind="sentence", kanji=FAKE_BIKE)
    seed_item(db, "w-clean1", kanji="歴史", reading="れきし", pos="名詞")
    result = gen_exercise(db, guard=guard, count=5)
    assert result["ok"] is True
    assert ids(result) == ["w-clean1"]
    assert [row["item_id"] for row in result["screened_out"]] == ["s-leak01"]
    assert result["screened_out"][0]["code"] == CANARY_VIOLATION
    assert FAKE_BIKE not in json.dumps(result, ensure_ascii=False)


def test_explicitly_requested_canary_material_fails_the_call(db, guard):
    """Silently substituting another item would hide the contamination."""
    seed_item(db, "s-leak02", kind="sentence", kanji=FAKE_BIKE)
    result = gen_exercise(db, guard=guard, item_ids=["s-leak02"])
    assert result["ok"] is False
    assert result["error"] == CANARY_VIOLATION
    assert result["exercises"] == []
    assert result["item_id"] == "s-leak02"
    assert result["findings"][0]["canary_id"] == canary_sentence_id(FAKE_BIKE)
    assert FAKE_BIKE not in json.dumps(result, ensure_ascii=False)


def test_a_cloze_derived_from_canary_material_is_gated_out(db, guard):
    """The word is fine; the carrier sentence is not, so the drill does not exist."""
    seed_item(db, "w-souko1", kanji="倉庫", reading="そうこ", pos="名詞")
    seed_item(db, "s-leak03", kind="sentence", kanji=FAKE_BIKE)
    assert guard.screen("倉庫") == ()

    result = gen_exercise(
        db, guard=guard, item_ids=["w-souko1"], direction=CLOZE_PRODUCTION
    )
    assert result["ok"] is False
    assert result["error"] == CANARY_VIOLATION

    # The same word in a receptive direction is untouched by that refusal.
    clean = gen_exercise(
        db, guard=guard, item_ids=["w-souko1"], direction=READ_TO_MEANING
    )
    assert clean["ok"] is True
    assert clean["exercises"][0]["material"] == "倉庫"


def test_echo_level_overlap_also_keeps_material_out_of_drills(db, guard):
    """A flag is not a violation, but a flagged candidate is still not drilled."""
    seed_item(db, "s-echo01", kind="sentence", kanji="自転車が倉庫の前で見つかった。")
    result = gen_exercise(db, guard=guard)
    assert result["ok"] is False
    assert result["error"] == NO_CANDIDATES
    assert result["screened_out"][0]["code"] == CANARY_ECHO
    assert result["screened_out"][0]["findings"][0]["severity"] == FLAG


def test_sealed_items_never_enter_the_pool(db, guard):
    seed_item(db, "s-probe1", kind="sentence", kanji="週末は歴史の本を借りました。", sealed=1)
    seed_item(db, "w-clean2", kanji="歴史", pos="名詞")
    result = gen_exercise(db, guard=guard, count=5)
    assert ids(result) == ["w-clean2"]


def test_requesting_a_sealed_item_is_refused_by_name(db, guard):
    seed_item(db, "s-probe2", kind="sentence", kanji="週末は歴史の本を借りました。", sealed=1)
    result = gen_exercise(db, guard=guard, item_ids=["s-probe2"])
    assert result["ok"] is False
    assert result["error"] == SEALED_ITEM
    assert result["sealed_items"] == ["s-probe2"]


def test_gen_exercise_refuses_without_a_loadable_canary_set(db):
    """Fail closed: no sealed set, no drills. Not 'drills without screening'."""
    seed_item(db, "w-clean3", kanji="自転車", pos="名詞")
    result = gen_exercise(db)
    assert result["ok"] is False
    assert result["error"] == CANARY_SET_UNAVAILABLE
    assert result["exercises"] == []


def test_gen_exercise_uses_the_configured_vault_when_no_guard_is_injected(
    db, fake_vault
):
    """The default guard comes from `vault_path` in the real config file."""
    config_mod.config_path().write_text(
        f'vault_path = "{fake_vault.as_posix()}"\n', encoding="utf-8"
    )
    config_mod.reset_config_cache()
    seed_item(db, "w-clean4", kanji="歴史", pos="名詞")
    result = gen_exercise(db)
    assert result["ok"] is True
    assert result["canary_sentences_screened_against"] == len(fake_rows())


# ---------------------------------------------------------------------------
# gen_exercise — caller-error edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, -1, exercises.MAX_COUNT + 1, True, 2.5])
def test_gen_exercise_rejects_a_bad_count(db, guard, count):
    result = gen_exercise(db, guard=guard, count=count)
    assert result["ok"] is False
    assert result["error"] == BAD_COUNT


def test_gen_exercise_rejects_an_unloggable_direction(db, guard):
    result = gen_exercise(db, guard=guard, direction="translate")
    assert result["ok"] is False
    assert result["error"] == UNKNOWN_DIRECTION


def test_gen_exercise_names_missing_items(db, guard):
    result = gen_exercise(db, guard=guard, item_ids=["w-nope01"])
    assert result["ok"] is False
    assert result["error"] == NO_SUCH_ITEM
    assert result["missing"] == ["w-nope01"]


def test_gen_exercise_empty_pool_is_an_empty_answer(db, guard):
    result = gen_exercise(db, guard=guard)
    assert result["ok"] is False
    assert result["error"] == NO_CANDIDATES
    assert result["exercises"] == []


def test_receptive_only_material_is_never_drilled_as_production(db, guard):
    seed_item(
        db,
        "s-yaku01",
        kind="sentence",
        kanji="週末は歴史の本を借りました。",
        production_eligible=0,
    )
    production = gen_exercise(db, guard=guard, direction=SHADOW)
    assert production["ok"] is False
    assert production["error"] == NO_CANDIDATES
    assert production["skipped"][0]["reason"] == SKIP_RECEPTIVE_ONLY

    receptive = gen_exercise(db, guard=guard, direction=READ_TO_MEANING)
    assert receptive["ok"] is True


def test_gen_exercise_requires_a_canary_guard_object(db):
    with pytest.raises(TypeError):
        gen_exercise(db, guard=object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_sentences — templates
# ---------------------------------------------------------------------------


def test_build_sentences_frames_a_noun(db, guard):
    seed_item(db, "w-jiten2", kanji="自転車", reading="じてんしゃ", pos="名詞")
    result = build_sentences(db, guard=guard, max_sentences=2)
    assert result["ok"] is True
    assert result["returned"] == 2
    first = result["sentences"][0]
    assert "自転車" in first["text"]
    assert first["origin"] == SOURCE_TEMPLATE
    assert first["needs_review"] is True
    assert first["untrusted_origin"] is False
    assert first["pos_family"] == "noun"


@pytest.mark.parametrize(
    ("pos", "family"),
    [
        ("名詞", "noun"),
        ("noun", "noun"),
        ("動詞,一般", "verb"),
        ("verb", "verb"),
        ("形容詞", "adj-i"),
        ("形状詞", "adj-na"),
        ("形容動詞", "adj-na"),
        ("副詞", "adverb"),
        ("代名詞", "noun"),
        ("助詞", None),
        (None, None),
        ("", None),
    ],
)
def test_coarse_pos_mapping(pos, family):
    assert coarse_pos(pos) == family


def test_build_sentences_skips_a_part_of_speech_it_has_no_frame_for(db, guard):
    """No template means no sentence — not invented Japanese."""
    seed_item(db, "w-part01", kanji="ばかり", pos="助詞")
    result = build_sentences(db, guard=guard)
    assert result["ok"] is False
    assert result["error"] == NO_CANDIDATES
    assert result["skipped"][0]["reason"] == SKIP_NO_TEMPLATE


def test_build_sentences_skips_receptive_only_items(db, guard):
    seed_item(db, "w-yaku02", kanji="拙者", pos="名詞", production_eligible=0)
    result = build_sentences(db, guard=guard)
    assert result["ok"] is False
    assert result["skipped"][0]["reason"] == SKIP_RECEPTIVE_ONLY


def test_build_sentences_screens_its_own_templates(db, guard):
    """A frame sentence that lands on a canary sentence is dropped, not returned."""
    fake = CanaryGuard.from_rows(
        [(canary_sentence_id("これは自転車です。"), "b1", "これは自転車です。", "")]
    )
    seed_item(db, "w-jiten3", kanji="自転車", pos="名詞")
    result = build_sentences(db, guard=fake, max_sentences=3)
    assert result["ok"] is True
    assert "これは自転車です。" not in [row["text"] for row in result["sentences"]]
    assert result["screened_out"][0]["template"] == "noun-kore-wa"
    assert result["screened_out"][0]["code"] == CANARY_VIOLATION


def test_build_sentences_rejects_a_bad_cap(db, guard):
    result = build_sentences(db, guard=guard, max_sentences=0)
    assert result["error"] == BAD_COUNT


def test_build_sentences_names_missing_items(db, guard):
    result = build_sentences(db, guard=guard, item_ids=["w-nope02"])
    assert result["error"] == NO_SUCH_ITEM


def test_build_sentences_refuses_a_sealed_item(db, guard):
    seed_item(db, "s-probe3", kind="sentence", kanji="週末は歴史の本を借りました。", sealed=1)
    result = build_sentences(db, guard=guard, item_ids=["s-probe3"])
    assert result["error"] == SEALED_ITEM


def test_build_sentences_refuses_without_a_loadable_canary_set(db):
    seed_item(db, "w-jiten4", kanji="自転車", pos="名詞")
    result = build_sentences(db)
    assert result["error"] == CANARY_SET_UNAVAILABLE
    assert result["sentences"] == []


# ---------------------------------------------------------------------------
# build_sentences — external material and the envelope contract
# ---------------------------------------------------------------------------

EXTERNAL = "駅前に赤い自転車を置いた。それから歴史の本を返した。"


def test_bare_string_source_is_refused(db, guard):
    seed_item(db, "w-jiten5", kanji="自転車", pos="名詞")
    result = build_sentences(db, guard=guard, source=EXTERNAL)
    assert result["ok"] is False
    assert result["error"] == UNENVELOPED_SOURCE
    assert result["sentences"] == []


def test_enveloped_source_without_confirmation_returns_the_challenge(db, guard):
    seed_item(db, "w-jiten6", kanji="自転車", pos="名詞")
    gate = EchoGate()
    envelope = wrap(EXTERNAL, source=SOURCE_MEDIA, locator="ep01#00:12:30")
    result = build_sentences(db, guard=guard, source=envelope, gate=gate)
    assert result["ok"] is False
    assert result["error"] == ECHO_BACK_REQUIRED
    assert result["sentences"] == []
    challenge = result["challenge"]
    assert challenge["envelope_id"] == envelope.envelope_id
    assert challenge["provenance"]["source"] == SOURCE_MEDIA
    assert challenge["chars"] == len(EXTERNAL)
    assert gate.pending() == 1


def test_confirmed_external_material_is_built_and_provenance_travels(db, guard):
    seed_item(db, "w-jiten7", kanji="自転車", pos="名詞")
    gate = EchoGate()
    envelope = wrap(EXTERNAL, source=SOURCE_MEDIA, locator="ep01#00:12:30")
    pending = build_sentences(db, guard=guard, source=envelope, gate=gate)
    confirmation = gate.confirm(pending["challenge"]["challenge_id"], EXTERNAL)

    result = build_sentences(
        db,
        guard=guard,
        source=envelope,
        confirmation=confirmation,
        gate=gate,
        max_sentences=3,
    )
    assert result["ok"] is True
    external = [row for row in result["sentences"] if row["origin"] == SOURCE_EXTERNAL]
    assert external, result["sentences"]
    assert external[0]["text"] == "駅前に赤い自転車を置いた。"
    assert external[0]["untrusted_origin"] is True
    assert external[0]["provenance"]["digest"] == envelope.digest
    assert result["source_provenance"]["provenance"]["locator"] == "ep01#00:12:30"
    # Real material comes first; frames only fill the remaining budget.
    assert result["sentences"][0]["origin"] == SOURCE_EXTERNAL


def test_a_spent_confirmation_cannot_authorise_a_second_build(db, guard):
    seed_item(db, "w-jiten8", kanji="自転車", pos="名詞")
    gate = EchoGate()
    envelope = wrap(EXTERNAL, source=SOURCE_MEDIA)
    pending = build_sentences(db, guard=guard, source=envelope, gate=gate)
    confirmation = gate.confirm(pending["challenge"]["challenge_id"], EXTERNAL)
    first = build_sentences(
        db, guard=guard, source=envelope, confirmation=confirmation, gate=gate
    )
    assert first["ok"] is True

    again = build_sentences(
        db, guard=guard, source=envelope, confirmation=confirmation, gate=gate
    )
    assert again["ok"] is False
    assert again["error"] == "confirmation_spent"


def test_a_confirmation_for_other_content_is_refused(db, guard):
    seed_item(db, "w-jiten9", kanji="自転車", pos="名詞")
    gate = EchoGate()
    mine = wrap(EXTERNAL, source=SOURCE_MEDIA)
    other = wrap("別の素材です。", source=SOURCE_MEDIA)
    pending = build_sentences(db, guard=guard, source=other, gate=gate)
    confirmation = gate.confirm(pending["challenge"]["challenge_id"], "別の素材です。")

    result = build_sentences(
        db, guard=guard, source=mine, confirmation=confirmation, gate=gate
    )
    assert result["ok"] is False
    assert result["error"] == "confirmation_mismatch"


def test_a_paraphrased_echo_never_reaches_build_sentences(db, guard):
    """The gate's own refusal, asserted here because this is the write path's door."""
    gate = EchoGate()
    envelope = wrap(EXTERNAL, source=SOURCE_MEDIA)
    seed_item(db, "w-jiten10", kanji="自転車", pos="名詞")
    pending = build_sentences(db, guard=guard, source=envelope, gate=gate)
    with pytest.raises(Exception) as excinfo:
        gate.confirm(pending["challenge"]["challenge_id"], EXTERNAL + "。")
    assert getattr(excinfo.value, "code", "") == ECHO_MISMATCH


def test_external_material_is_screened_against_the_canary_set(db, guard):
    """Media text that happens to be a canary sentence is not practice material."""
    seed_item(db, "w-souko2", kanji="倉庫", reading="そうこ", pos="名詞")
    gate = EchoGate()
    envelope = wrap(FAKE_BIKE, source=SOURCE_MEDIA, locator="ep02")
    pending = build_sentences(db, guard=guard, source=envelope, gate=gate)
    confirmation = gate.confirm(pending["challenge"]["challenge_id"], FAKE_BIKE)

    result = build_sentences(
        db,
        guard=guard,
        source=envelope,
        confirmation=confirmation,
        gate=gate,
        item_ids=["w-souko2"],
    )
    external = [
        row for row in result["sentences"] if row["origin"] == SOURCE_EXTERNAL
    ]
    assert external == []
    assert result["screened_out"][0]["origin"] == SOURCE_EXTERNAL
    assert result["screened_out"][0]["code"] == CANARY_VIOLATION
    assert FAKE_BIKE not in json.dumps(result, ensure_ascii=False)


def test_oversized_external_material_is_refused(db, guard):
    seed_item(db, "w-jiten11", kanji="自転車", pos="名詞")
    envelope = wrap("あ" * (MAX_SOURCE_CHARS + 1), source=SOURCE_MEDIA)
    result = build_sentences(db, guard=guard, source=envelope, gate=EchoGate())
    assert result["error"] == SOURCE_TOO_LARGE


def test_source_must_be_an_envelope_or_a_string(db, guard):
    with pytest.raises(TypeError):
        build_sentences(db, guard=guard, source=object())  # type: ignore[arg-type]


def test_blank_string_source_is_simply_no_source(db, guard):
    seed_item(db, "w-jiten12", kanji="自転車", pos="名詞")
    result = build_sentences(db, guard=guard, source="   ")
    assert result["ok"] is True
    assert result["external_lines_considered"] == 0
