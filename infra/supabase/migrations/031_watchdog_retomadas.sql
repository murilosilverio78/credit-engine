ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS retomadas_count INTEGER NOT NULL DEFAULT 0;

NOTIFY pgrst, 'reload schema';
