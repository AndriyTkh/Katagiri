"""T015 tests: failure taxonomy, retry/backoff/reconnect, degraded path,
and the one real Windows kill-and-resume test.

Layout:

- ``TestClassifyException`` -- structural + fallback classification.
- ``TestResilientCall`` -- retry/backoff/reconnect and the empty-vs-failure
  split, with fake clocks (no real ``asyncio.sleep``).
- ``TestCallOrDegrade`` -- the degraded-path wrapper.
- ``TestScriptedInjections`` -- the assignment's three named failures
  (plugin stopped / invalid API key / missing note), each run through a
  stub tool shaped like :class:`katagiri_agent.graph.ToolLike` so the test
  reads the same way a real ``tool.ainvoke(...)`` call would fail.
- ``test_kill_and_resume_real_subprocess`` -- spawns a real Python
  subprocess that runs T013's graph with a file-backed checkpointer, kills
  it after one checkpoint is committed, restarts it, and asserts the
  resumed run does not replay the already-committed node.
"""

from __future__ import annotations

import asyncio
import functools
import subprocess
import sys
import time
from pathlib import Path

import pytest

from katagiri_agent.resilience import (
    AuthError,
    Degraded,
    EmptyResult,
    ResilienceError,
    RetryPolicy,
    ToolCallError,
    TransportError,
    call_or_degrade,
    classify_exception,
    resilient_call,
)


def run_async(fn):
    """Run an ``async def test_...`` synchronously via ``asyncio.run``.

    No ``pytest-asyncio`` dependency in this project (it is a separate uv
    subproject on purpose, see ``clients.py``'s docstring, and pulling in a
    new test-only plugin just for this file is not worth the added
    surface) -- this is the whole bridge needed instead.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHTTPStatusError(Exception):
    def __init__(self, status_code: int, message: str = "HTTP error") -> None:
        self.response = _FakeResponse(status_code)
        super().__init__(message)


class TestClassifyException:
    def test_http_401_is_auth_error(self) -> None:
        err = classify_exception(_FakeHTTPStatusError(401), server="obsidian")
        assert isinstance(err, AuthError)
        assert err.server == "obsidian"

    def test_http_403_is_auth_error(self) -> None:
        err = classify_exception(_FakeHTTPStatusError(403), server="obsidian")
        assert isinstance(err, AuthError)

    def test_connection_refused_is_transport_error(self) -> None:
        err = classify_exception(
            ConnectionRefusedError("[WinError 1225] connection refused"), server="obsidian"
        )
        assert isinstance(err, TransportError)
        assert err.server == "obsidian"

    def test_timeout_is_transport_error(self) -> None:
        err = classify_exception(TimeoutError("timed out"), server="obsidian")
        assert isinstance(err, TransportError)

    def test_connection_closed_message_is_transport_error(self) -> None:
        # research.md names this exact phrase from open bug reports against
        # MultiServerMCPClient on Windows stdio -- no structured status
        # code is available for this one, hence the string fallback.
        err = classify_exception(RuntimeError("Connection closed"), server="katagiri")
        assert isinstance(err, TransportError)

    def test_invalid_api_key_message_is_auth_error(self) -> None:
        err = classify_exception(RuntimeError("401 Unauthorized: invalid api key"), server="obsidian")
        assert isinstance(err, AuthError)

    def test_unrecognised_exception_is_tool_call_error(self) -> None:
        err = classify_exception(ValueError("malformed topic argument"), server="katagiri")
        assert isinstance(err, ToolCallError)
        assert not isinstance(err, (TransportError, AuthError))

    def test_every_category_is_a_resilience_error_and_distinct(self) -> None:
        auth = classify_exception(_FakeHTTPStatusError(401), server="s")
        transport = classify_exception(ConnectionRefusedError(), server="s")
        generic = classify_exception(ValueError("x"), server="s")
        for err in (auth, transport, generic):
            assert isinstance(err, ResilienceError)
        assert {type(auth), type(transport), type(generic)} == {
            AuthError,
            TransportError,
            ToolCallError,
        }


# ---------------------------------------------------------------------------
# resilient_call
# ---------------------------------------------------------------------------


class _FakeClock:
    """Records backoff delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)


class TestResilientCall:
    @run_async
    async def test_retries_transport_error_then_succeeds(self) -> None:
        attempts = {"n": 0}
        reconnects = {"n": 0}

        async def flaky_call() -> dict:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionRefusedError("plugin stopped")
            return {"ok": True}

        async def reconnect() -> None:
            reconnects["n"] += 1

        clock = _FakeClock()
        result = await resilient_call(
            server="obsidian",
            tool="vault_read",
            call=flaky_call,
            reconnect=reconnect,
            policy=RetryPolicy(attempts=3, base_delay=0.1, factor=2.0),
            sleep=clock.sleep,
        )

        assert result == {"ok": True}
        assert attempts["n"] == 3
        assert reconnects["n"] == 2  # once before each retry, not after success
        assert clock.delays == [0.1, 0.2]  # base_delay * factor**(n-1)

    @run_async
    async def test_exhausts_retries_and_raises_transport_error(self) -> None:
        async def always_fails() -> dict:
            raise ConnectionRefusedError("plugin stopped")

        clock = _FakeClock()
        with pytest.raises(TransportError) as excinfo:
            await resilient_call(
                server="obsidian",
                tool="vault_read",
                call=always_fails,
                policy=RetryPolicy(attempts=3, base_delay=0.01, factor=2.0),
                sleep=clock.sleep,
            )
        assert excinfo.value.server == "obsidian"
        assert len(clock.delays) == 2  # slept between attempts 1->2 and 2->3, not after the last

    @run_async
    async def test_auth_error_is_never_retried(self) -> None:
        attempts = {"n": 0}

        async def bad_key() -> dict:
            attempts["n"] += 1
            raise _FakeHTTPStatusError(401, "invalid api key")

        clock = _FakeClock()
        with pytest.raises(AuthError):
            await resilient_call(
                server="obsidian",
                tool="vault_read",
                call=bad_key,
                policy=RetryPolicy(attempts=5, base_delay=0.01),
                sleep=clock.sleep,
            )
        assert attempts["n"] == 1
        assert clock.delays == []

    @run_async
    async def test_missing_note_is_empty_result_not_an_exception(self) -> None:
        async def missing_note() -> dict:
            return {"found": False, "path": "does/not/exist.md"}

        def classify_empty(result: dict) -> EmptyResult | None:
            if not result.get("found", True):
                return EmptyResult(
                    server="obsidian", tool="vault_read", reason="note not found", payload=result
                )
            return None

        outcome = await resilient_call(
            server="obsidian",
            tool="vault_read",
            call=missing_note,
            is_empty_result=classify_empty,
        )

        assert isinstance(outcome, EmptyResult)
        assert outcome.reason == "note not found"
        assert not isinstance(outcome, Exception)

    @run_async
    async def test_found_result_passes_through_unwrapped(self) -> None:
        async def found_note() -> dict:
            return {"found": True, "content": "# Goal\n"}

        def classify_empty(result: dict) -> EmptyResult | None:
            return None if result.get("found") else EmptyResult(
                server="obsidian", tool="vault_read", reason="not found"
            )

        outcome = await resilient_call(
            server="obsidian", tool="vault_read", call=found_note, is_empty_result=classify_empty
        )
        assert outcome == {"found": True, "content": "# Goal\n"}


# ---------------------------------------------------------------------------
# call_or_degrade
# ---------------------------------------------------------------------------


class TestCallOrDegrade:
    @run_async
    async def test_degrades_after_exhausted_transport_retries(self) -> None:
        async def always_down() -> dict:
            raise ConnectionRefusedError("plugin stopped")

        clock = _FakeClock()
        result, degraded = await call_or_degrade(
            server="obsidian",
            tool="vault_read",
            call=always_down,
            policy=RetryPolicy(attempts=2, base_delay=0.01),
            sleep=clock.sleep,
        )

        assert result is None
        assert isinstance(degraded, Degraded)
        assert degraded.server == "obsidian"
        assert "DEGRADED" in degraded.message()
        assert "obsidian" in degraded.message()

    @run_async
    async def test_auth_error_still_raises_not_degraded(self) -> None:
        async def bad_key() -> dict:
            raise _FakeHTTPStatusError(401, "invalid api key")

        with pytest.raises(AuthError):
            await call_or_degrade(
                server="obsidian", tool="vault_read", call=bad_key,
                policy=RetryPolicy(attempts=2, base_delay=0.01),
            )

    @run_async
    async def test_success_returns_result_and_no_degradation(self) -> None:
        async def ok_call() -> dict:
            return {"found": True}

        result, degraded = await call_or_degrade(server="obsidian", tool="vault_read", call=ok_call)
        assert result == {"found": True}
        assert degraded is None


# ---------------------------------------------------------------------------
# Scripted injections for the assignment's three named failures
# ---------------------------------------------------------------------------


class _StubTool:
    """Shaped like ``katagiri_agent.graph.ToolLike`` -- one async ``ainvoke``."""

    def __init__(self, name: str, fn) -> None:
        self.name = name
        self._fn = fn

    async def ainvoke(self, input):  # noqa: A002 -- matches ToolLike's signature
        return await self._fn(input)


class TestScriptedInjections:
    @run_async
    async def test_plugin_stopped(self) -> None:
        """The existing server (Obsidian plugin) is not running."""

        async def plugin_down(_args):
            raise ConnectionRefusedError(
                "[WinError 1225] The remote computer refused the network connection"
            )

        tool = _StubTool("vault_read", plugin_down)
        clock = _FakeClock()
        with pytest.raises(TransportError) as excinfo:
            await resilient_call(
                server="obsidian",
                tool=tool.name,
                call=lambda: tool.ainvoke({"path": "goal.md"}),
                policy=RetryPolicy(attempts=3, base_delay=0.01),
                sleep=clock.sleep,
            )
        report = str(excinfo.value)
        assert "obsidian" in report  # which server
        assert "refused" in report.lower() or "connection" in report.lower()  # why

    @run_async
    async def test_invalid_api_key(self) -> None:
        """The existing server rejects our bearer token."""

        async def unauthorized(_args):
            raise _FakeHTTPStatusError(401, "Unauthorized: invalid bearer token")

        tool = _StubTool("vault_read", unauthorized)
        clock = _FakeClock()
        with pytest.raises(AuthError) as excinfo:
            await resilient_call(
                server="obsidian",
                tool=tool.name,
                call=lambda: tool.ainvoke({"path": "goal.md"}),
                policy=RetryPolicy(attempts=3, base_delay=0.01),
                sleep=clock.sleep,
            )
        assert "obsidian" in str(excinfo.value)
        assert clock.delays == []  # never retried

    @run_async
    async def test_missing_note(self) -> None:
        """The note path does not exist -- a successful, empty answer."""

        async def not_found(_args):
            return {"found": False, "path": "goal-note-that-does-not-exist.md"}

        tool = _StubTool("vault_read", not_found)

        def classify_empty(result):
            if not result.get("found", True):
                return EmptyResult(
                    server="obsidian", tool=tool.name, reason="note not found", payload=result
                )
            return None

        outcome = await resilient_call(
            server="obsidian",
            tool=tool.name,
            call=lambda: tool.ainvoke({"path": "goal-note-that-does-not-exist.md"}),
            is_empty_result=classify_empty,
        )

        # The load-bearing assertion: this is a *value*, not a raised
        # exception -- the caller distinguishes it from the two failure
        # cases above by isinstance/type, not by inspecting a message.
        assert isinstance(outcome, EmptyResult)
        assert not isinstance(outcome, ResilienceError)


# ---------------------------------------------------------------------------
# The one real kill-and-resume test (Windows subprocess)
# ---------------------------------------------------------------------------

_SUBPROCESS_SCRIPT = r'''
import asyncio
import os
import sys

from katagiri_agent import graph as g
from katagiri_agent.checkpoint import open_checkpointer, thread_config


def _record(log_path, name):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(name + "\n")


class _Tool:
    def __init__(self, name, fn):
        self.name = name
        self._fn = fn

    async def ainvoke(self, args):
        return await self._fn(args)


async def main() -> None:
    db_path, log_path, marker_path, block_flag = sys.argv[1:5]
    block = block_flag == "1"
    # block_flag == "1": a fresh run -- pass the real initial state.
    # block_flag == "0": a *resume* -- LangGraph only continues an
    # interrupted thread instead of starting a new turn from START when the
    # input is None; re-passing the initial state here would start a
    # second, independent run on the same thread_id instead of resuming it.
    run_input = {"tired": False} if block else None

    async def start_session(args):
        _record(log_path, "start_session")
        return {
            "session_id": "kill-resume-thread",
            "action": {"kind": "continue_next_step", "topic": "particles"},
        }

    async def gen_exercise(args):
        _record(log_path, "gen_exercise")
        if block:
            # Signal the parent that open_session's checkpoint is already
            # committed (this node only runs after that write), then hang
            # long enough for the parent to TerminateProcess us.
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write("ready")
            await asyncio.sleep(30)
        return {"exercise": "stub"}

    async def log_lesson(args):
        _record(log_path, "log_lesson")
        return {"lesson_id": "L1"}

    async def log_observations(args):
        _record(log_path, "log_observations")
        return {"written": 1}

    tools = g.tools_by_name(
        [
            _Tool("start_session", start_session),
            _Tool("gen_exercise", gen_exercise),
            _Tool("log_lesson", log_lesson),
            _Tool("log_observations", log_observations),
        ]
    )

    with open_checkpointer(db_path) as saver:
        compiled = g.build_graph(tools, checkpointer=saver)
        cfg = thread_config("kill-resume-thread")
        await compiled.ainvoke(run_input, cfg)


if __name__ == "__main__":
    asyncio.run(main())
'''


def _wait_for_file(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.slow
def test_kill_and_resume_real_subprocess(tmp_path: Path) -> None:
    """Kill the agent process after a checkpoint write; restart; assert
    resume, not replay.

    Real ``subprocess`` + real ``Process.kill()`` (``TerminateProcess`` on
    Windows -- no chance for cleanup code to run) + a real file-backed
    ``AsyncBridgeSqliteSaver``. The killed run's ``gen_exercise`` node
    writes a marker *after* ``open_session``'s checkpoint has already been
    committed (LangGraph checkpoints after each completed node) and then
    hangs; once the marker appears we kill the process. The restarted run
    reuses the same checkpoint file and ``thread_id``.

    The assertion that matters: across both runs, ``start_session`` (the
    ``open_session`` node, committed before the kill) is called **exactly
    once** -- proof the resumed run did not replay it -- while
    ``gen_exercise`` (interrupted mid-node, never committed) and everything
    after it run only in the resumed process.
    """
    script_path = tmp_path / "kill_resume_flow.py"
    script_path.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")

    db_path = tmp_path / "checkpoints.sqlite"
    log_path = tmp_path / "calls.log"
    marker_path = tmp_path / "marker.txt"

    # --- Run 1: block inside gen_exercise, then kill after the marker ---
    proc1 = subprocess.Popen(
        [sys.executable, str(script_path), str(db_path), str(log_path), str(marker_path), "1"],
    )
    try:
        appeared = _wait_for_file(marker_path, timeout=20.0)
        assert appeared, "gen_exercise never signalled readiness before timeout"
        # open_session's checkpoint is committed by the time gen_exercise
        # runs at all (it is the next node); killing now is "after a
        # checkpoint write, before the flow finished".
        proc1.kill()
        proc1.wait(timeout=10)
    finally:
        if proc1.poll() is None:
            proc1.kill()
            proc1.wait(timeout=10)

    assert marker_path.exists()
    log_after_kill = log_path.read_text(encoding="utf-8").splitlines()
    assert log_after_kill.count("start_session") == 1
    assert log_after_kill.count("gen_exercise") == 1
    assert "log_lesson" not in log_after_kill  # never reached before the kill

    # --- Run 2: resume against the same db + thread_id, let it finish ---
    marker_path2 = tmp_path / "marker2.txt"
    proc2 = subprocess.run(
        [sys.executable, str(script_path), str(db_path), str(log_path), str(marker_path2), "0"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc2.returncode == 0, f"resumed run failed: {proc2.stderr}"

    full_log = log_path.read_text(encoding="utf-8").splitlines()
    # The load-bearing assertions: resumed, not replayed.
    assert full_log.count("start_session") == 1, (
        "start_session ran again after resume -- the flow replayed a "
        f"committed node instead of resuming from checkpoint. Full log: {full_log}"
    )
    assert full_log.count("gen_exercise") == 2, (
        "gen_exercise (interrupted, never committed) should re-run exactly "
        f"once on resume. Full log: {full_log}"
    )
    assert full_log.count("log_lesson") == 1
    assert full_log.count("log_observations") == 1
