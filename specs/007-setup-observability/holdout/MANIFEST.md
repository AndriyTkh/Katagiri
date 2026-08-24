# Held-out stability suite — 007 Setup Observability

**Authored**: 2026-08-24 (during planning, before `tasks.md` generation)
**Authoring commit**: `77792d9` (HEAD at authoring time was `11db75b`; the suite
itself landed as `77792d9`)
**Scope**: validation data for feature 007. Not part of the default test run.

**Addendum (same day, later commit)**: `test_stability_instances.py` — plus the
additive `Sandbox.env_unset` / `use_data_home()` / `use_default_home()` /
`default_home_entries()` seam in `conftest.py` — was authored **after** the
first three modules, when User Story 5 (side-by-side instances, FR-014..FR-017)
arrived mid-planning, and **still before task generation**. It therefore lands
in its own commit, dated 2026-08-24, after `77792d9` and before `tasks.md` is
finalized. The gate's version-history check (point 4 below) must expect **two**
authoring commits for this directory: `77792d9` (install/connection/logging) and
the instances commit. No file from the first commit was weakened: the conftest
change is additive only (a new field defaulting to dropping a stray
`KATAGIRI_DATA_HOME`, three new methods, no existing assertion or default
touched).

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
   the authoring commit(s) named at the top of this file — `77792d9` and the
   2026-08-24 instances commit — and nothing later.

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
  template cache is cold, server op 30 s, bootstrap 60 s, instance installs
  120 s seeded / 300 s cold) and is killed at teardown.
- `PYTHONUTF8=1` everywhere.
- Instance tests (`test_stability_instances.py`): "the default home" always
  means `<sandbox>/appdata/Katagiri`, because `LOCALAPPDATA` is redirected — the
  real `%LOCALAPPDATA%\Katagiri` is never a candidate for any assertion. Every
  child has `KATAGIRI_DATA_HOME` **deleted** unless the test sets it, so a value
  in the gate operator's own shell can neither cause nor mask a failure.
- The `--data-home` tests run the installer inside a **mirrored checkout** (a
  copy of `src/katagiri` plus an `agent/` skeleton, on `PYTHONPATH`, as cwd), so
  a checkout-relative writer lands in the copy. On top of that, the real
  `<repo>/agent/.env` is snapshotted and byte-restored by a guard fixture, so
  even an implementation that resolves "the checkout" from the installed
  package's own location cannot leave the developer's file modified.
- `vendor/` (1.3 GB) is never copied into the mirror; the steps that need it
  report themselves as not ready, which the wizard must survive anyway.

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

### `test_stability_instances.py` — side-by-side instances (US5, FR-014..FR-017)

| Test | Intent |
|---|---|
| `test_a_data_home_override_puts_config_database_and_logs_under_it` | `KATAGIRI_DATA_HOME` at a fresh directory: the installer puts `config.toml`, the database it names, and `logs/` inside it — and writes nothing at all to the default home. |
| `test_no_override_keeps_the_default_local_app_data_home` | **Regression, passes pre-implementation**: with neither override set, config/db/logs resolve under `%LOCALAPPDATA%\Katagiri` exactly as today (US5/3). |
| `test_two_data_homes_share_no_files_after_two_installs` | Installing instance B leaves instance A's whole tree byte-identical (full size+mtime fingerprint diff), and B is complete inside its own home. |
| `test_two_concurrent_servers_each_report_their_own_data_home` | Two live servers, one per home: each `connection_status` reports its own `data_home` with `data_home_source == "env"`, differing config/db/log paths and pids; killing one leaves the other answering as the same process. |
| `test_two_checkouts_pointed_at_one_data_home_report_the_same_home` | Edge case "two checkouts wired to the SAME home": both work, and both reports show the *same* `data_home`/config/db — sharing is visible, not hidden — while pids still differ. |
| `test_katagiri_config_outranks_the_data_home_for_the_config_file_only` | D9 precedence with both overrides set: the config file follows `KATAGIRI_CONFIG` (and the home gets no second config), while logs follow the data home; the diagnostic reports the same split. |
| `test_the_data_home_flag_isolates_an_instance_with_no_environment_variable` | `installer --data-home PATH` with no env var gives the same isolation as the env var (config/db/logs under PATH, default home empty). |
| `test_the_data_home_flag_persists_the_instance_wiring_in_the_checkout` | US5/4: after a `--data-home` install, the checkout's untracked `agent/.env` carries `KATAGIRI_DATA_HOME=PATH`, so a later launch from that folder inherits the instance with nothing exported by hand. |
| `test_an_empty_data_home_override_fails_loudly_and_never_falls_back[empty,blank]` | Invalid override (empty / whitespace): explicit non-zero failure naming the variable, no traceback — and critically, **nothing** written to the default home. |
| `test_a_relative_data_home_override_is_rejected_and_creates_nothing` | A relative override is rejected, not resolved against the working directory: no instance directory appears beside the checkout, nothing lands in the default home. |
| `test_a_data_home_that_does_not_exist_yet_is_created_or_clearly_reported` | Edge case "override points at a not-yet-existing directory": created (and then everything lands inside it) *or* refused while naming the path — never a crash, never a fallback to the default home, never a half-built instance. |

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

### Addendum — `test_stability_instances.py` (US5, authored 2026-08-24)

7. **"The instance wiring persists in that checkout" (US5/4, FR-015)** is read
   as research D9's design: the untracked `agent/.env` beside the installer's
   own checkout gains a `KATAGIRI_DATA_HOME=PATH` line. Because the spec does
   not pin *how* the checkout is resolved (module location vs. working
   directory), the test accepts the line in **either** the mirrored checkout's
   `agent/.env` (the run's cwd and import root) **or** the real checkout's — it
   asserts on the persisted *value*, which must name that instance's home. The
   real file's content is read and then byte-restored by a guard fixture, so
   this stays true without ever leaving the developer's `.env` modified. Only
   lines the run *added* to the real file count as evidence — a developer's
   checkout may already be wired to an instance of its own. If the
   implementation chooses a different wiring file, this test is the one to
   discuss with the user (binding rule point 3), not to quietly edit.
8. **"Nothing written to the default home"** is asserted as *the default home
   directory contains no entries at all* after the run. The directory itself is
   pre-created by the sandbox fixture, so its mere existence is not a failure —
   any file or subdirectory in it is.
9. **An invalid override must fail, not be ignored.** `KATAGIRI_DATA_HOME=""`
   is falsy, so the `if override:` shape used by the existing `KATAGIRI_CONFIG`
   lookup would silently fall back — which FR-014's edge case forbids ("never
   silently falls back to the default home"). The test therefore requires a
   non-zero exit whose message names `KATAGIRI_DATA_HOME`, and treats a
   whitespace-only value the same way. A traceback is not an acceptable
   "explicit message".
10. **A nonexistent data home** may be created or refused — the spec allows
    both ("created on first use (or reported clearly)"), so the test branches on
    the exit code and holds each branch to its own contract (created ⇒ the
    instance is complete inside it; refused ⇒ the path is named and no partial
    instance is left).
11. **`data_home_source`** is asserted as `"env"` for both the private-home and
    shared-home cases, per the updated contract's two-value vocabulary
    (`"default"`/`"env"`); the `--data-home` flag's own runs are checked by file
    layout rather than by a third source value, since the contract does not
    define one.
12. **Instance isolation is checked as a tree diff, not as path inequality**:
    instance A's full fingerprint (every relative path, size, mtime) must be
    unchanged by instance B's install. Distinct paths alone would not catch a
    second installer that opened or rewrote the first instance's database.
