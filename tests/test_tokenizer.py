"""Tokenizer tests, run against the **real vendored UniDic 3.1.0**.

There is no mock tagger here on purpose: the thing under test is the mapping
from MeCab's feature CSV onto :class:`~katagiri.tokenizer.Morph`, and a fake
dictionary would only ever confirm the mapping this module already believes in.
The dictionary is gitignored, so the whole module skips when it is absent (a CI
checkout without ``vendor/`` data) rather than failing.

The one thing deliberately *not* exercised end-to-end is
:func:`~katagiri.tokenizer.verify_dict`'s real re-hash of the ~500 MB archive:
that is what the ``verify`` CLI command is for. The mismatch and happy paths are
covered against a stand-in archive instead, which is where the interesting logic
(manifest lookup, problem collection) lives.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from katagiri import db
from katagiri import tokenizer as tok

fugashi = pytest.importorskip("fugashi")


def _dicdir_available() -> bool:
    try:
        tok.dicdir_path()
    except tok.TokenizerError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _dicdir_available(),
    reason=(
        "vendored UniDic 3.1.0 is absent (vendor/unidic/unidic); see "
        "vendor/README.md"
    ),
)

TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A katakana nonce word: phonotactically plausible, absent from UniDic, so MeCab
# must route it through unk-format (six feature fields, no lemma).
NONCE = "ズヴォルグ"


@pytest.fixture
def conn(tmp_path):
    """A migrated database at an explicit path (so no config/LOCALAPPDATA)."""
    connection = db.open_db(tmp_path / "katagiri.db")
    yield connection
    connection.close()


@pytest.fixture
def clean_tagger_cache():
    """Guarantee a test that clears the cached tagger does not leak that."""
    yield
    tok.reset_tagger_cache()


# ---------------------------------------------------------------------------
# Feature mapping
# ---------------------------------------------------------------------------


def test_feature_layout_is_fugashi_named_29_fields():
    """The mapping uses *named* features; this is the assumption behind that.

    If a future fugashi or dictionary hands back UnidicFeatures17/26, every
    lemma and POS in the database would silently shift, so the layout is pinned
    here rather than discovered in production.
    """
    node = tok.get_tagger()("日本語")[0]
    assert type(node.feature)._fields == fugashi.UnidicFeatures29._fields
    assert node.feature._fields[:8] == (
        "pos1", "pos2", "pos3", "pos4", "cType", "cForm", "lForm", "lemma",
    )


def test_tokenize_known_sentence():
    morphs = tok.tokenize("今日は良い天気ですね")

    assert [m.surface for m in morphs] == [
        "今日", "は", "良い", "天気", "です", "ね",
    ]
    assert all(not m.is_unknown for m in morphs)
    assert [m.pos1 for m in morphs] == [
        "名詞", "助詞", "形容詞", "名詞", "助動詞", "助詞",
    ]
    # pos4 is '*' for all of these and must surface as None, not as the sentinel.
    assert all(m.pos4 is None for m in morphs)


def test_conjugated_verb_maps_to_lemma_and_lemma_reading():
    morphs = tok.tokenize("食べました")

    assert [m.surface for m in morphs] == ["食べ", "まし", "た"]

    taberu = morphs[0]
    assert taberu.lemma == "食べる"
    # lForm (lemma reading), not kana (surface reading, which is タベ).
    assert taberu.lemma_reading == "タベル"
    assert (taberu.pos1, taberu.pos2) == ("動詞", "一般")
    assert taberu.infl_type == "下一段-バ行"
    assert taberu.infl_form == "連用形-一般"
    assert taberu.is_unknown is False

    masu = morphs[1]
    assert masu.lemma == "ます"
    assert masu.lemma_reading == "マス"
    assert masu.infl_type == "助動詞-マス"


def test_suru_verb_lemma_is_dictionary_form():
    morphs = tok.tokenize("ジョギングした")

    assert [m.surface for m in morphs] == ["ジョギング", "し", "た"]
    # UniDic's lemma for し is the kanji dictionary form.
    assert morphs[1].lemma == "為る"
    assert morphs[1].lemma_reading == "スル"
    assert morphs[1].infl_type == "サ行変格"


def test_unknown_word_is_flagged_and_falls_back_to_surface():
    morphs = tok.tokenize(NONCE)

    assert len(morphs) == 1
    unknown = morphs[0]
    assert unknown.is_unknown is True
    assert unknown.surface == NONCE
    # unk-format carries only six fields: POS survives, lemma and reading do not.
    assert unknown.lemma == NONCE, "unknown lemma must not collapse to None/''"
    assert unknown.lemma_reading is None
    assert unknown.infl_type is None
    assert unknown.infl_form is None
    assert unknown.pos1 == "名詞"


def test_known_and_unknown_in_one_sentence():
    morphs = tok.tokenize(f"{NONCE}を食べた")

    by_surface = {m.surface: m for m in morphs}
    assert by_surface[NONCE].is_unknown is True
    assert by_surface["食べ"].is_unknown is False
    assert by_surface["食べ"].lemma == "食べる"


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_yields_no_morphs(text):
    assert tok.tokenize(text) == []


def test_morph_is_immutable():
    morph = tok.tokenize("猫")[0]
    with pytest.raises(Exception):
        morph.surface = "犬"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tagger construction: cached, and never the pip-installed dictionary
# ---------------------------------------------------------------------------


def test_tagger_is_cached(clean_tagger_cache):
    first = tok.get_tagger()
    assert tok.get_tagger() is first
    tok.reset_tagger_cache()
    assert tok.get_tagger() is not first


def test_tagger_args_point_at_the_vendored_dictionary():
    dicdir = tok.dicdir_path()
    args = tok.tagger_args()

    assert dicdir.resolve().as_posix() in args
    assert "vendor/unidic/unidic" in args.replace("\\", "/")
    assert args.startswith("-d ")
    # -r must be explicit too, or MeCab would look for a system mecabrc.
    assert f'-r "{dicdir.resolve().as_posix()}/dicrc"' in args


def test_loaded_dictionary_is_the_vendored_one_not_an_installed_unidic():
    """No silent fallback: MeCab's own report must name the vendored path."""
    dicdir = tok.dicdir_path().resolve()
    loaded = [info["filename"] for info in tok.get_tagger().dictionary_info]

    assert loaded, "MeCab reported no loaded dictionary"
    for filename in loaded:
        normalized = str(filename).replace("\\", "/").casefold()
        assert dicdir.as_posix().casefold() in normalized
        assert "site-packages" not in normalized


def test_tagger_rejects_a_dictionary_outside_the_vendor_dir(monkeypatch, tmp_path):
    """A dicdir that is not the vendored one is a loud failure, not a fallback."""
    fake = tmp_path / "vendor" / "unidic" / "unidic"
    fake.mkdir(parents=True)
    for name in tok.REQUIRED_DICT_FILES:
        (fake / name).write_text("f[28]\n", encoding="utf-8")

    monkeypatch.setattr(tok, "dicdir_path", lambda: fake)
    tok.reset_tagger_cache()
    try:
        with pytest.raises(tok.DictionaryNotFoundError) as exc:
            tok.get_tagger()
    finally:
        tok.reset_tagger_cache()
    assert "vendor/README.md" in str(exc.value)


# ---------------------------------------------------------------------------
# Missing / wrong dictionary
# ---------------------------------------------------------------------------


def test_missing_dicdir_raises_pointing_at_vendor_readme(monkeypatch, tmp_path):
    monkeypatch.setattr(tok, "vendor_dir", lambda: tmp_path / "vendor")

    with pytest.raises(tok.DictionaryNotFoundError) as exc:
        tok.dicdir_path()

    message = str(exc.value)
    assert str(tmp_path / "vendor" / "unidic" / "unidic") in message
    assert "vendor/README.md" in message
    assert "unidic-3.1.0.zip" in message


def test_incomplete_dicdir_names_the_missing_files(monkeypatch, tmp_path):
    dicdir = tmp_path / "vendor" / "unidic" / "unidic"
    dicdir.mkdir(parents=True)
    (dicdir / "dicrc").write_text("; f[28]: lemma_id\n", encoding="utf-8")
    monkeypatch.setattr(tok, "vendor_dir", lambda: tmp_path / "vendor")

    with pytest.raises(tok.DictionaryNotFoundError) as exc:
        tok.dicdir_path()

    message = str(exc.value)
    assert "sys.dic" in message and "matrix.bin" in message
    # The one file that *is* present must not be reported missing.
    assert "dicrc" not in message


def test_unidic_lite_style_dicrc_is_rejected(monkeypatch, tmp_path):
    """A 17-field dicrc is the wrong dictionary, not a degraded one."""
    dicdir = tmp_path / "vendor" / "unidic" / "unidic"
    dicdir.mkdir(parents=True)
    for name in tok.REQUIRED_DICT_FILES:
        (dicdir / name).write_bytes(b"")
    (dicdir / "dicrc").write_text("; f[0]: pos1\n; f[16]: fForm\n", encoding="utf-8")
    monkeypatch.setattr(tok, "vendor_dir", lambda: tmp_path / "vendor")

    with pytest.raises(tok.DictionaryNotFoundError, match="f\\[28\\]"):
        tok.dicdir_path()


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def _metadata(conn) -> dict[str, tuple[str, str]]:
    return {
        row["key"]: (row["value"], row["updated_ts"])
        for row in conn.execute("SELECT key, value, updated_ts FROM metadata")
    }


def test_stamp_versions_writes_every_key(conn):
    written = tok.stamp_versions(conn)

    assert set(written) == set(tok.METADATA_KEYS)
    stored = _metadata(conn)
    assert set(stored) == set(tok.METADATA_KEYS)

    assert stored["tokenizer_name"][0] == "fugashi"
    assert stored["dict_name"][0] == "unidic"
    assert stored["dict_version"][0] == "3.1.0"
    assert stored["tokenizer_version"][0].startswith("fugashi ")
    assert tok.fugashi_version() in stored["tokenizer_version"][0]
    assert SHA256_RE.match(stored["dict_zip_sha256"][0])
    assert TS_RE.match(stored["stamped_ts"][0])
    for _value, updated_ts in stored.values():
        assert TS_RE.match(updated_ts)


def test_stamp_versions_is_an_idempotent_overwrite(conn):
    first = tok.stamp_versions(conn)
    second = tok.stamp_versions(conn)

    # No duplicate rows: metadata records the current state, not a history.
    assert len(_metadata(conn)) == len(tok.METADATA_KEYS)
    for key in tok.METADATA_KEYS:
        if key == "stamped_ts":
            continue
        assert first[key] == second[key], key
    assert _metadata(conn)["dict_version"][0] == "3.1.0"


def test_stamp_versions_joins_a_caller_transaction(conn):
    """Inside someone else's transaction the stamp rolls back with it."""
    conn.execute("BEGIN IMMEDIATE")
    tok.stamp_versions(conn)
    conn.execute("ROLLBACK")

    assert _metadata(conn) == {}


def test_zip_digest_is_read_from_the_manifest_not_recomputed(monkeypatch, tmp_path):
    """The 500 MB archive must not be hashed just to record provenance."""
    manifest = tmp_path / "CHECKSUMS.sha256"
    bogus = "a" * 64
    manifest.write_text(
        f"# fake manifest\n{bogus} vendor/unidic/{tok.UNIDIC_ZIP_NAME}\n",
        encoding="utf-8",
    )

    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("stamping provenance must not hash the archive")

    monkeypatch.setattr(hashlib, "sha256", explode)

    assert tok.manifest_dict_zip_sha256(manifest_path=manifest) == bogus


def test_manifest_digest_matches_the_committed_manifest():
    digest = tok.manifest_dict_zip_sha256()
    assert SHA256_RE.match(digest)

    manifest_text = (
        tok.vendor_dir() / "CHECKSUMS.sha256"
    ).read_text(encoding="utf-8")
    assert digest in manifest_text
    assert tok.UNIDIC_ZIP_NAME in manifest_text


# ---------------------------------------------------------------------------
# verify_dict
# ---------------------------------------------------------------------------


def _stand_in_archive(tmp_path, body: bytes) -> tuple:
    """A fake vendor tree holding a tiny 'archive' plus its manifest."""
    zip_path = tmp_path / "vendor" / "unidic" / tok.UNIDIC_ZIP_NAME
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(body)
    manifest = tmp_path / "CHECKSUMS.sha256"
    return zip_path, manifest


def test_verify_dict_reports_ok_for_a_matching_archive(monkeypatch, tmp_path):
    body = b"pretend this is unidic-3.1.0.zip"
    zip_path, manifest = _stand_in_archive(tmp_path, body)
    manifest.write_text(
        f"{hashlib.sha256(body).hexdigest()} vendor/unidic/{tok.UNIDIC_ZIP_NAME}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tok, "dict_zip_path", lambda: zip_path)

    report = tok.verify_dict(manifest_path=manifest)

    assert report.ok, report.problems
    assert report.expected_sha256 == report.actual_sha256
    assert report.dicdir_ok and report.tagger_ok
    # The real dictionary is still what got tokenized.
    assert report.sample_morphs == 6
    assert "OK" in report.render()


def test_verify_dict_reports_a_digest_mismatch(monkeypatch, tmp_path):
    zip_path, manifest = _stand_in_archive(tmp_path, b"tampered bytes")
    manifest.write_text(
        f"{'b' * 64} vendor/unidic/{tok.UNIDIC_ZIP_NAME}\n", encoding="utf-8"
    )
    monkeypatch.setattr(tok, "dict_zip_path", lambda: zip_path)

    report = tok.verify_dict(manifest_path=manifest)

    assert not report.ok
    assert any("checksum" in problem for problem in report.problems)
    assert report.expected_sha256 == "b" * 64
    assert report.actual_sha256 == hashlib.sha256(b"tampered bytes").hexdigest()
    assert "FAILED" in report.render()


def test_verify_dict_reports_a_missing_archive(monkeypatch, tmp_path):
    missing = tmp_path / "vendor" / "unidic" / tok.UNIDIC_ZIP_NAME
    manifest = tmp_path / "CHECKSUMS.sha256"
    manifest.write_text(
        f"{'c' * 64} vendor/unidic/{tok.UNIDIC_ZIP_NAME}\n", encoding="utf-8"
    )
    monkeypatch.setattr(tok, "dict_zip_path", lambda: missing)

    report = tok.verify_dict(manifest_path=manifest)

    assert not report.ok
    assert any("archive" in problem for problem in report.problems)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_selftest_cli_prints_morphs_to_stderr(capsys):
    assert tok.main(["selftest"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "", "diagnostics belong on stderr"
    assert "今日" in captured.err
    assert "キョウ" in captured.err  # lemma_reading column is populated
    assert "名詞/普通名詞/副詞可能/*" in captured.err
    assert "fugashi" in captured.err


def test_cli_rejects_an_unknown_command():
    with pytest.raises(SystemExit):
        tok.main(["frobnicate"])


def test_stamp_cli_writes_metadata_and_reports_to_stderr(tmp_path, monkeypatch, capsys):
    """``python -m katagiri.tokenizer stamp`` opens the configured DB (not one
    the test passes in directly -- mirrors ``fts_index``'s ``cli_db`` pattern)
    and stamps provenance, printing what it wrote to stderr."""
    path = tmp_path / "cli.db"
    db.open_db(path).close()

    real_open_db = db.open_db
    monkeypatch.setattr(db, "open_db", lambda *args, **kwargs: real_open_db(path))

    assert tok.main(["stamp"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "", "diagnostics belong on stderr"
    assert "stamped provenance" in captured.err
    for key in tok.METADATA_KEYS:
        assert key in captured.err

    conn = db.open_db(path)
    try:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?", ("dict_version",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["value"] == tok.DICT_VERSION


def test_stamp_cli_is_idempotent(tmp_path, monkeypatch, capsys):
    """Calling ``stamp`` twice must not raise or duplicate rows."""
    path = tmp_path / "cli.db"
    db.open_db(path).close()

    real_open_db = db.open_db
    monkeypatch.setattr(db, "open_db", lambda *args, **kwargs: real_open_db(path))

    assert tok.main(["stamp"]) == 0
    capsys.readouterr()
    assert tok.main(["stamp"]) == 0

    conn = db.open_db(path)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = ?", ("dict_version",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1
