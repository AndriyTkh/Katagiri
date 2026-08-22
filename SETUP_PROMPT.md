# Katagiri — one-shot setup prompt

Paste the block below into any coding agent that has shell + file access to a
fresh clone of this repository (e.g. Claude Code, Codex CLI, or a ChatGPT
session with code-execution/terminal access). It walks the agent through a
full install and tells it exactly which steps it must hand back to you,
because they require things only you can provide (a real API key, a
downloaded textbook you have the right to use, a running desktop app).

Windows only: `src/katagiri/mcp_server.py` hard-refuses to start outside
`win32`, and the installer assumes PowerShell/`schtasks`. Run this on Windows
(10/11).

---

## Prompt to paste

```
You are setting up "Katagiri" (an English<->Japanese study MCP server) from a
fresh clone, on Windows. Work through these steps in order. Do not skip a step
silently — if something needs a value only the human has (an API key, a
license-restricted file, a running desktop app), stop and ask for it instead
of guessing or inventing a placeholder that looks real.

1. Confirm tooling: check for `uv` (`uv --version`). If missing, tell the human
   to run `winget install --id astral-sh.uv -e` (or see
   https://docs.astral.sh/uv/getting-started/installation/) and stop until
   they confirm it's installed.

2. Root project deps: from the repo root, run `install.bat` (or `install.ps1`
   directly). This runs `uv sync` and then the interactive doctor at
   `src/katagiri/installer.py`, which reports READY/MISSING/MANUAL STEP for
   every component. Read vendor/README.md and vendor/CHECKSUMS.sha256 before
   this doctor's vendor check will pass — see step 3.

3. Vendor data — download what you legally can, flag what you can't:
   Read `vendor/README.md` in full first; it has the exact sources, licenses,
   and target paths for every file. Then:
   - `vendor/jmdict/` (jmdict-simplified English release, ~11 MB): download
     the zip named in vendor/CHECKSUMS.sha256's jmdict entry.
   - `vendor/kanjium/accents.txt` (~10 MB): download from the kanjium project
     revision pinned in CHECKSUMS.sha256.
   - `vendor/unidic/` (full UniDic, ~1 GB unpacked): download the full UniDic
     distribution (NOT unidic-lite) and unpack so dicrc/char.bin/matrix.bin/
     sys.dic/unk.dic sit directly in that folder.
   - `vendor/jreadability/jreadability-1.1.5.tar.gz`: download the sdist (not
     a wheel) from https://pypi.org/project/jreadability/.
   - `vendor/bccwj/BCCWJ_frequencylist_suw_ver1_0.zip`: download from the DOI
     record at https://doi.org/10.15084/00003218 (NINJAL; free for research/
     personal study per their terms, not open-redistribution).
   - `vendor/jlpt/n<1-5>-vocab-*.anki`: download all five levels from
     http://www.tanos.co.uk/jlpt/jlpt<N>/vocab/n<N>-vocab-kanji-eng.anki
   - Run `python scripts/fetch_taekim.py` — this one fetches + commits-ready
     itself, no manual download needed.
   - After placing files, compute checksums with the PowerShell snippet at
     the bottom of vendor/README.md and append them to
     `vendor/CHECKSUMS.sha256`. The loaders refuse any file whose digest
     doesn't match, so this step isn't optional.
   - `vendor/irodori/` (Japan Foundation lesson PDFs/MP3s): DO NOT attempt to
     download these. Stop here and tell the human: "Irodori materials are
     under Japan Foundation terms with no redistribution rights — you must
     acquire them yourself from the official Irodori distribution, then drop
     them in vendor/irodori/ and run `python scripts/fetch_irodori.py` to
     checksum them." This is a required human step, not optional-and-skip:
     the curriculum importer will run without it, but degraded (unanchored/
     text-only entries).
   - Note: jreadability/BCCWJ/JLPT lists are optional — the app runs without
     them, just with a partial difficulty score. UniDic/kanjium/JMdict are
     not optional.

4. Re-run `install.bat` (or `uv run python -m katagiri.installer` directly)
   now that vendor data exists. Follow its interactive prompts: it writes
   `%LOCALAPPDATA%\Katagiri\config.toml`, imports JMdict/kanjium into the
   database, builds search indexes, and offers to register optional Windows
   Scheduled Tasks (daily backup, Anki sync) — ask the human whether they
   want those before enabling them, since a personal tool installing
   background jobs without asking is exactly what this project's docs say
   not to do.

5. Agent subproject (a separate uv project, needed only if the human wants
   the LangGraph-style study agent, not just the raw MCP server):
   - `cd agent && uv sync`
   - `copy .env.example .env`
   - `uv run python scripts/setup.py` — this is interactive: it checks for
     the Obsidian "Local REST API" plugin, offers to pull the API token and
     TLS cert straight out of the plugin's own `data.json` if Obsidian is
     already running with the vault open, and prompts (hidden input) for an
     OpenRouter API key. If Obsidian isn't installed/running yet, it will
     say so — stop and tell the human:
     "Install Obsidian (https://obsidian.md), open (or create) a vault, then
     in that vault go to Settings > Community plugins > Browse > install and
     enable 'Local REST API' (coddingtonbear) — then re-run
     `agent/scripts/setup.py`."
   - For the OpenRouter key: tell the human to get one at https://openrouter.ai
     (Keys > Create key), and to set a spend cap on it. The free tier is
     50 requests/day, which is not much for real study sessions.

6. Verify: from the repo root, `uv run pytest -q` should pass (public/CI mode
   also exists via `uv run pytest --public-build`, but that's slower and not
   needed for a normal fresh install). If `agent/` was set up, its own test
   suite (if any) runs from inside `agent/`.

7. Final report to the human — list clearly, separated into:
   - Done automatically: what you downloaded/installed/configured.
   - Still needs the human, with the exact action for each: Irodori files,
     confirming/generating the Obsidian API token if setup.py couldn't pull
     it automatically, the OpenRouter key and its spend cap, and whether they
     want the optional Scheduled Tasks enabled.

Do not commit `agent/.env`, `%LOCALAPPDATA%\Katagiri\config.toml`, any real
API key/token, or any Irodori PDF/MP3 file, under any circumstance — all of
that is already gitignored; do not override or work around the ignore rules.
```
