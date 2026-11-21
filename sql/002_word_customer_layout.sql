-- Apply AFTER the previous Stage 02-5 sql/001_word_import.sql migration.
-- Non-destructive additive columns; existing Word imports and Excel records remain unchanged.
BEGIN;
ALTER TABLE career_word_import
    ADD COLUMN IF NOT EXISTS tenant_id UUID NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001';
ALTER TABLE career_word_import
    ADD COLUMN IF NOT EXISTS structured JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE career_word_import
    ADD COLUMN IF NOT EXISTS render_meta JSONB;
CREATE INDEX IF NOT EXISTS idx_career_word_import_tenant_created
    ON career_word_import (tenant_id, created_at DESC);
COMMIT;
