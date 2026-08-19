---
schema: 1
type: meta
---

# Roadmap

Sequenced so that nothing built later requires demolishing anything built earlier.

## Phase 0 — Vault only (now, no code)

Learn Japanese with plain Markdown and me. Prove the format is pleasant to read and write **before** any code depends on it. If a note format annoys you, change it now — it's free today and expensive after the parser exists.

- [x] Structure, conventions, schemas
- [x] First lesson, first words, first grammar
- [ ] Hiragana, week 1
- [ ] Daily shadowing
- [ ] 3–5 lessons, hand-written by me, so the format is battle-tested

## Phase 1 — Core library + CLI

- [ ] Topic-file parser + `validate`
- [ ] `katagiri fmt` — the formatter. Build this **second**, before anything else touches a topic file. It's what keeps hand-editing and machine-writing from fighting.
- [ ] Settings + script modes (kanji off, kana-only, rōmaji, audio-only)
- [ ] `known_set`
- [ ] Review log + FSRS scheduler
- [ ] `due` / `drill` in terminal (text only, no UI)
- [ ] Generated indexes, cross-topic views, word dossiers into `.derived/`
- [ ] Anki export — never be locked in

**Milestone:** you can do a full SRS session from the terminal.

## Phase 2 — Tokenizer + coverage

- [ ] fugashi/UniDic tokenization
- [ ] JMdict lookup, KANJIDIC2, pitch-accent DB → auto-fill `pitch:`
- [ ] `coverage` on arbitrary text
- [ ] `find_i_plus_one`
- [ ] Comprehension-debt list (your own frequency ranking)

**Milestone:** point it at a YouTube link, get "learn these 14 words and you'll understand 91% of it."

## Phase 3 — MCP + Claude Code

- [ ] `katagiri-mcp` wrapping the core
- [ ] obsidian-mcp alongside it
- [ ] Authoring tools (`add_vocab`, `log_error`, `triage_inbox`)
- [ ] `gen_exercise`, `build_sentences`
- [ ] Weekly sensei letter

**Milestone:** you talk to Claude Code, it teaches you and writes to your vault.

## Phase 4 — Audio

- [ ] TTS with content-hash caching (Codex voices, or VOICEVOX locally)
- [ ] Speaking cards answered aloud, scored by ASR
- [ ] Dictation drills
- [ ] Codex conversation with vocabulary ceiling
- [ ] Session notes auto-filed to `00-inbox/`

**Milestone:** you speak more Japanese per week than you read.

## Phase 5 — Media pipeline

- [ ] yt-dlp + whisper timestamped transcripts
- [ ] `ingest_media`, `explain_passage`, `mine_clips`
- [ ] Clip cards with audio + screenshot
- [ ] `tasks_from_media`
- [ ] Manga OCR
- [ ] Screenshot-question tool — mpv IPC `screenshot-to-file`, agent reads the image and answers about the frame

## Phase 6 — Prosody

- [ ] Pitch-contour extraction and overlay
- [ ] Shadow-dubbing with mora alignment scoring
- [ ] Karaoke mode
- [ ] Music as a media type — timed lyrics (`.lrc`/`.ass`) through the same subtitle pipeline as video; mine vocabulary from songs

## Phase 7 — App

Deliberately last. By now you know exactly what you use daily, and the core library is doing all the work — the app is genuinely just a view. Building the UI first is how these projects die: you end up with a beautiful shell around the wrong model.

## Phase 8 — The strange ones

See [[MOONSHOTS]] for the full list. Two of them are cheap enough to do out of order:

- [ ] **Seal the canary set** (MOONSHOTS §1) — do this in Phase 0. It costs an afternoon and it's worthless if you start it late, because anything you've already studied can't be held out.
- [ ] **L1 interference profile** (§11) — one conversation, reshapes all the phonology drills.

Then:
- [ ] Confusion graph → adversarial drill generation
- [ ] Rewind telemetry capture (§4 — highest value-to-effort on the list)
- [ ] Per-item decay anomaly detection → prescribe re-encoding (§8)
- [ ] Interlanguage grammar report (§2)
- [ ] Personal audiogram (§3)
- [ ] Voice-clone self-modeling (§6)
- [ ] The serialized audio drama (§5)
- [ ] Semantic gap analysis (§7)
- [ ] N-of-1 trials against the canary set (§9)
