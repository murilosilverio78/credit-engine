-- Migration 014: apply framing percentage to the gross contract balance

UPDATE eligibility_parameters
SET
  key = 'pct_max_contrato',
  label = 'Percentual maximo do valor do contrato'
WHERE key = 'pct_max_margem';

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS saldo_vincendo NUMERIC(15,2);

NOTIFY pgrst, 'reload schema';
