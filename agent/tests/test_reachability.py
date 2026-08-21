"""Seeded-reachability check for the demo DB (T016, spec 005-mcp-assignment, US2).

Not collected by the main suite: root ``pyproject.toml`` sets
``testpaths = ["tests"]``, and this file lives under ``agent/tests`` on
purpose (per T016's task text) even though it drives ``katagiri`` directly
(no agent-graph code exists yet -- T013's graph lives in a lane, not on
master). Run it explicitly, from the repo root, with **katagiri's own venv**
(not the agent's separate uv project):

    ./.venv/Scripts/python.exe -m pytest agent/tests/test_reachability.py -v

``katagiri`` is installed editable in that venv (see ``pyproject.toml``), so
no sys.path surgery is needed for it; ``scripts/build_demo_db.py`` is loaded
by file path below since ``scripts/`` is a plain directory, not a package.

What this asserts
------------------
The fresh-DB pitfall spec.md warns about is that every ``start_session()``
call collapses to ``open_first_lesson`` because there is nothing else in the
log for :func:`katagiri.session_tools.prescribe` to find. This test builds a
*seeded* scratch DB (schema + T016 seed rows only -- no JMdict/kanjium
import, which is unnecessary for either prescribe() or coverage() and costs
~21s) and drives ``start_session`` across it enough times to show that more
than one ``action.kind`` is reachable, then measures ``coverage()`` over two
seed-dependent sentences to show two materially different bands. See
``docs/assignment/demo-setup.md`` for the human-readable version of the same
states, written for whoever runs the actual defence.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from katagiri import db, session_tools
from katagiri.intelligence import coverage
from katagiri.session_tools import (
    ACTION_NEXT_STEP,
    ACTION_OPEN_FIRST_LESSON,
    ACTION_RESOLVE_THREAD,
    ACTION_REVISIT_TOPIC,
    ACTION_TIRED_MODE,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUILD_DEMO_DB_PATH = _REPO_ROOT / "scripts" / "build_demo_db.py"


def _load_build_demo_db() -> ModuleType:
    """Load ``scripts/build_demo_db.py`` by file path.

    It is a standalone script, not a package member, so it is loaded the
    same way any external tool would load it rather than importable via a
    normal ``import`` statement. Registering it in ``sys.modules`` *before*
    executing it is required: the module defines ``@dataclass`` classes, and
    dataclass processing looks its own module up in ``sys.modules`` while
    the class body is still executing.
    """
    spec = importlib.util.spec_from_file_location(
        "build_demo_db", _BUILD_DEMO_DB_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_demo_db = _load_build_demo_db()


@pytest.fixture
def seeded_conn(tmp_path):
    """A migrated, T016-seeded DB -- no JMdict/kanjium import.

    Neither `prescribe()` nor `coverage()` reads the JMdict tables:
    `coverage()` tokenizes with the vendored UniDic dictionary (independent
    of what has been imported into SQLite) and resolves words against
    `known_set`, which is built from `item` + `manual_marks`. Skipping the
    import step here is what keeps this test fast (well under a second,
    versus JMdict's ~21s) -- see the task's guidance not to pay that cost
    per test.
    """
    conn = db.open_db(tmp_path / "katagiri.db")
    build_demo_db._seed_demo_state(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def fresh_conn(tmp_path):
    """A migrated DB with *no* seed at all -- the pitfall state, for contrast."""
    conn = db.open_db(tmp_path / "fresh.db")
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# prescribe() rungs
# ---------------------------------------------------------------------------


def test_seed_is_idempotent(seeded_conn):
    """Re-running the seed step against an already-seeded DB adds nothing.

    ``scripts/build_demo_db.py``'s own idempotence contract (also exercised
    by re-running the real build script twice), checked here directly against
    the T016 rows: known-word items, their manual 'known' marks, and the
    unresolved thread must not duplicate.
    """
    build_demo_db._seed_demo_state(seeded_conn)  # second run, same connection

    n_items = seeded_conn.execute(
        "SELECT COUNT(*) FROM item WHERE id LIKE 'w-demo-%'"
    ).fetchone()[0]
    n_marks = seeded_conn.execute(
        "SELECT COUNT(*) FROM manual_marks WHERE item_id LIKE 'w-demo-%'"
    ).fetchone()[0]
    n_unresolved = seeded_conn.execute(
        "SELECT COUNT(*) FROM lesson_unresolved"
    ).fetchone()[0]

    assert n_items == len(build_demo_db._SEED_KNOWN_WORDS)
    assert n_marks == len(build_demo_db._SEED_KNOWN_WORDS)
    assert n_unresolved == 1


def test_first_session_prescribes_the_seeded_next_step(seeded_conn):
    """Demo state A: fresh seed, first start_session() call.

    `_next_step_action` outranks everything else in the ladder, and the seed
    put exactly one closed lesson with an unconsumed `next_step` in the log.
    """
    result = session_tools.start_session(seeded_conn)
    action = result["action"]
    assert action["kind"] == ACTION_NEXT_STEP
    assert action["lesson_id"] == build_demo_db._SEED_LESSON_NEXT_STEP


def test_second_session_falls_through_to_the_unresolved_thread(seeded_conn):
    """Demo state B: same seeded DB, the *next* start_session() call.

    Once the first call's next_step has been prescribed once, it drops out
    of the ladder (session_open event already names that lesson_id), and
    since this seed has no due topic revisit, the ladder falls through to
    the seed's other lesson's open thread.
    """
    session_tools.start_session(seeded_conn)  # consumes continue_next_step

    result = session_tools.start_session(seeded_conn)
    action = result["action"]
    assert action["kind"] == ACTION_RESOLVE_THREAD
    assert action["lesson_id"] == build_demo_db._SEED_LESSON_UNRESOLVED

    # And it stays reachable: nothing in this codebase ever sets
    # lesson_unresolved.resolved_ts, so a third call lands on the same rung.
    third = session_tools.start_session(seeded_conn)
    assert third["action"]["kind"] == ACTION_RESOLVE_THREAD


def test_tired_mode_is_reachable_regardless_of_seed_state(seeded_conn):
    """Demo state C: tired_mode_minimum overrides the ladder unconditionally."""
    result = session_tools.start_session(seeded_conn, tired=True)
    assert result["action"]["kind"] == ACTION_TIRED_MODE


def test_fresh_unseeded_db_collapses_to_open_first_lesson(fresh_conn):
    """Demo state D (the pitfall, kept for contrast): a DB nobody seeded.

    This is exactly the failure T016 exists to prevent on the *real* demo
    DB -- confirming it still happens on a genuinely empty one is what makes
    the seeded states above meaningful as a fix rather than a coincidence.
    """
    result = session_tools.start_session(fresh_conn)
    assert result["action"]["kind"] == ACTION_OPEN_FIRST_LESSON


def test_seeded_demo_db_reaches_more_than_one_action_kind(seeded_conn, fresh_conn):
    """The T016 acceptance bar: >=2 distinct prescribe() rungs are reachable
    by driving start_session() across the documented demo states.

    This DB does not seed a due topic revisit (`revisit_topic`) on purpose --
    once due, `_revisit_action` never re-checks it against anything that
    would stop firing, which would permanently shadow the unresolved-thread
    rung below it in the ladder and make the seeded DB's second reachable
    state undemonstratable. `revisit_topic` is documented as *not* seeded,
    and why, in docs/assignment/demo-setup.md.
    """
    kinds: set[str] = set()
    kinds.add(session_tools.start_session(seeded_conn)["action"]["kind"])
    kinds.add(session_tools.start_session(seeded_conn)["action"]["kind"])
    kinds.add(session_tools.start_session(seeded_conn, tired=True)["action"]["kind"])
    kinds.add(session_tools.start_session(fresh_conn)["action"]["kind"])

    assert len(kinds) >= 2, f"expected >=2 distinct action kinds, got {kinds}"
    assert ACTION_NEXT_STEP in kinds
    assert ACTION_RESOLVE_THREAD in kinds
    assert ACTION_TIRED_MODE in kinds
    assert ACTION_OPEN_FIRST_LESSON in kinds
    # revisit_topic is the one rung this seed does not attempt; documented,
    # not accidental.
    assert ACTION_REVISIT_TOPIC not in kinds


# ---------------------------------------------------------------------------
# coverage() outcomes
# ---------------------------------------------------------------------------


def test_coverage_is_high_over_a_sentence_of_only_seeded_known_words(seeded_conn):
    """Demo state E: '猫が好きです。' tokenizes to two content morphs (猫, 好き),
    both marked known by the seed -- 100% known, ">=95" band."""
    result = coverage(seeded_conn, "猫が好きです。")
    assert result["ok"] is True
    assert result["known_pct"] == 100.0
    assert result["band"] == ">=95"


def test_coverage_is_low_over_a_sentence_of_unseeded_words(seeded_conn):
    """Demo state F: '経済成長率が上昇した。' tokenizes to five content morphs
    (経済, 成長, 率, 上昇, 為る), none of which the seed marked known -- 0%
    known, "<80" band."""
    result = coverage(seeded_conn, "経済成長率が上昇した。")
    assert result["ok"] is True
    assert result["known_pct"] == 0.0
    assert result["band"] == "<80"


def test_coverage_reaches_materially_different_bands(seeded_conn):
    """The T016 acceptance bar's other half: >=2 distinct coverage() outcomes
    reachable against the seeded DB."""
    high = coverage(seeded_conn, "猫が好きです。")
    low = coverage(seeded_conn, "経済成長率が上昇した。")

    assert high["band"] != low["band"]
    assert high["known_pct"] is not None and low["known_pct"] is not None
    assert high["known_pct"] - low["known_pct"] >= 15.0  # materially different
