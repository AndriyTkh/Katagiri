# Research: Phase D — Teacher Loop

Settled decisions (coverage table in decisions-ledger.md; do not re-research):

- **Decision**: i+1 gated on curriculum grammar-DAG reachability AND known-word coverage, never vocabulary alone.
  **Rationale**: vocabulary-only i+1 proposes grammar the learner can't parse (Round 5 teacher HIGH finding).
  **Alternatives**: coverage-only gating (rejected).
  **Source**: ledger D-28.

- **Decision**: `log_observations` mandatory fields (`unassisted`, coverage band, `rubric_version`) — the unassisted pass-rate series; `start_session` returns exactly one prescribed action.
  **Rationale**: dashboards diffuse; pass-rate series is the phase's outcome instrument.
  **Source**: dev-plan D3, Round 5 clusters 7/16.

- **Decision**: Lesson memory = `unresolved[]`, `next_step` (write-at-close/read-at-open), `revisit_after` topic spacing; Anki schedules items, Katagiri schedules topics; tired-mode minimum session counts toward the gate.
  **Source**: dev-plan D4; ledger D-04 boundary.

- **Decision**: D6 stop-gate mechanical — 14 study days in 18-day window, concrete event-type counts, declared pauses, probe battery across ≥2 coverage bands, re-plan on two misses; mpv seek logger exempt (already shipped, kata-e6s closed).
  **Source**: ledger D-16/D-19.

- **Decision**: Canary set (A0b) sealed; probes may read, drills never; validator enforces.
  **Source**: ledger D-26.

- **Decision**: Untrusted-data envelope + echo-back before writes on media-derived content — implemented in D3, adversarially verified in E-verify.
  **Source**: ledger D-22.

- **Decision**: Difficulty-for-me = jreadability + BCCWJ + JLPT + coverage %; advanced modeling deferred (F-06).
  **Source**: dev-plan D2; ledger F-06.

Open (deliberately deferred to build time, not clarification-blockers): exact tool-name batch for the registry; curriculum.md authoring format details (learner-owned content task inside D2).
