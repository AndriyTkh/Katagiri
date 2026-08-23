r"""Optional, consent-gated Irodori Starter vocabulary seeding.

Downloads the official, freely-published Japan Foundation Table of Contents
PDF for Irodori Starter (A1) and seeds the words listed in its "Kanji Words"
rows as text-only word items, one ``home_topic`` per lesson (``irodori-l01``
.. ``irodori-l18``), so a fresh install has something real to study instead of
an empty database.

Only the Table of Contents is fetched -- never the full lesson PDFs/MP3s,
which stay hand-acquired per ``vendor/README.md``. JF's Irodori terms
explicitly permit non-commercial text extraction (illustrations are what's
off-limits; see ``vendor/README.md``), and the TOC is a JF-published
reference document, not the copyrighted lesson content itself.

This is the one place in the codebase that fetches data over the network at
setup time -- gated on explicit user consent, asked in
``katagiri.installer.step_irodori``, never in this module. Never imported or
invoked by the MCP server at request time; run only via
``python -m katagiri.irodori_import all`` from the installer.

Usage::

    python -m katagiri.irodori_import all        # download, verify, seed
    python -m katagiri.irodori_import seed        # seed only, from a local TOC
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
IRODORI_DIR = VENDOR_DIR / "irodori"
TOC_PATH = IRODORI_DIR / "starter_contents_en.pdf"
CHECKSUMS_FILE = VENDOR_DIR / "CHECKSUMS.sha256"
CHECKSUM_REL_PATH = "vendor/irodori/starter_contents_en.pdf"

# Official, free Japan Foundation distribution. Confirmed 2026-08-23 by
# fetching https://www.irodori.jpf.go.jp/en/starter/pdf.html and reading its
# real hrefs (the site literally serves filenames with an "X_" prefix).
TOC_URL = "https://www.irodori.jpf.go.jp/assets/data/starter/pdf/X_contents_en.pdf"

LESSON_HEADER_RE = re.compile(r"^Lesson\s+(\d+)\s+(.+)$", re.MULTILINE)
KANJI_WORDS_RE = re.compile(r"^Kanji Words\s+(.+)$", re.MULTILINE)
BRACKETED_RE = re.compile(r"^[（(].*[）)]$")


@dataclass(frozen=True)
class IrodoriLesson:
    number: int
    title_en: str
    kanji_words: tuple[str, ...]


def download_toc(dest: Path = TOC_PATH, *, timeout: float = 30) -> bytes:
    """Fetch the TOC PDF from the official JF URL.

    The only network call in this module. Callers (the installer step) are
    responsible for obtaining consent before calling this.
    """
    request = urllib.request.Request(
        TOC_URL, headers={"User-Agent": "katagiri-installer/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return data


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_or_pin_checksum(path: Path) -> tuple[str, str]:
    """Compare ``path`` against the pinned digest, or pin it on first success.

    Returns ``(status, digest)`` with status one of "verified", "pinned",
    "mismatch". A mismatch is reported, never silently accepted -- same
    doctrine as every other vendored file (see ``vendor/README.md``).
    """
    digest = sha256_of(path)
    existing: str | None = None
    if CHECKSUMS_FILE.is_file():
        for line in CHECKSUMS_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) == 2 and parts[1].strip() == CHECKSUM_REL_PATH:
                existing = parts[0].strip().lower()
                break
    if existing is None:
        with CHECKSUMS_FILE.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{digest}  {CHECKSUM_REL_PATH}\n")
        return "pinned", digest
    if existing != digest:
        return "mismatch", digest
    return "verified", digest


def parse_toc_text(text: str) -> list[IrodoriLesson]:
    """Split the TOC's extracted text into one :class:`IrodoriLesson` per
    "Lesson N <title>" header, pulling its "Kanji Words" row if present.

    Lessons 1-2 teach hiragana/katakana, not kanji, and carry no "Kanji
    Words" row -- an empty ``kanji_words`` tuple for those is correct, not a
    parse failure.
    """
    headers = list(LESSON_HEADER_RE.finditer(text))
    lessons: list[IrodoriLesson] = []
    for i, match in enumerate(headers):
        number = int(match.group(1))
        title = match.group(2).strip()
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[start:end]
        kanji_match = KANJI_WORDS_RE.search(body)
        words = ()
        if kanji_match:
            words = tuple(
                w for w in kanji_match.group(1).split() if not BRACKETED_RE.match(w)
            )
        lessons.append(IrodoriLesson(number=number, title_en=title, kanji_words=words))
    return lessons


def extract_lessons(path: Path = TOC_PATH) -> list[IrodoriLesson]:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    text = "\n".join(page.extract_text() for page in reader.pages)
    return parse_toc_text(text)


def seed_vocabulary(
    conn: sqlite3.Connection | None,
    lessons: list[IrodoriLesson],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seed each lesson's Kanji Words as text-only word items.

    ``home_topic`` is ``irodori-l<NN>`` per lesson. Reading/part-of-speech
    come from a JMdict lookup when one matches; otherwise the word is still
    inserted with ``reading`` left NULL -- the same degrade-visibly pattern
    ``text_only`` already carries elsewhere (see
    ``scripts/import_curriculum.py``), rather than guessing a pronunciation.
    ``INSERT OR IGNORE`` keeps re-runs idempotent.
    """
    from katagiri.jmdict_import import lookup_word
    from katagiri.session_tools import word_item_id

    jmdict_available = False
    if conn is not None:
        try:
            jmdict_available = bool(
                conn.execute("SELECT 1 FROM jmdict_entry LIMIT 1").fetchone()
            )
        except sqlite3.OperationalError:
            jmdict_available = False

    created_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    already_existed = 0
    looked_up = 0
    report: list[dict[str, Any]] = []

    for lesson in lessons:
        topic = f"irodori-l{lesson.number:02d}"
        for word in lesson.kanji_words:
            reading: str | None = None
            pos: str | None = None
            if jmdict_available:
                entries = lookup_word(conn, word, limit=1)
                if entries:
                    looked_up += 1
                    readings = entries[0].get("readings") or []
                    if readings:
                        reading = readings[0]["reading"]
                    senses = entries[0].get("senses") or []
                    if senses and senses[0].get("pos"):
                        pos = ",".join(senses[0]["pos"])

            item_id = word_item_id(word, reading)
            record = {
                "id": item_id,
                "kanji": word,
                "reading": reading,
                "topic": topic,
            }

            if dry_run or conn is None:
                record["action"] = "would-insert"
                report.append(record)
                continue

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO item
                    (id, kind, home_topic, kanji, reading, pos,
                     production_eligible, sealed, text_only, created_ts)
                VALUES (?, 'word', ?, ?, ?, ?, 1, 0, 1, ?)
                """,
                (item_id, topic, word, reading, pos, created_ts),
            )
            if cursor.rowcount == 1:
                inserted += 1
                record["action"] = "inserted"
            else:
                already_existed += 1
                record["action"] = "already-existed"
            report.append(record)

    if not dry_run and conn is not None:
        conn.commit()

    return {
        "lessons": len(lessons),
        "inserted": inserted,
        "already_existed": already_existed,
        "looked_up": looked_up,
        "entries": report,
    }


def _cmd_all(args: argparse.Namespace) -> int:
    print("Katagiri Irodori study-schedule seeder")
    print("=" * 60)
    print(f"Source: {TOC_URL}")
    print(f"Dest:   {TOC_PATH.relative_to(REPO_ROOT).as_posix()}")
    print()

    print("Downloading Table of Contents...")
    download_toc()
    status, digest = verify_or_pin_checksum(TOC_PATH)
    if status == "mismatch":
        print(f"MISMATCH: downloaded file does not match the pinned digest.")
        print(f"  sha256: {digest}")
        print("Refusing to seed from an unexpected file. Nothing was written.")
        return 1
    print(f"Checksum {status}: {digest}")
    print()

    return _seed(args, path=TOC_PATH)


def _cmd_seed(args: argparse.Namespace) -> int:
    print("Katagiri Irodori study-schedule seeder (seed-only, no download)")
    print("=" * 60)
    if not TOC_PATH.is_file():
        print(
            f"Missing {TOC_PATH.relative_to(REPO_ROOT).as_posix()}. "
            "Run 'python -m katagiri.irodori_import all' to download it first."
        )
        return 1
    return _seed(args, path=TOC_PATH)


def _seed(args: argparse.Namespace, *, path: Path) -> int:
    lessons = extract_lessons(path)
    print(f"Parsed {len(lessons)} lesson(s).")
    for lesson in lessons:
        print(
            f"  L{lesson.number:02d}  {lesson.title_en}  "
            f"({len(lesson.kanji_words)} word(s))"
        )
    print()

    if not lessons:
        print("Nothing to seed.")
        return 0

    if args.dry_run:
        result = seed_vocabulary(None, lessons, dry_run=True)
        print(f"Dry run: {len(result['entries'])} word(s) would be attempted.")
        return 0

    from katagiri.db import open_db

    conn = open_db()
    try:
        result = seed_vocabulary(conn, lessons, dry_run=False)
    finally:
        conn.close()

    print(f"Inserted:        {result['inserted']}")
    print(f"Already existed: {result['already_existed']}")
    print(f"JMdict matches:  {result['looked_up']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed an Irodori Starter study schedule from the official TOC."
    )
    parser.add_argument("--dry-run", action="store_true", help="parse and report only")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("all", help="download, verify, and seed")
    sub.add_parser("seed", help="seed from an already-downloaded local TOC")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.command == "all":
        return _cmd_all(args)
    return _cmd_seed(args)


if __name__ == "__main__":
    sys.exit(main())
