"""E-T008: the screenshot-question tool ("what does that sign say?").

FR-004 (specs/004-phase-e-media-overlay/spec.md): drive mpv's
``screenshot-to-file`` into a **confined scratch root** with a
**server-generated filename**, then hand the agent a read path into that same
root. The requirement exists because the input this tool anchors on — a media
title, a played file's name — is attacker-controlled: a malicious file or
subtitle name can carry ``../../`` or shell metacharacters, and nothing here
may let that text choose, or even influence, a filesystem path.

Two rules make that true structurally, not by convention:

1. **The filename is never derived from anything the media reports.**
   :func:`_generate_filename` calls :func:`uuid.uuid4` and nothing else — no
   title, no media id, no timestamp string built from untrusted input. The
   current moment (title, media id, anchor) is still captured and carried on
   :class:`ScreenshotArtifact`, wrapped in the same untrusted-data envelope
   :mod:`katagiri.media_channel` already applies (``moment.title`` arrives
   pre-enveloped from :meth:`~katagiri.media_channel.MediaChannel.media_now`)
   — it is available to show the learner *what was playing*, it is simply
   never consulted when deciding *where the file goes*.
2. **Every path this module touches is re-checked against the scratch root
   before use.** :func:`_confine` resolves both the root and the candidate
   path and requires the candidate to be :meth:`Path.relative_to` the root.
   This runs even though every candidate here was built from a UUID this
   module just generated — belt-and-suspenders is cheap, and it is what makes
   :class:`ScreenshotConfinementError` a real, testable refusal rather than an
   invariant nothing ever checks. :func:`read_screenshot_bytes` additionally
   restricts a caller-supplied ``screenshot_id`` to a closed character set
   (:data:`_ID_PATTERN`) before it ever touches a path, because that id — unlike
   the filename this module generates internally — is the one string an agent
   or a future MCP argument could hand back verbatim.

Capture is pluggable on purpose (:data:`Capture`). Actually driving mpv's
``screenshot-to-file`` IPC command or shelling out to an OS screenshot library
needs a live mpv instance or a live display — neither is available to a test
suite, and neither belongs coupled into the confinement logic this module
exists to get right. :func:`default_mpv_capture` is a real, working default
(reusing :mod:`katagiri.mpv_seek_logger`'s named-pipe transport, the one mpv
IPC implementation this codebase has), but every test in
``tests/test_screenshot.py`` injects its own ``capture`` stub instead of
touching mpv or a display.

Read path
---------
:func:`take_screenshot` returns a :class:`ScreenshotArtifact` and also files it
under its ``screenshot_id`` in an in-process registry (:func:`get_artifact`),
so a later caller that only has the id — the shape an MCP tool argument would
actually carry, since neither ``Path`` nor
:class:`~katagiri.envelope.Envelope` survive a JSON-RPC round trip — can still
look up the moment's metadata. :func:`read_screenshot_bytes` is the actual
agent-facing read: confined, id-validated, and the only function in this
module that opens the image file.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

from katagiri.config import Config, get_config
from katagiri.envelope import Envelope
from katagiri.events import utc_now_stamp
from katagiri.media_channel import MediaChannel
from katagiri.mpv_seek_logger import PIPE_PATH, Transport, connect_pipe

#: Extension every generated screenshot filename carries. mpv's
#: ``screenshot-to-file`` picks its own encoder from the target's extension;
#: png is mpv's own default and keeps this module dependency-free.
_SCREENSHOT_EXT: Final = ".png"

#: Closed character set for a caller-supplied ``screenshot_id`` passed back
#: into :func:`read_screenshot_bytes`. No path separator, no ``.``-only
#: segment, nothing that a filesystem or a shell could treat as structure —
#: matches the shape :func:`_generate_filename` actually produces (a bare
#: uuid4 hex plus extension) and rejects everything else, including a
#: traversal payload smuggled in as an "id".
_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}$")

#: Lines of mpv IPC traffic :func:`default_mpv_capture` reads before giving up
#: on a reply. Mirrors ``mpv_seek_logger.MAX_LINES_PER_REQUEST`` — this module
#: cannot import that constant without importing the whole polling module, and
#: a screenshot request is the same "one reply, eventually" shape as a
#: ``get_property`` call.
_MAX_CAPTURE_REPLY_LINES: Final = 500


class ScreenshotError(RuntimeError):
    """Base for every refusal this module raises."""


class ScreenshotCaptureError(ScreenshotError):
    """The capture mechanism (mpv IPC, an OS screenshot call, ...) failed."""


class ScreenshotConfinementError(ScreenshotError):
    """A path (or a caller-supplied id) fell outside the confined scratch root.

    This is the refusal :func:`_confine` and the id check in
    :func:`read_screenshot_bytes` raise; it is what a hostile media title or a
    hand-crafted ``screenshot_id`` gets instead of a filesystem read.
    """


class ScreenshotNotFoundError(ScreenshotError):
    """A well-formed, confined id has no screenshot file behind it."""


@dataclass(frozen=True, slots=True)
class ScreenshotArtifact:
    """One captured frame: a confined path plus the moment it was anchored to.

    ``title``/``media_id``/``anchor_ms`` come straight from
    :meth:`~katagiri.media_channel.MediaChannel.media_now` — ``title`` is
    already an :class:`~katagiri.envelope.Envelope` (or ``None``); nothing
    here re-wraps or re-derives it. ``path`` is always a descendant of the
    scratch root this artifact was captured into: every constructor that
    builds one is inside this module and runs it through :func:`_confine`
    first.
    """

    screenshot_id: str
    path: Path
    media_id: str | None
    anchor_ms: int | None
    title: Envelope | None
    created_ts: str

    def read_bytes(self) -> bytes:
        """The raw image bytes. Prefer :func:`read_screenshot_bytes` when only
        an id (not this object) is available — this method assumes ``path``
        is already confined, which is true for every artifact this module
        hands out, but is not itself a confinement check."""
        return self.path.read_bytes()


#: A capture mechanism: given the exact confined target path to write to,
#: produce the image file there (or raise :class:`ScreenshotCaptureError`).
#: Never receives anything derived from an untrusted title — only the path
#: this module already generated and confined.
Capture = Callable[[Path], None]

#: In-process index of artifacts this module has handed out, keyed by
#: ``screenshot_id``. Not persisted: a restart loses it exactly the way a
#: restarted mpv loses its own screenshot history, and nothing here treats it
#: as durable storage.
_ARTIFACTS: dict[str, ScreenshotArtifact] = {}


def _prepare_scratch_root(scratch_root: Path | str | None, *, config: Config | None = None) -> Path:
    """The confined root, created if absent, resolved to an absolute path.

    ``scratch_root`` overrides ``config.screenshot_scratch_root`` (T004) when
    given — tests use this to avoid touching ``%LOCALAPPDATA%``; a real caller
    normally leaves it unset.
    """
    if scratch_root is not None:
        root = Path(scratch_root)
    else:
        root = (config or get_config()).screenshot_scratch_root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _confine(candidate: Path, root: Path) -> Path:
    """``candidate`` resolved and verified to be ``root`` or a descendant.

    Raises :class:`ScreenshotConfinementError` rather than silently
    clamping — a path that would land outside the root is a bug or an attack,
    never something to "fix" by rewriting it into place.
    """
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ScreenshotConfinementError(
            "Refusing to use a path outside the confined screenshot scratch "
            "root."
        ) from None
    return resolved


def _generate_filename(ext: str = _SCREENSHOT_EXT) -> str:
    """A server-generated filename. See the module docstring, rule 1: this is
    the *only* place a screenshot's on-disk name is decided, and it never
    looks at a title, a path, or anything else the media reported."""
    return f"{uuid.uuid4().hex}{ext}"


def _validate_screenshot_id(screenshot_id: str) -> None:
    if not isinstance(screenshot_id, str) or not _ID_PATTERN.fullmatch(screenshot_id):
        raise ScreenshotConfinementError(
            "Refusing a screenshot id that is not a bare, server-generated "
            "filename (no path separators, no '..', no drive letter)."
        )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def default_mpv_capture(pipe_path: str = PIPE_PATH) -> Capture:
    """A working default :data:`Capture`: mpv's ``screenshot-to-file`` over
    the same named-pipe IPC :mod:`katagiri.mpv_seek_logger` already speaks.

    Opens its own short-lived connection per call rather than sharing one with
    a :class:`~katagiri.media_mpv.MpvChannel` — a screenshot request is rare
    enough that connection reuse is not worth coupling this module to that
    channel's client lifecycle. Never given a caller-controlled string: the
    only path it ever sends is the ``target`` this module already ran through
    :func:`_confine`.
    """

    def _capture(target: Path) -> None:
        try:
            transport: Transport = connect_pipe(pipe_path)
        except OSError as exc:
            raise ScreenshotCaptureError(
                f"mpv IPC pipe unavailable for screenshot capture: {exc}"
            ) from exc
        try:
            _mpv_screenshot_to_file(transport, target)
        finally:
            transport.close()

    return _capture


def _mpv_screenshot_to_file(transport: Transport, target: Path) -> None:
    """One request/reply round trip asking mpv to write ``target``.

    A minimal, self-contained JSON-IPC exchange — mirroring
    ``mpv_seek_logger.MpvClient``'s framing (request id, ``event`` messages
    set aside, a matching ``request_id`` is the reply) without depending on
    that class's private internals, since it only exposes ``get_property``.
    ``target`` is passed as one argument in a JSON array; this is mpv's IPC
    protocol, not a shell, so there is no quoting for a metacharacter in
    ``target`` (already confined, never attacker-influenced) to escape.
    """
    request_id = 1
    payload = {
        "command": ["screenshot-to-file", str(target), "video"],
        "request_id": request_id,
    }
    line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        transport.send(line)
    except OSError as exc:
        raise ScreenshotCaptureError(f"Writing to the mpv pipe failed: {exc}") from exc

    for _ in range(_MAX_CAPTURE_REPLY_LINES):
        try:
            raw = transport.readline()
        except OSError as exc:
            raise ScreenshotCaptureError(f"Reading from the mpv pipe failed: {exc}") from exc
        if not raw:
            raise ScreenshotCaptureError("mpv closed the IPC stream during screenshot capture.")
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            message: Any = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or "event" in message:
            continue
        if message.get("request_id") != request_id:
            continue
        if message.get("error") == "success":
            return
        raise ScreenshotCaptureError(
            f"mpv refused screenshot-to-file: {message.get('error')!r}"
        )

    raise ScreenshotCaptureError(
        f"No reply to screenshot-to-file within {_MAX_CAPTURE_REPLY_LINES} "
        "lines of IPC traffic."
    )


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def take_screenshot(
    channel: MediaChannel,
    *,
    capture: Capture,
    scratch_root: Path | str | None = None,
    config: Config | None = None,
    now: Callable[[], str] = utc_now_stamp,
) -> ScreenshotArtifact | None:
    """Capture the current frame of ``channel``, confined and server-named.

    ``channel`` supplies the anchor via
    :meth:`~katagiri.media_channel.MediaChannel.media_now` — the mpv-anchored
    moment FR-004 requires (any :class:`~katagiri.media_channel.MediaChannel`
    works structurally; mpv is what actually has a screenshot command).
    Returns ``None``, taking no screenshot, when the channel has nothing
    playing right now — mirrors ``media_now``'s own "no active media" contract
    (spec.md US1 acceptance scenario 2) rather than inventing a second error
    shape for the same condition.

    The moment's ``title`` (attacker-controlled, already an
    :class:`~katagiri.envelope.Envelope`) is carried on the returned artifact
    for display. It is read nowhere in this function's path-building —
    :func:`_generate_filename` is the only source of the on-disk name.
    """
    moment = channel.media_now(now=now)
    if moment is None:
        return None

    root = _prepare_scratch_root(scratch_root, config=config)
    screenshot_id = _generate_filename()
    target = _confine(root / screenshot_id, root)

    capture(target)

    artifact = ScreenshotArtifact(
        screenshot_id=screenshot_id,
        path=target,
        media_id=moment.media_id,
        anchor_ms=moment.anchor_ms,
        title=moment.title,
        created_ts=moment.updated_ts,
    )
    _ARTIFACTS[screenshot_id] = artifact
    return artifact


def get_artifact(screenshot_id: str) -> ScreenshotArtifact | None:
    """The artifact :func:`take_screenshot` filed under ``screenshot_id``, if
    this process has one. ``None`` covers both "never taken" and "process
    restarted since" — indistinguishable, and both mean the caller has no
    metadata to show."""
    return _ARTIFACTS.get(screenshot_id)


def read_screenshot_bytes(
    screenshot_id: str,
    *,
    scratch_root: Path | str | None = None,
    config: Config | None = None,
) -> bytes:
    """The agent read path: raw image bytes for ``screenshot_id``, confined.

    ``screenshot_id`` is validated against :data:`_ID_PATTERN` before it ever
    reaches a path join, then the joined path is re-verified with
    :func:`_confine` — the same double check every candidate path in this
    module gets, applied here to the one id that could plausibly arrive from
    outside this process (an MCP tool argument, once registered) rather than
    from :func:`_generate_filename` itself.
    """
    _validate_screenshot_id(screenshot_id)
    root = _prepare_scratch_root(scratch_root, config=config)
    target = _confine(root / screenshot_id, root)
    if not target.is_file():
        raise ScreenshotNotFoundError(
            f"No screenshot file for id {screenshot_id!r} under the confined "
            "scratch root."
        )
    return target.read_bytes()


def clear_artifact_registry() -> None:
    """Forget every in-process artifact. Tests only — mirrors
    ``envelope.reset_default_gate``'s "tests only" module-state reset."""
    _ARTIFACTS.clear()


__all__ = [
    "Capture",
    "ScreenshotArtifact",
    "ScreenshotCaptureError",
    "ScreenshotConfinementError",
    "ScreenshotError",
    "ScreenshotNotFoundError",
    "clear_artifact_registry",
    "default_mpv_capture",
    "get_artifact",
    "read_screenshot_bytes",
    "take_screenshot",
]
