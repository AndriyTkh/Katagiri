# Phase A workfile

Started 2026-08-19. Lanes from `bd ready` within kata-ph-a.

## Wave 1 (parallel, no deps): A0a · A0b · A0c

| Bead | Est | Scope | Key decisions |
|---|---|---|---|
| kata-a0a | 180m | pyproject/uv, py3.12 pin, src/katagiri, config loader (%LOCALAPPDATA%\Katagiri\config.toml), stderr-only logging, detect-secrets pre-commit, vendor policy (binaries gitignored, checksums committed), minimal MCP stdio server + Windows launch (.mcp.json, absolute interpreter, PYTHONUTF8=1) | mcp>=2,<3 (D-08); stubs raise (D-24) |
| kata-a0b | 150m | 200 graded sentences, 5 bands × 40, at docs/katagiri/katagiri/90-meta/canary/canary-set.md, sealed:true; scripts/validate_canary.py (stdlib) screams on any vault leakage | quarterly ~20-sentence samples, trend-line only (audit-log:42); IDs = s-sha1(normalized_jp)[:6] (ARCHITECTURE.md:50) |
| kata-a0c | 90m | skills pack v0 (.claude/skills/katagiri-study + 90-meta pointer): guess-first, coverage gate, mining budget, nuance-anchoring; study-log.jsonl protocol + scripts/log_study.py (manual until A5) | L1 profile conversation already done — 35-phonology/l1-profile.md filled (170 lines) |

## Wave 2 (after A0a): A1, A2 → A3 → …

Estimates for A1+ set when wave 1 closes.

## Close-out (2026-08-19)

Epic kata-ph-a CLOSED. 16/17 children done; kata-ph-a.1 (P3 mcp_server split) left open, non-gating.
Gate kata-avf passed cold: full suite **621 passed / 0 failed**; E2E fixture pipeline (real JMdict zip,
fabricated schema-11 Anki collection); MCP stdio subprocess E2E (8 contract tools, stdout protocol-clean);
restore drill (VACUUM INTO snapshot survives midfile corruption). Known limit pinned: single kanji of a
compound (勉) not sentence-substring-searchable (words index = morph boundary, trigram needs ≥3 chars).
Normalizer accuracy 200/200 (anti-overfit receipt 196/200) — label set agent-produced, **user audit
recommended** (kata-mz2). User-side manual steps → kata-mz2 (schtasks ×3, mpv.conf IPC line, Yomitan
import, ≥7 days real sync).

## Notes
- Git: stealth mode this session — no commits; report status at handoff.
- Vault-in-repo: docs/katagiri/katagiri/ treated as canonical vault (holds real L1 profile).
- Schema-version inconsistency in mockup docs (schema:1 vs 2) — new meta files use schema: 2 (matches settings/topic-file).
- Root doc duplicates (docs/l1-profile.md etc.) — cleanup candidate, not in wave 1 scope.
