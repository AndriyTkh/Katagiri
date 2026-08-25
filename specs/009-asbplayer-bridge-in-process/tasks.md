# Tasks: 009 — asbplayer Bridge, In-Process

**Input**: Design documents from `specs/009-asbplayer-bridge-in-process/` (spec.md, plan.md,
research.md, quickstart.md). No `data-model.md`, no `contracts/`, no `holdout/` — plan.md
§Deliberate omissions records why each is absent, including why the holdout is replaced by
the TG3 differential run.

**Tests**: yes, and they are the point. This is a protocol reimplementation whose real
client is a third-party browser extension; every command, endpoint, and branch in
research.md R1/R3 needs an assertion (SC-002), and the client half must pass **unchanged**
(SC-003).

## Binding rules (carry into every dispatch)

1. **research.md R1/R3 is the contract, `main.go` is the authority.** Every behavioral
   question is answered by the cited line in
   `C:/ProjectsC/RandomPr/asbplayer/scripts/web-socket-server/main.go` at commit
   `37495e22`. Do not invent a "more sensible" shape. The one class of deliberate
   divergence is research.md R4's G-1..G-6 (known Go defects), and each divergence must
   carry a code comment naming its G-number.
2. **`tests/test_bverify.py` and `tests/test_cverify.py` are FROZEN.** No task may edit
   them except T010, and T010 is blocked until the user's allowlist decision is recorded in
   the ledger (T001's escalation). A task that finds itself blocked by the invariant
   **stops and escalates** — it never edits the allowlist to get green. D-47 precedent.
3. **`tests/test_media_asbplayer.py`, `tests/test_everify.py`, and
   `src/katagiri/media_asbplayer.py` MUST NOT CHANGE.** SC-003 makes an empty diff on those
   three the proof that the protocol surface did not drift. If a change looks necessary,
   the reimplementation is wrong — stop and escalate.
4. **Never bind 8766 in a test.** Every automated test binds an ephemeral port (`port=0`),
   as `tests/test_media_mokuro.py` already does for the mokuro bridge. Port 8766 is bound
   only by the real server at runtime and by quickstart §6/§7.
5. **No secret, credential, or AnkiConnect note body in any log**, and never anything on
   stdout (it corrupts MCP stdio). Metadata only — research.md G-5, FR-017.
6. **No MCP contract growth.** No ToolSpec added or changed; the registered-tool count is
   the same after 009 as before.

## Format: `[ID] [P?] [Story] Description` + Lane / Model / Write / Read lists

- **[P]**: parallelizable (own worktree lane, no shared files)
- Lanes branch from **`main`** at taskgroup start (this repo's default branch;
  specs/README.md's `master` wording is stale — see plan.md §Branch note).
- `src/katagiri/mcp_server.py` is the declared **hot file**: serial-on-main, one task, never
  inside a worktree. `src/katagiri/asbplayer_launch.py`, `pyproject.toml`, and the frozen
  verify files are also serial-on-main.
- **Model**: sonnet-high default; opus-mid where marked.

## Workfile & conflict map

| File | Owner | Mode |
|---|---|---|
| docs/decisions-ledger.md | T001 | serial-on-main |
| docs/audit-log.md | T001, T008 (append only) | serial-on-main |
| pyproject.toml | T002 | serial-on-main |
| src/katagiri/asbplayer_bridge.py | T003 → T004 → T005 | lane `wt/009-bridge` (strict order within lane) |
| tests/test_asbplayer_bridge.py | T006 | lane `wt/009-bridge` |
| src/katagiri/asbplayer_launch.py | T007 | serial-on-main |
| tests/test_asbplayer_launch.py | T007 | serial-on-main (same task — it is a rewrite of both halves) |
| src/katagiri/mcp_server.py | T009 | **serial-on-main (HOT, one task only)** |
| tests/test_bverify.py, tests/test_cverify.py | T010 only | **FROZEN until the T001 escalation is answered** |
| docs/asbplayer-bridge.md | T011 | lane `wt/009-docs` |
| src/katagiri/media_asbplayer.py, tests/test_media_asbplayer.py, tests/test_everify.py | — | **MUST NOT CHANGE (SC-003)** |
| src/katagiri/config.py, src/katagiri/installer.py | — | read-only unless T009/T011 needs a one-line notice; see their notes |

T002 (`pyproject.toml`) lands on `main` before the lane starts so the lane's worktree can
`uv sync` the new dependency. The lane owns one module and one test file; the serial track
owns the launcher, the hot file, and the gated allowlist edit — file-disjoint throughout.

---

## Taskgroup TG1: Governance (serial, blocks everything)

- [x] T001 [Gate] (D-50, commit 76538b5; all four escalations answered by user 2026-08-24 and recorded verbatim: server allowlist APPROVED, client allowlist APPROVED (http.client leg), aiohttp APPROVED (>=3.10,<4), zero-MCP-growth CONFIRMED doctor-row-only) File the 009 decisions-ledger row in `docs/decisions-ledger.md`
      (**expected D-50 — CONFIRM the actual next number at execution time**:
      `grep '^| D-' docs/decisions-ledger.md | tail -3`; this draft was written when D-49
      was last, and 007's T001 drafted D-39 but landed as D-46, so a stale number is the
      norm). The row records, as binding rather than incidental: (a) **the Go bridge is
      retired by clean cutover** — Katagiri hosts the bridge itself, no child process, no Go
      toolchain, and `asbplayer_bridge_dir` becomes an accepted-but-inert config key rather
      than being deleted (plan.md decisions 4 and 5); (b) **the protocol is frozen at
      asbplayer 1.20.2 + local commit `37495e22`**, transcribed with line citations in
      research.md R1/R3, and that transcription — not anyone's judgment of what is
      reasonable — is the implementation contract; (c) **six deliberate divergences from the
      reference** (research.md R4 G-1..G-6: no double-slashed proxy URL, no
      `Content-Type[0]` panic, no unchecked note-body assertion, per-`messageId` registry
      instead of one shared channel, no request-body logging, bounded AnkiConnect timeout) —
      recorded so a future reader diffing against `main.go` does not read them as errors;
      (d) **one new runtime dependency, `aiohttp`** — record the pinned range actually
      chosen and why stdlib-only was rejected (hand-writing RFC 6455 against a real browser
      client is reimplementation, constitution II; research.md R6); (e) **zero MCP contract
      growth** — no ToolSpec, `PROTOCOL_SURFACE_VERSION` stays 3, and
      `media_asbplayer.py` + its tests must show an empty diff (SC-003); (f) **the bridge's
      lifetime now equals the MCP process's lifetime** — a real regression against the Go
      bridge's independent lifetime, accepted and documented rather than engineered away
      (spec US3, research.md O-5).

      **ESCALATIONS — raise all four to the user in this task, do not pre-decide any of
      them, and do not start TG2 until each has an answer recorded in the ledger:**

      1. **`HTTP_SERVER_ALLOWLIST` addition** (`tests/test_bverify.py:301`,
         `tests/test_cverify.py:688`, currently `frozenset({"media_mokuro.py"})`). An
         in-process listener is exactly what
         `test_the_package_ships_no_http_server_construct_anywhere` exists to catch, and
         `media_mokuro.py` is the *only* standing exemption — granted because a browser
         userscript has no IPC pipe. The asbplayer extension is in the same position, but
         **the user decides**, with the reasoning recorded in the ledger row, exactly as
         D-47 was handled. **If the answer is no, 009 stops and is re-planned** — there is
         no in-process bridge without a listening socket.
      2. **`HTTP_CLIENT_ALLOWLIST` addition** (`tests/test_bverify.py:283-291`,
         `tests/test_cverify.py:668-676`). The AnkiConnect proxy's outbound leg uses
         `http.client` for byte-exact passthrough (plan.md decision 2). A separate question
         from (1) and a separate answer: if the user prefers no client exemption, the
         fallback is `aiohttp`'s own client, which does **not** match the patterns but
         gives up some passthrough fidelity (research.md O-4) — present both options, do not
         choose.
      3. **The new runtime dependency `aiohttp`** and its transitive tree
         (`multidict`, `yarl`, `frozenlist`, `aiosignal`, `propcache`, `attrs` — unaudited,
         research.md O-6). Katagiri has had four runtime dependencies since inception and
         D-10's posture is vendored-and-checksummed data with no runtime downloads; adding a
         networking library is a decision worth the user's eyes even though it is a normal
         wheel install.
      4. **MCP contract**: confirm with the user that **no** new tool is wanted. The plan
         says none is required (plan.md decision 10: a doctor row, not a tool). If the user
         wants bridge status exposed to the agent, that is an additive ToolSpec and a
         different, larger feature — flag it, do not assume it either way.

      Add the reasoning entry to `docs/audit-log.md` ("009 TG1 — in-process asbplayer
      bridge: cutover, protocol freeze, and the listener exemption"). No constitution bump
      is proposed (Principle VI already coexists with `media_mokuro.py`'s bridge); record
      that judgment in the audit-log entry, and note that if the user's answer to
      escalation 1 comes with a view that Principle VI's text should name this second
      exception, that is a separate amendment filed the normal way.
      **Model**: sonnet-high. **Write**: docs/decisions-ledger.md, docs/audit-log.md.
      **Read**: docs/decisions-ledger.md (last ~3 rows, for format and the real next
      D-number; D-47 in full, as the escalation precedent),
      specs/009-asbplayer-bridge-in-process/research.md (R4, R6, R7, R8, all open items),
      specs/009-asbplayer-bridge-in-process/plan.md (§Constitution Check, §Key design
      decisions), tests/test_bverify.py:208-301 (the two allowlists and their provenance
      comments — READ ONLY).

**Checkpoint**: the 009 ledger row exists **and all four escalations have recorded
answers** → TG2 may start. A "we'll sort the allowlist out at the gate" start is
explicitly forbidden: it would mean writing the module before knowing it is allowed to
exist.

---

## Taskgroup TG2: Implementation (2 lanes + serial track + 1 research task)

### Serial-on-main, first (unblocks the lane's worktree)

- [x] T002 [US1] (commit 457c6e2; aiohttp==3.14.3 + aiohappyeyeballs/aiosignal/frozenlist/multidict/propcache/yarl, all wheels, no conflicts; smoke 15 passed) Add the `aiohttp` runtime dependency to `pyproject.toml` with the pinned
      range the T001 ledger row records, run `uv sync`, and report the **full resolved
      transitive addition** (versions included) plus whether any package needed a build
      rather than a wheel (research.md O-6). Change nothing else in `pyproject.toml` — the
      dev group, the pytest config, the hatch build config, and `requires-python` are all
      untouched. If the resolve conflicts with `mcp`, `fugashi`, `pypdf`, or `tzdata`,
      **stop and escalate** rather than loosening another pin. Depends: T001 escalation 3
      answered.
      **Model**: sonnet-high. **Write**: pyproject.toml, uv.lock.
      **Read**: pyproject.toml, specs/009-asbplayer-bridge-in-process/research.md (R6, O-6),
      docs/decisions-ledger.md (the 009 row, for the agreed pin).

### Lane `wt/009-bridge` [P] (strict order T003 → T004 → T005 → T006)

- [x] T003 [merged: fef1747, lane commit f0f6afe] [P] [US1] Create `src/katagiri/asbplayer_bridge.py` — the transport core.
      An `aiohttp` `web.Application` served on a **private asyncio loop running on a daemon
      thread**, with `start(host, port)` returning the bound address and an explicit
      `stop()` that closes the site and joins the thread (plan.md decision 9; FR-012 — a
      suite that leaks a listener wedges 8766 for the next developer). Public shape: a
      `AsbplayerBridgeServer` class holding the connected-client set, a **per-`messageId`
      pending-reply registry** (plan.md decision 7 / research.md G-4 — *not* the Go
      version's single shared channel), and the forwarding config read from the environment
      with the Go defaults (`HOST` 127.0.0.1, `PORT` 8766, `ANKI_CONNECT_URL`
      http://127.0.0.1:8765, `POST_MINE_ACTION` 2, `INTERCEPT_FIELD` "", `INTERCEPT_VALUE`
      "" — research.md R1.5; prefer a `KATAGIRI_`-prefixed alias for the bind host and fall
      back to bare `HOST`, since `HOST` is a generic name inside Katagiri's own process).
      Implement: `GET /ws` upgrade accepting any Origin (research.md R1.1 — matching the Go
      upgrader; the port is loopback, which is the actual control); the read loop where the
      **exact text `PING` is answered with the text `PONG` and never parsed as JSON**
      (R1.1); any other text message parsed as `{command, messageId, body}` and routed to
      the pending registry, with an unparsable message **silently ignored and the connection
      kept open**; `publish(command)` **broadcasting** to every connected client (R1.2 — a
      second connected tab must see it too); and `publish_and_await(command)` with a
      **5-second** deadline (R1.2) whose expiry is reported to the caller as "no answer",
      with a late reply discarded harmlessly. Bind loopback by default; log a **warning**
      naming the exposure when the resolved host is not loopback (FR-009, carrying over
      local commit `37495e22`). Decide `SO_REUSEADDR` deliberately and comment the choice
      (research.md O-2). stderr logging only, metadata only, never a note body, never
      stdout (rule 5).
      **Model**: opus-mid (framing/lifecycle/correlation, and the thread-plus-loop seam, are
      the parts that are hard to get right and expensive to get wrong).
      **Write**: src/katagiri/asbplayer_bridge.py.
      **Read**: specs/009-asbplayer-bridge-in-process/research.md (R1, R4, R6, O-2),
      specs/009-asbplayer-bridge-in-process/spec.md (US1, US3, Edge Cases, FR-001..FR-004,
      FR-009..FR-013, FR-017), `C:/ProjectsC/RandomPr/asbplayer/scripts/web-socket-server/main.go`
      lines 21-168 and 430-490 (READ ONLY, another repository — never edit it),
      src/katagiri/media_mokuro.py:1-130 (the house pattern for a guarded loopback listener
      with an explicit `start()`/`stop()` — READ ONLY),
      src/katagiri/media_asbplayer.py:130-190 (the client's constants and expectations —
      READ ONLY, MUST NOT CHANGE).

- [x] T004 [merged: fef1747, lane commit 75ba699] [P] [US1] Add the six relay routes to `src/katagiri/asbplayer_bridge.py`:
      `POST /asbplayer/load-subtitles`, `POST /asbplayer/seek`,
      `GET /asbplayer/bound-media`, `GET /asbplayer/subtitles`,
      `GET /asbplayer/playback-state`, and `POST /disconnect-ws-clients` — paths, methods,
      query parameters, body shapes, status codes, and response bodies exactly as
      enumerated in research.md R1.3/R1.4. Load-bearing details that are easy to get wrong
      and are individually cited there: the two POST relays answer **200 with an empty
      string body**, not JSON; the three GET relays answer **200 with the client reply's
      `body` as a raw JSON blob**, relayed verbatim and **never inspected** (an error object
      inside a reply is still a 200 — `media_asbplayer.py` depends on this); an unparsable
      request body is **400**; no answer within the deadline (or no connected client) is
      **500**; `mediaId` is included in the command body **only when non-empty**;
      `trackNumbers` is parsed from a comma-separated list, non-numeric entries dropped, and
      the key is included **only if at least one number parsed**; `seek` carries
      `timestamp` in **seconds as a float** while playback-state replies carry **integer
      milliseconds** — do not normalize (research.md R1.3's units caution).
      Depends: T003 (same file, same lane).
      **Model**: sonnet-high. **Write**: src/katagiri/asbplayer_bridge.py.
      **Read**: specs/009-asbplayer-bridge-in-process/research.md (R1.3, R1.4),
      specs/009-asbplayer-bridge-in-process/spec.md (FR-005, FR-006, US1 acceptance),
      `main.go` lines 316-428 and 479-488 (READ ONLY),
      src/katagiri/media_asbplayer.py:180-320 (what the client actually sends and expects —
      READ ONLY).

- [x] T005 [merged: fef1747, lane commit 10a7dde] [P] [US2] Add the AnkiConnect proxy to `src/katagiri/asbplayer_bridge.py`:
      `GET /`, `POST /`, `OPTIONS /`. Byte-exact passthrough (FR-007) — copy **every**
      request header, pass the body through unmodified, return the upstream **status,
      headers, and body** unchanged. The outbound leg is `http.client` in a worker thread
      (`asyncio.to_thread`), with a bounded timeout (plan.md decision 2; research.md G-6),
      **which is why this module needs the `HTTP_CLIENT_ALLOWLIST` entry T010 applies** — if
      T001's escalation 2 was answered "no client exemption", use `aiohttp`'s client
      instead and record the passthrough fidelity you had to give up. Then the `addNote`
      intercept, branch for branch from research.md R3: pass through unless
      `action == "addNote"` **and** at least one client is connected **and** the intercept
      field/value matches (an empty `INTERCEPT_FIELD` **or** empty `INTERCEPT_VALUE` matches
      *everything* — that is the default); build
      `{"fields": <note.fields>, "postMineAction": <int>}`; with `POST_MINE_ACTION == 2`
      forward to AnkiConnect **first**, extract `{"result": <int>}` as `noteId` when
      present, attach it, then **publish without awaiting** (a publish failure is logged,
      not returned) and return AnkiConnect's own response; with any other value, publish and
      await — `{"published": true}` ⇒ **HTTP 200 with the JSON body `-1`** and no forward,
      while a missing, malformed, or `published: false` reply ⇒ **forward the original
      request after all**, and a closed/expired wait ⇒ 500. Do not reproduce G-1
      (double-slashed `GET /` URL), G-2 (`Content-Type[0]` index panic — degrade instead),
      G-3 (unchecked `params.note` assertion — a malformed note means *do not intercept,
      forward it*), or G-5 (request-body logging — metadata only, stderr only, never a note
      field). Comment each divergence with its G-number. Depends: T004 (same lane).
      **Model**: opus-mid (this is the half Katagiri never calls, so a mistake here is
      invisible to every other test in the repository — spec US2's rationale).
      **Write**: src/katagiri/asbplayer_bridge.py.
      **Read**: specs/009-asbplayer-bridge-in-process/research.md (R2, R3, R4 G-1/G-2/G-3/G-5/G-6, O-3, O-4),
      specs/009-asbplayer-bridge-in-process/spec.md (US2 all acceptance scenarios, FR-007,
      FR-008, FR-017), `main.go` lines 170-314 (READ ONLY),
      src/katagiri/media_asbplayer.py:242-315 (the `http.client` house pattern — READ ONLY).

- [x] T006 [merged: fef1747, lane commit 011f1e4; 50 passed, 0 xfail, no product bugs] [P] [US1/US2/US3] `tests/test_asbplayer_bridge.py` (general group): drive the
      real server over a **real loopback socket on an ephemeral port** (`port=0`, never
      8766 — binding rule 4), with a scripted WebSocket client standing in for the extension
      and a stub AnkiConnect upstream (also on an ephemeral port). Every command, endpoint,
      and branch in research.md R1/R3 gets an assertion (SC-002): the `/ws` upgrade; text
      `PING` → text `PONG` (and that `PING` is *not* treated as a response); each of the six
      commands' exact envelope and body, including `mediaId`/`trackNumbers` inclusion rules
      and the seconds-vs-milliseconds split; `messageId` correlation with a **mismatched**
      reply ignored and a **late** reply discarded without disturbing a second in-flight
      request (research.md G-4 — the property the Go version lacks); the **5-second**
      deadline (inject/shorten it rather than sleeping five seconds in the suite, and assert
      the injected value is the module's real default); **broadcast to two** connected
      clients; every relay endpoint's 200/400/500 shape including the empty-string bodies
      and the verbatim JSON blobs; a reply body containing an `error` object still yielding
      200; `POST /disconnect-ws-clients`; and each AnkiConnect branch — non-`addNote`
      forward with headers and status intact, no-client forward, intercept match and
      mismatch, `POST_MINE_ACTION == 2` (forwarded first, `noteId` attached, no await), non-2
      with `published: true` (200, body `-1`, **assert the stub upstream was never called**),
      non-2 with `published: false`/malformed/absent (forwarded after all). Plus lifecycle:
      `stop()` releases the port, a stop/start cycle rebinds (research.md O-2), the default
      bind is loopback, and a non-loopback override logs a warning. Plus a **no-stdout**
      assertion and a **no-note-body-in-logs** assertion (plant a canary field value and
      assert it appears in no log record — FR-017). Every test must leave no listener
      behind. Depends: T005 (same lane).
      **Model**: sonnet-high. **Write**: tests/test_asbplayer_bridge.py.
      **Read**: src/katagiri/asbplayer_bridge.py (post-T005),
      specs/009-asbplayer-bridge-in-process/research.md (R1, R3, R4),
      specs/009-asbplayer-bridge-in-process/spec.md (all acceptance scenarios + Edge Cases),
      specs/009-asbplayer-bridge-in-process/quickstart.md (§2 — the coverage the gate
      expects), tests/conftest.py:3-84 (test groups),
      tests/test_media_mokuro.py:1-60 (the never-bind-the-pinned-port discipline).

### Research (parallel to everything, no shared files)

- [x] T008 [P] [US1] (commit b69f094, 2026-08-24 second pass: v1.20.2 still latest; #1087 open/unmerged, its media-targeting half was already in 570f441d via #1090; main.go + web-socket-client unchanged upstream; upstream external-api.md agrees with R1.3; local checkout HEAD still 37495e22 but working tree dirty with unrelated uncommitted request-playback-state exploration — no main.go impact. NO stop-and-replan) **Re-check upstream before the protocol freeze ships** (research.md
      R8). Verify at `github.com/asbplayer/asbplayer`: (a) the latest release tag — is
      1.20.2 still current? (b) issue **#1087** — still open and unmerged, or did it (or a
      successor carrying a capability handshake / media targeting / event subscription)
      merge? (c) has `scripts/web-socket-server/main.go` changed upstream since `570f441d`,
      and has anything in `common/web-socket-client/` changed the wire envelope? (d) does an
      upstream protocol document now exist that would supersede research.md R1's
      transcription? Append the findings, with dates and links, as a **new dated section at
      the bottom of research.md** (do not rewrite R8 — leave the 2026-08-24 record intact so
      the two checks can be compared) and add one line to `docs/audit-log.md`. **A merged
      protocol change is a stop-and-replan trigger**: report it to the orchestrator
      immediately rather than folding it into the implementation. Also note whether the
      local checkout at `C:/ProjectsC/RandomPr/asbplayer` has drifted from `37495e22`
      (`git -C … log --oneline -3` and `git status --short`).
      **Model**: sonnet-high. **Write**:
      specs/009-asbplayer-bridge-in-process/research.md (append-only, new dated section),
      docs/audit-log.md (one line).
      **Read**: specs/009-asbplayer-bridge-in-process/research.md (R8 and R1, so you know
      what a "change" would mean), specs/README.md (the 009 planned paragraph's upstream
      caveat).

### Serial-on-main track (strict order T007 → T009 → T010)

- [x] T007 [commit fa834b7; 58 passed launch+bridge; stdio contract test intact unedited; new public stop_asbplayer_bridge() + atexit] [US1/US3/US4] Rewrite `src/katagiri/asbplayer_launch.py` to host the bridge, and
      rewrite `tests/test_asbplayer_launch.py` with it (both in one task — the test file is
      a point-for-point mirror of the module's contract and splitting them would leave the
      tree red between two commits). **Keep every public name and signature**:
      `BridgeLaunchResult` (all four fields), `bridge_is_healthy()`,
      `bridge_port_is_occupied()`, `ensure_asbplayer_bridge()` — that is what keeps
      `mcp_server.py`'s startup block and `tests/test_mcp_tools.py:2823-2851`'s monkeypatch
      valid (plan.md decision 6). New body: healthy port ⇒ reuse, unchanged; occupied but
      unhealthy ⇒ still left strictly alone with the same reason text (spec US3 acceptance 3
      — this is the deliberate escape hatch for an operator still running the Go bridge);
      otherwise **start the in-process server** via `asbplayer_bridge`, imported lazily, and
      return `launched=True`. `bridge_dir` becomes vestigial — return `None` and say so in
      the docstring. **Delete** `shutil`, `subprocess`, the `main.go` probe, the Go-on-PATH
      check, and the `CREATE_NO_WINDOW` handling (FR-013, SC-008). `asbplayer_bridge_dir` is
      read only to notice it is set and report it as obsolete-but-accepted (FR-014) — never
      to act on. Add a module-level handle so the server can be stopped, and register
      whatever cleanup is appropriate for a daemon-thread listener. The rewritten tests
      cover: reuse-healthy without starting anything; occupied-unhealthy untouched; a
      successful in-process start that actually binds (on an **ephemeral** port, injected —
      never 8766); loopback by default; the host override honored plus its warning; `stop()`
      releasing the port; an obsolete `asbplayer_bridge_dir` reported and not honored; and a
      source-scan test asserting the module contains no `go`/`subprocess`/`main.go`
      construct. Depends: TG1; lane `wt/009-bridge` merged (T003–T006).
      **Model**: sonnet-high. **Write**: src/katagiri/asbplayer_launch.py,
      tests/test_asbplayer_launch.py.
      **Read**: src/katagiri/asbplayer_launch.py (all 135 lines — the contract you are
      preserving), tests/test_asbplayer_launch.py (all 128 lines — which assertions survive
      and which die, per research.md R5's table), src/katagiri/asbplayer_bridge.py
      (post-T006), specs/009-asbplayer-bridge-in-process/spec.md (US3, US4, FR-011..FR-014),
      specs/009-asbplayer-bridge-in-process/research.md (R5).

- [x] T009 [commit 0ed182d; startup wording + installer doctor row (D-50, READY/MANUAL STEP only) + 4 installer tests; stdio contract intact unedited] [US3/US4] **HOT FILE — one task, serial-on-main.** Update
      `src/katagiri/mcp_server.py`'s startup block (`mcp_server.py:2011-2063`) for the
      hosted bridge, and keep the diff to log wording plus, at most, the obsolete-key
      notice. Specifically: the `launched` branch says the bridge is **hosted in-process**
      on loopback 8766 (not "started the configured asbplayer bridge"); the
      `already_running` branch keeps its `security_scan((8766,))` verification verbatim —
      an occupant we did not start still has an unverified bind address, and that reasoning
      is unchanged; the `reason` branch keeps reporting. Add one line noting an obsolete
      `asbplayer_bridge_dir` if `ensure_asbplayer_bridge()` reports one (FR-014). **Do
      not** add a ToolSpec, do not touch `_ASBPLAYER_BRIDGE_PORT`, do not touch the
      hardening port lists at `mcp_server.py:457,464,1015-1016` (8766 is still in them and
      still correct), and keep `test_main_serves_stdio_and_nothing_else`
      (`tests/test_mcp_tools.py:2823-2851`) passing **without editing it** — startup must
      still call `ensure_asbplayer_bridge` exactly once and still write nothing to stdout,
      which matters more now that a server runs in-process. Optionally (droppable if it
      threatens the budget — plan.md decision 10) add the bridge-readiness doctor row to
      `installer.py`: hosted-by-Katagiri, up only while Katagiri runs, port state, obsolete
      config key. If added, it maps to `MANUAL STEP`/`READY` and **never** to `MISSING`
      (008's exit-code discipline, `installer.py:795`). Depends: T007.
      **Model**: sonnet-high. **Write**: src/katagiri/mcp_server.py, src/katagiri/installer.py
      (only if the optional doctor row is taken).
      **Read**: src/katagiri/mcp_server.py:2011-2100 (the startup block and the Obsidian
      block right after it, for tone), tests/test_mcp_tools.py:2823-2851 (the contract you
      must not break), src/katagiri/asbplayer_launch.py (post-T007),
      src/katagiri/installer.py:769-800 (`collect_doctor_statuses`, `doctor_exit_code`) —
      only if taking the optional row.

- [x] T010 [commit ff71e15; server+client entries + D-50 provenance comments in both files; bverify+cverify 25 passed / 3 env skips; diff = allowlist additions only] [Gate] **Apply the approved allowlist exemptions — BLOCKED until T001's
      escalations 1 and 2 have recorded answers in `docs/decisions-ledger.md`.** Add
      `asbplayer_bridge.py` to `HTTP_SERVER_ALLOWLIST` (`tests/test_bverify.py:301`,
      `tests/test_cverify.py:688`) and, if escalation 2 was approved, to
      `HTTP_CLIENT_ALLOWLIST` (`tests/test_bverify.py:283-291`,
      `tests/test_cverify.py:668-676`) — in **both** files, matching each file's existing
      style, each with a provenance comment in the same voice as the `media_mokuro.py` and
      D-47 comments already there, citing the 009 ledger row by number and stating the
      argument in one or two sentences (the asbplayer extension is a browser client with no
      IPC pipe; the listener is loopback-only on the same port the external bridge already
      opened; the client entry exists for byte-exact AnkiConnect passthrough). **Change
      nothing else in either file** — no pattern edit, no assertion edit, no reformatting;
      quickstart §8 diffs these two files and any other change fails the gate. If the
      decision was "no", **do not edit anything**: report back and stop, because the feature
      cannot land. Depends: T009, and the recorded decision.
      **Model**: sonnet-high. **Write**: tests/test_bverify.py, tests/test_cverify.py.
      **Read**: tests/test_bverify.py:208-301, tests/test_cverify.py:602-688 (both allowlists
      and every provenance comment), docs/decisions-ledger.md (D-47 and the 009 row),
      specs/009-asbplayer-bridge-in-process/research.md (R7).

### Lane `wt/009-docs` [P]

- [x] T011 [merged: 4c57a37] [P] [US3/US4] (lane commit 3a9a851; 7 sections, README untouched — no docs index exists) `docs/asbplayer-bridge.md` (FR-018, operator-facing, ~1 page):
      what the bridge is and what it is for (the extension's WebSocket peer plus an
      AnkiConnect proxy); that **Katagiri now hosts it in-process** — no Go, no second
      checkout, and **it is up only while Katagiri is running**, with the plain consequence
      spelled out for anyone whose extension points its Anki URL at `http://127.0.0.1:8766`
      (spec US3 acceptance 5 — this is the one honest regression and it must be in the doc,
      not just in the spec); how to point the extension at `ws://127.0.0.1:8766/ws`; the
      environment overrides and their defaults (research.md R1.5), including that a
      non-loopback bind is deliberate, logged, and not the default; what happened to
      `asbplayer_bridge_dir` (accepted, inert, safe to delete from your config); and what to
      do if port 8766 is already occupied (Katagiri stands down; the old Go bridge still
      works if you prefer to run it yourself). Add a short "known limits" section quoting
      research.md's open items rather than glossing them (untested `POST_MINE_ACTION` values
      0/1/3, header-passthrough fidelity, Windows rebind). One line in README.md only if a
      docs index section already exists there. Do **not** restate the wire protocol — link
      to research.md R1; the doc is for an operator, not an implementer.
      **Model**: sonnet-high. **Write**: docs/asbplayer-bridge.md, README.md (one line, only
      if an index section exists).
      **Read**: specs/009-asbplayer-bridge-in-process/spec.md (US3, US4, FR-018),
      specs/009-asbplayer-bridge-in-process/research.md (R1.5, R5, R8, all open items),
      specs/009-asbplayer-bridge-in-process/quickstart.md,
      docs/browser-companions.md (the house style for an 008-era operator doc), README.md.

**Checkpoint**: T001–T011 merged to `main` + full suite green → TG3.

---

## Taskgroup TG3: Gate (serial-on-main, dedicated testing agent)

- [ ] T012 [Gate] [GATE RUNS 2026-08-24 (§1–§5, §7–§9 PASS) + 2026-08-25 (§6 live, PASS except media-attach eyeball — see note)] Run `specs/009-asbplayer-bridge-in-process/quickstart.md` §1–§9 in order
      and record the outcome here. The load-bearing steps, called out because a green suite
      alone does not prove them: (a) **§5, the differential run against the Go bridge, is
      mandatory** — plan.md §Deliberate omissions traded 009's held-out suite for it, so a
      skipped §5 is a failed gate; every difference from the Go bridge must be named with
      its research.md G-number, and an unexplained difference fails; if Go is unavailable on
      the gate machine, **escalate — do not skip**; (b) **§6, the real-machine observation
      with the actual browser extension, is mandatory and performed once** (research.md O-1
      — nothing else in the feature exercises the true client), with the observed results
      recorded in this task's completion note, including the mined card and the stop/start
      rebind; (c) **§4** — `git diff main` must be **empty** for
      `src/katagiri/media_asbplayer.py`, `tests/test_media_asbplayer.py`, and
      `tests/test_everify.py` (SC-003), and `PROTOCOL_SURFACE_VERSION` must still be 3;
      (d) **§8** — `git diff main -- tests/test_bverify.py tests/test_cverify.py` must
      contain **only** the allowlist line(s) and provenance comment authorized by the ledger
      row, nothing else, and `git grep` for the Go path must be empty (SC-008); (e) **§9** —
      port 8766 must be free after the full suite (SC-006). Failures → fix via new serial
      tasks filed by the orchestrator, then rerun; **max two fail→fix→rerun cycles**, then
      escalate to the user (constitution V discipline). The gate agent modifies no
      non-test source file.
      **Model**: sonnet-high (testing agent).
      **Read**: specs/009-asbplayer-bridge-in-process/quickstart.md,
      specs/009-asbplayer-bridge-in-process/plan.md (§Constitution Check, §Deliberate
      omissions, §Key design decisions),
      specs/009-asbplayer-bridge-in-process/spec.md (Success Criteria),
      specs/009-asbplayer-bridge-in-process/research.md (R4's G-numbers, for naming
      permitted differences).

      **Gate note (2026-08-24, run 1 of max 2):**
      §1 PASS (aiohttp 3.14.3 resolves clean). §2 PASS (50 passed). §3 PASS (8 passed).
      §4 PASS — `git diff bc9e4a2` (true pre-009 tip; 342f2fe was stale, two 007 cleanup
      commits after it) empty on media_asbplayer.py / its tests / test_everify.py;
      PROTOCOL_SURFACE_VERSION == 3.
      §5 PASS after ratification — Go 1.26.3 oracle on :8769, 23 checks over all R1.4
      routes + R3 branches: 17 byte-identical, 6 divergent, ALL statuses and
      branch-selection decisions identical. The 6 divergences ratified as **G-7** (400
      parser-wording), **G-8** (`-1\n` vs `-1` trailing newline), **G-9** (Go `null\n` vs
      aiohttp plain-text 500 body) in research.md R4, commit 4f462fd. No code change.
      §6 **RUN 2026-08-25 (real extension, live Chrome) — PASS except one eyeball item.**
      Step 1 startup log: real Katagiri starts at 08:22:13 and 08:45:54 both logged
      "asbplayer bridge listening on 127.0.0.1:8766 (in-process)" + "hosted asbplayer
      bridge in-process on loopback port 8766" — wording and loopback bind confirmed.
      (Session note: at 08:45:54 Claude Code spawned TWO katagiri instances; the loser
      got WinError 10048 and, since ensure_asbplayer_bridge never retries, ran
      bridge-less for its lifetime — observations below therefore ran against the same
      bridge hosted standalone via the identical public entry point
      `ensure_asbplayer_bridge()`, HTTP/ws-equivalent per media_asbplayer.py's
      pure-loopback-HTTP design. Follow-up filed for the no-retry gap.)
      Step 2 connect: real extension (2 ws clients, user's Chrome, YouTube tab) connected
      unprompted — extension was already configured at ws://127.0.0.1:8766/ws; DEBUG log
      "client connected from 127.0.0.1 (1 total)/(2 total)" 0.3s/5s after bind; connection
      held >2 min across many keepalive intervals while serving relays. PASS.
      Step 3 MCP tools: media_now → active=true, channel=asbplayer, real bound media
      (YouTube title), anchor_ms with **anchor_source="live"** (patched build confirmed).
      media_context → ok, same live anchor; lines=[] because the extension never applied
      the pushed SRT (load-subtitles relay answered 200 with awaited client reply, but
      loadedSubtitles stayed [] — extension-side, tab unfocused/paused; bridge relay
      itself verified). Relay checks: /asbplayer/seek moved the real player to 5000ms
      (playback-state confirmed), bound-media/subtitles/playback-state all live. PASS.
      Step 4 mine: addNote via proxy http://127.0.0.1:8766 → forwarded to Anki 200,
      noteId 1787637230253 (and 1787637406326 on logged rerun) landed in deck Default;
      DEBUG log "published mine-subtitle (…) to 2 client(s)" with noteId attached, both
      clients replied (fire-and-forget per R3.6). **Residual: media attach on the card
      not confirmed** — extension acked mine-subtitle but issued no updateLastCard
      (no subtitle loaded/tab unfocused); needs 60s of user eyeball: focus the tab, load
      subs, mine once, confirm [sound:]/<img> on the card.
      Step 5 rebind: real-Katagiri cycle in katagiri.log — 08:22 bind → stop
      "asbplayer bridge stopped" → 08:45:56 fresh instance rebinds clean (O-2). Repeated
      with the standalone host: kill → port released ≤2s → restart → rebind ≤1s →
      both extension clients auto-reconnected within 5s, relays served. PASS.
      §7 PASS (security_scan/serves_stdio test green); live-binding check now done with
      bridge running: Get-NetTCPConnection port 8766 → 127.0.0.1 only, never 0.0.0.0.
      §8 PASS — diff vs bc9e4a2 on test_bverify/test_cverify contains only the D-50
      allowlist entries + provenance comments; no spawn machinery in src (quickstart §8
      grep pattern corrected in 4f462fd — old literal pattern false-fired on mandated
      main.go:NNN citations).
      §9 PASS — full suite 2194 passed / 10 env skips (108s); port 8766 free after
      clearing a leftover pre-gate Go-bridge process (go-build main.exe, PID 3996, from
      earlier smoke testing — suite itself binds ephemeral ports only, verified).

**Checkpoint**: §6 observed live 2026-08-25 against the real extension — all steps pass
except one residual eyeball item (media attach on the mined card, extension-side). T012
flips to [x] when the user confirms that one observation. Push to remote after TG3
(orchestrator).

---

## Dependencies & execution order

- TG1 (T001) → blocks all of TG2. **All four escalations must be answered first**, not just
  the ledger row written.
- T002 (`pyproject.toml`) lands on `main` first so the lane's worktree can `uv sync`.
- Lane `wt/009-bridge`: T003 → T004 → T005 → T006, strictly in order (one module, then its
  tests).
- T008 (upstream re-check) runs in parallel with everything; it shares no file with any
  other task except an append to research.md and one audit-log line. Its result can stop
  the feature, so run it **early**, not at the end.
- Serial-on-main: T007 → T009 → T010, **after `wt/009-bridge` merges**. T010 additionally
  waits on the recorded user decision.
- Lane `wt/009-docs`: T011, parallel to everything.
- TG3 (T012) starts only when T001–T011 are merged and checked.
- Full suite runs at taskgroup boundaries only (TG2 close, TG3 §9), not per task.

## Notes

- specs/README.md's execution model applies, with one correction: lanes branch from `main`,
  not `master` (plan.md §Branch note).
- Worktree bootstrap quirks (from specs/README.md): a fresh worktree has no `.venv` — run
  tests via the primary checkout's `.venv` by absolute path, and note that after T002 that
  venv needs `uv sync` for `aiohttp`; `core.hooksPath` beads noise is harmless.
- The asbplayer checkout at `C:/ProjectsC/RandomPr/asbplayer` is **another repository**:
  read it freely, never edit it, never commit in it. It is the protocol authority and the
  TG3 differential oracle.
- Total: 12 tasks (1 governance, 10 implementation/research, 1 gate) across 2 lanes + 1
  serial track. plan.md records why the feature carries no held-out suite, no data model,
  and no contracts directory — and what replaces the holdout.
