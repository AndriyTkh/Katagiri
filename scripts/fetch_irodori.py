#!/usr/bin/env python3
"""Acquisition/verification helper for Irodori (Japan Foundation) materials.

Usage
-----
    python scripts/fetch_irodori.py
    py -3 scripts/fetch_irodori.py           # Windows launcher

Irodori PDF/MP3 lesson materials carry custom Japan Foundation terms:
non-commercial text extraction is acceptable, the textbook's illustrations
are untouchable, and redistribution is forbidden. Because of that, THIS
SCRIPT NEVER DOWNLOADS, SCRAPES, OR MIRRORS IRODORI CONTENT FROM ANYWHERE.
It only:

1. Prints where to get the material by hand (see IRODORI_SOURCE_URL below --
   left unset on purpose; fill it in yourself once you have confirmed the
   official Japan Foundation distribution page).
2. Looks for files you already placed under vendor/irodori/.
3. Computes their SHA-256 digests and compares them against whatever is
   already committed in vendor/CHECKSUMS.sha256.
4. Reports any file with no committed digest yet so you can review it and
   append the digest yourself -- this script never writes to
   CHECKSUMS.sha256; that file is a committed pin and deciding what gets
   pinned is a human, by-hand step.

No network access. Not invoked by the katagiri package at runtime or import
time. Run this by hand, once per acquisition/verification pass.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
IRODORI_DIR = VENDOR_DIR / "irodori"
CHECKSUMS_FILE = VENDOR_DIR / "CHECKSUMS.sha256"

# TODO(operator): fill in the official Japan Foundation Irodori distribution
# URL here once you have confirmed it yourself. Do not guess -- an incorrect
# URL here is worse than no URL. Nothing else in this repository pins one
# yet (checked at authoring time), so this is intentionally left blank
# rather than hardcoding a guessed address.
IRODORI_SOURCE_URL: str | None = None  # e.g. "https://www.irodori.jpf.go.jp/..."

EXPECTED_SUFFIXES = (".pdf", ".mp3")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_committed_digests() -> dict[str, str]:
    """Parse vendor/CHECKSUMS.sha256 into {relative_posix_path: digest}."""
    digests: dict[str, str] = {}
    if not CHECKSUMS_FILE.is_file():
        return digests
    for line in CHECKSUMS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, rel = parts
        digests[rel.strip()] = digest.strip().lower()
    return digests


def find_local_files() -> list[Path]:
    if not IRODORI_DIR.is_dir():
        return []
    return sorted(
        p
        for p in IRODORI_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in EXPECTED_SUFFIXES
    )


def main() -> int:
    print("Irodori acquisition helper (Japan Foundation materials)")
    print("=" * 60)
    print(
        "This script never downloads Irodori content: custom Japan Foundation\n"
        "terms forbid redistribution, and the files must never be committed to\n"
        "this repository under any circumstance."
    )
    print()

    if IRODORI_SOURCE_URL:
        print(f"Official source: {IRODORI_SOURCE_URL}")
    else:
        print(
            "Official source URL: NOT SET.\n"
            "  -> Confirm the official Japan Foundation Irodori distribution\n"
            "     page yourself, then fill in IRODORI_SOURCE_URL at the top of\n"
            "     this script (or just acquire the PDF/MP3 lesson files by hand\n"
            "     and skip straight to placing them below -- see vendor/README.md)."
        )
    print(f"Expected location: {IRODORI_DIR.relative_to(REPO_ROOT).as_posix()}/")
    print()

    files = find_local_files()
    if not files:
        print("No local Irodori files found (looked for *.pdf, *.mp3).")
        print(
            "Nothing to verify yet -- this is expected on a fresh checkout.\n"
            "Irodori is acquired by hand and never committed, so an empty\n"
            "result here is not an error."
        )
        return 0

    committed = load_committed_digests()
    ok = 0
    mismatched = 0
    unpinned = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        digest = sha256_of(path)
        expected = committed.get(rel)
        if expected is None:
            print(f"NEW (no committed digest yet): {rel}")
            print(f"    sha256: {digest}")
            unpinned += 1
        elif expected == digest:
            print(f"OK: {rel}")
            ok += 1
        else:
            print(f"MISMATCH: {rel}")
            print(f"    expected: {expected}")
            print(f"    actual:   {digest}")
            mismatched += 1

    print()
    print(
        f"{len(files)} file(s) found: {ok} verified, {unpinned} unpinned, "
        f"{mismatched} mismatched."
    )
    if unpinned:
        print(
            "Review the NEW file(s) above, then append their digests to\n"
            "vendor/CHECKSUMS.sha256 yourself (see its 'Recording checksums'\n"
            "section in vendor/README.md). This script never writes that file."
        )
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
