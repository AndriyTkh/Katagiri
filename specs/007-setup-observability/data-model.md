# Data Model: 007 — Setup Observability

No database entities. Three transient/report shapes; no schema migration (constitution III).

## ConnectionStatusReport (tool output — authoritative shape in contracts/connection_status.md)

| Field | Type | Rule |
|---|---|---|
| status | "ok" | Always "ok" if the tool answered (degradation is in the flags, FR-006). |
| katagiri_version | str | From `katagiri.__version__`. |
| python_version | str | `platform.python_version()`. |
| transport | "stdio" | Constant today; field exists so future transports are diagnosable. |
| entry_point | str | How this process was launched (argv[0] / module). |
| pid | int | Instance disambiguator (shared-log edge case). |
| cwd | str | Process working directory. |
| data_home | str | Resolved instance root (`config_dir()`); distinguishes side-by-side installs. |
| data_home_source | "default" \| "env" | How the home was resolved (`KATAGIRI_DATA_HOME`). |
| config_path | str | Resolved path (env override honored). |
| config_exists | bool | |
| db_path | str | |
| db_available | bool | Read-only open attempt; lock/missing → false, never raise. |
| log_file_path | str | |
| client_info | {name: str, version: str} | `"unknown"`/`""` when client sent none. |
| secrets | {field: "set"\|"unset"} | Presence flags only; never values. Covers obsidian_api_token, mokuro_shared_secret. |
| changed_anything | false | Read-only tool, same idiom as security_status. |

**Validation rules**: no field may ever contain a secret value; response passes
`redact()`; `_assert_clean`-style leak check in tests covers payload + stderr.

## SetupLogRecord (rotating-file line — wizard, existing + verified)

`timestamp | level | logger | step N/M label -> outcome [error text]` — already emitted by
`installer._print_step`; FR-010 verifies coverage by test and closes gaps only if found.

## BootstrapLogRecord (new file `%LOCALAPPDATA%\Katagiri\logs\bootstrap.log`)

`timestamp | pid | phase | outcome | detail` — one line per bootstrap phase
(env check, agent env setup, server exec handoff), mirroring every existing
`say()/warn()/ok()` console line; failure before exec is the critical record.

## Server lifecycle records (additions to katagiri.log)

- start line (exists) + **pid**;
- `client connected: name=<x> version=<y> pid=<pid>` after initialize handshake;
- per-tool-call lines (exist via `_LoggedMCPServer.call_tool`) — unchanged.

## HoldoutManifest (`holdout/MANIFEST.md`)

Authoring date, authoring commit hash, run command, the binding no-modification rule,
and the list of stability scenarios covered (names only, no assertions duplicated).
