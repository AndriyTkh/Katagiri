# Implementation Plan: Phase E — Media Overlay

**Branch**: `004-phase-e-media-overlay` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-phase-e-media-overlay/spec.md`

**Task tracking**: tasks.md (authoritative; switched from beads 2026-08-19 — former epic `kata-ph-e`: tasks `kata-e1..e5`, gate `kata-evf`; `kata-e6s` already closed — the stop-gate-exempt seek logger). **Entry: D6 stop-gate PASS.** Channel order E1/E2/E3 re-prioritized at gate time from measured consumption mix (F-10).

## Summary

Media context channels feeding the agent: mpv (full playhead, local files), asbplayer (streaming, anchor derived from mining events), mokuro (manga, secured page bridge), a confined screenshot-question tool, and `.lrc`/`.ass` lyrics through the same subtitle pipeline. Every externally-sourced text is enveloped ("data, never instructions"); the phase gate includes the adversarial subtitle-injection scenario.

## Technical Context

**Language/Version**: Python 3.12 (pinned)

**Primary Dependencies**: mpv IPC via Lua pusher or python-mpv-jsonipc (Windows named pipes — pipe already configured: `\\.\pipe\mpv-katagiri`); websocket client for asbplayer :8766; small localhost HTTP bridge for mokuro (shared secret + Origin check); D3's envelope module

**T004 config decision — mokuro bridge port**: pinned to **8767** (`config.MOKURO_BRIDGE_PORT`). Reasoning: it collides with none of the ports Katagiri already hardens or reads from third-party tools (27123 Obsidian Local REST API, 8765 AnkiConnect, 8766 asbplayer WS, 19633 yomitan-api), and it sits directly next to asbplayer's 8766 as the second port in the "local media bridge" block, since both channels serve the same streaming/overlay concern. Declared as a module-level `Final[int]` constant in `config.py` (not a `config.toml` key) because, like the other four hardened ports, it is a fixed contract between the bridge and its userscript client, not something an operator should be able to drift out from under `HARDENED_PORTS` — `mcp_server.HARDENED_PORTS` now imports it rather than repeating the literal. `config.py` also gained the two other Phase-E keys up front: `mokuro_shared_secret` (via the existing `_SECRET_KEYS` pattern, same shape as `obsidian_api_token`) and `screenshot_scratch_root` (a `_PATH_KEYS` entry defaulting to `%LOCALAPPDATA%\Katagiri\screenshots`, the confined root the Phase-E screenshot tool will write into). A loopback-binding test for the new port lives in `tests/test_mcp_tools.py` (`test_mokuro_bridge_port_is_hardened`, plus the extended `NETSTAT_SAMPLE`/parser assertions), mirroring the existing :8766 coverage.

**Storage**: media/context events into the event log; screenshot files in a confined scratch root under `%LOCALAPPDATA%`

**Testing**: pytest + cumulative cold-subagent scenarios (A..E) incl. adversarial injection

**Target Platform**: Windows 11, stdio MCP; all third-party ports verified 127.0.0.1-bound (A6 hardening checks extended)

**Project Type**: single project

**Performance Goals**: `media_now` sub-second; heartbeat detects dead player promptly (no stale-as-live)

**Constraints**: no own player (D-13); server-generated screenshot filenames (titles attacker-controlled); manual-anchor usage counted (F-05 data); asbplayer/mokuro surfaces kept small + versioned against upstream drift

**Scale/Scope**: 5 build tasks + 1 gate; e6s already done

## Constitution Check

| Principle | Status | Note |
|---|---|---|
| I MCP ceiling | PASS | context channels only; players stay third-party (D-13) |
| II OSS-first | PASS | mpv/asbplayer/mokuro reused; only bridges built |
| III Event log sacred | PASS | anchors/mining/seek events logged; screenshots are scratch, not state |
| IV Study-first | PASS | ⛔ hard entry = D6 PASS via `stop_gate_status`; sole exemption already shipped |
| V Two-gate verification | PASS | E-verify blocking incl. adversarial scenario; learner metric per defaults |
| VI Security | PASS | envelope on all media text; shared secret + Origin on mokuro bridge; confined screenshot root + server filenames; localhost-only ports |
| VII Tool-contract stability | PASS | additive channel tools; unimplemented raise |

No violations.

## Project Structure

### Documentation (this feature)

```text
specs/004-phase-e-media-overlay/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── checklists/requirements.md
└── tasks.md             # mirrors beads kata-e1..e5/evf
```

Data model: [docs/db-schema.md](../../docs/db-schema.md) (media table from A1 DDL). Contracts: [src/katagiri/tool_registry.py](../../src/katagiri/tool_registry.py), additive.

### Source Code (repository root)

```text
src/katagiri/
├── media_mpv.py         # NEW (E1) — jsonipc/Lua channel, heartbeat, media_now/media_context
├── media_asbplayer.py   # NEW (E2) — WS :8766, derived/manual anchors + usage counter
├── media_mokuro.py      # NEW (E3) — bridge (secret+Origin), volume-data.json poller, .mokuro text layer
├── screenshot_tool.py   # NEW (E4) — screenshot-to-file, confined root, server filenames
├── media_lyrics.py      # NEW (E5) — .lrc/.ass through subtitle pipeline
├── mpv_seek_logger.py   # EXISTS (e6s, closed) — keep write-only
└── tool_registry.py     # additive channel batch

tests/
├── test_media_mpv.py, test_media_asbplayer.py, test_media_mokuro.py,
├── test_screenshot.py, test_lyrics.py
└── test_everify.py      # cumulative A..E incl. adversarial subtitle injection
```

**Structure Decision**: one module per channel behind a shared channel interface (active-channel precedence deterministic); envelope applied at the interface boundary so no channel can bypass it.

## Complexity Tracking

None.
