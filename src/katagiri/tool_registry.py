"""The MCP tool contract, declared once, in data.

This module is the contract file. Every tool :mod:`katagiri.mcp_server` registers
has exactly one :class:`ToolSpec` here, and every ToolSpec has exactly one
registered tool — ``tests/test_mcp_tools.py`` fails otherwise. The point is that a
tool's name, arguments and output shape cannot drift without an edit landing in a
reviewable file next to the code.

**Changes are additive-only.** After A6, a released tool may gain optional
arguments and output keys; it may not lose or rename them, change an argument
from optional to required, or change its meaning. A tool whose contract genuinely
has to break gets a new name, and the old spec stays until nothing calls it. The
congruence test compares these specs against the server's real JSON schemas, so a
removed or renamed argument is a test failure rather than a surprise at the other
end of a stdio pipe.

``stability`` says what a caller may rely on:

``stable``
    Implemented, contract frozen under the additive-only rule above.
``experimental``
    Implemented and honest about what it returns, but the shape may still change,
    or the data behind it is not fully populated yet.
``unimplemented``
    Registered so the gap is visible and typed, and it **raises**
    ``NotImplementedError`` when called. It must never return a plausible-looking
    stub: for a study tool, a wrong answer that looks right is worse than an
    error, because the learner cannot tell the difference.

Also here: :func:`redact`, the output-hygiene guard. It lives beside the contract
because "no credential ever appears in a tool result or in the event log" is part
of the contract, and because a helper the write path (:mod:`katagiri.events`) and
the read path (:mod:`katagiri.mcp_server`) both need cannot sit in either one
without an import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal

Stability = Literal["stable", "experimental", "unimplemented"]

STABILITIES: Final[tuple[Stability, ...]] = ("stable", "experimental", "unimplemented")


@dataclass(frozen=True, slots=True)
class ArgSpec:
    """One tool argument.

    ``type`` is a summary of the JSON schema type ("str", "int", "str | None"),
    not the schema itself; the schema is generated from the function signature and
    the test checks the two agree on names and on which arguments are required.
    """

    name: str
    type: str
    required: bool
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The declared contract for one MCP tool."""

    name: str
    summary: str
    args: tuple[ArgSpec, ...]
    output: str
    stability: Stability
    note: str | None = None

    @property
    def arg_names(self) -> tuple[str, ...]:
        return tuple(arg.name for arg in self.args)

    @property
    def required_args(self) -> frozenset[str]:
        return frozenset(arg.name for arg in self.args if arg.required)

    @property
    def implemented(self) -> bool:
        return self.stability != "unimplemented"


# ---------------------------------------------------------------------------
# The specs, in per-phase fragments
# ---------------------------------------------------------------------------
#
# ``TOOL_SPECS`` is the concatenation of the fragments below, in phase order, and
# it is the only name anything outside this module reads. The split is a seam for
# additive batches: a new phase appends to its own fragment instead of editing a
# 270-line literal, so a review diff shows one fragment and the phase it belongs
# to. Order inside a fragment, and the order of the fragments, is declaration
# order — ``tool_names()`` and the congruence test both see the same sequence they
# saw before the split.
#
# Provenance is by the commit that first declared the spec, cross-checked against
# the module each tool's logic lives in.

# Phase A — foundation and read-access MCP: liveness, the known set, the event
# log, local search, the dictionary, and the two mechanical status readouts.
# Backed by katagiri.known, katagiri.events, katagiri.jmdict_import and
# mcp_server's own logic layer.
_PHASE_A_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="ping",
        summary="Liveness check: server status and versions.",
        args=(),
        output="{status, katagiri_version, python}",
        stability="stable",
    ),
    ToolSpec(
        name="known_word",
        summary="Is an item id or surface form in the known set?",
        args=(
            ArgSpec(
                "query",
                "str",
                True,
                "An item id (resolved through the alias table) or a surface form "
                "matched against item.kanji / item.reading.",
            ),
        ),
        output=(
            "{query, item_id, found, ambiguous, is_known, source, suspect, "
            "manual_mark, redirected, matched_by} — plus 'candidates' when "
            "ambiguous is true, in which case is_known is null rather than a guess"
        ),
        stability="stable",
    ),
    ToolSpec(
        name="known_set_stats",
        summary="Shape of the known set: totals and splits by kind and source.",
        args=(),
        output=(
            "{total, known, unknown, suspect, by_source{src:{total,known}}, "
            "by_kind{kind:{total,known}}, latest_marks_by_value{mark:count}}"
        ),
        stability="stable",
    ),
    ToolSpec(
        name="recent_events",
        summary="Most recent event-log rows, newest first (ULID order).",
        args=(
            ArgSpec("limit", "int", False, "1 or more; defaults to 50."),
            ArgSpec("type", "str | None", False, "Exact event type filter."),
            ArgSpec("since_day", "str | None", False, "Inclusive YYYY-MM-DD day_key floor."),
        ),
        output=(
            "list of event rows: {id, dedupe_key, ts_device, ts_server, tz, "
            "day_key, session_id, type, item_id, direction, grade, latency_ms, "
            "answer_given, expected, audio_ref, media_ref, payload}"
        ),
        stability="stable",
    ),
    ToolSpec(
        name="search_db",
        summary="Definitive local search: item surfaces, aliases, and sentence text.",
        args=(
            ArgSpec("query", "str", True, "Non-empty search string."),
            ArgSpec("limit", "int", False, "Maximum hits; defaults to 20."),
        ),
        output=(
            "{query, limit, route ('words'|'trigram'), route_reason, hits[ "
            "{item_id, text, kind, source_index} ], hit_count, sentence_rows, "
            "index_empty, note}"
        ),
        stability="experimental",
        note=(
            "Length-routed: a query under 3 characters goes to the unicode61 "
            "word index (fts_sentence_words) because FTS5's trigram tokenizer "
            "silently matches nothing below 3 characters; 3 or more goes to the "
            "trigram index (fts_sentence_tri). Item exact/prefix matching and "
            "alias resolution run either way. The sentence indexes stay empty "
            "until A3 populates them, and the result says so instead of implying "
            "there is nothing to find."
        ),
    ),
    ToolSpec(
        name="lookup",
        summary="Dictionary lookup: JMdict senses plus pitch accent.",
        args=(ArgSpec("surface", "str", True, "Headword or reading to look up."),),
        output=(
            "{surface, found, entries[{seq, is_common, dict_version, "
            "kanji[{text, tags, common}], readings[{reading, tags, common, "
            "pitch}], senses[{sense_idx, pos, gloss, misc}], pitch}], note} — "
            "found is false with a note (entries []) when jmdict_entry has no "
            "rows, never a raise"
        ),
        # Promoted unimplemented -> experimental now that jmdict_import (A7)
        # exists. Per this module's additive-only rule, a stability promotion
        # is additive (it only relaxes a caller's expectations, it does not
        # remove or rename anything), so this is not a contract break.
        stability="experimental",
        note=(
            "Backed by katagiri.jmdict_import.lookup_word. If the jmdict "
            "tables are empty or absent (JMdict not imported yet), returns "
            "{found: false, entries: [], note: ...} instead of raising — a "
            "caller can tell 'not imported yet' from 'no such word'. The shape "
            "may still change as A7 settles."
        ),
    ),
    ToolSpec(
        name="stop_gate_status",
        summary="Mechanical PASS/FAIL of the study-consistency stop gate.",
        args=(),
        output=(
            "{pass, failing_criterion, failing_criteria, study_days_in_window, "
            "window_start, window_end, probe_battery_recorded, "
            "probe_coverage_bands, probe_observations, probe_unassisted, "
            "probe_unassisted_rate, probe_bands, required_coverage_bands, "
            "required_study_days, window_length_days, excluded_pause_days, "
            "study_day_keys, consecutive_failures, re_plan_triggered, "
            "re_plan_after_failures, ignored_pause_events, ignored_gate_events, "
            "gate_evaluation_event_id}"
        ),
        stability="stable",
        note=(
            "Two criteria gate `pass`, both counted, never judged: 14 study days "
            "inside the 18-day window ending today (a study day is a day_key with "
            "study_session events totalling >= 10 minutes, or at least one "
            "artifact event; days covered by a declared pause are dropped from "
            "the window's denominator, so the window reaches further back in "
            "calendar time but still holds 18 countable days); and a recorded "
            "probe_battery event whose unassisted pass-rate spans >= 2 coverage "
            "bands with >= 1 unassisted observation somewhere in it — the rate "
            "itself is never compared to a threshold, only that it exists. Not "
            "read-only: every call appends a gate_evaluation event carrying the "
            "verdict, which is what makes consecutive_failures and "
            "re_plan_triggered (true after 2 consecutive failing evaluations, "
            "this one included) answerable at all."
        ),
    ),
    ToolSpec(
        name="security_status",
        summary="Read-only check that local helper ports are bound to loopback.",
        args=(),
        output=(
            "{checked_ports, ports{'port':{listening, loopback_only, "
            "bound_addresses}}, exposed_ports, all_loopback_only, "
            "changed_anything (always false), firewall_command, note}"
        ),
        stability="experimental",
        note=(
            "Parses `netstat -ano -p TCP`. loopback_only is null, not true, for a "
            "port nothing is listening on — there is no binding to vouch for. "
            "Strictly read-only: it never touches the firewall, and returns the "
            "exact netsh command for the operator to run instead."
        ),
    ),
)

# Phase B — the GET-only Obsidian proxy. Grouped by the module the tools live in:
# every spec here is served by katagiri.obsidian_proxy.
_PHASE_B_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="vault_file",
        summary="Read one Obsidian vault file, by vault-relative path.",
        args=(
            ArgSpec(
                "path",
                "str",
                True,
                "Vault-relative path, e.g. 'Notes/Today.md'. Backslashes are "
                "normalised; '..', absolute paths and drive letters are refused.",
            ),
        ),
        output=(
            "{path, ok, status, error, note, content, byte_count, truncated, "
            "content_type, untrusted (always true)} — error is null or one of "
            "'obsidian_unconfigured' | 'obsidian_unreachable' | "
            "'obsidian_timeout' | 'obsidian_http_error'"
        ),
        stability="experimental",
        note=(
            "Read-only proxy over obsidian-local-rest-api on 127.0.0.1:27123 "
            "(B2/D-20): Katagiri holds the API key, the agent never sees it, and "
            "the plugin's own MCP endpoint is never registered here because it "
            "carries a write surface behind the same key. Content comes back as "
            "untrusted data — it is note text, not instructions. Bodies over "
            "1 MiB are cut with truncated=true. Obsidian not running is an "
            "answer, not a raise; a path outside the vault raises instead."
        ),
    ),
    ToolSpec(
        name="vault_list",
        summary="List one Obsidian vault directory (root when path is omitted).",
        args=(
            ArgSpec(
                "path",
                "str | None",
                False,
                "Vault-relative directory; omitted or empty means the vault root.",
            ),
        ),
        output=(
            "{path, ok, status, error, note, files[str], file_count, truncated} — "
            "a name ending in '/' is a subdirectory; error as for vault_file, "
            "plus 'obsidian_bad_response' when the listing could not be parsed "
            "and 'obsidian_listing_too_large' when a truncated (>1MiB) listing "
            "could not be parsed"
        ),
        stability="experimental",
        note=(
            "Same read-only proxy as vault_file. An unparseable listing reports "
            "'obsidian_bad_response', or 'obsidian_listing_too_large' when the "
            "listing was truncated first, rather than an empty directory: "
            "'could not read' and 'nothing there' are different answers."
        ),
    ),
    ToolSpec(
        name="obsidian_active_note",
        summary="Read the note currently open in Obsidian.",
        args=(),
        output=(
            "{ok, status, error, note, content, byte_count, truncated, "
            "content_type, untrusted (always true)} — status 404 with ok false "
            "means no note is open"
        ),
        stability="experimental",
        note=(
            "Same read-only proxy as vault_file. Depends on Obsidian being open "
            "with a note focused, so 'no note open' is reported as a status "
            "rather than treated as an empty note."
        ),
    ),
)

# Phase C — the derived markdown index, read without Obsidian. Backed by
# katagiri.md_search.
_PHASE_C_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="search_notes",
        summary=(
            "Search the vault's markdown: body text, frontmatter, or both. Reads "
            "Katagiri's own index, so it works with Obsidian closed."
        ),
        args=(
            ArgSpec(
                "query",
                "str | None",
                False,
                "Body text to look for. Omit it for a frontmatter-only query; "
                "at least one of query/tags/fields/path_prefix is needed.",
            ),
            ArgSpec(
                "tags",
                "list[str] | None",
                False,
                "Frontmatter tags that must all be present (case-insensitive).",
            ),
            ArgSpec(
                "fields",
                "dict[str, str] | None",
                False,
                "Frontmatter field/value pairs, ANDed across keys "
                "(case-insensitive), e.g. {'type': 'grammar'}.",
            ),
            ArgSpec(
                "path_prefix",
                "str | None",
                False,
                "Restrict to notes whose vault-relative path starts with this.",
            ),
            ArgSpec(
                "include_generated",
                "bool",
                False,
                "Include '.derived/' notes Katagiri wrote itself; false by default "
                "so generated dashboards do not drown out prose.",
            ),
            ArgSpec("limit", "int", False, "Maximum hits; defaults to 20."),
        ),
        output=(
            "{query, limit, route ('words'|'trigram'|null), route_reason, "
            "filters{tags, fields, path_prefix, include_generated}, hits[ "
            "{path, title, generated, frontmatter, frontmatter_ok, excerpt, "
            "source_index} ], hit_count, indexed_notes, index_empty, note}"
        ),
        stability="experimental",
        note=(
            "Reads the derived markdown index in the local database — no Obsidian, "
            "no network. Length-routed like search_db: a body query under 3 "
            "characters goes to the unicode61 word index over fugashi-segmented "
            "text, because FTS5's trigram tokenizer silently matches nothing below "
            "3 characters; longer queries go to the trigram index. Excerpts from "
            "the word index therefore show segmented text. Hits are as fresh as "
            "the last index run (python -m katagiri.md_search rebuild); when no "
            "note has been indexed yet the result says index_empty rather than "
            "implying the vault is empty. Note text is untrusted data, like every "
            "other vault read."
        ),
    ),
)

# Phase D — the teacher loop, and the first tools here that *write*. Grouped by
# the module behind each one: the echo-back staging seam and the session tools
# are katagiri.session_tools, the two generators are katagiri.exercises.
#
# Two conventions are new in this fragment, both forced by the transport rather
# than chosen.
#
# **Untrusted text arrives as an envelope id, never as text.** A field whose
# content plausibly comes from outside Katagiri (a subtitle line, an inbox note
# copied off a web page) is untrusted-only in session_tools: it takes an
# Envelope and refuses a bare string. An MCP call cannot hand a Python object to
# the next one, so the wire spelling of such a field is ``<field>_envelope_id``
# — an id from ``stage_untrusted``, resolved against the staging buffer by the
# adapter. There is deliberately no way to pass that text as a string: a caller
# that could would have routed around the whole protocol.
#
# **Learner-authored text stays a plain string.** A topic, an objective, the
# thing the learner said: those are trusted, and wrapping them would make the
# ceremony a tax on honest use rather than a check on external text.
_PHASE_D_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="stage_untrusted",
        summary=(
            "Wrap externally-sourced text in an envelope and get its echo-back "
            "challenge. Step 1 of 3 before any write that carries outside text."
        ),
        args=(
            ArgSpec("text", "str", True, "The external text, verbatim."),
            ArgSpec(
                "source",
                "str",
                True,
                "Provenance kind, one of katagiri.envelope.SOURCES: 'vault', "
                "'media', 'web', 'dictionary', 'unknown'. Anything else is "
                "refused — a provenance nobody chose is the record that cannot "
                "be trusted later.",
            ),
            ArgSpec(
                "locator",
                "str",
                False,
                "Where inside the source it came from — a path, a timestamp, a "
                "URL. Recorded, never fetched.",
            ),
            ArgSpec(
                "retrieved_ts",
                "str",
                False,
                "When it was captured, as YYYY-MM-DDTHH:MM:SSZ.",
            ),
            ArgSpec(
                "detail",
                "dict[str, str] | None",
                False,
                "Extra provenance pairs; they are digested with the text, so "
                "changing one later invalidates the envelope.",
            ),
        ),
        output=(
            "{ok, error, field, note, envelope_id, challenge_id, source, "
            "locator, chars, excerpt, digest_prefix, prompt, expires_ms} — the "
            "content itself is never returned, only an excerpt for display"
        ),
        stability="experimental",
        note=(
            "The staging buffer is a hand-off for one conversation (at most "
            "session_tools.MAX_STAGED envelopes, oldest evicted first), not a "
            "content store. An evicted id is not a lost write: staging the text "
            "again is one call. Nothing is written here."
        ),
    ),
    ToolSpec(
        name="confirm_untrusted",
        summary=(
            "Answer an echo-back challenge by restating the content. Step 2 of "
            "3: without this, enveloped text is never written."
        ),
        args=(
            ArgSpec(
                "challenge_id",
                "str",
                True,
                "The challenge_id stage_untrusted (or build_sentences) returned.",
            ),
            ArgSpec(
                "echo",
                "str",
                True,
                "The content itself, restated verbatim. The digest is "
                "recomputed from it, so echoing the challenge id back fails.",
            ),
        ),
        output="{ok, error, field, note, envelope_id, challenge_id, confirmed_ms}",
        stability="experimental",
        note=(
            "A confirmation is spendable exactly once, by the gate that issued "
            "it, and expires. Refusal codes come from katagiri.envelope "
            "unchanged: 'unknown_challenge', 'challenge_expired', "
            "'challenge_replayed', 'missing_echo', 'echo_mismatch'."
        ),
    ),
    ToolSpec(
        name="start_session",
        summary=(
            "Open a study session and return exactly one prescribed action — "
            "never a menu."
        ),
        args=(
            ArgSpec(
                "tired",
                "bool",
                False,
                "Declare a tired session: the prescription becomes reviews plus "
                "one mined word, which still counts as a study day.",
            ),
            ArgSpec(
                "session_id",
                "str | None",
                False,
                "Reuse an existing session id; omitted, a fresh one is minted.",
            ),
        ),
        output=(
            "{ok, error, field, note, session_id, opened_ts, event_id, "
            "tired_mode, action{kind, instruction, rationale, topic, lesson_id, "
            "unresolved_id, revisit_after, source}} — action is one dict, never "
            "a list"
        ),
        stability="experimental",
        note=(
            "Writes one 'session_open' event, which is also how the next "
            "session knows a lesson's next_step was already prescribed once. "
            "The action is chosen by a fixed ladder (tired mode, then an "
            "unconsumed next_step, then an overdue topic revisit, then the "
            "oldest open thread, then 'open a lesson') and its rationale says "
            "why that one, so the reasoning can be argued with."
        ),
    ),
    ToolSpec(
        name="log_lesson",
        summary="Record one lesson: open it, close it, or both in one call.",
        args=(
            ArgSpec("topic", "str", True, "The topic this lesson was about."),
            ArgSpec(
                "objective",
                "str",
                True,
                "The observable can-do objective it taught to.",
            ),
            ArgSpec(
                "lesson_id",
                "str | None",
                False,
                "Update this lesson (the usual close-at-end call); omitted, a "
                "new lesson row is inserted.",
            ),
            ArgSpec("session_id", "str | None", False, "The session it happened in."),
            ArgSpec(
                "closed",
                "bool",
                False,
                "True (the default) stamps closed_ts now and logs "
                "'lesson_close'; False leaves it open and logs 'lesson_open'.",
            ),
            ArgSpec(
                "next_step",
                "str | None",
                False,
                "What the next session should do. Refused unless the lesson is "
                "being closed: it is a conclusion, not a plan.",
            ),
            ArgSpec(
                "revisit_after",
                "str | int | None",
                False,
                "Schedule the topic: a YYYY-MM-DD day key, or a number of days "
                "from today.",
            ),
            ArgSpec(
                "free_notes",
                "str | None",
                False,
                "Free text, at most 500 characters (the schema's CHECK).",
            ),
            ArgSpec(
                "unresolved",
                "list[str] | None",
                False,
                "Questions served in the lesson and left open, at most 20.",
            ),
        ),
        output=(
            "{ok, error, field, note, lesson_id, created, closed, session_id, "
            "opened_ts, closed_ts, topic, next_step, revisit_after, "
            "unresolved_ids, event_id, untrusted}"
        ),
        stability="experimental",
        note=(
            "The lesson row, its unresolved threads and the event land in one "
            "transaction. An update COALESCEs each omitted field, so closing a "
            "lesson does not blank what opening it recorded. Katagiri schedules "
            "topics (revisit_after); Anki schedules items."
        ),
    ),
    ToolSpec(
        name="lessons",
        summary="Past lessons, newest first, with their computed outcome and threads.",
        args=(
            ArgSpec(
                "topic",
                "str | None",
                False,
                "Exact topic match — topics are names the learner chose, and a "
                "fuzzy match here would quietly merge two of them.",
            ),
            ArgSpec(
                "unresolved_only",
                "bool",
                False,
                "Keep only lessons that still have an open thread.",
            ),
            ArgSpec("limit", "int", False, "1 or more; defaults to 20."),
        ),
        output=(
            "list of lesson rows: {id, topic, objective, opened_ts, closed_ts, "
            "closed, session_id, next_step, revisit_after, free_notes, "
            "observation_count, item_count, unassisted_count, "
            "unresolved_served, unresolved_open, unresolved[{id, text, "
            "created_ts, resolved_ts, resolved}]}"
        ),
        stability="experimental",
        note=(
            "Reads only. The counts come from the lesson_outcome view rather "
            "than being recomputed here: a lesson's outcome is the shape of the "
            "observations recorded while it was open, and that join lives in "
            "the schema."
        ),
    ),
    ToolSpec(
        name="log_observations",
        summary=(
            "Record rubric-scored performances. This is the unassisted "
            "pass-rate series, and its mandatory fields are enforced."
        ),
        args=(
            ArgSpec(
                "observations",
                "list[dict[str, Any]]",
                True,
                "One or more records. Required per record: task_type, "
                "unassisted (bool or 0/1), coverage_band ('>=95' | '80-95' | "
                "'<80'), rubric_version. Optional: item_id, expected, "
                "produced, media_ref, ts, and stimulus_envelope_id — the media "
                "text performed against, which is untrusted-only and so "
                "arrives as a staged envelope id, never as text.",
            ),
            ArgSpec(
                "session_id",
                "str",
                True,
                "The session these happened in; an observation with no session "
                "cannot be joined to its lesson.",
            ),
        ),
        output=(
            "{ok, error, field, note, written, session_id, observation_ids, "
            "event_ids, unassisted, coverage_bands{band:count}, "
            "rubric_versions, rejected[{index, field, error, note}], untrusted}"
        ),
        stability="experimental",
        note=(
            "All-or-nothing, and nothing is defaulted: one record missing "
            "task_type, unassisted, coverage_band or rubric_version refuses the "
            "whole call with every rejection listed under 'rejected'. That is "
            "the deliberate trade — the observation log is append-only, so a "
            "batch half-written with one guessed rubric_version corrupts every "
            "trend line drawn afterwards, while a refusal costs one retry."
        ),
    ),
    ToolSpec(
        name="log_error",
        summary="Record one mistake: what was said, what was correct, and the pattern.",
        args=(
            ArgSpec("said", "str", True, "What the learner actually produced."),
            ArgSpec("correct", "str", True, "What it should have been."),
            ArgSpec(
                "pattern",
                "str",
                True,
                "The reusable part ('て-form of する', 'counter for flat "
                "objects'). A mistake logged without one is an anecdote.",
            ),
            ArgSpec(
                "severity",
                "str",
                True,
                "'low' | 'medium' | 'high'. No default: how much this cost is "
                "not a judgement the tool may make for the learner.",
            ),
            ArgSpec(
                "item_id",
                "str | None",
                False,
                "The item it was about; resolved through the alias table.",
            ),
            ArgSpec("session_id", "str | None", False, "The session it happened in."),
            ArgSpec(
                "context_envelope_id",
                "str | None",
                False,
                "Staged envelope id for the surrounding line. Untrusted-only — "
                "it typically comes off a subtitle — so it arrives enveloped "
                "and confirmed, or not at all.",
            ),
        ),
        output=(
            "{ok, error, field, note, event_id, session_id, item_id, pattern, "
            "severity, untrusted}"
        ),
        stability="experimental",
        note=(
            "Writes one 'error_logged' event; 'said' and 'correct' land in the "
            "log's answer_given / expected columns, which is the split those "
            "columns exist for. severity is checked before any envelope is "
            "unwrapped, so a refusal does not spend the caller's confirmation."
        ),
    ),
    ToolSpec(
        name="add_vocab",
        summary="Mine one word: an item row plus a 'mining' event.",
        args=(
            ArgSpec(
                "word",
                "str",
                True,
                "The headword the learner vouches for, so it is trusted text.",
            ),
            ArgSpec("reading", "str | None", False, "Kana reading."),
            ArgSpec(
                "meaning",
                "str | None",
                False,
                "The learner's working gloss. Recorded in the event payload, "
                "not on the item: glosses live on the dictionary side, and a "
                "working translation is a fact about the mining moment.",
            ),
            ArgSpec("pos", "str | None", False, "Part of speech."),
            ArgSpec("topic", "str | None", False, "Home topic for the item."),
            ArgSpec(
                "pitch",
                "int | None",
                False,
                "Drop position as an integer (0 = heiban); leave it out when "
                "unknown rather than guessing.",
            ),
            ArgSpec("note", "str | None", False, "A learner-authored note."),
            ArgSpec(
                "example_envelope_id",
                "str | None",
                False,
                "Staged envelope id for the anchor sentence. Untrusted-only: it "
                "is lifted from whatever the learner was watching.",
            ),
            ArgSpec("session_id", "str | None", False, "The session it happened in."),
        ),
        output=(
            "{ok, error, field, note, item_id, created, redirected, event_id, "
            "session_id, word, reading, untrusted}"
        ),
        stability="experimental",
        note=(
            "The item id is deterministic (w- + sha1(kanji|reading)[:6]), so "
            "mining the same word twice fills in blanks rather than duplicating "
            "it, and never overwrites a curated value. Nothing is written to "
            "the vault — the Obsidian bridge is GET-only, so the topic file "
            "gets this word when the derived exporters next run."
        ),
    ),
    ToolSpec(
        name="triage_inbox",
        summary=(
            "Propose filings for one inbox note, and apply the vocab ones on "
            "request."
        ),
        args=(
            ArgSpec(
                "note_envelope_id",
                "str",
                True,
                "Staged envelope id for the note's text. Untrusted-only: inbox "
                "captures are copied off web pages, subtitles and screenshots. "
                "Read the note with the vault tools and stage it — this tool "
                "reads nothing from the vault itself.",
            ),
            ArgSpec(
                "dry_run",
                "bool",
                False,
                "True (the default) classifies and proposes without writing "
                "anything, and needs no echo-back. False requires the "
                "confirmation and files the vocab proposals.",
            ),
            ArgSpec("session_id", "str | None", False, "The session it happened in."),
        ),
        output=(
            "{ok, error, field, note, dry_run, proposals[{line, kind, surface, "
            "hint, why, excerpt, item_id}], applied[{line, item_id, created, "
            "event_id}], deferred, line_count, truncated, event_id, "
            "session_id, untrusted}"
        ),
        stability="experimental",
        note=(
            "Classification is mechanical and reads shape, not meaning: nothing "
            "in the note is ever treated as an instruction. Only 'vocab' "
            "proposals are filed; sentence and question proposals come back "
            "under 'deferred' because a question needs the lesson it belongs to "
            "and a sentence belongs to the exercise path. Nothing in the vault "
            "is read, moved or deleted."
        ),
    ),
    ToolSpec(
        name="gen_exercise",
        summary=(
            "Generate drills for studied items, every string screened against "
            "the sealed canary set."
        ),
        args=(
            ArgSpec(
                "item_ids",
                "list[str] | None",
                False,
                "Name the material explicitly; ids are resolved through the "
                "alias table and each redirect is reported.",
            ),
            ArgSpec(
                "topic",
                "str | None",
                False,
                "Narrow the pool to one home topic. Ignored when item_ids is "
                "given.",
            ),
            ArgSpec(
                "direction",
                "str | None",
                False,
                "One of listen_to_meaning, meaning_to_speech, read_to_meaning, "
                "cloze_production, shadow — spelled as event.direction spells "
                "them, so the result can be logged.",
            ),
            ArgSpec("count", "int", False, "1 to 20 drills; defaults to 5."),
        ),
        output=(
            "{ok, error, note, exercises[...], requested, returned, direction, "
            "topic, screened_out[{item_id, code, findings}], skipped[{item_id, "
            "reason}], redirects[{from, to}], "
            "canary_sentences_screened_against, canary_bands}"
        ),
        stability="experimental",
        note=(
            "Reads only. Selection is deterministic (never-drilled first, then "
            "longest-ago, then by id) so a session's drills can be "
            "reconstructed from a log. Fails closed: with the canary set "
            "missing or tampered it refuses "
            "('canary_set_unavailable' / 'canary_set_tampered') rather than "
            "generating unscreened drills. An explicitly requested item the "
            "guard refuses fails the whole call; a pool candidate is dropped "
            "into screened_out and the next item is tried. A finding names the "
            "canary id and band, never the sealed sentence."
        ),
    ),
    ToolSpec(
        name="build_sentences",
        summary=(
            "Build practice sentences for target items, from templates or from "
            "enveloped external material, all canary-screened."
        ),
        args=(
            ArgSpec(
                "item_ids",
                "list[str] | None",
                False,
                "Target items; ids are resolved through the alias table.",
            ),
            ArgSpec("topic", "str | None", False, "Narrow the pool to one home topic."),
            ArgSpec(
                "source_envelope_id",
                "str | None",
                False,
                "Staged envelope id for external material to mine lines from. "
                "There is no string form: unenveloped source is refused.",
            ),
            ArgSpec(
                "challenge_id",
                "str | None",
                False,
                "The challenge this call answers. Omit both this and echo on "
                "the first call: the result comes back "
                "'echo_back_required' carrying the challenge to answer.",
            ),
            ArgSpec(
                "echo",
                "str | None",
                False,
                "The external material restated verbatim, answering "
                "challenge_id. The digest is recomputed from it.",
            ),
            ArgSpec(
                "max_sentences", "int", False, "1 to 20 sentences; defaults to 5."
            ),
        ),
        output=(
            "{ok, error, note, sentences[{text, target_item_id, origin, "
            "template, needs_review, untrusted_origin, provenance, "
            "canary_screened}], requested, returned, topic, screened_out, "
            "skipped, redirects, source_provenance, "
            "external_lines_considered, canary_sentences_screened_against, "
            "canary_bands} — plus 'challenge' when error is 'echo_back_required'"
        ),
        stability="experimental",
        note=(
            "Reads only; recording what was built is the caller's job, through "
            "log_observations. Template sentences come from a fixed table keyed "
            "by coarse part of speech — a part of speech with no template "
            "yields nothing rather than invented Japanese — and every sentence "
            "is marked needs_review because it is machine-scaffolded. "
            "Receptive-only items are skipped: a practice sentence is "
            "production material. Fails closed on the canary set like "
            "gen_exercise."
        ),
    ),
    # --- lesson memory: katagiri.lesson_memory -------------------------------
    #
    # US2's read side. The write side is already here — log_lesson records
    # next_step / revisit_after / unresolved, start_session consumes them — so
    # this batch adds exactly one tool: the aggregate that answers "where did we
    # leave off?" without appending a session_open event to find out.
    ToolSpec(
        name="lesson_memory",
        summary=(
            "Where the last session left off: the next action, open threads, "
            "pending next steps, due topic revisits and open lessons, in one read."
        ),
        args=(
            ArgSpec(
                "today",
                "str | None",
                False,
                "The day to read as, YYYY-MM-DD; omitted, the local today. "
                "Overdue-ness and revisit due dates are relative to it.",
            ),
            ArgSpec(
                "thread_limit",
                "int",
                False,
                "How many open threads to show; defaults to 5. The untruncated "
                "count comes back as open_threads_total either way.",
            ),
            ArgSpec(
                "revisit_limit",
                "int",
                False,
                "How many due topic revisits to show; defaults to 5.",
            ),
            ArgSpec(
                "next_step_limit",
                "int",
                False,
                "How many pending next steps to show; defaults to 3.",
            ),
            ArgSpec(
                "open_lesson_limit",
                "int",
                False,
                "How many still-open lessons to show; defaults to 5.",
            ),
        ),
        output=(
            "{day, lessons_total, next_action{kind, instruction, rationale, "
            "topic, lesson_id, unresolved_id, revisit_after, source}, "
            "open_threads[...], open_threads_total, pending_next_steps[...], "
            "due_revisits[...], due_revisits_total, next_revisit, "
            "open_lessons[...], open_lessons_total}"
        ),
        stability="experimental",
        note=(
            "Reads only — and that is why it is registered separately from "
            "start_session, which answers the same question but appends a "
            "'session_open' event to do it. next_action is start_session's "
            "prescription for a non-tired session, computed without consuming "
            "it, so looking is not the same as starting. Every truncated list "
            "comes with its untruncated total rather than quietly ending. This "
            "is the same snapshot the Today.md lesson-memory section renders."
        ),
    ),
    # --- vocabulary and grammar intelligence: katagiri.intelligence ----------
    #
    # US4, and both tools read only. Text arrives as a plain string here, which
    # is not an exception to the envelope rule above: that rule governs writes,
    # and nothing in this pair writes a row, an event, or a cache entry. A
    # measurement of a subtitle line is a number about that line, and it is
    # discarded with the response.
    ToolSpec(
        name="coverage",
        summary=(
            "Known-word coverage of a text, measured against the real known "
            "set, with the unknown types ranked."
        ),
        args=(
            ArgSpec(
                "text",
                "str",
                True,
                "The Japanese text to measure, up to 100000 characters. "
                "Measured, never stored.",
            ),
            ArgSpec(
                "top_unknown",
                "int",
                False,
                "How many unknown types to return, 0 to 200; defaults to 15. "
                "Each carries a cumulative_pct — the coverage this text would "
                "reach if everything up to and including it were known.",
            ),
        ),
        output=(
            "{ok, error, note, known_pct, known_ratio, band, chars, "
            "counts{morphs, counted_tokens, known_tokens, unknown_tokens, "
            "function_tokens, ignored_morphs, by_state}, types{counted, known, "
            "unknown}, unknown[{lemma, reading, surface, occurrences, state, "
            "cumulative_pct, ...}], unknown_types, known_queries}"
        ),
        stability="experimental",
        note=(
            "Reads only, and writes nothing to coverage_cache: that table is "
            "keyed by media / episode / sentence / topic scope, and a pasted "
            "string is none of those, so a row cached under an invented scope "
            "id could never be invalidated. known_pct and band are null, not "
            "zero, for a text with no countable content token — 'what "
            "percentage of nothing' has no honest numeric answer. Refuses with "
            "'tokenizer_unavailable' rather than guessing at segmentation."
        ),
    ),
    ToolSpec(
        name="find_i_plus_one",
        summary=(
            "Material that is i+1 on both axes: grammar reachable in the "
            "stored prereq DAG *and* vocabulary coverage clear, ranked by "
            "comprehension debt."
        ),
        args=(
            ArgSpec(
                "candidates",
                "list[dict[str, Any]] | None",
                False,
                "Candidates to judge: each needs 'text', and may carry 'id' "
                "(its item id, which is how the gate reads its stored grammar "
                "annotation and its sealed flag), 'grammar_ids' (an explicit "
                "annotation, which wins over the database) and 'source'. "
                "Omitted, the stored sentence items are used.",
            ),
            ArgSpec(
                "top",
                "int",
                False,
                "How many accepted candidates to return, 1 to 200; defaults to 10.",
            ),
            ArgSpec(
                "min_coverage_pct",
                "float",
                False,
                "The vocabulary half of the gate, 0 to 100; defaults to 80.",
            ),
            ArgSpec(
                "max_unknown_types",
                "int | None",
                False,
                "Cap on distinct unknown types; defaults to 1, null to disable.",
            ),
            ArgSpec(
                "max_new_grammar",
                "int | None",
                False,
                "Cap on grammar points that are reachable but not yet mastered; "
                "defaults to 1, null to disable.",
            ),
            ArgSpec(
                "min_understanding",
                "int",
                False,
                "The item.understanding score that counts as mastered, 1 to 5; "
                "defaults to 3.",
            ),
            ArgSpec(
                "require_grammar",
                "bool",
                False,
                "True (the default) gates out a candidate whose grammar could "
                "not be established at all. False offers material on vocabulary "
                "alone, which is what D-28 forbids, so the result says so in "
                "its note.",
            ),
            ArgSpec(
                "include_gated",
                "bool",
                False,
                "Return the rejected candidates with their measurements and "
                "reasons, instead of only the count of them.",
            ),
            ArgSpec(
                "top_unknown",
                "int",
                False,
                "Unknown types listed per candidate, 0 to 200; defaults to 5.",
            ),
            ArgSpec(
                "candidate_limit",
                "int",
                False,
                "How many stored sentence items to load when candidates is "
                "omitted; defaults to 200.",
            ),
            ArgSpec(
                "topic",
                "str | None",
                False,
                "Narrow the stored-item pool to one home topic. Ignored when "
                "candidates is given.",
            ),
            ArgSpec(
                "score_difficulty",
                "bool",
                False,
                "True (the default) adds the difficulty-for-me score — "
                "jreadability, BCCWJ frequency, JLPT level and coverage — to "
                "every entry. It costs an extra tokenization pass and is "
                "reported only; False skips it.",
            ),
        ),
        output=(
            "{ok, error, note, candidates[{order, id, text, source, accepted, "
            "gated_by, coverage{known_pct, known_ratio, band, counted_tokens, "
            "unknown_types, unknown}, grammar{ids, resolved_from, reachable, "
            "new, unresolved, unreachable[{id, missing_prereqs}], points}, "
            "debt{total, grammar, vocab, by_item}, difficulty}], gated[...], "
            "counts{offered, accepted, returned, gated, by_reason, "
            "unannotated}, gates{min_coverage_pct, max_unknown_types, "
            "max_new_grammar, min_understanding, require_grammar, "
            "reachability_edge_type}, ranked_by, scored_difficulty, "
            "difficulty_datasets, as_of, known_queries, mastery_queries, "
            "mastered_nodes}"
        ),
        stability="experimental",
        note=(
            "D-28 as one tool: the two axes are computed from independent "
            "sources and neither substitutes for the other, so a sentence at "
            "100% coverage whose grammar has an unmastered prerequisite is "
            "gated out with 'unreachable_grammar'. Difficulty-for-me is "
            "reported, never gating — a vendored dataset appearing or "
            "disappearing changes how material is described, never which "
            "material is offered, and 'difficulty_datasets' says which were "
            "readable. Sealed canary items are never offered and there is no "
            "override flag (D-26). Reads only: nothing records what was "
            "considered. Refuses with 'grammar_dag_cycle' naming the cycle "
            "rather than answering from a graph that has no answer."
        ),
    ),
)

TOOL_SPECS: Final[tuple[ToolSpec, ...]] = (
    _PHASE_A_SPECS + _PHASE_B_SPECS + _PHASE_C_SPECS + _PHASE_D_SPECS
)

TOOL_SPECS_BY_NAME: Final[dict[str, ToolSpec]] = {
    spec.name: spec for spec in TOOL_SPECS
}

if len(TOOL_SPECS_BY_NAME) != len(TOOL_SPECS):  # pragma: no cover - import-time guard
    raise RuntimeError("tool_registry has duplicate tool names.")


def tool_names() -> tuple[str, ...]:
    """Every declared tool name, in declaration order."""
    return tuple(spec.name for spec in TOOL_SPECS)


def get_spec(name: str) -> ToolSpec:
    """The spec for ``name``, or a ``KeyError`` naming what is available."""
    try:
        return TOOL_SPECS_BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"No tool named {name!r} is declared in the registry. "
            f"Declared: {', '.join(tool_names())}."
        ) from None


def specs_with_stability(stability: Stability) -> tuple[ToolSpec, ...]:
    """Every spec at one stability level."""
    if stability not in STABILITIES:
        raise ValueError(
            f"stability must be one of {STABILITIES}; got {stability!r}."
        )
    return tuple(spec for spec in TOOL_SPECS if spec.stability == stability)


# ---------------------------------------------------------------------------
# Output hygiene
# ---------------------------------------------------------------------------

REDACTED: Final = "[redacted]"
CIRCULAR: Final = "[circular]"

# Whole words, after splitting the key on punctuation and camelCase humps. Word
# matching rather than substring matching is deliberate: "tokenizer_version" is a
# schema column that must survive, while "access_token" must not.
SECRET_WORDS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "tokens",
        "secret",
        "secrets",
        "password",
        "passwords",
        "passwd",
        "apikey",
        "credential",
        "credentials",
        "authorization",
        "bearer",
    }
)

# Checked against the key with all separators removed, for the compound forms
# that word matching alone would miss ("api_key" -> api + key).
SECRET_COMPOUNDS: Final[tuple[str, ...]] = (
    "apikey",
    "apitoken",
    "authtoken",
    "accesskey",
    "secretkey",
    "privatekey",
)

# Exact keys the word rules would flag but that are known measurements, checked
# before those rules. This is an allow-list of *whole key names*, never of words:
# it can never widen "token" itself, and every entry is a literal Katagiri writes
# — morpheme counts out of katagiri.intelligence, where "token" means a unit of
# segmented text and not a credential. They are exempted rather than renamed
# because blanking the primary output of a measurement tool is a worse failure
# than the (impossible) leak it would be guarding against: a count is an int the
# tokenizer produced, and nothing a caller supplies can reach these keys.
NOT_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "counted_tokens",
        "known_tokens",
        "unknown_tokens",
        "function_tokens",
    }
)

_WORD_SPLIT: Final = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def is_secret_key(key: object) -> bool:
    """Does this mapping key name something that must never be reported?

    Errs toward redaction: a redacted count is a nuisance, a leaked credential is
    permanent — especially so for the event log, which is append-only and has no
    redaction path once a value is in it.
    """
    if not isinstance(key, str):
        return False
    if key in NOT_SECRET_KEYS:
        return False
    words = [word.lower() for word in _WORD_SPLIT.split(key) if word]
    if any(word in SECRET_WORDS for word in words):
        return True
    glued = "".join(words)
    return any(compound in glued for compound in SECRET_COMPOUNDS)


def redact(value: Any) -> Any:
    """Copy ``value`` with every secret-named entry replaced by ``[redacted]``.

    Recurses through dicts, lists and tuples; other values pass through. The
    input is never mutated, so a caller can keep using the original — the copy is
    what goes to the tool result or into an event payload.
    """
    return _redact(value, frozenset())


def _redact(value: Any, path: frozenset[int]) -> Any:
    if isinstance(value, dict):
        if id(value) in path:
            return CIRCULAR
        inner = path | {id(value)}
        return {
            key: (REDACTED if is_secret_key(key) else _redact(item, inner))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if id(value) in path:
            return CIRCULAR
        inner = path | {id(value)}
        cleaned = [_redact(item, inner) for item in value]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    return value


__all__ = [
    "CIRCULAR",
    "NOT_SECRET_KEYS",
    "REDACTED",
    "SECRET_COMPOUNDS",
    "SECRET_WORDS",
    "STABILITIES",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "ArgSpec",
    "Stability",
    "ToolSpec",
    "get_spec",
    "is_secret_key",
    "redact",
    "specs_with_stability",
    "tool_names",
]
