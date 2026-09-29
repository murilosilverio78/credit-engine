ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS uasg VARCHAR(6);

DO $$ BEGIN
  ALTER TABLE operations
    ADD CONSTRAINT operations_uasg_check
    CHECK (uasg IS NULL OR uasg ~ '^[0-9]{6}$');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
