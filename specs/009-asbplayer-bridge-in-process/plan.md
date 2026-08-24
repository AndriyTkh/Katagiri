# Implementation Plan: 009 — asbplayer Bridge, In-Process

**Branch**: `009-asbplayer-bridge-in-process` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-asbplayer-bridge-in-process/spec.md`

## Summary

Delete Katagiri's dependency on a Go toolchain and a second repository checkout by hosting
the asbplayer bridge itself. A new module `src/katagiri/asbplayer_bridge.py` runs an
`aiohttp` application on its own asyncio loop on a daemon thread, serving all ten routes
the Go bridge serves on `127.0.0.1:8766` — the `/ws` WebSocket upgrade with app-level
`PING`/`PONG`, the six server→client commands correlated by `messageId` with a 5-second
deadline, the five `/asbplayer/*` relay endpoints, `POST /disconnect-ws-clients`, and the
AnkiConnect proxy on `GET`/`POST`/`OPTIONS /` including the `addNote` intercept with
byte-exact header and body passthrough. `asbplayer_launch.py` keeps its public shape
(`ensure_asbplayer_bridge()` → `BridgeLaunchResult`) but starts the in-process server
instead of a child process; `mcp_server.py`'s startup block changes by a few log lines.
The client half (`media_asbplayer.py`) is not touched at all — that it needs no edit is
the feature's central proof (SC-003).

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`), uv-managed

**Primary Dependencies**: **one new runtime dependency — `aiohttp`** (see Key design
decision 1 and research.md R6), plus stdlib `asyncio`, `http.client`, `json`, `uuid`,
`socket`, `threading`. No new dev dependency.

**Storage**: none. 009 persists nothing — no config key added, no table, no file. (This is
why there is no `data-model.md`; see below.)

**Testing**: pytest, general group (`tests/conftest.py`). The bridge's own tests bind an
**ephemeral port** (`port=0`) and drive a real loopback WebSocket client, mirroring
`tests/test_media_mokuro.py`'s discipline of never binding the pinned port. The gate adds
one differential run against the Go bridge and one real-machine observation with the
actual browser extension.

**Target Platform**: Windows 11 only

**Performance Goals**: no regression against the Go bridge on the three routes Katagiri
calls — `media_asbplayer.py`'s client timeout is 2 s and must stay comfortable; the 5 s
command deadline is a contract value, not a tuning knob.

**Constraints**: loopback bind by default with the `37495e22` env override (FR-009);
identical protocol surface, `PROTOCOL_SURFACE_VERSION` stays 3 (FR-015); no ToolSpec added
(FR-015); no Go, no child process (FR-013); no secret or note body in any log (FR-017);
**`tests/test_bverify.py` / `tests/test_cverify.py` are frozen** until a user decision
authorizes the allowlist additions (FR-016 — the feature's one hard blocker); no listener
leaked by the suite (FR-012, SC-006).

**Scale/Scope**: 1 new module (~450 LOC), 1 rewritten module (~150 LOC), ~5 lines in
`mcp_server.py`, 1 new test file plus 1 rewritten one, 1 allowlist edit in 2 frozen files,
1 operator doc, 1 ledger row, 1 `pyproject.toml` dependency line. 0 new MCP tools.

## Constitution Check

*GATE: evaluated against constitution v1.4.0.*

| Principle | Verdict | Notes |
|---|---|---|
| I MCP ceiling | PASS | No app, no GUI, no service, no own player. The bridge is the transport asbplayer *already* required; 009 changes who hosts it, and strictly reduces the install surface (one repo instead of two, no Go). It adds no user-facing surface at all. |
| II OSS-first | PASS | The decisive argument *for* taking a dependency: hand-writing RFC 6455 framing would be reimplementing OSS, which this principle forbids. `aiohttp` is integrated, not vendored or forked. What 009 does reimplement is ~490 lines of glue whose upstream is not distributed as a library — a Go binary in another repository is not an integrable component for a Python process (research.md R6). |
| III Event log sacred | PASS | No DB access, no schema change, no migration, no event written by the bridge. |
| IV Study-first gating | PASS | Infrastructure under an already-shipped Phase E channel, not a phase entry. No interaction with D-19/D-33. |
| V Two-gate verification | PASS (adapted) | No learner metric (no new study surface). Cold-subagent analog: TG3's gate is run by a dedicated agent and includes a **differential run against the Go bridge** — an oracle-based check that is stronger than a scripted-fixture pass, and the reason no held-out suite is carried (see below). Max two fail→fix→rerun cycles. |
| VI Security hardening | **PASS with a required user decision** | stdio remains the only MCP transport; `server.run(transport="stdio")` is untouched. The new listener is the *same port on the same host* the Go bridge already opened — the exposure does not grow, it changes owner. But it moves a listening socket **into the Katagiri process**, which is precisely what `test_bverify.py`'s no-HTTP-server invariant exists to notice, and `media_mokuro.py` is the only standing exemption. **This is an escalation, not a plan decision** (FR-016, research.md R7). Loopback default and the logged non-loopback warning carry over `37495e22`; the existing `security_scan((8766,))` startup verification stays. |
| VII Contract stability | PASS | Zero ToolSpecs added or changed. The *external* protocol contract is deliberately frozen byte-for-byte, which is the strongest form of the principle applied to a non-MCP surface. Ledger row filed before code (TG1). |

**Post-design re-check**: PASS — Complexity Tracking empty. The one open governance item
(the allowlist decision) is escalated, not absorbed.

## Deliberate omissions (recorded so they are not mistaken for oversights)

**No `data-model.md`.** 009 stores nothing: no config key added, no table, no persisted
file, no new tool payload. Its structured types are three in-memory objects local to the
new module (the server handle, a connected-client record, a pending-reply record) and the
existing `BridgeLaunchResult`. The *wire* shapes — which are the only structures anyone
outside the module sees — are enumerated exhaustively in research.md R1 with `main.go` line
citations; a data-model document would restate that table with less provenance.

**No `contracts/` directory.** This feature has exactly one contract and it is not ours to
author: the asbplayer WebSocket/HTTP protocol. Copying research.md R1's table into
`contracts/` would create a second place for it to go stale, and the authoritative source
stays the cited Go file. 007 had a `contracts/` directory because it *defined* a new tool
payload; 009 defines nothing.

**No held-out validation suite. Replaced by a differential gate.** The case for a holdout
here is real — a protocol reimplementation is exactly the situation where tests authored
before the implementation are valuable — and it is rejected for a specific reason: a
held-out suite would be **transcribed from `main.go`, the same source the implementer must
read**, so it would not be independent evidence. It would test whether two people read the
same file the same way. The genuinely independent oracle is the **Go bridge itself**, which
still exists on this machine and still runs. So TG3 §5 runs the same scripted extension
double and the same client against *both* servers and diffs status codes and response
bodies route by route (SC-004), and TG3 §6 puts the **real browser extension** in front of
the Python bridge once, by hand (research.md O-1). That is stronger validation data than a
holdout, costs less machinery, and cannot be satisfied by a plausible-looking
reimplementation of a misreading. **Conclusion: no holdout for 009**; the differential run
and the real-machine observation are mandatory gate steps, and the gate fails if either is
skipped.

## Project Structure

### Documentation (this feature)

```text
specs/009-asbplayer-bridge-in-process/
├── spec.md              # What/why, user stories, FRs, SCs
├── plan.md              # This file
├── research.md          # The protocol contract (cited to main.go), stack choice, governance
├── quickstart.md        # Verify runbook (also the TG3 gate script)
└── tasks.md             # Taskgroups TG1 → TG2 → TG3
```

(no `data-model.md`, no `contracts/`, no `holdout/` — see Deliberate omissions)

### Source Code (repository root)

```text
src/katagiri/
├── asbplayer_bridge.py  # NEW, lane wt/009-bridge: aiohttp app on a daemon-thread event
│                        #   loop — /ws + framing, messageId registry + 5 s deadline, the
│                        #   five relay endpoints, /disconnect-ws-clients, the AnkiConnect
│                        #   proxy with the addNote intercept. Imports katagiri.config only.
├── asbplayer_launch.py  # REWRITTEN (serial-on-main): same public names
│                        #   (bridge_is_healthy, bridge_port_is_occupied,
│                        #   ensure_asbplayer_bridge, BridgeLaunchResult), new body — hosts
│                        #   instead of spawning. Loses shutil/subprocess entirely.
├── mcp_server.py        # HOT (serial-on-main): ~5 lines in main()'s startup block.
├── media_asbplayer.py   # UNTOUCHED — SC-003 makes this a requirement, not an intention.
└── config.py            # UNTOUCHED — asbplayer_bridge_dir stays loadable and unused
                         #   (FR-014); its template comment is updated by the docs task
                         #   ONLY if tests/test_skeleton.py:146 does not pin the string.

tests/
├── test_asbplayer_bridge.py   # lane wt/009-bridge: real loopback WS client on port 0
├── test_asbplayer_launch.py   # serial-on-main: rewritten for the hosted bridge
├── test_bverify.py            # FROZEN until the user decision; then one allowlist line
├── test_cverify.py            # FROZEN until the user decision; then one allowlist line
├── test_media_asbplayer.py    # MUST NOT CHANGE (SC-003)
└── test_everify.py            # MUST NOT CHANGE (SC-003)

docs/
├── decisions-ledger.md        # serial: the 009 row (D-50 expected — confirm at exec)
├── audit-log.md               # serial: reasoning entry
└── asbplayer-bridge.md        # lane wt/009-docs: operator doc (FR-018)

pyproject.toml                 # serial: one dependency line (aiohttp)
```

**Structure Decision**: single project, existing layout. One worktree lane for the new
module and its tests (`wt/009-bridge`), one for the doc (`wt/009-docs`), and a serial-on-main
track for `asbplayer_launch.py`, `mcp_server.py`, `pyproject.toml`, and the gated allowlist
edit. `mcp_server.py` is the declared hot file and is written by exactly one task.

**Branch note**: lanes branch from **`main`** (this repo's default branch); specs/README.md's
`master` wording is stale, as 008's plan already recorded.

## Key design decisions (detail and rationale in research.md)

1. **`aiohttp`, one new runtime dependency** (R6). The port must carry a WebSocket upgrade
   *and* nine HTTP routes, three with request bodies. `websockets` alone cannot serve the
   HTTP half (its `process_request` hook has no body); stdlib alone means hand-writing RFC
   6455 framing against a real browser client, which constitution II calls reimplementation
   and which is the single highest-risk thing this feature could do. **This is a genuine
   change to the dependency surface and TG1's ledger row must record it**, including the
   pinned range and the transitive tree (research.md O-6). Rejected fallback, recorded for
   the record: `websockets` sans-io over `asyncio.start_server` with hand-written HTTP/1.1.
2. **The AnkiConnect outbound leg uses `http.client` in a worker thread**, not an async
   client (R6). FR-007 wants byte-exact passthrough; a client that manages `Host`,
   `Content-Length`, and hop-by-hop headers for you is the wrong instrument, and
   `http.client` is already the house pattern in `media_asbplayer.py` and
   `asbplayer_launch.py`. Cost: the module needs an `HTTP_CLIENT_ALLOWLIST` entry **as well
   as** an `HTTP_SERVER_ALLOWLIST` entry.
3. **Both allowlist additions are escalations, not decisions** (R7, FR-016). `media_mokuro.py`
   is the only standing server exemption and D-47 is the precedent that a client exemption
   goes to the user with a ledger row. No implementation task may touch `test_bverify.py`
   or `test_cverify.py`; a single dedicated serial task applies the edit *after* the user's
   answer is in the ledger. **If the answer is no, the feature stops** — there is no
   in-process bridge without a listening socket, and no workaround will be invented.
4. **Clean cutover, not a config-gated fallback.** The Go spawn path is deleted outright:
   `shutil`, `subprocess`, the `main.go` probe, and the Go-on-PATH check all leave
   `asbplayer_launch.py`. Rationale: a fallback would mean maintaining two implementations
   of one port, and every bug report would start with "which one was running?". The escape
   hatch that matters already exists and is *kept*: `bridge_port_is_occupied()` means a
   learner who starts the old Go bridge by hand still gets a working channel, because
   Katagiri stands down and speaks to whatever is there (FR-011, spec US3 acceptance 3).
   That gives the rollback property of a fallback with none of the code.
5. **`asbplayer_bridge_dir` stays loadable and becomes inert** (FR-014). Deleting the key
   would break `tests/test_skeleton.py:115,146`, which pin the known-keys list and the
   config-template line, for no benefit. It is accepted, ignored, and reported once so the
   operator is not left believing it still does something.
6. **`asbplayer_launch.py` keeps its public names.** `ensure_asbplayer_bridge()`,
   `BridgeLaunchResult`, `bridge_is_healthy()`, `bridge_port_is_occupied()` all survive with
   their signatures. That keeps `mcp_server.py`'s startup block and
   `tests/test_mcp_tools.py:2823-2851`'s monkeypatch valid, and confines the hot-file diff
   to log wording.
7. **Per-`messageId` pending registry, not a shared response channel** (R4/G-4). The Go
   implementation's single channel lets one waiter eat another's reply; it never bites
   today only because Katagiri issues one request at a time. The replacement fixes it. This
   is the one place the reimplementation deliberately diverges from the reference, and it
   is a strict improvement with no observable contract change.
8. **The known Go defects G-1..G-6 are not reproduced** (R4): no double-slashed proxy URL,
   no `Content-Type[0]` index panic, no unchecked note-body type assertion, no request-body
   logging (Katagiri logs to stderr, never stdout, and never note contents — FR-017), and a
   bounded timeout on the AnkiConnect leg. Each divergence is listed with its reasoning so
   a reviewer diffing against `main.go` does not read them as mistakes.
9. **Own loop on a daemon thread, with an explicit `stop()`** (R6). `main()` is
   synchronous and ends in a blocking `server.run`, so the bridge cannot share its loop.
   `stop()` exists for the tests: a suite that leaks a listener between cases is how port
   8766 ends up occupied for the next developer (SC-006).
10. **A bridge-readiness doctor row is in scope; a bridge MCP tool is not.** 008
    deliberately added no bridge row (008 research.md R4) precisely so 009 could choose.
    The row is a natural home for FR-014's "your `asbplayer_bridge_dir` is obsolete" notice
    and FR-018's lifetime warning. It is scoped to the *smallest useful* addition and is
    explicitly droppable if it threatens the taskgroup budget.

## Risks

| Risk | Mitigation |
|---|---|
| **The allowlist decision goes against the feature** (FR-016) | Escalated in TG1 *before* any code task, exactly so the answer arrives while the cost of stopping is one ledger row. TG2 does not start until it is answered. |
| The real extension behaves differently from the Go bridge's assumed client (O-1) | TG3's real-machine step is mandatory, not optional; the differential run (SC-004) catches divergence at the HTTP layer before the extension ever sees it. |
| A subtle mining regression nobody notices, because Katagiri never calls that path (R5) | US2 is a P1 story with its own acceptance scenarios and its own tests against a stub AnkiConnect; the differential run covers every proxy branch. |
| Upstream #1087 merges and changes the protocol mid-flight (R8) | A dedicated TG2 re-check task, with an explicit stop-and-replan instruction rather than "absorb the change". |
| `aiohttp`'s transitive tree conflicts with `mcp`/`fugashi` or needs a build step (O-6) | The re-check/dependency task runs `uv sync` and reports the resolved tree; the gate confirms a clean resolve on this machine. |
| Windows rebind refusal after a fast restart (O-2) | `SO_REUSEADDR` decided deliberately in the module, and the gate exercises an explicit stop/start cycle. |
| A leaked listener wedges port 8766 for later runs (SC-006) | Every test binds port 0; `stop()` is part of the module's contract; the gate checks the port is free after the full suite. |
| Losing the bridge's independent lifetime hurts the learner (O-5) | Documented in the spec, the doc, and the doctor row rather than hidden; the rejected child-process option stays on record if it turns out to matter. |

## Complexity Tracking

*(empty — no constitution violations to justify. The Principle VI item is an escalation
awaiting a user decision, recorded in the Constitution Check row and in TG1, not a
violation being waived here.)*
