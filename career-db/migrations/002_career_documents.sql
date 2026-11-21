-- Additive migration. Does not touch company profile or Excel tables.
BEGIN;
CREATE TABLE IF NOT EXISTS career_documents (
    id uuid PRIMARY KEY,
    filename text NOT NULL,
    sha256 char(64) NOT NULL,
    byte_size integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 10485760),
    document_type text NOT NULL DEFAULT 'career_word' CHECK (document_type = 'career_word'),
    status text NOT NULL DEFAULT 'needs_review' CHECK (status IN ('needs_review','approved')),
    extraction jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(extraction)='object'),
    blocks jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(blocks)='array'),
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_by text NOT NULL,
    updated_by text NOT NULL,
    approved_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz
);
CREATE TABLE IF NOT EXISTS career_document_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES career_documents(id) ON DELETE RESTRICT,
    action text NOT NULL CHECK (action IN ('created','updated','approved')),
    actor text NOT NULL,
    old_version integer,
    new_version integer NOT NULL,
    old_extraction jsonb,
    new_extraction jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_career_documents_created_at ON career_documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_career_documents_status ON career_documents(status);
CREATE INDEX IF NOT EXISTS idx_career_audit_document ON career_document_audit(document_id, created_at DESC);
COMMIT;
