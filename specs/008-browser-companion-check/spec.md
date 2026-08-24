# Feature Specification: 008 — Browser Companion Check (detect + guide-to-install)

**Feature Branch**: `008-browser-companion-check`

**Created**: 2026-08-24

**Status**: Draft — **tasks.md is the task-tracking source of truth** (spec-kit; no beads history)

**Input**: Installer doctor detection of learner-installed browser companions — Yomitan
extension, asbplayer extension, mokuro userscript reachability — with a detect +
guide-to-install flow. Chrome forbids silent extension installs, so scope is strictly
presence check + Web Store handoff + re-check loop.

## Scope claim (binding)

Additive only, per constitution VII — and in the strongest form available: **this feature
registers no MCP tool and changes no existing tool's contract at all**. Its entire surface
is the installer/doctor CLI plus one new pure-detection module. No schema change, no
migration, no study surface, no phase-entry requirement (constitution IV applies to
phases; this is setup infrastructure, like 005 and 007).

**Hard boundaries (non-negotiable, restated as requirements below):**

1. **No silent installs.** Chrome/Chromium forbids programmatic extension installation
   outside enterprise policy, and Katagiri will not attempt it by any other route
   (no `--load-extension` flag injection, no CRX download, no policy-registry write).
   The flow is: *detect → print the official store URL and the manual steps → let the
   operator do it → re-check*.
2. **No extension file manipulation.** The detector reads browser profile data; it never
   writes, moves, patches, or deletes anything inside a browser profile, and never
   modifies an extension's files or preferences.
3. **`--check` stays read-only.** The doctor mode's existing contract ("report status,
   change nothing, prompt for nothing") holds for the new rows too.
4. **009 is not pre-empted.** 008 answers only "is the asbplayer *extension* present in a
   browser profile". It makes no statement about, and adds no code to, the asbplayer
   WebSocket bridge that `009-asbplayer-bridge-in-process` will replace.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The doctor tells me which browser companions are installed (Priority: P1)

As the operator finishing (or re-checking) a Katagiri install, I run the doctor and the
summary table has rows for the three learner-side browser companions — Yomitan, the
asbplayer extension, and the mokuro page-change bridge — each reported as present,
absent, or "could not tell", with the evidence (which browser and profile it was found
in, or which locations were searched). Today the doctor covers only machine-side
components; the browser half of the study loop is invisible to it, so a half-installed
setup looks READY.

**Why this priority**: everything else in the feature (the handoff text, the re-check
loop) is worthless without a trustworthy answer to "is it there?". This story is the MVP:
detection alone already converts a silent gap into a visible one.

**Independent Test**: point the detector at a synthetic browser-profile tree containing a
known extension id and at an empty one; the doctor rows flip between present and absent
accordingly, and nothing inside either tree is modified (compare a file-tree hash before
and after).

**Acceptance Scenarios**:

1. **Given** a browser profile that contains the Yomitan extension, **When** the doctor
   runs, **Then** the Yomitan row reports it as present and names the browser and profile
   it was found in.
2. **Given** a machine with a supported browser whose profiles contain neither companion
   extension, **When** the doctor runs, **Then** both extension rows report absent and
   name the profile locations that were searched.
3. **Given** the doctor runs in read-only mode, **When** it completes, **Then** no file
   under any browser profile has been created, modified, or deleted.
4. **Given** any companion is absent, **When** the doctor computes its exit code, **Then**
   the absence does **not** by itself make the exit code non-zero — a learner-side browser
   install is an optional, operator-owned step, reported the way scheduled tasks and the
   Irodori schedule already are.

---

### User Story 2 - When something is missing, I am handed the exact way to install it (Priority: P1)

As the operator, when a companion is reported absent I get, right there in the output, the
official install target (the Web Store / add-on listing URL, or for mokuro the userscript
setup steps) plus the two or three manual actions I must take. I do those actions in my
browser, come back, and re-check without re-running the whole install.

**Why this priority**: the whole reason the feature exists is that Katagiri *cannot* do
this step for the operator; the handoff text is the deliverable that replaces the install
Katagiri is forbidden to perform.

**Independent Test**: run the wizard's companion step against a profile tree with nothing
installed; assert the output contains each companion's install URL and its numbered manual
steps, that the process exits without having launched any installer or downloaded any
file, and that a re-check invoked from the same session picks up an extension that appeared
in the profile tree meanwhile.

**Acceptance Scenarios**:

1. **Given** a companion is absent, **When** the wizard's companion step runs
   interactively, **Then** it prints that companion's official install URL and the manual
   steps, and offers to re-check.
2. **Given** the operator installs the extension in the browser and chooses re-check,
   **When** the re-check runs, **Then** the row flips to present without the operator
   re-running any earlier wizard step.
3. **Given** the operator chooses to skip, **When** the wizard continues, **Then** the
   skipped state appears in the step summary and the run still ends successfully.
4. **Given** the installer runs non-interactively (`--yes`), **When** the companion step
   runs, **Then** it reports status and prints the handoff text once, prompts for nothing,
   waits for nothing, and never fails the run.
5. **Given** any run of this feature, **When** its whole output and every file it touched
   are inspected, **Then** no extension was installed, downloaded, unpacked, or modified by
   Katagiri.

---

### User Story 3 - Absence and ignorance are told apart (Priority: P2)

As the operator on a machine with several browsers, several Chrome profiles, a portable
browser, or a browser Katagiri does not know about, I am never told "not installed" when
the truth is "Katagiri could not look". The report distinguishes *searched and not found*
from *nothing to search / could not read*, and says which profiles were covered — so I do
not go install a second copy of an extension I already have in the profile that was never
scanned.

**Why this priority**: a confidently wrong "MISSING" is worse than no check at all — it
sends the operator to reinstall something that is already working, and it would make the
doctor table untrustworthy in general. It is P2 only because US1's happy path already
delivers value on the common single-profile machine.

**Independent Test**: run the detector against (a) a tree with two profiles where only the
second holds the extension, (b) no browser directory at all, and (c) a profile whose
preferences file is unreadable/corrupt; assert three distinct outcomes — found-in-profile-2,
unknown-no-browser-found, unknown-unreadable — and never a bare "absent" for (b) or (c).

**Acceptance Scenarios**:

1. **Given** two profiles in one browser where only one holds the extension, **When** the
   doctor runs, **Then** the row reports present and names the profile that has it.
2. **Given** no supported browser profile exists on the machine, **When** the doctor runs,
   **Then** the rows report "could not determine" with the reason, not "missing".
3. **Given** a profile whose data cannot be read (locked, corrupt, permission denied),
   **When** the doctor runs, **Then** that profile is reported as unreadable and the scan
   of the other profiles still completes.
4. **Given** any detection outcome, **When** the row is printed, **Then** it names the
   evidence source (profile path or the fact that none was found) rather than asserting a
   bare verdict.

### Edge Cases

- Extension present but **disabled** in the browser → reported as present-but-disabled if
  that state is legible from profile data, otherwise as present with a caveat; never
  silently counted as absent (and never "fixed" by Katagiri).
- Extension installed **unpacked / from source** (no store id) → the id-based check misses
  it; the row must be worded so the operator recognises this case rather than reinstalling.
- Browser **running** while the doctor reads its profile → read-only access must tolerate
  locked files (skip the locked source, report unreadable) and must never wait on a lock.
- Portable / non-default-location browser install → not found; falls under "could not
  determine", with the searched roots printed.
- Firefox-only learner (Yomitan ships for Firefox too) → if the Firefox variant is not
  covered by the detector, the row says so explicitly instead of reporting absent.
- mokuro: the page-change bridge's **shared secret is unset** in config → reported as
  "not configured" (the bridge fails closed by design), distinct from "configured but
  nothing is talking to it".
- mokuro: **the bridge is not running** at doctor time — which is the normal case, because
  no long-lived Katagiri process hosts it during an install — so the row must report
  configuration/reachability facts honestly and must not present "no answer on the port"
  as "the learner has not installed the userscript".
- The pinned bridge port is **occupied by something else** → reported as occupied-by-unknown,
  not as "bridge healthy".
- Detection is slow or hangs (huge profile tree, network filesystem) → bounded: the whole
  companion check has a hard time budget and degrades to "could not determine" on timeout,
  never wedging the installer.
- `--check` on a machine with no browsers at all (server/CI sandbox) → completes, exit code
  unaffected, no crash.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The doctor MUST report one status row per browser companion — Yomitan,
  asbplayer extension, mokuro page-change bridge — in both `--check` output and the
  wizard's doctor summary.
- **FR-002**: Detection MUST be read-only with respect to every browser profile: no
  create, write, rename, or delete anywhere under a browser's data directory, and no
  modification of any extension's files or settings.
- **FR-003**: Each row MUST distinguish at least three outcomes: present (with the
  browser/profile it was found in), absent (with the locations searched), and
  undetermined (with the reason: no browser found, unreadable, timed out, unsupported
  browser).
- **FR-004**: An absent or undetermined companion MUST NOT make the doctor's exit code
  non-zero on its own; these are operator-owned optional components, reported with the
  same "manual step" severity the scheduled-tasks and Irodori rows already use.
- **FR-005**: For every companion reported absent, the system MUST print an install
  handoff: the official listing URL and the numbered manual steps the operator performs
  in the browser.
- **FR-006**: The system MUST NOT install, download, unpack, side-load, or enable any
  browser extension or userscript, and MUST NOT write browser policy/registry entries to
  cause an install. Guidance and a URL are the only permitted outputs.
- **FR-007**: The installer MUST offer a re-check that re-runs only the companion
  detection (interactive step and/or post-wizard menu), so the operator can install in the
  browser and confirm without re-running earlier steps.
- **FR-008**: Under `--yes`, the companion step MUST be non-interactive and
  non-blocking: report + handoff text, no prompt, no wait, never a failed run.
- **FR-009**: `--check` MUST remain read-only and prompt-free with the new rows present.
- **FR-010**: The mokuro row MUST report the bridge's *configuration and port* facts
  (shared secret set/unset; pinned loopback port free / occupied / answering) and MUST
  state plainly that a not-running bridge is the expected state outside a live session —
  it must never be phrased as "the learner has not installed the userscript".
- **FR-011**: The companion check MUST complete within a bounded time budget and MUST
  degrade to "undetermined" rather than blocking the installer when a source is slow,
  locked, or unavailable.
- **FR-012**: The feature MUST add no MCP ToolSpec and MUST NOT alter any existing tool's
  name, arguments, or output shape.
- **FR-013**: No new general-purpose HTTP client may be introduced. Any liveness probe is
  limited to a bounded loopback TCP connect; the repository's single-HTTP-client invariant
  and its allowlist MUST remain unchanged and green.
- **FR-014**: No companion detection output may contain a secret value (in particular the
  mokuro shared secret), reported as presence only, consistent with the installer's
  existing "(set)/(unset)" idiom.
- **FR-015**: A short operator doc MUST state what each row means, what the operator does
  for each companion, and why Katagiri cannot install them.

### Key Entities

- **Companion**: a learner-side browser artifact Katagiri depends on but cannot install —
  identity (name), how it is detected (store id / port + config), its official install URL,
  and its manual steps.
- **Companion status**: one doctor row — companion name, verdict
  (present / absent / undetermined), evidence (profile path, port state, reason), and
  whether it is optional.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Running the doctor on a machine where a companion is missing names that
  companion and its install URL in the output, in one run, with no other command needed.
- **SC-002**: A doctor run on a synthetic profile tree with the extension present, and one
  with it absent, produce different verdicts for that row — and both leave the tree
  byte-identical (verified by hashing the tree before and after).
- **SC-003**: Zero browser extensions or userscripts are installed, downloaded, or
  modified by Katagiri in any code path of this feature, enforced by test, not by review.
- **SC-004**: `--check` exit codes are unchanged from pre-008 for every combination of
  companion states (absence never flips 0 → 1).
- **SC-005**: The companion check adds no more than 2 seconds to a doctor run on a normal
  machine, and is hard-bounded so no single source can stall it.
- **SC-006**: On a multi-profile machine, the report names the profile that holds the
  extension; on a machine with no readable browser data, it says "could not determine" —
  neither case ever prints a bare "missing".
- **SC-007**: The repository's single-HTTP-client and no-HTTP-server invariants stay green
  with no new allowlist entry.

## Assumptions

- Windows 11 is the only supported host (constitution technology constraints); detection
  may use Windows-only paths (`%LOCALAPPDATA%`, `%APPDATA%`) exactly as the existing Anki
  profile detection does.
- Chromium-family browsers (Chrome, Edge, Brave, and any browser using the same
  `User Data` layout) are the primary target because that is where both companion
  extensions ship. Firefox coverage is a bounded, best-effort extra: whichever way it is
  resolved, the row must state its own coverage honestly (FR-003) rather than guess.
- The companions are **optional** from the installer's point of view: the machine-side
  install is complete without them; they gate parts of the study loop, not the setup.
- The mokuro bridge is hosted by Katagiri itself and is not running during an install; the
  userscript that drives it is not shipped in this repository today. 008 reports the facts
  it can see and points at the setup instructions; producing or shipping the userscript is
  out of scope for this feature.
- asbplayer's **extension** (browser) and its **WebSocket bridge** (a separate local
  process) are different things. 008 covers only the extension's presence. The bridge —
  including its planned in-process Python replacement — belongs to
  `009-asbplayer-bridge-in-process` and is untouched here.
- Store listing ids are stable identifiers for the published extensions; a changed or
  unpublished listing is an operator-visible failure of the handoff URL, not of detection.
