"""D3: the session tools — one prescribed action in, every write out as an event.

What this module is for
----------------------
Phase D turns Katagiri from a thing that answers questions into a thing that
teaches. The teaching loop is: open a session and be told **one** thing to do,
do it, and have what happened land in the log in a shape that can be counted
later. This module is that loop's write surface: :func:`start_session`,
:func:`log_lesson`, :func:`lessons`, :func:`log_observations`,
:func:`log_error`, :func:`add_vocab`, :func:`log_listening`,
:func:`triage_inbox`.

Three rules shape every function here.

**One action, never a menu.** :func:`start_session` returns a single ``action``
dict, chosen by a fixed ladder (see :func:`prescribe`). A dashboard would push
the decision back onto the learner at exactly the moment decision-making is
most expensive, and a menu of five equally-plausible options is a dashboard
with fewer rows. The ladder is mechanical and documented so the choice can be
argued with rather than merely disliked.

**Mandatory fields are refused, never defaulted.** :func:`log_observations`
writes the unassisted pass-rate series, which is the phase's outcome
instrument. An observation missing ``unassisted``, ``coverage_band`` or
``rubric_version`` is rejected and *nothing* in that call is written — not one
of the batch. A quietly defaulted ``rubric_version`` would not fail visibly; it
would corrupt every trend line drawn afterwards, silently, forever, because the
observation log is append-only and there is no correction path.

**Externally-sourced text arrives in an envelope.** Fields whose content
plausibly comes from outside Katagiri — a subtitle line, an inbox note copied
off a web page — are declared *untrusted-only*: they take a
:class:`katagiri.envelope.Envelope` and refuse a bare ``str``. Every envelope in
a call must carry an echo-back :class:`~katagiri.envelope.Confirmation` before
its text can be committed. Learner-authored fields (a topic, an objective, the
thing the learner said) are trusted and take plain strings — but they also
*accept* an envelope, and enforce the ceremony when they get one, so wrapping
external text is never the more expensive option for a caller.

Failures are values
-------------------
Every public function returns one dict shape whether it succeeded or refused:
``ok``, ``error`` (a stable code from the constants below, or ``None``),
``field`` (which argument was at fault, when that is meaningful), ``note``
(what to do about it), plus that function's own keys, present and empty on a
refusal. This is the shape :mod:`katagiri.obsidian_proxy` uses, and the codes
from :mod:`katagiri.envelope` pass through unchanged rather than being
re-labelled here. ``ValueError`` is reserved for the caller-domain mistakes the
rest of the codebase also raises on (a nonsense ``limit``, a ``today`` that is
not a date); a refusal is a thing the agent can fix and retry.

What this module deliberately does not do
-----------------------------------------
It does not write to the vault. The Obsidian bridge is GET-only in this build
(:mod:`katagiri.obsidian_proxy`) and the only files Katagiri authors live under
``<vault>/.derived/`` (:mod:`katagiri.today_export`). So a mined word becomes an
``item`` row and a ``mining`` event, and the vault-side rendering is the
exporters' job. ``triage_inbox`` reads nothing and moves nothing: it takes the
inbox note's text from the caller (who read it with the vault tools), proposes
filings, and on ``dry_run=False`` writes only the database side.

SECRETS: every payload written here lands in an append-only log that is backed
up and cannot be edited or deleted. Never pass credentials, tokens or file
contents from outside the vault through these fields; see
:mod:`katagiri.events`. Nothing in this module logs content — refusals and log
records carry codes, ids, counts and digest prefixes only.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final

from katagiri.db import resolve_alias
from katagiri.envelope import (
    Confirmation,
    EchoGate,
    Envelope,
    EnvelopeError,
    default_gate,
    is_enveloped,
    make_excerpt,
    wrap,
)
from katagiri.events import (
    STUDY_LOG_TYPE,
    append_event,
    new_ulid,
    normalize_stamp,
    utc_now_stamp,
)
from katagiri.logging_setup import get_logger
from katagiri.normalizer import is_han_char, is_kana_char

# katagiri.intelligence is deliberately *not* imported at module scope: it
# imports COVERAGE_BANDS back from this module, and a top-level import here
# would make the two modules a circular pair that fails on whichever one
# happens to load first. :func:`_curriculum_action` imports it locally instead
# — by the time any function runs, both modules have finished loading.

_log = get_logger("session_tools")

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
#
# ``event.type`` has no CHECK constraint on purpose (see docs/db-schema.md), so
# a new tool logs its own type without a migration. Declaring them here as
# constants is what keeps the vocabulary from being spelled three ways: the D6
# stop gate counts these strings, and a typo would silently stop counting.

SESSION_OPEN_EVENT: Final = "session_open"
LESSON_OPEN_EVENT: Final = "lesson_open"
#: Already part of the schema's documented vocabulary and of the stop gate's
#: artifact set, so a closed lesson counts as a study day without further work.
LESSON_CLOSE_EVENT: Final = "lesson_close"
OBSERVATION_EVENT: Final = "observation"
ERROR_EVENT: Final = "error_logged"
#: Also pre-existing vocabulary: mining is an artifact of study.
MINING_EVENT: Final = "mining"
TRIAGE_EVENT: Final = "inbox_triage"

EVENT_TYPES: Final[tuple[str, ...]] = (
    SESSION_OPEN_EVENT,
    LESSON_OPEN_EVENT,
    LESSON_CLOSE_EVENT,
    OBSERVATION_EVENT,
    ERROR_EVENT,
    MINING_EVENT,
    TRIAGE_EVENT,
)

# ---------------------------------------------------------------------------
# Enumerations the schema also constrains
# ---------------------------------------------------------------------------

#: Exactly the ``observation.coverage_band`` CHECK. Duplicated here so the
#: refusal names the allowed values before SQLite gets a chance to.
COVERAGE_BANDS: Final[tuple[str, ...]] = (">=95", "80-95", "<80")

#: How much an error cost, in the only three grades that change what happens
#: next: it is worth a note, worth a drill, or worth stopping the lesson for.
SEVERITIES: Final[tuple[str, ...]] = ("low", "medium", "high")

# ---------------------------------------------------------------------------
# Prescribed actions
# ---------------------------------------------------------------------------

ACTION_TIRED_MODE: Final = "tired_mode_minimum"
ACTION_NEXT_STEP: Final = "continue_next_step"
ACTION_REVISIT_TOPIC: Final = "revisit_topic"
ACTION_RESOLVE_THREAD: Final = "resolve_thread"
ACTION_CURRICULUM_TOPIC: Final = "curriculum_topic"
ACTION_OPEN_FIRST_LESSON: Final = "open_first_lesson"

ACTION_KINDS: Final[tuple[str, ...]] = (
    ACTION_TIRED_MODE,
    ACTION_NEXT_STEP,
    ACTION_REVISIT_TOPIC,
    ACTION_RESOLVE_THREAD,
    ACTION_CURRICULUM_TOPIC,
    ACTION_OPEN_FIRST_LESSON,
)

TIRED_MODE_INSTRUCTION: Final = (
    "Tired mode: clear your due reviews, then mine exactly one word. Stop "
    "there."
)
TIRED_MODE_RATIONALE: Final = (
    "You declared a tired session. The minimum session is reviews plus one "
    "mined word — it still counts as a study day, and a streak kept small is "
    "a streak kept."
)
OPEN_FIRST_LESSON_INSTRUCTION: Final = (
    "Open a lesson: name one observable can-do objective, teach to it, then "
    "close it with log_lesson (next_step included)."
)
OPEN_FIRST_LESSON_RATIONALE: Final = (
    "No lesson in the log carries a next step, no topic is due for revisit, "
    "and no thread is left open — so the one useful action is to define the "
    "next lesson rather than to pick from a list."
)

#: How far back :func:`prescribe` looks for an unconsumed ``next_step``. A next
#: step from six lessons ago is not continuity, it is archaeology.
NEXT_STEP_LOOKBACK: Final = 5

#: Carried on every :func:`study_plan` answer, so a caller reading the outlook
#: never mistakes it for a second way to choose an action.
STUDY_PLAN_NOTE: Final = (
    "This is an informational outlook, not a prescription: start_session "
    "remains the single prescriber, and its answer is always one action, "
    "never a menu."
)

# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

#: ``lesson.free_notes`` has a ``length <= 500`` CHECK. Refusing here names the
#: field; letting SQLite refuse raises an IntegrityError from three frames down.
MAX_FREE_NOTES_CHARS: Final = 500
#: Short structured fields (topic, objective, a said/correct pair, a headword).
#: Not a schema constraint — a cap that keeps prose out of columns meant for
#: labels, so the observation log stays the place where detail lives.
MAX_TEXT_CHARS: Final = 2_000
MAX_UNRESOLVED_PER_CALL: Final = 20
MAX_OBSERVATIONS_PER_CALL: Final = 100
#: Inbox notes are one-line dumps by design (docs/.../00-inbox/README.md). A
#: note past this is not an inbox, and triaging half of it would be worse than
#: refusing.
MAX_INBOX_LINES: Final = 200
DEFAULT_LESSON_LIMIT: Final = 20
#: Staged envelopes held for the MCP adapter seam. Small on purpose: this is a
#: hand-off buffer for one conversation, not a content store.
MAX_STAGED: Final = 64

# ---------------------------------------------------------------------------
# Dose caps (FR-015) — how much of a session the learner is allowed to spend,
# as opposed to the "Caps" section above, which bounds how big one field or
# one call is allowed to be. Sourced from research.md, "Post-gate": "Caps:
# 20-30 min core/day, <=8 new words/day, <=2 new grammar/week, review queue
# hard-capped with deferral." Overflow past any of these is a DEFERRAL (the
# excess waits for tomorrow/next week), never a longer session.
# ---------------------------------------------------------------------------

#: research.md, "Post-gate": "<=8 new words/day". Counted from today's mining
#: events — mining is how a new word enters the log (see :func:`add_vocab`).
MAX_NEW_WORDS_PER_DAY: Final = 8
#: research.md, "Post-gate": "<=2 new grammar/week". Counted from lessons
#: whose topic is a grammar item and whose *first* lesson opened this week —
#: a later lesson revisiting the same topic does not spend the budget again.
MAX_NEW_GRAMMAR_PER_WEEK: Final = 2
#: A rolling 7-day window ending today, matching the precedent in
#: stop_gate.py (``STOP_GATE_WINDOW_DAYS``) of counting backwards from today
#: rather than snapping to a calendar week.
GRAMMAR_WEEK_WINDOW_DAYS: Final = 7
#: research.md, "Post-gate", A0 strand example: "10 replays of one 40-second
#: Irodori dialogue" — narrow-listening reps, not raw minutes. The event that
#: counts against this (a ``study_session`` payload's ``listening_reps``) is
#: written by a later lane (specs/006-teaching-method tasks T018/T019); until
#: then no session has logged any, so the full target is always left.
LISTENING_REPS_DAILY_TARGET: Final = 10

# ---------------------------------------------------------------------------
# Refusal codes (stable; adapters and tests may compare against these)
# ---------------------------------------------------------------------------

MISSING_FIELD: Final = "missing_field"
INVALID_FIELD: Final = "invalid_field"
FIELD_TOO_LONG: Final = "field_too_long"
ENVELOPE_REQUIRED: Final = "envelope_required"
CONFIRMATION_REQUIRED: Final = "confirmation_required"
UNKNOWN_STAGED_CONTENT: Final = "unknown_staged_content"
MISSING_SESSION_ID: Final = "missing_session_id"
UNKNOWN_LESSON: Final = "unknown_lesson"
NEXT_STEP_BEFORE_CLOSE: Final = "next_step_before_close"
INVALID_REVISIT_AFTER: Final = "invalid_revisit_after"
INVALID_TIMESTAMP: Final = "invalid_timestamp"
#: A close whose stamp precedes the ``opened_ts`` the lesson row already holds.
#: Distinct from :data:`INVALID_TIMESTAMP`, which is about a stamp the *caller*
#: supplied: here the caller passed nothing wrong and the clash is with stored
#: state, so the refusal has to name a different thing to fix.
CLOSE_BEFORE_OPEN: Final = "close_before_open"
TOO_MANY_UNRESOLVED: Final = "too_many_unresolved"
NO_OBSERVATIONS: Final = "no_observations"
TOO_MANY_OBSERVATIONS: Final = "too_many_observations"
OBSERVATIONS_REJECTED: Final = "observations_rejected"
MISSING_TASK_TYPE: Final = "missing_task_type"
MISSING_UNASSISTED: Final = "missing_unassisted"
INVALID_UNASSISTED: Final = "invalid_unassisted"
MISSING_COVERAGE_BAND: Final = "missing_coverage_band"
INVALID_COVERAGE_BAND: Final = "invalid_coverage_band"
MISSING_RUBRIC_VERSION: Final = "missing_rubric_version"
INVALID_SEVERITY: Final = "invalid_severity"
INVALID_PITCH: Final = "invalid_pitch"
#: :func:`log_listening`'s ``reps`` refusal: not a positive int. Reps are never
#: zero-filled to look measured, so a bad value is refused rather than clamped.
INVALID_REPS: Final = "invalid_reps"
INBOX_TOO_LARGE: Final = "inbox_too_large"
NOTHING_TO_TRIAGE: Final = "nothing_to_triage"
#: FR-015's daily new-word dose cap (:data:`MAX_NEW_WORDS_PER_DAY`), reached.
NEW_WORD_CAP_REACHED: Final = "new_word_cap_reached"

_STAMP_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DAY_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Failures, as values
# ---------------------------------------------------------------------------


class SessionToolError(Exception):
    """A write that did not happen, with a stable code and a fixable note.

    Raised internally and converted to a refusal dict at each public function's
    boundary, so callers never have to choose between two error styles. No
    subclass interpolates content — only field names, codes and counts.
    """

    code: str = INVALID_FIELD
    note: str = ""

    def __init__(
        self,
        note: str | None = None,
        *,
        code: str | None = None,
        field: str | None = None,
    ) -> None:
        self.field = field
        if code is not None:
            self.code = code
        if note is not None:
            self.note = note
        super().__init__(self.note or self.code)


class MissingRequiredField(SessionToolError):
    code = MISSING_FIELD

    def __init__(self, field: str) -> None:
        super().__init__(
            f"{field} is required and was empty. Nothing was written.",
            field=field,
        )


class InvalidFieldValue(SessionToolError):
    code = INVALID_FIELD


class FieldTooLong(SessionToolError):
    code = FIELD_TOO_LONG

    def __init__(self, field: str, limit: int) -> None:
        super().__init__(
            f"{field} exceeds {limit} characters. Structured fields hold "
            "labels; detail belongs in an observation.",
            field=field,
        )


class EnvelopeRequired(SessionToolError):
    code = ENVELOPE_REQUIRED

    def __init__(self, field: str) -> None:
        super().__init__(
            f"{field} carries externally-sourced text, so it takes an "
            "Envelope, not a string. Stage the content (stage_untrusted), echo "
            "it back (confirm_untrusted), then write it.",
            field=field,
        )


class ConfirmationRequired(SessionToolError):
    code = CONFIRMATION_REQUIRED

    def __init__(self, field: str) -> None:
        super().__init__(
            f"The envelope in {field} has no echo-back confirmation on this "
            "gate. Confirm it first; enveloped content is never written "
            "unconfirmed.",
            field=field,
        )


class UnknownStagedContent(SessionToolError):
    code = UNKNOWN_STAGED_CONTENT
    note = (
        "No staged envelope with that id. Stage the content again — the "
        "hand-off buffer holds only the current conversation's envelopes."
    )


class UnknownLesson(SessionToolError):
    code = UNKNOWN_LESSON

    def __init__(self, lesson_id: str) -> None:
        super().__init__(
            f"No lesson with id {lesson_id!r}. Omit lesson_id to record a new "
            "lesson instead of updating one.",
            field="lesson_id",
        )


class ObservationsRejected(SessionToolError):
    """One or more observations failed the mandatory-field check.

    Carries every rejection, not just the first: an agent fixing a batch should
    learn all of what is wrong with it in one round trip.
    """

    code = OBSERVATIONS_REJECTED
    note = (
        "No observation was written. Every observation needs task_type, "
        "unassisted, coverage_band and rubric_version; these are never "
        "defaulted, because a guessed rubric_version corrupts the pass-rate "
        "series permanently."
    )

    def __init__(self, rejected: list[dict[str, Any]]) -> None:
        self.rejected = rejected
        super().__init__()


def _base(fields: Mapping[str, Any]) -> dict[str, Any]:
    """The common head of every answer, success or refusal."""
    return {"ok": True, "error": None, "field": None, "note": "", **fields}


def _refused(answer: Mapping[str, Any], exc: Exception) -> dict[str, Any]:
    """Turn a raised failure into this module's one answer shape."""
    code = getattr(exc, "code", INVALID_FIELD)
    field = getattr(exc, "field", None)
    note = getattr(exc, "note", "") or str(exc)
    _log.warning("session tool refused: code=%s field=%s", code, field)
    refusal = {**answer, "ok": False, "error": code, "field": field, "note": note}
    if isinstance(exc, ObservationsRejected):
        refusal["rejected"] = exc.rejected
    return refusal


# ---------------------------------------------------------------------------
# The trust boundary: which fields may be a bare string
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Text:
    """Resolved text plus, when it came from an envelope, its provenance.

    ``provenance`` is the envelope's text-free record
    (:meth:`katagiri.envelope.Envelope.for_event`), which is what goes in the
    event payload beside the content: a later reader needs to know *which*
    outside text was written and where it came from.
    """

    value: str | None
    provenance: dict[str, Any] | None = None


def _confirmation_for(
    field: str,
    content: Envelope,
    confirmations: Mapping[str, Confirmation] | None,
    gate: EchoGate | None,
) -> tuple[Confirmation, EchoGate]:
    """The confirmation authorising ``content``, and the gate that issued it.

    A confirmation passed in the call wins; otherwise the staging buffer is
    consulted, which is what lets an MCP adapter run the ceremony across three
    tool calls without holding Python objects between them. The gate matters:
    a confirmation is only recognised by the gate that issued it, so a staged
    confirmation is spent against the gate it was made on.
    """
    supplied = None if confirmations is None else confirmations.get(content.envelope_id)
    if supplied is not None:
        return supplied, (gate if gate is not None else default_gate())

    staged = _staged.get(content.envelope_id)
    if staged is not None and staged.confirmation is not None:
        return staged.confirmation, staged.gate

    raise ConfirmationRequired(field)


def _resolve_text(
    field: str,
    value: str | Envelope | None,
    *,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    required: bool = False,
    untrusted_only: bool = False,
    max_chars: int = MAX_TEXT_CHARS,
) -> _Text:
    """Resolve one text field to the string that will be written, or refuse.

    ``untrusted_only`` marks a field whose content comes from outside Katagiri:
    it takes an :class:`~katagiri.envelope.Envelope` and refuses a ``str``,
    because a caller that can pass media text as a plain string has routed
    around the whole protocol. Every other field accepts a plain string —
    learner-authored text is trusted — *and* accepts an envelope, in which case
    the echo-back ceremony is enforced and the unwrapped text is written
    verbatim (never stripped or reflowed: the digest pinned exactly those
    bytes).
    """
    if value is None:
        if required:
            raise MissingRequiredField(field)
        return _Text(None)

    if is_enveloped(value):
        confirmation, active_gate = _confirmation_for(field, value, confirmations, gate)
        text = active_gate.unwrap_for_write(value, confirmation)
        if len(text) > max_chars:
            raise FieldTooLong(field, max_chars)
        if required and not text.strip():
            raise MissingRequiredField(field)
        return _Text(text, value.for_event())

    if not isinstance(value, str):
        raise InvalidFieldValue(
            f"{field} must be text or an Envelope; got {type(value).__name__}.",
            field=field,
        )
    if untrusted_only:
        raise EnvelopeRequired(field)

    text = value.strip()
    if not text:
        if required:
            raise MissingRequiredField(field)
        return _Text(None)
    if len(text) > max_chars:
        raise FieldTooLong(field, max_chars)
    return _Text(text)


def _precheck_text(
    field: str,
    value: str | Envelope | None,
    *,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    required: bool = False,
    untrusted_only: bool = False,
    max_chars: int = MAX_TEXT_CHARS,
) -> None:
    """Every refusal :func:`_resolve_text` can raise *without* spending anything.

    Same arguments, same exceptions, no unwrap: the trust-boundary rules, the
    caps, and the envelope's own integrity are all checkable before a
    confirmation is spent. What is left to
    :meth:`~katagiri.envelope.EchoGate.unwrap_for_write` is only the ledger —
    whether *this* gate confirmed *this* envelope and has not already spent it —
    and a confirmation that fails those checks was never spendable anyway.

    This exists for the multi-record case (:func:`log_observations`): record 3's
    bad ``coverage_band`` must not cost record 1 the confirmation its retry
    needs. It reads ``value.text`` only to measure it; the content is not
    returned and nothing here writes.
    """
    if value is None:
        if required:
            raise MissingRequiredField(field)
        return

    if is_enveloped(value):
        _confirmation_for(field, value, confirmations, gate)
        value.verify_integrity()
        if len(value.text) > max_chars:
            raise FieldTooLong(field, max_chars)
        if required and not value.text.strip():
            raise MissingRequiredField(field)
        return

    if not isinstance(value, str):
        raise InvalidFieldValue(
            f"{field} must be text or an Envelope; got {type(value).__name__}.",
            field=field,
        )
    if untrusted_only:
        raise EnvelopeRequired(field)

    text = value.strip()
    if not text:
        if required:
            raise MissingRequiredField(field)
        return
    if len(text) > max_chars:
        raise FieldTooLong(field, max_chars)


def _merge_provenance(
    target: dict[str, dict[str, Any]], field: str, resolved: _Text
) -> None:
    if resolved.provenance is not None:
        target[field] = resolved.provenance


def _untrusted_note(provenance: Mapping[str, Any]) -> str:
    if not provenance:
        return ""
    fields = ", ".join(sorted(provenance))
    return (
        f"Externally-sourced content was written into: {fields}. Its "
        "provenance and digest are recorded in the event payload; the log is "
        "append-only."
    )


# ---------------------------------------------------------------------------
# The echo-back ceremony, addressable by id (the MCP adapter seam)
# ---------------------------------------------------------------------------
#
# An MCP tool call cannot hand a Python object to the next one, and the
# ceremony is three moves: wrap, echo, write. Something has to hold the
# envelope in between, and the honest place is here — beside the write tools
# that spend the confirmation — rather than inside a registration adapter,
# which is supposed to stay thin.


@dataclass(frozen=True, slots=True)
class _Staged:
    envelope: Envelope
    challenge_id: str
    gate: EchoGate
    confirmation: Confirmation | None = None


_staged: dict[str, _Staged] = {}


def _remember(entry: _Staged) -> None:
    _staged[entry.envelope.envelope_id] = entry
    while len(_staged) > MAX_STAGED:
        # Dicts keep insertion order, so this drops the oldest hand-off. An
        # evicted envelope is not a lost write: staging it again is one call.
        del _staged[next(iter(_staged))]


def stage_untrusted(
    text: str,
    *,
    source: str,
    locator: str = "",
    retrieved_ts: str = "",
    detail: Mapping[str, Any] | None = None,
    gate: EchoGate | None = None,
) -> dict[str, Any]:
    """Wrap externally-sourced ``text`` and issue its echo-back challenge.

    Returns the ids the following two calls need plus a display excerpt — never
    the content, which is the point of the envelope. ``source`` must be one of
    :data:`katagiri.envelope.SOURCES`; an unknown one is a ``ValueError``,
    because a provenance nobody chose is exactly the record that cannot be
    trusted later.
    """
    answer = _base(
        {
            "envelope_id": None,
            "challenge_id": None,
            "source": source,
            "locator": locator,
            "chars": 0,
            "excerpt": "",
            "digest_prefix": "",
            "prompt": "",
            "expires_ms": None,
        }
    )
    active_gate = gate if gate is not None else default_gate()
    try:
        content = wrap(
            text,
            source=source,
            locator=locator,
            retrieved_ts=retrieved_ts,
            detail=detail,
        )
        challenge = active_gate.challenge(content)
    except EnvelopeError as exc:
        return _refused(answer, exc)

    _remember(
        _Staged(
            envelope=content,
            challenge_id=challenge.challenge_id,
            gate=active_gate,
        )
    )
    return {
        **answer,
        "envelope_id": content.envelope_id,
        "challenge_id": challenge.challenge_id,
        "chars": challenge.chars,
        "excerpt": challenge.excerpt,
        "digest_prefix": content.digest[:12],
        "prompt": challenge.prompt,
        "expires_ms": challenge.expires_ms,
        "note": content.note,
    }


def confirm_untrusted(
    challenge_id: str, echo: str | None, *, gate: EchoGate | None = None
) -> dict[str, Any]:
    """Answer a challenge by restating the content, and hold the confirmation.

    The digest is recomputed from ``echo`` against the challenge's own
    provenance, so this cannot be satisfied by echoing the challenge id back.
    On success the confirmation is remembered beside its envelope, which is what
    lets a later write call name only the envelope.
    """
    answer = _base(
        {
            "envelope_id": None,
            "challenge_id": challenge_id,
            "confirmed_ms": None,
        }
    )
    entry = next(
        (item for item in _staged.values() if item.challenge_id == challenge_id),
        None,
    )
    if gate is not None:
        active_gate = gate
    else:
        active_gate = entry.gate if entry is not None else default_gate()
    try:
        confirmation = active_gate.confirm(challenge_id, echo)
    except EnvelopeError as exc:
        return _refused(answer, exc)

    if entry is not None:
        _remember(
            _Staged(
                envelope=entry.envelope,
                challenge_id=entry.challenge_id,
                gate=active_gate,
                confirmation=confirmation,
            )
        )
    return {
        **answer,
        "envelope_id": confirmation.envelope_id,
        "confirmed_ms": confirmation.confirmed_ms,
    }


def staged_envelope(envelope_id: str) -> Envelope:
    """The staged envelope with this id, for handing to a write tool.

    Raises :class:`UnknownStagedContent` — an id the buffer never held (or has
    since evicted) is not a refusable write, it is a lost hand-off, and the
    caller must stage the content again.
    """
    entry = _staged.get(envelope_id)
    if entry is None:
        raise UnknownStagedContent(field="envelope_id")
    return entry.envelope


def reset_staged() -> None:
    """Forget every staged envelope and confirmation. Tests, and session end."""
    _staged.clear()


# ---------------------------------------------------------------------------
# Small shared validators
# ---------------------------------------------------------------------------


def new_session_id() -> str:
    """A fresh session id: ULID-suffixed, so it sorts by when it was opened."""
    return f"sess-{new_ulid()}"


def _require_session_id(session_id: str | None, field: str = "session_id") -> str:
    if session_id is None or not str(session_id).strip():
        raise SessionToolError(
            "session_id is required: an observation with no session cannot be "
            "joined to the lesson it happened in. Pass the id start_session "
            "returned.",
            code=MISSING_SESSION_ID,
            field=field,
        )
    return str(session_id).strip()


def _session_or_synthetic(session_id: str | None, kind: str, ts: str) -> str:
    """``session_id``, or a synthetic one naming the tool and the second.

    ``event.session_id`` is NOT NULL, and a word mined outside any session is
    still a real event. ``mark:<ts>`` is the existing spelling of this idea in
    :func:`katagiri.events.mark_item`; this follows it.
    """
    if session_id is not None and str(session_id).strip():
        return str(session_id).strip()
    return f"{kind}:{ts}"


def _check_stamp(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not _STAMP_RE.match(text):
        raise SessionToolError(
            f"{field} must be an exact YYYY-MM-DDTHH:MM:SSZ timestamp (the "
            "schema enforces the width, because these columns are compared "
            "lexicographically).",
            code=INVALID_TIMESTAMP,
            field=field,
        )
    return text


def _is_day_key(value: str) -> bool:
    if not _DAY_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _resolve_revisit_after(value: str | int | None, *, today: date) -> str | None:
    """``revisit_after`` as a day key: a date, or a number of days from today.

    Topic spacing is the one place Katagiri schedules anything, and "in ten
    days" is how a teacher actually thinks about it — so an int is accepted and
    resolved here rather than making every caller do calendar arithmetic.
    """
    bad = SessionToolError(
        "revisit_after must be a local YYYY-MM-DD day key or a non-negative "
        "number of days from today.",
        code=INVALID_REVISIT_AFTER,
        field="revisit_after",
    )
    if value is None:
        return None
    if isinstance(value, bool):
        raise bad
    if isinstance(value, int):
        if value < 0:
            raise bad
        return (today + timedelta(days=value)).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if not _is_day_key(text):
            raise bad
        return text
    raise bad


def _today(value: str | None) -> date:
    """``today`` as a date. A malformed one raises, matching the stop gate."""
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"today must be a YYYY-MM-DD date; got {value!r}.") from None


# ---------------------------------------------------------------------------
# start_session: exactly one prescribed action
# ---------------------------------------------------------------------------


def _action(
    kind: str,
    instruction: str,
    rationale: str,
    *,
    topic: str | None = None,
    lesson_id: str | None = None,
    unresolved_id: int | None = None,
    revisit_after: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """One prescribed action, always the same keys so callers never branch."""
    return {
        "kind": kind,
        "instruction": instruction,
        "rationale": rationale,
        "topic": topic,
        "lesson_id": lesson_id,
        "unresolved_id": unresolved_id,
        "revisit_after": revisit_after,
        "source": source,
    }


def _next_step_action(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The most recent closed lesson's next step, if it has not been prescribed.

    A next step is prescribed **once**. Re-prescribing it every session until
    the learner happens to do it would stall the loop at the first thing they
    avoided; letting it fall through means the log still holds it (``lessons``
    shows it) while the session gets a live action.
    """
    rows = conn.execute(
        """
        SELECT id, topic, objective, next_step, closed_ts
          FROM lesson
         WHERE closed_ts IS NOT NULL
           AND next_step IS NOT NULL
           AND trim(next_step) <> ''
         ORDER BY closed_ts DESC, id DESC
         LIMIT ?
        """,
        (NEXT_STEP_LOOKBACK,),
    ).fetchall()

    for row in rows:
        prescribed = conn.execute(
            """
            SELECT 1 FROM event
             WHERE type = ?
               AND json_extract(payload, '$.action.kind') = ?
               AND json_extract(payload, '$.action.lesson_id') = ?
             LIMIT 1
            """,
            (SESSION_OPEN_EVENT, ACTION_NEXT_STEP, row["id"]),
        ).fetchone()
        if prescribed is not None:
            continue
        return _action(
            ACTION_NEXT_STEP,
            str(row["next_step"]),
            (
                f"The lesson on {row['topic']!r} closed at {row['closed_ts']} "
                "with this next step. Continuity beats novelty."
            ),
            topic=str(row["topic"]),
            lesson_id=str(row["id"]),
            source="lesson.next_step",
        )
    return None


def _revisit_action(conn: sqlite3.Connection, today: date) -> dict[str, Any] | None:
    """The most overdue topic whose revisit date has arrived.

    A topic is no longer due once *another* lesson on it was opened after the
    revisit date: that lesson **is** the revisit, so nothing has to mark it
    done. The scheduling lesson itself is excluded — otherwise a lesson that
    backdated its own revisit date would cancel it on the spot. The comparison
    is lexicographic, which is exact because both columns are fixed-width.
    """
    row = conn.execute(
        """
        SELECT l.id, l.topic, l.objective, l.revisit_after
          FROM lesson l
         WHERE l.revisit_after IS NOT NULL
           AND l.revisit_after <= ?
           AND NOT EXISTS (
               SELECT 1 FROM lesson newer
                WHERE newer.topic = l.topic
                  AND newer.id <> l.id
                  AND newer.opened_ts > l.revisit_after || 'T00:00:00Z'
           )
         ORDER BY l.revisit_after ASC, l.id ASC
         LIMIT 1
        """,
        (today.isoformat(),),
    ).fetchone()
    if row is None:
        return None
    return _action(
        ACTION_REVISIT_TOPIC,
        (
            f"Revisit {row['topic']}: re-test the objective "
            f"({row['objective']}) cold, then log what you observe."
        ),
        (
            f"{row['topic']} was scheduled for revisit on "
            f"{row['revisit_after']} and no lesson has touched it since. "
            "Katagiri schedules topics; Anki schedules items."
        ),
        topic=str(row["topic"]),
        lesson_id=str(row["id"]),
        revisit_after=str(row["revisit_after"]),
        source="lesson.revisit_after",
    )


def _unresolved_action(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The oldest question that was served in a lesson and never answered."""
    row = conn.execute(
        """
        SELECT u.id AS unresolved_id, u.text AS text, u.created_ts AS created_ts,
               l.id AS lesson_id, l.topic AS topic
          FROM lesson_unresolved u
          JOIN lesson l ON l.id = u.lesson_id
         WHERE u.resolved_ts IS NULL
         ORDER BY u.created_ts ASC, u.id ASC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return _action(
        ACTION_RESOLVE_THREAD,
        f"Answer the open thread from {row['topic']}: {row['text']}",
        (
            f"It was served on {row['created_ts']} and is still open. An "
            "unanswered question compounds; a resolved one becomes a lesson."
        ),
        topic=str(row["topic"]),
        lesson_id=str(row["lesson_id"]),
        unresolved_id=int(row["unresolved_id"]),
        source="lesson_unresolved",
    )


def _curriculum_action(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The most foundational reachable grammar point that has no lesson yet.

    Reachability is :func:`katagiri.intelligence.grammar_reachability`'s verdict,
    unchanged: a ``prereq``-only walk, mastery via the known set or
    ``item.understanding``, sealed items never offered (intelligence.py, "The
    i+1 gate"). A mastered point is not a topic to open a lesson for; a point
    with an unmastered prerequisite is not yet reachable, however useful it
    would eventually be. Among what is left, the point with the smallest prereq
    closure is offered first — the curriculum's own idea of "next", not a guess
    made here.

    A grammar point that already names some lesson's topic is not offered
    again: it is either still being tracked by the next-step/revisit/unresolved
    rungs above, or it was resolved by them, and re-suggesting it here would
    just repeat one of those under a different name. Returns ``None`` when the
    curriculum has not been imported (no ``item_edge`` rows at all), when the
    stored graph has a cycle and so cannot answer reachability, or when nothing
    reachable and untaught remains — the fallback rung covers all three.
    """
    from katagiri.intelligence import (
        GRAMMAR_KIND,
        grammar_reachability,
        load_grammar_dag,
    )

    dag = load_grammar_dag(conn)
    if dag.cycle is not None or (not dag.prereqs and not dag.unlocked_by):
        return None
    rows = conn.execute(
        "SELECT id FROM item WHERE kind = ? AND sealed = 0 ORDER BY id ASC",
        (GRAMMAR_KIND,),
    ).fetchall()
    if not rows:
        return None
    grammar_ids = [str(row["id"]) for row in rows]
    taught = {
        str(row["topic"])
        for row in conn.execute("SELECT DISTINCT topic FROM lesson").fetchall()
    }
    verdicts = grammar_reachability(conn, grammar_ids, dag=dag)
    candidates = [
        verdict
        for grammar_id, verdict in verdicts.items()
        if not verdict["mastered"]
        and verdict["reachable"]
        and grammar_id not in taught
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda verdict: (verdict["closure_size"], verdict["id"]))
    topic = candidates[0]["id"]
    return _action(
        ACTION_CURRICULUM_TOPIC,
        (
            f"Open a lesson on {topic}: name its can-do objective, teach to "
            "it, then close it with log_lesson (next_step included)."
        ),
        (
            f"{topic} is reachable — every prerequisite in its closure is "
            "mastered — and no lesson has touched it yet. It is the "
            "curriculum's next point, not a pick from a list."
        ),
        topic=topic,
        source="curriculum_reachability",
    )


def _caps_block(conn: sqlite3.Connection, today: date) -> dict[str, int]:
    """How much dose budget is left today/this week (FR-015). Reads only.

    Additive on every action payload — the caller decides what to prescribe;
    this decides how much of it still fits. Overflow is reported here as a
    smaller number, never enforced by shortening what was already prescribed:
    the actual refusal (past zero) lives at the tool that would spend the
    budget, e.g. :func:`add_vocab`.
    """
    day_key = today.isoformat()
    mined_today = conn.execute(
        "SELECT COUNT(*) FROM event WHERE type = ? AND day_key = ?",
        (MINING_EVENT, day_key),
    ).fetchone()[0]
    new_words_left = max(0, MAX_NEW_WORDS_PER_DAY - int(mined_today))

    window_start = (
        today - timedelta(days=GRAMMAR_WEEK_WINDOW_DAYS - 1)
    ).isoformat()
    grammar_introduced = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT l.topic, MIN(l.opened_ts) AS first_opened
              FROM lesson l
              JOIN item i ON i.id = l.topic
             WHERE i.kind = 'grammar'
             GROUP BY l.topic
            HAVING substr(first_opened, 1, 10) >= ?
        )
        """,
        (window_start,),
    ).fetchone()[0]
    grammar_left = max(0, MAX_NEW_GRAMMAR_PER_WEEK - int(grammar_introduced))

    listening_reps_done = conn.execute(
        """
        SELECT COALESCE(SUM(json_extract(payload, '$.listening_reps')), 0)
          FROM event
         WHERE type = ?
           AND day_key = ?
        """,
        (STUDY_LOG_TYPE, day_key),
    ).fetchone()[0]
    listening_reps_left = max(
        0, LISTENING_REPS_DAILY_TARGET - int(listening_reps_done)
    )

    return {
        "new_words_left": new_words_left,
        "grammar_left": grammar_left,
        "listening_reps_left": listening_reps_left,
    }


def prescribe(
    conn: sqlite3.Connection, *, tired: bool = False, today: str | None = None
) -> dict[str, Any]:
    """Choose the one action this session should perform. Reads only.

    The ladder, first match wins:

    1. **tired mode**, when declared — reviews plus one mined word, nothing else.
    2. the newest closed lesson's unconsumed **next_step** (FR-006: written at
       close, read at open),
    3. the most overdue **topic revisit**,
    4. the oldest open **unresolved thread**,
    5. the most foundational reachable, untaught **curriculum topic** (FR-014:
       read from curriculum reachability),
    6. otherwise: **open a lesson** and define the objective.

    Order is deliberate. A declared tired session overrides everything because
    the alternative is no session at all. Next step outranks a due revisit
    because it is the more specific instruction — the learner already decided
    what should happen next, while the revisit date was arithmetic. Both outrank
    an open thread, which has no date and so is never late. The curriculum rung
    outranks the fallback because it names a specific, reachable point instead
    of leaving the objective undefined — but it ranks below the first three
    because those are about something already started, and finishing what was
    started beats starting something new. The fallback names the one thing
    that is always available, so the answer is never a menu and never empty.

    Every action, whichever rung produced it, carries an additive ``caps`` key
    (FR-015: ``new_words_left``, ``grammar_left``, ``listening_reps_left`` —
    see :func:`_caps_block`) reporting how much of today's/this week's dose
    budget is left. It never changes *which* action is chosen.

    Exposed separately from :func:`start_session` so the choice can be inspected
    (and tested) without appending an event.
    """
    day = _today(today)
    if tired:
        action = _action(
            ACTION_TIRED_MODE,
            TIRED_MODE_INSTRUCTION,
            TIRED_MODE_RATIONALE,
            source="tired_mode",
        )
    else:
        action = None
        for candidate in (
            _next_step_action(conn),
            _revisit_action(conn, day),
            _unresolved_action(conn),
            _curriculum_action(conn),
        ):
            if candidate is not None:
                action = candidate
                break
        if action is None:
            action = _action(
                ACTION_OPEN_FIRST_LESSON,
                OPEN_FIRST_LESSON_INSTRUCTION,
                OPEN_FIRST_LESSON_RATIONALE,
                source="empty_log",
            )
    action["caps"] = _caps_block(conn, day)
    return action


def start_session(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    tired: bool = False,
    today: str | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Open a study session and return **exactly one** prescribed action.

    The result's ``action`` is a single dict — never a list, never a ranked set
    of options with the top one highlighted, which is a menu wearing a hat. Its
    ``rationale`` says why this and not something else, so the learner can
    disagree with the reasoning rather than with an opaque verdict.

    Appends one ``session_open`` event carrying the action, which is also how
    the next session knows a ``next_step`` has already been prescribed once.
    """
    answer = _base(
        {
            "session_id": None,
            "opened_ts": None,
            "event_id": None,
            "tired_mode": bool(tired),
            "action": None,
        }
    )
    try:
        action = prescribe(conn, tired=tired, today=today)
        identifier = (
            str(session_id).strip()
            if session_id is not None and str(session_id).strip()
            else new_session_id()
        )
    except SessionToolError as exc:
        return _refused(answer, exc)

    opened_ts = utc_now_stamp()
    event_id = append_event(
        conn,
        type=SESSION_OPEN_EVENT,
        session_id=identifier,
        ts_device=opened_ts,
        tz=tz,
        payload={"action": action, "tired_mode": bool(tired)},
    )
    _log.info(
        "session opened: session=%s action=%s lesson=%s",
        identifier,
        action["kind"],
        action["lesson_id"],
    )
    return {
        **answer,
        "session_id": identifier,
        "opened_ts": opened_ts,
        "event_id": event_id,
        "action": action,
    }


def study_plan(
    conn: sqlite3.Connection,
    *,
    include_mastered: bool = True,
    today: str | None = None,
) -> dict[str, Any]:
    """The curriculum's reachability outlook, for browsing — never for picking.

    Every grammar-kind ``item`` row gets one entry, each carrying the same
    verdict :func:`katagiri.intelligence.grammar_reachability` already computes
    for the curriculum rung (:func:`_curriculum_action`), plus its stored T028
    tag dict (``jf_can_do``/``irodori_lesson``/``tae_kim_section``, D-39).
    Nothing here is picked or prescribed: :func:`start_session` remains the
    single source of "what to do now", and this function answers a different
    question — "what does the whole map look like" — for a learner who wants
    to see the shape of what is ahead without that becoming a menu to choose
    from.

    Ordering surfaces what is actionable first: reachable-and-unmastered
    topics, ordered by ascending prereq closure size (the curriculum's own
    idea of "next", same tie-break as the curriculum rung), then blocked
    topics ordered by ascending missing-prereq count then closure size, then
    mastered topics last (omitted entirely when ``include_mastered=False``).

    An empty or not-yet-imported curriculum (no ``item_edge`` rows, or a
    cyclic stored graph that cannot answer reachability) is not a failure: it
    returns ``ok=True`` with an empty ``curriculum`` list, zeroed ``counts``,
    and a ``note`` naming the condition.
    """
    from katagiri.intelligence import (
        GRAMMAR_KIND,
        _curriculum_tags_for,
        grammar_reachability,
        load_grammar_dag,
    )

    day = _today(today)
    caps = _caps_block(conn, day)
    empty_note = (
        "The curriculum has not been imported (no item_edge rows), or the "
        "stored graph has a cycle and cannot answer reachability. "
        f"{STUDY_PLAN_NOTE}"
    )

    dag = load_grammar_dag(conn)
    if dag.cycle is not None or (not dag.prereqs and not dag.unlocked_by):
        return {
            "ok": True,
            "curriculum": [],
            "counts": {
                "total": 0,
                "mastered": 0,
                "reachable_now": 0,
                "blocked": 0,
            },
            "caps": caps,
            "note": empty_note,
        }

    rows = conn.execute(
        "SELECT id FROM item WHERE kind = ? AND sealed = 0 ORDER BY id ASC",
        (GRAMMAR_KIND,),
    ).fetchall()
    if not rows:
        return {
            "ok": True,
            "curriculum": [],
            "counts": {
                "total": 0,
                "mastered": 0,
                "reachable_now": 0,
                "blocked": 0,
            },
            "caps": caps,
            "note": empty_note,
        }

    grammar_ids = [str(row["id"]) for row in rows]
    verdicts = grammar_reachability(conn, grammar_ids, dag=dag)
    tags = _curriculum_tags_for(conn, grammar_ids)

    total = len(grammar_ids)
    mastered_count = sum(1 for v in verdicts.values() if v["mastered"])
    reachable_now = sum(
        1 for v in verdicts.values() if not v["mastered"] and v["reachable"]
    )
    blocked_count = total - mastered_count - reachable_now

    reachable_nodes = [
        v for v in verdicts.values() if not v["mastered"] and v["reachable"]
    ]
    blocked_nodes = [
        v for v in verdicts.values() if not v["mastered"] and not v["reachable"]
    ]
    mastered_nodes = [v for v in verdicts.values() if v["mastered"]]

    reachable_nodes.sort(key=lambda v: (v["closure_size"], v["id"]))
    blocked_nodes.sort(
        key=lambda v: (len(v["missing_prereqs"]), v["closure_size"], v["id"])
    )
    mastered_nodes.sort(key=lambda v: v["id"])

    ordered = [*reachable_nodes, *blocked_nodes]
    if include_mastered:
        ordered.extend(mastered_nodes)

    curriculum = [
        {
            "id": v["id"],
            "kind": v["kind"],
            "mastered": v["mastered"],
            "mastered_via": v["mastered_via"],
            "reachable": v["reachable"],
            "unlock_ready": v["unlock_ready"],
            "understanding": v["understanding"],
            "missing_prereqs": v["missing_prereqs"],
            "prereqs": v["prereqs"],
            "attributes": tags.get(v["id"], {}),
        }
        for v in ordered
    ]

    return {
        "ok": True,
        "curriculum": curriculum,
        "counts": {
            "total": total,
            "mastered": mastered_count,
            "reachable_now": reachable_now,
            "blocked": blocked_count,
        },
        "caps": caps,
        "note": STUDY_PLAN_NOTE,
    }


# ---------------------------------------------------------------------------
# log_lesson / lessons
# ---------------------------------------------------------------------------


def log_lesson(
    conn: sqlite3.Connection,
    *,
    topic: str | Envelope,
    objective: str | Envelope,
    lesson_id: str | None = None,
    session_id: str | None = None,
    opened_ts: str | None = None,
    closed: bool = True,
    next_step: str | Envelope | None = None,
    revisit_after: str | int | None = None,
    free_notes: str | Envelope | None = None,
    unresolved: Sequence[str | Envelope] = (),
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    today: str | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Record one lesson: open it, close it, or do both in one call.

    Passing ``lesson_id`` updates that lesson (the usual close-at-end call);
    omitting it inserts a new row. ``closed=True`` — the default, because the
    interesting moment is the close — stamps ``closed_ts`` now and logs
    ``lesson_close``; ``closed=False`` leaves the lesson open and logs
    ``lesson_open``.

    ``next_step`` is refused on a lesson that is not being closed: it is a
    conclusion, and one written at open is a plan pretending to be an outcome.
    ``revisit_after`` accepts a day key or a number of days from today, and
    schedules the **topic** — Anki schedules items, Katagiri does not.

    The lesson row, its unresolved threads and the event land in one
    transaction: a lesson that is not in the log did not happen, and a log entry
    with no lesson behind it is a lie.
    """
    answer = _base(
        {
            "lesson_id": None,
            "created": False,
            "closed": False,
            "session_id": None,
            "opened_ts": None,
            "closed_ts": None,
            "topic": None,
            "next_step": None,
            "revisit_after": None,
            "unresolved_ids": [],
            "event_id": None,
            "untrusted": {},
        }
    )
    try:
        provenance: dict[str, dict[str, Any]] = {}

        def field(
            name: str,
            value: str | Envelope | None,
            *,
            required: bool = False,
            max_chars: int = MAX_TEXT_CHARS,
        ) -> str | None:
            resolved = _resolve_text(
                name,
                value,
                confirmations=confirmations,
                gate=gate,
                required=required,
                max_chars=max_chars,
            )
            _merge_provenance(provenance, name, resolved)
            return resolved.value

        topic_text = field("topic", topic, required=True)
        objective_text = field("objective", objective, required=True)

        if not closed and next_step is not None:
            raise SessionToolError(
                "next_step is written at close, not at open. Close the lesson "
                "with it, or leave it out.",
                code=NEXT_STEP_BEFORE_CLOSE,
                field="next_step",
            )
        next_step_text = field("next_step", next_step)
        notes_text = field(
            "free_notes", free_notes, max_chars=MAX_FREE_NOTES_CHARS
        )

        if len(unresolved) > MAX_UNRESOLVED_PER_CALL:
            raise SessionToolError(
                f"At most {MAX_UNRESOLVED_PER_CALL} unresolved threads per "
                "call. More than that is a backlog, not a lesson.",
                code=TOO_MANY_UNRESOLVED,
                field="unresolved",
            )
        unresolved_texts: list[str] = []
        for index, item in enumerate(unresolved):
            name = f"unresolved[{index}]"
            resolved = _resolve_text(
                name,
                item,
                confirmations=confirmations,
                gate=gate,
                required=True,
            )
            _merge_provenance(provenance, name, resolved)
            # ``required=True`` guarantees a value; the assert-free narrowing is
            # a str() rather than a cast so a future change cannot write None.
            unresolved_texts.append(str(resolved.value))

        stamp = utc_now_stamp()
        opened = _check_stamp("opened_ts", opened_ts) or stamp
        closed_ts = stamp if closed else None
        if closed_ts is not None and closed_ts < opened:
            raise SessionToolError(
                "opened_ts is in the future: a lesson cannot close before it "
                "opened.",
                code=INVALID_TIMESTAMP,
                field="opened_ts",
            )
        revisit = _resolve_revisit_after(revisit_after, today=_today(today))

        identifier = str(lesson_id).strip() if lesson_id is not None else None
        # Looked up before the transaction opens: an unknown lesson id is a
        # refusal the caller can fix, not a rollback.
        if identifier is not None:
            existing = conn.execute(
                "SELECT opened_ts FROM lesson WHERE id = ?", (identifier,)
            ).fetchone()
            if existing is None:
                raise UnknownLesson(identifier)
            opened = str(existing["opened_ts"])
            # The stored ``opened_ts`` replaces whatever the caller passed, so
            # the check above was made against a value that is no longer the
            # effective one. Re-check: ``lesson`` has a
            # ``closed_ts >= opened_ts`` CHECK, and letting SQLite enforce it
            # raises an IntegrityError out of the tool instead of returning the
            # refusal this module promises for everything a caller can fix.
            if closed_ts is not None and closed_ts < opened:
                raise SessionToolError(
                    "This lesson's stored opened_ts is in the future, so it "
                    "cannot be closed now: a lesson cannot close before it "
                    "opened. Fix the lesson's opened_ts first.",
                    code=CLOSE_BEFORE_OPEN,
                    field="lesson_id",
                )
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    session = (
        str(session_id).strip()
        if session_id is not None and str(session_id).strip()
        else None
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        created = identifier is None
        if identifier is None:
            identifier = new_ulid()
            conn.execute(
                """
                INSERT INTO lesson (
                    id, opened_ts, closed_ts, session_id, topic, objective,
                    next_step, revisit_after, free_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identifier,
                    opened,
                    closed_ts,
                    session,
                    topic_text,
                    objective_text,
                    next_step_text,
                    revisit,
                    notes_text,
                ),
            )
        else:
            # COALESCE on the *new* value: a close call that omits a field
            # leaves what the open call recorded, rather than blanking it.
            conn.execute(
                """
                UPDATE lesson
                   SET closed_ts     = COALESCE(?, closed_ts),
                       session_id    = COALESCE(?, session_id),
                       topic         = ?,
                       objective     = ?,
                       next_step     = COALESCE(?, next_step),
                       revisit_after = COALESCE(?, revisit_after),
                       free_notes    = COALESCE(?, free_notes)
                 WHERE id = ?
                """,
                (
                    closed_ts,
                    session,
                    topic_text,
                    objective_text,
                    next_step_text,
                    revisit,
                    notes_text,
                    identifier,
                ),
            )

        unresolved_ids: list[int] = []
        for text in unresolved_texts:
            cursor = conn.execute(
                """
                INSERT INTO lesson_unresolved (lesson_id, text, created_ts)
                VALUES (?, ?, ?)
                """,
                (identifier, text, stamp),
            )
            unresolved_ids.append(int(cursor.lastrowid or 0))

        event_id = append_event(
            conn,
            type=LESSON_CLOSE_EVENT if closed else LESSON_OPEN_EVENT,
            session_id=_session_or_synthetic(session, "lesson", stamp),
            ts_device=stamp,
            tz=tz,
            payload={
                "lesson_id": identifier,
                "topic": topic_text,
                "objective": objective_text,
                "next_step": next_step_text,
                "revisit_after": revisit,
                "unresolved_count": len(unresolved_ids),
                "created": created,
                "untrusted": provenance or None,
            },
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    _log.info(
        "lesson logged: lesson=%s created=%s closed=%s unresolved=%d "
        "untrusted_fields=%d",
        identifier,
        created,
        closed,
        len(unresolved_ids),
        len(provenance),
    )
    return {
        **answer,
        "lesson_id": identifier,
        "created": created,
        "closed": bool(closed),
        "session_id": session,
        "opened_ts": opened,
        "closed_ts": closed_ts,
        "topic": topic_text,
        "next_step": next_step_text,
        "revisit_after": revisit,
        "unresolved_ids": unresolved_ids,
        "event_id": event_id,
        "untrusted": provenance,
        "note": _untrusted_note(provenance),
    }


def lessons(
    conn: sqlite3.Connection,
    topic: str | None = None,
    unresolved_only: bool = False,
    limit: int = DEFAULT_LESSON_LIMIT,
) -> list[dict[str, Any]]:
    """Past lessons, newest first, with their computed outcome and threads.

    ``topic`` matches exactly — topics are names the learner chose, and a fuzzy
    match here would quietly merge two of them. ``unresolved_only`` keeps only
    lessons that still have an open thread, which is the "what did I leave
    hanging" question.

    Counts come from the ``lesson_outcome`` view rather than being computed
    here: a lesson's outcome is the shape of the observations recorded while it
    was open, and that join lives in the schema.
    """
    if limit < 1:
        raise ValueError(f"limit must be at least 1; got {limit}.")

    clauses: list[str] = []
    params: list[Any] = []
    if topic is not None:
        clauses.append("l.topic = ?")
        params.append(topic)
    if unresolved_only:
        clauses.append("o.unresolved_open > 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT l.id, l.topic, l.objective, l.opened_ts, l.closed_ts,
               l.session_id, l.next_step, l.revisit_after, l.free_notes,
               o.observation_count, o.item_count, o.unassisted_count,
               o.unresolved_served, o.unresolved_open
          FROM lesson l
          JOIN lesson_outcome o ON o.lesson_id = l.id
        {where}
         ORDER BY l.opened_ts DESC, l.id DESC
         LIMIT ?
        """,
        params,
    ).fetchall()

    records = [dict(row) for row in rows]
    if not records:
        return []

    threads: dict[str, list[dict[str, Any]]] = {record["id"]: [] for record in records}
    placeholders = ", ".join("?" * len(threads))
    for row in conn.execute(
        f"""
        SELECT id, lesson_id, text, created_ts, resolved_ts
          FROM lesson_unresolved
         WHERE lesson_id IN ({placeholders})
         ORDER BY created_ts ASC, id ASC
        """,
        tuple(threads),
    ):
        threads[str(row["lesson_id"])].append(
            {
                "id": int(row["id"]),
                "text": row["text"],
                "created_ts": row["created_ts"],
                "resolved_ts": row["resolved_ts"],
                "resolved": row["resolved_ts"] is not None,
            }
        )

    for record in records:
        record["unresolved"] = threads[record["id"]]
        record["closed"] = record["closed_ts"] is not None
    return records


# ---------------------------------------------------------------------------
# log_observations: the mandatory-field gate
# ---------------------------------------------------------------------------


def _reject(index: int, field: str, code: str, note: str) -> dict[str, Any]:
    return {"index": index, "field": field, "error": code, "note": note}


def _read_unassisted(value: Any) -> tuple[bool, str | None]:
    """``unassisted`` as ``(flag, rejection code or None)``.

    Only ``bool`` and ``0``/``1`` are accepted. ``"false"`` is exactly the kind
    of input that becomes ``True`` in a language where a non-empty string is
    truthy, and an assisted production is a *different* observation rather than
    a slightly worse one — so this field may never be guessed at.
    """
    if value is None:
        return False, MISSING_UNASSISTED
    if isinstance(value, bool):
        return value, None
    if isinstance(value, int) and value in (0, 1):
        return bool(value), None
    return False, INVALID_UNASSISTED


@dataclass(frozen=True, slots=True)
class _CheckedObservation:
    """One observation whose mandatory fields are known good.

    Holds the values :func:`_validate_observation` already derived so
    :func:`_build_observation` does not re-derive them (and cannot drift), plus
    the record itself, whose text fields are unwrapped later — once the whole
    batch has passed.
    """

    index: int
    record: Mapping[str, Any]
    stamp: str | None
    task_type: str
    unassisted: bool
    band: str
    rubric_version: str


def _validate_observation(
    index: int,
    record: Mapping[str, Any],
    *,
    confirmations: Mapping[str, Confirmation] | None,
    gate: EchoGate | None,
) -> tuple[_CheckedObservation | None, list[dict[str, Any]]]:
    """One observation, checked as far as is possible without spending anything.

    Mandatory fields are checked first; only if they all pass are the text
    fields pre-checked (:func:`_precheck_text`) — the trust boundary, the caps
    and each envelope's integrity, but *no* unwrap. Spending happens in
    :func:`_build_observation`, which the caller runs only after every record in
    the batch got through here: an observation whose ``rubric_version`` is
    missing must not cost an earlier record the confirmation its retry needs.

    Returns the checked record (``None`` when it was rejected) and its
    rejections. Text-field problems raise, exactly as they did when they were
    resolved here: they refuse the call rather than joining ``rejected``.
    """
    rejections: list[dict[str, Any]] = []

    task_type = record.get("task_type")
    if not isinstance(task_type, str) or not task_type.strip():
        rejections.append(
            _reject(
                index,
                "task_type",
                MISSING_TASK_TYPE,
                "task_type names what was attempted (cloze, translate_en_jp, "
                "shadow, ...) and is NOT NULL in the schema.",
            )
        )

    unassisted, unassisted_code = _read_unassisted(record.get("unassisted"))
    if unassisted_code is not None:
        rejections.append(
            _reject(
                index,
                "unassisted",
                unassisted_code,
                "unassisted must be a boolean (or 0/1): hints and lookups make "
                "a different observation, so this can never be unknown.",
            )
        )

    band = record.get("coverage_band")
    if band is None or (isinstance(band, str) and not band.strip()):
        rejections.append(
            _reject(
                index,
                "coverage_band",
                MISSING_COVERAGE_BAND,
                "coverage_band is required: a performance is only "
                f"interpretable against its input. One of {list(COVERAGE_BANDS)}.",
            )
        )
    elif not isinstance(band, str) or band.strip() not in COVERAGE_BANDS:
        rejections.append(
            _reject(
                index,
                "coverage_band",
                INVALID_COVERAGE_BAND,
                f"coverage_band must be one of {list(COVERAGE_BANDS)}.",
            )
        )
        band = None
    else:
        band = band.strip()

    rubric_version = record.get("rubric_version")
    if not isinstance(rubric_version, str) or not rubric_version.strip():
        rejections.append(
            _reject(
                index,
                "rubric_version",
                MISSING_RUBRIC_VERSION,
                "rubric_version is required and is never defaulted: scores are "
                "comparable only within a rubric version, and a guessed one "
                "corrupts the whole series retroactively.",
            )
        )

    ts = record.get("ts")
    try:
        stamp = _check_stamp(f"observations[{index}].ts", ts)
    except SessionToolError as exc:
        stamp = None
        rejections.append(
            _reject(index, "ts", INVALID_TIMESTAMP, exc.note)
        )

    if rejections:
        return None, rejections

    _precheck_text(
        f"observations[{index}].expected",
        record.get("expected"),
        confirmations=confirmations,
        gate=gate,
    )
    _precheck_text(
        f"observations[{index}].produced",
        record.get("produced"),
        confirmations=confirmations,
        gate=gate,
    )
    _precheck_text(
        f"observations[{index}].stimulus",
        record.get("stimulus"),
        confirmations=confirmations,
        gate=gate,
        untrusted_only=True,
    )

    return (
        _CheckedObservation(
            index=index,
            record=record,
            stamp=stamp,
            task_type=str(task_type).strip(),
            unassisted=unassisted,
            # ``band`` is the stripped value by here; the mandatory-field pass
            # above narrowed it and rejected everything else.
            band=str(band),
            rubric_version=str(rubric_version).strip(),
        ),
        [],
    )


def _build_observation(
    checked: _CheckedObservation,
    *,
    confirmations: Mapping[str, Confirmation] | None,
    gate: EchoGate | None,
) -> dict[str, Any]:
    """The row to write, unwrapping this record's envelopes — spending happens here.

    Call this only once *every* record in the batch has been through
    :func:`_validate_observation`. Each envelope's confirmation is spendable
    exactly once, and a batch is all-or-nothing, so an unwrap before the last
    record is known good would leave a refused call holding confirmations the
    retry can no longer use.
    """
    index = checked.index
    record = checked.record

    expected = _resolve_text(
        f"observations[{index}].expected",
        record.get("expected"),
        confirmations=confirmations,
        gate=gate,
    )
    produced = _resolve_text(
        f"observations[{index}].produced",
        record.get("produced"),
        confirmations=confirmations,
        gate=gate,
    )
    stimulus = _resolve_text(
        f"observations[{index}].stimulus",
        record.get("stimulus"),
        confirmations=confirmations,
        gate=gate,
        untrusted_only=True,
    )

    item_id = record.get("item_id")
    media_ref = record.get("media_ref")
    provenance: dict[str, dict[str, Any]] = {}
    for name, resolved in (
        ("expected", expected),
        ("produced", produced),
        ("stimulus", stimulus),
    ):
        _merge_provenance(provenance, name, resolved)

    return {
        "ts": checked.stamp,
        "task_type": checked.task_type,
        "unassisted": 1 if checked.unassisted else 0,
        "coverage_band": checked.band,
        "rubric_version": checked.rubric_version,
        "item_id": None if item_id is None else str(item_id),
        "media_ref": None if media_ref is None else str(media_ref),
        "expected": expected.value,
        "produced": produced.value,
        "stimulus": stimulus.value,
        "untrusted": provenance,
    }


def log_observations(
    conn: sqlite3.Connection,
    observations: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Record rubric-scored performances. **Mandatory fields are enforced.**

    Each observation is a mapping with the required keys ``task_type``,
    ``unassisted``, ``coverage_band`` (one of :data:`COVERAGE_BANDS`) and
    ``rubric_version``, plus optional ``item_id``, ``expected``, ``produced``,
    ``media_ref``, ``ts``, and ``stimulus`` — the last being untrusted-only
    (the media text the learner performed against), so it takes an envelope.

    Nothing is defaulted and nothing is partially written: the whole batch is
    validated first, and one bad record refuses the call with every rejection
    listed under ``rejected``. That is the deliberate trade. This series is the
    unassisted pass-rate the D6 gate reads, the observation log is append-only,
    and a batch that lands half-written with one guessed ``rubric_version`` is
    unfixable afterwards — whereas a refusal costs one retry.

    A single mapping is accepted as a one-element batch, because logging one
    observation is the common case.
    """
    answer = _base(
        {
            "written": 0,
            "session_id": None,
            "observation_ids": [],
            "event_ids": [],
            "unassisted": 0,
            "coverage_bands": {},
            "rubric_versions": [],
            "rejected": [],
            "untrusted": {},
        }
    )
    records: Sequence[Mapping[str, Any]]
    if isinstance(observations, Mapping):
        records = [observations]
    else:
        records = list(observations)

    try:
        session = _require_session_id(session_id)
        if not records:
            raise SessionToolError(
                "No observations were supplied. An empty batch is a caller "
                "mistake, not an empty result.",
                code=NO_OBSERVATIONS,
                field="observations",
            )
        if len(records) > MAX_OBSERVATIONS_PER_CALL:
            raise SessionToolError(
                f"At most {MAX_OBSERVATIONS_PER_CALL} observations per call.",
                code=TOO_MANY_OBSERVATIONS,
                field="observations",
            )
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise InvalidFieldValue(
                    f"observations[{index}] must be a mapping of fields; got "
                    f"{type(record).__name__}.",
                    field=f"observations[{index}]",
                )

        # Two passes, and the order is the point. The first spends nothing, so a
        # rejection in the last record cannot cost the first record the
        # confirmation its retry needs; only once the whole batch is known good
        # does the second pass unwrap.
        checked: list[_CheckedObservation] = []
        rejections: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            one, bad = _validate_observation(
                index, record, confirmations=confirmations, gate=gate
            )
            rejections.extend(bad)
            if one is not None:
                checked.append(one)
        if rejections:
            raise ObservationsRejected(rejections)

        prepared: list[dict[str, Any]] = [
            _build_observation(one, confirmations=confirmations, gate=gate)
            for one in checked
        ]
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    stamp = utc_now_stamp()
    observation_ids: list[str] = []
    event_ids: list[str] = []
    bands: dict[str, int] = {}
    versions: list[str] = []
    provenance: dict[str, dict[str, Any]] = {}

    conn.execute("BEGIN IMMEDIATE")
    try:
        for index, row in enumerate(prepared):
            observation_id = new_ulid()
            ts = row["ts"] or stamp
            conn.execute(
                """
                INSERT INTO observation (
                    id, ts, session_id, item_id, task_type, expected, produced,
                    unassisted, coverage_band, rubric_version, media_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    ts,
                    session,
                    row["item_id"],
                    row["task_type"],
                    row["expected"],
                    row["produced"],
                    row["unassisted"],
                    row["coverage_band"],
                    row["rubric_version"],
                    row["media_ref"],
                ),
            )
            event_ids.append(
                append_event(
                    conn,
                    type=OBSERVATION_EVENT,
                    session_id=session,
                    item_id=row["item_id"],
                    ts_device=ts,
                    tz=tz,
                    payload={
                        "observation_id": observation_id,
                        "task_type": row["task_type"],
                        "unassisted": bool(row["unassisted"]),
                        "coverage_band": row["coverage_band"],
                        "rubric_version": row["rubric_version"],
                        "stimulus": row["stimulus"],
                        "untrusted": row["untrusted"] or None,
                    },
                )
            )
            observation_ids.append(observation_id)
            bands[row["coverage_band"]] = bands.get(row["coverage_band"], 0) + 1
            if row["rubric_version"] not in versions:
                versions.append(row["rubric_version"])
            for name, entry in row["untrusted"].items():
                provenance[f"observations[{index}].{name}"] = entry
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    unassisted = sum(1 for row in prepared if row["unassisted"])
    _log.info(
        "observations logged: session=%s written=%d unassisted=%d bands=%s",
        session,
        len(observation_ids),
        unassisted,
        sorted(bands),
    )
    return {
        **answer,
        "written": len(observation_ids),
        "session_id": session,
        "observation_ids": observation_ids,
        "event_ids": event_ids,
        "unassisted": unassisted,
        "coverage_bands": bands,
        "rubric_versions": versions,
        "untrusted": provenance,
        "note": _untrusted_note(provenance),
    }


# ---------------------------------------------------------------------------
# log_error
# ---------------------------------------------------------------------------


def log_error(
    conn: sqlite3.Connection,
    *,
    said: str | Envelope,
    correct: str | Envelope,
    pattern: str | Envelope,
    severity: str,
    item_id: str | None = None,
    session_id: str | None = None,
    context: Envelope | None = None,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Record one mistake: what was said, what was correct, and the pattern.

    ``pattern`` is the reusable part — "て-form of する", "counter for flat
    objects" — and is what later drills and the confusion graph group by; a
    mistake logged without one is an anecdote. ``severity`` is one of
    :data:`SEVERITIES` and has no default, because "how much did this cost" is
    a judgement the tool must not make on the learner's behalf.

    ``said`` and ``correct`` land in the event's ``answer_given`` / ``expected``
    columns, which is the split those columns exist for. ``context`` is the
    surrounding line and is untrusted-only: it typically comes off a subtitle,
    so it arrives enveloped and confirmed or not at all.
    """
    answer = _base(
        {
            "event_id": None,
            "session_id": None,
            "item_id": None,
            "pattern": None,
            "severity": None,
            "untrusted": {},
        }
    )
    try:
        provenance: dict[str, dict[str, Any]] = {}

        def field(
            name: str,
            value: str | Envelope | None,
            *,
            required: bool = False,
            untrusted_only: bool = False,
        ) -> str | None:
            resolved = _resolve_text(
                name,
                value,
                confirmations=confirmations,
                gate=gate,
                required=required,
                untrusted_only=untrusted_only,
            )
            _merge_provenance(provenance, name, resolved)
            return resolved.value

        # Before any envelope is unwrapped: ``severity`` is checked against a
        # fixed enum and needs no content, so checking it after the unwrap
        # would spend the caller's echo-back confirmation on a call that was
        # always going to be refused — and a spent confirmation cannot be
        # reused on the retry.
        if not isinstance(severity, str) or severity.strip() not in SEVERITIES:
            raise InvalidFieldValue(
                f"severity must be one of {list(SEVERITIES)}; it has no "
                "default.",
                code=INVALID_SEVERITY,
                field="severity",
            )
        level = severity.strip()

        said_text = field("said", said, required=True)
        correct_text = field("correct", correct, required=True)
        pattern_text = field("pattern", pattern, required=True)
        context_text = field("context", context, untrusted_only=True)
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    stamp = utc_now_stamp()
    session = _session_or_synthetic(session_id, "error", stamp)
    target = None if item_id is None else str(item_id).strip() or None
    if target is not None:
        target = resolve_alias(conn, target)["canonical_id"]

    event_id = append_event(
        conn,
        type=ERROR_EVENT,
        session_id=session,
        item_id=target,
        ts_device=stamp,
        tz=tz,
        answer_given=said_text,
        expected=correct_text,
        payload={
            "pattern": pattern_text,
            "severity": level,
            "context": context_text,
            "untrusted": provenance or None,
        },
    )
    _log.info(
        "error logged: session=%s severity=%s item=%s untrusted_fields=%d",
        session,
        level,
        target,
        len(provenance),
    )
    return {
        **answer,
        "event_id": event_id,
        "session_id": session,
        "item_id": target,
        "pattern": pattern_text,
        "severity": level,
        "untrusted": provenance,
        "note": _untrusted_note(provenance),
    }


# ---------------------------------------------------------------------------
# add_vocab
# ---------------------------------------------------------------------------


def word_item_id(word: str, reading: str | None = None) -> str:
    """The schema's deterministic word id: ``w-`` + sha1(kanji|reading)[:6].

    Documented in docs/db-schema.md and reproduced rather than reinvented, so
    the same word computes to the same id on any machine and in any import
    order. It is a namespace, not a security digest — hence
    ``usedforsecurity=False`` — and a 6-hex-character space can collide in
    principle; the schema made that trade and a second id scheme here would be
    worse than the collision.
    """
    material = f"{word}|{reading or ''}".encode("utf-8")
    return "w-" + hashlib.sha1(material, usedforsecurity=False).hexdigest()[:6]


def _upsert_word_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    word: str,
    reading: str | None,
    pos: str | None,
    topic: str | None,
    pitch: int | None,
    created_ts: str,
) -> bool:
    """Insert the word, or fill in blanks on the row that is already there.

    Every update is ``COALESCE(existing, new)``: mining a word a second time
    may add a reading that was missing, and may never overwrite a value someone
    curated. Returns whether the row was created.
    """
    existed = conn.execute(
        "SELECT 1 FROM item WHERE id = ?", (item_id,)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO item (id, kind, kanji, reading, pos, home_topic, pitch,
                          created_ts)
        VALUES (?, 'word', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            reading    = COALESCE(item.reading, excluded.reading),
            pos        = COALESCE(item.pos, excluded.pos),
            home_topic = COALESCE(item.home_topic, excluded.home_topic),
            pitch      = COALESCE(item.pitch, excluded.pitch)
        """,
        (item_id, word, reading, pos, topic, pitch, created_ts),
    )
    return existed is None


def add_vocab(
    conn: sqlite3.Connection,
    *,
    word: str | Envelope,
    reading: str | Envelope | None = None,
    meaning: str | Envelope | None = None,
    pos: str | None = None,
    topic: str | None = None,
    pitch: int | None = None,
    note: str | Envelope | None = None,
    example: Envelope | None = None,
    session_id: str | None = None,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    today: str | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Mine one word: an ``item`` row plus a ``mining`` event.

    ``word`` is the headword the learner vouches for, so it is trusted text.
    ``example`` — the anchor sentence lifted from whatever they were watching —
    is untrusted-only and arrives enveloped.

    ``meaning`` is **not** an item column and is recorded in the event payload
    instead: glosses live on the dictionary side (``lexeme`` / ``jmdict_sense``)
    and a learner's working translation is a fact about the mining moment, not a
    fact about the word.

    Nothing is written to the vault: the Obsidian bridge is read-only in this
    build, so the topic file gets this word when the derived exporters next run.

    Refuses past the day's new-word cap (FR-015, :data:`MAX_NEW_WORDS_PER_DAY`,
    counted from today's ``mining`` events) with :data:`NEW_WORD_CAP_REACHED`,
    naming the cap and how many were already mined today. This is never a
    silent success at a smaller size — the overflow route is the inbox
    (``triage_inbox``), not a word mined anyway.

    ``today`` names the day the cap is counted against, matching the
    convention in :func:`prescribe`/:func:`start_session`: a ``YYYY-MM-DD``
    day key, defaulting to the real wall-clock date when omitted. Passing the
    same explicit ``today`` to both keeps ``prescribe``'s reported
    ``caps.new_words_left`` and this refusal in agreement — otherwise a caller
    who fixes the clock for one but not the other can see them disagree.
    """
    answer = _base(
        {
            "item_id": None,
            "created": False,
            "redirected": False,
            "event_id": None,
            "session_id": None,
            "word": None,
            "reading": None,
            "untrusted": {},
        }
    )
    try:
        provenance: dict[str, dict[str, Any]] = {}

        def field(
            name: str,
            value: str | Envelope | None,
            *,
            required: bool = False,
            untrusted_only: bool = False,
        ) -> str | None:
            resolved = _resolve_text(
                name,
                value,
                confirmations=confirmations,
                gate=gate,
                required=required,
                untrusted_only=untrusted_only,
            )
            _merge_provenance(provenance, name, resolved)
            return resolved.value

        # Before any envelope is unwrapped, for the same reason as ``severity``
        # in :func:`log_error`: this looks at nothing but ``pitch`` itself, and
        # a refusal after the unwrap would burn the confirmation the retry
        # needs.
        if pitch is not None:
            if isinstance(pitch, bool) or not isinstance(pitch, int):
                raise InvalidFieldValue(
                    "pitch is the drop position as an integer (0 = heiban), "
                    "not a contour string.",
                    code=INVALID_PITCH,
                    field="pitch",
                )
            if pitch < 0:
                raise InvalidFieldValue(
                    "pitch must be 0 or greater; leave it out when unknown.",
                    code=INVALID_PITCH,
                    field="pitch",
                )

        # Also before any envelope is unwrapped, and before the DB write
        # below: past the daily cap this is a refusal, not a smaller mining,
        # and a refusal after spending a confirmation would burn it for
        # nothing (FR-015).
        mined_today = conn.execute(
            "SELECT COUNT(*) FROM event WHERE type = ? AND day_key = ?",
            (MINING_EVENT, _today(today).isoformat()),
        ).fetchone()[0]
        if int(mined_today) >= MAX_NEW_WORDS_PER_DAY:
            raise SessionToolError(
                f"Daily new-word cap reached: {mined_today} of "
                f"{MAX_NEW_WORDS_PER_DAY} words already mined today. Put it "
                "in the inbox instead (triage_inbox) — it keeps until "
                "tomorrow's cap resets; this is a deferral, not a loss.",
                code=NEW_WORD_CAP_REACHED,
                field="word",
            )

        word_text = str(field("word", word, required=True))
        reading_text = field("reading", reading)
        meaning_text = field("meaning", meaning)
        note_text = field("note", note)
        example_text = field("example", example, untrusted_only=True)
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    stamp = utc_now_stamp()
    session = _session_or_synthetic(session_id, "mining", stamp)
    minted = word_item_id(word_text, reading_text)
    resolved_id = resolve_alias(conn, minted)
    item_id = resolved_id["canonical_id"]

    conn.execute("BEGIN IMMEDIATE")
    try:
        created = _upsert_word_item(
            conn,
            item_id=item_id,
            word=word_text,
            reading=reading_text,
            pos=None if pos is None else str(pos).strip() or None,
            topic=None if topic is None else str(topic).strip() or None,
            pitch=pitch,
            created_ts=stamp,
        )
        event_id = append_event(
            conn,
            type=MINING_EVENT,
            session_id=session,
            item_id=item_id,
            ts_device=stamp,
            tz=tz,
            payload={
                "source": "add_vocab",
                "word": word_text,
                "reading": reading_text,
                "meaning": meaning_text,
                "pos": pos,
                "topic": topic,
                "pitch": pitch,
                "note": note_text,
                "example": example_text,
                "created": created,
                "untrusted": provenance or None,
            },
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    _log.info(
        "vocab mined: item=%s created=%s session=%s untrusted_fields=%d",
        item_id,
        created,
        session,
        len(provenance),
    )
    return {
        **answer,
        "item_id": item_id,
        "created": created,
        "redirected": bool(resolved_id["redirected"]),
        "event_id": event_id,
        "session_id": session,
        "word": word_text,
        "reading": reading_text,
        "untrusted": provenance,
        "note": _untrusted_note(provenance),
    }


# ---------------------------------------------------------------------------
# log_listening
# ---------------------------------------------------------------------------


def log_listening(
    conn: sqlite3.Connection,
    *,
    source: str | Envelope,
    reps: int,
    session_id: str | None = None,
    ts: str | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Log one listening block: reps of known audio, not minutes.

    D-37 (docs/decisions-ledger.md) and FR-017 (spec.md): the input strand's
    listening blocks append to the **existing** ``study_session`` event series
    — no second unread channel — under a deterministic dedupe-key namespace,
    ``listen:<normalised ts>``, distinct by construction from the importer's
    ``study:<normalised ts>`` keys (:func:`katagiri.events.import_study_log`).
    The two prefixes are different strings compared by exact match on
    ``dedupe_key``, so a listening-block write and a later re-run of the
    importer over the same day can never collide or double-count.

    The logged metric is **reps of known audio** — replay count against an
    audio-anchored item, e.g. 10 replays of one 40-second Irodori dialogue —
    never minutes: ``minutes`` is not a key in this payload at all, never
    zero-filled to look measured, so a reps-only log makes no minutes claim
    and changes no day-qualification arithmetic (the D6 stop-gate, D-19 and
    D-33 are untouched).

    ``source`` is the identity of the known recording being replayed. Until
    TG4 lands an audio-anchor reference (FR-018), this is just the source
    string the learner names (e.g. an Irodori lesson/dialogue label); once an
    anchor exists, a later lane can extend this additively rather than
    replacing it. ``source`` is learner-authored, so it is trusted text and
    takes a plain string — but it also accepts an :class:`Envelope`, in which
    case the echo-back ceremony is enforced like every other field here.

    ``ts`` is the moment the listening happened, in the loose ISO-8601 shape
    :func:`katagiri.events.normalize_stamp` accepts (the same shape the
    importer normalises ``ts`` fields from). It defaults to now. Passing it
    explicitly is what lets a caller re-log the same block and observe the
    no-op — the dedupe key is derived from it, not from wall-clock time — and
    is the testability hook sibling tools expose as ``today``/``opened_ts``.

    Idempotent the way :func:`katagiri.events.import_study_log` is: calling
    this twice with a ``ts`` that normalises to the same second is a no-op the
    second time — ``append_event`` absorbs the ``UNIQUE`` violation on
    ``dedupe_key`` and hands back the first call's event id, and the returned
    ``duplicate`` flag says so.
    """
    answer = _base(
        {
            "event_id": None,
            "session_id": None,
            "source": None,
            "listening_reps": None,
            "ts": None,
            "duplicate": False,
            "untrusted": {},
        }
    )
    try:
        provenance: dict[str, dict[str, Any]] = {}

        def field(
            name: str,
            value: str | Envelope | None,
            *,
            required: bool = False,
        ) -> str | None:
            resolved = _resolve_text(
                name,
                value,
                required=required,
            )
            _merge_provenance(provenance, name, resolved)
            return resolved.value

        # Before any envelope is unwrapped, for the same reason as ``pitch``
        # in :func:`add_vocab`: this looks at nothing but ``reps`` itself, and
        # a refusal after the unwrap would burn a confirmation the retry needs.
        if reps is None:
            raise MissingRequiredField("reps")
        if isinstance(reps, bool) or not isinstance(reps, int) or reps <= 0:
            raise InvalidFieldValue(
                "reps must be a positive integer replay count. Minutes are "
                "never recorded here, and reps are never zero-filled or "
                "guessed to look measured — an unknown count is not logged.",
                code=INVALID_REPS,
                field="reps",
            )

        source_text = str(field("source", source, required=True))

        stamp_input = ts if ts is not None else utc_now_stamp()
        try:
            stamp = normalize_stamp(str(stamp_input))
        except ValueError as exc:
            raise SessionToolError(
                str(exc), code=INVALID_TIMESTAMP, field="ts"
            ) from exc
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    dedupe_key = f"listen:{stamp}"
    session = _session_or_synthetic(session_id, "listen", stamp)

    before = conn.execute(
        "SELECT 1 FROM event WHERE dedupe_key = ?", (dedupe_key,)
    ).fetchone()

    event_id = append_event(
        conn,
        type=STUDY_LOG_TYPE,
        session_id=session,
        ts_device=stamp,
        tz=tz,
        dedupe_key=dedupe_key,
        payload={
            "listening_reps": reps,
            "source": source_text,
            "untrusted": provenance or None,
        },
    )
    duplicate = before is not None

    _log.info(
        "listening logged: session=%s reps=%d source_len=%d duplicate=%s",
        session,
        reps,
        len(source_text),
        duplicate,
    )

    notes = []
    if duplicate:
        notes.append(
            "Already logged for this timestamp; nothing new was written."
        )
    untrusted_note = _untrusted_note(provenance)
    if untrusted_note:
        notes.append(untrusted_note)

    return {
        **answer,
        "event_id": event_id,
        "session_id": session,
        "source": source_text,
        "listening_reps": reps,
        "ts": stamp,
        "duplicate": duplicate,
        "untrusted": provenance,
        "note": " ".join(notes),
    }


# ---------------------------------------------------------------------------
# triage_inbox
# ---------------------------------------------------------------------------

PROPOSAL_VOCAB: Final = "vocab"
PROPOSAL_SENTENCE: Final = "sentence"
PROPOSAL_QUESTION: Final = "question"
PROPOSAL_UNCLASSIFIED: Final = "unclassified"

PROPOSAL_KINDS: Final[tuple[str, ...]] = (
    PROPOSAL_VOCAB,
    PROPOSAL_SENTENCE,
    PROPOSAL_QUESTION,
    PROPOSAL_UNCLASSIFIED,
)

#: Separators an inbox dump uses between a word and its scribbled gloss.
_HINT_SEPARATORS: Final[tuple[str, ...]] = (
    "\t",
    " - ",
    " — ",
    " – ",
    " = ",
    "：",
    ": ",
    " | ",
)
#: Punctuation that makes a line a sentence rather than a headword.
_SENTENCE_MARKS: Final = "。、！？!?…"
#: Above this many characters a line is prose no matter what is in it.
_SENTENCE_CHARS: Final = 12


def _inbox_lines(text: str) -> list[tuple[int, str]]:
    """Numbered content lines, with markdown scaffolding stripped.

    Frontmatter fences, headings, bullet markers and checkboxes are structure
    the learner's editor added, not content they captured.
    """
    lines: list[tuple[int, str]] = []
    in_frontmatter = False
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if number == 1 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if not line or line.startswith("#") or set(line) <= {"-", "*", "_"}:
            continue
        for marker in ("- [ ] ", "- [x] ", "- ", "* ", "+ ", "> "):
            if line.startswith(marker):
                line = line[len(marker) :].strip()
                break
        if line:
            lines.append((number, line))
    return lines


def _split_hint(line: str) -> tuple[str, str | None]:
    """A captured line as (surface, hint) — ``走る - to run`` and friends."""
    for separator in _HINT_SEPARATORS:
        if separator in line:
            head, _, tail = line.partition(separator)
            head, tail = head.strip(), tail.strip()
            if head:
                return head, tail or None
    return line, None


def _has_japanese(text: str) -> bool:
    return any(is_kana_char(char) or is_han_char(char) for char in text)


def _classify(line: str) -> tuple[str, str, str | None, str]:
    """Classify one inbox line: (kind, surface, hint, why).

    Mechanical and documented, because a classifier nobody can predict is a
    classifier nobody will let apply. Nothing in the line is ever treated as an
    instruction — this reads shape, not meaning.
    """
    surface, hint = _split_hint(line)
    if line.endswith(("?", "？")):
        return (
            PROPOSAL_QUESTION,
            line,
            None,
            "ends in a question mark: it is a thread to resolve, not an item.",
        )
    if not _has_japanese(surface):
        return (
            PROPOSAL_UNCLASSIFIED,
            surface,
            hint,
            "no Japanese in it, so there is nothing to file as vocabulary.",
        )
    if len(surface) > _SENTENCE_CHARS or any(
        char in _SENTENCE_MARKS for char in surface
    ):
        return (
            PROPOSAL_SENTENCE,
            surface,
            hint,
            "long enough or punctuated enough to be a sentence; file it as an "
            "example, not a headword.",
        )
    return (
        PROPOSAL_VOCAB,
        surface,
        hint,
        "short Japanese fragment: a headword to mine.",
    )


def triage_inbox(
    conn: sqlite3.Connection,
    note: Envelope,
    *,
    dry_run: bool = True,
    session_id: str | None = None,
    confirmations: Mapping[str, Confirmation] | None = None,
    gate: EchoGate | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    """Propose filings for one inbox note, and apply the vocab ones on request.

    ``note`` is the note's text, enveloped: inbox captures are copied off web
    pages, subtitles and screenshots, so this is untrusted-only. ``dry_run``
    (the default) verifies the envelope's integrity, classifies its lines and
    returns proposals **without writing anything** — no echo-back needed,
    because nothing is committed and the excerpts are what the learner reviews
    before deciding. ``dry_run=False`` requires the confirmation and writes.

    Applying files only the ``vocab`` proposals (an ``item`` row plus a
    ``mining`` event each) and records one ``inbox_triage`` event. Sentence and
    question proposals come back under ``deferred``: a question becomes an
    unresolved thread, which needs the lesson it belongs to
    (:func:`log_lesson`), and a sentence belongs to the exercise/sentence path.
    Guessing either from a one-line dump is how an inbox becomes a mess with
    more steps.

    Nothing in the vault is read, moved or deleted — the bridge is GET-only.
    """
    answer = _base(
        {
            "dry_run": bool(dry_run),
            "applied": [],
            "deferred": [],
            "proposals": [],
            "line_count": 0,
            "truncated": False,
            "event_id": None,
            "session_id": None,
            "untrusted": None,
        }
    )
    try:
        if not is_enveloped(note):
            raise EnvelopeRequired("note")
        note.verify_integrity()

        if dry_run:
            text = note.text
        else:
            confirmation, active_gate = _confirmation_for(
                "note", note, confirmations, gate
            )
            text = active_gate.unwrap_for_write(note, confirmation)

        lines = _inbox_lines(text)
        truncated = len(lines) > MAX_INBOX_LINES
        if truncated and not dry_run:
            raise SessionToolError(
                f"This note has more than {MAX_INBOX_LINES} capture lines. "
                "Triaging part of it would file half an inbox — split the note "
                "and apply each piece.",
                code=INBOX_TOO_LARGE,
                field="note",
            )
        lines = lines[:MAX_INBOX_LINES]
        if not lines:
            raise SessionToolError(
                "Nothing to triage: the note has no capture lines once "
                "headings and list markers are removed.",
                code=NOTHING_TO_TRIAGE,
                field="note",
            )
    except (SessionToolError, EnvelopeError) as exc:
        return _refused(answer, exc)

    proposals: list[dict[str, Any]] = []
    for number, line in lines:
        kind, surface, hint, why = _classify(line)
        proposals.append(
            {
                "line": number,
                "kind": kind,
                "surface": surface,
                "hint": hint,
                "why": why,
                "excerpt": make_excerpt(line),
                "item_id": word_item_id(surface) if kind == PROPOSAL_VOCAB else None,
            }
        )

    provenance = note.for_event()
    deferred = [
        proposal for proposal in proposals if proposal["kind"] != PROPOSAL_VOCAB
    ]
    if dry_run:
        return {
            **answer,
            "proposals": proposals,
            "deferred": deferred,
            "line_count": len(lines),
            "truncated": truncated,
            "untrusted": provenance,
            "note": (
                "Nothing was written. Confirm the note and call again with "
                "dry_run=False to file the vocab proposals."
            ),
        }

    stamp = utc_now_stamp()
    session = _session_or_synthetic(session_id, "triage", stamp)
    applied: list[dict[str, Any]] = []

    conn.execute("BEGIN IMMEDIATE")
    try:
        for proposal in proposals:
            if proposal["kind"] != PROPOSAL_VOCAB:
                continue
            item_id = resolve_alias(conn, str(proposal["item_id"]))["canonical_id"]
            created = _upsert_word_item(
                conn,
                item_id=item_id,
                word=str(proposal["surface"]),
                reading=None,
                pos=None,
                topic=None,
                pitch=None,
                created_ts=stamp,
            )
            event_id = append_event(
                conn,
                type=MINING_EVENT,
                session_id=session,
                item_id=item_id,
                ts_device=stamp,
                tz=tz,
                payload={
                    "source": "triage_inbox",
                    "word": proposal["surface"],
                    "meaning": proposal["hint"],
                    "line": proposal["line"],
                    "created": created,
                    "untrusted": provenance,
                },
            )
            applied.append(
                {
                    "line": proposal["line"],
                    "item_id": item_id,
                    "created": created,
                    "event_id": event_id,
                }
            )

        triage_event = append_event(
            conn,
            type=TRIAGE_EVENT,
            session_id=session,
            ts_device=stamp,
            tz=tz,
            payload={
                "lines": len(lines),
                "filed": len(applied),
                "deferred": len(deferred),
                "kinds": {
                    kind: sum(1 for p in proposals if p["kind"] == kind)
                    for kind in PROPOSAL_KINDS
                },
                "untrusted": provenance,
            },
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    _log.info(
        "inbox triaged: session=%s lines=%d filed=%d deferred=%d envelope=%s",
        session,
        len(lines),
        len(applied),
        len(deferred),
        note.envelope_id,
    )
    return {
        **answer,
        "proposals": proposals,
        "applied": applied,
        "deferred": deferred,
        "line_count": len(lines),
        "truncated": truncated,
        "event_id": triage_event,
        "session_id": session,
        "untrusted": provenance,
        "note": _untrusted_note({"note": provenance}),
    }


__all__ = [
    "ACTION_CURRICULUM_TOPIC",
    "ACTION_KINDS",
    "ACTION_NEXT_STEP",
    "ACTION_OPEN_FIRST_LESSON",
    "ACTION_RESOLVE_THREAD",
    "ACTION_REVISIT_TOPIC",
    "ACTION_TIRED_MODE",
    "CLOSE_BEFORE_OPEN",
    "CONFIRMATION_REQUIRED",
    "COVERAGE_BANDS",
    "DEFAULT_LESSON_LIMIT",
    "ENVELOPE_REQUIRED",
    "ERROR_EVENT",
    "EVENT_TYPES",
    "FIELD_TOO_LONG",
    "GRAMMAR_WEEK_WINDOW_DAYS",
    "INBOX_TOO_LARGE",
    "INVALID_COVERAGE_BAND",
    "INVALID_FIELD",
    "INVALID_PITCH",
    "INVALID_REPS",
    "INVALID_REVISIT_AFTER",
    "INVALID_SEVERITY",
    "INVALID_TIMESTAMP",
    "INVALID_UNASSISTED",
    "LESSON_CLOSE_EVENT",
    "LESSON_OPEN_EVENT",
    "LISTENING_REPS_DAILY_TARGET",
    "MAX_FREE_NOTES_CHARS",
    "MAX_INBOX_LINES",
    "MAX_NEW_GRAMMAR_PER_WEEK",
    "MAX_NEW_WORDS_PER_DAY",
    "MAX_OBSERVATIONS_PER_CALL",
    "MAX_STAGED",
    "MAX_TEXT_CHARS",
    "MAX_UNRESOLVED_PER_CALL",
    "MINING_EVENT",
    "MISSING_COVERAGE_BAND",
    "MISSING_FIELD",
    "MISSING_RUBRIC_VERSION",
    "MISSING_SESSION_ID",
    "MISSING_TASK_TYPE",
    "MISSING_UNASSISTED",
    "NEW_WORD_CAP_REACHED",
    "NEXT_STEP_BEFORE_CLOSE",
    "NOTHING_TO_TRIAGE",
    "NO_OBSERVATIONS",
    "OBSERVATIONS_REJECTED",
    "OBSERVATION_EVENT",
    "PROPOSAL_KINDS",
    "PROPOSAL_QUESTION",
    "PROPOSAL_SENTENCE",
    "PROPOSAL_UNCLASSIFIED",
    "PROPOSAL_VOCAB",
    "SESSION_OPEN_EVENT",
    "SEVERITIES",
    "TOO_MANY_OBSERVATIONS",
    "TOO_MANY_UNRESOLVED",
    "TRIAGE_EVENT",
    "UNKNOWN_LESSON",
    "UNKNOWN_STAGED_CONTENT",
    "ConfirmationRequired",
    "EnvelopeRequired",
    "FieldTooLong",
    "InvalidFieldValue",
    "MissingRequiredField",
    "ObservationsRejected",
    "SessionToolError",
    "UnknownLesson",
    "UnknownStagedContent",
    "add_vocab",
    "confirm_untrusted",
    "lessons",
    "log_error",
    "log_lesson",
    "log_listening",
    "log_observations",
    "new_session_id",
    "prescribe",
    "reset_staged",
    "staged_envelope",
    "stage_untrusted",
    "start_session",
    "triage_inbox",
    "word_item_id",
]
