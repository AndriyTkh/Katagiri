# Held-out stability suite — 007 Setup Observability

**Authored**: 2026-08-24 (during planning, before `tasks.md` generation)
**Authoring commit (HEAD at authoring time)**: `11db75b`
**Scope**: validation data for feature 007. Not part of the default test run.

## How the gate runs it

```bash
KATAGIRI_HOLDOUT=1 uv run pytest specs/007-setup-observability/holdout -q
```

PowerShell:

```powershell
$env:KATAGIRI_HOLDOUT='1'; uv run pytest specs/007-setup-observability/holdout -q
```

Without `KATAGIRI_HOLDOUT=1` every test in this directory skips, and a default
`uv run pytest` collects none of them at all (`testpaths = ["tests"]`).

## Binding rule (spec.md "Held-out rule", FR-012, SC-005)

1. These files were authored **before** task generation and are **excluded from
   every implementation task's context**. Implementing agents must not read,
   copy, or modify anything in this directory.
2. **No modification after authoring.** A gate failure is fixed by changing the
   implementation, never the held-out test.
3. If a held-out test is genuinely wrong, changing it requires an **explicit
   user decision recorded in the tasks.md gate task** — never an agent's
   judgement call.
4. The gate verifies point 2 from version history:
   `git log --oneline -- specs/007-setup-observability/holdout/` must show only
   the authoring commit.

## Isolation guarantees (audit these, not the assertions)

- Every subprocess runs with `LOCALAPPDATA` and `KATAGIRI_CONFIG` pointed at a
  throwaway sandbox under pytest's `tmp_path`; the real
  `%LOCALAPPDATA%\Katagiri` config/database/logs are never written.
- The bootstrap launcher is exercised as a **copy** inside a sandbox checkout
  skeleton, because it rewrites `agent/.env` beside itself; the developer's real
  `agent/.env` is never touched.
- No Windows scheduled task is ever registered (`--yes` suppresses that step;
  interactive runs answer its prompts and never say yes).
- Every subprocess has a timeout (installer 60 s seeded / 300 s if the JMdict
  template cache is cold, server op 30 s, bootstrap 60 s) and is killed at
  teardown.
- `PYTHONUTF8=1` everywhere.

## Scenarios

### `test_stability_install.py` — install chain (FR-001, FR-002, FR-003)

| Test | Intent |
|---|---|
| `test_yes_mode_completes_in_an_empty_sandbox_and_writes_a_config` | Non-interactive install with stdin closed finishes and leaves a config whose database is inside the sandbox. |
| `test_yes_mode_reports_a_per_step_status_summary` | Every wizard step announces itself, the doctor table closes the run, and the log file repeats the story. |
| `test_check_mode_on_a_broken_sandbox_exits_nonzero_and_names_the_failing_step` | Doctor mode on an uninstalled machine exits non-zero and names the components that are missing, with a reason each. |
| `test_check_mode_changes_nothing_except_its_own_log` | Doctor mode is read-only: byte-identical sandbox before/after, apart from its own log. |
| `test_running_yes_mode_twice_still_succeeds_and_keeps_the_same_config` | Re-running the installer is idempotent and does not relocate the database. |
| `test_aborting_the_wizard_records_the_step_it_died_on_in_the_log_file` | Choosing Abort at a failed step is recorded in the log with the step number and label. |
| `test_a_skipped_step_lets_the_wizard_finish_and_shows_up_in_the_summary` | Skip (the default, and what EOF means) keeps the wizard going and surfaces in the closing summary. |
| `test_a_sandboxed_install_creates_nothing_in_the_real_app_data` | A sandboxed install adds nothing to the real `%LOCALAPPDATA%\Katagiri` and leaves the real config untouched. |

### `test_stability_connection.py` — `connection_status` (FR-005, FR-006, contract)

| Test | Intent |
|---|---|
| `test_connection_status_is_listed_and_needs_no_arguments` | The tool is reachable through the normal registration seam, argument-free, described. |
| `test_connection_status_returns_every_contract_field_with_the_right_type` | The full contract shape, correct types, answering process's own pid, inside the five-second budget. |
| `test_reported_paths_point_inside_the_launch_sandbox` | Config/database/log paths describe the sandbox this instance was launched in. |
| `test_client_identity_from_the_handshake_is_reported` | The client name/version declared at `initialize` comes back in the report. |
| `test_two_sandboxes_report_different_paths_and_pids` | Two instances from two sandboxes are distinguishable by their reports. |
| `test_db_available_is_true_when_the_sandbox_database_exists` | A reachable database is reported available, at the path actually in use. |
| `test_db_available_is_false_when_the_sandbox_has_no_database` | A missing database is a flag, not a crash — and the read-only tool does not create one. |
| `test_a_missing_config_file_is_reported_as_data_not_an_error` | Config removed at call time is answered with `config_exists: false`, resolved when asked. |
| `test_a_client_that_sends_no_identity_is_reported_as_unknown` | An anonymous client is reported as `"unknown"`, not omitted (skips if the SDK rejects such a handshake). |
| `test_secrets_are_reported_as_presence_flags_only` | Both credential fields appear in a `set`/`unset` map and nowhere else. |
| `test_the_canary_secret_never_appears_in_the_response_or_on_stderr` | The planted canary token leaks into neither the payload, the raw frame, nor stderr. |

### `test_stability_logging.py` — traceability (FR-008..FR-011)

| Test | Intent |
|---|---|
| `test_a_bootstrap_launch_failure_is_recorded_in_a_log_file` | A launch that dies before the server starts leaves the failing phase and its reason in the log home, not only on the dead console. |
| `test_a_bootstrap_launch_keeps_stdout_protocol_clean` | During a client-driven launch, bootstrap diagnostics stay off stdout. |
| `test_the_server_log_records_the_client_identity_from_the_handshake` | The connected client's name and version are readable in the log afterwards. |
| `test_the_server_log_carries_enough_instance_identity_to_tell_instances_apart` | The answering instance's pid is in the log, so a shared log can be untangled. |
| `test_the_server_log_records_one_line_per_tool_call` | Each tool call leaves a record with name, outcome, and duration. |
| `test_the_server_stdout_carries_only_json_rpc_frames` | Every non-empty stdout line is a JSON-RPC frame; the start-up line is on stderr. |
| `test_an_installer_failure_is_reconstructible_from_the_log_file_alone` | A failing step's number, label, outcome and error text are all in the file. |
| `test_no_log_file_under_the_sandbox_contains_the_canary_secret` | Neither the installer nor the server writes the canary token into any log. |

## Interpretations recorded at authoring time

Where the spec left room, this is the reading these tests encode. Disagreement
is a decision for the user, per the binding rule above — not a licence to edit.

1. **"Completes without prompting" (FR-001)** is tested as *nothing blocks*:
   `--yes` runs with stdin closed and must still finish and write a config.
   Printing a prompt line that falls through on EOF is not failed here.
2. **"Doctor mode mutates nothing" (FR-002)** excludes two things: the doctor's
   own log file (FR-010 requires it) and SQLite's `-shm`/`-wal` sidecars, which
   a read-only open of a WAL database stamps as engine bookkeeping. The database
   file itself and `config.toml` must be byte-identical.
3. **Broken preconditions** are created inside the sandbox only: an absent Anki
   profile directory in `config.toml` (a step that cannot succeed), or an empty
   sandbox (nothing installed yet). No repo file is broken to make a test fail.
4. **`config_exists`** must be resolved when the tool is asked, not cached at
   start-up: the test deletes the config after the handshake and expects
   `false`. (The server creates a default config at start-up, so this is the
   only honest way to reach the "config missing" state over the wire.)
5. **Bootstrap log location** is `%LOCALAPPDATA%\Katagiri\logs\` per
   data-model.md — the filename inside it is not pinned; any file there
   carrying the phase and the reason satisfies the test.
6. **The JMdict template cache** (`tests/.cache/jmdict-*.db`) is reused to keep
   install runs at seconds. Its absence is not a failure: the affected tests
   fall back to a real import under a longer timeout.
