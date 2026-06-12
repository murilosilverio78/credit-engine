-- Migration 009: heartbeat_at para recovery seguro de opera??es
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_operations_heartbeat
  ON operations(heartbeat_at)
  WHERE status IN ('pending', 'processing');
