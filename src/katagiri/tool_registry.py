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


TOOL_SPECS: Final[tuple[ToolSpec, ...]] = (
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
            "{pass, failing_criterion, study_days_in_window, window_start, "
            "window_end, probe_battery_recorded, required_study_days, "
            "window_length_days, excluded_pause_days, study_day_keys, "
            "ignored_pause_events}"
        ),
        stability="stable",
        note=(
            "14 study days inside the 18-day window ending today. A study day is "
            "a day_key with study_session events totalling >= 10 minutes, or at "
            "least one artifact event. Days covered by a declared pause are "
            "dropped from the window's denominator, so the window reaches further "
            "back in calendar time but still holds 18 countable days. Mechanical "
            "on purpose: it reads the event log and reports, it does not judge."
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

_WORD_SPLIT: Final = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def is_secret_key(key: object) -> bool:
    """Does this mapping key name something that must never be reported?

    Errs toward redaction: a redacted count is a nuisance, a leaked credential is
    permanent — especially so for the event log, which is append-only and has no
    redaction path once a value is in it.
    """
    if not isinstance(key, str):
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
