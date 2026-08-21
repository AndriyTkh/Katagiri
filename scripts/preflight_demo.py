"""T026: pre-flight checks before recording the 005 demo.

Run this immediately before every rehearsal and before the graded take
itself (see docs/assignment/demo-setup.md's pre-flight step). It runs under
katagiri's own venv (the ``scripts/`` convention: ``sys.executable`` is
whatever interpreter invoked this file) but shells out to the agent venv's
python for anything that needs ``langchain_mcp_adapters``/``langchain_openai``
-- those packages are never installed into katagiri's own venv on purpose
(``katagiri_agent.clients``'s module docstring), so this script cannot
``import`` them directly.

Six checks, always run in this order:

1. demo port bound and distinct from the personal port (27123)
2. no stale katagiri/agent ``python.exe`` processes
3. required env keys present -- presence only, a value is never printed
4. the T011 isolation guard (``tests/test_demo_isolation.py``) passes --
   reused via a real ``pytest`` subprocess run, never duplicated here
5. the checkpoint DB's directory is writable
6. (skipped under ``--skip-live``) one real tool-call round-trip against
   each MCP connection: ``ping`` against katagiri (stdio), ``vault_list``
   against the demo Obsidian instance (streamable HTTP)

Every check prints one ``[PASS]``/``[FAIL]`` line as it runs. If anything
failed, a single actionable line per failure is printed again at the end
and the process exits 1; otherwise it exits 0. No check here ever prints a
credential value -- only key names, host:port pairs, paths, and process
ids/command lines are ever shown.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_ROOT = REPO_ROOT / "agent"
AGENT_ENV_FILE = AGENT_ROOT / ".env"
DEFAULT_AGENT_PYTHON = AGENT_ROOT / ".venv" / "Scripts" / "python.exe"
DEFAULT_CHECKPOINT_DB = AGENT_ROOT / "scratch" / "checkpoints.sqlite"
PERSONAL_OBSIDIAN_PORT = 27123

# Command-line hints that identify a katagiri or agent process, for the
# stale-process check below. Matched as plain substrings against a
# process's full command line (never against a value/secret -- these are
# module/entry-point names, not credentials).
_STALE_PROCESS_HINTS = ("katagiri.mcp_server", "katagiri_agent", "katagiri-mcp")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    actionable: str | None = None


# ---------------------------------------------------------------------------
# Small helpers shared by several checks
# ---------------------------------------------------------------------------


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` parser for ``agent/.env``.

    Not a full ``.env`` grammar -- just enough to read the keys this script
    needs for the *offline* port check (config sanity, not secrets). The
    *live* checks below never use this: they shell out to the agent venv's
    own python, which loads ``agent/.env`` for real via ``python-dotenv``.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _effective_env(*keys: str) -> dict[str, str]:
    """Process environment first, ``agent/.env`` second -- matching
    ``python-dotenv``'s own default of never overriding an already-set
    variable."""
    dotenv_values = _parse_dotenv(AGENT_ENV_FILE)
    result: dict[str, str] = {}
    for key in keys:
        if os.environ.get(key):
            result[key] = os.environ[key]
        elif dotenv_values.get(key):
            result[key] = dotenv_values[key]
    return result


# ---------------------------------------------------------------------------
# 1. Demo port bound and distinct from 27123
# ---------------------------------------------------------------------------


def check_demo_port() -> CheckResult:
    name = "demo port bound and distinct from 27123"
    env = _effective_env("OBSIDIAN_TRANSPORT", "OBSIDIAN_MCP_URL")
    transport = (env.get("OBSIDIAN_TRANSPORT") or "streamable_http").strip().lower()

    if transport == "stdio":
        return CheckResult(name, True, "OBSIDIAN_TRANSPORT=stdio -- no TCP port to check.")

    url = env.get("OBSIDIAN_MCP_URL")
    if not url:
        return CheckResult(
            name,
            False,
            "OBSIDIAN_MCP_URL is not set.",
            "Set OBSIDIAN_MCP_URL in agent/.env to the demo instance's own "
            "/mcp/ endpoint -- see docs/assignment/demo-setup.md Steps 2 and 6.",
        )

    parsed = urlsplit(url)
    port = parsed.port
    if port is None:
        return CheckResult(
            name,
            False,
            f"Could not parse a port out of OBSIDIAN_MCP_URL's host {parsed.netloc!r}.",
            "Set OBSIDIAN_MCP_URL to an explicit host:port, e.g. "
            "https://127.0.0.1:27224/mcp/.",
        )
    if port == PERSONAL_OBSIDIAN_PORT:
        return CheckResult(
            name,
            False,
            f"OBSIDIAN_MCP_URL points at port {port} -- the personal instance's port.",
            "Point OBSIDIAN_MCP_URL at the demo instance's own non-default "
            "port (e.g. 27223/27224) -- see docs/assignment/demo-setup.md Step 2.",
        )

    host = parsed.hostname or "127.0.0.1"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        bound = sock.connect_ex((host, port)) == 0

    if not bound:
        return CheckResult(
            name,
            False,
            f"Nothing is listening on {host}:{port}.",
            f"Open the demo Obsidian vault window and enable the Local REST "
            f"API plugin so something binds {host}:{port} -- see "
            "docs/assignment/demo-setup.md Steps 1-2, and run the manual "
            "netstat check in Step 5.",
        )
    return CheckResult(
        name, True, f"{host}:{port} is bound and distinct from {PERSONAL_OBSIDIAN_PORT}."
    )


# ---------------------------------------------------------------------------
# 2. No stale katagiri/agent processes
# ---------------------------------------------------------------------------


def check_no_stale_processes() -> CheckResult:
    name = "no stale katagiri/agent processes"
    ps_command = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
        "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name,
            False,
            f"Could not enumerate processes via PowerShell: {exc}",
            "Verify PowerShell (Get-CimInstance) is available on this "
            "machine, or check Task Manager by hand for leftover python.exe "
            "processes running katagiri.mcp_server or katagiri_agent.",
        )
    if proc.returncode != 0:
        return CheckResult(
            name,
            False,
            f"PowerShell process enumeration exited {proc.returncode}.",
            "Run the Get-CimInstance command above by hand to see the "
            "error, or check Task Manager for leftover python.exe processes.",
        )

    raw = proc.stdout.strip()
    if not raw:
        return CheckResult(name, True, "No python.exe processes found at all.")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CheckResult(
            name,
            False,
            f"Could not parse the PowerShell process listing: {exc}",
            "Run the Get-CimInstance command above by hand to inspect the output.",
        )

    rows = parsed if isinstance(parsed, list) else [parsed]
    this_pid = os.getpid()
    stale: list[tuple[int | None, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("ProcessId")
        cmdline = row.get("CommandLine") or ""
        if pid == this_pid:
            continue
        if any(hint in cmdline for hint in _STALE_PROCESS_HINTS):
            stale.append((pid, cmdline))

    if stale:
        listing = "; ".join(f"PID {pid}: {cmdline}" for pid, cmdline in stale)
        return CheckResult(
            name,
            False,
            f"{len(stale)} stale process(es) still running: {listing}",
            "Stop them before starting the demo (PowerShell: "
            "Stop-Process -Id <pid> -Force for each PID above) -- a "
            "leftover process can hold the fixture DB file lock or an "
            "already-bound stdio pipe.",
        )
    return CheckResult(name, True, "No stale katagiri/agent processes found.")


# ---------------------------------------------------------------------------
# 3. Required env keys present (presence only -- values never printed)
# ---------------------------------------------------------------------------


def check_katagiri_config_env() -> CheckResult:
    name = "KATAGIRI_CONFIG set (demo profile)"
    if os.environ.get("KATAGIRI_CONFIG", "").strip():
        return CheckResult(name, True, "KATAGIRI_CONFIG is set in this shell's environment.")
    return CheckResult(
        name,
        False,
        "KATAGIRI_CONFIG is not set in this shell's environment.",
        "Set KATAGIRI_CONFIG to the demo profile's config.toml before doing "
        "anything else -- see docs/assignment/demo-setup.md Step 6. Without "
        "it, katagiri silently falls back to the personal "
        "%LOCALAPPDATA%\\Katagiri profile.",
    )


_AGENT_ENV_CHECK_SRC = """
import json
from katagiri_agent import config as c

missing = []
for label, builder in (
    ("katagiri_connection (KATAGIRI_PYTHON)", c.katagiri_connection),
    ("obsidian_connection (Obsidian transport keys)", c.obsidian_connection),
    ("AgentSettings (OpenRouter keys)", c.AgentSettings.load),
):
    try:
        builder()
    except c.ConfigError as exc:
        missing.append({"label": label, "message": str(exc)})
print(json.dumps({"missing": missing}))
"""


def check_agent_env_presence(agent_python: Path) -> CheckResult:
    name = "required agent env keys present"
    if not agent_python.is_file():
        return CheckResult(
            name,
            False,
            f"Agent interpreter not found at {agent_python}.",
            "Provision agent/.venv (run `uv sync` inside agent/), or point "
            "--agent-python / KATAGIRI_AGENT_PYTHON at an interpreter with "
            "katagiri_agent installed.",
        )
    try:
        proc = subprocess.run(
            [str(agent_python), "-c", _AGENT_ENV_CHECK_SRC],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(AGENT_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name, False, f"Could not run the agent env presence check: {exc}", None
        )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        last_line = tail[-1] if tail else "<no output>"
        return CheckResult(
            name,
            False,
            f"Agent env presence check crashed: {last_line}",
            f"Run: {agent_python} -c \"from katagiri_agent import config\" "
            "to see the full traceback -- likely agent/.env or agent/.venv "
            "is incomplete.",
        )
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return CheckResult(
            name, False, f"Could not parse the agent env presence output: {exc}", None
        )

    missing = payload.get("missing") or []
    if missing:
        lines = "; ".join(f"{m['label']}: {m['message']}" for m in missing)
        return CheckResult(
            name,
            False,
            f"Missing agent env key(s) -- {lines}",
            "Fill in the missing key(s) in agent/.env (copy from "
            "agent/.env.example if needed). No value is ever shown above, "
            "only which key is missing.",
        )
    return CheckResult(
        name,
        True,
        "KATAGIRI_PYTHON, the Obsidian connection keys, and the OpenRouter "
        "keys are all present.",
    )


# ---------------------------------------------------------------------------
# 4. T011 isolation guard -- reused via a real pytest subprocess run
# ---------------------------------------------------------------------------


def check_isolation_guard() -> CheckResult:
    name = "T011 isolation guard (tests/test_demo_isolation.py)"
    test_path = REPO_ROOT / "tests" / "test_demo_isolation.py"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_path), "-q"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, False, f"Could not run the isolation guard: {exc}", None)

    if proc.returncode != 0:
        tail = proc.stdout.strip().splitlines()[-5:] or [proc.stderr.strip()]
        return CheckResult(
            name,
            False,
            "Isolation guard FAILED -- " + " | ".join(tail),
            f"Run: {sys.executable} -m pytest {test_path} -v  to see the "
            "full failure (it names the offending key, never a value).",
        )
    return CheckResult(name, True, "tests/test_demo_isolation.py passed.")


# ---------------------------------------------------------------------------
# 5. Checkpoint DB writable
# ---------------------------------------------------------------------------


def check_checkpoint_db_writable() -> CheckResult:
    name = "checkpoint DB directory writable"
    db_path_str = os.environ.get("KATAGIRI_AGENT_CHECKPOINT_DB", "").strip()
    db_path = Path(db_path_str) if db_path_str else DEFAULT_CHECKPOINT_DB

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(
            name,
            False,
            f"Could not create {db_path.parent}: {exc}",
            "Create the checkpoint directory by hand, or point "
            "KATAGIRI_AGENT_CHECKPOINT_DB at a writable path.",
        )

    probe = db_path.parent / ".preflight_write_check"
    try:
        probe.write_text("preflight", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            name,
            False,
            f"{db_path.parent} is not writable: {exc}",
            "Fix permissions on the checkpoint directory, or point "
            "KATAGIRI_AGENT_CHECKPOINT_DB at a writable path.",
        )
    return CheckResult(
        name,
        True,
        f"{db_path.parent} exists and is writable (checkpoint DB file: {db_path.name}).",
    )


# ---------------------------------------------------------------------------
# 6. Live round-trips (skipped under --skip-live)
# ---------------------------------------------------------------------------

_KATAGIRI_ROUNDTRIP_SRC = """
import asyncio, json
from langchain_mcp_adapters.client import MultiServerMCPClient
from katagiri_agent import config as c

async def main():
    try:
        conn = c.katagiri_connection()
    except c.ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return
    client = MultiServerMCPClient({"katagiri": conn})
    try:
        async with client.session("katagiri") as session:
            result = await session.call_tool("ping", {})
        ok = not result.isError
        print(json.dumps({"ok": ok, "error": None if ok else "ping reported isError"}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))

asyncio.run(main())
"""

_OBSIDIAN_ROUNDTRIP_SRC = """
import asyncio, json
from langchain_mcp_adapters.client import MultiServerMCPClient
from katagiri_agent import config as c

async def main():
    try:
        conn = c.obsidian_connection()
    except c.ConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return
    client = MultiServerMCPClient({"obsidian": conn})
    try:
        async with client.session("obsidian") as session:
            result = await session.call_tool("vault_list", {})
        ok = not result.isError
        print(json.dumps({"ok": ok, "error": None if ok else "vault_list reported isError"}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))

asyncio.run(main())
"""


def _run_roundtrip(
    agent_python: Path, source: str, label: str, actionable_hint: str
) -> CheckResult:
    name = f"real tool-call round-trip -- {label}"
    if not agent_python.is_file():
        return CheckResult(name, False, f"Agent interpreter not found at {agent_python}.", None)
    try:
        proc = subprocess.run(
            [str(agent_python), "-c", source],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(AGENT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, False, f"{label} round-trip timed out after 30s.", actionable_hint)
    except OSError as exc:
        return CheckResult(name, False, f"Could not run the {label} round-trip: {exc}", None)

    lines = proc.stdout.strip().splitlines()
    if not lines:
        tail = proc.stderr.strip().splitlines()[-3:] or ["<no output>"]
        return CheckResult(
            name, False, f"{label} round-trip produced no output -- {' | '.join(tail)}", actionable_hint
        )
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return CheckResult(
            name, False, f"{label} round-trip output unparseable: {lines[-1][:200]}", actionable_hint
        )
    if not payload.get("ok"):
        return CheckResult(name, False, f"{label} round-trip failed: {payload.get('error')}", actionable_hint)
    return CheckResult(name, True, f"{label} round-trip succeeded.")


def check_katagiri_roundtrip(agent_python: Path) -> CheckResult:
    return _run_roundtrip(
        agent_python,
        _KATAGIRI_ROUNDTRIP_SRC,
        "katagiri (stdio, ping)",
        "Check KATAGIRI_PYTHON/KATAGIRI_CONFIG in agent/.env (or this "
        "shell's environment) and that `python -m katagiri.mcp_server` "
        "starts cleanly by hand.",
    )


def check_obsidian_roundtrip(agent_python: Path) -> CheckResult:
    return _run_roundtrip(
        agent_python,
        _OBSIDIAN_ROUNDTRIP_SRC,
        "obsidian (demo vault, vault_list)",
        "Start the demo Obsidian vault window with the Local REST API "
        "plugin enabled and reachable at OBSIDIAN_MCP_URL -- see "
        "docs/assignment/demo-setup.md Steps 1-4.",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help=(
            "Run only the offline checks (port, processes, env presence, "
            "isolation guard, checkpoint dir) -- no MCP round-trips. Never "
            "use this in place of a full run right before recording."
        ),
    )
    parser.add_argument(
        "--agent-python",
        default=os.environ.get("KATAGIRI_AGENT_PYTHON", str(DEFAULT_AGENT_PYTHON)),
        help="Interpreter with katagiri_agent installed (default: agent/.venv relative to the repo root).",
    )
    args = parser.parse_args(argv)
    agent_python = Path(args.agent_python)

    checks = [
        check_demo_port(),
        check_no_stale_processes(),
        check_katagiri_config_env(),
        check_agent_env_presence(agent_python),
        check_isolation_guard(),
        check_checkpoint_db_writable(),
    ]
    if not args.skip_live:
        checks.append(check_katagiri_roundtrip(agent_python))
        checks.append(check_obsidian_roundtrip(agent_python))

    failures: list[CheckResult] = []
    for result in checks:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
        if not result.ok:
            failures.append(result)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for result in failures:
            print(f"  - {result.name}: {result.actionable or result.detail}")
        return 1

    print("All pre-flight checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
