# Katagiri

A personal English↔Japanese study tool, exposed as an **MCP server over stdio**.
Not a service: there is no network listener, no daemon, no multi-user story. It
runs on one Windows machine and is driven by whatever MCP client is attached.

**Windows only** — the server refuses to start on any other platform.

**New checkout, want a coding agent to install it for you?** Paste the prompt
in [`SETUP_PROMPT.md`](SETUP_PROMPT.md) into any agent with shell access to
this clone. It downloads what can legally be auto-fetched, installs both uv
projects, and tells you exactly which steps (API keys, Obsidian, Irodori
materials) it can't do for you.

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
with commented defaults the first time config is loaded. It may also hold local
credentials (the Obsidian Local REST API key), so treat it as a secret: never
commit it, never paste its contents into an issue, a log or a chat.

Large vendored dictionaries (full UniDic, kanjium accents) go in `vendor/`,
gitignored, with digests committed in `vendor/CHECKSUMS.sha256`. Nothing is
downloaded at runtime — see `vendor/README.md`.

## Grader environment (assignment 005)

This section is for a grader, or anyone else who has never seen this
repository, bringing up the whole system — Obsidian, the custom katagiri
MCP server, and the homework agent — from a clean Windows machine. It
covers only the **assignment 005 demo profile**: a fixture database, a
demo Obsidian vault, and a demo token, entirely separate from any personal
data. Full step-by-step detail lives in `docs/assignment/demo-setup.md`
(setup) and `specs/005-mcp-assignment/quickstart.md` (the rehearsed
demonstration script, including the 9-step defence walkthrough); this
section is the condensed path to a first successful run.

### 1. Install Obsidian and the pinned plugin version

1. Install the [Obsidian](https://obsidian.md) desktop app (any recent
   release; Obsidian itself is not version-pinned).
2. Open a **second, dedicated vault** for the demo — never a personal
   vault. A copy of this repo's `tests/demo_fixtures/vault/` (copied out of
   the checkout, not opened in place) works as the starting tree.
3. In that vault: **Settings → Community plugins** → enable community
   plugins → install **"Local REST API"**
   (`coddingtonbear/obsidian-local-rest-api`), **pinned at v5.1.0** (the
   version this project was built and spiked against) → enable it.
4. In the plugin's settings, change both ports away from the defaults
   (`27123`/`27124`) so this instance can never be mistaken for a personal
   one — e.g. HTTPS `27224`, HTTP `27223` (HTTP can stay disabled). The
   agent talks to the plugin's built-in Streamable HTTP `/mcp/` endpoint.

### 2. Trust the self-signed certificate

The plugin's HTTPS endpoint serves a **self-signed certificate**. Pick one:

- Fastest: set `OBSIDIAN_VERIFY_TLS=false` in `agent/.env` (step 4). Only
  do this against the demo instance on its non-default port — never
  against anything that could be a personal vault.
- Cleaner for a recording: export the demo instance's certificate and
  point `agent/.env`'s `OBSIDIAN_CA_BUNDLE` at the exported file, leaving
  `OBSIDIAN_VERIFY_TLS=true`.

### 3. Generate a demo token

In the plugin's settings, use **"Generate new API Key"** on this demo
instance. This token must be unique to the demo vault — never the value of
anyone's personal Local REST API token.

### 4. Clone and install both projects

```powershell
git clone <this repository's URL>
cd Katagiri
uv sync                     # katagiri server: creates .venv, Python 3.12
cd agent
uv sync                     # agent: separate uv project, separate venv
copy .env.example .env
cd ..
```

Edit `agent/.env` and fill in: `OBSIDIAN_MCP_URL`
(`https://127.0.0.1:<your HTTPS port>/mcp/`), `OBSIDIAN_API_TOKEN` (step
3), `OBSIDIAN_VERIFY_TLS`/`OBSIDIAN_CA_BUNDLE` (step 2), `OPENROUTER_API_KEY`
(step 7 below), and `KATAGIRI_PYTHON` (this checkout's
`.venv\Scripts\python.exe`). `.env` is gitignored; never commit it.

### 5. Point `KATAGIRI_CONFIG` at the demo profile

```powershell
copy tests\demo_fixtures\demo-config.toml.example C:\path\to\demo-config.toml
# edit the copy: fill in real absolute paths for vault_path, db_path, scratch_root
$env:KATAGIRI_CONFIG = "C:\path\to\demo-config.toml"
```

Leave this variable unset and katagiri falls back to the personal
`%LOCALAPPDATA%\Katagiri\config.toml` — never point it anywhere but a demo
config for a graded run.

### 6. Build the fixture database

```powershell
uv run python scripts/build_demo_db.py
```

Measured runtime: the JMdict import step takes **~21 seconds**; the
resulting database is **~82 MB**. The script prints its own timing and
writes a JSON receipt next to the database. Nothing is downloaded at run
time (JMdict/kanjium data is vendored and checksummed), and the script
refuses to write anywhere near the real personal profile.

### 7. Fund the model

The agent calls a **pinned** model, `openai/gpt-4o-mini` (recorded in
`OPENROUTER_MODEL` in `agent/.env`), through OpenRouter. OpenRouter's free
tier caps usage at **50 requests/day**, which one rehearsal alone can
exhaust — top up the account with a small balance before running the demo,
and put the resulting key in `OPENROUTER_API_KEY`.

**If the request ceiling is hit anyway**: either wait for the free-tier
daily reset before continuing, or swap `OPENROUTER_MODEL` in `agent/.env`
to a different model verified to support tool calling (it must be on
OpenRouter's `tool_use`-capable list — an incapable or unpinned model
silently stops emitting tool calls instead of failing loudly).

### 8. Start the custom server — independently, in its own process

In its own terminal, from the repository root:

```powershell
$env:PYTHONUTF8 = "1"
$env:KATAGIRI_CONFIG = "C:\path\to\demo-config.toml"
uv run katagiri-mcp
```

(the console script `pyproject.toml` registers; equivalent to
`uv run python -m katagiri.mcp_server`, see "Running the MCP server"
above). It speaks JSON-RPC over stdio and writes only to stderr. Start it
**before**, and independently of, the agent — the agent's own
`MultiServerMCPClient` connects to a katagiri process, it does not launch
this one.

### 9. Start the agent — a separate process, in a second terminal

```powershell
$env:PYTHONUTF8 = "1"
uv run --project agent python -m katagiri_agent
```

This is the invocation `specs/005-mcp-assignment/quickstart.md` walks
through in full (discovering both MCP connections, pointing at a specific
goal note, running the branching workflow); consult it for the exact
flags exercised in the rehearsed demonstration. The agent wires its own
katagiri stdio connection (via `KATAGIRI_PYTHON`/`KATAGIRI_MODULE` in
`agent/.env`) alongside its direct HTTPS connection to the demo Obsidian
instance — see `agent/README.md`'s "Connection configuration" section.

### 10. Verify isolation before recording

Every time, before recording, run the manual `netstat` check from
`docs/assignment/demo-setup.md` (its Step 5): confirm the demo port is
bound to loopback only, and confirm the personal Obsidian ports
(`27123`/`27124`) are not listening if personal Obsidian is meant to stay
closed for the run.

## Plan

See `docs/dev-plan.md`.
