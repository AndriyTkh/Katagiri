---
schema: 2
type: settings

# ── Script & reading ─────────────────────────────────────────
script_mode: kanji_with_furigana
# kanji_with_furigana  kanji shown, kana reading alongside      (default)
# kanji_only           kanji, furigana only on unknown kanji
# kana_only            no kanji anywhere. Reading column only.
# romaji               no Japanese script at all. Rōmaji generated from kana.
# audio_only           text hidden in drills wherever possible; audio prompts, spoken answers

kanji_enabled: false
# false = kanji is never *displayed* or *drilled*.
# Kanji data is still recorded in every topic file regardless.
# Display preference must never cause data loss — flip this to true
# in month 4 and everything you've learned is already annotated.

furigana_mode: unknown_only
# always | unknown_only | never
# unknown_only uses known_set: gloss only the kanji you haven't learned yet.
# It shrinks by itself as you improve. Best default once kanji is on.

romaji_until: 2026-09-01
# fmt strips the generated rōmaji column after this date.
# Set to null to keep it forever (see the note below).

reading_as_goal: false
# false = reading cards are generated but scheduled lazily, far behind
# listening and speaking. Set true when reading becomes a real goal.

# ── Learner profile ──────────────────────────────────────────
l1: uk                    # native Ukrainian
other_languages: [en-c2, fi]
interference_profile: 35-phonology/l1-profile.md
# Drills, phonology priorities and grammar framing are all built
# against this. See the profile — two of these three help you a lot,
# and the strongest one (English) is a liability for loanwords.

# ── Study balance ────────────────────────────────────────────
primary_directions: [listen_to_meaning, meaning_to_speech]
lazy_directions: [read_to_meaning]
target_io_ratio: 5        # input hours : output hours

# ── Immersion ────────────────────────────────────────────────
target_coverage: 0.90     # what counts as "ready to watch"
max_unknown_per_sentence: 1
register_lint: true       # warn when anime-register leaks into polite practice

# ── Voice ────────────────────────────────────────────────────
tts_provider: codex       # codex | voicevox | none
sensei_language: en       # en | simple-jp | jp-only  (gradual L1 removal)
---

# Settings

Everything display-related is a setting. Everything data-related is not.

**The principle:** the vault records complete information about every word — kanji, reading, pitch, register, provenance — regardless of what you've chosen to study. Settings control *rendering and scheduling*, never *storage*. This means every preference below is reversible at any time with zero data loss, and turning kanji on in month four is a config change, not a migration.

## `kanji_enabled: false`

Kanji columns disappear from every topic file after `katagiri fmt`. Kanji cards are never generated. The `Word` column still exists in the file's data — `fmt` just stops rendering it. Flip to `true` and the system already knows the 300 kanji that make up your spoken vocabulary and can order them by *your* usefulness rather than by JLPT level.

## `script_mode: romaji` and `audio_only`

These exist because you asked for them, and there are legitimate users: travellers on a three-month timeline, heritage speakers who already understand and only want to speak, anyone whose goal is purely conversational.

My professional objection, stated once and then dropped: rōmaji doesn't map cleanly onto Japanese phonology, so it quietly teaches wrong mora counts (おばあさん → "obaasan" hides that it's five beats) and wrong vowel devoicing (です → "desu" invites "de-su"). Kana is 46 symbols and roughly ten hours. It's also the only way to do dictation, which is the highest-yield listening drill there is.

That said — it's your setting, it works, and `audio_only` mode is genuinely well-served by this architecture, because pitch, mora count, and audio refs are all first-class fields rather than afterthoughts.

If you do go rōmaji: rōmaji is **generated from kana, never stored**. So the day you change your mind, there is nothing to migrate.

## Per-topic override

Any topic file can override in its frontmatter:

```yaml
script_mode: kana_only     # e.g. keep counters kana-only, they're read aloud anyway
```
