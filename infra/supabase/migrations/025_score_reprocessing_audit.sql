ALTER TYPE action_type
  ADD VALUE IF NOT EXISTS 'score_reprocessed';

NOTIFY pgrst, 'reload schema';
