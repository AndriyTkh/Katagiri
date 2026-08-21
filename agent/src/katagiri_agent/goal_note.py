"""T014: goal-note frontmatter -> literal-arg passthrough, with provenance (US1).

This module owns exactly two things that :mod:`katagiri_agent.graph` wires
together:

1. :func:`parse_goal_note` -- turn whatever ``vault_read`` (the **existing**
   server's tool, called through the graph's ``read_goal_note`` node, never
   read off disk in a production path) handed back into the steering field's
   value, or into one of a small set of named, non-crashing failure statuses.
   Missing or malformed frontmatter is always one of those named statuses --
   never a silently-defaulted value -- because a silent default here is
   exactly what would turn US1 back into a decorative read (research.md
   "§4 decorative-read fix"): the point of this task is that a defender can
   point at the note line and at the tool-call argument it produced, and a
   silently-defaulted value has no note line to point at.
2. :func:`build_provenance_entry` -- the record that makes T030's value trace
   a lookup instead of a reconstruction: note path -> existing-server tool +
   result field -> katagiri tool + argument name -> where the value lands in
   this run's output.

Constitution VI (vault content is untrusted data, never instructions):
nothing here ever executes, formats-as-code, or otherwise interprets the
frontmatter value as a directive. It is read as one opaque string and handed
to a caller (:mod:`katagiri_agent.graph`) that only ever places it into a
tool-call keyword argument (``topic=...``). No other frontmatter key, and
none of the note's Markdown body, is read by this module at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

# ---------------------------------------------------------------------------
# The steering field this task extracts. Fixed here, not re-derived, per
# T009's two demo goal-note fixtures (tests/demo_fixtures/vault/00-goals/
# goal-note.md and goal-note.variant-b.md), which both use this exact key.
# ---------------------------------------------------------------------------

STEERING_FIELD: Final = "goal_theme"
"""The frontmatter key whose value is passed through as a literal argument."""

FRONTMATTER_DELIMITER: Final = "---"

# ---------------------------------------------------------------------------
# Explicit, reported statuses -- never a silent default. Every branch of
# :func:`parse_goal_note` returns exactly one of these; a caller only has to
# switch on this small closed set, never guess from ``None``.
# ---------------------------------------------------------------------------

STATUS_OK: Final = "ok"
STATUS_NO_CONTENT: Final = "no_content"
STATUS_MALFORMED_FRONTMATTER: Final = "malformed_frontmatter"
STATUS_MISSING_FIELD: Final = "missing_field"

GOAL_NOTE_STATUSES: Final[tuple[str, ...]] = (
    STATUS_OK,
    STATUS_NO_CONTENT,
    STATUS_MALFORMED_FRONTMATTER,
    STATUS_MISSING_FIELD,
)


@dataclass(frozen=True, slots=True)
class GoalNoteResult:
    """The outcome of reading one goal note's steering field.

    ``value`` is only ever a non-empty string when ``status == STATUS_OK``;
    every other status leaves it ``None`` on purpose, so a caller cannot
    accidentally read a value that was never actually parsed out.
    """

    status: str
    value: str | None
    frontmatter: dict[str, str] | None
    detail: str


def _extract_raw_text(vault_read_result: Any) -> str | None:
    """Normalize ``vault_read``'s ``ainvoke`` return value into raw note text.

    T005's spike (``agent/scripts/spike_existing.py``, ``_first_structured``)
    found the Streamable HTTP plugin's read call answers with either a bare
    string (the markdown body) or a dict carrying it under ``content`` /
    ``text`` / ``body``; a langchain_mcp_adapters tool built with
    ``response_format="content_and_artifact"`` can also hand back a
    ``(content, artifact)`` tuple, or a list of content blocks. This function
    mirrors that same tolerance so :func:`parse_goal_note` never has to care
    which shape the transport chose -- and returns ``None`` (never raises)
    when nothing readable is found, which :func:`parse_goal_note` turns into
    :data:`STATUS_NO_CONTENT`.
    """
    if isinstance(vault_read_result, str):
        return vault_read_result
    if isinstance(vault_read_result, dict):
        for key in ("content", "text", "body"):
            value = vault_read_result.get(key)
            if isinstance(value, str):
                return value
        return None
    if isinstance(vault_read_result, (list, tuple)):
        for item in vault_read_result:
            text = _extract_raw_text(item)
            if text is not None:
                return text
        return None
    return None


def _parse_frontmatter_block(raw: str) -> dict[str, str] | None:
    """Parse a leading ``---`` / ``---`` block into a flat ``str -> str`` dict.

    Deliberately **not** a general YAML parser -- ``agent/``'s pyproject
    carries no YAML dependency, and T009's two demo fixtures (the only
    frontmatter this task is scoped to read) are flat ``key: value`` lines
    with no nesting, lists, or quoting. Returns ``None`` (malformed) rather
    than guessing when: the note does not open with ``---`` on its own first
    line, the block never closes with a second ``---`` line, or a non-blank
    line inside the block is not ``key: value`` shaped.
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        return None

    frontmatter: dict[str, str] = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == FRONTMATTER_DELIMITER:
            closed = True
            break
        if not line.strip():
            continue
        if ":" not in line:
            return None
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            return None
        frontmatter[key] = value

    if not closed:
        return None
    return frontmatter


def parse_goal_note(vault_read_result: Any, *, note_path: str) -> GoalNoteResult:
    """Extract :data:`STEERING_FIELD` from ``vault_read``'s result.

    ``note_path`` is only used to build a human-readable ``detail`` string
    (the line a defender reads on screen) -- it plays no part in parsing.
    Every return path is one of :data:`GOAL_NOTE_STATUSES`; there is no
    silent-default branch here for the graph to accidentally hit.
    """
    raw = _extract_raw_text(vault_read_result)
    if raw is None or not raw.strip():
        return GoalNoteResult(
            status=STATUS_NO_CONTENT,
            value=None,
            frontmatter=None,
            detail=f"{note_path!r}: vault_read returned no readable text.",
        )

    frontmatter = _parse_frontmatter_block(raw)
    if frontmatter is None:
        return GoalNoteResult(
            status=STATUS_MALFORMED_FRONTMATTER,
            value=None,
            frontmatter=None,
            detail=(
                f"{note_path!r}: content does not open with a well-formed "
                f"'{FRONTMATTER_DELIMITER}' ... '{FRONTMATTER_DELIMITER}' "
                "frontmatter block."
            ),
        )

    value = frontmatter.get(STEERING_FIELD)
    if not value:
        return GoalNoteResult(
            status=STATUS_MISSING_FIELD,
            value=None,
            frontmatter=frontmatter,
            detail=(
                f"{note_path!r}: frontmatter has no non-empty "
                f"{STEERING_FIELD!r} key (keys present: {sorted(frontmatter)})."
            ),
        )

    return GoalNoteResult(
        status=STATUS_OK,
        value=value,
        frontmatter=frontmatter,
        detail=f"{note_path!r}: {STEERING_FIELD}={value!r}.",
    )


# ---------------------------------------------------------------------------
# Provenance: the record T030's value trace looks up instead of reconstructs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceEntry:
    """note path -> existing-server tool + result field -> katagiri tool +
    argument name -> where it shows up in the final output.

    Every field here is a plain string, so :meth:`as_dict` serializes
    losslessly into ``GraphState['provenance']`` (a JSON-able list of dicts)
    and into a checkpointed run's transcript.
    """

    note_path: str
    source_tool: str
    source_field: str
    value: str
    katagiri_tool: str
    katagiri_argument: str
    output_field: str

    def as_dict(self) -> dict[str, str]:
        return {
            "note_path": self.note_path,
            "source_tool": self.source_tool,
            "source_field": self.source_field,
            "value": self.value,
            "katagiri_tool": self.katagiri_tool,
            "katagiri_argument": self.katagiri_argument,
            "output_field": self.output_field,
        }

    def __str__(self) -> str:  # pragma: no cover - convenience for transcripts
        return (
            f"{self.note_path} --{self.source_tool}--> "
            f"{self.source_field}={self.value!r} --literal-arg--> "
            f"{self.katagiri_tool}({self.katagiri_argument}={self.value!r}) "
            f"--> state[{self.output_field!r}]"
        )


def build_provenance_entry(
    *,
    note_path: str,
    value: str,
    katagiri_tool: str,
    katagiri_argument: str,
    output_field: str,
    source_tool: str = "vault_read",
    source_field: str = STEERING_FIELD,
) -> ProvenanceEntry:
    """Build the one provenance record for a literal-arg passthrough.

    Called from :mod:`katagiri_agent.graph`'s exercise/review path nodes at
    the moment the value is actually placed into a katagiri tool call --
    not earlier -- so ``katagiri_tool``/``katagiri_argument``/``output_field``
    are never guessed ahead of the routing decision.
    """
    return ProvenanceEntry(
        note_path=note_path,
        source_tool=source_tool,
        source_field=source_field,
        value=value,
        katagiri_tool=katagiri_tool,
        katagiri_argument=katagiri_argument,
        output_field=output_field,
    )


__all__ = [
    "FRONTMATTER_DELIMITER",
    "GOAL_NOTE_STATUSES",
    "GoalNoteResult",
    "ProvenanceEntry",
    "STATUS_MALFORMED_FRONTMATTER",
    "STATUS_MISSING_FIELD",
    "STATUS_NO_CONTENT",
    "STATUS_OK",
    "STEERING_FIELD",
    "build_provenance_entry",
    "parse_goal_note",
]
