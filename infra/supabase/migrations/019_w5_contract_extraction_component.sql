-- Migration 019: add the contract extraction component enum value.
-- Kept separate because a new enum value cannot be used before transaction commit.

ALTER TYPE component_type
  ADD VALUE IF NOT EXISTS 'contrato_extracao';

NOTIFY pgrst, 'reload schema';
