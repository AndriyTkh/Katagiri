# Tool triage — substantive tools vs. helpers

Scope: katagiri's own MCP surface, all 33 tools declared in
`src/katagiri/tool_registry.py` (`TOOL_SPECS`, read-only source for this
table — nothing here changes the registry). This document does **not**
triage the existing Obsidian Local REST API server; that server is
documented separately in `docs/assignment/existing-server-contract.md`.

Seven of the 33 (`media_now`, `media_context`, `lyrics_now`,
`lyrics_context`, `screenshot_capture`, `screenshot_read`, `open_anki`) are
Phase E additions — the media overlay and Anki launcher — landed after this
assignment's own T012/T021 triage was fixed. They are listed among the
helpers below for completeness, but they postdate and are unrelated to the
005 assignment's scope.

**On the rubric text**: `specs/005-mcp-assignment/spec.md` cites "rubric
§3" repeatedly (FR-015, SC-003, and — at spec.md line 60 — an explicit
"rubric §3 criterion 5" for *observable side effect*) but the 100-point
assignment document itself (`ai_agentic_lab_assignment`, spec.md line 9)
is not checked into this repository. The five criteria scored below are
reconstructed from those citations plus the settled decision already
recorded in `specs/005-mcp-assignment/research.md` ("Triage table names
the substantive tools..."), not quoted verbatim from an uncommitted
external file. They are the ordinary shape a "substantive tool" claim has
to survive: a distinct purpose, logic beyond simple retrieval, a
meaningful input/output contract, real error/failure handling, and an
observable effect on the workflow (the criterion spec.md names outright).

## The verdict (fixed at T012, mirrored here)

- **Substantive (5)**: `coverage`, `find_i_plus_one`, `gen_exercise`,
  `build_sentences`, `triage_inbox`. Three of the five (`gen_exercise`,
  `build_sentences`, `triage_inbox`) are beyond retrieval outright —
  they generate material or write state — clearing FR-015's "≥2 beyond
  retrieval" with room to spare.
- **Primary data source (1)**: `lookup` — JMdict senses plus pitch accent,
  read from the vendored dictionary import rather than hand-authored.
- **Helper (27)**: every other registered tool. Five of those twenty-seven
  are in the model-facing allowlist anyway (session/logging plumbing and
  the envelope ceremony); the other twenty-two are server-surface only —
  fifteen predating this assignment plus the seven Phase E media/
  screenshot/Anki additions. Neither group claims substantive status — see
  the table below for why, one line each.

## Substantive tools scored against the five criteria

| Tool | Distinct purpose | Beyond retrieval | Meaningful I/O contract | Error/failure handling | Observable effect on workflow |
|---|---|---|---|---|---|
| `coverage` | Only tool that measures known-word coverage of arbitrary pasted text against the real known set — nothing else in the registry answers "how much of this can I already read". | Computes a derived metric: `known_pct`/`band` plus unknown types ranked by `cumulative_pct`. Not a row lookup — real tokenization + aggregation over `katagiri.intelligence`. | Free-form `text` in (up to 100k chars); rich structured stats out (`counts`, `types`, ranked `unknown[]`). | Refuses with `tokenizer_unavailable` rather than guessing at segmentation; `known_pct`/`band` come back `null`, not `0`, when there is no countable token — a deliberate "no honest numeric answer" distinction. | Read-only itself, but its ranked-unknown output is what `find_i_plus_one`'s vocabulary gate and the theme/topic branch consume — it feeds the diagnostic branch, it doesn't sit beside it. |
| `find_i_plus_one` | Only tool that gates material on *two independent axes at once* (grammar-DAG reachability and vocabulary coverage) — the assignment's own D-28 requirement, named nowhere else. | A real ranking/gating algorithm: comprehension-debt scoring, prerequisite-DAG reachability, optional difficulty-for-me scoring across four vendored datasets, cycle detection. Nothing here is a lookup by key. | Candidates with free-form `text` (+ optional `grammar_ids`) in; per-candidate `accepted`/`gated_by`/`coverage`/`grammar`/`debt`/`difficulty` out, plus aggregate `counts`/`gates`. | Refuses with `grammar_dag_cycle` naming the offending cycle instead of answering from a graph with no valid answer; every rejection carries `gated_by` rather than a bare false. | Read-only, but it is the material-selection step the diagnostic branch depends on — its ranked output is what gets handed to `gen_exercise`/`build_sentences` downstream. |
| `gen_exercise` | Only tool that turns "these items are due" into an actual drill set. | Beyond retrieval: deterministic selection (never-drilled first, then longest-ago) *plus* generation, with every candidate string screened against the sealed canary set before it can be returned. | `item_ids`/`topic`/`direction`/`count` in; generated `exercises[]` plus `screened_out`/`skipped`/`redirects` out. | Fails closed and distinguishes failure shapes: an explicitly requested item that the canary guard refuses fails the whole call; a pool candidate that fails is dropped into `screened_out` and the next item is tried instead — not the same failure mode collapsed into one. | Read-only itself; its output is what the learner is drilled on and what `log_observations` is later scored against — it is the payload of the exercise branch, not a side note to it. |
| `build_sentences` | Only tool that authors practice sentences, from templates or from enveloped external material — distinct from both `gen_exercise` (drills on existing items) and `triage_inbox` (files existing text). | Squarely beyond retrieval: template selection keyed by coarse part of speech (a POS with no template yields nothing rather than invented Japanese), external-line mining under the envelope protocol, canary screening like `gen_exercise`. | `item_ids`/`topic`/`source_envelope_id`/`challenge_id`/`echo` in; `sentences[]` with `origin`/`provenance`/`needs_review`/`canary_screened` out. | Two-step echo-back flow (`echo_back_required` carrying the challenge) when external material is the source; receptive-only items are skipped rather than given a production sentence; fails closed on the canary set exactly like `gen_exercise`. | Read-only; recording what was built is deliberately the caller's job through `log_observations`, but the sentences it authors are the material the rest of the loop acts on. |
| `triage_inbox` | Only tool that turns an unstructured inbox capture into filed vocabulary — distinct from `add_vocab` (mines one already-identified word) and from the vault-read tools (which only retrieve text, never classify it). | Unambiguously beyond retrieval: mechanical classification of shape (not meaning) into vocab/sentence/question, and on `dry_run=False` it **writes** — item rows plus mining events — the clearest "observable side effect" case of the five. | `note_envelope_id` (envelope-only, never a bare string) + `dry_run` in; `proposals[]`/`applied[]`/`deferred` out. | `dry_run=True` (default) previews with no echo-back required; `dry_run=False` requires the spent envelope confirmation before anything is filed — preview and commit are never the same call. | The strongest case on this criterion: a real, logged state change (new item rows, `event_id`) rather than a read that merely informs the next step. |

## Every other tool of the 33 — helper, one line each

Featured (allowlisted to the model per T012) but not substantive — session
bookkeeping and the envelope ceremony, not tool-design substance:

1. `start_session` — chooses and logs one prescribed action from a fixed
   ladder; it dispatches to existing state, it does not generate or
   transform study material.
2. `log_lesson` — inserts/updates one lesson row (open/close in one call);
   a structured write-and-echo, no computation over its inputs.
3. `log_observations` — validates and writes rubric-scored performance
   rows; the work is committing already-decided data, not reasoning over
   it (all-or-nothing validation, no defaulting).
4. `stage_untrusted` — envelope step 1 of 3: wraps external text and
   returns an echo-back challenge; infrastructure for the untrusted-data
   protocol, not a study-domain capability.
5. `confirm_untrusted` — envelope step 2 of 3: verifies the echoed text
   against the digest; the same ceremony role as `stage_untrusted`, not a
   capability of its own.

Server-surface only (not in the model-facing allowlist at all — discoverable
over the MCP protocol, never offered to the pinned model in the demo):

6. `ping` — liveness/version readout; no domain logic.
7. `known_word` — single membership check against the known set; pure
   retrieval by id or surface form.
8. `known_set_stats` — pre-aggregated counts read straight off stored
   state; no transformation is performed here, the shape is already in
   the tables.
9. `recent_events` — raw event-log tail, newest first; a filtered read,
   not an analysis.
10. `search_db` — FTS-backed retrieval over items/aliases/sentence text;
    the registry's own summary calls it "definitive local search", which
    is a retrieval claim, not a generation one.
11. `stop_gate_status` — mechanical PASS/FAIL comparison against fixed
    thresholds; it does append a `gate_evaluation` event, but the work is
    a threshold check, not generation or transformation of study material.
12. `security_status` — parses `netstat` and reports port bindings; an
    infrastructure check, unrelated to the study domain.
13. `vault_file` — GET-only proxy that returns one note's raw content;
    retrieval through a relay, nothing computed over what comes back.
14. `vault_list` — GET-only proxy that lists one vault directory; same
    relay role as `vault_file`, one level up.
15. `obsidian_active_note` — GET-only proxy for whichever note is focused
    in Obsidian; retrieval, and only conditionally available (depends on
    Obsidian being open).
16. `search_notes` — FTS-backed retrieval over the derived markdown index;
    the katagiri-index counterpart to `search_db`, same retrieval shape.
17. `lessons` — read-only listing of past lesson rows, with counts already
    computed by the `lesson_outcome` view rather than by this tool.
18. `log_error` — records one mistake row (`said`/`correct`/`pattern`);
    a structured write-and-echo, no computation over its inputs.
19. `add_vocab` — inserts or fills in one item row plus a mining event; a
    single deterministic write (id derived from kanji+reading), not
    generation or multi-source reasoning.
20. `lesson_memory` — read-only aggregate snapshot (next action, open
    threads, due revisits, pending next steps) assembled from stored rows;
    an aggregation of existing state, not authored content.

Phase E additions (server-surface only, postdate this assignment's T012/T021
triage — the media overlay and Anki launcher):

21. `media_now` — probes the active media channel (mpv) for the current
    title/playhead, enveloped; a liveness/state probe, not generation.
22. `media_context` — the subtitle/lyric window around the current
    playhead on the active channel; retrieval of what is already on
    screen, not authored content.
23. `lyrics_now` — the lyric line active at the mpv playhead, read from a
    supplied `.lrc`/`.ass` file; a lookup against a file the caller names,
    not generation.
24. `lyrics_context` — the window of lyric lines around the playhead from
    the same file; same retrieval shape as `lyrics_now`, one level up.
25. `screenshot_capture` — captures mpv's current frame to a
    server-named file; a capture operation with no domain logic over the
    image itself.
26. `screenshot_read` — returns a previously captured screenshot's raw
    bytes, base64-encoded; pure retrieval by id.
27. `open_anki` — launches the Anki desktop app if not already running;
    an infrastructure/process-launch action, unrelated to the study
    domain and orthogonal to the katagiri database entirely.

## The allowlist-vs-surface split, stated plainly

The pinned model in the demo (`openai/gpt-4o-mini`, per T012) is bound to a
**client-side allowlist of 11 of these 33 tools**: the five substantive
tools, `lookup` as the primary data source, and five helpers required for
the session/logging graph and the envelope ceremony (`start_session`,
`log_lesson`, `log_observations`, `stage_untrusted`, `confirm_untrusted`).
The remaining 22 tools stay fully registered and discoverable at the MCP
protocol level — a caller that lists tools over stdio sees all 33 — but the
agent graph never offers them to the model. This narrowing is
**client-side only**; there is no server-side profile, per
`specs/005-mcp-assignment/research.md`'s "Tool narrowing" decision, because
katagiri really does have 33 tools and pretending otherwise at the server
would misstate the scope claim.

## Framing the surplus honestly

Twenty-two of the thirty-three tools exist for reasons that have nothing to
do with this assignment's demo: liveness (`ping`), security posture
(`security_status`), the 006 study-consistency gate (`stop_gate_status`),
raw event-log and search access for debugging and future tooling
(`recent_events`, `search_db`, `search_notes`), the Obsidian read bridge
used by the learner's own workflow outside any demo session (`vault_file`,
`vault_list`, `obsidian_active_note`), day-to-day logging/mining that
predates this feature entirely (`log_error`, `add_vocab`, `lessons`,
`lesson_memory`, `known_word`, `known_set_stats`), and — landed later still,
under Phase E — the media overlay and Anki launcher (`media_now`,
`media_context`, `lyrics_now`, `lyrics_context`, `screenshot_capture`,
`screenshot_read`, `open_anki`), none of which the demo flow touches at
all. None of it was added to pad the tool count for this submission — the
registry predates T012's allowlist decision (and the Phase E tools postdate
it entirely, added for the author's own daily use), and the additive-only
contract rule in `tool_registry.py`'s own module docstring means nothing
here could have been trimmed to look leaner even if that were desirable.
The honest framing is: this is **production surface for a tool the author
uses daily**, of which a curated eleven-tool subset is exposed to the
graded model, and the triage above is what separates "substantive" from
"helper" inside that full surface rather than inside the smaller
allowlist.
