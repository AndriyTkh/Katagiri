# Katagiri

A personal English↔Japanese study tool, exposed as an **MCP server over stdio**.
Not a service: there is no network listener, no daemon, no multi-user story. It
runs on one Windows machine and is driven by whatever MCP client is attached.

## Setup (uv)

```powershell
uv sync                 # create .venv and install deps (Python 3.12)
uv run pytest -q        # tests
uv run pre-commit install   # optional: enable the hygiene + secret-scan hooks
```

## Running the MCP server

The server speaks JSON-RPC on **stdout**, so nothing else may write there:
all logging goes to **stderr only** (see `src/katagiri/logging_setup.py`).
Launch it with `PYTHONUTF8=1` so Japanese text survives the Windows console
codepage.

```powershell
$env:PYTHONUTF8 = "1"; uv run python -m katagiri.mcp_server
```

`.mcp.json` at the repo root wires this up for MCP clients, using the
interpreter at `.venv\Scripts\python.exe`.

## Configuration

Machine-specific paths (vault, Anki data dir, scratch, database) live in
`%LOCALAPPDATA%\Katagiri\config.toml`, never in this repo. The file is created
with commented defaults the first time config is loaded.

Large vendored dictionaries (full UniDic, kanjium accents) go in `vendor/`,
gitignored, with digests committed in `vendor/CHECKSUMS.sha256`. Nothing is
downloaded at runtime — see `vendor/README.md`.

## Plan

See `docs/dev-plan.md`.
