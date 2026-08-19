"""A6: the MCP tool surface — registry congruence, tool behaviour, hardening.

Two things are being defended here.

The first is the *contract*: every tool the server registers has a ToolSpec, every
ToolSpec has a registered tool, and the A6 snapshot at the bottom pins the tool
names and their required arguments so that a later change can only add. A break
here is not a broken test, it is a broken promise to whatever is on the other end
of the stdio pipe.

The second is *honesty*: an unimplemented tool raises with a reason instead of
returning an empty result that reads like an answer, search_db says out loud that
its indexes are unpopulated, the stop gate counts rather than judges, and nothing
— not a tool result, not an event payload — carries a credential.

Event rows are seeded with direct INSERTs (the log's triggers block UPDATE and
DELETE, not INSERT) so that ``day_key`` is exactly what each test says it is.
Going through :func:`katagiri.events.append_event` would derive ``day_key`` from
the machine's own time zone, which would make the stop-gate arithmetic depend on
where the test runs.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

import pytest

from katagiri import config as config_mod
from katagiri import events, mcp_server, tool_registry
from katagiri.db import open_db
from katagiri.tool_registry import (
    CIRCULAR,
    REDACTED,
    STABILITIES,
    TOOL_SPECS,
    ArgSpec,
    ToolSpec,
    get_spec,
    is_secret_key,
    redact,
    tool_names,
)

TODAY = "2026-08-19"
TS = "T12:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures and seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A migrated database that the MCP tools' own ``open_db()`` will also find.

    The tools take no connection argument, so the only honest way to point them
    at a scratch database is to move the configuration — which also exercises the
    real config path.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config_mod.reset_config_cache()
    conn = open_db()
    try:
        yield conn
    finally:
        conn.close()
        config_mod.reset_config_cache()


def seed_event(
    conn: sqlite3.Connection,
    *,
    day: str,
    type: str,
    payload: Any = None,
    item_id: str | None = None,
    session_id: str = "test-session",
) -> str:
    event_id = events.new_ulid()
    conn.execute(
        """
        INSERT INTO event (id, ts_device, ts_server, tz, day_key, session_id,
                           type, item_id, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            f"{day}{TS}",
            f"{day}{TS}",
            "UTC",
            day,
            session_id,
            type,
            item_id,
            None if payload is None else json.dumps(payload, ensure_ascii=False),
        ),
    )
    return event_id


def seed_item(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    kind: str = "word",
    kanji: str | None = None,
    reading: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO item (id, kind, kanji, reading, created_ts) VALUES (?,?,?,?,?)",
        (item_id, kind, kanji, reading, f"2026-01-01{TS}"),
    )


def seed_sentence(
    conn: sqlite3.Connection, rowid: int, item_id: str, jp: str, shadow: str
) -> None:
    """Insert a sentence and both FTS index rows.

    The FTS5 tables are external-content (``content='sentence_text'``) and the
    schema ships no synchronising triggers, so index rows are written by hand
    here exactly as the A3 indexer will have to write them.
    """
    conn.execute(
        "INSERT INTO sentence_text (rowid, item_id, jp, shadow_text) VALUES (?,?,?,?)",
        (rowid, item_id, jp, shadow),
    )
    conn.execute(
        "INSERT INTO fts_sentence_words (rowid, shadow_text) VALUES (?, ?)",
        (rowid, shadow),
    )
    conn.execute("INSERT INTO fts_sentence_tri (rowid, jp) VALUES (?, ?)", (rowid, jp))


def study_days(conn: sqlite3.Connection, days: list[str], minutes: int = 30) -> None:
    for day in days:
        seed_event(
            conn, day=day, type=events.STUDY_LOG_TYPE, payload={"minutes": minutes}
        )


def days_back(count: int, *, end: str = TODAY, skip: set[str] | None = None) -> list[str]:
    """``count`` consecutive calendar days ending at ``end``, newest last."""
    from datetime import date, timedelta

    last = date.fromisoformat(end)
    out: list[str] = []
    offset = 0
    while len(out) < count:
        day = (last - timedelta(days=offset)).isoformat()
        offset += 1
        if skip and day in skip:
            continue
        out.append(day)
    return sorted(out)


# ---------------------------------------------------------------------------
# Registry <-> server congruence
# ---------------------------------------------------------------------------


def registered_tools() -> dict[str, Any]:
    tools = asyncio.run(mcp_server.server.list_tools())
    return {tool.name: tool for tool in tools}


def test_every_registered_tool_has_a_spec_and_vice_versa():
    registered = set(registered_tools())
    declared = set(tool_names())

    assert registered - declared == set(), (
        "tools registered on the server with no ToolSpec; the registry is the "
        "contract file and must list them"
    )
    assert declared - registered == set(), (
        "ToolSpecs with no registered tool; a declared tool that does not exist "
        "is worse than an undeclared one"
    )


def test_specs_agree_with_the_generated_json_schemas():
    """The registry's argument summary must match the real wire schema."""
    for name, tool in registered_tools().items():
        spec = get_spec(name)
        schema = tool.input_schema
        properties = set(schema.get("properties", {}))
        required = set(schema.get("required", []))

        assert properties == set(spec.arg_names), (
            f"{name}: schema arguments {sorted(properties)} != registry "
            f"{sorted(spec.arg_names)}"
        )
        assert required == set(spec.required_args), (
            f"{name}: schema requires {sorted(required)}, registry says "
            f"{sorted(spec.required_args)}"
        )


def test_specs_are_well_formed():
    for spec in TOOL_SPECS:
        assert spec.stability in STABILITIES
        assert spec.summary.strip(), f"{spec.name} has no summary"
        assert spec.output.strip(), f"{spec.name} has no output shape"
        if spec.stability != "stable":
            assert spec.note, (
                f"{spec.name} is {spec.stability} and must say why in its note"
            )


def test_every_tool_is_importable_and_described():
    for name, tool in registered_tools().items():
        assert callable(getattr(mcp_server, name)), (
            f"{name} is not a module-level function; the adapter must stay a thin "
            "wrapper around something directly callable"
        )
        assert tool.description and len(tool.description) > 20


def _dummy_args(spec: ToolSpec) -> dict[str, Any]:
    def value(arg: ArgSpec) -> Any:
        return 1 if arg.type.startswith("int") else "x"

    return {arg.name: value(arg) for arg in spec.args if arg.required}


def test_unimplemented_specs_raise_when_called():
    # 'lookup' was A6's only unimplemented tool; it was promoted to
    # experimental once jmdict_import (A7) landed. Per tool_registry's
    # additive-only rule a stability promotion is additive (it only relaxes a
    # caller's expectations, nothing is removed or renamed), so this no longer
    # asserts that an unimplemented tool exists — it just guards any that do.
    unimplemented = tool_registry.specs_with_stability("unimplemented")
    for spec in unimplemented:
        func = getattr(mcp_server, spec.name)
        with pytest.raises(NotImplementedError) as exc:
            func(**_dummy_args(spec))
        # A bare "not implemented" tells the caller nothing about whether to wait
        # or to work around it.
        assert len(str(exc.value)) > 40, f"{spec.name} raises without a reason"


# The A6 contract, frozen. Later work may add tools, and may add *optional*
# arguments to these; it may not remove a tool, rename or drop an argument, or
# promote an optional argument to required. Editing this table to make a failing
# test pass is the break it exists to catch.
A6_CONTRACT: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "ping": (frozenset(), frozenset()),
    "known_word": (frozenset({"query"}), frozenset()),
    "known_set_stats": (frozenset(), frozenset()),
    "recent_events": (frozenset(), frozenset({"limit", "type", "since_day"})),
    "search_db": (frozenset({"query"}), frozenset({"limit"})),
    "lookup": (frozenset({"surface"}), frozenset()),
    "stop_gate_status": (frozenset(), frozenset()),
    "security_status": (frozenset(), frozenset()),
    # B2 (additive): the proxied, GET-only Obsidian reads. Behaviour is tested in
    # tests/test_obsidian_proxy.py; they are listed here so the congruence and
    # additive-only checks cover them too.
    "vault_file": (frozenset({"path"}), frozenset()),
    "vault_list": (frozenset(), frozenset({"path"})),
    "obsidian_active_note": (frozenset(), frozenset()),
}


@pytest.mark.parametrize("name", sorted(A6_CONTRACT))
def test_a6_contract_is_additive_only(name):
    required, optional = A6_CONTRACT[name]
    spec = get_spec(name)  # raises if the tool was removed or renamed
    assert spec.required_args == required, (
        f"{name}: required arguments changed since A6 — that is a breaking change"
    )
    present = set(spec.arg_names)
    assert optional <= present, (
        f"{name}: optional arguments {sorted(optional - present)} were dropped"
    )


# ---------------------------------------------------------------------------
# known_word / known_set_stats
# ---------------------------------------------------------------------------


def test_known_word_reports_a_manual_mark(db):
    seed_item(db, "w-1", kanji="走る", reading="はしる")
    db.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?,?,?)",
        ("w-1", "known", f"2026-08-01{TS}"),
    )

    by_id = mcp_server.known_word("w-1")
    assert by_id["found"] is True
    assert by_id["is_known"] is True
    assert by_id["source"] == "manual"
    assert by_id["matched_by"] == "item_id"

    by_surface = mcp_server.known_word("はしる")
    assert by_surface["item_id"] == "w-1"
    assert by_surface["matched_by"] == "surface"


def test_known_word_distinguishes_not_found_from_not_known(db):
    seed_item(db, "w-2", kanji="難しい")
    missing = mcp_server.known_word("w-nope")
    assert missing["found"] is False
    assert missing["is_known"] is None, "unknown-to-us must not read as 'not known'"

    present = mcp_server.known_word("w-2")
    assert present["found"] is True
    assert present["is_known"] is False


def test_known_set_stats_counts_through_the_tool(db):
    seed_item(db, "w-3", kanji="猫")
    seed_item(db, "g-1", kind="grammar", reading="ので")
    db.execute(
        "INSERT INTO manual_marks (item_id, mark, ts) VALUES (?,?,?)",
        ("w-3", "known", f"2026-08-02{TS}"),
    )

    stats = mcp_server.known_set_stats()
    assert stats["total"] == 2
    assert stats["known"] == 1
    assert stats["by_kind"]["word"]["known"] == 1
    assert stats["latest_marks_by_value"] == {"known": 1}


# ---------------------------------------------------------------------------
# recent_events
# ---------------------------------------------------------------------------


def test_recent_events_filters_and_orders(db):
    seed_event(db, day="2026-08-01", type="review", item_id="w-1")
    seed_event(db, day="2026-08-05", type="mining")
    seed_event(db, day="2026-08-09", type="review")

    newest_first = mcp_server.recent_events(limit=10)
    assert [row["day_key"] for row in newest_first] == [
        "2026-08-09",
        "2026-08-05",
        "2026-08-01",
    ]

    reviews = mcp_server.recent_events(limit=10, type="review")
    assert {row["type"] for row in reviews} == {"review"}

    since = mcp_server.recent_events(limit=10, since_day="2026-08-05")
    assert len(since) == 2
    assert mcp_server.recent_events(limit=1) == newest_first[:1]


def test_recent_events_rejects_a_useless_limit(db):
    with pytest.raises(ValueError, match="limit"):
        mcp_server.recent_events(limit=0)


def test_recent_events_scrubs_a_secret_left_in_a_payload(db):
    """The log is append-only, so the read path is the last place to catch this."""
    seed_event(
        db,
        day="2026-08-10",
        type="review",
        payload={"api_key": "sk-live-abcdef", "grade": 3},
    )
    seed_event(db, day="2026-08-11", type="mining", payload={"items_mined": 4})

    rows = mcp_server.recent_events(limit=5)
    dirty = json.loads(rows[1]["payload"])
    clean = json.loads(rows[0]["payload"])

    assert dirty["api_key"] == REDACTED
    assert "sk-live-abcdef" not in json.dumps(rows)
    assert dirty["grade"] == 3, "redaction must not touch the rest of the payload"
    assert clean == {"items_mined": 4}
    # Nothing to redact means the stored bytes are handed back untouched.
    assert rows[0]["payload"] == '{"items_mined": 4}'


# ---------------------------------------------------------------------------
# search_db
# ---------------------------------------------------------------------------


def test_search_routes_short_queries_to_the_word_index(db):
    seed_sentence(db, 1, "s-1", "犬が走る。", "犬 が 走る 。")

    result = mcp_server.search_db("犬")

    assert result["route"] == "words"
    assert "unicode61" in result["route_reason"]
    sources = {hit["source_index"] for hit in result["hits"]}
    assert mcp_server.WORD_INDEX in sources
    assert any(hit["item_id"] == "s-1" for hit in result["hits"])


def test_search_routes_longer_queries_to_the_trigram_index(db):
    seed_sentence(db, 1, "s-1", "犬が走る。", "犬 が 走る 。")

    result = mcp_server.search_db("が走る")

    assert result["route"] == "trigram"
    hit = next(h for h in result["hits"] if h["item_id"] == "s-1")
    assert hit["source_index"] == mcp_server.TRIGRAM_INDEX
    assert hit["text"] == "犬が走る。"


def test_short_query_would_find_nothing_on_the_trigram_index(db):
    """Why the routing exists: trigram is silent below 3 characters."""
    seed_sentence(db, 1, "s-1", "犬が走る。", "犬 が 走る 。")

    silent = db.execute(
        "SELECT rowid FROM fts_sentence_tri WHERE fts_sentence_tri MATCH ?", ('"犬"',)
    ).fetchall()
    assert silent == []
    assert mcp_server.search_db("犬")["hit_count"] >= 1


def test_search_matches_items_exactly_prefix_and_through_aliases(db):
    seed_item(db, "w-10", kanji="走る", reading="はしる")
    db.execute(
        "INSERT INTO alias (alias_id, canonical_id, created_ts) VALUES (?,?,?)",
        ("w-old", "w-10", f"2026-01-01{TS}"),
    )

    exact = mcp_server.search_db("走る")
    assert exact["hits"][0] == {
        "item_id": "w-10",
        "text": "走る",
        "kind": "word",
        "source_index": "item_exact",
    }

    prefix = mcp_server.search_db("走")
    assert [hit["source_index"] for hit in prefix["hits"]] == ["item_prefix"]

    aliased = mcp_server.search_db("w-old")
    assert aliased["hits"][0]["item_id"] == "w-10"
    assert aliased["hits"][0]["source_index"] == "alias"


def test_search_says_the_sentence_index_is_empty(db):
    seed_item(db, "w-11", kanji="猫")

    empty = mcp_server.search_db("猫")

    assert empty["index_empty"] is True
    assert empty["sentence_rows"] == 0
    assert "A3" in empty["note"], "the caller must be told why there are no sentences"
    # The item hit still lands: an empty index is not an empty answer.
    assert empty["hit_count"] == 1

    seed_sentence(db, 1, "s-9", "猫が寝る。", "猫 が 寝る 。")
    filled = mcp_server.search_db("猫")
    assert filled["index_empty"] is False
    assert filled["note"] is None


def test_search_treats_the_query_as_text_not_query_syntax(db):
    seed_sentence(db, 1, "s-1", 'これは"引用"です。', 'これ は " 引用 " です 。')
    # An unquoted double quote or a bare OR would be FTS5 syntax; neither may
    # reach the parser.
    for query in ['"', "OR", "a* OR b", '引用" AND x']:
        result = mcp_server.search_db(query)
        assert isinstance(result["hits"], list)


def test_search_rejects_empty_and_bad_limits(db):
    with pytest.raises(ValueError, match="non-empty"):
        mcp_server.search_db("   ")
    with pytest.raises(ValueError, match="limit"):
        mcp_server.search_db("猫", limit=0)


def test_search_honours_the_limit(db):
    for index in range(1, 6):
        seed_item(db, f"w-2{index}", kanji=f"走{index}")
    result = mcp_server.search_db("走", limit=2)
    assert result["hit_count"] == 2
    assert len(result["hits"]) == 2


# ---------------------------------------------------------------------------
# search_notes (C/TG-C3): the markdown index, exposed
# ---------------------------------------------------------------------------
#
# Behaviour of the engine itself lives in tests/test_md_search.py, which needs
# the vendored UniDic to index a vault. These tests are about the *adapter and
# its contract*, so they seed the derived tables directly — the md FTS tables are
# self-contained, not external-content, so a row can be written without invoking
# the tokenizer, and the whole section runs on a machine with no dictionary.


def seed_note(
    conn: sqlite3.Connection,
    rowid: int,
    path: str,
    *,
    title: str | None = None,
    body: str = "",
    shadow_text: str | None = None,
    generated: int = 0,
    fields: dict[str, list[str]] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO md_note (rowid, path, title, generated, frontmatter,
                             index_version, indexed_ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rowid,
            path,
            title,
            generated,
            json.dumps(fields, ensure_ascii=False) if fields else None,
            1,
            f"{TODAY}{TS}",
        ),
    )
    for key, values in (fields or {}).items():
        for idx, value in enumerate(values):
            conn.execute(
                "INSERT INTO md_frontmatter (note_rowid, key, idx, value) "
                "VALUES (?, ?, ?, ?)",
                (rowid, key, idx, value),
            )
    conn.execute(
        "INSERT INTO fts_md_tri (rowid, body) VALUES (?, ?)", (rowid, body)
    )
    conn.execute(
        "INSERT INTO fts_md_words (rowid, shadow_text) VALUES (?, ?)",
        (rowid, shadow_text if shadow_text is not None else body),
    )
    conn.commit()


def test_search_notes_is_registered_with_a_spec(db):
    assert "search_notes" in registered_tools()
    spec = get_spec("search_notes")
    assert spec.stability == "experimental"
    assert spec.required_args == frozenset(), (
        "every search_notes argument is optional: a frontmatter-only query is a "
        "first-class use, so requiring 'query' would forbid it"
    )
    assert set(spec.arg_names) == {
        "query",
        "tags",
        "fields",
        "path_prefix",
        "include_generated",
        "limit",
    }


def test_search_notes_finds_a_note_by_body_text(db):
    seed_note(db, 1, "Notes/particles.md", title="Particles", body="the particle wa")

    result = mcp_server.search_notes("particle")

    assert result["route"] == "trigram"
    assert result["hit_count"] == 1
    hit = result["hits"][0]
    assert hit["path"] == "Notes/particles.md"
    assert hit["source_index"] == "fts_md_tri"
    assert "particle" in hit["excerpt"]
    assert result["index_empty"] is False


def test_search_notes_filters_on_frontmatter_without_a_query(db):
    seed_note(db, 1, "Notes/a.md", body="alpha", fields={"tags": ["grammar"]})
    seed_note(db, 2, "Notes/b.md", body="beta", fields={"tags": ["vocab"]})

    result = mcp_server.search_notes(tags=["Grammar"])

    assert result["route"] is None
    assert [hit["path"] for hit in result["hits"]] == ["Notes/a.md"]
    assert result["filters"]["tags"] == ["Grammar"]


def test_search_notes_passes_every_argument_through(db):
    seed_note(db, 1, "Notes/a.md", body="alpha note", fields={"type": ["grammar"]})
    seed_note(db, 2, "Notes/b.md", body="alpha note", fields={"type": ["vocab"]})
    seed_note(db, 3, ".derived/c.md", body="alpha note", generated=1)

    plain = mcp_server.search_notes("alpha", limit=5)
    assert {hit["path"] for hit in plain["hits"]} == {"Notes/a.md", "Notes/b.md"}
    assert plain["limit"] == 5

    generated = mcp_server.search_notes("alpha", include_generated=True)
    assert ".derived/c.md" in {hit["path"] for hit in generated["hits"]}

    filtered = mcp_server.search_notes(
        "alpha", fields={"type": "grammar"}, path_prefix="Notes/"
    )
    assert [hit["path"] for hit in filtered["hits"]] == ["Notes/a.md"]


def test_search_notes_says_the_index_is_empty_rather_than_finding_nothing(db):
    """An unindexed vault and an absent note are different answers."""
    result = mcp_server.search_notes("anything")

    assert result["index_empty"] is True
    assert result["hit_count"] == 0
    assert result["note"] and "rebuild" in result["note"]


def test_search_notes_rejects_an_empty_request(db):
    with pytest.raises(ValueError, match="frontmatter filter"):
        mcp_server.search_notes("   ")
    with pytest.raises(ValueError, match="limit"):
        mcp_server.search_notes("alpha", limit=0)


def test_search_notes_never_touches_obsidian(db, monkeypatch):
    """SC-001: the markdown path must answer with Obsidian closed."""

    def explode(*args, **kwargs):  # pragma: no cover - the point is it is not called
        raise AssertionError("search_notes reached the Obsidian proxy")

    for name in ("read_vault_file", "list_vault_dir", "read_active_note", "_get"):
        if hasattr(mcp_server.obsidian_proxy, name):
            monkeypatch.setattr(mcp_server.obsidian_proxy, name, explode)

    seed_note(db, 1, "Notes/a.md", body="alpha")
    assert mcp_server.search_notes("alpha")["hit_count"] == 1


# ---------------------------------------------------------------------------
# Phase D US1 (D/TG-D3): the teacher loop, registered
# ---------------------------------------------------------------------------
#
# Behaviour of the logic behind these lives in tests/test_session_tools.py and
# tests/test_exercises.py. What is defended here is the *registration*: the
# specs and the adapters agree, the untrusted-only fields have no string
# spelling on the wire, and the three-call echo-back ceremony is actually
# drivable through the tools as registered — a seam that works only when driven
# from Python is not a seam an MCP client can use.

# Required and optional arguments, as declared. Same table shape as A6_CONTRACT
# and the same rule from here on: later work may add optional arguments, never
# remove one or promote it to required.
D_US1_CONTRACT: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "stage_untrusted": (
        frozenset({"text", "source"}),
        frozenset({"locator", "retrieved_ts", "detail"}),
    ),
    "confirm_untrusted": (frozenset({"challenge_id", "echo"}), frozenset()),
    "start_session": (frozenset(), frozenset({"tired", "session_id"})),
    "log_lesson": (
        frozenset({"topic", "objective"}),
        frozenset(
            {
                "lesson_id",
                "session_id",
                "closed",
                "next_step",
                "revisit_after",
                "free_notes",
                "unresolved",
            }
        ),
    ),
    "lessons": (frozenset(), frozenset({"topic", "unresolved_only", "limit"})),
    "log_observations": (frozenset({"observations", "session_id"}), frozenset()),
    "log_error": (
        frozenset({"said", "correct", "pattern", "severity"}),
        frozenset({"item_id", "session_id", "context_envelope_id"}),
    ),
    "add_vocab": (
        frozenset({"word"}),
        frozenset(
            {
                "reading",
                "meaning",
                "pos",
                "topic",
                "pitch",
                "note",
                "example_envelope_id",
                "session_id",
            }
        ),
    ),
    "triage_inbox": (
        frozenset({"note_envelope_id"}),
        frozenset({"dry_run", "session_id"}),
    ),
    "gen_exercise": (
        frozenset(),
        frozenset({"item_ids", "topic", "direction", "count"}),
    ),
    "build_sentences": (
        frozenset(),
        frozenset(
            {
                "item_ids",
                "topic",
                "source_envelope_id",
                "challenge_id",
                "echo",
                "max_sentences",
            }
        ),
    ),
}


@pytest.fixture(autouse=True)
def clean_envelope_state():
    """No staged envelope, confirmation or cached canary set survives a test.

    All three are process-wide singletons by design (one ledger per process is
    what makes a replay detectable), which means a leftover confirmation could
    make a later test's write succeed for the wrong reason.
    """
    from katagiri import exercises as exercises_mod
    from katagiri import session_tools as session_tools_mod
    from katagiri.envelope import reset_default_gate

    session_tools_mod.reset_staged()
    reset_default_gate()
    exercises_mod.reset_canary_cache()
    yield
    session_tools_mod.reset_staged()
    reset_default_gate()
    exercises_mod.reset_canary_cache()


def test_phase_d_us1_tools_are_registered_with_specs():
    registered = registered_tools()
    for name in D_US1_CONTRACT:
        assert name in registered, f"{name} is declared in the spec but not registered"
        spec = get_spec(name)
        assert spec.stability == "experimental"
        assert spec.note, "a Phase D tool must say why its shape may still change"


def test_the_phase_d_fragment_holds_exactly_the_us1_batch():
    """The fragment is the additive batch's diff; an accidental extra shows here."""
    assert {spec.name for spec in tool_registry._PHASE_D_SPECS} == set(D_US1_CONTRACT)
    assert len(tool_registry._PHASE_D_SPECS) == 11
    # Fragment concatenation, not replacement: the earlier phases are all still
    # declared and still registered.
    assert len(TOOL_SPECS) == 8 + 3 + 1 + 11 == len(registered_tools())


@pytest.mark.parametrize("name", sorted(D_US1_CONTRACT))
def test_d_us1_contract_is_additive_only(name):
    required, optional = D_US1_CONTRACT[name]
    spec = get_spec(name)  # raises if the tool was removed or renamed
    assert spec.required_args == required, (
        f"{name}: required arguments changed — that is a breaking change"
    )
    present = set(spec.arg_names)
    assert optional <= present, (
        f"{name}: optional arguments {sorted(optional - present)} were dropped"
    )


@pytest.mark.parametrize(
    ("tool", "field"),
    [
        ("log_error", "context"),
        ("add_vocab", "example"),
        ("triage_inbox", "note"),
        ("build_sentences", "source"),
    ],
)
def test_untrusted_only_fields_have_no_string_spelling(tool, field):
    """The whole point of the id: there must be no way to pass the text instead.

    A caller that could hand media text to a write path as a plain string has
    routed around the envelope protocol, so the wire surface must not offer the
    option — not even as an ignored argument.
    """
    names = set(get_spec(tool).arg_names)
    assert f"{field}_envelope_id" in names
    assert field not in names, (
        f"{tool}.{field} is untrusted-only; only its envelope id may cross MCP"
    )


def test_the_observation_stimulus_is_documented_as_an_envelope_id():
    """It rides inside 'observations', so the spec is the only place to say so."""
    summary = next(
        arg.summary
        for arg in get_spec("log_observations").args
        if arg.name == "observations"
    )
    assert "stimulus_envelope_id" in summary
    assert "rubric_version" in summary, "the mandatory fields must be named"


def test_start_session_prescribes_exactly_one_action_and_logs_it(db):
    result = mcp_server.start_session()

    assert result["ok"] is True
    assert isinstance(result["action"], dict), "one action, never a list of options"
    assert result["action"]["kind"] == "open_first_lesson"
    assert result["action"]["rationale"]
    logged = mcp_server.recent_events(limit=1, type="session_open")
    assert json.loads(logged[0]["payload"])["action"]["kind"] == "open_first_lesson"


def test_log_lesson_and_lessons_round_trip_through_the_tools(db):
    session = mcp_server.start_session()["session_id"]

    written = mcp_server.log_lesson(
        topic="particles",
        objective="use は and が in one sentence",
        session_id=session,
        next_step="drill が with existence verbs",
        revisit_after=10,
        unresolved=["why not どこが?"],
    )
    assert written["ok"] is True
    assert written["created"] is True and written["closed"] is True
    assert len(written["unresolved_ids"]) == 1

    rows = mcp_server.lessons(topic="particles")
    assert [row["id"] for row in rows] == [written["lesson_id"]]
    assert rows[0]["unresolved"][0]["resolved"] is False
    assert mcp_server.lessons(topic="nothing-like-this") == []
    assert mcp_server.lessons(unresolved_only=True)[0]["id"] == written["lesson_id"]


def test_log_observations_refuses_a_batch_missing_a_rubric_version(db):
    session = mcp_server.start_session()["session_id"]
    good = {
        "task_type": "cloze",
        "unassisted": True,
        "coverage_band": ">=95",
        "rubric_version": "r1",
    }

    refused = mcp_server.log_observations(
        [good, {**good, "rubric_version": None}], session_id=session
    )

    assert refused["ok"] is False
    assert refused["error"] == "observations_rejected"
    assert refused["written"] == 0, "all-or-nothing: the good record must not land"
    assert [bad["field"] for bad in refused["rejected"]] == ["rubric_version"]

    accepted = mcp_server.log_observations([good], session_id=session)
    assert accepted["ok"] is True and accepted["written"] == 1
    assert accepted["unassisted"] == 1


def test_log_error_writes_the_pattern_and_refuses_a_guessed_severity(db):
    session = mcp_server.start_session()["session_id"]

    logged = mcp_server.log_error(
        said="猫がいます",
        correct="猫があります",
        pattern="いる vs ある for inanimate subjects",
        severity="medium",
        session_id=session,
    )
    assert logged["ok"] is True
    assert logged["severity"] == "medium"

    refused = mcp_server.log_error(
        said="x", correct="y", pattern="z", severity="quite bad", session_id=session
    )
    assert refused["ok"] is False
    assert refused["error"] == "invalid_severity"
    assert refused["field"] == "severity"


def test_add_vocab_writes_an_item_and_a_mining_event(db):
    result = mcp_server.add_vocab(word="走る", reading="はしる", meaning="to run")

    assert result["ok"] is True
    assert result["created"] is True
    assert mcp_server.known_word("走る")["item_id"] == result["item_id"]
    mined = mcp_server.recent_events(limit=1, type="mining")
    assert json.loads(mined[0]["payload"])["source"] == "add_vocab"


def test_the_echo_back_ceremony_runs_as_three_tool_calls(db):
    """Stage, confirm, write — with only ids crossing between the calls."""
    external = "犬が公園を走っていました。"

    staged = mcp_server.stage_untrusted(external, source="media", locator="ep3 12:04")
    assert staged["ok"] is True
    assert staged["excerpt"] and external not in json.dumps(staged), (
        "an excerpt is for display; the tool must not hand back the content"
    )

    confirmed = mcp_server.confirm_untrusted(staged["challenge_id"], external)
    assert confirmed["ok"] is True
    assert confirmed["envelope_id"] == staged["envelope_id"]

    written = mcp_server.add_vocab(
        word="走る", example_envelope_id=staged["envelope_id"]
    )
    assert written["ok"] is True
    assert set(written["untrusted"]) == {"example"}
    assert written["untrusted"]["example"]["provenance"]["source"] == "media"
    assert written["untrusted"]["example"]["untrusted"] is True
    payload = json.loads(mcp_server.recent_events(limit=1, type="mining")[0]["payload"])
    assert payload["example"] == external


def test_a_write_without_the_echo_back_is_refused(db):
    staged = mcp_server.stage_untrusted("ここで待ってください。", source="media")

    refused = mcp_server.add_vocab(
        word="待つ", example_envelope_id=staged["envelope_id"]
    )

    assert refused["ok"] is False
    assert refused["error"] == "confirmation_required"
    assert refused["field"] == "example"


def test_a_paraphrased_echo_does_not_confirm_anything(db):
    text = "明日は雨が降るでしょう。"
    staged = mcp_server.stage_untrusted(text, source="web")

    refused = mcp_server.confirm_untrusted(staged["challenge_id"], text[:-1])

    assert refused["ok"] is False
    assert refused["error"] == "echo_mismatch"
    # And echoing the token back instead of the content is not a way through.
    assert (
        mcp_server.confirm_untrusted(staged["challenge_id"], staged["challenge_id"])[
            "error"
        ]
        == "echo_mismatch"
    )


def test_an_unknown_envelope_id_is_a_lost_handoff_not_a_refusal(db):
    from katagiri.session_tools import UnknownStagedContent

    # A refusal would invite a retry that cannot work: the buffer never held
    # this id (or has evicted it), and the fix is to stage the text again.
    with pytest.raises(UnknownStagedContent):
        mcp_server.add_vocab(word="走る", example_envelope_id="env_nope")


def test_triage_inbox_proposes_without_writing_on_a_dry_run(db):
    note = "走る - to run\nこれは何ですか？\n猫が窓から外を見ている。"
    staged = mcp_server.stage_untrusted(note, source="vault", locator="00-inbox/x.md")

    proposed = mcp_server.triage_inbox(staged["envelope_id"])

    assert proposed["ok"] is True
    assert proposed["dry_run"] is True
    assert proposed["applied"] == []
    kinds = [proposal["kind"] for proposal in proposed["proposals"]]
    assert kinds == ["vocab", "question", "sentence"]
    assert mcp_server.recent_events(limit=5, type="mining") == []

    # Applying needs the echo-back the dry run did not.
    assert (
        mcp_server.triage_inbox(staged["envelope_id"], dry_run=False)["error"]
        == "confirmation_required"
    )
    mcp_server.confirm_untrusted(staged["challenge_id"], note)
    applied = mcp_server.triage_inbox(staged["envelope_id"], dry_run=False)
    assert applied["ok"] is True
    assert [entry["line"] for entry in applied["applied"]] == [1]
    assert len(applied["deferred"]) == 2


def test_the_generators_fail_closed_without_the_canary_set(db):
    """Losing drills is a bad afternoon; unscreened drills are a bad year."""
    seed_item(db, "w-40", kanji="走る", reading="はしる")

    drills = mcp_server.gen_exercise(count=3)
    sentences = mcp_server.build_sentences(max_sentences=3)

    for result in (drills, sentences):
        assert result["ok"] is False
        assert result["error"] in {"canary_set_unavailable", "canary_set_tampered"}
        assert result["note"]
    assert drills["exercises"] == []
    assert sentences["sentences"] == []


def test_the_generators_answer_with_a_canary_set_present(db, monkeypatch):
    """Screened, deterministic, and read-only — the guard is injected here."""
    from katagiri import exercises as exercises_mod

    guard = exercises_mod.CanaryGuard(())
    monkeypatch.setattr(exercises_mod, "load_canary_guard", lambda *a, **k: guard)
    seed_item(db, "w-41", kanji="走る", reading="はしる")
    db.execute("UPDATE item SET pos = 'verb' WHERE id = 'w-41'")

    drills = mcp_server.gen_exercise(item_ids=["w-41"], count=2)
    assert drills["ok"] is True
    assert drills["returned"] >= 1
    assert drills["canary_sentences_screened_against"] == 0

    sentences = mcp_server.build_sentences(item_ids=["w-41"], max_sentences=2)
    assert sentences["ok"] is True
    assert all(item["needs_review"] for item in sentences["sentences"])
    assert all(item["canary_screened"] for item in sentences["sentences"])


def test_build_sentences_hands_back_the_challenge_then_accepts_the_echo(
    db, monkeypatch
):
    """The read path runs the same ceremony; the source has no string spelling."""
    from katagiri import exercises as exercises_mod

    monkeypatch.setattr(
        exercises_mod, "load_canary_guard", lambda *a, **k: exercises_mod.CanaryGuard(())
    )
    seed_item(db, "w-42", kanji="走る", reading="はしる")
    db.execute("UPDATE item SET pos = 'verb' WHERE id = 'w-42'")
    external = "毎朝公園を走るのが好きです。"
    staged = mcp_server.stage_untrusted(external, source="media")

    demanded = mcp_server.build_sentences(
        item_ids=["w-42"], source_envelope_id=staged["envelope_id"]
    )
    assert demanded["ok"] is False
    assert demanded["error"] == "echo_back_required"
    assert demanded["sentences"] == []
    challenge = demanded["challenge"]

    built = mcp_server.build_sentences(
        item_ids=["w-42"],
        source_envelope_id=staged["envelope_id"],
        challenge_id=challenge["challenge_id"],
        echo=external,
    )
    assert built["ok"] is True
    mined = [item for item in built["sentences"] if item["origin"] == "external"]
    assert [item["text"] for item in mined] == [external]
    assert mined[0]["untrusted_origin"] is True
    assert mined[0]["provenance"]["provenance"]["source"] == "media"


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


def seed_jmdict_entry(
    conn: sqlite3.Connection,
    *,
    seq: int = 1358280,
    kanji: str = "食べる",
    reading: str = "たべる",
    gloss: str = "to eat",
) -> None:
    """One tiny jmdict_entry + kanji + reading + sense row, for lookup tests."""
    conn.execute(
        "INSERT INTO jmdict_entry (seq, is_common, dict_version) VALUES (?, 1, 'test')",
        (seq,),
    )
    conn.execute(
        "INSERT INTO jmdict_kanji (seq, kanji, pri) VALUES (?, ?, 'common')",
        (seq, kanji),
    )
    conn.execute(
        "INSERT INTO jmdict_reading (seq, reading, pri) VALUES (?, ?, 'common')",
        (seq, reading),
    )
    conn.execute(
        "INSERT INTO jmdict_sense (seq, sense_idx, pos, gloss_en, misc) "
        "VALUES (?, 1, 'v1,vt', ?, NULL)",
        (seq, gloss),
    )


def test_lookup_returns_an_entry_for_a_seeded_word(db):
    seed_jmdict_entry(db)

    result = mcp_server.lookup("食べる")

    assert result["surface"] == "食べる"
    assert result["found"] is True
    assert result["note"] is None
    assert len(result["entries"]) == 1
    entry = result["entries"][0]
    assert entry["seq"] == 1358280
    assert entry["kanji"] == [{"text": "食べる", "tags": ["common"], "common": True}]
    assert entry["senses"][0]["gloss"] == "to eat"


def test_lookup_reports_found_false_when_jmdict_is_not_imported(db):
    result = mcp_server.lookup("食べる")

    assert result["found"] is False
    assert result["entries"] == []
    assert "not imported" in result["note"]


# ---------------------------------------------------------------------------
# stop_gate_status
# ---------------------------------------------------------------------------


def test_stop_gate_passes_with_fourteen_study_days_in_eighteen(db):
    study_days(db, days_back(14))

    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["pass"] is True
    assert gate["failing_criterion"] is None
    assert gate["study_days_in_window"] == 14
    assert gate["window_start"] == "2026-08-02"
    assert gate["window_end"] == TODAY
    assert gate["window_length_days"] == 18
    assert gate["excluded_pause_days"] == 0


def test_stop_gate_fails_with_thirteen_and_names_the_count(db):
    study_days(db, days_back(13))

    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["pass"] is False
    assert gate["study_days_in_window"] == 13
    assert "13" in gate["failing_criterion"]
    assert "14" in gate["failing_criterion"]
    assert "study_days_in_window" in gate["failing_criterion"]


def test_stop_gate_counts_days_outside_the_window_not_at_all(db):
    study_days(db, days_back(14))  # 2026-08-06 .. 2026-08-19, all inside
    study_days(db, days_back(4, end="2026-08-01"))  # 2026-07-29 .. 08-01, outside

    gate = mcp_server.stop_gate(db, today=TODAY)
    assert gate["window_start"] == "2026-08-02"
    assert gate["study_days_in_window"] == 14, "the four earlier days must not count"
    assert gate["pass"] is True

    # Six days later the same history only reaches twelve days into the window.
    later = mcp_server.stop_gate(db, today="2026-08-25")
    assert later["window_start"] == "2026-08-08"
    assert later["study_days_in_window"] == 12
    assert later["pass"] is False


def test_short_sessions_only_count_when_the_day_reaches_ten_minutes(db):
    study_days(db, days_back(13))
    thin_day = "2026-08-05"
    seed_event(db, day=thin_day, type=events.STUDY_LOG_TYPE, payload={"minutes": 5})
    assert mcp_server.stop_gate(db, today=TODAY)["study_days_in_window"] == 13

    # Two short sessions on the same day do add up.
    seed_event(db, day=thin_day, type=events.STUDY_LOG_TYPE, payload={"minutes": 6})
    gate = mcp_server.stop_gate(db, today=TODAY)
    assert gate["study_days_in_window"] == 14
    assert gate["pass"] is True


@pytest.mark.parametrize(
    "artifact_type",
    sorted(mcp_server.ARTIFACT_EVENT_TYPES),
)
def test_a_single_artifact_event_makes_a_study_day(db, artifact_type):
    study_days(db, days_back(13))
    seed_event(db, day="2026-08-04", type=artifact_type, item_id="w-1")

    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["study_days_in_window"] == 14
    assert "2026-08-04" in gate["study_day_keys"]
    assert gate["pass"] is True


def test_unrelated_event_types_do_not_make_a_study_day(db):
    study_days(db, days_back(13))
    seed_event(db, day="2026-08-04", type="seek")
    seed_event(db, day="2026-08-03", type="regen_yomitan")

    assert mcp_server.stop_gate(db, today=TODAY)["study_days_in_window"] == 13


def test_declared_pause_leaves_the_window_out_of_paused_days(db):
    paused = {f"2026-08-{day:02d}" for day in range(10, 15)}
    seed_event(
        db,
        day="2026-08-09",
        type=mcp_server.PAUSE_EVENT_TYPE,
        payload={"start_day": "2026-08-10", "end_day": "2026-08-14"},
    )
    study_days(db, days_back(14, skip=paused))

    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["window_length_days"] == 18
    assert gate["excluded_pause_days"] == 5
    # The window reaches five days further back in calendar time to stay at 18
    # countable days.
    assert gate["window_start"] == "2026-07-28"
    assert gate["study_days_in_window"] == 14
    assert gate["pass"] is True
    assert not paused & set(gate["study_day_keys"])


def test_study_on_a_paused_day_is_not_counted(db):
    seed_event(
        db,
        day="2026-08-09",
        type=mcp_server.PAUSE_EVENT_TYPE,
        payload={"days": ["2026-08-10"]},
    )
    study_days(db, ["2026-08-10"])

    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["study_day_keys"] == []
    assert gate["excluded_pause_days"] == 1


def test_an_unreadable_pause_payload_is_reported_not_assumed(db):
    bad = seed_event(
        db,
        day="2026-08-09",
        type=mcp_server.PAUSE_EVENT_TYPE,
        payload={"note": "away for a bit"},
    )
    gate = mcp_server.stop_gate(db, today=TODAY)

    assert gate["ignored_pause_events"] == [bad]
    assert gate["excluded_pause_days"] == 0, (
        "an unreadable pause must not silently excuse days"
    )


def test_probe_battery_flag(db):
    assert mcp_server.stop_gate(db, today=TODAY)["probe_battery_recorded"] is False
    seed_event(db, day="2026-08-12", type=mcp_server.PROBE_EVENT_TYPE)
    assert mcp_server.stop_gate(db, today=TODAY)["probe_battery_recorded"] is True


def test_stop_gate_tool_reads_the_configured_database(db):
    from datetime import date

    # The tool takes no arguments and uses the real clock, so the seed is built
    # relative to today rather than to this module's fixed date.
    study_days(db, days_back(14, end=date.today().isoformat()))

    gate = mcp_server.stop_gate_status()

    assert gate["study_days_in_window"] == 14
    assert gate["pass"] is True
    assert gate["window_end"] == date.today().isoformat()


def test_stop_gate_rejects_a_malformed_today(db):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        mcp_server.stop_gate(db, today="19/08/2026")


def test_empty_log_fails_the_gate_with_a_zero_count(db):
    gate = mcp_server.stop_gate(db, today=TODAY)
    assert gate["pass"] is False
    assert gate["study_days_in_window"] == 0
    assert "0 of 14" in gate["failing_criterion"]


# ---------------------------------------------------------------------------
# security_status
# ---------------------------------------------------------------------------

NETSTAT_SAMPLE = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1400
  TCP    127.0.0.1:27123        0.0.0.0:0              LISTENING       2222
  TCP    0.0.0.0:8765           0.0.0.0:0              LISTENING       3333
  TCP    127.0.0.1:8766         0.0.0.0:0              LISTENING       4444
  TCP    [::1]:8766             [::]:0                 LISTENING       4444
  TCP    192.168.1.20:5555      10.0.0.9:443           ESTABLISHED     6666
"""


def test_netstat_parser_separates_loopback_from_wildcard_binds():
    report = mcp_server.parse_netstat(NETSTAT_SAMPLE, mcp_server.HARDENED_PORTS)

    assert report["27123"] == {
        "listening": True,
        "loopback_only": True,
        "bound_addresses": ["127.0.0.1"],
    }
    assert report["8765"]["listening"] is True
    assert report["8765"]["loopback_only"] is False
    assert report["8766"]["loopback_only"] is True
    assert report["8766"]["bound_addresses"] == ["127.0.0.1", "::1"]

    # Nothing listening means there is no binding to vouch for.
    assert report["19633"] == {
        "listening": False,
        "loopback_only": None,
        "bound_addresses": [],
    }


def test_netstat_parser_ignores_established_connections():
    report = mcp_server.parse_netstat(
        "  TCP    192.168.1.20:8765      10.0.0.9:443           ESTABLISHED     1",
        (8765,),
    )
    assert report["8765"]["listening"] is False


def test_netstat_parser_survives_a_localised_state_word():
    """Non-English Windows translates LISTENING; the wildcard peer still gives it away."""
    report = mcp_server.parse_netstat(
        "  TCP    0.0.0.0:8765      0.0.0.0:0     ABHÖREN     1", (8765,)
    )
    assert report["8765"]["listening"] is True
    assert report["8765"]["loopback_only"] is False


def test_security_status_is_read_only_and_hands_back_the_command(monkeypatch):
    monkeypatch.setattr(mcp_server, "netstat_text", lambda: NETSTAT_SAMPLE)

    status = mcp_server.security_status()

    assert status["changed_anything"] is False
    assert status["exposed_ports"] == [8765]
    assert status["all_loopback_only"] is False
    assert status["checked_ports"] == list(mcp_server.HARDENED_PORTS)
    assert status["firewall_command"].startswith("netsh advfirewall")
    assert "action=block" in status["firewall_command"]
    assert "dir=in" in status["firewall_command"]
    for port in mcp_server.HARDENED_PORTS:
        assert str(port) in status["firewall_command"]


def test_security_status_reports_a_fully_loopback_machine(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "netstat_text",
        lambda: "  TCP    127.0.0.1:8765   0.0.0.0:0   LISTENING   1",
    )
    status = mcp_server.security_status()
    assert status["exposed_ports"] == []
    assert status["all_loopback_only"] is True


def test_security_status_raises_when_it_cannot_look(monkeypatch):
    def boom() -> str:
        raise RuntimeError("netstat is unavailable")

    monkeypatch.setattr(mcp_server, "netstat_text", boom)
    # "Could not look" must not be reported as "nothing exposed".
    with pytest.raises(RuntimeError, match="netstat"):
        mcp_server.security_status()


# ---------------------------------------------------------------------------
# Redaction guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "api_key",
        "apiKey",
        "API_KEY",
        "access_token",
        "accessToken",
        "refresh-token",
        "password",
        "user_passwd",
        "client_secret",
        "SECRET",
        "authorization",
        "bearer",
        "credentials",
        "private_key",
    ],
)
def test_secret_keys_are_recognised(key):
    assert is_secret_key(key) is True
    assert redact({key: "sk-live-1234"}) == {key: REDACTED}


@pytest.mark.parametrize(
    "key",
    [
        "tokenizer_version",
        "item_id",
        "session_id",
        "dedupe_key",
        "day_key",
        "minutes",
        "shadow_text",
        "keys",
    ],
)
def test_ordinary_keys_survive(key):
    assert is_secret_key(key) is False
    assert redact({key: "value"}) == {key: "value"}


def test_redaction_reaches_nested_structures():
    payload = {
        "outer": {"api_key": "sk-1", "safe": 1},
        "list": [{"token": "t"}, {"grade": 3}],
        "tuple": ({"password": "p"},),
        "value": "not a key called token",
    }

    cleaned = redact(payload)

    assert cleaned["outer"] == {"api_key": REDACTED, "safe": 1}
    assert cleaned["list"] == [{"token": REDACTED}, {"grade": 3}]
    assert cleaned["tuple"] == ({"password": REDACTED},)
    assert cleaned["value"] == "not a key called token"
    assert "sk-1" not in json.dumps(cleaned, default=str)
    # The caller's own object is untouched.
    assert payload["outer"]["api_key"] == "sk-1"


def test_redaction_passes_through_scalars_and_survives_a_cycle():
    assert redact(5) == 5
    assert redact("token") == "token", "a value is not a key"
    assert redact(None) is None

    looped: dict[str, Any] = {"name": "x"}
    looped["self"] = looped
    assert redact(looped) == {"name": "x", "self": CIRCULAR}

    nested_list: list[Any] = [1]
    nested_list.append(nested_list)
    assert redact(nested_list) == [1, CIRCULAR]


def test_non_string_keys_do_not_break_the_guard():
    assert is_secret_key(7) is False
    assert redact({7: {"token": "t"}}) == {7: {"token": REDACTED}}


# ---------------------------------------------------------------------------
# stdout hygiene: stdout belongs to the JSON-RPC transport
# ---------------------------------------------------------------------------


def test_tool_calls_write_nothing_to_stdout(db, capsys, monkeypatch):
    monkeypatch.setattr(mcp_server, "netstat_text", lambda: NETSTAT_SAMPLE)
    seed_item(db, "w-30", kanji="猫")
    study_days(db, days_back(3))
    seed_event(db, day="2026-08-18", type="review", item_id="w-30")

    mcp_server.ping()
    mcp_server.known_word("猫")
    mcp_server.known_set_stats()
    mcp_server.recent_events(limit=5)
    mcp_server.search_db("猫")
    mcp_server.stop_gate_status()
    mcp_server.security_status()
    mcp_server.lookup("猫")
    # Phase D too: these ones write, and a write path logs more than a read one.
    session = mcp_server.start_session()["session_id"]
    mcp_server.lessons()
    mcp_server.add_vocab(word="猫", reading="ねこ", session_id=session)
    mcp_server.log_error(
        said="猫がいます",
        correct="猫があります",
        pattern="いる vs ある",
        severity="low",
        session_id=session,
    )
    staged = mcp_server.stage_untrusted("猫が寝ている。", source="media")
    mcp_server.confirm_untrusted(staged["challenge_id"], "猫が寝ている。")
    mcp_server.gen_exercise(count=1)
    mcp_server.build_sentences(max_sentences=1)

    captured = capsys.readouterr()
    assert captured.out == "", (
        "stdout carries the JSON-RPC framing; a stray print corrupts the session"
    )


def test_main_serves_stdio_and_nothing_else(monkeypatch, capsys):
    """stdio only: no SSE app, no HTTP transport, no socket to reach.

    ``setup_logging`` is stubbed rather than called: it installs a handler bound
    to whatever ``sys.stderr`` is at the time, and under capture that would leave
    every later test in the session logging into a closed temporary file.
    """
    run_calls: list[dict[str, Any]] = []
    logging_calls: list[int] = []
    monkeypatch.setattr(
        mcp_server.server, "run", lambda **kwargs: run_calls.append(kwargs)
    )
    monkeypatch.setattr(mcp_server, "setup_logging", logging_calls.append)

    mcp_server.main()

    assert run_calls == [{"transport": "stdio"}]
    assert logging_calls, "main must configure stderr logging before serving"
    assert capsys.readouterr().out == "", "startup must not touch stdout"
