-- MVP admin UI identifies override requesters and reviewers by display name.
ALTER TABLE credit_overrides
  ALTER COLUMN requested_by TYPE text USING requested_by::text;

ALTER TABLE credit_overrides
  ALTER COLUMN reviewed_by TYPE text USING reviewed_by::text;

ALTER TABLE audit_trail
  ALTER COLUMN actor_id TYPE text USING actor_id::text;
