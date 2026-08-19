# Research: Phase E — Media Overlay

Settled decisions (coverage table in decisions-ledger.md):

- **Decision**: No own media player; player = mpv + asbplayer + mokuro + MCP context channel.
  **Rationale**: DRM wall identical either way; months of cost; violates MCP-ceiling and OSS-first.
  **Alternatives**: own player (rejected, D-13); browser-wide OCR overlay (superseded, F-08); agent interface inside player (rejected).
  **Source**: ledger D-13; dev-plan v1.

- **Decision**: asbplayer has no playhead upstream (issue #1087) → anchor derived automatically from the last mining/copy event's timestamp; manual anchors accepted and counted so the upstream-PR option (F-05) fires on data.
  **Source**: dev-plan E2; ledger F-05.

- **Decision**: mokuro page-change bridge requires shared secret + Origin validation; `volume-data.json` poller as fallback; `.mokuro` JSON as text layer.
  **Rationale**: localhost bridge reachable by any browser page without the checks.
  **Source**: dev-plan E3; Round 5 security cluster.

- **Decision**: Screenshots to a confined scratch root with server-generated filenames — media titles are attacker-controlled (path traversal).
  **Source**: dev-plan E4; ledger D-22.

- **Decision**: All externally-sourced text (subtitles, OCR, lyrics) wrapped in the untrusted-data envelope; adversarial injection scenario is a mandatory E-verify case.
  **Source**: ledger D-22/D-23.

- **Decision**: Channel order E1/E2/E3 fixed at gate time by consumption mix measured during the D6 window.
  **Source**: ledger F-10.

- **Decision**: Rewind telemetry capture slice only (seek-back events; shipped early under the D6 exemption as kata-e6s); analysis stays a moonshot.
  **Source**: dev-plan E6; moonshot §4.

No NEEDS CLARIFICATION items remain (channel order is a designed decision-at-gate, not a gap).
