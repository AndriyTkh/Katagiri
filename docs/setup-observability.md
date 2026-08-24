# Setup Observability

Operator-facing guide to diagnosing a Katagiri install: where the logs are,
how to read a failed install without re-running it, how to run the install
tests, how to cross-check which instance a client is actually talking to, and
how to stand up a second side-by-side instance for testing. Background and
contracts: `specs/007-setup-observability/quickstart.md`,
`specs/007-setup-observability/contracts/connection_status.md`,
`specs/007-setup-observability/data-model.md`. Ledger: D-46.

## Where the logs live

Both files live under the instance's data home —
`%LOCALAPPDATA%\Katagiri\logs\` by default, or `<data-home>\logs\` when
`KATAGIRI_DATA_HOME` is set (see "Side-by-side instances" below).

- **`bootstrap.log`** — one line per bootstrap phase (env check, agent env
  setup, server exec handoff), format
  `timestamp | pid | phase | outcome | detail`. This is the file to check
  when the server never got far enough to open `katagiri.log` at all — a
  failure here is, by construction, the record of the last thing that
  happened before the process gave up.
- **`katagiri.log`** — the running server's log: a start line with its pid,
  a `client connected: name=<x> version=<y> pid=<pid>` line after the MCP
  initialize handshake, and one line per tool call thereafter (via the
  server's `_LoggedMCPServer.call_tool` wrapper).
- The interactive wizard (`setup.bat`) also emits step lines to its own
  console/log output — `timestamp | level | logger | step N/M label ->
  outcome [error text]` — already covered by existing tests; nothing new to
  look for there.

No secret **values** are ever written to any of these files — only presence
flags where relevant.

## Reading a failed install from logs alone

1. Open `bootstrap.log` first. If the last line's `outcome` is a failure,
   its `phase` tells you which stage never completed (env check vs. agent
   env setup vs. server exec handoff), and `detail` carries the error text.
2. If `bootstrap.log` shows every phase succeeding, the failure happened
   after handoff — check `katagiri.log` for the start line and whatever
   came after it (a missing `client connected:` line means the process
   started but no client ever completed a handshake with it).
3. Cross-reference the `pid` field between the two files, and against
   `connection_status`'s `pid` field (below), to confirm you're reading the
   logs for the process you think you are — this matters once more than one
   instance has run against the same log directory.

## Running the install tests

```bash
uv run pytest tests/test_installer_setup.py tests/test_launch_chain.py -q
uv run pytest tests/test_bootstrap_log.py -q
```

Both suites sandbox `LOCALAPPDATA`/`KATAGIRI_CONFIG` per test — nothing
touches a real install. See `specs/007-setup-observability/quickstart.md`
sections 1 and 3 for expected output and what each suite confirms.

## Cross-client diagnosis with `connection_status`

Call the `connection_status` MCP tool (no arguments) from whatever client
session you want to identify. It never raises — missing config, an
unreachable/locked DB, or an absent client identity all surface as `false`
flags rather than an error. Full contract:
`specs/007-setup-observability/contracts/connection_status.md`.

Fields most useful for diagnosis:

| Field | Use |
|---|---|
| `pid`, `entry_point`, `cwd` | Which process, launched how, from where. |
| `data_home` | The resolved instance root — **the** answer to "which install is this?" when more than one exists on the machine. |
| `data_home_source` | `"default"` or `"env"` — whether `KATAGIRI_DATA_HOME` picked it. |
| `config_path` / `config_exists` | Where config was expected to be, and whether it's there. |
| `db_path` / `db_available` | Where the DB was expected to be, and whether it opened. |
| `log_file_path` | Where `katagiri.log` for *this* process is being written. |
| `client_info` | The MCP client's self-reported name/version. |
| `secrets` | Presence-only map (`"set"`/`"unset"`) for `obsidian_api_token`, `mokuro_shared_secret` — never a value. |

Two instances launched from different data homes return different
`data_home`/`config_path`/`db_path` values even if everything else about the
call looks identical — that's the mechanism for telling them apart.

## Side-by-side testing instance

To stand up a second, isolated instance for testing without touching the
primary install:

1. Clone (or already have) a second checkout of this repository.
2. From that checkout, run:

   ```
   setup.bat -- --data-home <path>
   ```

   This persists `<path>` as the instance's data home (config, DB, and logs
   all live under it) and wires the same value into that checkout's
   `agent/.env`.
3. Open a client session against each checkout and call `connection_status`
   in both — they report different `data_home` (`data_home_source: "env"`
   for the one launched with `--data-home`) and different `db_path`, and the
   original install's files are untouched.

`KATAGIRI_DATA_HOME` is the underlying environment override (same
precedent as `KATAGIRI_CONFIG`); `--data-home` is just the installer flag
that sets it durably for a given checkout.

## How the gate runs the held-out suite

The held-out stability suite lives outside `tests/` and is never collected
by a plain `uv run pytest`. Only the gate task runs it, with:

```bash
KATAGIRI_HOLDOUT=1 uv run pytest specs/007-setup-observability/holdout -q
```

(PowerShell: `$env:KATAGIRI_HOLDOUT='1'; uv run pytest
specs/007-setup-observability/holdout -q`)

No contents of that suite are reproduced here — see
`specs/007-setup-observability/quickstart.md` section 5 for the full
expected-output and no-modification verification steps.
