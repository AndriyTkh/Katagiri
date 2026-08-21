"""T015: failure taxonomy, retry/backoff/reconnect, and the degraded path.

spec.md US4 / research.md "Failure handling" ask for three things a caller
must be able to tell apart **by type or shape, never by string-matching a
message**:

1. **Transport loss** -- the existing server's connection dropped or was
   never reachable (the assignment's "plugin stopped" injection). Raised as
   :class:`TransportError`, naming *which* server and *why*, in human-
   readable form.
2. **Auth failure** -- the existing server is reachable but rejects our
   credentials (the assignment's "invalid API key" injection). Raised as
   :class:`AuthError`. Never retried -- a rejected key is not a transient
   condition backoff can fix.
3. **A successful call that found nothing** (the assignment's "missing
   note" injection). This is **not a failure**: it is returned as
   :class:`EmptyResult`, never raised, so ``isinstance(x, EmptyResult)`` vs.
   ``except ResilienceError`` is the caller's whole decision -- no message
   parsing required.

:func:`resilient_call` is the retry/backoff/reconnect loop over one tool
call; :func:`call_or_degrade` wraps it once more so that an existing-server
connection that never recovers ends the run on a **degraded katagiri-only**
path that *states* its degradation (:class:`Degraded`) rather than crashing
or silently pretending nothing happened.

Nothing here touches ``katagiri_agent.graph`` -- T017 is the task that wires
this module in at the node boundaries T013 already defined.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# The failure taxonomy
# ---------------------------------------------------------------------------


class ResilienceError(Exception):
    """Base for every *real* failure this module raises.

    Always carries ``server`` (which connection) and ``detail`` (why, in
    human-readable prose) -- the two things spec.md US4 acceptance 1
    requires the surfaced report to name. ``cause`` keeps the original
    exception for a traceback/log, never for re-classification.
    """

    def __init__(self, *, server: str, detail: str, cause: BaseException | None = None) -> None:
        self.server = server
        self.detail = detail
        self.cause = cause
        super().__init__(f"[{server}] {detail}")


class TransportError(ResilienceError):
    """The connection to ``server`` was lost, refused, or never reachable.

    The assignment's "plugin stopped" injection lands here. Transient by
    nature -- :func:`resilient_call` retries this one with backoff and a
    reconnect attempt; :func:`call_or_degrade` degrades instead of raising
    once retries are exhausted.
    """


class AuthError(ResilienceError):
    """``server`` is reachable but rejected our credentials.

    The assignment's "invalid API key" injection lands here. Never
    transient -- retrying with the same bad key cannot succeed, so neither
    :func:`resilient_call` nor :func:`call_or_degrade` retries or degrades
    this one; it always propagates so the caller fixes configuration.
    """


class ToolCallError(ResilienceError):
    """A real failure from ``server`` that is neither transport nor auth.

    The catch-all for "something else went wrong" -- still a failure (still
    raised, still an instance of :class:`ResilienceError`), just not one of
    the two categories the assignment's triad names specifically.
    """


@dataclass(frozen=True, slots=True)
class EmptyResult:
    """A **successful** call whose answer is "nothing here" -- never raised.

    The assignment's "missing note" injection produces this, not an
    exception: the server was reached, answered correctly, and the correct
    answer was empty. ``payload`` keeps whatever the underlying tool
    returned so nothing is lost, only reclassified.
    """

    server: str
    tool: str
    reason: str
    payload: Any = None


def classify_exception(exc: BaseException, *, server: str) -> ResilienceError:
    """Map a raw exception from a tool/session call onto the taxonomy above.

    Checked in this order, structured signals before strings, so most real
    providers are classified without ever reading ``str(exc)``:

    1. An HTTP-style ``exc.response.status_code`` of 401/403 -> auth.
    2. A stdlib connection/timeout/OS-level exception -> transport (covers
       ``ConnectionError``, ``ConnectionRefusedError``, ``TimeoutError``,
       and the ``OSError`` family a killed stdio subprocess or a refused
       socket raises).
    3. A message containing one of a short list of well-known
       connection-loss phrases (``"connection closed"``, the exact wording
       the day-1 spike's bug reports use per research.md, plus
       ``"connection refused"`` / ``"session is closed"`` /
       ``"broken pipe"``) -> transport. This is the one deliberately-named
       string fallback, for MCP/adapter libraries that raise a plain
       ``RuntimeError``/``McpError`` with no structured status -- kept
       narrow and named here so it is auditable, not silent guessing.
    4. A message containing an auth-shaped phrase (``"unauthorized"``,
       ``"invalid api key"``, ``"forbidden"``, a bare ``"401"``) -> auth,
       same rationale as (3).
    5. Anything else -> :class:`ToolCallError` -- still a failure, just not
       one of the two named categories.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in (401, 403):
        return AuthError(server=server, detail=f"authentication rejected (HTTP {status})", cause=exc)

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return TransportError(
            server=server, detail=f"{type(exc).__name__}: {exc}", cause=exc
        )

    message = str(exc).lower()
    transport_phrases = (
        "connection closed",
        "connection refused",
        "session is closed",
        "broken pipe",
        "server disconnected",
    )
    if any(phrase in message for phrase in transport_phrases):
        return TransportError(server=server, detail=str(exc), cause=exc)

    auth_phrases = ("unauthorized", "invalid api key", "forbidden", "401")
    if any(phrase in message for phrase in auth_phrases):
        return AuthError(server=server, detail=str(exc), cause=exc)

    return ToolCallError(server=server, detail=str(exc), cause=exc)


# ---------------------------------------------------------------------------
# Retry / backoff / reconnect
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded backoff: ``attempts`` tries, sleeping
    ``base_delay * factor ** (n - 1)`` between them (n = 1-based attempt
    number of the failure just seen). ``attempts=3`` with the defaults
    below sleeps 0.25s then 0.5s before the third and final try.
    """

    attempts: int = 3
    base_delay: float = 0.25
    factor: float = 2.0


RetryHook = Callable[[int, ResilienceError], None]


async def resilient_call(
    *,
    server: str,
    tool: str,
    call: Callable[[], Awaitable[T]],
    is_empty_result: Callable[[T], EmptyResult | None] | None = None,
    reconnect: Callable[[], Awaitable[None]] | None = None,
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: RetryHook | None = None,
) -> T | EmptyResult:
    """Call ``call()`` once, or retry it, per the taxonomy above.

    - :class:`AuthError` never retries -- raised on the first occurrence.
    - :class:`TransportError` retries up to ``policy.attempts`` times,
      calling ``reconnect()`` (re-establishing the MCP session) between
      attempts when one is supplied, sleeping the backoff delay first. The
      final attempt's failure is re-raised, not swallowed.
    - :class:`ToolCallError` is treated the same as transport for retry
      purposes (some real failures *are* transient) but is a distinct type
      the caller can still tell apart from a connection problem.
    - If ``is_empty_result`` is given and it recognises the returned value
      as "found nothing" (e.g. ``{"found": False, ...}``), that value is
      returned wrapped as :class:`EmptyResult` -- this path never raises,
      by construction, which is what lets a caller distinguish "empty" from
      "failed" without inspecting message text.
    """
    last_error: ResilienceError | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            result = await call()
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 -- classified immediately below
            err = classify_exception(exc, server=server)
            if isinstance(err, AuthError):
                raise err from exc
            last_error = err
            if on_retry is not None:
                on_retry(attempt, err)
            if attempt >= policy.attempts:
                raise err from exc
            if reconnect is not None:
                await reconnect()
            await sleep(policy.base_delay * (policy.factor ** (attempt - 1)))
            continue

        if is_empty_result is not None:
            empty = is_empty_result(result)
            if empty is not None:
                return empty
        return result

    # Every branch above either returns or raises; this is unreachable but
    # keeps the function's control flow provably total for a type checker.
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# The degraded katagiri-only path
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Degraded:
    """A run that finished **without** ``server`` -- and says so.

    spec.md US4 acceptance 3: the degraded path must complete and *state*
    the degradation, never hide it. :meth:`message` is the human-readable
    line meant for the transcript/output, not a log a learner never sees.
    """

    server: str
    reason: str

    def message(self) -> str:
        return (
            f"DEGRADED: continuing katagiri-only -- {self.server} could not be "
            f"reached after retries ({self.reason}). This run completed without it."
        )


async def call_or_degrade(
    *,
    server: str,
    tool: str,
    call: Callable[[], Awaitable[T]],
    is_empty_result: Callable[[T], EmptyResult | None] | None = None,
    reconnect: Callable[[], Awaitable[None]] | None = None,
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: RetryHook | None = None,
) -> tuple[T | EmptyResult | None, Degraded | None]:
    """:func:`resilient_call`, but exhausted :class:`TransportError` retries
    degrade instead of raising.

    Returns ``(result, None)`` on success (including an :class:`EmptyResult`
    success) or ``(None, degraded)`` once ``server`` stays unreachable.
    :class:`AuthError` and :class:`ToolCallError` still propagate -- a bad
    key or an unrelated real failure is a configuration problem, not
    something a degraded path should silently paper over; only "the
    existing server would not come back" degrades, per spec.md US4's own
    framing ("existing-server loss ... degraded katagiri-only path").
    """
    try:
        result = await resilient_call(
            server=server,
            tool=tool,
            call=call,
            is_empty_result=is_empty_result,
            reconnect=reconnect,
            policy=policy,
            sleep=sleep,
            on_retry=on_retry,
        )
        return result, None
    except TransportError as err:
        return None, Degraded(server=server, reason=err.detail)


__all__ = [
    "AuthError",
    "Degraded",
    "EmptyResult",
    "ResilienceError",
    "RetryPolicy",
    "ToolCallError",
    "TransportError",
    "call_or_degrade",
    "classify_exception",
    "resilient_call",
]
