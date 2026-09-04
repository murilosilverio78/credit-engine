-- Limit automatic retries while preserving the original operation and snapshots.

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS analysis_attempts INTEGER NOT NULL DEFAULT 1;

DO $$ BEGIN
  ALTER TABLE operations
    ADD CONSTRAINT operations_analysis_attempts_check
    CHECK (analysis_attempts >= 1);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON COLUMN operations.analysis_attempts IS
  'Total analysis attempts, including the initial execution';

NOTIFY pgrst, 'reload schema';
