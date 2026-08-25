# vendor/ — local, gitignored data dependencies

This directory holds large third-party data files that Katagiri needs at runtime.
**The binaries themselves are never committed.** Only this README and
`CHECKSUMS.sha256` are tracked (see the negation patterns in the root
`.gitignore`).

## Hard rules

1. **No runtime downloads, ever.** Katagiri must never fetch data over the
   network while serving MCP requests. Acquisition is a deliberate, manual,
   documented setup step performed by the operator. Two exceptions, both
   installer-only and gated on explicit operator consent at a prompt (never
   under `--yes`, never at MCP runtime): the Irodori Table of Contents PDF
   (see the Irodori section below), and the vendor files themselves — the
   installer wizard (or `python -m katagiri.vendor_fetch`, run by hand) can
   fetch the missing files in this directory from the official sources
   documented below (`src/katagiri/vendor_fetch.py`).
2. **Every file is checksummed.** After acquiring a component, record its
   SHA-256 in `vendor/CHECKSUMS.sha256`. That file *is* committed, so the repo
   pins exactly which bytes are expected.
3. **Checksums are verified at load time.** Loaders refuse to use a vendored
   file whose digest does not match the committed entry, and raise with the
   expected/actual digests rather than degrading silently. For the *optional*
   difficulty datasets below, the loader still raises (the bad bytes are never
   read) and the difficulty scorer turns that raise into an unavailable component
   carrying both digests — degrading the score loudly, in the result, never
   quietly using the file.
4. **No secrets or machine-specific paths here.** Local absolute paths belong in
   `%LOCALAPPDATA%\Katagiri\config.toml`, not in the repo.

## Expected contents

| Path | Component | Approx. size |
| --- | --- | --- |
| `vendor/unidic/` | Full UniDic (MeCab dictionary, incl. accent/pitch fields) | ~1 GB unpacked |
| `vendor/kanjium/accents.txt` | kanjium pitch-accent database (single TSV-ish file) | ~10 MB |
| `vendor/jmdict/jmdict-eng-*.json.zip` | jmdict-simplified English release | ~11 MB |
| `vendor/jreadability/jreadability-*.tar.gz` | jreadability sdist — the readability coefficients | ~12 KB |
| `vendor/bccwj/BCCWJ_frequencylist_suw_ver*.zip` | BCCWJ short-unit frequency list (one TSV inside) | ~8 MB |
| `vendor/jlpt/n<1-5>-vocab-*.anki` | tanos JLPT vocabulary lists, one file per level | ~8 MB total |
| `vendor/irodori/` | Irodori (Japan Foundation) PDF/MP3 lesson materials — **hand-acquired, never committed** | varies |
| `vendor/taekim/` | Tae Kim's Guide to Japanese Grammar extracts — CC BY-NC-SA, **committable with attribution** | small (HTML/text extracts) |
| `vendor/asbplayer-extension/` | Custom asbplayer Chrome unpacked extension build, Katagiri-bridge defaults baked in | ~9 MB |

jreadability, BCCWJ, and the tanos JLPT lists are the **difficulty-for-me**
datasets (D-10 policy, `docs/dev-plan.md` D2). They are *optional*:
`katagiri.intelligence.difficulty_for_me` scores on whichever of them loaded and
reports `weight_used`, so a checkout without them still runs a study session —
with a visibly partial score. Irodori and Tae Kim (FR-019/FR-020, D-10) are
optional in the same sense but for a different reason: the curriculum importer
tolerates their absence, marking affected items unanchored or text-only rather
than failing (see `research.md` §Post-gate). Everything else above is not
optional.

## Acquisition

### Full UniDic

1. Obtain the full UniDic distribution for the version pinned in
   `CHECKSUMS.sha256` (the `unidic` Python distribution's downloader, or the
   upstream UniDic release archive — either way, download it *once*, manually).
2. Unpack it to `vendor/unidic/` so that `dicrc`, `char.bin`, `matrix.bin`,
   `sys.dic`, and `unk.dic` sit directly inside that directory.
3. Compute digests and append them to `CHECKSUMS.sha256` (see below).

Note: only the *full* UniDic carries the accent/pitch and detailed lemma fields
Katagiri relies on; the trimmed "unidic-lite" dictionary is not a substitute.

### kanjium accents

1. Download `accents.txt` from the kanjium project at the revision pinned in
   `CHECKSUMS.sha256`.
2. Place it at `vendor/kanjium/accents.txt`.
3. Append its digest to `CHECKSUMS.sha256`.

### jreadability (readability coefficients)

* **Source**: PyPI sdist, `https://pypi.org/project/jreadability/` — release
  1.1.5, file `jreadability-1.1.5.tar.gz` (sha256 as published by PyPI, which is
  the digest in `CHECKSUMS.sha256`).
* **License**: MIT (© 2024 Joshua Hamilton). Implements the readability model of
  Lee & Hasebe, *Readability measurement of Japanese texts based on levelled
  corpora*.
* **Version pinned**: 1.1.5, retrieved 2026-08-19.

1. Download the **sdist** (`.tar.gz`), not a wheel, to
   `vendor/jreadability/jreadability-1.1.5.tar.gz`.
2. Append its digest to `CHECKSUMS.sha256`.

It is **vendored rather than installed**. Nothing imports the package: the loader
reads the six coefficients out of `src/jreadability/jreadability.py` inside the
archive and recomputes the features on the *vendored full UniDic*, because that is
the dictionary every other number in this project comes from (upstream defaults to
`unidic-lite`, which Katagiri refuses to load — see
`src/katagiri/tokenizer.py`). Both agree exactly on upstream's own README example
(6.438). Installing the package would pull `unidic-lite` into the venv, which is
the one dictionary this project must not have available.

### BCCWJ short-unit frequency list

* **Source**: NINJAL, 『現代日本語書き言葉均衡コーパス』短単位語彙表 (Version 1.0),
  DOI [10.15084/00003218](https://doi.org/10.15084/00003218) → the repository
  record's file `BCCWJ_frequencylist_suw_ver1_0.zip`. Landing page:
  `https://ccd.ninjal.ac.jp/bccwj/freq-list.html`.
* **License**: NINJAL states the frequency lists are free to use for **research
  and educational purposes** (「研究、教育目的であれば無償で自由にお使いになれます」).
  Personal study qualifies; this is *not* an open license — do not redistribute the
  file or a derived list, and see `BCCWJ_frequencylist_manual_ver1_0b.pdf` on that
  page for the full usage notes.
* **Version pinned**: `suw` (短単位) Version 1.0, retrieved 2026-08-19. 185,136
  ranked lemmas, one TSV inside the zip.

1. Download the zip from the DOI record to
   `vendor/bccwj/BCCWJ_frequencylist_suw_ver1_0.zip` — keep it zipped, the loader
   streams the TSV out of it.
2. Append its digest to `CHECKSUMS.sha256`.

Short unit (`suw`) and not long unit (`luw`): UniDic's morphs *are* short units,
so a `lemma` from the tokenizer and a `lemma` column in this list are the same
kind of object. The `luw` lists would need a second segmentation to compare
against.

### tanos JLPT vocabulary lists

* **Source**: `http://www.tanos.co.uk/jlpt/jlpt<N>/vocab/n<N>-vocab-kanji-eng.anki`
  for N = 1…5 (Jonathan Waller's JLPT Resources).
* **License**: CC BY — the site's "Use my data!" page
  (`http://www.tanos.co.uk/jlpt/sharing/`) licenses everything not for sale under
  Creative Commons Attribution. **Credit the site** in anything published from
  this data.
* **Version pinned**: the files as served on 2026-08-19 (the site is unversioned;
  the digests in `CHECKSUMS.sha256` are the version).

1. Download one file per level into `vendor/jlpt/`, keeping the `n<level>-vocab-…`
   filenames — the loader reads the level from the filename.
2. Append the digests to `CHECKSUMS.sha256`.

Why the `.anki` exports: they are the only machine-readable form the site offers
for **all five levels** (the combined `jlpt_vocab_2345.xls` covers N2–N5 only and
is legacy BIFF8, which would mean a new dependency), and an Anki-1 export is plain
SQLite, so the loader reads it with the standard library. If you prefer a
different tanos artefact, the loader's contract is "one file per level, named
`n<level>-vocab-*.anki`, with a `Front` field holding the Japanese".

### Irodori (Japan Foundation)

* **Source**: the official Japan Foundation Irodori distribution
  (`irodori.jpf.go.jp`).
* **License**: custom Japan Foundation terms. Non-commercial text extraction is
  acceptable; the textbook's illustrations are untouchable; **no
  redistribution**. Because of this, **the lesson PDF/MP3 files are never
  committed to this repository, under any circumstance** — stricter than every
  other row in this document, and stricter than the `vendor/*` gitignore rule
  needs to be for anything else here.
* **Version pinned**: the lesson materials themselves are hand-acquired and
  hand-verified per operator, not pinned to a release — see below. The one
  exception is the Table of Contents PDF (lesson titles, can-do goals, short
  word lists — not the lesson content itself), which the installer can fetch
  automatically.

**Lesson PDFs/MP3s** (hand-acquired, as before):

1. Acquire the lesson PDF(s)/MP3(s) yourself, by hand, from the official Japan
   Foundation Irodori site (or whatever other means you already have the rights
   to use them through).
2. Place them under `vendor/irodori/`.
3. Run `python scripts/fetch_irodori.py`. It computes their digests and checks
   them against anything already recorded in `CHECKSUMS.sha256`, then reports
   which files are new. Review its output and append digests yourself — the
   script never downloads, scrapes, or writes `CHECKSUMS.sha256`; it only reads
   files you already placed locally.

**Table of Contents / starter study schedule** (automated, consent-gated):

The installer wizard (`python -m katagiri.installer`) can, with your explicit
yes at the prompt, download the TOC PDF from the URL pinned in
`katagiri.irodori_import.TOC_URL`, verify/pin its digest into
`CHECKSUMS.sha256`, and seed a per-lesson `home_topic` (`irodori-l01` ..
`irodori-l18`) of word items from its "Kanji Words" lists, so a fresh install
has something real to study without you acquiring anything by hand. This is
the one deliberate exception to "no runtime downloads, ever" — it only runs
from the installer, gated on consent, never from the MCP server at request
time. Re-run `python -m katagiri.installer` to install it later if you skip it
the first time.

### Tae Kim's Guide to Japanese Grammar

* **Source**: `https://guidetojapanese.org/` (Tae Kim's Guide to Japanese
  Grammar); `scripts/fetch_taekim.py` fetches the grammar-index page as the
  specific extract.
* **License**: Creative Commons **Attribution-NonCommercial-ShareAlike**
  (CC BY-NC-SA). Unlike Irodori, extracts of this material **are** committable
  — but only together with attribution. Required attribution text:

  > Tae Kim's Guide to Japanese Grammar, © Tae Kim, https://guidetojapanese.org/
  > — licensed under CC BY-NC-SA (Creative Commons
  > Attribution-NonCommercial-ShareAlike).

* **Version pinned**: whatever `scripts/fetch_taekim.py` last fetched; see its
  recorded digest in `CHECKSUMS.sha256` for the exact bytes.

1. Run `python scripts/fetch_taekim.py`. It fetches the extract, writes it under
   `vendor/taekim/`, appends its digest to `CHECKSUMS.sha256` if new, and checks
   that this file still carries the attribution notice above.
2. Review the extract and the `CHECKSUMS.sha256` diff before committing either
   — unlike every other vendored component, these files are allowed into the
   repository.

### asbplayer extension (Chrome unpacked build)

* **Source**: local checkout at `C:\ProjectsC\RandomPr\asbplayer` — the user's
  fork of asbplayer, carrying uncommitted playback-state additions on top of
  upstream. Not a public release artifact; this vendor copy is a build output,
  not a downloaded dependency, and (like the rest of this directory) is not
  committed to the Katagiri repo.
* **Defaults changed for Katagiri**: before building, `common/settings/settings-provider.ts`
  was edited so a fresh install points at the Katagiri bridge out of the box —
  `ankiConnectUrl: 'http://127.0.0.1:8766'` (was `8765`, the standard
  AnkiConnect port; `8766` is Katagiri's AnkiConnect proxy) and
  `webSocketClientEnabled: true` (was `false`), pairing with the pre-existing
  `webSocketServerUrl: 'ws://127.0.0.1:8766/ws'`.
* **Build command**: from the asbplayer checkout root,
  `node .yarn/releases/yarn-3.2.0.cjs workspace @project/extension run build`
  (wxt build; yarn is vendored in-repo as a Berry release, no global yarn
  install needed). Output lands in `extension/.output/chrome-mv3/` and is
  mirrored here with `robocopy ... /MIR`.
* **Version pinned**: built 2026-08-25 from the fork's working tree as it
  stood that day (uncommitted changes included) — this is a snapshot, not a
  tagged release; rebuild from the same checkout to refresh it.
* **Loading it**: `chrome://extensions` → enable Developer mode → "Load
  unpacked" → select `vendor/asbplayer-extension/` (the directory containing
  `manifest.json`).

## Recording checksums

From the repo root, in PowerShell:

```powershell
Get-ChildItem -Recurse -File vendor |
  Where-Object { $_.Name -notin @('README.md','CHECKSUMS.sha256') } |
  ForEach-Object {
    $rel = (Resolve-Path -Relative $_.FullName) -replace '^\.\\','' -replace '\\','/'
    "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower())  $rel"
  } | Sort-Object
```

Append the output to `vendor/CHECKSUMS.sha256`, review the diff, and commit only
that file.
