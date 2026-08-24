# Research & Decisions: 008 — Browser Companion Check

Scouted 2026-08-24 against the repository at `main` (HEAD `342f2fe`) plus two web lookups
for store listing ids. Findings that could not be settled without running code on a real
browser profile are recorded as **open items** at the bottom rather than guessed at.

## R1. How a Chromium extension's presence can be detected locally, without its cooperation

Katagiri gets no help from the extension itself (no Yomitan/asbplayer local API is
reachable at install time without the learner wiring one up). So detection has to read
what the browser leaves on disk. Four candidate signals, in decreasing order of
reliability:

| # | Signal | Path (Chrome, Windows) | Tells you | Caveats |
|---|---|---|---|---|
| A | Extension payload directory | `…\User Data\<Profile>\Extensions\<extension-id>\<version>\manifest.json` | The extension's files are on disk for that profile | Survives *disabling*; can lag an uninstall until cleanup runs; absent for unpacked/dev loads |
| B | Profile `Preferences` JSON | `…\User Data\<Profile>\Preferences` → `extensions.settings.<id>` | Installed **and** its enabled/disabled state | Large JSON (tens of MB on old profiles); written by a running browser, so it can be mid-write; key layout is not a public contract |
| C | Profile `Secure Preferences` JSON | `…\User Data\<Profile>\Secure Preferences` | Same, for MAC-protected entries | Same caveats, plus the layout differs between Chrome versions |
| D | `Local State` profile roster | `…\User Data\Local State` → `profile.info_cache` | *Which* profiles exist and their display names | Needed to name profiles in the report; says nothing about extensions |

- **Decision**: use **A as the primary signal** and **B as an enrichment**, with **D** to
  enumerate profiles for the report. Rationale: A is a filesystem `is_dir()` check — cheap,
  lock-free, safe against a running browser, and it cannot be broken by a JSON layout
  change. B is read only if it is present and parses within the time budget, purely to add
  "(disabled)" to the verdict; a failure to read it downgrades the row's detail, never its
  verdict. C is **not** read (layout instability buys nothing over A).
- **Roots to scan** (each `…\User Data`): Chrome `%LOCALAPPDATA%\Google\Chrome`,
  Chrome Beta/Dev/Canary siblings, Edge `%LOCALAPPDATA%\Microsoft\Edge`, Brave
  `%LOCALAPPDATA%\BraveSoftware\Brave-Browser`, Vivaldi `%LOCALAPPDATA%\Vivaldi`,
  Opera `%APPDATA%\Opera Software\Opera Stable`. Within a root, profile dirs are
  `Default` and `Profile *` (plus whatever `Local State` names).
- **Multi-profile problem** (spec US3): a single boolean over all profiles is what makes
  this check untrustworthy, so the detector returns *per-profile* hits and the row reports
  the profile that has it. "No profile has it" and "no profile could be read" are separate
  outcomes, never merged.
- **Rejected alternatives**: querying the Chrome Web Store API for the learner's installs
  (no such thing without an authenticated Google account — and out of the security
  posture); driving DevTools/CDP against a running browser (requires a debug port,
  i.e. asking the learner to relaunch their browser — a bigger imposition than the
  install we are trying to detect); the extension's own `Local Extension Settings\<id>`
  LevelDB directory (a third presence signal, same information as A, plus a binary format
  we would have to parse).
- **Precedent in-repo**: `installer._detect_anki_data_dir()` (installer.py:617-635) is
  exactly this shape already — "best-effort locate a third-party app's profile folder at
  its default Windows path… pure filesystem/env reads, no subprocess, no network. Returns
  `None` if not installed/moved/no profile yet." 008 follows it deliberately.

**Why silent install is out of the question**: Chrome removed inline/programmatic
installation years ago; the only non-interactive install paths left are enterprise policy
(`ExtensionInstallForcelist` under `HKLM`, i.e. writing machine security policy — squarely
in "modifying system/security settings" territory and off-limits) and `--load-extension`
on a relaunched browser with a locally unpacked CRX (side-loading, which also breaks store
updates). Both are rejected; the spec's boundary #1 is not a Katagiri preference, it is the
platform's rule.

## R2. Firefox variant

Yomitan does publish a Firefox add-on, and a Firefox-only learner is a plausible user of
this repo. The equivalent signals are `%APPDATA%\Mozilla\Firefox\profiles.ini` (profile
roster) → `<profile>\extensions.json` (installed add-ons, with ids like
`{…}`/`yomitan@…`) or the `<profile>\extensions\` directory of XPIs.

- **Decision**: Firefox support is **in scope as best-effort**, behind the same
  three-outcome rule: if the detector cannot enumerate Firefox profiles, the row says the
  Firefox side was not covered rather than reporting absent. asbplayer's Firefox story is
  weaker than Chrome's (its published listing and its bridge/AnkiConnect flows are
  Chromium-first), so Firefox coverage is asserted for Yomitan and left explicitly
  "unknown/not covered" for asbplayer unless the implementing task verifies otherwise.
- **Open item O-3** below records what is *not* verified about the Firefox `extensions.json`
  shape.

## R3. mokuro "userscript reachability" — what is actually checkable

Scouting `src/katagiri/media_mokuro.py` changes the shape of this question:

- mokuro-reader is a browser app with no IPC surface. It fires a
  `mokuro-reader:page.change` CustomEvent; a **~10-line userscript the learner installs**
  POSTs that event to a small HTTP server **Katagiri itself runs** —
  `MokuroBridgeServer`, bound to `127.0.0.1`, pinned port `config.MOKURO_BRIDGE_PORT`
  (8767), requiring `mokuro_shared_secret` (compared with `hmac.compare_digest`, never
  logged) plus an `Origin` check, and **failing closed when the secret is unset**.
- **Two facts the installer must not paper over**: (a) that userscript **does not exist in
  this repository** — it is described in `media_mokuro.py`'s docstring, `docs/oss-components.md`,
  and `docs/dev-plan.md`, but no `.user.js` artifact is shipped; (b) the bridge is **never
  started by the MCP server** — `mcp_server.py:1791,1834` constructs `MokuroChannel(secret=None)`
  per call, and nothing calls `MokuroBridgeServer.start()` outside tests and the
  `MokuroChannel` context manager.
- **Therefore**: "probe the bridge and see if the userscript answers" is not a coherent
  check at installer time. What *is* checkable, cheaply and honestly:
  1. `mokuro_shared_secret` set or unset in config (presence only, never the value) — an
     unset secret means the bridge would reject every push, so this is the real blocking
     precondition;
  2. whether anything is listening on the pinned loopback port — a bounded
     `socket.create_connection(("127.0.0.1", 8767), timeout≈0.2)`, reported as
     free / occupied, with "free" explicitly labelled as *expected* outside a live session;
  3. the setup instructions (install a userscript manager, add the page-change script,
     point it at `http://127.0.0.1:8767` with the shared-secret header).
- **Decision**: the mokuro row reports (1) + (2) and carries (3) as its handoff text. It is
  worded as configuration readiness, never as "the learner has not installed the
  userscript" (spec FR-010). Existing coverage to mirror: `tests/test_media_mokuro.py`
  (every test uses `port=0`, so the pinned port is never bound by the suite — 008's tests
  must not bind it either; a *connect* attempt is fine).
- **HTTP-client invariant**: `tests/test_bverify.py` `HTTP_CLIENT_PATTERNS` match
  `urllib.request` / `http.client` / `requests`|`httpx` imports and attribute calls;
  `HTTP_SERVER_PATTERNS` match `.bind((`, `.listen(`, `serve_forever`, framework names.
  A bare `socket.create_connection(...)` matches **neither**. So the port probe needs
  **no allowlist entry and no D-47-style ledger exemption** — and the plan holds that as a
  hard constraint: if an implementer reaches for `http.client` here, the task stops and
  escalates instead of adding an allowlist row. (Precedent for the shape:
  `asbplayer_launch.bridge_port_is_occupied()`, which is exactly this socket connect; that
  file is allowlisted only because of the separate `http.client` health probe next to it.)

## R4. asbplayer: extension vs. website vs. bridge — and the 009 boundary

asbplayer exists as three distinct things, and conflating them is the main way 008 could
damage 009:

1. **The browser extension** (Chrome Web Store listing) — what the learner installs to get
   subtitle capture and mining on streaming sites. *This, and only this, is what 008
   detects.*
2. **The asbplayer web app** (the hosted/self-hosted player page) — usable without the
   extension for local files; not a local install, so nothing on disk to detect. Out of
   scope; the handoff text may mention it as the "no extension needed" path.
3. **The WebSocket bridge** — a separate local process (today a Go checkout started by
   `asbplayer_launch.py` from `config.asbplayer_bridge_dir`, hosting `ws://127.0.0.1:8766/ws`
   plus a small HTTP surface and an AnkiConnect proxy). `media_asbplayer.py` is its client.
- **Decision**: 008 touches **none** of `asbplayer_launch.py`, `media_asbplayer.py`, or
  `config.asbplayer_bridge_dir`, adds no bridge health row, and its handoff text says
  nothing about how the bridge is hosted. Rationale: `009-asbplayer-bridge-in-process` is
  planned to replace the Go bridge with an in-process Python WS server (specs/README.md);
  any 008 row asserting "bridge dir configured / bridge healthy" would become a contract
  009 has to preserve or migrate. The extension row is orthogonal to that decision — the
  learner needs the extension either way.
- Note for 009's author, not a 008 deliverable: a bridge-readiness doctor row is a natural
  009 addition once the hosting question is settled.

## R5. Store listing ids / handoff targets

Verified by web lookup 2026-08-24:

| Companion | Handoff URL | Store id |
|---|---|---|
| Yomitan (Chrome/Chromium) | `https://chromewebstore.google.com/detail/yomitan/likgccmbimhjbgkjambclfkhldnlhbnn` | `likgccmbimhjbgkjambclfkhldnlhbnn` |
| Yomitan (Firefox) | `https://addons.mozilla.org/firefox/addon/yomitan/` | AMO slug `yomitan` (internal add-on id **not verified** — open item O-3) |
| asbplayer (Chrome/Chromium) | `https://chromewebstore.google.com/detail/asbplayer-language-learni/hkledmpjpaehamkiehglnbelcpdflcab` | `hkledmpjpaehamkiehglnbelcpdflcab` |
| mokuro userscript | no store listing — userscript-manager setup steps (see R3) | n/a |

Upstream repos, for the doc: `yomidevs/yomitan`, `asbplayer/asbplayer` (both recorded in
`docs/oss-components.md:117` as post-move locations).

- **Decision**: ids live in **one catalog constant** in the new module, each with a comment
  citing this table, so a re-verification is a single-file edit. The doc and the handoff
  text both render from that catalog — the URL is never duplicated in prose.

## R6. Where the code lives

- **Decision**: a new module `src/katagiri/companions.py` — pure detection + the catalog +
  the handoff text, no imports of the rest of the package beyond `config`. `installer.py`
  gains only (a) a `probe_companions()` wrapper turning the detector's output into
  `ComponentStatus` rows inside `collect_doctor_statuses()`, and (b) one appended wizard
  step + a post-wizard re-check entry.
- **Rationale**: installer.py is already 1717 lines and is the feature's only hot file;
  keeping detection out of it means the serial-on-main diff is two small blocks, and the
  detector is unit-testable without spawning the installer. It also respects installer.py's
  own stated rule ("must not import other Katagiri modules at top level other than
  `config`") — `companions` is imported lazily inside the probe, exactly like
  `katagiri.anki_launch` is at installer.py:640.
- **Severity mapping**: absent → `MANUAL STEP`, never `MISSING`, because
  `doctor_exit_code()` (installer.py:795) returns 1 iff any row is `MISSING`. This is the
  same argument `probe_irodori` already records in-line ("optional and consent-gated…
  not a problem to flag via doctor_exit_code"). This is what keeps SC-004 true.
- **Step wiring**: append one label to `STEP_LABELS` (currently 10 entries) and bump
  `TOTAL_STEPS` 11 → 12. Appending is safe: `tests/test_installer_setup.py:166` indexes
  `STEP_LABELS[7]` (scheduled tasks) and the other tests iterate the tuple generically.

## R7. Governance

- **Decision**: one decisions-ledger row, filed **before** any code task (007's T001
  pattern). It records: no new ToolSpec (constitution VII satisfied trivially); the
  no-silent-install / no-file-manipulation boundary as a *binding* rule rather than a
  current limitation; the read-only-profile-access rule; the "absent ⇒ MANUAL STEP, never
  MISSING" exit-code decision; the socket-connect-only probe and the explicit finding that
  **no HTTP allowlist change is required** (contrast D-47); and the 008/009 boundary.
- **Next D-number**: the ledger's last row today is **D-48**, so the row is expected to be
  **D-49** — but the number MUST be re-confirmed at execution time (`grep '^| D-' docs/decisions-ledger.md | tail -3`),
  because this draft goes stale the moment another feature files a row. 007's T001 landed
  as D-46 after being drafted as D-39; that is the norm, not an anomaly.
- No constitution amendment: nothing in principles I–VII changes, and no new principle is
  needed for a CLI-only, contract-free feature.

## Open items (not resolved — do not treat as decided)

- **O-1 — Disabled-extension legibility.** Whether "installed but disabled" is reliably
  readable from `Preferences` → `extensions.settings.<id>.state` across current Chrome
  versions was not verified against a real profile. Until it is, the implementation must
  treat the disabled flag as best-effort detail and never let it change the verdict.
- **O-2 — Uninstall lag.** How long a removed extension's `Extensions\<id>\` directory
  survives (garbage collection timing) is unverified. A stale directory would produce a
  false "present". Mitigation the tasks carry: when the `Preferences` read succeeds and
  shows no entry for an id whose directory exists, report present-with-caveat rather than
  a confident present. Whether that combination actually occurs is unverified.
- **O-3 — Firefox specifics.** The `extensions.json` schema, and Yomitan's add-on id inside
  it, were not verified against a real Firefox profile; `profiles.ini` parsing (relative
  vs. absolute `Path=`, `IsRelative`) likewise. The implementing task must verify or
  degrade the Firefox row to "not covered".
- **O-4 — Portable / non-default installs.** No general way to find a Chromium profile that
  is not under the standard roots. Accepted as permanently "undetermined"; an operator
  override (a config key pointing at extra profile roots) was considered and deferred — no
  evidence yet that anyone needs it.
- **O-5 — mokuro userscript artifact.** The repository ships no userscript, so the handoff
  text can only describe the steps rather than link a file. Whether 008 should *add* a
  `.user.js` (and where it would live) is a real question this feature deliberately does
  not answer; it is called out here so 008 is not mistaken for having closed it.
- **O-6 — Store id drift.** The two Chrome ids were confirmed from the live store listings
  on 2026-08-24. Nothing in the repo re-verifies them over time; a delisted or migrated
  extension would silently produce false "absent" verdicts. No monitoring is proposed —
  the failure mode is visible to the operator the first time the URL 404s.
