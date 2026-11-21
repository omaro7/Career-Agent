-- Stage 02-6: additive, does not modify Excel imports, templates, or old Word records.
-- Execute AFTER Stage 02-5 001_word_import.sql + 002_word_customer_layout.sql.
BEGIN;
CREATE TABLE IF NOT EXISTS career_word_ai_run (
    id UUID PRIMARY KEY,
    import_id UUID NOT NULL REFERENCES career_word_import(id) ON DELETE RESTRICT,
    tenant_id UUID NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'FAILED', 'STALE')),
    focus TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    structured JSONB,
    evidence JSONB,
    warnings JSONB,
    meta JSONB,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_career_word_ai_run_history
    ON career_word_ai_run (tenant_id, import_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_career_word_ai_run_one_active
    ON career_word_ai_run (import_id)
    WHERE status IN ('QUEUED','RUNNING');
COMMIT;
