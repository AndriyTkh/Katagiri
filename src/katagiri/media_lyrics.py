"""E-T011: lyrics through mpv's WATCH-mode subtitle pipeline (FR-005).

This is deliberately *not* a fourth standalone channel with its own
connection lifecycle. mpv already owns the playhead (``media_mpv.py``'s
``_sample``/``_probe_now``/``_probe_context`` triad) and already gates every
externally-sourced string through :func:`katagiri.envelope.wrap` before it
reaches the agent — see that module's docstring, "What 'context' means for
mpv", for why its own subtitle window is capped at the single line mpv's IPC
currently has on screen. A lyric file is the one case where this codebase
*can* build the true multi-line window that docstring calls out as
out-of-scope for mpv itself: `.lrc`/`.ass` are plain text this process can
read and parse directly, no IPC round trip required, so
:class:`LyricsChannel` parses the whole file once at construction and serves
a genuine window of lines before/after the playhead — strictly more capable
than mpv's own MVP context, built from the same windowing/envelope shape.

Division of labour, concretely:

* **mpv still owns the anchor.** :class:`LyricsChannel` takes a
  ``get_anchor_ms`` callable rather than opening a second named-pipe
  connection; :func:`mpv_anchor_supplier` adapts any object exposing a public
  ``media_now()`` (i.e. any :class:`~katagiri.media_channel.MediaChannel`,
  ``MpvChannel`` included) into that callable, so this module never imports
  ``media_mpv`` or reimplements its IPC.
* **This module owns file parsing and windowing.** :func:`parse_lrc` and
  :func:`parse_ass` turn `.lrc`/`.ass` text into :class:`LyricLine` tuples —
  the same "timestamped line with a start/end window" shape
  ``media_mpv.py``'s ``sub-text``/``sub-start``/``sub-end`` triad already
  produces, just derived from a file instead of a live property. Those lines
  feed :class:`LyricsChannel`, a :class:`~katagiri.media_channel.MediaChannel`
  subclass (``kind = "lyrics"``, already present in
  :data:`~katagiri.media_channel.CHANNEL_PRECEDENCE`) that reuses
  ``media_channel.py``'s envelope enforcement exactly as ``MpvChannel`` does —
  ``_probe_now``/``_probe_context`` return raw, unenveloped dataclasses;
  ``media_now``/``media_context`` (not overridden here, per that module's
  ``__init_subclass__`` guard) wrap every text field before it crosses the
  boundary.
* **Mining attribution.** Every line's provenance carries a source
  reference — ``locator="lyrics:<filename>:<line_no>"`` plus a ``detail``
  mapping with ``source_file``/``line_no``/``start_ms`` — so a line captured
  for mining (the project's term for saving a line as a study source) can
  always be traced back to exactly which file and line it came from, the same
  way ``media_mpv.py``'s ``locator=f"mpv:{file_name}:sub"`` attributes a
  subtitle line to its player.

Lyric text is exactly as untrusted as a subtitle line (FR-006/D-22): nothing
here ever returns a bare ``str`` to a caller outside this module — even the
raw dataclasses (:class:`~katagiri.media_channel.RawMoment`,
:class:`~katagiri.media_channel.RawLine`, :class:`~katagiri.media_channel.
RawContext`) are the same pre-envelope shape ``media_mpv.py`` uses, and the
public :meth:`~katagiri.media_channel.MediaChannel.media_now`/
:meth:`~katagiri.media_channel.MediaChannel.media_context` wrap them before
anything leaves this boundary.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from katagiri.media_channel import (
    MediaChannel,
    RawContext,
    RawLine,
    RawMoment,
)

#: `.lrc`/`.ass` — the two lyric-carrying formats FR-005 names.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset({"lrc", "ass"})

#: Lines of context on each side of the current one, when a caller does not
#: ask for a specific radius. Mirrors a small subtitle window, not the whole
#: song — enough to answer "what was just said/sung" without dumping lyrics.
DEFAULT_CONTEXT_RADIUS: int = 2


# ---------------------------------------------------------------------------
# Parsed shape — the file-derived analogue of mpv's sub-text/-start/-end
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LyricLine:
    """One parsed, timestamped lyric line, attributable back to its source.

    ``end_ms`` is ``None`` for a line with no known close (the last line in
    an `.lrc` file, or an `.ass` cue this parser could not read an End
    timestamp for) — an honest "open-ended", not a fabricated duration.
    """

    text: str
    start_ms: int
    end_ms: int | None
    line_no: int
    source_name: str


# ---------------------------------------------------------------------------
# .lrc parsing
# ---------------------------------------------------------------------------

#: One leading `[mm:ss]`, `[mm:ss.cc]` or `[mm:ss.mmm]` tag. Metadata tags
#: (`[ar:Artist]`, `[ti:Title]`, ...) never match — their first group is not
#: all digits — so they are skipped as a side effect of this pattern rather
#: than needing a separate denylist.
_LRC_TAG = re.compile(r"^\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\](.*)$")


def _timestamp_ms(minutes: str, seconds: str, frac: str | None) -> int:
    """``mm:ss[.f{1,3}]`` to milliseconds. 1 digit = deciseconds, 2 =
    centiseconds, 3 = milliseconds — the three widths both LRC and ASS
    authors actually use in the wild."""
    total = int(minutes) * 60_000 + int(seconds) * 1000
    if frac:
        digits = len(frac)
        value = int(frac)
        if digits == 1:
            total += value * 100
        elif digits == 2:
            total += value * 10
        else:
            total += int(frac[:3].ljust(3, "0"))
    return total


def parse_lrc(content: str, *, source_name: str) -> tuple[LyricLine, ...]:
    """Parse `.lrc` text into timestamped, windowed lines.

    Enhanced-LRC lines with more than one leading tag (the same lyric
    repeated at a verse and a chorus) become one :class:`LyricLine` per
    timestamp, all sharing the same source ``line_no`` — the file has one
    line, the song has two moments it is sung. Untimed lines (plain metadata,
    or a timed tag with no text after it — an instrumental gap) are skipped:
    an instrumental gap is "no active lyric line", the same as mpv reporting
    an empty ``sub-text``, not a line with empty text.
    """
    entries: list[tuple[int, int, str]] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        remainder = raw_line
        timestamps: list[int] = []
        while True:
            match = _LRC_TAG.match(remainder)
            if match is None:
                break
            minutes, seconds, frac, rest = match.groups()
            timestamps.append(_timestamp_ms(minutes, seconds, frac))
            remainder = rest
        if not timestamps:
            continue
        text = remainder.strip()
        if not text:
            continue
        for start_ms in timestamps:
            entries.append((start_ms, line_no, text))

    entries.sort(key=lambda entry: (entry[0], entry[1]))
    lines: list[LyricLine] = []
    for index, (start_ms, line_no, text) in enumerate(entries):
        end_ms = entries[index + 1][0] if index + 1 < len(entries) else None
        if end_ms is not None and end_ms <= start_ms:
            end_ms = None
        lines.append(
            LyricLine(
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                line_no=line_no,
                source_name=source_name,
            )
        )
    return tuple(lines)


# ---------------------------------------------------------------------------
# .ass parsing (karaoke lyrics reusing the subtitle format, per FR-005)
# ---------------------------------------------------------------------------

_ASS_DIALOGUE_PREFIX = "Dialogue:"
#: `H:MM:SS.f{1,3}` — ASS's own Start/End timestamp shape.
_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})[.:](\d{1,3})$")
#: Override blocks (`{\k30}`, `{\pos(0,0)}`, ...) — karaoke timing and
#: styling directives, never lyric text.
_ASS_OVERRIDE_TAG = re.compile(r"\{[^}]*\}")


def _ass_timestamp_ms(stamp: str) -> int | None:
    match = _ASS_TIME.match(stamp.strip())
    if match is None:
        return None
    hours, minutes, seconds, frac = match.groups()
    total = (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
    return total + _timestamp_ms("0", "0", frac)


def _strip_ass_markup(text: str) -> str:
    """Drop override blocks and line-break escapes, collapsing whitespace.

    ``{\\k30}`` karaoke-timing tags and ``{\\pos(...)}`` styling directives
    are not lyric text; ``\\N``/``\\n``/``\\h`` are ASS's own line-break and
    hard-space escapes, not literal backslash-N. What is left is the sung
    text, flattened to one line the same way mpv's own ``sub-text`` already
    arrives as a single displayed line.
    """
    stripped = _ASS_OVERRIDE_TAG.sub("", text)
    for escape in ("\\N", "\\n", "\\h"):
        stripped = stripped.replace(escape, " ")
    return " ".join(stripped.split())


def parse_ass(content: str, *, source_name: str) -> tuple[LyricLine, ...]:
    """Parse the `[Events]` `Dialogue:` lines of an `.ass` file.

    Field layout follows the ASS v4+ ``Format: Layer, Start, End, Style,
    Name, MarginL, MarginR, MarginV, Effect, Text`` line: ``Text`` is field
    index 9 and is the only field allowed to contain commas, so the payload
    is split with ``maxsplit=9`` rather than a naive ``split(",")`` that
    would fragment a comma inside the lyric itself.
    """
    entries: list[tuple[int, int | None, int, str]] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line.startswith(_ASS_DIALOGUE_PREFIX):
            continue
        payload = raw_line[len(_ASS_DIALOGUE_PREFIX):].strip()
        fields = payload.split(",", 9)
        if len(fields) < 10:
            continue
        start_ms = _ass_timestamp_ms(fields[1])
        if start_ms is None:
            continue
        end_ms = _ass_timestamp_ms(fields[2])
        text = _strip_ass_markup(fields[9])
        if not text:
            continue
        entries.append((start_ms, end_ms, line_no, text))

    entries.sort(key=lambda entry: (entry[0], entry[2]))
    return tuple(
        LyricLine(
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            line_no=line_no,
            source_name=source_name,
        )
        for start_ms, end_ms, line_no, text in entries
    )


def parse_lyrics_text(
    content: str, *, source_name: str, suffix: str
) -> tuple[LyricLine, ...]:
    """Dispatch to :func:`parse_lrc`/:func:`parse_ass` by (lowercased,
    dot-stripped) file suffix; anything else is a caller mistake, not a
    silent empty result."""
    normalized = suffix.lower().lstrip(".")
    if normalized == "lrc":
        return parse_lrc(content, source_name=source_name)
    if normalized == "ass":
        return parse_ass(content, source_name=source_name)
    raise ValueError(
        f"unsupported lyrics format {suffix!r}; expected one of "
        f"{sorted(SUPPORTED_SUFFIXES)}"
    )


def parse_lyrics_file(path: str | Path) -> tuple[LyricLine, ...]:
    """Read and parse a `.lrc`/`.ass` file from disk.

    ``utf-8-sig`` tolerates a leading BOM — common in `.ass` files exported
    by Windows subtitle/karaoke tools — without leaking it into the first
    parsed timestamp tag.
    """
    resolved = Path(path)
    content = resolved.read_text(encoding="utf-8-sig")
    return parse_lyrics_text(content, source_name=resolved.name, suffix=resolved.suffix)


# ---------------------------------------------------------------------------
# The channel — same envelope/windowing shape as MpvChannel, anchored on it
# ---------------------------------------------------------------------------


def mpv_anchor_supplier(mpv_channel: Any) -> Callable[[], int | None]:
    """Adapt any object with a public ``media_now()`` into the
    ``get_anchor_ms`` callable :class:`LyricsChannel` needs.

    Takes ``mpv_channel`` structurally (any
    :class:`~katagiri.media_channel.MediaChannel`, not specifically
    ``media_mpv.MpvChannel``) so this module never has to import
    ``media_mpv`` — the anchor comes from whatever channel is playing the
    file the lyrics belong to, mpv being the one FR-005 names.
    """

    def _get_anchor_ms() -> int | None:
        moment = mpv_channel.media_now()
        return None if moment is None else moment.anchor_ms

    return _get_anchor_ms


class LyricsChannel(MediaChannel):
    """`.lrc`/`.ass` lyrics served through mpv's WATCH-mode subtitle pipeline.

    Not a standalone connection: the playhead comes from ``get_anchor_ms``
    (see :func:`mpv_anchor_supplier`), and this class owns only file parsing
    plus the windowing/envelope shape ``media_channel.py`` already defines.
    """

    kind = "lyrics"

    def __init__(
        self,
        *,
        path: str | Path,
        get_anchor_ms: Callable[[], int | None],
        get_media_id: Callable[[], str | None] | None = None,
        context_radius: int = DEFAULT_CONTEXT_RADIUS,
        lines: tuple[LyricLine, ...] | None = None,
    ) -> None:
        """``lines`` lets a caller (tests, or a future bulk-import path)
        supply already-parsed lines without touching disk; by default the
        file at ``path`` is parsed once, here, so a long song's lyrics are
        not re-parsed on every probe."""
        self._path = Path(path)
        self._source_name = self._path.name
        self._lines = lines if lines is not None else parse_lyrics_file(self._path)
        self._starts = [line.start_ms for line in self._lines]
        self._get_anchor_ms = get_anchor_ms
        self._get_media_id = get_media_id or (lambda: self._source_name)
        self._context_radius = context_radius

    def _current_index(self, anchor_ms: int) -> int | None:
        """Index of the line active at ``anchor_ms`` (the last line whose
        start is at or before the anchor), or ``None`` before the first
        line's start — the file-derived equivalent of mpv reporting no
        current ``sub-text``."""
        if not self._starts:
            return None
        index = bisect_right(self._starts, anchor_ms) - 1
        return None if index < 0 else index

    def _line_locator(self, line: LyricLine) -> str:
        return f"lyrics:{self._source_name}:{line.line_no}"

    def _line_detail(self, line: LyricLine) -> Mapping[str, Any]:
        return {
            "source_file": self._source_name,
            "line_no": line.line_no,
            "start_ms": line.start_ms,
        }

    # -- MediaChannel interface ---------------------------------------------

    def _probe_now(self) -> RawMoment | None:
        anchor_ms = self._get_anchor_ms()
        if anchor_ms is None:
            return None
        media_id = self._get_media_id()
        index = self._current_index(anchor_ms)
        if index is None:
            return RawMoment(
                media_id=media_id,
                anchor_ms=anchor_ms,
                displayed_text=None,
                locator=f"lyrics:{self._source_name}",
            )
        line = self._lines[index]
        return RawMoment(
            media_id=media_id,
            anchor_ms=anchor_ms,
            displayed_text=line.text,
            locator=self._line_locator(line),
            detail=self._line_detail(line),
        )

    def _probe_context(self, **kwargs: Any) -> RawContext | None:
        anchor_ms = self._get_anchor_ms()
        if anchor_ms is None:
            return None
        media_id = self._get_media_id()
        index = self._current_index(anchor_ms)
        if index is None:
            return RawContext(media_id=media_id, anchor_ms=anchor_ms, lines=())

        radius = kwargs.get("radius", self._context_radius)
        if radius < 0:
            raise ValueError(f"radius must not be negative; got {radius}")
        lo = max(0, index - radius)
        hi = min(len(self._lines), index + radius + 1)
        window = tuple(
            RawLine(
                text=line.text,
                start_ms=line.start_ms,
                end_ms=line.end_ms,
                locator=self._line_locator(line),
                detail=self._line_detail(line),
            )
            for line in self._lines[lo:hi]
        )
        return RawContext(media_id=media_id, anchor_ms=anchor_ms, lines=window)


__all__ = [
    "DEFAULT_CONTEXT_RADIUS",
    "SUPPORTED_SUFFIXES",
    "LyricLine",
    "LyricsChannel",
    "mpv_anchor_supplier",
    "parse_ass",
    "parse_lrc",
    "parse_lyrics_file",
    "parse_lyrics_text",
]
