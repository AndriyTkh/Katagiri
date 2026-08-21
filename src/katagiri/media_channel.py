"""E-T003: the shared interface every media-context channel implements.

Phase E adds channels — mpv, asbplayer, mokuro, a screenshot tool, lyrics —
that report what the learner is currently watching, listening to or reading.
Every one of them hands the agent text it did not write: a subtitle line, a
displayed title, an OCR'd manga panel. That is exactly the kind of content
D3's envelope (:mod:`katagiri.envelope`) exists to gate, so this module makes
the gate structural rather than a convention each channel has to remember.

Three contracts live here, because a later channel implementing only two of
them would be the interesting bug:

1. **Envelope enforcement at the boundary.** :class:`MediaChannel` is a
   template: subclasses implement ``_probe_now``/``_probe_context``, which
   return *raw* dataclasses (:class:`RawMoment`, :class:`RawContext`) whose
   text fields are plain, unenveloped ``str``. The public methods
   :meth:`MediaChannel.media_now` and :meth:`MediaChannel.media_context` are
   the only path from a raw probe to an agent-visible result, and they always
   wrap every text field with :func:`katagiri.envelope.wrap` before
   returning. ``__init_subclass__`` refuses to define a class that overrides
   either public method, so a channel cannot short-circuit the wrap by
   overriding the method that performs it — the enforcement is at class
   *definition* time, not code review time.

2. **Deterministic active-channel precedence.** Two channels can be live at
   once (mpv playing a file while asbplayer also reports a bound tab); the
   spec requires one deterministic answer for "which is active", not a race.
   :func:`select_active_channel` takes every candidate's moment plus an
   explicit ``now`` and returns the same channel every time for the same
   inputs — no random tie-break, no wall-clock read inside the decision
   itself (the caller supplies ``now``; see point 3).

3. **Heartbeat/staleness anchored on the existing mechanism.** `media_heartbeat`
   (docs/db-schema.md; ``src/katagiri/migrations/0001_init.sql``) already
   answers "is this live?" by comparing the row's ``updated_ts`` against now
   rather than storing a flag — a crashed player cannot leave a stale
   ``is_live = 1`` behind because there is no such column to leave stale.
   :func:`is_stale` and :class:`HeartbeatRow` (mirroring the table's exact
   columns: ``media_id``, ``anchor_ms``, ``displayed_text``, ``updated_ts``)
   reuse that one mechanism. Nothing in this module invents a second one —
   every liveness question in every channel answers itself by calling
   :func:`is_stale` (directly or via :meth:`MediaMoment.is_live` /
   :meth:`HeartbeatRow.is_live`), so there is exactly one place a staleness
   bug could live.

Decision functions take ``now`` as an argument and never read the clock
themselves, so precedence and staleness are pure and testable without
sleeping. Capturing *when* a moment was observed is a different concern
(genuinely wants the real clock most of the time) and stays injectable via
the ``now`` callable on :meth:`MediaChannel.media_now`, the same pattern
:mod:`katagiri.envelope` uses for its own ``clock``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Final

from katagiri.envelope import Envelope, SOURCE_MEDIA, wrap
from katagiri.events import TS_FORMAT, utc_now_stamp

# ---------------------------------------------------------------------------
# Staleness / liveness — the one mechanism, reused everywhere
# ---------------------------------------------------------------------------

#: How stale a heartbeat may be before it is reported dead. mpv's own seek
#: logger (``mpv_seek_logger.DEFAULT_POLL_INTERVAL_S``) samples once a second
#: when a channel is genuinely alive, so 15s tolerates a handful of missed
#: polls or a brief pause without misreporting a live player as dead, while
#: staying short against the length of a viewing session — "no stale-as-live"
#: (plan.md Performance Goals) without becoming flaky on ordinary jitter.
DEFAULT_STALE_THRESHOLD_MS: Final = 15_000


def is_stale(
    updated_ts: str,
    *,
    now: datetime,
    threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
) -> bool:
    """True if ``updated_ts`` (a `media_heartbeat`-shaped stamp) is too old.

    ``updated_ts`` is compared, not parsed and diffed: the schema's timestamp
    columns are fixed-width UTC (``docs/db-schema.md`` "Timestamps") so that
    they sort lexicographically, and a threshold expressed the same way keeps
    that property — the cutoff is itself one of these stamps. ``now`` must be
    an explicit, timezone-aware instant; this function never reads the clock,
    which is what makes it (and everything built on it) testable without
    sleeping and safe to call from a decision that must be reproducible.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if threshold_ms <= 0:
        raise ValueError(f"threshold_ms must be positive; got {threshold_ms}")
    cutoff = now.astimezone(timezone.utc) - timedelta(milliseconds=threshold_ms)
    return updated_ts < cutoff.strftime(TS_FORMAT)


def is_live(
    updated_ts: str,
    *,
    now: datetime,
    threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
) -> bool:
    """``not is_stale(...)`` — the affirmative spelling, for call sites that
    read better as "is this channel live" than as a double negative."""
    return not is_stale(updated_ts, now=now, threshold_ms=threshold_ms)


@dataclass(frozen=True, slots=True)
class HeartbeatRow:
    """Mirrors `media_heartbeat` exactly: same four columns, same semantics.

    ``id`` is not modeled here — the table's ``CHECK (id = 1)`` makes it a
    constant a caller never chooses. This is a plain, *unenveloped* shape on
    purpose: it is the local cache row a channel writes for the "what was on
    screen" resume pointer (``today_export._resume_section``), not text
    handed to the agent — that path is :class:`MediaMoment`, below, which is
    always enveloped. Storing the same displayed text in both places is fine;
    what must never happen is a second, differently-shaped liveness column.
    """

    media_id: str | None
    anchor_ms: int | None
    displayed_text: str | None
    updated_ts: str

    def is_live(
        self, *, now: datetime, threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS
    ) -> bool:
        return is_live(self.updated_ts, now=now, threshold_ms=threshold_ms)


# ---------------------------------------------------------------------------
# Active-channel precedence — deterministic, no wall clock in the decision
# ---------------------------------------------------------------------------

#: Fixed tie-break order when more than one channel is live at once. mpv is
#: first: it is the primary surface (plan.md/tasks.md — screenshot and
#: lyrics are literally anchored to it, FR-004/FR-005), so when a local file
#: is playing alongside a streaming tab or a manga reader, the local file
#: wins. A later channel is appended here, never inserted, so an existing
#: precedence decision cannot change shape under a channel it never
#: considered — additive, per the tool-contract-stability rule (constitution
#: VII) this module already has to honour for its public API.
CHANNEL_PRECEDENCE: Final[tuple[str, ...]] = (
    "mpv",
    "asbplayer",
    "mokuro",
    "lyrics",
    "screenshot",
)


def precedence_rank(kind: str) -> int:
    """Lower sorts first. An unlisted kind ranks last, not an error: a new
    channel that has not yet been added to :data:`CHANNEL_PRECEDENCE` should
    still resolve deterministically, just never win a tie against a known one.
    """
    try:
        return CHANNEL_PRECEDENCE.index(kind)
    except ValueError:
        return len(CHANNEL_PRECEDENCE)


def select_active_channel(
    moments: Iterable["MediaMoment"],
    *,
    now: datetime,
    threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS,
) -> "MediaMoment | None":
    """The one channel that counts as "active" right now, or ``None``.

    Deterministic in the sense the spec asks for (edge case: "Two channels
    active at once ... deterministic precedence"): the same set of moments and
    the same ``now`` always produce the same answer, regardless of the order
    ``moments`` arrives in. Only live moments (per :func:`is_live`) are
    eligible; among those, :data:`CHANNEL_PRECEDENCE` breaks the tie, and
    ``channel``/``media_id`` break any tie precedence itself does not (e.g.
    two moments reported for the same channel kind), so the result never
    depends on iteration or insertion order.
    """
    live = [m for m in moments if m.is_live(now=now, threshold_ms=threshold_ms)]
    if not live:
        return None
    return min(
        live,
        key=lambda m: (precedence_rank(m.channel), m.channel, m.media_id or ""),
    )


# ---------------------------------------------------------------------------
# Raw probes — what a channel backend observes, before envelope enforcement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawMoment:
    """What :meth:`MediaChannel._probe_now` returns: untrusted, unenveloped.

    ``displayed_text``/``title`` are exactly the fields :meth:`MediaChannel
    .media_now` will envelope; nothing else in this dataclass is
    externally-sourced free text. ``locator`` and ``detail`` become the
    provenance the envelope carries (see :class:`katagiri.envelope.Provenance`)
    — enough for a later reader to know *which* subtitle/title this was
    without the envelope's digest having to guess.
    """

    media_id: str | None
    anchor_ms: int | None
    displayed_text: str | None
    title: str | None = None
    locator: str = ""
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawLine:
    """One line of surrounding context, before envelope enforcement."""

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    locator: str = ""
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawContext:
    """What :meth:`MediaChannel._probe_context` returns: untrusted lines."""

    media_id: str | None
    anchor_ms: int | None
    lines: tuple[RawLine, ...]


# ---------------------------------------------------------------------------
# Enveloped results — what crosses the interface boundary to the agent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MediaMoment:
    """The enveloped result of :meth:`MediaChannel.media_now`.

    ``updated_ts`` is stamped in exactly the `media_heartbeat.updated_ts`
    shape so :meth:`is_live` can reuse :func:`is_stale` unchanged — the same
    liveness question, asked the same way, whether the row came from the
    database or from a channel that has not written it yet.
    """

    channel: str
    media_id: str | None
    anchor_ms: int | None
    displayed_text: Envelope | None
    title: Envelope | None
    updated_ts: str

    def is_live(
        self, *, now: datetime, threshold_ms: int = DEFAULT_STALE_THRESHOLD_MS
    ) -> bool:
        return is_live(self.updated_ts, now=now, threshold_ms=threshold_ms)

    def heartbeat_row(self) -> HeartbeatRow:
        """The plain, unenveloped shape for the `media_heartbeat` cache row.

        Deliberately unwraps: the DB row is a local "what was on screen"
        pointer a channel writes for itself, not agent-facing output, so it
        is not where the envelope's echo-back ceremony belongs.
        """
        text = self.displayed_text.text if self.displayed_text is not None else None
        return HeartbeatRow(
            media_id=self.media_id,
            anchor_ms=self.anchor_ms,
            displayed_text=text,
            updated_ts=self.updated_ts,
        )


@dataclass(frozen=True, slots=True)
class ContextLine:
    """One enveloped line of :class:`MediaContext`."""

    text: Envelope
    start_ms: int | None
    end_ms: int | None


@dataclass(frozen=True, slots=True)
class MediaContext:
    """The enveloped result of :meth:`MediaChannel.media_context`."""

    channel: str
    media_id: str | None
    anchor_ms: int | None
    lines: tuple[ContextLine, ...]


def _envelope_optional(
    text: str | None, *, locator: str, detail: Mapping[str, Any] | None
) -> Envelope | None:
    if text is None:
        return None
    return wrap(text, source=SOURCE_MEDIA, locator=locator, detail=detail)


# ---------------------------------------------------------------------------
# The channel interface
# ---------------------------------------------------------------------------


class MediaChannel(ABC):
    """Base class every context channel (mpv, asbplayer, mokuro, ...) extends.

    Implement :meth:`_probe_now` and :meth:`_probe_context`; do not implement
    :meth:`media_now` or :meth:`media_context` — ``__init_subclass__`` raises
    ``TypeError`` at class-definition time if a subclass defines either,
    because those two methods are where the envelope gets applied and a
    subclass overriding them could skip that step. This is checked once, when
    the class is created, not once per call, so the cost of the guarantee is
    paid at import time rather than on every `media_now()`.
    """

    #: Set by every subclass. Also the tie-break key in
    #: :data:`CHANNEL_PRECEDENCE` — "mpv", "asbplayer", "mokuro", "lyrics" or
    #: "screenshot".
    kind: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for guarded in ("media_now", "media_context"):
            if guarded in cls.__dict__:
                raise TypeError(
                    f"{cls.__name__} must not override {guarded}(); envelope "
                    f"enforcement lives in MediaChannel.{guarded}() and "
                    f"overriding it would let untrusted text bypass the "
                    f"envelope. Implement _probe_now()/_probe_context() "
                    f"instead."
                )
        if not cls.kind:
            raise TypeError(f"{cls.__name__} must set a non-empty 'kind'.")

    @abstractmethod
    def _probe_now(self) -> RawMoment | None:
        """One raw sample of "what is on screen right now", or ``None`` if
        the channel has nothing to report (idle, disconnected, no media
        bound)."""

    @abstractmethod
    def _probe_context(self, **kwargs: Any) -> RawContext | None:
        """A raw window of surrounding lines, or ``None`` if unavailable.

        Keyword arguments are the channel's own (e.g. how many lines of
        context); the interface does not constrain their shape because a
        subtitle window, an asbplayer line list and an OCR'd manga panel are
        genuinely different requests.
        """

    def media_now(self, *, now: Callable[[], str] = utc_now_stamp) -> MediaMoment | None:
        """The current moment, enveloped. Not overridable — see the class
        docstring."""
        raw = self._probe_now()
        if raw is None:
            return None
        return MediaMoment(
            channel=self.kind,
            media_id=raw.media_id,
            anchor_ms=raw.anchor_ms,
            displayed_text=_envelope_optional(
                raw.displayed_text, locator=raw.locator, detail=raw.detail
            ),
            title=_envelope_optional(raw.title, locator=raw.locator, detail=raw.detail),
            updated_ts=now(),
        )

    def media_context(self, **kwargs: Any) -> MediaContext | None:
        """Surrounding context, enveloped. Not overridable — see the class
        docstring."""
        raw = self._probe_context(**kwargs)
        if raw is None:
            return None
        lines = tuple(
            ContextLine(
                text=wrap(
                    line.text,
                    source=SOURCE_MEDIA,
                    locator=line.locator,
                    detail=line.detail,
                ),
                start_ms=line.start_ms,
                end_ms=line.end_ms,
            )
            for line in raw.lines
        )
        return MediaContext(
            channel=self.kind,
            media_id=raw.media_id,
            anchor_ms=raw.anchor_ms,
            lines=lines,
        )


__all__ = [
    "CHANNEL_PRECEDENCE",
    "DEFAULT_STALE_THRESHOLD_MS",
    "ContextLine",
    "HeartbeatRow",
    "MediaChannel",
    "MediaContext",
    "MediaMoment",
    "RawContext",
    "RawLine",
    "RawMoment",
    "is_live",
    "is_stale",
    "precedence_rank",
    "select_active_channel",
]
