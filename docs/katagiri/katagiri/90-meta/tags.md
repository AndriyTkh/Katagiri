---
schema: 1
type: meta
---

# Tag & vocabulary registry

Keep this closed-ish. Free-form tags are how a vault becomes unqueryable.

## `register`
`polite` `plain` `casual` `rough` `humble` `honorific` `feminine` `masculine` `archaic` `anime-only` `written-only` `neutral`

## `POS` column codes (compact, for topic tables)

| Code | Meaning |
|---|---|
| `n` | noun |
| `v1` | ichidan verb (食べる) |
| `v5` | godan verb (飲む) |
| `virr` | irregular (する・来る) |
| `vt` / `vi` | transitive / intransitive — append: `v1·vt` |
| `adj-i` | i-adjective |
| `adj-na` | na-adjective |
| `adv` | adverb |
| `exp` | set phrase / expression |
| `part` | particle |
| `ctr` | counter |
| `pn` | pronoun |
| `conj` | conjunction |
| `int` | interjection |

Append a counter to a noun with `·`: `n·〜つ`, `n·〜杯`, `n·〜人`.

## Topic roots
`food/*` `people/*` `transport` `body` `time/*` `place/*` `nature` `verbs/*` `adjectives/*` `school` `work` `home` `clothing` `emotion` `weather` `numbers` `counters` `greetings`

A word's home topic is the file it lives in. The `Also` column cross-lists.

## `script_mode` values
`kanji_with_furigana` `kanji_only` `kana_only` `romaji` `audio_only` — see [[90-meta/settings]]

## Review directions
`listen_to_meaning` `meaning_to_speech` `read_to_meaning` `cloze_production` `shadow`

## Skills
`listening` `speaking` `pronunciation` `prosody` `reading` `grammar` `vocabulary`

## Adding a new tag
Add it here first, in the same commit. If it isn't in this file, `validate_vault` flags it.
