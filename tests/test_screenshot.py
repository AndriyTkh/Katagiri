"""E-T008: the screenshot-question tool.

No live mpv, no live display anywhere in this file: :class:`FakeMpvPipe`
(mirrors ``tests/test_media_mpv.py``'s fixture) drives a real
:class:`~katagiri.media_mpv.MpvChannel` over a scripted pipe, and every
``capture`` argument to :func:`~katagiri.screenshot_tool.take_screenshot` is a
plain Python stub the test controls — never
:func:`~katagiri.screenshot_tool.default_mpv_capture`, never a real mpv IPC
round trip, never an OS screenshot call.

The load-bearing scenario is the hostile-title case (FR-004's acceptance
scenario 1, spec.md): a media title containing a path-traversal or shell
payload must never influence the filename or path a screenshot lands at, and
must never reach the ``capture`` callable as anything other than a
:class:`pathlib.Path` that is already confined to the scratch root.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from katagiri.envelope import Envelope
from katagiri.media_channel import MediaChannel
from katagiri.media_mpv import MpvChannel
from katagiri.mpv_seek_logger import basename as _mpv_basename
from katagiri.screenshot_tool import (
    ScreenshotArtifact,
    ScreenshotCaptureError,
    ScreenshotConfinementError,
    ScreenshotNotFoundError,
    clear_artifact_registry,
    get_artifact,
    read_screenshot_bytes,
    take_screenshot,
)

NOW_TS = "2026-08-21T12:00:00Z"


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_artifact_registry()
    yield
    clear_artifact_registry()


# ---------------------------------------------------------------------------
# The fake mpv pipe peer (mirrors tests/test_media_mpv.py's FakeMpvPipe)
# ---------------------------------------------------------------------------


class FakeMpvPipe:
    def __init__(self, properties: dict[str, Any]) -> None:
        self.properties = properties
        self._pending: deque[bytes] = deque()
        self.closed = False

    def send(self, line: bytes) -> None:
        request = json.loads(line.decode("utf-8"))
        request_id = request["request_id"]
        name = request["command"][1]
        value = self.properties.get(name)
        reply = {"request_id": request_id, "error": "success", "data": value}
        self._pending.append(json.dumps(reply).encode("utf-8") + b"\n")

    def readline(self) -> bytes:
        if self._pending:
            return self._pending.popleft()
        return b""

    def close(self) -> None:
        self.closed = True


def _channel(properties: dict[str, Any]) -> MpvChannel:
    pipe = FakeMpvPipe(properties)
    return MpvChannel(connect=lambda: pipe)


def _idle_channel() -> MpvChannel:
    def _fail() -> FakeMpvPipe:
        raise OSError("mpv IPC pipe unavailable")

    return MpvChannel(connect=_fail)


PLAYING_PROPS: dict[str, Any] = {
    "time-pos": 42.0,
    "path": r"C:\Users\me\Media\Show\ep01.mkv",
    "media-title": "Show - ep01",
    "sub-text": "a normal subtitle line",
    "sub-start": 41.0,
    "sub-end": 43.0,
}

#: Payloads FR-004 specifically names: a traversal sequence and a shell
#: metacharacter string, both delivered through the media *title* (the field
#: mpv reports and this module must never let choose a path).
HOSTILE_TITLES = [
    "../../../etc/passwd",
    "..\\..\\..\\Windows\\System32\\config",
    "\"; rm -rf / #",
    "$(rm -rf /)",
]


def _hostile_props(title: str) -> dict[str, Any]:
    return {
        "time-pos": 99.0,
        "path": r"C:\Users\me\Media\Show\ep02.mkv",
        "media-title": title,
        "sub-text": "line under a hostile title",
        "sub-start": 98.0,
        "sub-end": 100.0,
    }


class RawTitleChannel(MediaChannel):
    """A minimal :class:`~katagiri.media_channel.MediaChannel` that reports a
    title *verbatim*, with none of ``MpvChannel``'s own basename reduction.

    ``take_screenshot`` must stay confined against this channel too — its
    confinement guarantee cannot depend on any particular channel already
    having sanitised what it reports. ``media_now``'s envelope enforcement
    (the base class's, not overridable) still wraps the title; what this
    class controls is only what raw text goes *into* that envelope.
    """

    kind = "test-raw"

    def __init__(self, *, media_id: str, anchor_ms: int, title: str) -> None:
        self._media_id = media_id
        self._anchor_ms = anchor_ms
        self._title = title

    def _probe_now(self):  # type: ignore[override]
        from katagiri.media_channel import RawMoment

        return RawMoment(
            media_id=self._media_id,
            anchor_ms=self._anchor_ms,
            displayed_text=None,
            title=self._title,
            locator=f"test:{self._media_id}",
        )

    def _probe_context(self, **kwargs: Any):  # type: ignore[override]
        return None


class RecordingCapture:
    """A ``Capture`` stub that records every path it was asked to write and
    creates an (empty) file there, so downstream ``is_file()``/read checks
    behave like a real capture happened."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, target: Path) -> None:
        self.calls.append(target)
        target.write_bytes(b"\x89PNG\r\n\x1a\n-fake-frame-")


class FailingCapture:
    def __call__(self, target: Path) -> None:
        raise ScreenshotCaptureError("simulated capture failure")


# ---------------------------------------------------------------------------
# take_screenshot: happy path
# ---------------------------------------------------------------------------


def test_take_screenshot_writes_into_confined_root_with_server_generated_name(tmp_path):
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()

    artifact = take_screenshot(
        channel, capture=capture, scratch_root=tmp_path, now=lambda: NOW_TS
    )

    assert isinstance(artifact, ScreenshotArtifact)
    assert len(capture.calls) == 1
    target = capture.calls[0]
    # Confined: the written path is inside the scratch root.
    assert target.resolve().parent == tmp_path.resolve()
    assert target == artifact.path
    assert target.is_file()
    # Server-generated: the name has nothing to do with the title/media id.
    assert "ep01" not in target.name
    assert "Show" not in target.name
    assert target.suffix == ".png"
    # Metadata carried through, title still enveloped (untrusted-data contract).
    assert artifact.media_id == "ep01.mkv"
    assert artifact.anchor_ms == 42_000
    assert isinstance(artifact.title, Envelope)
    assert artifact.title.text == "Show - ep01"
    assert artifact.created_ts == NOW_TS


def test_take_screenshot_registers_artifact_for_later_lookup(tmp_path):
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    looked_up = get_artifact(artifact.screenshot_id)
    assert looked_up == artifact


def test_take_screenshot_returns_none_when_nothing_playing(tmp_path):
    channel = _idle_channel()
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    assert artifact is None
    assert capture.calls == []  # never even attempted a capture


def test_take_screenshot_propagates_capture_failure_without_leaving_a_registered_artifact(
    tmp_path,
):
    channel = _channel(PLAYING_PROPS)

    with pytest.raises(ScreenshotCaptureError):
        take_screenshot(channel, capture=FailingCapture(), scratch_root=tmp_path)

    # No artifact was filed for a capture that never succeeded.
    assert list(tmp_path.iterdir()) == []


def test_two_screenshots_get_two_distinct_server_generated_names(tmp_path):
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()

    first = take_screenshot(channel, capture=capture, scratch_root=tmp_path)
    second = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    assert first.screenshot_id != second.screenshot_id
    assert first.path != second.path


# ---------------------------------------------------------------------------
# The hostile-title scenario (FR-004 acceptance scenario 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile_title", HOSTILE_TITLES)
def test_hostile_media_title_never_reaches_the_filesystem_path(tmp_path, hostile_title):
    channel = _channel(_hostile_props(hostile_title))
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    assert artifact is not None
    # MpvChannel already reduces a title to its basename before this module
    # ever sees it (media_mpv.py's own privacy boundary, reused from
    # mpv_seek_logger.basename) — so the envelope carries that reduced form,
    # not the raw payload verbatim. This module must not rely on that upstream
    # reduction either way: the real assertion below is that the *path*
    # capture() receives never carries the payload, reduced or not.
    assert artifact.title.text == _mpv_basename(hostile_title)

    # ...but the path capture() was actually asked to write is confined and
    # carries none of the payload.
    assert len(capture.calls) == 1
    target = capture.calls[0]
    resolved_root = tmp_path.resolve()
    assert target.resolve().parent == resolved_root
    assert target.resolve().is_relative_to(resolved_root)

    target_str = str(target)
    assert ".." not in target_str
    assert "etc" not in target_str
    assert "passwd" not in target_str
    assert "rm -rf" not in target_str
    assert "System32" not in target_str
    assert "$(" not in target_str
    assert ";" not in target_str

    # Nothing outside the scratch root was created or touched.
    for path in tmp_path.rglob("*"):
        assert path.resolve().is_relative_to(resolved_root)


@pytest.mark.parametrize("hostile_title", HOSTILE_TITLES)
def test_verbatim_hostile_title_from_an_unsanitizing_channel_stays_out_of_the_path(
    tmp_path, hostile_title
):
    """The strict version of the hostile-title scenario: a channel that does
    *no* upstream sanitisation at all (unlike ``MpvChannel``, which already
    reduces to a basename) still must never let the payload reach a
    filesystem path or the ``capture`` callable as anything but a confined
    ``Path``.
    """
    channel = RawTitleChannel(
        media_id="ep02.mkv", anchor_ms=99_000, title=hostile_title
    )
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    assert artifact is not None
    # The raw payload really did arrive intact, as untrusted *data*...
    assert artifact.title.text == hostile_title
    assert artifact.title.provenance.source == "media"

    # ...but capture() only ever saw a confined Path with a server-generated
    # name — never a string built from the title, and never anything handed
    # to a shell (capture() receives a pathlib.Path, not a command line).
    assert len(capture.calls) == 1
    target = capture.calls[0]
    resolved_root = tmp_path.resolve()
    assert isinstance(target, Path)
    assert target.resolve().is_relative_to(resolved_root)
    assert target.resolve().parent == resolved_root

    target_str = str(target)
    assert hostile_title not in target_str
    assert ".." not in target_str
    assert ";" not in target_str
    assert "$(" not in target_str
    assert "etc" not in target_str
    assert "passwd" not in target_str
    assert "System32" not in target_str

    # And the artifact's own screenshot_id (the only thing a future MCP tool
    # argument would carry) is likewise payload-free and round-trips through
    # the same confinement check the read path applies.
    assert hostile_title not in artifact.screenshot_id
    data = read_screenshot_bytes(artifact.screenshot_id, scratch_root=tmp_path)
    assert data == target.read_bytes()


def test_hostile_media_id_from_path_also_stays_out_of_the_filename(tmp_path):
    """Even the *path* mpv reports (basename-reduced upstream, but still
    attacker-influenced) must never seed the screenshot filename — only
    ``take_screenshot``'s own uuid4 call may."""
    props = dict(PLAYING_PROPS)
    props["path"] = "C:\\Users\\me\\Media\\..\\..\\evil; rm -rf ~.mkv"
    channel = _channel(props)
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    target = capture.calls[0]
    assert "evil" not in target.name
    assert "rm" not in target.name
    assert ".." not in target.name
    assert target.resolve().parent == tmp_path.resolve()


# ---------------------------------------------------------------------------
# read_screenshot_bytes: the agent read path
# ---------------------------------------------------------------------------


def test_read_screenshot_bytes_returns_exactly_what_was_captured(tmp_path):
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()
    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)

    data = read_screenshot_bytes(artifact.screenshot_id, scratch_root=tmp_path)

    assert data == artifact.path.read_bytes()
    assert data.startswith(b"\x89PNG")


def test_read_screenshot_bytes_missing_file_raises_not_found(tmp_path):
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()
    artifact = take_screenshot(channel, capture=capture, scratch_root=tmp_path)
    artifact.path.unlink()

    with pytest.raises(ScreenshotNotFoundError):
        read_screenshot_bytes(artifact.screenshot_id, scratch_root=tmp_path)


@pytest.mark.parametrize(
    "hostile_id",
    [
        "../../../etc/passwd",
        "..\\..\\secrets.png",
        "/etc/passwd",
        "C:\\Windows\\System32\\config.png",
        "foo/../../bar.png",
        "; rm -rf ~.png",
        "no_extension_at_all",
        "",
    ],
)
def test_read_screenshot_bytes_refuses_a_hostile_or_malformed_id(tmp_path, hostile_id):
    with pytest.raises(ScreenshotConfinementError):
        read_screenshot_bytes(hostile_id, scratch_root=tmp_path)


def test_read_screenshot_bytes_cannot_escape_root_even_with_a_well_formed_looking_id(
    tmp_path,
):
    """A sibling file placed just outside the scratch root must stay
    unreachable even if an id happens to look well-formed after traversal is
    stripped — belt-and-suspenders on top of the character-set check."""
    outside = tmp_path.parent / "outside-secret.png"
    outside.write_bytes(b"do-not-read-me")
    try:
        # This id is rejected by the character-set check before it can ever
        # be joined with the root, so the file is never opened.
        with pytest.raises(ScreenshotConfinementError):
            read_screenshot_bytes("..%2Foutside-secret.png", scratch_root=tmp_path)
    finally:
        outside.unlink()


# ---------------------------------------------------------------------------
# Scratch root creation
# ---------------------------------------------------------------------------


def test_scratch_root_is_created_if_missing(tmp_path):
    root = tmp_path / "does" / "not" / "exist" / "yet"
    assert not root.exists()
    channel = _channel(PLAYING_PROPS)
    capture = RecordingCapture()

    artifact = take_screenshot(channel, capture=capture, scratch_root=root)

    assert root.is_dir()
    assert artifact.path.resolve().parent == root.resolve()
