# Demo-profile setup runbook — specs/005-mcp-assignment (US3)

This is the explicit, numbered setup for the **demo profile**: a dedicated
Obsidian vault, on its own port, with its own token, that the homework
agent and the graded recording use instead of anything personal. It
implements FR-009/FR-010 and the isolation promised by US3. Governance for
this (ledger D-11/D-20/D-22, amended/scoped by D-34) is settled and is not
re-argued here — see `docs/decisions-ledger.md` for the rows.

**Do not skip the manual netstat step (Step 5).** It is manual on purpose:
the demo port is *outside* what Katagiri's own `security_status` tool
checks (see "Why this is manual" below), so nothing in the codebase will
catch a misconfigured port for you.

## Ledger context (read, don't re-litigate)

- **D-11**: Obsidian via `local-rest-api` v5.1+, personal instance on
  `:27123`; amended by D-20.
- **D-20**: the plugin's own MCP endpoint is never registered with
  *katagiri's* agent surface; katagiri proxies GET-only, holding the
  personal token itself.
- **D-22**: secrets live under `%LOCALAPPDATA%\Katagiri`; stdio-only MCP;
  loopback + firewall verification is part of the security workstream.
- **D-34**: scopes D-20 for this feature only — the homework agent's
  *direct* connection to the plugin's built-in MCP endpoint on a
  **dedicated demo vault** (own port, own token, synthetic content) is
  outside D-20's prohibition. Katagiri's own personal-vault GET-only proxy
  (D-11/D-20) is untouched by any of this: it still only ever talks to
  `127.0.0.1:27123` (hardcoded — see Step 2's port choice below for why
  that matters) and it still only ever holds the *personal* token.

## What "the demo profile" means, concretely

Three things, selected together by `KATAGIRI_CONFIG` (Step 6) and never
touched by the graded run: a **fixture DB** (Step 7), a **demo vault**
(Step 1 — the tree committed at `tests/demo_fixtures/vault/`, containing
the two goal-note variants used by US1's changed-valid-input demo plus a
small curriculum stub and an inbox stub), and a **demo token** (Step 3,
naming a *different* Local REST API instance than the personal one).

## Numbered setup

### Step 1 — Open a second Obsidian window on the demo vault

1. In your existing Obsidian window (the personal vault), use
   **File → Open another vault → Open folder as vault**, or launch
   Obsidian a second time from its Start Menu/desktop shortcut — either
   opens a second, independent window.
2. Point the new window at a folder containing a copy of
   `tests/demo_fixtures/vault/` (copy the tree out of the checkout; do not
   open the git working copy itself as a vault — keeps Obsidian's own
   `.obsidian/` config and any workspace state out of the repository).
3. Confirm you now have **two** Obsidian windows: personal vault in one,
   demo vault in the other. Keep them visually distinguishable (e.g.
   different window title/vault name) so a recording never shows the
   wrong one.

### Step 2 — Install/enable the Local REST API plugin on the demo vault, on a non-default port

1. In the demo-vault window: **Settings → Community plugins** → make sure
   community plugins are enabled → install **"Local REST API"**
   (`coddingtonbear/obsidian-local-rest-api`) if not already present, at
   the same **v5.1+** floor D-11 requires (the T005 spike observed
   **v5.1.0**) → enable it.
2. Open the plugin's settings panel. It defaults to the same ports the
   personal instance uses (insecure HTTP `27123`, HTTPS `27124`). **Change
   both** to non-default values so this instance can never be mistaken
   for the personal one — e.g. HTTPS **`27224`**, insecure HTTP
   **`27223`** (leave the insecure port disabled if you don't need it, as
   the personal instance already does per the T005 spike note in
   `docs/assignment/part-a-server-decision.md`).
3. **Why the exact numbers matter beyond "different"**: katagiri's own
   Obsidian proxy (`src/katagiri/obsidian_proxy.py`) hardcodes
   `OBSIDIAN_PORT = 27123` — it is not read from config and cannot be
   pointed anywhere else. So the demo instance being on a different port
   is not just tidy separation; it is *structurally* the reason katagiri's
   own vault tools (`vault_file`, `vault_list`, `obsidian_active_note`)
   can never reach the demo vault, by construction, no matter what the
   demo `config.toml` says. That is intentional — see "Personal Obsidian
   during recording" below.

### Step 3 — Generate a distinct demo token

1. In the demo-vault plugin settings, use its **"Generate new API Key"**
   (or equivalent) action, on this instance, to produce a token that has
   never been used anywhere else.
2. Copy it into `agent/.env`'s `OBSIDIAN_API_TOKEN` (gitignored; see
   `agent/.env.example`) — this is the token the **homework agent** uses
   to talk **directly** to the plugin's built-in `/mcp/` endpoint (D-34).
   It is never written into any katagiri `config.toml`.
3. Confirm it is *different* from whatever token lives in the personal
   `%LOCALAPPDATA%\Katagiri\config.toml`'s `obsidian_api_token` — two
   distinct tokens, two distinct instances, matching FR-010.

### Step 4 — Trust or bypass the self-signed certificate

The plugin's HTTPS endpoint (the demo instance's `27224`) uses a
self-signed certificate, same as the personal instance's `27124` (T005
spike). Pick one:

- **Bypass** (fastest for a rehearsal): set `OBSIDIAN_VERIFY_TLS=false` in
  `agent/.env`. Acceptable here *specifically* because the target is the
  demo instance on a non-default port with a demo-only token — never do
  this for a connection that could reach the personal instance.
- **Trust properly** (better for a recording you want to look clean):
  export the demo instance's certificate and point `agent/.env`'s
  `OBSIDIAN_CA_BUNDLE` at the exported file, leaving
  `OBSIDIAN_VERIFY_TLS=true`. `agent/src/katagiri_agent/config.py`'s
  `httpx_client_factory` reads either.

### Step 5 — Manual `netstat` verification (do this every time, before recording)

This step exists because **the demo port is invisible to katagiri's own
hardening check.** `security_status`'s `security_scan()`
(`src/katagiri/mcp_server.py`) only ever inspects a fixed tuple —
`HARDENED_PORTS = (27123, 8766, 19633, 8765)` — none of which is `27223`/
`27224`. Nothing in the codebase will ever tell you the demo port is
misconfigured; only a human running `netstat` will.

1. With the demo Obsidian window open and the plugin enabled, run (an
   elevated prompt is not required):
   ```
   netstat -ano | findstr 2722
   ```
2. Confirm you see the demo port (e.g. `27224`) in `LISTENING` state, and
   **confirm you do NOT see it bound to anything but loopback**
   (`127.0.0.1:27224`, not `0.0.0.0:27224`) — same "loopback-only" bar
   `security_scan()` holds the four hardened ports to, just checked by
   hand here because this port isn't one of them.
3. **Separately**, confirm the *personal* port's actual state matches
   whatever you decided in the "Personal Obsidian during recording"
   section below:
   ```
   netstat -ano | findstr 27123
   netstat -ano | findstr 27124
   ```
   If you decided personal Obsidian stays **closed** during recording
   (the recommendation — see below), this should show **nothing**
   listening on `27123`/`27124`. If it shows something, personal Obsidian
   is still running and the recording is not isolated the way it's
   supposed to be — close it before proceeding.
4. Record the actual `netstat` output (or a screenshot) once, during
   rehearsal, as the evidence this check was really run — this is the
   "asserted mechanically, not promised in prose" half of US3.

### Step 6 — Set `KATAGIRI_CONFIG`

1. Copy `tests/demo_fixtures/demo-config.toml.example` to a location
   outside the repository (e.g. `%LOCALAPPDATA%\Katagiri\demo-config.toml`
   or any path you prefer) and fill in the real absolute paths for this
   machine (`vault_path` → the demo vault copy from Step 1; `db_path` →
   where Step 7 will build the fixture DB; `scratch_root` → a scratch
   folder of its own). Leave `obsidian_api_token` unset — see the
   template's own comment on why it is inert for the demo profile.
2. Set the environment variable so katagiri's MCP server (started
   independently, per FR-001) loads that file instead of the personal
   `%LOCALAPPDATA%\Katagiri\config.toml`:
   ```
   $env:KATAGIRI_CONFIG = "C:\path\to\your\demo-config.toml"
   ```
   (or `setx` for a persistent value in a dedicated demo shell profile).
3. Absent this variable, behavior is byte-identical to today (T007,
   FR-008) — this is the *only* permitted `src/katagiri/` change in this
   feature, and it changes nothing until you set it.

### Step 7 — Build the fixture DB

1. Run the demo DB build script (its exact flags are still being
   finalized alongside it — see `scripts/build_demo_db.py`, T008/T016 of
   this feature's tasks.md):
   ```
   uv run python scripts/build_demo_db.py
   ```
2. It runs the migration, imports the vendored JMdict data (no runtime
   downloads, D-10), and seeds the study state the demo needs — enough
   for **≥2 distinct `prescribe()` rungs** and **≥2 distinct coverage
   outcomes** to be reachable (FR-004), using the `food`/`transport`
   topic split that lines up with the two goal-note variants'
   `goal_theme` values (`tests/demo_fixtures/vault/00-goals/`).
3. Note (and record, for the grader) the wall-clock time the JMdict
   import step takes — this script is expected to print it.

*Reachable-states section*: which specific `action.kind` values and
coverage outcomes the seeded DB reaches is finalized at T016 (after this
script's seed rows land) and will be appended here as its own section —
not duplicated in advance of that task actually seeding the DB.

### Step 8 — Decide: does personal Obsidian stay closed during recording?

**Decision, written down now, per FR-010: yes — personal Obsidian stays
closed during the recording. Recommended and adopted.**

**Consequence of closing it** (the accepted trade-off, US3 acceptance 5):
katagiri's own vault-proxy tools — `vault_file`, `vault_list`,
`obsidian_active_note` (`src/katagiri/obsidian_proxy.py`, hardcoded to
`127.0.0.1:27123`) — will report themselves **unreachable**
(`obsidian_unreachable`, a clean answer dict, never a crash) for the
whole recording, because nothing will be listening on `27123`. This is
**fine** and does not silently break the demo, because:

- The graded flow's actual vault read (US1's goal-note frontmatter) never
  goes through katagiri's proxy at all — it is the homework agent's own,
  direct connection to the **demo** vault's plugin endpoint (D-34), which
  stays open throughout (it's the vault the recording is *using*).
- Katagiri's **disk-backed** reads — curriculum parsing
  (`katagiri.intelligence.load_curriculum`/`import_curriculum`) and
  markdown search (`katagiri.md_search.vault_root` and everything built
  on it, including `search_notes`) — read `vault_path` directly off disk
  and do not depend on any Obsidian process being open at all. With
  `vault_path` pointed at the demo vault (Step 6), these keep working
  throughout the recording.
- If the flow or defence script ever calls a katagiri tool that *does*
  need the personal-Obsidian-shaped proxy (it shouldn't, by design — the
  demo profile has no reason to touch the personal vault), that call
  failing loudly and legibly is itself a fine thing to show: it is a
  concrete demonstration that the isolation is real, not just claimed.

**If this decision is ever revisited** (e.g. an instructor question makes
opening personal Obsidian briefly useful): reopening it does **not**
change which vault the agent or katagiri's `vault_path` point at — it
only changes whether katagiri's proxy tools regain a live personal-vault
connection, which is orthogonal to everything this recording exercises.
Re-run Step 5's second `netstat` check afterward to confirm you know
which state you're back in before recording anything.

## What comes later (not this task)

- **Pre-flight + Windows Defender section**: added by T026
  (`scripts/preflight_demo.py`), appended below this runbook's numbered
  steps once that script exists — do not duplicate its content here in
  advance.
- **Reachable-states table**: added by T016 once the fixture DB's seed
  rows are finalized (see the placeholder note under Step 7).
