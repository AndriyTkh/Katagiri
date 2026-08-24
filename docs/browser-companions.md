# Browser Companions

Operator-facing guide to the three browser-companion rows the doctor reports
(`setup.bat --check` and the wizard's doctor summary): Yomitan, the asbplayer
extension, and the mokuro page-change bridge. Background and design:
`specs/008-browser-companion-check/spec.md` (FR-015), `research.md` (R3, R4,
R5). This describes the designed behavior of those rows, not necessarily
today's code state — see `tasks.md` for what has landed.

## What the rows mean

Each row reports one of three verdicts, never a bare yes/no:

- **present** — the companion was found, with evidence: which browser and
  profile it was found in (or, for mokuro, which configuration/port facts
  were observed).
- **absent** — searched and not found. The row names the locations that
  were searched, and is followed by a MANUAL STEP with the install
  instructions (below).
- **could not determine** — Katagiri could not tell, and says why (no
  supported browser found, a profile was unreadable/locked, the check timed
  out, or the browser/profile shape is not one Katagiri covers, e.g.
  Firefox for asbplayer or a portable browser install).

**Read the evidence, not just the verdict.** A row always names where it
looked (profile path) or why it couldn't. That is what lets you tell "not
installed" apart from "not fully checked" — if the profile that actually has
the extension was never scanned (a second Chrome profile, a portable
install), the fix is not reinstalling, it's pointing the check at the right
place or accepting "could not determine".

**Absence never fails the run.** These companions are optional from the
installer's point of view — the machine-side setup is complete without
them; they gate parts of the study loop, not setup itself. An absent or
undetermined row is reported as `MANUAL STEP`, the same severity already
used for scheduled tasks and the Irodori schedule, and never turns
`--check`'s exit code non-zero by itself.

## Why "could not determine" is its own, honest verdict

Katagiri could easily collapse "could not determine" into "absent" — but a
confidently wrong "missing" is worse than admitting ignorance. It would send
you to reinstall something you already have, in a profile the check never
looked at, or on a browser it doesn't know how to read. So "could not
determine" is reported as its own outcome, always with a reason (no browser
found / unreadable / timed out / unsupported browser or profile shape), and
it carries the same non-blocking `MANUAL STEP` severity as absence rather
than pretending to certainty it doesn't have.

If you see "could not determine," check whether the reason names a profile
or browser you don't actually use. If it does, there's nothing to do. If it
names your real profile, look at the reason: an unreadable profile usually
means the browser was running and holding a lock (fully close it and
re-run `--check`); "no browser found" or "unsupported" means Katagiri
doesn't know where to look on this machine, and the only recourse today is
to confirm manually in the browser itself (`chrome://extensions`, etc.) —
there is no override to point the check at a nonstandard location.

## What to do, per companion

### Yomitan

If the row reports absent or could not determine:

1. Open the official Chrome Web Store listing:
   `https://chromewebstore.google.com/detail/yomitan/likgccmbimhjbgkjambclfkhldnlhbnn`
2. Click "Add to Chrome" (or your Chromium-family browser's equivalent) and
   install it.
3. Re-run `setup.bat --check` (or choose the wizard's re-check option). The
   row flips to present once the extension is on disk — no other setup step
   needs to be re-run.

Firefox users: Yomitan also ships as a Firefox add-on
(`https://addons.mozilla.org/firefox/addon/yomitan/`), but the doctor's
Firefox coverage is best-effort and may report "could not determine" even
when the add-on is installed. If you're on Firefox and the row can't
confirm it, check `about:addons` yourself.

### asbplayer (extension)

If the row reports absent or could not determine:

1. Open the official Chrome Web Store listing:
   `https://chromewebstore.google.com/detail/asbplayer-language-learni/hkledmpjpaehamkiehglnbelcpdflcab`
2. Click "Add to Chrome" (or your Chromium-family browser's equivalent) and
   install it.
3. Re-run `setup.bat --check`. The row flips to present once found.

This row is about the browser **extension** only — the piece that gives you
subtitle capture and mining on streaming sites. It says nothing about, and
does not check, any local bridge process asbplayer's mining flow may talk
to; that is a separate concern outside this doc's scope.

### mokuro (page-change bridge)

mokuro-reader has no extension to install. Instead, a small userscript you
add yourself pushes page-change events to a tiny local server Katagiri
hosts. The row reports readiness facts, not "installed/not installed":

- whether `mokuro_shared_secret` is set in config (presence only — the
  value itself is never shown or logged); an unset secret means the bridge
  would reject every push, so this is the real blocking precondition, and
- whether the pinned loopback port is free, occupied, or answering. A
  **free port is the expected state** outside an active study session —
  it does not mean the userscript is missing, so don't read it as a
  problem.

If the secret is unset or you haven't set up the userscript side yet:

1. Set `mokuro_shared_secret` in `%LOCALAPPDATA%\Katagiri\config.toml`
   (any local value you choose; treat it like the other secrets in that
   file — never commit or paste it anywhere).
2. Install a userscript manager in your browser (e.g. Tampermonkey or
   Violentmonkey).
3. Add the mokuro page-change userscript, pointed at the pinned loopback
   port (`config.MOKURO_BRIDGE_PORT`, 8767 by default), sending the shared
   secret you set in step 1.

Katagiri does not ship this userscript in the repository today — the
steps above are what you write or source yourself. There is nothing to
re-check automatically the way there is for a store extension: the bridge
is only up during an active Katagiri session, so "not answering" at doctor
time is normal, not a failure.

## Why Katagiri cannot install these for you

This is a platform rule, not a missing feature. Chrome removed
programmatic/inline extension installation years ago. The only
non-interactive install paths left are enterprise policy (writing
`ExtensionInstallForcelist` under `HKLM` — machine security policy, out of
bounds for a personal tool) and `--load-extension` with a locally unpacked
CRX (side-loading, which also breaks store updates and disables Chrome's own
update/integrity checks for that extension). Both are rejected. So the flow
Katagiri offers is: detect, hand you the official store URL and the exact
manual steps, let you do the install in your own browser, then re-check —
never a silent or automated install, and never a write to any browser
profile, extension file, or policy/registry entry.

## Known blind spots

These are open items from `research.md`, not resolved gaps this doc is
glossing over:

- **Unpacked / dev-loaded extensions.** An extension loaded unpacked (no
  store id) is invisible to the id-based check. If you know you sideloaded
  Yomitan or asbplayer from source, a "could not determine" or "absent"
  row does not mean reinstall it — it means the check can't see this
  install method at all.
- **Portable browser installs.** There is no general way to find a
  Chromium profile that isn't under the standard install roots. This is
  accepted as permanently "could not determine" for portable browsers; no
  override to point the check at a nonstandard location exists today.
- **Firefox coverage limits.** The `extensions.json` schema and Yomitan's
  add-on id inside it, and `profiles.ini` parsing details, were not
  verified against a real Firefox profile at design time. asbplayer's
  Firefox story is weaker still (its listing and mining flow are
  Chromium-first), so the asbplayer row may report Firefox as "not
  covered" rather than attempt a check there.
- **No userscript file shipped in this repo.** The mokuro page-change
  userscript is described in `src/katagiri/media_mokuro.py`'s docstring
  and in `docs/oss-components.md`, but no `.user.js` file exists in this
  repository for you to install directly — you write or source it
  yourself, per the steps above.
