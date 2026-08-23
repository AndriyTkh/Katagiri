# Plan: Live playback-state anchor for the asbplayer channel (F-05)

## Context

Katagiri's asbplayer media channel has no live playhead — anchors come from the
last mining/copy event or a manual `manual_anchor_ms` override (upstream issue
#1087, decisions-ledger F-05). The F-05 trigger ("manual anchors prove annoying
in practice") has fired. We control the full stack locally: the asbplayer
source checkout at `C:/ProjectsC/RandomPr/asbplayer` (extension + Go
WebSocket-server bridge in `scripts/web-socket-server`) and Katagiri at
`C:/ProjectsC/RandomPr/Katagiri`. Add a `get-playback-state` command through
all three layers so `media_now`/`media_context` answer "what is he saying right
now?" with a real live anchor, from any MCP client.

The extension already has everything needed: `binding.ts` exposes
`currentTimeMs` (reads `video.currentTime * 1000`), and the content-script
message switch in `binding.ts` (~line 878) already answers `request-subtitles`
and `request-current-subtitle` the same way this feature needs.

## Spec

There is no spec-kit spec for this; the binding authorities are
`specs/004-phase-e-media-overlay/spec.md` (envelope + anchor semantics, F-05)
and the frozen protocol contract below.

## Global Constraints

1. **No git commits, no pushes, in either repo.** Working-tree edits only
   (Katagiri conservative agent profile). Report proposed commit splits in the
   final report instead.
2. **Frozen protocol contract** (both lanes implement exactly this):
   - WS command (server → extension):
     `{"command": "get-playback-state", "messageId": "<uuid>", "body": {"mediaId": "<optional>"}}`
   - Extension response:
     `{"command": "response", "messageId": "<same>", "body": {"playbackState": {"mediaId": "<id>", "timestampMs": <int>, "playing": <bool>} | null}}`
     `playbackState` is `null` when no media matches, the target is not a
     streaming video element (local asbplayer-webapp media is out of scope for
     v1 and returns `null`), or the tab did not answer.
   - Bridge HTTP endpoint: `GET /asbplayer/playback-state` (optional
     `?mediaId=...`), replying with the extension response's `body` JSON
     verbatim (same relay pattern as `GET /asbplayer/subtitles`).
   - Units: **integer milliseconds**, field name `timestampMs` (matches
     `get-subtitles` ms units; `seek-timestamp` uses seconds — do not copy it).
3. **asbplayer changes must be upstream-PR quality** (F-05 will be PR'd to
   issue #1087): follow the existing `get-bound-media`/`get-subtitles` code
   patterns exactly — same file layout, naming style, response plumbing, and
   the external-api.md doc gets a new section in the same format.
4. **Katagiri conventions unchanged**: channel returns raw text/fields; the
   untrusted-data envelope is applied only by the `MediaChannel` base class.
   Never log or persist media titles as filesystem paths. Loopback only.
5. Anchor precedence in the Katagiri channel (design ruling, see ledger):
   explicit `manual_anchor_ms` kwarg > live playback state > persistent
   `set_manual_anchor` override > event-log derivation. Live anchor gets
   `source="live"`. A failed/absent playback-state probe (unreachable, HTTP
   404/5xx from an older bridge, malformed reply) must degrade silently to the
   pre-existing precedence chain — never crash the probe.
6. Do not modify `.specify/`. Do not revert or rework unrelated uncommitted
   changes already present in either working tree.
7. Tests: Katagiri lane runs
   `uv run pytest tests/test_media_asbplayer.py tests/test_everify.py -n auto --dist loadgroup`
   green before reporting. asbplayer lane must pass a TypeScript typecheck of
   the touched workspaces and `go build ./...` in `scripts/web-socket-server`.

## Task 1: asbplayer extension + Go bridge — `get-playback-state`

Repo: `C:/ProjectsC/RandomPr/asbplayer` (branch `main`, clean tree; edit in
place, no commits).

### 1a. Common WS client types + dispatch

File `common/web-socket-client/web-socket-client.ts`:
- Add `GetPlaybackStateCommand` interface (`command: 'get-playback-state'`,
  `messageId: string`, `body: { mediaId?: string }`) next to
  `GetSubtitlesCommand` (~line 70).
- Add `PlaybackState` type: `{ mediaId: string; timestampMs: number; playing: boolean }`.
- Add `GetPlaybackStateResponseBody` (`{ playbackState: PlaybackState | null }`).
- Add optional handler property
  `onGetPlaybackState?: (mediaId: string | undefined) => Promise<PlaybackState | null>;`
  next to `onGetSubtitles` (~line 101), reset it in the same place existing
  handlers are reset (~line 277).
- Add an `else if (payload.command === 'get-playback-state')` branch to the
  message switch (~line 193 pattern): call the handler, send
  `{command: 'response', messageId, body: {playbackState: <result ?? null>}}`.
  Mirror the `get-subtitles` branch exactly, including the
  handler-undefined guard.

### 1b. Content-script playback-state answer

File `common/src/message.ts`:
- Add `RequestPlaybackStateMessage extends Message` with
  `command: 'request-playback-state'` (next to `RequestSubtitlesMessage`,
  ~line 442).
- Add `RequestPlaybackStateResponse` interface
  (`{ timestampMs: number; playing: boolean }`) next to
  `RequestSubtitlesResponse` (~line 775).

File `extension/src/services/binding.ts` (message switch, ~line 878):
- Add `case 'request-playback-state':` returning
  `sendResponse({ timestampMs: this.currentTimeMs, playing: !this.video.paused })`.
  Follow the `request-subtitles` case's shape. Use the existing
  `currentTimeMs` getter (~line 331); do not duplicate its logic.

### 1c. Background wiring

File `extension/src/services/web-socket-client-binding.ts`:
- Add `requestPlaybackStateFromVideoElement(tabId, src)` helper mirroring
  `requestSubtitlesFromVideoElement` (~line 166) but sending
  `{ command: 'request-playback-state' }` and returning
  `RequestPlaybackStateResponse | undefined` (catch send failures → undefined,
  same as the subtitles helper).
- Wire `client.onGetPlaybackState = async (mediaId) => { ... }` next to
  `onGetSubtitles` (~line 326): resolve targets via
  `resolveMediaTargets(tabRegistry, mediaId)`, take the first target; if it is
  a video-element target, request playback state from it and return
  `{ mediaId: streamingMediaId(videoElement.id, videoElement.src), timestampMs, playing }`;
  any other case (no target, asbplayer-webapp target, no answer) returns
  `null`.
- Reuse the exact same id derivation (`streamingMediaId`) used by
  `onGetBoundMedia` so ids match across commands.

### 1d. Go bridge endpoint

Files in `scripts/web-socket-server` (read `main.go` first; follow how
`/asbplayer/subtitles` relays `get-subtitles`):
- Add `GET /asbplayer/playback-state` with optional `mediaId` query param.
  Build the `get-playback-state` WS command with a fresh messageId, relay to
  the connected extension client, wait with the same timeout machinery the
  other GET endpoints use, and write the response `body` JSON verbatim with
  the same status-code behavior (5xx when no extension answers, mirroring
  `/asbplayer/subtitles`).

### 1e. Docs + validation

- `docs/docs/reference/external-api.md`: add a `### get-playback-state`
  section (request/response examples in the existing house format, with the
  "extension v1.20.0+" note style) and add the endpoint to the "HTTP-based
  API" list.
- Validation: run the repo's TypeScript typecheck for the touched packages
  (discover the script in `package.json` / workspace configs — e.g.
  `yarn` + the extension/common typecheck or build scripts) and
  `go build ./...` inside `scripts/web-socket-server`. Both must pass.
  If a full extension build is quick, run it; do not fight unrelated
  pre-existing build issues — report them instead.

## Task 2: Katagiri channel — live anchor from playback state

Repo: `C:/ProjectsC/RandomPr/Katagiri` (branch `phase-e`; tree already has
unrelated uncommitted edits — leave them intact; no commits).

File `src/katagiri/media_asbplayer.py`:
- Add `REQUEST_GET_PLAYBACK_STATE: Final = "get-playback-state"`, include it
  in `SUPPORTED_COMMANDS`, map it to `"/asbplayer/playback-state"` in
  `_COMMAND_PATHS`. Bump `PROTOCOL_SURFACE_VERSION` to 3 and extend its
  docstring/comment.
- Add frozen dataclass `PlaybackState` (`media_id: str`, `timestamp_ms: int`,
  `playing: bool`).
- Add `get_playback_state(client) -> PlaybackState | None`, following
  `get_bound_media`'s validation style: reply `{"playbackState": null}` or
  `{"error": ...}` → `None`; missing `playbackState` key outright →
  `AsbplayerProtocolError` (shape drift); a present object must carry a string
  `mediaId` and numeric `timestampMs` (coerce via `_coerce_ms`) — malformed →
  `AsbplayerProtocolError`. `playing` missing/malformed coerces to `False`.
- HTTP status nuance: an older bridge without the endpoint answers 404, which
  `AsbplayerClient.request` raises as `AsbplayerProtocolError`. The playback
  probe must catch **all** `AsbplayerError` (and `OSError`) itself and treat
  them as "no live state" — a missing endpoint must not poison the whole
  sample or drop the connection for the other two commands. Implement the
  probe as a small helper on the channel that calls `get_playback_state` and
  returns `None` on any of those failures (log at debug/warning once per
  probe, matching existing logging tone).
- Anchor precedence in `_resolve_anchor` (new signature may accept the live
  state): explicit `override_ms` kwarg (one-shot manual) → `source="manual"`
  (counted, unchanged) > live playback state → `AnchorResult(anchor_ms=state.timestamp_ms, source="live")`
  (never counted as manual) > persistent `self._manual_override_ms` →
  `source="manual"` (counted, unchanged) > event-log derivation (unchanged).
  Note this changes today's behavior where the persistent override beats
  everything: the module docstring's manual-anchor section must be updated to
  document the new order and why (live evidence is fresher than a persistent
  override set before it existed).
- `_sample` fetches playback state alongside bound-media/subtitles (one extra
  GET per probe) and passes it through; `_probe_now` uses a live anchor to
  select `displayed_text` exactly as it does for derived anchors, and
  `detail={"anchor_source": "live"}` flows through unchanged via
  `AnchorResult.source`.
- Update the module docstring's "Why this channel looks different" section:
  the bridge now has a live-position query when the patched local
  build/bridge is running; event-log/manual anchors remain the fallback for
  stock builds. Keep the issue #1087 / F-05 references.
- Export new names in `__all__`.

File `src/katagiri/mcp_server.py`:
- Update the `media_context` tool description sentence about
  `manual_anchor_ms` (currently says asbplayer "has no live position feed of
  its own") to say the override takes precedence over the live playhead when
  the patched asbplayer build provides one, and remains the fallback
  otherwise. Keep it accurate and short; `media_now`'s description needs no
  change unless it repeats the same claim.

File `tests/test_media_asbplayer.py` (extend existing patterns — scripted
`CommandClient` doubles):
- `get_playback_state` happy path, `null` state, `error` reply, missing-key
  drift → `AsbplayerProtocolError`, malformed `timestampMs` →
  `AsbplayerProtocolError`, missing `playing` → `False`.
- Precedence: live state present → anchor `source="live"`, anchor_ms =
  `timestampMs`, `manual_anchor_uses` NOT incremented, no
  `media_manual_anchor` event appended.
- Explicit `manual_anchor_ms` kwarg beats live.
- Persistent `set_manual_anchor` loses to live but still wins over event-log
  when live probe fails.
- Playback-state request raising `AsbplayerProtocolError`/`AsbplayerUnavailable`
  → sample still succeeds via old anchor chain (regression guard for stock
  bridges).
- `media_context` end-to-end through the channel: live anchor centers the
  window.
- Run `uv run pytest tests/test_media_asbplayer.py tests/test_everify.py -n auto --dist loadgroup`
  and report the summary. If `tests/test_everify.py` asserts on the old
  precedence or the mcp_server description text, update those assertions to
  the new contract.
