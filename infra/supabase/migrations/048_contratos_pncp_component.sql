-- Mantida separada para que o novo valor do enum possa ser usado em uma
-- migration posterior, após o commit desta alteração de tipo.
ALTER TYPE component_type
  ADD VALUE IF NOT EXISTS 'contratos_pncp';

NOTIFY pgrst, 'reload schema';
