# Quickstart / Validation Runbook: 007 — Setup Observability

Prerequisites: Windows 11, uv on PATH, repo checked out, `uv sync` done once.
All commands from repo root. Nothing here touches the real `%LOCALAPPDATA%\Katagiri`.

## 1. Install-chain tests (US1)

```bash
uv run pytest tests/test_installer_setup.py tests/test_launch_chain.py -q
```

Expected: all pass; each test creates its own sandbox (`LOCALAPPDATA` +
`KATAGIRI_CONFIG` redirected); no prompt hangs (interactive paths are stdin-scripted);
no Windows scheduled task registered (check: `schtasks /query | findstr Katagiri` → none
beyond pre-existing).

## 2. Connection diagnostics (US2)

```bash
uv run pytest tests/test_connection_status.py tests/test_mcp_tools.py -q
```

Expected: congruence table includes `connection_status`; over-the-wire call returns the
contract shape (contracts/connection_status.md); paths match the test sandbox; secret
scan finds nothing. Manual cross-client check from a live client session: call
`connection_status`, confirm `config_path`/`db_path` are the ones you expect and
`client_info` names the client you're in.

## 3. Logging (US3)

```bash
uv run pytest tests/test_bootstrap_log.py -q
```

Expected: sandboxed bootstrap run writes `logs\bootstrap.log` with phase lines; a
pre-exec failure is reconstructible from the file alone; `katagiri.log` gains
client-identity + pid lines after a handshake; no secret values in any file.

## 4. Full regression

```bash
uv run pytest -n auto --dist loadgroup
```

Expected: green; wall-clock within +10% of the pre-007 baseline (SC-006).

## 5. Held-out stability gate (US4) — gate task only

```bash
KATAGIRI_HOLDOUT=1 uv run pytest specs/007-setup-observability/holdout -q
```

(PowerShell: `$env:KATAGIRI_HOLDOUT='1'; uv run pytest specs/007-setup-observability/holdout -q`)

Expected: all held-out tests pass. Then verify no post-authoring modification:

```bash
git log --oneline -- specs/007-setup-observability/holdout/
```

Expected: only the authoring commit (plus merge commits touching nothing inside).
A default `uv run pytest` must collect zero holdout tests (`testpaths=["tests"]`).

## 6. Side-by-side instances (US5)

```bash
uv run pytest tests/test_instances.py -q
```

Expected: `KATAGIRI_DATA_HOME` isolates config/logs; installer `--data-home` writes
config+db under the given home and persists the wiring in `agent/.env`; two sandboxed
instances run concurrently with zero shared files; `connection_status` from each
reports its own `data_home` with `data_home_source: "env"`. Manual check: clone the
repo to a neighbor folder, run `setup.bat -- --data-home <path>` there, open a client
session in each folder, call `connection_status` in both — different `data_home`,
different `db_path`, and the original install's files untouched.

## 7. Mutation demonstration (SC-001, one-off at gate)

In a sandbox, break one wizard precondition (e.g. point `KATAGIRI_CONFIG` at a directory
whose vendor data is absent) and run the doctor: exit code non-zero, failing step named.
Confirm at least one install test fails when the corresponding behavior is reverted
(spot-check by temporarily stubbing the step runner — do not commit).
