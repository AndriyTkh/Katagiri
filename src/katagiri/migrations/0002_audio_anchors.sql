-- Katagiri schema, migration 0002: audio-anchor reference on `item` (D-38).
--
-- Scoped exception to the whole-schema-in-one-migration rule (D-12/D-27),
-- filed as D-38 in docs/decisions-ledger.md before this file existed. Additive
-- only: no rename, no drop, no derived-table rebuild, no new table. `item` is
-- the one row-per-studied-unit table (`kind` includes 'sentence'), so this is
-- also how a sentence item gets an audio anchor -- there is no separate
-- source-of-truth `sentence` table (see docs/db-schema.md).
--
-- Same rule as 0001: this file must not contain transaction control (BEGIN/
-- COMMIT/END TRANSACTION/ROLLBACK/VACUUM) or touch user_version: db.py wraps
-- each migration in exactly one transaction and stamps the version itself,
-- and validates that on load.
--
-- `item` carries no append-only trigger (only `event` and `observation` do,
-- per 0001), so adding these columns needs no trigger recreation.

ALTER TABLE item ADD COLUMN audio_source TEXT;
-- Reference to the recording an audio anchor points into (e.g. an Irodori
-- unit's MP3 track name/path). Nullable: most items have no anchor.

ALTER TABLE item ADD COLUMN audio_offset_ms INTEGER
    CHECK (audio_offset_ms IS NULL OR audio_offset_ms >= 0);
-- Position within audio_source, in milliseconds -- same unit and naming
-- pattern as sub_lines.start_ms/end_ms and media_heartbeat.anchor_ms.
-- Nullable, and meaningless without audio_source.

ALTER TABLE item ADD COLUMN text_only INTEGER NOT NULL DEFAULT 0
    CHECK (text_only IN (0, 1));
-- 0/1: 1 marks an item as text-only -- not eligible for A0 production drills
-- (e.g. it has no natural spoken realization to anchor). Distinct from
-- production_eligible (receptive-only material such as archaic/role-language
-- forms): text_only is about the *absence* of a production-worthy audio
-- anchor at A0, not about the register of the item itself. Default 0 so
-- existing and newly imported rows stay production-eligible unless flagged.
