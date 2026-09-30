-- Valor escolhido para precificação de um relatório sem alterar a cotação.
ALTER TABLE operations
  ADD COLUMN IF NOT EXISTS valor_operacao_relatorio NUMERIC(15, 2);

-- Exibido na confirmação antes de iniciar a etapa paga do relatório.
INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  (
    'custo_estimado_relatorio_usd',
    0.54,
    'Custo estimado de geração do relatório',
    'USD',
    'custos_operacionais'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
