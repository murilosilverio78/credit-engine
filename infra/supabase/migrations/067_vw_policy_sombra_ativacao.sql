-- Acompanhamento da política em SOMBRA desde sua promoção.
-- Entregue para aplicação controlada; não aplicar automaticamente.

CREATE OR REPLACE VIEW vw_policy_sombra_ativacao AS
WITH policy_sombra AS (
  SELECT id, versao, sombra_desde
    FROM policy_versions
   WHERE status = 'SOMBRA'
), ultimos_runs AS (
  SELECT
    run.*,
    row_number() OVER (
      PARTITION BY run.operation_id
      ORDER BY run.created_at DESC, run.id DESC
    ) AS ordem
  FROM policy_shadow_runs run
  JOIN policy_sombra policy ON policy.id = run.policy_version_id
), regras_disparadas AS (
  SELECT
    run.id AS run_id,
    COALESCE(
      jsonb_agg(rule ->> 'codigo' ORDER BY rule ->> 'codigo')
        FILTER (WHERE COALESCE((rule ->> 'disparou')::BOOLEAN, FALSE)),
      '[]'::JSONB
    ) AS regras_novas_disparadas
  FROM ultimos_runs run
  LEFT JOIN LATERAL jsonb_array_elements(
    COALESCE(run.comparativo -> 'regras_novas', '[]'::JSONB)
  ) AS rule ON TRUE
  GROUP BY run.id
)
SELECT
  run.operation_id,
  policy.versao AS policy_versao,
  policy.sombra_desde,
  run.created_at AS run_created_at,
  run.classe_geral,
  run.classe_comparativo,
  regras.regras_novas_disparadas,
  count(*) FILTER (WHERE run.classe_comparativo = 'INESPERADA') OVER ()
    AS total_inesperada
FROM ultimos_runs run
JOIN policy_sombra policy ON policy.id = run.policy_version_id
JOIN operations operation ON operation.id = run.operation_id
JOIN regras_disparadas regras ON regras.run_id = run.id
WHERE run.ordem = 1
  AND operation.created_at >= policy.sombra_desde;

REVOKE ALL ON vw_policy_sombra_ativacao FROM PUBLIC, anon, authenticated;
GRANT SELECT ON vw_policy_sombra_ativacao TO service_role;

NOTIFY pgrst, 'reload schema';
