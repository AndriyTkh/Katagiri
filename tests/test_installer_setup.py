"""End-to-end installer/doctor coverage (spec 007, T005, US1).

Every test here spawns the real ``python -m katagiri.installer`` entry point
as a subprocess against a fully isolated sandbox: ``LOCALAPPDATA``,
``APPDATA`` and ``KATAGIRI_CONFIG`` are all pointed into a per-test ``tmp_path``
before the subprocess ever starts, so nothing here can read or write the
real ``%LOCALAPPDATA%\\Katagiri`` or the real study database.

Two real, GUI-launching side effects live behind the wizard's Anki/Obsidian
steps (``anki_launch.launch_anki`` / ``obsidian_launch.launch_obsidian``).
Both locate their target exe via ``LOCALAPPDATA`` (and Anki's data dir via
``APPDATA``) before ever calling ``subprocess.Popen`` -- sandboxing those two
variables is what keeps every test here from possibly popping open a real
Anki/Obsidian window on a dev machine that has them installed, without
needing to touch installer.py at all.

JMdict is never imported from ground zero here: ``real_jmdict_template``
(``tests/conftest.py``) hands back a cached, pre-imported database file which
each test that needs a "ready" install copies straight to the sandbox's
default db path (``<LOCALAPPDATA>/Katagiri/katagiri.db``) *before* invoking
the installer, so ``step_jmdict`` / ``probe_jmdict`` see existing entries and
skip the real (~20s) import subprocess entirely.

FR-010 log gaps found while writing this suite (see the two ``NOTE`` blocks
below, and the T005 return message): the interactive R/S/A prompt logs a
step's raw result *before* asking Retry/Skip/Abort, so the ACTION NEEDED line
is written whether the operator ends up retrying or skipping -- picking
Skip produces no distinguishing log line of its own (only Abort does, at
``_run_wizard_steps``'s ``except _WizardAborted`` handler). And the final
per-component doctor summary table (``_print_doctor_summary``) is printed to
stdout only; it never goes through ``_log``, so the end state of an
interactive run is not reconstructable from the log file alone -- only each
step's own attempt(s) are. Both are read-only observations; per task
instructions, installer.py is not touched here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHORT_TIMEOUT = 60
_FULL_WIZARD_TIMEOUT = 120  # several internal subprocess steps run serially


def _sandbox_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """A fresh, fully isolated env plus its config/db paths.

    Sandboxes ``LOCALAPPDATA`` (config/db/logs default home), ``APPDATA``
    (Anki profile autodetect) and ``KATAGIRI_CONFIG`` (belt-and-suspenders:
    pins the config path explicitly to the same sandbox even if some code
    path ever stopped deriving it from ``LOCALAPPDATA``). ``PYTHONUTF8=1``
    matches every other subprocess call the installer itself makes.
    """
    local_appdata = tmp_path / "AppDataLocal"
    appdata = tmp_path / "AppDataRoaming"
    local_appdata.mkdir(parents=True, exist_ok=True)
    appdata.mkdir(parents=True, exist_ok=True)
    cfg_path = local_appdata / "Katagiri" / "config.toml"
    db_path = local_appdata / "Katagiri" / "katagiri.db"

    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_appdata)
    env["APPDATA"] = str(appdata)
    env["KATAGIRI_CONFIG"] = str(cfg_path)
    env["PYTHONUTF8"] = "1"
    return env, cfg_path, db_path


def _seed_jmdict(real_jmdict_template, db_path: Path) -> None:
    """Copy the cached JMdict template to ``db_path`` -- never ground zero."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    real_jmdict_template.materialize(db_path)


def _run_installer(
    args: list[str],
    env: dict[str, str],
    *,
    input_text: str | None = None,
    timeout: int = _SHORT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "katagiri.installer", *args],
        cwd=str(_REPO_ROOT),
        env=env,
        input=input_text if input_text is not None else "",
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _log_path(local_appdata_env_value: str) -> Path:
    return Path(local_appdata_env_value) / "Katagiri" / "logs" / "katagiri.log"


# ---------------------------------------------------------------------------
# US1 AC1 / FR-001 / FR-010: --yes completes, writes config, logs every step
# ---------------------------------------------------------------------------


def test_yes_completes_with_stdin_closed_and_writes_config(real_jmdict_template, tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(["--yes"], env, input_text="", timeout=_FULL_WIZARD_TIMEOUT)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert cfg_path.exists()
    # No interactive prompt text (config prompts, R/S/A, y/N) ever printed --
    # --yes really is non-interactive end to end.
    assert "Enter to keep" not in proc.stdout
    assert "[R]etry" not in proc.stdout


def test_yes_prints_per_step_summary_for_every_step(real_jmdict_template, tmp_path):
    from katagiri import installer as installer_mod

    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    for n, label in enumerate(installer_mod.STEP_LABELS, start=1):
        assert re.search(rf"\[{n}/\d+\]\s+{re.escape(label)}\s+\.\.\.\s+\S", proc.stdout), (
            f"missing per-step summary line for step {n} ({label})\n---\n{proc.stdout}"
        )


def test_yes_logs_every_step_outcome_to_sandbox_log(real_jmdict_template, tmp_path):
    """FR-010: step label + outcome must be reconstructable from the log file."""
    from katagiri import installer as installer_mod

    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    log_path = _log_path(env["LOCALAPPDATA"])
    assert log_path.is_file(), "installer did not write to the sandboxed katagiri.log"
    log_text = log_path.read_text(encoding="utf-8")

    for n, label in enumerate(installer_mod.STEP_LABELS, start=1):
        assert re.search(
            rf"step {n}/\d+ {re.escape(label)}: (OK|SKIP|ACTION NEEDED)", log_text
        ), f"log missing outcome line for step {n} ({label})\n---\n{log_text}"


def test_yes_never_registers_a_real_scheduled_task(real_jmdict_template, tmp_path):
    """Scheduled-tasks step must be suppressed outright under --yes (never schtasks.exe)."""
    from katagiri import installer as installer_mod

    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    label = installer_mod.STEP_LABELS[7]
    assert label == "Scheduled tasks (optional)"
    assert f"{label} ... SKIP (skipped under --yes)" in proc.stdout
    log_text = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert "skipped under --yes" in log_text
    # None of the three schtasks builders' task names were ever offered.
    assert "Create scheduled task for" not in proc.stdout


# ---------------------------------------------------------------------------
# FR-001 / US1 AC1: double --yes is idempotent
# ---------------------------------------------------------------------------


def test_double_yes_is_idempotent(real_jmdict_template, tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    first = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert first.returncode == 0, first.stdout + first.stderr
    config_after_first = cfg_path.read_text(encoding="utf-8")

    second = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert second.returncode == 0, second.stdout + second.stderr
    config_after_second = cfg_path.read_text(encoding="utf-8")

    assert config_after_first == config_after_second
    assert "Config ... OK (config.toml present)" in second.stdout
    assert "Config ... OK (created config.toml)" in first.stdout


# ---------------------------------------------------------------------------
# US1 AC2 / FR-002: --check is read-only and names the failing step
# ---------------------------------------------------------------------------


def test_check_on_fresh_sandbox_is_readonly_and_nonzero(tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)

    proc = _run_installer(["--check"], env)

    assert proc.returncode == 1
    # Named failing steps, not just a bare exit code.
    assert re.search(r"^config\s+MISSING", proc.stdout, re.MULTILINE)
    assert re.search(r"^jmdict/kanjium import\s+MISSING", proc.stdout, re.MULTILINE)
    # Read-only: nothing was created.
    assert not cfg_path.exists()
    assert not db_path.exists()


def test_check_after_yes_reports_ready_and_zero(real_jmdict_template, tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    setup = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert setup.returncode == 0, setup.stdout + setup.stderr

    check = _run_installer(["--check"], env)
    assert check.returncode == 0, check.stdout + check.stderr
    assert re.search(r"^config\s+READY", check.stdout, re.MULTILINE)
    assert re.search(r"^jmdict/kanjium import\s+READY", check.stdout, re.MULTILINE)


def test_check_detects_precondition_broken_after_install(real_jmdict_template, tmp_path):
    """SC-001: breaking a step's precondition inside the sandbox makes --check fail it.

    Removes only the sandbox's own jmdict db (never the real one, never the
    vendored zip) after a successful install, then re-runs --check and
    confirms the regression is caught and named.

    NOTE: a *corrupted-but-present* db file (e.g. truncated/non-sqlite bytes)
    is not handled gracefully here -- ``_ro_query_scalar`` (installer.py
    ~328-348) only catches ``sqlite3.OperationalError``, not the
    ``sqlite3.DatabaseError`` ("file is not a database") a corrupted file
    raises, so ``--check`` crashes with an unhandled traceback instead of
    reporting MISSING. Confirmed interactively; not asserted here per the
    "don't edit installer.py, report the gap" instruction -- using a missing
    file instead exercises the same MISSING-detection path without hitting
    that crash.
    """
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    setup = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert setup.returncode == 0, setup.stdout + setup.stderr

    before = _run_installer(["--check"], env)
    assert before.returncode == 0
    assert re.search(r"^jmdict/kanjium import\s+READY", before.stdout, re.MULTILINE)

    # Break the precondition: remove the sandbox's own db file.
    db_path.unlink()

    after = _run_installer(["--check"], env)
    assert after.returncode == 1
    assert re.search(r"^jmdict/kanjium import\s+MISSING", after.stdout, re.MULTILINE)


# ---------------------------------------------------------------------------
# US1 AC3 / FR-003: interactive retry/skip/abort paths (scripted stdin)
# ---------------------------------------------------------------------------

# Config-step answers common to the interactive tests below: keep vault_path
# and the Obsidian token unset, but set anki_data_dir to a directory that
# does not exist -- step_anki's ``anki_sync`` subprocess then reliably fails
# (exit code 2, "no such collection"), giving a deterministic, fully
# sandboxed ACTION NEEDED at step 4 without touching any real app or
# triggering a slow/networked step. Anything typed after the R/S/A answer is
# left unscripted: every remaining prompt hits EOF and takes its documented
# default (decline / keep-unset), which is itself part of what's under test.
_BOGUS_ANKI_DIR = r"C:\nonexistent-katagiri-anki-test-dir"
_CONFIG_ANSWERS = f"\n{_BOGUS_ANKI_DIR}\n\n"


def test_interactive_skip_continues_and_shows_in_final_summary(real_jmdict_template, tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(
        [],
        env,
        input_text=_CONFIG_ANSWERS + "s\n",
        timeout=_FULL_WIZARD_TIMEOUT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Anki sync ... ACTION NEEDED" in proc.stdout
    assert "[R]etry / [S]kip / [A]bort setup? [S]:" in proc.stdout
    # Wizard continued past the failed step instead of stopping.
    assert "Backup rehearsal ... OK" in proc.stdout
    # The skipped step shows up in the final doctor summary.
    assert re.search(r"^anki mirror\s+MISSING\s+not synced yet", proc.stdout, re.MULTILINE)
    # Never aborted.
    assert "Setup aborted" not in proc.stdout


def test_interactive_abort_logs_step_number(real_jmdict_template, tmp_path):
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    proc = _run_installer(
        [],
        env,
        input_text=_CONFIG_ANSWERS + "a\n",
        timeout=_FULL_WIZARD_TIMEOUT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Setup aborted at step 4 (Anki sync)" in proc.stdout
    # Steps after the abort point never ran.
    assert "Backup rehearsal" not in proc.stdout

    log_text = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert "wizard aborted by operator at step 4 (Anki sync)" in log_text


def test_interactive_scheduled_tasks_step_never_registers_real_schtasks(
    real_jmdict_template, tmp_path
):
    """Interactive runs must always Skip the scheduled-tasks step in tests.

    Declining every task offer (EOF -> "" -> not "y") is the only way this
    step is exercised interactively; the assertion below confirms no task
    name was ever handed to a "y" answer that could reach schtasks.exe.
    """
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    # Answer config prompts, skip the (deliberately absent) Anki failure, and
    # leave every scheduled-tasks y/N prompt to hit EOF (declined).
    proc = _run_installer(
        [],
        env,
        input_text=_CONFIG_ANSWERS + "s\n",
        timeout=_FULL_WIZARD_TIMEOUT,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Scheduled tasks (optional) ... SKIP (no scheduled tasks created)" in proc.stdout
    assert re.search(r"^scheduled tasks\s+MANUAL STEP", proc.stdout, re.MULTILINE)
