# Feature Specification: 007 — Setup Observability & Cross-Client Connection Diagnostics

**Feature Branch**: `007-setup-observability`

**Created**: 2026-08-24

**Status**: Draft — **tasks.md will be the task-tracking source of truth** (spec-kit; no beads history)

**Input**: User description: "Plan ahead how to test the whole setup + install process; add clear logs for MCP activity and tracking setup issues; include an MCP endpoint for connection status that includes all the config/path information to ensure the correct client is selected; come up with stability tests ahead of time and exclude them from planning so they can be used as validation data."

## Scope claim (binding)

Additive-only, per constitution VII. One new MCP tool (`connection_status`) is the only
contract growth; it requires a decisions-ledger row (D-24 contract-diff justification)
filed **before** its code task. Everything else is test coverage, logging plumbing, and
one held-out validation suite. No schema migration, no new study surface, no phase-entry
requirement (constitution IV applies to phases; this is infrastructure, like 005).

**Held-out rule (binding for this feature)**: the stability/validation suite under
`specs/007-setup-observability/holdout/` is authored during planning, **before** task
generation, and is *excluded from every implementation task's Read list*. Implementing
agents MUST NOT read, copy, or modify it. It runs only at gate tasks, as unbiased
validation data. A gate failure is fixed by changing the implementation, never the
held-out test — a held-out test may only be changed with an explicit user decision
recorded in the tasks.md gate task.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install process is provably testable (Priority: P1)

As the operator setting up Katagiri on a fresh or broken machine, I can run an automated
test suite that exercises the whole install chain — environment check, dependency sync,
the 11-step installer wizard (interactive retry/skip/abort paths and the non-interactive
`--yes` mode), the doctor-only `--check` mode, and the MCP client launch chain
(client config → bootstrap script → server handshake) — against isolated sandboxes, so a
setup regression is caught by tests instead of by a failed install on a real machine.

**Why this priority**: everything else in this feature (logging, diagnostics endpoint,
held-out suite) exists to serve setup reliability; without repeatable install tests there
is no way to confirm any of it works.

**Independent Test**: run the new installer test group on a machine with no Katagiri
app-data; all tests pass without touching the real `%LOCALAPPDATA%\Katagiri` or the real
study database.

**Acceptance Scenarios**:

1. **Given** an isolated sandbox with no config file, **When** the installer runs
   non-interactively with defaults accepted, **Then** it completes without prompting,
   writes a config file into the sandbox, and reports a per-step status summary.
2. **Given** a sandbox where one step's precondition is broken (e.g. vendor data
   missing), **When** the installer runs in doctor-only mode, **Then** it exits non-zero
   and names the failing step, without mutating anything.
3. **Given** a wizard step that fails, **When** the operator chooses Skip, **Then** the
   wizard continues, records the skipped status, and the final summary shows it.
4. **Given** the project MCP client config, **When** the client launch chain starts the
   server in a sandbox, **Then** the server completes the protocol handshake and its
   stdout carries protocol traffic only (diagnostics on stderr).

---

### User Story 2 - Cross-client connection diagnosis in one call (Priority: P1)

As the operator testing Katagiri from multiple MCP clients (Claude Code today, others
later), when a client behaves oddly I call one diagnostic tool from inside that client
and immediately see which server instance answered: where its config file is, which
database and log file it is using, which paths are configured (present/absent, never
secret values), what version is running, how it was launched, and what client identified
itself at the handshake — so "wrong client / wrong config / wrong database" is diagnosed
in one call instead of by comparing files by hand.

**Why this priority**: cross-client issue tracking is the stated pain; every future
client integration test depends on this endpoint existing first.

**Independent Test**: call the diagnostic tool over the real protocol from a spawned
client session; assert the reported paths match the sandbox the server was launched in,
and that no secret value appears anywhere in the response.

**Acceptance Scenarios**:

1. **Given** a running server, **When** the diagnostic tool is called, **Then** the
   response includes config-file path, database path, log-file path, server version,
   transport, and launch entry point, all matching the actual runtime environment.
2. **Given** a client that identified itself at the protocol handshake, **When** the
   diagnostic tool is called, **Then** the response includes that client's name/version
   as seen by the server.
3. **Given** a config with secrets set, **When** the diagnostic tool is called, **Then**
   secret-bearing fields are reported as presence flags only ("set"/"unset"), and the
   existing redaction layer covers the whole response.
4. **Given** two server instances launched from different sandboxes, **When** each is
   asked for its status, **Then** the two responses differ in the reported paths,
   proving the endpoint distinguishes instances.

---

### User Story 3 - Setup issues and MCP activity are traceable after the fact (Priority: P2)

As the operator reporting a setup or connection issue hours after it happened (console
long closed), I can open one log location and reconstruct what happened: which wizard
steps ran and how they ended, what the bootstrap launcher did before the server started,
when clients connected and identified themselves, and which tool calls ran (name,
duration, outcome). Failures carry enough context to file an issue without re-running.

**Why this priority**: builds on existing logging (the wizard and per-tool-call logs
already exist); the gaps are the bootstrap launcher (currently console-only), connection
lifecycle events, and a documented "where to look" story.

**Independent Test**: run an install plus one client session in a sandbox, then assert
the log file(s) contain wizard step outcomes, bootstrap phases, a connection/handshake
record, and per-tool-call lines — and no secret values.

**Acceptance Scenarios**:

1. **Given** a bootstrap launch that fails before the server starts, **When** the
   operator inspects the log location afterwards, **Then** the failure phase and reason
   are recorded in a file (not only on the dead console).
2. **Given** a completed client session, **When** the log is inspected, **Then** it
   shows server start, client identity at handshake, and one record per tool call with
   name, duration, and ok/error outcome.
3. **Given** any logged failure, **When** the log line is read, **Then** it contains no
   secret values (tokens reported as presence only).

---

### User Story 4 - Held-out stability suite gates the feature (Priority: P2, Gate)

As the operator, I want the feature validated by stability tests that were written
*before* implementation planning and kept out of the implementers' view, so passing them
measures real robustness rather than tests written to match the code.

**Why this priority**: it is the feature's acceptance instrument; it produces no user
surface itself.

**Independent Test**: the held-out suite is excluded from default test collection, runs
green only via an explicit opt-in flag at the gate, and version control shows it was
authored before tasks.md.

**Acceptance Scenarios**:

1. **Given** the default test run, **When** it executes, **Then** no held-out test is
   collected.
2. **Given** the gate task, **When** the held-out suite runs against the implemented
   feature, **Then** all held-out tests pass without any held-out file having been
   modified since authoring (verified against version history).

### Edge Cases

- Installer run on a machine where the package manager (uv) is missing → wrapper fails
  loudly with install hint; test asserts the message, not just the exit code.
- Wizard aborted mid-run (Ctrl-C / EOF) → abort is logged with the step number; re-run
  resumes cleanly (steps re-read config fresh).
- Diagnostic tool called when config file is absent (pre-install client launch) → tool
  still answers, reporting the resolved default paths and "config missing", never raising.
- Diagnostic tool called when the database is unreachable/locked → answers with the
  path plus an availability flag; the tool itself never crashes the session.
- Client that sends no identity at handshake → diagnostic reports "unknown client"
  rather than omitting the field.
- Log directory unwritable → logging degrades to stderr-only without crashing the server
  (existing behavior; regression-tested here).
- Two clients connected to two server instances sharing one log file → records carry
  enough instance identity (pid/session) to tell them apart.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide automated tests covering the installer's
  non-interactive mode end-to-end in an isolated sandbox: completes without prompts,
  writes config, produces a per-step status summary, mutates nothing outside the sandbox.
- **FR-002**: The system MUST provide automated tests for doctor-only mode: read-only,
  exit code reflects readiness, failing step is named in output.
- **FR-003**: The system MUST provide automated tests for the interactive wizard's
  retry/skip/abort decision paths (scripted stdin), including the post-wizard menu
  reachability and the "skipped step appears in summary" behavior.
- **FR-004**: The system MUST provide an automated test of the client launch chain as
  configured for real clients (project client config → bootstrap → server), asserting a
  successful protocol handshake and a protocol-clean stdout.
- **FR-005**: The system MUST expose one new diagnostic tool (`connection_status`)
  reporting at minimum: config-file path + existence, database path + availability,
  log-file path, server version, transport, launch entry point, process id, working
  directory, and the client identity captured at the protocol handshake; secret-bearing
  config fields appear as presence flags only.
- **FR-006**: The diagnostic tool MUST never raise for foreseeable degraded states
  (missing config, unreachable database, unknown client); it reports them as data.
- **FR-007**: The diagnostic tool MUST be additive: appended via the existing
  registration seam, spec/adapter congruence tests extended, redaction layer applied,
  and a decisions-ledger row filed before the code task.
- **FR-008**: The bootstrap launcher MUST log its phases and failures to a file (in
  addition to console/stderr), using the same log location convention as the server,
  while keeping stdout protocol-clean during client-driven launches.
- **FR-009**: The server MUST log connection lifecycle: start (already present), client
  identity as received at handshake, and enough per-instance identity (pid) to
  disambiguate concurrent instances in a shared log.
- **FR-010**: Setup/installer failures MUST be reconstructible from the log file alone:
  step label, outcome (ready/action-needed/skipped/aborted), and error text for
  failures. (Wizard already logs steps; this requirement covers verifying it by test and
  closing any gap found.)
- **FR-011**: No log record or tool output introduced by this feature may contain a
  secret value; the existing leak checks (canary token, secret-word scan of payload and
  stderr) MUST be extended to the new surfaces.
- **FR-012**: The held-out stability suite MUST live outside default test collection,
  run only via an explicit opt-in, and be excluded from all implementation-task context;
  the feature's gate task runs it unmodified.
- **FR-013**: A short operator doc MUST state where logs live, what each surface records,
  and how to run the install tests and the held-out gate.

### Key Entities

- **Connection status report**: the diagnostic tool's response — runtime identity
  (version, transport, entry point, pid, cwd), path map (config/db/log + availability),
  secret presence flags, client identity.
- **Setup log record**: file-logged line from wizard or bootstrap — step/phase label,
  outcome, error text, timestamp, process identity.
- **Held-out suite**: validation tests + a manifest freezing authorship time; consumed
  only by the gate task.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A setup regression in any wizard step or the launch chain is detected by
  the automated install tests (demonstrated by mutation: breaking a step's precondition
  in a sandbox makes at least one test fail).
- **SC-002**: "Which server/config/database is this client talking to?" is answerable
  with exactly one tool call from inside any connected client, in under 5 seconds.
- **SC-003**: Given only the log directory from a failed install or session, the
  operator can name the failing step/phase and its error without re-running anything.
- **SC-004**: Zero secret values in any new log line or tool response, enforced by
  automated leak checks, not by review.
- **SC-005**: 100% of the held-out stability suite passes at the gate without any
  held-out file modification after authoring.
- **SC-006**: Default test runs get no slower than +10% wall-clock from this feature
  (install tests are sandboxed and parallel-safe; held-out suite excluded by default).

## Assumptions

- Windows 11 is the only supported host (matches constitution technology constraints);
  install tests may use Windows-only facilities and isolated `%LOCALAPPDATA%` overrides.
- Claude Code is the only client wired today; "cross-client" means the endpoint and
  tests are client-agnostic (any spec-compliant client can be diagnosed), not that other
  clients get config written for them in this feature.
- The existing rotating-file log location remains the single log home; this feature adds
  records and one new writer (bootstrap), not a new logging system. The append-only
  study event log is NOT used for infrastructure logging (constitution III: it records
  study evidence, not diagnostics).
- `uv sync` itself (package resolution) is trusted third-party behavior; tests cover the
  wrapper's failure messaging, not uv's internals.
- The scheduled-tasks wizard step is exercised only in its skip/`--yes`-suppressed form;
  tests never register real Windows scheduled tasks.
- Real vendor-data imports are avoided in install tests where possible (reuse the cached
  JMdict template mechanism); ground-zero behavior stays with `--public-build`.
