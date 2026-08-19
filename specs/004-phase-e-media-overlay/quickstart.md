# Quickstart: Phase E validation (E-verify)

Mirrors gate bead `kata-evf`. Fixtures/scripted players only.

## Prerequisites

- **D6 stop-gate PASS** (`stop_gate_status`).
- mpv with `input-ipc-server=\\.\pipe\mpv-katagiri` (already in mpv.conf per kata-mz2 progress); scripted asbplayer WS fixture; mokuro bridge fixture with secret.

## Steps

```bash
uv run pytest tests/test_media_mpv.py tests/test_media_asbplayer.py tests/test_media_mokuro.py tests/test_screenshot.py tests/test_lyrics.py -ra
```

```bash
uv run pytest tests/test_everify.py -ra
```

## Expected outcomes

1. mpv position/title via `media_now`; subtitle window via `media_context`.
2. asbplayer subtitle window anchored from a fixture mining event; manual anchor counted.
3. mokuro current page context; bridge rejects missing secret / wrong Origin.
4. Screenshot round-trip: hostile media title (`..\` in title) → file lands under confined root with server-generated name; agent reads exactly that frame.
5. One `.lrc` through WATCH mode; lyric line minable with source ref.
6. **Adversarial**: subtitle line containing tool-call instructions triggers NO write tool (envelope + echo-back holds).
7. Stale heartbeat → structured "no active media", never stale-as-live.
8. Cumulative: scenarios A..D still green.

## Milestone E (manual)

Anchored "what did she just say?" answered on the primary consumption surface; words mined with source refs; ≥5 of last 7 days show Phase-E tool events.
