# Quickstart / Verify Runbook: 009 — asbplayer Bridge, In-Process

Prerequisites: Windows 11, uv on PATH, repo checked out, `uv sync` done after the
`aiohttp` dependency landed. All commands from the repo root.

The automated sections bind **ephemeral ports only** — nothing here binds 8766 except §6
and §7, which are the deliberate real-machine steps. Before starting, confirm 8766 is free
(and that no old Go bridge is running):

```powershell
Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
```

This file doubles as the TG3 gate script: the gate runs §1–§9 in order. **§5 and §6 are
not optional** — plan.md §Deliberate omissions replaces the held-out suite with them, so
skipping either fails the gate.

## 1. Dependency resolves cleanly

```bash
uv sync
uv run python -c "import aiohttp, sys; print(aiohttp.__version__, sys.version)"
```

Expected: a clean resolve with no build step (wheels only), and the pinned range from the
ledger row actually installed. Record the resolved version and the transitive additions
(research.md O-6).

## 2. Bridge protocol tests

```bash
uv run pytest tests/test_asbplayer_bridge.py -q
```

Expected: green. Covers, against a real loopback WebSocket client on an ephemeral port:
the `/ws` upgrade; text `PING` → text `PONG`; all six commands' envelopes and bodies;
`messageId` correlation including a mismatched reply and a late reply; the 5-second
deadline; broadcast to two connected clients; the five relay endpoints' status codes and
bodies (including 400 on an unparsable body and 500 on no answer);
`POST /disconnect-ws-clients`; and every AnkiConnect proxy branch from research.md R3
against a stub upstream — non-`addNote` forward, no-client forward, intercept
field/value match and mismatch, `POST_MINE_ACTION == 2` (forward first, note id attached,
publish without awaiting), non-2 with `published: true` (HTTP 200, body `-1`, no forward),
and non-2 with `published: false` / malformed / absent (forward after all).

## 3. Launcher tests

```bash
uv run pytest tests/test_asbplayer_launch.py -q
```

Expected: green. Covers reuse of a healthy bridge without starting a second one; an
occupied-but-unhealthy port left strictly alone; a successful in-process start; loopback
bind by default; the documented host override honored with a warning; `stop()` releasing
the port; and — the cutover proof — that no Go lookup and no `subprocess` call exists in
the module any more.

## 4. The client half did not move (SC-003)

```bash
uv run pytest tests/test_media_asbplayer.py tests/test_everify.py -q
git diff main --stat -- tests/test_media_asbplayer.py tests/test_everify.py src/katagiri/media_asbplayer.py
```

Expected: green, and the diff **empty for all three files**. A non-empty diff means the
protocol surface drifted; the gate fails and the question goes back to the orchestrator.
Also confirm the surface version is untouched:

```bash
uv run python -c "from katagiri.media_asbplayer import PROTOCOL_SURFACE_VERSION as v; assert v == 3, v; print('surface v3 OK')"
```

## 5. Differential run against the Go bridge (mandatory — this is 009's validation data)

The Go bridge is the oracle. Run the same scripted extension double and the same HTTP
client against both servers and diff the answers.

1. Start the Go bridge on a **non-default** port so it cannot collide with anything:
   ```powershell
   cd C:\ProjectsC\RandomPr\asbplayer\scripts\web-socket-server
   $env:HOST="127.0.0.1"; $env:PORT="8769"; go run main.go
   ```
   (If Go is unavailable on the gate machine, say so explicitly in the gate note and
   escalate — do **not** silently skip this section.)
2. Start the Python bridge on another free port from a REPL or the test helper, with the
   same `ANKI_CONNECT_URL` pointed at the same stub AnkiConnect used in §2.
3. Connect the same scripted WebSocket double to both, then, for each of the ten routes in
   research.md R1.4 and each AnkiConnect branch in R3, issue the identical request to both
   and compare **status code and response body**.

Expected: identical status and body for every route and every branch (SC-004). Differences
are permitted **only** where research.md R4 (G-1..G-6) records a deliberate divergence, and
each such difference must be named in the gate note with its G-number. Any unexplained
difference fails the gate.

## 6. Real-machine observation with the actual extension (mandatory, once)

Research O-1: nothing before this step has involved the real client.

1. Stop the Go bridge. Start Katagiri normally; confirm the startup log says the bridge is
   hosted in-process and names the loopback bind.
2. Open a streaming page with the asbplayer extension configured to use
   `ws://127.0.0.1:8766/ws`. Confirm the connection is accepted (the bridge logs a client
   connect) and stays up across at least one keepalive interval.
3. Call `media_now` and `media_context` through an MCP client; confirm real bound media, a
   real subtitle window, and — with the patched extension build — a live anchor.
4. Mine one card with the extension's AnkiConnect URL pointed at `http://127.0.0.1:8766`;
   confirm the card lands in Anki with asbplayer media attached.
5. Stop Katagiri; confirm port 8766 is released. Start it again immediately; confirm the
   rebind succeeds (research.md O-2) and the extension reconnects.

Record every observation in the gate note. A failure here is a real failure — this step
exists because nothing else exercises the true client.

## 7. Loopback and hardening

```bash
uv run pytest tests/test_mcp_tools.py -q -k "security_scan or serves_stdio"
```

Then, with Katagiri running (from §6), confirm the live binding:

```powershell
Get-NetTCPConnection -LocalPort 8766 -State Listen | Select-Object LocalAddress, LocalPort
```

Expected: `127.0.0.1` (and/or `::1`) only — never `0.0.0.0`. The registered-tool count must
be unchanged from pre-009 and `server.run(transport="stdio")` must still be the only
transport call.

## 8. Repository invariants — the one authorized change, and nothing else

```bash
uv run pytest tests/test_bverify.py tests/test_cverify.py -q
git diff main -- tests/test_bverify.py tests/test_cverify.py
```

Expected: green, and the diff containing **exactly** the allowlist line(s) named by the
approved ledger row — `asbplayer_bridge.py` added to `HTTP_SERVER_ALLOWLIST` and (per
plan.md design decision 2) to `HTTP_CLIENT_ALLOWLIST` — plus the provenance comment citing
that row. Any other change in either file, any other module gaining a server construct, or
an allowlist edit with **no** ledger row behind it: the gate fails and the question goes to
the user (D-47 precedent).

Also confirm the Go path is gone. Grep for actual spawn machinery rather than the literal
strings `go run`/`main.go` — research.md R1 mandates roughly 50 `main.go:NNN` line-citation
comments in `asbplayer_bridge.py` as provenance for the protocol transcription, and those
citations are exactly what the literal-string grep falsely fires on; the real invariant is
that nothing spawns a Go process anymore:

```bash
git grep -n "subprocess\|shutil\.which\|Popen\|go run" -- src/katagiri | cat
```

Expected: no output (SC-008).

## 9. Full regression, and no leaked listener

```bash
uv run pytest -n auto --dist loadgroup
```

Expected: green, wall-clock within noise of the pre-009 baseline. Then, with no Katagiri
running:

```powershell
Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
```

Expected: nothing (SC-006). A listener surviving the suite means a test leaked one, which
is a failure even if every assertion passed.

## Rollback

009 is removable in one step, and the old path is still on disk: revert
`src/katagiri/asbplayer_launch.py` and `src/katagiri/mcp_server.py`'s startup block, delete
`src/katagiri/asbplayer_bridge.py` and its test file, drop the `aiohttp` line from
`pyproject.toml`, and revert the two allowlist entries. The Go checkout at
`C:/ProjectsC/RandomPr/asbplayer` is untouched by this feature and still runs. Nothing
persists — no config key added, no table, no file on disk — so a rollback leaves no
residue beyond the ledger row, which stays as history.
