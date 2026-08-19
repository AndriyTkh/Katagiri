# vendor/ — local, gitignored data dependencies

This directory holds large third-party data files that Katagiri needs at runtime.
**The binaries themselves are never committed.** Only this README and
`CHECKSUMS.sha256` are tracked (see the negation patterns in the root
`.gitignore`).

## Hard rules

1. **No runtime downloads, ever.** Katagiri must never fetch data over the
   network while serving MCP requests. Acquisition is a deliberate, manual,
   documented setup step performed by the operator.
2. **Every file is checksummed.** After acquiring a component, record its
   SHA-256 in `vendor/CHECKSUMS.sha256`. That file *is* committed, so the repo
   pins exactly which bytes are expected.
3. **Checksums are verified at load time.** Loaders refuse to use a vendored
   file whose digest does not match the committed entry, and raise with the
   expected/actual digests rather than degrading silently.
4. **No secrets or machine-specific paths here.** Local absolute paths belong in
   `%LOCALAPPDATA%\Katagiri\config.toml`, not in the repo.

## Expected contents

| Path | Component | Approx. size |
| --- | --- | --- |
| `vendor/unidic/` | Full UniDic (MeCab dictionary, incl. accent/pitch fields) | ~1 GB unpacked |
| `vendor/kanjium/accents.txt` | kanjium pitch-accent database (single TSV-ish file) | ~10 MB |

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
