-- Migration 021: add the Comprasnet contract component enum value.
-- Kept separate because a new enum value cannot be used before transaction commit.

ALTER TYPE component_type
  ADD VALUE IF NOT EXISTS 'contratos_comprasnet';

NOTIFY pgrst, 'reload schema';
