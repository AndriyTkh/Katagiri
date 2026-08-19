# Research: Phase B — Obsidian Render

All decisions for this phase are already settled and argued in the project's audit trail.
Per the review-coverage rule (decisions-ledger.md), settled scope is not re-researched.

- **Decision**: Obsidian access via obsidian-local-rest-api v5.1+ on :27123, proxied by Katagiri; GET-only tools; plugin MCP endpoint never registered with the agent.
  **Rationale**: the plugin endpoint exposes PUT/PATCH/DELETE + `command_execute` behind the same token — unacceptable agent write/exec surface for a vault of prose.
  **Alternatives considered**: MarkusPfundstein/mcp-obsidian (rejected — stale, pre-5.x PATCH format); registering the plugin MCP endpoint directly (rejected — write surface).
  **Source**: ledger D-11 amended by D-20; audit-log Round 5 cluster 9.

- **Decision**: Exporter is a section registry; Phase-B `Today.md` built strictly from existing Phase-A data; writes confined to `.derived/` with generated-file header + overwrite refusal.
  **Rationale**: later phases extend sections instead of rewriting the exporter; header guard protects prose (the vault is prose, DB is state).
  **Alternatives considered**: monolithic per-phase dashboards (rejected — rewrite churn each phase).
  **Source**: dev-plan v1.1 B1; Round 5.

- **Decision**: Verification = cumulative cold-subagent fixture pass incl. a scripted direct-HTTP bypass attempt that must be refused, plus learner metric (Today.md opened ≥5 of last 7 days).
  **Source**: ledger D-15/D-23; dev-plan Verification protocol.

No NEEDS CLARIFICATION items remain.
