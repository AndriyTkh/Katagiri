-- Katagiri schema, migration 0001: whole initial schema.
--
-- One migration, all DDL. Classification (source-of-truth vs derived) and the
-- rationale for non-obvious columns live in docs/db-schema.md; this file stays
-- DDL-only. No scheduler state (Anki owns scheduling), no strength columns in
-- source-of-truth tables, no hash-chain columns.
--
-- This file must not contain transaction control (BEGIN/COMMIT/END TRANSACTION/
-- ROLLBACK/VACUUM) or touch user_version: db.py wraps each migration in exactly
-- one transaction and stamps the version itself, and validates that on load.
--
-- Timestamps are ISO-8601 UTC to whole seconds, exactly 'YYYY-MM-DDTHH:MM:SSZ'
-- (20 characters), enforced by a GLOB CHECK on every timestamp column. The
-- format is fixed-width on purpose: these columns are compared and sorted
-- lexicographically, and mixing in fractional seconds would silently break that
-- ordering ('...:00Z' sorts *after* '...:00.5Z'). Sub-second ordering comes from
-- the ULID primary keys instead. Day keys are local 'YYYY-MM-DD'.
--
-- Booleans are INTEGER 0/1 with CHECK constraints. Derived tables are created
-- here but evolve by drop-and-rebuild scripts, so they never carry foreign keys
-- pointing at other derived tables.

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: append-only event log
-- ---------------------------------------------------------------------------

CREATE TABLE event (
    id            TEXT PRIMARY KEY,        -- ULID: time-sortable, client-generated
    dedupe_key    TEXT UNIQUE,             -- nullable; idempotent retries collapse here
    ts_device     TEXT NOT NULL,           -- ISO-8601 UTC, clock of the device
    ts_server     TEXT NOT NULL,           -- ISO-8601 UTC, clock of this server
    tz            TEXT NOT NULL,           -- IANA zone in force on the device
    day_key       TEXT NOT NULL,           -- YYYY-MM-DD *local*: streaks/daily rollups
    session_id    TEXT NOT NULL,
    type          TEXT NOT NULL,           -- open vocabulary: review, review_batch,
                                           -- study_session, mark_known, regen_yomitan,
                                           -- lesson_close, seek, mining,
                                           -- tombstone_session, ...
    item_id       TEXT,                    -- soft reference, deliberately no FK
    direction     TEXT,
    grade         INTEGER,
    latency_ms    INTEGER,
    answer_given  TEXT,                    -- what the learner produced
    expected      TEXT,                    -- what was being asked for
    audio_ref     TEXT,
    media_ref     TEXT,
    payload       TEXT,                    -- JSON, type-specific extras; never secrets

    CHECK (ts_device GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (ts_server GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (day_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    CHECK (direction IS NULL OR direction IN (
        'listen_to_meaning', 'meaning_to_speech', 'read_to_meaning',
        'cloze_production', 'shadow')),
    CHECK (grade IS NULL OR grade BETWEEN 1 AND 4),
    CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CHECK (payload IS NULL OR json_valid(payload))
);

-- Append-only. These fire on INSERT OR REPLACE too, because db.connect() sets
-- PRAGMA recursive_triggers = ON; without it REPLACE's implicit delete would
-- skip the delete trigger and silently overwrite logged history.
CREATE TRIGGER event_no_update BEFORE UPDATE ON event
BEGIN
    SELECT RAISE(ABORT, 'event log is append-only');
END;

CREATE TRIGGER event_no_delete BEFORE DELETE ON event
BEGIN
    SELECT RAISE(ABORT, 'event log is append-only');
END;

CREATE INDEX event_day_key_idx    ON event(day_key);
CREATE INDEX event_item_id_idx    ON event(item_id);
CREATE INDEX event_type_idx       ON event(type);
CREATE INDEX event_session_id_idx ON event(session_id);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: append-only observation log (rubric-scored performances)
-- ---------------------------------------------------------------------------

CREATE TABLE observation (
    id             TEXT PRIMARY KEY,       -- ULID
    ts             TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    item_id        TEXT,                   -- soft reference, no FK
    task_type      TEXT NOT NULL,
    expected       TEXT,
    produced       TEXT,
    unassisted     INTEGER NOT NULL,       -- 0/1: hints or lookups used?
    coverage_band  TEXT NOT NULL,          -- comprehension band of the surrounding input
    rubric_version TEXT NOT NULL,          -- scores are only comparable within a version
    media_ref      TEXT,

    CHECK (ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (unassisted IN (0, 1)),
    CHECK (coverage_band IN ('>=95', '80-95', '<80'))
);

CREATE TRIGGER observation_no_update BEFORE UPDATE ON observation
BEGIN
    SELECT RAISE(ABORT, 'observation log is append-only');
END;

CREATE TRIGGER observation_no_delete BEFORE DELETE ON observation
BEGIN
    SELECT RAISE(ABORT, 'observation log is append-only');
END;

CREATE INDEX observation_item_id_idx    ON observation(item_id);
CREATE INDEX observation_session_id_idx ON observation(session_id);
CREATE INDEX observation_ts_idx         ON observation(ts);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: lessons
-- ---------------------------------------------------------------------------

CREATE TABLE lesson (
    id            TEXT PRIMARY KEY,
    opened_ts     TEXT NOT NULL,
    closed_ts     TEXT,                    -- NULL while the lesson is open
    session_id    TEXT,                    -- session the lesson was conducted in
    topic         TEXT NOT NULL,
    objective     TEXT NOT NULL,           -- observable can-do statement
    next_step     TEXT,                    -- written at close, not at open
    revisit_after TEXT,                    -- YYYY-MM-DD local
    free_notes    TEXT,                    -- hard-capped so notes cannot become prose

    CHECK (opened_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (closed_ts IS NULL OR closed_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (free_notes IS NULL OR length(free_notes) <= 500),
    CHECK (revisit_after IS NULL
           OR revisit_after GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    CHECK (closed_ts IS NULL OR closed_ts >= opened_ts)
);

CREATE INDEX lesson_opened_ts_idx ON lesson(opened_ts);
CREATE INDEX lesson_topic_idx     ON lesson(topic);

-- One row per question that was served but not answered on the spot.
CREATE TABLE lesson_unresolved (
    id           INTEGER PRIMARY KEY,
    lesson_id    TEXT NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    created_ts   TEXT NOT NULL,
    resolved_ts  TEXT,                     -- NULL = still unresolved

    CHECK (created_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (resolved_ts IS NULL OR resolved_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

CREATE INDEX lesson_unresolved_lesson_idx ON lesson_unresolved(lesson_id);
CREATE INDEX lesson_unresolved_open_idx   ON lesson_unresolved(lesson_id)
    WHERE resolved_ts IS NULL;

CREATE TABLE lesson_media (
    lesson_id TEXT NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
    media_id  TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    note      TEXT,
    PRIMARY KEY (lesson_id, media_id)
);

CREATE INDEX lesson_media_media_idx ON lesson_media(media_id);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: studied units and their graph
-- ---------------------------------------------------------------------------

CREATE TABLE item (
    id                 TEXT PRIMARY KEY,   -- deterministic: w-/s-/k-/g- prefixed
    kind               TEXT NOT NULL,
    home_topic         TEXT,
    kanji              TEXT,
    reading            TEXT,
    pitch              INTEGER,            -- drop position; 0 = heiban, NULL = unknown
    mora_count         INTEGER,
    register           TEXT,               -- casual/polite/humble/archaic/role-language...
    pos                TEXT,
    verb_class         TEXT,
    jlpt               TEXT,               -- 'N5'..'N1'
    level              TEXT,               -- curriculum level, 'A0'...
    understanding      INTEGER,            -- 1-5 self-rating, grammar items only
    production_eligible INTEGER NOT NULL DEFAULT 1,  -- 0 = receptive only
    sealed             INTEGER NOT NULL DEFAULT 0,   -- held-out probe / canary exclusion
    lexeme_ref         TEXT,               -- soft ref into derived lexeme, no FK
    jmdict_seq         INTEGER,            -- soft ref into derived jmdict_entry, no FK
    created_ts         TEXT NOT NULL,

    CHECK (created_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (kind IN ('word', 'kanji', 'grammar', 'sentence')),
    CHECK (verb_class IS NULL OR verb_class IN ('godan', 'ichidan', 'irregular')),
    CHECK (understanding IS NULL OR understanding BETWEEN 1 AND 5),
    CHECK (pitch IS NULL OR pitch >= 0),
    CHECK (mora_count IS NULL OR mora_count > 0),
    CHECK (production_eligible IN (0, 1)),
    CHECK (sealed IN (0, 1))
);

CREATE INDEX item_kind_idx       ON item(kind);
CREATE INDEX item_home_topic_idx ON item(home_topic);
CREATE INDEX item_lexeme_ref_idx ON item(lexeme_ref);
CREATE INDEX item_kanji_idx      ON item(kanji);

-- Grammar DAG (and unlock relations) as edges, never as an array column.
CREATE TABLE item_edge (
    from_id   TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    to_id     TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
    edge_type TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id, edge_type),
    CHECK (edge_type IN ('prereq', 'unlock')),
    CHECK (from_id <> to_id)
);

CREATE INDEX item_edge_to_idx ON item_edge(to_id, edge_type);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: identifier aliases (renames keep old ids resolvable)
-- ---------------------------------------------------------------------------

CREATE TABLE alias (
    alias_id     TEXT PRIMARY KEY,         -- the retired id; PK also serves the
                                           -- alias(alias_id) lookup index
    canonical_id TEXT NOT NULL,
    reason       TEXT,
    created_ts   TEXT NOT NULL,

    CHECK (created_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (alias_id <> canonical_id)
);

CREATE INDEX alias_canonical_idx ON alias(canonical_id);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: manual known/unknown marks (override the Anki mirror)
-- ---------------------------------------------------------------------------

CREATE TABLE manual_marks (
    item_id TEXT NOT NULL,                 -- soft ref: marks may predate the item row
    mark    TEXT NOT NULL,
    ts      TEXT NOT NULL,
    note    TEXT,
    PRIMARY KEY (item_id, ts),             -- history kept; latest ts wins
    CHECK (ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (mark IN ('known', 'unknown', 'suspect'))
);

CREATE INDEX manual_marks_mark_idx ON manual_marks(mark);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: media the learner studies from
-- ---------------------------------------------------------------------------

CREATE TABLE media (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,        -- anime/drama/podcast/audiobook/...
    title            TEXT,
    source           TEXT,                 -- URL or episode reference
    transcript_path  TEXT,
    register_profile TEXT,                 -- expected register mix, for task selection
    sub_delay_ms     INTEGER NOT NULL DEFAULT 0,  -- signed subtitle offset correction
    added_ts         TEXT NOT NULL,

    CHECK (added_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- Live "what is on screen right now" pointer. Single row by construction; the
-- caller derives is_live from the age of updated_ts rather than storing a flag.
CREATE TABLE media_heartbeat (
    id             INTEGER PRIMARY KEY,
    media_id       TEXT,
    anchor_ms      INTEGER,
    displayed_text TEXT,
    updated_ts     TEXT NOT NULL,

    CHECK (id = 1),
    CHECK (updated_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- ---------------------------------------------------------------------------
-- SOURCE OF TRUTH: settings (global with per-topic overrides)
-- ---------------------------------------------------------------------------

CREATE TABLE settings (
    scope      TEXT NOT NULL DEFAULT 'global',
    key        TEXT NOT NULL,
    value      TEXT,
    updated_ts TEXT NOT NULL,
    PRIMARY KEY (scope, key),
    CHECK (updated_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- ---------------------------------------------------------------------------
-- DERIVED: version registry for every rebuildable artefact
-- ---------------------------------------------------------------------------

CREATE TABLE metadata (
    key        TEXT PRIMARY KEY,           -- tokenizer_version, jmdict_version,
                                           -- protocol_version, rubric_version, ...
    value      TEXT,
    updated_ts TEXT NOT NULL,
    CHECK (updated_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- ---------------------------------------------------------------------------
-- DERIVED: dictionary-side identity and the item <-> lexeme crosswalk
-- ---------------------------------------------------------------------------

CREATE TABLE lexeme (
    id           TEXT PRIMARY KEY,         -- 'lx-<seq>-<sense_idx>'
    jmdict_seq   INTEGER NOT NULL,
    sense_idx    INTEGER NOT NULL,         -- sense granularity: senses drift apart
    headword     TEXT,
    reading      TEXT,
    pos          TEXT,
    gloss_en     TEXT,
    dict_version TEXT NOT NULL,            -- which import produced this row
    UNIQUE (jmdict_seq, sense_idx)
);

CREATE INDEX lexeme_headword_idx ON lexeme(headword);
CREATE INDEX lexeme_reading_idx  ON lexeme(reading);

CREATE TABLE morph_lexeme_map (
    item_id    TEXT NOT NULL,              -- no FK: rebuilt wholesale by the importer
    lexeme_id  TEXT NOT NULL,
    surface    TEXT,                       -- inflected form that produced the link
    method     TEXT,                       -- how the link was made (auditable)
    confidence REAL,
    PRIMARY KEY (item_id, lexeme_id),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE INDEX morph_lexeme_map_lexeme_idx ON morph_lexeme_map(lexeme_id);

-- ---------------------------------------------------------------------------
-- DERIVED: Anki mirror (read-only snapshot of the collection)
-- ---------------------------------------------------------------------------

CREATE TABLE anki_cards (
    card_id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL,
    deck    TEXT,
    ivl     INTEGER,                       -- days; ivl >= 21 feeds the known set
    due     INTEGER,                       -- Anki's own scheduling, mirrored not owned
    reps    INTEGER,
    lapses  INTEGER,
    mod     INTEGER
);

CREATE INDEX anki_cards_note_idx ON anki_cards(note_id);
CREATE INDEX anki_cards_ivl_idx  ON anki_cards(ivl);

CREATE TABLE anki_notes (
    note_id INTEGER PRIMARY KEY,
    model   TEXT,
    fields  TEXT,                          -- JSON array in notetype field order (names are renameable; ordinals are stable)
    tags    TEXT,
    mod     INTEGER,
    CHECK (fields IS NULL OR json_valid(fields))
);

-- Which Katagiri item a mirrored note stands for; resolved at mirror time.
CREATE TABLE anki_item_map (
    note_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    method  TEXT,
    PRIMARY KEY (note_id, item_id)
);

CREATE INDEX anki_item_map_item_idx ON anki_item_map(item_id);

CREATE TABLE mirror_meta (
    id                  INTEGER PRIMARY KEY,
    snapshot_ts         TEXT NOT NULL,
    collection_mtime    INTEGER,           -- staleness check without reopening Anki
    anki_schema_version INTEGER,

    CHECK (id = 1),
    CHECK (snapshot_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- ---------------------------------------------------------------------------
-- DERIVED: JMdict import (jmdict-simplified), sense level
-- ---------------------------------------------------------------------------

CREATE TABLE jmdict_entry (
    seq          INTEGER PRIMARY KEY,      -- JMdict ent_seq, the stable upstream id
    is_common    INTEGER NOT NULL DEFAULT 0,
    dict_version TEXT NOT NULL,
    CHECK (is_common IN (0, 1))
);

CREATE TABLE jmdict_kanji (
    seq   INTEGER NOT NULL,
    kanji TEXT NOT NULL,
    pri   TEXT,                            -- upstream priority tags, kept verbatim
    PRIMARY KEY (seq, kanji)
);

CREATE INDEX jmdict_kanji_kanji_idx ON jmdict_kanji(kanji);

CREATE TABLE jmdict_reading (
    seq     INTEGER NOT NULL,
    reading TEXT NOT NULL,
    pri     TEXT,
    PRIMARY KEY (seq, reading)
);

CREATE INDEX jmdict_reading_reading_idx ON jmdict_reading(reading);

CREATE TABLE jmdict_sense (
    seq       INTEGER NOT NULL,
    sense_idx INTEGER NOT NULL,
    pos       TEXT,
    gloss_en  TEXT,
    misc      TEXT,                        -- usage tags: arch, uk, hon, vulg, ...
    PRIMARY KEY (seq, sense_idx)
);

-- ---------------------------------------------------------------------------
-- DERIVED: kanjium pitch accent
-- ---------------------------------------------------------------------------

CREATE TABLE pitch_accent (
    surface        TEXT NOT NULL,
    reading        TEXT NOT NULL,
    accent         TEXT NOT NULL,          -- upstream notation; a word may have several
    source_version TEXT,
    PRIMARY KEY (surface, reading, accent)
);

CREATE INDEX pitch_accent_reading_idx ON pitch_accent(reading);

-- ---------------------------------------------------------------------------
-- DERIVED: sentence search (content table + two FTS5 indexes)
-- ---------------------------------------------------------------------------

-- Content table for both FTS indexes. `rowid` is declared explicitly as an
-- INTEGER PRIMARY KEY so that the value the FTS indexes join on is a real,
-- stable column: an implicit rowid can be renumbered by VACUUM, which would
-- silently point every FTS hit at the wrong sentence. item_id is the logical
-- key and stays UNIQUE.
CREATE TABLE sentence_text (
    rowid             INTEGER PRIMARY KEY,
    item_id           TEXT NOT NULL UNIQUE,
    jp                TEXT NOT NULL,       -- raw Japanese, no inserted spaces
    shadow_text       TEXT,                -- fugashi output, space-segmented
    dict_version      TEXT,
    tokenizer_version TEXT
);

-- Word search over the space-segmented shadow text. unicode61 tokenizes on the
-- inserted spaces, so this is real word matching.
CREATE VIRTUAL TABLE fts_sentence_words USING fts5(
    shadow_text,
    content='sentence_text',
    content_rowid='rowid',
    tokenize='unicode61'
);

-- Substring search over the raw text. Needed alongside the word index because
-- trigram alone returns nothing for queries shorter than 3 characters.
CREATE VIRTUAL TABLE fts_sentence_tri USING fts5(
    jp,
    content='sentence_text',
    content_rowid='rowid',
    tokenize='trigram'
);

-- ---------------------------------------------------------------------------
-- DERIVED: subtitle lines (window queries around a timestamp)
-- ---------------------------------------------------------------------------

CREATE TABLE sub_lines (
    media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms   INTEGER NOT NULL,
    text     TEXT NOT NULL,
    PRIMARY KEY (media_id, idx),
    CHECK (end_ms >= start_ms)
);

CREATE INDEX sub_lines_time_idx ON sub_lines(media_id, start_ms);

-- ---------------------------------------------------------------------------
-- DERIVED: computed caches (coverage, strength, frequency, comprehension debt)
-- ---------------------------------------------------------------------------

-- Folds over the known_set view plus sub_lines / sentence_text token counts.
CREATE TABLE coverage_cache (
    scope_kind  TEXT NOT NULL,             -- 'media' | 'episode' | 'sentence' | 'topic'
    scope_id    TEXT NOT NULL,
    known_ratio REAL,
    coverage_band TEXT,
    total_tokens INTEGER,
    known_tokens INTEGER,
    computed_ts TEXT NOT NULL,
    PRIMARY KEY (scope_kind, scope_id),
    CHECK (coverage_band IS NULL OR coverage_band IN ('>=95', '80-95', '<80')),
    CHECK (computed_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

-- Everything here is a fold over event/observation; never written by hand.
CREATE TABLE item_stat_cache (
    item_id            TEXT PRIMARY KEY,
    strength           REAL,
    comprehension_debt REAL,
    frequency_rank     INTEGER,
    review_count       INTEGER,
    last_event_ts      TEXT,
    computed_ts        TEXT NOT NULL,

    CHECK (last_event_ts IS NULL OR last_event_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    CHECK (computed_ts GLOB
        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z')
);

CREATE INDEX item_stat_cache_strength_idx ON item_stat_cache(strength);

-- ---------------------------------------------------------------------------
-- DERIVED VIEWS
-- ---------------------------------------------------------------------------

-- A lesson's outcome is never stored as prose: it is the shape of the
-- observations recorded while the lesson was open, plus its unresolved tally.
-- The window is inclusive of closed_ts: an observation recorded in the same
-- second the lesson closed belongs to that lesson.
CREATE VIEW lesson_outcome AS
SELECT
    l.id                                        AS lesson_id,
    l.topic                                     AS topic,
    l.objective                                 AS objective,
    l.opened_ts                                 AS opened_ts,
    l.closed_ts                                 AS closed_ts,
    COUNT(o.id)                                 AS observation_count,
    COUNT(DISTINCT o.item_id)                   AS item_count,
    SUM(COALESCE(o.unassisted, 0))              AS unassisted_count,
    (SELECT COUNT(*) FROM lesson_unresolved u
      WHERE u.lesson_id = l.id)                 AS unresolved_served,
    (SELECT COUNT(*) FROM lesson_unresolved u
      WHERE u.lesson_id = l.id AND u.resolved_ts IS NULL)
                                                AS unresolved_open
FROM lesson l
LEFT JOIN observation o
       ON o.ts >= l.opened_ts
      AND o.ts <= COALESCE(l.closed_ts, '9999-12-31T23:59:59Z')
      AND (l.session_id IS NULL OR o.session_id = l.session_id)
GROUP BY l.id;

-- Known set = the Anki mirror's maturity rule (ivl >= 21 days) with manual
-- known/unknown marks overriding it. Rows exist for every item *and* for any
-- id that has only ever been marked, so a mark on a not-yet-imported item is
-- not silently dropped.
--
-- 'suspect' deliberately does NOT decide is_known: it is a flag for review, not
-- a verdict, so the mirror still decides and the flag is exposed separately.
-- is_known and source therefore branch on the same predicate.
CREATE VIEW known_set AS
WITH latest_mark AS (
    SELECT m.item_id, m.mark
      FROM manual_marks m
     WHERE m.ts = (SELECT MAX(m2.ts) FROM manual_marks m2
                    WHERE m2.item_id = m.item_id)
),
mirror_known AS (
    SELECT DISTINCT map.item_id
      FROM anki_item_map map
      JOIN anki_cards c ON c.note_id = map.note_id
     WHERE c.ivl >= 21
),
all_ids AS (
    SELECT id AS item_id FROM item
    UNION
    SELECT item_id FROM manual_marks
)
SELECT a.item_id AS item_id,
       CASE
           WHEN lm.mark IN ('known', 'unknown')
               THEN CASE WHEN lm.mark = 'known' THEN 1 ELSE 0 END
           WHEN mk.item_id IS NOT NULL THEN 1
           ELSE 0
       END AS is_known,
       CASE
           WHEN lm.mark IN ('known', 'unknown') THEN 'manual'
           ELSE 'anki'
       END AS source,
       CASE WHEN lm.mark = 'suspect' THEN 1 ELSE 0 END AS suspect,
       lm.mark AS manual_mark
  FROM all_ids a
  LEFT JOIN latest_mark  lm ON lm.item_id = a.item_id
  LEFT JOIN mirror_known mk ON mk.item_id = a.item_id;
