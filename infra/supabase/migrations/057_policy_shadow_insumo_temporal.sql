-- Adds the temporal-input classification for shadow observations.
-- Intentionally delivered only as a migration; do not apply manually.

ALTER TABLE policy_shadow_runs
  DROP CONSTRAINT IF EXISTS policy_shadow_runs_classe_geral_check;

ALTER TABLE policy_shadow_runs
  ADD CONSTRAINT policy_shadow_runs_classe_geral_check
  CHECK (
    classe_geral IN (
      'IGUAL',
      'ESPERADA',
      'SEM_DADOS',
      'INESPERADA',
      'INSUMO_TEMPORAL'
    )
  );

NOTIFY pgrst, 'reload schema';
