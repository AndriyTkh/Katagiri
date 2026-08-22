#!/usr/bin/env python3
"""Fetch/extract the vendored Tae Kim grammar-guide material.

Usage
-----
    python scripts/fetch_taekim.py
    py -3 scripts/fetch_taekim.py            # Windows launcher

Tae Kim's "Guide to Japanese Grammar" (guidetojapanese.org) is published
under a Creative Commons Attribution-NonCommercial-ShareAlike (CC BY-NC-SA)
license. Unlike Irodori, extracts of this material MAY be committed to this
repository -- but only together with the attribution below (also recorded in
vendor/README.md).

This is a one-time, by-hand setup/acquisition script. It is never imported
or invoked by the katagiri package at runtime, and it makes no network call
unless you run it directly yourself.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
TAEKIM_DIR = VENDOR_DIR / "taekim"
CHECKSUMS_FILE = VENDOR_DIR / "CHECKSUMS.sha256"
README_FILE = VENDOR_DIR / "README.md"

# The specific extracts this script acquires. Kept to a short, named list on
# purpose -- this is a set of scoped extracts, not a mirror of the whole site.
# Add a page here (and a matching parser in import_curriculum.py) as more
# grammar topics need seed vocabulary.
PAGES: tuple[tuple[str, str], ...] = (
    ("https://guidetojapanese.org/learn/grammar", "grammar-index.html"),
    ("https://guidetojapanese.org/learn/grammar/stateofbeing", "stateofbeing.html"),
)

ATTRIBUTION_NOTICE = (
    "Tae Kim's Guide to Japanese Grammar, (c) Tae Kim, "
    "https://guidetojapanese.org/ -- licensed under CC BY-NC-SA "
    "(Creative Commons Attribution-NonCommercial-ShareAlike). Extracts under "
    "vendor/taekim/ are used and redistributed under that license; this "
    "notice is the license's required attribution."
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "katagiri-vendor-fetch/1 (by-hand, one-time)"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (by-hand script)
        return resp.read()


def load_committed_digests() -> dict[str, str]:
    digests: dict[str, str] = {}
    if not CHECKSUMS_FILE.is_file():
        return digests
    for line in CHECKSUMS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            digest, rel = parts
            digests[rel.strip()] = digest.strip().lower()
    return digests


def append_checksum(rel_path: str, digest: str) -> None:
    existing = (
        CHECKSUMS_FILE.read_text(encoding="utf-8") if CHECKSUMS_FILE.is_file() else ""
    )
    if rel_path in existing:
        return  # already recorded; never duplicate
    if existing and not existing.endswith("\n"):
        existing += "\n"
    existing += f"{digest}  {rel_path}\n"
    CHECKSUMS_FILE.write_text(existing, encoding="utf-8")


def attribution_recorded() -> bool:
    if not README_FILE.is_file():
        return False
    text = README_FILE.read_text(encoding="utf-8")
    return "guidetojapanese.org" in text and "CC BY-NC-SA" in text


def main() -> int:
    print("Tae Kim grammar-guide acquisition helper")
    print("=" * 60)
    print("License: CC BY-NC-SA -- committable WITH attribution (unlike Irodori).")
    print()

    TAEKIM_DIR.mkdir(parents=True, exist_ok=True)
    committed = load_committed_digests()
    failures = 0

    for source_url, dest_name in PAGES:
        dest = TAEKIM_DIR / dest_name
        rel = dest.relative_to(REPO_ROOT).as_posix()
        print(f"Source: {source_url}")

        try:
            data = fetch(source_url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Could not fetch {source_url}: {exc}")
            print(
                "No network access from this environment, or the site is\n"
                "unreachable right now. Run this script by hand, from a machine\n"
                "with internet access, when you are ready to (re)acquire this\n"
                "Tae Kim extract. Nothing was written for this page."
            )
            failures += 1
            print()
            continue

        dest.write_bytes(data)
        digest = sha256_bytes(data)
        print(f"Wrote {rel} ({len(data)} bytes).")
        print(f"sha256: {digest}")

        if committed.get(rel) == digest:
            print("Digest matches the one already committed in vendor/CHECKSUMS.sha256.")
        else:
            append_checksum(rel, digest)
            committed[rel] = digest
            print(
                f"Recorded digest in {CHECKSUMS_FILE.relative_to(REPO_ROOT).as_posix()}."
            )
        print()

    if attribution_recorded():
        print("Attribution notice already present in vendor/README.md.")
    else:
        print(
            "WARNING: attribution notice not found in vendor/README.md.\n"
            f"Required text: {ATTRIBUTION_NOTICE}"
        )

    print("Review the extracts and the CHECKSUMS.sha256 diff before committing.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
