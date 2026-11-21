-- ADDITIVE migration for Stage04-2; existing profiles are not modified.
BEGIN;
ALTER TABLE public.career_ai_edit_proposal
    ADD COLUMN IF NOT EXISTS applied_at timestamptz,
    ADD COLUMN IF NOT EXISTS applied_source_hash char(64);
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
      WHERE conrelid='public.career_ai_edit_proposal'::regclass AND contype='c'
        AND pg_get_constraintdef(oid) LIKE '%APPROVED_NOT_APPLIED%'
    LOOP
        EXECUTE format('ALTER TABLE public.career_ai_edit_proposal DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;
ALTER TABLE public.career_ai_edit_proposal
    DROP CONSTRAINT IF EXISTS career_ai_edit_proposal_stage04_status_check;
ALTER TABLE public.career_ai_edit_proposal
    ADD CONSTRAINT career_ai_edit_proposal_stage04_status_check
    CHECK (status IN ('DRAFT','APPROVED_NOT_APPLIED','CANCELLED','APPLIED'));
COMMIT;
