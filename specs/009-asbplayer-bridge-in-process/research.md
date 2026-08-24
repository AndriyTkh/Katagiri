# Research & Decisions: 009 — asbplayer Bridge, In-Process

Scouted 2026-08-24 against the Katagiri repository at `main` (HEAD `342f2fe`) and against
the asbplayer checkout at `C:/ProjectsC/RandomPr/asbplayer`, HEAD `37495e22`
("fix(web-socket-server): bind to 127.0.0.1 by default via HOST env", 2026-08-23) on top of
upstream tag `1.20.2` (`570f441d`). The bridge source is a single file,
`scripts/web-socket-server/main.go`, 490 lines. **All line citations below are into that
file at that commit.** Findings that could not be settled without running the real
extension are recorded as **open items** at the bottom rather than guessed at.

---

## R1. The protocol contract (this is the authority for the reimplementation)

### R1.1 Transport and framing

| Fact | Source |
|---|---|
| One route upgrades: `GET /ws` | `main.go:479` |
| Upgrader accepts **any** Origin (`CheckOrigin` always true) | `main.go:23-25` |
| gorilla/websocket permits one concurrent writer per connection, so every write is serialized behind a per-client mutex | `main.go:21-22, 28-31, 78-82` |
| All server→client traffic is **text** frames carrying JSON | `main.go:141` |
| Client set is a plain map guarded by one shared mutex; add/remove log a line | `main.go:84-96` |
| Read loop: any read error breaks and disconnects the client | `main.go:110-116` |
| App-level keepalive: a text message equal to the ASCII string `PING` is answered with the text `PONG`, and is **not** parsed as JSON | `main.go:118-119` |
| Any other message is `json.Unmarshal`ed into `{command, messageId, body}`; on error it is **silently dropped**, on success it is pushed onto one shared response channel | `main.go:120-125` |

### R1.2 Command envelope and correlation

- Server→client command: `{"command": <string>, "messageId": <uuid v4 string>, "body": <object>}`
  (`main.go:56-60`; UUID from `uuid.NewString()`, e.g. `main.go:244`).
- Client→server response: `{"command": <string>, "messageId": <same>, "body": <raw JSON>}`
  (`main.go:61-65`). Only `messageId` and `body` are consumed.
- `publishMessage` **broadcasts** the marshalled command to every connected client
  (`main.go:131-145`). There is no per-client addressing.
- `publishMessageAndAwaitResponse` publishes, then loops reading the shared response
  channel until a response's `MessageId` equals the command's, forwarding it to a
  per-request channel; **`time.After(5 * time.Second)`** closes the per-request channel
  and gives up (`main.go:147-168`). A publish error also closes it immediately
  (`main.go:150-153`).
- A closed channel (`ok == false` at the receive) is the caller's signal for "no answer" and
  every caller turns it into `echo.NewHTTPError(http.StatusInternalServerError, nil)`
  (`main.go:271-273, 333-335, 364-366, 378-380, 406-408, 423-425`).

### R1.3 The six server→client commands

| Command | Body | Triggered by | Reply consumed? | Source |
|---|---|---|---|---|
| `mine-subtitle` | `{"fields": <note.fields>, "postMineAction": <int>}`, plus `"noteId": <int64>` when action 2 and the id could be parsed | `POST /` addNote intercept | only in the non-2 branch (`{"published": bool}`) | `main.go:244-247, 254, 275-281` |
| `load-subtitles` | `{"files": [{"name","base64"}, …]}` | `POST /asbplayer/load-subtitles` | reply awaited, body discarded | `main.go:326-338` |
| `seek-timestamp` | `{"timestamp": <float seconds>}` plus `"mediaId"` only when non-empty | `POST /asbplayer/seek` | reply awaited, body discarded | `main.go:351-369` |
| `get-bound-media` | `{}` | `GET /asbplayer/bound-media` | body relayed verbatim | `main.go:373-383` |
| `get-subtitles` | `{}` plus `"mediaId"` (non-empty query) and `"trackNumbers"` (`[]int`, parsed from a comma list, only if at least one parsed) | `GET /asbplayer/subtitles` | body relayed verbatim | `main.go:385-411` |
| `get-playback-state` | `{}` plus `"mediaId"` when non-empty | `GET /asbplayer/playback-state` | body relayed verbatim | `main.go:413-428` |

**Units caution** (already burned once, see docs/superpowers/plans/playback-state-live-anchor.md):
`seek-timestamp` carries **seconds** as a float; `get-playback-state`'s reply carries
**integer milliseconds** (`timestampMs`). Do not normalize them to one unit.

### R1.4 The HTTP surface — all ten routes

Registered at `main.go:479-488`, in this order:

| # | Method + path | Behavior | Source |
|---|---|---|---|
| 1 | `GET /ws` | WebSocket upgrade | `main.go:479, 98-129` |
| 2 | `POST /disconnect-ws-clients` | closes and forgets every client, logs each; returns 200 with an empty body | `main.go:480, 430-439` |
| 3 | `GET /` | AnkiConnect passthrough (see R2) | `main.go:481, 212-225` |
| 4 | `POST /` | AnkiConnect proxy + `addNote` intercept (see R2/R3) | `main.go:482, 227-286` |
| 5 | `POST /asbplayer/load-subtitles` | body `{"files":[{"name","base64"}]}` → `load-subtitles`; 400 on unparsable body; 500 on no answer; else **200 with an empty string body** | `main.go:483, 316-339` |
| 6 | `POST /asbplayer/seek` | body `{"timestamp": float, "mediaId": string}` → `seek-timestamp`; same 400/500/200-empty shape | `main.go:484, 341-370` |
| 7 | `GET /asbplayer/bound-media` | → `get-bound-media`; **200 + reply body as raw JSON blob** | `main.go:485, 372-383` |
| 8 | `GET /asbplayer/subtitles` | query `mediaId`, `trackNumbers` → `get-subtitles`; 200 + raw JSON blob | `main.go:486, 385-411` |
| 9 | `GET /asbplayer/playback-state` | query `mediaId` → `get-playback-state`; 200 + raw JSON blob | `main.go:487, 413-428` |
| 10 | `OPTIONS /` | forwarded to AnkiConnect with an empty body (CORS preflight passthrough) | `main.go:488, 311-314` |

Note the relay endpoints always answer **200** when *any* matching reply arrives, even if
that reply's body is an error object — the bridge never inspects it. `media_asbplayer.py`
already relies on this (`reply.get("error")` handling at `media_asbplayer.py:506`).

No CORS headers are generated by the bridge itself; there is no CORS middleware
(`main.go:451-470` installs only a request logger). Whatever CORS the extension sees on
`/` comes from AnkiConnect through the header copy.

### R1.5 Configuration surface

`main.go:441-449`, all via environment (`godotenv` autoload reads a `.env` next to the
binary — `main.go:16`; `.env.example` documents the same six):

| Env var | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | bind address — **the local commit `37495e22` change**; upstream 1.20.2 bound `:PORT`, i.e. all interfaces |
| `PORT` | `8766` | listen port |
| `ANKI_CONNECT_URL` | `http://127.0.0.1:8765` | proxy upstream |
| `POST_MINE_ACTION` | `2` | 0 none / 1 open dialog / 2 update last card / 3 export card |
| `INTERCEPT_FIELD` | `""` | note field to match for interception |
| `INTERCEPT_VALUE` | `""` | required value of that field |

The startup banner prints all six (`main.go:448-449`) — none is a secret.

---

## R2. The AnkiConnect proxy (the part Katagiri never calls, and must not break)

`forwardToAnkiConnect` (`main.go:170-198`) is the whole passthrough:

1. Build a new request with the **same method** and the buffered body to `AnkiConnectUrl`
   (`main.go:171`).
2. **Copy every request header verbatim** — `for key, values := range c.Request().Header { ankiConnectRequest.Header[key] = values }` (`main.go:173-175`). Note this assigns the
   slice into the canonicalized header map directly; there is no filtering, no hop-by-hop
   removal, no Host rewrite beyond what `http.NewRequest` does from the URL.
3. `http.DefaultClient.Do` (`main.go:181`) — no timeout at all.
4. Read the whole upstream body, **add every upstream response header** to the reply
   (`main.go:190-194`), then write the body with the upstream status code
   (`main.go:196`).
5. Return the raw upstream body bytes to the caller, which is how the `addNote` branch gets
   the note id.

`GET /` (`main.go:212-225`) is *not* the same code path: it does
`http.Get(fmt.Sprintf("%s/%s", AnkiConnectUrl, c.Path()))`. With `c.Path() == "/"` that
builds `http://127.0.0.1:8765//` — see G-1.

`OPTIONS /` (`main.go:311-314`) calls `forwardToAnkiConnect` with an **empty** body buffer.

---

## R3. The `addNote` intercept, branch by branch

`handlePostRequest`, `main.go:227-286`:

1. Read and buffer the body; `json.Unmarshal` into `{action, params}` (`main.go:228-235`).
   Unparsable → **400** with the error (`main.go:234`).
2. Stash `action` in the echo context for the request logger (`main.go:237`).
3. **Pass-through condition** (`main.go:239`): if `action != "addNote"` **or** there are
   **zero** connected WS clients **or** `shouldInterceptAddNote(...)` is false → plain
   forward, return.
4. `shouldInterceptAddNote` (`main.go:288-309`): an empty `INTERCEPT_FIELD` **or** empty
   `INTERCEPT_VALUE` ⇒ **intercept everything** (true). Otherwise `params.note.fields` must
   be an object, the named field must be a string, and it must equal `INTERCEPT_VALUE`;
   any type mismatch ⇒ false (do not intercept).
5. Build the command: `{"fields": params["note"]["fields"], "postMineAction": <int>}`
   (`main.go:244-247`). **This is an unchecked type assertion** — see G-3.
6. **`POST_MINE_ACTION == 2`** (`main.go:249-265`): forward to AnkiConnect **first**; if
   that succeeded, parse `{"result": <int64>}` out of the response
   (`extractNoteIdFromAnkiConnectResponse`, `main.go:200-210`; a null/absent/unparsable
   result just omits the id) and set `command.Body["noteId"]`; then `publishMessage`
   **without awaiting** (a publish failure is printed, not returned); return the forward's
   error. The HTTP response the caller sees is AnkiConnect's own.
7. **Any other action value** (`main.go:267-285`): publish and await. No answer (channel
   closed) → **500**. An answer whose body fails to unmarshal into `{"published": bool}`,
   **or** `published == false` → **forward the original request to AnkiConnect after all**
   (`main.go:278-281`). `published == true` → answer **HTTP 200 with the JSON body `-1`**
   (`main.go:283`), never forwarding.

That last shape — a bare `-1` as the whole JSON body — is what the extension reads as "the
note was handled by asbplayer, here is a fake note id".

---

## R4. Behaviors in the Go source that are bugs, not contract

Reproducing these faithfully would be wrong. Each is called out so an implementer does not
"match the reference" into a defect.

- **G-1 — double slash on `GET /`.** `main.go:213` builds `<url>//` because `c.Path()`
  already starts with `/`. AnkiConnect tolerates it. The replacement should forward `GET /`
  to the AnkiConnect URL itself. Behavior visible to a client is unchanged.
- **G-2 — `Content-Type` index panic.** `main.go:196` and `main.go:221` index
  `Header["Content-Type"][0]` with no length check; an upstream response without that
  header panics the handler. The replacement must fall back to a sane default (or omit the
  header) instead.
- **G-3 — unchecked type assertion on the note body.** `main.go:245` does
  `request.Params["note"].(map[string]interface{})["fields"]` with no `, ok`; an `addNote`
  whose `params.note` is not an object panics *after* `shouldInterceptAddNote` has already
  returned true (which it does whenever `INTERCEPT_FIELD` is empty — the default). The
  replacement must treat a malformed note as "do not intercept, forward it".
- **G-4 — one shared response channel.** `main.go:38, 123, 157` route every client reply
  through a single unbuffered channel that each waiter drains in a loop; a reply belonging
  to request A can be consumed and discarded by request B's loop (B sees a non-matching id
  and simply loops again, having eaten the message). With one in-flight request at a time —
  which is what `media_asbplayer.py` does (`AsbplayerClient`: "one outstanding request at a
  time") — it never bites. The replacement must use a **per-`messageId` registry** so
  concurrent requests are safe; that is a strict improvement, not a contract change.
- **G-5 — the request logger prints request bodies.** `main.go:461-469` re-reads the body
  in the logger and prints parsed AnkiConnect fields to stdout. Katagiri logs to stderr,
  never stdout (stdout corrupts MCP stdio), and must not log note contents at all
  (spec FR-017). Log metadata only.
- **G-6 — no client timeout on the AnkiConnect leg.** `http.DefaultClient` (`main.go:181`)
  has no timeout, so a wedged AnkiConnect wedges the handler forever. The replacement
  should carry a bounded timeout; the only observable difference is that a hung upstream
  eventually produces an error instead of hanging, which is strictly better and matches
  Katagiri's house style everywhere else.

---

## R5. What Katagiri itself uses today

- `src/katagiri/media_asbplayer.py` is the **client** half: `http.client.HTTPConnection` to
  `127.0.0.1:8766`, one GET at a time, 2 s timeout (`media_asbplayer.py:141-143, 242-315`),
  over exactly three of the ten routes — `/asbplayer/bound-media`,
  `/asbplayer/subtitles`, `/asbplayer/playback-state` (`_COMMAND_PATHS`,
  `media_asbplayer.py:183-187`). `PROTOCOL_SURFACE_VERSION = 3`
  (`media_asbplayer.py:172`). **Katagiri never calls the other seven routes** — they exist
  for the extension and for the bridge's own CLI helpers. That asymmetry is the reason
  US2's mining story needs its own tests: no Katagiri test would ever notice it break.
- `src/katagiri/asbplayer_launch.py` (135 lines) is the **launcher**: `bridge_is_healthy()`
  (any HTTP answer on `GET /` counts — `asbplayer_launch.py:35-49`),
  `bridge_port_is_occupied()` (bare `socket.create_connection`, 0.2 s —
  `asbplayer_launch.py:52-58`), and `ensure_asbplayer_bridge()` which refuses to act on an
  occupied-but-unhealthy port, requires `asbplayer_bridge_dir` to contain `main.go`,
  requires `go` on PATH, and spawns `go run main.go` with `HOST=127.0.0.1` seeded via
  `env.setdefault` and `CREATE_NO_WINDOW` on Windows (`asbplayer_launch.py:61-134`).
- `src/katagiri/mcp_server.py:2011-2063` is the only caller: at startup it calls
  `ensure_asbplayer_bridge()` once, and on `already_running` runs `security_scan((8766,))`
  to confirm the reused bridge is loopback-only before saying so in the log. Port 8766 also
  appears in the hardening surface at `mcp_server.py:457, 464, 1015-1016`.
- `src/katagiri/installer.py` mentions asbplayer only as an 008 browser-**extension**
  companion row (`installer.py:774, 778, 1311`) — 008 deliberately added no bridge row
  (008 research.md R4), so 009 inherits no doctor contract it must preserve.

### Existing tests that pin current behavior

| File | What it pins | Verdict for 009 |
|---|---|---|
| `tests/test_asbplayer_launch.py` (7 tests, 128 lines) | the whole Go-spawn contract: reuse-healthy, leave-occupied-alone, unconfigured message, missing `main.go`, missing Go, `["go","run","main.go"]` argv with `cwd`/DEVNULL/`HOST=127.0.0.1`, explicit `HOST` preserved | **Rewritten.** Five of seven tests assert Go-specific behavior that ceases to exist. Reuse-healthy and leave-occupied-alone survive in spirit and must be re-asserted against the new implementation. |
| `tests/test_media_asbplayer.py` | the client half, entirely against scripted doubles | **Unchanged** — and SC-003 makes that a requirement, not a hope. |
| `tests/test_everify.py` | Phase-E scenarios B/D/F through `AsbplayerChannel` with scripted doubles | **Unchanged.** |
| `tests/test_mcp_tools.py:2594-2612` | `security_scan` reporting 8766 loopback-only from scripted `netstat` output | **Unchanged** (scripted input, not a live port). |
| `tests/test_mcp_tools.py:2823-2851` (`test_main_serves_stdio_and_nothing_else`) | `main()` calls `ensure_asbplayer_bridge` exactly once, stdout stays empty | **Adapted** — the monkeypatch target and `BridgeLaunchResult` construction stay valid if the function keeps its name and shape; the assertion that startup touches no stdout becomes *more* load-bearing once a server runs in-process. |
| `tests/test_skeleton.py:115, 146` | `asbplayer_bridge_dir` is a known config key and its commented template line | **Unchanged** — which is exactly why FR-014 keeps the key loadable instead of deleting it. |
| `tests/test_bverify.py`, `tests/test_cverify.py` | `HTTP_SERVER_ALLOWLIST = {"media_mokuro.py"}`, `HTTP_CLIENT_ALLOWLIST = {obsidian_proxy, asbplayer_launch, media_asbplayer, irodori_import, vendor_fetch}` | **Changed, but only by user decision** — see R7. |
| `tests/test_media_channel.py` | channel precedence, no bridge involvement | **Unchanged.** |

---

## R6. Hosting stack — how a Python process serves WS *and* HTTP on one port

The hard constraint: **port 8766 must carry both** the WebSocket upgrade on `/ws` and nine
plain HTTP routes, three of which have request bodies. Katagiri's runtime dependencies
today are `fugashi`, `mcp`, `pypdf`, `tzdata` (`pyproject.toml:7-12`) — nothing that speaks
WebSocket.

| Option | Verdict | Why |
|---|---|---|
| **stdlib only** — `http.server` + a hand-written RFC 6455 handshake and frame codec | **Rejected** | The client is a real browser WebSocket: masked client frames, fragmentation, control frames, close handshake, payloads past 64 KiB (`load-subtitles` base64). Hand-writing that is *reimplementing OSS*, which constitution II forbids in as many words, and it is the highest-risk part of the feature for zero dependency saving. |
| **`websockets` alone** | **Rejected** | Its server's `process_request` hook sees headers but not request bodies; three of the endpoints are POSTs with bodies. It cannot serve the HTTP half. |
| **`websockets` sans-io (`ServerProtocol`) over `asyncio.start_server`, with hand-written HTTP/1.1 parsing** | **Rejected (viable but worse)** | Correct framing from a library, but we then own HTTP/1.1 parsing, chunked bodies, and keep-alive — trading one hand-rolled protocol for another. Recorded as the fallback if the chosen option ever becomes unavailable. |
| **`aiohttp`** | **Chosen** | One dependency covers both halves: `web.Application` routes map 1:1 onto echo's ten routes, `web.WebSocketResponse` handles the upgrade and framing on the same port, and `Request.read()` gives the exact body bytes the passthrough needs. Pure-Python-plus-wheels, cp312 Windows wheels published, widely deployed, actively maintained. |

- **Decision**: add **`aiohttp`** as the single new runtime dependency, declared in
  `pyproject.toml`. This is a real change to the dependency surface and TG1's ledger row
  must record it (constitution II is satisfied — we are integrating OSS rather than
  reimplementing a protocol — but the vendoring/pinning posture of D-10 means the choice is
  a decision, not a detail).
- **Outbound AnkiConnect leg**: `http.client`, in a worker thread
  (`asyncio.to_thread`), *not* `aiohttp`'s client. Rationale: FR-007 demands byte-exact
  header and body passthrough, and `http.client` is the house pattern already used by
  `media_asbplayer.py` and `asbplayer_launch.py`; an async client that manages `Host`,
  `Content-Length`, and hop-by-hop headers on your behalf is the wrong tool for a
  passthrough. **Consequence**: the new module matches `HTTP_CLIENT_PATTERNS` as well as
  `HTTP_SERVER_PATTERNS`, so it needs an entry in **both** allowlists (R7).
- **Threading model**: the MCP server's `main()` is synchronous and ends in
  `server.run(transport="stdio")`. The bridge therefore runs its own asyncio loop on a
  dedicated daemon thread, started from `ensure_asbplayer_bridge()`, with an explicit
  `stop()` that closes the site and joins the thread (FR-012 — tests must never leak a
  listener).
- **Rejected**: keeping the bridge as a *separate Python child process*. It would preserve
  the Go bridge's independent lifetime (the one thing 009 loses, spec US3) but reintroduces
  process management, a second entry point, orphan cleanup on Windows, and a health-check
  race — all the machinery this feature exists to delete. Recorded here because it is the
  obvious counter-proposal and the answer is "no, deliberately".

---

## R7. Governance — the allowlist question is a user decision, not an implementation choice

`tests/test_bverify.py:301` and `tests/test_cverify.py:688` both read
`HTTP_SERVER_ALLOWLIST = frozenset({"media_mokuro.py"})`, and
`test_the_package_ships_no_http_server_construct_anywhere`
(`test_bverify.py:621-644`) fails on any package module matching `HTTP_SERVER_PATTERNS` —
which include a bare `\baiohttp\b`, `.bind((`, `.listen(`, and
`start_server|create_server` (`test_bverify.py:212-225`). A new bridge module trips this
by construction. Likewise `HTTP_CLIENT_ALLOWLIST` (`test_bverify.py:283-291`,
`test_cverify.py:668-676`) gates `http.client`.

- **Precedent**: `media_mokuro.py` holds the *only* server exemption, and its allowlist
  comment (`test_bverify.py:293-300`) records the argument — a browser userscript has no
  IPC pipe, so a guarded loopback listener is the only transport available. The asbplayer
  extension is in exactly the same position, and the exemption argument is the same shape.
  **D-47** is the process precedent for the client side: two pre-007 fetchers needed an
  `HTTP_CLIENT_ALLOWLIST` entry and it was taken to the **user** for a decision and a
  ledger row rather than edited in.
- **Decision**: this feature does **not** pre-approve either allowlist edit. TG1's
  governance task files the ledger row *and* raises the allowlist additions as an explicit
  **ESCALATION to the user**. No implementation task may edit `test_bverify.py` or
  `test_cverify.py`; the edit lands in one dedicated serial task that is blocked until the
  user's decision is recorded in the ledger. If the answer is no, the feature stops and is
  re-planned — it cannot be worked around.
- **Next D-number**: the ledger's last row today is **D-49** (008's), so 009's row is
  expected to be **D-50** — re-confirm at execution time
  (`grep '^| D-' docs/decisions-ledger.md | tail -3`). 007's T001 drafted D-39 and landed
  as D-46; a stale draft number is the norm.
- **No constitution amendment is proposed.** Principle VI's "stdio-only MCP transport, no
  network listener" is about the MCP transport and already coexists with
  `media_mokuro.py`'s bridge; if the user's decision on the allowlist comes with a view
  that the principle text should name this second exception, that is a separate amendment
  filed the normal way (ledger row first).

---

## R8. Upstream state (re-check before shipping — tasks.md carries this as a task)

Recorded in specs/README.md's planned entry from the 2026-08-24 audit: the repository moved
to `github.com/asbplayer/asbplayer`; **1.20.2 is still the latest release**; issue **#1087**
is an **open, unmerged proposal** (WS media targeting, note updates, event subscription,
capability handshake) with **no linked PRs and no overlap** with the playback-state work
that local commit `37495e22` carries. No canonical WebSocket protocol document could be
fetched, so "no other drift" is best-effort from release notes.

Why this must be re-checked and not trusted: 009 *freezes* a protocol transcription. If
#1087 (or any successor) merges and ships a capability handshake, a bridge that does not
answer it becomes a bridge the extension declines to talk to. The re-check task must
report: latest release tag, whether #1087 or a successor merged, and whether
`scripts/web-socket-server/main.go` changed upstream since `570f441d`. **A merged protocol
change is a stop-and-replan trigger, not something to absorb quietly.**

---

## Open items (not resolved — do not treat as decided)

- **O-1 — The real extension has never been tested against a Python bridge.** Every claim
  here is read off the Go source and off `media_asbplayer.py`'s client behavior. The
  extension's own expectations (reconnect cadence, whether it tolerates a differently
  ordered header set, whether it sends protocol-level pings) are unverified. The TG3 gate's
  real-machine step is the only thing that closes this, and it is why that step is
  mandatory rather than optional.
- **O-2 — Windows rebind behavior.** Whether a fast MCP-server restart can hit a refused
  rebind on 8766 (`TIME_WAIT`, or Windows' `SO_EXCLUSIVEADDRUSE` default) was not measured.
  The implementation must decide `SO_REUSEADDR` deliberately and the gate must exercise a
  stop/start cycle.
- **O-3 — `POST_MINE_ACTION` values 0/1/3 are untested end-to-end.** The Go code's only
  branch point is `== 2` vs. everything else, and the default is 2, so the non-2 path may
  never have run on this machine. The reimplementation must implement the branch as written;
  whether the extension actually answers `{"published": …}` in those modes is unverified.
- **O-4 — Header passthrough fidelity is asserted against a stub, not against Anki.** The
  Go code copies the header map wholesale, including hop-by-hop headers that a Python
  client may refuse to send verbatim (`Connection`, `Transfer-Encoding`,
  `Content-Length`). Which headers actually survive, and whether AnkiConnect cares, is
  unverified; the differential run (SC-004) is the intended way to find out.
- **O-5 — Losing the bridge's independent lifetime.** Spec US3 documents it; nobody has
  measured how often the learner mines while Katagiri is *not* running. If that turns out
  to be common, the rejected "separate Python child process" option in R6 becomes live
  again. Deliberately not solved here.
- **O-6 — `aiohttp` is a new dependency with transitive dependencies.** Its own dependency
  tree (`multidict`, `yarl`, `frozenlist`, `aiosignal`, `propcache`, `attrs`) was not
  audited against D-10's vendoring/pinning posture. TG1's ledger row should record the
  pinned range actually chosen, and the gate should confirm `uv sync` resolves it on this
  machine without a build step.
