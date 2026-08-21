"""T017: graph-level integration smoke test for T013's diagnostic-branch
graph, run end to end with the **existing** server stubbed (``vault_read``)
and every katagiri tool stubbed too -- no MCP server, no network call,
runtime well under a second.

This complements ``test_smoke_stdio.py`` (which proves the wire path to the
real katagiri server works) by proving the *graph* built on top of that
wire path does the right thing once it has real-shaped data flowing through
it: branch on server-computed ``action.kind`` (never model free-choice),
literal-arg passthrough of the goal note's ``goal_theme`` frontmatter field
into the tool call the branch selects, and a provenance entry recorded at
the moment that happens (T014).

Two scenarios:

- ``test_wellformed_goal_note_routes_and_passes_through_topic`` -- variant A
  of the demo goal note (``tests/demo_fixtures/vault/00-goals/goal-note.md``,
  ``goal_theme: food``) read verbatim off disk and handed back by the
  stubbed ``vault_read``. ``start_session`` is stubbed to return
  ``action.kind == "continue_next_step"``, which
  :data:`katagiri_agent.graph.ACTION_KIND_TO_PATH` maps to the exercise
  path -- so this scenario checks the branch, the passthrough, and the
  provenance entry all at once.
- ``test_malformed_frontmatter_reports_its_status`` -- content that does not
  open with a well-formed frontmatter block. ``start_session`` is stubbed to
  return ``action.kind == "revisit_topic"`` (the review path), so this
  scenario checks the *other* two things: ``goal_note_status`` names the
  failure explicitly (never a silent default), and, since no ``goal_theme``
  was ever parsed, no provenance entry is appended -- the action's own
  ``topic`` field is what reaches the tool call instead.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from katagiri_agent.goal_note import STATUS_MALFORMED_FRONTMATTER, STATUS_OK
from katagiri_agent.graph import (
    PATH_EXERCISE,
    PATH_REVIEW,
    build_graph,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GOAL_NOTE_VARIANT_A = (
    _REPO_ROOT / "tests" / "demo_fixtures" / "vault" / "00-goals" / "goal-note.md"
)


def run_async(fn):
    """Run an ``async def test_...`` synchronously via ``asyncio.run``.

    Same rationale and same shape as ``test_smoke_stdio.py``'s copy of this
    helper (no ``pytest-asyncio`` dependency in this project) -- kept as an
    independent copy per file rather than a shared import, on purpose.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


@dataclass
class RecordingStubTool:
    """A minimal :class:`katagiri_agent.graph.ToolLike` stub.

    Satisfies the one method the graph's nodes need (``ainvoke``), records
    every call's arguments (so a test can assert on them afterward), and
    answers from a caller-supplied ``handler`` -- a plain function of the
    call's ``dict`` arguments to whatever result that tool would have
    returned for real.
    """

    name: str
    handler: Callable[[dict[str, Any]], Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def ainvoke(self, input: Mapping[str, Any]) -> Any:
        args = dict(input)
        self.calls.append(args)
        return self.handler(args)


def _read_variant_a_content() -> str:
    return _GOAL_NOTE_VARIANT_A.read_text(encoding="utf-8")


@run_async
async def test_wellformed_goal_note_routes_and_passes_through_topic():
    vault_read = RecordingStubTool(
        name="vault_read", handler=lambda args: _read_variant_a_content()
    )
    start_session = RecordingStubTool(
        name="start_session",
        handler=lambda args: {
            "session_id": "sess-wellformed",
            "action": {
                "kind": "continue_next_step",
                "topic": "fallback-topic-should-not-be-used",
                "instruction": "keep drilling the current unit",
            },
        },
    )
    gen_exercise = RecordingStubTool(
        name="gen_exercise", handler=lambda args: {"exercise_id": "ex-1", "items": []}
    )
    log_lesson = RecordingStubTool(
        name="log_lesson", handler=lambda args: {"lesson_id": "lesson-1"}
    )
    log_observations = RecordingStubTool(
        name="log_observations", handler=lambda args: {"written": 1}
    )

    tools = {
        "vault_read": vault_read,
        "start_session": start_session,
        "gen_exercise": gen_exercise,
        "log_lesson": log_lesson,
        "log_observations": log_observations,
    }
    graph = build_graph(tools=tools)

    result = await graph.ainvoke(
        {
            "session_id": None,
            "tired": False,
            "goal_note_path": "00-goals/goal-note.md",
        }
    )

    # The branch: action.kind="continue_next_step" -> path="exercise", per
    # ACTION_KIND_TO_PATH -- read back from state, not re-derived here, so
    # this assertion fails if the graph's own routing disagrees with the
    # table it is supposed to follow.
    assert result["action"]["kind"] == "continue_next_step"
    assert result["path"] == PATH_EXERCISE

    # The goal note parsed cleanly, so goal_theme ("food", variant A's
    # frontmatter value) is what reached gen_exercise -- not the action's
    # own fallback topic.
    assert result["goal_note_status"] == STATUS_OK
    assert result["goal_theme"] == "food"
    assert len(gen_exercise.calls) == 1
    assert gen_exercise.calls[0]["topic"] == "food"

    # Provenance: exactly one entry, recorded at the moment goal_theme
    # landed in gen_exercise's topic argument.
    assert len(result["provenance"]) == 1
    entry = result["provenance"][0]
    assert entry["value"] == "food"
    assert entry["katagiri_tool"] == "gen_exercise"
    assert entry["katagiri_argument"] == "topic"
    assert entry["output_field"] == "exercise_result"

    # The write side (US2 acceptance 3) actually ran.
    assert len(log_lesson.calls) == 1
    assert len(log_observations.calls) == 1
    assert result["summary"]


@run_async
async def test_malformed_frontmatter_reports_its_status():
    malformed_content = (
        "# Not a goal note\n\nThis file has no frontmatter block at all.\n"
    )
    vault_read = RecordingStubTool(
        name="vault_read", handler=lambda args: malformed_content
    )
    start_session = RecordingStubTool(
        name="start_session",
        handler=lambda args: {
            "session_id": "sess-malformed",
            "action": {
                "kind": "revisit_topic",
                "topic": "action-level-fallback-topic",
                "instruction": "revisit the due objective",
            },
        },
    )
    find_i_plus_one = RecordingStubTool(
        name="find_i_plus_one", handler=lambda args: {"items": []}
    )
    log_lesson = RecordingStubTool(
        name="log_lesson", handler=lambda args: {"lesson_id": "lesson-2"}
    )
    log_observations = RecordingStubTool(
        name="log_observations", handler=lambda args: {"written": 1}
    )

    tools = {
        "vault_read": vault_read,
        "start_session": start_session,
        "find_i_plus_one": find_i_plus_one,
        "log_lesson": log_lesson,
        "log_observations": log_observations,
    }
    graph = build_graph(tools=tools)

    result = await graph.ainvoke(
        {
            "session_id": None,
            "tired": False,
            "goal_note_path": "00-goals/malformed.md",
        }
    )

    # The branch still runs correctly on the (different) server-computed
    # action.kind, independent of the frontmatter outcome.
    assert result["action"]["kind"] == "revisit_topic"
    assert result["path"] == PATH_REVIEW

    # The failure is reported explicitly -- never a silent default -- and no
    # goal_theme value exists to pass through.
    assert result["goal_note_status"] == STATUS_MALFORMED_FRONTMATTER
    assert result["goal_theme"] is None

    # find_i_plus_one still gets called, on the review path, but with the
    # prescribed action's own topic (the only source left) rather than a
    # frontmatter value that was never successfully parsed.
    assert len(find_i_plus_one.calls) == 1
    assert find_i_plus_one.calls[0]["topic"] == "action-level-fallback-topic"

    # No provenance entry: goal_theme was never truthy, so the passthrough
    # branch that records one never ran.
    assert result["provenance"] == []

    assert len(log_lesson.calls) == 1
    assert len(log_observations.calls) == 1
