# Research: Phase C — Prose Search

Settled decisions (do not re-research; see decisions-ledger.md coverage table):

- **Decision**: Katagiri keeps its **own** markdown search, independent of Obsidian running.
  **Rationale**: prose recall must not depend on a desktop app being open; Obsidian's search is unreachable headless.
  **Alternatives considered**: Obsidian search via REST (rejected — availability coupling); indexing through the plugin MCP endpoint (rejected — D-20 forbids exposing it).
  **Source**: ledger D-11; dev-plan C2.

- **Decision**: C1 (DB search) folded into A6; `search_db` is definitive — no "proper" rewrite in this phase.
  **Rationale**: tool-contract stability (Round 5).
  **Source**: ledger D-24.

- **Decision**: Index is derived tier — drop-and-rebuild script, version-stamped rows; FTS5 dual-index conventions (fugashi shadow column + trigram, query-length routing) reused from A3.
  **Source**: ledger D-09, D-27.

- **Sizing**: 8–15h developer estimate (Round 5), to be re-baselined bottom-up before build (D-29).

No NEEDS CLARIFICATION items remain.
