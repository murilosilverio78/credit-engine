-- Cobertura passa a registrar fontes e memória de cálculo sem alterar o catálogo v1.
-- Entregue para aplicação controlada; não aplicar automaticamente.

INSERT INTO finding_catalog (codigo, versao, escopo, classe_padrao, natureza, tipo_valor, descricao)
VALUES (
  'cobertura_parcela', 2, 'OPERACAO', 'INFORMATIVO', 'CODIGO', 'OBJETO',
  'Cobertura da parcela pelo fluxo do contrato, com fontes de fator e parcela'
)
ON CONFLICT (codigo, versao) DO NOTHING;

-- A v3 ainda é RASCUNHO e pode apontar ao índice dentro do novo objeto.
UPDATE policy_rules r
   SET condicao = jsonb_set(
     r.condicao,
     '{comparacao}',
     '{"campo":"indice","op":"<","ref":1.25}'::JSONB
   )
  FROM policy_versions p
 WHERE p.id = r.policy_version_id
   AND p.versao = 3
   AND p.status = 'RASCUNHO'
   AND r.codigo = 'cobertura_parcela'
   AND r.classe = 'AJUSTE'
   AND r.parametro_alvo = 'alcada';

NOTIFY pgrst, 'reload schema';
