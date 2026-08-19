"""Yomitan dictionary export: paint the known set onto every Japanese web page.

Yomitan (the Yomichan successor) reads a *dictionary* as a zip containing an
``index.json`` plus numbered ``term_bank_N.json`` files. Katagiri exports its
known set as such a dictionary whose only content is a tag: every known word is
one term entry carrying the ``known`` tag and no definition. Yomitan then shows
that tag whenever the learner hovers a word they already own, which is the
cheapest possible daily-felt payoff — no new UI, no new habit, just colour on
text they were reading anyway.

Format decisions (all deliberately minimal-valid rather than clever):

* ``format: 3`` with **V1 term banks** — an array of 8-element arrays
  ``[expression, reading, definitionTags, rules, score, definitions, sequence,
  termTags]``. V3 dictionaries may use either term-bank shape; the array form is
  the one Yomitan has read unchanged the longest, so it is the least likely to
  break on an upgrade.
* ``definitionTags`` **and** ``termTags`` both carry ``known``: the first is what
  Yomitan renders next to the (empty) definition, the second is what it renders
  next to the headword. Only the second is visible in the compact popup, so
  dropping either loses the signal in one of the two views.
* ``definitions`` is ``["known"]`` rather than empty. Yomitan hides an entry with
  no definitions at all, and an entry that cannot be seen cannot colour anything.
* ``revision`` is ``<UTC date>-<term count>``. Yomitan shows the revision string
  in its dictionary list, so the learner can tell at a glance which import they
  are running and how big it was, and two exports on the same day still differ.
* The zip is written with fixed member timestamps, so the same known set always
  produces a byte-identical file. A re-import that changes nothing should look
  like it changed nothing.

**Suspect items are excluded.** ``known_set`` exposes ``suspect`` as a flag
beside ``is_known`` rather than folding it in, precisely so each consumer can
decide. For this consumer the decision is exclusion: the whole value of the
overlay is that the colour is trustworthy, and a word the learner has flagged as
"I am not sure I really know this" painted the same green as one they own would
make every green mark mean "probably". Uncertain knowledge must not colour as
known. The item still shows up in Yomitan through its normal dictionaries; it
just does not get Katagiri's badge until the suspicion is resolved.

Regeneration is **drift-triggered**, not scheduled: re-importing is a manual
60-second chore in Yomitan's settings, so it is worth doing only when the
dictionary is meaningfully out of date. The trigger is a change of more than
:data:`DRIFT_THRESHOLD` exportable terms since the last regen. Both outcomes —
the regen and the decision *not* to regen — are appended to the event log, so
"why is my overlay stale?" has an answer in the history rather than in a guess.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from katagiri import db
from katagiri.config import get_config
from katagiri.events import append_event
from katagiri.logging_setup import get_logger

_log = get_logger("yomitan_export")

DICT_TITLE: Final = "Katagiri Known"
DICT_FORMAT: Final = 3
KNOWN_TAG: Final = "known"
TAG_CATEGORY: Final = "frequent"
TAG_NOTES: Final = "Katagiri known word"

REGEN_EVENT_TYPE: Final = "yomitan_regen"
SKIP_EVENT_TYPE: Final = "yomitan_skip"
SESSION_ID: Final = "yomitan-export"

DRIFT_THRESHOLD: Final = 150
OUTPUT_SUBDIR: Final = "yomitan"

INDEX_MEMBER: Final = "index.json"
TERM_BANK_MEMBER: Final = "term_bank_1.json"
TAG_BANK_MEMBER: Final = "tag_bank_1.json"

# Fixed member timestamp so an unchanged known set yields an unchanged zip.
_ZIP_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)

MAX_SURFACE_LEN: Final = 24

# Kana, CJK ideographs (incl. extension A and compatibility), the iteration mark
# 々 and the long vowel mark ー. A surface with none of these is not a Japanese
# word Yomitan will ever look up.
_JAPANESE_RE: Final = re.compile(
    "[々぀-ゟ゠-ヿー㐀-䶿一-鿿豈-﫿]"
)
# Whitespace, and the placeholder marks a dictionary-form entry uses for a slot
# (〜, ～, ellipsis, ASCII tilde). None of them appear inside running text, so a
# surface containing one would never match anything on a page.
_IMPLAUSIBLE_RE: Final = re.compile("[\\s〜～…~]")


class YomitanExportError(RuntimeError):
    """Raised when the dictionary cannot be written."""


# ---------------------------------------------------------------------------
# Reading the known set
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Term:
    """One exportable headword.

    ``reading`` is empty when it would merely repeat ``expression`` — that is
    Yomitan's own convention for a kana-only headword, and writing the kana twice
    makes the popup show it twice.
    """

    expression: str
    reading: str

    def bank_entry(self) -> list[Any]:
        """This term as a V1 term-bank row."""
        return [
            self.expression,
            self.reading,
            KNOWN_TAG,  # definitionTags
            "",  # rules (deinflection) — none: these are surface marks
            0,  # score
            [KNOWN_TAG],  # definitions
            0,  # sequence
            KNOWN_TAG,  # termTags
        ]


@dataclass(frozen=True, slots=True)
class KnownTerms:
    """The exportable terms, plus what was left out and why."""

    terms: tuple[Term, ...] = ()
    skipped_no_item: int = 0
    skipped_no_surface: int = 0
    duplicates: int = 0

    @property
    def count(self) -> int:
        return len(self.terms)

    def skip_counts(self) -> dict[str, int]:
        return {
            "skipped_no_item": self.skipped_no_item,
            "skipped_no_surface": self.skipped_no_surface,
            "duplicates": self.duplicates,
        }


def plausible_surface(value: str | None) -> bool:
    """Could ``value`` appear verbatim in Japanese text on a web page?

    Yomitan matches on exact substrings, so a headword that cannot occur in
    running text — a grammar pattern with a 〜 slot, a whole sentence, an id, an
    empty string — can only bloat the dictionary. Length is capped because a
    surface longer than :data:`MAX_SURFACE_LEN` characters is a sentence that
    ended up in a word field, not a word.
    """
    if not value:
        return False
    text = value.strip()
    if not text or len(text) > MAX_SURFACE_LEN:
        return False
    if _IMPLAUSIBLE_RE.search(text):
        return False
    return _JAPANESE_RE.search(text) is not None


def known_terms(conn: sqlite3.Connection) -> KnownTerms:
    """Every known word that can be painted onto a page.

    Selects ``known_set`` rows with ``is_known = 1`` and ``suspect = 0`` (see the
    module docstring for why suspicion excludes), restricted to items of kind
    ``word`` plus rows with no ``item`` row at all — the mark-only ids the view's
    UNION keeps visible. Those unlinked ids are carried into the candidate set on
    purpose rather than filtered out in SQL, so they can be *counted* here
    instead of vanishing: an id that has been marked known but never imported has
    no kanji and no reading, so there is no surface to colour, and a silent drop
    would hide a real gap between the mark log and the item table.

    Kanji, grammar and sentence items are excluded by kind. A single kanji is not
    a word boundary Yomitan looks up the way this overlay means it, and a grammar
    pattern or sentence never matches running text verbatim.

    Duplicate ``(expression, reading)`` pairs — two item ids for the same
    surface, typically an alias that has not been merged — collapse to one entry
    and are counted.
    """
    rows = conn.execute(
        """
        SELECT k.item_id AS item_id,
               i.id      AS linked_id,
               i.kanji   AS kanji,
               i.reading AS reading
          FROM known_set k
          LEFT JOIN item i ON i.id = k.item_id
         WHERE k.is_known = 1
           AND k.suspect = 0
           AND (i.kind = 'word' OR i.kind IS NULL)
         ORDER BY k.item_id
        """
    ).fetchall()

    seen: set[tuple[str, str]] = set()
    terms: list[Term] = []
    skipped_no_item = 0
    skipped_no_surface = 0
    duplicates = 0

    for row in rows:
        if row["linked_id"] is None:
            # A mark on an id that has no item row yet: nothing to colour.
            skipped_no_item += 1
            continue

        kanji = (row["kanji"] or "").strip()
        reading = (row["reading"] or "").strip()
        expression = kanji or reading
        if not plausible_surface(expression):
            skipped_no_surface += 1
            continue

        # A reading is only worth carrying when it adds something and is itself a
        # usable surface; otherwise Yomitan is better off with none.
        if reading and reading != expression and plausible_surface(reading):
            term_reading = reading
        else:
            term_reading = ""

        key = (expression, term_reading)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        terms.append(Term(expression=expression, reading=term_reading))

    # Sorted so the zip is reproducible regardless of id order.
    terms.sort(key=lambda term: (term.expression, term.reading))

    return KnownTerms(
        terms=tuple(terms),
        skipped_no_item=skipped_no_item,
        skipped_no_surface=skipped_no_surface,
        duplicates=duplicates,
    )


# ---------------------------------------------------------------------------
# Writing the dictionary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenResult:
    """A dictionary that was written."""

    path: Path
    terms: int
    revision: str
    event_id: str | None = None
    skipped: dict[str, int] = field(default_factory=dict)


def output_dir(out_dir: Path | str | None = None) -> Path:
    """Where dictionaries are written: ``<scratch_root>/yomitan`` by default.

    Scratch, not the config directory: a dictionary zip is a disposable artefact
    regenerated from the database whenever it drifts, and it has no business
    sitting next to the backups.
    """
    if out_dir is not None:
        return Path(out_dir)
    return get_config().scratch_root / OUTPUT_SUBDIR


def make_revision(terms: int, *, today: str | None = None) -> str:
    """``<UTC date>-<term count>``, the string Yomitan shows in its list."""
    date = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{date}-{terms}"


def build_index(revision: str) -> dict[str, Any]:
    """The ``index.json`` payload. Minimal-valid for a format-3 dictionary."""
    return {
        "title": DICT_TITLE,
        "format": DICT_FORMAT,
        "revision": revision,
        "sequenced": False,
        "author": "Katagiri",
        "description": (
            "Katagiri's known set as tags. Every entry marks a word the learner "
            "already knows; there are no definitions."
        ),
    }


def build_tag_bank() -> list[list[Any]]:
    """The ``tag_bank_1.json`` payload: one tag, ``known``.

    ``[name, category, order, notes, score]``. The ``frequent`` category is what
    gives the tag Yomitan's neutral pill styling; ``order`` 0 puts it first,
    which matters because it is the only thing these entries carry.
    """
    return [[KNOWN_TAG, TAG_CATEGORY, 0, TAG_NOTES, 0]]


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_dict_zip(path: Path, terms: tuple[Term, ...], revision: str) -> None:
    """Write the dictionary zip at ``path``, atomically.

    Written to a sibling ``.part`` file and renamed, so an interrupted export
    cannot leave a half-written zip that Yomitan will happily try to import.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.part")
    members = (
        (INDEX_MEMBER, _dump(build_index(revision))),
        (TERM_BANK_MEMBER, _dump([term.bank_entry() for term in terms])),
        (TAG_BANK_MEMBER, _dump(build_tag_bank())),
    )
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, blob in members:
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, blob)
        temp.replace(path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise YomitanExportError(
            f"Could not write the Yomitan dictionary {path}: {exc}"
        ) from exc


def dict_filename(revision: str) -> str:
    return f"katagiri-known-{revision}.zip"


def generate_dict(
    conn: sqlite3.Connection, out_dir: Path | str | None = None
) -> GenResult:
    """Write a Yomitan dictionary for the current known set and log the regen.

    The event's payload carries the **basename only**, never the full path: the
    event log is durable, append-only and backed up, and a machine-local absolute
    path is exactly the kind of thing that has no business being permanent.
    """
    known = known_terms(conn)
    revision = make_revision(known.count)
    path = output_dir(out_dir) / dict_filename(revision)
    write_dict_zip(path, known.terms, revision)

    event_id = append_event(
        conn,
        type=REGEN_EVENT_TYPE,
        session_id=SESSION_ID,
        payload={
            "terms": known.count,
            "revision": revision,
            "path": path.name,
            **known.skip_counts(),
        },
    )
    _log.info(
        "Wrote Yomitan dictionary %s (%d terms, revision %s).",
        path.name,
        known.count,
        revision,
    )
    if known.skipped_no_item:
        _log.info(
            "%d known id(s) had no item row and were skipped; they have no "
            "surface to colour.",
            known.skipped_no_item,
        )
    return GenResult(
        path=path,
        terms=known.count,
        revision=revision,
        event_id=event_id,
        skipped=known.skip_counts(),
    )


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriftResult:
    """How far the last exported dictionary has drifted from the known set."""

    delta: int
    threshold: int
    should_regen: bool
    last_regen_terms: int | None

    @property
    def first_run(self) -> bool:
        return self.last_regen_terms is None


def _last_regen_terms(conn: sqlite3.Connection) -> int | None:
    """``payload.terms`` of the most recent regen event, or ``None``.

    Ordered by ``id``: ULIDs sort by time, so two regens in the same second still
    order correctly, which ``ts_server`` alone could not do.
    """
    row = conn.execute(
        """
        SELECT payload
          FROM event
         WHERE type = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (REGEN_EVENT_TYPE,),
    ).fetchone()
    if row is None or row["payload"] is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except json.JSONDecodeError:  # pragma: no cover - schema CHECKs json_valid
        return None
    terms = payload.get("terms") if isinstance(payload, dict) else None
    return int(terms) if isinstance(terms, int) else None


def check_drift(
    conn: sqlite3.Connection, threshold: int = DRIFT_THRESHOLD
) -> DriftResult:
    """Compare the current exportable term count against the last regen's.

    ``delta`` is signed — positive when the known set grew — but the trigger
    compares its magnitude. A known set that *shrank* by two hundred words (a
    re-import that reset intervals, a batch of unknown marks) leaves the overlay
    lying just as badly as one that grew, and lying green is the worse direction
    to be stale in.

    With no prior regen event there is nothing to compare against, so the first
    run always regenerates and ``delta`` is the whole current count.
    """
    if threshold < 0:
        raise ValueError(f"threshold must not be negative; got {threshold}.")

    current = known_terms(conn).count
    last = _last_regen_terms(conn)
    if last is None:
        return DriftResult(
            delta=current,
            threshold=threshold,
            should_regen=True,
            last_regen_terms=None,
        )
    delta = current - last
    return DriftResult(
        delta=delta,
        threshold=threshold,
        should_regen=abs(delta) > threshold,
        last_regen_terms=last,
    )


@dataclass(frozen=True, slots=True)
class MaybeResult:
    """The outcome of a drift-gated regen attempt: either way, a result."""

    regenerated: bool
    drift: DriftResult
    generated: GenResult | None = None
    event_id: str | None = None


def maybe_regen(
    conn: sqlite3.Connection,
    out_dir: Path | str | None = None,
    threshold: int = DRIFT_THRESHOLD,
) -> MaybeResult:
    """Regenerate the dictionary only if the known set has drifted far enough.

    The skip is logged as well as the regen. A decision not to act is still a
    decision, and without it the log would show a suspicious silence between
    regens instead of the reason for it.
    """
    drift = check_drift(conn, threshold)
    if drift.should_regen:
        generated = generate_dict(conn, out_dir)
        return MaybeResult(
            regenerated=True,
            drift=drift,
            generated=generated,
            event_id=generated.event_id,
        )

    event_id = append_event(
        conn,
        type=SKIP_EVENT_TYPE,
        session_id=SESSION_ID,
        payload={"delta": drift.delta, "threshold": drift.threshold},
    )
    _log.info(
        "Yomitan dictionary is current: known set moved by %d term(s), "
        "threshold is %d. Nothing regenerated.",
        drift.delta,
        drift.threshold,
    )
    return MaybeResult(regenerated=False, drift=drift, event_id=event_id)


# ---------------------------------------------------------------------------
# The manual half
# ---------------------------------------------------------------------------


def reimport_checklist() -> tuple[str, ...]:
    """The 60-second manual re-import, as numbered steps.

    Yomitan has no import API a local script may call, so this last mile is
    unavoidably manual. Writing the steps down next to the generator is the
    difference between a chore and a chore the learner has to remember. Step 3 is
    a removal on purpose: importing over an existing dictionary of the same title
    leaves both installed, and two Katagiri dictionaries disagreeing about what
    is known is worse than none.
    """
    return (
        "1. Open Yomitan's settings (extension icon -> the cog).",
        "2. Go to Dictionaries.",
        f'3. Remove the old "{DICT_TITLE}" entry if one is listed.',
        "4. Click Import, choose the zip printed above, and wait for it to "
        "finish.",
        f'5. Confirm the term count next to "{DICT_TITLE}" matches the count '
        "printed above.",
        "6. Hover a word you know on any Japanese page and check the "
        f'"{KNOWN_TAG}" tag appears.',
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_checklist() -> None:
    """Checklist to stderr: stdout is the JSON-RPC wire everywhere else."""
    print("\nRe-import in Yomitan (about 60 seconds):", file=sys.stderr)
    for line in reimport_checklist():
        print(f"  {line}", file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.yomitan_export",
        description=(
            "Generate a Yomitan dictionary that tags the words you already know."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="always write a new dictionary")
    check = sub.add_parser("check", help="report drift without writing anything")
    auto = sub.add_parser("auto", help="write only if the known set has drifted")
    for command in (gen, auto):
        command.add_argument(
            "--out", type=Path, default=None, help="output directory"
        )
    for command in (gen, check, auto):
        command.add_argument("--db", type=Path, default=None, help="database path")
    for command in (check, auto):
        command.add_argument(
            "--threshold",
            type=int,
            default=DRIFT_THRESHOLD,
            help=f"terms of drift before a regen (default {DRIFT_THRESHOLD})",
        )
    return parser


def _report_generated(result: GenResult) -> None:
    print(f"dictionary : {result.path}")
    print(f"terms      : {result.terms}")
    print(f"revision   : {result.revision}")
    for name, value in sorted(result.skipped.items()):
        if value:
            print(f"{name:<11}: {value}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.yomitan_export``."""
    args = _build_parser().parse_args(argv)
    logging.getLogger("katagiri").setLevel(logging.INFO)

    try:
        conn = db.open_db(args.db)
    except (sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "gen":
            result = generate_dict(conn, args.out)
            _report_generated(result)
            _print_checklist()
            return 0

        if args.command == "check":
            drift = check_drift(conn, args.threshold)
            last = "none yet" if drift.first_run else str(drift.last_regen_terms)
            print(f"last regen : {last}")
            print(f"delta      : {drift.delta:+d}")
            print(f"threshold  : {drift.threshold}")
            print(f"regen due  : {'yes' if drift.should_regen else 'no'}")
            return 0

        if args.command == "auto":
            outcome = maybe_regen(conn, args.out, args.threshold)
            if outcome.generated is not None:
                _report_generated(outcome.generated)
                _print_checklist()
                return 0
            print(
                f"up to date : known set moved by {outcome.drift.delta:+d} "
                f"term(s), threshold {outcome.drift.threshold}"
            )
            return 0
    except (YomitanExportError, sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    return 2  # pragma: no cover - argparse rejects unknown commands first


__all__ = [
    "DICT_FORMAT",
    "DICT_TITLE",
    "DRIFT_THRESHOLD",
    "INDEX_MEMBER",
    "KNOWN_TAG",
    "REGEN_EVENT_TYPE",
    "SESSION_ID",
    "SKIP_EVENT_TYPE",
    "TAG_BANK_MEMBER",
    "TERM_BANK_MEMBER",
    "DriftResult",
    "GenResult",
    "KnownTerms",
    "MaybeResult",
    "Term",
    "YomitanExportError",
    "build_index",
    "build_tag_bank",
    "check_drift",
    "dict_filename",
    "generate_dict",
    "known_terms",
    "main",
    "make_revision",
    "maybe_regen",
    "output_dir",
    "plausible_surface",
    "reimport_checklist",
    "write_dict_zip",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
