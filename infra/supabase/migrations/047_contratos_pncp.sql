ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS contrato_vigencia_inicio DATE,
  ADD COLUMN IF NOT EXISTS contrato_vigencia_fim DATE,
  ADD COLUMN IF NOT EXISTS contrato_dedicacao_exclusiva BOOLEAN,
  ADD COLUMN IF NOT EXISTS contrato_pncp_controle TEXT;

NOTIFY pgrst, 'reload schema';
