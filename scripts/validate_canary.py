#!/usr/bin/env python3
"""Validate the sealed canary probe set and check the vault for contamination.

Usage
-----
    python scripts/validate_canary.py          # from the repo root
    py -3 scripts/validate_canary.py           # Windows launcher

Exit code 0 = clean, 1 = failure. Intended to be wired into a pre-commit hook
and into CI once either exists; for now run it manually after touching anything
under docs/katagiri/katagiri/.

What it checks
--------------
1. The canary set file parses: YAML-ish frontmatter has `sealed: true`, and the
   band tables yield exactly 200 rows with unique ids.
2. Every id equals "s-" + sha1(japanese)[:6] recomputed from the Japanese
   column, so a silently edited sentence is caught.
3. No canary id and no exact canary Japanese string appears anywhere else under
   docs/katagiri/katagiri/ (the canary directory itself is excluded). Any hit is
   a contamination failure: the sentences must never be studied.

Python 3.12+, standard library only. It must run before the project package
exists, so it imports nothing from this repository.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT = REPO_ROOT / "docs" / "katagiri" / "katagiri"
CANARY_DIR = VAULT / "90-meta" / "canary"
CANARY_FILE = CANARY_DIR / "canary-set.md"

EXPECTED_COUNT = 200
EXPECTED_BANDS = ("b1", "b2", "b3", "b4", "b5")
SCAN_SUFFIXES = (".md", ".jsonl", ".txt", ".tsv")

ID_RE = re.compile(r"^s-[0-9a-f]{6}$")

# Contamination reports echo Japanese text; a default Windows console is cp1252
# and would raise UnicodeEncodeError mid-report. Force UTF-8 on our own streams.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):  # pragma: no cover
        pass


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")


def sentence_id(japanese: str) -> str:
    return "s-" + hashlib.sha1(japanese.encode("utf-8")).hexdigest()[:6]


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter mapping, body). Frontmatter is flat key: value only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1 :])
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return {}, text


def parse_rows(body: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Extract (id, band, japanese) triples from markdown table rows."""
    rows: list[tuple[str, str, str]] = []
    problems: list[str] = []
    for lineno, line in enumerate(body.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        first = cells[0]
        if first in ("id", "") or set(first) <= set("-: "):
            continue  # header or separator row
        if not ID_RE.match(first):
            problems.append(f"line {lineno}: table row with malformed id {first!r}")
            continue
        if len(cells) != 5:
            problems.append(
                f"line {lineno}: row {first} has {len(cells)} columns, expected 5"
            )
            continue
        rows.append((first, cells[1], cells[2]))
    return rows, problems


def scan_for_contamination(
    ids: set[str], sentences: dict[str, str]
) -> tuple[int, int]:
    """Search the vault (minus the canary dir) for canary ids and sentences."""
    hits = 0
    scanned = 0
    for path in sorted(VAULT.rglob("*")):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        try:
            path.relative_to(CANARY_DIR)
        except ValueError:
            pass
        else:
            continue  # inside the canary directory: exempt
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(REPO_ROOT).as_posix()
        for cid in sorted(ids):
            if cid in text:
                print(f"CANARY CONTAMINATION: {rel}: {cid}")
                hits += 1
        for japanese in sorted(sentences):
            if japanese in text:
                print(f"CANARY CONTAMINATION: {rel}: {japanese}")
                hits += 1
    return scanned, hits


def main() -> int:
    if not CANARY_FILE.is_file():
        fail(f"canary set not found at {CANARY_FILE}")
        return 1

    text = CANARY_FILE.read_text(encoding="utf-8")
    meta, body = split_frontmatter(text)
    errors: list[str] = []

    if meta.get("sealed") != "true":
        errors.append(
            f"frontmatter must declare 'sealed: true' (found {meta.get('sealed')!r})"
        )

    rows, problems = parse_rows(body)
    errors.extend(problems)

    if len(rows) != EXPECTED_COUNT:
        errors.append(f"expected {EXPECTED_COUNT} sentences, parsed {len(rows)}")

    seen: dict[str, str] = {}
    band_counts: dict[str, int] = {b: 0 for b in EXPECTED_BANDS}
    sentences: dict[str, str] = {}
    for cid, band, japanese in rows:
        if cid in seen:
            errors.append(f"duplicate id {cid} (japanese {japanese!r} and {seen[cid]!r})")
        else:
            seen[cid] = japanese
        recomputed = sentence_id(japanese)
        if recomputed != cid:
            errors.append(f"id mismatch for {japanese!r}: file says {cid}, sha1 gives {recomputed}")
        if band in band_counts:
            band_counts[band] += 1
        else:
            errors.append(f"row {cid}: unknown band {band!r}")
        if japanese in sentences:
            errors.append(f"duplicate japanese string: {japanese!r}")
        sentences[japanese] = cid

    scanned, hits = scan_for_contamination(set(seen), sentences)

    if hits:
        fail(f"{hits} contamination hit(s) across {scanned} scanned file(s)")
        for err in errors:
            fail(err)
        return 1

    if errors:
        for err in errors:
            fail(err)
        return 1

    print("canary set OK")
    print(f"  file:          {CANARY_FILE.relative_to(REPO_ROOT).as_posix()}")
    print("  sealed:        true")
    print(f"  sentences:     {len(rows)}")
    print(f"  unique ids:    {len(seen)}")
    print("  hashes:        all 200 ids match sha1(japanese)[:6]")
    print(
        "  bands:         "
        + ", ".join(f"{b}={band_counts[b]}" for b in EXPECTED_BANDS)
    )
    print(f"  vault files scanned (canary dir excluded): {scanned}")
    print("  contamination: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
