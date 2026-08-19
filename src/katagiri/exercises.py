"""D3: drill generation, sentence building, and the sealed-canary validator hook.

Why this module exists
---------------------
Phase D's teacher loop needs two generators — ``gen_exercise`` turns studied
items into drills, ``build_sentences`` turns them into practice sentences — and
both of them are the exact place where the project's one irreplaceable
measurement can be destroyed.

The canary set (A0b) is 200 sentences sealed before any studying happened. Its
whole value is that it is language the learner has *never met*: a rising score on
it cannot be explained away by familiarity. That value does not degrade
gracefully. One canary sentence used as a drill and the sentence is spent; a
handful and the band it belongs to stops meaning anything, permanently, with no
way to tell afterwards which numbers were still honest. Probes may read the set.
Drills never touch it — and "never" here is not a rule the generator is trusted
to remember, it is a check every generated string passes through.

So the shape of this module is: generators that are boring, and a validator that
is not. :class:`CanaryGuard` is that validator, and both entry points refuse to
run without one.

Fail closed, on purpose
-----------------------
:func:`load_canary_guard` raises :class:`CanarySetUnavailable` when the vault is
unconfigured, the sealed file is missing, or its ids no longer match their own
sentences — and the generators turn that into a refusal rather than generating
unscreened drills. Losing drills for a session because a path is wrong is a bad
afternoon. Generating unscreened drills is a bad year: contamination is silent,
and by the time the trend line looks odd the evidence of what leaked is gone.

How overlap is actually detected
--------------------------------
Four tiers, cheapest first, all on text normalised the same way on both sides
(kana folded to hiragana, punctuation and whitespace dropped) so a katakana
re-spelling or a stripped comma is not a way through:

1. **Id leak** — a canary id (``s-xxxxxx``) appearing anywhere in the candidate.
   Checked against the *raw* text, because normalisation eats the hyphen.
2. **Containment, forwards** — a canary sentence (or its kana reading) inside the
   candidate. This is the rule ``scripts/validate_canary.py`` enforces over the
   vault; here it runs before the text exists as a file.
3. **Containment, backwards** — the candidate inside a canary sentence, which is
   how contamination actually arrives: not a whole sentence pasted in, but a
   fragment of one used as a drill line.
4. **Cloze reconstruction and echo** — the candidate is split on blank markers
   and each piece is screened too (a cloze is a canary sentence with a hole in
   it, and matches neither direction whole), and finally the candidate and the
   canary set are compared by shared 8-character shingles.

Tier 4's shingles carry a deliberate restriction: a shingle only counts if it
holds at least two kanji. Without that, every sentence using 〜なければなりません
would collide with the canary sentence that happens to use it, and a validator
that cries wolf on ordinary grammar is a validator someone turns off. Shared
grammar is not contamination; shared *content* is. Hence two severities:
:data:`REFUSE` for tiers 1–3, which are evidence of the same sentence, and
:data:`FLAG` for tier 4, which is evidence of the same content. Drills drop
candidates at either severity — there is always another item — but only a refusal
is reported as a violation, so the distinction stays visible instead of being
averaged away.

A finding never carries the canary sentence. It names the id, the band, which
column matched and how many characters — enough to audit, and nothing that copies
sealed text into a log, an error string or an event payload. The sealed file is
the only place those sentences live, which is the point.

Untrusted text (FR-004, D-22)
-----------------------------
:func:`build_sentences` will build from media- or vault-derived material, and that
material may only arrive as an :class:`~katagiri.envelope.Envelope`. A bare
``str`` source is refused with :data:`UNENVELOPED_SOURCE` rather than quietly
trusted, and an envelope without a spent-once confirmation comes back as
:data:`ECHO_BACK_REQUIRED` *carrying the challenge to answer*, so the echo-back
ceremony is something the caller is handed rather than something it must know to
ask for. External text is screened against the canary set exactly like generated
text is.

What this module does not do
----------------------------
It does not write. No event row, no vault file, no ``item`` insert — reads only.
Registration of these functions as MCP tools, and the write paths that record
what was drilled, are T009's; keeping the generators side-effect-free is what
makes them safe to call twice while a session decides what to serve.

Failures are values with stable codes, in the shape ``obsidian_proxy`` uses:
``{"ok": False, "error": <code>, "note": <what happened>, ...}``. Type misuse by a
programmer still raises.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from katagiri.envelope import (
    Challenge,
    Confirmation,
    Envelope,
    EnvelopeError,
    EchoGate,
    default_gate,
    is_enveloped,
)
from katagiri.logging_setup import get_logger
from katagiri.normalizer import is_han_char, katakana_to_hiragana

logger = get_logger("exercises")

# ---------------------------------------------------------------------------
# Where the sealed set lives
# ---------------------------------------------------------------------------

#: Vault-relative location of the sealed canary set. Vault-relative rather than
#: repo-relative because the vault is what the learner actually owns; the copy
#: under ``docs/`` is the same file seen from the repository.
CANARY_VAULT_PATH: Final = "90-meta/canary/canary-set.md"

CANARY_ID_PREFIX: Final = "s-"
_CANARY_ID_RE: Final = re.compile(r"^s-[0-9a-f]{6}$")
_CANARY_ID_IN_TEXT_RE: Final = re.compile(r"s-[0-9a-f]{6}")

#: Shortest candidate that may be judged a *fragment* of a canary sentence.
#: Below this, "contained in a canary sentence" is true of ordinary words (です,
#: 今日は) and says nothing.
MIN_CONTAINMENT_CHARS: Final = 8

#: Shingle width for the echo tier, and how many kanji a shingle must hold to
#: count. Two kanji in eight characters is content; kana-only runs of that length
#: are grammar scaffolding shared by every sentence using the pattern.
SHINGLE_CHARS: Final = 8
MIN_SHINGLE_HAN: Final = 2

# ---------------------------------------------------------------------------
# Drill vocabulary
# ---------------------------------------------------------------------------

#: The five drill directions, spelled exactly as ``event.direction``'s CHECK
#: constraint spells them (docs/db-schema.md). A generator that invented a sixth
#: would produce drills whose results cannot be logged.
LISTEN_TO_MEANING: Final = "listen_to_meaning"
MEANING_TO_SPEECH: Final = "meaning_to_speech"
READ_TO_MEANING: Final = "read_to_meaning"
CLOZE_PRODUCTION: Final = "cloze_production"
SHADOW: Final = "shadow"

DIRECTIONS: Final = (
    LISTEN_TO_MEANING,
    MEANING_TO_SPEECH,
    READ_TO_MEANING,
    CLOZE_PRODUCTION,
    SHADOW,
)

#: Directions in which the learner produces Japanese. ``item.production_eligible
#: = 0`` marks receptive-only material — role language, archaic forms — and it
#: must never be drilled in these.
PRODUCTION_DIRECTIONS: Final = frozenset({MEANING_TO_SPEECH, CLOZE_PRODUCTION, SHADOW})

DRILLABLE_KINDS: Final = ("word", "sentence", "grammar")

#: How an answer can be checked. ``automatic`` means the schema knows the target
#: string; ``observation`` means it does not, and the performance is scored
#: through ``log_observations`` instead. ``item`` carries no gloss column, so
#: meaning-side answers are honestly unverifiable here rather than invented.
VERIFY_AUTOMATIC: Final = "automatic"
VERIFY_OBSERVATION: Final = "observation"

#: The blank a cloze leaves behind. Full-width so it survives in Japanese text
#: without looking like punctuation.
CLOZE_BLANK: Final = "＿＿＿"

DEFAULT_COUNT: Final = 5
MAX_COUNT: Final = 20
DEFAULT_SENTENCES: Final = 5
MAX_SENTENCES: Final = 20

#: Ceiling on external material accepted by :func:`build_sentences`. The envelope
#: allows far more; a sentence-mining pass over a whole episode transcript is a
#: different tool.
MAX_SOURCE_CHARS: Final = 20_000

# ---------------------------------------------------------------------------
# Stable codes
# ---------------------------------------------------------------------------

CANARY_VIOLATION: Final = "canary_violation"
CANARY_ECHO: Final = "canary_echo"
CANARY_SET_UNAVAILABLE: Final = "canary_set_unavailable"
CANARY_SET_TAMPERED: Final = "canary_set_tampered"
SEALED_ITEM: Final = "sealed_item"
NO_SUCH_ITEM: Final = "no_such_item"
NO_CANDIDATES: Final = "no_candidates"
BAD_COUNT: Final = "bad_count"
UNKNOWN_DIRECTION: Final = "unknown_direction"
UNENVELOPED_SOURCE: Final = "unenveloped_source"
ECHO_BACK_REQUIRED: Final = "echo_back_required"
SOURCE_TOO_LARGE: Final = "source_too_large"

#: Structural skip reasons — why an item yielded no drill. Not refusals: nothing
#: is wrong, the material just does not support what was asked for.
SKIP_NO_DIRECTION: Final = "no_direction_available"
SKIP_RECEPTIVE_ONLY: Final = "receptive_only"
SKIP_NO_TEMPLATE: Final = "no_template_for_pos"
SKIP_NO_SURFACE: Final = "no_surface_text"

# ---------------------------------------------------------------------------
# Notes returned to the caller
# ---------------------------------------------------------------------------

CANARY_UNCONFIGURED_NOTE: Final = (
    "The sealed canary set could not be loaded, so no drill was generated. "
    "Generating unscreened drills is refused: a canary sentence used once as a "
    "drill is spent permanently. Set 'vault_path' in config.toml (under "
    f"%LOCALAPPDATA%\\Katagiri) so that {CANARY_VAULT_PATH} is readable, then "
    "retry."
)
CANARY_TAMPERED_NOTE: Final = (
    "The sealed canary set failed its own integrity check — an id no longer "
    "matches sha1 of its sentence, or the file no longer declares itself sealed. "
    "No drill was generated. Restore the file from history; do not edit it. Run "
    "scripts/validate_canary.py for the offending rows."
)
CANARY_REFUSED_NOTE: Final = (
    "Refused: the requested material overlaps the sealed canary set, which "
    "probes read and drills never touch. The overlapping canary sentence is not "
    "quoted here — only its id, band and match length — so this refusal does not "
    "itself leak sealed text."
)
UNENVELOPED_SOURCE_NOTE: Final = (
    "External material must arrive in the untrusted-data envelope, not as a bare "
    "string. Wrap it with katagiri.envelope.wrap(text, source=...) so its "
    "provenance travels with it and the echo-back gate can see it. Nothing was "
    "built."
)
ECHO_BACK_REQUIRED_NOTE: Final = (
    "This material came from outside Katagiri, so it needs echo-back "
    "confirmation before it can be built into practice sentences. Answer the "
    "challenge in 'challenge' by echoing the content back verbatim, then call "
    "again with the confirmation. Nothing was built."
)
SEALED_ITEM_NOTE: Final = (
    "That item is sealed: it belongs to the held-out probe pool and may be "
    "served by a probe, never by a drill. Nothing was generated."
)
NO_CANDIDATES_NOTE: Final = (
    "No drillable material matched. This is an empty answer, not an error — "
    "'skipped' says which items could not yield the requested drill and "
    "'screened_out' says which were withheld by the canary guard."
)

# ---------------------------------------------------------------------------
# Failures that are raised rather than returned
# ---------------------------------------------------------------------------


class ExercisesError(RuntimeError):
    """Base for this module's raising failures, carrying a stable code."""

    code: str = CANARY_SET_UNAVAILABLE
    note: str = ""

    def __init__(self, note: str | None = None) -> None:
        if note is not None:
            self.note = note
        super().__init__(self.note or self.code)


class CanarySetUnavailable(ExercisesError):
    """The sealed set could not be loaded, so nothing may be generated.

    Raised by :func:`load_canary_guard`; both entry points turn it into a
    refusal value. Two codes, because the operator's next move differs: fix a
    path, or restore a file that was edited.
    """

    code = CANARY_SET_UNAVAILABLE
    note = CANARY_UNCONFIGURED_NOTE


class CanarySetTampered(CanarySetUnavailable):
    code = CANARY_SET_TAMPERED
    note = CANARY_TAMPERED_NOTE


# ---------------------------------------------------------------------------
# Normalisation shared by both sides of every comparison
# ---------------------------------------------------------------------------

#: Dropped before comparing. Punctuation and spacing are the cheapest way to
#: make a copied sentence look new, and none of it carries meaning for this
#: purpose. The long-vowel mark is deliberately *kept*: it distinguishes words.
_DROPPED_CHARS: Final = frozenset(
    " \t\r\n　。、，．,.！!？?；;：:「」『』（）()〔〕[]【】｛｝{}〈〉《》"
    "・…‥〜～―–—\"'“”‘’`|/\\*#＊＃"
)

#: Blank markers a cloze can use, including the one this module writes. A
#: candidate is split on these so each surviving piece is screened on its own.
_BLANK_RE: Final = re.compile(
    r"[_＿○〇◯]+|\[[^\]]{0,40}\]|【[^】]{0,40}】|（\s*）|\(\s*\)|\.{3,}|…+"
)

#: Sentence-ish boundaries for splitting external material into candidate lines.
_SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[。！？!?\n])")


def normalize_for_screening(text: str) -> str:
    """Fold ``text`` to the form both sides of a canary comparison are compared in.

    Katakana becomes hiragana (so a re-spelled sentence is still the same
    sentence) and punctuation and whitespace are dropped (so a stripped comma is
    not a bypass). Nothing else: no kanji-to-kana conversion, because that would
    collapse genuinely different sentences onto each other and a false refusal
    trains the operator to disable the check.
    """

    if not isinstance(text, str):  # pragma: no cover - programmer error
        raise TypeError("screened content must be str")
    folded = katakana_to_hiragana(text)
    return "".join(ch for ch in folded if ch not in _DROPPED_CHARS)


def canary_sentence_id(japanese: str) -> str:
    """``"s-" + sha1(japanese)[:6]`` — the id scheme the sealed file states.

    Recomputed rather than trusted: it is how a silently edited sentence is
    caught, both by ``scripts/validate_canary.py`` and by :meth:`CanaryGuard.
    from_markdown`.
    """

    return CANARY_ID_PREFIX + hashlib.sha1(japanese.encode("utf-8")).hexdigest()[:6]


def _content_shingles(normalized: str, width: int = SHINGLE_CHARS) -> Iterator[str]:
    """Every ``width``-character window holding at least :data:`MIN_SHINGLE_HAN` kanji.

    The kanji requirement is what separates "these two sentences are about the
    same thing" from "these two sentences use the same grammar point".
    """

    if len(normalized) < width:
        return
    for start in range(len(normalized) - width + 1):
        window = normalized[start : start + width]
        han = sum(1 for ch in window if is_han_char(ch))
        if han >= MIN_SHINGLE_HAN:
            yield window


# ---------------------------------------------------------------------------
# The sealed set, as data
# ---------------------------------------------------------------------------

REFUSE: Final = "refuse"
FLAG: Final = "flag"

MATCH_ID: Final = "id"
MATCH_JAPANESE: Final = "japanese"
MATCH_READING: Final = "reading"
MATCH_SHINGLE: Final = "shingle"


@dataclass(frozen=True, slots=True)
class CanarySentence:
    """One sealed row. Held in memory only for the duration of a screening."""

    id: str
    band: str
    japanese: str
    reading: str = ""

    def __repr__(self) -> str:
        """Redacted: a sealed sentence never reaches a log through a repr."""
        return (
            f"CanarySentence(id={self.id!r}, band={self.band!r}, "
            f"chars={len(self.japanese)}, japanese=<sealed, redacted>)"
        )


@dataclass(frozen=True, slots=True)
class CanaryFinding:
    """One reason a candidate must not be drilled.

    Carries no sealed text — id, band, which column matched, how many characters,
    and where in the candidate. That is auditable and safe to log; the sentence
    itself stays in the sealed file.
    """

    code: str
    severity: str
    canary_id: str
    band: str
    matched: str
    matched_chars: int
    where: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "canary_id": self.canary_id,
            "band": self.band,
            "matched": self.matched,
            "matched_chars": self.matched_chars,
            "where": self.where,
        }


def refuses(findings: Sequence[CanaryFinding]) -> bool:
    """True when any finding is a refusal rather than a flag."""
    return any(finding.severity == REFUSE for finding in findings)


def worst_code(findings: Sequence[CanaryFinding]) -> str:
    """The code a caller should be answered with: a refusal outranks a flag."""
    for finding in findings:
        if finding.severity == REFUSE:
            return finding.code
    return CANARY_ECHO if findings else ""


class CanaryGuard:
    """The validator hook: sealed sentences in, verdicts on candidate text out.

    Built once per process (see :func:`load_canary_guard`) and injected into the
    generators, which refuse to run without one. Tests build their own from
    invented sentences — the real sealed rows are never copied into a test file,
    and no method here returns one.
    """

    __slots__ = ("_by_id", "_needles", "_shingles", "_bands", "min_containment", "shingle_chars")

    def __init__(
        self,
        sentences: Iterable[CanarySentence],
        *,
        min_containment: int = MIN_CONTAINMENT_CHARS,
        shingle_chars: int = SHINGLE_CHARS,
    ) -> None:
        if int(min_containment) <= 0:
            raise ValueError("min_containment must be positive")
        if int(shingle_chars) <= 0:
            raise ValueError("shingle_chars must be positive")
        self.min_containment = int(min_containment)
        self.shingle_chars = int(shingle_chars)

        self._by_id: dict[str, CanarySentence] = {}
        self._needles: list[tuple[str, CanarySentence, str]] = []
        self._shingles: dict[str, CanarySentence] = {}
        bands: dict[str, int] = {}

        for sentence in sentences:
            self._by_id[sentence.id] = sentence
            bands[sentence.band] = bands.get(sentence.band, 0) + 1
            japanese = normalize_for_screening(sentence.japanese)
            if japanese:
                self._needles.append((japanese, sentence, MATCH_JAPANESE))
                for shingle in _content_shingles(japanese, self.shingle_chars):
                    self._shingles.setdefault(shingle, sentence)
            reading = normalize_for_screening(sentence.reading or "")
            if reading and reading != japanese:
                self._needles.append((reading, sentence, MATCH_READING))
        self._bands: dict[str, int] = dict(sorted(bands.items()))

    # -- introspection (counts only, never content) ------------------------

    def __len__(self) -> int:
        return len(self._by_id)

    def bands(self) -> dict[str, int]:
        return dict(self._bands)

    def ids(self) -> frozenset[str]:
        return frozenset(self._by_id)

    def is_canary_id(self, item_id: str | None) -> bool:
        return bool(item_id) and item_id in self._by_id

    def __repr__(self) -> str:
        return f"CanaryGuard(sentences={len(self)}, bands={self._bands})"

    # -- construction ------------------------------------------------------

    @classmethod
    def from_rows(cls, rows: Iterable[Sequence[str]], **kwargs: Any) -> CanaryGuard:
        """Build from ``(id, band, japanese[, reading])`` tuples.

        The id is *recomputed*, not trusted: a row whose id does not match its
        own sentence is the tampering case, and it raises rather than screening
        against a set whose identities have drifted.
        """

        sentences: list[CanarySentence] = []
        for row in rows:
            if len(row) < 3:
                raise CanarySetTampered(
                    "A canary row has fewer than three columns; the sealed file's "
                    "shape is (id, band, japanese, reading, english)."
                )
            cid, band, japanese = row[0], row[1], row[2]
            reading = row[3] if len(row) > 3 else ""
            if not _CANARY_ID_RE.match(cid):
                raise CanarySetTampered(
                    f"Canary row id {cid!r} is not of the form s-xxxxxx."
                )
            if canary_sentence_id(japanese) != cid:
                raise CanarySetTampered(
                    f"Canary row {cid} does not hash to its own sentence "
                    f"(recomputed {canary_sentence_id(japanese)}). The sealed set "
                    "was edited; the sentence is not quoted here."
                )
            sentences.append(
                CanarySentence(id=cid, band=band, japanese=japanese, reading=reading)
            )
        if not sentences:
            raise CanarySetUnavailable(
                "The canary set parsed to zero sentences, so nothing could be "
                "screened. Refusing to generate drills against an empty guard."
            )
        return cls(sentences, **kwargs)

    @classmethod
    def from_markdown(cls, text: str, **kwargs: Any) -> CanaryGuard:
        """Parse the sealed file's frontmatter and band tables.

        ``sealed: true`` is required. A file that no longer declares itself
        sealed is either not the canary set or is a canary set someone decided to
        unseal, and neither is something to screen against silently.
        """

        meta, body = _split_frontmatter(text)
        if meta.get("sealed") != "true":
            raise CanarySetTampered(
                "The canary set does not declare 'sealed: true' in its "
                f"frontmatter (found {meta.get('sealed')!r}). Nothing was loaded."
            )
        return cls.from_rows(_parse_canary_rows(body), **kwargs)

    @classmethod
    def from_file(cls, path: Path | str, **kwargs: Any) -> CanaryGuard:
        resolved = Path(path)
        if not resolved.is_file():
            raise CanarySetUnavailable(
                f"{CANARY_UNCONFIGURED_NOTE} (looked for {resolved})"
            )
        return cls.from_markdown(resolved.read_text(encoding="utf-8"), **kwargs)

    # -- the actual check --------------------------------------------------

    def screen(self, text: str, *, where: str = "") -> tuple[CanaryFinding, ...]:
        """Every reason ``text`` must not be drilled, strongest tier first.

        Empty result means clean. Tiers 1–3 (id leak, containment either way)
        return without computing tier 4: once the same sentence is established,
        a shared shingle adds nothing but noise.
        """

        if not text:
            return ()
        if not isinstance(text, str):  # pragma: no cover - programmer error
            raise TypeError("screened content must be str")

        findings: list[CanaryFinding] = []

        # Tier 1: an id, on the raw text — normalisation would eat the hyphen.
        for match in _CANARY_ID_IN_TEXT_RE.finditer(text):
            sentence = self._by_id.get(match.group(0))
            if sentence is not None:
                findings.append(
                    CanaryFinding(
                        code=CANARY_VIOLATION,
                        severity=REFUSE,
                        canary_id=sentence.id,
                        band=sentence.band,
                        matched=MATCH_ID,
                        matched_chars=len(match.group(0)),
                        where=where,
                    )
                )

        # Tiers 2 and 3, over the whole candidate and each cloze segment.
        segments = _screening_segments(text)
        seen: set[tuple[str, str]] = set()
        for needle, sentence, column in self._needles:
            key = (sentence.id, column)
            if key in seen:
                continue
            for segment in segments:
                if not segment:
                    continue
                if needle in segment:
                    overlap = len(needle)
                elif len(segment) >= self.min_containment and segment in needle:
                    overlap = len(segment)
                else:
                    continue
                seen.add(key)
                findings.append(
                    CanaryFinding(
                        code=CANARY_VIOLATION,
                        severity=REFUSE,
                        canary_id=sentence.id,
                        band=sentence.band,
                        matched=column,
                        matched_chars=overlap,
                        where=where,
                    )
                )
                break

        if findings:
            _log_findings(findings)
            return tuple(findings)

        # Tier 4: shared content. A flag, not a refusal.
        whole = normalize_for_screening(text)
        echoed: dict[str, int] = {}
        for shingle in _content_shingles(whole, self.shingle_chars):
            sentence = self._shingles.get(shingle)
            if sentence is None:
                continue
            echoed[sentence.id] = max(echoed.get(sentence.id, 0), len(shingle))
        for canary_id, overlap in sorted(echoed.items()):
            sentence = self._by_id[canary_id]
            findings.append(
                CanaryFinding(
                    code=CANARY_ECHO,
                    severity=FLAG,
                    canary_id=canary_id,
                    band=sentence.band,
                    matched=MATCH_SHINGLE,
                    matched_chars=overlap,
                    where=where,
                )
            )
        if findings:
            _log_findings(findings)
        return tuple(findings)

    def screen_all(self, parts: Iterable[tuple[str, str]]) -> tuple[CanaryFinding, ...]:
        """Screen several ``(where, text)`` pieces of one candidate together."""
        findings: list[CanaryFinding] = []
        for where, text in parts:
            if text:
                findings.extend(self.screen(text, where=where))
        return tuple(findings)


def _log_findings(findings: Sequence[CanaryFinding]) -> None:
    for finding in findings:
        logger.warning(
            "canary %s: candidate overlaps %s (band %s) on %s, %d chars, at %s",
            "refusal" if finding.severity == REFUSE else "flag",
            finding.canary_id,
            finding.band,
            finding.matched,
            finding.matched_chars,
            finding.where or "candidate",
        )


def _screening_segments(text: str) -> tuple[str, ...]:
    """The whole candidate plus each cloze segment, all normalised.

    A cloze is a canary sentence with a hole punched in it: it contains no canary
    sentence and is contained in none, which is exactly how a naive substring
    check misses it. Screening the pieces closes that.
    """

    pieces = [text, *(_BLANK_RE.split(text))]
    out: list[str] = []
    for piece in pieces:
        normalized = normalize_for_screening(piece)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[index + 1 :])
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    return {}, text


def _parse_canary_rows(body: str) -> list[tuple[str, str, str, str]]:
    """``(id, band, japanese, reading)`` for every band-table row in ``body``."""
    rows: list[tuple[str, str, str, str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        first = cells[0]
        if first == "id" or not first or set(first) <= set("-: "):
            continue  # header or separator
        if not _CANARY_ID_RE.match(first):
            raise CanarySetTampered(
                f"A canary table row has a malformed id ({first!r}). Nothing was "
                "loaded."
            )
        reading = cells[3] if len(cells) > 3 else ""
        rows.append((first, cells[1], cells[2], reading))
    return rows


# ---------------------------------------------------------------------------
# Loading the process-wide guard
# ---------------------------------------------------------------------------

_cached_guard: tuple[Path, int, int, CanaryGuard] | None = None


def canary_set_path(vault_path: Path | str | None = None) -> Path:
    """Absolute path of the sealed set, from ``vault_path`` or the configuration."""
    if vault_path is None:
        from katagiri.config import ConfigError, get_config

        try:
            root = get_config().require_vault_path()
        except ConfigError as exc:
            raise CanarySetUnavailable() from exc
    else:
        root = Path(vault_path)
    return root.joinpath(*CANARY_VAULT_PATH.split("/"))


def load_canary_guard(
    *, vault_path: Path | str | None = None, refresh: bool = False
) -> CanaryGuard:
    """The guard the generators screen against, cached on the file's own stats.

    Cached by ``(path, size, mtime_ns)`` rather than forever: the sealed file
    must not change, and if it does the next call notices instead of screening
    against a stale copy for the life of the process.
    """

    global _cached_guard
    path = canary_set_path(vault_path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise CanarySetUnavailable(
            f"{CANARY_UNCONFIGURED_NOTE} (looked for {path})"
        ) from exc

    if not refresh and _cached_guard is not None:
        cached_path, size, mtime, guard = _cached_guard
        if cached_path == path and size == stat.st_size and mtime == stat.st_mtime_ns:
            return guard

    guard = CanaryGuard.from_file(path)
    logger.info(
        "canary guard loaded: %d sealed sentences, bands %s", len(guard), guard.bands()
    )
    _cached_guard = (path, stat.st_size, stat.st_mtime_ns, guard)
    return guard


def reset_canary_cache() -> None:
    """Forget the cached guard. Tests, and an operator who fixed the vault path."""
    global _cached_guard
    _cached_guard = None


# ---------------------------------------------------------------------------
# Answer shapes
# ---------------------------------------------------------------------------


def _failure(code: str, note: str, **extra: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {"ok": False, "error": code, "note": note}
    answer.update(extra)
    return answer


def _success(note: str = "", **extra: Any) -> dict[str, Any]:
    answer: dict[str, Any] = {"ok": True, "error": None, "note": note}
    answer.update(extra)
    return answer


def _resolve_guard(guard: CanaryGuard | None) -> tuple[CanaryGuard | None, dict[str, Any] | None]:
    """The injected guard, or the loaded one, or a refusal. Never ``None, None``."""
    if guard is not None:
        if not isinstance(guard, CanaryGuard):
            raise TypeError("guard must be a CanaryGuard")
        return guard, None
    try:
        return load_canary_guard(), None
    except CanarySetUnavailable as exc:
        logger.warning("generation refused: %s", exc.code)
        return None, _failure(exc.code, exc.note, exercises=[], sentences=[])


# ---------------------------------------------------------------------------
# Reading items
# ---------------------------------------------------------------------------

_ITEM_COLUMNS: Final = (
    "i.id AS id, i.kind AS kind, i.home_topic AS home_topic, i.kanji AS kanji, "
    "i.reading AS reading, i.pos AS pos, i.jlpt AS jlpt, i.level AS level, "
    "i.register AS register, i.understanding AS understanding, "
    "i.production_eligible AS production_eligible, i.sealed AS sealed"
)

_LAST_DRILLED: Final = (
    "(SELECT MAX(e.ts_server) FROM event e "
    " WHERE e.item_id = i.id AND e.direction IS NOT NULL) AS last_drilled"
)

#: Never-drilled first, then longest-ago, then by id so two runs agree. No RNG:
#: a generator whose output cannot be reproduced cannot be debugged from a log.
_ORDER_BY: Final = "ORDER BY (last_drilled IS NULL) DESC, last_drilled ASC, i.id ASC"


def _sentence_source(row: sqlite3.Row) -> str:
    """The Japanese of a sentence item.

    ``sentence_text.jp`` is the derived home of sentence text; ``item.kanji``
    holds it before that table is populated. Preferring the derived row and
    falling back keeps drills working on a database whose FTS side has not been
    built yet.
    """

    keys = row.keys()
    if "jp" in keys and row["jp"]:
        return str(row["jp"])
    return str(row["kanji"] or "")


def _fetch_items(
    conn: sqlite3.Connection,
    *,
    item_ids: Sequence[str] | None,
    topic: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    where = ["i.kind IN (%s)" % ", ".join("?" * len(DRILLABLE_KINDS))]
    params: list[Any] = list(DRILLABLE_KINDS)
    if item_ids:
        where.append("i.id IN (%s)" % ", ".join("?" * len(item_ids)))
        params.extend(item_ids)
    else:
        # Sealed rows are excluded here rather than by a constraint: "sealed"
        # restricts what may be *served*, not what may exist. An explicitly
        # requested sealed id is answered with SEALED_ITEM instead of silently
        # vanishing, which is why the filter is only on the pool query.
        where.append("i.sealed = 0")
    if topic:
        where.append("i.home_topic = ?")
        params.append(topic)
    sql = (
        f"SELECT {_ITEM_COLUMNS}, st.jp AS jp, {_LAST_DRILLED} "
        "FROM item i LEFT JOIN sentence_text st ON st.item_id = i.id "
        f"WHERE {' AND '.join(where)} {_ORDER_BY} LIMIT ?"
    )
    params.append(int(limit))
    return list(conn.execute(sql, params))


_LIKE_ESCAPE: Final = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _sentence_carrier(
    conn: sqlite3.Connection, surface: str
) -> tuple[str, str] | None:
    """A production-eligible, unsealed sentence containing ``surface``.

    This is the only way a word item can yield a cloze: the blank has to be cut
    out of a sentence that already exists, because inventing the carrier would
    mean inventing Japanese.
    """

    if not surface:
        return None
    pattern = f"%{surface.translate(_LIKE_ESCAPE)}%"
    row = conn.execute(
        "SELECT i.id AS id, COALESCE(st.jp, i.kanji) AS jp "
        "FROM item i LEFT JOIN sentence_text st ON st.item_id = i.id "
        "WHERE i.kind = 'sentence' AND i.sealed = 0 "
        "  AND i.production_eligible = 1 "
        "  AND COALESCE(st.jp, i.kanji) LIKE ? ESCAPE '\\' "
        "ORDER BY length(COALESCE(st.jp, i.kanji)) ASC, i.id ASC LIMIT 1",
        (pattern,),
    ).fetchone()
    if row is None or not row["jp"]:
        return None
    return str(row["id"]), str(row["jp"])


# ---------------------------------------------------------------------------
# Building one drill
# ---------------------------------------------------------------------------

_PROMPTS: Final = {
    READ_TO_MEANING: "Read this aloud and say what it means: {material}",
    LISTEN_TO_MEANING: "Listen (spoken, text hidden) and say what it means: {material}",
    CLOZE_PRODUCTION: "Fill the blank: {material}",
    SHADOW: "Shadow this line, matching timing and pitch: {material}",
    MEANING_TO_SPEECH: "Produce a Japanese sentence that uses: {material}",
}


def _offered_directions(row: sqlite3.Row) -> tuple[str, ...]:
    """Which drills this item's own columns can actually support."""
    kind = str(row["kind"])
    production = bool(row["production_eligible"])
    if kind == "word":
        offered = [READ_TO_MEANING]
        if production:
            offered.append(CLOZE_PRODUCTION)
        return tuple(offered)
    if kind == "sentence":
        offered = [READ_TO_MEANING, LISTEN_TO_MEANING]
        if production:
            offered.append(SHADOW)
        return tuple(offered)
    if kind == "grammar":
        return (MEANING_TO_SPEECH,) if production else ()
    return ()


def _build_drill(
    conn: sqlite3.Connection, row: sqlite3.Row, direction: str
) -> dict[str, Any] | None:
    """One drill dict, or ``None`` when this item cannot support ``direction``."""
    kind = str(row["kind"])
    kanji = str(row["kanji"] or "")
    reading = str(row["reading"] or "")
    surface = kanji or reading

    if direction in PRODUCTION_DIRECTIONS and not row["production_eligible"]:
        return None

    if kind == "word":
        if not surface:
            return None
        if direction == READ_TO_MEANING:
            return _drill(
                row,
                direction,
                material=surface,
                expected=None,
                verify=VERIFY_OBSERVATION,
                cue="kanji" if kanji else "reading",
                reading=reading or None,
            )
        if direction == CLOZE_PRODUCTION:
            carrier = _sentence_carrier(conn, surface)
            if carrier is None:
                return None
            carrier_id, carrier_text = carrier
            return _drill(
                row,
                direction,
                material=carrier_text.replace(surface, CLOZE_BLANK, 1),
                expected=surface,
                verify=VERIFY_AUTOMATIC,
                cue="cloze",
                reading=reading or None,
                carrier_item_id=carrier_id,
            )
        return None

    if kind == "sentence":
        text = _sentence_source(row)
        if not text:
            return None
        if direction == READ_TO_MEANING:
            return _drill(
                row,
                direction,
                material=text,
                expected=None,
                verify=VERIFY_OBSERVATION,
                cue="sentence",
                reading=reading or None,
            )
        if direction == LISTEN_TO_MEANING:
            # Text hidden by protocol: the reading is what gets spoken, and the
            # written form travels only as `hidden_material` so a caller can
            # check the answer without showing it first.
            spoken = reading or text
            drill = _drill(
                row,
                direction,
                material=spoken,
                expected=None,
                verify=VERIFY_OBSERVATION,
                cue="reading" if reading else "sentence",
            )
            drill["hidden_material"] = text
            return drill
        if direction == SHADOW:
            return _drill(
                row,
                direction,
                material=text,
                expected=text,
                verify=VERIFY_OBSERVATION,
                cue="sentence",
                reading=reading or None,
            )
        return None

    if kind == "grammar" and direction == MEANING_TO_SPEECH:
        material = surface or str(row["id"])[2:].replace("-", " ")
        return _drill(
            row,
            direction,
            material=material,
            expected=None,
            verify=VERIFY_OBSERVATION,
            cue="grammar",
            understanding=row["understanding"],
        )
    return None


def _drill(
    row: sqlite3.Row,
    direction: str,
    *,
    material: str,
    expected: str | None,
    verify: str,
    cue: str,
    **extra: Any,
) -> dict[str, Any]:
    drill: dict[str, Any] = {
        "item_id": str(row["id"]),
        "kind": str(row["kind"]),
        "direction": direction,
        "prompt": _PROMPTS[direction].format(material=material),
        "material": material,
        "expected": expected,
        "verify": verify,
        "cue": cue,
        "jlpt": row["jlpt"],
        "level": row["level"],
        "home_topic": row["home_topic"],
        "production_eligible": bool(row["production_eligible"]),
        "canary_screened": True,
    }
    drill.update(extra)
    return drill


def _drill_texts(drill: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Every string of a drill that a learner could see, paired with its field.

    The prompt carries English carrier text as well as the material, and it is
    screened too: it is the string that would end up in a note.
    """

    parts = [
        ("material", str(drill.get("material") or "")),
        ("expected", str(drill.get("expected") or "")),
        ("prompt", str(drill.get("prompt") or "")),
    ]
    for key in ("hidden_material", "reading"):
        value = drill.get(key)
        if value:
            parts.append((key, str(value)))
    return tuple(parts)


# ---------------------------------------------------------------------------
# gen_exercise
# ---------------------------------------------------------------------------


def gen_exercise(
    conn: sqlite3.Connection,
    *,
    item_ids: Sequence[str] | None = None,
    topic: str | None = None,
    direction: str | None = None,
    count: int = DEFAULT_COUNT,
    guard: CanaryGuard | None = None,
) -> dict[str, Any]:
    """Generate up to ``count`` drills, every string screened against the canary set.

    Reads only. Selection is deterministic — never-drilled items first, then
    longest-ago, then by id — so the same database answers the same way twice and
    a session's drills can be reconstructed from a log.

    ``item_ids`` names material explicitly; without it the pool is every unsealed
    drillable item, optionally narrowed to ``topic``. An explicitly requested
    item that the guard refuses fails the whole call (the caller asked for that
    one, and silently substituting another would hide the contamination); a pool
    candidate is dropped into ``screened_out`` and the next item is tried.

    Failure codes: :data:`BAD_COUNT`, :data:`UNKNOWN_DIRECTION`,
    :data:`NO_SUCH_ITEM`, :data:`SEALED_ITEM`, :data:`CANARY_VIOLATION`,
    :data:`CANARY_ECHO`, :data:`CANARY_SET_UNAVAILABLE`,
    :data:`CANARY_SET_TAMPERED`, :data:`NO_CANDIDATES`.
    """

    if not isinstance(count, int) or isinstance(count, bool) or count <= 0 or count > MAX_COUNT:
        return _failure(
            BAD_COUNT,
            f"count must be an integer between 1 and {MAX_COUNT}; nothing was "
            "generated.",
            exercises=[],
        )
    if direction is not None and direction not in DIRECTIONS:
        return _failure(
            UNKNOWN_DIRECTION,
            f"Unknown drill direction {direction!r}. The loggable directions are "
            f"{', '.join(DIRECTIONS)} — a drill in any other direction could not "
            "be recorded.",
            exercises=[],
        )

    resolved_guard, refusal = _resolve_guard(guard)
    if resolved_guard is None:
        assert refusal is not None
        return refusal

    explicit = list(dict.fromkeys(item_ids or ()))
    redirects: list[dict[str, str]] = []
    if explicit:
        explicit, redirects = _canonicalise(conn, explicit)

    rows = _fetch_items(
        conn,
        item_ids=explicit or None,
        topic=topic,
        # Room to screen and skip past unusable items without a second query.
        limit=max(count * 8, 40) if not explicit else len(explicit),
    )

    if explicit:
        found = {str(row["id"]) for row in rows}
        missing = [iid for iid in explicit if iid not in found]
        if missing:
            return _failure(
                NO_SUCH_ITEM,
                "No item row for: " + ", ".join(missing) + ". Nothing was generated.",
                exercises=[],
                missing=missing,
            )
        sealed = [str(row["id"]) for row in rows if row["sealed"]]
        if sealed:
            logger.warning("gen_exercise refused sealed items: %s", ", ".join(sealed))
            return _failure(
                SEALED_ITEM, SEALED_ITEM_NOTE, exercises=[], sealed_items=sealed
            )

    exercises: list[dict[str, Any]] = []
    screened_out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    wanted = [direction] if direction else None

    for row in rows:
        if len(exercises) >= count:
            break
        offered = _offered_directions(row)
        candidates = [d for d in (wanted or offered) if d in offered]
        if not candidates:
            skipped.append(
                {
                    "item_id": str(row["id"]),
                    "reason": SKIP_RECEPTIVE_ONLY
                    if not row["production_eligible"]
                    and (direction in PRODUCTION_DIRECTIONS if direction else False)
                    else SKIP_NO_DIRECTION,
                    "direction": direction,
                }
            )
            continue

        item_findings: list[CanaryFinding] = []
        built = False
        for candidate in candidates:
            drill = _build_drill(conn, row, candidate)
            if drill is None:
                continue
            findings = resolved_guard.screen_all(_drill_texts(drill))
            if findings:
                item_findings.extend(findings)
                continue
            exercises.append(drill)
            built = True
            break

        if built:
            continue
        if item_findings:
            if explicit:
                logger.warning(
                    "gen_exercise refused requested item %s: %s",
                    row["id"],
                    worst_code(item_findings),
                )
                return _failure(
                    worst_code(item_findings),
                    CANARY_REFUSED_NOTE,
                    exercises=[],
                    item_id=str(row["id"]),
                    findings=[f.as_dict() for f in item_findings],
                )
            screened_out.append(
                {
                    "item_id": str(row["id"]),
                    "code": worst_code(item_findings),
                    "findings": [f.as_dict() for f in item_findings],
                }
            )
            continue
        skipped.append({"item_id": str(row["id"]), "reason": SKIP_NO_SURFACE})

    payload: dict[str, Any] = {
        "exercises": exercises,
        "requested": count,
        "returned": len(exercises),
        "direction": direction,
        "topic": topic,
        "screened_out": screened_out,
        "skipped": skipped,
        "redirects": redirects,
        "canary_sentences_screened_against": len(resolved_guard),
        "canary_bands": resolved_guard.bands(),
    }
    if not exercises:
        return _failure(NO_CANDIDATES, NO_CANDIDATES_NOTE, **payload)
    logger.info(
        "gen_exercise: %d drill(s), %d screened out, %d skipped",
        len(exercises),
        len(screened_out),
        len(skipped),
    )
    return _success(
        f"{len(exercises)} drill(s), each screened against the sealed canary set.",
        **payload,
    )


def _canonicalise(
    conn: sqlite3.Connection, item_ids: Sequence[str]
) -> tuple[list[str], list[dict[str, str]]]:
    """Resolve renamed ids, reporting each redirect rather than hiding it."""
    from katagiri.db import resolve_alias

    canonical: list[str] = []
    redirects: list[dict[str, str]] = []
    for item_id in item_ids:
        resolved = resolve_alias(conn, item_id)
        target = str(resolved.get("canonical_id") or item_id)
        if resolved.get("redirected"):
            redirects.append({"from": item_id, "to": target})
        if target not in canonical:
            canonical.append(target)
    return canonical, redirects


# ---------------------------------------------------------------------------
# build_sentences
# ---------------------------------------------------------------------------

#: Frame sentences, by coarse part of speech. Grammatically safe with a plain
#: dictionary-form or stem surface and nothing else; there is no template for an
#: unknown part of speech, because a generator that guesses produces Japanese the
#: learner then has to unlearn.
TEMPLATES: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "noun": (
        ("noun-kore-wa", "これは{surface}です。"),
        ("noun-arimasu-ka", "{surface}がありますか。"),
        ("noun-suki", "私は{surface}が好きです。"),
    ),
    "verb": (
        ("verb-mainichi", "私は毎日{surface}。"),
        ("verb-koto-ga-dekimasu", "{surface}ことができます。"),
    ),
    "adj-i": (
        ("adj-i-totemo", "今日はとても{surface}。"),
        ("adj-i-noun", "{surface}本を読みました。"),
    ),
    "adj-na": (
        ("adj-na-heya", "この部屋は{surface}です。"),
        ("adj-na-noun", "{surface}な人に会いました。"),
    ),
    "adverb": (
        ("adverb-hanashite", "{surface}話してください。"),
        ("adverb-tabemasu", "{surface}食べます。"),
    ),
}

#: Longest hint first so 形容動詞 is not read as 形容詞 and 代名詞 not as a verb.
_POS_HINTS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("adj-na", ("形状詞", "形容動詞", "na-adj", "na adjective", "adjectival noun")),
    ("adj-i", ("形容詞", "i-adj", "i adjective", "adjective", "adj")),
    ("adverb", ("副詞", "adverb", "adv")),
    ("verb", ("動詞", "verb", "godan", "ichidan")),
    ("noun", ("代名詞", "名詞", "noun", "pronoun")),
)

SOURCE_TEMPLATE: Final = "template"
SOURCE_EXTERNAL: Final = "external"


def coarse_pos(pos: str | None) -> str | None:
    """Map ``item.pos`` onto a template family, or ``None`` when unrecognised.

    Both UniDic tags and English labels are accepted because both appear in
    practice — JMdict-derived rows say ``noun``, tokenizer-derived rows say
    ``名詞``.
    """

    if not pos:
        return None
    lowered = pos.lower()
    for family, hints in _POS_HINTS:
        for hint in hints:
            if hint.lower() in lowered:
                return family
    return None


def _external_lines(text: str) -> list[str]:
    """Split unwrapped external material into candidate sentences."""
    lines: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(text):
        stripped = piece.strip()
        if len(stripped) >= 4 and stripped not in lines:
            lines.append(stripped)
    return lines


def _source_text(
    source: str | Envelope | None,
    confirmation: Confirmation | None,
    gate: EchoGate | None,
) -> tuple[str, dict[str, Any] | None, Challenge | None, dict[str, Any] | None]:
    """Unwrap external material, or explain what is missing.

    Returns ``(text, provenance, challenge, refusal)``. Exactly one of the last
    two is set when the material cannot be used: a challenge to answer, or a
    refusal that no ceremony fixes.
    """

    if source is None:
        return "", None, None, None

    if isinstance(source, str):
        if not source.strip():
            return "", None, None, None
        return (
            "",
            None,
            None,
            _failure(UNENVELOPED_SOURCE, UNENVELOPED_SOURCE_NOTE, sentences=[]),
        )

    if not is_enveloped(source):
        raise TypeError("source must be an Envelope, a str, or None")

    if len(source.text) > MAX_SOURCE_CHARS:
        return (
            "",
            None,
            None,
            _failure(
                SOURCE_TOO_LARGE,
                f"External material exceeds {MAX_SOURCE_CHARS} characters. Mine "
                "the passage being studied, not the whole transcript. Nothing "
                "was built.",
                sentences=[],
            ),
        )

    resolved_gate = gate if gate is not None else default_gate()
    if confirmation is None:
        try:
            challenge = resolved_gate.challenge(source)
        except EnvelopeError as exc:
            return "", None, None, _failure(exc.code, exc.note, sentences=[])
        return "", None, challenge, None

    try:
        text = resolved_gate.unwrap_for_write(source, confirmation)
    except EnvelopeError as exc:
        logger.warning("build_sentences refused external material: %s", exc.code)
        return "", None, None, _failure(exc.code, exc.note, sentences=[])
    return text, source.for_event(), None, None


def build_sentences(
    conn: sqlite3.Connection,
    *,
    item_ids: Sequence[str] | None = None,
    topic: str | None = None,
    source: str | Envelope | None = None,
    confirmation: Confirmation | None = None,
    gate: EchoGate | None = None,
    max_sentences: int = DEFAULT_SENTENCES,
    guard: CanaryGuard | None = None,
) -> dict[str, Any]:
    """Build practice sentences for target items, screened against the canary set.

    Two supplies, both screened the same way. **Templates**: frame sentences from
    :data:`TEMPLATES`, chosen by the item's part of speech; a part of speech with
    no template yields nothing rather than invented Japanese. **External
    material**: lines from ``source``, which must be an
    :class:`~katagiri.envelope.Envelope` — a bare string is refused with
    :data:`UNENVELOPED_SOURCE`, and an envelope without a confirmation comes back
    as :data:`ECHO_BACK_REQUIRED` carrying the challenge to answer.

    Receptive-only items (``production_eligible = 0``) are skipped: a practice
    sentence is production material.

    Reads only; recording what was built is the caller's, through the event log.
    """

    if (
        not isinstance(max_sentences, int)
        or isinstance(max_sentences, bool)
        or max_sentences <= 0
        or max_sentences > MAX_SENTENCES
    ):
        return _failure(
            BAD_COUNT,
            f"max_sentences must be an integer between 1 and {MAX_SENTENCES}; "
            "nothing was built.",
            sentences=[],
        )

    resolved_guard, refusal = _resolve_guard(guard)
    if resolved_guard is None:
        assert refusal is not None
        return refusal

    text, provenance, challenge, source_refusal = _source_text(
        source, confirmation, gate
    )
    if source_refusal is not None:
        return source_refusal
    if challenge is not None:
        logger.info(
            "build_sentences awaiting echo-back: challenge=%s envelope=%s",
            challenge.challenge_id,
            challenge.envelope_id,
        )
        return _failure(
            ECHO_BACK_REQUIRED,
            ECHO_BACK_REQUIRED_NOTE,
            sentences=[],
            challenge={
                "challenge_id": challenge.challenge_id,
                "envelope_id": challenge.envelope_id,
                "prompt": challenge.prompt,
                "excerpt": challenge.excerpt,
                "chars": challenge.chars,
                "expires_ms": challenge.expires_ms,
                "provenance": challenge.provenance.as_dict(),
            },
        )

    explicit = list(dict.fromkeys(item_ids or ()))
    redirects: list[dict[str, str]] = []
    if explicit:
        explicit, redirects = _canonicalise(conn, explicit)

    rows = _fetch_items(
        conn,
        item_ids=explicit or None,
        topic=topic,
        limit=max(max_sentences * 4, 20) if not explicit else len(explicit),
    )
    if explicit:
        found = {str(row["id"]) for row in rows}
        missing = [iid for iid in explicit if iid not in found]
        if missing:
            return _failure(
                NO_SUCH_ITEM,
                "No item row for: " + ", ".join(missing) + ". Nothing was built.",
                sentences=[],
                missing=missing,
            )
        sealed = [str(row["id"]) for row in rows if row["sealed"]]
        if sealed:
            logger.warning("build_sentences refused sealed items: %s", ", ".join(sealed))
            return _failure(
                SEALED_ITEM, SEALED_ITEM_NOTE, sentences=[], sealed_items=sealed
            )

    sentences: list[dict[str, Any]] = []
    screened_out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    external_lines = _external_lines(text) if text else []
    seen_text: set[str] = set()

    for row in rows:
        if len(sentences) >= max_sentences:
            break
        item_id = str(row["id"])
        surface = str(row["kanji"] or row["reading"] or "")
        if not surface:
            skipped.append({"item_id": item_id, "reason": SKIP_NO_SURFACE})
            continue
        if not row["production_eligible"]:
            skipped.append({"item_id": item_id, "reason": SKIP_RECEPTIVE_ONLY})
            continue

        produced_here = 0

        # External material first: real Japanese beats a frame sentence.
        for line in external_lines:
            if len(sentences) >= max_sentences:
                break
            if surface not in line or line in seen_text:
                continue
            findings = resolved_guard.screen(line, where="external")
            if findings:
                screened_out.append(
                    {
                        "item_id": item_id,
                        "code": worst_code(findings),
                        "origin": SOURCE_EXTERNAL,
                        "findings": [f.as_dict() for f in findings],
                    }
                )
                continue
            seen_text.add(line)
            sentences.append(
                {
                    "text": line,
                    "target_item_id": item_id,
                    "origin": SOURCE_EXTERNAL,
                    "template": None,
                    "needs_review": True,
                    "untrusted_origin": True,
                    "provenance": provenance,
                    "canary_screened": True,
                }
            )
            produced_here += 1

        family = coarse_pos(row["pos"])
        if family is None:
            if produced_here == 0:
                skipped.append(
                    {"item_id": item_id, "reason": SKIP_NO_TEMPLATE, "pos": row["pos"]}
                )
            continue

        for name, template in TEMPLATES[family]:
            if len(sentences) >= max_sentences:
                break
            candidate = template.replace("{surface}", surface)
            if candidate in seen_text:
                continue
            findings = resolved_guard.screen(candidate, where=f"template:{name}")
            if findings:
                screened_out.append(
                    {
                        "item_id": item_id,
                        "code": worst_code(findings),
                        "origin": SOURCE_TEMPLATE,
                        "template": name,
                        "findings": [f.as_dict() for f in findings],
                    }
                )
                continue
            seen_text.add(candidate)
            sentences.append(
                {
                    "text": candidate,
                    "target_item_id": item_id,
                    "origin": SOURCE_TEMPLATE,
                    "template": name,
                    "pos_family": family,
                    "needs_review": True,
                    "untrusted_origin": False,
                    "provenance": None,
                    "canary_screened": True,
                }
            )
            produced_here += 1

    payload: dict[str, Any] = {
        "sentences": sentences,
        "requested": max_sentences,
        "returned": len(sentences),
        "topic": topic,
        "screened_out": screened_out,
        "skipped": skipped,
        "redirects": redirects,
        "source_provenance": provenance,
        "external_lines_considered": len(external_lines),
        "canary_sentences_screened_against": len(resolved_guard),
        "canary_bands": resolved_guard.bands(),
    }
    if not sentences:
        return _failure(NO_CANDIDATES, NO_CANDIDATES_NOTE, **payload)
    logger.info(
        "build_sentences: %d sentence(s), %d screened out, %d skipped",
        len(sentences),
        len(screened_out),
        len(skipped),
    )
    return _success(
        f"{len(sentences)} practice sentence(s), each screened against the sealed "
        "canary set. Machine-scaffolded: check them before they become notes.",
        **payload,
    )


__all__ = [
    "BAD_COUNT",
    "CANARY_ECHO",
    "CANARY_SET_TAMPERED",
    "CANARY_SET_UNAVAILABLE",
    "CANARY_VAULT_PATH",
    "CANARY_VIOLATION",
    "CLOZE_BLANK",
    "CLOZE_PRODUCTION",
    "DEFAULT_COUNT",
    "DEFAULT_SENTENCES",
    "DIRECTIONS",
    "DRILLABLE_KINDS",
    "ECHO_BACK_REQUIRED",
    "FLAG",
    "LISTEN_TO_MEANING",
    "MAX_COUNT",
    "MAX_SENTENCES",
    "MAX_SOURCE_CHARS",
    "MEANING_TO_SPEECH",
    "MIN_CONTAINMENT_CHARS",
    "NO_CANDIDATES",
    "NO_SUCH_ITEM",
    "PRODUCTION_DIRECTIONS",
    "READ_TO_MEANING",
    "REFUSE",
    "SEALED_ITEM",
    "SHADOW",
    "SHINGLE_CHARS",
    "SKIP_NO_DIRECTION",
    "SKIP_NO_SURFACE",
    "SKIP_NO_TEMPLATE",
    "SKIP_RECEPTIVE_ONLY",
    "SOURCE_EXTERNAL",
    "SOURCE_TEMPLATE",
    "SOURCE_TOO_LARGE",
    "TEMPLATES",
    "UNENVELOPED_SOURCE",
    "UNKNOWN_DIRECTION",
    "VERIFY_AUTOMATIC",
    "VERIFY_OBSERVATION",
    "CanaryFinding",
    "CanaryGuard",
    "CanarySentence",
    "CanarySetTampered",
    "CanarySetUnavailable",
    "ExercisesError",
    "build_sentences",
    "canary_sentence_id",
    "canary_set_path",
    "coarse_pos",
    "gen_exercise",
    "load_canary_guard",
    "normalize_for_screening",
    "refuses",
    "reset_canary_cache",
    "worst_code",
]
