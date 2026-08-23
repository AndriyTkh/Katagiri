#!/usr/bin/env python3
"""Seed curriculum vocabulary for a grammar topic from vendored extracts.

Usage
-----
    python scripts/import_curriculum.py --dry-run
    python scripts/import_curriculum.py --topic g-desu-copula
    py -3 scripts/import_curriculum.py                       # Windows launcher

    # Curriculum graph (item/item_edge) instead of vocab seeding:
    python scripts/import_curriculum.py --curriculum-md --dry-run
    python scripts/import_curriculum.py --curriculum-md --curriculum-path curriculum.md

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

``--curriculum-md`` is a separate mode from the vocab seeding above: it is a
thin CLI wrapper around ``katagiri.intelligence.import_curriculum``, which
parses the vault's ``curriculum.md`` grammar graph (nodes/edges/T028
attributes) into the ``item``/``item_edge`` tables. It shares nothing with the
Tae Kim vocab flow except the argparse entry point and the DB connection.

This is a one-time, by-hand import script. It is never imported or invoked by
the katagiri package at runtime, takes no network access (the curriculum-md
mode still touches only the local DB and vault-configured file), and is
idempotent: every write is ``INSERT OR IGNORE`` or an additive upsert, so a
re-run leaves existing ids/edges/attributes alone.
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


def _run_curriculum_md(args: argparse.Namespace) -> int:
    """Handle ``--curriculum-md``: import curriculum.md's grammar graph.

    A thin wrapper around ``katagiri.intelligence.import_curriculum`` -- all the
    parsing, cycle detection and idempotent upserts live there. This just opens
    the DB, calls it, and prints the result dict human-readably.
    """
    from katagiri.db import open_db
    from katagiri.intelligence import import_curriculum

    print("Katagiri curriculum graph importer")
    print("=" * 60)
    print(f"Mode:   {'DRY RUN (no writes)' if args.dry_run else 'writing to DB'}")
    if args.curriculum_path:
        print(f"Path:   {args.curriculum_path}")
    print()

    conn = open_db()
    try:
        result = import_curriculum(
            conn,
            path=args.curriculum_path,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if not result.get("ok", False):
        print(f"FAILED: {result.get('error', 'UNKNOWN_ERROR')}")
        note = result.get("note")
        if note:
            print(note)
        skipped = result.get("skipped")
        if skipped:
            print("Skipped:")
            for line in skipped:
                print(f"  {line}")
        return 1

    print(f"Curriculum file: {result['path']}")
    print()

    nodes = result["nodes"]
    print("Nodes:")
    print(f"  ids (mentioned, written):   {nodes['ids']}")
    print(f"  declared in file:           {nodes['declared']}")
    print(f"  items created:              {nodes['items_created']}")
    print(f"  stubs created (id-only):    {nodes['stubs_created']}")
    print(f"  levels filled in:           {nodes['levels_filled']}")
    print(f"  unchanged:                  {nodes['unchanged']}")
    print()

    edges = result["edges"]
    print("Edges:")
    print(f"  parsed:                     {edges['parsed']}")
    print(f"  written (not blocked):      {edges['written']}")
    print(f"  created (new):              {edges['created']}")
    print(f"  already present:            {edges['already_present']}")
    if edges["by_type"]:
        print(f"  by type:                    {edges['by_type']}")
    print()

    orphan_edges = result["orphan_edges"]
    print(f"Orphan edges (in DB, not in file, never deleted): {len(orphan_edges)}")
    for edge in orphan_edges:
        print(f"  {edge['from_id']} -> {edge['to_id']} ({edge['edge_type']})")
    print()

    attrs = result["attributes"]
    print("Attributes (T028 tags):")
    print(f"  parsed:                     {attrs['parsed']}")
    print(f"  created (new):              {attrs['created']}")
    print(f"  already present:            {attrs['already_present']}")
    if attrs["by_attr"]:
        print(f"  by attr:                    {attrs['by_attr']}")
    print()

    orphan_attrs = result["orphan_attributes"]
    print(f"Orphan attributes (in DB, not in file, never deleted): {len(orphan_attrs)}")
    for orphan in orphan_attrs:
        print(f"  {orphan['id']}  {orphan['attribute']}")
    print()

    skipped = result["skipped"]
    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for line in skipped:
            print(f"  {line}")
        print()

    note = result.get("note")
    if note:
        print(note)
        print()

    if result["dry_run"]:
        print("Dry run: nothing was written. Re-run without --dry-run to write.")
    else:
        print("Done: curriculum graph written to the database.")
    return 0


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
    parser.add_argument(
        "--curriculum-md",
        action="store_true",
        help=(
            "import the curriculum graph (item/item_edge) from curriculum.md via "
            "katagiri.intelligence.import_curriculum, instead of seeding Tae Kim "
            "vocab. --dry-run and --curriculum-path apply to this mode; --topic "
            "is ignored."
        ),
    )
    parser.add_argument(
        "--curriculum-path",
        default=None,
        help=(
            "override the curriculum.md path (default: the vault's copy, per "
            "katagiri.intelligence.curriculum_path). Only used with "
            "--curriculum-md."
        ),
    )
    args = parser.parse_args(argv)

    # Japanese text in a cp1252 console raises UnicodeEncodeError otherwise.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.curriculum_md:
        return _run_curriculum_md(args)

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
