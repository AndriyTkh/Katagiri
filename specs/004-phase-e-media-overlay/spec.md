# Feature Specification: Phase E — Media Overlay (Context Channel)

**Feature Branch**: `004-phase-e-media-overlay`

**Created**: 2026-08-19

**Status**: Active — **tasks.md is the task-tracking source of truth** (switched from beads 2026-08-19; former epic `kata-ph-e` retired, IDs kept as `[was: kata-*]`)

**Input**: User description: "Phase E media overlay: mpv, asbplayer, mokuro context channels, screenshot tool, music/karaoke" — expanded from docs/dev-plan.md v1.1 Phase E. No own media player (D-13): players are mpv + asbplayer + mokuro, reached through MCP context channels.

**Entry precondition**: ⛔ D6 stop-gate PASS (`stop_gate_status`) — sole exemption already shipped (write-only mpv seek logger, kata-e6s). Channel order E1/E2/E3 is finalized by the consumption mix measured during the D6 window (F-10); priorities below are defaults.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - "What did she just say?" while watching local files (Priority: P1)

As the learner watching a local file in mpv, I ask the agent what was just said; via the mpv channel (Lua pusher or python-mpv-jsonipc over Windows named pipes, with heartbeat) the agent calls `media_now` / `media_context` and answers anchored to the exact playhead.

**Why this priority**: mpv is the local-files first-class surface with a full playhead — the strongest anchor quality of the three channels.

**Independent Test**: Cold agent retrieves current position/title and the subtitle window around the playhead from a scripted mpv session.

**Acceptance Scenarios**:

1. **Given** mpv playing with the IPC pipe configured, **When** `media_now` is called, **Then** title + playhead return; **When** `media_context` is called, **Then** subtitle lines around the playhead return inside the untrusted-data envelope.
2. **Given** mpv not running or heartbeat stale, **Then** tools return a structured "no active media" error, never stale data presented as live.

---

### User Story 2 - Same question on streaming via asbplayer (Priority: P2)

As the learner streaming with asbplayer (WS :8766, `get-bound-media` + `get-subtitles`), I get subtitle context anchored **automatically from my last mining/copy event's timestamp** (no playhead exists upstream — issue #1087). Manual anchors are accepted and their use is counted, so the upstream-PR trigger (F-05) fires on data, not annoyance.

**Independent Test**: Fixture mining event at time T; subtitle window returned centers on T; manual-anchor usage increments a counter.

**Acceptance Scenarios**:

1. **Given** a recent mining/copy event, **When** context is requested, **Then** the anchor derives from that event's timestamp automatically.
2. **Given** a manual anchor supplied, **Then** it is used and its use is logged/counted.

---

### User Story 3 - Manga context via mokuro (Priority: P3)

As the learner reading manga in mokuro, page changes reach Katagiri through a page-change userscript → localhost bridge secured with a **shared secret + Origin validation**; `volume-data.json` polling is the fallback; the `.mokuro` JSON serves as the text layer for the current page.

**Acceptance Scenarios**:

1. **Given** a bridge request missing the shared secret or with a wrong Origin, **Then** it is rejected.
2. **Given** the userscript unavailable, **When** the poller fallback is active, **Then** current page context still resolves from `volume-data.json`.

---

### User Story 4 - Ask about the current frame (Priority: P2)

As the learner, I ask "what does that sign say?" — the screenshot-question tool drives mpv `screenshot-to-file` into a **confined scratch root with server-generated filenames** (media titles are attacker-controlled; no path traversal), and the agent reads the frame. Ships immediately after the first channel lands.

**Acceptance Scenarios**:

1. **Given** a media title containing `..\` or path separators, **When** a screenshot is taken, **Then** the file lands in the confined root under a server-generated name.
2. **Given** the screenshot exists, **Then** the agent can read exactly that file and nothing outside the root.

---

### User Story 5 - Music and karaoke lines are minable (Priority: P3)

As the learner listening to music through mpv, timed lyrics (`.lrc`/`.ass`) flow through the same subtitle pipeline, so lyric lines are anchored and minable like subtitle lines.

**Acceptance Scenarios**:

1. **Given** an `.lrc` file loaded in WATCH mode, **When** context is requested at time T, **Then** the lyric line at T returns and can be mined with a source ref.

---

### Edge Cases

- **Adversarial subtitle** (the E-verify scenario): a subtitle line containing tool-call instructions MUST NOT trigger any write tool — envelope + echo-back holds. *(D-22)*
- Two channels active at once (mpv + asbplayer) → deterministic precedence, surfaced in the response.
- Rewind telemetry: seek-back events already captured by the exempt E6 slice; analysis stays a moonshot (out of scope).
- Ports :8766 (asbplayer) and the mokuro bridge bound to 127.0.0.1 only, verified per A6 hardening.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: mpv channel: Lua pusher or python-mpv-jsonipc (Windows named pipes), heartbeat daemon, `media_now` / `media_context`; local files first-class with full playhead. *(bead kata-e1)*
- **FR-002**: asbplayer channel: WS :8766, `get-bound-media` + `get-subtitles`; anchor auto-derived from the last mining/copy event timestamp; manual anchors accepted and counted. *(kata-e2)*
- **FR-003**: mokuro channel: page-change userscript → localhost bridge with shared secret + Origin validation; `volume-data.json` poller fallback; `.mokuro` JSON as text layer. *(kata-e3)*
- **FR-004**: Screenshot-question tool: mpv `screenshot-to-file` → confined scratch root, server-generated filenames; agent reads the frame. *(kata-e4)*
- **FR-005**: Music/karaoke: `.lrc`/`.ass` through the same subtitle pipeline; lyric lines minable with source refs. *(kata-e5)*
- **FR-006**: ALL externally-sourced text (subtitles, OCR, lyrics) wrapped in the untrusted-data envelope with a "data, never instructions" contract in every media tool description. *(D-22)*
- **FR-007**: All channel tools registered additively; unimplemented tools raise; tokens/secrets never in outputs. *(constitution VI, VII)*

### Key Entities

- **Media context**: channel id, title, anchor (playhead | derived | manual), text window, envelope wrapper.
- **Anchor**: timestamp + provenance (playhead/mining-event/manual); manual use counted for F-05.
- **Screenshot artifact**: server-named file under the confined root, linked to media context.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** (E-verify): cumulative cold-subagent pass (A..E): mpv position/title; asbplayer subtitle window from anchor; mokuro page; screenshot round-trip; one `.lrc` through WATCH mode; **adversarial subtitle-injection scenario refused**. *(kata-evf)*
- **SC-002** (Milestone E): anchored "what did she just say?" answered on the primary consumption surface; words mined with source refs.
- **SC-003** (learner metric): ≥5 of last 7 days show events from Phase-E tools.

## Assumptions

- D6 gate passed before any Phase-E code beyond the already-shipped seek logger.
- Channel build order re-prioritized from measured consumption mix at gate time (F-10); E4 ships immediately after whichever channel lands first.
- asbplayer upstream playhead PR (F-05) remains opportunistic — manual/derived anchors are the permanent fallback.
- Task state lives in tasks.md (beads retired for this phase 2026-08-19; `kata-e1..e5`/`kata-evf` are historical refs).
