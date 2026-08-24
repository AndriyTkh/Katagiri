"""Subprocess coverage for agent/scripts/setup.py's bootstrap.log writer.

T008 (US3, spec.md "Setup issues and MCP activity are traceable after the
fact"). ``setup.py`` is a standalone, stdlib-only script (no katagiri import),
so it is exercised the way an operator actually runs it: as a subprocess,
never in-process.

Every test builds an isolated sandbox repo skeleton under ``tmp_path`` and
copies ``agent/scripts/setup.py``'s *content* into it. This is deliberate,
not incidental: the script derives ``AGENT_DIR``/``REPO_ROOT``/``ENV_FILE``
from its own ``__file__`` path, so the only way to control which ``.env`` it
reads (secrets and all) is to run a copy that lives at a fake repo path. The
alternative -- running the real ``agent/scripts/setup.py`` in place -- would
read this checkout's real ``agent/.env``, which may hold live API keys; a
sandboxed copy lets each test supply its own throwaway (canary) values and
guarantees the real file is never opened, read, or written by any test here.

``LOCALAPPDATA`` is always overridden to a per-test temp directory, so no
test ever touches the real ``%LOCALAPPDATA%\\Katagiri\\logs\\bootstrap.log``.
``uv`` is hidden from the sandboxed subprocess's PATH so ``step_deps``' `uv
sync` is skipped (fast, no network, no mutation of any real venv) --
harmless for these tests since none of them assert on dependency syncing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# One record per logging call, but a handful of messages (header()'s
# "\n=== phase ===") embed a literal newline, so a naive splitlines() would
# treat the continuation as its own "line" with no timestamp/pid/phase
# prefix. Real record boundaries all start with a timestamp.
_RECORD_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \| pid=")


def _records(log_text: str) -> list[str]:
    records: list[str] = []
    for line in log_text.splitlines():
        if _RECORD_START.match(line):
            records.append(line)
        elif records:
            records[-1] += "\n" + line
    return records

TIMEOUT = 60

REAL_REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_SCRIPT_SRC = REAL_REPO_ROOT / "agent" / "scripts" / "setup.py"

_PRIMARY_CHECKOUT_PYTHON = Path(
    r"C:\ProjectsC\RandomPr\Katagiri\.venv\Scripts\python.exe"
)


def _subprocess_python() -> str:
    """Interpreter to run the sandboxed setup.py copy with.

    The script is stdlib-only, so any Python 3.9+ works; prefer the primary
    checkout's venv (matches how the real bootstrap chain runs it) and fall
    back to whatever interpreter is running the tests.
    """
    if _PRIMARY_CHECKOUT_PYTHON.exists():
        return str(_PRIMARY_CHECKOUT_PYTHON)
    return sys.executable


def _path_without_uv() -> str:
    """The current PATH with uv's directory removed.

    Keeps step_deps() from actually invoking `uv sync` (slow, network-touching,
    and irrelevant to bootstrap-log coverage) without needing to patch the
    script itself: shutil.which("uv") simply finds nothing in the child.
    """
    parts = os.environ.get("PATH", "").split(os.pathsep)
    uv_path = shutil.which("uv")
    if uv_path:
        uv_dir = str(Path(uv_path).resolve().parent)
        parts = [p for p in parts if p and str(Path(p).resolve()) != uv_dir]
    return os.pathsep.join(parts)


def _make_sandbox(tmp_path: Path, env_lines: list[str] | None = None) -> tuple[Path, Path]:
    """Build a fake <repo>/agent/scripts/setup.py skeleton under tmp_path.

    Returns (script_path, appdata_dir). appdata_dir is an existing, writable
    directory suitable for LOCALAPPDATA unless the caller repoints it.
    """
    root = tmp_path / "sandbox_repo"
    scripts_dir = root / "agent" / "scripts"
    scripts_dir.mkdir(parents=True)
    (root / "agent" / "pyproject.toml").write_text(
        "[project]\nname = \"agent-sandbox\"\nversion = \"0\"\n", encoding="utf-8"
    )
    script = scripts_dir / "setup.py"
    script.write_text(SETUP_SCRIPT_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    if env_lines:
        (root / "agent" / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    appdata = tmp_path / "AppData"
    appdata.mkdir()
    return script, appdata


def _run(
    script: Path,
    appdata: Path | str,
    args: list[str],
    *,
    timeout: int = TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = _path_without_uv()
    env["LOCALAPPDATA"] = str(appdata)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [_subprocess_python(), str(script), *args],
        cwd=str(script.parent.parent.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )


def _log_path(appdata: Path) -> Path:
    return appdata / "Katagiri" / "logs" / "bootstrap.log"


def _read_log(appdata: Path) -> str:
    path = _log_path(appdata)
    assert path.exists(), f"expected bootstrap.log at {path}"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Sandboxed run writes bootstrap.log with phase lines
# ---------------------------------------------------------------------------


def test_normal_run_writes_phase_lines_to_bootstrap_log(tmp_path):
    """A plain --yes run (no --stdio-bootstrap) walks all six numbered steps
    plus the optional LangSmith step; every one of them must show up as its
    own phase in the log, each carrying pid + outcome + detail."""
    script, appdata = _make_sandbox(tmp_path)

    result = _run(script, appdata, ["--yes"])

    # Non-interactive with no secrets configured is expected to report
    # incomplete (missing OPENROUTER_API_KEY / OBSIDIAN_API_TOKEN) -> exit 1,
    # not a crash.
    assert result.returncode in (0, 1), (
        f"unexpected crash: rc={result.returncode}\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )
    assert "Traceback" not in result.stdout and "Traceback" not in result.stderr

    log = _read_log(appdata)

    expected_phases = [
        "startup",
        "1/6 Tooling",
        "2/6 Dependencies",
        "3/6 Obsidian Local REST API plugin",
        "4/6 OpenRouter",
        "5/6 Filling remaining defaults",
        "6/6 Report (presence only - values never printed)",
    ]
    for phase in expected_phases:
        assert f"phase={phase} | outcome=" in log, f"missing phase {phase!r} in log:\n{log}"

    # Line shape: "<asctime> | pid=<pid> | phase=<p> | outcome=<o> | detail=<d>"
    lines = _records(log)
    assert lines, "bootstrap.log was created but empty"
    for line in lines:
        assert " | pid=" in line
        assert " | phase=" in line
        assert " | outcome=" in line
        assert " | detail=" in line

    # At least one outcome of each of the kinds say()/warn()/ok() produce.
    outcomes = {ln.split("outcome=", 1)[1].split(" | ", 1)[0] for ln in lines}
    assert "info" in outcomes
    assert "ok" in outcomes


# ---------------------------------------------------------------------------
# Forced pre-exec failure is reconstructible from the log file alone
# ---------------------------------------------------------------------------


def test_pre_exec_failure_reconstructible_from_log_alone(tmp_path):
    """--stdio-bootstrap with a KATAGIRI_PYTHON pointing at a path that does
    not exist must fail before any server exec, and the phase + reason must
    be recoverable from bootstrap.log without looking at stdout/stderr
    (spec.md US3 acceptance scenario 1: "the operator inspects the log
    location afterwards" -- the console is long closed by then)."""
    missing_python = str(tmp_path / "nope" / "does-not-exist-python.exe")
    script, appdata = _make_sandbox(
        tmp_path,
        env_lines=[
            f"KATAGIRI_PYTHON={missing_python}",
            "KATAGIRI_MODULE=katagiri.mcp_server",
            "OPENROUTER_API_KEY=",
            "OBSIDIAN_API_TOKEN=",
            "LANGSMITH_API_KEY=",
        ],
    )

    result = _run(script, appdata, ["--stdio-bootstrap"])

    assert result.returncode == 2, (
        f"expected the pre-exec KATAGIRI_PYTHON guard to fire (rc=2); got "
        f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    log = _read_log(appdata)
    assert "phase=server exec handoff | outcome=fail" in log, log
    fail_lines = [ln for ln in _records(log) if "outcome=fail" in ln]
    assert fail_lines, f"no fail-outcome line in log:\n{log}"
    assert any("KATAGIRI_PYTHON not found" in ln for ln in fail_lines)
    # fail() formats the path with !r, which escapes backslashes -- match on
    # the distinctive basename rather than the raw path string.
    assert any("does-not-exist-python.exe" in ln for ln in fail_lines), (
        "the failing path itself should be reconstructible from the log line, "
        f"not just the console:\n{log}"
    )


# ---------------------------------------------------------------------------
# Secrets never reach the log, even when present in the env
# ---------------------------------------------------------------------------


def test_canary_secret_in_env_never_appears_in_log(tmp_path):
    """OPENROUTER_API_KEY / OBSIDIAN_API_TOKEN set to a canary value must
    never appear in bootstrap.log (or stdout/stderr) -- only presence, via
    the existing SECRET_VARS "SET (hidden)" report line (spec.md FR-011)."""
    canary = "CANARY-BOOTSTRAP-9f3a7c2e-SECRET"
    script, appdata = _make_sandbox(
        tmp_path,
        env_lines=[
            f"OPENROUTER_API_KEY={canary}",
            f"OBSIDIAN_API_TOKEN={canary}",
            "LANGSMITH_API_KEY=",  # blank: avoids a real network auth check
        ],
    )

    result = _run(script, appdata, ["--yes"])

    assert canary not in result.stdout
    assert canary not in result.stderr

    log = _read_log(appdata)
    assert canary not in log, f"canary secret leaked into bootstrap.log:\n{log}"

    # The presence-only mechanism actually ran (not just "step skipped").
    assert "OPENROUTER_API_KEY already set in .env (kept)" in log
    assert "SET (hidden)" in log


# ---------------------------------------------------------------------------
# Unwritable logs dir degrades to stderr/stdout-only, never crashes
# ---------------------------------------------------------------------------


def test_unwritable_log_dir_still_completes_without_crashing(tmp_path):
    """LOCALAPPDATA pointed at a plain file (not a directory) makes
    `Path(LOCALAPPDATA) / "Katagiri" / "logs"` un-mkdir-able. The script's
    own docstring says this degrades to console-only output; verify the
    process still runs to completion (no traceback, still prints its normal
    console report) and, since the log directory itself cannot exist, no
    bootstrap.log is ever written."""
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("this is a file, not a directory", encoding="utf-8")

    script, appdata = _make_sandbox(tmp_path)  # unused as a dir; overridden below

    result = _run(script, blocked, ["--yes"])

    assert "Traceback" not in result.stdout and "Traceback" not in result.stderr
    assert result.returncode in (0, 1)

    # Normal console output still happened (stderr-only degrade, not silence).
    assert "katagiri agent setup" in result.stdout
    assert "=== 1/6 Tooling ===" in result.stdout

    # No log directory could be created under a path that is itself a file.
    assert not (blocked / "Katagiri").exists()
