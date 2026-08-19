# Implementation Plan: Phase B — Obsidian Render

**Branch**: `001-phase-b-obsidian-render` | **Date**: 2026-08-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-phase-b-obsidian-render/spec.md`

**Beads mirror**: epic `kata-ph-b` (tasks `kata-b1`, `kata-b2`, gate `kata-bvf`). Beads is authoritative for task state.

## Summary

Two deliverables: (1) an aggregate exporter built as a **section registry** that renders `Today.md` into the vault's `.derived/` folder from Phase-A data only (due count, streak, known trend, weakest morphs, resume pointers); (2) a **GET-only Obsidian proxy** — Katagiri holds the obsidian-local-rest-api token and exposes read-shaped vault tools; the plugin's own MCP endpoint is never registered with the agent (D-20). Phase closes on B-verify (cumulative cold-agent pass + bypass-refused) plus the Today.md adoption metric.

## Technical Context

**Language/Version**: Python 3.12 (pinned `>=3.12,<3.13`), uv-managed

**Primary Dependencies**: `mcp>=2,<3` (thin adapter over plain functions), stdlib `urllib`/`http.client` or minimal HTTP client for :27123 (keep deps lean — OSS-first does not mean dependency-heavy)

**Storage**: existing SQLite DB (read-only for this feature); vault filesystem writes confined to `.derived/`

**Testing**: pytest (unit) + cumulative cold-subagent fixture scenarios (A..B) per constitution V

**Target Platform**: Windows 11, stdio-only MCP, stderr-only logging, `PYTHONUTF8=1`

**Project Type**: single project (`src/katagiri`, `tests/`)

**Performance Goals**: exporter run < seconds on personal vault; not a hot path

**Constraints**: REST token read from `%LOCALAPPDATA%` config, never logged/echoed; generated-file header guard on all writes; tool registry additive-only

**Scale/Scope**: one user, one vault, 2 build tasks + 1 gate

## Constitution Check

*GATE: pass before implementation; re-check at phase close.*

| Principle | Status | Note |
|---|---|---|
| I MCP ceiling | PASS | MCP tools + a file exporter; no UI |
| II OSS-first | PASS | obsidian-local-rest-api reused; only the proxy + exporter are built |
| III Event log sacred | PASS | exporter reads DB; regen/refusal actions logged; `.derived/` is derived tier |
| IV Study-first | PASS | entry precondition: A-verify green + ≥4 study days prior week |
| V Two-gate verification | PASS | B-verify bead (`kata-bvf`) is blocking; learner metric = Today.md opened ≥5/7 days |
| VI Security | PASS | token server-side only; GET-only surface; plugin MCP endpoint never registered; bypass-refusal is a scripted verify scenario |
| VII Tool-contract stability | PASS | new vault-read tools added additively to `tool_registry.py` |

No violations → Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-phase-b-obsidian-render/
├── spec.md
├── plan.md              # this file
├── research.md          # pointers to settled decisions (ledger/audit-log)
├── quickstart.md        # B-verify validation guide
├── checklists/requirements.md
└── tasks.md             # mirrors beads kata-b1/b2/bvf
```

Data model: [docs/db-schema.md](../../docs/db-schema.md) is authoritative — no separate data-model.md (no new source-of-truth tables in this phase). Contracts: checked-in [src/katagiri/tool_registry.py](../../src/katagiri/tool_registry.py) is the contract surface; additions listed in tasks.

### Source Code (repository root)

```text
src/katagiri/
├── exporter.py          # NEW — section registry + Today.md renderers + header guard
├── obsidian_proxy.py    # NEW — token loading, GET-only REST calls, structured errors
├── mcp_server.py        # register new read tools (additive)
├── tool_registry.py     # additive entries: vault_read_note, vault_list, today_render (names finalized at build)
└── config.py            # vault path + token location already handled here

tests/
├── test_exporter.py     # NEW — registry, sections, header-guard refusal
├── test_obsidian_proxy.py  # NEW — GET-only surface, token never in output, 401/down errors
└── test_bverify.py      # NEW — cumulative cold-agent scenarios A..B (fixtures)
```

**Structure Decision**: single-project layout continues; two new modules, additive tool registrations, three new test files. Section-registry API is the one designed-for-extension seam (D4 lesson memory and Phase-E resume pointers plug in later).

## Complexity Tracking

None — no constitution violations.
