-- Stage 02-6 cancellation: additive changes to the AI run status only.
-- No change to Word sources, reviewed profiles, templates, or other tables.
BEGIN;
ALTER TABLE career_word_ai_run DROP CONSTRAINT IF EXISTS career_word_ai_run_status_check;
ALTER TABLE career_word_ai_run
    ADD CONSTRAINT career_word_ai_run_status_check
    CHECK (status IN ('QUEUED','RUNNING','CANCELLING','CANCELLED','DONE','FAILED','STALE'));
DROP INDEX IF EXISTS idx_career_word_ai_run_one_active;
CREATE UNIQUE INDEX idx_career_word_ai_run_one_active
    ON career_word_ai_run(import_id)
    WHERE status IN ('QUEUED','RUNNING','CANCELLING');
COMMIT;
