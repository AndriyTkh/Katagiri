r"""Installer/doctor coverage for the browser companion step (spec 008, T007).

Every subprocess test here spawns the real ``python -m katagiri.installer``
entry point against a fully isolated sandbox, mirroring
``tests/test_installer_setup.py``'s harness idioms (``_sandbox_env`` /
``_run_installer`` / ``_log_path``, duplicated here rather than imported --
this suite does not import private helpers across test files, and neither
does the file it mirrors): ``LOCALAPPDATA``, ``APPDATA`` and
``KATAGIRI_CONFIG`` are all pointed into a per-test ``tmp_path`` before the
subprocess ever starts, so nothing here can read or write the real
``%LOCALAPPDATA%\Katagiri`` or a real browser profile. A few tests exercise
``step_companions`` directly, in-process, with a scripted ``prompt`` callable
and ``monkeypatch.setenv`` for ``LOCALAPPDATA``/``APPDATA`` -- the notes for
this task call that out as the robust way to script "a companion appears
between the two checks" without racing a subprocess's synchronous stdin.

Covers (spec 008 US1/US2, SC-003/SC-004, quickstart.md SS2-3):

* ``--check`` prints all three companion rows with their evidence.
* ``doctor_exit_code()`` is unaffected by any combination of companion
  verdicts (a named ``exit_code`` test, since the TG3 gate greps for it).
* ``--yes`` runs the new step with stdin closed, prompts for nothing,
  completes, and shows the step in the summary (FR-008).
* The interactive re-check loop picks up a companion that appears in the
  synthetic profile tree between two checks, and the skip path surfaces in
  the step's own result.
* The install handoff URL for an absent companion appears verbatim.
* The mokuro shared secret planted in the sandbox config never reaches
  stdout, stderr, or the sandboxed log file.

A bug was found while writing this suite -- ``installer.RawConfig`` had no
``mokuro_shared_secret`` field and ``read_raw_config`` never populated one,
so ``mokuro_companion_status(cfg)`` (which reads that attribute via
``getattr(cfg, "mokuro_shared_secret", None)``) always saw ``None`` and
reported the mokuro row as "(unset)" regardless of what was actually in
config.toml. Fixed in ``installer.py``'s ``RawConfig``/``read_raw_config``
(T007 follow-up); see ``test_mokuro_row_reflects_configured_secret`` below.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from katagiri import installer as installer_mod
from katagiri.companions import ASBPLAYER_QUERY, YOMITAN_QUERY

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHORT_TIMEOUT = 60
_FULL_WIZARD_TIMEOUT = 60

_YOMITAN_ID = YOMITAN_QUERY.chromium_ids[0]
_ASBPLAYER_ID = ASBPLAYER_QUERY.chromium_ids[0]

_COMPANION_ROW_NAMES = ("Yomitan", "asbplayer", "mokuro page-change bridge")


# ---------------------------------------------------------------------------
# Harness (mirrors tests/test_installer_setup.py's idioms; see module docstring)
# ---------------------------------------------------------------------------


def _sandbox_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path, Path]:
    """A fresh, fully isolated env plus its config/db/LOCALAPPDATA/APPDATA paths.

    Sandboxes ``LOCALAPPDATA`` (config/db/logs home *and* the Chromium-family
    browser roots this test plants), ``APPDATA`` (Firefox root / Anki
    autodetect) and ``KATAGIRI_CONFIG``. ``PYTHONUTF8=1`` matches every other
    subprocess call the installer itself makes.
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
    return env, cfg_path, db_path, local_appdata, appdata


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


def _make_chrome_default_profile(local_appdata: Path, *, extension_ids: tuple[str, ...] = ()) -> Path:
    """Plant a synthetic ``Chrome/Default`` profile under the sandboxed LOCALAPPDATA.

    Enough for ``companions.enumerate_browser_roots``/``enumerate_chromium_profiles``
    to find one real profile (so an id that is *not* planted resolves to
    ``absent``, never ``undetermined`` -- see the module's verdict rules).
    Each id in ``extension_ids`` gets an empty ``Extensions/<id>/`` directory,
    which is the sole primary signal the detector reads (no ``Preferences``
    file is written -- its absence only costs enabled/disabled detail, never
    the verdict).
    """
    profile = local_appdata / "Google" / "Chrome" / "User Data" / "Default"
    profile.mkdir(parents=True, exist_ok=True)
    for ext_id in extension_ids:
        (profile / "Extensions" / ext_id).mkdir(parents=True, exist_ok=True)
    return profile


def _write_config_with_secret(cfg_path: Path, secret: str) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(f'mokuro_shared_secret = "{secret}"\n', encoding="utf-8")


# ---------------------------------------------------------------------------
# US1 / FR-001: --check prints all three companion rows with their evidence
# ---------------------------------------------------------------------------


def test_check_prints_three_companion_rows_with_evidence(tmp_path):
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    _make_chrome_default_profile(local_appdata, extension_ids=(_YOMITAN_ID,))

    proc = _run_installer(["--check"], env)

    # Read-only: nothing under the synthetic profile tree was touched, and
    # --check never creates config/db (spec FR-002/FR-009).
    assert not cfg_path.exists()
    assert not db_path.exists()

    for name in _COMPANION_ROW_NAMES:
        assert re.search(rf"^{re.escape(name)}\s", proc.stdout, re.MULTILINE), (
            f"missing doctor row for {name!r}\n---\n{proc.stdout}"
        )

    # Yomitan: planted extension id -> READY, evidence names the profile.
    assert re.search(r"^Yomitan\s+READY\s+.*Chrome/Default", proc.stdout, re.MULTILINE), proc.stdout
    # asbplayer: same profile scanned, id not planted -> MANUAL STEP (never
    # MISSING -- an absent optional companion must not move the exit code,
    # spec FR-004/SC-004), evidence names the profile that was searched.
    assert re.search(r"^asbplayer\s+MANUAL STEP\s+.*Chrome/Default", proc.stdout, re.MULTILINE), proc.stdout
    # mokuro: configuration-readiness row, never MISSING either.
    assert re.search(
        r"^mokuro page-change bridge\s+MANUAL STEP\s+.*mokuro_shared_secret is \(unset\)",
        proc.stdout,
        re.MULTILINE,
    ), proc.stdout


def test_check_on_machine_with_no_browsers_never_prints_bare_missing(tmp_path):
    """US3 / SC-006: no supported browser root -> 'could not determine', never MISSING."""
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    # No Chrome/Firefox tree planted at all.

    proc = _run_installer(["--check"], env)

    for name in ("Yomitan", "asbplayer"):
        assert not re.search(rf"^{re.escape(name)}\s+MISSING", proc.stdout, re.MULTILINE), proc.stdout
        assert re.search(
            rf"^{re.escape(name)}\s+MANUAL STEP\s+.*no supported browser data directory found",
            proc.stdout,
            re.MULTILINE,
        ), proc.stdout


# ---------------------------------------------------------------------------
# exit_code (gate greps for this name -- see quickstart.md SS3 / SC-004)
# ---------------------------------------------------------------------------


def test_doctor_exit_code_identical_all_companions_absent_vs_all_present():
    """SC-004: absence/presence of a companion never flips doctor_exit_code()."""
    base_rows = [
        installer_mod.ComponentStatus("config", "READY", "config.toml present"),
        installer_mod.ComponentStatus("jmdict/kanjium import", "READY", "1 entries"),
    ]
    all_absent = base_rows + [
        installer_mod.ComponentStatus(name, "MANUAL STEP", "absent") for name in _COMPANION_ROW_NAMES
    ]
    all_present = base_rows + [
        installer_mod.ComponentStatus(name, "READY", "present") for name in _COMPANION_ROW_NAMES
    ]
    mixed = base_rows + [
        installer_mod.ComponentStatus(_COMPANION_ROW_NAMES[0], "READY", "present"),
        installer_mod.ComponentStatus(_COMPANION_ROW_NAMES[1], "MANUAL STEP", "undetermined"),
        installer_mod.ComponentStatus(_COMPANION_ROW_NAMES[2], "MANUAL STEP", "absent"),
    ]

    code_absent = installer_mod.doctor_exit_code(all_absent)
    code_present = installer_mod.doctor_exit_code(all_present)
    code_mixed = installer_mod.doctor_exit_code(mixed)

    assert code_absent == code_present == code_mixed == 0

    # And the property holds independently of whatever the *other* rows say:
    # a genuine MISSING elsewhere still trips the code, identically, no
    # matter what the three companion rows read.
    broken_base = [
        installer_mod.ComponentStatus("config", "MISSING", "does not exist yet"),
    ]
    broken_absent = broken_base + [
        installer_mod.ComponentStatus(name, "MANUAL STEP", "absent") for name in _COMPANION_ROW_NAMES
    ]
    broken_present = broken_base + [
        installer_mod.ComponentStatus(name, "READY", "present") for name in _COMPANION_ROW_NAMES
    ]
    assert installer_mod.doctor_exit_code(broken_absent) == installer_mod.doctor_exit_code(broken_present) == 1


def test_check_exit_code_on_sandbox_with_no_browsers_matches_non_companion_rows_only(tmp_path):
    """SC-004, subprocess form: --check's real exit code equals what the
    non-companion rows alone would have produced on the same sandbox --
    i.e. the companion rows contribute nothing to the exit code (never a
    git-stash comparison; the pre-008 code path is reconstructed from the
    same run's own status list instead, per the task's implementation note).
    """
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    # Fresh sandbox: config/jmdict are MISSING, which is exactly the
    # pre-008 nonzero-exit case this test pins against.

    cfg = installer_mod.read_raw_config(cfg_path)
    all_statuses = installer_mod.collect_doctor_statuses(cfg, _REPO_ROOT)
    companion_names = set(_COMPANION_ROW_NAMES)
    non_companion = [s for s in all_statuses if s.name not in companion_names]
    companion_rows = [s for s in all_statuses if s.name in companion_names]
    assert len(companion_rows) == 3, all_statuses

    code_with_companions = installer_mod.doctor_exit_code(all_statuses)
    code_without_companions = installer_mod.doctor_exit_code(non_companion)
    assert code_with_companions == code_without_companions

    proc = _run_installer(["--check"], env)
    assert proc.returncode == code_with_companions == code_without_companions == 1, proc.stdout


# ---------------------------------------------------------------------------
# FR-008: --yes runs the new step non-interactively
# ---------------------------------------------------------------------------


def test_yes_runs_companion_step_with_stdin_closed_no_prompt_and_shows_summary(real_jmdict_template, tmp_path):
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    real_jmdict_template.materialize(db_path)
    # No browser tree planted: every companion is either undetermined (no
    # browser found) or absent (mokuro's secret is unset) -- never present --
    # so the step must land on SKIP, never ACTION NEEDED, under --yes.

    proc = _run_installer(["--yes"], env, input_text="", timeout=_FULL_WIZARD_TIMEOUT)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # No prompt text of any kind -- FR-008: no prompt, no wait.
    assert "[R]e-check" not in proc.stdout
    assert "[S]kip?" not in proc.stdout
    # The step shows up in the per-step summary, addressed by its exact label
    # and step number ([11/12]) with the detail the task description pins.
    assert re.search(
        r"\[11/12\]\s+Browser companion check \(optional\)\s+\.\.\.\s+SKIP\s+"
        r"\(Yomitan: undetermined; asbplayer: undetermined; "
        r"mokuro page-change bridge: absent\)",
        proc.stdout,
    ), proc.stdout

    log_text = _log_path(env["LOCALAPPDATA"]).read_text(encoding="utf-8")
    assert re.search(r"step 11/\d+ Browser companion check \(optional\): SKIP", log_text), log_text


def test_yes_never_blocks_never_fails_when_every_companion_is_present(real_jmdict_template, tmp_path):
    """FR-008's other half: --yes must finish OK (not SKIP) when everything is present."""
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    real_jmdict_template.materialize(db_path)
    _make_chrome_default_profile(local_appdata, extension_ids=(_YOMITAN_ID, _ASBPLAYER_ID))
    _write_config_with_secret(cfg_path, "a-real-looking-secret-value")

    proc = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Yomitan/asbplayer both present, and mokuro_shared_secret is genuinely
    # set in config.toml, so all three companions read "present" -- the step
    # must finish OK, not SKIP (FR-008's other half).
    assert re.search(r"\[11/12\]\s+Browser companion check \(optional\)\s+\.\.\.\s+OK", proc.stdout), proc.stdout
    assert "Yomitan: present" in proc.stdout
    assert "asbplayer: present" in proc.stdout
    assert "mokuro page-change bridge: present" in proc.stdout


# ---------------------------------------------------------------------------
# US2 acceptance 2/3: interactive re-check flip and skip (in-process, per the
# task's implementation note -- a scripted prompt callable plants the
# extension between the two detection passes, avoiding any race with a
# subprocess's synchronous stdin).
# ---------------------------------------------------------------------------


def _companion_cfg(tmp_path: Path) -> installer_mod.RawConfig:
    return installer_mod.RawConfig(
        config_file=tmp_path / "config.toml",
        scratch_root=tmp_path / "scratch",
        db_path=tmp_path / "katagiri.db",
    )


def test_interactive_recheck_flips_row_when_extension_appears_between_checks(tmp_path, monkeypatch):
    local_appdata = tmp_path / "AppDataLocal"
    appdata = tmp_path / "AppDataRoaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(appdata))
    appdata.mkdir(parents=True, exist_ok=True)
    # A real profile exists from the first check onward, but Yomitan's id is
    # not there yet -> the first detection pass reads "absent", not
    # "undetermined" (spec US3: a real, searched, empty profile is a
    # different answer from "nothing to search").
    profile = _make_chrome_default_profile(local_appdata)

    calls = {"n": 0}

    def scripted_prompt(_text: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            # US2 acceptance 2: the operator installs the extension in their
            # browser, then asks for a re-check -- simulated here by planting
            # the extension directory the instant before answering "r".
            (profile / "Extensions" / _YOMITAN_ID).mkdir(parents=True, exist_ok=True)
            return "r"
        # This cfg never sets mokuro_shared_secret, so mokuro's row stays
        # "absent" and the loop cannot exit via all_present -- the operator's
        # only way out on the second round is Skip (US2 acceptance 3).
        return "s"

    cfg = _companion_cfg(tmp_path)
    result = installer_mod.step_companions(cfg, assume_yes=False, prompt=scripted_prompt)

    assert result.status == "SKIP"
    assert "Yomitan: present" in result.detail
    assert "asbplayer: absent" in result.detail
    assert calls["n"] == 2  # re-check, then skip -- no extra prompt round


def test_interactive_recheck_prints_absent_then_present_for_the_same_row(tmp_path, monkeypatch, capsys):
    """Same flip as above, but asserts on the printed report ordering, since
    US2 acceptance 2 is about what the operator *sees* re-check do, not just
    the final StepResult.
    """
    local_appdata = tmp_path / "AppDataLocal"
    appdata = tmp_path / "AppDataRoaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(appdata))
    appdata.mkdir(parents=True, exist_ok=True)
    profile = _make_chrome_default_profile(local_appdata)

    calls = {"n": 0}

    def scripted_prompt(_text: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            (profile / "Extensions" / _YOMITAN_ID).mkdir(parents=True, exist_ok=True)
            return "r"
        return "s"

    cfg = _companion_cfg(tmp_path)
    installer_mod.step_companions(cfg, assume_yes=False, prompt=scripted_prompt)

    out = capsys.readouterr().out
    first_absent = out.find("Yomitan: absent")
    later_present = out.find("Yomitan: present")
    assert first_absent != -1, out
    assert later_present != -1, out
    assert first_absent < later_present, out


def test_interactive_skip_ends_step_without_recheck_and_never_installs_anything(tmp_path, monkeypatch):
    """US2 acceptance 3: choosing Skip (default/EOF) ends the step with the
    outcome so far, and -- the load-bearing boundary check -- never touches
    the synthetic profile tree (no install of any kind, spec FR-006).
    """
    local_appdata = tmp_path / "AppDataLocal"
    appdata = tmp_path / "AppDataRoaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(appdata))
    appdata.mkdir(parents=True, exist_ok=True)
    profile = _make_chrome_default_profile(local_appdata)
    before = sorted(p.relative_to(local_appdata) for p in local_appdata.rglob("*"))

    cfg = _companion_cfg(tmp_path)
    calls = {"n": 0}

    def eof_like_prompt(_text: str) -> str:
        calls["n"] += 1
        return ""  # empty answer == the documented default == Skip

    result = installer_mod.step_companions(cfg, assume_yes=False, prompt=eof_like_prompt)

    assert result.status == "SKIP"
    assert calls["n"] == 1  # asked exactly once, then skipped -- no re-check loop
    assert "Yomitan: absent" in result.detail
    after = sorted(p.relative_to(local_appdata) for p in local_appdata.rglob("*"))
    assert before == after, "step_companions must never create/modify anything under a browser profile"
    _ = profile  # only used to assert on its ancestry above


# ---------------------------------------------------------------------------
# US2 acceptance 1 / FR-005: the handoff URL for an absent companion appears
# verbatim in the output.
# ---------------------------------------------------------------------------


def test_absent_companion_handoff_urls_appear_verbatim(real_jmdict_template, tmp_path):
    from katagiri.companions import ASBPLAYER_ENTRY, YOMITAN_ENTRY

    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    real_jmdict_template.materialize(db_path)
    # A real, scanned profile with neither extension installed -> both rows
    # verdict as "absent" (not "undetermined"), which is the only verdict the
    # handoff is printed for (spec FR-005 says "for every companion reported
    # absent"; US3 deliberately withholds the handoff for "could not tell").
    _make_chrome_default_profile(local_appdata)

    proc = _run_installer(["--yes"], env, timeout=_FULL_WIZARD_TIMEOUT)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert YOMITAN_ENTRY.url in proc.stdout, proc.stdout
    assert ASBPLAYER_ENTRY.url in proc.stdout, proc.stdout
    # And Katagiri never claims to have acted on either URL: no code path
    # here launches a browser or downloads anything (spec FR-006).
    assert "Popen" not in proc.stdout


# ---------------------------------------------------------------------------
# FR-014: the mokuro shared secret never leaks.
# ---------------------------------------------------------------------------


def test_mokuro_shared_secret_never_appears_in_check_output_or_log(tmp_path):
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    secret = "KATAGIRI-TEST-SECRET-9f3a-DO-NOT-LEAK"
    _write_config_with_secret(cfg_path, secret)

    proc = _run_installer(["--check"], env)

    assert secret not in proc.stdout
    assert secret not in proc.stderr
    log_path = _log_path(env["LOCALAPPDATA"])
    if log_path.exists():
        assert secret not in log_path.read_text(encoding="utf-8")
    # The installer's existing (set)/(unset) idiom is what should appear
    # instead -- never the value.
    assert "(set)" in proc.stdout or "(unset)" in proc.stdout


def test_mokuro_shared_secret_never_appears_via_interactive_companion_step(tmp_path, monkeypatch):
    """Same FR-014 boundary, exercised through step_companions directly."""
    local_appdata = tmp_path / "AppDataLocal"
    appdata = tmp_path / "AppDataRoaming"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setenv("APPDATA", str(appdata))
    appdata.mkdir(parents=True, exist_ok=True)

    secret = "KATAGIRI-TEST-SECRET-INTERACTIVE-DO-NOT-LEAK"
    # installer.RawConfig is frozen and has no mokuro_shared_secret field (the
    # bug this file documents); simulate "as if it were wired up" with a
    # plain namespace instead, since mokuro_companion_status only ever needs
    # a `.mokuro_shared_secret` attribute (see its docstring: "anything
    # exposing a mokuro_shared_secret attribute").
    from types import SimpleNamespace

    cfg_with_secret = SimpleNamespace(mokuro_shared_secret=secret)

    result = installer_mod.step_companions(cfg_with_secret, assume_yes=True, prompt=input)

    assert secret not in result.detail
    assert secret not in result.status


# ---------------------------------------------------------------------------
# Bug found while writing this suite (see module docstring) -- now fixed:
# RawConfig plumbs mokuro_shared_secret through, so the doctor row reflects
# a genuinely configured secret.
# ---------------------------------------------------------------------------


def test_mokuro_row_reflects_configured_secret(tmp_path):
    env, cfg_path, db_path, local_appdata, appdata = _sandbox_env(tmp_path)
    _write_config_with_secret(cfg_path, "a-real-looking-secret-value")

    proc = _run_installer(["--check"], env)

    assert re.search(
        r"^mokuro page-change bridge\s+READY\s+.*mokuro_shared_secret is \(set\)",
        proc.stdout,
        re.MULTILINE,
    ), proc.stdout
