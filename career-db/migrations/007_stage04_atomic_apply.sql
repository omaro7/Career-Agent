-- Stage04-2 atomic Word apply: adds approval audit status columns only.
-- No Word/Excel/employee/career_project data is changed by this migration.
BEGIN;
ALTER TABLE public.career_ai_edit_proposal
  ADD COLUMN IF NOT EXISTS applied_at timestamptz;
ALTER TABLE public.career_ai_edit_proposal
  ADD COLUMN IF NOT EXISTS applied_source_hash char(64);
-- Explicitly remove only the known Stage04 original status constraint(s).
ALTER TABLE public.career_ai_edit_proposal
  DROP CONSTRAINT IF EXISTS career_ai_edit_proposal_status_check;
ALTER TABLE public.career_ai_edit_proposal
  DROP CONSTRAINT IF EXISTS career_ai_edit_proposal_stage04_status_check;
ALTER TABLE public.career_ai_edit_proposal
  ADD CONSTRAINT career_ai_edit_proposal_stage04_status_check
  CHECK (status IN ('DRAFT','APPROVED_NOT_APPLIED','CANCELLED','APPLIED'));
COMMIT;
