#!/usr/bin/env python3
"""Seed curriculum vocabulary for a grammar topic from vendored extracts.

Usage
-----
    python scripts/import_curriculum.py --dry-run
    python scripts/import_curriculum.py --topic g-desu-copula
    py -3 scripts/import_curriculum.py                       # Windows launcher

Why this exists
---------------
The DB ships 20 grammar skeleton items (``g-desu-copula`` and friends) with no
word items tagged to them, so ``gen_exercise(topic=...)`` answers
NO_CANDIDATES. That is a content gap, not a code bug: something has to put
real words behind the skeleton. This script does it from the material already
vendored under ``vendor/taekim/``.

Tae Kim's "Guide to Japanese Grammar" (guidetojapanese.org) is CC BY-NC-SA, so
extracts of it MAY live in this repository together with the attribution
recorded in ``vendor/README.md``. Irodori may not, which is why
:class:`IrodoriVocabSource` below is a deliberate stub.

This is a one-time, by-hand import script. It is never imported or invoked by
the katagiri package at runtime, takes no network access, and is idempotent:
every write is ``INSERT OR IGNORE``, so a re-run leaves existing ids alone.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "vendor"
TAEKIM_STATEOFBEING = VENDOR_DIR / "taekim" / "stateofbeing.html"

DEFAULT_TOPIC = "g-desu-copula"

# The Tae Kim page shape, verified against the vendored HTML:
#
#   <h3>Vocabulary</h3>
#   <ol>
#   <li>人 【ひと】 &#8211; person</li>
#   <li>元気 【げん・き】 &#8211; healthy; lively<br />
#   ＊Used as a greeting to indicate whether one is well</li>
#   </ol>
#
# The block repeats once per grammar section on the page, and words recur
# across blocks -- hence the dedupe in TaeKimVocabSource.extract().
VOCAB_BLOCK_RE = re.compile(r"<h3>Vocabulary</h3>\s*<ol>(.*?)</ol>", re.S)
LIST_ITEM_RE = re.compile(r"<li>(.*?)</li>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
ENTRY_RE = re.compile(
    r"^(?P<kanji>\S+)\s+【(?P<reading>[^】]+)】\s*[–—-]\s*(?P<gloss>.+)$"
)


@dataclass(frozen=True)
class VocabEntry:
    """One parsed dictionary word, before it becomes an ``item`` row."""

    kanji: str
    reading: str  # katakana/hiragana reading, no "・" separators
    gloss: str  # console output only; the item table has no gloss column
    source: str  # provenance tag, e.g. "taekim:stateofbeing"


class VocabSource(Protocol):
    """Anything that can hand back a list of parsed vocabulary entries."""

    def extract(self) -> list[VocabEntry]: ...


class TaeKimVocabSource:
    """Parse the Vocabulary lists out of a vendored Tae Kim grammar page."""

    def __init__(
        self, path: Path = TAEKIM_STATEOFBEING, source: str = "taekim:stateofbeing"
    ) -> None:
        self.path = path
        self.source = source

    def extract(self) -> list[VocabEntry]:
        raw_html = self.path.read_text(encoding="utf-8", errors="replace")
        blocks = VOCAB_BLOCK_RE.findall(raw_html)
        if not blocks:
            print(
                f"WARNING: no '<h3>Vocabulary</h3><ol>...' block found in "
                f"{self.path.name}; the page shape may have changed upstream."
            )
            return []
        print(f"Found {len(blocks)} Vocabulary block(s) in {self.path.name}.")

        entries: list[VocabEntry] = []
        seen: set[tuple[str, str]] = set()

        for block in blocks:
            for raw_item in LIST_ITEM_RE.findall(block):
                # Drop any leftover markup (<br />, <span>, ...), then unescape
                # so &#8211; becomes the en dash the entry regex expects.
                text = html.unescape(TAG_RE.sub(" ", raw_item))
                # Some entries trail an annotation line ("＊Used as a greeting
                # ..."). Keep only the definition line itself.
                for cut in ("\n", "＊"):
                    idx = text.find(cut)
                    if idx != -1:
                        text = text[:idx]
                text = text.strip()
                if not text:
                    continue

                match = ENTRY_RE.match(text)
                if match is None:
                    print(f"WARNING: skipping unrecognised vocab line: {text!r}")
                    continue

                kanji = match.group("kanji").strip()
                reading = match.group("reading").replace("・", "").strip()
                gloss = match.group("gloss").strip()

                key = (kanji, reading)
                if key in seen:
                    continue  # first occurrence wins, including its gloss
                seen.add(key)
                entries.append(
                    VocabEntry(
                        kanji=kanji, reading=reading, gloss=gloss, source=self.source
                    )
                )

        return entries


class IrodoriVocabSource:
    """Placeholder for the Irodori side of the curriculum.

    Intentionally unimplemented. The Irodori PDFs are not CC-licensed and are
    never committed to this repository, so extracting them needs a separate,
    local-only path that a later task will design.
    """

    def extract(self) -> list[VocabEntry]:
        raise NotImplementedError(
            "Irodori PDF parsing not implemented yet -- see vendor/README.md "
            "for why the raw PDF is never committed"
        )


def import_entries(
    conn,
    entries: list[VocabEntry],
    topic: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Write ``entries`` as text-only word items homed on ``topic``.

    ``text_only=1`` is load-bearing, not a placeholder: these words carry no
    audio anchor (``audio_source`` stays NULL), and per
    migrations/0002_audio_anchors.sql plus the A0-production gate in
    intelligence.py, ``text_only`` is exactly the flag that withholds an
    unanchored item from A0 production drills while still allowing it as
    reading/recognition material.

    ``INSERT OR IGNORE`` keeps re-runs idempotent: an id that already exists
    (e.g. one the learner later mined for real via ``add_vocab``) is left
    untouched, never overwritten.
    """
    # Same one-liner timestamp pattern used elsewhere in this codebase
    # (e.g. src/katagiri/jmdict_import.py) -- there is no shared helper.
    created_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    from katagiri.session_tools import word_item_id

    inserted = 0
    already_existed = 0
    reported: list[dict] = []

    for entry in entries:
        item_id = word_item_id(entry.kanji, entry.reading)
        record = {
            "id": item_id,
            "kanji": entry.kanji,
            "reading": entry.reading,
            "gloss": entry.gloss,
            "source": entry.source,
        }

        if dry_run:
            record["action"] = "would-insert"
            reported.append(record)
            continue

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO item
                (id, kind, home_topic, kanji, reading,
                 production_eligible, sealed, text_only, created_ts)
            VALUES (?, 'word', ?, ?, ?, 1, 0, 1, ?)
            """,
            (item_id, topic, entry.kanji, entry.reading, created_ts),
        )
        if cursor.rowcount == 1:
            inserted += 1
            record["action"] = "inserted"
        else:
            already_existed += 1
            record["action"] = "already-existed"
        reported.append(record)

    if not dry_run:
        conn.commit()

    return {
        "inserted": inserted,
        "already_existed": already_existed,
        "entries": reported,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed word items for a grammar topic from vendored Tae Kim extracts."
        )
    )
    parser.add_argument(
        "--topic",
        default=DEFAULT_TOPIC,
        help=f"home_topic to tag the imported words with (default: {DEFAULT_TOPIC})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and report only; do not touch the database",
    )
    args = parser.parse_args(argv)

    # Japanese text in a cp1252 console raises UnicodeEncodeError otherwise.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    print("Katagiri curriculum vocabulary importer")
    print("=" * 60)
    print(f"Topic:  {args.topic}")
    print(f"Mode:   {'DRY RUN (no writes)' if args.dry_run else 'writing to DB'}")
    print()

    if not TAEKIM_STATEOFBEING.is_file():
        print(
            f"Missing vendored extract: "
            f"{TAEKIM_STATEOFBEING.relative_to(REPO_ROOT).as_posix()}\n"
            "Run scripts/fetch_taekim.py by hand first."
        )
        return 1

    source: VocabSource = TaeKimVocabSource()
    entries = source.extract()
    print(f"Parsed {len(entries)} distinct entr{'y' if len(entries) == 1 else 'ies'}:")
    for entry in entries:
        print(f"  {entry.kanji}  [{entry.reading}]  -- {entry.gloss}  ({entry.source})")
    print()

    if not entries:
        print("Nothing to import.")
        return 0

    if args.dry_run:
        result = import_entries(None, entries, args.topic, dry_run=True)
        for record in result["entries"]:
            print(f"  {record['action']}: {record['id']}  {record['kanji']}")
        print()
        print(f"Dry run: {len(result['entries'])} row(s) would be attempted.")
        print("Re-run without --dry-run to write them.")
        return 0

    from katagiri.db import open_db

    conn = open_db()
    try:
        result = import_entries(conn, entries, args.topic, dry_run=False)
    finally:
        conn.close()

    for record in result["entries"]:
        print(f"  {record['action']}: {record['id']}  {record['kanji']}")
    print()
    print(f"Inserted:        {result['inserted']}")
    print(f"Already existed: {result['already_existed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
