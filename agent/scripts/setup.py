"""One-file interactive setup for the katagiri agent.

Run from anywhere with any Python 3.9+ (stdlib only, no venv needed):

    python agent/scripts/setup.py

What it does, in order:
  1. Tooling: checks `uv` is installed and the pinned Python is available.
  2. Deps: runs `uv sync` in agent/ (creates agent/.venv); checks the
     primary checkout's .venv exists (KATAGIRI_PYTHON target).
  3. Obsidian: checks the Local REST API plugin is installed in the vault,
     version >= 5.1, and whether the /mcp/ endpoint is currently listening.
     Offers to copy the vault's own API token and self-signed cert out of
     the plugin's data.json (with your consent; values are never printed).
  4. Tokens: prompts for OPENROUTER_API_KEY (hidden input) with
     instructions for getting one.
  5. Writes agent/.env - merges with what's already there: your existing
     non-empty values always win; only blanks are filled.
  6. Prints a presence-only report (SET / blank per variable - never the
     values) and exits non-zero if something required is still missing.

Idempotent: rerun any time; it only fills gaps.
"""

from __future__ import annotations

import getpass
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# --yes: accept every default, never prompt (safe for agent harnesses/CI).
# Auto-detected too: getpass on Windows reads the *console* directly, so a
# harness-spawned process with a console but null stdin would block forever
# on a hidden prompt. Only prompt when stdin is a real interactive terminal.
#
# --stdio-bootstrap: run the whole setup (non-interactive, all output on
# stderr - stdout belongs to the MCP JSON-RPC stream), then launch the
# katagiri MCP server inheriting this process's stdio. This is what
# .mcp.json points at, so registering the MCP connection IS the setup.
STDIO_BOOTSTRAP = "--stdio-bootstrap" in sys.argv
ASSUME_YES = STDIO_BOOTSTRAP or "--yes" in sys.argv or "-y" in sys.argv
INTERACTIVE = sys.stdin.isatty() and not ASSUME_YES
_OUT = sys.stderr if STDIO_BOOTSTRAP else sys.stdout

_t0 = time.monotonic()
_step_t = _t0

# --- paths (script lives at <repo>/agent/scripts/setup.py) ----------------
SCRIPT = Path(__file__).resolve()
AGENT_DIR = SCRIPT.parent.parent
REPO_ROOT = AGENT_DIR.parent
ENV_FILE = AGENT_DIR / ".env"
ENV_EXAMPLE = AGENT_DIR / ".env.example"
VAULT_DIR = REPO_ROOT / "docs" / "katagiri" / "katagiri"
PLUGIN_DIR = VAULT_DIR / ".obsidian" / "plugins" / "obsidian-local-rest-api"
CERT_FILE = AGENT_DIR / "obsidian-cert.pem"

OBSIDIAN_HOST = "127.0.0.1"
OBSIDIAN_PORT = 27124
PLUGIN_MIN_VERSION = (5, 1)
DEFAULT_MODEL = "openai/gpt-4o-mini"

# Variables that are CORRECT when blank on the current setup. The setup
# never nags about these; the comments explain when they'd ever be filled.
OK_BLANK = {
    "KATAGIRI_CONFIG": "only the demo profile sets this (docs/assignment/demo-setup.md)",
    "OBSIDIAN_STDIO_COMMAND": "only used if OBSIDIAN_TRANSPORT=stdio (it isn't)",
    "OBSIDIAN_STDIO_ARGS": "only used if OBSIDIAN_TRANSPORT=stdio (it isn't)",
    "OBSIDIAN_CA_BUNDLE": "alternative to OBSIDIAN_VERIFY_TLS=false; filled if you export the cert below",
}

# Required for the *agent graph* to run; the katagiri MCP server itself
# needs neither, so --stdio-bootstrap treats these as warnings, not gates.
REQUIRED = ("OPENROUTER_API_KEY", "OBSIDIAN_API_TOKEN")

VAR_ORDER = [
    "PYTHONUTF8",
    "KATAGIRI_PYTHON",
    "KATAGIRI_MODULE",
    "KATAGIRI_CONFIG",
    "OBSIDIAN_TRANSPORT",
    "OBSIDIAN_MCP_URL",
    "OBSIDIAN_API_TOKEN",
    "OBSIDIAN_VERIFY_TLS",
    "OBSIDIAN_CA_BUNDLE",
    "OBSIDIAN_STDIO_COMMAND",
    "OBSIDIAN_STDIO_ARGS",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
]

SECRET_VARS = {"OPENROUTER_API_KEY", "OBSIDIAN_API_TOKEN"}


def say(msg: str) -> None:
    print(msg, flush=True, file=_OUT)


def header(msg: str) -> None:
    global _step_t
    now = time.monotonic()
    if now - _step_t > 0.5:
        say(f"      ({now - _step_t:.1f}s)")
    _step_t = now
    say(f"\n=== {msg} ===")


def warn(msg: str) -> None:
    say(f"  [!] {msg}")


def ok(msg: str) -> None:
    say(f"  [ok] {msg}")


def ask_yn(prompt: str, default: bool = True) -> bool:
    if not INTERACTIVE:
        say(f"  {prompt} -> auto-{'yes' if default else 'no'} (non-interactive)")
        return default
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {prompt} {suffix} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def ask_secret(prompt: str) -> str:
    """Hidden prompt; skipped entirely when non-interactive (Windows getpass
    reads the console device, not stdin, so it would block a harness run)."""
    if not INTERACTIVE:
        say("  (non-interactive: secret prompt skipped - rerun in a terminal to paste it)")
        return ""
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, OSError):
        return ""


# --- .env parsing / writing ------------------------------------------------

def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_env(values: dict[str, str]) -> None:
    lines = [
        "# katagiri-agent environment. Written/updated by agent/scripts/setup.py.",
        "# Gitignored - never commit. Rerun the setup script to fill gaps.",
        "",
        "# Console/subprocess encoding (see .env.example for why).",
        f"PYTHONUTF8={values.get('PYTHONUTF8', '1')}",
        "",
        "# --- katagiri connection (stdio) ---",
        f"KATAGIRI_PYTHON={values.get('KATAGIRI_PYTHON', '')}",
        f"KATAGIRI_MODULE={values.get('KATAGIRI_MODULE', 'katagiri.mcp_server')}",
        "# Blank = your normal %LOCALAPPDATA%\\Katagiri config. Only the demo",
        "# profile (docs/assignment/demo-setup.md) points this elsewhere.",
        f"KATAGIRI_CONFIG={values.get('KATAGIRI_CONFIG', '')}",
        "",
        "# --- Obsidian Local REST API connection ---",
        f"OBSIDIAN_TRANSPORT={values.get('OBSIDIAN_TRANSPORT', 'streamable_http')}",
        f"OBSIDIAN_MCP_URL={values.get('OBSIDIAN_MCP_URL', f'https://{OBSIDIAN_HOST}:{OBSIDIAN_PORT}/mcp/')}",
        f"OBSIDIAN_API_TOKEN={values.get('OBSIDIAN_API_TOKEN', '')}",
        "# true + OBSIDIAN_CA_BUNDLE (exported plugin cert) is the clean path;",
        "# false is the documented escape hatch for the self-signed cert.",
        f"OBSIDIAN_VERIFY_TLS={values.get('OBSIDIAN_VERIFY_TLS', 'true')}",
        f"OBSIDIAN_CA_BUNDLE={values.get('OBSIDIAN_CA_BUNDLE', '')}",
        "# Only used if OBSIDIAN_TRANSPORT=stdio (a wrapper process). Blank",
        "# is correct for the streamable_http transport this build targets.",
        f"OBSIDIAN_STDIO_COMMAND={values.get('OBSIDIAN_STDIO_COMMAND', '')}",
        f"OBSIDIAN_STDIO_ARGS={values.get('OBSIDIAN_STDIO_ARGS', '')}",
        "",
        "# --- Model layer ---",
        f"OPENROUTER_API_KEY={values.get('OPENROUTER_API_KEY', '')}",
        "# Pinned (T012) - never blank, never auto-routing (spec FR-007).",
        f"OPENROUTER_MODEL={values.get('OPENROUTER_MODEL', DEFAULT_MODEL)}",
        "",
    ]
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")


# --- steps ------------------------------------------------------------------

def step_tooling() -> list[str]:
    header("1/6 Tooling")
    problems: list[str] = []
    if shutil.which("uv"):
        ok("uv found")
    else:
        problems.append("uv not installed")
        warn("uv not found. Install (PowerShell):")
        say('      powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"')
    pin = AGENT_DIR / ".python-version"
    if pin.exists():
        ok(f"agent Python pinned to {pin.read_text(encoding='utf-8').strip()} (uv provisions it during sync)")
    return problems


def step_deps() -> list[str]:
    header("2/6 Dependencies")
    problems: list[str] = []
    if shutil.which("uv"):
        say("  running `uv sync` in agent/ (live output below) ...")
        try:
            t = time.monotonic()
            proc = subprocess.Popen(
                ["uv", "sync"], cwd=str(AGENT_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            tail: list[str] = []
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    say(f"      uv | {line}")
                    tail.append(line)
            code = proc.wait(timeout=600)
            if code == 0:
                ok(f"agent/.venv in sync ({time.monotonic() - t:.1f}s)")
            else:
                problems.append("uv sync failed")
                warn(f"uv sync failed (exit {code}) - last lines above")
        except (subprocess.TimeoutExpired, OSError) as exc:
            problems.append(f"uv sync errored: {exc}")
            warn(f"uv sync errored: {exc}")
    else:
        problems.append("skipped uv sync (no uv)")
        warn("skipping uv sync - install uv first")

    primary_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if primary_python.exists():
        ok(f"primary checkout venv found ({primary_python})")
    else:
        problems.append("primary checkout .venv missing")
        warn("primary checkout has no .venv - the agent spawns katagiri from it.")
        say(f"      fix: cd {REPO_ROOT} ; uv sync")
    return problems


def read_plugin_data() -> dict | None:
    data_json = PLUGIN_DIR / "data.json"
    if not data_json.exists():
        return None
    try:
        return json.loads(data_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"could not read plugin data.json: {exc}")
        return None


def step_obsidian(env: dict[str, str]) -> list[str]:
    header("3/6 Obsidian Local REST API plugin")
    problems: list[str] = []

    manifest_file = PLUGIN_DIR / "manifest.json"
    if not manifest_file.exists():
        problems.append("Obsidian plugin not installed")
        warn(f"plugin not found under {PLUGIN_DIR}")
        say("      Install in Obsidian: Settings > Community plugins >")
        say('      Browse > "Local REST API" (coddingtonbear) > Install + Enable.')
        return problems

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        version = manifest.get("version", "0")
        ok(f"plugin installed, version {version}")
        numbers = tuple(int(n) for n in re.findall(r"\d+", version)[:2])
        if numbers < PLUGIN_MIN_VERSION:
            problems.append(f"plugin version {version} < 5.1")
            warn("version below the 5.1 floor (D-11) - update the plugin in Obsidian")
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        problems.append("unreadable plugin manifest")
        warn(f"could not read plugin manifest: {exc}")

    # Is the endpoint up right now? (Plugin only listens while Obsidian
    # runs with this vault open - informational, not fatal.)
    try:
        with socket.create_connection((OBSIDIAN_HOST, OBSIDIAN_PORT), timeout=2):
            ok(f"endpoint listening on {OBSIDIAN_HOST}:{OBSIDIAN_PORT}")
    except OSError:
        warn(f"nothing listening on {OBSIDIAN_HOST}:{OBSIDIAN_PORT} - start Obsidian")
        say("      (open the vault at docs/katagiri/katagiri; not a setup failure,")
        say("      the agent just needs it running at run time)")

    data = read_plugin_data()

    # API token: offer to copy from the plugin's own data.json.
    if env.get("OBSIDIAN_API_TOKEN"):
        ok("OBSIDIAN_API_TOKEN already set in .env (kept)")
    elif data and data.get("apiKey"):
        say("  The vault's API token lives in the plugin's own data.json.")
        if ask_yn("Copy it into agent/.env now? (value is never printed)"):
            env["OBSIDIAN_API_TOKEN"] = str(data["apiKey"])
            ok("OBSIDIAN_API_TOKEN filled from plugin data.json")
        else:
            warn("skipped - find it in Obsidian: Settings > Local REST API > API key")
    else:
        problems.append("OBSIDIAN_API_TOKEN unavailable")
        warn("no token in .env and none found in plugin data.json.")
        say("      Get it in Obsidian: Settings > Local REST API > copy API key,")
        say("      then rerun this script and paste it at the prompt.")
        token = ask_secret("      Paste token now (or Enter to skip): ")
        if token:
            env["OBSIDIAN_API_TOKEN"] = token
            problems.pop()
            ok("OBSIDIAN_API_TOKEN set")

    # TLS: offer the clean path - export the plugin's self-signed cert so
    # verification can stay ON (instead of OBSIDIAN_VERIFY_TLS=false).
    if env.get("OBSIDIAN_CA_BUNDLE") and Path(env["OBSIDIAN_CA_BUNDLE"]).exists():
        ok("OBSIDIAN_CA_BUNDLE already set and file exists (kept)")
    elif data and isinstance(data.get("crypto"), dict) and data["crypto"].get("cert"):
        say("  The plugin serves HTTPS with a self-signed cert. Exporting that cert")
        say("  lets the agent verify TLS properly instead of OBSIDIAN_VERIFY_TLS=false.")
        if ask_yn(f"Export cert to {CERT_FILE.name} and enable TLS verification?"):
            try:
                CERT_FILE.write_text(data["crypto"]["cert"], encoding="utf-8")
                env["OBSIDIAN_CA_BUNDLE"] = str(CERT_FILE)
                env["OBSIDIAN_VERIFY_TLS"] = "true"
                ok(f"cert exported to {CERT_FILE} (public cert, not a secret)")
            except OSError as exc:
                warn(f"cert export failed: {exc}")
    elif env.get("OBSIDIAN_VERIFY_TLS", "").lower() == "false":
        warn("OBSIDIAN_VERIFY_TLS=false (documented escape hatch; kept as-is)")

    return problems


def step_openrouter(env: dict[str, str]) -> list[str]:
    header("4/6 OpenRouter")
    problems: list[str] = []

    if not env.get("OPENROUTER_MODEL"):
        env["OPENROUTER_MODEL"] = DEFAULT_MODEL
    ok(f"model pinned: {env['OPENROUTER_MODEL']}")

    if env.get("OPENROUTER_API_KEY"):
        ok("OPENROUTER_API_KEY already set in .env (kept)")
        return problems

    say("  You need an OpenRouter API key:")
    say("      1. https://openrouter.ai -> sign in -> Keys -> Create key")
    say("      2. Top up credits (Credits page) - free tier's 50 req/day is not")
    say("         enough for rehearsal + recording (T027).")
    say("      3. On the key, set a credit limit (e.g. $5) to cap spend.")
    key = ask_secret("  Paste key (input hidden; Enter to skip): ")
    if key:
        env["OPENROUTER_API_KEY"] = key
        ok("OPENROUTER_API_KEY set")
    else:
        # not added to problems: the report's REQUIRED check covers it
        warn("skipped - rerun this script when you have the key")
    return problems


def step_defaults(env: dict[str, str]) -> None:
    header("5/6 Filling remaining defaults")
    primary_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    defaults = {
        "PYTHONUTF8": "1",
        "KATAGIRI_PYTHON": str(primary_python).replace("\\", "/"),
        "KATAGIRI_MODULE": "katagiri.mcp_server",
        "OBSIDIAN_TRANSPORT": "streamable_http",
        "OBSIDIAN_MCP_URL": f"https://{OBSIDIAN_HOST}:{OBSIDIAN_PORT}/mcp/",
        "OBSIDIAN_VERIFY_TLS": "true",
        "OPENROUTER_MODEL": DEFAULT_MODEL,
    }
    for key, value in defaults.items():
        if not env.get(key):
            env[key] = value
            ok(f"{key} = {value}")
    for key, why in OK_BLANK.items():
        if not env.get(key):
            say(f"  [--] {key} left blank on purpose: {why}")


def step_report(env: dict[str, str], problems: list[str]) -> int:
    header("6/6 Report (presence only - values never printed)")
    for key in VAR_ORDER:
        value = env.get(key, "")
        if value:
            shown = "SET (hidden)" if key in SECRET_VARS else value
            say(f"  {key:24} {shown}")
        else:
            note = OK_BLANK.get(key)
            say(f"  {key:24} {'blank (ok: ' + note + ')' if note else 'BLANK'}")

    missing = [k for k in REQUIRED if not env.get(k)]
    say(f"\n  total time: {time.monotonic() - _t0:.1f}s")
    if problems or missing:
        warn("setup incomplete:")
        for item in problems:
            say(f"      - {item}")
        for item in missing:
            say(f"      - {item} still blank (needed by the agent graph, not the MCP server)")
        say("  Fill the gaps any time: python agent/scripts/setup.py")
        return 0 if STDIO_BOOTSTRAP else 1
    ok("setup complete - agent/.env is ready")
    return 0


def launch_server(env: dict[str, str]) -> int:
    """--stdio-bootstrap tail: hand stdio over to the katagiri MCP server."""
    import os

    python = env.get("KATAGIRI_PYTHON", "")
    module = env.get("KATAGIRI_MODULE", "katagiri.mcp_server")
    if not python or not Path(python).exists():
        say(f"[fatal] KATAGIRI_PYTHON not found: {python!r} - run `uv sync` at repo root")
        return 2
    child_env = dict(os.environ)
    child_env["PYTHONUTF8"] = "1"
    if env.get("KATAGIRI_CONFIG"):
        child_env["KATAGIRI_CONFIG"] = env["KATAGIRI_CONFIG"]
    say(f"[bootstrap] launching {module} ...")
    # stdin/stdout inherited untouched: they carry the MCP JSON-RPC stream.
    proc = subprocess.run([python, "-m", module], env=child_env, cwd=str(REPO_ROOT))
    return proc.returncode


def main() -> int:
    say("katagiri agent setup")
    say(f"  repo:  {REPO_ROOT}")
    say(f"  vault: {VAULT_DIR}")

    if not AGENT_DIR.is_dir() or not (AGENT_DIR / "pyproject.toml").exists():
        say(f"[fatal] agent/ project not found at {AGENT_DIR} - run from the Katagiri repo")
        return 2

    env = parse_env(ENV_FILE)
    if not env and ENV_EXAMPLE.exists():
        env = {k: v for k, v in parse_env(ENV_EXAMPLE).items() if v}

    problems: list[str] = []
    problems += step_tooling()
    problems += step_deps()
    problems += step_obsidian(env)
    problems += step_openrouter(env)
    step_defaults(env)

    write_env(env)
    ok(f"wrote {ENV_FILE}")
    code = step_report(env, problems)
    if STDIO_BOOTSTRAP:
        return launch_server(env)
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted - nothing partial was left behind unless the report said so")
        sys.exit(130)
