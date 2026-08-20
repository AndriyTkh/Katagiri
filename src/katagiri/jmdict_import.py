"""JMdict (jmdict-simplified) and kanjium pitch-accent import.

Both tables sets are *derived*: they are rebuilt wholesale from vendored files,
never migrated and never merged. So each import is DELETE + INSERT inside **one**
transaction — a reader (``known_set``, sentence search, ``lookup_word``) either
sees the previous import or the new one, never a half-empty dictionary. Nothing
is dropped, so no reader ever meets a missing table.

Vendor policy: every vendored file is verified at load time against
``vendor/CHECKSUMS.sha256``, and a digest mismatch (or a file the manifest does
not list) *refuses the import* before a single row is written. There are no
runtime downloads.

The 117 MB JSON lives inside its zip and is read straight out of the archive:
:class:`_WordArrayReader` streams the ``words`` array object by object with
``json.JSONDecoder.raw_decode`` over a sliding buffer, so peak memory is a few
megabytes rather than the ~2 GB a whole-file ``json.load`` would cost. Nothing
is extracted to disk.

Mapping to the 0001_init.sql DDL
--------------------------------
The DDL is fixed; the mapping bends to it. jmdict-simplified carries a few
fields the columns have no home for, and those are *packed* into the existing
tag-ish columns as a comma-separated token list rather than dropped:

``jmdict_entry``
    ``seq``          = ``int(word["id"])`` (JMdict ent_seq)
    ``is_common``    = 1 when *any* kanji or kana form has ``common: true``
    ``dict_version`` = the file's top-level ``version`` (e.g. ``3.6.2``)

``jmdict_kanji`` / ``jmdict_reading``
    one row per written / reading form. ``pri`` holds the form's upstream
    ``tags`` verbatim, plus these reserved tokens:
      * ``common`` — the form's own ``common: true`` flag (per-form; the entry
        column only records "some form is common"). jmdict-simplified has
        already folded the raw ``ke_pri``/``re_pri`` codes into this boolean, so
        ``common`` *is* the priority information the DDL comment asks for.
      * ``appliesToKanji=A|B`` — a kana form restricted to some kanji forms.
        Omitted for the ``["*"]`` (applies to all) default.

``jmdict_sense``
    one row per sense, ``sense_idx`` 1-based in upstream order (dictionaries
    cite "sense 1"; the ordering is upstream's and is preserved).
    ``pos``      = ``partOfSpeech`` joined with ``,``
    ``gloss_en`` = English glosses (``lang == "eng"``) joined with ``"; "``
    ``misc``     = ``misc`` tags joined with ``,``, plus reserved
    ``appliesToKanji=…`` / ``appliesToKana=…`` tokens when the sense is
    restricted to a subset of forms (again, the ``["*"]`` default is omitted).
    A sense with no English gloss keeps its row with ``gloss_en`` NULL.

Dropped, because the DDL has no column and no comment inviting a pack:
``related``/``antonym``/``field``/``dialect``/``info``/``languageSource`` on
senses, gloss ``type``/``gender``, and the file's ``tags`` legend. Re-import
after a schema change if they are ever wanted.

``pitch_accent`` (kanjium ``accents.txt``, TSV ``surface⇥reading⇥accent``)
    one row per *accent value*: upstream comma-separates alternatives, and the
    primary key is ``(surface, reading, accent)``, so ``1,0`` becomes two rows.
    Values are kept verbatim, including register-qualified forms like
    ``(副)0``. Kana-only lines carry an empty reading field; ``reading`` is then
    set to the surface, which is what makes them joinable with a kana-only
    JMdict entry. ``source_version`` = ``kanjium:<first 12 hex of the sha256>``,
    since the file itself carries no version.

Provenance lands in ``metadata``: ``jmdict_version``, ``jmdict_dict_date``,
``jmdict_imported_ts``, and ``kanjium_source_version`` /
``kanjium_imported_ts``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Iterator

from katagiri import db
from katagiri.logging_setup import get_logger

CHECKSUM_FILE_NAME: Final = "CHECKSUMS.sha256"
VENDOR_DIR_NAME: Final = "vendor"
JMDICT_DIR_NAME: Final = "jmdict"
JMDICT_ZIP_GLOB: Final = "jmdict-eng-*.json.zip"
KANJIUM_DIR_NAME: Final = "kanjium"
KANJIUM_FILE_NAME: Final = "accents.txt"

# Derived tables this module owns, in delete order.
JMDICT_TABLES: Final = ("jmdict_sense", "jmdict_reading", "jmdict_kanji", "jmdict_entry")
PITCH_TABLES: Final = ("pitch_accent",)

_HASH_CHUNK: Final = 1 << 20
_READ_CHUNK: Final = 1 << 20
# Words held before a flush. Bounds memory during the streaming insert; the
# whole insert is one transaction regardless.
_BATCH_WORDS: Final = 2000
# Compact the sliding parse buffer once this much of it has been consumed.
_COMPACT_AT: Final = 1 << 20

_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")
# Matches the top-level "words": [ that opens the entry array.
_WORDS_KEY_RE: Final = re.compile(r'"words"\s*:\s*\[')
_SKIPPABLE: Final = " \t\r\n,"

GLOSS_JOINER: Final = "; "
TAG_JOINER: Final = ","
COMMON_TOKEN: Final = "common"
APPLIES_KANJI_TOKEN: Final = "appliesToKanji="
APPLIES_KANA_TOKEN: Final = "appliesToKana="
APPLIES_JOINER: Final = "|"
# jmdict-simplified's "no restriction" marker.
_ALL_FORMS: Final = "*"

ENGLISH: Final = "eng"
ACCENT_SEPARATOR: Final = ","

_logger = get_logger("jmdict_import")


class JmdictImportError(RuntimeError):
    """Base class for every failure this module raises."""


class VendorFileError(JmdictImportError):
    """A vendored file is missing, ambiguous, or unreadable."""


class ChecksumError(JmdictImportError):
    """A vendored file is not listed in the manifest, or its digest differs.

    ``expected``/``actual`` are the manifest digest and the recomputed one
    (``actual`` only, for a file the manifest does not list).
    """

    def __init__(
        self, message: str, *, expected: str | None = None, actual: str | None = None
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Row counts and provenance of one :func:`import_jmdict` call."""

    entries: int
    kanji_rows: int
    reading_rows: int
    sense_rows: int
    version: str | None
    dict_date: str | None


@dataclass(frozen=True, slots=True)
class PitchResult:
    """Row counts and provenance of one :func:`import_kanjium` call."""

    rows: int
    lines: int
    source_version: str


# ---------------------------------------------------------------------------
# Vendored files and their checksums
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """The checkout root: the nearest ancestor holding a ``vendor`` directory.

    Vendored data is repository state, not package data, so it is located
    relative to the checkout rather than to ``config``. A non-editable install
    with no ``vendor`` beside it therefore fails loudly here instead of
    importing an empty dictionary.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / VENDOR_DIR_NAME).is_dir():
            return parent
    raise VendorFileError(
        f"No '{VENDOR_DIR_NAME}' directory found above {here}. The JMdict and "
        "kanjium files are vendored in the repository; run this from a checkout "
        "(or pass explicit paths)."
    )


def vendor_dir() -> Path:
    """``<repo>/vendor``."""
    return repo_root() / VENDOR_DIR_NAME


def checksum_manifest_path() -> Path:
    """``<repo>/vendor/CHECKSUMS.sha256``."""
    return vendor_dir() / CHECKSUM_FILE_NAME


def default_jmdict_zip() -> Path:
    """The single ``jmdict-eng-*.json.zip`` under ``vendor/jmdict``.

    Several candidates is an error rather than "newest wins": which dictionary
    build produced the lexemes is provenance, not a detail to guess at.
    """
    directory = vendor_dir() / JMDICT_DIR_NAME
    candidates = sorted(directory.glob(JMDICT_ZIP_GLOB))
    if not candidates:
        raise VendorFileError(
            f"No file matching {JMDICT_ZIP_GLOB} in {directory}. See "
            "vendor/README.md for how to fetch the jmdict-simplified release."
        )
    if len(candidates) > 1:
        listing = ", ".join(path.name for path in candidates)
        raise VendorFileError(
            f"{len(candidates)} JMdict archives in {directory} ({listing}); "
            "refusing to guess which one to import. Pass the path explicitly, "
            "or keep one."
        )
    return candidates[0]


def default_kanjium_path() -> Path:
    """``<repo>/vendor/kanjium/accents.txt``."""
    return vendor_dir() / KANJIUM_DIR_NAME / KANJIUM_FILE_NAME


def read_manifest(manifest_path: Path | str | None = None) -> dict[str, str]:
    """Parse ``CHECKSUMS.sha256`` into ``{relative posix path: digest}``.

    Comment (``#``) and blank lines are skipped. Anything else must be a
    64-hex-digit lowercase digest followed by whitespace and a path — a
    malformed line is an error, because a manifest that silently loses an entry
    turns "verified at load" into "not checked".
    """
    path = Path(manifest_path) if manifest_path is not None else checksum_manifest_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VendorFileError(
            f"Could not read the checksum manifest {path}: {exc}. Vendored data "
            "is verified at load time, so the import cannot proceed without it."
        ) from exc

    entries: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or not _DIGEST_RE.match(parts[0]):
            raise VendorFileError(
                f"{path} line {number} is not '<64-hex digest>  <path>': {raw!r}."
            )
        entries[parts[1].strip().replace("\\", "/")] = parts[0]
    return entries


def _manifest_key(entries: dict[str, str], target: Path) -> str:
    """Find the manifest entry naming ``target``.

    Manifest paths are repo-relative; ``target`` is usually absolute. An entry
    matches when its slash-separated components are a trailing run of the
    target's own components (case-insensitively, since this runs on Windows).
    Zero matches and several matches are both errors: the point of the manifest
    is that there is exactly one expectation per file.
    """
    target_parts = [part.casefold() for part in target.resolve().parts]
    matches = [
        key
        for key in entries
        if (candidate := [p.casefold() for p in Path(key).parts])
        and target_parts[-len(candidate) :] == candidate
    ]
    if not matches:
        raise ChecksumError(
            f"{target} is not listed in the checksum manifest. Vendored files "
            "are verified at load time; add the digest to "
            f"{CHECKSUM_FILE_NAME} (or import a file that is listed)."
        )
    if len(matches) > 1:
        raise ChecksumError(
            f"{target} matches {len(matches)} checksum manifest entries "
            f"({', '.join(sorted(matches))}); the manifest is ambiguous."
        )
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        raise VendorFileError(f"Could not read {path} to verify it: {exc}.") from exc
    return digest.hexdigest()


def verify_vendor_file(
    path: Path | str, *, manifest_path: Path | str | None = None
) -> str:
    """Recompute ``path``'s sha256 and check it against the manifest.

    Returns the digest so a caller can use it as a source version. Raises
    :class:`ChecksumError` when the file is unlisted or tampered with, and
    :class:`VendorFileError` when it (or the manifest) cannot be read. Callers
    run this *before* touching the database: a bad file must not be able to
    delete a good import.
    """
    target = Path(path)
    if not target.is_file():
        raise VendorFileError(
            f"The vendored file {target} does not exist or is not a file."
        )
    entries = read_manifest(manifest_path)
    key = _manifest_key(entries, target)
    expected = entries[key]
    actual = _sha256(target)
    if actual != expected:
        raise ChecksumError(
            f"Checksum mismatch for {target}: the manifest entry '{key}' expects "
            f"{expected} but the file hashes to {actual}. Refusing to import "
            "data that is not the bytes this build was written against; "
            "re-download the file or update vendor/CHECKSUMS.sha256 "
            "deliberately.",
            expected=expected,
            actual=actual,
        )
    return actual


# ---------------------------------------------------------------------------
# Streaming the words array
# ---------------------------------------------------------------------------


class _WordArrayReader:
    """Streams ``{"version": …, "words": [ … ]}`` one word object at a time.

    The header is parsed by taking everything before ``"words":[`` and closing
    it off as its own object, which is cheap and needs no second pass. Words are
    then pulled with ``raw_decode`` over a buffer that is refilled on demand: a
    decode failure means "not enough bytes yet" unless the stream is exhausted,
    in which case the file really is truncated.
    """

    def __init__(self, stream: io.TextIOBase, *, chunk_size: int = _READ_CHUNK) -> None:
        self._stream = stream
        self._chunk_size = chunk_size
        self._decoder = json.JSONDecoder()
        self._buffer = ""
        self._pos = 0
        self._eof = False
        self._started = False
        self.version: str | None = None
        self.dict_date: str | None = None

    def _fill(self) -> bool:
        """Append the next chunk. False when the stream is exhausted."""
        if self._eof:
            return False
        data = self._stream.read(self._chunk_size)
        if not data:
            self._eof = True
            return False
        self._buffer += data
        return True

    def read_header(self) -> None:
        """Consume everything up to the first word, filling in the metadata."""
        if self._started:
            return
        while True:
            match = _WORDS_KEY_RE.search(self._buffer)
            if match is not None:
                break
            if not self._fill():
                raise JmdictImportError(
                    "The JMdict JSON has no top-level 'words' array; this does "
                    "not look like a jmdict-simplified file."
                )
        header_text = self._buffer[: match.start()] + '"words":[]}'
        try:
            header = json.loads(header_text)
        except ValueError as exc:
            raise JmdictImportError(
                f"Could not parse the JMdict header preceding the words array: "
                f"{exc}."
            ) from exc
        if not isinstance(header, dict):
            raise JmdictImportError(
                "The JMdict JSON's top level is not an object; expected "
                "jmdict-simplified's {version, dictDate, words: [...]}."
            )
        version = header.get("version")
        dict_date = header.get("dictDate")
        self.version = None if version is None else str(version)
        self.dict_date = None if dict_date is None else str(dict_date)
        self._buffer = self._buffer[match.end() :]
        self._pos = 0
        self._started = True

    def _skip_separators(self) -> bool:
        """Advance past whitespace and commas. False at end of stream."""
        while True:
            while self._pos < len(self._buffer) and self._buffer[self._pos] in _SKIPPABLE:
                self._pos += 1
            if self._pos < len(self._buffer):
                return True
            if not self._fill():
                return False

    def words(self) -> Iterator[dict[str, Any]]:
        """Yield each word object in file order."""
        self.read_header()
        while True:
            if not self._skip_separators():
                raise JmdictImportError(
                    "The JMdict JSON ended inside the words array (truncated or "
                    "corrupt archive); nothing was imported."
                )
            if self._buffer[self._pos] == "]":
                return
            while True:
                try:
                    word, end = self._decoder.raw_decode(self._buffer, self._pos)
                except ValueError as exc:
                    # Either the object straddles the buffer's end, or the JSON
                    # is broken. More bytes tell the two apart.
                    if self._fill():
                        continue
                    raise JmdictImportError(
                        f"Could not parse a JMdict word object: {exc}."
                    ) from exc
                break
            self._pos = end
            if not isinstance(word, dict):
                raise JmdictImportError(
                    f"The JMdict words array holds a {type(word).__name__}, not "
                    "an object."
                )
            yield word
            if self._pos >= _COMPACT_AT:
                self._buffer = self._buffer[self._pos :]
                self._pos = 0


def _sole_json_member(archive: zipfile.ZipFile, path: Path) -> str:
    """The one ``.json`` member of the archive."""
    members = [
        name
        for name in archive.namelist()
        if name.lower().endswith(".json") and not name.endswith("/")
    ]
    if len(members) != 1:
        raise VendorFileError(
            f"{path} contains {len(members)} .json members "
            f"({', '.join(members) or 'none'}); expected exactly one "
            "jmdict-simplified JSON file."
        )
    return members[0]


# ---------------------------------------------------------------------------
# Mapping words to rows
# ---------------------------------------------------------------------------


def _restriction_token(prefix: str, applies: Any) -> str | None:
    """``appliesToKanji=A|B`` for a real restriction, else None."""
    if not isinstance(applies, list) or not applies:
        return None
    forms = [str(form) for form in applies if form not in (None, "")]
    if not forms or _ALL_FORMS in forms:
        return None
    return prefix + APPLIES_JOINER.join(forms)


def _pack_tags(
    tags: Any,
    *,
    common: bool = False,
    applies_to_kanji: Any = None,
    applies_to_kana: Any = None,
) -> str | None:
    """Comma-join upstream tags plus the reserved tokens; None when empty."""
    tokens: list[str] = []
    if isinstance(tags, list):
        tokens.extend(str(tag) for tag in tags if tag not in (None, ""))
    if common:
        tokens.append(COMMON_TOKEN)
    for prefix, applies in (
        (APPLIES_KANJI_TOKEN, applies_to_kanji),
        (APPLIES_KANA_TOKEN, applies_to_kana),
    ):
        token = _restriction_token(prefix, applies)
        if token is not None:
            tokens.append(token)
    return TAG_JOINER.join(tokens) if tokens else None


def _english_gloss(sense: dict[str, Any]) -> str | None:
    """English glosses of one sense, joined; None when it has none.

    ``lang`` missing is treated as English: the eng-only build sometimes omits
    it, and this module only ever imports the English release.
    """
    glosses = sense.get("gloss")
    if not isinstance(glosses, list):
        return None
    texts = [
        str(entry["text"]).strip()
        for entry in glosses
        if isinstance(entry, dict)
        and entry.get("text")
        and str(entry.get("lang") or ENGLISH) == ENGLISH
    ]
    return GLOSS_JOINER.join(texts) if texts else None


def _forms(word: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = word.get(key)
    return [form for form in value if isinstance(form, dict)] if isinstance(value, list) else []


def _word_rows(
    word: dict[str, Any], version: str
) -> tuple[
    tuple[Any, ...], list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]
]:
    """Turn one jmdict-simplified word into its four table rows."""
    raw_id = word.get("id")
    try:
        seq = int(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise JmdictImportError(
            f"JMdict word id {raw_id!r} is not an integer, but jmdict_entry.seq "
            "is the upstream ent_seq and must be one."
        ) from exc

    kanji_forms = _forms(word, "kanji")
    kana_forms = _forms(word, "kana")
    is_common = any(bool(form.get("common")) for form in (*kanji_forms, *kana_forms))

    entry_row: tuple[Any, ...] = (seq, 1 if is_common else 0, version)

    kanji_rows = [
        (
            seq,
            str(form["text"]),
            _pack_tags(form.get("tags"), common=bool(form.get("common"))),
        )
        for form in kanji_forms
        if form.get("text")
    ]
    reading_rows = [
        (
            seq,
            str(form["text"]),
            _pack_tags(
                form.get("tags"),
                common=bool(form.get("common")),
                applies_to_kanji=form.get("appliesToKanji"),
            ),
        )
        for form in kana_forms
        if form.get("text")
    ]

    senses = word.get("sense")
    sense_rows: list[tuple[Any, ...]] = []
    if isinstance(senses, list):
        for index, sense in enumerate(senses, start=1):
            if not isinstance(sense, dict):
                continue
            pos = sense.get("partOfSpeech")
            sense_rows.append(
                (
                    seq,
                    index,
                    _pack_tags(pos),
                    _english_gloss(sense),
                    _pack_tags(
                        sense.get("misc"),
                        applies_to_kanji=sense.get("appliesToKanji"),
                        applies_to_kana=sense.get("appliesToKana"),
                    ),
                )
            )
    return entry_row, kanji_rows, reading_rows, sense_rows


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(conn: sqlite3.Connection, values: dict[str, str | None]) -> None:
    """Write provenance into ``metadata`` (inside the caller's transaction)."""
    now = _utc_now()
    conn.executemany(
        "INSERT OR REPLACE INTO metadata(key, value, updated_ts) VALUES (?, ?, ?)",
        [(key, value, now) for key, value in values.items()],
    )


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def import_jmdict(
    conn: sqlite3.Connection,
    zip_path: Path | str | None = None,
    *,
    manifest_path: Path | str | None = None,
) -> ImportResult:
    """Rebuild the ``jmdict_*`` tables from the vendored jmdict-simplified zip.

    ``conn`` is a migrated Katagiri database. The archive is verified against
    the checksum manifest first, then read straight out of the zip and written
    in a single transaction: on any failure the previous import is still there,
    untouched.
    """
    path = Path(zip_path) if zip_path is not None else default_jmdict_zip()
    verify_vendor_file(path, manifest_path=manifest_path)

    previous = _row_count(conn, "jmdict_entry")
    counts = {"entries": 0, "kanji": 0, "readings": 0, "senses": 0}

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise VendorFileError(f"Could not open the JMdict archive {path}: {exc}.") from exc

    with archive:
        member = _sole_json_member(archive, path)
        with archive.open(member) as raw:
            # newline='' keeps the decoder from rewriting line endings inside
            # gloss text; the parser does not care about lines at all.
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = _WordArrayReader(stream)
            reader.read_header()
            version = reader.version
            if not version:
                raise JmdictImportError(
                    f"{path} declares no 'version'; jmdict_entry.dict_version is "
                    "NOT NULL because an import with unknown provenance is not "
                    "worth keeping."
                )

            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in JMDICT_TABLES:
                    conn.execute(f"DELETE FROM {table}")

                entry_batch: list[tuple[Any, ...]] = []
                kanji_batch: list[tuple[Any, ...]] = []
                reading_batch: list[tuple[Any, ...]] = []
                sense_batch: list[tuple[Any, ...]] = []

                def flush() -> None:
                    if not entry_batch:
                        return
                    conn.executemany(
                        "INSERT OR REPLACE INTO jmdict_entry"
                        "(seq, is_common, dict_version) VALUES (?, ?, ?)",
                        entry_batch,
                    )
                    conn.executemany(
                        "INSERT OR REPLACE INTO jmdict_kanji(seq, kanji, pri) "
                        "VALUES (?, ?, ?)",
                        kanji_batch,
                    )
                    conn.executemany(
                        "INSERT OR REPLACE INTO jmdict_reading(seq, reading, pri) "
                        "VALUES (?, ?, ?)",
                        reading_batch,
                    )
                    conn.executemany(
                        "INSERT OR REPLACE INTO jmdict_sense"
                        "(seq, sense_idx, pos, gloss_en, misc) VALUES (?, ?, ?, ?, ?)",
                        sense_batch,
                    )
                    counts["entries"] += len(entry_batch)
                    counts["kanji"] += len(kanji_batch)
                    counts["readings"] += len(reading_batch)
                    counts["senses"] += len(sense_batch)
                    entry_batch.clear()
                    kanji_batch.clear()
                    reading_batch.clear()
                    sense_batch.clear()

                for word in reader.words():
                    entry_row, kanji_rows, reading_rows, sense_rows = _word_rows(
                        word, version
                    )
                    entry_batch.append(entry_row)
                    kanji_batch.extend(kanji_rows)
                    reading_batch.extend(reading_rows)
                    sense_batch.extend(sense_rows)
                    if len(entry_batch) >= _BATCH_WORDS:
                        flush()
                flush()

                if not counts["entries"] and previous:
                    raise JmdictImportError(
                        f"{path} yielded no entries while the database holds "
                        f"{previous}; refusing to replace a good import with an "
                        "empty one."
                    )

                _stamp(
                    conn,
                    {
                        "jmdict_version": version,
                        "jmdict_dict_date": reader.dict_date,
                        "jmdict_imported_ts": _utc_now(),
                    },
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
                    pass
                raise

    _logger.info(
        "Imported JMdict %s (%s): %d entries, %d kanji, %d readings, %d senses.",
        version,
        reader.dict_date or "no date",
        counts["entries"],
        counts["kanji"],
        counts["readings"],
        counts["senses"],
    )
    return ImportResult(
        entries=counts["entries"],
        kanji_rows=counts["kanji"],
        reading_rows=counts["readings"],
        sense_rows=counts["senses"],
        version=version,
        dict_date=reader.dict_date,
    )


def _accent_rows(line: str, source_version: str) -> list[tuple[Any, ...]]:
    """Split one ``surface⇥reading⇥accent`` line into pitch_accent rows."""
    fields = line.split("\t")
    if len(fields) < 3:
        return []
    surface = fields[0].strip()
    reading = fields[1].strip() or surface
    if not surface:
        return []
    rows = []
    for accent in fields[2].split(ACCENT_SEPARATOR):
        accent = accent.strip()
        if accent:
            rows.append((surface, reading, accent, source_version))
    return rows


def import_kanjium(
    conn: sqlite3.Connection,
    path: Path | str | None = None,
    *,
    manifest_path: Path | str | None = None,
) -> PitchResult:
    """Rebuild ``pitch_accent`` from the vendored kanjium ``accents.txt``.

    Same contract as :func:`import_jmdict`: checksum first, then DELETE +
    INSERT in one transaction.
    """
    source = Path(path) if path is not None else default_kanjium_path()
    digest = verify_vendor_file(source, manifest_path=manifest_path)
    source_version = f"kanjium:{digest[:12]}"

    previous = _row_count(conn, "pitch_accent")
    rows: list[tuple[Any, ...]] = []
    lines = 0
    try:
        with source.open(encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n").rstrip("\r")
                if not line:
                    continue
                lines += 1
                rows.extend(_accent_rows(line, source_version))
    except (OSError, UnicodeDecodeError) as exc:
        raise VendorFileError(f"Could not read {source}: {exc}.") from exc

    if not rows and previous:
        raise JmdictImportError(
            f"{source} yielded no accents while the database holds {previous} "
            "rows; refusing to replace a good import with an empty one."
        )

    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in PITCH_TABLES:
            conn.execute(f"DELETE FROM {table}")
        # OR IGNORE, not OR REPLACE: upstream repeats some (surface, reading,
        # accent) triples across lines, and the first spelling is as good as the
        # second.
        conn.executemany(
            "INSERT OR IGNORE INTO pitch_accent"
            "(surface, reading, accent, source_version) VALUES (?, ?, ?, ?)",
            rows,
        )
        stored = _row_count(conn, "pitch_accent")
        _stamp(
            conn,
            {
                "kanjium_source_version": source_version,
                "kanjium_imported_ts": _utc_now(),
            },
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - rollback of a dead txn
            pass
        raise

    _logger.info(
        "Imported %d pitch-accent rows from %d kanjium lines (%s).",
        stored,
        lines,
        source_version,
    )
    return PitchResult(rows=stored, lines=lines, source_version=source_version)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _split_tags(value: str | None) -> list[str]:
    return [tag for tag in (value or "").split(TAG_JOINER) if tag]


def lookup_word(
    conn: sqlite3.Connection, surface: str, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Look ``surface`` up as either a written form or a reading.

    Returns one dict per matching entry, common-first (then by ascending seq,
    which is JMdict's own rough frequency-ish ordering and is at least stable).
    Each entry carries its written forms, its readings — each with whatever
    kanjium pitch accents match that (surface, reading) pair — and its senses
    with part-of-speech, English gloss, and usage tags.

    Pitch is attached per reading: a reading is looked up against every written
    form of the entry *and* against itself, the latter being how kana-only
    words (whose kanjium line has an empty reading field) match.
    """
    if not surface:
        return []
    entries = conn.execute(
        "SELECT seq, is_common, dict_version FROM jmdict_entry "
        "WHERE seq IN ("
        "  SELECT seq FROM jmdict_kanji WHERE kanji = ?"
        "  UNION SELECT seq FROM jmdict_reading WHERE reading = ?"
        ") ORDER BY is_common DESC, seq ASC"
        + (" LIMIT ?" if limit is not None else ""),
        (surface, surface) if limit is None else (surface, surface, limit),
    ).fetchall()

    results: list[dict[str, Any]] = []
    for entry in entries:
        seq = int(entry["seq"])
        kanji = [
            {
                "text": row["kanji"],
                "tags": _split_tags(row["pri"]),
                "common": COMMON_TOKEN in _split_tags(row["pri"]),
            }
            for row in conn.execute(
                "SELECT kanji, pri FROM jmdict_kanji WHERE seq = ? ORDER BY rowid",
                (seq,),
            )
        ]
        readings = [
            {
                "reading": row["reading"],
                "tags": _split_tags(row["pri"]),
                "common": COMMON_TOKEN in _split_tags(row["pri"]),
                "pitch": [],
            }
            for row in conn.execute(
                "SELECT reading, pri FROM jmdict_reading WHERE seq = ? ORDER BY rowid",
                (seq,),
            )
        ]
        senses = [
            {
                "sense_idx": int(row["sense_idx"]),
                "pos": _split_tags(row["pos"]),
                "gloss": row["gloss_en"],
                "misc": _split_tags(row["misc"]),
            }
            for row in conn.execute(
                "SELECT sense_idx, pos, gloss_en, misc FROM jmdict_sense "
                "WHERE seq = ? ORDER BY sense_idx",
                (seq,),
            )
        ]

        pitch_rows: list[dict[str, Any]] = []
        reading_texts = [str(item["reading"]) for item in readings]
        surfaces = [str(item["text"]) for item in kanji] + reading_texts
        if reading_texts and surfaces:
            placeholders_r = ", ".join("?" * len(reading_texts))
            placeholders_s = ", ".join("?" * len(surfaces))
            for row in conn.execute(
                "SELECT surface, reading, accent FROM pitch_accent "
                f"WHERE reading IN ({placeholders_r}) "
                f"AND surface IN ({placeholders_s}) ORDER BY rowid",
                (*reading_texts, *surfaces),
            ):
                pitch_rows.append(
                    {
                        "surface": row["surface"],
                        "reading": row["reading"],
                        "accent": row["accent"],
                    }
                )
        for item in readings:
            accents = [
                row["accent"] for row in pitch_rows if row["reading"] == item["reading"]
            ]
            # dict.fromkeys: the same accent can arrive via several written
            # forms, and duplicates would read as alternative pitches.
            item["pitch"] = list(dict.fromkeys(accents))

        results.append(
            {
                "seq": seq,
                "is_common": bool(entry["is_common"]),
                "dict_version": entry["dict_version"],
                "kanji": kanji,
                "readings": readings,
                "senses": senses,
                "pitch": pitch_rows,
            }
        )
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.jmdict_import",
        description=(
            "Rebuild the derived dictionary tables from the vendored "
            "jmdict-simplified and kanjium files."
        ),
    )
    parser.add_argument(
        "what",
        nargs="?",
        default="all",
        choices=("jmdict", "kanjium", "all"),
        help="which import to run (default: all)",
    )
    parser.add_argument("--db", type=Path, default=None, help="database to write")
    parser.add_argument(
        "--jmdict", type=Path, default=None, help="override the JMdict archive path"
    )
    parser.add_argument(
        "--kanjium", type=Path, default=None, help="override the accents.txt path"
    )
    parser.add_argument(
        "--manifest", type=Path, default=None, help="override CHECKSUMS.sha256"
    )
    parser.add_argument(
        "--lookup",
        default=None,
        help="after importing, print lookup_word() for this surface",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m katagiri.jmdict_import``."""
    args = _build_parser().parse_args(argv)
    conn = db.open_db(args.db)
    try:
        if args.what in ("jmdict", "all"):
            result = import_jmdict(
                conn, args.jmdict, manifest_path=args.manifest
            )
            print(
                f"jmdict {result.version} ({result.dict_date}): "
                f"{result.entries} entries, {result.kanji_rows} kanji, "
                f"{result.reading_rows} readings, {result.sense_rows} senses"
            )
        if args.what in ("kanjium", "all"):
            pitch = import_kanjium(conn, args.kanjium, manifest_path=args.manifest)
            print(
                f"kanjium {pitch.source_version}: {pitch.rows} accent rows "
                f"from {pitch.lines} lines"
            )
        if args.lookup:
            found = lookup_word(conn, args.lookup)
            print(f"lookup {args.lookup!r}: {len(found)} entries")
            print(json.dumps(found, ensure_ascii=False, indent=2))
    except (JmdictImportError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}")
        return 2
    finally:
        conn.close()
    return 0


__all__ = [
    "ACCENT_SEPARATOR",
    "APPLIES_KANA_TOKEN",
    "APPLIES_KANJI_TOKEN",
    "CHECKSUM_FILE_NAME",
    "COMMON_TOKEN",
    "JMDICT_TABLES",
    "PITCH_TABLES",
    "ChecksumError",
    "ImportResult",
    "JmdictImportError",
    "PitchResult",
    "VendorFileError",
    "checksum_manifest_path",
    "default_jmdict_zip",
    "default_kanjium_path",
    "import_jmdict",
    "import_kanjium",
    "lookup_word",
    "main",
    "read_manifest",
    "repo_root",
    "vendor_dir",
    "verify_vendor_file",
]


if __name__ == "__main__":  # pragma: no cover
    # Production entry point (installer subprocess, manual reimport): runs under
    # the shared rotating log in %LOCALAPPDATA%\Katagiri\logs. See
    # katagiri.applog.run_cli for why this is not inside main().
    from katagiri.applog import run_cli

    raise SystemExit(run_cli("jmdict_import", main))
