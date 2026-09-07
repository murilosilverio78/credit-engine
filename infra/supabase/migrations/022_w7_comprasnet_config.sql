-- Migration 022: configure Comprasnet contract enrichment and source precedence.

ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS valor_global_contrato NUMERIC(15,2),
  ADD COLUMN IF NOT EXISTS fonte_prazo_vincendo VARCHAR(30),
  ADD COLUMN IF NOT EXISTS fonte_valor_global VARCHAR(30);

DO $$ BEGIN
  ALTER TABLE operations
    ADD CONSTRAINT operations_fonte_prazo_vincendo_check
    CHECK (
      fonte_prazo_vincendo IS NULL
      OR fonte_prazo_vincendo IN ('COMPRASNET', 'EXTRACAO_LLM', 'DEFAULT')
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE operations
    ADD CONSTRAINT operations_fonte_valor_global_check
    CHECK (
      fonte_valor_global IS NULL
      OR fonte_valor_global IN ('COMPRASNET', 'EXTRACAO_LLM')
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

INSERT INTO component_config (
  component,
  enabled,
  timeout_seconds,
  max_retries,
  cache_ttl_hours,
  weight,
  description
) VALUES (
  'contratos_comprasnet',
  TRUE,
  90,
  3,
  0,
  NULL,
  'Contrato, faturas e empenhos pela API publica Comprasnet'
)
ON CONFLICT (component) DO NOTHING;

INSERT INTO component_snapshots (operation_id, component, status)
SELECT id, 'contratos_comprasnet', 'pending'
FROM operations
WHERE completed_at IS NULL
  AND status IN ('pending', 'processing')
  AND cotacao_id IS NOT NULL
ON CONFLICT (operation_id, component) DO NOTHING;

NOTIFY pgrst, 'reload schema';
