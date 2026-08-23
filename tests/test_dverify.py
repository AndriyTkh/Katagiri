r"""kata-dvf: the Phase D gate — the teacher loop, verified against real modules.

Fourth in the chain after ``tests/test_averify.py``, ``tests/test_bverify.py``
and ``tests/test_cverify.py``, and read the same way: a *cold* verification
harness, not a unit-test suite. Nothing here mocks a Katagiri module. Every
assertion is made against the real public API of
:mod:`katagiri.session_tools`, :mod:`katagiri.exercises`,
:mod:`katagiri.intelligence`, :mod:`katagiri.lesson_memory`,
:mod:`katagiri.envelope` and :mod:`katagiri.today_export`, driven over a
migrated database that the product's own ``open_db``/config path opened.

**Fixtures only, and never the learner's data.** The vault this file builds is a
throwaway directory holding two fixture files at the *real* vault-relative
paths: a synthetic sealed set at :data:`~katagiri.exercises.CANARY_VAULT_PATH`
whose rows are invented sentences carrying real ids, and a fixture
``curriculum.md`` at :data:`~katagiri.intelligence.CURRICULUM_VAULT_PATH`
declaring a four-node ``prereqs``/``unlocks`` chain. The repository's real vault
under ``docs/katagiri`` and the real 200-sentence canary set are never read,
never written and never quoted — which is also why no real sealed sentence
appears anywhere below (the convention ``tests/test_exercises.py`` established).

**The seven outcomes.** ``specs/003-phase-d-teacher-loop/quickstart.md``:20–28
names what Phase D is answerable for, and each is a named test here:

1. the full lesson loop on fixtures — i+1 pick → exercise → ``log_error`` →
   mine — with the artifacts landing in **both** the append-only event log and a
   rendered ``Today.md`` (the ``lesson_memory`` section, reached through
   :data:`katagiri.today_export.SECTIONS`, not by calling the builder directly);
2. ``log_observations`` without ``rubric_version`` → rejected, ``written == 0``,
   and *nothing* of the batch written;
3. ``start_session`` returns exactly one prescribed action, a dict and never a
   list, and reflects a prior ``next_step`` across two sessions;
4. a sentence at 100% vocabulary coverage whose grammar sits behind an
   unmastered prerequisite is still excluded from ``find_i_plus_one``;
5. a media-derived write without echo-back confirmation → refused;
6. a canary sentence reaching a drill → the validator screams;
7. cumulative: scenarios A..C run again, in one session, in order, on one
   database, and still hold.

**Why section 7 is written the way it is.** "Scenarios A..C still green" is not
a claim that three test functions pass somewhere in the suite — pytest gives
each of them a fresh database, so passing separately proves only that each works
from nothing. Composition is the actual risk in a loop whose every write is
append-only: the second lesson's ``next_step`` has to outrank the first's, a
refused observation batch must not consume the confirmation the retry needs, and
the prescription ladder has to keep answering with one action once the log has
history in it. So :func:`scenario_a`, :func:`scenario_b` and :func:`scenario_c`
are module-level functions, each self-contained and each asserting its own
outcome; the individual tests call one apiece, and
:class:`TestCumulativeScenarios` calls all three against a single ``world``, in
order, then checks that the composed event log is the sum of the three.

The scenario helpers therefore assert only what is true of a log that already
has history. In particular :func:`scenario_c` does not assert "an empty log
prescribes the first lesson" — that is checked once, separately, where an empty
log is guaranteed — and it asserts that a consumed ``next_step`` is not
prescribed *again* by naming the lesson id rather than by expecting a particular
fallback, since which fallback appears depends on what earlier scenarios left
behind. That is the point of running them together.

Like C-verify this file needs the vendored UniDic: coverage measurement is a
function of the tokenizer, so ``find_i_plus_one`` cannot answer without it. The
module skips with that reason rather than failing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import exercises, intelligence, lesson_memory, session_tools, today_export
from katagiri import tokenizer as tok
from katagiri.db import open_db
from katagiri.envelope import (
    SOURCE_MEDIA,
    EchoGate,
    Envelope,
    reset_default_gate,
    wrap,
)
from katagiri.exercises import (
    CANARY_ECHO,
    CANARY_VAULT_PATH,
    CANARY_VIOLATION,
    ECHO_BACK_REQUIRED,
    READ_TO_MEANING,
    SOURCE_EXTERNAL,
    UNENVELOPED_SOURCE,
    build_sentences,
    canary_sentence_id,
    gen_exercise,
    reset_canary_cache,
)
from katagiri.intelligence import (
    CURRICULUM_VAULT_PATH,
    EDGE_PREREQ,
    GATE_UNREACHABLE_GRAMMAR,
    find_i_plus_one,
    import_curriculum,
)
from katagiri.session_tools import (
    ACTION_KINDS,
    ACTION_NEXT_STEP,
    CONFIRMATION_REQUIRED,
    ENVELOPE_REQUIRED,
    ERROR_EVENT,
    LESSON_CLOSE_EVENT,
    MINING_EVENT,
    MISSING_RUBRIC_VERSION,
    OBSERVATION_EVENT,
    OBSERVATIONS_REJECTED,
    SESSION_OPEN_EVENT,
    add_vocab,
    confirm_untrusted,
    log_error,
    log_lesson,
    log_observations,
    reset_staged,
    stage_untrusted,
    staged_envelope,
    start_session,
)

def _dicdir_available() -> bool:
    try:
        tok.dicdir_path()
    except tok.TokenizerError:
        return False
    return True


#: Coverage is a function of the tokenizer, so the i+1 half of this gate needs
#: the vendored dictionary. Skipped with the reason rather than failed —
#: mirrored from ``test_cverify.py`` rather than imported, like everything else
#: in this family.
pytestmark = pytest.mark.skipif(
    not _dicdir_available(),
    reason=(
        "vendored UniDic 3.1.0 is absent (vendor/unidic/unidic); see "
        "vendor/README.md"
    ),
)


# ---------------------------------------------------------------------------
# 0. Fixture data
# ---------------------------------------------------------------------------
#
# Everything below is invented. The three "sealed" sentences stand in for the
# real canary set and are the same three ``tests/test_exercises.py`` uses,
# mirrored rather than imported for the reason every constant in this family is
# mirrored: a gate that asks another test file what its fixture contains cannot
# notice that fixture changing underneath it.

FAKE_BIKE = "赤い自転車が倉庫の前に止まっています。"
FAKE_BIKE_READING = "あかいじてんしゃがそうこのまえにとまっています。"
FAKE_MUST = "毎朝の体操を続けなければなりません。"
FAKE_KATA = "テレビのニュースで新しい制度の説明を聞きました。"

#: How many sentences the fixture sealed set holds. Asserted on the generators'
#: own report, so a guard that silently loaded something else fails here.
FAKE_CANARY_COUNT = 3


def fake_canary_rows() -> list[tuple[str, str, str, str]]:
    return [
        (canary_sentence_id(FAKE_BIKE), "b1", FAKE_BIKE, FAKE_BIKE_READING),
        (canary_sentence_id(FAKE_MUST), "b3", FAKE_MUST, ""),
        (canary_sentence_id(FAKE_KATA), "b4", FAKE_KATA, ""),
    ]


def fake_canary_markdown() -> str:
    lines = [
        "---",
        "schema: 2",
        "type: meta",
        "sealed: true",
        "---",
        "",
        "# Synthetic set (D-verify fixture; no real sealed sentence is here)",
        "",
        "| id | band | japanese | reading (kana) | english |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cid, band, japanese, reading in fake_canary_rows():
        lines.append(f"| {cid} | {band} | {japanese} | {reading} | (invented) |")
    return "\n".join(lines) + "\n"


#: A fixture ``curriculum.md`` in the real file's shape: a fenced node block of
#: ``id:``/``prereqs:``/``unlocks:`` declarations. The chain is the real phase-1
#: one (copula → wa → o → masu) because the whole point of outcome 4 is a wall
#: partway up a chain, and ``unlocks`` is present because the quickstart's
#: prerequisites name it — an unlock edge is reported but never walked, and this
#: gate would notice if it started being treated as a requirement.
FIXTURE_CURRICULUM = """---
schema: 1
type: index
---

# D-verify fixture curriculum

## Phase 1

```yaml
id: g-desu-copula
level: A0
---
id: g-wa-topic
prereqs: [g-desu-copula]
level: A0
---
id: g-o-object
prereqs: [g-wa-topic]
level: A1
---
id: g-masu-form
prereqs: [g-o-object]
level: A1
unlocks: [g-te-form]
```
"""

#: The four chain nodes plus the unlock target, which the importer stubs.
CURRICULUM_IDS = (
    "g-desu-copula",
    "g-wa-topic",
    "g-o-object",
    "g-masu-form",
    "g-te-form",
)

#: Mastered in the fixture world. ``g-o-object`` is deliberately left
#: unmastered: it is the wall coverage cannot see (outcome 4).
MASTERED = ("g-desu-copula", "g-wa-topic")

TS = "2026-08-01T00:00:00Z"

#: Two 100%-coverage sentences. Identical in every respect the vocabulary gate
#: can see, and different only in their grammar annotation — which is what makes
#: outcome 4 an observation rather than an inference.
GOOD_SENTENCE = "猫です。"
WALL_SENTENCE = "犬です。"
GOOD_ID = "sent-good"
WALL_ID = "sent-wall"
CANARY_ITEM_ID = "sent-canary"

#: Mined in scenario A. Absent from every other fixture, so finding its item row
#: and its ``mining`` event proves the mine happened rather than that something
#: else matched.
MINED_WORD = "電車"
MINED_READING = "でんしゃ"

#: The line the learner heard. Externally-sourced, so it may only reach a write
#: inside an envelope — never as a ``str``.
MEDIA_LINE = "この電車は次の駅で止まります。"


# ---------------------------------------------------------------------------
# 1. The cold world: a temporary %LOCALAPPDATA%, a fixture vault, a real db
# ---------------------------------------------------------------------------


class World:
    """One cold Phase D world: a migrated database and a fixture vault."""

    def __init__(self, conn: sqlite3.Connection, vault: Path) -> None:
        self.conn = conn
        self.vault = vault

    # -- event log readers -------------------------------------------------

    def event_count(self, event_type: str) -> int:
        row = self.conn.execute(
            "SELECT count(*) AS n FROM event WHERE type = ?", (event_type,)
        ).fetchone()
        return int(row["n"])

    def event_types(self) -> set[str]:
        return {
            str(row["type"]) for row in self.conn.execute("SELECT DISTINCT type FROM event")
        }

    def row_count(self, table: str) -> int:
        row = self.conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    def today_text(self) -> str:
        """Render and read ``<vault>/.derived/Today.md`` through the real writer.

        Deliberately through :func:`katagiri.today_export.write_today` rather
        than by calling the lesson-memory builder: outcome 1 is about the section
        reaching the *page*, which means going through the registry
        (:data:`katagiri.today_export.SECTIONS`), the frontmatter and the atomic
        write, and the assertion should fail if the builder is registered
        nowhere.
        """
        target = today_export.write_today(self.conn, self.vault)
        return target.read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A migrated database, a fixture vault, and the curriculum imported.

    Function-scoped on purpose. Each of the outcome tests gets a database with no
    history, so a passing assertion is about the tool and not about what an
    earlier test happened to leave behind — and section 7 gets exactly one such
    world and runs three scenarios through it, which is where composition is
    checked.

    The vault is a throwaway directory carrying the two fixture files at their
    real vault-relative paths, so the product's own vault plumbing
    (:func:`katagiri.exercises.canary_set_path`,
    :func:`katagiri.intelligence.curriculum_path`) is what finds them.
    """
    app_data = tmp_path / "AppData"
    (app_data / "Katagiri").mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()

    canary = vault.joinpath(*CANARY_VAULT_PATH.split("/"))
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text(fake_canary_markdown(), encoding="utf-8")

    curriculum = vault.joinpath(*CURRICULUM_VAULT_PATH.split("/"))
    curriculum.parent.mkdir(parents=True, exist_ok=True)
    curriculum.write_text(FIXTURE_CURRICULUM, encoding="utf-8")

    db_path = tmp_path / "katagiri.db"
    (app_data / "Katagiri" / "config.toml").write_text(
        f'db_path = "{db_path.as_posix()}"\n'
        f'scratch_root = "{(tmp_path / "scratch").as_posix()}"\n'
        f'vault_path = "{vault.as_posix()}"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("LOCALAPPDATA", str(app_data))
    config_mod.reset_config_cache()
    reset_canary_cache()
    reset_default_gate()
    reset_staged()

    conn = open_db()
    try:
        # The fixture vault really is what the product resolves to.
        assert exercises.canary_set_path() == canary
        assert intelligence.curriculum_path(vault) == curriculum

        imported = import_curriculum(conn, root=vault)
        assert imported["ok"] is True, imported
        seed_world(conn)
        yield World(conn, vault)
    finally:
        conn.close()
        config_mod.reset_config_cache()
        reset_canary_cache()
        reset_default_gate()
        reset_staged()


# ---------------------------------------------------------------------------
# 2. Seeding helpers (direct INSERTs, so timestamps are exactly what is said)
# ---------------------------------------------------------------------------


def seed_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kind: str = "word",
    kanji: str | None = None,
    reading: str | None = None,
    pos: str | None = None,
    home_topic: str | None = None,
    production_eligible: int = 1,
    sealed: int = 0,
) -> str:
    conn.execute(
        """
        INSERT INTO item (id, kind, home_topic, kanji, reading, pos,
                          production_eligible, sealed, created_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            kind,
            home_topic,
            kanji,
            reading,
            pos,
            production_eligible,
            sealed,
            TS,
        ),
    )
    return item_id


def mark_known(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?, 'known', ?)",
        (item_id, TS),
    )


def seed_sentence(
    conn: sqlite3.Connection, item_id: str, jp: str, *, sealed: int = 0
) -> str:
    seed_item(conn, item_id, kind="sentence", sealed=sealed)
    conn.execute(
        "INSERT INTO sentence_text (item_id, jp) VALUES (?, ?)", (item_id, jp)
    )
    return item_id


def seed_world(conn: sqlite3.Connection) -> None:
    """The vocabulary and material every scenario shares.

    Both content words are marked known, so both fixture sentences measure at
    100% vocabulary coverage; the only difference between them is which grammar
    point they are annotated with. ``sent-canary`` carries a *fixture* canary
    sentence as its text — the route outcome 6 is about.
    """
    seed_item(conn, "w-neko", kanji="猫", reading="ねこ", pos="名詞")
    mark_known(conn, "w-neko")
    seed_item(conn, "w-inu", kanji="犬", reading="いぬ", pos="名詞")
    mark_known(conn, "w-inu")

    seed_sentence(conn, GOOD_ID, GOOD_SENTENCE)
    seed_sentence(conn, WALL_ID, WALL_SENTENCE)
    seed_sentence(conn, CANARY_ITEM_ID, FAKE_BIKE)

    for node in MASTERED:
        mark_known(conn, node)
    conn.commit()


def envelope_for(text: str, *, locator: str = "ep01 12:03") -> tuple[Envelope, str]:
    """Stage ``text`` as media-derived content; return its envelope and challenge.

    The real three-move ceremony, through the real staging buffer: this is how an
    MCP adapter hands an envelope from one tool call to the next, so a test that
    constructed an :class:`~katagiri.envelope.Envelope` by hand would be testing
    a path no caller uses.
    """
    staged = stage_untrusted(text, source=SOURCE_MEDIA, locator=locator)
    assert staged["ok"] is True, staged
    assert staged["envelope_id"], staged
    # The excerpt is display; the content is not in the answer.
    assert staged["chars"] == len(text)
    return staged_envelope(staged["envelope_id"]), staged["challenge_id"]


# ---------------------------------------------------------------------------
# 3. The scenarios, as re-invocable functions (see the module docstring)
# ---------------------------------------------------------------------------


def scenario_a(world: World) -> dict[str, Any]:
    """Outcome 1: the full lesson loop, landing in the log **and** on the page.

    One pass of the loop the phase exists for: open a session, pick material
    that is i+1 on both axes, drill it, log the mistake it produced, mine the
    word that mistake exposed, and close the lesson with a next step. Then the
    two places the result has to be visible — the append-only event log, and the
    rendered ``Today.md``.

    Both halves are asserted because either alone is satisfiable by a broken
    build: a loop that writes events nobody can read has no memory, and a page
    rendered from state that was never logged is a story.
    """
    conn = world.conn
    before = {
        event_type: world.event_count(event_type)
        for event_type in (
            SESSION_OPEN_EVENT,
            LESSON_CLOSE_EVENT,
            ERROR_EVENT,
            MINING_EVENT,
        )
    }

    # -- open ---------------------------------------------------------------
    session = start_session(conn)
    assert session["ok"] is True, session
    session_id = session["session_id"]
    assert isinstance(session["action"], dict), session["action"]

    # -- i+1 pick: both axes, in one call ----------------------------------
    picked = find_i_plus_one(
        conn,
        [
            {"id": GOOD_ID, "text": GOOD_SENTENCE, "grammar_ids": ["g-wa-topic"]},
            {"id": WALL_ID, "text": WALL_SENTENCE, "grammar_ids": ["g-masu-form"]},
        ],
        include_gated=True,
        score_difficulty=False,
    )
    assert picked["ok"] is True, picked
    assert [entry["id"] for entry in picked["candidates"]] == [GOOD_ID], picked
    assert [entry["id"] for entry in picked["gated"]] == [WALL_ID], picked
    # Outcome 4, in the middle of the loop rather than off to one side: the
    # sentence that was excluded had nothing wrong with its vocabulary.
    (gated,) = picked["gated"]
    assert gated["coverage"]["known_pct"] == 100.0, gated
    assert gated["gated_by"] == [GATE_UNREACHABLE_GRAMMAR], gated

    # -- exercise ----------------------------------------------------------
    drilled = gen_exercise(conn, item_ids=[GOOD_ID], count=1)
    assert drilled["ok"] is True, drilled
    assert drilled["returned"] == 1, drilled
    (drill,) = drilled["exercises"]
    assert drill["item_id"] == GOOD_ID
    assert drill["canary_screened"] is True
    assert GOOD_SENTENCE in drill["material"]
    # Screened against the fixture set, not against nothing.
    assert drilled["canary_sentences_screened_against"] == FAKE_CANARY_COUNT

    built = build_sentences(conn, item_ids=["w-neko"], max_sentences=2)
    assert built["ok"] is True, built
    assert built["sentences"], built
    assert all("猫" in row["text"] for row in built["sentences"]), built
    assert all(row["canary_screened"] is True for row in built["sentences"]), built

    # -- log_error ---------------------------------------------------------
    logged = log_error(
        conn,
        said="猫がです",
        correct="猫です",
        pattern="copula after a bare noun",
        severity="medium",
        item_id="w-neko",
        session_id=session_id,
    )
    assert logged["ok"] is True, logged
    assert logged["pattern"] == "copula after a bare noun"

    # -- mine --------------------------------------------------------------
    mined = add_vocab(
        conn,
        word=MINED_WORD,
        reading=MINED_READING,
        meaning="train",
        pos="名詞",
        topic="transport",
        session_id=session_id,
    )
    assert mined["ok"] is True, mined
    assert mined["created"] is True, mined
    mined_id = mined["item_id"]
    assert conn.execute(
        "SELECT kanji FROM item WHERE id = ?", (mined_id,)
    ).fetchone()["kanji"] == MINED_WORD

    # -- close, with the next step and one open thread ---------------------
    topic = f"copula drill {session_id[-6:]}"
    next_step = f"Re-test the copula cold, then mine one word from {MINED_WORD}."
    thread = "Why is が wrong before です here?"
    closed = log_lesson(
        conn,
        topic=topic,
        objective="Say what something is with です, unassisted.",
        session_id=session_id,
        closed=True,
        next_step=next_step,
        revisit_after=0,
        unresolved=[thread],
    )
    assert closed["ok"] is True, closed
    assert closed["closed"] is True
    assert closed["next_step"] == next_step
    assert len(closed["unresolved_ids"]) == 1

    # -- half one: the event log ------------------------------------------
    for event_type in before:
        assert world.event_count(event_type) == before[event_type] + 1, (
            f"{event_type} did not land in the append-only log"
        )
    mining_payload = json.loads(
        conn.execute(
            "SELECT payload FROM event WHERE type = ? AND item_id = ?",
            (MINING_EVENT, mined_id),
        ).fetchone()["payload"]
    )
    assert mining_payload["word"] == MINED_WORD
    assert mining_payload["source"] == "add_vocab"

    # -- half two: the rendered page --------------------------------------
    page = world.today_text()
    assert "generated: true" in page, "the page did not come from the exporter"
    assert f"## {lesson_memory.SECTION_HEADING}" in page, page[-2000:]
    # The one prescribed action the *next* session would get, on the page.
    assert next_step in page, page[-2000:]
    assert topic in page, page[-2000:]
    assert thread in page, page[-2000:]

    return {
        "session_id": session_id,
        "lesson_id": closed["lesson_id"],
        "topic": topic,
        "next_step": next_step,
        "mined_item_id": mined_id,
    }


def scenario_b(world: World) -> dict[str, Any]:
    """Outcome 2: an observation without ``rubric_version`` is rejected entire.

    Two calls and one count. The refused batch names the missing field and
    writes nothing — not the good record beside it, which is the part that
    matters: this series is the D6 gate's instrument, the log is append-only, and
    a batch that landed half-written with one guessed ``rubric_version`` could
    never be corrected. Then the same batch *with* the field, so the refusal is
    known to be about ``rubric_version`` and not about the call being malformed
    in some other way.
    """
    conn = world.conn
    observations_before = world.row_count("observation")
    events_before = world.event_count(OBSERVATION_EVENT)

    session = start_session(conn)
    session_id = session["session_id"]

    good = {
        "task_type": "cloze",
        "unassisted": True,
        "coverage_band": ">=95",
        "rubric_version": "v1",
    }
    missing = {
        "task_type": "translate_en_jp",
        "unassisted": False,
        "coverage_band": "80-95",
    }

    refused = log_observations(conn, [good, missing], session_id=session_id)
    assert refused["ok"] is False, refused
    assert refused["error"] == OBSERVATIONS_REJECTED, refused
    assert refused["written"] == 0, refused
    assert refused["observation_ids"] == [], refused
    assert [entry["field"] for entry in refused["rejected"]] == ["rubric_version"]
    assert refused["rejected"][0]["error"] == MISSING_RUBRIC_VERSION
    assert refused["rejected"][0]["index"] == 1
    assert refused["note"], "a refusal with no explanation is not an answer"

    # Nothing at all was written: not the batch, and not the valid record in it.
    assert world.row_count("observation") == observations_before
    assert world.event_count(OBSERVATION_EVENT) == events_before

    # The positive control. One field is the whole difference.
    accepted = log_observations(
        conn, [good, {**missing, "rubric_version": "v1"}], session_id=session_id
    )
    assert accepted["ok"] is True, accepted
    assert accepted["written"] == 2, accepted
    assert accepted["rubric_versions"] == ["v1"], accepted
    assert accepted["unassisted"] == 1, accepted
    assert world.row_count("observation") == observations_before + 2
    assert world.event_count(OBSERVATION_EVENT) == events_before + 2

    return {"session_id": session_id, "written": accepted["written"]}


def scenario_c(world: World) -> dict[str, Any]:
    """Outcome 3: exactly one action, a dict, and a ``next_step`` carried forward.

    Three sessions. The first establishes the shape — one ``action``, a mapping,
    with no list anywhere in the answer for a menu to hide in. Then a lesson is
    closed carrying a distinctive ``next_step``, and the second session must
    prescribe *that*, naming the lesson it came from: the continuity claim
    (FR-006, written at close and read at open). The third session must not
    prescribe it again — a next step is consumed once, or the loop stalls
    forever on the first thing the learner avoided.

    Nothing here assumes an empty log, which is what lets section 7 re-invoke it
    after scenarios A and B have written history. The third assertion is made by
    *lesson id* rather than by expecting a particular fallback, because which
    fallback appears legitimately depends on what else is in the log.
    """
    conn = world.conn
    session_opens_before = world.event_count(SESSION_OPEN_EVENT)

    first = start_session(conn)
    assert first["ok"] is True, first
    action = first["action"]
    assert isinstance(action, dict), f"action must be one dict, got {type(action)}"
    assert not isinstance(action, list)
    assert action["kind"] in ACTION_KINDS, action
    assert action["instruction"] and action["rationale"], action
    # A menu would have to arrive as a list somewhere in the answer. None does.
    assert [key for key, value in first.items() if isinstance(value, list)] == [], first

    marker = first["session_id"][-6:]
    topic = f"te-form {marker}"
    next_step = f"Drill the て-form of する ten times cold ({marker})."
    closed = log_lesson(
        conn,
        topic=topic,
        objective=f"Chain two actions with て, unassisted ({marker}).",
        session_id=first["session_id"],
        closed=True,
        next_step=next_step,
    )
    assert closed["ok"] is True, closed
    lesson_id = closed["lesson_id"]

    second = start_session(conn)
    assert second["ok"] is True, second
    carried = second["action"]
    assert isinstance(carried, dict), carried
    assert carried["kind"] == ACTION_NEXT_STEP, carried
    assert carried["instruction"] == next_step, carried
    assert carried["lesson_id"] == lesson_id, carried
    assert carried["topic"] == topic, carried
    assert carried["source"] == "lesson.next_step", carried
    assert second["session_id"] != first["session_id"]

    third = start_session(conn)
    assert isinstance(third["action"], dict), third
    assert third["action"]["lesson_id"] != lesson_id, (
        "the same next step was prescribed twice; a next step is consumed once"
    )

    # The prescription itself is in the log, which is how the ladder knows.
    assert world.event_count(SESSION_OPEN_EVENT) == session_opens_before + 3
    prescribed = json.loads(
        conn.execute(
            "SELECT payload FROM event WHERE id = ?", (second["event_id"],)
        ).fetchone()["payload"]
    )
    assert prescribed["action"]["lesson_id"] == lesson_id
    assert prescribed["tired_mode"] is False

    return {
        "lesson_id": lesson_id,
        "topic": topic,
        "next_step": next_step,
        "session_ids": [
            first["session_id"],
            second["session_id"],
            third["session_id"],
        ],
    }


# ---------------------------------------------------------------------------
# 4. Outcome 1 — the full lesson loop
# ---------------------------------------------------------------------------


def test_the_full_lesson_loop_lands_in_the_event_log_and_on_the_page(world):
    """Quickstart outcome 1. Scenario A, run once against a fresh world."""
    scenario_a(world)


def test_the_lesson_memory_section_is_reached_through_the_page_registry(world):
    """The page grows at the registry seam, and this is the seam being used.

    Outcome 1 says the loop's artifacts reach ``Today.md``. That is only a claim
    about the *product* if the section is registered rather than rendered by a
    test calling the builder itself — so the registry is asserted directly, by
    key, and then the page is checked to carry the heading that key owns.
    """
    keys = [getattr(builder, "section_key", None) for builder in today_export.SECTIONS]
    assert lesson_memory.SECTION_KEY in keys, keys

    page = world.today_text()
    assert f"## {lesson_memory.SECTION_HEADING}" in page, page

    # Never empty, even with nothing recorded: a vanished section reads as
    # "nothing owed" when it may mean "nothing logged".
    assert "No lessons have been recorded yet" in page, page


# ---------------------------------------------------------------------------
# 5. Outcome 2 — the mandatory-field gate
# ---------------------------------------------------------------------------


def test_log_observations_without_a_rubric_version_is_rejected(world):
    """Quickstart outcome 2. Scenario B, run once against a fresh world."""
    scenario_b(world)


def test_a_refused_batch_leaves_the_observation_log_untouched(world):
    """The refusal is all-or-nothing measured from an empty table.

    Scenario B measures deltas, so it holds inside a session that already logged
    observations. This one starts from zero, which is the only way to state the
    strong form: after a refused batch the table is *empty*, not merely no
    larger than it was.
    """
    conn = world.conn
    assert world.row_count("observation") == 0
    session = start_session(conn)

    refused = log_observations(
        conn,
        {"task_type": "shadow", "unassisted": True, "coverage_band": ">=95"},
        session_id=session["session_id"],
    )
    assert refused["ok"] is False
    assert refused["error"] == OBSERVATIONS_REJECTED
    assert refused["written"] == 0
    assert world.row_count("observation") == 0
    assert world.event_count(OBSERVATION_EVENT) == 0

    # A blank string is not a version either — nothing here is defaulted.
    blank = log_observations(
        conn,
        {
            "task_type": "shadow",
            "unassisted": True,
            "coverage_band": ">=95",
            "rubric_version": "   ",
        },
        session_id=session["session_id"],
    )
    assert blank["error"] == OBSERVATIONS_REJECTED, blank
    assert blank["rejected"][0]["error"] == MISSING_RUBRIC_VERSION
    assert world.row_count("observation") == 0


# ---------------------------------------------------------------------------
# 6. Outcome 3 — one prescribed action, carried across sessions
# ---------------------------------------------------------------------------


def test_start_session_returns_exactly_one_action_and_reflects_a_next_step(world):
    """Quickstart outcome 3. Scenario C, run once against a fresh world."""
    scenario_c(world)


def test_an_empty_log_still_prescribes_exactly_one_action(world):
    """One action even when the log is empty, checked where that is guaranteed.

    Scenario C cannot assert this — by section 7 the log has history — but the
    "never a menu, never empty" property is most at risk precisely when there is
    nothing to choose from, so it is asserted once, here. With this world's
    fixture curriculum imported, the 006 curriculum rung outranks the
    ``open_first_lesson`` fallback, so the one action is a curriculum topic;
    the bare fallback itself is asserted in ``tests/test_session_tools.py``
    (``test_curriculum_unavailable_falls_back_to_open_first_lesson``).
    """
    opened = start_session(world.conn)
    assert opened["ok"] is True, opened
    action = opened["action"]
    assert isinstance(action, dict)
    assert action["kind"] == session_tools.ACTION_CURRICULUM_TOPIC, action
    assert action["source"] == "curriculum_reachability", action
    assert action["lesson_id"] is None
    assert world.event_count(SESSION_OPEN_EVENT) == 1


def test_a_tired_session_still_gets_one_action_and_not_a_reduced_menu(world):
    """Tired mode overrides the ladder without changing the answer's shape."""
    tired = start_session(world.conn, tired=True)
    assert tired["tired_mode"] is True
    assert isinstance(tired["action"], dict)
    assert tired["action"]["kind"] == session_tools.ACTION_TIRED_MODE
    assert tired["action"]["source"] == "tired_mode"


# ---------------------------------------------------------------------------
# 7. Outcome 4 — coverage may never answer the grammar question
# ---------------------------------------------------------------------------


def test_unreachable_grammar_is_excluded_at_full_vocabulary_coverage(world):
    """Quickstart outcome 4, over the fixture ``curriculum.md``-shaped DAG.

    Mirrors ``tests/test_intelligence.py``'s independent test — same chain, same
    wall, same tempting number — and is here as the *cumulative* re-assertion:
    the edges under it were not seeded row by row but imported from a fixture
    ``curriculum.md`` through :func:`katagiri.intelligence.import_curriculum`,
    which is the path a learner's real graph travels. So this fails if the
    parser, the importer or the reachability walk breaks, not only the gate.
    """
    conn = world.conn
    stored = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM item WHERE kind = 'grammar'"
        )
    }
    assert set(CURRICULUM_IDS) <= stored, stored
    prereqs = {
        (str(row["from_id"]), str(row["to_id"]))
        for row in conn.execute(
            "SELECT from_id, to_id FROM item_edge WHERE edge_type = ?", (EDGE_PREREQ,)
        )
    }
    assert ("g-o-object", "g-masu-form") in prereqs, prereqs

    result = find_i_plus_one(
        conn,
        [{"id": WALL_ID, "text": WALL_SENTENCE, "grammar_ids": ["g-masu-form"]}],
        include_gated=True,
        score_difficulty=False,
    )
    assert result["ok"] is True, result
    assert result["candidates"] == [], result

    (entry,) = result["gated"]
    assert entry["coverage"]["known_pct"] == 100.0, entry
    assert entry["coverage"]["unknown_types"] == 0, entry
    assert entry["accepted"] is False
    assert entry["gated_by"] == [GATE_UNREACHABLE_GRAMMAR], entry
    assert entry["grammar"]["reachable"] is False
    assert entry["grammar"]["unreachable"] == [
        {"id": "g-masu-form", "missing_prereqs": ["g-o-object"]}
    ], entry["grammar"]
    assert result["counts"]["by_reason"] == {GATE_UNREACHABLE_GRAMMAR: 1}, result
    assert result["gates"]["reachability_edge_type"] == EDGE_PREREQ


def test_no_relaxation_turns_the_grammar_wall_into_comprehensible_input(world):
    """There is no flag that serves it, and mastering the prerequisite opens it.

    The mirror image is the load-bearing half: if the candidate never became
    available, the gate above could be passing because something unrelated is
    broken.
    """
    conn = world.conn
    candidate = {"id": WALL_ID, "text": WALL_SENTENCE, "grammar_ids": ["g-masu-form"]}

    wide_open = find_i_plus_one(
        conn,
        [candidate],
        require_grammar=False,
        min_coverage_pct=0.0,
        max_unknown_types=None,
        max_new_grammar=None,
        include_gated=True,
        score_difficulty=False,
    )
    assert wide_open["candidates"] == [], wide_open
    assert wide_open["gated"][0]["gated_by"] == [GATE_UNREACHABLE_GRAMMAR]

    mark_known(conn, "g-o-object")
    conn.commit()
    opened = find_i_plus_one(conn, [candidate], score_difficulty=False)
    assert [entry["id"] for entry in opened["candidates"]] == [WALL_ID], opened
    assert opened["candidates"][0]["gated_by"] == []


# ---------------------------------------------------------------------------
# 8. Outcome 5 — a media-derived write without echo-back is refused
# ---------------------------------------------------------------------------


def test_a_media_derived_write_without_echo_back_is_refused(world):
    """Quickstart outcome 5, on both consuming tools, and nothing is written.

    ``stage_untrusted`` with no ``confirm_untrusted`` between it and the write:
    the envelope exists, its id is known, and the write is still refused with
    :data:`~katagiri.session_tools.CONFIRMATION_REQUIRED` naming the field. The
    refusal is checked against the database as well as against the answer — a
    tool that refused *and* wrote would satisfy the return-value assertion.
    """
    conn = world.conn
    session = start_session(conn)
    errors_before = world.event_count(ERROR_EVENT)
    mining_before = world.event_count(MINING_EVENT)
    items_before = world.row_count("item")

    envelope, _challenge_id = envelope_for(MEDIA_LINE)

    refused_error = log_error(
        conn,
        said="止まます",
        correct="止まります",
        pattern="masu-form of a godan verb",
        severity="low",
        session_id=session["session_id"],
        context=envelope,
    )
    assert refused_error["ok"] is False, refused_error
    assert refused_error["error"] == CONFIRMATION_REQUIRED, refused_error
    assert refused_error["field"] == "context", refused_error
    assert refused_error["event_id"] is None
    assert world.event_count(ERROR_EVENT) == errors_before

    refused_mine = add_vocab(
        conn,
        word=MINED_WORD,
        reading=MINED_READING,
        session_id=session["session_id"],
        example=envelope,
    )
    assert refused_mine["ok"] is False, refused_mine
    assert refused_mine["error"] == CONFIRMATION_REQUIRED, refused_mine
    assert refused_mine["field"] == "example", refused_mine
    assert refused_mine["item_id"] is None
    assert world.event_count(MINING_EVENT) == mining_before
    assert world.row_count("item") == items_before, "a refused mine created a row"


def test_media_text_may_not_reach_an_untrusted_only_field_as_a_string(world):
    """The ceremony cannot be routed around by passing the line as a ``str``.

    ``confirmation_required`` is only a real boundary if there is no cheaper
    door beside it. There is not: an untrusted-only field refuses a bare string
    outright, and the refusal says what to do instead.
    """
    conn = world.conn
    session = start_session(conn)

    unenveloped = log_error(
        conn,
        said="止まます",
        correct="止まります",
        pattern="masu-form of a godan verb",
        severity="low",
        session_id=session["session_id"],
        context=MEDIA_LINE,  # type: ignore[arg-type]
    )
    assert unenveloped["ok"] is False, unenveloped
    assert unenveloped["error"] == ENVELOPE_REQUIRED, unenveloped
    assert unenveloped["field"] == "context"
    assert "stage_untrusted" in unenveloped["note"]
    assert world.event_count(ERROR_EVENT) == 0

    # The sentence builder is the other consumer of external material, and it
    # refuses the same two ways: a string outright, an unconfirmed envelope with
    # the challenge to answer.
    bare = build_sentences(conn, item_ids=["w-neko"], source=MEDIA_LINE)
    assert bare["ok"] is False, bare
    assert bare["error"] == UNENVELOPED_SOURCE, bare

    envelope, _ = envelope_for(MEDIA_LINE, locator="ep01 12:44")
    unconfirmed = build_sentences(conn, item_ids=["w-neko"], source=envelope)
    assert unconfirmed["ok"] is False, unconfirmed
    assert unconfirmed["error"] == ECHO_BACK_REQUIRED, unconfirmed
    assert unconfirmed["sentences"] == []
    assert unconfirmed["challenge"]["envelope_id"] == envelope.envelope_id


def test_the_same_write_succeeds_once_the_content_is_echoed_back(world):
    """The refusal is about the missing ceremony, not about the field.

    Without this the tests above are satisfied by a tool that refuses
    everything. Restating the line verbatim makes the identical call land, and
    the event records the provenance rather than only the text.
    """
    conn = world.conn
    session = start_session(conn)
    envelope, challenge_id = envelope_for(MEDIA_LINE)

    confirmed = confirm_untrusted(challenge_id, MEDIA_LINE)
    assert confirmed["ok"] is True, confirmed
    assert confirmed["envelope_id"] == envelope.envelope_id

    mined = add_vocab(
        conn,
        word=MINED_WORD,
        reading=MINED_READING,
        session_id=session["session_id"],
        example=envelope,
    )
    assert mined["ok"] is True, mined
    assert "example" in mined["untrusted"], mined
    provenance = mined["untrusted"]["example"]
    assert provenance["untrusted"] is True, provenance
    assert provenance["provenance"]["source"] == SOURCE_MEDIA, provenance
    assert provenance["chars"] == len(MEDIA_LINE), provenance
    assert mined["note"], "an enveloped write says what it wrote and where from"

    payload = json.loads(
        conn.execute(
            "SELECT payload FROM event WHERE id = ?", (mined["event_id"],)
        ).fetchone()["payload"]
    )
    assert payload["example"] == MEDIA_LINE
    assert payload["untrusted"]["example"]["digest"]


# ---------------------------------------------------------------------------
# 9. Outcome 6 — a canary sentence reaching a drill makes the validator scream
# ---------------------------------------------------------------------------


def test_a_canary_sentence_referenced_by_a_drill_is_refused(world):
    """Quickstart outcome 6, on the route that matters: an explicit request.

    ``sent-canary`` holds a *fixture* sealed sentence as its text. Asking for it
    by id must fail the whole call — substituting a different item would hide
    the contamination — and the refusal must name the canary id and the column
    that matched while quoting none of the sentence, which is the property that
    makes the scream safe to log.
    """
    conn = world.conn
    refused = gen_exercise(conn, item_ids=[CANARY_ITEM_ID], count=1)

    assert refused["ok"] is False, refused
    assert refused["error"] == CANARY_VIOLATION, refused
    assert refused["exercises"] == [], refused
    assert refused["item_id"] == CANARY_ITEM_ID
    assert refused["findings"], refused
    assert {finding["canary_id"] for finding in refused["findings"]} == {
        canary_sentence_id(FAKE_BIKE)
    }, refused["findings"]
    assert all(finding["severity"] == exercises.REFUSE for finding in refused["findings"])

    # The scream carries ids, bands and counts — never the sealed text.
    blob = json.dumps(refused, ensure_ascii=False)
    assert FAKE_BIKE not in blob
    assert FAKE_BIKE_READING not in blob


def test_a_canary_sentence_in_the_pool_is_screened_out_and_never_drilled(world):
    """The other route: not asked for, just present. Dropped, and said so.

    A pool candidate the guard refuses is reported under ``screened_out`` and the
    next item is tried, so an unlucky database still yields drills — but the
    canary item may not appear among them under any direction.
    """
    conn = world.conn
    # The whole fixture pool, so the canary item is reached rather than left
    # beyond a count the earlier items already filled.
    generated = gen_exercise(conn, count=exercises.MAX_COUNT)
    assert generated["ok"] is True, generated
    assert generated["canary_sentences_screened_against"] == FAKE_CANARY_COUNT

    assert CANARY_ITEM_ID not in [drill["item_id"] for drill in generated["exercises"]]
    screened = {row["item_id"]: row for row in generated["screened_out"]}
    assert CANARY_ITEM_ID in screened, generated["screened_out"]
    assert screened[CANARY_ITEM_ID]["code"] == CANARY_VIOLATION

    # Every offered direction, not just the default one.
    for direction in (READ_TO_MEANING, exercises.LISTEN_TO_MEANING, exercises.SHADOW):
        one = gen_exercise(conn, item_ids=[CANARY_ITEM_ID], direction=direction, count=1)
        assert one["ok"] is False, (direction, one)
        assert one["error"] in {CANARY_VIOLATION, CANARY_ECHO}, (direction, one)


def test_external_material_carrying_a_canary_sentence_is_screened_out(world):
    """Sentence building screens the same way, on text that came from outside.

    The material is enveloped and confirmed — the ceremony is satisfied — so the
    only thing that can keep the sealed line out of the result is the validator.
    """
    conn = world.conn
    clean_line = "毎日自転車で学校に行きます。"
    material = f"{FAKE_BIKE}\n{clean_line}\n"

    # The ceremony run explicitly, on its own gate, so ``confirmation`` is the
    # real object rather than the staging buffer's copy: the call below is fully
    # authorised, and the only thing left that can stop the sealed line is the
    # validator.
    gate = EchoGate()
    envelope = wrap(material, source=SOURCE_MEDIA, locator="ep02 04:10")
    challenge = gate.challenge(envelope)
    confirmation = gate.confirm(challenge.challenge_id, material)

    seed_item(conn, "w-jitensha", kanji="自転車", reading="じてんしゃ", pos="名詞")
    conn.commit()

    built = build_sentences(
        conn,
        item_ids=["w-jitensha"],
        source=envelope,
        confirmation=confirmation,
        gate=gate,
        max_sentences=5,
    )
    assert built["ok"] is True, built
    assert built["external_lines_considered"] == 2, built

    # The clean line got through; the sealed one is in ``screened_out`` and in no
    # sentence, under any origin.
    texts = [row["text"] for row in built["sentences"]]
    assert clean_line in texts, built
    assert all(FAKE_BIKE not in text for text in texts), built
    screened = built["screened_out"]
    assert screened, "the sealed line passed the external screen"
    assert {row["code"] for row in screened} == {CANARY_VIOLATION}, screened
    assert {row["origin"] for row in screened} == {SOURCE_EXTERNAL}, screened
    assert {
        finding["canary_id"] for row in screened for finding in row["findings"]
    } == {canary_sentence_id(FAKE_BIKE)}, screened

    # And the report still quotes none of it.
    assert FAKE_BIKE not in json.dumps(built, ensure_ascii=False)


def test_the_generators_refuse_to_run_without_a_sealed_set_at_all(world, tmp_path):
    """No guard is not "no contamination": it is a refusal.

    The one failure mode this whole hook exists to prevent is a build that
    screens against nothing and reports success. Pointed at a vault with no
    sealed file, both generators must fail closed.
    """
    empty_vault = tmp_path / "no-canary-vault"
    empty_vault.mkdir()
    reset_canary_cache()
    with pytest.raises(exercises.CanarySetUnavailable):
        exercises.load_canary_guard(vault_path=empty_vault)

    # And a tampered set — one whose frontmatter no longer says sealed — is
    # refused rather than loaded.
    target = world.vault.joinpath(*CANARY_VAULT_PATH.split("/"))
    target.write_text(
        fake_canary_markdown().replace("sealed: true", "sealed: false"),
        encoding="utf-8",
    )
    reset_canary_cache()
    with pytest.raises(exercises.CanarySetTampered):
        exercises.load_canary_guard(vault_path=world.vault)

    refused = gen_exercise(world.conn, count=1)
    assert refused["ok"] is False, refused
    assert refused["error"] == exercises.CANARY_SET_TAMPERED, refused
    assert refused["exercises"] == []


# ---------------------------------------------------------------------------
# 10. Outcome 7 — cumulative: A..C compose in one session
# ---------------------------------------------------------------------------


class TestCumulativeScenarios:
    """Outcome 7: scenarios A, B and C, in order, against one database.

    Not "the earlier tests still pass elsewhere" — each of them ran against a
    fresh world, which proves only that each works from nothing. Here the three
    run in sequence inside one test, so scenario B's refusal has to hold with a
    lesson already closed, and scenario C's continuity claim has to hold with
    another lesson's ``next_step`` already unconsumed in the log. Each helper
    re-asserts its own outcome as it goes; this class adds the arithmetic that
    only makes sense once they have all run.
    """

    def test_scenarios_a_b_and_c_compose_on_one_log(self, world):
        first = scenario_a(world)
        second = scenario_b(world)
        third = scenario_c(world)

        assert first["lesson_id"] != third["lesson_id"]
        assert first["next_step"] != third["next_step"]

        # A opened one session, B one, C three.
        assert world.event_count(SESSION_OPEN_EVENT) == 5
        # A closed one lesson, C closed one.
        assert world.event_count(LESSON_CLOSE_EVENT) == 2
        assert world.row_count("lesson") == 2
        # A logged one error and mined one word; nothing else writes those.
        assert world.event_count(ERROR_EVENT) == 1
        assert world.event_count(MINING_EVENT) == 1
        # B's refused batch wrote nothing; its accepted batch wrote two.
        assert world.row_count("observation") == second["written"] == 2
        assert world.event_count(OBSERVATION_EVENT) == 2

        assert {
            SESSION_OPEN_EVENT,
            LESSON_CLOSE_EVENT,
            ERROR_EVENT,
            MINING_EVENT,
            OBSERVATION_EVENT,
        } <= world.event_types()

    def test_every_next_step_is_prescribed_exactly_once_across_the_scenarios(
        self, world
    ):
        """Continuity that does not stall, stated over a log with two of them.

        Scenario A closes a lesson with a ``next_step`` and never opens another
        session; scenario C closes a second and then opens three. The ladder has
        to walk both — the newest first, the older one after it is consumed —
        and neither may be prescribed twice, or the loop would return forever to
        whichever step the learner declined to do. Neither claim is observable
        with one lesson in the log, which is why it lives here and not in
        :func:`scenario_c`.
        """
        first = scenario_a(world)
        scenario_b(world)
        third = scenario_c(world)

        for label, lesson_id in (("A", first["lesson_id"]), ("C", third["lesson_id"])):
            times = world.conn.execute(
                """
                SELECT count(*) AS n FROM event
                 WHERE type = ?
                   AND json_extract(payload, '$.action.kind') = ?
                   AND json_extract(payload, '$.action.lesson_id') = ?
                """,
                (SESSION_OPEN_EVENT, ACTION_NEXT_STEP, lesson_id),
            ).fetchone()["n"]
            assert int(times) == 1, (
                f"scenario {label}'s next step was prescribed {times} times; a "
                "next step is prescribed exactly once"
            )

    def test_the_page_still_reads_after_all_three_scenarios(self, world):
        """The closing cumulative check: the page reflects the composed log.

        Section 7's own risk is that composition breaks the *reading* side —
        two lessons, two consumed next steps, one open thread and a due revisit,
        all in one section. So the page is rendered last, and what it prints as
        the next session's one action has to be the action a real
        ``start_session`` then hands back. A page that disagreed with the tool
        would be worse than no page: the learner would act on the wrong one.
        """
        first = scenario_a(world)
        scenario_b(world)
        third = scenario_c(world)
        assert first["lesson_id"] != third["lesson_id"]

        # Read-only, and the same function the section renders from.
        expected = session_tools.prescribe(world.conn)
        page = world.today_text()

        assert f"## {lesson_memory.SECTION_HEADING}" in page, page
        assert f"Next session opens with: {expected['instruction']}" in page, (
            page[-3000:]
        )
        # A's leftovers survived C's writes rather than being replaced by them.
        assert first["topic"] in page, page[-3000:]
        assert "Open threads (1)" in page, page[-3000:]
        assert "Topics due for revisit (1)" in page, page[-3000:]
        assert "No lessons have been recorded yet" not in page
        assert world.row_count("lesson") == 2

        # And the page and the tool agree, which is the point of asserting both.
        opened = start_session(world.conn)
        assert opened["action"]["kind"] == expected["kind"], opened["action"]
        assert opened["action"]["instruction"] == expected["instruction"]
        assert opened["action"]["lesson_id"] == expected["lesson_id"]
