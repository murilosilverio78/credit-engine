-- Manual upload pause state and token expiration for certificate uploads.
ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'manual_review';

ALTER TABLE upload_tasks
  ALTER COLUMN expires_at SET DEFAULT (NOW() + INTERVAL '48 hours');
