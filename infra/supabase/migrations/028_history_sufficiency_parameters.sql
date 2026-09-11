-- Parameters for minimum history before volatility and HHI are fully trusted.

INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  (
    'pd_min_anos_completos_volatilidade',
    2,
    'Minimo de anos completos para calcular volatilidade',
    'anos',
    'risco_credito'
  ),
  (
    'pd_mult_historico_insuficiente',
    1.08,
    'Multiplicador de PD para historico insuficiente',
    'multiplicador',
    'risco_credito'
  ),
  (
    'hhi_min_meses_recebimento',
    6,
    'Minimo de meses para usar HHI sem conservadorismo adicional',
    'meses',
    'risco_credito'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
