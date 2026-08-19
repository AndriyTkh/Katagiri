# Quickstart: Phase B validation (B-verify)

Mirrors gate bead `kata-bvf`. Run against **fixtures**, never live personal data.

## Prerequisites

- A-verify green (`tests/test_averify.py` passing).
- Obsidian running with obsidian-local-rest-api bound to 127.0.0.1:27123 (for the proxy scenarios); token configured in `%LOCALAPPDATA%` per `config.py`.
- Fixture DB + mini vault from `tests/fixtures/`.

## Steps

```bash
uv run pytest tests/test_exporter.py tests/test_obsidian_proxy.py -ra
```

```bash
uv run pytest tests/test_bverify.py -ra
```

## Expected outcomes

1. Exporter writes `Today.md` under `.derived/` with all Phase-B sections + generated-file header.
2. Exporter **refuses** to overwrite a headerless file at the target path (scenario asserts the refusal).
3. Cold-agent scenario reads `Today.md` and one arbitrary note through proxy tools only.
4. Direct-HTTP bypass attempt is refused / impossible from the registered toolset; REST token appears nowhere in outputs, errors, or the event log.
5. Cumulative: all Phase-A scenarios still green (regression gate).

## Learner metric (manual, from event log)

`Today.md` opened ≥5 of the last 7 days during the phase window. A green test suite does not close the phase without this.
