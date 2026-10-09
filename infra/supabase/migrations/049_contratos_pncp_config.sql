INSERT INTO component_config (
  component,
  enabled,
  timeout_seconds,
  max_retries,
  cache_ttl_hours,
  weight,
  description,
  data_scope
) VALUES (
  'contratos_pncp',
  TRUE,
  20,
  2,
  24,
  NULL,
  'Carteira publica e contrato cedido pela busca textual do PNCP',
  'CLIENTE'
)
ON CONFLICT (component) DO UPDATE
SET data_scope = EXCLUDED.data_scope,
    cache_ttl_hours = EXCLUDED.cache_ttl_hours;

INSERT INTO component_snapshots (operation_id, component, status)
SELECT o.id, 'contratos_pncp', 'pending'
FROM operations o
WHERE o.completed_at IS NULL
  AND o.status NOT IN ('reprovada_triagem', 'cotacao_encerrada')
ON CONFLICT (operation_id, component) DO NOTHING;

NOTIFY pgrst, 'reload schema';
