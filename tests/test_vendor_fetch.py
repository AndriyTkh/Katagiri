"""Unit tests for the pure/offline parts of ``katagiri.vendor_fetch``.

No network, ever: only the URL map's shape, the manifest line editing, the
sanity checks on tiny fixture bytes, and the UniDic unpack on a synthetic zip.
The actual downloads are exercised by an operator running the installer, not
by this suite.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from katagiri import vendor_fetch as vf

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "vendor" / "CHECKSUMS.sha256"


def _manifest_relpaths() -> list[str]:
    relpaths = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("  ", 1)
        if len(parts) == 2:
            relpaths.append(parts[1].strip())
    return relpaths


# ---------------------------------------------------------------------------
# URL map completeness vs CHECKSUMS.sha256
# ---------------------------------------------------------------------------


def test_every_artifact_has_a_manifest_entry():
    manifest = set(_manifest_relpaths())
    missing = [a.relpath for a in vf.ARTIFACTS if a.relpath not in manifest]
    assert not missing, f"artifacts without a pinned checksum: {missing}"


def test_every_downloadable_manifest_entry_has_an_artifact():
    """Every manifest entry is either fetchable via vendor_fetch or one of the
    known hand/script-acquired families (taekim via fetch_taekim.py, irodori
    hand-acquired + TOC via irodori_import)."""
    exempt_prefixes = ("vendor/taekim/", "vendor/irodori/")
    uncovered = [
        p
        for p in _manifest_relpaths()
        if p not in vf.ARTIFACTS_BY_RELPATH and not p.startswith(exempt_prefixes)
    ]
    assert not uncovered, f"manifest entries vendor_fetch cannot fetch: {uncovered}"


def test_artifact_kinds_and_static_urls_are_wellformed():
    for artifact in vf.ARTIFACTS:
        assert artifact.kind in ("zip", "targz", "sqlite", "text"), artifact
        assert artifact.approx_mb > 0, artifact
        assert "\\" not in artifact.relpath, artifact
        if artifact.url is not None:
            assert artifact.url.startswith(("http://", "https://")), artifact


def test_jlpt_artifacts_cover_all_five_levels():
    jlpt = sorted(p for p in vf.ARTIFACTS_BY_RELPATH if p.startswith("vendor/jlpt/"))
    assert jlpt == [f"vendor/jlpt/n{n}-vocab-kanji-eng.anki" for n in range(1, 6)]
    for n in range(1, 6):
        artifact = vf.ARTIFACTS_BY_RELPATH[f"vendor/jlpt/n{n}-vocab-kanji-eng.anki"]
        assert artifact.url == (
            f"http://www.tanos.co.uk/jlpt/jlpt{n}/vocab/n{n}-vocab-kanji-eng.anki"
        )


# ---------------------------------------------------------------------------
# Manifest line editing
# ---------------------------------------------------------------------------

_SAMPLE = (
    "# header comment\n"
    "aaaa  vendor/x/one.zip\n"
    "# mid comment\n"
    "bbbb  vendor/y/two.txt\n"
)


def test_update_manifest_text_replaces_in_place_preserving_everything_else():
    new_text, old = vf.update_manifest_text(_SAMPLE, "vendor/x/one.zip", "CCCC")
    assert old == "aaaa"
    assert new_text == (
        "# header comment\n"
        "cccc  vendor/x/one.zip\n"
        "# mid comment\n"
        "bbbb  vendor/y/two.txt\n"
    )


def test_update_manifest_text_appends_when_path_is_new():
    new_text, old = vf.update_manifest_text(_SAMPLE, "vendor/z/new.bin", "dddd")
    assert old is None
    assert new_text == _SAMPLE + "dddd  vendor/z/new.bin\n"


def test_update_manifest_text_appends_newline_terminated_on_unterminated_file():
    new_text, old = vf.update_manifest_text("aaaa  vendor/x/one.zip", "vendor/z/n.b", "ffff")
    assert old is None
    assert new_text == "aaaa  vendor/x/one.zip\nffff  vendor/z/n.b\n"


def test_update_manifest_text_does_not_match_substring_paths():
    text = "aaaa  vendor/x/one.zip.bak\n"
    new_text, old = vf.update_manifest_text(text, "vendor/x/one.zip", "eeee")
    assert old is None
    assert new_text == text + "eeee  vendor/x/one.zip\n"


def test_read_manifest_digest():
    assert vf.read_manifest_digest(_SAMPLE, "vendor/y/two.txt") == "bbbb"
    assert vf.read_manifest_digest(_SAMPLE, "vendor/absent") is None
    assert vf.read_manifest_digest("# only comments\n", "vendor/x") is None


# ---------------------------------------------------------------------------
# Sanity checks on tiny fixture bytes
# ---------------------------------------------------------------------------


def test_looks_like_html():
    assert vf.looks_like_html(b"<!DOCTYPE html><html>...")
    assert vf.looks_like_html(b"  \n<HTML lang='en'>")
    assert vf.looks_like_html(b"\xef\xbb\xbf<!doctype html>")
    assert not vf.looks_like_html(b"PK\x03\x04zipdata")
    assert not vf.looks_like_html(b"SQLite format 3\x00")
    assert not vf.looks_like_html(b"")


def test_is_sqlite_header():
    assert vf.is_sqlite_header(b"SQLite format 3\x00" + b"\x00" * 20)
    assert not vf.is_sqlite_header(b"<!DOCTYPE html>")


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_sanity_check_rejects_html_for_every_kind(tmp_path):
    page = _write(tmp_path, "page.zip", b"<!DOCTYPE html><html>oops</html>")
    for kind in ("zip", "targz", "sqlite", "text"):
        problem = vf.sanity_check_file(page, kind)
        assert problem is not None and "HTML" in problem


def test_sanity_check_rejects_empty_file(tmp_path):
    empty = _write(tmp_path, "empty.txt", b"")
    assert vf.sanity_check_file(empty, "text") == "empty file"


def test_sanity_check_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("inner.txt", "hello")
    good = _write(tmp_path, "good.zip", buf.getvalue())
    assert vf.sanity_check_file(good, "zip") is None
    bad = _write(tmp_path, "bad.zip", b"PK\x03\x04 truncated garbage")
    assert vf.sanity_check_file(bad, "zip") is not None


def test_sanity_check_targz(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        payload = b"coefficients"
        info = tarfile.TarInfo("pkg/data.py")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    good = _write(tmp_path, "good.tar.gz", buf.getvalue())
    assert vf.sanity_check_file(good, "targz") is None
    bad = _write(tmp_path, "bad.tar.gz", b"\x1f\x8b not really gzip tar")
    assert vf.sanity_check_file(bad, "targz") is not None


def test_sanity_check_sqlite(tmp_path):
    good = _write(tmp_path, "deck.anki", b"SQLite format 3\x00" + b"\x00" * 100)
    assert vf.sanity_check_file(good, "sqlite") is None
    bad = _write(tmp_path, "notdb.anki", b"junk bytes here")
    assert vf.sanity_check_file(bad, "sqlite") is not None


def test_sanity_check_text(tmp_path):
    good = _write(tmp_path, "accents.txt", "漢字\t1\n".encode("utf-8"))
    assert vf.sanity_check_file(good, "text") is None


# ---------------------------------------------------------------------------
# Size estimate for the consent prompt
# ---------------------------------------------------------------------------


def test_format_size_estimate_scales():
    assert vf.format_size_estimate(["vendor/kanjium/accents.txt"]) == "~10 MB"
    assert vf.format_size_estimate(
        ["vendor/jreadability/jreadability-1.1.5.tar.gz"]
    ) == "~1 MB"
    everything = [a.relpath for a in vf.ARTIFACTS]
    assert vf.format_size_estimate(everything) == "~570 MB"
    assert vf.estimate_download_mb(["vendor/unknown"]) == 0


# ---------------------------------------------------------------------------
# UniDic unpack (synthetic zip, no network)
# ---------------------------------------------------------------------------


def _fake_unidic_zip(tmp_path: Path, prefix: str) -> Path:
    zip_path = tmp_path / "unidic-3.1.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in vf.UNIDIC_DICDIR_FILES:
            zf.writestr(f"{prefix}{name}", f"fake {name}")
    return zip_path


def test_unpack_unidic_flattens_the_top_level_folder(tmp_path):
    zip_path = _fake_unidic_zip(tmp_path, "unidic-3.1.0/")
    dicdir = tmp_path / "unidic"
    note = vf.unpack_unidic(zip_path, dicdir)
    assert "unpacked" in note
    assert vf.unidic_dicdir_valid(dicdir)
    assert (dicdir / "dicrc").read_text() == "fake dicrc"


def test_unpack_unidic_handles_files_at_zip_root(tmp_path):
    zip_path = _fake_unidic_zip(tmp_path, "")
    dicdir = tmp_path / "unidic"
    vf.unpack_unidic(zip_path, dicdir)
    assert vf.unidic_dicdir_valid(dicdir)


def test_unpack_unidic_skips_an_already_valid_dicdir(tmp_path):
    dicdir = tmp_path / "unidic"
    dicdir.mkdir()
    for name in vf.UNIDIC_DICDIR_FILES:
        (dicdir / name).write_text("existing")
    note = vf.unpack_unidic(tmp_path / "missing.zip", dicdir)  # zip never opened
    assert "skipped" in note
    assert (dicdir / "dicrc").read_text() == "existing"


def test_unpack_unidic_rejects_a_zip_without_dicrc(tmp_path):
    zip_path = tmp_path / "notdic.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "nope")
    with pytest.raises(vf.VendorFetchError):
        vf.unpack_unidic(zip_path, tmp_path / "unidic")
