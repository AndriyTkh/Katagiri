# Defence script — spec 005-mcp-assignment (T024)

This is the word-for-word walkthrough for the graded recording: the
assignment's 9 demonstration steps, in order, each with what is on screen,
what is said, and what proves it to a grader watching the recording rather
than reading the source; the 5-segment timing table with a per-segment
budget; a prepared answer for each likely instructor probe; and the demo
cut list. `specs/005-mcp-assignment/quickstart.md` **is** the executable
runbook this script narrates — the commands below are copied verbatim from
it so the two files cannot drift silently. If they ever do, quickstart.md
wins (it is what TG-E's rehearsal, T028, actually amends) and this file
gets corrected to match, not the other way round.

**Reconstruction notice.** The assignment's own document
(`ai_agentic_lab_assignment`, a 100-point rubric) is **not checked into this
repository** (spec.md line 9/22). `specs/005-mcp-assignment/spec.md` and
`quickstart.md` restate the "9 demonstration steps" and "5-segment timing
table" as **binding requirements** (FR-017, US5 acceptance 5, the
005-verify gate) with enough structural detail — segment names, segment
minute budgets, the step list in `quickstart.md`'s own numbered headings —
to reconstruct the steps faithfully. This script follows `quickstart.md`'s
own step numbering and segment structure exactly, because `quickstart.md`
states outright that it *is* the defence, executed start to finish by the
005-verify gate. Where this script adds stage directions (what to say,
what proves it) beyond what quickstart.md already says, that is this
task's own addition, not a quote from the external assignment file. This
mirrors the reconstruction disclosure `docs/assignment/tool-triage.md`
already carries for the rubric's five substantive-tool criteria.

**Status of the commands below, honestly.** As of this writing (after
T012–T017, T020–T023), `agent/src/katagiri_agent/` has no `__main__.py` —
`uv run --project agent python -m katagiri_agent ...` is quickstart.md's
committed *intended* invocation, not yet a runnable command. Final assembly
(wiring `build_graph`, `clients.build_bound_model`, and
`katagiri_agent.resilience` together behind a CLI entry point) is tracked
as later work before TG-E's rehearsal (T028). This script's segment-3/4
commands are copied from quickstart.md as the current source of truth; if
T028 finds the real CLI needs different flags or output, **quickstart.md
gets amended first**, and this file's affected steps are corrected in the
same pass — do not let this script and quickstart.md say two different
things about what to type.

**Two more things T028/T027 close, not this task**: the timing table's
*Actual* column is intentionally blank below — T028 fills it in from the
real rehearsal, this task only ships the *budget*. And the "inference
readiness" line the tasks.md text for T027 asks this file to carry:
**pending T027** — the $10 OpenRouter top-up has not happened yet, so no
step below that calls the pinned model (`openai/gpt-4o-mini`) has been
exercised end-to-end against a funded account. Everything mechanical about
the graph, the branch, the provenance passthrough, and the failure
taxonomy is unit- and integration-tested against stubs (T013/T015/T017);
what is specifically unverified live is the funded-inference path.

---

## The 9 demonstration steps

Numbered exactly as `quickstart.md`'s own step headings (Step 0 is
off-camera pre-flight, not one of the 9).

### Step 1 — Start the custom MCP server independently of the agent

**On screen**: a terminal window, nothing else running yet.

```powershell
$env:PYTHONUTF8="1"; $env:KATAGIRI_CONFIG="<demo config path>"; uv run katagiri
```

**Say**: "This is katagiri, my own MCP server, starting as its own
process — I didn't launch it from inside the agent, and the agent doesn't
manage its lifecycle. It talks stdio only, `mcp>=2,<3`, no network
listener. Its contract is 26 tools, declared once in
`src/katagiri/tool_registry.py`, checked into the repo. `KATAGIRI_CONFIG`
points it at the demo profile, not my personal database."

**Proves it**: the process starts in its own window and stays running;
the terminal shows no error; a grader can see two independent process
trees (this one, and the agent's, started in Step 2) rather than one
process spawning the other.

### Step 2 — Show the agent discovering both MCP connections

**On screen**: a second terminal.

```powershell
uv run --project agent python -m katagiri_agent --list-connections
```

**Say**: "The agent opens one `MultiServerMCPClient` with two server
entries — katagiri over stdio, the Obsidian Local REST API server over
Streamable HTTP against the demo vault instance. Both initialize and both
report their discovered tool count."

**Proves it**: the command's output lists both connection names with
non-zero discovered-tool counts — katagiri's count matches "26 tools
registered, 11 featured" (`docs/assignment/tool-triage.md`'s allowlist
split) and the Obsidian connection lists at least `vault_list` /
`vault_read` (`OBSIDIAN_FEATURED_TOOLS`,
`agent/src/katagiri_agent/clients.py`).

### Step 3 — Invoke a tool from the approved existing server successfully

**On screen**: the same terminal, or a REPL/small script calling
`vault_read` directly against the demo instance.

**Say**: "This is the demo vault's goal note, read through the Obsidian
plugin's own MCP endpoint — not a file read, not something katagiri's
proxy touches. Here's the raw frontmatter block: `goal_theme: food`,
`focus: restaurant-ordering`."

**Proves it**: the raw `vault_read` return value is visible on screen,
frontmatter and all, matching the shape documented in
`docs/assignment/existing-server-contract.md` §4/§8 (a bare string or a
`content`/`text`/`body`-keyed dict, both tolerated by
`katagiri_agent.goal_note._extract_raw_text`).

### Step 4 — Run an agent flow in which that result affects a later step

**On screen**: the flow's transcript scrolling in the terminal.

```powershell
uv run --project agent python -m katagiri_agent --goal-note "Goals.md"
```

**Say**: "Watch this line —
`[read_goal_note] frontmatter status='ok': '...': goal_theme='food'.` —
that's the existing server's data entering the graph. Now watch the next
tool call downstream: `topic='food'` is passed as a literal keyword
argument, not as an instruction to the model. That's the passthrough the
rubric calls the decorative-read fix."

**Proves it**: the transcript line
`[exercise_path] called gen_exercise({'topic': 'food', ...})` (or
`review_path` / `find_i_plus_one`, depending which path the seeded action
lands on) shows the exact string `'food'` (or `'transport'` for variant
B) appearing verbatim as an argument value — the literal string traced
from the frontmatter, per `katagiri_agent.graph.make_exercise_path` /
`make_review_path` and the `ProvenanceEntry` appended to
`state["provenance"]`.

### Step 5 — Explain that tool's contract and the server's role

**On screen**: `docs/assignment/existing-server-contract.md` open
alongside the terminal (or read aloud from it).

**Say**: walk the 8 rows for `vault_read` — name, model-facing
description, input schema (`filepath`, vault-relative, no leading slash),
output schema (bare string or `content`/`text`/`body` dict, both
tolerated), purpose (the one point the existing server's data enters the
graph), error conditions (the three-way split below), side effects
(none — GET-shaped, read-only, no write-shaped tool bound), and why a
notes server has a real role here: "the vault is the learner's own
workbook — nothing in `agent/` or katagiri can read a goal note except by
asking this server for it; there's no shortcut that respects the
untrusted-vault-content boundary."

**Proves it**: every row named out loud matches
`docs/assignment/existing-server-contract.md` verbatim — a grader with
the doc open on a second monitor can follow along and check.

### Step 6 — Run one complete workflow that uses the custom server

**On screen**: continuation of Step 4's same run (same command, same
transcript scrolling further).

**Say**: "`start_session` just returned one prescribed action —
`action.kind='continue_next_step'` (or whichever kind the seeded DB state
returns on this call) — and the graph looked that kind up in a fixed
table, `ACTION_KIND_TO_PATH`, and took the `exercise` path. That's not the
model choosing; it's a dictionary lookup on server-computed data. Now
`gen_exercise` runs, then grading, then the close: `log_lesson` and
`log_observations` write into the fixture DB's event log, then a one-line
summary."

**Proves it**: the transcript shows, in order, one line per node —
`[open_session] action.kind='...' -> path='...'`, the path's tool call
line, `[grade_node] graded via ...`, `[close_session] called
log_lesson(...)`, `[close_session] called log_observations(...)`, and
`[summary_node] synthesized summary (no tool called): ...` — matching
`katagiri_agent.graph`'s eight nodes (`read_goal_note` → `open_session` →
one of `exercise_path`/`review_path`/`triage_path` → `grade_node` →
`close_session` → `summary_node`) exactly, per T013's own module
docstring and `docs/assignment/design-rationale.md` §3.

### Step 7 — Show evidence that at least three custom tools are exposed

**On screen**: Step 2's connection-listing output, plus
`docs/assignment/tool-triage.md`.

**Say**: "Five tools here are substantive, not helpers —
`coverage`, `find_i_plus_one`, `gen_exercise`, `build_sentences`,
`triage_inbox` — and three of those five are beyond retrieval outright:
they generate material or write state. `lookup` is the primary data
source, reading the vendored JMdict import. The model only ever sees an
11-tool allowlist; katagiri itself still exposes all 26 at the protocol
level — that's a client-side narrowing, not a server-side profile, so the
scope claim (`src/katagiri/tool_registry.py` untouched) holds."

**Proves it**: Step 2's tool-count output shows 26 discoverable at
katagiri's connection and the triage doc's verdict table lists the same
five substantive tools plus `lookup`, matching FR-015/SC-003 word for
word.

### Step 8 — Explain one important custom tool contract and design decision

**On screen**: `docs/assignment/tool-contracts.md`, scrolled to one
substantive tool (`gen_exercise` is the recommended pick — it is both
generative and screened), plus its paragraph in
`docs/assignment/design-rationale.md` §2.

**Say**: walk `gen_exercise`'s 8 rows (generated schema rows from
`scripts/gen_tool_contracts.py`, hand-written purpose/errors/side
effects/example from T020), then its "why this belongs at the MCP
boundary" paragraph: "every candidate string is screened against a sealed
canary set before it's returned — that set can never be visible to the
model or to `agent/` code, so this screening can only happen server-side.
Inlining it would mean handing the canary set to the caller, which
defeats the entire point."

**Proves it**: the doc on screen matches
`docs/assignment/tool-contracts.md`'s generated+hand blocks for
`gen_exercise` exactly (drift-checked by `tests/test_contract_docs.py`),
and the rationale paragraph matches
`docs/assignment/design-rationale.md` §2 verbatim.

### Step 9 — Demonstrate one realistic failure involving the existing MCP server

**On screen**: the demo Obsidian window, then the agent terminal.

**Say** (primary injection — stop the plugin mid-flow): "I'm disabling
the Local REST API plugin right now, mid-run." *(disable the community
plugin, or close the demo Obsidian window)* "Watch the agent report which
connection failed and why, retry with backoff, and either recover once I
re-enable it, or — if it stays down — finish on a degraded katagiri-only
path that says so instead of hiding it."

**Proves it**: a `TransportError`-shaped report names the server
(`"obsidian"`) and a human-readable reason
(`katagiri_agent.resilience.classify_exception` / `ResilienceError`'s
`[{server}] {detail}` format); if recovery happens, a line shows the
session re-establishing; if it does not, the transcript prints
`Degraded.message()`'s exact text —
`"DEGRADED: continuing katagiri-only -- obsidian could not be reached
after retries (...). This run completed without it."` If asked, show the
kill-and-resume: kill the agent process after a checkpoint write, restart
it with the same `thread_id`, and point at the transcript **not**
repeating nodes that already completed — the real Windows
kill-and-resume T015 exercised, via `AsyncBridgeSqliteSaver`
(`agent/src/katagiri_agent/checkpoint.py`).

**Then undo the injection exactly as documented** — see "Undoing the
failure injection" below — before continuing to Segment 5.

---

## The 5-segment timing table

| # | Segment | Budget | Actual | Steps covered |
|---|---|---|---|---|
| 1 | Startup and architecture overview | 2 min | _(T028 fills in)_ | Steps 1–2 |
| 2 | Existing server inside an agent flow | 2–3 min | _(T028 fills in)_ | Steps 3–5 |
| 3 | Custom MCP end-to-end workflow | 3–4 min | _(T028 fills in)_ | Steps 6–8 |
| 4 | Failure scenario | 2 min | _(T028 fills in)_ | Step 9 |
| 5 | Questions and one small variation | 3–4 min | _(T028 fills in)_ | probes below |

Total budget: 12–15 min against the rehearsal gate's stated 10–15 minute
window (spec.md SC-007, quickstart.md "Expected outcomes" item 7). The
*Actual* column stays blank until TG-E's T028 rehearsal records real
timings against this table and amends both this file and
`quickstart.md` wherever reality diverged — per T028's own task text,
that amendment happens **here**, not from memory on the day.

---

## Prepared answers for likely instructor probes

These map onto Segment 5 and onto `quickstart.md`'s own "Questions and
one small variation" section, expanded into full prepared answers.

### "Vary a valid input"

**Do**: rerun with the goal-note variant B (`goal-note.variant-b.md`,
`goal_theme: transport`) instead of variant A (`goal_theme: food`).

```powershell
uv run --project agent python -m katagiri_agent --goal-note "Goals-B.md"
```

**Say**: "Same tool, same argument name, a different literal value —
`topic='transport'` instead of `topic='food'` — flowing into the same
katagiri call. The output changes because the argument changed, not
because I changed which tool gets called." Point at the transcript line
and at the exercise/review result's topic field to show the difference
concretely (`docs/assignment/existing-server-contract.md`'s closing note:
"same tool, same argument shape, a different literal value").

### "Give an invalid input"

**Do**: point `vault_read` at a note with malformed or missing
frontmatter (a fixture with no `---`/`---` block, or with frontmatter but
no `goal_theme` key).

**Say**: "This isn't a crash and it isn't a silent default either —
watch the status line:
`[read_goal_note] frontmatter status='missing_field': ...` names exactly
which condition happened and which keys *were* present. Nothing downstream
pretends a theme was supplied; the exercise path falls back to the
prescribed action's own topic field instead, and that fallback is visible
in the transcript, not hidden." Cite
`katagiri_agent.goal_note.GOAL_NOTE_STATUSES`
(`ok` / `no_content` / `malformed_frontmatter` / `missing_field`) as the
closed set of outcomes — there is no fifth, undocumented shape.

### "Identify a side effect"

**Say**: "The `log_lesson` and `log_observations` calls in `close_session`
are the observable side effect — they write into the fixture DB's
append-only event log." Then show it: read the log back (`recent_events`
tool, or a direct query against the demo DB) and point at the new rows
with today's timestamp and this run's `session_id`. Add, if pressed on
*why* it's append-only: "constitution III, 'Event Log Is Sacred' — nothing
in this project edits or deletes a written event; `triage_inbox` on
`dry_run=False` would be the other write path, but this graph's triage
branch always runs `dry_run=True`, so it only proposes, never files."

### "Say where a returned value came from"

**Say**: "It's a lookup, not a reconstruction — the provenance record is
right here." Read the `ProvenanceEntry` off `state["provenance"]` (or the
checkpoint) and walk its fields aloud:

```json
{
  "note_path": "00-goals/goal-note.md",
  "source_tool": "vault_read",
  "source_field": "goal_theme",
  "value": "food",
  "katagiri_tool": "gen_exercise",
  "katagiri_argument": "topic",
  "output_field": "exercise_result"
}
```

"Note path, to the tool that read it, to the field it extracted, to the
literal value, to the katagiri tool and argument it was placed into, to
the output field it ended up shaping — every hop is named, nothing is
inferred after the fact."

---

## Undoing the failure injection

Per Step 9 and `quickstart.md`'s Segment 4: undo exactly as follows, so
the recording can continue and so a second take never starts from a
half-broken environment.

- **Plugin stopped (primary)**: re-enable the Local REST API community
  plugin in the demo Obsidian window (or reopen the demo vault window if
  it was closed instead). Confirm recovery by re-running Step 2's
  `--list-connections` command and checking the Obsidian connection is
  discovered again, or by watching the agent's own reconnect line in the
  transcript if the run is still live.
- **Invalid API key (backup)**: restore the correct demo token into
  `agent/.env`'s `OBSIDIAN_API_TOKEN` from wherever it was staged before
  the injection (never type the real token on camera — swap the file, or
  restore from a pre-saved backup value, off-screen if needed).
- **Missing note (backup)**: restore the goal note's original filename/
  path (if the injection renamed or moved it) or restore its frontmatter
  content (if the injection blanked or corrupted it) from the committed
  fixture at `tests/demo_fixtures/vault/00-goals/`.

None of these injections touch the fixture DB or the checkpoint DB, so no
database restore step is needed after any of the three.

---

## Demo cut list

**Binding, per FR-017 and `quickstart.md`'s "Rehearsal rules"**: the
following never appear in the recording, full stop, regardless of how a
segment is going:

- **No VOICEVOX** — no text-to-speech, no audio synthesis of any kind.
- **No Irodori content** — the textbook backbone this project studies
  from is never shown, quoted, or referenced on camera.
- **No worksheets** — no printed or on-screen worksheet material.
- **No drill modes** — no gameified/drill-style study UI; the demo shows
  the MCP workflow, not a study session's UI polish.

**If a segment overruns, cut in this order** — drop the cheapest thing
first, never the thing a rubric section is actually scored on:

1. **Segment 5's "one small variation" (variant B rerun)** — cut first.
   The provenance-trace probe answer can be given verbally from the
   already-completed Segment 2/3 run instead of a fresh rerun; SC-002's
   "two runs differ" claim was already demonstrated once in Segment 3.
2. **Segment 1's `--list-connections` narration detail** — say "both
   connections are up, 26 and N tools respectively" in one sentence
   instead of reading the full output line by line; the terminal output
   stays on screen either way for a grader to read themselves.
3. **Segment 4's kill-and-resume demonstration** — only show this if
   asked; the primary plugin-stopped injection and its recovery/degrade
   report alone already satisfy SC-004's "distinct, readable reports"
   bar. Kill-and-resume moves to a verbal description ("this was verified
   in rehearsal; here's what the checkpoint looks like on disk") if time
   is short.
4. **Segment 3's full 8-row read of `gen_exercise`'s contract (Step 8)**
   — narrow to just the "why this belongs at the MCP boundary" paragraph
   (the canary-set argument) and skip reading every schema row aloud; the
   doc stays visible on screen for the grader to check the rest.
5. **Never cut**: Steps 1, 2, 4, 6, 9 (the minimum-condition rule's own
   evidence — both connections discovered and called, the literal-argument
   passthrough, the server-computed branch, one realistic failure) or the
   provenance-trace probe answer itself. These are the rubric's
   highest-leverage items (spec.md's "Why this priority" notes for US1/US2/
   US4) and cutting any of them risks the 59/100 ceiling spec.md warns
   about for a decorative-read demo.

---

## Cross-references

- `specs/005-mcp-assignment/quickstart.md` — the executable runbook this
  script narrates; the source of truth for exact commands and step
  numbering.
- `docs/assignment/existing-server-contract.md` — `vault_read`'s full
  8-row contract (Step 5).
- `docs/assignment/tool-contracts.md` — the generated+hand-written 8-row
  table for all 26 katagiri tools (Step 8).
- `docs/assignment/tool-triage.md` — the substantive/helper/primary-source
  verdict (Step 7).
- `docs/assignment/design-rationale.md` — per-tool MCP-boundary rationale,
  the workflow walkthrough, trade-offs, and the reserved-topic
  differentiation (Steps 6, 8, and any "how is this different from a
  research agent" probe not listed above).
- `docs/assignment/demo-setup.md` — the demo-profile setup this script
  assumes is already done (Step 0, off-camera).
