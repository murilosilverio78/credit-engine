CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS score_snapshot_versions (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  operation_id    UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  snapshot_id     UUID REFERENCES component_snapshots(id) ON DELETE SET NULL,
  parsed_result   JSONB NOT NULL,
  raw_result      JSONB,
  score_contrib   NUMERIC(5,2),
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archive_reason  TEXT NOT NULL DEFAULT 'snapshot_substituido'
);

CREATE INDEX IF NOT EXISTS idx_score_snapshot_versions_operation_archived
  ON score_snapshot_versions (operation_id, archived_at DESC);

ALTER TABLE score_snapshot_versions ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all_score_snapshot_versions"
    ON score_snapshot_versions
    FOR ALL
    TO service_role
    USING (TRUE)
    WITH CHECK (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE OR REPLACE FUNCTION archive_score_snapshot_before_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF OLD.component = 'score_engine'
     AND OLD.parsed_result IS DISTINCT FROM NEW.parsed_result
     AND OLD.parsed_result IS NOT NULL THEN
    INSERT INTO score_snapshot_versions (
      operation_id,
      snapshot_id,
      parsed_result,
      raw_result,
      score_contrib,
      started_at,
      completed_at,
      archive_reason
    ) VALUES (
      OLD.operation_id,
      OLD.id,
      OLD.parsed_result,
      OLD.raw_result,
      OLD.score_contrib,
      OLD.started_at,
      OLD.completed_at,
      'snapshot_substituido'
    );
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_archive_score_snapshot_before_update
  ON component_snapshots;

CREATE TRIGGER trg_archive_score_snapshot_before_update
  BEFORE UPDATE OF parsed_result ON component_snapshots
  FOR EACH ROW
  EXECUTE FUNCTION archive_score_snapshot_before_update();

NOTIFY pgrst, 'reload schema';
