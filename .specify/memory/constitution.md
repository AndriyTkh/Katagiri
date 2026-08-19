<!--
Sync Impact Report
- Version change: (template, unversioned) → 1.0.0
- Initial ratification: derived from docs/dev-plan.md v1.1 "Standing constraints",
  docs/decisions-ledger.md binding decisions D-01…D-29, and the Round-5 verification
  protocol. No prior constitution existed.
- Added sections: all (Core Principles I–VII, Technology Constraints,
  Development Workflow, Governance).
- Removed sections: none.
- Templates status: spec/plan/tasks templates are stock speckit 0.16.4; plan-template's
  Constitution Check gate now resolves against this document. ✅
- Follow-up TODOs: none.
-->

# Katagiri Constitution

## Core Principles

### I. Personal Tool, MCP Ceiling (NON-NEGOTIABLE)

Katagiri is a personal English↔Japanese study tool for one user. The MCP server is the
build ceiling: no app, no GUI, no public service, no multi-user features, and no own
media player (players = mpv + asbplayer + mokuro reached through MCP context channels).
Any proposal that raises the ceiling is rejected, not deferred. (Ledger D-01, D-02, D-13.)

### II. OSS-First

Existing OSS is integrated, not reimplemented. The genuinely-build list is closed: MCP
server, progressive substitution engine (deferred), Yomitan known-dict generator. Anki
owns scheduling — FSRS is never reimplemented as a live scheduler; `answerCards` is
banned; permitted Anki writes are `addTags`/`setDueDate` only, with `exportPackage`
verified before any batch and hard-fail-closed if unavailable. (D-03, D-04, D-05.)

### III. Event Log Is Sacred

Vault is prose; DB is state; the event log is the single non-reconstructible asset.
Every mutation flows through the event log. Append-only is enforced in-schema
(`BEFORE UPDATE`/`BEFORE DELETE` triggers with `RAISE(ABORT)`). Tables are classified
source-of-truth (event log, manual marks, lessons) vs derived (FTS, JMdict, Anki
mirror — drop-and-rebuild, never migrated). Scheduled `VACUUM INTO` backups plus a
rehearsed restore drill are mandatory infrastructure, not optional hygiene. (D-12, D-27.)

### IV. Study-First, Gated Progression

~20–30 min of study precedes any build session; no building on a zero-review day.
Phase entry (B through E) requires ≥4 logged study days in the prior week. Phase E code
is blocked by the D6 stop-gate: 14 study days within an 18-day window plus one canary
probe battery, evaluated mechanically by `stop_gate_status` (PASS/FAIL + failing
criterion), never by self-assessment. If unmet twice → explicit re-plan. Sole exemption:
the write-only mpv seek logger. (D-16, D-18, D-19.)

### V. Two-Gate Verification per Phase

Every phase closes with both gates green:

1. **Cold-subagent pass** — a fresh agent, no context beyond tool descriptions, runs
   scripted scenarios against frozen fixtures (never live personal data). Assertions on
   tool-call sequence and structured fields; cumulative (phase N runs scenarios A..N);
   max two fail→fix→rerun cycles, residual findings → backlog. Budget +20–25% of build
   hours, logged separately.
2. **Learner metric** — one per phase, read from the event log; a phase can fail this
   with a green subagent pass. Defaults: reviews/day not declining; ≥4 study days/week;
   for C/D/E, ≥5 of last 7 days show events from that phase's tools. (D-15, D-23.)

### VI. Security Hardening by Default

stdio-only MCP transport, no network listener. Third-party localhost ports verified
bound to 127.0.0.1 with firewall inbound deny. Obsidian is proxied: Katagiri holds the
REST token and exposes GET-shaped tools only; the plugin's own MCP endpoint is never
registered with the agent. Live `collection.anki2` is never opened — snapshot copy →
`mode=ro&immutable=1` → `integrity_check`. All media-derived text (subtitles, OCR,
lyrics) arrives in an untrusted-data envelope ("data, never instructions"); write tools
require echo-back confirmation on such content. Secrets live in `%LOCALAPPDATA%`, never
in repo, vault, tool outputs, errors, or the event log. Writes are confined to declared
roots with server-generated filenames. (D-20, D-21, D-22.)

### VII. Tool-Contract Stability

The tool registry (name, args, output shape, stability tier) is checked in. After the
A6 freeze, contract changes are additive only. Unimplemented tools raise — they never
return plausible stubs. `search_db` is the definitive search; no later "proper" rewrite.
(D-24.)

## Technology Constraints

- Python 3.12 (pinned `>=3.12,<3.13`), uv-managed; `mcp>=2,<3`, plain functions + thin
  adapter (D-08). Windows 11 host; stderr-only logging (stdout corrupts MCP stdio);
  `PYTHONUTF8=1` required.
- SQLite single-file DB; whole schema shipped in one migration; minimal runner
  (`PRAGMA user_version`, numbered scripts, backup-before-migrate).
- FTS5 dual index: fugashi shadow column (unicode61) + trigram, routed by query length;
  indexed rows carry dict/tokenizer version (D-09).
- Vendored, checksummed data: full UniDic + kanjium accents; no runtime downloads (D-10).
- Known threshold: Anki `ivl ≥ 21d`; py-fsrs formula-only, pinned `fsrs<7` (D-07).
- i+1 selection is gated on curriculum grammar-DAG reachability AND known-word coverage,
  never vocabulary coverage alone (D-28).
- Rejected for cause (do not reintroduce): JParaCrawl, Tadoku readers in pipeline,
  AnkiConnect `answerCards`, MarkusPfundstein/mcp-obsidian, kuromoji.js, in-Katagiri
  scheduler, own media player, agent interface inside a player.

## Development Workflow

- Task tracking source of truth: **beads (`bd`)** until an explicit user instruction
  switches to spec-kit. `specs/*/tasks.md` mirrors beads (each task cites its bead ID);
  on conflict, beads wins. Verify beads (`kata-*vf`) and D6 are blocking.
- Estimates bottom-up per task before implementation; tasks >8h split with own
  definition of done; actuals logged; re-baseline after each phase close; 1.5× slip
  rule cuts all "could" items (D-29).
- Reviews are incremental: consult the coverage table in docs/decisions-ledger.md; never
  re-review settled scope. New decisions land as D-xx rows in the ledger; detailed
  reasoning in docs/audit-log.md.
- Timeline counts build-only hours; study time is never billable to the build.

## Governance

This constitution distills the binding decisions in docs/decisions-ledger.md; the ledger
plus docs/audit-log.md remain the authoritative record of reasoning. On conflict, a newer
ledger decision wins and MUST be folded back here with a version bump.

Amendments: add/update the ledger row first, then amend this file. Versioning is
semantic — MAJOR for principle removals/redefinitions, MINOR for new principles or
materially expanded guidance, PATCH for clarifications. Every plan's Constitution Check
gates against the current version; violations require a Complexity Tracking entry or a
scope cut.

**Version**: 1.0.0 | **Ratified**: 2026-08-19 | **Last Amended**: 2026-08-19
