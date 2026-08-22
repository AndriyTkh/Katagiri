# Quickstart: 005 validation — **this file is the defence runbook**

The 005-verify gate (TG-E) executes this document start to finish. It is not a summary of the defence; it *is* the defence, in order, with the assignment's 9 demonstration steps numbered as they appear on the day. TG-E amends this file wherever rehearsal diverged from it. Demo profile only — never the personal DB, vault, or token.

## Prerequisites

- TG-A..TG-D merged; `uv run pytest` green and `uv run pytest --public-build` green.
- Demo profile prepared per `docs/assignment/demo-setup.md`: fixture DB built (record the build time), demo vault present, demo Obsidian instance on its **non-default** port with its **own** token, `KATAGIRI_CONFIG` pointed at the demo config.
- OpenRouter account **topped up** (T027) and the pinned model confirmed reachable.
- Windows Defender / firewall prompts pre-approved.
- Recording surface clean: no personal vault window, no personal notes, no tokens visible.

## Step 0 — pre-flight (off camera)

```powershell
uv run python scripts/preflight_demo.py
```

Must exit 0. It checks: demo port bound and ≠ 27123, no stale katagiri/agent processes, required env keys present (presence only), the T011 isolation guard, one real tool-call round-trip per connection, checkpoint DB writable.

## Segment 1 — Independent startup and architecture overview (2 min)

**Step 1 — start the custom MCP server independently of the agent.** In its own terminal, in its own process:

```powershell
$env:PYTHONUTF8="1"; $env:KATAGIRI_CONFIG="<demo config path>"; uv run katagiri-mcp
```

Say out loud: separate process, stdio transport, `mcp>=2,<3`, 33 registered tools, contract checked in at `src/katagiri/tool_registry.py`.

**Step 2 — show the agent discovering both MCP connections.** In a second terminal:

```powershell
uv run --project agent python -m katagiri_agent --list-connections
```

Both connections initialize; the katagiri stdio connection and the Obsidian connection are both listed with their discovered tool counts.

## Segment 2 — Existing MCP server inside an agent flow (2–3 min)

**Step 3 — invoke a tool from the approved existing server successfully.** Read the demo vault's goal note through the Obsidian Local REST API MCP server. Show the raw result, including the frontmatter block.

**Step 4 — run an agent flow in which that result affects a later step.**

```powershell
uv run --project agent python -m katagiri_agent --goal-note "Goals.md"
```

Point at the transcript line where the frontmatter field value appears **as a literal argument** to the katagiri call (theme filter on `find_i_plus_one` / topic on `gen_exercise`), then at the output that changed because of it.

**Step 5 — explain that tool's contract and the server's role.** From `docs/assignment/existing-server-contract.md`: name, model-facing description, arguments and constraints, returned content, likely errors, side effects, and why a notes server has a real role in a study agent (the vault is the learner's workbook).

## Segment 3 — Custom MCP end-to-end workflow (3–4 min)

**Step 6 — run one complete workflow that uses the custom server.** The same command as step 4 continues: `start_session` returns exactly one prescribed action → the graph **branches on `action.kind`** → the chosen path runs (exercise / review / triage) → grade → `log_lesson` / `log_observations` → summary. Name the branch taken and why the server chose it.

**Step 7 — show evidence that at least three custom tools are exposed.** Show the discovered tool list from step 2 plus `docs/assignment/tool-triage.md`: `coverage`, `find_i_plus_one`, `gen_exercise`, `build_sentences`, `triage_inbox` as substantive (≥2 beyond retrieval), `lookup` as the primary-data-source tool over vendored JMdict. Note that the model is bound to an allowlisted featured subset (11 tools) while the server exposes all 33.

**Step 8 — explain one important custom tool contract and design decision.** One tool from `docs/assignment/tool-contracts.md` in full 8-row form, plus its "why this belongs at the MCP boundary" paragraph.

## Segment 4 — Failure scenario (2 min)

**Step 9 — demonstrate one realistic failure involving the existing MCP server.** Primary choice: stop the Obsidian plugin mid-flow. Backups: invalid API key; missing note path. Show the agent reporting *which* connection failed and why, retrying with backoff, re-establishing the session on recovery — or continuing on the degraded katagiri-only path and saying so. If asked, show the kill-and-resume from the `SqliteSaver` checkpoint.

Then undo the injection exactly as documented in `docs/assignment/defence-script.md`.

## Segment 5 — Questions and one small variation (3–4 min)

- **Changed valid input**: rerun with goal-note variant B; the katagiri call argument and the output both change.
- **Invalid input**: a malformed/missing frontmatter field → explicit reported condition, never a silent default.
- **Trace a value**: note line → existing-server result field → katagiri argument → final output, read off the provenance record.
- **Name a side effect**: the `log_lesson` / `log_observations` writes into the fixture DB's append-only event log.

## Expected outcomes (005-verify)

1. Both connections initialize, are discovered, and are successfully called; the custom server was started independently, in its own process. *(SC-001)*
2. One goal-note frontmatter value is traced note → existing-server result → katagiri argument → output; variants A and B produce different outputs. *(SC-002)*
3. ≥3 substantive custom tools shown exposed, ≥2 beyond retrieval, `lookup` named as the primary data source. *(SC-003)*
4. All three failure injections give distinct readable reports; one kill-and-resume completes on Windows; failure is distinguishable from a successful empty result. *(SC-004)*
5. Contract docs cover all 33 tools; the drift check is green and fails on an induced registry edit. *(SC-005)*
6. A non-author reaches a successful start from the README alone. *(SC-006)*
7. All 9 steps fit 10–15 minutes; per-segment timings recorded against the budget. *(SC-007)*
8. `detect-secrets` green; no personal DB / vault / token reachable under the demo profile. *(SC-008)*
9. `git diff --stat master -- src/katagiri/` lists exactly `config.py`; `tool_registry.py` untouched. *(SC-009)*

## Automated portion

```powershell
uv run pytest tests/test_config_override.py tests/test_demo_isolation.py tests/test_contract_docs.py -ra
```

```powershell
uv run --project agent pytest -ra
```

```powershell
uv run pytest --public-build
```

## Rehearsal rules

- Max two rerun cycles per rehearsal task (TG-E T028–T030), matching this project's cold-gate convention.
- Every divergence between this runbook and reality is fixed **here**, not remembered.
- Cut list is binding: no VOICEVOX, no Irodori content, no worksheets, no drill modes in the recording. If a segment overruns, cut in the order given in `docs/assignment/defence-script.md`.
