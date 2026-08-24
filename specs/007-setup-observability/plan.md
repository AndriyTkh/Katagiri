# Implementation Plan: 007 — Setup Observability & Cross-Client Connection Diagnostics

**Branch**: `007-setup-observability` | **Date**: 2026-08-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-setup-observability/spec.md`

## Summary

Make the install chain testable and observable: (1) automated tests over
`setup.bat` → `uv sync` → `python -m katagiri.installer` (11-step wizard, `--yes`,
`--check`, retry/skip/abort) and the `.mcp.json` → `agent/scripts/setup.py
--stdio-bootstrap` → `katagiri.mcp_server` launch chain, all in isolated
`LOCALAPPDATA`/`KATAGIRI_CONFIG` sandboxes; (2) file logging for the bootstrap
launcher + client-identity/lifecycle records in the server log; (3) one additive MCP
tool `connection_status` (ledger row first) reporting runtime identity, path map, and
secret-presence flags; (4) a held-out stability suite (authored pre-tasks, invisible to
implementers) run only at the gate.

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`), uv-managed

**Primary Dependencies**: `mcp>=2,<3` (stdio server), stdlib `logging` +
`katagiri.applog` rotating handler; no new runtime dependencies

**Storage**: existing rotating log files under `%LOCALAPPDATA%\Katagiri\logs\`
(katagiri.log + new bootstrap.log); NO use of the append-only study event log for
diagnostics (constitution III)

**Testing**: pytest; groups per `tests/conftest.py` (`compile`/general/`mcp`);
real-server tests via the `_StdioClient`/`mcp_client` subprocess pattern
(tests/test_abc_workflow.py:469-626) with `LOCALAPPDATA` redirected per test

**Target Platform**: Windows 11 only

**Project Type**: single project (MCP server + installer CLI)

**Performance Goals**: `connection_status` answers < 5 s (SC-002); default suite
wall-clock growth ≤ 10% (SC-006)

**Constraints**: stdio stdout = pure JSON-RPC (all diagnostics stderr + file);
secrets never in logs/outputs (constitution VI); contract changes additive-only
(constitution VII); install tests never register real Windows scheduled tasks; never
touch real `%LOCALAPPDATA%\Katagiri`

**Scale/Scope**: 1 new MCP tool (26 → 27), ~1 module (`installer` test surface),
~5 new test files + 1 held-out suite, 1 short operator doc

## Constitution Check

*GATE: evaluated against constitution v1.4.0.*

| Principle | Verdict | Notes |
|---|---|---|
| I MCP ceiling | PASS | No app/GUI/service; one diagnostic tool inside the existing server. |
| II OSS-first | PASS | No new build-list item; stdlib logging only. |
| III Event log sacred | PASS | Diagnostics go to rotating text logs, never the event table; no schema change, no migration. |
| IV Study-first gating | PASS | Infrastructure feature, not a phase — same posture as 005 ("no study surface"). No D6/006-gate interaction. |
| V Two-gate verification | PASS (adapted) | Cold-subagent analog = the held-out suite executed at the gate task; learner metric N/A (no study surface) — recorded here as the argued exemption, mirroring 005. |
| VI Security hardening | PASS | stdio-only unchanged; `connection_status` reports paths + presence flags, never token values; `redact()` wraps the adapter; leak checks extended (FR-011). Paths are not secrets in this repo's model (config.py excludes only `obsidian_api_token`/`mokuro_shared_secret` from repr). |
| VII Contract stability | CONDITIONAL PASS | One additive ToolSpec. Requires decisions-ledger row (D-24 justification) filed **before** the code task — this is task T001, blocking. |

**Post-design re-check**: PASS — design adds no violation; Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/007-setup-observability/
├── plan.md              # This file
├── research.md          # Phase 0 decisions
├── data-model.md        # Report/record shapes
├── quickstart.md        # Gate/validation runbook
├── contracts/
│   └── connection_status.md   # New tool contract
├── holdout/             # HELD-OUT stability suite — DO NOT READ during implementation
│   ├── MANIFEST.md      # authorship freeze + run instructions
│   ├── conftest.py      # self-contained fixtures (server spawn, sandbox)
│   └── test_stability_*.py
└── tasks.md             # /speckit-tasks output
```

### Source Code (repository root)

```text
src/katagiri/
├── mcp_server.py        # HOT (serial-on-master): connection_status adapter + logic fn;
│                        #   client-identity capture at handshake; lifecycle log lines
├── tool_registry.py     # HOT (serial-on-master): _INFRA_007_SPECS fragment appended
├── installer.py         # read-mostly; only if FR-010 log-gap found (expected: none)
├── config.py            # read-only (config_path/get_config reused)
└── applog.py            # read-only (logs_dir/log_file_path reused)

agent/scripts/
└── setup.py             # lane wt/007-bootstrap: file logging (bootstrap.log), phases

tests/
├── test_installer_setup.py   # lane wt/007-install-tests: --yes/--check/wizard paths
├── test_launch_chain.py      # lane wt/007-install-tests: .mcp.json chain, mcp group
├── test_bootstrap_log.py     # lane wt/007-bootstrap
├── test_connection_status.py # serial-on-master (follows the tool)
└── test_mcp_tools.py         # serial-on-master: congruence table grows by one

docs/
├── decisions-ledger.md       # serial: D-39 row (additive tool + holdout governance)
└── setup-observability.md    # lane wt/007-docs: operator doc (FR-013)
```

**Structure Decision**: single project, existing layout; three worktree lanes
(`wt/007-install-tests`, `wt/007-bootstrap`, `wt/007-docs`) + serial-on-master tasks for
the two hot files and `tests/conftest.py`/`test_mcp_tools.py`. Full Workfile & conflict
map lands in tasks.md.

## Held-out suite protocol (binding on task generation)

- Authored **before** `/speckit-tasks` runs, by a dedicated author session, from
  spec.md + the public contracts/ only.
- Lives at `specs/007-setup-observability/holdout/` — outside `tests/`, so the default
  `uv run pytest` (rootdir collection of `tests/`) never collects it. Explicit run:
  `uv run pytest specs/007-setup-observability/holdout -q`.
- Every implementation task's Read list MUST NOT include any `holdout/` path, and every
  implementation dispatch prompt carries: "Do not read, copy, or modify
  specs/007-setup-observability/holdout/ — held-out validation data."
- Only the gate task runs it; a failure fixes the implementation. Changing a held-out
  test requires an explicit user decision recorded in the gate task text.
- `MANIFEST.md` records the authoring commit; the gate verifies
  `git log --oneline -- specs/007-setup-observability/holdout/` shows no post-authoring
  modification.

## Complexity Tracking

*(empty — no constitution violations to justify)*
