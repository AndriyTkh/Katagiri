"""Tests for :mod:`katagiri.companions` (spec 008, task T004).

Everything here operates on synthetic browser-profile trees built entirely
under ``tmp_path`` -- never a real profile, never the real ``%LOCALAPPDATA%``.
The environment mapping passed to the module under test is always a plain
dict built by the test, never ``os.environ``.

Two tests are named so quickstart.md section 3's gate can find them by
``-k readonly`` / ``-k no_install``: :func:`test_readonly_full_scan_leaves_tree_byte_identical`
and :func:`test_no_install_source_scan_finds_no_network_or_registry_constructs`.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import katagiri.companions as companions_mod
from katagiri.companions import (
    VERDICT_ABSENT,
    VERDICT_PRESENT,
    VERDICT_UNDETERMINED,
    ExtensionQuery,
    detect_extension,
    detect_extensions,
    mokuro_companion_status,
    probe_mokuro_bridge_port,
)

# ---------------------------------------------------------------------------
# Synthetic-tree helpers
# ---------------------------------------------------------------------------

EXT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_EXT_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _chrome_root(tmp_path: Path) -> Path:
    """``%LOCALAPPDATA%\\Google\\Chrome\\User Data`` under a synthetic root."""
    root = tmp_path / "local" / "Google" / "Chrome" / "User Data"
    root.mkdir(parents=True)
    return root


def _make_profile(root: Path, dir_name: str) -> Path:
    profile = root / dir_name
    profile.mkdir(parents=True)
    return profile


def _add_extension_dir(profile: Path, ext_id: str) -> None:
    (profile / "Extensions" / ext_id).mkdir(parents=True)


def _write_preferences(profile: Path, settings: dict | None) -> None:
    """Write a ``Preferences`` file. ``settings=None`` omits the settings key."""
    body: dict = {"extensions": {}}
    if settings is not None:
        body["extensions"]["settings"] = settings
    (profile / "Preferences").write_text(json.dumps(body), encoding="utf-8")


def _env_for(tmp_path: Path) -> dict[str, str]:
    """``LOCALAPPDATA`` pointed at the synthetic root; no other env vars."""
    return {"LOCALAPPDATA": str(tmp_path / "local")}


def _free_port() -> int:
    """An ephemeral port that is free at the moment this returns.

    Never the pinned 8767 (``katagiri.config.MOKURO_BRIDGE_PORT``) -- this
    binds port 0 and lets the OS assign one, mirroring
    tests/test_media_mokuro.py's own discipline.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hash_tree(root: Path) -> str:
    """A content+structure hash of everything under ``root``."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# US1 / US3 acceptance 1: present in the only profile
# ---------------------------------------------------------------------------


def test_present_in_only_profile_names_that_profile(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    profile = _make_profile(root, "Default")
    _add_extension_dir(profile, EXT_ID)

    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))

    assert status.verdict == VERDICT_PRESENT
    assert "Chrome/Default" in status.detail


# ---------------------------------------------------------------------------
# US3 acceptance 1: present in profile 2 of 2, names profile 2
# ---------------------------------------------------------------------------


def test_present_in_second_of_two_profiles_names_it(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    _make_profile(root, "Default")  # profile 1: nothing installed
    profile_2 = _make_profile(root, "Profile 1")
    _add_extension_dir(profile_2, EXT_ID)

    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))

    assert status.verdict == VERDICT_PRESENT
    assert "Chrome/Profile 1" in status.detail
    # The detail names the profile that HAS it; it must be the hit evidence,
    # not merely a byproduct of listing every profile that was searched.
    assert status.detail.startswith("found in")


# ---------------------------------------------------------------------------
# Absent from all profiles -> evidence lists searched locations
# ---------------------------------------------------------------------------


def test_absent_from_all_profiles_lists_searched_locations(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    _make_profile(root, "Default")
    _make_profile(root, "Profile 1")

    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))

    assert status.verdict == VERDICT_ABSENT
    assert "Chrome/Default" in status.detail
    assert "Chrome/Profile 1" in status.detail


# ---------------------------------------------------------------------------
# US3 acceptance 2: no browser root at all -> undetermined, never absent
# ---------------------------------------------------------------------------


def test_no_browser_root_is_undetermined_not_absent(tmp_path: Path) -> None:
    # An env with no LOCALAPPDATA/APPDATA at all: nothing to search.
    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env={})

    assert status.verdict == VERDICT_UNDETERMINED
    assert status.verdict != VERDICT_ABSENT
    assert "no supported browser data directory found" in status.detail


def test_localappdata_set_but_no_matching_dirs_is_still_undetermined(tmp_path: Path) -> None:
    # LOCALAPPDATA exists but none of the known browser subdirectories do.
    (tmp_path / "local").mkdir()
    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))

    assert status.verdict == VERDICT_UNDETERMINED


# ---------------------------------------------------------------------------
# US3 acceptance 3: unreadable/corrupt Preferences -> that profile reported
# unreadable, rest of the scan completes
# ---------------------------------------------------------------------------


def test_corrupt_preferences_reported_unreadable_scan_continues(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    profile_1 = _make_profile(root, "Default")
    _add_extension_dir(profile_1, EXT_ID)
    (profile_1 / "Preferences").write_text("{not valid json!!", encoding="utf-8")

    profile_2 = _make_profile(root, "Profile 1")
    _add_extension_dir(profile_2, OTHER_EXT_ID)
    _write_preferences(profile_2, {OTHER_EXT_ID: {"state": 1}})

    query_1 = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    query_2 = ExtensionQuery(name="OtherExt", chromium_ids=(OTHER_EXT_ID,))
    (status_1, status_2), scan = detect_extensions([query_1, query_2], env=_env_for(tmp_path))

    # The directory-based signal still wins: presence is not downgraded by an
    # enrichment failure (open item O-1).
    assert status_1.verdict == VERDICT_PRESENT
    # The corrupt Preferences file shows up as a malformed/unreadable note.
    assert any("malformed" in n or "unreadable" in n for n in scan.notes)
    # The rest of the scan completed: both profiles were enumerated and the
    # second profile's own extension was found normally.
    assert len(scan.profiles) == 2
    assert status_2.verdict == VERDICT_PRESENT
    assert "Chrome/Profile 1" in status_2.detail


# ---------------------------------------------------------------------------
# Open item O-2: directory present, Preferences settings map present but the
# id is missing from it -> present-with-caveat
# ---------------------------------------------------------------------------


def test_settings_map_present_but_id_missing_is_present_with_caveat(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    profile = _make_profile(root, "Default")
    _add_extension_dir(profile, EXT_ID)
    # The settings map exists and is non-empty, but has no entry for EXT_ID.
    _write_preferences(profile, {OTHER_EXT_ID: {"state": 1}})

    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))

    assert status.verdict == VERDICT_PRESENT
    assert "leftover" in status.detail or "caveat" in status.detail.lower() or "no profile entry" in status.detail


# ---------------------------------------------------------------------------
# Time-budget exceeded -> undetermined
# ---------------------------------------------------------------------------


def test_time_budget_exceeded_is_undetermined(tmp_path: Path) -> None:
    ticks = [0.0]

    def clock() -> float:
        ticks[0] += 1.0
        return ticks[0]

    root = _chrome_root(tmp_path)
    profile = _make_profile(root, "Default")
    _add_extension_dir(profile, EXT_ID)

    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    # budget smaller than one clock tick guarantees the deadline trips
    # immediately, regardless of what is actually on disk.
    status = detect_extension(query, env=_env_for(tmp_path), budget=0.5, clock=clock)

    assert status.verdict == VERDICT_UNDETERMINED
    assert "time budget" in status.detail or "budget" in status.detail


# ---------------------------------------------------------------------------
# mokuro: secret presence-only, value never leaked
# ---------------------------------------------------------------------------


def test_mokuro_secret_set_is_present_and_never_leaks_value() -> None:
    canary = "sk-canary-do-not-print-9f8e7d6c5b4a"
    cfg = SimpleNamespace(mokuro_shared_secret=canary)
    port = _free_port()

    status = mokuro_companion_status(cfg, host="127.0.0.1", port=port, timeout=0.2)

    assert status.verdict == VERDICT_PRESENT
    assert canary not in status.detail
    assert canary not in repr(status)
    assert canary not in str(status)


def test_mokuro_secret_unset_is_absent_and_never_leaks_value() -> None:
    canary_would_be = "sk-canary-should-never-appear-anyway"
    cfg = SimpleNamespace(mokuro_shared_secret=None)
    port = _free_port()

    status = mokuro_companion_status(cfg, host="127.0.0.1", port=port, timeout=0.2)

    assert status.verdict == VERDICT_ABSENT
    assert "(unset)" in status.detail
    assert canary_would_be not in status.detail


# ---------------------------------------------------------------------------
# mokuro: port free vs occupied (never the pinned 8767)
# ---------------------------------------------------------------------------


def test_mokuro_port_free_is_reported_as_expected_state() -> None:
    cfg = SimpleNamespace(mokuro_shared_secret="whatever")
    port = _free_port()

    assert probe_mokuro_bridge_port(host="127.0.0.1", port=port, timeout=0.2) is False

    status = mokuro_companion_status(cfg, host="127.0.0.1", port=port, timeout=0.2)
    assert "free" in status.detail
    assert "expected" in status.detail


def test_mokuro_port_occupied_is_reported_as_occupied_by_unknown() -> None:
    cfg = SimpleNamespace(mokuro_shared_secret="whatever")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        # A generous backlog: nothing here ever calls accept(), and each probe
        # below opens (and immediately closes) its own connection, so the
        # backlog queue must have room for more than one at a time.
        srv.listen(8)
        occupied_port = srv.getsockname()[1]

        assert (
            probe_mokuro_bridge_port(host="127.0.0.1", port=occupied_port, timeout=0.2)
            is True
        )

        status = mokuro_companion_status(
            cfg, host="127.0.0.1", port=occupied_port, timeout=0.2
        )
        assert "occupied" in status.detail
        assert "healthy" not in status.detail


# ---------------------------------------------------------------------------
# SC-002 boundary: readonly -- full scan leaves the tree byte-identical
# ---------------------------------------------------------------------------


def test_readonly_full_scan_leaves_tree_byte_identical(tmp_path: Path) -> None:
    root = _chrome_root(tmp_path)
    profile_1 = _make_profile(root, "Default")
    _add_extension_dir(profile_1, EXT_ID)
    _write_preferences(profile_1, {EXT_ID: {"state": 1}})

    profile_2 = _make_profile(root, "Profile 1")
    _add_extension_dir(profile_2, OTHER_EXT_ID)
    _write_preferences(profile_2, None)  # no settings key at all

    before = _hash_tree(tmp_path)

    query_1 = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    query_2 = ExtensionQuery(name="OtherExt", chromium_ids=(OTHER_EXT_ID,))
    detect_extensions([query_1, query_2], env=_env_for(tmp_path))

    after = _hash_tree(tmp_path)
    assert before == after


# ---------------------------------------------------------------------------
# SC-003 boundary: no_install -- source scan for network/registry/CRX constructs
# ---------------------------------------------------------------------------


def test_no_install_source_scan_finds_no_network_or_registry_constructs() -> None:
    module_path = Path(companions_mod.__file__)
    source = module_path.read_text(encoding="utf-8")

    banned = (
        "urlopen",
        "urlretrieve",
        "http.client",
        "urllib.request",
        "requests",
        "httpx",
        "winreg",
        "load-extension",
        ".crx",
    )
    found = [token for token in banned if token in source]
    assert found == [], f"banned network/registry/CRX construct(s) found: {found}"


def test_no_install_empty_tree_scan_leaves_it_empty(tmp_path: Path) -> None:
    """Behavioral half of SC-003: an empty profile tree stays empty."""
    root = _chrome_root(tmp_path)
    _make_profile(root, "Default")

    before = _hash_tree(tmp_path)
    query = ExtensionQuery(name="TestExt", chromium_ids=(EXT_ID,))
    status = detect_extension(query, env=_env_for(tmp_path))
    after = _hash_tree(tmp_path)

    assert status.verdict == VERDICT_ABSENT
    assert before == after
