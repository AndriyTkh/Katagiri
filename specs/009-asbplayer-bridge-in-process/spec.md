# Feature Specification: 009 — asbplayer Bridge, In-Process

**Feature Branch**: `009-asbplayer-bridge-in-process`

**Created**: 2026-08-24

**Status**: Draft — **tasks.md is the task-tracking source of truth** (spec-kit; no beads history)

**Input**: Replace the external Go WebSocket bridge that `src/katagiri/asbplayer_launch.py`
starts with `go run main.go` from a configured checkout — which requires a Go toolchain on
PATH and a second repository on disk — with a Python WebSocket + HTTP server hosted inside
Katagiri, serving the same `ws://127.0.0.1:8766/ws` and the same HTTP surface the asbplayer
browser extension and `media_asbplayer.py` already speak.

## Scope claim (binding)

Additive only, per constitution VII: **this feature registers no MCP tool and changes no
existing tool's contract.** `media_now` / `media_context` keep their exact arguments and
output shape; what changes is who is listening on port 8766, not what the answer looks
like. No schema change, no migration, no phase-entry requirement (constitution IV applies
to phases; the bridge is infrastructure under Phase E's already-shipped asbplayer channel).

**This feature is a protocol reimplementation, and that is its whole risk.** The client on
the other end is a third-party browser extension Katagiri does not control. The spec is
therefore written as a *compatibility* spec: the observable behavior of the replacement is
defined by the Go bridge's behavior, enumerated line-by-line in research.md, not by what
seems reasonable.

**Hard boundaries (non-negotiable, restated as requirements below):**

1. **Loopback by default.** The listener binds `127.0.0.1` unless an explicit environment
   override says otherwise, carrying over local commit `37495e22`'s fix. A non-loopback
   bind is a deliberate, logged, operator act — never a default and never silent.
2. **No new listening surface.** Port 8766 is the *only* port this feature opens, and it is
   the same port the Go bridge already opened. Constitution VI's "stdio-only MCP transport,
   no network listener" is about the MCP transport; this bridge is the same category of
   exception as `media_mokuro.py`'s page-change bridge, and it must be recorded as such
   through the same process (see FR-016 — an allowlist change is a **user decision**, not
   an implementation detail).
3. **No AnkiConnect behavior invention.** The AnkiConnect proxy on `POST /` passes headers
   and bodies through unchanged, and its `addNote` intercept reproduces the Go
   `POST_MINE_ACTION` semantics exactly. Constitution II's Anki rules (`answerCards`
   banned) are unaffected: the proxy forwards whatever the extension sends and originates
   no Anki call of its own.
4. **No MCP contract growth.** No ToolSpec is added or changed. A bridge-readiness doctor
   row (an installer/CLI row, not a tool) is permitted and is the only new operator-visible
   surface.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - asbplayer works on a machine with no Go toolchain (Priority: P1)

As the learner, I start Katagiri on a fresh Windows machine that has never had Go
installed and no second checkout of the asbplayer repository, open a streaming site with
the asbplayer extension, and `media_now` answers with the bound media and the current
subtitle — exactly as it does today on the developer machine where the Go bridge happens
to be present. Today that machine gets "asbplayer bridge was not started: Go was not found
on PATH", and the whole asbplayer channel is silently dead.

**Why this priority**: this is the entire feature. Every other story is a property the
replacement must not break while delivering this one.

**Independent Test**: with `go` removed from PATH and `asbplayer_bridge_dir` unset, start
Katagiri, connect a WebSocket client to `ws://127.0.0.1:8766/ws` that answers
`get-bound-media` / `get-subtitles` / `get-playback-state` the way the extension does, and
assert `media_now` / `media_context` return the same structures they return today against
the Go bridge.

**Acceptance Scenarios**:

1. **Given** no Go toolchain and no configured bridge checkout, **When** Katagiri starts,
   **Then** `ws://127.0.0.1:8766/ws` accepts a WebSocket connection and the startup log
   says the bridge is hosted in-process, with no mention of Go or of a missing checkout.
2. **Given** a connected client, **When** an HTTP `GET /asbplayer/bound-media` arrives,
   **Then** the bridge sends `{"command":"get-bound-media","messageId":"<uuid>","body":{}}`
   over the socket, waits for the reply whose `messageId` matches, and returns that reply's
   `body` verbatim as the HTTP JSON response.
3. **Given** no client is connected, **When** any relayed endpoint is called, **Then** the
   bridge answers with the same 5xx the Go bridge answers with, and `media_asbplayer.py`'s
   existing "unreachable channel" degradation runs unchanged.
4. **Given** a client that never replies, **When** a relayed endpoint is called, **Then**
   the bridge gives up after 5 seconds and answers 5xx — it does not hang the caller.
5. **Given** the extension's app-level keepalive, **When** the text `PING` arrives on the
   socket, **Then** the bridge replies with the text `PONG` and does **not** treat the
   message as a JSON response.

---

### User Story 2 - Mining through the bridge keeps working (Priority: P1)

As the learner, I mine a card from the asbplayer extension with the extension's AnkiConnect
URL pointed at `http://127.0.0.1:8766` (the configuration the Go bridge exists to support),
and the card lands in Anki with asbplayer's media attached — the `addNote` is intercepted,
the `mine-subtitle` command reaches the extension, and the response the extension sees is
byte-compatible with what AnkiConnect would have said.

**Why this priority**: the AnkiConnect proxy is the half of the bridge that is *not*
Katagiri's own client surface. If it regresses, the learner's mining flow breaks in a way
no Katagiri test would notice, because Katagiri never calls it.

**Independent Test**: point a scripted AnkiConnect client at the bridge with a stub
AnkiConnect upstream; assert that (a) a non-`addNote` action is forwarded and its response
returned with headers and status intact; (b) an `addNote` with a connected client and
`POST_MINE_ACTION=2` is forwarded first, the note id extracted from the response and
attached to the published `mine-subtitle` command; (c) with `POST_MINE_ACTION` other than
2, a client that answers `{"published": true}` causes the bridge to answer `-1` without
forwarding, and a client that answers `{"published": false}` (or does not answer) causes
the original request to be forwarded after all.

**Acceptance Scenarios**:

1. **Given** an AnkiConnect request whose `action` is not `addNote`, **When** it arrives on
   `POST /`, **Then** it is forwarded to the configured AnkiConnect URL with every request
   header copied, and the upstream status, headers, and body are returned unchanged.
2. **Given** an `addNote` request and **no** connected WebSocket client, **When** it
   arrives, **Then** it is forwarded to AnkiConnect unmodified — the intercept never
   swallows a note when there is nobody to hand it to.
3. **Given** an `addNote` request that matches the configured intercept field/value (or no
   intercept field is configured) and a connected client, **When** `POST_MINE_ACTION` is
   `2`, **Then** the request is forwarded to AnkiConnect first, the resulting note id is
   attached to the `mine-subtitle` command, and the command is published without waiting
   for a reply.
4. **Given** the same request with `POST_MINE_ACTION` not `2`, **When** the client replies
   `{"published": true}` within the timeout, **Then** the bridge answers HTTP 200 with the
   JSON body `-1` and never forwards to AnkiConnect.
5. **Given** the same request, **When** the client's reply is missing, malformed, or has
   `published: false`, **Then** the bridge falls back to forwarding the original request to
   AnkiConnect.
6. **Given** a CORS preflight `OPTIONS /`, **When** it arrives, **Then** it is forwarded to
   AnkiConnect and AnkiConnect's own response headers are returned — the bridge invents no
   CORS headers of its own.

---

### User Story 3 - The bridge's lifetime and reach are told honestly (Priority: P1)

As the operator, I know that the bridge now lives and dies with the Katagiri MCP process,
that it listens only on loopback, and that if something else already owns port 8766 —
including a Go bridge I started myself — Katagiri says so and stands down instead of
fighting for the port. Nothing about this is left to be discovered by a broken mining
session.

**Why this priority**: this is the one genuine behavior change of the feature. The Go
bridge was an independent process that outlived any given Katagiri run; an in-process
server does not. A learner whose extension points its Anki URL at :8766 will find mining
broken whenever Katagiri is not running, and that must be documented, not discovered.

**Independent Test**: start the bridge, confirm the listening socket is bound to
`127.0.0.1` only (the existing `security_scan` shape); stop the host process and confirm
the port is released; occupy the port first and confirm Katagiri starts without error, logs
the occupancy, and never binds.

**Acceptance Scenarios**:

1. **Given** a default configuration, **When** the bridge starts, **Then** the socket is
   bound to `127.0.0.1` and the existing loopback verification reports `loopback_only`.
2. **Given** the documented environment override set to a non-loopback address, **When**
   the bridge starts, **Then** it binds as instructed and logs a warning naming the
   exposure — deliberate, visible, never the default.
3. **Given** port 8766 is already occupied, **When** Katagiri starts, **Then** it does not
   attempt to bind, logs what it found, and the asbplayer channel still works if the
   occupant is a compatible bridge.
4. **Given** the Katagiri process exits (cleanly or not), **When** the port is checked,
   **Then** it is free — no orphaned listener, no `go run` child left behind.
5. **Given** the bridge is hosted in-process, **When** the operator reads the doctor row
   and the operator doc, **Then** both state plainly that the bridge is up only while
   Katagiri is running and what that means for an extension whose Anki URL points at 8766.

---

### User Story 4 - The Go path is gone, without breaking a machine that still has it (Priority: P2)

As the operator upgrading an existing install, my `asbplayer_bridge_dir` config key does not
cause an error, the doctor tells me it is no longer used, and nothing tries to run `go` any
more. If my old Go bridge is still running, Katagiri coexists with it (US3 acceptance 3)
rather than double-binding or killing it.

**Why this priority**: correctness of the cutover, not new capability. It is P2 because
exactly one machine is affected and the failure mode is a confusing message, not data loss.

**Independent Test**: load a config that still sets `asbplayer_bridge_dir`; assert startup
succeeds, no `go` lookup happens anywhere in the process, and the key is reported as
ignored rather than honored.

**Acceptance Scenarios**:

1. **Given** a config that still sets `asbplayer_bridge_dir`, **When** Katagiri starts,
   **Then** it starts normally and the key is treated as obsolete-but-accepted, with the
   fact surfaced once (log line and doctor row), not silently ignored.
2. **Given** any code path in the package, **When** it is searched for a Go toolchain
   lookup or a `go run` invocation, **Then** none exists.
3. **Given** the operator docs, **When** they describe setting asbplayer up, **Then** they
   no longer instruct anyone to clone a bridge checkout or install Go.

### Edge Cases

- **Two extensions / two browser tabs connect at once** → the Go bridge broadcasts every
  command to *every* connected client and accepts the first reply whose `messageId`
  matches; the replacement must do the same, including the broadcast, or a second tab
  silently changes which media answers.
- **A reply arrives after its 5-second deadline** → the waiter is gone; the late reply must
  be discarded without disturbing any other in-flight request. (The Go implementation
  drains replies through a single shared channel, which is a known source of
  cross-talk — research.md G-4; the replacement must not reproduce the *bug*, only the
  contract.)
- **A client disconnects mid-request** → the pending waiter times out or fails fast; no
  exception escapes to the HTTP layer, and the client is removed from the broadcast set.
- **A WebSocket text frame that is neither `PING` nor valid JSON** → ignored, connection
  kept open (the Go bridge silently swallows an unmarshalable message).
- **A very large `load-subtitles` payload** (base64 subtitle files) → must be sent as one
  logical message without truncation; the framing layer must handle payloads well past
  64 KiB.
- **Browser-level control frames** (protocol-level ping/pong, close handshake) → handled by
  the WebSocket layer, distinct from the app-level text `PING`/`PONG`.
- **AnkiConnect is not running** → the proxy's upstream call fails; the bridge answers an
  error rather than crashing, and `bridge_is_healthy()`'s "any HTTP answer proves the
  bridge is up" premise still holds.
- **AnkiConnect answers without a `Content-Type` header** → the Go code indexes
  `Header["Content-Type"][0]` and would panic; the replacement must degrade, not crash
  (research.md G-2).
- **Port 8766 occupied by something that is not a bridge** → same posture as today: do not
  bind, do not start a second server, report it.
- **The MCP process is restarted while the extension is connected** → the extension's
  socket drops; reconnection is the extension's business, and the bridge must accept the
  reconnect cleanly (no stale client entry, no port still in `TIME_WAIT` refusing the
  rebind — `SO_REUSEADDR` semantics considered explicitly on Windows).
- **Katagiri is not running at all** and the extension's Anki URL points at 8766 → mining
  fails at the extension. This is a real regression against the Go bridge's independent
  lifetime, and it is documented (US3 acceptance 5), not engineered away.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST host a WebSocket endpoint at `ws://<bind>:8766/ws` inside the
  Katagiri process, accepting the connection the asbplayer extension already makes, with no
  external process and no Go toolchain involved.
- **FR-002**: The system MUST implement the six server→client commands the extension
  understands — `mine-subtitle`, `load-subtitles`, `seek-timestamp`, `get-bound-media`,
  `get-subtitles`, `get-playback-state` — with the exact JSON envelope
  `{"command", "messageId", "body"}` and a fresh UUID per command.
- **FR-003**: The system MUST correlate replies by `messageId`, MUST broadcast each command
  to every connected client, MUST accept the first matching reply, and MUST abandon the
  wait after **5 seconds**, answering the HTTP caller with a server error.
- **FR-004**: The system MUST answer the app-level text message `PING` with the text
  message `PONG`, and MUST NOT parse it as a command response.
- **FR-005**: The system MUST serve the five relay HTTP endpoints with their existing paths,
  methods, query parameters, request bodies, and response shapes:
  `POST /asbplayer/load-subtitles`, `POST /asbplayer/seek`, `GET /asbplayer/bound-media`,
  `GET /asbplayer/subtitles` (optional `mediaId`, `trackNumbers`),
  `GET /asbplayer/playback-state` (optional `mediaId`) — the exact contract is enumerated in
  research.md and is the authority.
- **FR-006**: The system MUST serve `POST /disconnect-ws-clients`, closing and forgetting
  every connected client.
- **FR-007**: The system MUST serve the AnkiConnect proxy on `/` for `GET`, `POST`, and
  `OPTIONS`, forwarding to the configured AnkiConnect URL (default
  `http://127.0.0.1:8765`) with **every request header copied unchanged** and the request
  body passed through byte-for-byte, and returning the upstream status, headers, and body
  unchanged.
- **FR-008**: On `POST /` the system MUST intercept `addNote` — and only `addNote` — when a
  WebSocket client is connected and the configured intercept field/value matches (an
  unconfigured intercept field matches everything), reproducing the `POST_MINE_ACTION`
  branches exactly as enumerated in research.md, including the "forward anyway" fallbacks.
- **FR-009**: The system MUST bind `127.0.0.1` by default and MUST honor a documented
  environment override for the bind host, carrying over local commit `37495e22`'s behavior;
  a non-loopback bind MUST be logged as a warning naming the exposure.
- **FR-010**: The system MUST honor the same environment configuration surface the Go
  bridge read — bind host, port, AnkiConnect URL, post-mine action, intercept field,
  intercept value — with the same defaults, so an operator's existing `.env`-shaped
  knowledge transfers.
- **FR-011**: The system MUST NOT bind the port when it is already occupied; it MUST report
  what it found and leave the occupant alone, preserving today's `asbplayer_launch.py`
  posture.
- **FR-012**: The listener MUST be released when the host process exits, and the system
  MUST expose an explicit stop that releases it without exiting the process (so tests never
  leak a listener between cases).
- **FR-013**: The system MUST NOT invoke a Go toolchain, MUST NOT spawn a child process for
  the bridge, and no code path in the package may look up `go` on PATH.
- **FR-014**: `asbplayer_bridge_dir` MUST remain a loadable config key that produces no
  error, MUST NOT be honored, and its obsolescence MUST be surfaced to the operator at
  least once (log line and/or doctor row) rather than silently ignored.
- **FR-015**: The feature MUST add no MCP ToolSpec and MUST NOT alter any existing tool's
  name, arguments, or output shape. `media_asbplayer.py`'s `PROTOCOL_SURFACE_VERSION` MUST
  remain 3 — the surface is unchanged, only its host is.
- **FR-016**: The new module WILL match `HTTP_SERVER_PATTERNS` (and, if the proxy's
  outbound leg uses `http.client`, `HTTP_CLIENT_PATTERNS`) in `tests/test_bverify.py` and
  `tests/test_cverify.py`. Adding it to those allowlists is a **user decision recorded in
  docs/decisions-ledger.md before the code lands** — the D-47 precedent — never a
  unilateral test edit. No other module may gain a server construct.
- **FR-017**: No secret, token, or credential may be logged, echoed, or included in any
  bridge response. Request logging MUST NOT reproduce AnkiConnect request bodies (the Go
  bridge's request logger parses and prints request fields; the replacement logs metadata
  only).
- **FR-018**: An operator doc MUST state what the bridge is, that it is hosted by Katagiri
  and lives only while Katagiri runs, how to point the extension at it, what the
  environment overrides are, and what happened to the Go checkout.

### Key Entities

- **Bridge server**: the in-process listener on 8766 — its bind address, its set of
  connected WebSocket clients, its pending-reply registry keyed by `messageId`, and its
  AnkiConnect forwarding configuration.
- **Client command**: `{command, messageId, body}` sent server→client; six known values.
- **Client response**: `{command, messageId, body}` sent client→server; matched to a
  pending command by `messageId`, its `body` relayed verbatim.
- **Bridge launch result**: the existing `BridgeLaunchResult` shape (`launched`,
  `already_running`, `bridge_dir`, `reason`), whose meaning shifts from "started a child
  process" to "started the in-process server"; `bridge_dir` becomes vestigial.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a machine with no Go on PATH and no bridge checkout, the asbplayer channel
  answers `media_now` with real bound media — the current behavior on such a machine is no
  answer at all.
- **SC-002**: Every command, endpoint, and branch enumerated in research.md's protocol
  contract has at least one automated test asserting the replacement's behavior matches the
  cited Go source line — coverage measured against that enumeration, not against
  line coverage.
- **SC-003**: `tests/test_media_asbplayer.py` and `tests/test_everify.py` pass **unchanged**
  — the client half of the protocol needed no edit, which is the strongest available proof
  that the surface did not drift.
- **SC-004**: A differential run against the Go bridge (same client, same scripted extension
  double, both servers) produces identical HTTP status codes and identical response bodies
  for every relay endpoint and every AnkiConnect proxy branch.
- **SC-005**: The registered-tool count is unchanged from pre-009, and
  `PROTOCOL_SURFACE_VERSION` is still 3.
- **SC-006**: The full suite leaves no listener on 8766 and no orphaned process: after the
  run, the port is free.
- **SC-007**: `tests/test_bverify.py` and `tests/test_cverify.py` differ from `main` in
  exactly one way — the new module's name in the allowlist(s) named by the approved ledger
  row — and every other invariant assertion is untouched and green.
- **SC-008**: A `grep` of `src/katagiri` for `go run`, `shutil.which("go")`, or
  `main.go` returns nothing.

## Assumptions

- Windows 11 is the only supported host (constitution technology constraints); the server
  must nonetheless not depend on platform-specific socket behavior beyond what is tested.
- The protocol baseline is **asbplayer 1.20.2 plus local commit `37495e22`** (F-05
  playback-state relay + HOST bind), the checkout at
  `C:/ProjectsC/RandomPr/asbplayer/scripts/web-socket-server`. Upstream issue **#1087 is an
  open, unmerged proposal** with no overlap with the playback-state work; it must be
  re-checked before this feature ships (tasks.md carries that as a task) because a merged
  #1087 would change the protocol this feature freezes.
- No canonical upstream WebSocket protocol document exists; the Go source is the
  specification, and research.md's enumeration — cited to `main.go` line numbers — is this
  feature's transcription of it.
- The learner's extension is the only WebSocket client that matters. The bridge's CLI
  helper scripts (`scripts/web-socket-server/cli/*`) are curl one-liners against the same
  HTTP endpoints and need no separate support.
- Anki and AnkiConnect keep their current local contract (`http://127.0.0.1:8765`); the
  proxy is a passthrough and asserts nothing about AnkiConnect's own semantics.
- The Go checkout stays on disk as a reference and as the differential oracle for SC-004;
  it is simply no longer *run by Katagiri*. Nothing in this repository is deleted from that
  other repository.
