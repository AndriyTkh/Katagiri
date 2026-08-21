"""T027b: wiring tests for ``katagiri_agent.graph`` -- resilience routing
(part b) and model-in-graph (part c), stubbed end to end. No MCP server, no
network call: every tool is a stub, and the "model" is a stub object with
an ``ainvoke`` coroutine returning a canned response.

Four things this file checks:

1. A transport failure on the **existing** server's ``vault_read`` call
   degrades -- the run completes to a summary, and the degradation is
   recorded in ``state["degraded"]``, never silently hidden and never a
   crashed run.
2. A transport failure on a **katagiri** call has no fallback -- it
   propagates as a classified :class:`resilience.TransportError`, since
   katagiri is the server this whole flow exists to exercise.
3. A missing note is a **successful empty result**, not an exception --
   ``goal_note_status`` lands on ``STATUS_NO_CONTENT`` and the run completes
   normally.
4. ``GraphDeps.model`` set vs. ``None``: the model-provided path exercises
   the stub model's ``ainvoke`` for both grading and the summary; the
   model-``None`` path stays on the deterministic fallback (already
   covered implicitly by ``test_graph_flow.py`` staying green, checked here
   explicitly too).
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import pytest

from katagiri_agent import resilience
from katagiri_agent.goal_note import STATUS_NO_CONTENT
from katagiri_agent.graph import PATH_EXERCISE, build_graph


def run_async(fn):
    """Run an ``async def test_...`` synchronously via ``asyncio.run``.

    Same rationale as this project's other test files -- no
    ``pytest-asyncio`` dependency, kept as an independent copy per file.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# A fast policy: two attempts, effectively no sleep, so a failing-retry test
# runs in milliseconds instead of the real ~0.75s default backoff.
FAST_POLICY = resilience.RetryPolicy(attempts=2, base_delay=0.001, factor=1.0)


@dataclass
class RecordingStubTool:
    """Same shape as ``test_graph_flow.py``'s stub -- copied, not imported,
    per this project's per-file-copy convention for test helpers.
    """

    name: str
    handler: Callable[[dict[str, Any]], Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def ainvoke(self, input: Mapping[str, Any]) -> Any:
        args = dict(input)
        self.calls.append(args)
        return self.handler(args)


@dataclass
class AlwaysFailsStubTool:
    """A tool whose every call raises the same exception.

    Used to drive a retry loop to full exhaustion deterministically (no
    "succeeds on attempt N" flakiness) -- exactly what a permanently-dead
    connection would do.
    """

    name: str
    exc: BaseException
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def ainvoke(self, input: Mapping[str, Any]) -> Any:
        self.calls.append(dict(input))
        raise self.exc


def _base_tools() -> dict[str, RecordingStubTool]:
    """The full stub-tool set a well-formed run needs, minus ``vault_read``."""
    return {
        "start_session": RecordingStubTool(
            name="start_session",
            handler=lambda args: {
                "session_id": "sess-1",
                "action": {
                    "kind": "continue_next_step",
                    "topic": "fallback-topic",
                    "instruction": "keep drilling",
                },
            },
        ),
        "gen_exercise": RecordingStubTool(
            name="gen_exercise", handler=lambda args: {"exercise_id": "ex-1", "items": []}
        ),
        "log_lesson": RecordingStubTool(
            name="log_lesson", handler=lambda args: {"lesson_id": "lesson-1"}
        ),
        "log_observations": RecordingStubTool(
            name="log_observations", handler=lambda args: {"written": 1}
        ),
    }


# ---------------------------------------------------------------------------
# 1. Obsidian-side transport exhaustion -> degraded, katagiri-only completion.
# ---------------------------------------------------------------------------


@run_async
async def test_obsidian_transport_exhaustion_degrades_and_completes():
    tools = _base_tools()
    tools["vault_read"] = AlwaysFailsStubTool(
        name="vault_read", exc=ConnectionError("connection closed")
    )
    graph = build_graph(tools=tools, retry_policy=FAST_POLICY)

    result = await graph.ainvoke(
        {"session_id": None, "tired": False, "goal_note_path": "00-goals/goal-note.md"}
    )

    # The run completed all the way to a summary -- degraded, not crashed.
    assert result["summary"]
    assert result["path"] == PATH_EXERCISE

    # No goal_theme/goal_note reached the graph, and the degradation is
    # recorded in state, not only printed.
    assert result["goal_theme"] is None
    assert result["goal_note"] is None
    assert len(result["degraded"]) == 1
    entry = result["degraded"][0]
    assert entry["node"] == "read_goal_note"
    assert entry["server"] == "obsidian"

    # The retry loop actually ran to exhaustion (FAST_POLICY.attempts == 2).
    assert len(tools["vault_read"].calls) == 2

    # katagiri-only continuation: the exercise path still ran, using the
    # prescribed action's own fallback topic since no goal_theme exists.
    assert tools["gen_exercise"].calls[0]["topic"] == "fallback-topic"
    assert len(tools["log_lesson"].calls) == 1
    assert len(tools["log_observations"].calls) == 1


# ---------------------------------------------------------------------------
# 2. Katagiri-side transport exhaustion -> propagates, no fallback exists.
# ---------------------------------------------------------------------------


@run_async
async def test_katagiri_transport_exhaustion_propagates():
    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(name="vault_read", handler=lambda args: "unused")
    tools["start_session"] = AlwaysFailsStubTool(
        name="start_session", exc=ConnectionError("connection refused")
    )
    graph = build_graph(tools=tools, retry_policy=FAST_POLICY)

    with pytest.raises(resilience.TransportError) as excinfo:
        await graph.ainvoke(
            {"session_id": None, "tired": False, "goal_note_path": None}
        )

    assert excinfo.value.server == "katagiri"
    # Exhausted the fast policy's two attempts -- no silent single-try give-up.
    assert len(tools["start_session"].calls) == 2


@run_async
async def test_katagiri_auth_error_never_retries():
    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(name="vault_read", handler=lambda args: "unused")
    tools["start_session"] = AlwaysFailsStubTool(
        name="start_session", exc=RuntimeError("401 unauthorized")
    )
    graph = build_graph(tools=tools, retry_policy=FAST_POLICY)

    with pytest.raises(resilience.AuthError):
        await graph.ainvoke({"session_id": None, "tired": False, "goal_note_path": None})

    # AuthError never retries, even though FAST_POLICY allows 2 attempts.
    assert len(tools["start_session"].calls) == 1


# ---------------------------------------------------------------------------
# 3. Missing note -> EmptyResult semantics, not an exception.
# ---------------------------------------------------------------------------


@run_async
async def test_missing_note_is_empty_result_not_exception():
    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(
        name="vault_read",
        handler=lambda args: {"found": False, "error": "note not found"},
    )
    graph = build_graph(tools=tools, retry_policy=FAST_POLICY)

    result = await graph.ainvoke(
        {"session_id": None, "tired": False, "goal_note_path": "00-goals/missing.md"}
    )

    # No exception reached the caller; the run completed with a named,
    # non-crashing status -- distinguishable from the degraded case above
    # (no state["degraded"] entry: obsidian answered successfully, it just
    # found nothing).
    assert result["goal_note_status"] == STATUS_NO_CONTENT
    assert result.get("degraded") in (None, [])
    assert result["summary"]

    # Only one call -- a successful empty result never triggers a retry.
    assert len(tools["vault_read"].calls) == 1


# ---------------------------------------------------------------------------
# 4. Model-provided vs. model-None grading/summary paths.
# ---------------------------------------------------------------------------


@dataclass
class _StubModelResponse:
    content: str


class StubModel:
    """A fake bound chat model: records every prompt, answers canned text.

    No network call -- ``ainvoke`` is a plain coroutine returning a
    pre-built response object with a ``.content`` attribute, the same shape
    a real ``AIMessage`` has.
    """

    def __init__(self, feedback: str, summary: str) -> None:
        self.feedback = feedback
        self.summary = summary
        self.prompts: list[str] = []
        self._calls = 0

    async def ainvoke(self, prompt: str) -> _StubModelResponse:
        self.prompts.append(prompt)
        self._calls += 1
        # grade_node calls first, summary_node calls second, in one run.
        text = self.feedback if self._calls == 1 else self.summary
        return _StubModelResponse(content=text)


@run_async
async def test_model_provided_path_used_for_grade_and_summary():
    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(name="vault_read", handler=lambda args: "unused")
    model = StubModel(feedback="great job on the cloze set", summary="Session complete: solid.")
    graph = build_graph(tools=tools, model=model, retry_policy=FAST_POLICY)

    result = await graph.ainvoke(
        {"session_id": None, "tired": False, "goal_note_path": None}
    )

    assert len(model.prompts) == 2
    assert result["grade"]["model_feedback"] == "great job on the cloze set"
    # Baseline mandatory fields are still present -- the model only adds
    # free-text feedback, it never has to reproduce log_observations' shape.
    assert result["grade"]["task_type"] == "cloze_production"
    assert result["grade"]["unassisted"] is True
    assert result["summary"] == "Session complete: solid."


@run_async
async def test_model_none_path_stays_deterministic():
    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(name="vault_read", handler=lambda args: "unused")
    graph = build_graph(tools=tools, retry_policy=FAST_POLICY)

    result = await graph.ainvoke(
        {"session_id": None, "tired": False, "goal_note_path": None}
    )

    # No model_feedback key: the deterministic _default_grader ran, not
    # _model_grader.
    assert "model_feedback" not in result["grade"]
    assert result["grade"]["task_type"] == "cloze_production"
    assert result["summary"].startswith("session=")


@run_async
async def test_explicit_grader_still_wins_over_model():
    """An explicit ``grader`` (T013's original override point) beats even a
    non-``None`` ``model`` -- the precedence documented on ``GraphDeps``.
    """

    async def stub_grader(state):
        return {
            "task_type": "custom",
            "unassisted": False,
            "coverage_band": "0-10",
            "rubric_version": "test-v1",
        }

    tools = _base_tools()
    tools["vault_read"] = RecordingStubTool(name="vault_read", handler=lambda args: "unused")
    model = StubModel(feedback="should never be used", summary="should never be used either")
    graph = build_graph(tools=tools, model=model, grader=stub_grader, retry_policy=FAST_POLICY)

    result = await graph.ainvoke(
        {"session_id": None, "tired": False, "goal_note_path": None}
    )

    assert result["grade"]["task_type"] == "custom"
    # The model is still consulted by summary_node (grader only overrides
    # grading), so exactly one prompt (the summary's) reaches it -- and
    # since it is StubModel's *first* call, it gets the "feedback" canned
    # text, not the "summary" one (that branch only fires on a second call,
    # which grading never makes here because stub_grader intercepted it).
    assert len(model.prompts) == 1
    assert result["summary"] == "should never be used"
