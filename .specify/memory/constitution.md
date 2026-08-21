<!--
Sync Impact Report
- Version change: 1.2.0 → 1.3.0
- Amendment: Principle IV (Study-First, Gated Progression) — the 006 entry gate addendum
  records a user waiver of its blocking effect. The D-33 criteria (≥10 study days, ≥6
  scored, ≥3 dictation) remain computed mechanically and surfaced as informational
  `stop_gate_status` keys, but no longer block 006 TG2–TG8 implementation, which the user
  authorized to proceed before the first study day (D-35, 2026-08-21). Rationale recorded:
  the gate assumed study concurrent with build; the user's setting inverts that — the
  teaching method must be complete before learning starts. The D6 stop-gate for Phase E
  (D-19: 14-in-18 + probe battery) is untouched and remains fully blocking.
- Rationale for MINOR (not MAJOR): the addendum is not removed or redefined — its criteria,
  mechanics, and instrumentation stand verbatim; what changes is a recorded, scoped waiver
  of one enforcement effect, per the same user-override mechanism already precedented in
  the ledger (D-30). Materially changed guidance on an existing principle, no principle
  removed.
- Ledger row filed first per Governance's amendment procedure: D-35 (usage gates waived
  pre-study — Phase B adoption metric + 006 entry gate blocking effect). Reasoning:
  docs/audit-log.md "Gate waivers — pre-study build-out (2026-08-21)".
- Added sections: none (existing Principle IV addendum amended in place).
- Removed sections: none.
- Templates status: spec/plan/tasks templates are stock speckit 0.16.4; plan-template's
  Constitution Check gate now resolves against this document. ✅
- Follow-up TODOs: none.

---

Sync Impact Report (superseded by the entry above — kept for history)
- Version change: 1.1.0 → 1.2.0
- Amendment: Principle VI (Security Hardening by Default) gains a scoping clause on the
  Obsidian-proxy sentence. "The plugin's own MCP endpoint is never registered with the
  agent" scopes to katagiri's own agent surface; it does not reach a separate, disposable
  agent's direct connection to the plugin's MCP endpoint on a dedicated demo vault (own
  port, own token, synthetic content) — the specs/005-mcp-assignment homework agent's
  carve-out. Katagiri's personal-vault GET-only proxy is untouched; no katagiri contract
  change; no personal REST token reachable from that agent's environment.
- Rationale for MINOR (not PATCH): the scoping names an explicit carve-out surface
  (a separate agent, a separate vault) that the prior wording did not address at all —
  materially expanded guidance on an existing principle, not a restatement of the same
  scope. No principle was removed or redefined, so not MAJOR.
- Ledger row filed first per Governance's amendment procedure: D-34 (005 scoping of
  D-20 — homework agent + dedicated demo vault carve-out; plugin MCP endpoint observed
  in the T005 spike as Streamable HTTP, plugin v5.1.0, https://127.0.0.1:27124).
  Reasoning: docs/audit-log.md "005 T006 — Principle VI scoping for the homework agent
  (2026-08-20)".
- Added sections: none (existing Principle VI amended in place).
- Removed sections: none.
- Templates status: spec/plan/tasks templates are stock speckit 0.16.4; plan-template's
  Constitution Check gate now resolves against this document. ✅
- Follow-up TODOs: none.

---

Sync Impact Report (superseded by the entry above — kept for history)
- Version change: 1.0.0 → 1.1.0
- Amendment: Principle IV (Study-First, Gated Progression) gains the 006 entry gate —
  contract-touching 006-teaching-method taskgroups (US2–US8) additionally require ≥10
  study days, ≥6 with a scored observation, ≥3 with a dictation artifact, evaluated
  mechanically and surfaced as additive `stop_gate_status` output keys. Explicitly
  additive to the existing D-19 mechanics (14-in-18 study days + probe battery), which
  remain necessary and unchanged.
- Rationale for MINOR (not PATCH): a new gate criterion is materially expanded guidance
  on an existing principle, not a mere clarification; no principle was removed or
  redefined, so not MAJOR.
- Ledger rows filed first per Governance's amendment procedure: D-32 (Phase-0 teaching
  rules — KANA mode, coverage unit, dictation slug `phase0-kana-dictation`, staged kana
  gates) and D-33 (006 entry gate, additive to D-19). Reasoning: docs/audit-log.md
  "006 TG0/TG1 — Phase-0 teaching rules and entry-gate governance (2026-08-20)".
- Added sections: none (existing Principle IV amended in place).
- Removed sections: none.
- Templates status: spec/plan/tasks templates are stock speckit 0.16.4; plan-template's
  Constitution Check gate now resolves against this document. ✅
- Follow-up TODOs: none.

---

Sync Impact Report (superseded by the entry above — kept for history)
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

**006 entry gate (additive)**: contract-touching 006-teaching-method taskgroups (US2–US8)
are additionally blocked until the event log shows ≥10 study days, ≥6 with a scored
observation, ≥3 with a dictation artifact — arbitrary or TIRED-only days do not satisfy
it. This gate is layered on top of the D6 mechanics above, not a substitute for them: the
14-in-18 count and the probe battery remain necessary conditions, unchanged and
independently evaluated; the 006 criteria only add requirements. Evaluated mechanically,
surfaced as additive `stop_gate_status` output keys — no new ToolSpec. (D-32, D-33.)
**Blocking effect waived by user decision (D-35, 2026-08-21)**: the criteria above remain
computed and surfaced as informational keys, but do not block 006 TG2–TG8 implementation,
which proceeds pre-study so the teaching method is complete before learning starts. The
D6 stop-gate for Phase E is not covered by this waiver and remains fully blocking.

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

**005 scoping (additive)**: the sentence above — "the plugin's own MCP endpoint is never
registered with the agent" — scopes to **katagiri's** agent surface; that prohibition is
unchanged and remains fully binding there. It does not reach a separate, disposable
agent's direct connection to the plugin's MCP endpoint on a **dedicated demo vault**: own
port, own token, synthetic content, no personal data ever in scope. This carve-out is the
entire concession — katagiri's personal-vault GET-only proxy stays untouched, no katagiri
contract change results, and no personal REST token is ever reachable from that agent's
environment. (D-20, D-34.)

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

**Version**: 1.3.0 | **Ratified**: 2026-08-19 | **Last Amended**: 2026-08-21
