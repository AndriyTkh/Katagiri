# Tasks: 007 — Setup Observability & Cross-Client Connection Diagnostics

**Input**: Design documents from `specs/007-setup-observability/` (spec.md, plan.md,
research.md, data-model.md, contracts/connection_status.md, quickstart.md)

**Tests**: explicitly requested by the feature — install-chain tests ARE the feature
(US1); plus the held-out gate (US4).

## HELD-OUT RULE (binding, every dispatch)

`specs/007-setup-observability/holdout/` is validation data, authored 2026-08-24
(commits `77792d9` + `4cedd6e` adding the instance module, both pre-tasks
finalization), run only at T015. **No implementation task may read, copy, or modify
anything under `holdout/`.** Every dispatch prompt carries this sentence verbatim:
*"Do not read, copy, or modify specs/007-setup-observability/holdout/ — held-out
validation data."* A gate failure fixes the implementation; changing a held-out test
requires an explicit user decision recorded in T015.

## Format: `[ID] [P?] [Story] Description` + Lane / Model / Write / Read lists

- **[P]**: parallelizable (own worktree lane, no shared files)
- Lanes branch from `master` at taskgroup start; hot files
  (`src/katagiri/tool_registry.py`, `src/katagiri/mcp_server.py`) are serial-on-master.
- **Model**: sonnet-high default; opus-mid where marked (hard task).

## Workfile & conflict map

| File | Owner | Mode |
|---|---|---|
| docs/decisions-ledger.md | T001 | serial-on-master |
| docs/audit-log.md | T001 | serial-on-master |
| src/katagiri/tool_registry.py | T002 | serial-on-master (HOT) |
| src/katagiri/mcp_server.py | T002, then T003 | serial-on-master (HOT, one task at a time) |
| tests/test_connection_status.py | T004 | serial-on-master (follows the tool) |
| tests/test_mcp_tools.py | T004 | serial-on-master (shared congruence table) |
| tests/test_installer_setup.py | T005 | lane `wt/007-install-tests` |
| tests/test_launch_chain.py | T006 | lane `wt/007-install-tests` |
| agent/scripts/setup.py | T007, then T013 | lane `wt/007-bootstrap` (sequential within lane) |
| tests/test_bootstrap_log.py | T008 | lane `wt/007-bootstrap` |
| src/katagiri/config.py | T011 | lane `wt/007-instances` |
| src/katagiri/installer.py | T012 | lane `wt/007-instances` |
| tests/test_instances.py | T014 | lane `wt/007-instances` |
| docs/setup-observability.md | T009 | lane `wt/007-docs` |
| specs/007-setup-observability/holdout/** | — | FROZEN (gate reads only) |

Note: T005 subprocess-RUNS installer.py but never edits it; T012 edits it (additive
flag) — file-disjoint writes, behavior-compatible (existing flags unchanged). T002
READS config.py only. If T005 finds an FR-010 log gap in installer.py → new serial
task, escalate to orchestrator (do not fix inside the lane).

---

## Taskgroup TG1: Governance (serial, blocks everything)

- [x] T001 [Gate] (landed as **D-46**, ledger had advanced past D-39; commit 8faec0e) File decisions-ledger row **D-39** in `docs/decisions-ledger.md`:
      (a) additive `connection_status` ToolSpec (26 → 27 tools) with D-24 contract-diff
      justification — read-only diagnostic, no secret values, presence flags only,
      contract in specs/007-setup-observability/contracts/connection_status.md;
      (b) held-out-suite governance: authored pre-tasks (commits `77792d9` +
      `4cedd6e`), gate-only execution, modification
      requires explicit user decision; (c) side-by-side instances: additive
      `KATAGIRI_DATA_HOME` env override in `config_dir()` + installer `--data-home`
      persisting to `agent/.env` — same D-22 argument as the existing
      `KATAGIRI_CONFIG` precedent (config.py:188-194), default behavior unchanged,
      constitution I untouched (multiple installed copies, still one user). Add
      reasoning entry to
      `docs/audit-log.md` ("007 TG1 — connection_status + holdout governance"). No
      constitution bump needed (additive tool already VII-compliant; record that
      judgment in the audit-log entry).
      **Model**: sonnet-high. **Write**: docs/decisions-ledger.md, docs/audit-log.md.
      **Read**: docs/decisions-ledger.md (last ~5 rows for format + next D-number
      confirmation), specs/007-setup-observability/contracts/connection_status.md,
      specs/007-setup-observability/plan.md (§Held-out suite protocol).

**Checkpoint**: D-39 exists → TG2 may start.

---

## Taskgroup TG2: Implementation (3 parallel lanes + serial track)

### Serial-on-master track (strict order T002 → T003 → T004)

- [x] T002 [merged: 4ae918b; tool count landed 34→35, brief's 26→27 was stale] [US2] Implement `connection_status`: new `_INFRA_007_SPECS` fragment tuple
      in `src/katagiri/tool_registry.py` concatenated into `TOOL_SPECS`; logic function
      + thin `@server.tool` adapter (redact()-wrapped) in `src/katagiri/mcp_server.py`
      next to `ping`. Output per contract, exactly. Degraded states never raise
      (FR-006): missing config → `config_exists: false` with resolved default paths;
      locked/missing DB → `db_available: false` (bounded read-only open attempt);
      no client identity → `{"name": "unknown", "version": ""}`. Include `data_home`
      (= `str(config_dir())`) and `data_home_source` ("env" when `KATAGIRI_DATA_HOME`
      is set, else "default") — resolve via config accessors so the values stay correct
      whether or not T011 has merged yet. Client identity: read
      the initialize-handshake clientInfo off the session object — **verify the exact
      accessor against the pinned mcp SDK version first** (research.md D3); fall back
      to "unknown", never parse wire traffic. Secrets map = presence flags only.
      **Model**: opus-mid (SDK accessor verification + contract exactness).
      **Write**: src/katagiri/tool_registry.py, src/katagiri/mcp_server.py.
      **Read**: specs/007-setup-observability/contracts/connection_status.md,
      specs/007-setup-observability/research.md (D1–D3),
      src/katagiri/tool_registry.py:87-113 + 1555-1571 + 1609-1635 (seam, concat,
      redact/SECRET_WORDS), src/katagiri/mcp_server.py:548-593 + 725-745 (security_scan,
      ping, security_status patterns) + 567-576 (adapter seam comment),
      src/katagiri/config.py:180-225 + 440-443, src/katagiri/db.py:88-90,
      src/katagiri/applog.py:107-119, pyproject.toml:1-20 (version + mcp pin).

- [x] T003 [merged: 39a205d; client-connected line via first-call latch] [US3] Server lifecycle logging in `src/katagiri/mcp_server.py`: add pid to
      the startup log lines; after initialize handshake, log one INFO line
      `client connected: name=<x> version=<y> pid=<pid>` (reuse T002's clientInfo
      accessor); confirm per-tool-call lines (`_LoggedMCPServer.call_tool`) untouched.
      stdout stays pure JSON-RPC — logging via existing logger only. Depends: T002
      (same file, same accessor).
      **Model**: sonnet-high. **Write**: src/katagiri/mcp_server.py.
      **Read**: src/katagiri/mcp_server.py:89-170 + 1735-1830 (logger, _LoggedMCPServer,
      main), specs/007-setup-observability/data-model.md (§Server lifecycle records),
      T002's completion notes.

- [x] T004 [merged: ab68cef; config-write-on-boot behaviors pinned as in-process unit tests, rest over the wire] [US2] Tests for the tool: new `tests/test_connection_status.py` — over-the-wire
      call via the per-file-mirrored `_StdioClient` pattern (spawn in sandbox, handshake,
      tools/call): every contract field present + typed; paths point inside the sandbox;
      `db_available` false when sandbox has no DB; canary secret planted in sandbox
      config never appears in payload or stderr (extend the `_assert_clean` idiom);
      two sandboxes → different path maps. Extend `tests/test_mcp_tools.py` congruence
      table by the one new tool. Mark mcp-group tests per conftest convention.
      Depends: T002 (T003 harmless if merged first).
      **Model**: sonnet-high. **Write**: tests/test_connection_status.py,
      tests/test_mcp_tools.py.
      **Read**: specs/007-setup-observability/contracts/connection_status.md,
      tests/test_abc_workflow.py:460-640 (_StdioClient/mcp_client pattern + _assert_clean),
      tests/test_mcp_tools.py (congruence structure), tests/conftest.py:3-84 (groups).

### Lane `wt/007-install-tests` [P] [US1]

- [x] T005 [merged: 22d79f3; FR-010 gaps filed as T016] [P] [US1] `tests/test_installer_setup.py` (general group): subprocess runs of
      `python -m katagiri.installer` with `LOCALAPPDATA` + `KATAGIRI_CONFIG` sandboxed,
      `PYTHONUTF8=1`: `--yes` completes with stdin closed, writes config, prints
      per-step summary, logs steps to sandbox katagiri.log (FR-010 verification — if a
      step outcome is NOT reconstructible from the log, STOP and report the gap to the
      orchestrator instead of editing installer.py); `--check` read-only + non-zero exit
      naming the failing step on a broken sandbox (break preconditions inside the
      sandbox only, e.g. absent vendor data); scripted-stdin retry/skip/abort paths:
      Skip continues + shows in summary, abort logs step number; double `--yes`
      idempotent; scheduled-tasks step never registers real schtasks (suppressed under
      --yes; interactive tests always Skip it). Seed sandboxes from the cached JMdict
      template (tests/.cache) — never a ground-zero import. Timeouts on every
      subprocess (60s; 300s only if cache cold).
      **Model**: sonnet-high. **Write**: tests/test_installer_setup.py.
      **Read**: src/katagiri/installer.py:61-110 + 812-830 + 1261-1340 + 1417-1520 +
      1553-1580 (steps, retry/skip/abort, menus, main, flags),
      src/katagiri/config.py:180-199, src/katagiri/applog.py:107-119,
      tests/conftest.py:3-84 + 109-143 (groups, jmdict template fixture),
      specs/007-setup-observability/spec.md (US1 + edge cases).

- [x] T006 [merged: 0b2e5bf; first attempt hung on stderr pipe-buffer deadlock, retried with tree-kill + stderr pump] [P] [US1] `tests/test_launch_chain.py` (mcp group): spawn
      `python agent/scripts/setup.py --stdio-bootstrap` exactly as `.mcp.json` does
      (env `PYTHONUTF8=1`, sandboxed LOCALAPPDATA), initialize handshake completes,
      stdout carries only JSON-RPC frames (every non-empty line parses as JSON),
      stderr carries startup lines. Mirror the `_StdioClient` pattern per-file. Use
      `xdist_group` if it binds anything shared (it shouldn't — stdio only).
      **Model**: sonnet-high. **Write**: tests/test_launch_chain.py.
      **Read**: .mcp.json, agent/scripts/setup.py:1-130 + 440-520 (bootstrap phases,
      launch_server), tests/test_abc_workflow.py:460-640, tests/conftest.py:3-84.

### Lane `wt/007-bootstrap` [P] [US3]

- [x] T007 [merged: 959ea46] [P] [US3] File logging for `agent/scripts/setup.py`: self-contained stdlib
      FileHandler appending to `%LOCALAPPDATA%\Katagiri\logs\bootstrap.log`
      (create dirs; NO import of katagiri — research.md D4); every `say()/warn()/ok()`
      line mirrored with timestamp + pid + phase; failure before server exec is
      recorded with phase + reason; unwritable log dir degrades to stderr-only
      (try/except OSError), never crashes; stdout untouched (JSON-RPC channel after
      exec). Secret env values (SECRET_VARS set) stay presence-only, as today.
      **Model**: sonnet-high. **Write**: agent/scripts/setup.py.
      **Read**: agent/scripts/setup.py (whole file — it is the task target),
      specs/007-setup-observability/data-model.md (§BootstrapLogRecord),
      specs/007-setup-observability/research.md (D4).

- [x] T008 [merged: 92c37eb] [P] [US3] `tests/test_bootstrap_log.py`: sandboxed bootstrap run writes
      bootstrap.log with phase lines; forced pre-exec failure (env override pointing
      at a nonexistent server module) is reconstructible from the file alone; canary
      secret in env never appears in the log; unwritable logs dir → process still
      works, warns on stderr. Depends: T007 (same lane, sequential).
      **Model**: sonnet-high. **Write**: tests/test_bootstrap_log.py.
      **Read**: agent/scripts/setup.py (post-T007), tests/conftest.py:3-84,
      specs/007-setup-observability/spec.md (US3 acceptance + edge cases).

### Lane `wt/007-bootstrap` (continued, after T008)

- [x] T013 [merged: 8b412a9; also fixed launch_server KATAGIRI_CONFIG env-clobber bug] [P] [US5] Instance-env passthrough in `agent/scripts/setup.py`: read
      `KATAGIRI_DATA_HOME` and `KATAGIRI_CONFIG` from `agent/.env` (same mechanism as
      `KATAGIRI_PYTHON`/`KATAGIRI_MODULE` today) and forward them into the server's
      environment at exec; already-set process env wins over `.env` (client may inject
      env via `.mcp.json`). Write `bootstrap.log` under the resolved data home (reuse
      T007's logger, honoring the override). Depends: T007 (same file).
      **Model**: sonnet-high. **Write**: agent/scripts/setup.py.
      **Read**: agent/scripts/setup.py (post-T007), specs/007-setup-observability/
      research.md (D9), specs/007-setup-observability/spec.md (US5).

### Lane `wt/007-instances` [P] [US5]

- [x] T011 [merged: b96ca41] [P] [US5] `KATAGIRI_DATA_HOME` in `src/katagiri/config.py`: `config_dir()`
      returns `Path($KATAGIRI_DATA_HOME)` when set — absolute path required; empty or
      relative value raises `ConfigError` with an explicit message (NEVER silently
      fall back to the default home — that would risk the real study DB). Docstring
      mirrors the `KATAGIRI_CONFIG` D-22 paragraph (config.py:188-194). Precedence
      documented: `KATAGIRI_CONFIG` (file) > `KATAGIRI_DATA_HOME` (home) > default.
      `logs_dir()` follows automatically (applog.py:107-114) — do not touch applog.py.
      **Model**: sonnet-high. **Write**: src/katagiri/config.py.
      **Read**: src/katagiri/config.py:160-200, src/katagiri/applog.py:100-120,
      specs/007-setup-observability/research.md (D9).

- [x] T012 [merged: 8d4d6f6] [US5] Installer `--data-home PATH` in `src/katagiri/installer.py`: resolves
      the whole run (config, db path written into config.toml, logs) under PATH —
      implement by setting `KATAGIRI_DATA_HOME` in the process env early in `main()`
      (before any config access), so every step inherits it through T011's seam;
      validate absolute path, create the directory if missing; persist
      `KATAGIRI_DATA_HOME=PATH` into the checkout's `agent/.env` (append/update key,
      create file if absent — untracked file, never touch `.mcp.json`); doctor summary
      prints the active data home. Default run (no flag, no env) byte-identical
      behavior to today. Depends: T011 (same lane, sequential).
      **Model**: sonnet-high. **Write**: src/katagiri/installer.py.
      **Read**: src/katagiri/installer.py:61-110 + 812-830 + 1553-1580,
      src/katagiri/config.py (post-T011), specs/007-setup-observability/research.md
      (D9), agent/scripts/setup.py:1-60 (the .env format it must stay compatible with).

- [x] T014 [merged: 004f54d] [US5] `tests/test_instances.py` (general group; mcp-group marks only where
      a real server is spawned): env var isolates config+logs into a fresh home;
      installer `--data-home` puts config/db/logs under the home AND persists the key
      into a sandboxed `agent/.env` copy (never the real one); default run untouched
      (regression); precedence KATAGIRI_CONFIG > KATAGIRI_DATA_HOME; invalid override
      (empty/relative) → explicit error and NOTHING written to the default home; two
      homes → two concurrent servers, `connection_status` reports distinct `data_home`
      with source "env" (needs T002 merged — coordinate with orchestrator, or mark
      that one test `xfail` until TG2-serial merges and flip in the same lane before
      merge). Depends: T011, T012.
      **Model**: sonnet-high. **Write**: tests/test_instances.py.
      **Read**: specs/007-setup-observability/spec.md (US5 + edge cases),
      specs/007-setup-observability/contracts/connection_status.md,
      tests/test_abc_workflow.py:460-640 (client pattern), tests/conftest.py:3-84,
      src/katagiri/config.py + src/katagiri/installer.py (post-T011/T012).

### Lane `wt/007-docs` [P]

- [x] T009 [merged: 77c8a97] [P] [US1/US3/US5] `docs/setup-observability.md` (FR-013, operator-facing,
      ~1 page): where logs live (katagiri.log, bootstrap.log, what each records), how
      to read a failed install from logs alone, how to run the install tests, how to
      call `connection_status` and read its fields for cross-client diagnosis
      (including `data_home`/`data_home_source` for "which instance is this?"), how to
      install a side-by-side testing instance (`setup.bat -- --data-home <path>` in
      the second checkout), how the gate runs the held-out suite (command only — no
      holdout contents). Link from README.md if a docs index exists there (one line
      max).
      **Model**: sonnet-high. **Write**: docs/setup-observability.md, README.md (one
      line, only if an index section exists).
      **Read**: specs/007-setup-observability/quickstart.md,
      specs/007-setup-observability/contracts/connection_status.md,
      specs/007-setup-observability/data-model.md, README.md.

- [x] T016 [merged: 9208db7] [US1] (filed by orchestrator 2026-08-24 per T005 FR-010 findings) installer.py
      log-observability fixes, serial-on-master AFTER wt/007-instances merges:
      (a) `_run_step_with_retry` (~1321-1358): log an explicit line when operator picks
      Skip (and Retry), not just Abort — a scripted Skip currently leaves no
      distinguishing record (log shows only "ACTION NEEDED");
      (b) `_print_doctor_summary` (~1372-1377): mirror the per-component doctor table
      through `_log` so end-state (READY/MISSING/MANUAL STEP) is reconstructible from
      the log alone;
      (c) `_ro_query_scalar` (~328-348): catch `sqlite3.DatabaseError` (not just
      OperationalError) so a corrupted DB file makes `--check` report MISSING instead
      of crashing with a traceback. Then un-NOTE/extend
      `test_check_detects_precondition_broken_after_install` to use a corrupted file.
      **Model**: sonnet-high. **Write**: src/katagiri/installer.py,
      tests/test_installer_setup.py (extend only).
      **Read**: T005 findings above, src/katagiri/installer.py:328-348 + 1321-1377.

- [x] T017 [US2] (orchestrator-filed at TG2 close; commit 19d00b9) Integrate
      connection_status into cross-feature contract surfaces: regen
      docs/assignment/tool-contracts.md via scripts/gen_tool_contracts.py; add
      connection_status to the frozen handshake tool sets in tests/test_averify.py,
      tests/test_bverify.py, tests/test_cverify.py (additive, D-46). Fixes 6 of the 8
      TG2-close failures.
- [x] T018 [Gate] (orchestrator-filed at TG2 close; resolved as **D-47**, commit 4a3b7d1)
      test_averify/test_cverify HTTP-client invariant failures: urllib.request found in
      src/katagiri/irodori_import.py + vendor_fetch.py. Investigation confirmed both
      pre-existing (commits 5002422 / e0f5b64, pre-007). Resolution per user decision:
      both files added to HTTP_CLIENT_ALLOWLIST in test_bverify.py + test_cverify.py as
      deliberate consent-gated setup-time fetchers; ledger row D-47 + audit-log entry.

**Checkpoint**: all TG2 tasks merged to master + full suite green → TG3.

---

## Taskgroup TG3: Gate (serial-on-master, dedicated testing agent)

- [ ] T015 [Gate] [US4] Validation gate, in order:
      1. `uv run pytest -n auto --dist loadgroup` — green; wall-clock vs pre-007
         baseline ≤ +10% (SC-006; baseline ~132s per CLAUDE.md).
      2. `git log --oneline -- specs/007-setup-observability/holdout/` — only the two
         authoring commits (`77792d9` + `4cedd6e`, per MANIFEST.md) touch holdout
         contents.
      3. `KATAGIRI_HOLDOUT=1 uv run pytest specs/007-setup-observability/holdout -q`
         (PowerShell: `$env:KATAGIRI_HOLDOUT='1'; ...`) — expect: every test that was
         expected-fail at authoring (per MANIFEST.md) now PASSES; nothing that passed
         at authoring regresses; SDK-conditional skips may remain skipped. 100% pass
         of non-skipped tests required (SC-005).
      4. Mutation demonstration (SC-001, one-off): quickstart.md §6 — do not commit
         the mutation.
      5. Default collection check: `uv run pytest --collect-only -q | grep -c holdout`
         → 0.
      Failures → fix via new serial tasks, rerun; max two fail→fix→rerun cycles, then
      escalate to user (constitution V discipline). A held-out test may be changed
      ONLY by explicit user decision recorded here.
      **Model**: sonnet-high (testing agent; must not modify non-test files, and must
      not modify holdout files at all).
      **Read**: specs/007-setup-observability/quickstart.md,
      specs/007-setup-observability/holdout/MANIFEST.md (run instructions only — the
      gate agent may read holdout, it implements nothing).

**Checkpoint**: feature complete. Push to remote after TG3 (orchestrator).

---

## Dependencies & execution order

- TG1 (T001) → blocks all of TG2.
- Serial track order: T002 → T003 → T004 (hot files, one at a time on master).
- Lanes, parallel to each other AND to the serial track (no shared files, see map):
  `wt/007-install-tests` T005→T006; `wt/007-bootstrap` T007→T008→T013;
  `wt/007-instances` T011→T012→T014; `wt/007-docs` T009.
- T014's connection_status-dependent test needs T002 merged to master — the
  instances lane rebases on master after TG2-serial merges, or lands that one test
  xfail and flips it pre-merge (task text says how).
- Each lane merges independently when its own tests pass; checkbox flips at merge.
- TG3 starts only when every TG2 task is merged and checked.
- Full suite runs at taskgroup boundaries only (TG2 close, TG3 step 1), not per task.

## Notes

- specs/README.md execution model applies (worktree bootstrap quirks: no .venv in
  lanes — use primary checkout's .venv by absolute path; beads hook noise harmless).
- Installer FR-010 gap (if T005 finds one): new serial task on installer.py, filed by
  the orchestrator — lanes never touch installer.py.
- The holdout author recorded interpretation notes in MANIFEST.md §Interpretations;
  the GATE agent may consult them; implementers must not.
