-- Stage04 additive migration. Does not touch Word/Employee/Career/Excel records.
BEGIN;
CREATE TABLE IF NOT EXISTS public.career_ai_edit_proposal (
    proposal_id uuid PRIMARY KEY,
    import_id uuid NOT NULL,
    profile_id text NOT NULL,
    project_id text NOT NULL,
    project_index integer NOT NULL CHECK(project_index >= 0),
    source_path text NOT NULL,
    field text NOT NULL CHECK(field IN ('projectName','submittedRole','clientName','workDescription','technologiesText','periodText')),
    actual_field text NOT NULL,
    old_value text NOT NULL,
    new_value text NOT NULL,
    source_hash char(64) NOT NULL,
    project_name text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'DRAFT'
        CHECK(status IN ('DRAFT','APPROVED_NOT_APPLIED','CANCELLED')),
    created_at timestamptz NOT NULL DEFAULT NOW(),
    approved_at timestamptz,
    cancelled_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_career_ai_edit_import_id
    ON public.career_ai_edit_proposal(import_id, created_at DESC);
COMMIT;
