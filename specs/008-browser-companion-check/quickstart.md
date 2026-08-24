# Quickstart / Verify Runbook: 008 — Browser Companion Check

Prerequisites: Windows 11, uv on PATH, repo checked out, `uv sync` done once.
All commands from the repo root. **Nothing here touches a real browser profile** — every
automated check builds a synthetic profile tree in a temp directory; the two manual checks
(§4) are read-only observation of your own browser.

This file doubles as the TG3 gate script: the gate runs §1–§7 in order.

## 1. Detector unit tests

```bash
uv run pytest tests/test_companions.py -q
```

Expected: green. Covers present / absent / undetermined per profile, multi-profile
resolution (the row names the profile that has it), unreadable-profile degradation, no
supported browser found, the mokuro secret-presence and port-state readings, and the
time-budget degradation path.

## 2. Installer/doctor integration tests

```bash
uv run pytest tests/test_installer_companions.py -q
```

Expected: green. Covers the three new doctor rows appearing in `--check` and in the wizard
summary, the appended wizard step running under `--yes` without prompting, the interactive
re-check path, and — the load-bearing one — `--check`'s exit code being unaffected by any
combination of companion verdicts.

## 3. The no-install / read-only proofs

These are the checks the feature's boundary rests on; they live in the test files above but
are called out separately because a gate must confirm them by name.

```bash
uv run pytest tests/test_companions.py tests/test_installer_companions.py -q -k "readonly or no_install or exit_code"
```

Expected: green, and **non-empty** — if `-k` selects zero tests, the boundary is untested
and the gate fails. Each of the three must be present:

- **read-only**: a synthetic profile tree is hashed before and after a full detection run;
  the hashes match (SC-002).
- **no-install**: no code path in `companions.py` or the new installer blocks downloads,
  unpacks, writes to a browser directory, or writes browser policy. Enforced as a source
  scan (no `urlopen`/`urlretrieve`/`http.client`/`requests`/`httpx`/`winreg`/`CRX`/
  `--load-extension` construct in the new module) plus a behavioral assertion that a run
  against an empty profile tree leaves it empty (SC-003).
- **exit_code**: `doctor_exit_code()` returns the same value with all companions absent as
  with all present (SC-004).

## 4. Manual observation (once, on the real machine)

Read-only, no changes:

1. `python -m katagiri.installer --check` — confirm the three new rows appear, that the
   verdicts match reality on your machine, and that each row names its evidence (profile
   path, or the reason it could not tell).
2. If a companion is absent, confirm the printed URL opens the correct official listing
   (Yomitan / asbplayer Chrome Web Store; see research.md R5).
3. Confirm the mokuro row reads as *configuration readiness* — secret set/unset, port
   free/occupied — and that a free port is described as expected, not as a problem.
4. Install one absent companion in the browser, then re-run `--check`: the row flips
   without any other step being re-run (US2 acceptance 2).

## 5. Repository invariants (must be untouched)

```bash
uv run pytest tests/test_bverify.py tests/test_cverify.py -q
```

Expected: green, with **no change to `HTTP_CLIENT_ALLOWLIST` or `HTTP_SERVER_ALLOWLIST`**
in either file. Verify the allowlists literally:

```bash
git diff main -- tests/test_bverify.py tests/test_cverify.py
```

Expected: empty for both files. If the port probe forced an allowlist edit, the gate fails
and the question goes back to the user (a D-47-style decision + ledger row), per plan.md
design decision 5.

## 6. Tool-contract stability

```bash
uv run pytest tests/test_mcp_tools.py -q
```

Expected: green with the registered-tool count **unchanged** from pre-008. 008 adds no
ToolSpec; a changed count means something is wrong.

## 7. Full regression

```bash
uv run pytest -n auto --dist loadgroup
```

Expected: green; wall-clock within noise of the pre-008 baseline (the companion check runs
only inside the doctor, and the new tests are filesystem-only).

## Rollback

008 is removable in one step: delete `src/katagiri/companions.py`, revert the two blocks in
`installer.py` (the `probe_companions()` entry in `collect_doctor_statuses()` and the
appended step label/runner + menu entry), and delete the two test files. Nothing persists —
no config key, no table, no file on disk — so a rollback leaves no residue.
