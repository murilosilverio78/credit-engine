-- Separa explicitamente análises manuais de produção e de teste.
-- Cotações Broadfactor já possuem o mesmo campo desde a migration 034.

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS ambiente VARCHAR(20) NOT NULL DEFAULT 'PRODUCAO';

CREATE INDEX IF NOT EXISTS idx_operations_source_ambiente_created_at
  ON operations (source, ambiente, created_at DESC);

NOTIFY pgrst, 'reload schema';
