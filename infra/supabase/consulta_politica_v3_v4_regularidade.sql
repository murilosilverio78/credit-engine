-- Comparação da última execução em sombra v3/v4 por operação.
-- Troque o UUID pela operação desejada; não altera dados.
WITH ultimas AS (
  SELECT
    run.operation_id,
    policy.versao,
    run.created_at,
    run.resultado,
    run.comparativo,
    row_number() OVER (
      PARTITION BY run.operation_id, policy.versao
      ORDER BY run.created_at DESC
    ) AS posicao
  FROM policy_shadow_runs run
  JOIN policy_versions policy ON policy.id = run.policy_version_id
  WHERE run.operation_id = '997d0261-d55d-4e08-ad8f-db5515319f91'::UUID
    AND policy.versao IN (3, 4)
),
v3 AS (
  SELECT * FROM ultimas WHERE versao = 3 AND posicao = 1
),
v4 AS (
  SELECT * FROM ultimas WHERE versao = 4 AND posicao = 1
)
SELECT
  COALESCE(v4.operation_id, v3.operation_id) AS operation_id,
  v3.created_at AS executado_v3_em,
  v4.created_at AS executado_v4_em,
  v3.resultado ->> 'fator_regularidade' AS fator_regularidade_v3,
  v4.resultado ->> 'indice_regularidade' AS indice_regularidade_v4,
  v4.resultado ->> 'fator_regularidade_v4' AS fator_regularidade_v4,
  v4.resultado -> 'certidoes_avaliadas' AS certidoes_avaliadas_v4,
  v4.resultado -> 'certidoes_ausentes' AS certidoes_ausentes_v4,
  v4.resultado -> 'condicoes_precedentes' AS condicoes_precedentes_v4,
  v4.comparativo
FROM v3
FULL OUTER JOIN v4 ON v4.operation_id = v3.operation_id;
