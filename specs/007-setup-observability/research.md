# Research & Decisions: 007 — Setup Observability

All Technical Context unknowns were resolved by direct repo scouting (2026-08-24); no
external research tasks were needed. One SDK detail is flagged for in-task verification.

## D1. Where the new ToolSpec lands

- **Decision**: new fragment tuple `_INFRA_007_SPECS` in `src/katagiri/tool_registry.py`,
  concatenated at the end of `TOOL_SPECS` (seam at tool_registry.py:87-101, 1555-1564).
  Thin adapter in `mcp_server.py`'s adapter region next to `ping` (mcp_server.py:581),
  logic function beside `security_scan` (mcp_server.py:548).
- **Rationale**: the per-phase-fragment seam is append-only by design; a distinct
  infra fragment keeps phase fragments untouched and the serial-on-master diff one block.
- **Alternatives**: appending to `_PHASE_E_D44_SPECS` — rejected, muddies phase history.

## D2. `connection_status` content & degraded-state behavior

- **Decision**: pure-read logic function; no subprocess (unlike `security_status`).
  Fields: `config_path` + `config_exists`, `db_path` + `db_available` (open attempt,
  read-only), `log_file_path`, `katagiri_version`, `python_version`, `transport`
  ("stdio"), `entry_point` (`sys.argv[0]` + `__main__` module hint), `pid`, `cwd`,
  `client_info` (name/version or `"unknown"`), `secrets` map of presence flags
  (`obsidian_api_token`, `mokuro_shared_secret` → "set"/"unset"), `changed_anything:
  False`. Never raises for missing config / locked DB — reports flags (FR-006).
- **Rationale**: covers every "wrong client / wrong config / wrong DB" axis the user
  named; mirrors installer's "(set)/(unset)" secret idiom; `redact()` still wraps the
  adapter as defense-in-depth.
- **Alternatives**: reusing `security_scan()` port data inside the response — rejected
  (subprocess cost + Windows-only netstat already exposed by `security_status`; keep
  tools orthogonal).

## D3. Client identity capture

- **Decision**: capture the client's `clientInfo` (name, version) from the MCP
  `initialize` handshake and (a) log it at INFO with pid, (b) expose it via
  `connection_status`. Implementation point: the server session's stored
  initialization params (mcp SDK ≥2 keeps client params on the session object) —
  **verify exact accessor against the pinned mcp version during the task**; if the SDK
  exposes nothing, fall back to `"unknown"` (spec edge case) rather than parsing wire
  traffic.
- **Rationale**: handshake is the only place the client names itself; graceful fallback
  keeps FR-006's never-raise promise.

## D4. Bootstrap file logging

- **Decision**: `agent/scripts/setup.py` gets a self-contained appender writing to
  `%LOCALAPPDATA%\Katagiri\logs\bootstrap.log` (same dir convention as
  `katagiri.applog`, but no import of `katagiri` — the script must log even when the
  package is broken/not yet synced, which is exactly the failure it exists to record).
  Plain stdlib `logging` FileHandler, best-effort (`try/except OSError` → stderr-only
  degrade), every existing `say()/warn()/ok()` line mirrored, stdout untouched during
  `--stdio-bootstrap` (stdout belongs to JSON-RPC after exec).
- **Rationale**: closes the only echo-only gap in the chain; independence from the
  package is the point (installer.py already logs via applog).
- **Alternatives**: importing `katagiri.applog` — rejected (circular failure mode:
  can't log the failure of the thing it imports).

## D5. Installer test strategy

- **Decision**: two layers.
  1. **Subprocess CLI tests** (`tests/test_installer_setup.py`, general group):
     run `python -m katagiri.installer --yes` / `--check` with `LOCALAPPDATA` and
     `KATAGIRI_CONFIG` pointed into tmp sandboxes; assert exit codes, config creation,
     step-summary text, log-file content, zero writes outside sandbox. Interactive
     retry/skip/abort driven by scripted stdin piped to the subprocess (Skip is the
     EOF default per installer.py:1321 — EOF-driven paths are cheap to script).
  2. **Launch-chain test** (`tests/test_launch_chain.py`, `mcp` group, runs last):
     spawn `python agent/scripts/setup.py --stdio-bootstrap` exactly as `.mcp.json`
     does (env `PYTHONUTF8=1`), perform initialize handshake with the
     `_StdioClient` pattern (mirrored per-file, per repo convention at
     test_abc_workflow.py:472-475), assert handshake completes, stdout is protocol
     only, stderr carries the startup lines.
- **Rationale**: subprocess = tests the real operator surface incl. exit codes and
  prompt handling; in-process step-function unit tests add little over the wizard's own
  doctor mode and couple tests to private helpers.
- **Constraints honored**: no real schtasks (steps suppressed under `--yes`; interactive
  tests always Skip that step); heavy imports avoided by sandbox configs pointing at
  the cached JMdict template DB where a DB is needed at all; ground-zero stays
  `--public-build`-only.

## D6. Held-out suite placement & exclusion

- **Decision**: `specs/007-setup-observability/holdout/` with its own `conftest.py`;
  default `uv run pytest` collects only `tests/` (verified: `pyproject.toml:35` sets
  `testpaths = ["tests"]`); the gate command runs the holdout path explicitly. Belt-and-braces: holdout `conftest.py` asserts an opt-in env var
  `KATAGIRI_HOLDOUT=1` and skips the whole suite without it, so accidental collection
  can never fail-or-pass silently in a default run.
- **Rationale**: outside `tests/` keeps it out of implementer glob patterns and out of
  default collection without touching `tests/conftest.py` (one less hot-file edit).

## D7. Governance

- **Decision**: single ledger row **D-39** covering (a) the additive `connection_status`
  ToolSpec with D-24 contract-diff justification, (b) the held-out-suite governance rule
  (authored pre-tasks, gate-only, modification requires user decision). Filed as T001,
  blocking all TG2+ code tasks. Constitution needs no bump (additive tool is already
  VII-compliant; no principle text changes).
- **Rationale**: "governance first" (006 precedent); one row, two clauses — they share
  one feature and one rationale.

## D9. Side-by-side instances (added 2026-08-24, user instruction mid-planning)

- **Decision**: one new env var **`KATAGIRI_DATA_HOME`**, honored in
  `config.config_dir()`: when set (absolute path), it replaces
  `%LOCALAPPDATA%\Katagiri` as the instance root; `logs_dir()` follows automatically
  (it is `config_dir()/logs`, applog.py:107-114 — **no applog change needed**); the
  instance's `config.toml` carries its own `db_path`, which the installer writes under
  the data home. Precedence: `KATAGIRI_CONFIG` (file-specific, exists today) >
  `KATAGIRI_DATA_HOME` > default. Empty/relative override → explicit `ConfigError`,
  never a silent fallback to the default home (protects the real study DB).
- **Installer**: new flag `--data-home PATH` — resolves config/db/logs under PATH for
  the whole run, and persists `KATAGIRI_DATA_HOME=PATH` into the checkout's
  `agent/.env` (untracked, per-checkout — the natural instance-wiring file, already
  read by the bootstrap for `KATAGIRI_PYTHON`/`KATAGIRI_MODULE`). No tracked file is
  edited, so a testing checkout stays clean.
- **Bootstrap**: `agent/scripts/setup.py` additionally forwards `KATAGIRI_DATA_HOME`
  and `KATAGIRI_CONFIG` from `agent/.env` into the server's environment at exec, and
  writes its own `bootstrap.log` under the resolved data home.
- **connection_status**: gains `data_home` + `data_home_source` ("default"|"env")
  fields (contract updated pre-implementation — the tool does not exist yet, so this
  is not a post-freeze contract change).
- **Rationale**: single-seam change (one function) isolates everything at once;
  default path untouched → existing install needs zero action (FR-014); mirrors the
  documented `KATAGIRI_CONFIG` precedent's D-22 argument verbatim.
- **Alternatives**: auto-deriving an instance from the checkout path — rejected
  (changes default behavior for the existing install; magic identity is exactly what
  connection_status exists to avoid). Editing `.mcp.json` per instance — rejected
  (tracked file; dirty tree in every testing checkout).

## D8. Lane split (feeds tasks.md conflict map)

- `wt/007-install-tests`: owns `tests/test_installer_setup.py`, `tests/test_launch_chain.py`.
- `wt/007-bootstrap`: owns `agent/scripts/setup.py`, `tests/test_bootstrap_log.py`
  (bootstrap logging AND instance-env passthrough — same file, same lane).
- `wt/007-instances`: owns `src/katagiri/config.py`, `src/katagiri/installer.py`,
  `tests/test_instances.py` (KATAGIRI_DATA_HOME + --data-home). Read-only consumers of
  config.py (T002) are unaffected — additive function change only.
- `wt/007-docs`: owns `docs/setup-observability.md`.
- Serial-on-master: `tool_registry.py`, `mcp_server.py`, `tests/test_connection_status.py`,
  `tests/test_mcp_tools.py` (congruence), `docs/decisions-ledger.md`, gate tasks.
- No lane touches another lane's files; no lane touches hot files. Lanes are
  conflict-free by construction.
