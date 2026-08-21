# Design rationale

This is the prose companion to `docs/assignment/tool-triage.md` (which tool)
and `docs/assignment/existing-server-contract.md` (the one existing-server
tool documented in 8 rows). It answers a different question for each: not
"is this tool substantive" but "why does calling it belong at an MCP
boundary at all", and "why is this whole shape not the reserved topic".

## 1. Why the existing server has a reasonable role here

The existing server is the Obsidian Local REST API MCP plugin, reached on a
**dedicated demo vault** (own port `27224`, own token, synthetic content —
`docs/assignment/demo-setup.md`), never the personal `27123` instance
katagiri's own proxy tools are hardcoded to (D-34). Its role in this
project is narrow and specific, not decorative:

- It is the **only** source of the goal note. The note is authored by the
  learner in Obsidian, a separate application with its own file access;
  nothing in `agent/` or `katagiri` can read it except by asking that
  plugin's server for it. There is no "just read the file" shortcut that
  respects the constitution's untrusted-vault-content boundary, because the
  point is to go through the server's own read path, not around it.
- Its output crosses into the workflow as **data, once, at one seam**:
  `read_goal_note` (`agent/src/katagiri_agent/graph.py`) calls `vault_read`,
  `katagiri_agent.goal_note.parse_goal_note` extracts one frontmatter field,
  and that value is passed as a **literal argument** — `topic` on
  `gen_exercise` / `find_i_plus_one` — never as an instruction, never
  interpreted, never reformatted as code. This is exactly Part C's ask: use
  one tool of the existing server, in this project's own configuration, for
  something the workflow actually needs, not a token "discovered but never
  called" gesture.
- It is read-only in this project. Two tools are bound
  (`OBSIDIAN_FEATURED_TOOLS = {"vault_list", "vault_read"}`,
  `agent/src/katagiri_agent/clients.py`); every write-shaped tool the plugin
  also exposes (`vault_write`, `vault_append`, `vault_patch`, `vault_move`,
  `vault_delete`, `command_execute`, …) is never bound. The existing
  server's role is "supply one untrusted string the graph turns into an
  argument", not "act on the learner's vault" — a smaller, auditable role
  is the reasonable one for a homework agent with no legitimate write
  reason.
- Practically, `vault_list` is bound but the diagnostic-branch graph never
  calls it at runtime — it was used once, at T005 spike time, to discover
  the goal note's path, and stays bound alongside `vault_read` for
  completeness of the featured subset rather than because the graph
  dispatches it. See §4 for why this is called out honestly rather than
  quietly dropped from the allowlist.

## 2. Per-featured-tool: why it belongs at the MCP boundary

"At the MCP boundary" here means: why does this capability have to live in
a separate server process behind a tool call, rather than being inlined as
a plain function in `agent/`. The short answer repeats across most of
these: the server owns state, a secret, or a dataset the caller must never
hold directly. One paragraph each; full 8-row contracts (schema, errors,
examples) live in `docs/assignment/tool-contracts.md` and
`existing-server-contract.md` — this section is the "why here", not a
restatement of "what".

**`coverage`** — needs the learner's real known-word set and a tokenizer
over vendored linguistic data (`katagiri.intelligence`). That state is
private and stateful (it changes as the learner studies); a client-side
reimplementation would either duplicate the tokenizer and the known-set
query or go stale the moment the two diverged. Crossing the boundary is
what keeps "how much of this text can I read" answered from one
authoritative place regardless of which caller asks.

**`find_i_plus_one`** — the same argument, sharper: its ranking depends on
a grammar-prerequisite DAG, four vendored datasets, and comprehension-debt
scores computed from the event log. This is a stateful algorithm over data
the agent has no independent copy of and should not carry one of — the
boundary is what lets the algorithm live once, tested once, in the server
that owns the data it ranks.

**`gen_exercise`** and **`build_sentences`** — both screen every candidate
string against a **sealed canary set** before returning it. The canary set
must never be visible to the model or to `agent/` code — its entire value
as a leak detector depends on staying server-side. These two tools cannot
be inlined at all: inlining would require handing the canary set to the
caller, which defeats its purpose. The MCP boundary is the only place this
screening can happen without leaking the thing being screened for.

**`triage_inbox`** — the one path-tool that writes: new item rows and a
mining event land in the fixture DB when `dry_run=False`. Constitution III
("Event Log Is Sacred") requires a single write path into that log; the
boundary is what enforces "only the server that owns the DB may write to
it" instead of trusting every caller's transaction discipline.

**`lookup`** — reads the vendored JMdict import plus pitch-accent data.
Large, static, and versioned as one artifact; the boundary means the model
never has to embed or independently query a dictionary, and `found: false`
(rather than a plausible-looking guess) is a server-side guarantee no
client-side fallback could make honestly.

**`start_session`** — the branch key of the entire graph. `action.kind`
must be **server-computed** (FR-003: never model free-choice, never a
constant) precisely because it is read off the learner's real study state
— due reviews, open threads, the prescription ladder. Inlining this would
mean the agent deciding what to study, which is the one thing this project
deliberately refuses to let it do.

**`log_lesson`** / **`log_observations`** — the write side of the same
sacred log `triage_inbox` writes into. They exist at the boundary for the
identical reason: only the server holding the DB connection may commit to
it, and a structured tool call is the auditable form that write takes.

**`stage_untrusted`** / **`confirm_untrusted`** — these exist *only because*
of the MCP trust boundary. An externally-sourced string (a served-but-
unresolved thread, in the triage path) is never passed as a bare argument;
it is staged, echoed back, and confirmed before any write tool will accept
its envelope id. Without a boundary to enforce the ceremony across, there
would be nothing to envelope against — this pair's whole reason to exist
is the boundary itself.

**`vault_read`** — see §1: the one point where the existing server's data
enters the graph, consumed as an opaque string and only ever placed into a
katagiri keyword argument.

**`vault_list`** — bound for parity with `vault_read` (the pair T005's
spike exercised to find, then read, the goal note) but not dispatched by
the diagnostic-branch graph at runtime; see §4's honest-surplus framing.

## 3. How the tool set supports the workflow

The graph (`agent/src/katagiri_agent/graph.py`, T013) is one path, walked
node by node, each node calling exactly the tools it needs and nothing
else:

1. **`read_goal_note`** — `vault_read` (existing server) fetches the demo
   goal note; `goal_note.parse_goal_note` extracts `goal_theme` and reports
   its own status (`ok` / `no_content` / `malformed_frontmatter` /
   `missing_field`) rather than defaulting silently.
2. **`open_session`** — `start_session` returns one prescribed `action`
   with a `kind`. `ACTION_KIND_TO_PATH` (a fixed table, not branching logic)
   maps that `kind` onto exactly one of three paths — this is the "branch
   on server data, not on model choice" requirement made literal: the
   routing function (`route_on_action_kind`) does nothing but echo back the
   path `open_session` already computed.
3. **One of three paths**, chosen by that table:
   - *exercise* (`continue_next_step` / `open_first_lesson`) →
     `gen_exercise`, topic = `goal_theme` (if the note supplied one) else
     the action's own topic.
   - *review* (`revisit_topic` / `tired_mode_minimum`) → `find_i_plus_one`,
     same topic precedence.
   - *triage* (`resolve_thread`) → the envelope ceremony,
     `stage_untrusted` → `confirm_untrusted` → `triage_inbox(dry_run=True)`,
     on the unresolved thread's text.
4. **`grade_node`** — turns whichever path result exists into a scored
   observation. No tool call here by design (grading is a judgement about
   material already fetched); the shipped default is a deterministic,
   network-free stub (see §4 — this is a real, current limitation, not a
   simplification for this document).
5. **`close_session`** — `log_lesson` then `log_observations`, the
   observable side effect the log-back-read acceptance criterion checks
   for.
6. **`summary_node`** — one human-readable line, no tool call, closing the
   transcript.

Every node prints and appends a transcript line naming the tool and the
exact arguments — the mechanism the defence points at on screen to show
the branch actually happened, per FR-004 and T013's own module docstring.

## 4. Trade-offs and known limitations

Framed honestly rather than minimized — the reviewer should be able to
find each of these in the code, not take this document's word for it.

- **stdio-only for katagiri.** The custom server is reached exclusively
  over stdio, one client per process, started independently before the
  agent. There is no network listener, so no remote or multi-client access
  — appropriate for a personal tool (constitution I) but a real ceiling:
  nothing here scales past one caller talking to one server instance.
- **Single-user.** Both the demo profile and the personal one assume
  exactly one learner. `start_session`'s prescription ladder, the known-word
  set, and the event log are not partitioned by identity anywhere in this
  design — a second concurrent user was never a design goal and would need
  new work, not configuration.
- **Fixture-bound demo.** The graded run is deliberately restricted to a
  seeded fixture DB, a demo vault, and a demo token (`KATAGIRI_CONFIG`
  override, T007) so the personal DB/vault/token are structurally
  unreachable from the demo profile (T011's isolation guard). The upside is
  safety; the cost is that the demo's reachable states are exactly what
  T016 seeded — a fresh/empty DB collapses every run to "open a lesson",
  which is why the seed is asserted, not assumed.
- **Pinned model, and — as of this task — not actually called by the
  graph.** `openai/gpt-4o-mini` via OpenRouter is pinned (FR-007) so a
  provider's default routing cannot silently substitute a model that
  cannot emit tool calls. But the diagnostic-branch graph built in T013
  never invokes the model at all: `grade_node`'s shipped default
  (`_default_grader`) is a deterministic, network-free stub, and
  `GraphDeps.model` exists only so a future LLM-backed grader could be
  injected without a graph-structure change. `clients.build_bound_model`
  (T012) — which really does bind the featured subset to the model and
  return a bound model — is exercised by the smoke test (T017) but is not
  what `build_graph` wires up; the graph takes tools as a plain
  name-to-tool mapping precisely so the branch is never model free-choice
  (see graph.py's own module docstring). The pinned model's job in this
  project, currently, is to exist and be verified reachable (T027) — not
  to decide anything inside the graded workflow. This is worth stating
  plainly rather than letting "pinned model" read as "the model grades
  the session".
- **Degraded-mode limits.** `katagiri_agent.resilience` implements the
  full taxonomy spec.md US4 asks for — `TransportError` (retried with
  backoff and reconnect), `AuthError` (never retried, always surfaced),
  `EmptyResult` (a successful non-answer, never raised) — plus
  `call_or_degrade`, which turns exhausted existing-server retries into a
  stated, non-hidden **katagiri-only** completion. Two limits on this,
  read directly from the module: first, degradation is one-directional —
  only loss of the *existing* server degrades gracefully; there is no
  analogous "run without katagiri" path, because katagiri is the primary
  server the whole workflow is organized around, so its loss is an
  outright failure, not a degraded continuation. Second — and this is a
  genuine gap as of this writing, not a stylistic choice — `graph.py`'s
  `_call_tool` helper calls `tool.ainvoke(...)` directly and does not
  route through `resilient_call` / `call_or_degrade` anywhere; the
  resilience module is built and unit-tested against its own taxonomy
  (`agent/tests/test_resilience.py`) but is not yet imported by
  `katagiri_agent.graph`. The module docstring itself says wiring it in at
  the node boundaries is later work. Until that wiring lands, a
  transport failure inside a live graph run surfaces as whatever raw
  exception the MCP adapter raises, not as a classified
  `TransportError`/degraded completion.
- **The 26-tool surplus, framed as production surface.** Katagiri
  registers 26 tools; the model-facing allowlist is 11 of them (plus 2 on
  the existing server). The other 15 are not padding — they predate this
  feature (`ping`, `known_word`, `known_set_stats`, `recent_events`,
  `search_db`, `stop_gate_status`, `security_status`, `vault_file`,
  `vault_list` at the katagiri layer, `obsidian_active_note`,
  `search_notes`, `lessons`, `log_error`, `add_vocab`, `lesson_memory`) and
  serve the author's day-to-day use of katagiri outside any demo session.
  `tool-triage.md` gives the one-line reason each stays a helper. The
  honest framing, repeated from that document because it matters here too:
  this is production surface for a tool the author uses daily, of which a
  curated subset is exposed to the graded model — not a tool count
  inflated for the assignment, and not one that could have been trimmed
  without editing a registry this feature's scope claim forbids touching.

## 5. Differentiation from the reserved research-agent topic

The assignment reserves a topology built around a **research agent**: the
model *proposes* an experiment or approach, the system *runs* it, the
result is *compared to a stored baseline*, and the comparison is *recorded*
in an experiment store for later retrieval — propose → run → compare-to-
baseline → record, with persistence of the comparison itself as a first-
class artifact.

This project's topology is different in kind, not just in domain:

- **The branch is a diagnostic read of already-computed state, not a
  proposal.** `start_session` returns one prescribed `action` computed by
  `katagiri.session_tools.prescribe()` from the learner's existing DB rows
  (due reviews, open threads, ladder position) *before* the agent graph
  ever runs. The graph's only job is to look up `action.kind` in a fixed
  table (`ACTION_KIND_TO_PATH`) and take the one path that table names.
  Nothing in this project asks the model to propose what to try next; the
  server already decided, and the graph's contribution is auditable
  routing, not proposal generation.
- **There is no "run an experiment" step.** The exercise/review/triage
  paths call one katagiri tool each (`gen_exercise` / `find_i_plus_one` /
  the triage ceremony) to fetch or generate study material for the branch
  already chosen. None of them execute a model-authored plan, a
  parameterized trial, or anything with a notion of "this run" as an
  experimental unit distinct from an ordinary study session.
- **There is no baseline to compare against, and no comparison step.**
  `grade_node` scores the one path result that exists against a fixed
  rubric shape (`task_type`/`unassisted`/`coverage_band`/`rubric_version`);
  it does not hold a prior run's result, does not diff against one, and
  nothing in `GraphState` carries a "baseline" field. A grade is a judgment
  about this session, not a delta against a stored comparison.
- **There is no experiment store.** The only persistence this workflow
  produces is `log_lesson` and `log_observations` writes into katagiri's
  existing event log — the same sacred, append-only log every ordinary
  study session writes into (constitution III). There is no separate table,
  file, or object recording "trial N vs. baseline", no retrieval API for
  past experiments, and no schema anywhere in this feature for the concept
  of an experiment as opposed to a lesson/observation pair. `recent_events`
  and `lessons` (both helpers, never generation/comparison tools) can be
  read back, but they read back *lessons*, not experiment results.

In one sentence: this project demonstrates a **diagnostic branch on
server-computed learner state** — read state, branch on what the state
already says to do, act, grade, log — with no propose/run/compare/record
loop and no experiment store anywhere in the design, which is exactly the
shape spec.md's US2 asks for and exactly the shape the reserved topic is
not.
