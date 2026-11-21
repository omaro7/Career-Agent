CREATE TABLE IF NOT EXISTS career_word_import (
    id UUID PRIMARY KEY,
    original_filename TEXT NOT NULL,
    source_path TEXT NOT NULL,
    extracted JSONB NOT NULL,
    reviewed JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'REVIEW',
    template_id TEXT,
    output_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_career_word_import_created_at
    ON career_word_import(created_at DESC);
