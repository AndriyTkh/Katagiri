# asbplayer Bridge

Operator-facing guide to the local bridge the asbplayer browser extension
talks to. Background and design:
`specs/009-asbplayer-bridge-in-process/spec.md` (FR-018, US3, US4),
`research.md` (the wire-protocol authority — see R1 below, do not expect
it restated here).

## What it is

The asbplayer extension needs two things from your machine: a WebSocket
peer to push subtitle/media commands to and receive replies from
(`mine-subtitle`, `get-bound-media`, `get-subtitles`, `get-playback-state`,
`load-subtitles`, `seek-timestamp`), and an AnkiConnect proxy on the same
port, so the extension can talk to Anki through one URL and have its
`addNote` calls intercepted for mining. That combined listener — WebSocket
peer plus AnkiConnect proxy, on `127.0.0.1:8766` — is "the bridge." It used
to be a separate Go program (`scripts/web-socket-server/main.go` in a
second checkout of the asbplayer repository, launched with `go run`);
Katagiri no longer uses that program. For the exact commands, HTTP routes,
and response shapes, see `specs/009-asbplayer-bridge-in-process/research.md`
R1 — this doc does not repeat the wire protocol.

## Katagiri now hosts it in-process

As of this feature, Katagiri's own MCP server process hosts the bridge
directly — no Go toolchain, no second checkout, no child process to
launch or babysit. The listener starts when Katagiri starts and stops when
Katagiri stops.

**The one honest regression, and it matters if you mine while Katagiri is
closed:** the old Go bridge could run on its own, independent of Katagiri,
for as long as you wanted — so mining worked even with Katagiri not
running. The in-process bridge cannot do that: **it is up only while
Katagiri is running.** If your asbplayer extension's Anki Connect URL is
set to `http://127.0.0.1:8766` and you try to mine a card while Katagiri
is not running, mining will fail — there is nothing listening on 8766 at
all. This is not a bug to work around; it is the deliberate tradeoff this
feature makes (spec US3 acceptance 5). If you rely on mining outside your
Katagiri sessions, keep Katagiri running while you study, or continue
running the old Go bridge yourself (see "If port 8766 is already
occupied" below).

## Pointing the extension at the bridge

**If you're using the vendored extension build** (`vendor/asbplayer-extension/`,
see `vendor/README.md`), there is nothing to configure: that build's
defaults are already baked in — WebSocket server URL
`ws://127.0.0.1:8766/ws` and Anki Connect URL `http://127.0.0.1:8766` — so a
fresh "Load unpacked" install points at this bridge out of the box. The
installer wizard's "asbplayer browser extension (optional)" step
(`katagiri.installer.step_asbplayer_extension`) verifies both URLs are
present in the build, offers to open `chrome://extensions`, and prints the
"Load unpacked" steps; `python -m katagiri.installer --check`'s doctor
table carries the same verification as a read-only row ("asbplayer
extension (vendored build)").

**If you installed asbplayer from its official Chrome Web Store listing (or
any other upstream build)** instead of the vendored one, its defaults point
at the *old* AnkiConnect port and won't have the WebSocket client enabled,
so you still need to edit its settings by hand. In the extension's
settings, set the WebSocket server URL to:

```
ws://127.0.0.1:8766/ws
```

and point its Anki Connect URL at the same host and port
(`http://127.0.0.1:8766`) so the AnkiConnect proxy and `addNote` intercept
apply. Both settings are unchanged from the old Go bridge — nothing about
the extension-side configuration is different, only what is listening on
the other end.

## Environment overrides and defaults

The bridge honors the same environment variables the Go bridge read, with
the same defaults, so an existing `.env`-shaped setup transfers unchanged
(research.md R1.5):

| Env var | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | bind address |
| `PORT` | `8766` | listen port |
| `ANKI_CONNECT_URL` | `http://127.0.0.1:8765` | proxy upstream |
| `POST_MINE_ACTION` | `2` | 0 none / 1 open dialog / 2 update last card / 3 export card |
| `INTERCEPT_FIELD` | `""` | note field to match for interception |
| `INTERCEPT_VALUE` | `""` | required value of that field |

Binding to anything other than `127.0.0.1` (or `::1`) is a deliberate,
supported override — not the default, and not silent. If you set `HOST` to
a non-loopback address, the startup log prints a warning naming the
exposure. Leave `HOST` unset unless you specifically need the bridge
reachable from another machine, and understand that you are opening a
listener beyond your own machine if you do.

## What happened to `asbplayer_bridge_dir`

The old `asbplayer_bridge_dir` config key (the path to your Go checkout)
is still accepted in config — loading it produces no error — but it is no
longer honored for anything. Katagiri never looks for a `main.go`, never
looks up `go` on PATH, and never spawns a bridge process. The obsolescence
is surfaced once (a log line and/or a doctor row) so you know the key is
now inert rather than silently ignored. It is safe to delete the key
(and the Go checkout it pointed at) from your config whenever you like;
nothing depends on it.

## If port 8766 is already occupied

Katagiri never binds a port that is already in use. If something is
already listening on 8766 when Katagiri starts — most commonly, your old
Go bridge still running on its own — Katagiri logs what it found and
stands down: it does not attempt to bind, does not start a second
listener, and does not kill the occupant. The asbplayer channel keeps
working through whatever is already listening, as long as it is a
compatible bridge. In other words, if you prefer the old bridge's
independent lifetime (see the regression above), you can keep running it
yourself exactly as before — Katagiri will get out of its way.

## Known limits

These are open items from `specs/009-asbplayer-bridge-in-process/research.md`
("Open items") — real gaps, not glossed over:

- **The real extension has never been tested against a Python bridge**
  before this feature's gate. Reconnect cadence, header-set tolerance, and
  whether the extension sends protocol-level pings were unverified until
  the mandatory real-machine step in `quickstart.md` §6.
- **Windows rebind behavior** (a fast Katagiri restart hitting a refused
  rebind on 8766, e.g. `TIME_WAIT` or Windows' `SO_EXCLUSIVEADDRUSE`
  default) was a design question, not a measured fact, until the gate's
  stop/start cycle exercised it.
- **`POST_MINE_ACTION` values 0, 1, and 3 are untested end-to-end.** The
  default is `2`, which is what most installs actually exercise; whether
  the extension answers `{"published": ...}` correctly for the other
  values was unverified at design time.
- **Header-passthrough fidelity to real AnkiConnect is asserted against a
  stub, not against Anki itself.** The proxy copies request headers
  wholesale; which headers actually survive a real round trip, and
  whether AnkiConnect cares, was intended to be settled by the
  differential run against the Go bridge (quickstart.md §5), not assumed.

If you hit behavior that looks like one of these, check
`specs/009-asbplayer-bridge-in-process/research.md` for the current state
of that item before assuming it's a new bug.
