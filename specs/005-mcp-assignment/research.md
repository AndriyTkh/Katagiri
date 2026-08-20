# Research: 005 — MCP Assignment

Settled decisions from the v3 council rounds. **Do not re-research any of these.** Anything genuinely open is listed at the bottom with the task that closes it.

- **Decision**: Existing server = **Obsidian Local REST API MCP** (the approved-list repo `coddingtonbear/obsidian-local-rest-api`).
  **Rationale**: one coherent narrative — the same vault is the study workbook, Part C documents one existing server, and the failure demo uses the assignment's own suggested triad for this server (stop the plugin / invalid API key / missing note) against real retry-and-degrade logic. A weather server would need a contrived link into a Japanese-study flow.
  **Alternatives**: Playwright MCP (no natural role); OpenWeather (kept as documented contingency only).
  **Source**: v3 "Part A decision" (examiner R2 + architect R2 conflict, resolved).

- **Decision**: **OpenWeather is a documented contingency, not a parallel build.** If the instructor rejects Obsidian, the weather result must chain into **≥2 dependent decisions** (theme selection *and* review-priority evidence).
  **Rationale**: a single weather→theme hop is precisely the "isolated showcase" that rubric §4 marks Developing (5–9/14).
  **Source**: v3 Part A decision.

- **Decision**: The existing server's read is **not decorative**. The demo vault holds a learner-authored goal note with structured frontmatter (`goal_theme:`, `focus:`); the agent reads it through the existing server and passes the field value as a **literal argument** into the katagiri branch call (theme filter for `find_i_plus_one`, topic for `gen_exercise`). The demo runs **twice** with different note contents and shows different downstream behavior; the defence traces value source → output.
  **Rationale**: this is the direct mitigation for the minimum-condition rule's 59-point cap ("the agent does not incorporate both servers into agent flows").
  **Source**: v3 "§4 decorative-read fix".

- **Decision**: The demo DB is **seeded**, not fresh: ≥2 `prescribe()` rungs and ≥2 coverage outcomes must be reachable.
  **Rationale**: a fresh DB collapses every run to "open a lesson", which makes the `action.kind` branch look like decoration even though it is real.
  **Source**: v3 "§4 decorative-read fix".

- **Decision**: Flow topology = **diagnostic branch on data**: read goal note → `start_session` → branch on `action.kind` (verified structured `action{kind, instruction, rationale, topic, lesson_id, ...}`, never a menu) → exercise / review / triage path → grade → `log_lesson` / `log_observations` → summary. The rationale doc explicitly contrasts this with the **reserved** research-agent example (no propose → run → compare-to-baseline → record chain).
  **Rationale**: satisfies "tool results affect later steps" through server-computed state rather than model whim, and stays clear of the reserved topic.
  **Source**: v3 "Agent (agent/ uv subproject)".

- **Decision**: Framework = **LangGraph** + `langchain-mcp-adapters` `MultiServerMCPClient`; inference through **OpenRouter** via `ChatOpenAI(base_url=...)` with the model **pinned** to a verified tool-calling model; **$10 top-up before the defence** (free tier's 50 requests/day dies in rehearsal).
  **Source**: v3; plan-v1 decision 1.

- **Decision**: Checkpointer = **sync `SqliteSaver`**, `langgraph-checkpoint-sqlite>=3.0.1` (CVE-2025-67644 floor). One real kill-and-resume test on Windows before the defence.
  **Rationale**: the async saver adds an event-loop dimension to a transport path that already has open Windows bugs; sync is enough for a single-user demo.
  **Source**: v3 "Failure handling".

- **Decision**: **Day-1 spike before any graph code** — an end-to-end tool **CALL** (not merely `list_tools`) through `MultiServerMCPClient` + stdio on Windows. Documented fallback: attach to a manually started katagiri process.
  **Rationale**: open bug reports exist for exactly this path (`NotImplementedError` / `SelectorEventLoop`, "Connection closed" on call).
  **Source**: v3 "Day-1 spike".

- **Decision**: Tool narrowing is a **client-side allowlist** binding the curated featured subset; katagiri keeps all 26 tools discoverable. The surplus is framed as production surface plus a triage table. **No server-side profile.**
  **Rationale**: preserves the scope claim and constitution VII; also honest — the server really does have 26 tools.
  **Source**: v3 "Agent".

- **Decision**: Demo isolation via **one additive katagiri change**: a config-path env override (`KATAGIRI_CONFIG`) plus a committed demo profile — fixture DB built by a scripted JMdict import (step timed and documented for the grader), demo vault, demo token. The graded demo never touches the personal DB / vault / token.
  **Source**: v3 "Demo isolation" (architect F1+F2, operator #1, teacher Q1); constitution V.

- **Decision**: **Ledger row before wiring**, scoping D-20: the plugin's MCP endpoint is never registered with *katagiri's* agent surface; the homework agent's connection to a dedicated demo vault (own port, own token, synthetic content) is outside that scope. Constitution bump per governance.
  **Source**: v3 "Demo isolation".

- **Decision**: Demo vault mechanics are **explicit numbered steps**: second Obsidian window/vault; plugin bound to a **non-default** port (personal stays 27123); distinct demo token; **manual `netstat` check** on the demo port before recording (it is invisible to `HARDENED_PORTS`); a documented decision on whether personal Obsidian stays closed during recording (katagiri's vault tools degrade gracefully if so — acceptable, curriculum reads from disk via `vault_root`).
  **Source**: v3 "Demo vault mechanics".

- **Decision**: Contract-doc generator lives in **katagiri's `scripts/`** (it imports `katagiri.tool_registry`, so it runs in katagiri's venv) and emits schema rows for all tools. Purpose / Errors / Side-effects / Example prose is **hand-written**; featured tools additionally get a "why at the MCP boundary" paragraph. Drift check wired into **pre-commit / pytest** — this repo has **no CI**.
  **Rationale**: generated schemas cannot drift; hand-written prose is what the rubric actually rewards. The earlier "lockfile-diff CI" idea was rejected as bogus — there is no CI to hang it on.
  **Source**: v3 "Docs & defence package".

- **Decision**: Triage table names the substantive tools — `coverage`, `find_i_plus_one`, `gen_exercise`, `build_sentences`, `triage_inbox` (≥2 beyond retrieval) — with `lookup` as the **primary-data-source** tool over vendored JMdict. Everything else is a helper.
  **Source**: v3 "Triage table".

- **Decision**: Defence script written **verbatim against the assignment's 9-step checklist** with the 5-segment timing table, plus a **demo cut list**: no VOICEVOX, no Irodori content, no worksheets, no drill modes in the recording. README gains a grader-environment section (Obsidian app + plugin version pin + cert trust + token setup), dry-run by a non-author. Pre-flight script covers ports, processes, keys, one agent tool-call round-trip, and pre-approved Defender prompts.
  **Source**: v3 "Docs & defence package".

- **Decision**: `agent/.env.example` + `.gitignore` entries land **before the first commit**; `PYTHONUTF8=1` in the agent launch config; `langchain-mcp-adapters` ↔ `mcp>=2,<3` intersection verified day 1; integration smoke test = spawn katagiri over stdio, assert featured tools listed **and** one call round-trips.
  **Source**: v3 "Agent".

- **Decision (dead end, do not revisit)**: ChatGPT / Claude **consumer-voice** frontend. Verified: consumer voice modes cannot call MCP tools; connectors cannot reach localhost; tunnelling violates constitution VI and D-22. Voice later = an OpenAI Realtime API layer over *our own* agent, deferred behind F-03.
  **Source**: v3 "Governing principles".

## Open, deliberately (each names its closing task)

- **Which exact Obsidian MCP variant the course tested** — plugin-built-in `/mcp/` (Streamable HTTP, bearer token, self-signed cert) vs a stdio wrapper. Closed by the user-side instructor question (**T001**). Not a blocker: `langchain-mcp-adapters` supports both (`StreamableHttpConnection` exposes `httpx_client_factory`, so a self-signed cert is handled with `verify=False`; stdio for a wrapper), so the connection config is kept swappable and the answer changes configuration, never topology.
- **Exact pinned OpenRouter model id** — chosen at T012 from models verified to emit tool calls; recorded in `agent/.env.example` and the README.
- **Exact featured-subset membership** — the substantive five plus whichever session/logging tools the graph actually calls; fixed at T012 and mirrored into the triage table at T021.
- **Whether personal Obsidian stays closed during recording** — decided and written down at T010, not left to the day.
