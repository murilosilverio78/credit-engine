-- =============================================================
-- Migration 033 — separação entre dados de teste e de produção
--
-- Executar ANTES de trocar as variáveis da Broadfactor no Railway.
-- Marca tudo o que existe hoje como TESTE (no caso da ingestão) ou
-- conforme a origem, e passa a tratar o que vier depois como PRODUCAO.
--
-- Regras de classificação:
--   TESTE      → origens de teste (playwright_e2e, debug_e2e, frontend_mvp,
--                *_smoke_test, teste_migracao_asyncio) em qualquer data;
--                e TUDO que veio da ingestão Broadfactor até agora, porque
--                a integração apontava para a API de desenvolvimento.
--   PRODUCAO   → análises manuais (admin_ui), que sempre usaram dados reais
--                de Portal e Comprasnet; e tudo criado a partir de agora.
--
-- Execução MANUAL no Supabase SQL Editor. Idempotente.
-- =============================================================

BEGIN;

-- -------------------------------------------------------------
-- 1. Coluna em operations, cotacoes_broadfactor e descartes_ingestao
-- -------------------------------------------------------------
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS ambiente VARCHAR(10) NOT NULL DEFAULT 'PRODUCAO';

ALTER TABLE cotacoes_broadfactor
  ADD COLUMN IF NOT EXISTS ambiente VARCHAR(10) NOT NULL DEFAULT 'PRODUCAO';

ALTER TABLE descartes_ingestao
  ADD COLUMN IF NOT EXISTS ambiente VARCHAR(10) NOT NULL DEFAULT 'PRODUCAO';

DO $$ BEGIN
  ALTER TABLE operations ADD CONSTRAINT operations_ambiente_check
    CHECK (ambiente IN ('TESTE', 'PRODUCAO'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE cotacoes_broadfactor ADD CONSTRAINT cotacoes_ambiente_check
    CHECK (ambiente IN ('TESTE', 'PRODUCAO'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE descartes_ingestao ADD CONSTRAINT descartes_ambiente_check
    CHECK (ambiente IN ('TESTE', 'PRODUCAO'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -------------------------------------------------------------
-- 2. Classificação do que já existe.
--    Só atua em linhas ainda não classificadas manualmente: a coluna
--    nasce com 'PRODUCAO', então a reexecução é inofensiva porque os
--    critérios abaixo dependem de origem e de data de criação.
-- -------------------------------------------------------------

-- 2.1 Origens de teste, em qualquer data
UPDATE operations SET ambiente = 'TESTE'
 WHERE ambiente <> 'TESTE'
   AND (source IN ('playwright_e2e', 'debug_e2e', 'frontend_mvp',
                   'teste_migracao_asyncio')
        OR source LIKE '%smoke_test%'
        OR source LIKE 'teste%');

-- 2.2 Ingestão Broadfactor anterior à virada (API de desenvolvimento)
UPDATE operations SET ambiente = 'TESTE'
 WHERE ambiente <> 'TESTE'
   AND source = 'broadfactor_ingestao'
   AND created_at < NOW();

UPDATE cotacoes_broadfactor SET ambiente = 'TESTE'
 WHERE ambiente <> 'TESTE'
   AND created_at < NOW();

UPDATE descartes_ingestao SET ambiente = 'TESTE'
 WHERE ambiente <> 'TESTE'
   AND estagio = 'S0_INGESTAO'
   AND created_at < NOW();

-- -------------------------------------------------------------
-- 3. Índices para os filtros de métrica
-- -------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_operations_ambiente
  ON operations (ambiente, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_cotacoes_ambiente
  ON cotacoes_broadfactor (ambiente);

-- -------------------------------------------------------------
-- 4. Visão de carteira, já filtrando teste (sem leitor ainda; serve a
--    consultas de acompanhamento e à futura exposição por cedente).
-- -------------------------------------------------------------
CREATE OR REPLACE VIEW vw_operacoes_producao
WITH (security_invoker = true) AS
SELECT o.*
  FROM operations o
 WHERE o.ambiente = 'PRODUCAO';

REVOKE ALL ON vw_operacoes_producao FROM PUBLIC, anon, authenticated;
GRANT SELECT ON vw_operacoes_producao TO service_role;

COMMIT;

-- -------------------------------------------------------------
-- 5. Conferência
-- -------------------------------------------------------------
SELECT ambiente, source, count(*) AS operacoes,
       min(created_at)::date AS primeira,
       max(created_at)::date AS ultima
  FROM operations
 GROUP BY ambiente, source
 ORDER BY ambiente, operacoes DESC;

NOTIFY pgrst, 'reload schema';
