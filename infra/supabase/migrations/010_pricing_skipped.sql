-- Migration 010: campo pricing_skipped_reason para visibilidade do motivo de taxa n?o calculada
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS pricing_skipped_reason TEXT;
