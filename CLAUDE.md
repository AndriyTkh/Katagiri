# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- **All phases**: task tracking is spec-kit (`specs/README.md` is the authoritative workflow
  doc) — the `specs/NNN-*/tasks.md` checkboxes ARE the task list; do not open competing beads.
  **Beads retired 2026-08-21** (Phase B closed under D-35; retirement checklist in
  specs/README.md executed): the beads DB is a read-only archive — `[was:]` refs point into
  it. A few archived P2/P3 beads (kata-3t7, kata-626, kata-mz2, kata-ph-a.1) may still be
  referenced by ID and closed via `bd close` when their work lands; do not create new beads.
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files (all phases).

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Build & Test

```bash
# Normal run: skips ground-zero 'compile' drills; the real JMdict comes from a
# cached template under tests/.cache (imported once, then file-copied).
uv run pytest

# Faster local loop: parallel workers. ~1.9x on this suite (246s -> 132s).
# --dist loadgroup keeps the two real :27123-binding tests
# (test_abc_workflow.py::test_04a_..., test_bverify.py's vault-read/no-replay
# tests, all tagged @pytest.mark.xdist_group("obsidian-port-27123")) on one
# worker so they don't race each other for the fixed port.
uv run pytest -n auto --dist loadgroup

# Public build / full validation: ground-zero reimports, corruption/restore
# drills, everything. Run before releases.
uv run pytest --public-build
```

Test groups (see `tests/conftest.py`): `compile` (ground-zero rebuilds, first,
public-build only) → general → `mcp` (spawns the real MCP server over stdio, last).

Under `-n`, MCP-subprocess fixture teardown has occasionally left orphaned
`katagiri-mcp.exe` processes behind (seen once, not reproduced since) — if a
plain `uv run pytest` later fails with "failed to remove file ... katagiri-mcp.exe
... used by another process", check `tasklist /FI "IMAGENAME eq katagiri-mcp.exe"`
for stragglers before assuming it's a real bug.

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
