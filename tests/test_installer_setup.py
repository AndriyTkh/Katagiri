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

FR-010 log gaps found while writing the T005 suite (see the T005 return
message) have since been fixed under T016: a scripted Skip after ACTION
NEEDED now writes its own ``_log`` line (distinct from Abort's), and
``_print_doctor_summary`` mirrors every per-component end state (READY/
MISSING/MANUAL STEP) to the log, not just stdout. ``_ro_query_scalar`` also
now catches ``sqlite3.DatabaseError`` (the parent of ``OperationalError``),
so a corrupted-but-present db file makes ``--check`` report MISSING instead
of crashing -- see ``test_check_detects_precondition_broken_after_install``.
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


def test_check_handles_corrupted_db_file_gracefully(real_jmdict_template, tmp_path):
    """T016: a corrupted-but-present db file must report MISSING, not crash.

    Previously ``_ro_query_scalar`` only caught ``sqlite3.OperationalError``,
    not the ``sqlite3.DatabaseError`` ("file is not a database") a corrupted
    file raises, so ``--check`` crashed with an unhandled traceback. Fixed by
    broadening the except clause to ``sqlite3.DatabaseError`` (the parent of
    ``OperationalError``). Overwrites the sandbox's own db file (never the
    real one, never the vendored zip) with garbage bytes after a successful
    install, then confirms --check degrades to a graceful MISSING with a
    non-zero exit and no traceback in stderr.
    """
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    setup = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert setup.returncode == 0, setup.stdout + setup.stderr

    # Corrupt the precondition in place: overwrite with non-sqlite bytes,
    # rather than deleting it -- this exercises the DatabaseError path
    # ("file is not a database") instead of the missing-file path.
    db_path.write_bytes(b"not a sqlite database, just garbage bytes\x00\x01\x02")

    after = _run_installer(["--check"], env)
    assert after.returncode == 1, after.stdout + after.stderr
    assert re.search(r"^jmdict/kanjium import\s+MISSING", after.stdout, re.MULTILINE)
    assert "Traceback" not in after.stderr
    assert "Traceback" not in after.stdout


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

    # T016: a scripted Skip now leaves its own distinguishing log line,
    # separate from the plain step-outcome line _print_step already writes.
    log_text = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert re.search(
        r"step 4/\d+ Anki sync: operator chose Skip after ACTION NEEDED", log_text
    ), f"log missing distinguishing Skip line\n---\n{log_text}"


def test_yes_and_check_log_doctor_end_state_per_component(real_jmdict_template, tmp_path):
    """T016: the final doctor table's per-component end state must also reach the log.

    Previously only each step's own attempt(s) were logged (via _print_step);
    the doctor summary table itself was stdout-only. Confirm both a --yes run
    and a --check run mirror every row (name/status/detail) into katagiri.log.
    """
    env, cfg_path, db_path = _sandbox_env(tmp_path)
    _seed_jmdict(real_jmdict_template, db_path)

    setup = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)
    assert setup.returncode == 0, setup.stdout + setup.stderr

    log_text = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert "doctor config: READY" in log_text
    assert "doctor jmdict/kanjium import: READY" in log_text

    check = _run_installer(["--check"], env)
    assert check.returncode == 0, check.stdout + check.stderr
    log_text_after_check = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert "doctor config: READY" in log_text_after_check
    assert "doctor jmdict/kanjium import: READY" in log_text_after_check


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


# ---------------------------------------------------------------------------
# T019: a flag-only --data-home run must re-home the installer log too
# ---------------------------------------------------------------------------


def _copy_installer_script(tmp_path: Path) -> tuple[Path, Path]:
    """Copy ``installer.py`` into a throwaway ``<fake-repo>/src/katagiri/``.

    ``installer._repo_root()`` derives from ``Path(__file__).resolve().parents[2]``
    -- not the process cwd -- so running ``python -m katagiri.installer
    --data-home ...`` as a subprocess against the real checkout would make
    ``_persist_data_home_env`` write ``KATAGIRI_DATA_HOME=...`` straight into
    *this worktree's real* ``agent/.env``. Running a copy of the script from a
    fake ``<tmp>/src/katagiri/installer.py`` keeps ``_repo_root()`` -- and
    therefore that write -- inside ``tmp_path`` instead, while every actual
    import inside the script (``katagiri.config``, ``katagiri.applog``, ...)
    still resolves to the real, installed package (import resolution follows
    ``sys.path``, not the running script's own directory).
    """
    fake_repo = tmp_path / "fake-checkout"
    pkg_dir = fake_repo / "src" / "katagiri"
    pkg_dir.mkdir(parents=True)
    script = pkg_dir / "installer.py"
    script.write_text(
        (_REPO_ROOT / "src" / "katagiri" / "installer.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return fake_repo, script


def test_data_home_flag_relocates_installer_log_before_argparse_runs(tmp_path):
    """T019 regression: ``run_cli``'s ``setup_logging()`` (installer.py's
    ``__main__`` block) used to run *before* ``main()`` parsed ``--data-home``,
    so a flag-only override run's log landed under the default home instead of
    the override, and nothing under the override's ``logs/`` ever appeared.
    The fix primes ``KATAGIRI_DATA_HOME`` from ``sys.argv`` before ``run_cli``
    runs. Exercises the real ``python <installer.py>`` entry point end to end
    (not ``installer.main()`` in-process), since the bug lived in the
    ``__main__`` sequencing itself.
    """
    fake_repo, script = _copy_installer_script(tmp_path)
    local_appdata = tmp_path / "AppDataLocal"
    local_appdata.mkdir()
    data_home = tmp_path / "override-home"

    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_appdata)
    env["PYTHONUTF8"] = "1"
    env.pop("KATAGIRI_DATA_HOME", None)
    env.pop("KATAGIRI_CONFIG", None)

    proc = subprocess.run(
        [sys.executable, str(script), "--data-home", str(data_home), "--check"],
        cwd=str(fake_repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=_SHORT_TIMEOUT,
    )

    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    assert (data_home / "logs" / "katagiri.log").is_file(), (
        f"expected installer log under the --data-home override:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    # Nothing -- not even an empty logs/ dir -- may appear under the default
    # home: it must stay completely untouched by a flag-only override run.
    assert not (local_appdata / "Katagiri").exists()

    # And the *real* checkout's agent/.env (not the fake one) must never see
    # this test's throwaway override path.
    real_env_file = _REPO_ROOT / "agent" / ".env"
    if real_env_file.exists():
        assert "override-home" not in real_env_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# T020: an empty/blank KATAGIRI_DATA_HOME must fail cleanly, no traceback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["", "   "])
def test_blank_katagiri_data_home_env_exits_cleanly_without_traceback(tmp_path, bad_value):
    """T020 regression: an empty/blank ``KATAGIRI_DATA_HOME`` correctly exited
    nonzero and left the default home untouched, but also leaked a raw Python
    traceback to stderr (``config_dir()``'s ``ConfigError`` propagating
    uncaught through ``run_cli``). The fix catches ``ConfigError`` at
    ``installer.main()``'s call to ``config_path()`` and reports it the same
    clean way as an invalid ``--data-home`` flag.
    """
    local_appdata = tmp_path / "AppDataLocal"
    local_appdata.mkdir()

    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_appdata)
    env["PYTHONUTF8"] = "1"
    env["KATAGIRI_DATA_HOME"] = bad_value
    env.pop("KATAGIRI_CONFIG", None)  # KATAGIRI_CONFIG outranks it -- must not mask this

    proc = _run_installer(["--yes"], env)

    assert proc.returncode != 0, proc.stdout + proc.stderr
    combined = proc.stdout + proc.stderr
    assert "KATAGIRI_DATA_HOME" in combined
    assert "Traceback (most recent call last)" not in combined
    assert not (local_appdata / "Katagiri").exists()
