# Tasks: 008 — Browser Companion Check

**Input**: Design documents from `specs/008-browser-companion-check/` (spec.md, plan.md,
research.md, quickstart.md). No `data-model.md`, no `contracts/`, no `holdout/` — plan.md
§Deliberate omissions records why each is absent.

**Tests**: yes — the feature's boundaries (read-only profile access, no install, unchanged
exit codes, unchanged HTTP allowlists) are only credible as assertions, so test tasks are
first-class here.

## Binding rules (carry into every dispatch)

1. **Never install anything.** No download, no unpack, no side-load, no `--load-extension`,
   no browser policy/registry write. Detect and print a URL; that is the entire flow.
2. **Never write inside a browser profile.** Reads only, and reads that tolerate a running
   browser (locked file → report unreadable, never wait, never retry in a loop).
3. **No new HTTP client.** The mokuro probe is a bounded `socket.create_connection` only.
   If a task believes it needs `http.client`/`urllib.request`/`requests`/`httpx`, it
   **stops and escalates to the orchestrator** — an `HTTP_CLIENT_ALLOWLIST` edit needs a
   user decision and its own ledger row (D-47 precedent), never a unilateral change.
4. **Do not touch 009's territory**: `asbplayer_launch.py`, `media_asbplayer.py`,
   `config.asbplayer_bridge_dir`. Read them if useful; edit nothing.
5. **No MCP contract growth.** No ToolSpec added or changed; the registered-tool count is
   the same after 008 as before.

## Format: `[ID] [P?] [Story] Description` + Lane / Model / Write / Read lists

- **[P]**: parallelizable (own worktree lane, no shared files)
- Lanes branch from **`main`** at taskgroup start (this repo's default branch;
  specs/README.md's `master` wording is stale — see plan.md §Branch note).
- `src/katagiri/installer.py` is the feature's **hot file**: serial-on-main, one task at a
  time, never inside a worktree.
- **Model**: sonnet-high default; opus-mid where marked.

## Workfile & conflict map

| File | Owner | Mode |
|---|---|---|
| docs/decisions-ledger.md | T001 | serial-on-main |
| docs/audit-log.md | T001 | serial-on-main |
| src/katagiri/companions.py | T002, then T003 | lane `wt/008-companions` (sequential within lane) |
| tests/test_companions.py | T004 | lane `wt/008-companions` |
| src/katagiri/installer.py | T005, then T006 | **serial-on-main (HOT, one task at a time)** |
| tests/test_installer_companions.py | T007 | serial-on-main (follows the installer edits) |
| docs/browser-companions.md | T008 | lane `wt/008-docs` |
| tests/test_bverify.py, tests/test_cverify.py | — | **FROZEN** (gate reads only; any needed edit escalates) |
| src/katagiri/asbplayer_launch.py, media_asbplayer.py, media_mokuro.py | — | **FROZEN** (read-only reference; 009's territory) |

Note: T005/T006 edit `installer.py` on `main` while the `wt/008-companions` lane owns
`companions.py` — file-disjoint. T005 imports `companions` lazily inside the probe, so the
serial track can land before the lane merges only if the import is guarded; the dependency
order below avoids that entirely by putting T005 after the lane's merge.

---

## Taskgroup TG1: Governance (serial, blocks everything)

- [x] T001 [Gate] (landed as **D-49**; commit 3136028) File the 008 decisions-ledger row in `docs/decisions-ledger.md`
      (**expected D-49 — CONFIRM the actual next number at execution time**:
      `grep '^| D-' docs/decisions-ledger.md | tail -3`; this draft was written when D-48
      was last, and 007's T001 drafted D-39 but landed as D-46, so a stale number is the
      norm). The row records, as binding rather than incidental: (a) **zero MCP contract
      growth** — 008 registers no ToolSpec and changes none, the strongest form of D-24
      additive-only; (b) **no silent install, ever** — Chrome permits programmatic
      installation only via enterprise policy (`ExtensionInstallForcelist`, a machine
      security-policy write) or unpacked side-loading, both rejected on principle, so the
      detect → store-URL handoff → re-check shape is a platform fact recorded as a rule,
      not a temporary limitation; (c) **browser profiles are read-only to Katagiri** — no
      write, rename, or delete under any browser data directory, and no extension file
      manipulation; (d) **absent companion ⇒ `MANUAL STEP`, never `MISSING`**, so
      `doctor_exit_code()` (installer.py:795) is unchanged — same argument `probe_irodori`
      already carries in-line, and what keeps SC-004 true; (e) **the mokuro row is
      configuration readiness, not liveness** — the bridge is Katagiri's own server, not
      running at install time, and the driving userscript is not shipped in this repo
      (research.md R3); (f) **the loopback probe is a bare `socket.create_connection`, so
      no `HTTP_CLIENT_ALLOWLIST` change is required** — explicitly contrasted with D-47,
      which needed one; if implementation ever forces an HTTP client here, that is a new
      user decision, not a silent allowlist edit; (g) **the 008/009 boundary** — 008
      detects only the asbplayer *browser extension*, adds no bridge row, and leaves
      `009-asbplayer-bridge-in-process` free to choose its hosting. Add the reasoning entry
      to `docs/audit-log.md` ("008 TG1 — browser companion detection boundaries"). No
      constitution bump (nothing in I–VII changes; record that judgment in the audit-log
      entry).
      **Model**: sonnet-high. **Write**: docs/decisions-ledger.md, docs/audit-log.md.
      **Read**: docs/decisions-ledger.md (last ~3 rows, for format + the real next
      D-number), specs/008-browser-companion-check/research.md (R3, R4, R6, R7),
      specs/008-browser-companion-check/plan.md (§Constitution Check, §Key design
      decisions).

**Checkpoint**: the 008 ledger row exists → TG2 may start.

---

## Taskgroup TG2: Implementation (2 lanes + serial track)

### Lane `wt/008-companions` [P] (strict order T002 → T003 → T004)

- [x] T002 [P] [US1/US3] [merged: 2f44dc7, lane commit 5903f2c] Create `src/katagiri/companions.py` — the detection core. Stdlib
      only plus `from katagiri.config import MOKURO_BRIDGE_PORT` (installer.py's
      no-package-imports rule applies here too, since this module is imported from it).
      Public shape: a `CompanionStatus` dataclass (name, verdict, evidence/detail,
      optional flag) with verdict ∈ {present, absent, undetermined} — **three outcomes,
      never a boolean** (spec US3); a browser-root enumerator covering Chrome (+ Beta/Dev/
      Canary), Edge, Brave, Vivaldi, Opera under `%LOCALAPPDATA%`/`%APPDATA%`; per-root
      profile enumeration via `Local State` → `profile.info_cache` with a `Default` /
      `Profile *` directory fallback; and per-profile extension presence from
      `<profile>\Extensions\<id>\` (primary, an `is_dir()` — lock-free), enriched but never
      overruled by `<profile>\Preferences` → `extensions.settings.<id>` for the
      enabled/disabled detail (research.md R1, open items O-1/O-2: a `Preferences` read
      that succeeds and shows no entry for an id whose directory exists ⇒
      present-with-caveat, not a confident present). Every read is wrapped: unreadable /
      locked / malformed ⇒ that source is reported unreadable and the scan continues; no
      supported browser root found ⇒ `undetermined` with the searched roots listed, NEVER
      `absent`. Hard time budget on the whole scan (≈2 s, per-source sub-budget), exceeded
      ⇒ `undetermined` (FR-011). Firefox: best-effort via
      `%APPDATA%\Mozilla\Firefox\profiles.ini` → `<profile>\extensions.json`; **verify the
      shape against a real profile if one exists on the machine, and if you cannot,
      degrade the Firefox side to an explicit "not covered" rather than asserting absent**
      (research.md O-3). Absolutely no writes anywhere under a browser directory.
      **Model**: opus-mid (three-outcome discipline + degradation paths are the whole
      point of the module).
      **Write**: src/katagiri/companions.py.
      **Read**: specs/008-browser-companion-check/research.md (R1, R2, and open items
      O-1..O-4), specs/008-browser-companion-check/spec.md (US1, US3, Edge Cases,
      FR-001..FR-004, FR-011), src/katagiri/installer.py:1-35 (module rules) + 112-127
      (`StepResult`/`ComponentStatus` shapes) + 617-648 (`_detect_anki_data_dir`, the
      in-repo precedent for best-effort third-party profile detection),
      src/katagiri/config.py:36-60 (`MOKURO_BRIDGE_PORT`, `_SECRET_KEYS`).

- [x] T003 [P] [US2] [merged: 2f44dc7, lane commit c5682b2] Extend `src/katagiri/companions.py` with the catalog, the handoff
      text, and the mokuro row. (a) One module-level catalog constant holding, per
      companion: display name, store id(s), official install URL, and the numbered manual
      steps — **each entry carrying a comment citing research.md R5 as its provenance**, so
      re-verification is a one-file edit. Values (verified 2026-08-24): Yomitan Chrome
      `likgccmbimhjbgkjambclfkhldnlhbnn` →
      `https://chromewebstore.google.com/detail/yomitan/likgccmbimhjbgkjambclfkhldnlhbnn`;
      Yomitan Firefox → `https://addons.mozilla.org/firefox/addon/yomitan/`; asbplayer
      Chrome `hkledmpjpaehamkiehglnbelcpdflcab` →
      `https://chromewebstore.google.com/detail/asbplayer-language-learni/hkledmpjpaehamkiehglnbelcpdflcab`.
      (b) A handoff renderer producing, for an absent companion, the URL plus its numbered
      manual steps — plain ASCII, no emoji (installer.py's stated rule). (c) The mokuro
      companion: **configuration readiness, not liveness** — report `mokuro_shared_secret`
      as set/unset (presence only, NEVER the value — FR-014, matching the installer's
      "(set)/(unset)" idiom) and the pinned loopback port 8767 as free / occupied via
      `socket.create_connection(("127.0.0.1", MOKURO_BRIDGE_PORT), timeout≈0.2)` wrapped in
      `except OSError`. **Socket connect only — no `http.client`, no `urllib.request`, no
      `requests`/`httpx`** (binding rule 3: needing one means stopping and escalating).
      Word the row so a free port reads as *expected* outside a live session, and so it
      never implies the learner failed to install the userscript. The mokuro handoff
      describes the userscript-manager steps; it links no file, because this repo ships no
      `.user.js` (research.md O-5) — say that plainly rather than implying one exists.
      Depends: T002 (same file, same lane).
      **Model**: sonnet-high. **Write**: src/katagiri/companions.py.
      **Read**: specs/008-browser-companion-check/research.md (R3, R4, R5, O-5),
      specs/008-browser-companion-check/spec.md (US2, FR-005, FR-006, FR-010, FR-013,
      FR-014), src/katagiri/media_mokuro.py:1-130 (bridge semantics, secret header,
      fail-closed behavior — READ ONLY, never edit),
      src/katagiri/asbplayer_launch.py:30-60 (`bridge_port_is_occupied`, the exact
      socket-probe shape to copy — READ ONLY, never edit), src/katagiri/config.py:36-60 +
      140-155 (`MOKURO_BRIDGE_PORT`, `mokuro_shared_secret` doc text).

- [x] T004 [P] [US1/US2/US3] [merged: 2f44dc7, lane commit e1b780c; 15 passed] `tests/test_companions.py` (general group): synthetic browser
      profile trees built entirely in `tmp_path` — **never a real profile, never the real
      `%LOCALAPPDATA%`** (monkeypatch the env/roots). Cover: extension present in the only
      profile ⇒ present, evidence names the profile; present in profile 2 of 2 ⇒ present,
      names profile 2 (US3 acceptance 1); absent from all profiles ⇒ absent, evidence lists
      the searched locations; no browser root at all ⇒ **undetermined**, not absent (US3
      acceptance 2); unreadable/corrupt `Preferences` ⇒ that profile reported unreadable and
      the rest of the scan completes (US3 acceptance 3); directory present but `Preferences`
      shows no entry ⇒ present-with-caveat (open item O-2); time-budget exceeded ⇒
      undetermined. mokuro: secret set vs unset both reported presence-only and the secret
      VALUE never appears in any output string (plant a canary secret and assert it is
      absent); port free vs occupied (bind an ephemeral port and probe *that*, injected —
      **never bind 8767**, per tests/test_media_mokuro.py's own discipline). Two named
      boundary tests the gate greps for (quickstart §3): a **`readonly`** test hashing the
      whole synthetic tree before and after a full scan and asserting equality (SC-002),
      and a **`no_install`** test source-scanning `companions.py` for
      `urlopen`/`urlretrieve`/`http.client`/`urllib.request`/`requests`/`httpx`/`winreg`/
      `load-extension`/`.crx` constructs and asserting none (SC-003). Depends: T003 (same
      lane).
      **Model**: sonnet-high. **Write**: tests/test_companions.py.
      **Read**: src/katagiri/companions.py (post-T003),
      specs/008-browser-companion-check/spec.md (all acceptance scenarios + Edge Cases),
      specs/008-browser-companion-check/quickstart.md (§1, §3 — the `-k` names the gate
      greps must exist), tests/conftest.py:3-84 (test groups),
      tests/test_media_mokuro.py:1-60 (the never-bind-the-pinned-port discipline).

### Serial-on-main track (HOT: `installer.py`, strict order T005 → T006 → T007)

- [x] T005 [US1] (commit 01bc647; --check exit code byte-identical pre/post) Wire detection into the doctor in `src/katagiri/installer.py`: add
      `probe_companions(cfg)` returning the three `ComponentStatus` rows (one per
      companion) and append them to `collect_doctor_statuses()` (installer.py:769-783).
      Import `katagiri.companions` **lazily, inside the probe** — installer.py's top-level
      rule is `config` only, and `probe_anki`/`_anki_manual_step_detail` (installer.py:640)
      is the in-file precedent. Status mapping is load-bearing: present ⇒ `READY`; absent or
      undetermined ⇒ **`MANUAL STEP`** — never `MISSING`, because `doctor_exit_code()`
      (installer.py:795) returns 1 on any `MISSING` and SC-004 requires `--check` exit codes
      to be byte-identical to pre-008 for every companion state. Detail text carries the
      evidence (profile path / reason / port state), truncated to fit the doctor table
      (`render_doctor_table`, installer.py:785). Any exception out of the detector is caught
      here and becomes an `undetermined` row — the doctor never crashes on a browser it did
      not expect. `--check` stays read-only and prompt-free (FR-009). Depends: TG1; lane
      `wt/008-companions` merged (T002–T004).
      **Model**: sonnet-high. **Write**: src/katagiri/installer.py.
      **Read**: src/katagiri/installer.py:596-800 (probe region, `collect_doctor_statuses`,
      `render_doctor_table`, `doctor_exit_code`) + 617-648 (lazy-import precedent) +
      1373-1387 (`_print_doctor_summary`) + 1662-1706 (`main`, `--check` path),
      src/katagiri/companions.py (post-T003), specs/008-browser-companion-check/spec.md
      (FR-001, FR-003, FR-004, FR-009), specs/008-browser-companion-check/research.md (R6).

- [x] T006 [US2] (commit d279371; STEP_LABELS 10→11, TOTAL_STEPS 11→12) Add the wizard step and the re-check loop in `src/katagiri/installer.py`:
      **append** one label to `STEP_LABELS` (currently 10 entries — appending keeps
      `STEP_LABELS[7]`, indexed by tests/test_installer_setup.py:166, stable) and bump
      `TOTAL_STEPS` 11 → 12; add `step_companions(cfg, *, assume_yes, prompt=input)`
      returning `StepResult` and register it in `_wizard_step_runners`
      (installer.py:1294-1320). Behavior: print each companion's verdict; for each absent
      one print its handoff (URL + numbered manual steps); then, **interactively only**,
      offer re-check / skip in the existing prompt idiom — re-check re-runs *only* the
      detection (US2 acceptance 2), skip returns `SKIP` so the outcome shows in the summary
      (US2 acceptance 3). Under `--yes`: report + handoff printed once, **no prompt, no
      wait**, result never `ACTION NEEDED` (FR-008 — an absent optional browser extension
      must not make an unattended install look failed). Add a post-wizard menu entry that
      re-runs the companion check alone (installer.py:1426). Log the step outcome through
      `_log` like every other step (T016/007 established that the log must reconstruct the
      end state). ASCII only, no emoji. Depends: T005 (same hot file, strictly after).
      **Model**: sonnet-high. **Write**: src/katagiri/installer.py.
      **Read**: src/katagiri/installer.py:76-110 (`WIZARD_PREAMBLE`, `STEP_LABELS`,
      `TOTAL_STEPS`) + 1149-1215 (`step_schtasks`/`step_backup`, the optional-step idiom) +
      1261-1360 (`_print_step`, `_wizard_step_runners`, `_run_step_with_retry`) + 1426-1540
      (`_post_wizard_menu`, `_run_wizard_steps`), src/katagiri/companions.py (post-T003),
      specs/008-browser-companion-check/spec.md (US2, FR-005..FR-008).

- [x] T007 [US1/US2] (commit 6b98676; 12 passed + 1 strict xfail; gate grep `-k "readonly or no_install or exit_code"` → 5 non-empty. Bug found by this suite: `installer.RawConfig` lacks `mokuro_shared_secret`, so `mokuro_companion_status` always sees None — fixed serial-on-main immediately after T007 in commit 9c76116 — RawConfig gained `mokuro_shared_secret`, xfail flipped to passing, 44 passed) `tests/test_installer_companions.py` (general group): subprocess runs
      of `python -m katagiri.installer` with `LOCALAPPDATA` + `KATAGIRI_CONFIG` sandboxed
      and `PYTHONUTF8=1`, exactly the 007 harness style, plus in-process unit calls where a
      subprocess would be wasteful. Cover: `--check` output contains the three companion
      rows with their evidence; **`exit_code`** — a named test (the gate greps for it,
      quickstart §3) asserting `doctor_exit_code()` is identical with all companions absent
      and with all present, and that a sandboxed `--check` with no browsers exits with the
      same code as pre-008 (SC-004); `--yes` runs the new step with stdin closed, prompts
      for nothing, completes, and shows the step in the summary (FR-008); scripted-stdin
      interactive path exercises re-check (a companion appears in the synthetic tree between
      the two checks and the row flips) and skip (summary shows skipped); the handoff URL
      for each absent companion appears verbatim in the output; the mokuro shared secret
      planted in the sandbox config never appears in stdout, stderr, or the sandbox log.
      Never touch a real browser profile or the real `%LOCALAPPDATA%`. Timeouts on every
      subprocess (60 s). Depends: T006.
      **Model**: sonnet-high. **Write**: tests/test_installer_companions.py.
      **Read**: src/katagiri/installer.py (post-T006),
      tests/test_installer_setup.py:1-180 (sandbox harness, step-label iteration idiom),
      tests/conftest.py:3-84, specs/008-browser-companion-check/spec.md (US1/US2 acceptance
      + SC-003/SC-004), specs/008-browser-companion-check/quickstart.md (§2, §3).

### Lane `wt/008-docs` [P]

- [x] T008 [P] [US1/US2/US3] [merged: 80215cb] `docs/browser-companions.md` (FR-015, operator-facing, ~1
      page): what each of the three doctor rows means and how to read its evidence; why
      "could not determine" is a distinct, honest verdict and what to do about it; per
      companion, what the operator does (Yomitan and asbplayer: open the store URL, install,
      re-run `--check`; mokuro: set the shared secret, install a userscript manager, add the
      page-change script pointed at the pinned loopback port) — **rendering URLs from
      research.md R5, not invented**; **why Katagiri cannot install these for you** (Chrome
      permits programmatic install only via enterprise policy or unpacked side-loading, both
      out of bounds — this is the platform's rule, not a missing feature); and the known
      blind spots, quoted from research.md's open items rather than glossed
      (unpacked/dev-loaded extensions, portable browser installs, Firefox coverage limits,
      no userscript file shipped in this repo). One line in README.md only if a docs index
      section already exists there. Do NOT describe the asbplayer bridge or its hosting —
      that is 009's subject (research.md R4).
      **Model**: sonnet-high. **Write**: docs/browser-companions.md, README.md (one line,
      only if an index section exists).
      **Read**: specs/008-browser-companion-check/research.md (R3, R4, R5, and all open
      items), specs/008-browser-companion-check/spec.md (US1–US3, FR-015),
      specs/008-browser-companion-check/quickstart.md, docs/setup-observability.md (the
      house style for a 007-era operator doc), README.md.

**Checkpoint**: T001–T008 merged to `main` + full suite green → TG3.

---

## Taskgroup TG3: Gate (serial-on-main, dedicated testing agent)

- [x] T009 [Gate] (gate cleared 2026-08-24, zero fix cycles: §1 15 passed; §2 13 passed; §3
      `-k "readonly or no_install or exit_code"` selected 5, all passed — non-empty; §4 real-machine
      read-only `--check` exit 0, observed verdicts: Yomitan READY (Chrome/Default, enabled state
      unknown), asbplayer MANUAL STEP (not found in Chrome/Edge profiles; Firefox variant not covered),
      mokuro MANUAL STEP (secret unset, port 8767 free = expected outside a session) — §4's optional
      "install one absent companion then re-check" sub-step NOT performed (requires a real extension
      install, outside read-only gate scope; the re-check flip is covered by T007's scripted-stdin
      tests instead); §5 diff vs main on test_bverify/test_cverify empty, no allowlist change, 25
      passed/3 env skips; §6 no companion ToolSpec, tool count 35 unchanged, 189 passed; §7 full
      suite 2139 passed/10 expected env skips in 93.44s) Run `specs/008-browser-companion-check/quickstart.md` §1–§7 in order and
      record the outcome here. The load-bearing steps, called out because a green suite
      alone does not prove them: (a) §3's `-k "readonly or no_install or exit_code"`
      selection must be **non-empty** — zero selected tests means the boundary is untested
      and the gate FAILS; (b) §5 — `git diff main -- tests/test_bverify.py
      tests/test_cverify.py` must be **empty**: no `HTTP_CLIENT_ALLOWLIST` or
      `HTTP_SERVER_ALLOWLIST` change (if an edit was needed, STOP and escalate to the user
      for a D-47-style decision + ledger row; do not accept the edit); (c) §6 — the
      registered-tool count is unchanged from pre-008; (d) §4's manual observation performed
      once on the real machine, read-only, with the observed verdicts recorded in this task's
      completion note. Failures → fix via new serial tasks filed by the orchestrator, then
      rerun; max two fail→fix→rerun cycles, then escalate to the user (constitution V
      discipline). The gate agent modifies no non-test source file.
      **Model**: sonnet-high (testing agent).
      **Read**: specs/008-browser-companion-check/quickstart.md,
      specs/008-browser-companion-check/plan.md (§Constitution Check, design decision 5),
      specs/008-browser-companion-check/spec.md (Success Criteria).

**Checkpoint**: feature complete. Push to remote after TG3 (orchestrator).

---

## Dependencies & execution order

- TG1 (T001) → blocks all of TG2.
- Lane `wt/008-companions`: T002 → T003 → T004, all in one lane (one file + its tests).
- Lane `wt/008-docs`: T008, parallel to everything else (no shared files).
- Serial-on-main: T005 → T006 → T007, **after `wt/008-companions` merges** (T005 imports
  `companions`). This ordering is why the hot file is only ever written by one task at a
  time and never inside a worktree.
- TG3 (T009) starts only when T001–T008 are merged and checked.
- Full suite runs at taskgroup boundaries only (TG2 close, TG3 §7), not per task.

## Notes

- specs/README.md's execution model applies, with one correction: lanes branch from `main`,
  not `master` (plan.md §Branch note).
- Worktree bootstrap quirks (from specs/README.md): a fresh worktree has no `.venv` — run
  tests via the primary checkout's `.venv` by absolute path; `core.hooksPath` beads noise is
  harmless.
- Total: 9 tasks (1 governance, 7 implementation, 1 gate) across 2 lanes + 1 serial track.
  Deliberately small — 008 is a doctor UX feature, and plan.md records why it carries no
  held-out suite, no data model, and no contracts directory.
