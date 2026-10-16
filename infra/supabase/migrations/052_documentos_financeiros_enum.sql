-- Executar isoladamente antes da 053: PostgreSQL nao permite usar enum novo
-- na mesma transacao em que ele e adicionado.
ALTER TYPE component_type ADD VALUE IF NOT EXISTS 'documentos_financeiros';
