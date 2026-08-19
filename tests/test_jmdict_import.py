"""JMdict / kanjium import tests.

Everything here runs against a hand-built seven-entry jmdict-simplified file and
a five-line accents.txt, zipped and checksummed in ``tmp_path``. The real 117 MB
vendored archive is never opened: these tests are about the mapping, the
transaction, and the checksum gate, none of which get truer at scale.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from katagiri import config as config_mod
from katagiri import db
from katagiri import jmdict_import as jm

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# Seven words, chosen for the shapes that break importers: several written forms
# for one entry, several senses, a kana-only entry, form-restricted readings and
# senses, a sense whose only gloss is not English, and two entries that share a
# reading but disagree about being common.
WORDS = [
    {
        "id": "1358280",
        "kanji": [{"common": True, "text": "食べる", "tags": []}],
        "kana": [
            {"common": True, "text": "たべる", "tags": [], "appliesToKanji": ["*"]}
        ],
        "sense": [
            {
                "partOfSpeech": ["v1", "vt"],
                "appliesToKanji": ["*"],
                "appliesToKana": ["*"],
                "misc": [],
                "gloss": [{"lang": "eng", "text": "to eat"}],
            },
            {
                "partOfSpeech": ["v1", "vt"],
                "appliesToKanji": ["*"],
                "appliesToKana": ["*"],
                "misc": ["col"],
                "gloss": [
                    {"lang": "eng", "text": "to live on"},
                    {"lang": "eng", "text": "to make a living"},
                ],
            },
        ],
    },
    {
        "id": "1259290",
        "kanji": [
            {"common": True, "text": "見る", "tags": []},
            {"common": False, "text": "観る", "tags": ["rK"]},
        ],
        "kana": [{"common": True, "text": "みる", "tags": [], "appliesToKanji": ["*"]}],
        "sense": [
            {
                "partOfSpeech": ["v1", "vt"],
                "appliesToKanji": ["見る"],
                "appliesToKana": ["*"],
                "misc": ["uk"],
                "gloss": [{"lang": "eng", "text": "to see"}],
            },
            {
                "partOfSpeech": ["v1", "vt"],
                "appliesToKanji": ["観る"],
                "appliesToKana": ["*"],
                "misc": [],
                "gloss": [{"lang": "eng", "text": "to watch (a play, movie)"}],
            },
        ],
    },
    {
        "id": "1201000",
        "kanji": [{"common": True, "text": "神", "tags": []}],
        "kana": [{"common": True, "text": "かみ", "tags": [], "appliesToKanji": ["*"]}],
        "sense": [
            {
                "partOfSpeech": ["n"],
                "misc": [],
                "gloss": [{"lang": "eng", "text": "god"}],
            }
        ],
    },
    {
        # Same reading as 神, deliberately not common: the ordering test.
        "id": "1201001",
        "kanji": [{"common": False, "text": "加味", "tags": []}],
        "kana": [{"common": False, "text": "かみ", "tags": [], "appliesToKanji": ["*"]}],
        "sense": [
            {
                "partOfSpeech": ["n", "vs"],
                "misc": [],
                "gloss": [{"lang": "eng", "text": "seasoning"}],
            }
        ],
    },
    {
        # Kana only: no written form at all.
        "id": "1002000",
        "kanji": [],
        "kana": [
            {"common": True, "text": "こんにちは", "tags": [], "appliesToKanji": ["*"]}
        ],
        "sense": [
            {
                "partOfSpeech": ["int"],
                "misc": ["uk"],
                "gloss": [{"lang": "eng", "text": "hello"}],
            }
        ],
    },
    {
        # Mixed-language glosses, and a second sense with no English at all.
        "id": "1003000",
        "kanji": [{"common": False, "text": "微妙", "tags": []}],
        "kana": [
            {"common": False, "text": "びみょう", "tags": [], "appliesToKanji": ["*"]}
        ],
        "sense": [
            {
                "partOfSpeech": ["adj-na"],
                "misc": [],
                "gloss": [
                    {"lang": "eng", "text": "subtle"},
                    {"lang": "ger", "text": "heikel"},
                ],
            },
            {
                "partOfSpeech": ["adj-na"],
                "misc": ["sl"],
                "gloss": [{"lang": "ger", "text": "zweifelhaft"}],
            },
        ],
    },
    {
        # A reading restricted to one of two written forms.
        "id": "1004000",
        "kanji": [
            {"common": True, "text": "行く", "tags": []},
            {"common": False, "text": "逝く", "tags": ["rK"]},
        ],
        "kana": [
            {"common": True, "text": "いく", "tags": [], "appliesToKanji": ["*"]},
            {"common": False, "text": "ゆく", "tags": [], "appliesToKanji": ["行く"]},
        ],
        "sense": [
            {
                "partOfSpeech": ["v5k-s", "vi"],
                "misc": [],
                "gloss": [{"lang": "eng", "text": "to go"}],
            }
        ],
    },
]

VERSION = "3.6.2-test"
DICT_DATE = "2026-08-17"

# surface, reading, accent — with a comma list, a kana-only line whose reading
# field is empty, and a duplicate of the first line.
ACCENTS = "\n".join(
    [
        "食べる\tたべる\t2",
        "神\tかみ\t1,0",
        "こんにちは\t\t5",
        "見る\tみる\t1",
        "食べる\tたべる\t2",
    ]
)


def _payload(words):
    return {
        "version": VERSION,
        "languages": ["eng"],
        "commonOnly": False,
        "dictDate": DICT_DATE,
        "dictRevisions": ["1.09"],
        "tags": {"v1": "Ichidan verb", "uk": "usually kana"},
        "words": words,
    }


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Vendor:
    """A tmp checkout-shaped vendor tree: the two data files and a manifest."""

    def __init__(self, root):
        self.root = root
        self.jmdict = root / "vendor" / "jmdict" / "jmdict-eng-test.json.zip"
        self.kanjium = root / "vendor" / "kanjium" / "accents.txt"
        self.manifest = root / "vendor" / "CHECKSUMS.sha256"
        self.jmdict.parent.mkdir(parents=True, exist_ok=True)
        self.kanjium.parent.mkdir(parents=True, exist_ok=True)

    def write_jmdict(self, words=None):
        payload = _payload(WORDS if words is None else words)
        with zipfile.ZipFile(self.jmdict, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "jmdict-eng-test.json", json.dumps(payload, ensure_ascii=False)
            )

    def write_kanjium(self, text=None):
        self.kanjium.write_text(ACCENTS if text is None else text, encoding="utf-8")

    def write_manifest(self):
        """Digest whatever is on disk now. Manifest paths are repo-relative."""
        lines = ["# test manifest"]
        for path in (self.kanjium, self.jmdict):
            relative = path.relative_to(self.root).as_posix()
            lines.append(f"{_sha256(path)}  {relative}")
        self.manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def refresh(self):
        self.write_jmdict()
        self.write_kanjium()
        self.write_manifest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_app_data(tmp_path, monkeypatch):
    """Keep config, backups, and the default db path inside tmp."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    config_mod.reset_config_cache()
    yield tmp_path
    config_mod.reset_config_cache()


@pytest.fixture
def conn(local_app_data):
    connection = db.open_db(local_app_data / "katagiri.db")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def vendor(tmp_path):
    tree = Vendor(tmp_path / "checkout")
    tree.refresh()
    return tree


def _imported(conn, vendor):
    return jm.import_jmdict(conn, vendor.jmdict, manifest_path=vendor.manifest)


def _counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("jmdict_entry", "jmdict_kanji", "jmdict_reading", "jmdict_sense")
    }


def _metadata(conn):
    return {
        row[0]: row[1] for row in conn.execute("SELECT key, value FROM metadata")
    }


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def test_import_reports_and_writes_the_expected_row_counts(conn, vendor):
    result = _imported(conn, vendor)

    assert result.entries == 7
    assert result.version == VERSION
    assert result.dict_date == DICT_DATE
    # 1 + 2 + 1 + 1 + 0 + 1 + 2 written forms, 1 + 1 + 1 + 1 + 1 + 1 + 2 readings,
    # 2 + 2 + 1 + 1 + 1 + 2 + 1 senses.
    assert (result.kanji_rows, result.reading_rows, result.sense_rows) == (8, 8, 10)
    assert _counts(conn) == {
        "jmdict_entry": 7,
        "jmdict_kanji": 8,
        "jmdict_reading": 8,
        "jmdict_sense": 10,
    }


def test_entry_is_common_when_any_form_is_common(conn, vendor):
    _imported(conn, vendor)
    common = dict(
        conn.execute("SELECT seq, is_common FROM jmdict_entry").fetchall()
    )
    assert common[1358280] == 1
    assert common[1259290] == 1  # 観る is not common, 見る is
    assert common[1201001] == 0
    assert common[1003000] == 0
    versions = {
        row[0] for row in conn.execute("SELECT DISTINCT dict_version FROM jmdict_entry")
    }
    assert versions == {VERSION}


def test_per_form_common_and_tags_are_packed_into_pri(conn, vendor):
    _imported(conn, vendor)
    forms = dict(
        conn.execute(
            "SELECT kanji, pri FROM jmdict_kanji WHERE seq = 1259290"
        ).fetchall()
    )
    assert forms["見る"] == "common"
    assert forms["観る"] == "rK"


def test_reading_restriction_is_packed_into_pri(conn, vendor):
    _imported(conn, vendor)
    readings = dict(
        conn.execute(
            "SELECT reading, pri FROM jmdict_reading WHERE seq = 1004000"
        ).fetchall()
    )
    # いく applies to every written form, so no token; ゆく does not.
    assert readings["いく"] == "common"
    assert readings["ゆく"] == "appliesToKanji=行く"


def test_senses_keep_upstream_order_pos_and_joined_english_glosses(conn, vendor):
    _imported(conn, vendor)
    senses = conn.execute(
        "SELECT sense_idx, pos, gloss_en, misc FROM jmdict_sense WHERE seq = 1358280 "
        "ORDER BY sense_idx"
    ).fetchall()
    assert [row["sense_idx"] for row in senses] == [1, 2]
    assert senses[0]["pos"] == "v1,vt"
    assert senses[0]["gloss_en"] == "to eat"
    assert senses[0]["misc"] is None
    assert senses[1]["gloss_en"] == "to live on; to make a living"
    assert senses[1]["misc"] == "col"


def test_sense_restrictions_are_packed_into_misc(conn, vendor):
    _imported(conn, vendor)
    senses = conn.execute(
        "SELECT sense_idx, misc FROM jmdict_sense WHERE seq = 1259290 "
        "ORDER BY sense_idx"
    ).fetchall()
    assert senses[0]["misc"] == "uk,appliesToKanji=見る"
    assert senses[1]["misc"] == "appliesToKanji=観る"


def test_non_english_glosses_are_dropped_and_may_leave_gloss_null(conn, vendor):
    _imported(conn, vendor)
    senses = conn.execute(
        "SELECT sense_idx, gloss_en FROM jmdict_sense WHERE seq = 1003000 "
        "ORDER BY sense_idx"
    ).fetchall()
    assert senses[0]["gloss_en"] == "subtle"
    assert senses[1]["gloss_en"] is None


def test_metadata_is_stamped(conn, vendor):
    _imported(conn, vendor)
    meta = _metadata(conn)
    assert meta["jmdict_version"] == VERSION
    assert meta["jmdict_dict_date"] == DICT_DATE
    assert meta["jmdict_imported_ts"].endswith("Z")


# ---------------------------------------------------------------------------
# Rebuild semantics
# ---------------------------------------------------------------------------


def test_rebuild_is_idempotent(conn, vendor):
    first = _imported(conn, vendor)
    before = _counts(conn)
    second = _imported(conn, vendor)

    assert second == first
    assert _counts(conn) == before


def test_rebuild_removes_entries_upstream_dropped(conn, vendor):
    _imported(conn, vendor)
    vendor.write_jmdict(WORDS[:3])
    vendor.write_manifest()

    result = _imported(conn, vendor)

    assert result.entries == 3
    remaining = {
        row[0] for row in conn.execute("SELECT seq FROM jmdict_entry")
    }
    assert remaining == {1358280, 1259290, 1201000}
    # Child rows of a dropped entry go with it.
    assert not conn.execute(
        "SELECT 1 FROM jmdict_sense WHERE seq = 1003000"
    ).fetchall()


def test_a_failure_mid_import_leaves_the_previous_import_intact(
    conn, vendor, monkeypatch
):
    _imported(conn, vendor)
    before = _counts(conn)
    before_meta = _metadata(conn)

    real = jm._word_rows
    calls = {"n": 0}

    def exploding(word, version):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom, halfway through")
        return real(word, version)

    monkeypatch.setattr(jm, "_word_rows", exploding)
    # One word per flush, so two words really are inserted before the failure:
    # the rollback has to undo inserts, not just the opening DELETE.
    monkeypatch.setattr(jm, "_BATCH_WORDS", 1)
    # Five words, failing on the third: a leaked partial write would show up as
    # a two-entry table where seven entries used to be.
    vendor.write_jmdict(WORDS[:5])
    vendor.write_manifest()

    with pytest.raises(RuntimeError, match="boom"):
        _imported(conn, vendor)

    assert _counts(conn) == before
    assert _metadata(conn) == before_meta
    assert conn.execute("SELECT COUNT(*) FROM jmdict_entry").fetchone()[0] == 7
    # The transaction really was closed, not left open.
    assert not conn.in_transaction


def test_an_empty_file_does_not_replace_a_good_import(conn, vendor):
    _imported(conn, vendor)
    vendor.write_jmdict([])
    vendor.write_manifest()

    with pytest.raises(jm.JmdictImportError, match="refusing to replace"):
        _imported(conn, vendor)

    assert _counts(conn)["jmdict_entry"] == 7


def test_an_empty_file_is_allowed_when_there_is_nothing_to_lose(conn, vendor):
    vendor.write_jmdict([])
    vendor.write_manifest()

    result = _imported(conn, vendor)

    assert result.entries == 0
    assert _metadata(conn)["jmdict_version"] == VERSION


# ---------------------------------------------------------------------------
# Checksum gate
# ---------------------------------------------------------------------------


def test_verify_vendor_file_returns_the_recomputed_digest(vendor):
    digest = jm.verify_vendor_file(vendor.jmdict, manifest_path=vendor.manifest)
    assert digest == _sha256(vendor.jmdict)


def test_verify_vendor_file_refuses_a_tampered_file(vendor):
    with vendor.kanjium.open("a", encoding="utf-8") as handle:
        handle.write("\n偽\tにせ\t0\n")

    with pytest.raises(jm.ChecksumError) as caught:
        jm.verify_vendor_file(vendor.kanjium, manifest_path=vendor.manifest)

    assert caught.value.expected != caught.value.actual
    assert caught.value.actual == _sha256(vendor.kanjium)


def test_verify_vendor_file_refuses_a_file_the_manifest_omits(vendor):
    stray = vendor.root / "vendor" / "kanjium" / "extra.txt"
    stray.write_text("nothing\n", encoding="utf-8")

    with pytest.raises(jm.ChecksumError, match="not listed"):
        jm.verify_vendor_file(stray, manifest_path=vendor.manifest)


def test_verify_vendor_file_refuses_a_missing_file(vendor):
    with pytest.raises(jm.VendorFileError, match="does not exist"):
        jm.verify_vendor_file(
            vendor.root / "vendor" / "jmdict" / "gone.zip",
            manifest_path=vendor.manifest,
        )


def test_a_tampered_archive_is_refused_before_any_row_is_touched(conn, vendor):
    _imported(conn, vendor)
    before = _counts(conn)
    with vendor.jmdict.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(jm.ChecksumError, match="Checksum mismatch"):
        _imported(conn, vendor)

    assert _counts(conn) == before


def test_read_manifest_rejects_a_malformed_line(vendor):
    vendor.manifest.write_text("deadbeef  vendor/x\n", encoding="utf-8")
    with pytest.raises(jm.VendorFileError, match="line 1"):
        jm.read_manifest(vendor.manifest)


def test_read_manifest_skips_comments_and_blank_lines(vendor):
    entries = jm.read_manifest(vendor.manifest)
    assert set(entries) == {
        "vendor/jmdict/jmdict-eng-test.json.zip",
        "vendor/kanjium/accents.txt",
    }


# ---------------------------------------------------------------------------
# kanjium
# ---------------------------------------------------------------------------


def _pitch(conn):
    return conn.execute(
        "SELECT surface, reading, accent, source_version FROM pitch_accent "
        "ORDER BY surface, reading, accent"
    ).fetchall()


def test_kanjium_splits_comma_lists_dedupes_and_fills_kana_readings(conn, vendor):
    result = jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)

    rows = [(row[0], row[1], row[2]) for row in _pitch(conn)]
    assert ("神", "かみ", "0") in rows
    assert ("神", "かみ", "1") in rows
    # Empty reading field means the surface is the reading.
    assert ("こんにちは", "こんにちは", "5") in rows
    # 食べる appears twice in the file; the triple is stored once.
    assert rows.count(("食べる", "たべる", "2")) == 1
    assert result.rows == len(rows) == 5
    assert result.lines == 5
    assert result.source_version.startswith("kanjium:")


def test_kanjium_stamps_metadata_and_is_idempotent(conn, vendor):
    first = jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)
    second = jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)

    assert second == first
    meta = _metadata(conn)
    assert meta["kanjium_source_version"] == first.source_version
    assert meta["kanjium_imported_ts"].endswith("Z")


def test_kanjium_rebuild_drops_rows_upstream_removed(conn, vendor):
    jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)
    vendor.write_kanjium("食べる\tたべる\t2\n")
    vendor.write_manifest()

    result = jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)

    assert result.rows == 1
    assert [(row[0], row[1], row[2]) for row in _pitch(conn)] == [
        ("食べる", "たべる", "2")
    ]


# ---------------------------------------------------------------------------
# lookup_word
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded(conn, vendor):
    jm.import_jmdict(conn, vendor.jmdict, manifest_path=vendor.manifest)
    jm.import_kanjium(conn, vendor.kanjium, manifest_path=vendor.manifest)
    return conn


def test_lookup_word_matches_a_written_form_with_readings_senses_and_pitch(loaded):
    found = jm.lookup_word(loaded, "食べる")

    assert len(found) == 1
    entry = found[0]
    assert entry["seq"] == 1358280
    assert entry["is_common"] is True
    assert [form["text"] for form in entry["kanji"]] == ["食べる"]
    assert [reading["reading"] for reading in entry["readings"]] == ["たべる"]
    assert entry["readings"][0]["common"] is True
    assert entry["readings"][0]["pitch"] == ["2"]
    assert [sense["gloss"] for sense in entry["senses"]] == [
        "to eat",
        "to live on; to make a living",
    ]
    assert entry["senses"][0]["pos"] == ["v1", "vt"]


def test_lookup_word_matches_a_reading_and_puts_common_entries_first(loaded):
    found = jm.lookup_word(loaded, "かみ")

    assert [entry["seq"] for entry in found] == [1201000, 1201001]
    assert [entry["is_common"] for entry in found] == [True, False]
    # Both alternatives of the comma list ride along, in file order.
    assert found[0]["readings"][0]["pitch"] == ["1", "0"]
    # The uncommon homophone has no kanjium line of its own.
    assert found[1]["readings"][0]["pitch"] == []


def test_lookup_word_finds_a_kana_only_entry_and_its_pitch(loaded):
    found = jm.lookup_word(loaded, "こんにちは")

    assert len(found) == 1
    assert found[0]["kanji"] == []
    assert found[0]["readings"][0]["pitch"] == ["5"]
    assert found[0]["senses"][0]["misc"] == ["uk"]


def test_lookup_word_attaches_pitch_found_via_another_written_form(loaded):
    # The kanjium line is keyed on 見る; 観る is the same entry and reading.
    found = jm.lookup_word(loaded, "観る")

    assert len(found) == 1
    assert found[0]["readings"][0]["pitch"] == ["1"]
    assert {row["surface"] for row in found[0]["pitch"]} == {"見る"}


def test_lookup_word_respects_a_limit(loaded):
    assert len(jm.lookup_word(loaded, "かみ", limit=1)) == 1


def test_lookup_word_of_an_unknown_surface_is_empty(loaded):
    assert jm.lookup_word(loaded, "存在しない語") == []
    assert jm.lookup_word(loaded, "") == []


# ---------------------------------------------------------------------------
# Streaming reader
# ---------------------------------------------------------------------------


def test_the_reader_streams_across_buffer_boundaries(vendor):
    """A one-byte chunk size exercises every refill path in the reader."""
    with zipfile.ZipFile(vendor.jmdict) as archive:
        with archive.open("jmdict-eng-test.json") as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = jm._WordArrayReader(stream, chunk_size=1)
            ids = [word["id"] for word in reader.words()]

    assert reader.version == VERSION
    assert ids == [word["id"] for word in WORDS]


def test_a_truncated_words_array_is_an_error(conn, vendor):
    payload = json.dumps(_payload(WORDS), ensure_ascii=False)
    with zipfile.ZipFile(vendor.jmdict, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("jmdict-eng-test.json", payload[: len(payload) // 2])
    vendor.write_manifest()

    with pytest.raises(jm.JmdictImportError):
        _imported(conn, vendor)


def test_a_file_without_a_words_array_is_an_error(conn, vendor):
    with zipfile.ZipFile(vendor.jmdict, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("jmdict-eng-test.json", json.dumps({"version": "1"}))
    vendor.write_manifest()

    with pytest.raises(jm.JmdictImportError, match="no top-level 'words' array"):
        _imported(conn, vendor)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_all_imports_both_sources(local_app_data, vendor, capsys):
    code = jm.main(
        [
            "all",
            "--db",
            str(local_app_data / "cli.db"),
            "--jmdict",
            str(vendor.jmdict),
            "--kanjium",
            str(vendor.kanjium),
            "--manifest",
            str(vendor.manifest),
            "--lookup",
            "食べる",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "7 entries" in out
    assert "5 accent rows" in out
    assert "to eat" in out

    check = db.connect(local_app_data / "cli.db")
    try:
        assert check.execute("SELECT COUNT(*) FROM jmdict_entry").fetchone()[0] == 7
        assert check.execute("SELECT COUNT(*) FROM pitch_accent").fetchone()[0] == 5
    finally:
        check.close()


def test_cli_reports_a_checksum_failure_without_traceback(local_app_data, vendor, capsys):
    with vendor.kanjium.open("a", encoding="utf-8") as handle:
        handle.write("偽\tにせ\t0\n")

    code = jm.main(
        [
            "kanjium",
            "--db",
            str(local_app_data / "cli.db"),
            "--kanjium",
            str(vendor.kanjium),
            "--manifest",
            str(vendor.manifest),
        ]
    )

    assert code == 2
    assert "Checksum mismatch" in capsys.readouterr().out


def test_cli_defaults_to_the_repository_vendor_tree(monkeypatch, local_app_data, vendor):
    """No --jmdict/--kanjium means 'find them under <repo>/vendor'."""
    monkeypatch.setattr(jm, "repo_root", lambda: vendor.root)

    assert jm.default_jmdict_zip() == vendor.jmdict
    assert jm.default_kanjium_path() == vendor.kanjium
    assert jm.checksum_manifest_path() == vendor.manifest


def test_the_real_vendor_tree_is_discoverable_and_listed():
    """The repo's own files exist and are named in the manifest (no hashing)."""
    entries = jm.read_manifest()
    manifest_keys = set(entries)
    for path in (jm.default_jmdict_zip(), jm.default_kanjium_path()):
        assert path.is_file()
        relative = path.resolve().relative_to(jm.repo_root().resolve()).as_posix()
        assert relative in manifest_keys


def test_import_is_rejected_on_a_database_without_the_derived_tables(tmp_path, vendor):
    """Unmigrated database: fail with a SQL error, not a partial write."""
    connection = sqlite3.connect(str(tmp_path / "bare.db"), isolation_level=None)
    try:
        with pytest.raises(sqlite3.Error):
            jm.import_jmdict(connection, vendor.jmdict, manifest_path=vendor.manifest)
    finally:
        connection.close()
