# Implementation Plan: 008 — Browser Companion Check

**Branch**: `008-browser-companion-check` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/008-browser-companion-check/spec.md`

## Summary

Teach the installer's doctor about the three learner-side browser companions it currently
cannot see — Yomitan, the asbplayer extension, and the mokuro page-change bridge — and give
the operator a handoff instead of an install. A new pure module
`src/katagiri/companions.py` reads Chromium (and best-effort Firefox) profile data
read-only to decide present / absent / undetermined per profile, carries the store-listing
catalog and the manual steps, and probes the pinned mokuro loopback port with a bounded
socket connect. `installer.py` gains exactly two small blocks: `probe_companions()` feeding
`collect_doctor_statuses()`, and one appended wizard step (+ post-wizard re-check entry)
that prints the handoff and re-checks on demand. Absence maps to `MANUAL STEP`, so
`--check` exit codes are unchanged. No MCP tool, no schema change, no HTTP client, no
extension is ever installed or modified.

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`), uv-managed

**Primary Dependencies**: stdlib only — `pathlib`, `json`, `socket`, `os`. No new runtime
dependency; `companions.py` imports nothing from the package except `katagiri.config`
(for `MOKURO_BRIDGE_PORT`), mirroring installer.py's own top-level-import rule

**Storage**: none. 008 persists nothing — no config key, no table, no file. (This is why
there is no `data-model.md`; see below.)

**Testing**: pytest, general group (`tests/conftest.py`); synthetic browser-profile trees
built in `tmp_path`, never a real profile; installer paths exercised as subprocess runs in
the 007 `tests/test_installer_setup.py` sandbox style (`LOCALAPPDATA` + `KATAGIRI_CONFIG`
redirected, `PYTHONUTF8=1`)

**Target Platform**: Windows 11 only

**Project Type**: single project (MCP server + installer CLI); this feature is CLI-only

**Performance Goals**: companion check adds ≤ 2 s to a doctor run (SC-005); every source
read is individually time-bounded and degrades to "undetermined"

**Constraints**: `--check` read-only (existing contract); browser profiles read-only
(FR-002); no silent install / no extension file manipulation (spec boundaries 1–2); no new
HTTP client — bounded loopback `socket.create_connection` only, and the
`HTTP_CLIENT_ALLOWLIST` in `tests/test_bverify.py`/`tests/test_cverify.py` must stay
byte-unchanged (FR-013); no secret values in output (FR-014); no MCP contract growth
(FR-012); nothing in `asbplayer_launch.py` / `media_asbplayer.py` / `media_mokuro.py` is
edited, so `009-asbplayer-bridge-in-process` is unconstrained

**Scale/Scope**: 1 new module (~250 LOC), 2 small blocks in `installer.py`, 2 new test
files, 1 operator doc, 1 ledger row. 0 new MCP tools (count unchanged).

## Constitution Check

*GATE: evaluated against constitution v1.4.0.*

| Principle | Verdict | Notes |
|---|---|---|
| I MCP ceiling | PASS | No app, no GUI, no service, no own player. A CLI doctor row about third-party browser tools is the opposite of raising the ceiling — it hands the install back to the operator. |
| II OSS-first | PASS | Integrates existing OSS (Yomitan, asbplayer, mokuro) by pointing at their official distributions; reimplements nothing, vendors nothing, adds no build-list item. |
| III Event log sacred | PASS | No DB access at all. No schema change, no migration, no event written. |
| IV Study-first gating | PASS | Setup infrastructure, not a phase — same posture as 005 and 007. No phase-entry check, no interaction with D-19/D-33. |
| V Two-gate verification | PASS (adapted) | No learner metric (no study surface). Cold-subagent analog: TG3's gate runs the full suite plus the invariant scans and the read-only/no-install proofs, by a dedicated agent. Argued exemption recorded here, mirroring 005/007. |
| VI Security hardening | PASS | stdio-only unchanged (no server code touched). No network listener added; the only network syscall is a bounded loopback TCP connect to the already-hardened 8767. Browser profiles are read-only. No secret value printed (mokuro secret reported as set/unset, matching config.py's `_SECRET_KEYS` posture). Explicitly refuses the enterprise-policy install route, which would mean writing machine security policy. |
| VII Contract stability | PASS | Zero ToolSpecs added or changed — the strongest form of "additive only". Still requires a decisions-ledger row before the code tasks, per the ledger-first discipline (TG1). |

**Post-design re-check**: PASS — Complexity Tracking empty.

## Deliberate omissions (recorded so they are not mistaken for oversights)

**No `data-model.md`.** 008 stores nothing: no config key, no table, no persisted file, no
new tool payload. The only structured things are two in-memory dataclasses local to
`companions.py` (a companion catalog entry and a per-companion status), and their fields
are fully specified by spec.md's Key Entities plus the existing `ComponentStatus` row shape
in `installer.py:120-127`. A data-model document here would restate one dataclass and go
stale. 007 had one because it defined a tool payload and log-record formats; 008 defines
neither.

**No held-out validation suite.** 007's holdout existed because the user asked for
stability tests authored before planning, as unbiased validation data for a feature whose
whole point was setup robustness. 008 is a doctor UX feature: its surface is a few CLI rows
and a URL, its riskiest behaviors (read-only profile access, no-install, exit-code
stability) are best proven by *direct* assertions the implementer should be reading —
"hash the profile tree before and after" and "the allowlist file is unchanged" are checks
that work better when the implementer knows they exist and writes code that satisfies them
than when they are sprung at a gate. The holdout machinery (a separate collection root, a
manifest, a governance clause, a frozen-history verification step) would cost more than the
feature does. **Conclusion: no holdout for 008.** The TG3 gate carries the equivalent
adversarial checks directly (quickstart §5–§7).

**No contracts/ directory.** Nothing here is a contract: no tool, no endpoint, no file
format. The store-id catalog is the closest thing and it lives in code with research.md R5
as its provenance.

## Project Structure

### Documentation (this feature)

```text
specs/008-browser-companion-check/
├── spec.md              # What/why, user stories, FRs, SCs
├── plan.md              # This file
├── research.md          # Detection options, mokuro reality, 008/009 boundary, open items
├── quickstart.md        # Verify runbook (also the TG3 gate script)
└── tasks.md             # Taskgroups TG1 → TG2 → TG3
```

(no `data-model.md`, no `contracts/`, no `holdout/` — see Deliberate omissions)

### Source Code (repository root)

```text
src/katagiri/
├── companions.py        # NEW, lane wt/008-companions: profile scan, catalog, handoff
│                        #   text, bounded mokuro port probe. stdlib + katagiri.config only.
├── installer.py         # HOT (serial-on-main): probe_companions() into
│                        #   collect_doctor_statuses(); one appended STEP_LABELS entry +
│                        #   step runner + post-wizard re-check entry. Two tasks, never
│                        #   concurrently.
├── media_mokuro.py      # UNTOUCHED (read-only reference: port + secret semantics)
├── asbplayer_launch.py  # UNTOUCHED (009's territory)
└── media_asbplayer.py   # UNTOUCHED (009's territory)

tests/
├── test_companions.py            # lane wt/008-companions: synthetic profile trees
└── test_installer_companions.py  # serial-on-main: doctor rows, exit code, wizard step

docs/
├── decisions-ledger.md           # serial: the 008 row (D-49 expected — confirm at exec)
├── audit-log.md                  # serial: reasoning entry
└── browser-companions.md         # lane wt/008-docs: operator doc (FR-015)
```

**Structure Decision**: single project, existing layout. Two worktree lanes
(`wt/008-companions`, `wt/008-docs`) plus a serial-on-main track for `installer.py` and its
tests. `installer.py` is the only hot file and is written by exactly two tasks, strictly
sequenced. Full Workfile & conflict map is in tasks.md.

**Branch note**: `specs/README.md`'s execution model says lanes branch from `master`; this
repository's actual default branch is **`main`** (`origin/HEAD → origin/main`). 008's lanes
branch from `main`. The README sentence is stale for this repo state and is left for a
separate cleanup rather than edited as a side effect of this feature.

## Key design decisions (detail and rationale in research.md)

1. **Primary signal is the extension payload directory** (`…\User Data\<Profile>\Extensions\<id>\`),
   enriched — never overruled — by the profile `Preferences` JSON (R1). A filesystem
   `is_dir()` is lock-free against a running browser and immune to JSON layout drift.
2. **Three outcomes, per profile** — present / absent / undetermined, each with evidence
   (R1, spec US3). "Could not look" is never printed as "not installed".
3. **Absent ⇒ `MANUAL STEP`, never `MISSING`** (R6), so `doctor_exit_code()`
   (installer.py:795) is untouched and SC-004 holds. Same argument `probe_irodori` and
   `probe_schtasks` already record in-line.
4. **mokuro is a configuration-readiness row, not a liveness row** (R3): the bridge is
   Katagiri's own server, is not running at install time, and the driving userscript is not
   shipped in this repo. Report secret set/unset + port free/occupied, and say plainly that
   "free" is expected.
5. **The port probe is `socket.create_connection` with a ~0.2 s timeout — never an HTTP
   client** (R3). It matches neither `HTTP_CLIENT_PATTERNS` nor `HTTP_SERVER_PATTERNS` in
   `tests/test_bverify.py`, so **no allowlist entry and no D-47-style exemption row is
   required**. This is a hard constraint, not a preference: if an implementer finds they
   need `http.client`/`urllib.request` here, the task **stops and escalates to the
   orchestrator** rather than adding an allowlist entry — an allowlist change would need
   its own user decision and ledger row, mirroring D-47.
6. **Detection lives outside installer.py** (R6), imported lazily inside the probe, so the
   hot-file diff is two small blocks and the detector is unit-testable without a subprocess.
7. **008 says nothing about the asbplayer bridge** (R4) — extension presence only — so
   `009-asbplayer-bridge-in-process` inherits no doctor row it must preserve or migrate.
8. **One catalog constant owns every store id and URL** (R5); the doc and the handoff text
   render from it, so re-verification is a one-file edit.

## Risks

| Risk | Mitigation |
|---|---|
| False "present" from a stale extension directory after uninstall (open item O-2) | `Preferences` cross-check downgrades to present-with-caveat; verdict wording never claims certainty the signal cannot support |
| False "absent" for an unpacked/dev-loaded extension | Row wording names the id-based method so the operator recognises the case; covered by an explicit edge case in spec.md |
| Firefox specifics unverified (O-3) | Firefox row degrades to "not covered" unless the task verifies the format; Firefox coverage is never asserted for asbplayer |
| Store ids drift / listing moves (O-6) | Single catalog constant with a provenance comment citing research.md R5; failure is operator-visible (404), not silent |
| Doctor run slowed by a huge/networked profile tree | Per-source time budget + overall bound; timeout degrades to "undetermined" (FR-011, SC-005) |
| An implementer "helpfully" adds an install shortcut | Boundary is in the spec, the ledger row, and a dedicated test (SC-003); TG3 gate re-checks it |

## Complexity Tracking

*(empty — no constitution violations to justify)*
