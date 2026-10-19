-- Registra o componente pagamentos_portal no enum component_type.
-- Sem este valor, qualquer leitura/gravação em component_snapshots com
-- component = 'pagamentos_portal' falha (erro 22P02), e o endpoint interno
-- de executar componente responde 500.
ALTER TYPE component_type ADD VALUE IF NOT EXISTS 'pagamentos_portal';

NOTIFY pgrst, 'reload schema';
