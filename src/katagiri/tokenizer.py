"""Japanese tokenization on the vendored full UniDic 3.1.0, via fugashi/MeCab.

This is the substrate every morph-level feature sits on: coverage, mining, and
the ``morph_lexeme_map`` crosswalk all agree on *whatever this module calls a
morph*, so the mapping from MeCab's feature CSV to :class:`Morph` is the
contract, not an implementation detail.

Dictionary policy
-----------------
The dictionary is **vendored**, at ``vendor/unidic/unidic`` (unpacked from
``vendor/unidic/unidic-3.1.0.zip``). It is never downloaded at runtime and the
tagger is *never* allowed to fall back to a pip-installed ``unidic`` /
``unidic-lite``: those are different dictionaries with different feature
layouts, so silently using one would change every lemma and POS tag in the
database without changing the recorded ``dict_version``. Both the dictionary
directory and the ``-r dicrc`` are therefore passed explicitly, and
:func:`get_tagger` re-reads MeCab's own ``dictionary_info`` afterwards to prove
the dictionary it actually loaded is the vendored one.

Verification is split by cost. The zip's SHA-256 in ``vendor/CHECKSUMS.sha256``
is the manifest anchor and *is* the recorded provenance, but the unpacked
dictionary is ~775 MB: re-hashing it (or the zip) on every startup would add
seconds to every server launch for a file that cannot change between two
launches of the same checkout. So:

* at load time — presence of ``dicrc`` and the binary dictionary files, plus a
  ``dicrc`` sanity check that this is *full* UniDic (29 feature fields, not
  unidic-lite's 17), plus a post-build check of the loaded dictionary path;
* on demand only — :func:`verify_dict`, which re-hashes the zip against the
  manifest (``python -m katagiri.tokenizer verify``).

Feature mapping
---------------
Full UniDic 3.1.0 emits 29 comma-separated feature fields, in exactly the order
``dicrc`` documents, and fugashi's own :class:`fugashi.UnidicFeatures29`
namedtuple matches that order field for field (verified empirically against the
vendored dictionary, and re-asserted at tagger build time). Named access is used
rather than positional indexing, and :func:`get_tagger` fails loud if fugashi
hands back any other feature wrapper.

Two UniDic subtleties the mapping resolves:

* ``lForm`` is the reading of the **lemma** (食べ → ``タベル``) while ``kana``
  is the reading of the **surface** (食べ → ``タベ``). :attr:`Morph.lemma_reading`
  is the lemma's reading, so it comes from ``lForm``.
* Unknown words are emitted through ``unk-format``, which carries only the
  first six fields — so an unknown morph has POS but no lemma, no reading and
  no inflection. :attr:`Morph.lemma` falls back to the surface there (a caller
  grouping by lemma should not silently collapse every unknown word into one
  bucket), and :attr:`Morph.lemma_reading` stays ``None``.

UniDic writes "unspecified" as ``*``; that is normalised to ``None`` so callers
never have to know the sentinel.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Final

import fugashi

from katagiri.jmdict_import import (
    ChecksumError,
    VendorFileError,
    read_manifest,
    vendor_dir,
    verify_vendor_file,
)
from katagiri.logging_setup import get_logger

TOKENIZER_NAME: Final = "fugashi"
DICT_NAME: Final = "unidic"
DICT_VERSION: Final = "3.1.0"

UNIDIC_VENDOR_DIR_NAME: Final = "unidic"
UNIDIC_DICDIR_NAME: Final = "unidic"
UNIDIC_ZIP_NAME: Final = f"unidic-{DICT_VERSION}.zip"
DICRC_NAME: Final = "dicrc"

# Files MeCab needs before it can open a dictionary directory at all. Checked by
# stat, not by digest: this runs on every process start.
REQUIRED_DICT_FILES: Final = ("dicrc", "char.bin", "matrix.bin", "sys.dic", "unk.dic")
# Full UniDic's dicrc documents feature f[28] (lemma_id). unidic-lite's 17-field
# dicrc does not, so this one token separates the two distributions cheaply.
_FULL_UNIDIC_DICRC_MARKER: Final = "f[28]"
# Read far enough into dicrc to cover the feature list; the whole file is a few
# KB, so this is a guard against a pathological file, not an optimisation.
_DICRC_READ_LIMIT: Final = 1 << 16

# Probe used to confirm the loaded dictionary really produces 29 named features.
# Must be a word full UniDic knows, or the probe would come back through
# unk-format with only six fields.
_PROBE_TEXT: Final = "日本語"
_SELFTEST_TEXT: Final = "今日は良い天気ですね"

_UNSPECIFIED: Final = "*"

METADATA_KEYS: Final = (
    "tokenizer_name",
    "tokenizer_version",
    "dict_name",
    "dict_version",
    "dict_zip_sha256",
    "stamped_ts",
)

_logger = get_logger("tokenizer")


class TokenizerError(RuntimeError):
    """Base class for every failure this module raises."""


class DictionaryNotFoundError(TokenizerError):
    """The vendored UniDic directory is absent, incomplete, or the wrong build."""


# ---------------------------------------------------------------------------
# Locating the vendored dictionary
# ---------------------------------------------------------------------------


def _acquisition_hint() -> str:
    return (
        "See vendor/README.md ('Acquisition' > 'Full UniDic') for how to obtain "
        f"and unpack {UNIDIC_ZIP_NAME}; the digest in vendor/CHECKSUMS.sha256 "
        "pins which bytes are expected. Katagiri never downloads it at runtime, "
        "and it will not fall back to a pip-installed unidic/unidic-lite."
    )


def _unidic_vendor_dir() -> Path:
    """``<repo>/vendor/unidic``, or a tokenizer-flavoured error."""
    try:
        base = vendor_dir()
    except VendorFileError as exc:
        raise DictionaryNotFoundError(
            f"Cannot locate the repository's vendor directory ({exc}). The "
            f"UniDic {DICT_VERSION} dictionary is vendored repository state, so "
            f"tokenization only works from a checkout. {_acquisition_hint()}"
        ) from exc
    return base / UNIDIC_VENDOR_DIR_NAME


def dict_zip_path() -> Path:
    """``<repo>/vendor/unidic/unidic-3.1.0.zip`` — the manifest's anchor file."""
    return _unidic_vendor_dir() / UNIDIC_ZIP_NAME


def dicdir_path() -> Path:
    """The unpacked dictionary directory, checked for presence and shape.

    Raises :class:`DictionaryNotFoundError` — naming the missing path and
    pointing at ``vendor/README.md`` — rather than returning a path MeCab would
    reject later with a bare ``failed to initialize``.
    """
    dicdir = _unidic_vendor_dir() / UNIDIC_DICDIR_NAME

    if not dicdir.is_dir():
        raise DictionaryNotFoundError(
            f"The vendored UniDic {DICT_VERSION} dictionary directory {dicdir} "
            f"does not exist. {_acquisition_hint()}"
        )

    missing = [name for name in REQUIRED_DICT_FILES if not (dicdir / name).is_file()]
    if missing:
        raise DictionaryNotFoundError(
            f"The vendored dictionary directory {dicdir} is missing "
            f"{', '.join(missing)}. Unpack the archive so these files sit "
            f"*directly* inside it (not in a nested subdirectory). "
            f"{_acquisition_hint()}"
        )

    dicrc = dicdir / DICRC_NAME
    try:
        head = dicrc.read_text(encoding="utf-8", errors="replace")[:_DICRC_READ_LIMIT]
    except OSError as exc:
        raise DictionaryNotFoundError(
            f"Could not read {dicrc}: {exc}. {_acquisition_hint()}"
        ) from exc
    if _FULL_UNIDIC_DICRC_MARKER not in head:
        raise DictionaryNotFoundError(
            f"{dicrc} does not declare feature {_FULL_UNIDIC_DICRC_MARKER}, so "
            f"this is not the full UniDic {DICT_VERSION} distribution (most "
            "likely unidic-lite, whose 17 feature fields carry no accent or "
            f"lemma detail). {_acquisition_hint()}"
        )

    return dicdir


def tagger_args(dicdir: Path | str | None = None) -> str:
    """The MeCab argument string: explicit ``-d`` dicdir and ``-r`` dicrc.

    Both are always passed. Without ``-d`` fugashi would try to import an
    installed ``unidic``/``unidic-lite`` package, and without ``-r`` MeCab would
    look for a system ``mecabrc`` — either one silently substitutes a different
    dictionary for the vendored one.

    Paths are rendered with forward slashes because fugashi splits this string
    with :mod:`shlex`, where a Windows backslash is an escape character.
    """
    base = Path(dicdir) if dicdir is not None else dicdir_path()
    posix = base.resolve().as_posix()
    return f'-d "{posix}" -r "{posix}/{DICRC_NAME}"'


# ---------------------------------------------------------------------------
# The tagger
# ---------------------------------------------------------------------------


def _loaded_dictionary_paths(tagger: fugashi.Tagger) -> list[Path]:
    paths: list[Path] = []
    for info in tagger.dictionary_info:
        filename = info.get("filename") if isinstance(info, dict) else None
        if filename:
            paths.append(Path(str(filename)))
    return paths


def _assert_vendored_dictionary(tagger: fugashi.Tagger, dicdir: Path) -> None:
    """Prove MeCab loaded the vendored dictionary and nothing else."""
    loaded = _loaded_dictionary_paths(tagger)
    if not loaded:
        raise DictionaryNotFoundError(
            f"MeCab reported no loaded dictionary after being pointed at "
            f"{dicdir}; refusing to tokenize with an unidentified dictionary."
        )
    resolved = dicdir.resolve()
    stray = [
        path
        for path in loaded
        if resolved not in (path.resolve().parent, *path.resolve().parents)
    ]
    if stray:
        listing = ", ".join(str(path) for path in stray)
        raise DictionaryNotFoundError(
            f"MeCab loaded dictionaries outside the vendored directory "
            f"{resolved}: {listing}. That would mean lemmas and POS tags come "
            f"from a dictionary other than the recorded "
            f"{DICT_NAME} {DICT_VERSION}. {_acquisition_hint()}"
        )


def _assert_full_unidic_features(tagger: fugashi.Tagger, dicdir: Path) -> None:
    """Confirm fugashi's named 29-field wrapper is what this dictionary yields."""
    nodes = tagger(_PROBE_TEXT)
    if not nodes:
        raise DictionaryNotFoundError(
            f"The dictionary at {dicdir} tokenized {_PROBE_TEXT!r} into nothing; "
            "it is not a usable UniDic build."
        )
    feature = nodes[0].feature
    expected = fugashi.UnidicFeatures29._fields
    actual = getattr(type(feature), "_fields", ())
    if tuple(actual) != tuple(expected):
        raise DictionaryNotFoundError(
            f"The dictionary at {dicdir} produced {len(actual)} feature fields "
            f"({type(feature).__name__}), not the {len(expected)} of full "
            f"UniDic {DICT_VERSION}. Katagiri's Morph mapping is defined against "
            f"the 29-field layout. {_acquisition_hint()}"
        )


@lru_cache(maxsize=1)
def get_tagger() -> fugashi.Tagger:
    """The process-wide tagger, built once against the vendored dictionary.

    Cached because constructing it memory-maps the dictionary — per-call
    construction would dominate the cost of tokenizing a sentence. The cache is
    keyed on nothing: there is exactly one dictionary, by policy.
    """
    dicdir = dicdir_path()
    args = tagger_args(dicdir)
    try:
        tagger = fugashi.Tagger(args)
    except Exception as exc:  # fugashi raises bare RuntimeError/OSError subtypes
        raise DictionaryNotFoundError(
            f"MeCab could not initialize with the vendored dictionary "
            f"({args}): {exc}. {_acquisition_hint()}"
        ) from exc

    _assert_vendored_dictionary(tagger, dicdir)
    _assert_full_unidic_features(tagger, dicdir)
    _logger.debug("tagger built against vendored dictionary %s", dicdir)
    return tagger


def reset_tagger_cache() -> None:
    """Drop the cached tagger (tests, or after the dictionary is replaced)."""
    get_tagger.cache_clear()


# ---------------------------------------------------------------------------
# Morphs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Morph:
    """One UniDic morph.

    ``pos1``..``pos4`` are UniDic's four-level part of speech, coarsest first.
    ``infl_type``/``infl_form`` are UniDic's ``cType``/``cForm`` (conjugation
    class and conjugated form). Every optional field is ``None`` when UniDic
    said ``*`` or, for an unknown word, said nothing at all.
    """

    surface: str
    lemma: str
    lemma_reading: str | None
    pos1: str | None
    pos2: str | None
    pos3: str | None
    pos4: str | None
    infl_type: str | None
    infl_form: str | None
    is_unknown: bool


def _clean(value: Any) -> str | None:
    """UniDic's ``*``/empty sentinels become ``None``."""
    if value is None:
        return None
    text = str(value)
    if not text or text == _UNSPECIFIED:
        return None
    return text


def _morph_from_node(node: Any) -> Morph:
    feature = node.feature
    surface = node.surface
    return Morph(
        surface=surface,
        # Unknown words carry no lemma field at all; the surface is the only
        # honest lemma for them, and it keeps lemma-keyed grouping from merging
        # every unknown word together.
        lemma=_clean(feature.lemma) or surface,
        lemma_reading=_clean(feature.lForm),
        pos1=_clean(feature.pos1),
        pos2=_clean(feature.pos2),
        pos3=_clean(feature.pos3),
        pos4=_clean(feature.pos4),
        infl_type=_clean(feature.cType),
        infl_form=_clean(feature.cForm),
        is_unknown=bool(node.is_unk),
    )


def tokenize(text: str, *, tagger: fugashi.Tagger | None = None) -> list[Morph]:
    """Tokenize ``text`` into UniDic morphs, in surface order.

    Empty or whitespace-only input yields an empty list rather than an error:
    callers tokenize user-supplied lines and a blank line is not a failure.
    """
    if not text or not text.strip():
        return []
    active = tagger if tagger is not None else get_tagger()
    return [_morph_from_node(node) for node in active(text)]


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """ISO-8601 UTC to whole seconds, the format every CHECK in the schema wants."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fugashi_version() -> str:
    """fugashi's distribution version. (The module exposes no ``__version__``.)"""
    try:
        return package_version("fugashi")
    except PackageNotFoundError:  # pragma: no cover - installed by definition
        return "unknown"


def tokenizer_version(tagger: fugashi.Tagger | None = None) -> str:
    """Version string for the tokenizer *stack*, not just the Python wrapper.

    fugashi vendors its own MeCab build and exposes neither ``__version__`` nor
    MeCab's own version number, so the MeCab side is identified by the
    dictionary *format* version it reports for the loaded dictionary — the one
    MeCab-level compatibility number that is actually observable.
    """
    active = tagger if tagger is not None else get_tagger()
    formats = sorted(
        {
            str(info.get("version"))
            for info in active.dictionary_info
            if isinstance(info, dict) and info.get("version") is not None
        }
    )
    suffix = f"; mecab-dic-format {'/'.join(formats)}" if formats else ""
    return f"fugashi {fugashi_version()}{suffix}"


def manifest_dict_zip_sha256(*, manifest_path: Path | str | None = None) -> str:
    """The zip's expected digest, *read* from the manifest — never recomputed.

    Recording provenance must not cost a hash of the archive; the manifest is
    the committed authority on which bytes this build expects, and
    :func:`verify_dict` is where the actual file is checked against it.
    """
    entries = read_manifest(manifest_path)
    target = f"{Path(vendor_dir()).name}/{UNIDIC_VENDOR_DIR_NAME}/{UNIDIC_ZIP_NAME}"
    digest = entries.get(target)
    if digest is None:
        # Fall back to a suffix match so a manifest written with a different
        # relative prefix still resolves, but keep "exactly one expectation".
        tail = f"{UNIDIC_VENDOR_DIR_NAME}/{UNIDIC_ZIP_NAME}"
        matches = sorted(
            key for key in entries if key.casefold().endswith(tail.casefold())
        )
        if len(matches) != 1:
            raise TokenizerError(
                f"vendor/CHECKSUMS.sha256 has {len(matches)} entries for "
                f"{tail} (expected exactly one). The manifest is the authority "
                "on the vendored dictionary's identity, so provenance cannot be "
                "stamped without it."
            )
        digest = entries[matches[0]]
    return digest


def stamp_versions(
    conn: sqlite3.Connection, *, manifest_path: Path | str | None = None
) -> dict[str, str]:
    """Record tokenizer and dictionary provenance in ``metadata``.

    Returns the key/value pairs written. Overwrites any previous stamp — the
    table records what produced the *current* derived data, not a history — and
    is idempotent apart from ``stamped_ts``.

    The tagger is built as part of this: stamping "UniDic 3.1.0 produced these
    morphs" while the dictionary cannot even be loaded would be a lie recorded
    in the database.
    """
    tagger = get_tagger()
    values = {
        "tokenizer_name": TOKENIZER_NAME,
        "tokenizer_version": tokenizer_version(tagger),
        "dict_name": DICT_NAME,
        "dict_version": DICT_VERSION,
        "dict_zip_sha256": manifest_dict_zip_sha256(manifest_path=manifest_path),
        "stamped_ts": _utc_now(),
    }

    now = _utc_now()
    rows = [(key, value, now) for key, value in values.items()]
    # One transaction so a crash cannot leave half a provenance stamp, unless the
    # caller already owns one (then the stamp lands or rolls back with theirs).
    owns = not conn.in_transaction
    if owns:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO metadata(key, value, updated_ts) "
            "VALUES (?, ?, ?)",
            rows,
        )
    except Exception:
        if owns:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - rollback of a failed stamp
                pass
        raise
    if owns:
        conn.execute("COMMIT")
    return values


# ---------------------------------------------------------------------------
# Explicit verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """Outcome of :func:`verify_dict`."""

    zip_path: Path | None
    dicdir: Path | None
    expected_sha256: str | None
    actual_sha256: str | None
    dicdir_ok: bool
    tagger_ok: bool
    sample_morphs: int
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        lines = [
            f"dictionary : {DICT_NAME} {DICT_VERSION}",
            f"zip        : {self.zip_path}",
            f"dicdir     : {self.dicdir} ({'ok' if self.dicdir_ok else 'FAILED'})",
            f"expected   : {self.expected_sha256}",
            f"actual     : {self.actual_sha256}",
            f"tagger     : {'ok' if self.tagger_ok else 'FAILED'}"
            f" ({self.sample_morphs} morphs from the probe sentence)",
            f"result     : {'OK' if self.ok else 'FAILED'}",
        ]
        lines.extend(f"problem    : {problem}" for problem in self.problems)
        return "\n".join(lines)


def verify_dict(*, manifest_path: Path | str | None = None) -> VerifyReport:
    """Full, deliberate verification of the vendored dictionary.

    Unlike the load-time checks this **re-hashes the archive** against
    ``vendor/CHECKSUMS.sha256``, which is why it is a command the operator runs
    and not something every startup pays for. Collects problems into the report
    instead of raising, so one run says everything that is wrong.
    """
    problems: list[str] = []
    zip_path: Path | None = None
    dicdir: Path | None = None
    expected: str | None = None
    actual: str | None = None
    dicdir_ok = False
    tagger_ok = False
    sample = 0

    try:
        zip_path = dict_zip_path()
    except TokenizerError as exc:
        problems.append(str(exc))

    try:
        expected = manifest_dict_zip_sha256(manifest_path=manifest_path)
    except (TokenizerError, VendorFileError) as exc:
        problems.append(f"manifest: {exc}")

    if zip_path is not None:
        try:
            actual = verify_vendor_file(zip_path, manifest_path=manifest_path)
        except ChecksumError as exc:
            actual = exc.actual if getattr(exc, "actual", None) else None
            problems.append(f"checksum: {exc}")
        except VendorFileError as exc:
            problems.append(f"archive: {exc}")

    try:
        dicdir = dicdir_path()
        dicdir_ok = True
    except TokenizerError as exc:
        problems.append(str(exc))

    if dicdir_ok:
        try:
            morphs = tokenize(_SELFTEST_TEXT)
            sample = len(morphs)
            tagger_ok = sample > 0
            if not tagger_ok:
                problems.append(
                    f"tagger produced no morphs for {_SELFTEST_TEXT!r}."
                )
        except (TokenizerError, OSError, RuntimeError) as exc:
            problems.append(f"tagger: {exc}")

    return VerifyReport(
        zip_path=zip_path,
        dicdir=dicdir,
        expected_sha256=expected,
        actual_sha256=actual,
        dicdir_ok=dicdir_ok,
        tagger_ok=tagger_ok,
        sample_morphs=sample,
        problems=tuple(problems),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def format_morph(morph: Morph) -> str:
    """One tab-separated line per morph, for eyeballing selftest output."""
    pos = "/".join(part or _UNSPECIFIED for part in
                   (morph.pos1, morph.pos2, morph.pos3, morph.pos4))
    return (
        f"{morph.surface}\t{morph.lemma}\t{morph.lemma_reading or _UNSPECIFIED}\t"
        f"{pos}\t{morph.infl_type or _UNSPECIFIED}\t"
        f"{morph.infl_form or _UNSPECIFIED}\t"
        f"{'UNK' if morph.is_unknown else 'known'}"
    )


def _use_utf8_stderr() -> None:
    """Make Japanese printable on a cp1252 Windows console instead of crashing."""
    stream = sys.stderr
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - redirected stderr
            pass


def main(argv: list[str] | None = None) -> int:
    """``python -m katagiri.tokenizer [verify|selftest]``. Output goes to stderr."""
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.tokenizer",
        description=(
            "Inspect the vendored UniDic tokenizer. 'verify' re-hashes the "
            "archive against vendor/CHECKSUMS.sha256; 'selftest' tokenizes a "
            "sample sentence. Both write to stderr, like every other Katagiri "
            "diagnostic."
        ),
    )
    parser.add_argument("command", choices=("verify", "selftest"))
    args = parser.parse_args(argv)

    _use_utf8_stderr()

    if args.command == "verify":
        report = verify_dict()
        print(report.render(), file=sys.stderr)
        return 0 if report.ok else 1

    try:
        morphs = tokenize(_SELFTEST_TEXT)
    except TokenizerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"tokenizer  : {tokenizer_version()}", file=sys.stderr)
    print(f"dictionary : {DICT_NAME} {DICT_VERSION} at {dicdir_path()}", file=sys.stderr)
    print(f"text       : {_SELFTEST_TEXT}", file=sys.stderr)
    print(
        "surface\tlemma\tlemma_reading\tpos1/2/3/4\tinfl_type\tinfl_form\tflag",
        file=sys.stderr,
    )
    for morph in morphs:
        print(format_morph(morph), file=sys.stderr)
    return 0


__all__ = [
    "DICT_NAME",
    "DICT_VERSION",
    "METADATA_KEYS",
    "REQUIRED_DICT_FILES",
    "TOKENIZER_NAME",
    "DictionaryNotFoundError",
    "Morph",
    "TokenizerError",
    "VerifyReport",
    "dicdir_path",
    "dict_zip_path",
    "format_morph",
    "fugashi_version",
    "get_tagger",
    "main",
    "manifest_dict_zip_sha256",
    "reset_tagger_cache",
    "stamp_versions",
    "tagger_args",
    "tokenize",
    "tokenizer_version",
    "verify_dict",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
