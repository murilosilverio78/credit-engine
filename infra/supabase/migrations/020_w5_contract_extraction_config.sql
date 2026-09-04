-- Migration 020: configure contract extraction and its resource limits.

CREATE TABLE IF NOT EXISTS contract_extraction_parameters (
  key         VARCHAR(60) PRIMARY KEY,
  value       NUMERIC(15,2) NOT NULL,
  label       TEXT NOT NULL,
  unit        VARCHAR(20) NOT NULL,
  updated_by  UUID,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE contract_extraction_parameters ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all" ON contract_extraction_parameters
    FOR ALL TO service_role USING (TRUE);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

INSERT INTO contract_extraction_parameters (key, value, label, unit) VALUES
  ('ocr_max_pages', 15, 'Maximo de paginas processadas por OCR', 'paginas'),
  ('timeout_total_seconds', 300, 'Timeout total da extracao de contrato', 'segundos'),
  ('llm_max_chars', 60000, 'Maximo de caracteres enviados ao LLM', 'caracteres')
ON CONFLICT (key) DO NOTHING;

INSERT INTO component_config (
  component,
  enabled,
  timeout_seconds,
  max_retries,
  cache_ttl_hours,
  weight,
  description
) VALUES (
  'contrato_extracao',
  TRUE,
  300,
  1,
  0,
  NULL,
  'Extracao de vigencia e condicoes do contrato Broadfactor'
)
ON CONFLICT (component) DO NOTHING;

INSERT INTO component_snapshots (operation_id, component, status)
SELECT id, 'contrato_extracao', 'pending'
FROM operations
WHERE completed_at IS NULL
  AND status IN ('pending', 'processing')
  AND cotacao_id IS NOT NULL
ON CONFLICT (operation_id, component) DO NOTHING;

NOTIFY pgrst, 'reload schema';
