-- Migration 011: degraded company data flag
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS dado_cadastral_degradado BOOLEAN DEFAULT FALSE;
