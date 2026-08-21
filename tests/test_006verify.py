"""006 T036: is the assessment cadence from T033 actually measurable?

Core behavior 5 of ``.claude/skills/katagiri-study/SKILL.md`` (and its
operational companion, ``docs/katagiri/katagiri/70-drills/assessment-cadence.md``)
describes three cadence exercises in prose: a weekly mora-count dictation, a
weekly five-word pitch-pattern marking, and a monthly 60-second monologue.
That prose explicitly declines to claim these are measurable through
existing tools: "Whether these three are actually measurable through
existing tools alone -- no new tool, no new table -- is verified by fixtures
in a separate task (006 T036), not asserted here." This file is that
verification.

Each scenario below is a cold, invented fixture (never real vault or
curriculum data -- same discipline as ``test_dverify.py``) that drives the
*real* product tools (``start_session``, ``log_lesson``, ``log_observations``,
``log_error``, ``mcp_server.dictionary_lookup``, ``backup.copy_vault_snapshot``)
and then reads the result back out of the append-only ``event`` log (joined
to the ``lesson``/``observation`` table rows where the event payload itself
doesn't carry a field), proving the record kind is retrievable without any
new tool or table.

One test function per scenario, no shared mutable state beyond the normal
fixture -- a later task (T041) is expected to append more scenarios here.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from katagiri import backup
from katagiri import config as config_mod
from katagiri import mcp_server, session_tools
from katagiri.db import open_db
from katagiri.session_tools import (
    ERROR_EVENT,
    LESSON_CLOSE_EVENT,
    OBSERVATION_EVENT,
    log_error,
    log_lesson,
    log_observations,
    start_session,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# The three reserved topic slugs from assessment-cadence.md steps 6 / 6 / 5.
# Free text as far as ``log_lesson`` is concerned (no enum validation), but
# by convention passed verbatim every time -- mirroring the existing
# ``phase0-kana-dictation`` slug in stop_gate.py.
WEEKLY_MORA_DICTATION_TOPIC = "weekly-mora-dictation"
WEEKLY_PITCH_MARKING_TOPIC = "weekly-pitch-marking"
MONTHLY_MONOLOGUE_TOPIC = "monthly-monologue"

# The two log_error pattern names the cadence exercises reuse. Both must
# already exist elsewhere in the codebase -- see
# test_the_two_reused_log_error_patterns_already_existed_before_this_file
# below, which greps for them instead of taking this comment's word for it.
MORA_LENGTH_PATTERN = "mora-length"
DEVOICED_VOWELS_PATTERN = "devoiced-vowels"


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A bare database, no vault -- mirrors test_mcp_tools.py's ``db`` fixture."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    handle = open_db()
    try:
        yield handle
    finally:
        handle.close()
        config_mod.reset_config_cache()


def event_payloads(conn: sqlite3.Connection, event_type: str) -> list[dict]:
    """Every payload for one event type, oldest first -- same join shape as
    test_dverify.py uses to check the append-only log's content."""
    rows = conn.execute(
        "SELECT payload FROM event WHERE type = ? ORDER BY id", (event_type,)
    ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def lesson_row(conn: sqlite3.Connection, lesson_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM lesson WHERE id = ?", (lesson_id,)
    ).fetchone()
    assert row is not None, f"no lesson row for {lesson_id}"
    return dict(row)


def observation_row(conn: sqlite3.Connection, observation_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM observation WHERE id = ?", (observation_id,)
    ).fetchone()
    assert row is not None, f"no observation row for {observation_id}"
    return dict(row)


def seed_pitch_word(
    conn: sqlite3.Connection,
    *,
    seq: int,
    kanji: str,
    reading: str,
    accent: str,
    gloss: str,
) -> None:
    """Direct-INSERT a minimal jmdict entry plus its kanjium pitch row.

    Mirrors test_mcp_tools.py's ``seed_jmdict_entry`` helper, extended with
    the ``pitch_accent`` insert -- deliberately bypassing the real import
    pipeline (too heavy for a fixture; test_jmdict_import.py's ``Vendor``
    class runs the actual zip/checksum import and is the right tool for
    testing *that*, not for seeding a handful of fixture words here).

    ``accent`` is text, not int: kanjium's own upstream notation (see
    jmdict_import.py's ``_accent_rows``/``PITCH_TABLES`` handling) stores it
    as a string, one row per accent value.
    """
    conn.execute(
        "INSERT INTO jmdict_entry (seq, is_common, dict_version) VALUES (?, 0, 'fixture')",
        (seq,),
    )
    conn.execute(
        "INSERT INTO jmdict_kanji (seq, kanji) VALUES (?, ?)",
        (seq, kanji),
    )
    conn.execute(
        "INSERT INTO jmdict_reading (seq, reading) VALUES (?, ?)",
        (seq, reading),
    )
    conn.execute(
        "INSERT INTO jmdict_sense (seq, sense_idx, pos, gloss_en) VALUES (?, 1, ?, ?)",
        (seq, "n", gloss),
    )
    conn.execute(
        "INSERT INTO pitch_accent (surface, reading, accent, source_version) "
        "VALUES (?, ?, ?, 'fixture')",
        (kanji, reading, accent),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# provenance: the two reused log_error patterns are not invented here
# ---------------------------------------------------------------------------


def test_the_two_reused_log_error_patterns_already_existed_before_this_file():
    """Requirement #4: assert the pattern names, don't just assert *a* string.

    ``mora-length`` and ``devoiced-vowels`` are supposed to be reused from
    Core behavior 1's existing vocabulary, not invented for this cadence
    work. Grep the pre-existing sources instead of trusting a comment.
    """
    skill_md = (
        REPO_ROOT / ".claude" / "skills" / "katagiri-study" / "SKILL.md"
    ).read_text(encoding="utf-8")
    errors_ledger = (
        REPO_ROOT
        / "docs"
        / "katagiri"
        / "katagiri"
        / "60-review"
        / "errors.md"
    ).read_text(encoding="utf-8")

    for pattern in (MORA_LENGTH_PATTERN, DEVOICED_VOWELS_PATTERN):
        assert pattern in skill_md, (
            f"{pattern!r} missing from SKILL.md -- Core behavior 1's "
            "pattern-name examples should already list it"
        )
        assert pattern in errors_ledger, (
            f"{pattern!r} missing from the production error ledger -- "
            "it should already be a tracked pattern, occurrence count 0 or not"
        )


# ---------------------------------------------------------------------------
# scenario 1: weekly mora-count dictation
# ---------------------------------------------------------------------------


def test_a_fixture_week_produces_a_measurable_mora_count_dictation_record(conn):
    """Outcome 1 from assessment-cadence.md's dictation recipe (steps 4-6).

    A heard line ("vendor" content -- trusted first-party audio, no
    envelope: there is no ``SOURCE_*`` constant for vendored Irodori/kanjium
    material, same as the canary/curriculum fixtures in test_dverify.py)
    yields one mora-length error, one devoiced-vowel error, one closing
    observation, and one lesson closed under the reserved topic slug.
    """
    session = start_session(conn, tired=False)
    assert session["ok"] is True, session
    session_id = session["session_id"]

    mora_error = log_error(
        conn,
        said="ビール",
        correct="びいる",
        pattern=MORA_LENGTH_PATTERN,
        severity="medium",
        session_id=session_id,
    )
    assert mora_error["ok"] is True, mora_error

    devoiced_error = log_error(
        conn,
        said="です (voiced す)",
        correct="です (devoiced す)",
        pattern=DEVOICED_VOWELS_PATTERN,
        severity="low",
        session_id=session_id,
    )
    assert devoiced_error["ok"] is True, devoiced_error

    observed = log_observations(
        conn,
        {
            "task_type": "mora-dictation",
            "unassisted": True,
            "coverage_band": "80-95",
            "rubric_version": "v1",
            "produced": "びーる (2 mora errors logged separately)",
        },
        session_id=session_id,
    )
    assert observed["ok"] is True, observed

    closed = log_lesson(
        conn,
        topic=WEEKLY_MORA_DICTATION_TOPIC,
        objective="Transcribe one unheard Irodori line mora-by-mora from a single play.",
        session_id=session_id,
        closed=True,
    )
    assert closed["ok"] is True, closed
    assert closed["topic"] == WEEKLY_MORA_DICTATION_TOPIC

    # -- measurability: pull every piece back out of the append-only log.
    error_payloads = event_payloads(conn, ERROR_EVENT)
    patterns_seen = {payload["pattern"] for payload in error_payloads}
    assert MORA_LENGTH_PATTERN in patterns_seen
    assert DEVOICED_VOWELS_PATTERN in patterns_seen

    lesson_payloads = event_payloads(conn, LESSON_CLOSE_EVENT)
    (dictation_payload,) = [
        payload
        for payload in lesson_payloads
        if payload["topic"] == WEEKLY_MORA_DICTATION_TOPIC
    ]
    assert dictation_payload["lesson_id"] == closed["lesson_id"]

    observation_payloads = event_payloads(conn, OBSERVATION_EVENT)
    (dictation_observation,) = [
        payload
        for payload in observation_payloads
        if payload["task_type"] == "mora-dictation"
    ]
    # "produced" isn't in the event payload -- it only lands in the
    # observation table row, same join shape test_dverify.py uses for
    # mining_payload -> mined_id -> item.
    stored_observation = observation_row(conn, dictation_observation["observation_id"])
    assert "mora errors" in stored_observation["produced"]


# ---------------------------------------------------------------------------
# scenario 2: weekly five-word pitch-pattern marking
# ---------------------------------------------------------------------------


def test_a_fixture_week_produces_a_measurable_pitch_pattern_marking_record(conn):
    """Outcome 2 from assessment-cadence.md's pitch-marking recipe (steps 3-6).

    Four of the five fixture words (hashi/ame/kami/ima) and their accent
    numbers are the real documented examples from
    ``docs/katagiri/katagiri/35-phonology/pitch-accent.md``; the fifth
    (kyoushitsu) is fixture-only, to fill out "five words" without
    depending on JMdict actually being imported. All five are seeded
    directly (bypassing the real import pipeline, same rationale as
    ``seed_pitch_word``'s docstring) and then read back through the real
    ``lookup`` tool's underlying function, proving the kanjium number in
    step 3's table comes from the vendored data rather than being recalled.
    """
    seed_pitch_word(conn, seq=9001, kanji="橋", reading="はし", accent="2", gloss="bridge")
    seed_pitch_word(conn, seq=9002, kanji="雨", reading="あめ", accent="1", gloss="rain")
    seed_pitch_word(conn, seq=9003, kanji="紙", reading="かみ", accent="2", gloss="paper")
    seed_pitch_word(conn, seq=9004, kanji="今", reading="いま", accent="1", gloss="now")
    seed_pitch_word(
        conn, seq=9005, kanji="教室", reading="きょうしつ", accent="0", gloss="classroom"
    )

    words = [
        ("橋", "はし", "2"),
        ("雨", "あめ", "1"),
        ("紙", "かみ", "2"),
        ("今", "いま", "1"),
        ("教室", "きょうしつ", "0"),
    ]

    # step 3: read the kanjium number for every word via the real lookup path.
    looked_up_accents: dict[str, str] = {}
    for kanji, reading, _expected_accent in words:
        result = mcp_server.dictionary_lookup(conn, kanji)
        assert result["found"] is True, result
        (entry,) = result["entries"]
        (reading_row,) = [r for r in entry["readings"] if r["reading"] == reading]
        (pitch,) = reading_row["pitch"]
        looked_up_accents[kanji] = pitch

    for kanji, _reading, expected_accent in words:
        assert looked_up_accents[kanji] == expected_accent

    session = start_session(conn, tired=False)
    session_id = session["session_id"]

    # step 4: one mismatch carrying the length-creep tell -> reuse mora-length.
    mismatch = log_error(
        conn,
        said="[1] (marked the accented mora louder and longer)",
        correct="[2]",
        pattern=MORA_LENGTH_PATTERN,
        severity="low",
        session_id=session_id,
    )
    assert mismatch["ok"] is True, mismatch

    # step 5: one closing observation -- pitch-height-only mismatches (if any)
    # go in the note text, not a log_error call, per the drill's own step 4.
    observed = log_observations(
        conn,
        {
            "task_type": "pitch-marking",
            "unassisted": True,
            "coverage_band": ">=95",
            "rubric_version": "v1",
            "produced": "4/5 matched kanjium; 1 pitch-height-only mismatch on 教室",
        },
        session_id=session_id,
    )
    assert observed["ok"] is True, observed

    closed = log_lesson(
        conn,
        topic=WEEKLY_PITCH_MARKING_TOPIC,
        objective="Mark pitch pattern for five words in kana only, check against kanjium.",
        session_id=session_id,
        closed=True,
    )
    assert closed["ok"] is True, closed
    assert closed["topic"] == WEEKLY_PITCH_MARKING_TOPIC

    # -- measurability, same pull-it-back-out-of-the-log discipline.
    error_payloads = event_payloads(conn, ERROR_EVENT)
    assert any(payload["pattern"] == MORA_LENGTH_PATTERN for payload in error_payloads)

    lesson_payloads = event_payloads(conn, LESSON_CLOSE_EVENT)
    (pitch_payload,) = [
        payload
        for payload in lesson_payloads
        if payload["topic"] == WEEKLY_PITCH_MARKING_TOPIC
    ]
    assert pitch_payload["lesson_id"] == closed["lesson_id"]

    observation_payloads = event_payloads(conn, OBSERVATION_EVENT)
    (pitch_observation,) = [
        payload
        for payload in observation_payloads
        if payload["task_type"] == "pitch-marking"
    ]
    stored_observation = observation_row(conn, pitch_observation["observation_id"])
    assert "kanjium" in stored_observation["produced"]


# ---------------------------------------------------------------------------
# scenario 3: monthly 60-second monologue
# ---------------------------------------------------------------------------


def test_a_fixture_month_produces_a_measurable_monologue_artifact_record(conn, tmp_path):
    """Outcome 3 from assessment-cadence.md's monologue recipe (steps 3-5).

    Deliberately has no ``log_error`` call: the drill's own step 4 has no
    error path for the monologue at all (only a trend note in
    ``log_observations``), so requirement #4 ("each record kind's error path
    lands on an existing pattern") does not apply here -- there is no error
    path to check, by design, not by omission.

    The vault-backup half is checked against the real
    ``copy_vault_snapshot`` -- the function the drill cites as already
    covering both audio extensions (FR-007/T005) -- rather than only against
    the ``VAULT_SNAPSHOT_EXTENSIONS`` constant, so a regression in the walk
    itself would be caught too.
    """
    vault = tmp_path / "vault"
    monologue_dir = vault / "80-progress" / "monologues"
    monologue_dir.mkdir(parents=True)
    artifact = monologue_dir / "2026-08-monologue.mp3"
    artifact.write_bytes(b"fixture audio, not a real recording")
    relative_path = artifact.relative_to(vault).as_posix()

    # Excluded regardless of extension -- proves the exclusion is live, not
    # merely documented.
    (vault / "local").mkdir()
    (vault / "local" / "scratch-monologue.mp3").write_bytes(b"should never be backed up")
    (vault / ".derived").mkdir()
    (vault / ".derived" / "stale-monologue.mp3").write_bytes(b"rebuildable, not backed up")

    assert ".mp3" in backup.VAULT_SNAPSHOT_EXTENSIONS
    assert ".wav" in backup.VAULT_SNAPSHOT_EXTENSIONS

    snapshot_path = backup.copy_vault_snapshot(vault, dest_dir=tmp_path / "backups")
    with zipfile.ZipFile(snapshot_path) as archive:
        names = archive.namelist()
    assert relative_path in names, names
    assert not any("local" in name for name in names), names
    assert not any(".derived" in name for name in names), names

    session = start_session(conn, tired=False)
    session_id = session["session_id"]

    observed = log_observations(
        conn,
        {
            "task_type": "monologue",
            "unassisted": True,
            "coverage_band": ">=95",
            "rubric_version": "v1",
            "media_ref": relative_path,
            "produced": "62s, 2 disfluencies (prior month: 58s, 4 disfluencies)",
        },
        session_id=session_id,
    )
    assert observed["ok"] is True, observed

    closed = log_lesson(
        conn,
        topic=MONTHLY_MONOLOGUE_TOPIC,
        objective="Speak unscripted for about 60 seconds; compare trend to last month.",
        session_id=session_id,
        closed=True,
    )
    assert closed["ok"] is True, closed
    assert closed["topic"] == MONTHLY_MONOLOGUE_TOPIC

    # -- measurability: the artifact reference and its trend both come back
    # out of the append-only log, joined the same way test_dverify.py joins
    # its mining event back to the item it created.
    observation_payloads = event_payloads(conn, OBSERVATION_EVENT)
    (monologue_observation,) = [
        payload
        for payload in observation_payloads
        if payload["task_type"] == "monologue"
    ]
    stored_observation = observation_row(conn, monologue_observation["observation_id"])
    assert stored_observation["media_ref"] == relative_path
    assert "disfluencies" in stored_observation["produced"]

    lesson_payloads = event_payloads(conn, LESSON_CLOSE_EVENT)
    (monologue_payload,) = [
        payload
        for payload in lesson_payloads
        if payload["topic"] == MONTHLY_MONOLOGUE_TOPIC
    ]
    assert monologue_payload["lesson_id"] == closed["lesson_id"]
