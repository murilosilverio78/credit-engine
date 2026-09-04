-- Migration 016: W3 credit signals derived from government receipts

INSERT INTO pricing_parameters (key, value, label, unit, grupo) VALUES
  (
    'pd_cv_corte_moderado',
    0.70,
    'Corte de CV para volatilidade moderada',
    'coeficiente',
    'risco_credito'
  ),
  (
    'pd_cv_corte_alto',
    0.80,
    'Corte de CV para volatilidade alta',
    'coeficiente',
    'risco_credito'
  ),
  (
    'pd_mult_volatilidade_moderada',
    1.08,
    'Multiplicador de PD para volatilidade moderada',
    'multiplicador',
    'risco_credito'
  ),
  (
    'pd_mult_volatilidade_alta',
    1.15,
    'Multiplicador de PD para volatilidade alta',
    'multiplicador',
    'risco_credito'
  )
ON CONFLICT (key) DO NOTHING;

NOTIFY pgrst, 'reload schema';
