-- Comparativo entre a política primária em sombra e sua versão de referência.
-- Entregue para aplicação controlada; não aplicar automaticamente.

ALTER TABLE policy_shadow_runs
  ADD COLUMN IF NOT EXISTS comparativo JSONB NULL,
  ADD COLUMN IF NOT EXISTS classe_comparativo TEXT NULL;

ALTER TABLE policy_shadow_runs
  DROP CONSTRAINT IF EXISTS policy_shadow_runs_classe_comparativo_check;

ALTER TABLE policy_shadow_runs
  ADD CONSTRAINT policy_shadow_runs_classe_comparativo_check
  CHECK (classe_comparativo IS NULL OR classe_comparativo IN ('IGUAL', 'ESPERADA', 'INESPERADA'));

NOTIFY pgrst, 'reload schema';
