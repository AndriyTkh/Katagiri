# Tasks: Phase E — Media Overlay

**Input**: Design documents from `/specs/004-phase-e-media-overlay/`

**Prerequisites**: plan.md, spec.md; ⛔ **D6 stop-gate PASS before any task below** (sole exemption kata-e6s already shipped as src/katagiri/mpv_seek_logger.py).

**Tests**: Included (constitution V).

**✅ SOURCE OF TRUTH**: this tasks.md (spec-kit). Beads retired for phases C–E; historical bead IDs kept as `[was: kata-*]`.

**Taskgroup rule**: each `## Taskgroup` is the merge unit — all tasks fully done, tests green, merged to `master` before the next group. No task spans groups. Completed boxes: `[x]`.

**Integration branch (2026-08-21, D-42): `phase-e`, not `master`.** D6 (line above) is still FAIL — 0 real study days since the 2026-08-20 DB reset, no probe battery. D-42 waives the block for isolated implementation only: lanes below merge into `phase-e`, never into `master` or any `006-*` branch/worktree, until D6 actually reads PASS. Read every "master" below as `phase-e` for merge target purposes; TG-E5 (E-verify, T013/T014) stays excluded from the waiver and blocked until D6 passes for real.

**Organization**: US1 = mpv (was kata-e1), US2 = asbplayer (was kata-e2), US3 = mokuro (was kata-e3), US4 = screenshot (was kata-e4), US5 = lyrics (was kata-e5). Default order below assumes mpv-first; T001 may swap US2 (asbplayer) into TG-E3 — see the reorder procedure in T001.

**mpv coupling (constrains any reorder)**: FR-004 (screenshot) and FR-005 (lyrics WATCH pipeline) hardcode mpv — US4/US5 depend on the **mpv channel specifically**, not on "whichever channel lands first". US2/US3 depend only on the interface.

## Workfile & conflict map

| Taskgroup | Worktree? | New files (lane-owned) | Shared/hot files touched |
|---|---|---|---|
| TG-E1 Setup | no (master) | — | docs/dev-plan.md, this file |
| TG-E2 Foundational: channel iface | yes: `wt/e-channel` | src/katagiri/media_channel.py, tests/test_media_channel.py | none |
| TG-E2 Foundational: config+hardening | no (master, serial) | — | src/katagiri/config.py (phase-E keys), src/katagiri/mcp_server.py (HARDENED_PORTS), tests/test_mcp_tools.py |
| TG-E3 First channel (default mpv) | yes: `wt/e-mpv` | src/katagiri/media_mpv.py, tests/test_media_mpv.py | none |
| TG-E3 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG-E4 lane US2 | yes: `wt/e-asb` | src/katagiri/media_asbplayer.py, tests/test_media_asbplayer.py | none |
| TG-E4 lane US3 | yes: `wt/e-mokuro` | src/katagiri/media_mokuro.py, tests/test_media_mokuro.py | none |
| TG-E4 lane US4 | yes: `wt/e-shot` | src/katagiri/screenshot_tool.py, tests/test_screenshot.py | none |
| TG-E4 lane US5 | yes: `wt/e-lyrics` | src/katagiri/media_lyrics.py, tests/test_lyrics.py | none |
| TG-E4 registration | no (master, serial) | — | tool_registry.py, mcp_server.py, tests/test_mcp_tools.py |
| TG-E5 Gate | no (master) | tests/test_everify.py | docs/decisions-ledger.md, docs/dev-plan.md |

**Hot-file rule**: `tool_registry.py`/`mcp_server.py` edited only in serial-on-master registration tasks (T004, T007, T012) using the phase-fragment seam from specs/003 T003. `config.py` gets ALL phase-E keys upfront in T004 — lanes read keys, never add them. **`media_channel.py` joins the hot-file list once TG-E2 merges (interface freeze)**: channel lanes only implement it; if a lane discovers an interface gap mid-TG-E4, the fix is a serial-on-master commit to media_channel.py, then every open lane rebases onto master before its merge. Every channel lane owns disjoint files → conflict-free merges.

**Test convention (parallel lanes on one machine)**: no fixed-port listeners in unit tests — mock the WS/HTTP peer or bind port 0 (ephemeral) and pass the port in. A fixed :8766 test server in one lane collides with another lane's run or a live asbplayer.

## Format: `[ID] [P?] [Story] Description` — each task lists **Write** and **Read**.

## Taskgroup E1: Setup (serial, master)

- [x] T001 Gate check + channel order: confirm `stop_gate_status` PASS recorded (specs/003 T022). Read the consumption mix from the D6-window evidence — **metric**: per-channel evidence counts over the D6 window: local video = mpv seek events (e6s logger) in the event log; streaming = WATCH-mode study-log entries naming browser/asbplayer sources; manga = study-log reading entries naming manga/mokuro. If streaming clearly dominates, swap US2 (asbplayer) into TG-E3; if signal is ambiguous, keep mpv-first default and note it. **Reorder procedure — on swap, rewrite in this file**: (1) conflict-map rows for TG-E3/TG-E4, (2) TG-E3 heading + task texts (channel module/test names), (3) TG-E4 lane list (mpv lane moves in, becomes the lane US4/US5 wait on), (4) Dependencies & Execution Order, (5) Implementation Strategy lane count. Task IDs keep their slots (T005–T007 = TG-E3 whatever the channel). Write: docs/dev-plan.md, this tasks.md. Read: `stop_gate_status` output, event log (seek events), docs/katagiri/katagiri/60-review/study-log.md, specs/003 T022 record. [was: kata-ph-e entry]
  **Result (2026-08-21)**: `stop_gate_status` D6 reads **FAIL** — 0 study days since the 2026-08-20 DB reset, no probe battery. Proceeding anyway under **D-42** (isolation-scoped waiver, implementation only; TG-E5 excluded). Consumption-mix evidence: **none** — the event log has nothing D6-window-relevant post-reset, so the signal is unambiguously absent, not merely ambiguous. Default holds: **mpv-first**, no reorder. Re-evaluate the channel-order question once real study evidence exists and TG-E5 is unblocked, not before.
- [x] T002 Bottom-up estimates for every task below; split >8h tasks in this file (D-29). Write: this tasks.md. Read: plan.md, research.md, Phase C/D estimate-accuracy notes. [was: kata-ph-e]
  **Estimates (T002, 2026-08-21; human-hour scale, Phase C/D/006 anchors; expected agent wall-clock ≈0.1×)**: T003 3 · T004 2.5 · T005 4 · T006 2.5 · T007 1.5 · T008 3 · T009 3.5 · T010 4 · T011 2.5 · T012 2 · T013 4 · T014 1.5. **Total ≈34h** (≈3.4h wall-clock at the ≈0.1× ratio). No task >8h — no splits (D-29).

## Taskgroup E2: Foundational (iface in worktree; config+hardening serial)

- [x] T003 [P] Shared channel interface: envelope enforcement at the boundary (no channel can bypass it) + deterministic active-channel precedence + heartbeat/staleness contract anchored on the existing `media_heartbeat` mechanism (docs/db-schema.md:101 — liveness derived from row age; do NOT invent a second liveness mechanism). Worktree `wt/e-channel`. Write: NEW src/katagiri/media_channel.py, NEW tests/test_media_channel.py. Read: src/katagiri/envelope.py API (D phase), docs/db-schema.md media table + media_heartbeat, plan.md structure decision, spec.md channel-precedence requirements. [was: kata-ph-e]
- [x] T004 Config + hardening prep (serial, master). :8766 is ALREADY hardened (`HARDENED_PORTS`, mcp_server.py:556; loopback test exists at tests/test_mcp_tools.py:721–738) — do not redo it. Remaining work: pin the mokuro bridge port to a concrete number (decide here, record in plan.md Technical Context), add it to HARDENED_PORTS + loopback test; declare ALL phase-E config keys upfront in config.py — mokuro shared secret via the `_SECRET_KEYS` pattern (config.py:33), mokuro bridge port, screenshot scratch-root under %LOCALAPPDATA%. Write: src/katagiri/config.py, src/katagiri/mcp_server.py (HARDENED_PORTS), tests/test_mcp_tools.py, plan.md. Read: mcp_server.py security_scan section + HARDENED_PORTS, config.py:1–60 (_SECRET_KEYS pattern), docs/decisions-ledger.md A6 entries. [was: kata-ph-e]

**Checkpoint**: suite green; interface frozen (media_channel.py now hot-file); all phase-E config keys exist.

## Taskgroup E3: First channel — default US1 mpv (P1) 🎯 MVP

**Goal**: playhead-anchored context for local files. **[was: kata-e1]** (If T001 swapped channels, this group builds asbplayer with the same three-task shape and US4/US5 wait for the mpv lane in TG-E4.)

- [x] T005 [US1] jsonipc (or Lua pusher) channel over `\\.\pipe\mpv-katagiri`, heartbeat via media_heartbeat rows (stale never reported live), media_now/media_context implementing the media_channel interface. Worktree `wt/e-mpv`. Write: NEW src/katagiri/media_mpv.py. Read: media_channel.py interface, src/katagiri/mpv_seek_logger.py (existing pipe usage), research.md mpv IPC notes. [was: kata-e1]
- [x] T006 [P] [US1] Unit tests incl. stale-heartbeat path + enveloped subtitle windows; mock the pipe peer (no live mpv dependency). Write: NEW tests/test_media_mpv.py. Read: fixture recipe (tests/test_mcp_tools.py:1–80), media_mpv.py API. [was: kata-e1]
- [x] T007 [US1] Registration (serial, master): additive ToolSpec entries into phase-E fragment + adapter block + smoke tests; enveloped subtitle windows verified at the tool boundary. Write: tool_registry.py, mcp_server.py, tests/test_mcp_tools.py. Read: seam layout (specs/003 T003), media_mpv.py + media_channel.py APIs. [was: kata-e1]

**Checkpoint**: full suite green; first channel live — unblocks TG-E4 lanes (all four if this was mpv; US2/US3 only if it wasn't).

## Taskgroup E4: Remaining channels — parallel lanes

Merge order: US4 → US2 → US3 → US5 (screenshot ships first — completes the MVP pair; lyrics last, first cut under the 1.5× slip rule). US4/US5 branch the moment the **mpv** lane is merged (that's the TG-E3 checkpoint in the default order); US2/US3 branch at the TG-E3 checkpoint regardless.

- [x] T008 [P] [US4] Screenshot-question tool (mpv-anchored per FR-004): screenshot-to-file → confined scratch root (config key from T004), server-generated filenames (titles attacker-controlled), agent read path; hostile-title test case. Worktree `wt/e-shot`. Write: NEW src/katagiri/screenshot_tool.py, NEW tests/test_screenshot.py. Read: media_mpv.py (anchor access), config.py phase-E keys, spec.md FR-004. [was: kata-e4]
- [x] T009 [P] [US2] asbplayer channel: WS :8766 client (get-bound-media, get-subtitles), anchor derived from last mining/copy event, manual anchors accepted + usage counted (F-05 data); surface kept small + versioned against upstream drift. Tests mock the WS peer or bind port 0 — never a fixed listener. Worktree `wt/e-asb`. Write: NEW src/katagiri/media_asbplayer.py, NEW tests/test_media_asbplayer.py. Read: media_channel.py interface, research.md asbplayer protocol notes, event-log mining-event shape (docs/db-schema.md). [was: kata-e2]
- [x] T010 [P] [US3] mokuro channel: page-change bridge on the port pinned in T004, shared secret from config `_SECRET_KEYS` + Origin validation; volume-data.json poller fallback; .mokuro text layer. Tests bind port 0 / mock the bridge — never the pinned port. Worktree `wt/e-mokuro`. Write: NEW src/katagiri/media_mokuro.py, NEW tests/test_media_mokuro.py. Read: media_channel.py interface, config.py phase-E keys (secret + port), research.md mokuro notes, T004 hardening tests. [was: kata-e3]
- [x] T011 [P] [US5] Lyrics (mpv WATCH pipeline per FR-005): .lrc/.ass through the subtitle pipeline; lyric lines minable with source refs. Worktree `wt/e-lyrics`. Write: NEW src/katagiri/media_lyrics.py, NEW tests/test_lyrics.py. Read: media_channel.py + media_mpv.py subtitle pipeline, spec.md FR-005. [was: kata-e5]
- [x] T012 Registration (serial, master, after lane merges in the order above): additive ToolSpec batch for all TG-E4 tools + adapter blocks + smoke tests. Write: tool_registry.py, mcp_server.py, tests/test_mcp_tools.py. Read: seam layout, new module APIs. [was: kata-e2..e5]

**Checkpoint**: full suite green. If the slip rule cuts US5 (or others), mark the task line `~~cut~~` with a dated note here instead of deleting it.

## Taskgroup E5: Gate — E-verify (P0)

**[was: kata-evf]** Needs all built channels merged (cut stories noted first). Max two rerun cycles.

- [ ] T013 [Gate] Cumulative cold-subagent scenarios A..E: mpv position/title; asbplayer window from anchor; mokuro page; screenshot round-trip; one .lrc through WATCH mode; **adversarial subtitle-injection refused**. Write: NEW tests/test_everify.py. Read: quickstart.md runbook, envelope.py contract, prior *verify tests. [was: kata-evf]
- [ ] T014 [Gate] Milestone E manual checks (anchored question on primary surface, mined words with source refs, learner metric); ledger/coverage update; weekly status line; mark phase complete. Write: docs/decisions-ledger.md, docs/dev-plan.md, this tasks.md. Read: event log, coverage table. [was: kata-evf]

## Dependencies & Execution Order

- TG-E1 → TG-E2 → TG-E3 → TG-E4 → TG-E5. Within TG-E2: T003 (worktree) ∥ T004 (serial master). TG-E4: US2/US3 lanes need only the frozen interface; US4/US5 lanes need the merged **mpv** channel (FR-004/FR-005). T012 closes the group.
- Default order: all four TG-E4 lanes branch at the TG-E3 checkpoint. Swapped order (asbplayer first): US3 branches at TG-E3 checkpoint; mpv lane joins TG-E4; US4/US5 branch only after the mpv lane merges.

## Implementation Strategy

MVP = mpv channel + E4 (answers "what did she just say?" + "what does that sign say?" on the primary surface). Max parallel width 4 worktrees in TG-E4 (default order); each lane's Read list is its complete context. Lyrics last, cut first.
