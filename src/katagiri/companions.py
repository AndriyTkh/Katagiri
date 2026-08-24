r"""Browser companion detection (Yomitan / asbplayer extensions, and friends).

Katagiri cannot install a browser extension -- Chrome permits programmatic
installation only through enterprise policy or unpacked side-loading, both of
which this project refuses on principle (spec 008, boundary 1). What it *can*
do is look at what the browser already left on disk and tell the operator the
truth about it. This module is that look, and nothing else: pure, read-only
filesystem and environment inspection, with a hard time budget.

Rules this module holds itself to (008 binding rules; see
``specs/008-browser-companion-check/``):

* **Read-only, always.** Nothing here creates, writes, renames or deletes
  anything under a browser data directory. There is no ``open(..., "w")``,
  no ``mkdir``, no ``unlink`` in this file, and there must never be one.
* **Never wait on a lock.** A browser that is running keeps its profile files
  open; a read that fails or is slow marks *that source* unreadable and the
  scan moves on. No retry loop, no backoff, no blocking.
* **Three outcomes, never a boolean.** Every row is ``present`` / ``absent`` /
  ``undetermined``. "We looked everywhere and it is not there" and "we had
  nothing to look at" are different answers and are never merged (spec US3,
  FR-003). In particular: no supported browser root on the machine yields
  ``undetermined`` with the searched roots listed -- never ``absent``.
* **No HTTP client.** Nothing in this module opens a network connection at
  all. (008's mokuro row, added by T003, uses a bare
  ``socket.create_connection`` against the pinned loopback port; that is still
  not an HTTP client.)
* **Stdlib only, plus ``katagiri.config``.** This module is imported from
  ``installer.py``, which must stay importable on a half-set-up checkout, so
  it inherits installer.py's "no other Katagiri imports" rule.

Detection strategy (research.md R1). The primary signal for a Chromium-family
browser is the extension payload directory ``<profile>\Extensions\<id>\``: a
plain ``is_dir()``, which is cheap, lock-free and immune to JSON layout churn.
``<profile>\Preferences`` -> ``extensions.settings.<id>`` is read only to
*enrich* that answer with the enabled/disabled detail; a failure to read it
downgrades the row's detail, never its verdict (open item O-1). Where the
``Preferences`` read succeeds and shows no entry for an id whose directory
exists, the hit is reported present-with-caveat rather than a confident
present, because a stale payload directory can outlive an uninstall
(open item O-2).

Measured on Chrome (Windows) 2026-08-24, and worth knowing before anyone
"fixes" the enrichment: that browser keeps ``extensions.settings`` in
**Secure Preferences**, not in ``Preferences`` -- the latter's ``extensions``
object has no ``settings`` key at all, and the Secure Preferences entry
expresses enabled/disabled through ``disable_reasons``/``active_bit`` rather
than the ``state`` enum. Research R1 deliberately rejected reading Secure
Preferences (unstable layout, no verdict-level information beyond signal A),
so on such a profile the row simply reports the enabled state as unknown. What
it must never do is read the *missing* ``settings`` object as "no entry for
this id" and stamp the O-2 leftover caveat on a perfectly healthy install:
"the whole settings map is absent" and "the map is there and this id is not in
it" are handled as two different cases below.

Firefox (research.md R2, open item O-3) is covered best-effort via
``%APPDATA%\Mozilla\Firefox\profiles.ini`` -> ``<profile>\extensions.json``.
The ``extensions.json`` shape *was* verified against a real Firefox profile on
a development machine on 2026-08-24 -- it is
``{"schemaVersion": int, "addons": [{"id", "type", "active", "userDisabled",
"appDisabled", "defaultLocale": {"name"}, ...}]}`` -- and ``profiles.ini``'s
``[ProfileN] / Name / IsRelative / Path`` layout likewise (relative paths use
forward slashes, e.g. ``Profiles/z3rkhu89.default-release``). What was *not*
verifiable is any companion's Firefox add-on id, since none was installed
there. So the Firefox side matches on add-on id **or** display name, and a
query that supplies neither is reported as "Firefox not covered" rather than
contributing an ``absent`` -- see :class:`ExtensionQuery.firefox_ids` /
``firefox_names`` and :func:`_firefox_covered_for`.

The catalog of real companion ids, install URLs and handoff text is *not*
here; it arrives with 008 T003. This module is the parameterised engine, so
that the ids live in exactly one place and the detection logic can be tested
against synthetic profile trees (every entry point takes an injectable
``env`` mapping and an injectable clock).
"""

from __future__ import annotations

import configparser
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Sequence

# Imported for the module's public surface: 008 T003's mokuro row reports the
# pinned loopback bridge port, and re-exporting it here keeps callers from
# having to import katagiri.config themselves. This is the only non-stdlib
# import this module is permitted (installer.py's module rule).
from katagiri.config import MOKURO_BRIDGE_PORT

__all__ = [
    "MOKURO_BRIDGE_PORT",
    "VERDICT_PRESENT",
    "VERDICT_ABSENT",
    "VERDICT_UNDETERMINED",
    "DEFAULT_TIME_BUDGET_S",
    "PER_SOURCE_BUDGET_S",
    "CompanionStatus",
    "ExtensionQuery",
    "BrowserRoot",
    "ProfileRef",
    "ExtensionHit",
    "BrowserScan",
    "candidate_browser_roots",
    "enumerate_browser_roots",
    "enumerate_chromium_profiles",
    "enumerate_firefox_profiles",
    "scan_browsers",
    "detect_extensions",
    "detect_extension",
]

# --------------------------------------------------------------------------
# Verdicts and budgets
# --------------------------------------------------------------------------

VERDICT_PRESENT: Final = "present"
VERDICT_ABSENT: Final = "absent"
VERDICT_UNDETERMINED: Final = "undetermined"

#: Hard ceiling for a whole companion scan (spec FR-011, SC-005). Exceeding it
#: degrades every not-yet-answered row to ``undetermined``; it never blocks.
DEFAULT_TIME_BUDGET_S: Final = 2.0

#: Soft ceiling for one source (one JSON file). A source that overruns is
#: recorded as slow and the global deadline check that follows usually ends the
#: scan. Reads cannot be interrupted mid-call, so this is a report-and-continue
#: bound, not a preemption.
PER_SOURCE_BUDGET_S: Final = 0.6

#: Refuse to parse a profile JSON larger than this. Old Chrome ``Preferences``
#: files reach tens of MB (research.md R1, caveat on signal B); parsing one
#: would blow the whole time budget for a detail we treat as optional anyway.
MAX_JSON_BYTES: Final = 16 * 1024 * 1024

#: How many evidence items a detail string names before it says "+N more".
_MAX_EVIDENCE_ITEMS: Final = 4


# --------------------------------------------------------------------------
# Browser roots
# --------------------------------------------------------------------------

# (label, environment variable, path relative to it). Chromium-family roots all
# end at the directory that directly contains ``Local State`` and the profile
# directories -- for most browsers that is ``...\User Data``, but Opera puts the
# profile at ``%APPDATA%\Opera Software\Opera Stable`` with no such level.
# Source: research.md R1 ("Roots to scan").
_CHROMIUM_ROOT_SPECS: Final[tuple[tuple[str, str, str], ...]] = (
    ("Chrome", "LOCALAPPDATA", r"Google\Chrome\User Data"),
    ("Chrome Beta", "LOCALAPPDATA", r"Google\Chrome Beta\User Data"),
    ("Chrome Dev", "LOCALAPPDATA", r"Google\Chrome Dev\User Data"),
    ("Chrome Canary", "LOCALAPPDATA", r"Google\Chrome SxS\User Data"),
    ("Edge", "LOCALAPPDATA", r"Microsoft\Edge\User Data"),
    ("Brave", "LOCALAPPDATA", r"BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", "LOCALAPPDATA", r"Vivaldi\User Data"),
    ("Opera", "APPDATA", r"Opera Software\Opera Stable"),
)

_FIREFOX_ROOT_SPEC: Final[tuple[str, str, str]] = ("Firefox", "APPDATA", r"Mozilla\Firefox")


@dataclass(frozen=True, slots=True)
class BrowserRoot:
    """One browser data directory that actually exists on this machine."""

    browser: str
    path: Path
    family: str = "chromium"  # "chromium" | "firefox"


@dataclass(frozen=True, slots=True)
class ProfileRef:
    """One browser profile directory, with whatever display name we could read."""

    browser: str
    path: Path
    dir_name: str
    display_name: str = ""

    @property
    def label(self) -> str:
        if self.display_name and self.display_name != self.dir_name:
            return _ascii(f"{self.browser}/{self.dir_name} ({self.display_name})")
        return _ascii(f"{self.browser}/{self.dir_name}")


@dataclass(frozen=True, slots=True)
class ExtensionHit:
    """Evidence that one extension id is installed in one profile."""

    extension_id: str
    profile: ProfileRef
    enabled: bool | None = None  # None == could not tell
    caveat: str = ""  # non-empty => present-with-caveat (open item O-2)

    def describe(self) -> str:
        state = ""
        if self.enabled is False:
            state = " (disabled)"
        elif self.enabled is True:
            state = " (enabled)"
        note = f" [{self.caveat}]" if self.caveat else ""
        return f"{self.profile.label}{state}{note}"


@dataclass(frozen=True, slots=True)
class CompanionStatus:
    """One doctor row: a companion, a three-way verdict, and its evidence.

    ``verdict`` is one of :data:`VERDICT_PRESENT` / :data:`VERDICT_ABSENT` /
    :data:`VERDICT_UNDETERMINED` -- never a boolean, because "searched and not
    found" and "could not look" must not collapse into one another (spec US3).
    ``optional`` is carried so the installer can map every companion to
    ``MANUAL STEP`` rather than ``MISSING`` and leave ``doctor_exit_code()``
    untouched (spec FR-004).
    """

    name: str
    verdict: str
    detail: str = ""
    optional: bool = True

    @property
    def is_present(self) -> bool:
        return self.verdict == VERDICT_PRESENT


@dataclass(frozen=True, slots=True)
class ExtensionQuery:
    """What to look for, for one companion.

    ``chromium_ids`` are Chrome Web Store ids (the ``Extensions\\<id>``
    directory name). ``firefox_ids`` are add-on ids as they appear in
    ``extensions.json``; ``firefox_names`` are display names matched
    case-insensitively against the add-on's ``defaultLocale.name``. A query
    that supplies neither Firefox signal declares the Firefox side **not
    covered**, and any Firefox profile found on the machine then downgrades a
    would-be ``absent`` to ``undetermined`` instead of being ignored
    (spec Edge Cases, research.md R2).
    """

    name: str
    chromium_ids: tuple[str, ...] = ()
    firefox_ids: tuple[str, ...] = ()
    firefox_names: tuple[str, ...] = ()
    optional: bool = True


def _firefox_covered_for(query: ExtensionQuery) -> bool:
    """True when the query carries a usable Firefox signal (id or name)."""
    return bool(query.firefox_ids or query.firefox_names)


# --------------------------------------------------------------------------
# Time budget
# --------------------------------------------------------------------------


class _Deadline:
    """A monotonic budget with an injectable clock (tests need determinism)."""

    __slots__ = ("_clock", "_budget", "_start", "_tripped")

    def __init__(self, budget: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._budget = budget
        self._start = clock()
        self._tripped = False

    @property
    def tripped(self) -> bool:
        return self._tripped

    def elapsed(self) -> float:
        return self._clock() - self._start

    def remaining(self) -> float:
        return self._budget - self.elapsed()

    def expired(self) -> bool:
        """Check the budget. Once tripped, stays tripped."""
        if self._tripped:
            return True
        if self.remaining() <= 0:
            self._tripped = True
        return self._tripped


# --------------------------------------------------------------------------
# Guarded, read-only primitives
# --------------------------------------------------------------------------


def _ascii(text: str) -> str:
    """Force a browser-supplied string to printable ASCII.

    Profile display names and profile paths are whatever the learner typed;
    the installer prints its status lines on a console that may be cp1252, and
    a ``UnicodeEncodeError`` from a doctor row would be a much worse failure
    than a mangled profile name (installer.py's ASCII-output rule).
    """
    return text.encode("ascii", "backslashreplace").decode("ascii")


def _environ(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_iterdir(path: Path) -> tuple[list[Path], str]:
    """List a directory. Returns ``(entries, error)``; never raises."""
    try:
        return sorted(path.iterdir()), ""
    except OSError as exc:
        return [], _errtext(exc)


def _errtext(exc: BaseException) -> str:
    """A short, ASCII, path-free reason. Callers add the path themselves."""
    name = type(exc).__name__
    text = str(exc).strip()
    if not text:
        return name
    # Windows OSError strings embed the path; keep only the reason clause.
    reason = text.split(":", 1)[0].strip()
    reason = reason.replace("[Errno", "errno").replace("[WinError", "winerror")
    reason = reason.replace("]", "")
    return _ascii(reason[:80]) or name


def _read_json(
    path: Path,
    deadline: _Deadline,
    notes: list[str],
    *,
    what: str,
) -> Any | None:
    """Read and parse one JSON file, tolerating every way it can fail.

    Returns ``None`` and appends a human-readable note on any of: missing,
    locked by a running browser, permission denied, oversized, malformed, or
    not valid UTF-8. Never waits, never retries.
    """
    if deadline.expired():
        notes.append(f"{what}: skipped (time budget)")
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        notes.append(f"{what}: unreadable ({_errtext(exc)})")
        return None
    if size > MAX_JSON_BYTES:
        notes.append(f"{what}: skipped (file is {size // (1024 * 1024)} MB)")
        return None
    started = deadline.elapsed()
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        notes.append(f"{what}: unreadable ({_errtext(exc)})")
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        notes.append(f"{what}: malformed ({_errtext(exc)})")
        return None
    took = deadline.elapsed() - started
    if took > PER_SOURCE_BUDGET_S:
        notes.append(f"{what}: slow ({took:.1f}s)")
    return data


def _dig(data: Any, *keys: str) -> Any | None:
    """Walk nested mappings defensively; anything unexpected yields ``None``."""
    node: Any = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# --------------------------------------------------------------------------
# Root and profile enumeration
# --------------------------------------------------------------------------


def candidate_browser_roots(env: Mapping[str, str] | None = None) -> tuple[tuple[str, Path], tuple[str, ...]]:
    """Every root this detector knows how to look at, existing or not.

    Returns ``(candidates, missing_env_vars)``. The candidate list is what a
    row prints as "locations searched" -- an operator who keeps a portable
    browser somewhere else (open item O-4) needs to see that list to recognise
    their case.
    """
    environ = _environ(env)
    candidates: list[tuple[str, Path]] = []
    missing: list[str] = []
    specs = list(_CHROMIUM_ROOT_SPECS) + [_FIREFOX_ROOT_SPEC]
    for label, var, relative in specs:
        base = environ.get(var)
        if not base:
            if var not in missing:
                missing.append(var)
            continue
        candidates.append((label, Path(base) / relative))
    return tuple(candidates), tuple(missing)


def enumerate_browser_roots(env: Mapping[str, str] | None = None) -> tuple[BrowserRoot, ...]:
    """The subset of :func:`candidate_browser_roots` that exists on disk."""
    environ = _environ(env)
    found: list[BrowserRoot] = []
    for label, var, relative in _CHROMIUM_ROOT_SPECS:
        base = environ.get(var)
        if not base:
            continue
        path = Path(base) / relative
        if _safe_is_dir(path):
            found.append(BrowserRoot(label, path, "chromium"))
    label, var, relative = _FIREFOX_ROOT_SPEC
    base = environ.get(var)
    if base:
        path = Path(base) / relative
        if _safe_is_dir(path):
            found.append(BrowserRoot(label, path, "firefox"))
    return tuple(found)


def enumerate_chromium_profiles(
    root: BrowserRoot,
    deadline: _Deadline | None = None,
    notes: list[str] | None = None,
) -> tuple[ProfileRef, ...]:
    """Profile directories under one Chromium-family root.

    The directory listing is authoritative (``Default`` and ``Profile *``);
    ``Local State`` -> ``profile.info_cache`` only supplies display names and
    can add a profile directory whose name follows neither convention. A
    ``Local State`` that is locked, oversized or malformed therefore costs the
    report its friendly names, never its profile list (research.md R1, signal
    D).
    """
    deadline = deadline or _Deadline(DEFAULT_TIME_BUDGET_S, time.monotonic)
    notes = notes if notes is not None else []

    entries, err = _safe_iterdir(root.path)
    if err:
        notes.append(f"{root.browser}: profile list unreadable ({err})")
    dir_names: list[str] = []
    for entry in entries:
        name = entry.name
        if (name == "Default" or name.startswith("Profile ")) and _safe_is_dir(entry):
            dir_names.append(name)

    names: dict[str, str] = {}
    info = _dig(
        _read_json(
            root.path / "Local State",
            deadline,
            notes,
            what=f"{root.browser} Local State",
        ),
        "profile",
        "info_cache",
    )
    if isinstance(info, dict):
        for dir_name, meta in info.items():
            if not isinstance(dir_name, str):
                continue
            if isinstance(meta, dict):
                display = meta.get("name")
                if isinstance(display, str):
                    names[dir_name] = display
            if dir_name not in dir_names and _safe_is_dir(root.path / dir_name):
                dir_names.append(dir_name)

    return tuple(
        ProfileRef(root.browser, root.path / name, name, names.get(name, ""))
        for name in sorted(set(dir_names))
    )


def enumerate_firefox_profiles(
    root: BrowserRoot,
    deadline: _Deadline | None = None,
    notes: list[str] | None = None,
) -> tuple[ProfileRef, ...]:
    """Profiles from ``profiles.ini``, falling back to the ``Profiles`` folder.

    ``profiles.ini`` layout verified against a real profile 2026-08-24: one
    ``[ProfileN]`` section per profile with ``Name``, ``IsRelative`` and
    ``Path``; a relative ``Path`` uses forward slashes and is relative to the
    Firefox root. ``[General]``, ``[Install...]`` and ``[BackgroundTasksProfiles]``
    sections are not profiles and are skipped (open item O-3).
    """
    deadline = deadline or _Deadline(DEFAULT_TIME_BUDGET_S, time.monotonic)
    notes = notes if notes is not None else []

    profiles: list[ProfileRef] = []
    seen: set[str] = set()
    ini_path = root.path / "profiles.ini"
    parser = configparser.RawConfigParser(strict=False)
    parsed = False
    if not deadline.expired():
        try:
            text = ini_path.read_text(encoding="utf-8", errors="replace")
            parser.read_string(text)
            parsed = True
        except OSError as exc:
            notes.append(f"Firefox profiles.ini: unreadable ({_errtext(exc)})")
        except configparser.Error as exc:
            notes.append(f"Firefox profiles.ini: malformed ({_errtext(exc)})")
    else:
        notes.append("Firefox profiles.ini: skipped (time budget)")

    if parsed:
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            raw_path = parser.get(section, "path", fallback="").strip()
            if not raw_path:
                continue
            relative = parser.get(section, "isrelative", fallback="1").strip()
            path = (root.path / raw_path.replace("/", os.sep)) if relative != "0" else Path(raw_path)
            if not _safe_is_dir(path):
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            profiles.append(
                ProfileRef(
                    "Firefox",
                    path,
                    path.name,
                    parser.get(section, "name", fallback="").strip(),
                )
            )

    if not profiles:
        entries, err = _safe_iterdir(root.path / "Profiles")
        if err and not parsed:
            notes.append(f"Firefox profile folder: unreadable ({err})")
        for entry in entries:
            if _safe_is_dir(entry) and str(entry).lower() not in seen:
                seen.add(str(entry).lower())
                profiles.append(ProfileRef("Firefox", entry, entry.name))

    return tuple(profiles)


# --------------------------------------------------------------------------
# Per-profile extension lookup
# --------------------------------------------------------------------------


def _chromium_hits_for_profile(
    profile: ProfileRef,
    ids: Sequence[str],
    deadline: _Deadline,
    notes: list[str],
) -> tuple[ExtensionHit, ...]:
    """Primary signal (payload directory) plus optional Preferences detail."""
    present_ids = [
        ext_id for ext_id in ids if _safe_is_dir(profile.path / "Extensions" / ext_id)
    ]
    if not present_ids:
        return ()

    # Only now is the expensive Preferences read worth attempting, and only if
    # the budget still allows it. Its absence costs detail, never the verdict.
    settings: Any | None = None
    prefs_read = False
    if not deadline.expired():
        data = _read_json(
            profile.path / "Preferences",
            deadline,
            notes,
            what=f"{profile.label} Preferences",
        )
        if data is not None:
            prefs_read = True
            settings = _dig(data, "extensions", "settings")

    hits: list[ExtensionHit] = []
    for ext_id in present_ids:
        enabled: bool | None = None
        caveat = ""
        if prefs_read and isinstance(settings, dict):
            entry = settings.get(ext_id)
            if isinstance(entry, dict):
                state = entry.get("state")
                reasons = entry.get("disable_reasons")
                if isinstance(state, int) and not isinstance(state, bool):
                    # Chrome's enum: 0 = disabled, 1 = enabled. Treated as
                    # best-effort detail only (open item O-1).
                    enabled = state != 0
                elif isinstance(reasons, (int, list)) and not isinstance(reasons, bool):
                    # Newer layouts drop ``state`` and express the same thing as
                    # a disable-reason bitmask/list; empty means enabled.
                    enabled = not reasons
                else:
                    caveat = "enabled state unknown"
            else:
                # Directory on disk but no settings entry: possibly a payload
                # left behind by an uninstall that has not been garbage
                # collected yet (open item O-2). Present, with a caveat --
                # never silently downgraded to absent.
                caveat = "payload on disk but no profile entry; may be a leftover"
        elif prefs_read:
            caveat = "enabled state unknown"
        hits.append(ExtensionHit(ext_id, profile, enabled, caveat))
    return tuple(hits)


def _firefox_hits_for_profile(
    profile: ProfileRef,
    queries: Sequence[ExtensionQuery],
    deadline: _Deadline,
    notes: list[str],
) -> dict[str, list[ExtensionHit]]:
    """Match ``extensions.json`` add-ons by id or display name.

    Falls back to ``<profile>\\extensions\\<id>.xpi`` when the JSON cannot be
    read, which still catches an id-known add-on on a locked profile.
    """
    by_query: dict[str, list[ExtensionHit]] = {}
    data = _read_json(
        profile.path / "extensions.json",
        deadline,
        notes,
        what=f"{profile.label} extensions.json",
    )
    addons = data.get("addons") if isinstance(data, dict) else None

    if isinstance(addons, list):
        for query in queries:
            if not _firefox_covered_for(query):
                continue
            want_ids = {i.lower() for i in query.firefox_ids}
            want_names = {n.lower() for n in query.firefox_names}
            for addon in addons:
                if not isinstance(addon, dict):
                    continue
                if addon.get("type") not in (None, "extension"):
                    continue
                addon_id = addon.get("id")
                addon_id = addon_id.lower() if isinstance(addon_id, str) else ""
                display = _dig(addon, "defaultLocale", "name")
                display = display.strip().lower() if isinstance(display, str) else ""
                if not (addon_id in want_ids or (display and display in want_names)):
                    continue
                enabled: bool | None = None
                active = addon.get("active")
                if isinstance(active, bool):
                    enabled = active and not bool(addon.get("userDisabled")) and not bool(
                        addon.get("appDisabled")
                    )
                by_query.setdefault(query.name, []).append(
                    ExtensionHit(addon_id or display, profile, enabled)
                )
        return by_query

    # extensions.json unusable -- try the XPI filenames, which are the add-on
    # ids. Nothing to match a display name against here, so a name-only query
    # simply finds nothing and the unreadable note keeps the row honest.
    for query in queries:
        for ext_id in query.firefox_ids:
            xpi = profile.path / "extensions" / f"{ext_id}.xpi"
            try:
                found = xpi.is_file()
            except OSError:
                found = False
            if found:
                by_query.setdefault(query.name, []).append(
                    ExtensionHit(ext_id, profile, None, "read from extensions folder")
                )
    return by_query


# --------------------------------------------------------------------------
# The scan
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrowserScan:
    """Everything one pass over the machine's browser data found.

    Deliberately separate from the verdicts: one scan answers every companion
    query, and tests can assert on the raw evidence.
    """

    hits: Mapping[str, tuple[ExtensionHit, ...]] = field(default_factory=dict)
    roots_searched: tuple[str, ...] = ()
    roots_found: tuple[str, ...] = ()
    profiles: tuple[ProfileRef, ...] = ()
    firefox_profiles: tuple[ProfileRef, ...] = ()
    notes: tuple[str, ...] = ()
    timed_out: bool = False
    missing_env_vars: tuple[str, ...] = ()

    @property
    def profile_labels(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.profiles)


def scan_browsers(
    queries: Sequence[ExtensionQuery],
    *,
    env: Mapping[str, str] | None = None,
    budget: float = DEFAULT_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> BrowserScan:
    """One read-only pass over every known browser root.

    Every failure mode -- no root, unreadable root, locked profile file,
    malformed JSON, budget exhausted -- is recorded and the scan continues.
    Nothing here raises for a browser-side problem.
    """
    deadline = _Deadline(budget, clock)
    notes: list[str] = []
    candidates, missing_env = candidate_browser_roots(env)
    if missing_env:
        notes.append("environment variable(s) unset: " + ", ".join(missing_env))

    roots = enumerate_browser_roots(env)
    chromium_ids = sorted({i for q in queries for i in q.chromium_ids})
    hits: dict[str, list[ExtensionHit]] = {q.name: [] for q in queries}
    id_owner: dict[str, list[str]] = {}
    for query in queries:
        for ext_id in query.chromium_ids:
            id_owner.setdefault(ext_id, []).append(query.name)

    profiles: list[ProfileRef] = []
    firefox_profiles: list[ProfileRef] = []

    for root in roots:
        if deadline.expired():
            notes.append(f"{root.browser}: skipped (time budget)")
            continue
        if root.family == "chromium":
            found = enumerate_chromium_profiles(root, deadline, notes)
            profiles.extend(found)
            for profile in found:
                if deadline.expired():
                    notes.append(f"{profile.label}: skipped (time budget)")
                    continue
                for hit in _chromium_hits_for_profile(profile, chromium_ids, deadline, notes):
                    for owner in id_owner.get(hit.extension_id, ()):
                        hits[owner].append(hit)
        else:
            found = enumerate_firefox_profiles(root, deadline, notes)
            firefox_profiles.extend(found)
            profiles.extend(found)
            covered = [q for q in queries if _firefox_covered_for(q)]
            if not covered:
                continue
            for profile in found:
                if deadline.expired():
                    notes.append(f"{profile.label}: skipped (time budget)")
                    continue
                for name, found_hits in _firefox_hits_for_profile(
                    profile, covered, deadline, notes
                ).items():
                    hits[name].extend(found_hits)

    return BrowserScan(
        hits={name: tuple(v) for name, v in hits.items()},
        roots_searched=tuple(_ascii(str(p)) for _, p in candidates),
        roots_found=tuple(_ascii(f"{r.browser} ({r.path})") for r in roots),
        profiles=tuple(profiles),
        firefox_profiles=tuple(firefox_profiles),
        notes=tuple(notes),
        timed_out=deadline.expired(),
        missing_env_vars=missing_env,
    )


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


def _join(items: Sequence[str], limit: int = _MAX_EVIDENCE_ITEMS) -> str:
    shown = list(items[:limit])
    extra = len(items) - len(shown)
    if extra > 0:
        shown.append(f"+{extra} more")
    return ", ".join(shown)


def _verdict_for(query: ExtensionQuery, scan: BrowserScan) -> CompanionStatus:
    hits = tuple(scan.hits.get(query.name, ()))
    if hits:
        detail = "found in " + _join([h.describe() for h in hits])
        if scan.timed_out:
            detail += "; scan hit its time budget before finishing"
        return CompanionStatus(query.name, VERDICT_PRESENT, detail, query.optional)

    if scan.timed_out:
        return CompanionStatus(
            query.name,
            VERDICT_UNDETERMINED,
            f"scan exceeded its {DEFAULT_TIME_BUDGET_S:.0f}s budget before it could answer; "
            f"scanned {len(scan.profiles)} profile(s) so far",
            query.optional,
        )

    if not scan.roots_found:
        if scan.roots_searched:
            where = "searched: " + _join(scan.roots_searched, 6)
        else:
            where = "nothing could be searched: " + _join(
                [f"%{v}% is unset" for v in scan.missing_env_vars]
            )
        return CompanionStatus(
            query.name,
            VERDICT_UNDETERMINED,
            "no supported browser data directory found; " + where,
            query.optional,
        )

    if not scan.profiles:
        return CompanionStatus(
            query.name,
            VERDICT_UNDETERMINED,
            "browser installed but no readable profile found in "
            + _join(scan.roots_found),
            query.optional,
        )

    # A Firefox profile exists but this companion has no Firefox signal to
    # match on: absence over the Chromium half is not absence overall
    # (spec Edge Cases: "Firefox-only learner").
    if scan.firefox_profiles and not _firefox_covered_for(query):
        return CompanionStatus(
            query.name,
            VERDICT_UNDETERMINED,
            "not found in "
            + _join([p.label for p in scan.profiles if p.browser != "Firefox"])
            + "; Firefox is installed but this companion's Firefox variant is not "
            "covered by the detector",
            query.optional,
        )

    unreadable = [n for n in scan.notes if "unreadable" in n or "malformed" in n]
    chromium_profiles = [p for p in scan.profiles if p.browser != "Firefox"]
    if not chromium_profiles and not scan.firefox_profiles:
        return CompanionStatus(
            query.name,
            VERDICT_UNDETERMINED,
            "no readable browser profile; " + _join(unreadable),
            query.optional,
        )

    detail = f"not found in {len(scan.profiles)} profile(s): " + _join(scan.profile_labels)
    if unreadable:
        detail += "; some sources unreadable: " + _join(unreadable, 2)
    detail += (
        "; an extension loaded unpacked or installed outside these profiles "
        "would not be seen"
    )
    return CompanionStatus(query.name, VERDICT_ABSENT, detail, query.optional)


def detect_extensions(
    queries: Sequence[ExtensionQuery],
    *,
    env: Mapping[str, str] | None = None,
    budget: float = DEFAULT_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
    scan: BrowserScan | None = None,
) -> tuple[tuple[CompanionStatus, ...], BrowserScan]:
    """Answer every query from a single read-only pass.

    Returns the rows plus the scan they came from, so a caller (the installer's
    doctor) can print shared evidence -- searched roots, unreadable sources --
    without scanning twice. Pass ``scan`` to re-use an earlier pass.
    """
    if scan is None:
        scan = scan_browsers(queries, env=env, budget=budget, clock=clock)
    return tuple(_verdict_for(q, scan) for q in queries), scan


def detect_extension(
    query: ExtensionQuery,
    *,
    env: Mapping[str, str] | None = None,
    budget: float = DEFAULT_TIME_BUDGET_S,
    clock: Callable[[], float] = time.monotonic,
) -> CompanionStatus:
    """Single-companion convenience wrapper around :func:`detect_extensions`."""
    rows, _ = detect_extensions([query], env=env, budget=budget, clock=clock)
    return rows[0]
