# Feature Specification: 005 — MCP Assignment (agent + defence package)

**Feature Branch**: `005-mcp-assignment`

**Created**: 2026-08-20

**Status**: Active — **tasks.md is the task-tracking source of truth** (spec-kit; no beads history for this feature, so no `[was: kata-*]` refs exist)

**Input**: Council-reviewed expansion plan v3, section "Feature 005 — mcp-assignment", plus the university MCP lab assignment (100-point rubric, `ai_agentic_lab_assignment`). Where the plan and older drafts disagree, v3 wins; nothing in this spec re-opens a v3 decision.

**Entry precondition**: none from the phase ladder. This feature is a *hard-deadline external deliverable*, not a Katagiri phase: it does not consume the D6 stop-gate budget and it does not require ≥4 logged study days, because it adds no study surface (constitution IV applies to phase entry, and this is not a phase). It runs alongside 003/006 work.

## Scope claim (binding)

**No contract changes to katagiri. No changes under `src/katagiri/` except ONE additive config env override.**

Everything else this feature produces lives in:

- `agent/` — new uv subproject (the homework agent; separate dependency tree)
- `scripts/` — new scripts (demo-DB builder, contract-doc generator, pre-flight)
- `docs/assignment/` — new deliverable docs (contracts, rationale, defence script, setup)
- `tests/` and `agent/tests/` — new test files
- demo fixtures (fixture DB build recipe, demo vault notes)
- `docs/decisions-ledger.md` + `.specify/memory/constitution.md` — one governance row + the bump it requires
- `.pre-commit-config.yaml` + `README.md` — one hook entry, one grader section

The 26 tools in `src/katagiri/tool_registry.py` are frozen for this feature: the assignment is satisfied by *documenting and consuming* the existing contract surface, not by growing it. Any temptation to add a tool "for the demo" is a scope violation and is refused (constitution VII: additive-only, and additive still means justified).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The existing server's data actually steers the run (Priority: P1) [US1]

As the student defending this assignment, I show the agent read a **learner-authored goal note** out of a dedicated demo Obsidian vault through the **Obsidian Local REST API MCP server**, and I trace the value of one frontmatter field (e.g. `goal_theme:`, `focus:`) into a **literal argument** of a later katagiri tool call — a theme filter on `find_i_plus_one`, a topic on `gen_exercise`. The run is then repeated with a *different* goal note and produces visibly different downstream behavior.

**Why this priority**: rubric §4 (Integration into the agent workflow, 14 pts) and the minimum-condition rule both hinge on the existing connection not being a decorative read. A "read a note, then ignore it" demo caps the whole submission at 59/100. This is the single highest-leverage requirement in the feature.

**Independent Test**: run the agent twice against two demo goal notes differing only in the frontmatter field; the two transcripts differ in the argument passed to the katagiri branch call *and* in the produced material. A defender can point at the note line and at the tool-call argument on screen.

**Acceptance Scenarios**:

1. **Given** the demo vault contains a goal note with structured frontmatter, **When** the agent runs, **Then** the existing server's tool is discovered, called successfully, and its returned field value appears verbatim as an argument in a subsequent katagiri tool call.
2. **Given** two goal notes with different field values, **When** the agent runs once per note, **Then** the katagiri call arguments differ and the final output differs accordingly.
3. **Given** the run completes, **When** the value is traced, **Then** the provenance chain note → existing-server tool result → katagiri argument → final output is recoverable from the run transcript without guesswork.
4. **Given** the graded demo, **When** any vault tool is called, **Then** the target is the **demo** vault on its own port with its own token — never the personal vault.

---

### User Story 2 - One custom-server workflow that branches on data, not on a menu (Priority: P1) [US2]

As the student, I run one complete workflow over the custom server (katagiri, stdio, separate process, started independently): read goal note → `start_session` → **branch on `action.kind`** from the returned prescribed action → exercise path / review path / triage path → grade → `log_lesson` / `log_observations` → summary. The branch is driven by server-computed data, and the flow is explicitly contrasted, in writing, against the assignment's **reserved** research-agent example.

**Why this priority**: rubric §1 (25 pts), §3 (18 pts) and §4 all read this flow. `start_session` already returns exactly one prescribed action with a `kind` (never a menu, per 003 FR-003) — that is what makes a genuine data-driven branch cheap here, and it is the differentiator from the reserved topic's propose→run→compare-to-baseline→record loop.

**Independent Test**: with a seeded fixture DB, drive the graph so that at least two different `action.kind` values are reached across runs, and each takes a different path with a different tool sequence.

**Acceptance Scenarios**:

1. **Given** the seeded demo DB, **When** `start_session` returns an action, **Then** the graph routes on `action.kind` (not on model free-choice, not on a hard-coded constant) and the chosen branch is visible in the transcript.
2. **Given** the seeded demo DB, **When** the demo is run across its documented states, **Then** **≥2 distinct `prescribe()` rungs** and **≥2 distinct coverage outcomes** are reachable — a fresh/empty DB collapses every run to "open a lesson" and is therefore not an acceptable demo dataset.
3. **Given** the workflow completes, **When** the event log is read back, **Then** the lesson and observation writes are present (the flow has an observable side effect, rubric §3 criterion 5).
4. **Given** the agent's bound toolset, **When** tools are listed, **Then** the client-side allowlist exposes the curated featured subset to the model while katagiri keeps all 26 tools discoverable at the protocol level.

---

### User Story 3 - The graded demo cannot touch my real study data (Priority: P1) [US3]

As the learner-owner, when the demo runs — including on a stranger's machine or in a recording — it reads a **fixture DB**, a **demo vault**, and a **demo token**. My personal DB, personal vault (port 27123) and personal REST token are unreachable from the agent's environment, and that unreachability is asserted mechanically, not promised in prose.

**Why this priority**: constitution V (frozen fixtures, never live personal data) and constitution VI (secrets, proxied Obsidian) are non-negotiable, and the assignment itself demands a dedicated demonstration vault. It is also the only thing standing between a screen recording and a permanent public leak of personal notes.

**Independent Test**: with the demo profile active, a test asserts the resolved DB path is the fixture DB and that the personal Obsidian token is not resolvable from the agent process environment; flipping the profile off restores the personal paths.

**Acceptance Scenarios**:

1. **Given** `KATAGIRI_CONFIG` points at the demo profile, **When** katagiri resolves configuration, **Then** the demo config file is used and the default `%LOCALAPPDATA%\Katagiri\config.toml` is not read.
2. **Given** the env override is absent, **When** katagiri resolves configuration, **Then** behavior is byte-identical to today (the change is additive; no existing test changes meaning).
3. **Given** the demo profile, **When** the isolation guard runs, **Then** it fails loudly if the resolved vault token or DB path belongs to the personal profile.
4. **Given** the demo Obsidian plugin instance, **When** the pre-recording check runs, **Then** the demo port is confirmed bound and distinct from 27123, with a distinct token (this port is invisible to katagiri's `HARDENED_PORTS` check, so the check is manual and documented).
5. **Given** the recording session, **When** personal Obsidian is closed, **Then** katagiri's vault tools degrade gracefully and curriculum reads still succeed from disk via `vault_root` — the documented, accepted trade-off.

---

### User Story 4 - A realistic failure is surfaced, retried, and survived (Priority: P1) [US4]

As the student, I kill the existing server mid-flow (stop the plugin) or feed it an invalid key or a missing note, and the agent **reports the failure clearly**, retries with backoff, **re-establishes the MCP session**, and **resumes from its checkpoint** — or, when the server stays down, continues on a **degraded katagiri-only path** instead of dying.

**Why this priority**: rubric §5 (10 pts) requires a *realistic, reproduced* failure, and §6 (8 pts) requires distinguishable errors. The assignment's own suggested triad for this server is exactly the triad we demonstrate, which removes any argument about the failure being artificial.

**Independent Test**: three scripted failure injections (plugin stopped / invalid API key / missing note path); each produces a distinct, human-readable report; the stopped-plugin case additionally demonstrates one real kill-and-resume from the checkpointer on Windows.

**Acceptance Scenarios**:

1. **Given** a running flow, **When** the existing server becomes unreachable, **Then** the agent surfaces which connection failed and why, retries with backoff, and re-establishes the session on recovery.
2. **Given** a process kill after a checkpoint write, **When** the agent is restarted, **Then** it resumes from the checkpoint rather than replaying the whole flow.
3. **Given** the existing server stays unavailable, **When** the flow continues, **Then** the degraded katagiri-only path completes and the degradation is stated in the output, not hidden.
4. **Given** an invalid tool input, **When** the tool returns an error, **Then** the caller can distinguish *failure* from *successful empty result* (rubric Part B) and the transcript shows which it was.

---

### User Story 5 - A grader can reproduce it and a defender can explain it (Priority: P2) [US5]

As the student and as the grader, the repository carries: an 8-row contract table for **every** custom tool (generated for the mechanical rows, hand-written for Purpose / Errors / Side-effects / Example), a documented tool for the existing server written *in the context of this project*, a triage table separating the substantive tools from the helpers, a design rationale including explicit differentiation from the reserved topic, a grader-environment section in the README, a pre-flight script, and a defence script mapped verbatim onto the assignment's 9-step demonstration checklist with a per-segment timing table.

**Why this priority**: rubric §2 is 25 points — equal to architecture — and is the part most often lost to "documentation exists but omits schema/behaviour/rationale detail". It is P2 only because it cannot be finished before the thing it documents exists.

**Independent Test**: a non-author follows the README on a clean machine, starts the custom server independently, starts the agent, and reaches a successful run; separately, the contract-drift check fails when a tool spec is edited without regenerating the docs.

**Acceptance Scenarios**:

1. **Given** `tool_registry.py` is the checked-in contract, **When** the generator runs, **Then** name / model-facing description / input schema / output schema rows are emitted for all 26 tools from the registry, with no hand-copied schema anywhere.
2. **Given** a tool spec changes, **When** the drift check runs (pre-commit and pytest), **Then** it fails until the generated doc is regenerated — this repo has **no CI**, so pre-commit + pytest are the only enforcement points that exist.
3. **Given** the featured subset, **When** the rationale is read, **Then** each featured tool carries a "why this belongs at the MCP boundary" paragraph and the tool set is mapped to the workflow.
4. **Given** the triage table, **When** it is read against the rubric, **Then** the ≥3 substantive tools are named with at least two beyond retrieval, and the primary-data-source tool is identified.
5. **Given** the defence script, **When** it is walked, **Then** all 9 demonstration steps are covered in order inside the 10–15 minute budget, with a cut list naming what is deliberately *not* shown.

---

### Gate - 005-verify: rehearsed, not hoped for [Gate]

The feature closes on a rehearsal gate, not on "code exists": one end-to-end run of the defence runbook (quickstart.md **is** that runbook), one failure-demo rehearsal, and one value-trace rehearsal including a changed valid input and an invalid input. Rehearsal happens on the demo profile with the pinned model and a funded OpenRouter balance.

**Independent Test**: the runbook is executed start to finish by the author once, and its startup half by a non-author, with timings recorded against the 5-segment table.

**Acceptance Scenarios**:

1. **Given** the runbook, **When** it is executed, **Then** every one of the 9 demonstration steps is reached and the total lands inside 10–15 minutes.
2. **Given** the rehearsal, **When** a segment overruns, **Then** the cut list is applied and the runbook is amended — the fix is the script, not improvisation on the day.
3. **Given** the free OpenRouter tier's 50-request/day ceiling, **When** rehearsal begins, **Then** the account is already topped up; discovering the ceiling during the recording is a foreseeable, prevented failure.

### Edge Cases

- **Instructor answers "the course tested the other Obsidian variant"** after the graph is built → the connection config is swappable by construction (`langchain-mcp-adapters` supports both Streamable HTTP with a self-signed-cert `httpx_client_factory` and stdio), so the answer changes configuration, not topology.
- **Instructor rejects the Obsidian option entirely** → the documented contingency is OpenWeather (`mschneider82/mcp-openweather`, stdio, `OWM_API_KEY`, Go build). It is *documented, not built to parity*. If it fires, the weather result must chain into **≥2 dependent decisions** (theme selection *and* review-priority evidence) or the submission lands in Developing-tier §4.
- **Fresh/empty demo DB** → every run collapses to "open a lesson", killing US2 acceptance 2. The fixture DB must be seeded; the seeding step is scripted and its runtime is measured and documented for the grader.
- **Model silently swapped by provider routing** → the model is pinned to a verified tool-calling model; an unpinned model that cannot emit tool calls is an unrecoverable defence failure.
- **Windows stdio tool-call path broken** (open bug reports exist for `NotImplementedError` / `SelectorEventLoop` and "Connection closed" on call through `MultiServerMCPClient`) → this is the day-1 spike, before any graph code; documented fallback is attaching to a manually started katagiri process.
- **Contract docs drift from the registry** → drift check; no CI means the check must live where commits and tests already run.
- **Secret committed** → `.env.example` plus `.gitignore` entries land **before** the agent's first commit; `detect-secrets` pre-commit is already in place as the backstop.
- **Worktree teardown destroys gitignored vendored data** (happened twice in this repo) → lane instructions copy vendored data, never junction it, and teardown scans for reparse points first.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The agent MUST connect to **two** MCP servers through one `MultiServerMCPClient`: katagiri over **stdio** (`mcp>=2,<3`, no network listener — constitution VI) and the existing approved server per the instructor's answer. Both connections MUST be discoverable and callable in the demo. *(rubric §1, minimum-condition rule)*
- **FR-002**: The existing server's tool result MUST be consumed as a **literal argument** to a later katagiri call — the goal-note frontmatter field → theme/topic argument passthrough — and MUST NOT be a decorative read. *(rubric §4; v3 §4 decorative-read fix)*
- **FR-003**: The graph MUST branch on `action.kind` from `start_session`'s single prescribed action (structured `action{kind, instruction, rationale, topic, lesson_id, ...}`), routing to exercise / review / triage paths. *(rubric §4)*
- **FR-004**: The demo dataset MUST be a seeded fixture DB in which **≥2 `prescribe()` rungs** and **≥2 coverage outcomes** are reachable. *(US2 acceptance 2)*
- **FR-005**: Failure handling MUST detect existing-server loss, surface it clearly, retry with backoff, re-establish the MCP session, resume from a checkpoint, and offer a degraded katagiri-only path. Checkpointer MUST be the **sync `SqliteSaver`** with `langgraph-checkpoint-sqlite>=3.0.1` (CVE-2025-67644 floor), with one real kill-and-resume test executed on Windows before the defence. *(rubric §5, §6)*
- **FR-006**: A **client-side** tool allowlist MUST bind the curated featured subset for the model. Katagiri MUST keep all 26 tools discoverable; **no server-side profile, no registry edit**. The surplus is framed in docs as production surface, with a triage table. *(scope claim; constitution VII)*
- **FR-007**: The inference model MUST be a **pinned** OpenRouter model verified to support tool calling, reached via `ChatOpenAI(base_url=...)`. *(defence reliability)*
- **FR-008**: Katagiri MUST accept **one additive** config-path env override (`KATAGIRI_CONFIG`). Absent the variable, behavior is unchanged. This is the *only* permitted `src/katagiri/` change in this feature. *(scope claim; D-22 secret location preserved)*
- **FR-009**: A committed **demo profile** MUST exist: fixture DB (built by a scripted JMdict import whose runtime is timed and documented), demo vault fixture with the goal note(s), and a demo token — such that the graded demo never touches the personal DB, vault, or token. *(constitution V, VI; assignment's dedicated-vault boundary)*
- **FR-010**: The demo Obsidian plugin instance MUST bind a **non-default port** (personal stays 27123) with a **distinct token**, and the setup runbook MUST include a manual `netstat` verification step (the demo port is outside katagiri's `HARDENED_PORTS` coverage) and a documented decision on whether personal Obsidian stays closed during recording.
- **FR-011**: A ledger row MUST be filed **before** any wiring, scoping D-20: the plugin's MCP endpoint is never registered with *katagiri's* agent surface; the homework agent's connection to a **dedicated demo vault** (own port, own token, synthetic content) lies outside that prohibition. Constitution VI MUST be amended with the version bump governance requires. *(governance: ledger first, then constitution, then code)*
- **FR-012**: Contract documentation MUST cover **every** custom tool with the assignment's 8 rows. Name / model-facing description / input schema / output schema rows MUST be **generated** from `src/katagiri/tool_registry.py` by a script living in katagiri's `scripts/` (it imports `katagiri.tool_registry`, so it runs in katagiri's venv, not the agent's). Purpose / Error conditions / Side effects / Example MUST be **hand-written**. *(rubric §2)*
- **FR-013**: A drift check MUST fail when the registry and the generated docs disagree, wired into **pre-commit and pytest** — the repo has **no CI**, so these are the only runners. *(rubric §2 reproducibility)*
- **FR-014**: At least one tool of the existing server MUST be documented in the 8-row form, written in the context of this project and configuration (not copied from upstream docs). *(rubric Part C)*
- **FR-015**: A triage table MUST identify the substantive tools — `coverage`, `find_i_plus_one`, `gen_exercise`, `build_sentences`, `triage_inbox` (≥2 of them beyond retrieval) — and `lookup` as the **primary-data-source tool** over vendored JMdict; every other tool is labelled a helper. *(rubric §3, minimum-condition rule)*
- **FR-016**: A design rationale MUST cover: the existing server's role, per-featured-tool "why at the MCP boundary", trade-offs and known limitations, and explicit **differentiation from the reserved research-agent topic**. *(rubric §2, submission item 6; reserved-topic rule)*
- **FR-017**: A defence script MUST map verbatim onto the assignment's **9 demonstration steps** with the **5-segment timing table**, and MUST carry a **demo cut list**: no VOICEVOX, no Irodori content, no worksheets, no drill modes in the recording. *(defence format)*
- **FR-018**: The README MUST carry a grader-environment section: Obsidian app + pinned plugin version, self-signed-cert trust, token setup, and independent start commands for the custom server and the agent. A non-author MUST dry-run it. *(submission item 3, rubric §1 reproducibility)*
- **FR-019**: A pre-flight script MUST check ports, processes, keys, and one real agent tool-call round-trip, and Windows Defender prompts MUST be pre-approved before recording.
- **FR-020**: `agent/.env.example` and `.gitignore` entries MUST land **before** the agent subproject's first commit; `PYTHONUTF8=1` MUST be set in the agent launch config; the `langchain-mcp-adapters` ↔ `mcp>=2,<3` version intersection MUST be verified on day 1.
- **FR-021**: An integration smoke test MUST spawn katagiri over stdio and assert **both** that the featured tools are listed **and** that one call round-trips.
- **FR-022**: A user-side written question to the instructor MUST be sent as the first action, asking which exact Obsidian MCP server / version / commit the course tested (plugin-built-in `/mcp/` vs stdio wrapper). It is a **decision point, not a blocker**: build proceeds against whichever variant is installable, with the connection config kept swappable.

### Key Entities

- **Goal note** — a learner-authored markdown note in the demo vault whose frontmatter carries the steering field(s) (`goal_theme`, `focus`). Two variants exist so the demo can show a changed valid input.
- **Prescribed action** — katagiri's `start_session` payload `action{kind, instruction, rationale, topic, lesson_id, ...}`; `kind` is the graph's branch key.
- **Demo profile** — the triple (fixture DB, demo vault, demo token) selected by `KATAGIRI_CONFIG`.
- **Featured subset** — the allowlisted tools bound to the model; a strict subset of the 26 registered tools.
- **Contract row set** — per-tool 8-row table; four rows generated from the registry, four hand-written.
- **Checkpoint** — a `SqliteSaver` thread record enabling kill-and-resume.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Both MCP connections initialize, are discovered, and are successfully called in one recorded run; the custom server is started independently, in its own process. *(rubric §1; minimum-condition rule cleared)*
- **SC-002**: The value of one goal-note frontmatter field is traced on screen from note → existing-server result → katagiri call argument → final output; two runs with different notes produce different outputs. *(rubric §4)*
- **SC-003**: ≥3 substantive custom tools are shown exposed, ≥2 beyond retrieval, with `lookup` named as the primary-data-source tool. *(rubric §3)*
- **SC-004**: All three failure injections (stopped plugin / invalid key / missing note) produce distinct, readable reports; one kill-and-resume completes on Windows. *(rubric §5, §6)*
- **SC-005**: Contract docs exist for all 26 tools; the drift check is green and demonstrably fails on an induced registry edit. *(rubric §2)*
- **SC-006**: A non-author reaches a successful run from the README alone. *(rubric §1/§2 reproducibility)*
- **SC-007**: The rehearsed defence fits 10–15 minutes with all 9 steps covered; timings are recorded per segment.
- **SC-008**: Zero secrets committed (`detect-secrets` green) and zero personal-data paths reachable from the demo profile. *(rubric §6; constitution V, VI)*
- **SC-009**: The scope claim holds at merge: `git diff --stat` over `src/katagiri/` shows **only** `config.py`, and `src/katagiri/tool_registry.py` is untouched.

## Out of scope (explicit)

- Any new katagiri tool, any change to an existing tool contract, any server-side tool profile.
- ChatGPT / Claude consumer-voice frontends (verified dead: consumer voice modes cannot call MCP tools, connectors cannot reach localhost, tunnelling would violate constitution VI and D-22). Voice on our own agent is deferred behind F-03.
- VOICEVOX, Irodori content, worksheets, drill modes — all on the demo cut list.
- Feature 006 teaching-method work; the two features share no files.
- Building the OpenWeather contingency to parity.

## Assumptions

- `start_session` already returns exactly one structured prescribed action with a `kind` field (003 FR-003, shipped).
- The 26-tool registry and stdio-only transport are stable for the duration of this feature.
- `langchain-mcp-adapters` can drive both a stdio connection and a Streamable HTTP connection with a self-signed certificate (via `httpx_client_factory`), so the instructor's answer is a config change.
- The default branch is `master`; the repo has **no CI** — pre-commit and pytest are the only automated runners.
- The learner-owner is also the student defending; "user-side" tasks (instructor question, OpenRouter top-up) cannot be delegated to an agent.
