r"""Consent-gated downloader for the vendored data files.

Fetches the vendor/ artifacts listed in :data:`ARTIFACTS` from their official
sources. This is the *second* deliberate exception to vendor/README.md's
"no runtime downloads, ever" rule (the first is the Irodori TOC PDF, see
``katagiri.irodori_import``): downloads run only

* from the installer wizard, after the operator answers "y" at an explicit
  consent prompt (never under ``--yes``, never at MCP request time), or
* from this module's own CLI, ``python -m katagiri.vendor_fetch``, which an
  operator runs by hand.

:func:`fetch_missing` is never auto-invoked: callers pass the explicit list
of missing/mismatched manifest paths and are responsible for having obtained
consent first (same contract as ``irodori_import.download_toc``).

Concurrency note: another process may legitimately be downloading into
vendor/ at the same time (e.g. an agent following SETUP_PROMPT.md). This
module therefore never deletes or re-downloads a file that already exists and
matches ``vendor/CHECKSUMS.sha256``, re-reads that manifest immediately
before every write, and edits it minimally (single-line replace or append).

Stdlib only: urllib.request, zipfile, tarfile, hashlib, shutil.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_RELPATH = "vendor/CHECKSUMS.sha256"

USER_AGENT = "katagiri-installer/1.0"

# Files the unpacked UniDic dicdir must contain (same list the tokenizer
# checks at load time).
UNIDIC_DICDIR_FILES = ("dicrc", "char.bin", "matrix.bin", "sys.dic", "unk.dic")
UNIDIC_ZIP_RELPATH = "vendor/unidic/unidic-3.1.0.zip"
UNIDIC_DICDIR_RELPATH = "vendor/unidic/unidic"

_JMDICT_RELEASES_API = (
    "https://api.github.com/repos/scriptin/jmdict-simplified/releases"
)
_JMDICT_TAG_PREFIX = "3.6.2"
_JMDICT_ASSET_RE = re.compile(r"^jmdict-eng-3\.6\.2\+.*\.json\.zip$")

_BCCWJ_PRIMARY_URL = (
    "https://repository.ninjal.ac.jp/record/3234/files/BCCWJ_frequencylist_suw_ver1_0.zip"
)
_BCCWJ_LANDING_URL = "https://ccd.ninjal.ac.jp/bccwj/freq-list.html"
_BCCWJ_ZIP_RE = re.compile(
    r"""https?://[^"'<>\s]+BCCWJ_frequencylist_suw_ver1_0\.zip"""
)

_PYPI_JREADABILITY_JSON = "https://pypi.org/pypi/jreadability/1.1.5/json"


@dataclass(frozen=True)
class Artifact:
    """One downloadable vendor file.

    ``kind`` drives the post-download sanity check: ``zip`` must open via
    zipfile, ``targz`` via tarfile, ``sqlite`` must carry the SQLite magic,
    ``text`` must be non-empty non-HTML.
    """

    relpath: str  # forward slashes, relative to the repo root
    kind: str  # "zip" | "targz" | "sqlite" | "text"
    approx_mb: float
    url: str | None = None  # static source URL; None -> resolved dynamically


ARTIFACTS: tuple[Artifact, ...] = (
    Artifact(UNIDIC_ZIP_RELPATH, "zip", 526.0,
             "https://cotonoha-dic.s3-ap-northeast-1.amazonaws.com/unidic-3.1.0.zip"),
    Artifact("vendor/jmdict/jmdict-eng-3.6.2.json.zip", "zip", 11.0),  # GitHub API
    Artifact("vendor/kanjium/accents.txt", "text", 10.0,
             "https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt"),
    Artifact("vendor/bccwj/BCCWJ_frequencylist_suw_ver1_0.zip", "zip", 8.0),  # primary + fallback
    Artifact("vendor/jlpt/n1-vocab-kanji-eng.anki", "sqlite", 2.0,
             "http://www.tanos.co.uk/jlpt/jlpt1/vocab/n1-vocab-kanji-eng.anki"),
    Artifact("vendor/jlpt/n2-vocab-kanji-eng.anki", "sqlite", 2.0,
             "http://www.tanos.co.uk/jlpt/jlpt2/vocab/n2-vocab-kanji-eng.anki"),
    Artifact("vendor/jlpt/n3-vocab-kanji-eng.anki", "sqlite", 2.0,
             "http://www.tanos.co.uk/jlpt/jlpt3/vocab/n3-vocab-kanji-eng.anki"),
    Artifact("vendor/jlpt/n4-vocab-kanji-eng.anki", "sqlite", 2.0,
             "http://www.tanos.co.uk/jlpt/jlpt4/vocab/n4-vocab-kanji-eng.anki"),
    Artifact("vendor/jlpt/n5-vocab-kanji-eng.anki", "sqlite", 2.0,
             "http://www.tanos.co.uk/jlpt/jlpt5/vocab/n5-vocab-kanji-eng.anki"),
    Artifact("vendor/jreadability/jreadability-1.1.5.tar.gz", "targz", 0.02),  # PyPI API
)

ARTIFACTS_BY_RELPATH: dict[str, Artifact] = {a.relpath: a for a in ARTIFACTS}


class VendorFetchError(Exception):
    """A download or verification failed in a way the operator must see."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no I/O)
# ---------------------------------------------------------------------------


def looks_like_html(head: bytes) -> bool:
    """True when the first bytes look like an HTML page (error page, login
    wall, or landing page served instead of the artifact)."""
    stripped = head.lstrip(b" \t\r\n")
    if stripped.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        stripped = stripped[3:].lstrip(b" \t\r\n")
    lowered = stripped[:32].lower()
    return lowered.startswith(b"<!doctype") or lowered.startswith(b"<html")


def is_sqlite_header(head: bytes) -> bool:
    return head.startswith(b"SQLite format 3")


def estimate_download_mb(relpaths: list[str]) -> float:
    """Sum of approximate sizes for the artifacts we know how to fetch."""
    return sum(
        ARTIFACTS_BY_RELPATH[p].approx_mb for p in relpaths if p in ARTIFACTS_BY_RELPATH
    )


def format_size_estimate(relpaths: list[str]) -> str:
    """Human size estimate for the consent prompt, e.g. ``"~530 MB"``."""
    mb = estimate_download_mb(relpaths)
    if mb >= 1000:
        return f"~{mb / 1024:.1f} GB"
    if mb >= 10:
        return f"~{int(round(mb / 10.0) * 10)} MB"
    return f"~{max(1, int(round(mb)))} MB"


def update_manifest_text(
    manifest_text: str, relpath: str, digest: str
) -> tuple[str, str | None]:
    """Return ``(new_text, old_digest)`` with ``relpath``'s line replaced.

    Preserves every other line (comments, order, trailing newline) byte for
    byte. When the path has no entry yet, appends one at the end and returns
    ``old_digest=None``. The path match is exact on the manifest's relative
    path column (two-space separator, forward slashes).
    """
    lines = manifest_text.splitlines(keepends=True)
    new_line_body = f"{digest.lower()}  {relpath}"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("  ", 1)
        if len(parts) != 2 or parts[1].strip() != relpath:
            continue
        old_digest = parts[0].strip().lower()
        eol = "\n" if line.endswith("\n") else ""
        lines[i] = new_line_body + eol
        return "".join(lines), old_digest
    # Append: keep the file newline-terminated.
    text = "".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + new_line_body + "\n", None


def read_manifest_digest(manifest_text: str, relpath: str) -> str | None:
    """The pinned digest for ``relpath``, or ``None`` when unlisted."""
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("  ", 1)
        if len(parts) == 2 and parts[1].strip() == relpath:
            return parts[0].strip().lower()
    return None


# ---------------------------------------------------------------------------
# Sanity checks (file-based, but no network)
# ---------------------------------------------------------------------------


def sanity_check_file(path: Path, kind: str) -> str | None:
    """Return ``None`` when ``path`` plausibly is what ``kind`` says,
    else a short problem description. Rejects HTML masquerading as data for
    every kind."""
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError as exc:
        return f"unreadable: {exc}"
    if not head:
        return "empty file"
    if looks_like_html(head):
        return "got an HTML page instead of the file (bad URL or upstream error page)"
    if kind == "zip":
        try:
            with zipfile.ZipFile(path) as zf:
                if not zf.namelist():
                    return "zip archive is empty"
        except zipfile.BadZipFile as exc:
            return f"not a valid zip: {exc}"
        return None
    if kind == "targz":
        try:
            with tarfile.open(path, "r:gz") as tf:
                tf.next()
        except (tarfile.TarError, OSError, EOFError) as exc:
            return f"not a valid tar.gz: {exc}"
        return None
    if kind == "sqlite":
        if not is_sqlite_header(head):
            return "not a SQLite database (missing 'SQLite format 3' header)"
        return None
    if kind == "text":
        return None  # non-empty + non-HTML already established
    return f"unknown artifact kind {kind!r}"


# ---------------------------------------------------------------------------
# Network (urllib only; callers hold operator consent)
# ---------------------------------------------------------------------------


def _open_url(url: str, *, timeout: float = 60):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


def _fetch_small(url: str, *, timeout: float = 60) -> bytes:
    with _open_url(url, timeout=timeout) as response:
        return response.read()


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _download_to(url: str, target: Path, *, timeout: float = 120) -> None:
    """Stream ``url`` to ``<target>.part``, then atomically rename.

    Prints coarse progress lines (every ~10% when the size is known, every
    ~25 MB otherwise) in the installer's plain-print style.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    printed_marks: set[int] = set()
    try:
        with _open_url(url, timeout=timeout) as response, part.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    mark = done * 10 // total  # 0..10
                    if mark and mark not in printed_marks:
                        printed_marks.add(mark)
                        print(
                            f"    {target.name}: {done / (1 << 20):.0f} / "
                            f"{total / (1 << 20):.0f} MB ({mark * 10}%)"
                        )
                else:
                    mark = done // (25 << 20)
                    if mark and mark not in printed_marks:
                        printed_marks.add(mark)
                        print(f"    {target.name}: {done / (1 << 20):.0f} MB downloaded")
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _download_with_retries(url: str, target: Path, *, attempts: int = 3) -> None:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            _download_to(url, target)
            return
        except (OSError, ValueError) as exc:  # URLError subclasses OSError
            last = exc
            if attempt < attempts:
                print(f"    attempt {attempt}/{attempts} failed ({exc}); retrying...")
    raise VendorFetchError(f"download failed after {attempts} attempts: {url}: {last}")


# --- dynamic URL resolvers -------------------------------------------------


def _resolve_jmdict_url() -> str:
    data = json.loads(_fetch_small(_JMDICT_RELEASES_API).decode("utf-8"))
    for release in data:
        tag = str(release.get("tag_name", ""))
        if not tag.lstrip("v").startswith(_JMDICT_TAG_PREFIX):
            continue
        for asset in release.get("assets", ()):
            name = str(asset.get("name", ""))
            if _JMDICT_ASSET_RE.match(name):
                return str(asset["browser_download_url"])
    raise VendorFetchError(
        "could not find a jmdict-eng-3.6.2+*.json.zip asset in "
        "scriptin/jmdict-simplified releases"
    )


def _resolve_bccwj_urls() -> list[str]:
    """Primary DOI-record URL first, then whatever the landing page links."""
    urls = [_BCCWJ_PRIMARY_URL]
    try:
        page = _fetch_small(_BCCWJ_LANDING_URL).decode("utf-8", errors="replace")
        found = _BCCWJ_ZIP_RE.search(page)
        if found and found.group(0) not in urls:
            urls.append(found.group(0))
    except OSError:
        pass  # landing page unreachable; the primary URL may still work
    return urls


def _resolve_jreadability_url() -> str:
    data = json.loads(_fetch_small(_PYPI_JREADABILITY_JSON).decode("utf-8"))
    for entry in data.get("urls", ()):
        if entry.get("packagetype") == "sdist" and str(entry.get("filename", "")).endswith(
            ".tar.gz"
        ):
            return str(entry["url"])
    raise VendorFetchError("PyPI JSON for jreadability 1.1.5 lists no sdist")


def candidate_urls(artifact: Artifact) -> list[str]:
    """Source URLs to try in order for ``artifact`` (may hit the network to
    resolve API-indirected sources)."""
    if artifact.url is not None:
        return [artifact.url]
    if artifact.relpath == "vendor/jmdict/jmdict-eng-3.6.2.json.zip":
        return [_resolve_jmdict_url()]
    if artifact.relpath == "vendor/bccwj/BCCWJ_frequencylist_suw_ver1_0.zip":
        return _resolve_bccwj_urls()
    if artifact.relpath == "vendor/jreadability/jreadability-1.1.5.tar.gz":
        return [_resolve_jreadability_url()]
    raise VendorFetchError(f"no download source known for {artifact.relpath}")


# ---------------------------------------------------------------------------
# UniDic unpack
# ---------------------------------------------------------------------------


def unidic_dicdir_valid(dicdir: Path) -> bool:
    return all((dicdir / name).is_file() for name in UNIDIC_DICDIR_FILES)


def unpack_unidic(zip_path: Path, dicdir: Path) -> str:
    """Unpack the UniDic zip so ``dicdir`` holds dicrc/char.bin/... directly.

    The upstream zip nests the dictionary under a top-level folder; the
    member containing ``dicrc`` is located by inspection rather than by an
    assumed folder name. Skips (returning a note) when ``dicdir`` is already
    valid -- possibly produced by a concurrent setup process.
    """
    if unidic_dicdir_valid(dicdir):
        return f"{dicdir} already unpacked and complete; unpack skipped"
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        prefix = None
        for name in names:
            posix = name.replace("\\", "/")
            if posix == "dicrc" or posix.endswith("/dicrc"):
                prefix = posix[: -len("dicrc")]
                break
        if prefix is None:
            raise VendorFetchError(f"{zip_path.name} contains no dicrc; not a UniDic zip")
        members = [n for n in names if n.replace("\\", "/").startswith(prefix)]
        with tempfile.TemporaryDirectory(dir=str(dicdir.parent)) as tmp:
            tmp_path = Path(tmp)
            zf.extractall(tmp_path, members=members)
            src = tmp_path / Path(prefix.rstrip("/")) if prefix else tmp_path
            dicdir.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                dest = dicdir / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
    if not unidic_dicdir_valid(dicdir):
        missing = [n for n in UNIDIC_DICDIR_FILES if not (dicdir / n).is_file()]
        raise VendorFetchError(
            f"unpacked {zip_path.name} but {dicdir} is still missing: {', '.join(missing)}"
        )
    return f"unpacked to {dicdir}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _verify_or_pin_checksum(repo_root: Path, relpath: str) -> str | None:
    """Check ``relpath`` against the manifest; pin the new digest on drift.

    The manifest is re-read immediately before any write (another process may
    be editing it concurrently) and edited minimally. Returns ``None`` on
    success (matched, or pinned with a printed old->new notice), else a
    problem string. The caller has already sanity-checked the bytes, so a
    mismatch here is treated as deliberate upstream drift, not garbage.
    """
    manifest = repo_root / MANIFEST_RELPATH
    actual = _sha256_of(repo_root / relpath)
    manifest_text = manifest.read_text(encoding="utf-8")
    expected = read_manifest_digest(manifest_text, relpath)
    if expected == actual:
        return None
    # Re-read fresh right before writing: minimal window for clobbering a
    # concurrent editor's changes to *other* lines.
    manifest_text = manifest.read_text(encoding="utf-8")
    new_text, old = update_manifest_text(manifest_text, relpath, actual)
    manifest.write_text(new_text, encoding="utf-8", newline="")
    if old is None:
        print(f"    {relpath}: no manifest entry; pinned {actual}")
    else:
        print(
            f"    {relpath}: upstream bytes changed; manifest updated "
            f"{old[:12]}... -> {actual[:12]}..."
        )
    return None


def _already_good(repo_root: Path, relpath: str) -> bool:
    """True when the file exists and matches the manifest (e.g. a concurrent
    process finished it first) -- such files are never touched."""
    path = repo_root / relpath
    if not path.is_file():
        return False
    manifest = repo_root / MANIFEST_RELPATH
    try:
        expected = read_manifest_digest(manifest.read_text(encoding="utf-8"), relpath)
    except OSError:
        return False
    return expected is not None and _sha256_of(path) == expected


def fetch_one(repo_root: Path, relpath: str) -> str:
    """Fetch a single artifact. Returns a human status line; raises
    :class:`VendorFetchError` on failure."""
    artifact = ARTIFACTS_BY_RELPATH.get(relpath)
    if artifact is None:
        raise VendorFetchError(
            f"no download source known for {relpath}; see vendor/README.md"
        )
    target = repo_root / relpath

    if _already_good(repo_root, relpath):
        note = "already present and verified; download skipped"
    else:
        last_error: str | None = None
        note = ""
        for url in candidate_urls(artifact):
            print(f"  Downloading {relpath}")
            print(f"    from {url}")
            _download_with_retries(url, target)
            problem = sanity_check_file(target, artifact.kind)
            if problem is None:
                last_error = None
                break
            target.unlink(missing_ok=True)
            last_error = f"{url}: {problem}"
            print(f"    rejected: {problem}")
        if last_error is not None:
            raise VendorFetchError(f"{relpath}: {last_error}")
        problem = _verify_or_pin_checksum(repo_root, relpath)
        if problem is not None:
            raise VendorFetchError(f"{relpath}: {problem}")
        note = "downloaded and verified"

    if relpath == UNIDIC_ZIP_RELPATH:
        unpack_note = unpack_unidic(target, repo_root / UNIDIC_DICDIR_RELPATH)
        note = f"{note}; {unpack_note}"
    return note


def fetch_missing(repo_root: Path, relpaths: list[str]) -> list[str]:
    """Fetch every artifact in ``relpaths`` (manifest-relative, forward
    slashes). Returns failure messages; an empty list means everything either
    downloaded and verified or was already present and correct.

    Never invoked automatically: the caller must hold explicit operator
    consent (installer prompt or the ``python -m katagiri.vendor_fetch`` CLI).
    """
    failures: list[str] = []
    for relpath in relpaths:
        try:
            note = fetch_one(repo_root, relpath)
        except VendorFetchError as exc:
            failures.append(str(exc))
            print(f"  FAILED: {exc}")
        except (OSError, ValueError) as exc:  # URL resolution / disk errors
            failures.append(f"{relpath}: {exc}")
            print(f"  FAILED: {relpath}: {exc}")
        else:
            print(f"  {relpath}: {note}")
    return failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _missing_relpaths(repo_root: Path) -> list[str]:
    return [
        a.relpath for a in ARTIFACTS if not _already_good(repo_root, a.relpath)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m katagiri.vendor_fetch",
        description=(
            "Download missing vendor data files from their official sources. "
            "Running this command IS the consent step: nothing in Katagiri "
            "invokes it automatically."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "manifest-relative path (e.g. vendor/kanjium/accents.txt) to fetch; "
            "repeatable. Default: every known artifact that is missing or fails "
            "its checksum."
        ),
    )
    args = parser.parse_args(argv)

    if args.only:
        targets = [p.replace("\\", "/") for p in args.only]
        unknown = [p for p in targets if p not in ARTIFACTS_BY_RELPATH]
        if unknown:
            parser.error(
                "unknown artifact path(s): " + ", ".join(unknown)
                + "; known: " + ", ".join(sorted(ARTIFACTS_BY_RELPATH))
            )
    else:
        targets = _missing_relpaths(REPO_ROOT)
        if not targets:
            print("All known vendor artifacts are present and verified.")
            return 0

    print(f"Fetching {len(targets)} artifact(s), {format_size_estimate(targets)} total.")
    failures = fetch_missing(REPO_ROOT, targets)
    if failures:
        print(f"{len(failures)} artifact(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
